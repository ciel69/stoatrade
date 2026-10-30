"""Новый дашборд: `./tb dash2 [--port 8767] [--max-seconds N]`.

Один порт на 127.0.0.1: статика собранного фронта (`frontend/dist`, сборка `npm ci && npm run build` в `frontend/`) и WebSocket `/ws`. Старый дашборд (`./tb dash`, 8766) не затрагивается.
Протокол (docs/FRONTEND_PLAN.md, раздел 4): сообщение {"topic","seq","ts","data"}; при подключении клиент получает `hello` и снимок каждой темы, дальше тема приходит только когда её содержимое
изменилось (один общий цикл раз в TICK_S секунд на всех клиентов, а не опрос от каждого). Раз в HEARTBEAT_S секунд тема `link` (клиент по ней видит, что связь жива). Клиент -> сервер: {"op":"ping"} -> {"topic":"pong"}.
Опроса нет: при обрыве клиент переподключается сам и получает свежие снимки. Команды управления сюда не приходят (только чтение; POST с CSRF остаётся за ctl старого сервера до переноса).
Защита: слушаем только 127.0.0.1, проверка Host и Origin, не больше MAX_CLIENTS сокетов, лимит размера входящего сообщения, никаких полей вне белых списков тем.
"""
import argparse
import asyncio
import json
import mimetypes
import os
import sys
import time
from http import HTTPStatus

from tb import paths
from tb.dash2 import topics

HOST = "127.0.0.1"
DEFAULT_PORT = 8767
MAX_CLIENTS = 5
TICK_S = 1.0
HEARTBEAT_S = 15.0
DEV_ORIGINS = ("http://127.0.0.1:5173", "http://localhost:5173")      # Vite dev server (npm run dev)
CTYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
          ".json": "application/json; charset=utf-8", ".woff2": "font/woff2", ".png": "image/png", ".ico": "image/x-icon", ".map": "application/json"}
PLACEHOLDER = """<!doctype html><meta charset="utf-8"><title>open-trader: новый дашборд</title>
<body style="font:16px system-ui;background:#0b121a;color:#e8eef4;padding:32px;max-width:640px">
<h1>Фронт ещё не собран</h1><p>Сервер и сокет работают. Соберите интерфейс:</p>
<pre style="background:#121c28;padding:12px;border-radius:8px">cd frontend\nnpm ci\nnpm run build</pre>
<p>Для разработки: <code>npm run dev</code> (порт 5173, сокет проксируется сюда).</p></body>"""


def dist_dir(root=None):
    return os.path.join(paths.root() if root is None else root, "frontend", "dist")


def csp(port):
    return ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self' ws://127.0.0.1:%d ws://localhost:%d; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'" % (port, port))


def safe_file(base, rel):
    """Путь внутри base или None (никаких выходов наверх, только обычные файлы)."""
    p = os.path.normpath(os.path.join(base, rel.lstrip("/")))
    if os.path.commonpath([base, p]) != base or not os.path.isfile(p):
        return None
    return p


class Hub(object):
    def __init__(self, root=None, port=DEFAULT_PORT, tick_s=TICK_S, heartbeat_s=HEARTBEAT_S, max_clients=MAX_CLIENTS, clock=time.time):
        self.root, self.port, self.tick_s, self.heartbeat_s, self.max_clients, self.clock = root, port, tick_s, heartbeat_s, max_clients, clock
        self.clients = set()
        self.state = {}                 # тема -> {"seq", "json", "msg"}
        self.link_seq = 0
        self.stats = {"connects": 0, "messages": 0, "changes": 0}

    # ---- сообщения
    def _msg(self, topic, seq, data):
        return json.dumps({"topic": topic, "seq": seq, "ts": round(self.clock(), 3), "data": data}, ensure_ascii=False, separators=(",", ":"))

    async def refresh(self):
        """Пересобирает темы (в потоке: чтение файлов) и рассылает только изменившиеся. -> список изменившихся тем."""
        changed = []
        for name in topics.TOPICS:
            try:
                data = await asyncio.to_thread(topics.build, name, self.root, None)
            except Exception as e:           # одна сломанная тема не должна ронять цикл
                data = {"error": type(e).__name__}
            text = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            cur = self.state.get(name)
            if cur is not None and cur["json"] == text:
                continue
            seq = 1 if cur is None else cur["seq"] + 1
            self.state[name] = {"seq": seq, "json": text, "msg": self._msg(name, seq, data)}
            changed.append(name)
        if changed:
            self.stats["changes"] += len(changed)
            self.broadcast([self.state[n]["msg"] for n in changed])
        return changed

    def broadcast(self, msgs):
        from websockets import broadcast
        for m in msgs:
            broadcast(self.clients, m)
            self.stats["messages"] += len(self.clients)

    def snapshot_messages(self):
        return [self._msg("hello", 0, {"topics": sorted(topics.TOPICS), "tick_s": self.tick_s, "heartbeat_s": self.heartbeat_s, "server_ts": round(self.clock(), 3)})] + \
               [self.state[n]["msg"] for n in sorted(self.state)]

    async def loop(self):
        last_hb = self.clock()
        while True:
            await self.refresh()
            if self.clock() - last_hb >= self.heartbeat_s:
                last_hb = self.clock()
                self.link_seq += 1
                self.broadcast([self._msg("link", self.link_seq, {"clients": len(self.clients)})])
            await asyncio.sleep(self.tick_s)

    # ---- соединения
    async def handler(self, ws):
        from websockets.exceptions import ConnectionClosed
        self.stats["connects"] += 1
        try:
            if not self.state:
                await self.refresh()
            sent = {n: self.state[n]["seq"] for n in self.state}
            for m in self.snapshot_messages():
                await ws.send(m)
            self.clients.add(ws)                 # в рассылку только после снимка: иначе изменение темы обогнало бы hello
            for n in sorted(self.state):         # тема изменилась, пока шёл снимок: досылаем текущую
                if self.state[n]["seq"] != sent.get(n):
                    await ws.send(self.state[n]["msg"])
            async for raw in ws:
                try:
                    m = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if isinstance(m, dict) and m.get("op") == "ping":
                    await ws.send(self._msg("pong", 0, {}))
        except ConnectionClosed:                 # клиент закрыл вкладку или оборвал связь: не ошибка сервера
            pass
        finally:
            self.clients.discard(ws)

    def process_request(self, connection, request):
        from websockets.datastructures import Headers
        from websockets.http11 import Response
        host = (request.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if host not in ("127.0.0.1", "localhost", "::1"):                      # защита от DNS rebinding
            return connection.respond(HTTPStatus.FORBIDDEN, "bad host\n")
        path = request.path.split("?", 1)[0]
        if path == "/ws":
            if len(self.clients) >= self.max_clients:
                return connection.respond(HTTPStatus.SERVICE_UNAVAILABLE, "too many clients\n")
            return None
        if request.headers.get("Upgrade", "").lower() == "websocket":          # сокет на другой путь не принимаем
            return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        base = dist_dir(self.root)
        rel = "index.html" if path in ("", "/") else path
        f = safe_file(base, rel)
        if f is None and "." not in os.path.basename(path):                    # маршруты SPA
            f = safe_file(base, "index.html")
        if f is None:
            if path in ("", "/") or "." not in os.path.basename(path):
                body = PLACEHOLDER.encode("utf-8")
                ctype = CTYPES[".html"]
            else:
                return connection.respond(HTTPStatus.NOT_FOUND, "not found\n")
        else:
            with open(f, "rb") as fh:
                body = fh.read()
            ctype = CTYPES.get(os.path.splitext(f)[1].lower()) or mimetypes.guess_type(f)[0] or "application/octet-stream"
        h = Headers([("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", "no-store" if rel.endswith(".html") or f is None else "public, max-age=3600"),
                     ("X-Content-Type-Options", "nosniff"), ("Content-Security-Policy", csp(self.port)), ("Referrer-Policy", "no-referrer")])
        return Response(200, "OK", h, body)


async def serve(port=DEFAULT_PORT, root=None, max_seconds=None, ready=None):
    from websockets.asyncio.server import serve as ws_serve
    hub = Hub(root=root, port=port)
    origins = ["http://127.0.0.1:%d" % port, "http://localhost:%d" % port] + list(DEV_ORIGINS) + [None]      # None: клиенты без Origin (не браузер, тесты)
    async with ws_serve(hub.handler, HOST, port, origins=origins, process_request=hub.process_request, max_size=4096, ping_interval=20, ping_timeout=20) as srv:
        if ready is not None:
            ready(hub, srv)
        task = asyncio.ensure_future(hub.loop())
        try:
            if max_seconds:
                await asyncio.sleep(max_seconds)
            else:
                await asyncio.Future()
        finally:
            task.cancel()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tb dash2", description="новый дашборд (Vue + WebSocket), только чтение, 127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--max-seconds", type=float, default=None, dest="max_seconds")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    print("дашборд v2: http://%s:%d/  (старый: ./tb dash)" % (HOST, a.port), flush=True)
    try:
        asyncio.run(serve(a.port, max_seconds=a.max_seconds))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
