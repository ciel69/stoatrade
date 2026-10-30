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

    def test_home_pnl_today_counts_all_trades_even_flagged_artifacts(self):
        today = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        rows = []
        for tid, pnl in (("T1", 1.5), ("T2", 9.0)):
            rows.append({"ev": "open", "id": tid, "ts": today, "sym": "SOL", "side": "long", "mode": "paper", "entry": 100.0, "sl": 99.0, "risk_usdt": 1.0, "venue": "bybit"})
            rows.append({"ev": "close", "id": tid, "ts_close": today, "pnl_usdt": pnl, "reason": "tp"})
        w(self.root, "state/trades.jsonl", "".join(json.dumps(r) + "\n" for r in rows))
        w(self.root, "state/exclude_trades.json", json.dumps({"ids": ["T2"]}))
        m = topics.build("home", self.root, None, "paper")["money"]
        self.assertEqual((m["pnl_today"], m["trades_today"]), (10.5, 2))        # артефакты принимаем как случившееся: как на старом дашборде, в UI-тестах нужны

    def test_home_curve_cumulative_counts_all_and_trims(self):
        now = time.time()
        rows = []
        for i, (tid, pnl) in enumerate((("T1", 1.0), ("T2", 9.0), ("T3", -0.4))):
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3600 * (3 - i)))
            rows.append({"ev": "open", "id": tid, "ts": ts, "sym": "SOL", "side": "long", "mode": "paper", "entry": 100.0, "sl": 99.0, "risk_usdt": 1.0, "venue": "bybit"})
            rows.append({"ev": "close", "id": tid, "ts_close": ts, "pnl_usdt": pnl, "reason": "tp"})
        w(self.root, "state/trades.jsonl", "".join(json.dumps(r) + "\n" for r in rows))
        w(self.root, "state/exclude_trades.json", json.dumps({"ids": ["T2"]}))
        c = topics.build("home", self.root, None, "paper")["curve"]
        self.assertEqual([p[1] for p in c["points"]], [1.0, 10.0, 9.6])
        self.assertEqual((c["base"], c["total"]), (0.0, 3))
        old = topics.CURVE_POINTS
        topics.CURVE_POINTS = 1
        try:
            t = topics.build("home", self.root, None, "paper")["curve"]
        finally:
            topics.CURVE_POINTS = old
        self.assertEqual(([p[1] for p in t["points"]], t["base"]), ([9.6], 10.0))      # хвост обрезан, база хранит сумму отрезанного

    def test_plans_and_pending_blocks_are_stable_and_filtered(self):
        now = 1000.0
        v2 = {"plans": {"cards": [
            {"id": "A", "sym": "SOL", "side": "long", "mode": "paper", "col": "watching", "trigger": {"text": "цена ≥ 100", "dist_pct": 2.0, "eta_s": 61}, "net_rr": 2.5, "risk_usdt": 0.3},
            {"id": "B", "sym": "ETH", "side": "short", "mode": "paper", "col": "waiting", "trigger": {"text": "лимит стоит", "dist_pct": 0.2, "eta_s": None}},
            {"id": "C", "sym": "BTC", "side": "long", "mode": "live", "col": "waiting", "trigger": {"dist_pct": 0.1}},
            {"id": "D", "sym": "XRP", "side": "long", "mode": "paper", "col": "in_trade", "trigger": {}}]}}
        p = topics._plans(v2, "paper", now)
        self.assertEqual(p["counts"], {"waiting": 1, "watching": 1, "in_trade": 1, "closed": 0})
        self.assertEqual([x["id"] for x in p["near"]], ["B", "A"])                      # ближайшие к входу первыми, чужой режим не виден
        self.assertEqual(p["near"][1]["eta_at"], 1060)                                   # момент, а не остаток: тема не меняется со временем
        self.assertEqual(topics._plans(v2, "paper", now + 1)["near"][1]["eta_at"], 1060)
        full = {"plans": {"cards": [{"id": "E", "sym": "SOL", "side": "short", "mode": "paper", "col": "waiting", "status": "armed", "setup": "level_reject", "price": 143.5, "ttl_left_s": 7213.0,
                                     "stop": 146.0, "targets": [{"id": "t1", "price": 140.0}], "hold": None,
                                     "trigger": {"text": "5м закрытие выше 144", "dist_pct": 0.35, "eta_s": 61, "missing": ["5м закрытие выше 144", "объём 5м ≥ x2 медианы"], "met_n": 1, "leaf_n": 3,
                                                 "seq": None, "level": 144.0, "kind": "candle_close", "dir": "above", "beyond": False, "eta_tf": "5м"}}]}}
        e = topics._plans(full, "paper", now)["near"][0]
        self.assertEqual((e["missing"], e["met_n"], e["leaf_n"], e["tf"], e["level"], e["kind"], e["stop"], e["target"], e["setup"]),
                         (["5м закрытие выше 144", "объём 5м ≥ x2 медианы"], 1, 3, "5м", 144.0, "candle_close", 146.0, 140.0, "level_reject"))
        self.assertEqual(e["ttl_at"], 8220)                                              # срок жизни плана моментом (округлён до 30 с), а не остатком
        pend = topics._pending([{"id": "L1", "sym": "SOL", "side": "long", "price": 99.0, "cur": 100.0, "dist_pct": -1.0, "left_s": 600.0, "ttl_min": 15}], now)
        self.assertEqual((pend["total"], pend["items"][0]["expires_at"], pend["items"][0]["dist_pct"]), (1, 1600, -1.0))

    def test_trades_topic_cards_reasons_and_review(self):
        now = time.time()
        rows = []
        for i, (tid, pnl, reason) in enumerate((("T1", 1.0, "tp"), ("T2", -0.5, "sl"), ("T3", -0.2, "sl"))):
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3600 * (3 - i)))
            rows.append({"ev": "open", "id": tid, "ts": ts, "ts_open": ts, "sym": "SOL", "side": "long", "mode": "paper", "entry": 100.0, "sl": 99.0, "margin": 4.0, "lev": 10, "risk_usdt": 0.5, "venue": "bybit"})
            rows.append({"ev": "close", "id": tid, "ts_close": ts, "exit": 101.0, "pnl_usdt": pnl, "reason": reason, "err": ["late_entry"] if tid == "T2" else []})
        w(self.root, "state/trades.jsonl", "".join(json.dumps(r) + "\n" for r in rows))
        t = topics.build("trades", self.root, None, "paper")
        self.assertEqual((t["total"], [c["id"] for c in t["items"]]), (3, ["T3", "T2", "T1"]))          # новые первыми
        self.assertEqual({g["name"]: g["n"] for g in t["reasons"]}, {"sl": 2, "tp": 1})
        self.assertEqual(([g["name"] for g in t["errs"]], t["unreviewed"]), (["late_entry"], 2))
        self.assertEqual(topics.build("trades", self.root, None, "live")["items"], [])               # другой режим: чужих сделок нет
        self.assertAlmostEqual(t["items"][2]["pct"], 25.0, places=3)                                  # +1.0 к марже 4.0

    def test_decisions_topic_filters_by_mode_and_models_periods(self):
        rows = [{"seq": 1, "t": time.time() - 30, "ts": "2026-10-30T10:00:00Z", "actor": "brain:entry_scout", "what": "note", "kind": "note", "plan": None, "why": "без плана"},
                {"seq": 2, "t": time.time() - 20, "ts": "2026-10-30T10:00:10Z", "actor": "exec", "what": "move_stop", "kind": "cmd", "plan": "PL-1030-SOL-01", "why": "стоп подтянут",
                 "args": {"prev": 99.0, "to": 100.0}, "res": {"ok": True}}]
        w(self.root, "state/decisions.jsonl", "".join(json.dumps(r) + "\n" for r in rows))
        w(self.root, "state/plans/PL-1030-SOL-01/spec.json", json.dumps({"symbol": "SOL", "side": "long", "mode": "paper"}))
        d = topics.build("decisions", self.root, None, "paper")
        self.assertEqual([x["seq"] for x in d["items"]], [2, 1])                         # новые сверху, запись без плана видна
        self.assertEqual(d["items"][0]["mode"], "paper")
        live = topics.build("decisions", self.root, None, "live")
        self.assertEqual([x["seq"] for x in live["items"]], [1])                         # план paper в режиме live не виден
        m = topics.build("models", self.root, None, "paper")
        self.assertEqual(sorted(m["periods"]), ["1", "30", "7"])

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
            first = [await self.recv(ws) for _ in range(8)]
            self.assertEqual([m["topic"] for m in first], ["hello", "brain", "ctl", "events", "decisions", "home", "models", "trades"])
            self.assertEqual(first[1]["data"]["status"], "idle")
            self.assertEqual((first[0]["data"]["mode"], first[5]["data"]["mode"]), ("paper", "paper"))
            self.assertIn("enabled", first[0]["data"]["ctl"])
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
            for _ in range(8):
                await self.recv(a)
                await self.recv(b)
            await a.send(json.dumps({"op": "mode", "mode": "live"}))
            got = [await self.recv(a) for _ in range(4)]
            self.assertEqual(sorted((m["topic"], m["data"]["mode"]) for m in got), [("decisions", "live"), ("home", "live"), ("models", "live"), ("trades", "live")])
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
