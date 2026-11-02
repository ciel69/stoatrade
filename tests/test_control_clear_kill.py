"""Снятие KILL из нового дашборда (решение владельца 2026-11-02): действие ctl `kill.clear`.
Контракт: по умолчанию флаг allow_clear_kill_from_dash выключен -> E_DISABLED; включён -> снятие только при выполненных предусловиях (исполнитель жив, день не идёт,
стоп на бирже у каждой позиции, при flatten позиции закрыты); live только после всех жёстких ворот, nonce и фразы; аудит с причиной; старый дашборд и CLI действие не получают.
Временный корень и поддельный ключ, фейковый ctl; живые команды платформе не отправляются."""
import json
import os
import unittest

import _bootstrap  # noqa: F401
import _ctl
from tb.control import audit, client, ctlstate, handlers, protocol
from tb.core import exec_cfg
from tb.dash import ctlweb

NOW = _ctl.NOW
POS_OK = {"sym": "BTCUSDT", "side": "Buy", "qty": 0.001, "sl": 60000.0}
POS_NO_SL = {"sym": "ETHUSDT", "side": "Buy", "qty": 0.01}


class Base(unittest.TestCase):
    flag = True
    profile = "paper"

    def setUp(self):
        self.e = _ctl.Env(self)
        self.e.good_live()
        self.cfg(self.flag)
        self.c = self.e.ctl()
        self.e.good_exec(profile=self.profile)
        exec_cfg.set_kill(False, "ручная остановка", self.e.root)
        ctlstate.save_platform_state(dict(ctlstate.platform_state(self.e.root), state="halted", mode="observe"), self.e.root)

    def cfg(self, flag):
        c = {"version": 1, "auth": {"mode": "token"}}
        if flag is not None:
            c["allow_clear_kill_from_dash"] = flag
        self.e.w("config/control.json", c)

    def positions(self, *pos):
        self.e.w("runtime/exec_snapshot.json", {"ts_epoch": self.e.clock_t[0], "env": "mainnet", "account": {"equity": 30.0, "free": 30.0}, "positions": list(pos), "orders": []})

    def killed(self):
        return exec_cfg.kill_state(self.e.root) is not None

    def rows(self):
        return [r for r in audit.tail(self.e.root, 200) if r["action"] == "kill.clear"]

    def gate_ids(self, res):
        return [g["id"] for g in res["data"]["gates"] if not g["ok"]]


class TestFlag(Base):
    flag = False

    def test_disabled_by_default_in_protocol(self):
        self.assertFalse(protocol.DEFAULTS["allow_clear_kill_from_dash"])
        for v in (None, False):
            self.cfg(v)
            r = self.e.challenge(self.c, "kill.clear", {})
            self.assertEqual((r["status"], r["code"]), ("rejected", "E_DISABLED"))

    def test_command_disabled_and_kill_stays(self):
        r = self.e.confirmed(self.c, "kill.clear", {})
        self.assertEqual(r["code"], "E_DISABLED")
        r, _ = self.e.send(self.c, "kill.clear", {})
        self.assertEqual(r["code"], "E_DISABLED")
        self.assertTrue(self.killed())

    def test_non_true_values_do_not_enable(self):
        for v in ("true", 1, "yes", []):
            self.cfg(v)
            self.assertEqual(self.e.challenge(self.c, "kill.clear", {})["code"], "E_DISABLED", v)

    def test_flag_is_reread_each_time(self):
        self.cfg(True)
        self.assertEqual(self.e.challenge(self.c, "kill.clear", {})["code"], "OK")
        self.cfg(False)
        self.assertEqual(self.e.challenge(self.c, "kill.clear", {})["code"], "E_DISABLED")


class TestPaper(Base):
    def test_challenge_describes_kill_and_gives_nonce_without_phrases(self):
        r = self.e.challenge(self.c, "kill.clear", {})
        self.assertEqual((r["status"], r["code"]), ("done", "OK"))
        d = r["data"]
        self.assertTrue(d["danger"] and d["nonce"] and d["phrases"] == [] and not d["blocked"])
        self.assertEqual(d["kill"], {"reason": "ручная остановка", "flatten": False, "mode": "paper", "active": True})
        self.assertTrue(all(g["ok"] for g in d["gates"]))
        self.assertTrue(self.killed())                       # challenge ничего не снимает

    def test_clears_after_confirmation_and_audit_has_reason_and_mode(self):
        self.positions(POS_OK)
        r = self.e.confirmed(self.c, "kill.clear", {})
        self.assertEqual((r["status"], r["code"]), ("done", "OK"))
        self.assertFalse(self.killed())
        row = self.rows()[-1]
        self.assertEqual((row["result"], row["code"], row["mode"]), ("ok", "OK", "paper"))
        self.assertIn("ручная остановка", row["note"])
        self.assertTrue(audit.verify(self.e.root)["ok"])
        self.c.watch()                                       # halted -> stopped сам
        self.assertEqual(ctlstate.platform_state(self.e.root)["state"], "stopped")
        self.assertFalse(self.c.status()["kill"])

    def test_no_nonce_or_wrong_nonce_keeps_kill(self):
        r, _ = self.e.send(self.c, "kill.clear", {})
        self.assertEqual(r["code"], "E_NONCE")
        r, _ = self.e.send(self.c, "kill.clear", {}, nonce="левый")
        self.assertEqual(r["code"], "E_NONCE")
        self.assertTrue(self.killed())

    def test_nonce_is_single_use(self):
        ch = self.e.challenge(self.c, "kill.clear", {})
        self.e.advance(3)
        r, _ = self.e.send(self.c, "kill.clear", {}, nonce=ch["data"]["nonce"])
        self.assertEqual(r["code"], "OK")
        exec_cfg.set_kill(False, "снова", self.e.root)
        r, _ = self.e.send(self.c, "kill.clear", {}, nonce=ch["data"]["nonce"])
        self.assertEqual(r["code"], "E_NONCE")
        self.assertTrue(self.killed())

    def test_nonce_too_early(self):
        ch = self.e.challenge(self.c, "kill.clear", {})
        r, _ = self.e.send(self.c, "kill.clear", {}, nonce=ch["data"]["nonce"])
        self.assertEqual(r["code"], "E_NONCE")
        self.assertTrue(self.killed())

    def test_no_kill_is_state_error(self):
        exec_cfg.clear_kill(self.e.root)
        r = self.e.challenge(self.c, "kill.clear", {})
        self.assertEqual((r["code"], r["data"]["why"]), ("E_STATE", "no_kill"))
        self.assertEqual(self.e.confirmed(self.c, "kill.clear", {})["code"], "E_STATE")

    def test_position_without_stop_blocks(self):
        self.positions(POS_OK, POS_NO_SL)
        r = self.e.challenge(self.c, "kill.clear", {})
        self.assertEqual((r["code"], r["data"]["blocked"], r["data"]["nonce"]), ("E_GATE", True, None))
        self.assertEqual(self.gate_ids(r), ["H2"])
        self.assertIn("ETHUSDT", [g["msg"] for g in r["data"]["gates"] if g["id"] == "H2"][0])
        r = self.e.confirmed(self.c, "kill.clear", {})
        self.assertEqual(r["code"], "E_GATE")
        self.assertTrue(any(g["id"] == "H2" for g in r["data"]["gates"]))
        self.assertTrue(self.killed())
        self.assertEqual(self.rows()[-1]["result"], "rejected")

    def test_stale_snapshot_blocks(self):
        self.e.w("runtime/exec_snapshot.json", {"ts_epoch": NOW - 600, "positions": [], "account": {}})
        self.assertEqual(self.gate_ids(self.e.challenge(self.c, "kill.clear", {})), ["H2"])

    def test_dead_executor_blocks(self):
        self.e.good_exec(profile="paper", now=NOW - 600)
        r = self.e.challenge(self.c, "kill.clear", {})
        self.assertEqual(r["code"], "E_GATE")
        self.assertIn("K1", self.gate_ids(r))
        os.remove(self.e.p("runtime", "exec_health.json"))
        self.assertIn("K1", self.gate_ids(self.e.challenge(self.c, "kill.clear", {})))
        self.assertTrue(self.killed())

    def test_day_closing_or_running_blocks(self):
        for state in ("closing", "starting", "running"):
            ctlstate.save_platform_state(dict(ctlstate.platform_state(self.e.root), state=state), self.e.root)
            r = self.e.confirmed(self.c, "kill.clear", {})
            self.assertEqual(r["code"], "E_GATE", state)
            self.assertIn("K2", [g["id"] for g in r["data"]["gates"] if not g["ok"]])
            self.assertTrue(self.killed())

    def test_flatten_kill_waits_until_positions_closed(self):
        exec_cfg.set_kill(True, "флэттен", self.e.root)
        self.positions(POS_OK)
        r = self.e.challenge(self.c, "kill.clear", {})
        self.assertIn("K3", self.gate_ids(r))
        self.positions()
        r = self.e.challenge(self.c, "kill.clear", {})
        self.assertEqual((r["code"], r["data"]["kill"]["flatten"]), ("OK", True))

    def test_unreadable_kill_file_still_has_reason_and_is_cleared_only_via_gates(self):
        self.e.w("state/KILL", "кривой текст", raw=True)
        r = self.e.challenge(self.c, "kill.clear", {})
        self.assertEqual(r["data"]["kill"]["reason"], "кривой текст")

    def test_reason_is_trimmed_and_flat(self):
        exec_cfg.set_kill(False, "строка\n" + "я" * 400, self.e.root)
        r = self.e.challenge(self.c, "kill.clear", {})
        self.assertLessEqual(len(r["data"]["kill"]["reason"]), 120)
        self.assertNotIn("\n", r["data"]["kill"]["reason"])

    def test_extra_args_are_dropped(self):
        ok, _m, a = protocol.validate_args("kill.clear", {"force": True, "skip_gates": 1})
        self.assertEqual((ok, a), (True, {}))

    def test_error_texts_come_from_catalog(self):
        self.positions(POS_NO_SL)
        r = self.e.challenge(self.c, "kill.clear", {})
        self.assertEqual(r["msg"], protocol.CODES["E_GATE"])
        self.assertNotIn(self.e.root, json.dumps(r, ensure_ascii=False))


class TestLive(Base):
    profile = "trade"

    def test_live_needs_nonce_and_phrase(self):
        ch = self.e.challenge(self.c, "kill.clear", {})
        self.assertEqual(ch["code"], "OK", ch)
        d = ch["data"]
        self.assertEqual(d["kill"]["mode"], "live")
        self.assertEqual(d["phrases"], ["СНИМАЮ KILL LIVE 2026-10-27"])
        ids = [g["id"] for g in d["gates"]]
        for gid in ("K1", "K2", "H2", "H3", "H4", "H5", "H6", "H7"):
            self.assertIn(gid, ids)
        self.assertNotIn("H1", ids)
        self.e.advance(3)
        r, _ = self.e.send(self.c, "kill.clear", {}, nonce=d["nonce"])
        self.assertEqual(r["code"], "E_PHRASE")                     # без фразы
        r, _ = self.e.send(self.c, "kill.clear", {}, nonce=d["nonce"], phrases=["СНИМАЮ KILL LIVE 2026-10-26"])
        self.assertEqual(r["code"], "E_PHRASE")
        self.assertTrue(self.killed())
        r, _ = self.e.send(self.c, "kill.clear", {}, nonce=d["nonce"], phrases=d["phrases"])
        self.assertEqual((r["status"], r["code"]), ("done", "OK"))
        self.assertFalse(self.killed())
        self.assertEqual(self.rows()[-1]["mode"], "live")

    def test_confirmed_helper_flow(self):
        self.assertEqual(self.e.confirmed(self.c, "kill.clear", {})["code"], "OK")
        self.assertFalse(self.killed())

    def test_live_hard_gates_block(self):
        cases = {"H3": dict(ws={"public": {"up": True}, "private": {"up": False}}), "H4": dict(diffs=2), "H5": None}
        self.e.good_exec(profile="trade", **cases["H3"])
        r = self.e.challenge(self.c, "kill.clear", {})
        self.assertEqual((r["code"], self.gate_ids(r)), ("E_GATE", ["H3"]))
        self.e.good_exec(profile="trade", **cases["H4"])
        self.assertEqual(self.gate_ids(self.e.challenge(self.c, "kill.clear", {})), ["H4"])
        self.e.good_exec(profile="trade")
        self.e.w("state/portfolio.json", {"venues": {"bybit": {"equity": 30.0}}, "hwm": 100.0})       # просадка выше порога остановки
        self.assertEqual(self.gate_ids(self.e.challenge(self.c, "kill.clear", {})), ["H5"])
        self.assertTrue(self.killed())

    def test_live_position_without_stop_blocks(self):
        self.positions(POS_NO_SL)
        self.assertEqual(self.gate_ids(self.e.challenge(self.c, "kill.clear", {})), ["H2"])
        self.assertEqual(self.e.confirmed(self.c, "kill.clear", {})["code"], "E_GATE")
        self.assertTrue(self.killed())

    def test_trade_profile_in_observe_is_not_trusted(self):
        self.e.good_exec(profile="trade", observe=True)
        self.assertEqual(self.e.challenge(self.c, "kill.clear", {})["data"]["kill"]["mode"], "live")
        self.assertIn("H2", self.gate_ids(self.e.challenge(self.c, "kill.clear", {})))


class TestNotForOldDash(Base):
    def test_old_dash_and_cli_do_not_know_the_action(self):
        self.assertNotIn("kill.clear", client.ACTIONS)
        self.assertNotIn("kill.clear", protocol.ACTIONS)
        web = ctlweb.CtlWeb(self.e.root)
        for body in ({"action": "kill.clear", "args": {}}, {"action": "challenge", "args": {"action": "kill.clear"}}):
            self.assertEqual(web._norm(body)[2], "E_UNKNOWN")
        import tb.control.cli as cli
        self.assertNotIn("kill.clear", " ".join(protocol.ACTIONS))
        self.assertTrue(hasattr(cli, "protocol"))

    def test_old_dash_source_has_no_clear_path(self):
        import inspect
        src = inspect.getsource(ctlweb)
        self.assertNotIn("kill.clear", src)
        self.assertNotIn("clear_kill", src)

    def test_challenge_of_inner_action_is_valid_for_ctl(self):
        ok, _m, a = protocol.validate_args("challenge", {"action": "kill.clear", "args": {}})
        self.assertTrue(ok)
        self.assertEqual(a, {"action": "kill.clear", "args": {}})


if __name__ == "__main__":
    unittest.main()
