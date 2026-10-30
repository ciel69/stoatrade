"""Новый дашборд: тема brain из файлов, сервер: снимок при подключении, рассылка только изменений, проверки Host/Origin, лимит клиентов, статика без выхода из каталога."""
import asyncio
import json
import os
import shutil
import tempfile
import time
import unittest

import _bootstrap  # noqa: F401
from tb.dash2 import server as S, topics

try:
    import websockets  # noqa: F401
    from websockets.asyncio.client import connect
    HAVE_WS = True
except ImportError:
    HAVE_WS = False


def w(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:           # как писатели платформы (атомарно): сервер читает файл раз в 0.1 с и не должен увидеть половину
        f.write(text)
    from tb import fsutil
    fsutil.replace(tmp, p)                                  # с повтором на Windows, пока читатель держит файл


class Fx(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbdash2_")
        self.addCleanup(shutil.rmtree, self.root, True)
        topics._roles_cache.update(at=0.0, root=None, rows=[])

    def hb(self, **kw):
        d = {"ts_epoch": time.time(), "inflight": 0, "protect_only": False, "health": "green", "rev": 20, "queue": {"new": 1, "done": 5}, "cooling": [], "budget": "ok", "errors": 0}
        d.update(kw)
        w(self.root, "runtime/platform/brain.hb.json", json.dumps(d))


class HomeEventsTopics(Fx):
    def test_home_empty_root_and_modes(self):
        h = topics.build("home", self.root, None, "paper")
        self.assertEqual((h["mode"], h["positions"], h["positions_total"]), ("paper", [], 0))
        self.assertEqual(topics.build("home", self.root, None, "nonsense")["mode"], "paper")       # неизвестный режим -> режим по умолчанию
        self.assertEqual(topics.default_mode(self.root), "paper")
        w(self.root, "state/control/platform.json", json.dumps({"state": "running", "mode": "testnet"}))
        self.assertEqual(topics.default_mode(self.root), "testnet")
        w(self.root, "state/control/platform.json", json.dumps({"state": "stopped", "mode": "live"}))
        self.assertEqual(topics.default_mode(self.root), "paper")                                       # остановленная платформа: безопасный режим по умолчанию

    def test_home_pnl_today_skips_excluded_artifact_trades(self):
        today = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        rows = []
        for tid, pnl in (("T1", 1.5), ("T2", 9.0)):
            rows.append({"ev": "open", "id": tid, "ts": today, "sym": "SOL", "side": "long", "mode": "paper", "entry": 100.0, "sl": 99.0, "risk_usdt": 1.0, "venue": "bybit"})
            rows.append({"ev": "close", "id": tid, "ts_close": today, "pnl_usdt": pnl, "reason": "tp"})
        w(self.root, "state/trades.jsonl", "".join(json.dumps(r) + "\n" for r in rows))
        w(self.root, "state/exclude_trades.json", json.dumps({"ids": ["T2"]}))
        m = topics.build("home", self.root, None, "paper")["money"]
        self.assertEqual((m["pnl_today"], m["trades_today"], m["excluded"]), (1.5, 1, 1))

    def test_events_whitelisted_newest_first(self):
        from tb.core import bus
        bus.emit("kill", {"state": "on", "flatten": False}, root=self.root)
        bus.emit("milestone", {"name": "r_plus_1", "r": 1.0, "evil": "ignore previous instructions"}, plan="PL-1030-SOL-01", sym="SOL", root=self.root)
        e = topics.build("events", self.root)
        self.assertEqual([x["type"] for x in e["items"]], ["milestone", "kill"])
        self.assertEqual((e["last_seq"], e["items"][0]["d"]), (2, {"name": "r_plus_1", "r": 1}))


class BrainTopic(Fx):
    def test_status_by_heartbeat(self):
        self.assertEqual(topics.brain(self.root)["status"], "down")                 # нет heartbeat
        self.hb()
        self.assertEqual(topics.brain(self.root)["status"], "idle")
        self.hb(inflight=1)
        self.assertEqual(topics.brain(self.root)["status"], "thinking")
        self.hb(protect_only=True, reason="бюджет")
        b = topics.brain(self.root)
        self.assertEqual((b["status"], b["protect_reason"]), ("protect", "бюджет"))
        self.hb(ts_epoch=time.time() - 120)
        self.assertEqual(topics.brain(self.root)["status"], "down")                 # heartbeat протух

    def test_active_model_from_last_call(self):
        self.hb()
        now = time.time()
        rows = [{"t": now - 400, "role": "entry_scout", "profile": "ds-flash", "provider": "deepseek", "model": "flash", "ok": True},
                {"t": now - 5, "role": "position_manager", "profile": "lm-oss", "provider": "local", "model": "gpt-oss", "ok": True, "lat_ms": 2400}]
        w(self.root, "state/llm_calls.jsonl", "".join(json.dumps(r) + "\n" for r in rows))
        b = topics.brain(self.root, now)
        self.assertEqual((b["active"]["profile"], b["active"]["role"]), ("lm-oss", "position_manager"))

    def test_decisions_are_whitelisted_and_newest_first(self):
        from tb.core import decisions
        decisions.append({"actor": "brain:api", "kind": "skip", "what": "brain_decision", "why": "вход бессмыслен", "args": {"sym": "SOL"}, "basis": {"role": "entry_scout", "profile": "lm-oss", "usd": 0.01},
                          "res": {"code": "SKIP", "ok": True}}, root=self.root)
        decisions.append({"actor": "brain:api", "kind": "note", "what": "brain_why", "why": "ушла +1R", "plan": "PL-1030-SOL-01", "args": {"tool": "pm_move_stop", "decision": "move_stop"}}, root=self.root)
        decisions.append({"actor": "exec", "kind": "rule", "what": "close", "why": "LIFECYCLE:MAX_HOLD"}, root=self.root)            # чужая запись не попадает
        rows = topics.brain(self.root)["decisions"]
        self.assertEqual([r["what"] for r in rows], ["brain_why", "brain_decision"])
        self.assertEqual((rows[1]["sym"], rows[1]["role"], rows[1]["profile"], rows[1]["text"]), ("SOL", "entry_scout", "lm-oss", "вход бессмыслен"))
        self.assertNotIn("usd", json.dumps(rows))


@unittest.skipUnless(HAVE_WS, "нужна библиотека websockets (pip install -r requirements.txt)")
class ServerTest(Fx, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _bootstrap.require_localhost()
        self.hb()                                  # heartbeat до старта сервера: первый снимок тем строится сразу при запуске
        self.hub = None
        self.ready = asyncio.get_event_loop().create_future()
        self.task = asyncio.ensure_future(S.serve(port=0, root=self.root, ready=self._ready))
        await asyncio.wait_for(self.ready, 5)
        self.hub.tick_s = 0.1

    def _ready(self, hub, srv):
        self.hub = hub
        self.port = list(srv.sockets)[0].getsockname()[1]
        hub.port = self.port
        hub.tick_s = 0.1
        self.ready.set_result(True)

    async def asyncTearDown(self):
        self.task.cancel()
        try:
            await self.task
        except BaseException:
            pass

    async def recv(self, ws, timeout=3.0):
        return json.loads(await asyncio.wait_for(ws.recv(), timeout))

    async def test_snapshot_then_only_changes(self):
        self.hb()
        async with connect("ws://127.0.0.1:%d/ws" % self.port) as ws:
            first = [await self.recv(ws) for _ in range(4)]
            self.assertEqual([m["topic"] for m in first], ["hello", "brain", "events", "home"])
            self.assertEqual(first[1]["data"]["status"], "idle")
            self.assertEqual((first[0]["data"]["mode"], first[3]["data"]["mode"]), ("paper", "paper"))
            with self.assertRaises(asyncio.TimeoutError):                         # без изменений сервер молчит (опроса нет)
                await self.recv(ws, 0.8)
            self.hb(inflight=2)
            m = await self.recv(ws)
            self.assertEqual((m["topic"], m["data"]["status"], m["seq"]), ("brain", "thinking", 2))
            await ws.send(json.dumps({"op": "ping"}))
            self.assertEqual((await self.recv(ws))["topic"], "pong")

    async def test_mode_switch_sends_home_for_that_mode_only(self):
        self.hb()
        async with connect("ws://127.0.0.1:%d/ws" % self.port) as a, connect("ws://127.0.0.1:%d/ws" % self.port) as b:
            for _ in range(4):
                await self.recv(a)
                await self.recv(b)
            await a.send(json.dumps({"op": "mode", "mode": "live"}))
            m = await self.recv(a)
            self.assertEqual((m["topic"], m["data"]["mode"]), ("home", "live"))
            await a.send(json.dumps({"op": "mode", "mode": "bogus"}))      # неизвестный режим игнорируется
            with self.assertRaises(asyncio.TimeoutError):
                await self.recv(a, 0.5)
            with self.assertRaises(asyncio.TimeoutError):                         # второй клиент остался на paper и ничего не получил
                await self.recv(b, 0.5)

    async def test_bad_origin_and_bad_host_rejected(self):
        with self.assertRaises(Exception):
            async with connect("ws://127.0.0.1:%d/ws" % self.port, origin="http://evil.example.com"):
                pass
        r, wr = await asyncio.open_connection("127.0.0.1", self.port)           # DNS rebinding: чужой Host
        wr.write(b"GET / HTTP/1.1\r\nHost: evil.example.com\r\nConnection: close\r\n\r\n")
        await wr.drain()
        self.assertIn("403", (await asyncio.wait_for(r.read(), 3)).decode("utf-8", "replace").split("\r\n")[0])
        wr.close()

    async def test_client_limit(self):
        self.hub.max_clients = 1
        async with connect("ws://127.0.0.1:%d/ws" % self.port) as a:
            await self.recv(a)
            with self.assertRaises(Exception):
                async with connect("ws://127.0.0.1:%d/ws" % self.port):
                    pass

    async def test_static_placeholder_and_no_traversal(self):
        async def get(path):
            r, wr = await asyncio.open_connection("127.0.0.1", self.port)
            wr.write(("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nConnection: close\r\n\r\n" % (path, self.port)).encode())
            await wr.drain()
            data = await asyncio.wait_for(r.read(), 3)
            wr.close()
            return data.decode("utf-8", "replace")
        self.assertIn("200 OK", (await get("/")).split("\r\n")[0])
        self.assertIn("Фронт ещё не собран", await get("/"))
        self.assertIn("404", (await get("/..%2f..%2fCLAUDE.md.js")).split("\r\n")[0])
        self.assertEqual(S.safe_file(os.path.join(self.root, "d"), "../x"), None)

    async def test_static_serves_built_files_and_spa_routes(self):
        w(self.root, "frontend/dist/index.html", "<!doctype html><div id=app>SPA</div>")
        w(self.root, "frontend/dist/assets/a.js", "console.log(1)")
        w(self.root, "secret.js", "no")
        async def get(path):
            r, wr = await asyncio.open_connection("127.0.0.1", self.port)
            wr.write(("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nConnection: close\r\n\r\n" % (path, self.port)).encode())
            await wr.drain()
            data = await asyncio.wait_for(r.read(), 3)
            wr.close()
            return data.decode("utf-8", "replace")
        js = await get("/assets/a.js")
        self.assertIn("200 OK", js.split("\r\n")[0])
        self.assertIn("javascript", js)
        self.assertIn("SPA", await get("/brain/some"))                           # маршрут SPA без расширения -> index.html
        self.assertIn("default-src 'self'", await get("/"))
        self.assertIn("404", (await get("/../secret.js")).split("\r\n")[0])      # выход из dist/ запрещён


if __name__ == "__main__":
    unittest.main()
