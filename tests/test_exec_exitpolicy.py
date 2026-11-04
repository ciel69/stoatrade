"""R4, Р·Р°РґР°С‡Р° 16: РїСЂРёРјРµРЅРµРЅРёРµ РїРѕР»РёС‚РёРєРё РІС‹С…РѕРґР° РёСЃРїРѕР»РЅРёС‚РµР»РµРј (Requirement 9; СЃРІРѕР№СЃС‚РІР° 8, 18, 19, 20).

РџР°СЂРёС‚РµС‚ СЃ `exitsim` (СЃРІРѕР№СЃС‚РІРѕ 8): СЃРёРЅС‚РµС‚РёС‡РµСЃРєРёР№ С‚РёРєРѕРІС‹Р№ РїСѓС‚СЊ РїРѕРґР°С‘С‚СЃСЏ РёСЃРїРѕР»РЅРёС‚РµР»СЋ РІ РїРѕСЂСЏРґРєРµ СЃРІРµС‡Рё
В«РѕС‚РєСЂС‹С‚РёРµ -> РЅРµР±Р»Р°РіРѕРїСЂРёСЏС‚РЅС‹Р№ СЌРєСЃС‚СЂРµРјСѓРј -> Р±Р»Р°РіРѕРїСЂРёСЏС‚РЅС‹Р№ -> Р·Р°РєСЂС‹С‚РёРµВ» (СЃСЂР°РІРЅРµРЅРёРµ СЃ `order="S"`) Рё
В«РѕС‚РєСЂС‹С‚РёРµ -> Р±Р»Р°РіРѕРїСЂРёСЏС‚РЅС‹Р№ -> РЅРµР±Р»Р°РіРѕРїСЂРёСЏС‚РЅС‹Р№ -> Р·Р°РєСЂС‹С‚РёРµВ» (`order="T"`). РљР°Рє Р·Р°РїРёСЃР°РЅРѕ РІ СЂРµРІРёР·РёРё Р·Р°РґР°С‡Рё 6 (РѕС‚РєСЂС‹С‚С‹Р№
РІРѕРїСЂРѕСЃ 3), РґР»СЏ `S` Р±РµСЂСѓС‚СЃСЏ С‚РѕР»СЊРєРѕ РїСѓС‚Рё Р±РµР· СЃС‚РѕРїРѕРІС‹С… РїСЂР°РІРёР»: РёСЃРїРѕР»РЅРёС‚РµР»СЊ РЅР° С‚РёРєР°С… РїСЂРёРјРµРЅРёР» Р±С‹ РЅРѕРІС‹Р№ СЃС‚РѕРї РЅРµРјРµРґР»РµРЅРЅРѕ,
Р° `S` РѕС‚РєР»Р°РґС‹РІР°РµС‚ РµРіРѕ РґРѕ СЃР»РµРґСѓСЋС‰РµР№ СЃРІРµС‡Рё.
"""
import json
import os
import unittest

import _bootstrap  # noqa: F401
from fakes.harness import Env, plan_long
from tb.core import exitdoc, exec_cfg
from tb.logic import exitpol, exitsim
from tb.memory import cases, features

COSTS = {"fee": 0.00055, "maker_fee": 0.00055, "slip": 0.0, "stop_slip_bps": 0.0, "spread_bps": 0.0}


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def _plan(**over):
    base = plan_long(targets=[{"id": "t1", "price": 110.0, "frac": 1.0, "exec": "touch"}], manage=[],
                     lifecycle={"ttl_min": 60, "max_hold_min": 3})
    base.update(over)
    return base


class ExitPolicyEnv(unittest.TestCase):
    cfg_over = None

    def setUp(self):
        self.e = Env(cfg_over=self.cfg_over)

    def tearDown(self):
        self.e.close()

    def floor(self):
        return exitpol.floor(self.e.ex.cfg.get("auto_protect"))

    def snapshot(self, policy, ver="X-0002", lease_ts=None, lease_h=6):
        floor = self.floor()
        pol = exitpol.normalize(policy, floor)
        h = exitpol.policy_hash(pol)
        now = self.e.clock.time()
        _write(os.path.join(self.e.root, "config", "exits.json"),
               {"schema": "tb.exits_cfg/1", "enabled": True, "lease": {"lease_h": lease_h}})
        _write(exitdoc.snapshot_path(self.e.root), {"schema": exitdoc.SNAPSHOT_SCHEMA, "ver": ver, "policy": pol, "policy_h": h,
                                                    "floor_h": exitpol.policy_hash(floor), "ts": now})
        _write(exitdoc.lease_path(self.e.root), {"ts": now if lease_ts is None else lease_ts, "ver": ver, "policy_h": h, "by": "promote"})
        return pol

    def enter(self, plan=None):
        r = self.e.submit(plan if plan is not None else _plan())
        self.assertTrue(r["ok"], r)
        self.e.tick(100.1)
        self.e.candle(100.3)
        self.e.tick(100.25)
        return r["plan_id"]

    def tick_at(self, ts, px):
        dt = ts - self.e.clock.time()
        if dt > 0:
            self.e.clock.advance(dt)
        self.e.tick(px, dt=0.0)

    def sim_plan(self, rt, hold=3.0):
        return {"side": "long", "entry": {"kind": "market"}, "entry_px": rt["pos"]["avg"], "stop": rt["pos"]["initial_stop"],
                "tps": [110.0], "fracs": [1.0], "max_hold_min": hold, "tick": 0.01}

    def assert_parts(self, rt, sim, t0):
        q0 = float(rt["pos"]["entries"][0]["qty"])
        parts = list(rt["realized"]["parts"])
        self.assertEqual(len(parts), len(sim["parts"]), (parts, sim["parts"]))
        for got, ref in zip(parts, sim["parts"]):
            self.assertAlmostEqual(got["px"], ref["px"], places=6)
            self.assertAlmostEqual(float(got["qty"]) / q0, ref["frac"], places=6)
            self.assertAlmostEqual(round((float(got["ts"]) - t0) / 60.0), round((float(ref["ts"]) - t0) / 60.0))


class RecordOnEntry(ExitPolicyEnv):
    def test_finalize_entry_writes_pos_exit_and_limits(self):
        pol = self.snapshot({"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "taker"}]}, ver="X-0007")
        pid = self.enter()
        rt = self.e.rt(pid)
        ex = rt["pos"]["exit"]
        self.assertEqual(ex["ver"], "X-0007")
        self.assertEqual(ex["why"], "OK")
        self.assertEqual(ex["max_hold_min"], 3)
        self.assertEqual(ex["horizon"], "intraday")
        self.assertEqual(ex["policy_h"], exitpol.policy_hash(pol))
        self.assertEqual(ex["policy"], pol)
        self.assertIsNone(ex["atr5_pct"])          # 5Рј РёСЃС‚РѕСЂРёРё РЅР° РІС…РѕРґРµ РЅРµС‚
        lim = self.e.ex.limits_for(self.e.ex.plans[pid])
        self.assertEqual(lim["exit_policy"], pol)
        self.assertEqual(lim["exit_ctx"]["max_hold_min"], 3)
        self.assertIsNone(self.e.ex.exit_status)

    def test_adopt_position_writes_pos_exit(self):
        pol = self.snapshot({"timeout": {"hold_frac": 1.0, "exec": "taker"}})
        self.e.port.pos["SOL"] = {"sym": "SOL", "side": "long", "size": 1.0, "avg": 99.0, "mark": 100.0, "sl": 95.0, "tp": 0.0,
                                  "liq": 50.0, "upnl": 0.0, "lev": 5.0, "margin": 20.0, "ts": 0.0}
        r = self.e.command("adopt", None, {"symbol": "SOL"})
        self.assertTrue(r["ok"], r)
        ex = self.e.rt(r["plan_id"])["pos"]["exit"]
        self.assertEqual((ex["ver"], ex["why"], ex["policy"]), ("X-0002", "OK", pol))


class Parity(ExitPolicyEnv):
    cfg_over = {"auto_protect": {"enabled": False}}

    def _run(self, order):
        pol = self.snapshot({"partial": [{"at_r": 1.0, "frac": 0.5, "exec": "taker"}],
                             "timeout": {"hold_frac": 1.0, "exec": "taker"}})
        pid = self.enter()
        rt = self.e.rt(pid)
        avg, stop = float(rt["pos"]["avg"]), float(rt["pos"]["initial_stop"])
        ru = abs(avg - stop)
        level = avg + ru
        t0 = float(rt["ts"]["entered"])
        rows = [(t0, avg, avg + 0.05, avg - 0.05, avg),
                (t0 + 60, avg + 0.1, level, avg - 0.1, avg + 0.5),
                (t0 + 120, avg + 0.6, avg + 0.6, avg + 0.6, avg + 0.6)]
        ticks = [(t0 + 60.2, avg + 0.1), (t0 + 60.4, avg - 0.1), (t0 + 60.6, level), (t0 + 60.8, avg + 0.5),
                 (t0 + 180.2, avg + 0.6)]
        if order == "T":
            ticks = [(t0 + 60.2, avg + 0.1), (t0 + 60.4, level), (t0 + 60.6, avg - 0.1), (t0 + 60.8, avg + 0.5),
                     (t0 + 180.2, avg + 0.6)]
        for ts, px in ticks:
            self.tick_at(ts, px)
        rt = self.e.rt(pid)
        sim = exitsim.run(self.sim_plan(rt), rows, pol, self.floor(), COSTS, order=order,
                          lot={"qty": float(rt["pos"]["entries"][0]["qty"]), "qty_step": "0.1", "min_qty": "0.1"})
        self.assertTrue(sim["filled"])
        self.assert_parts(rt, sim, t0)
        return rt, sim

    def test_parity_order_s_without_stop_rules(self):
        rt, sim = self._run("S")
        self.assertEqual(rt["status"], "closed")
        self.assertEqual(sim["exit_reason"], "timeout")
        self.assertEqual(rt["close"]["reason"], "rule")
        self.assertEqual(rt["close"]["rule"], "xp_timeout")
        self.assertGreaterEqual(len(rt["realized"]["parts"]), 2)

    def test_parity_order_t_in_candle(self):
        rt, sim = self._run("T")
        self.assertEqual(rt["status"], "closed")
        self.assertEqual(sim["exit_reason"], "timeout")
        self.assertEqual(len(rt["realized"]["parts"]), len(sim["parts"]))
        self.assertEqual(rt["close"]["reason"], "rule")
        self.assertEqual(rt["close"]["rule"], "xp_timeout")


class ParityStop(ExitPolicyEnv):
    def test_new_stop_inside_candle_order_t(self):
        self.snapshot({"be": {"r": 0.5}, "lock": [{"at_r": 1.0, "keep": 0.5}]})
        pid = self.enter()
        rt = self.e.rt(pid)
        avg, stop = float(rt["pos"]["avg"]), float(rt["pos"]["initial_stop"])
        ru = abs(avg - stop)
        t0 = float(rt["ts"]["entered"])
        high = avg + 2.0 * ru
        rows = [(t0, avg, avg + 0.05, avg - 0.05, avg),
                (t0 + 60, avg + 0.3, high, avg - 0.1, avg + 1.0)]
        for ts, px in ((t0 + 60.2, avg + 0.3), (t0 + 60.4, high), (t0 + 60.6, avg - 0.1)):
            self.tick_at(ts, px)
        rt = self.e.rt(pid)
        sim = exitsim.run(self.sim_plan(rt, hold=600), rows, rt["pos"]["exit"]["policy"], self.floor(), COSTS, order="T",
                          lot={"qty": float(rt["pos"]["entries"][0]["qty"]), "qty_step": "0.1", "min_qty": "0.1"})
        self.assertEqual(rt["status"], "closed")
        self.assertEqual(sim["exit_reason"], "trail")
        self.assertAlmostEqual(float(rt["pos"]["cur_stop"]), round(avg + 0.5 * 2.0 * ru, 10), places=6)
        self.assert_parts(rt, sim, t0)


class PolicyRules(ExitPolicyEnv):
    def test_policy_actions_log_xp_and_rule_fired(self):
        self.snapshot({"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "taker"}], "be": {"r": 0.5}})
        pid = self.enter()
        avg = float(self.e.rt(pid)["pos"]["avg"])
        self.e.tick(avg + 1.2)
        rules = [d for d in self.e.decisions("rule") if d.get("what") == "partial_close"]
        self.assertTrue(rules, self.e.decisions("rule"))
        self.assertTrue(any(str(d.get("why")).startswith("XP:partial") for d in rules), rules)
        fired = [e for e in self.e.bus("rule_fired") if str(e["d"].get("rule")).startswith("xp_")]
        self.assertTrue(fired, self.e.bus("rule_fired"))

    def test_hold_and_kill_do_not_block_policy(self):
        self.snapshot({"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "taker"}],
                       "timeout": {"hold_frac": 0.5, "exec": "taker"}})
        pid = self.enter()
        p = self.e.ex.plans[pid]
        p["rt"]["hold"], p["rt"]["hold_reason"] = "brain", "brain"
        self.e.ex.save(p, sync=True)
        rt = self.e.rt(pid)
        avg, stop = float(rt["pos"]["avg"]), float(rt["pos"]["initial_stop"])
        t0 = float(rt["ts"]["entered"])
        self.tick_at(t0 + 10.2, avg + 1.2 * abs(avg - stop))
        self.assertLess(float(self.e.rt(pid)["pos"]["qty"]), float(rt["pos"]["qty"]))       # hold РЅРµ Р·Р°РґРµСЂР¶Р°Р» С„РёРєСЃР°С†РёСЋ
        exec_cfg.set_kill(flatten=False, reason="test", root=self.e.root)
        self.tick_at(t0 + 100.2, avg + 1.3 * abs(avg - stop))
        self.assertEqual(self.e.rt(pid)["status"], "closed")                                # KILL РЅРµ Р±Р»РѕРєРёСЂСѓРµС‚ RISK_DOWN


class VersionOnPosition(ExitPolicyEnv):
    def test_snapshot_change_does_not_change_open_position(self):
        pol_a = self.snapshot({"partial": [{"at_r": 1.0, "frac": 0.2, "exec": "taker"}]}, ver="X-0002")
        pid = self.enter()
        rt = self.e.rt(pid)
        avg = float(rt["pos"]["avg"])
        stop = float(rt["pos"]["initial_stop"])
        self.snapshot({"be": {"r": 0.4}, "partial": [{"at_r": 2.0, "frac": 0.5, "exec": "taker"}]}, ver="X-0003")
        self.assertEqual(self.e.rt(pid)["pos"]["exit"]["ver"], "X-0002")
        self.e.tick(avg + 0.6 * abs(avg - stop))
        self.assertEqual(float(self.e.port.pos["SOL"]["sl"]), stop)          # be РІРµСЂСЃРёРё X-0003 РЅРµ СЃСЂР°Р±РѕС‚Р°Р»
        self.e.tick(avg + 1.1 * abs(avg - stop))
        self.assertLess(float(self.e.rt(pid)["pos"]["qty"]), float(rt["pos"]["qty"]))       # partial РІРµСЂСЃРёРё X-0002 СЃСЂР°Р±РѕС‚Р°Р»
        self.assertEqual(self.e.rt(pid)["pos"]["exit"]["policy"], pol_a)
        self.assertEqual(self.e.rt(pid)["pos"]["exit"]["ver"], "X-0002")

    def test_stricter_floor_acts_over_position_version(self):
        self.snapshot({"be": {"r": 1.0}})
        pid = self.enter()
        rt = self.e.rt(pid)
        avg, stop = float(rt["pos"]["avg"]), float(rt["pos"]["initial_stop"])
        self.e.ex.cfg["auto_protect"]["be_r"] = 0.5            # владелец ужесточил пол: он действует поверх версии 1.0
        self.e.tick(avg + 0.6 * abs(avg - stop))
        self.assertGreater(float(self.e.port.pos["SOL"]["sl"]), avg)
        self.assertEqual(self.e.rt(pid)["pos"]["exit"]["policy"]["be"]["r"], 1.0)     # версия позиции не изменилась

    def test_new_position_uses_new_version(self):
        self.snapshot({"partial": [{"at_r": 1.0, "frac": 0.2, "exec": "taker"}],
                       "timeout": {"hold_frac": 1.0, "exec": "taker"}}, ver="X-0002")
        pid = self.enter()
        self.tick_at(float(self.e.rt(pid)["ts"]["entered"]) + 180.2, 100.3)
        self.assertEqual(self.e.rt(pid)["status"], "closed")
        self.e.timer(1, dt=1.0)
        self.snapshot({"partial": [{"at_r": 1.0, "frac": 0.5, "exec": "taker"}]}, ver="X-0003")
        pid2 = self.enter()
        self.assertEqual(self.e.rt(pid2)["pos"]["exit"]["ver"], "X-0003")


class LeaseAndFallback(ExitPolicyEnv):
    def test_promotion_lease_ok_then_stale_lease_floor(self):
        self.snapshot({"partial": [{"at_r": 1.0, "frac": 0.2, "exec": "taker"}],
                       "timeout": {"hold_frac": 1.0, "exec": "taker"}}, ver="X-0002")
        pid = self.enter()
        self.assertEqual(self.e.rt(pid)["pos"]["exit"]["why"], "OK")
        self.tick_at(float(self.e.rt(pid)["ts"]["entered"]) + 180.2, 100.3)
        self.assertEqual(self.e.rt(pid)["status"], "closed")
        self.e.timer(1, dt=1.0)
        old = self.e.clock.time() - 7 * 3600.0
        self.snapshot({"partial": [{"at_r": 1.0, "frac": 0.5, "exec": "taker"}]}, ver="X-0003", lease_ts=old)
        pid2 = self.enter()
        ex = self.e.rt(pid2)["pos"]["exit"]
        self.assertEqual((ex["ver"], ex["why"]), (exitdoc.FLOOR_VER, "LEASE"))
        self.assertEqual(ex["policy"], self.floor())
        self.assertEqual(self.e.ex.exit_status["code"], "EXIT_POLICY_FALLBACK:LEASE")
        self.assertEqual(self.e.ex._exit_status_from({"PL-x": "LEASE"})["plan"], "PL-x")

    def test_no_snapshot_falls_back_to_floor(self):
        pid = self.enter()
        ex = self.e.rt(pid)["pos"]["exit"]
        self.assertEqual((ex["ver"], ex["why"]), (exitdoc.FLOOR_VER, "SNAPSHOT"))
        self.assertEqual(ex["policy"], self.floor())
        self.assertEqual(self.e.ex.exit_status["code"], "EXIT_POLICY_FALLBACK:SNAPSHOT")

    def test_readable_record_survives_restart(self):
        pol = self.snapshot({"timeout": {"hold_frac": 1.0, "exec": "taker"}})
        pid = self.enter()
        self.snapshot({"be": {"r": 0.4}}, ver="X-0003")          # СЃРЅРёРјРѕРє СЃРјРµРЅРёР»СЃСЏ РґРѕ РїРµСЂРµР·Р°РїСѓСЃРєР°
        from tb.executor.core import Executor
        ex2 = Executor(self.e.port, root=self.e.root, clock=self.e.clock.time, sleep=self.e.clock.advance, profile="testnet")
        ex2.start()
        p2 = ex2.plans[pid]
        self.assertEqual(ex2.limits_for(p2)["exit_policy"], pol)
        self.assertIsNone(ex2.exit_status)

    def test_unreadable_position_policy_after_restart_falls_to_floor(self):
        self.snapshot({"partial": [{"at_r": 1.0, "frac": 0.2, "exec": "taker"}]})
        pid = self.enter()
        p = self.e.ex.plans[pid]
        p["rt"]["pos"]["exit"] = {"ver": "X-0002", "policy_h": "deadbeef", "why": "OK"}
        self.e.ex.save(p, sync=True)
        from tb.executor.core import Executor
        ex2 = Executor(self.e.port, root=self.e.root, clock=self.e.clock.time, sleep=self.e.clock.advance, profile="testnet")
        ex2.start()
        lim = ex2.limits_for(ex2.plans[pid])
        self.assertEqual(lim["exit_policy"], self.floor())
        self.assertEqual(ex2.exit_status["code"], "EXIT_POLICY_FALLBACK:POS")


class TimeoutFromEntry(ExitPolicyEnv):
    def test_brain_hold_extension_does_not_move_timeout(self):
        self.snapshot({"timeout": {"hold_frac": 1.0, "exec": "taker"}})
        pid = self.enter()
        rt = self.e.rt(pid)
        avg, stop = float(rt["pos"]["avg"]), float(rt["pos"]["initial_stop"])
        t0 = float(rt["ts"]["entered"])
        self.tick_at(t0 + 30.2, avg + 0.8 * abs(avg - stop))            # Р±РµР·СѓР±С‹С‚РѕРє: РїСЂРѕРґР»РµРЅРёРµ СЃСЂРѕРєР° СЂР°Р·СЂРµС€РµРЅРѕ
        r = self.e.command("amend", pid, payload={"lifecycle": {"max_hold_min": 30}})
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.e.spec(pid)["lifecycle"]["max_hold_min"], 30)
        self.assertEqual(self.e.rt(pid)["pos"]["exit"]["max_hold_min"], 3)
        self.tick_at(t0 + 150.2, avg + 0.5)
        self.assertEqual(self.e.rt(pid)["status"], "managing")       # СЃСЂРѕРє РїР»Р°РЅР° РїСЂРѕРґР»С‘РЅ, С‚Р°Р№Рј-Р°СѓС‚ РєРѕРґР° вЂ” РЅРµС‚
        self.tick_at(t0 + 180.2, avg + 0.4)
        rt = self.e.rt(pid)
        self.assertEqual(rt["status"], "closed")
        self.assertEqual((rt["close"]["reason"], rt["close"]["rule"]), ("rule", "xp_timeout"))
        tr = self.e.trades()[0]
        self.assertEqual(tr.get("exit_rule"), "timeout")
        res = features.result_from_close(tr, tr)
        self.assertEqual(res["exit_rule"], "timeout")
        self.assertIn(res["exit_rule"], cases.EXIT_RULES)


class ReconcilePolicyOrders(unittest.TestCase):
    def setUp(self):
        self.e = Env()
        self.pid = self.e.enter()
        self.plan_n = int(self.e.rt(self.pid)["plan_n"])

    def tearDown(self):
        self.e.close()

    def _order(self, link):
        self.e.port.orders[link] = {"sym": "SOL", "link_id": link, "order_id": "O-" + link, "side": "Sell", "type": "limit", "price": 110.0,
                                    "qty": 0.1, "filled": 0.0, "reduce_only": True, "status": "New", "ts": 0.0}
        return link

    def test_policy_order_of_open_position_is_not_cancelled(self):
        link = self._order("tbx-xp-%d-xp_partial_0" % self.plan_n)
        self.e.ex.need_reconcile = True
        self.e.timer(1, dt=31)
        self.assertEqual(self.e.port.orders[link]["status"], "New")

    def test_policy_order_of_closed_position_is_cancelled(self):
        link = self._order("tbx-xp-999-xp_partial_0")
        self.e.ex.need_reconcile = True
        self.e.timer(1, dt=31)
        self.assertEqual(self.e.port.orders[link]["status"], "Cancelled")


if __name__ == "__main__":
    unittest.main()
