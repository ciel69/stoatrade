"""Тема `day` и операция `day_report` нового дашборда (tb.dash2.topics.day, day_report) и доступность day.resume, carry.decide, report.ack через мост.
Временный корень, без платформы, сети и живых команд."""
import asyncio
import json
import os
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb.dash2 import ctlbridge, server as S, topics

try:
    import websockets  # noqa: F401
    from websockets.asyncio.client import connect
    HAVE_WS = True
except ImportError:
    HAVE_WS = False


def w(root, rel, obj):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        if isinstance(obj, str):
            f.write(obj)
        else:
            json.dump(obj, f)


DAY = {"schema": "tb.day/1", "day_id": "D-20261102-1", "state": "closing", "mode": "paper", "agent": "brain", "started": 1000.0, "stopped": None, "needs_resume": False,
       "api_key": "SECRET-KEY", "runner": {"pid": 1, "hb": 5}, "path": "C:/secret/dir",
       "steps": [{"name": "S1", "phase": "start", "ok": True, "dur_s": 1.2, "code": None, "detail": {"amber": True, "token": "SECRET-T"}, "ts": 1},
                 {"name": "S3", "phase": "start", "ok": False, "dur_s": 0.4, "code": "E_GATE", "detail": {"why": "расходятся", "blocking": [{"id": "H1", "msg": "нет связи", "action": "проверьте сеть", "sev": "red"}]}},
                 {"name": "C6", "phase": "stop", "ok": None, "dur_s": None, "code": None, "detail": None}],
       "owner": {"questions": [{"id": "Q1", "item": "pos:PL-1", "plan": "PL-1", "text": "Переносить BTC?", "options": ["carry", "close"], "answer": None, "answered_by": None, "asked_ts": 5},
                               {"id": "Q2", "item": "pos:PL-2", "plan": "PL-2", "text": "Ещё?", "options": ["carry", "close"], "answer": "close", "answered_by": "owner"}],
                 "deadline_ts": 5000.0},
       "closing": {"source": "owner", "failed": {"items": ["pos:PL-9"], "manual": [], "reason": "not_confirmed"}, "job": {"id": "J", "deadline": 1}, "cancel_cmds": {"PL-3": {"cmd": "x"}}},
       "dayplan": {"source": "agent", "regime": "range", "bias": "neutral", "session": {"type": "position"}, "max_new_plans": 4},
       "preflight": {"red": [], "amber": ["H4"]}, "facts": {"big": "x" * 5000}, "report": {"ready": False, "acked": False},
       "carry_in": [{"plan": "PL-0", "sym": "ETHUSDT", "side": "long", "stop": 3000.0, "risk_usdt": 0.4, "alive": True, "in_plans": True}],
       "abort": None}


class DayTopic(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbdash2d_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def put_day(self, d=DAY):
        w(self.root, "state/days/%s/day.json" % d["day_id"], d)

    def test_registered_with_interval_and_mode_independent(self):
        self.assertIn("day", topics.TOPICS)
        self.assertNotIn("day", topics.MODE_TOPICS)
        self.assertTrue(0 < topics.INTERVAL["day"] <= 10)

    def test_empty_root_gives_no_days_and_no_crash(self):
        t = topics.build("day", self.root, 1000.0)
        self.assertEqual((t["banner"], t["current"], t["last"], t["days"]), (None, None, None, []))

    def test_current_day_whitelist_without_secrets_and_paths(self):
        self.put_day()
        w(self.root, "state/days/D-20261102-1/checklist.json", {"rows": [{"item": "pos:PL-1", "kind": "pos", "plan": "PL-1", "sym": "BTCUSDT", "side": "long", "stype": "intraday",
                                                                         "rule": "A3", "decision": "ask_owner", "codes": ["K2_RISK"], "facts": {"huge": 1}, "_n": 5}]})
        w(self.root, "state/days/D-20261102-1/carry.json", {"items": [{"plan": "PL-1", "sym": "BTCUSDT", "side": "long", "stop": 60000, "risk_usdt": 0.5, "why": "тезис живой", "decided_by": "owner",
                                                                       "view": {"x": 1}, "basis": ["a"]}]})
        w(self.root, "state/control/day_banner.json", {"level": "warn", "text": "Жду решения по переносу", "day_id": "D-20261102-1", "day_report": None})
        t = topics.build("day", self.root, 1000.0)
        c = t["current"]
        self.assertEqual((c["day_id"], c["state"], c["mode"], c["needs_resume"]), ("D-20261102-1", "closing", "paper", False))
        self.assertEqual([(s["name"], s["ok"]) for s in c["steps"]], [("S1", True), ("S3", False), ("C6", None)])
        self.assertTrue(c["steps"][0]["amber"])
        self.assertEqual(c["steps"][1]["blocking"], [{"id": "H1", "msg": "нет связи", "fix": "проверьте сеть"}])
        self.assertEqual([(q["plan"], q["answer"]) for q in c["questions"]], [("PL-1", None), ("PL-2", "close")])
        self.assertEqual(c["deadline_ts"], 5000.0)
        self.assertEqual(c["closing"]["failed"], {"items": ["pos:PL-9"], "manual": [], "reason": "not_confirmed"})
        self.assertEqual(c["dayplan"], {"source": "agent", "regime": "range", "bias": "neutral", "max_new_plans": 4, "session": "position"})
        self.assertEqual(c["checklist"][0]["codes"], ["K2_RISK"])
        self.assertNotIn("facts", c["checklist"][0])
        self.assertEqual(c["carry"][0]["why"], "тезис живой")
        self.assertNotIn("view", c["carry"][0])
        self.assertEqual((c["carry_in"][0]["plan"], c["carry_in"][0]["alive"]), ("PL-0", True))
        self.assertEqual(t["banner"]["level"], "warn")
        text = repr(t)
        for bad in ("SECRET", "api_key", "C:/secret", "runner", "huge", "xxxxx", "job"):
            self.assertNotIn(bad, text)

    def test_list_newest_first_with_report_flags_and_no_volatile_fields(self):
        for n, (state, net) in enumerate([("stopped", 1.5), ("stopped", -0.3), ("running", None)], 1):
            d = dict(DAY, day_id="D-20261101-%d" % n, state=state)
            d["report"] = {"ready": net is not None, "acked": n == 1, "verdict": "ok", "net": net, "carried_n": 1, "questions_n": 0}
            self.put_day(d)
        t = topics.build("day", self.root, 1000.0)
        self.assertEqual([x["day_id"] for x in t["days"]], ["D-20261101-3", "D-20261101-2", "D-20261101-1"])
        self.assertEqual((t["days"][1]["report_ready"], t["days"][1]["net"], t["days"][2]["acked"]), (True, -0.3, True))
        self.assertEqual(t["current"]["day_id"], "D-20261101-3")
        self.assertEqual(topics.build("day", self.root, 1000.0), topics.build("day", self.root, 2000.0))

    def test_broken_day_file_is_skipped(self):
        w(self.root, "state/days/D-20261102-1/day.json", "{не json")
        self.put_day(dict(DAY, day_id="D-20261102-2", state="stopped"))
        t = topics.build("day", self.root, 1000.0)
        self.assertEqual([x["day_id"] for x in t["days"]], ["D-20261102-2"])

    def test_abort_is_shown_without_raw_detail(self):
        d = dict(DAY, state="stopped", abort={"step": "S1", "code": "E_GATE", "detail": {"kind": "hard", "blocking": [{"id": "H2", "msg": "KILL активен", "action": "снимите KILL", "ok": False}], "token": "S"}})
        self.put_day(d)
        a = topics.build("day", self.root, 1000.0)["current"]["abort"]
        self.assertEqual((a["step"], a["code"]), ("S1", "E_GATE"))
        self.assertEqual(a["blocking"][0]["msg"], "KILL активен")
        self.assertNotIn("kind", repr(a))

    def test_report_op_whitelist_and_bad_ids(self):
        self.put_day(dict(DAY, state="stopped"))
        rep = {"day_id": "D-20261102-1", "kind": "stop", "mode": "paper", "verdict": "attention", "pnl": {"net": 0.42, "fees": 0.05, "funding": -0.01, "realized_gross": 0.46, "r_sum": 1.5},
               "trades_n": 3, "open_trades_n": 0, "cost": {"usd": 0.1234, "calls": 12, "tokens_in": 9999, "by_profile": {"x": 1}}, "carried": [{"plan": "PL-1", "sym": "BTCUSDT", "side": "long", "stop": 1.0,
               "risk_usdt": 0.2, "why": "ок", "decided_by": "owner", "view": {"k": 1}}], "open_questions": [{"id": "Q1", "text": "вопрос", "options": []}], "trades": [{"secret_note": "x"}],
               "session": [{"id": "S-1", "type": "day", "pnl": 0.4, "trades": 3, "budget_used": 0.1}], "api_key": "SECRET", "narrative": "итог ясен"}
        w(self.root, "state/days/D-20261102-1/report.json", rep)
        w(self.root, "state/days/D-20261102-1/report.md", "# Итог\n<script>alert(1)</script>\n")
        r = topics.day_report(self.root, "D-20261102-1")
        self.assertEqual((r["ok"], r["id"]), (True, "D-20261102-1"))
        d = r["data"]
        self.assertEqual((d["pnl"]["net"], d["trades_n"], d["cost"], d["verdict"]), (0.42, 3, {"usd": 0.1234, "calls": 12}, "attention"))
        self.assertIn("<script>", d["md"])                       # текст отдаётся как есть: клиент выводит его без HTML
        self.assertNotIn("SECRET", repr(r))
        self.assertNotIn("secret_note", repr(r))
        self.assertNotIn("tokens_in", repr(r))
        for bad in ("../../etc/passwd", "D-1", "", None, 5, "D-20261102-1/../x"):
            self.assertFalse(topics.day_report(self.root, bad)["ok"], bad)
        self.assertEqual(topics.day_report(self.root, "D-20269999-1"), {"id": "D-20269999-1", "ok": False, "error": "no report"})

    def test_report_with_broken_json_or_missing_md(self):
        w(self.root, "state/days/D-20261102-1/report.json", "{не json")
        self.assertFalse(topics.day_report(self.root, "D-20261102-1")["ok"])
        w(self.root, "state/days/D-20261102-3/report.json", {"day_id": "D-20261102-3", "pnl": "мусор", "cost": [], "carried": "x"})
        r = topics.day_report(self.root, "D-20261102-3")
        self.assertTrue(r["ok"])
        self.assertEqual((r["data"]["md"], r["data"]["carried"], r["data"]["pnl"]["net"]), ("", [], None))


class DayActions(unittest.TestCase):
    def test_actions_are_full_only_with_strict_args(self):
        f = ctlbridge.CtlBridge.allowed_full
        self.assertEqual(f("day.resume", {"x": 1}), ({}, None))
        self.assertEqual(f("carry.decide", {"plan": "PL-1", "decision": "carry", "evil": 1})[1], None)
        self.assertEqual(f("carry.decide", {"plan": "PL-1", "decision": "hold"})[1], "E_ARGS")
        self.assertEqual(f("carry.decide", {"decision": "close"})[1], "E_ARGS")
        self.assertEqual(f("report.ack", {"day_id": "D-20261102-1"})[0], {"day_id": "D-20261102-1"})
        self.assertEqual(f("report.ack", {})[1], "E_ARGS")
        for a in ("day.resume", "carry.decide", "report.ack"):
            self.assertEqual(ctlbridge.CtlBridge.allowed(a, {"plan": "P", "decision": "close", "day_id": "D-1"})[1], "E_ELEVATE")      # без расширенного режима недоступны


@unittest.skipUnless(HAVE_WS, "нет websockets")
class DayServer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _bootstrap.require_localhost()
        self.root = tempfile.mkdtemp(prefix="tbdash2ds_")
        self.addCleanup(shutil.rmtree, self.root, True)
        w(self.root, "state/days/D-20261102-1/report.json", {"day_id": "D-20261102-1", "verdict": "ok", "pnl": {"net": 1.0}})
        self.ready = asyncio.get_event_loop().create_future()
        self.task = asyncio.ensure_future(S.serve(port=0, root=self.root, ready=self._ready))
        await asyncio.wait_for(self.ready, 5)

    def _ready(self, hub, srv):
        self.port = list(srv.sockets)[0].getsockname()[1]
        hub.port = self.port
        self.ready.set_result(True)

    async def asyncTearDown(self):
        self.task.cancel()
        try:
            await self.task
        except BaseException:
            pass

    async def test_day_report_answers_only_asker_and_rejects_bad_id(self):
        async with connect("ws://127.0.0.1:%d/ws" % self.port) as a, connect("ws://127.0.0.1:%d/ws" % self.port) as b:
            async def reply(ws):
                for _ in range(60):
                    m = json.loads(await asyncio.wait_for(ws.recv(), 3))
                    if m["topic"] == "day_report_detail":
                        return m
                self.fail("ответа day_report_detail нет")
            await a.send(json.dumps({"op": "day_report", "id": "../../x"}))
            self.assertFalse((await reply(a))["data"]["ok"])
            await a.send(json.dumps({"op": "day_report", "id": "D-20261102-1"}))
            m = await reply(a)
            self.assertEqual((m["data"]["ok"], m["data"]["data"]["pnl"]["net"]), (True, 1.0))
            seen = []
            try:
                while True:
                    seen.append(json.loads(await asyncio.wait_for(b.recv(), 0.5))["topic"])
            except asyncio.TimeoutError:
                pass
            self.assertNotIn("day_report_detail", seen)
            self.assertIn("day", seen)                           # тема day пришла в снимке при подключении


if __name__ == "__main__":
    unittest.main()
