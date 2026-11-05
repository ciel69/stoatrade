"""R4, задача 17: maker-выходы и post-only тейков (Requirement 10; свойство 24).

Исполнитель на FakePort: заявки политики по уровню `at_r` после входа (post-only reduce-only, ссылка `tbx-xp-...`), пересчёт
количества при изменении размера, отказ post-only -> рынок, maker-закрытие по времени с таймером `wait_s`, частичное заполнение
по факту, восстановление таймера после перезапуска, снятие заявок при закрытии позиции; тейки плана `resting` — post-only, если
цена не пересекает книгу. Paper-порт: отказ пересекающего post-only и заполнение по проходу уровня на `maker.through_ticks` и
`maker.through_bps` из `config/exits.json`.
"""
import json
import os
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from fakes.clock import FakeClock
from fakes.harness import Env, plan_long
from tb.core import exitdoc
from tb.executor.paper_port import PaperPort, load_costs
from tb.executor.ports import KIND_REJECTED, PortError
from tb.logic import exitpol

MAKER = {"through_ticks": 1, "through_bps": 2, "wait_s": 60}


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def _plan(**over):
    base = plan_long(targets=[{"id": "t1", "price": 120.0, "frac": 1.0, "exec": "touch"}], manage=[],
                     lifecycle={"ttl_min": 60, "max_hold_min": 3})
    base.update(over)
    return base


class MakerEnv(unittest.TestCase):
    def setUp(self):
        self.e = Env()

    def tearDown(self):
        self.e.close()

    def floor(self):
        return exitpol.floor(self.e.ex.cfg.get("auto_protect"))

    def snapshot(self, policy, ver="X-0002"):
        floor = self.floor()
        pol = exitpol.normalize(policy, floor)
        h = exitpol.policy_hash(pol)
        now = self.e.clock.time()
        _write(os.path.join(self.e.root, "config", "exits.json"),
               {"schema": "tb.exits_cfg/1", "enabled": True, "lease": {"lease_h": 6}, "maker": MAKER})
        _write(exitdoc.snapshot_path(self.e.root), {"schema": exitdoc.SNAPSHOT_SCHEMA, "ver": ver, "policy": pol, "policy_h": h,
                                                    "floor_h": exitpol.policy_hash(floor), "ts": now})
        _write(exitdoc.lease_path(self.e.root), {"ts": now, "ver": ver, "policy_h": h, "by": "promote"})
        return pol

    def enter(self, plan=None):
        pid = self.e.enter(plan if plan is not None else _plan())
        return pid, self.e.rt(pid)

    def tick_at(self, ts, px):
        dt = ts - self.e.clock.time()
        if dt > 0:
            self.e.clock.advance(dt)
        self.e.tick(px, dt=0.0)

    def maker_calls(self):
        return [c for c in self.e.port.calls if c[0] == "place_reduce_only"]

    def policy_order(self, rt, rule):
        return (rt.get("policy_orders") or {}).get(rule)


class PolicyPriceLimits(MakerEnv):
    def test_level_post_only_reduce_only_and_stop_untouched(self):
        self.snapshot({"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]})
        pid, rt = self.enter()
        avg, stop = float(rt["pos"]["avg"]), float(rt["pos"]["initial_stop"])
        rec = self.policy_order(rt, "xp_partial_0")
        self.assertIsNotNone(rec, rt.get("policy_orders"))
        self.assertEqual(rec["state"], "resting")
        self.assertAlmostEqual(rec["price"], round(avg + abs(avg - stop), 2), places=6)
        link = rec["link"]
        self.assertTrue(link.startswith("tbx-xp-%d-" % int(rt["plan_n"])), link)
        o = self.e.port.orders[link]
        self.assertTrue(o["reduce_only"])
        self.assertEqual(o["side"], "Sell")
        self.assertEqual(o["type"], "limit")
        self.assertAlmostEqual(o["qty"], rec["qty"], places=6)
        call = [c for c in self.maker_calls() if c[1][3] == link][-1]
        self.assertIs(call[1][5], True)                                    # post_only=True
        self.assertEqual(float(self.e.port.pos["SOL"]["sl"]), stop)        # стоп на бирже не тронут (Requirement 10.4)

    def test_post_only_reject_closes_fraction_market(self):
        self.snapshot({"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]})
        self.e.port.script("place_reduce_only", PortError(KIND_REJECTED, "post only", code="POST_ONLY"))
        pid, rt = self.enter()
        q0 = float(rt["pos"]["entries"][0]["qty"])
        self.assertLess(float(self.e.rt(pid)["pos"]["qty"]), q0 - 1e-9)      # доля закрыта рынком сразу
        rej = [d for d in self.e.decisions("event") if d.get("what") == "policy_maker_reject"]
        self.assertTrue(rej, self.e.decisions("event"))
        self.assertEqual(self.policy_order(self.e.rt(pid), "xp_partial_0")["state"], "filled")

    def test_partial_fill_counted_by_fact(self):
        self.snapshot({"partial": [{"at_r": 1.0, "frac": 0.5, "exec": "maker"}]})
        pid, rt = self.enter()
        rec = self.policy_order(rt, "xp_partial_0")
        q0 = float(rt["pos"]["qty"])
        part = self.e.port.fill(rec["link"], rec["qty"] / 2.0, rec["price"])
        self.assertEqual(part["link_id"], rec["link"])
        self.e.pump()
        rt = self.e.rt(pid)
        rec = self.policy_order(rt, "xp_partial_0")
        self.assertAlmostEqual(rec["filled_qty"], float(part["qty"]), places=6)
        self.assertAlmostEqual(float(rt["pos"]["qty"]), q0 - float(part["qty"]), places=6)
        self.assertEqual(rec["state"], "resting")                          # доля заполнена частично: остаток заявки ждёт

    def test_resize_recounts_policy_qty(self):
        self.snapshot({"partial": [{"at_r": 0.7, "frac": 0.3, "exec": "maker"},
                                   {"at_r": 1.4, "frac": 0.3, "exec": "maker"}]})
        pid, rt = self.enter()
        before = float(rt["pos"]["qty"])
        far0 = float(self.policy_order(rt, "xp_partial_1")["qty"])
        avg = float(rt["pos"]["avg"])
        ev = {"exec_id": "MANUAL1", "sym": "SOL", "side": "Sell", "price": avg, "qty": round(before / 2.0, 6), "fee": 0.0, "is_maker": False,
              "link_id": "", "order_id": "MANUAL", "stop_type": "", "exec_type": "Trade", "closed_size": round(before / 2.0, 6), "pnl": 0.0,
              "ts_ms": int(self.e.clock.time() * 1000)}
        self.assertIsNotNone(self.e.ex.apply_execution(ev))                # ручное частичное закрытие на бирже
        self.e.tick(avg)
        rt = self.e.rt(pid)
        far = self.policy_order(rt, "xp_partial_1")
        live = sum(float(r.get("qty") or 0) - float(r.get("filled_qty") or 0) for r in rt["policy_orders"].values()
                   if r.get("state") == "resting")
        self.assertLessEqual(live, float(rt["pos"]["qty"]) + 1e-9, (live, rt["pos"]["qty"]))
        self.assertGreater(before, float(rt["pos"]["qty"]))
        self.assertTrue(far["state"] == "cancelled" or far["qty"] < far0, far)

    def test_fraction_below_lot_closes_market(self):
        self.snapshot({"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]})
        self.e.port.pos["SOL"] = {"sym": "SOL", "side": "long", "size": 0.1, "avg": 100.0, "mark": 100.0, "sl": 99.0, "tp": 0.0,
                                  "liq": 50.0, "upnl": 0.0, "lev": 5.0, "margin": 2.0, "ts": 0.0}
        r = self.e.command("adopt", None, {"symbol": "SOL"})
        self.assertTrue(r["ok"], r)
        rt = self.e.rt(r["plan_id"])
        self.assertEqual(rt["status"], "closed")                          # неделимый остаток закрыт рынком
        rej = [d for d in self.e.decisions("event") if d.get("what") == "policy_maker_reject"]
        self.assertTrue(rej, self.e.decisions("event"))

    def test_near_tp_maker_limit_at_table_level(self):
        self.snapshot({"near_tp": {"q": 0.4, "frac": 0.3, "min_r": 0.5, "exec": "maker",
                                   "table": {"brain": {"intraday": 0.8}}}})
        pid, rt = self.enter()
        rec = self.policy_order(rt, "xp_near")
        self.assertIsNotNone(rec, rt.get("policy_orders"))
        avg, stop = float(rt["pos"]["avg"]), float(rt["pos"]["initial_stop"])
        self.assertAlmostEqual(rec["price"], round(avg + 0.8 * abs(avg - stop), 2), places=6)
        self.assertTrue(rec["link"].startswith("tbx-xp-%d-xp_near" % int(rt["plan_n"])))

    def test_orders_cancelled_when_position_closes(self):
        self.snapshot({"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]})
        pid, rt = self.enter()
        link = self.policy_order(rt, "xp_partial_0")["link"]
        self.e.tick(98.9)                                                  # стоп на бирже
        self.assertEqual(self.e.rt(pid)["status"], "closed")
        self.assertIn(self.e.port.orders[link]["status"], ("Cancelled", "Filled"))


class TimeMaker(MakerEnv):
    def _timeout_order(self, pid):
        self.snapshot({"timeout": {"hold_frac": 1.0, "exec": "maker"}})
        pid, rt = self.enter()
        t0 = float(rt["ts"]["entered"])
        avg, stop = float(rt["pos"]["avg"]), float(rt["pos"]["initial_stop"])
        self.tick_at(t0 + 180.2, avg + 0.5 * abs(avg - stop))
        rt = self.e.rt(pid)
        return pid, t0, rt

    def test_timer_bounded_and_stop_stays(self):
        pid, t0, rt = self._timeout_order(None)
        rec = self.policy_order(rt, "xp_timeout")
        self.assertIsNotNone(rec, rt.get("policy_orders"))
        self.assertEqual(rec["state"], "maker_wait")
        self.assertGreater(rec["price"], float(rt["pos"]["avg"]))            # long: цена не ниже max(last, ask)
        self.assertAlmostEqual(rec["until"] - self.e.clock.time(), 60.0, places=3)
        stop = float(self.e.port.pos["SOL"]["sl"])
        self.assertLessEqual(stop, float(self.e.port.px["SOL"][2]))
        self.e.tick(float(self.e.rt(pid)["pos"]["avg"]) + 0.5 * abs(float(self.e.rt(pid)["pos"]["avg"]) - stop), dt=61.0)
        rt = self.e.rt(pid)
        self.assertEqual(rt["status"], "closed")                            # wait_s + одна проверка таймера
        self.assertEqual((rt["close"]["reason"], rt["close"]["rule"]), ("rule", "xp_timeout"))
        self.assertLessEqual(self.e.clock.time() - (t0 + 180.2), 60.0 + 2.0)
        self.assertEqual(self.e.trades()[0].get("exit_rule"), "timeout")
        self.assertFalse([o for o in self.e.port.orders.values() if o["status"] == "New" and o["reduce_only"]])

    def test_partial_fill_then_market_closes_remainder(self):
        pid, t0, rt = self._timeout_order(None)
        rec = self.policy_order(rt, "xp_timeout")
        qty = float(rt["pos"]["qty"])
        part = self.e.port.fill(rec["link"], rec["qty"] / 2.0, rec["price"])
        self.e.pump()
        self.assertAlmostEqual(float(self.e.rt(pid)["pos"]["qty"]), qty - float(part["qty"]), places=6)
        self.assertEqual(self.policy_order(self.e.rt(pid), "xp_timeout")["state"], "maker_wait")
        self.e.tick(float(self.e.rt(pid)["pos"]["avg"]) + 0.5, dt=61.0)
        rt = self.e.rt(pid)
        self.assertEqual(rt["status"], "closed")
        self.assertEqual(rt["close"]["rule"], "xp_timeout")
        self.assertAlmostEqual(float(rt["realized"]["parts"][-1]["qty"]), qty - float(part["qty"]), places=6)

    def test_timer_restored_after_restart(self):
        pid, t0, rt = self._timeout_order(None)
        self.assertEqual(self.policy_order(rt, "xp_timeout")["state"], "maker_wait")
        from tb.executor.core import Executor
        ex2 = Executor(self.e.port, root=self.e.root, clock=self.e.clock.time, sleep=self.e.clock.advance, profile="testnet")
        ex2.start()
        self.assertEqual((ex2.store.load(pid)[1].get("policy_orders") or {})["xp_timeout"]["state"], "maker_wait")
        self.e.clock.advance(61.0)
        avg = float(ex2.store.load(pid)[1]["pos"]["avg"])
        self.e.port.set_price("SOL", avg + 0.5)
        ex2.on_ticker("SOL", avg + 0.5, avg + 0.48, avg + 0.52, avg + 0.5, 0.0, self.e.clock.time())
        rt2 = ex2.store.load(pid)[1]
        self.assertEqual(rt2["status"], "closed")                          # просроченный таймер: снятие и рынок
        self.assertEqual(rt2["close"]["rule"], "xp_timeout")


class PlanTargetsPostOnly(MakerEnv):
    def test_targets_placed_post_only_when_passive(self):
        pid, rt = self.enter(plan_long())
        calls = [c for c in self.maker_calls() if str(c[1][3]).startswith("tbx-")]
        self.assertTrue(calls)
        self.assertTrue(all(c[1][5] is True for c in calls), calls)

    def test_crossing_target_is_not_post_only(self):
        pid, rt = self.enter(plan_long())
        p = self.e.ex.plans[pid]
        self.assertTrue(self.e.ex.act._passive(p, 102.5))
        self.assertFalse(self.e.ex.act._passive(p, 100.0))


class PaperMakerModel(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbmaker_")
        os.makedirs(os.path.join(self.root, "config"))
        _write(os.path.join(self.root, "config", "exits.json"),
               {"schema": "tb.exits_cfg/1", "enabled": True, "maker": MAKER})
        self.clk = FakeClock()
        cfg = load_costs(self.root)
        cfg["latency_ms"] = 0
        self.p = PaperPort(40.0, cfg, clock=self.clk.time)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def tick(self, last, spread=0.1):
        self.clk.advance(1.0)
        return self.p.on_ticker("SOL", last, last - spread / 2.0, last + spread / 2.0, last, None, self.clk.time())

    def test_maker_params_from_config(self):
        cfg = load_costs(self.root)
        self.assertEqual((cfg["through_ticks"], cfg["through_bps"], cfg["wait_s"]), (1, 2, 60))

    def test_fill_needs_through_ticks_and_bps(self):
        self.tick(100.0)
        self.p.place_entry("SOL", "long", 1.0, "tbx-1-1", "market", sl=95.0)
        self.p.place_reduce_only("SOL", "sell", 0.5, "tbx-xp-1-xp_partial_0", price=101.0, post_only=True)
        self.tick(101.0)                                                   # касание: нет прохода
        self.assertAlmostEqual(self.p.positions()[0]["size"], 1.0, places=6)
        self.tick(101.02)                                                   # 1 тик, но меньше 2 б.п. (0.0202)
        self.assertAlmostEqual(self.p.positions()[0]["size"], 1.0, places=6)
        self.tick(101.03)                                                   # проход на 1 тик и 2 б.п.
        self.assertAlmostEqual(self.p.positions()[0]["size"], 0.5, places=6)
        ex = [e for e in self.p.executions(0) if e["link_id"] == "tbx-xp-1-xp_partial_0"][-1]
        self.assertTrue(ex["is_maker"])
        self.assertAlmostEqual(ex["price"], 101.0, places=6)

    def test_crossing_post_only_is_rejected(self):
        self.tick(100.0)
        self.p.place_entry("SOL", "long", 1.0, "tbx-1-1", "market", sl=95.0)
        r = self.p.place_reduce_only("SOL", "sell", 0.5, "tbx-xp-1-xp_partial_0", price=99.9, post_only=True)
        self.assertEqual((r["status"], r["cancel_type"]), ("Cancelled", "CancelByPostOnly"))
        self.assertAlmostEqual(self.p.positions()[0]["size"], 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
