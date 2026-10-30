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
HEAVY_S = 2.0           # home собирается около секунды: обновляем не чаще раза в 2 с
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
    """Состояние рассылки. Темы двух видов: общие (brain, events) и зависящие от режима клиента (home): последние строятся для каждого режима, который сейчас смотрит
    хотя бы один клиент, и уходят только тем клиентам, у кого этот режим выбран. Тяжёлые темы (home собирается около секунды) обновляются раз в HEAVY_S."""

    def __init__(self, root=None, port=DEFAULT_PORT, tick_s=TICK_S, heartbeat_s=HEARTBEAT_S, max_clients=MAX_CLIENTS, clock=time.time, heavy_s=HEAVY_S):
        self.root, self.port, self.tick_s, self.heartbeat_s, self.max_clients, self.clock, self.heavy_s = root, port, tick_s, heartbeat_s, max_clients, clock, heavy_s
        self.clients = {}               # соединение -> режим
        self.state = {}                 # (тема, режим|None) -> {"seq", "json", "msg"}
        self.link_seq = 0
        self._heavy_at = {}             # (тема, режим) -> когда собирали
        self.stats = {"connects": 0, "messages": 0, "changes": 0}

    # ---- сообщения
    def _msg(self, topic, seq, data):
        return json.dumps({"topic": topic, "seq": seq, "ts": round(self.clock(), 3), "data": data}, ensure_ascii=False, separators=(",", ":"))

    def modes_in_use(self):
        return set(self.clients.values()) | {topics.default_mode(self.root)}

    async def _rebuild(self, topic, mode, force=False):
        key = (topic, mode)
        if topic in topics.MODE_TOPICS and not force and self.clock() - self._heavy_at.get(key, 0.0) < self.heavy_s and key in self.state:
            return False
        self._heavy_at[key] = self.clock()
        try:
            data = await asyncio.to_thread(topics.build, topic, self.root, None, mode)
        except Exception as e:           # одна сломанная тема не должна ронять цикл
            data = {"error": type(e).__name__}
        text = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        cur = self.state.get(key)
        if cur is not None and cur["json"] == text:
            return False
        seq = 1 if cur is None else cur["seq"] + 1
        self.state[key] = {"seq": seq, "json": text, "msg": self._msg(topic, seq, data)}
        return True

    async def refresh(self, force=False):
        """Пересобирает темы (в потоке: чтение файлов) и рассылает только изменившиеся. -> список изменившихся (тема, режим)."""
        changed = []
        for name in topics.TOPICS:
            if await self._rebuild(name, None, force):
                changed.append((name, None))
        for name in topics.MODE_TOPICS:
            for mode in sorted(self.modes_in_use()):
                if await self._rebuild(name, mode, force):
                    changed.append((name, mode))
        if changed:
            self.stats["changes"] += len(changed)
            for key in changed:
                self.send_topic(key)
        return changed

    def send_topic(self, key):
        from websockets import broadcast
        msg = self.state[key]["msg"]
        targets = [ws for ws, mode in self.clients.items() if key[1] is None or key[1] == mode]
        broadcast(targets, msg)
        self.stats["messages"] += len(targets)

    def broadcast_all(self, msg):
        from websockets import broadcast
        broadcast(list(self.clients), msg)
        self.stats["messages"] += len(self.clients)

    def snapshot_messages(self, mode):
        out = [self._msg("hello", 0, {"topics": sorted(list(topics.TOPICS) + list(topics.MODE_TOPICS)), "tick_s": self.tick_s, "heartbeat_s": self.heartbeat_s,
                                       "server_ts": round(self.clock(), 3), "mode": mode, "modes": list(topics.MODES)})]
        out += [self.state[(n, None)]["msg"] for n in sorted(topics.TOPICS) if (n, None) in self.state]
        out += [self.state[(n, mode)]["msg"] for n in sorted(topics.MODE_TOPICS) if (n, mode) in self.state]
        return out

    async def loop(self):
        last_hb = self.clock()
        while True:
            await self.refresh()
            if self.clock() - last_hb >= self.heartbeat_s:
                last_hb = self.clock()
                self.link_seq += 1
                self.broadcast_all(self._msg("link", self.link_seq, {"clients": len(self.clients)}))
            await asyncio.sleep(self.tick_s)

    # ---- соединения
    async def handler(self, ws):
        from websockets.exceptions import ConnectionClosed
        self.stats["connects"] += 1
        try:
            mode = topics.default_mode(self.root)
            if not all((n, None) in self.state for n in topics.TOPICS) or any((n, mode) not in self.state for n in topics.MODE_TOPICS):
                await self.refresh(force=False)
                for n in topics.MODE_TOPICS:
                    if (n, mode) not in self.state:
                        await self._rebuild(n, mode, force=True)
            sent = {k: v["seq"] for k, v in self.state.items()}
            for m in self.snapshot_messages(mode):
                await ws.send(m)
            self.clients[ws] = mode                  # в рассылку только после снимка: иначе изменение темы обогнало бы hello
            for key in sorted(self.state, key=str):  # тема изменилась, пока шёл снимок: досылаем текущую
                if (key[1] is None or key[1] == mode) and self.state[key]["seq"] != sent.get(key):
                    await ws.send(self.state[key]["msg"])
            async for raw in ws:
                try:
                    m = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if not isinstance(m, dict):
                    continue
                if m.get("op") == "ping":
                    await ws.send(self._msg("pong", 0, {}))
                elif m.get("op") == "mode" and m.get("mode") in topics.MODES:
                    await self.set_mode(ws, m["mode"])
        except ConnectionClosed:                 # клиент закрыл вкладку или оборвал связь: не ошибка сервера
            pass
        finally:
            self.clients.pop(ws, None)

    async def set_mode(self, ws, mode):
        """Клиент переключил режим: свежая сборка home для него (если ещё не строилась) и снимок."""
        self.clients[ws] = mode
        for n in topics.MODE_TOPICS:
            if (n, mode) not in self.state:
                await self._rebuild(n, mode, force=True)
            if (n, mode) in self.state:
                await ws.send(self.state[(n, mode)]["msg"])

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
