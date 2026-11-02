"""Мост команд управления нового дашборда (tb.dash2.ctlbridge): те же защиты, что у POST-пути старого, плюс CSRF соединения и белый список.
Временные TB_ROOT и TB_KEYS_DIR (поддельный ключ), фейковый ctl-цикл, никаких живых команд платформе, сети и биржи."""
import asyncio
import contextlib
import io
import json
import logging
import os
import threading
import time
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import _ctl
from tb.control import audit, auth, client
from tb.dash import keyweb
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
    extra_auth = {}

    def setUp(self):
        self.e = _ctl.Env(self, now=time.time())
        self.e.w("config/control.json", {"version": 1, "auth": dict({"mode": self.auth, "remember_days": 30}, **self.extra_auth)})
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
                 ("llm.provider", {"op": "key_clear", "id": "x"}), ("exchange.set", {"op": "mode", "id": "bybit", "mode": "live"}), ("report.ack", {}),
                 ("kill.clear", {})]           # снятие KILL только в расширенном режиме
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

    def test_live_start_explains_what_is_needed(self):
        r = self.b.handle(WS, self.msg(args={"mode": "live", "session": "day"}))
        self.assertEqual(r["code"], "E_ELEVATE")
        self.assertIn("dash2_full", r["msg"])                  # без расширенного режима live из этого дашборда невозможен
        self.assertIn("старом дашборде", r["msg"])             # путь через старый дашборд остаётся

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


MARK = "sk-FAKEDASH2KEY-MARKER0123456789zz"


class TestFullOff(Base):
    """Флаг выключен (нет ключа, false, не bool): поведение строго прежнее, опасное E_ELEVATE, ключ не пишется."""

    def test_flag_absent_or_not_true_keeps_everything_elevated(self):
        for val in (None, False, "true", 1, "yes"):
            self.e.w("config/control.json", {"version": 1, "auth": dict({"mode": "open_local"}, **({} if val is None else {"dash2_full": val}))})
            b = ctlbridge.CtlBridge(self.e.root)
            info = b.open(WS)
            self.assertEqual((info["enabled"], info["full"], info["modes"]), (True, False, ["day.start", "day.stop"]), val)
            self.assertEqual(b.sess[WS][1]["level"], "open")
            for action, args in (("risk.set", {"level": 5}), ("exchange.set", {"op": "activate", "id": "bybit"}), ("brain.role", {"op": "opts", "role": "news_reader", "budget": {"day_usd": 0.5}})):
                r = b.handle(WS, {"id": "x", "csrf": info["csrf"], "kind": "command", "action": action, "args": args})
                self.assertEqual((r["ok"], r["code"]), (False, "E_ELEVATE"), (val, action))
            r = b.llmkey(WS, {"id": "k", "csrf": info["csrf"], "provider": "deepseek", "value": MARK})
            self.assertEqual((r["ok"], r["code"]), (False, "E_ELEVATE"))
            self.assertNotIn(MARK, json.dumps(r))
        self.assertEqual(self.inbox(), [])
        self.assertFalse(os.path.exists(os.path.join(self.e.keys, "deepseek.env")))


class FullBase(Base):
    extra_auth = {"dash2_full": True}

    def queued(self):
        out = []
        for n in self.inbox():
            with open(os.path.join(self.e.root, "state", "control", "_inbox", n), encoding="utf-8") as f:
                out.append(json.load(f))
        return out

    def audit_rows(self):
        return audit.tail(self.e.root, 200)


class TestFull(FullBase):
    def test_session_is_full_and_hello_tells_the_ui(self):
        self.assertTrue(self.info["enabled"] and self.info["full"])
        self.assertEqual(self.b.sess[WS][1]["level"], "full")
        for a in ("risk.set", "exchange.set", "brain.role", "llmkey", "day.start", "report.ack"):
            self.assertIn(a, self.info["modes"])
        for a in ("platform.shutdown", "challenge"):
            self.assertNotIn(a, self.info["modes"])
        self.assertNotIn("key", json.dumps(self.info).replace("llmkey", ""))

    def test_dangerous_actions_reach_the_queue_with_only_validated_args(self):
        evil = {"nonce": "n", "applied": True, "team": "t", "foo": {"a": 1}}
        cases = [("risk.set", dict({"level": 5}, **evil)), ("exchange.set", dict({"op": "activate", "id": "bybit"}, **evil)),
                 ("brain.role", dict({"op": "opts", "role": "news_reader", "budget": {"day_usd": 0.5}}, **evil)), ("mode.set", dict({"mode": "live"}, **evil))]
        for action, args in cases:
            r = self.b.handle(WS, self.msg(action=action, args=args))
            self.assertTrue(r["ok"] and r.get("accepted_id"), (action, r))
        q = self.queued()
        self.assertEqual(len(q), len(cases))
        for cmd in q:
            self.assertEqual((cmd["src"], cmd["nonce"], cmd["phrase_hash"]), ("dash", None, None))
            self.assertTrue(cmd.get("mac"))
            self.assertNotIn("foo", cmd["args"])
            self.assertNotIn("nonce", cmd["args"])
            self.assertNotIn("team", cmd["args"])
        self.assertEqual(sorted((c["action"], c["args"].get("level")) for c in q if c["action"] == "risk.set"), [("risk.set", 5)])
        rows = [(r["action"], r["src"], r["code"]) for r in self.audit_rows()]
        self.assertIn(("risk.set", "dash2", "OK"), rows)

    def test_kill_clear_is_full_only_and_not_in_base(self):
        self.assertIn("kill.clear", self.info["modes"])
        self.assertIn("kill.clear", ctlbridge.FULL_ACTIONS)
        self.assertEqual(ctlbridge.CtlBridge.allowed_full("kill.clear", {"force": True, "applied": True}), ({}, None))
        self.assertEqual(ctlbridge.CtlBridge.allowed("kill.clear", {}), (None, "E_ELEVATE"))
        self.assertEqual(ctlbridge.CtlBridge.allowed_full("platform.shutdown", {}), (None, "E_ELEVATE"))

    def test_live_day_start_needs_the_owner_switch_even_in_full(self):
        for kind in ("challenge", "command"):
            r = self.b.handle(WS, self.msg(kind=kind, action="day.start", args={"mode": "live", "session": "day"}))
            self.assertEqual((r["ok"], r["code"]), (False, "E_DISABLED"), kind)
        self.assertEqual(self.queued(), [])
        self.assertIn(("day.start", "E_DISABLED"), self.audit_codes())
        self.assertEqual(ctlbridge.CtlBridge.allowed_full("platform.shutdown", {}), (None, "E_ELEVATE"))

    def test_allowed_full_filters_args(self):
        f = ctlbridge.CtlBridge.allowed_full
        self.assertEqual(f("risk.set", {"level": 3, "nonce": "x", "applied": True, "foo": 1}), ({"level": 3, "accepted": []}, None))
        a, code = f("kill.set", {"flatten": True, "reason": "стоп", "applied": True, "nonce": "n"})
        self.assertEqual((code, a["applied"], a["flatten"]), (None, False, True))
        self.assertEqual(f("day.stop", {"mode": "hard", "flatten": True}), ({"mode": "soft"}, None))
        self.assertEqual(f("risk.set", {"level": 99})[1], "E_ARGS")
        self.assertEqual(f("exchange.set", {"op": "activate", "id": "bybit", "x": 1})[0], {"op": "activate", "id": "bybit"})
        self.assertEqual(f("notify.set", {"master": True, "evil": 1})[1], "E_ARGS")
        self.assertEqual(f("platform.shutdown", {}), (None, "E_ELEVATE"))
        for bad in ("challenge", "no.such", "", None, "llm.key.set"):
            self.assertEqual(f(bad, {}), (None, "E_UNKNOWN"), bad)

    def test_shutdown_and_unknown_rejected_and_audited(self):
        for kind in ("command", "challenge"):
            r = self.b.handle(WS, self.msg(kind=kind, action="platform.shutdown", args={}))
            self.assertEqual((r["ok"], r["code"]), (False, "E_ELEVATE"))
            for action in ("no.such", "challenge", "", None):
                r = self.b.handle(WS, self.msg(kind=kind, action=action, args={}))
                self.assertEqual((r["ok"], r["code"]), (False, "E_UNKNOWN"), action)
        self.assertEqual(self.inbox(), [])
        rows = [(r["action"], r["src"], r["code"]) for r in self.audit_rows()]
        self.assertIn(("platform.shutdown", "dash2", "E_ELEVATE"), rows)

    def test_csrf_and_rate_limit_still_apply(self):
        for bad in ("x" * 32, None, "", 5):
            r = self.b.handle(WS, self.msg(action="risk.set", args={"level": 5}, csrf=bad))
            self.assertEqual(r["code"], "E_CSRF")
        self.assertEqual(self.inbox(), [])
        self.assertIn(("risk.set"[:12] and "ws:risk.set", "E_CSRF"), self.audit_codes())
        n = int(self.b.web.conf()["max_post_per_min"])
        for _ in range(n):
            self.assertNotEqual(self.b.handle(WS, self.msg(action="risk.set", args={"level": 9}), now=1000.0)["code"], "E_RATE")
        self.assertEqual(self.b.handle(WS, self.msg(action="risk.set", args={"level": 9}), now=1001.0)["code"], "E_RATE")
        self.assertEqual(self.b.llmkey(WS, {"id": "k", "csrf": self.info["csrf"], "provider": "deepseek", "value": MARK}, now=1002.0)["code"], "E_RATE")

    def test_client_cannot_claim_a_level_and_flag_off_drops_powers(self):
        r = self.b.handle(WS, self.msg(action="risk.set", args={"level": 5}, level="full", full=True))
        self.assertTrue(r["ok"])
        self.e.w("config/control.json", {"version": 1, "auth": {"mode": "open_local"}})      # флаг сняли при живом соединении
        r = self.b.handle(WS, self.msg(action="risk.set", args={"level": 5}))
        self.assertEqual((r["ok"], r["code"]), (False, "E_AUTH"))
        r = self.b.llmkey(WS, {"id": "k", "csrf": self.info["csrf"], "provider": "deepseek", "value": MARK})
        self.assertEqual(r["code"], "E_ELEVATE")
        self.assertEqual(len(self.queued()), 1)

    def test_bad_confirmation_fields_rejected(self):
        for over in ({"nonce": 5}, {"nonce": "x" * 300}, {"phrases": "строка"}, {"phrases": ["a", "b", "c", "d"]}, {"phrases": [1]}):
            r = self.b.handle(WS, self.msg(action="risk.set", args={"level": 5}, **over))
            self.assertEqual(r["code"], "E_BODY", over)
        self.assertEqual(self.inbox(), [])

    def test_unknown_nonce_is_rejected_by_cwebs_gate(self):
        r = self.b.handle(WS, self.msg(action="risk.set", args={"level": 5}, nonce="made-up-nonce"))
        self.assertEqual((r["ok"], r["code"]), (False, "E_NONCE"))
        self.assertEqual(self.inbox(), [])

    def test_dangerous_challenge_needs_correct_phrases(self):
        """Фейк ctl: challenge опасный, нужны фразы. Без верных фраз команда не уходит дальше; с верными уходит с хешем фраз."""
        label = self.b.sess[WS][1]["label"]
        args = client.validate_args("risk.set", {"level": 9})[2]
        now = 5000.0
        nonce = auth.make_nonce(label, client.args_digest("risk.set", args), now, None)
        phr = ["ПОДТВЕРЖДАЮ РИСК 9"]

        def fake_wait(cid, root=None, timeout=4.0, **kw):
            return {"status": "done", "code": "OK", "msg": "нужно подтверждение", "data": {"danger": True, "nonce": nonce, "phrases": phr, "warnings": [{"id": "W1"}],
                                                                                           "gates": [{"id": "g1", "ok": True}], "blocked": False, "expires_in": 60, "mac": "zzz"}}
        with mock.patch.object(client, "wait_result", fake_wait):
            ch = self.b.handle(WS, self.msg(kind="challenge", action="risk.set", args={"level": 9}), now=now)
        self.assertTrue(ch["ok"], ch)
        d = ch["data"]
        self.assertEqual((d["danger"], d["phrases"], d["nonce"], d["expires_in"]), (True, phr, nonce, 60))
        self.assertNotIn("mac", d)
        self.assertNotIn(nonce, json.dumps(self.audit_rows()))
        before = len(self.inbox())
        for bad in (None, [], ["не то"], ["ПОДТВЕРЖДАЮ РИСК 8"]):
            r = self.b.handle(WS, self.msg(action="risk.set", args={"level": 9}, nonce=nonce, phrases=bad), now=now + 5)
            self.assertEqual((r["ok"], r["code"]), (False, "E_PHRASE"), bad)
        self.assertEqual(len(self.inbox()), before)
        r = self.b.handle(WS, self.msg(action="risk.set", args={"level": 9}, nonce=nonce, phrases=["  подтверждаю  риск 9"]), now=now + 5)
        self.assertEqual(r["code"], "E_PHRASE")                       # регистр значим, пробелы схлопываются
        r = self.b.handle(WS, self.msg(action="risk.set", args={"level": 9}, nonce=nonce, phrases=["ПОДТВЕРЖДАЮ   РИСК 9"]), now=now + 5)
        self.assertTrue(r["ok"] and r.get("accepted_id"), r)
        self.assertEqual(len(self.inbox()), before + 1)
        cmd = [c for c in self.queued() if c["id"] == r["accepted_id"]][0]
        self.assertEqual((cmd["nonce"], cmd["phrase_hash"]), (nonce, client.protocol.phrase_hash(phr)))
        self.assertNotIn(nonce, json.dumps(self.audit_rows()))
        r2 = self.b.handle(WS, self.msg(action="risk.set", args={"level": 9}, nonce=nonce, phrases=phr), now=now + 6)
        self.assertEqual(r2["code"], "E_NONCE")                       # nonce одноразовый

    def test_nonce_is_not_transferable_between_connections(self):
        other = object()
        info2 = self.b.open(other)
        label = self.b.sess[WS][1]["label"]
        args = client.validate_args("risk.set", {"level": 9})[2]
        nonce = auth.make_nonce(label, client.args_digest("risk.set", args), 5000.0, None)
        r = self.b.handle(other, dict(self.msg(action="risk.set", args={"level": 9}, nonce=nonce), csrf=info2["csrf"]), now=5005.0)
        self.assertEqual(r["code"], "E_NONCE")
        self.assertEqual(self.inbox(), [])


def add_provider(e, pid="deepseek"):
    e.w("config/local/providers.json", {"providers": {pid: {"adapter": "openai_compat", "base_url": "https://x.example/v1", "enabled": False,
                                                           "key_file": pid + ".env", "key_env": pid.upper() + "_API_KEY",
                                                           "models": {"m": {"name": "m1", "price": {"in": 1, "out": 1}}}}}})


class TestFullLlmKey(FullBase):
    def setUp(self):
        super().setUp()
        add_provider(self.e)

    def keymsg(self, value=MARK, provider="deepseek", **over):
        m = {"id": "k1", "csrf": self.info["csrf"], "provider": provider, "value": value}
        m.update(over)
        return m

    def all_text(self):
        """Всё, что лежит в корне платформы (аудит, очередь, состояние, конфиги)."""
        out = []
        for d, _dirs, files in os.walk(self.e.root):
            for f in files:
                with open(os.path.join(d, f), encoding="utf-8", errors="replace") as fh:
                    out.append(fh.read())
        return "\n".join(out)

    def test_key_written_and_value_never_leaks(self):
        out, err = io.StringIO(), io.StringIO()
        lg = io.StringIO()
        h = logging.StreamHandler(lg)
        logging.getLogger().addHandler(h)
        old = logging.getLogger().level
        logging.getLogger().setLevel(logging.DEBUG)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                m = self.keymsg()
                r = self.b.llmkey(WS, m)
        finally:
            logging.getLogger().removeHandler(h)
            logging.getLogger().setLevel(old)
        self.assertEqual((r["ok"], r["code"], r["kind"], r["msg"]), (True, "OK", "llmkey", "ключ сохранён"), r)
        self.assertNotIn("value", m)                                   # значение вынуто из сообщения
        with open(os.path.join(self.e.keys, "deepseek.env"), encoding="utf-8") as f:
            self.assertIn(MARK, f.read())                              # единственное место, где оно есть (каталог ключей вне корня платформы)
        outputs = [json.dumps(r), out.getvalue(), err.getvalue(), lg.getvalue(), self.all_text(), json.dumps(self.info), repr(self.b.sess), json.dumps(self.audit_rows())]
        for o in outputs:
            self.assertNotIn(MARK, o)
        self.assertEqual(self.inbox(), [])
        rows = [(a["action"], a["src"], a["result"]) for a in self.audit_rows()]
        self.assertIn(("llm.key.set", "dash2", "ok"), rows)

    def test_exception_text_cannot_carry_the_value(self):
        def boom(root, environ, data, label, now):
            raise RuntimeError("не удалось записать " + data["value"])
        with mock.patch.object(keyweb, "handle", boom):
            r = self.b.llmkey(WS, self.keymsg())
        self.assertEqual((r["ok"], r["code"]), (False, "E_INTERNAL"))
        self.assertNotIn(MARK, json.dumps(r) + self.all_text())

    def test_bad_inputs_rejected_without_echo(self):
        bads = [self.keymsg(value="a b c d e f g h i j k l"), self.keymsg(value=MARK + "\n"), self.keymsg(value="x" * 600), self.keymsg(value=""), self.keymsg(value=12345),
                self.keymsg(value=MARK + "é"), self.keymsg(provider="../x"), self.keymsg(provider=None), self.keymsg(provider="Bad Id"), self.keymsg(csrf="nope")]
        for m in bads:
            r = self.b.llmkey(WS, m)
            self.assertFalse(r["ok"], m)
            self.assertIn(r["code"], ("E_ARGS", "E_CSRF"))
            self.assertNotIn(MARK, json.dumps(r))
        self.assertEqual(self.b.llmkey(WS, self.keymsg(provider="nope-provider"))["code"], "E_UNKNOWN")
        self.assertFalse(os.path.exists(os.path.join(self.e.keys, "deepseek.env")))
        self.assertNotIn(MARK, self.all_text())

    def test_exchange_key_files_not_accepted(self):
        self.e.w("config/local/providers.json", {"providers": {"evil": {"adapter": "openai_compat", "base_url": "https://x.example/v1", "enabled": False,
                                                                       "key_file": "bybit_mainnet_trade.env", "key_env": "BYBIT_API_SECRET",
                                                                       "models": {"m": {"name": "m1", "price": {"in": 1, "out": 1}}}}}})
        r = self.b.llmkey(WS, self.keymsg(provider="evil"))
        self.assertEqual((r["ok"], r["code"]), (False, "E_KEY"))
        self.assertNotIn(MARK, json.dumps(r))
        self.assertFalse(os.path.exists(os.path.join(self.e.keys, "bybit_mainnet_trade.env")))

    def test_no_session_no_key(self):
        r = self.b.llmkey(object(), self.keymsg())
        self.assertEqual(r["code"], "E_AUTH")


class TestFullPipeline(FullBase):
    def setUp(self):
        super().setUp()
        self.c = self.e.ctl(clock=time.time, reconf=lambda p, o, t: (True, "OK"))
        self.e.good_exec(profile="paper", now=time.time())
        self.stop = threading.Event()
        _ctl.pump(self.c, self.stop)
        self.addCleanup(self.stop.set)

    def final(self, cid):
        for _ in range(100):
            res = self.b.result_of(cid)
            if res:
                return res
            time.sleep(0.05)
        return None

    def test_real_ctl_rejects_dangerous_command_without_confirmation(self):
        r = self.b.handle(WS, self.msg(action="risk.set", args={"level": 9}))
        self.assertTrue(r["ok"], r)                                    # мост пропустил, решает ctl
        res = self.final(r["accepted_id"])
        self.assertIsNotNone(res)
        self.assertNotEqual(res["status"], "done", res)

    def test_challenge_for_dangerous_action_goes_to_ctl_and_returns_checks(self):
        r = self.b.handle(WS, self.msg(kind="challenge", action="risk.set", args={"level": 9}))
        self.assertIn("gates", r["data"], r)
        self.assertIn("expires_in", r["data"])


class TestFullKillClear(FullBase):
    """Снятие KILL через мост и настоящий ctl (фейковые файлы во временном корне): флаг, challenge с причиной, nonce, снятие, отказ при позиции без стопа."""

    def setUp(self):
        super().setUp()
        self.c = self.e.ctl(clock=time.time)
        self.e.good_exec(profile="paper", now=time.time())
        self.stop = threading.Event()
        _ctl.pump(self.c, self.stop)
        self.addCleanup(self.stop.set)
        self.flag(True)
        from tb.core import exec_cfg
        self.exec_cfg = exec_cfg
        exec_cfg.set_kill(False, "тест моста", self.e.root)

    def flag(self, on):
        self.e.w("config/control.json", {"version": 1, "auth": {"mode": "open_local", "dash2_full": True}, "allow_clear_kill_from_dash": on})

    def final(self, cid):
        for _ in range(100):
            res = self.b.result_of(cid)
            if res:
                return res
            time.sleep(0.05)
        return None

    def test_flag_off_is_disabled_and_kill_stays(self):
        self.flag(False)
        r = self.b.handle(WS, self.msg(kind="challenge", action="kill.clear", args={}))
        self.assertEqual((r["ok"], r["code"]), (False, "E_DISABLED"))
        self.assertIsNotNone(self.exec_cfg.kill_state(self.e.root))

    def test_position_without_stop_blocks_with_checks(self):
        self.e.w("runtime/exec_snapshot.json", {"ts_epoch": time.time(), "positions": [{"sym": "ETHUSDT", "qty": 1}], "orders": []})
        r = self.b.handle(WS, self.msg(kind="challenge", action="kill.clear", args={}))
        self.assertEqual((r["ok"], r["code"]), (False, "E_GATE"))
        self.assertIn("H2", [g["id"] for g in r["data"]["gates"] if not g["ok"]])
        self.assertNotIn("nonce", r["data"])

    def test_full_flow_clears_kill_and_audits_reason(self):
        ch = self.b.handle(WS, self.msg(kind="challenge", action="kill.clear", args={}))
        self.assertTrue(ch["ok"], ch)
        d = ch["data"]
        self.assertEqual((d["danger"], d["phrases"], d["kill"]["reason"]), (True, [], "тест моста"))
        time.sleep(d["min_delay"] + 0.3)
        r = self.b.handle(WS, self.msg(action="kill.clear", args={}, nonce=d["nonce"]))
        self.assertTrue(r["ok"] and r.get("accepted_id"), r)
        res = self.final(r["accepted_id"])
        self.assertEqual((res["status"], res["code"]), ("done", "OK"), res)
        self.assertIsNone(self.exec_cfg.kill_state(self.e.root))
        rows = [x for x in audit.tail(self.e.root, 100) if x["action"] == "kill.clear" and x["result"] == "ok"]
        self.assertTrue(any("тест моста" in x.get("note", "") and x.get("mode") == "paper" for x in rows), rows)

    def test_no_nonce_is_refused_by_ctl(self):
        r = self.b.handle(WS, self.msg(action="kill.clear", args={}))
        res = self.final(r["accepted_id"]) if r.get("accepted_id") else r
        self.assertEqual(res["code"], "E_NONCE")
        self.assertIsNotNone(self.exec_cfg.kill_state(self.e.root))


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


@unittest.skipUnless(HAVE_WS, "нужна библиотека websockets")
class TestOverSocketFull(FullBase, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        add_provider(self.e)
        await TestOverSocket.asyncSetUp(self)

    _ready = TestOverSocket._ready
    asyncTearDown = TestOverSocket.asyncTearDown
    recv_topic = TestOverSocket.recv_topic

    async def test_hello_full_and_llmkey_route(self):
        async with connect("ws://127.0.0.1:%d/ws" % self.port) as ws:
            seen = []
            raw = await asyncio.wait_for(ws.recv(), 6)
            seen.append(raw)
            ctl = json.loads(raw)["data"]["ctl"]
            self.assertTrue(ctl["enabled"] and ctl["full"] and "llmkey" in ctl["modes"] and "platform.shutdown" not in ctl["modes"])
            await ws.send(json.dumps({"op": "llmkey", "id": "k9", "csrf": ctl["csrf"], "provider": "deepseek", "value": MARK}))
            end = time.time() + 6
            res = None
            while time.time() < end and res is None:
                raw = await asyncio.wait_for(ws.recv(), max(0.1, end - time.time()))
                seen.append(raw)
                m = json.loads(raw)
                if m["topic"] == "ctl_result":
                    res = m["data"]
            self.assertIsNotNone(res)
            self.assertEqual((res["id"], res["kind"], res["ok"]), ("k9", "llmkey", True), res)
            await ws.send(json.dumps({"op": "ctl", "id": "s1", "csrf": ctl["csrf"], "kind": "command", "action": "platform.shutdown", "args": {}}))
            r = await self.recv_topic(ws, "ctl_result")
            self.assertEqual(r["data"]["code"], "E_ELEVATE")
        self.assertFalse(any(MARK in s for s in seen))


if __name__ == "__main__":
    unittest.main()
