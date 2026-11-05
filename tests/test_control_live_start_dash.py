"""Запуск торгового дня в LIVE из нового дашборда (решение владельца 2026-11-02): мост dash2 + настоящий ctl на фейках во временном корне.
Контракт: выключатель control.allow_live_start_from_dash (по умолчанию false, E_DISABLED, строго true); без расширенного режима E_ELEVATE; в расширенном режиме путь тот же,
что у старого дашборда: challenge -> nonce -> фразы live (дата UTC и уровень риска) -> ctl сам проверяет ворота H1-H8, предупреждения W (accepted), кулдауны. Ничего не ослаблено.
Живые команды платформе, сеть и биржа не используются; время сдвигается общими часами моста и ctl."""
import json
import os
import threading
import time
import unittest

import _bootstrap  # noqa: F401
import _ctl
from tb.control import audit, ctlstate, protocol
from tb.core import exec_cfg
from tb.dash import ctlweb
from tb.dash2 import ctlbridge

WS = object()
LIVE = {"mode": "live", "session": "day"}


class Base(unittest.TestCase):
    flag = True
    full = True
    profile = "paper"                # профиль исполнителя до старта (в trade ворота H2-H4 проверяются сразу, в paper они откладываются на «после переключения»)

    def setUp(self):
        self.off = 0.0
        self.clk = lambda: time.time() + self.off
        self.e = _ctl.Env(self, now=time.time())
        self.e.good_live()
        self.reconfs = []
        self.conf(self.flag, self.full)
        self.b = ctlbridge.CtlBridge(self.e.root, clock=self.clk)
        self.info = self.b.open(WS)
        self.c = self.e.ctl(clock=self.clk, reconf=self.fake_reconf)
        self.fresh()
        self.stop = threading.Event()
        _ctl.pump(self.c, self.stop)
        _ctl.patient_ctl_wait(self, self.stop)
        self.addCleanup(self.stop.set)

    def fake_reconf(self, profile, observe, timeout):
        self.reconfs.append((profile, observe))
        self.e.good_exec(profile=profile, observe=observe, now=self.clk())
        return True, "OK"

    def conf(self, flag, full=True, **extra):
        c = {"version": 1, "auth": {"mode": "open_local", "dash2_full": full}}
        if flag is not None:
            c["allow_live_start_from_dash"] = flag
        c.update(extra)
        self.cur = (flag, full)
        self.e.w("config/control.json", c)

    def fresh(self):
        """Свежие файлы исполнителя и мозга на текущий момент (часы сдвигаются; гейты H2/H3/H6 чувствительны к возрасту). Исполнитель до старта в paper."""
        self.e.good_live(now=self.clk())               # good_live пишет config/control.json в режиме token: возвращаем настройки теста
        self.conf(*self.cur)
        self.e.good_exec(profile=self.profile, now=self.clk())

    def adv(self, s):
        self.off += s

    def msg(self, kind, args=None, **over):
        m = {"id": "c%d" % int(self.clk() * 1000 % 10 ** 9), "csrf": self.info.get("csrf"), "kind": kind, "action": "day.start", "args": dict(LIVE if args is None else args)}
        m.update(over)
        return m

    def ch(self, args=None):
        return self.b.handle(WS, self.msg("challenge", args))

    def cmd(self, args=None, **over):
        return self.b.handle(WS, self.msg("command", args, **over))

    def final(self, cid):
        for _ in range(200):
            res = self.b.result_of(cid)
            if res:
                # ctl пишет результат в outbox раньше строки аудита (handlers.finish): дождаться и её, иначе под нагрузкой rows() её ещё не видит
                for _ in range(100):
                    if any(x.get("id") == cid for x in audit.tail(self.e.root, 300)):
                        break
                    time.sleep(0.05)
                return res
            time.sleep(0.05)
        return None

    def state(self):
        return ctlstate.platform_state(self.e.root)["state"]

    def inbox(self):
        d = os.path.join(self.e.root, "state", "control", "_inbox")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def rows(self, action="day.start"):
        return [r for r in audit.tail(self.e.root, 300) if r["action"] == action]

    def full_flow(self, args=None, phrases=None):
        """challenge -> ожидание -> command с nonce и фразами (по умолчанию из ответа challenge, как подставляет интерфейс). -> (challenge, ответ моста, итог ctl или None)."""
        ch = self.ch(args)
        self.assertTrue(ch["ok"], ch)
        d = ch["data"]
        self.adv(5)
        self.fresh()
        a = dict(LIVE if args is None else args, accepted=[w["id"] for w in d.get("warnings") or []])
        r = self.cmd(a, nonce=d["nonce"], phrases=d["phrases"] if phrases is None else phrases)
        res = self.final(r["accepted_id"]) if r.get("accepted_id") else None
        return ch, r, res


class TestSwitchOff(Base):
    flag = False

    def test_default_is_off_in_protocol(self):
        self.assertIs(protocol.DEFAULTS["allow_live_start_from_dash"], False)
        with open(os.path.join(os.path.dirname(__file__), "..", "config", "control.json"), encoding="utf-8") as f:
            self.assertIsInstance(json.load(f).get("allow_live_start_from_dash"), bool)

    def test_disabled_for_challenge_and_command_nothing_queued(self):
        for kind in ("challenge", "command"):
            r = self.b.handle(WS, self.msg(kind))
            self.assertEqual((r["ok"], r["code"]), (False, "E_DISABLED"), kind)
            self.assertIn("allow_live_start_from_dash", r["msg"])
        self.assertEqual(self.inbox(), [])
        self.assertEqual(self.state(), "stopped")
        self.assertIn("E_DISABLED", [x["code"] for x in self.rows()])

    def test_absent_and_non_true_values_do_not_enable(self):
        for v in (None, False, "true", 1, "yes", [], {}):
            self.conf(v)
            r = self.ch()
            self.assertEqual(r["code"], "E_DISABLED", v)
        self.assertEqual(self.inbox(), [])

    def test_flag_is_reread_each_time(self):
        self.conf(True)
        self.assertEqual(self.ch()["code"], "OK")
        self.conf(False)
        self.assertEqual(self.ch()["code"], "E_DISABLED")

    def test_paper_start_does_not_depend_on_the_switch(self):
        r = self.b.handle(WS, self.msg("challenge", {"mode": "paper", "session": "day"}))
        self.assertNotIn(r["code"], ("E_DISABLED", "E_ELEVATE"), r)


class TestNeedsFull(Base):
    full = False

    def test_switch_on_without_full_is_elevate(self):
        self.assertFalse(self.info["full"])
        for kind in ("challenge", "command"):
            r = self.b.handle(WS, self.msg(kind))
            self.assertEqual((r["ok"], r["code"]), (False, "E_ELEVATE"), kind)
            self.assertIn("dash2_full", r["msg"])
        self.assertEqual(self.inbox(), [])
        self.assertIn("E_ELEVATE", [x["code"] for x in self.rows()])

    def test_client_cannot_claim_full(self):
        r = self.b.handle(WS, self.msg("command", level="full", full=True, nonce="x", phrases=["x"]))
        self.assertEqual(r["code"], "E_ELEVATE")
        self.assertEqual(self.inbox(), [])

    def test_flag_off_and_full_off_is_elevate_not_disabled(self):
        self.conf(False, False)
        self.assertEqual(self.ch()["code"], "E_ELEVATE")


class TestFlow(Base):
    def test_hello_lists_day_start_in_full_mode(self):
        self.assertTrue(self.info["full"])
        self.assertIn("day.start", self.info["modes"])
        self.assertNotIn("platform.shutdown", self.info["modes"])

    def test_challenge_gives_gates_h1_h8_nonce_and_date_bound_phrase(self):
        ch = self.ch()
        d = ch["data"]
        self.assertTrue(ch["ok"], ch)
        self.assertEqual([g["id"] for g in d["gates"]], ["H%d" % i for i in range(1, 9)])
        self.assertTrue(all(g["ok"] for g in d["gates"]) and not d["blocked"] and d["danger"])
        self.assertTrue(d["nonce"])
        day = time.strftime("%Y-%m-%d", time.gmtime(self.clk()))
        self.assertEqual(d["phrases"], ["ТОРГУЮ LIVE %s УРОВЕНЬ 3" % day])
        self.assertEqual(self.state(), "stopped")                       # challenge ничего не запускает

    def test_no_nonce_never_starts_the_day(self):
        r = self.cmd(dict(LIVE, accepted=[]))
        res = self.final(r["accepted_id"]) if r.get("accepted_id") else r
        self.assertEqual(res["code"], "E_NONCE")
        self.assertEqual(self.state(), "stopped")
        self.assertEqual(self.reconfs, [])

    def test_wrong_or_missing_phrase_keeps_day_stopped(self):
        d = self.ch()["data"]
        self.adv(5)
        self.fresh()
        for ph in (None, [], ["ТОРГУЮ LIVE 2020-01-01 УРОВЕНЬ 3"], ["торгую live"], ["ТОРГУЮ LIVE %s УРОВЕНЬ 3" % time.strftime("%Y-%m-%d", time.gmtime(self.clk() - 86400))]):
            r = self.cmd(dict(LIVE, accepted=[]), nonce=d["nonce"], phrases=ph)
            self.assertEqual((r["ok"], r["code"]), (False, "E_PHRASE"), ph)
        self.assertEqual(self.inbox(), [])
        self.assertEqual(self.state(), "stopped")

    def test_phrase_of_another_day_is_refused_by_ctl_too(self):
        """Даже если мост пропустил, ctl сверяет хеш фраз со своей датой: фраза «вчерашнего» дня не проходит после смены суток между challenge и командой."""
        d = self.ch()["data"]
        self.adv(86400 + 5)                                              # сутки UTC сменились
        self.fresh()
        r = self.cmd(dict(LIVE, accepted=[]), nonce=d["nonce"], phrases=d["phrases"])
        self.assertFalse(self.state() == "running")
        res = self.final(r["accepted_id"]) if r.get("accepted_id") else r
        self.assertIn(res["code"], ("E_NONCE", "E_PHRASE"))

    def test_nonce_from_another_args_set_is_refused(self):
        d = self.ch()["data"]
        self.adv(5)
        self.fresh()
        r = self.cmd({"mode": "live", "session": "scalp", "accepted": []}, nonce=d["nonce"], phrases=d["phrases"])
        self.assertEqual((r["ok"], r["code"]), (False, "E_NONCE"))
        self.assertEqual(self.state(), "stopped")

    def test_success_switches_exec_to_trade_and_audits_live(self):
        ch, r, res = self.full_flow()
        self.assertEqual((res["status"], res["code"]), ("done", "OK"), res)
        self.assertEqual(self.reconfs, [("trade", False)])
        st = ctlstate.platform_state(self.e.root)
        self.assertEqual((st["state"], st["mode"], st["trading"]), ("running", "live", True))
        ok = [x for x in self.rows() if x["code"] == "OK" and x["result"] == "ok"]
        self.assertTrue(ok and ok[-1]["mode"] == "live", self.rows())
        self.assertTrue(audit.verify(self.e.root)["ok"])
        self.assertIn(("dash2", "OK"), [(x["src"], x["code"]) for x in self.rows()])       # запись моста: источник dash2
        self.assertEqual(len(ctlstate.live_starts(self.e.root, self.clk())["starts"]), 1)

    def test_warnings_must_be_accepted_and_land_in_audit(self):
        os.remove(self.e.p("state", "trades.jsonl"))                     # W1 и W9
        ch = self.ch()
        d = ch["data"]
        ids = sorted(w["id"] for w in d["warnings"])
        self.assertEqual(ids, ["W1", "W9"])
        self.assertEqual(len(d["phrases"]), 2)
        self.adv(5)
        self.fresh()
        os.remove(self.e.p("state", "trades.jsonl"))
        r = self.cmd(dict(LIVE, accepted=[]), nonce=d["nonce"], phrases=d["phrases"])
        res = self.final(r["accepted_id"])
        self.assertEqual(res["code"], "E_WARN")                          # без accepted не стартует
        self.assertEqual(self.state(), "stopped")
        d = self.ch()["data"]
        self.adv(5)
        self.fresh()
        os.remove(self.e.p("state", "trades.jsonl"))
        r = self.cmd(dict(LIVE, accepted=["W1"]), nonce=d["nonce"], phrases=d["phrases"])
        self.assertEqual(self.final(r["accepted_id"])["code"], "E_WARN_CHANGED")      # приняты не все
        self.assertEqual(self.state(), "stopped")
        d = self.ch()["data"]
        self.adv(5)
        self.fresh()
        os.remove(self.e.p("state", "trades.jsonl"))
        r = self.cmd(dict(LIVE, accepted=ids), nonce=d["nonce"], phrases=d["phrases"])
        res = self.final(r["accepted_id"])
        self.assertEqual((res["status"], res["code"]), ("done", "OK"), res)
        # ctl пишет ответ в outbox раньше строки аудита (handlers.finish): под параллельной нагрузкой ждём строку
        deadline, ok_rows = time.monotonic() + 15.0, []
        while time.monotonic() < deadline:
            ok_rows = [x for x in self.rows() if x["code"] == "OK" and x["result"] == "ok" and x["src"] == "dash"]
            if ok_rows:
                break
            time.sleep(0.05)
        self.assertTrue(ok_rows, "строка аудита day.start OK не появилась за 15 с")
        row = ok_rows[-1]
        self.assertEqual(row["mode"], "live")
        self.assertEqual(sorted(w["id"] for w in row["warnings_accepted"]), ids)
        self.assertEqual(sorted(w["id"] for w in ctlstate.platform_state(self.e.root)["live_waivers"]), ids)

    def test_second_start_while_running_is_state_error(self):
        self.full_flow()
        self.adv(80)
        self.fresh()
        self.e.good_exec(profile="trade", now=self.clk())
        r = self.ch()
        self.assertEqual((r["ok"], r["code"]), (False, "E_STATE"))


class TestHardGates(Base):
    profile = "trade"
    """Каждое из H1-H8 как единственная причина: challenge E_GATE без nonce, а провал после challenge (до команды) тоже E_GATE, день не стартует."""

    def h1(self):
        exec_cfg.set_kill(False, "тест", self.e.root)

    def h2(self):
        snap = self.e.r("runtime/exec_snapshot.json")
        snap["positions"] = [{"sym": "SOLUSDT", "side": "long", "size": 1, "sl": 0}]
        self.e.w("runtime/exec_snapshot.json", snap)

    def h3(self):
        self.e.good_exec(profile=self.profile, now=self.clk(), ws={"public": {"up": True}, "private": {"up": False}})

    def h4(self):
        self.e.good_exec(profile=self.profile, now=self.clk(), diffs=2)

    def h5(self):
        self.e.w("state/portfolio.json", {"venues": {"bybit": {"equity": 30.0, "free": 30.0}}, "hwm": 45.0})

    def h6(self):
        os.remove(self.e.p("runtime", "platform", "brain.hb.json"))

    def h7(self):
        ag = self.e.r("config/agents.json")
        ag["agents"]["api"]["limits"]["allow_add"] = True
        self.e.w("config/agents.json", ag)

    def h8(self):
        for i in range(3):
            ctlstate.note_live_start(self.e.root, self.clk() - 1000 * (i + 1))

    def test_each_gate_blocks_challenge(self):
        for gid in ("h1", "h2", "h3", "h4", "h5", "h6", "h7", "h8"):
            with self.subTest(gate=gid):
                self.setUp_clean()
                getattr(self, gid)()
                r = self.ch()
                self.assertEqual((r["ok"], r["code"]), (False, "E_GATE"), (gid, r))
                self.assertIn(gid.upper(), [g["id"] for g in r["data"]["gates"] if not g["ok"]])
                self.assertNotIn("nonce", r["data"])
                self.assertEqual(self.state(), "stopped")
                self.assertEqual(self.reconfs, [])

    def test_each_gate_blocks_command_after_valid_challenge(self):
        for gid in ("h1", "h2", "h3", "h4", "h5", "h6", "h7", "h8"):
            with self.subTest(gate=gid):
                self.setUp_clean()
                ch = self.ch()
                self.assertTrue(ch["ok"], (gid, ch))
                self.adv(5)
                self.fresh()
                getattr(self, gid)()
                r = self.cmd(dict(LIVE, accepted=[]), nonce=ch["data"]["nonce"], phrases=ch["data"]["phrases"])
                res = self.final(r["accepted_id"]) if r.get("accepted_id") else r
                self.assertEqual(res["code"], "E_GATE", (gid, res))
                self.assertIn(gid.upper(), [g["id"] for g in res["data"]["gates"] if not g["ok"]])
                self.assertEqual(self.state(), "stopped")
                self.assertEqual(self.reconfs, [])
                self.assertEqual(ctlstate.live_starts(self.e.root, self.clk())["starts"], [] if gid != "h8" else ctlstate.live_starts(self.e.root, self.clk())["starts"])

    def setUp_clean(self):
        """Чистое окружение между подпроверками: без KILL, свежие файлы, история запусков пуста, платформа остановлена."""
        for rel in (("state", "KILL"), ("state", "control", "live_starts.json")):
            try:
                os.remove(self.e.p(*rel))
            except OSError:
                pass
        self.reconfs[:] = []
        ctlstate.save_platform_state(dict(ctlstate.platform_state(self.e.root), state="stopped", mode="observe"), self.e.root)
        self.fresh()
        self.e.w("state/portfolio.json", {"venues": {"bybit": {"equity": 30.0, "free": 30.0, "ts": _ctl.iso(self.clk())}}, "hwm": 30.0})
        self.b.web.nonces.clear()

    def test_gates_are_the_same_set_as_old_dashboard(self):
        """Ни одно ворота не добавлено и не убрано мостом: набор и порядок = gates.HARD."""
        from tb.control import gates
        self.assertEqual(len(gates.HARD), 8)
        self.assertEqual([g["id"] for g in self.ch()["data"]["gates"]], ["H%d" % i for i in range(1, 9)])


class TestUnchanged(Base):
    def test_shutdown_still_forbidden(self):
        for kind in ("challenge", "command"):
            r = self.b.handle(WS, dict(self.msg(kind), action="platform.shutdown", args={}))
            self.assertEqual((r["ok"], r["code"]), (False, "E_ELEVATE"), kind)
        self.assertNotIn("platform.shutdown", ctlbridge.FULL_ACTIONS)
        self.assertEqual(self.inbox(), [])

    def test_old_dashboard_behaviour_is_untouched(self):
        """Старый дашборд: live требует токен (needs_full), выключатель dash2 на него не влияет и в его коде не упоминается."""
        import inspect
        self.assertTrue(ctlweb.needs_full("day.start", {"mode": "live"}, "paper"))
        self.assertNotIn("allow_live_start_from_dash", inspect.getsource(ctlweb))
        web = ctlweb.CtlWeb(self.e.root)
        s = web.sessions.setdefault("x", web.new_session(self.clk(), "open")[1])
        self.assertEqual(web.challenge({"action": "day.start", "args": dict(LIVE)}, s, self.clk())[0], 401)       # сессия open: нужен токен

    def test_non_live_modes_in_full_mode_do_not_need_the_switch(self):
        self.conf(False)
        r = self.b.handle(WS, self.msg("challenge", {"mode": "paper", "session": "day"}))
        self.assertNotEqual(r["code"], "E_DISABLED")

    def test_other_dangerous_actions_unaffected_by_switch(self):
        self.conf(False)
        r = self.b.handle(WS, dict(self.msg("challenge"), action="risk.set", args={"level": 4}))
        self.assertNotEqual(r["code"], "E_DISABLED")

    def test_ctl_topic_reports_switch(self):
        from tb.dash2 import topics
        self.c.write_status()
        self.assertTrue(topics.ctl(self.e.root, self.clk())["live_start"])
        self.conf(False)
        self.c.write_status()
        self.assertFalse(topics.ctl(self.e.root, self.clk())["live_start"])


if __name__ == "__main__":
    unittest.main()
