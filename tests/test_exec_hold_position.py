"""Инцидент PL-1103-ETH-02 (paper, 2026-11-03): пауза плана в позиции, E_REV_STALE от мозга, отказ частичного закрытия остатка.

Решение владельца: план в позиции на паузе не стоит. hold плана только до входа (запрет входа) и для добора; на позиции стоп, безубыток,
частичные и полные закрытия работают всегда. Мозг ревизию не присылает: stop/amend проверяются по текущему плану.
"""
import unittest

import _bootstrap  # noqa: F401
from fakes.harness import Env
from tb.core import plan_schema
from tb.executor import core


def codes(res):
    return [e["code"] for e in (res or {}).get("errors") or []]


class HoldInPosition(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def tearDown(self):
        self.e.close()

    def test_hold_command_rejected_in_position(self):
        pid = self.e.enter()
        self.assertIn(self.e.rt(pid)["status"], ("entered", "managing"))
        r = self.e.command("hold", pid)
        self.assertFalse(r["ok"])
        self.assertEqual(codes(r), ["E_STATUS_FORBIDS"])
        self.assertIn("в позиции", r["errors"][0]["msg"])
        self.assertIsNone(self.e.rt(pid).get("hold"))
        r = self.e.command("release", pid)                     # снятие работает как раньше
        self.assertTrue(r["ok"], r)

    def test_brain_pause_dropped_on_entry(self):
        r = self.e.submit()
        pid = r["plan_id"]
        self.assertTrue(self.e.command("hold", pid)["ok"])
        self.assertEqual(self.e.rt(pid)["hold"], "brain")
        self.e.tick(100.1)
        self.e.candle(100.3)
        self.e.tick(100.25)
        self.assertNotIn("SOL", self.e.port.pos)              # пауза до входа запрещает вход
        # переход в entered при стоящей паузе (вход, исполненный биржей до паузы, или adopt): пауза мозга снимается в _to_status
        p = self.e.ex.plans[pid]
        self.assertEqual(p["rt"]["hold"], "brain")
        self.assertTrue(self.e.ex._to_status(p, "entered", "TEST"))
        self.assertIsNone(self.e.rt(pid).get("hold"))
        rel = self.e.decisions("event", "hold_released")
        self.assertTrue(rel and rel[-1]["res"]["code"] == "HOLD_IN_POSITION" and "ENTRY" in rel[-1]["why"], rel)

    def test_existing_brain_pause_in_position_dropped_on_tick_and_stop_moves(self):
        """Как ETH-02 после перезапуска: план уже в позиции с rt.hold=brain. Первый тик снимает паузу с записью, безубыток работает."""
        pid = self.e.enter()
        p = self.e.ex.plans[pid]
        p["rt"]["hold"], p["rt"]["hold_reason"] = "brain", "brain"
        self.e.ex.save(p, sync=True)
        self.e.tick(101.5)
        self.assertIsNone(self.e.rt(pid).get("hold"))
        self.assertTrue(self.e.decisions("event", "hold_released"))
        self.assertGreater(self.e.port.pos["SOL"]["sl"], 100.0)

    def test_system_hold_in_position_keeps_protection_and_stays(self):
        pid = self.e.enter()
        p = self.e.ex.plans[pid]
        p["rt"]["hold"], p["rt"]["hold_reason"] = "position_drift", "position_drift"
        self.e.tick(101.5)
        self.assertGreater(self.e.port.pos["SOL"]["sl"], 100.0)    # защитный перенос стопа при системном hold
        self.assertEqual(self.e.ex.plans[pid]["rt"]["hold"], "position_drift")   # системная причина остаётся (запрещает добор)
        self.assertFalse(self.e.decisions("event", "hold_released"))
        skips = [d["res"]["code"] for d in self.e.decisions("skip", "rule")]
        self.assertNotIn("NO_DATA", skips)


class PartialCloseRemainder(unittest.TestCase):
    def test_close_qty(self):
        self.assertEqual(core.close_qty(0.005, 0.01, 0.01), (0.01, True))           # ETH-02: остаток 1 лот, frac 0.5 -> весь остаток
        self.assertEqual(core.close_qty(0.05, 0.1, 0.1, "0.1"), (0.1, True))
        self.assertEqual(core.close_qty(0.15, 0.3, 0.1, "0.2"), (0.3, True))        # 0.1 < min_qty 0.2, остаток 0.3 < 2 лотов: целиком
        self.assertEqual(core.close_qty(0.003, 0.05, 0.01), (0.01, False))          # делимый остаток: один минимальный лот
        self.assertEqual(core.close_qty(0.07, 0.14, 0.01), (0.07, False))
        self.assertEqual(core.close_qty(0.07, 0.14, 0.01, full_req=True), (0.14, True))

    def test_partial_close_of_one_lot_remainder_closes_it(self):
        """Было: остаток 1 лот, close frac 0.5 -> объём 0 -> E_STATUS_FORBIDS «закрытие не выполнено» (ETH-02 08:00, 09:12, 09:14)."""
        from unittest import mock
        e = Env()
        try:
            pid = e.enter()
            p = e.ex.plans[pid]
            p["rt"]["pos"]["qty"] = 0.1                                         # лот SOL у FakePort: шаг 0.1, min_qty 0.1
            calls = []

            def fake_close(pp, qty, reason, why="", full=False):
                calls.append((qty, full))
                return "ok", {}
            with mock.patch.object(e.ex.act, "close", fake_close), mock.patch.object(e.ex, "sync_position", lambda pp: None):
                r = e.command("close", pid, payload={"frac": 0.5})
            self.assertTrue(r["ok"], r)
            self.assertEqual(calls, [(None, True)])                              # закрыт весь неделимый остаток
        finally:
            e.close()


class BrainHoldPlanCheck(unittest.TestCase):
    def test_pm_hold_plan_in_position_is_semantic_error(self):
        from tb.brain import validate
        o = {"schema": "tb.pm/1", "event_seq": 1, "plan": "PL-1103-ETH-02", "decision": "hold_plan", "args": {}, "why": "x", "conf": 0.5, "next": "event"}
        self.assertIn("HOLD_IN_POSITION", validate._pm(o, {"plan": "PL-1103-ETH-02", "has_position": True}))
        self.assertNotIn("HOLD_IN_POSITION", validate._pm(o, {"plan": "PL-1103-ETH-02", "has_position": False}))
        self.assertEqual(validate._pm(dict(o, decision="hold"), {"plan": "PL-1103-ETH-02", "has_position": True}), [])


class BrainRev(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def tearDown(self):
        self.e.close()

    def test_brain_stop_without_rev_checked_by_current_plan(self):
        pid = self.e.enter()
        self.e.tick(101.5)
        r = self.e.command("stop", pid, payload={"to": 101.0})           # мозг без rev_expected
        self.assertTrue(r["ok"], r)
        r = self.e.command("stop", pid, payload={"to": 99.6})            # шире текущего: отказ по состоянию, не по ревизии
        self.assertFalse(r["ok"])
        self.assertNotIn("E_REV_STALE", codes(r))

    def test_owner_without_rev_still_rejected_and_float_rev_ok(self):
        pid = self.e.enter()
        self.e.tick(101.5)
        r = self.e.command("stop", pid, payload={"to": 101.0}, actor="owner")
        self.assertEqual(codes(r), ["E_REV_STALE"])
        r = self.e.command("stop", pid, payload={"to": 101.0}, actor="owner", rev_expected=float(self.e.spec(pid)["rev"]))
        self.assertTrue(r["ok"], r)

    def test_check_rev_int_float(self):
        self.assertEqual(plan_schema.check_rev({"rev": 2}, 2.0), [])
        self.assertEqual(plan_schema.check_rev({"rev": 2.0}, 2), [])
        for bad in (1, 2.5, "2", True, None, float("nan"), float("inf")):
            self.assertEqual([i["code"] for i in plan_schema.check_rev({"rev": 2}, bad)], ["E_REV_STALE"], bad)


if __name__ == "__main__":
    unittest.main()
