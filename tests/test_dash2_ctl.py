"""Мост команд управления нового дашборда (tb.dash2.ctlbridge): те же защиты, что у POST-пути старого, плюс CSRF соединения и белый список.
Временные TB_ROOT и TB_KEYS_DIR (поддельный ключ), фейковый ctl-цикл, никаких живых команд платформе, сети и биржи."""
import asyncio
import json
import os
import threading
import time
import unittest

import _bootstrap  # noqa: F401
import _ctl
from tb.control import audit, client
from tb.dash2 import ctlbridge, server as S

try:
    from websockets.asyncio.client import connect
    HAVE_WS = True
except ImportError:
    HAVE_WS = False

WS = object()          # «соединение» для чистых тестов моста
PAPER = {"mode": "paper", "session": "day"}


class Base(unittest.TestCase):
    auth = "open_local"

    def setUp(self):
        self.e = _ctl.Env(self, now=time.time())
        self.e.w("config/control.json", {"version": 1, "auth": {"mode": self.auth, "remember_days": 30}})
        self.b = ctlbridge.CtlBridge(self.e.root)
        self.info = self.b.open(WS)

    def msg(self, kind="command", action="day.start", args=None, **over):
        m = {"id": "c1", "csrf": self.info.get("csrf"), "kind": kind, "action": action, "args": PAPER if args is None else args}
        m.update(over)
        return m

    def inbox(self):
        d = os.path.join(self.e.root, "state", "control", "_inbox")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def audit_codes(self):
        return [(a["action"], a["code"]) for a in audit.tail(self.e.root, 100)]


class TestAccess(Base):
    def test_session_open_only_in_open_local_with_key(self):
        self.assertTrue(self.info["enabled"] and len(self.info["csrf"]) >= 20)
        self.assertNotIn("level", self.info)
        s = self.b.sess[WS][1]
        self.assertEqual(s["level"], "open")                  # токен этот дашборд не принимает: «full» невозможен
        self.assertFalse(s.get("remember"))

    def test_token_mode_disables_commands(self):
        self.e.w("config/control.json", {"version": 1, "auth": {"mode": "token"}})
        b = ctlbridge.CtlBridge(self.e.root)
        info = b.open(WS)
        self.assertEqual(info, {"enabled": False, "reason": "token_mode"})
        r = b.handle(WS, {"id": "x", "csrf": "", "kind": "command", "action": "day.start", "args": PAPER})
        self.assertEqual(r["code"], "E_AUTH")
        self.assertEqual(self.inbox(), [])

    def test_no_key_disables(self):
        e = _ctl.Env(self, with_key=False)
        b = ctlbridge.CtlBridge(e.root)
        self.assertEqual(b.open(WS), {"enabled": False, "reason": "no_key"})

    def test_wrong_csrf_rejected_and_audited(self):
        r = self.b.handle(WS, self.msg(csrf="x" * 32))
        self.assertEqual((r["ok"], r["code"]), (False, "E_CSRF"))
        r = self.b.handle(WS, self.msg(csrf=None))
        self.assertEqual(r["code"], "E_CSRF")
        self.assertEqual(self.inbox(), [])
        self.assertIn("E_CSRF", [c for _a, c in self.audit_codes()])

    def test_rate_limit_per_connection(self):
        n = int(self.b.web.conf()["max_post_per_min"])
        for _ in range(n):
            self.assertNotEqual(self.b.handle(WS, self.msg(action="risk.set", args={"level": 9}), now=1000.0)["code"], "E_RATE")
        self.assertEqual(self.b.handle(WS, self.msg(action="risk.set", args={"level": 9}), now=1001.0)["code"], "E_RATE")
        self.assertNotEqual(self.b.handle(WS, self.msg(action="risk.set", args={"level": 9}), now=1100.0)["code"], "E_RATE")      # минута прошла


class TestWhitelist(Base):
    def test_dangerous_and_unknown_actions_never_reach_the_queue(self):
        cases = [("day.start", {"mode": "live", "session": "day"}), ("risk.set", {"level": 5}), ("kill.set", {"flatten": True, "reason": "x"}),
                 ("stop.emergency", {"flatten": True}), ("mode.set", {"mode": "live"}), ("brain.use", {"profile": "ds-pro", "all": True}), ("platform.shutdown", {}),
                 ("llm.provider", {"op": "key_clear", "id": "x"}), ("exchange.set", {"op": "mode", "id": "bybit", "mode": "live"}), ("report.ack", {})]
        for action, args in cases:
            for kind in ("command", "challenge"):
                r = self.b.handle(WS, self.msg(kind=kind, action=action, args=args))
                self.assertEqual((r["ok"], r["code"]), (False, "E_ELEVATE"), (action, kind, r))
        for action in ("challenge", "no.such", "", None):
            r = self.b.handle(WS, self.msg(action=action))
            self.assertEqual((r["ok"], r["code"]), (False, "E_UNKNOWN"), action)
        self.assertEqual(self.inbox(), [])
        codes = self.audit_codes()
        self.assertIn(("day.start", "E_ELEVATE"), codes)
        self.assertIn(("risk.set", "E_ELEVATE"), codes)

    def test_live_start_explains_where_to_go(self):
        r = self.b.handle(WS, self.msg(args={"mode": "live", "session": "day"}))
        self.assertIn("старом дашборде", r["msg"])

    def test_bad_mode_is_arg_error(self):
        self.assertEqual(self.b.handle(WS, self.msg(args={"mode": "moon"}))["code"], "E_ARGS")
        self.assertEqual(self.b.handle(WS, self.msg(args={}))["code"], "E_ARGS")

    def test_allowed_args_are_filtered(self):
        a, code = ctlbridge.CtlBridge.allowed("day.start", {"mode": "paper", "session": "day", "team": "x", "nonce": "n", "phrases": ["p"], "accepted": ["W1"], "allow_amber": True, "minutes": 5})
        self.assertIsNone(code)
        self.assertEqual(a, {"mode": "paper", "session": "day", "accepted": ["W1"], "allow_amber": True})
        a, code = ctlbridge.CtlBridge.allowed("day.stop", {"mode": "hard", "flatten": True})
        self.assertEqual((a, code), ({"mode": "soft"}, None))


class TestPipeline(Base):
    def setUp(self):
        super().setUp()
        self.c = self.e.ctl(clock=time.time, reconf=lambda p, o, t: (True, "OK"))
        self.e.good_exec(profile="paper", now=time.time())
        self.stop = threading.Event()
        _ctl.pump(self.c, self.stop)
        self.addCleanup(self.stop.set)

    def test_challenge_goes_through_ctl_and_returns_checks_without_nonce(self):
        r = self.b.handle(WS, self.msg(kind="challenge"))
        self.assertIn(r["code"], ("OK", "E_GATE", "E_STATE"), r)
        self.assertIn("gates", r["data"])
        self.assertNotIn("nonce", json.dumps(r))
        self.assertTrue(all(g.get("id") for g in r["data"]["gates"]))

    def test_command_is_signed_and_submitted_with_only_whitelisted_fields(self):
        r = self.b.handle(WS, self.msg(args={"mode": "paper", "session": "day", "team": "evil", "nonce": "n", "accepted": []}))
        self.assertTrue(r["ok"], r)
        cid = r.get("accepted_id")
        self.assertRegex(cid, r"^K-\d{8}T\d{6}-[0-9a-f]{6}$")
        cmd = None
        if cid + ".json" in self.inbox():
            with open(os.path.join(self.e.root, "state", "control", "_inbox", cid + ".json"), encoding="utf-8") as f:
                cmd = json.load(f)
        res = None
        for _ in range(100):
            res = self.b.result_of(cid)
            if res:
                break
            time.sleep(0.05)
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], cid)
        if cmd:        # ctl мог уже забрать файл: тогда проверяем по аудиту, что подпись принята
            self.assertEqual((cmd["src"], cmd["action"], cmd["nonce"]), ("dash", "day.start", None))
            self.assertNotIn("team", cmd["args"])
            self.assertTrue(cmd.get("mac"))
        self.assertNotIn("E_MAC", [c for _a, c in self.audit_codes()])

    def test_day_stop_soft_only(self):
        r = self.b.handle(WS, self.msg(action="day.stop", args={"mode": "hard", "flatten": True}))
        self.assertTrue(r["ok"] and r.get("accepted_id"), r)


@unittest.skipUnless(HAVE_WS, "нужна библиотека websockets")
class TestOverSocket(Base, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _bootstrap.require_localhost()
        self.hub = None
        self.ready = asyncio.get_event_loop().create_future()
        self.task = asyncio.ensure_future(S.serve(port=0, root=self.e.root, ready=self._ready))
        await asyncio.wait_for(self.ready, 8)

    def _ready(self, hub, srv):
        self.hub = hub
        self.port = list(srv.sockets)[0].getsockname()[1]
        hub.port = self.port
        self.ready.set_result(True)

    async def asyncTearDown(self):
        self.task.cancel()
        try:
            await self.task
        except BaseException:
            pass

    async def recv_topic(self, ws, topic, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            m = json.loads(await asyncio.wait_for(ws.recv(), max(0.1, end - time.time())))
            if m["topic"] == topic:
                return m
        self.fail("нет сообщения " + topic)

    async def test_hello_gives_csrf_and_commands_are_checked(self):
        async with connect("ws://127.0.0.1:%d/ws" % self.port) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 6))
            ctl = hello["data"]["ctl"]
            self.assertTrue(ctl["enabled"] and ctl["csrf"])
            await ws.send(json.dumps({"op": "ctl", "id": "a1", "csrf": "bad", "kind": "command", "action": "day.start", "args": PAPER}))
            r = await self.recv_topic(ws, "ctl_result")
            self.assertEqual((r["data"]["id"], r["data"]["code"]), ("a1", "E_CSRF"))
            await ws.send(json.dumps({"op": "ctl", "id": "a2", "csrf": ctl["csrf"], "kind": "command", "action": "risk.set", "args": {"level": 9}}))
            r = await self.recv_topic(ws, "ctl_result")
            self.assertEqual((r["data"]["id"], r["data"]["code"]), ("a2", "E_ELEVATE"))
        self.assertEqual(self.inbox(), [])

    async def test_csrf_is_per_connection(self):
        async with connect("ws://127.0.0.1:%d/ws" % self.port) as a, connect("ws://127.0.0.1:%d/ws" % self.port) as b:
            ca = json.loads(await asyncio.wait_for(a.recv(), 6))["data"]["ctl"]["csrf"]
            cb = json.loads(await asyncio.wait_for(b.recv(), 6))["data"]["ctl"]["csrf"]
            self.assertNotEqual(ca, cb)
            await b.send(json.dumps({"op": "ctl", "id": "z", "csrf": ca, "kind": "command", "action": "day.start", "args": PAPER}))      # чужой токен соединения не годится
            r = await self.recv_topic(b, "ctl_result")
            self.assertEqual(r["data"]["code"], "E_CSRF")
        self.assertEqual(self.inbox(), [])

    async def test_ctl_topic_reports_stopped_ctl_without_volatile_fields(self):
        async with connect("ws://127.0.0.1:%d/ws" % self.port) as ws:
            m = await self.recv_topic(ws, "ctl")
            d = m["data"]
            self.assertFalse(d["alive"])
            self.assertTrue(d["configured"])
            self.assertNotIn("ts", d)
            self.assertNotIn("age_s", json.dumps(d))


if __name__ == "__main__":
    unittest.main()
