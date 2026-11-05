"""R4: контрфактический симулятор выхода `tb.logic.exitsim` (Requirement 3; свойства 6, 7, 9, 10, 25, 27).

Сценарии задаются минутными свечами R(i, o, h, l, c) = (60*i, o, h, l, c, 1.0) и входом `entry_px` (как у пути прогона).
Издержки в ручных сценариях нулевые, если не оговорено; комиссии taker 0.001 / maker 0.0005, проскальзывание 0, стоп
без проскальзывания и спреда. Пол (`floor`) — `exec_limits.auto_protect`; пустая политика — только начальный стоп.

Эталон свойства 10 — `logic/sim.py` на путях, где правила пола по цене не сработали до выхода (ревизия 2026-11-04:
на остальных прогонах `sim.py` срабатывает правилами пола по закрытию, а `exitsim` — на экстремуме).
"""
import random
import unittest

import _bootstrap  # noqa: F401
from tb.logic import exitpol, exitsim, qty, sim

COSTS = {"fee": 0.001, "maker_fee": 0.0005, "slip": 0.0, "stop_slip_bps": 0.0, "spread_bps": 0.0}
AP = {"enabled": True, "be_r": 0.7, "trail_activate_r": 1.0, "trail_lock_frac": 0.5, "min_profit_r": 0.2, "stop_slip_bps": 0}
FLOOR = exitpol.floor(AP)
FAR = {"enabled": True, "be_r": 5.0, "trail_activate_r": 5.0, "trail_lock_frac": 0.5, "min_profit_r": 0.2, "stop_slip_bps": 0}
FAR_FLOOR = exitpol.floor(FAR)
EMPTY = {"schema": exitpol.SCHEMA}
POL = {"schema": exitpol.SCHEMA}


def R(i, o, h, l, c):
    return (60 * i, o, h, l, c, 1.0)


TB = 1760000000                       # реальная метка epoch: `conds.elapsed` не считает время от нулевой метки


def TR(i, o, h, l, c):
    return (TB + 60 * i, o, h, l, c, 1.0)


def plan(side="long", stop=99.0, tps=(), fracs=None, kind="market", level=100.0, hold=240, tick=0.01):
    p = {"side": side, "entry": {"kind": kind, "level": level, "ttl_min": 30}, "stop": stop, "tps": list(tps),
         "max_hold_min": hold, "tick": tick}
    if fracs is not None:
        p["fracs"] = list(fracs)
    return p


def go(p, rows, policy=None, floor=None, costs=None, **kw):
    return exitsim.run(p, rows, POL if policy is None else policy, EMPTY if floor is None else floor,
                       COSTS if costs is None else costs, **kw)


def flat(n, px=100.0):
    return [R(i, px, px, px, px) for i in range(n)]


class TestProperty6Determinism(unittest.TestCase):
    def test_same_args_same_result(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 101.2, 99.5, 100.8), R(2, 100.5, 100.9, 99.6, 99.9)]
        pol = {"schema": exitpol.SCHEMA, "lock": [{"at_r": 1.0, "keep": 0.5}]}
        a, b = go(plan(), rows, pol, FLOOR), go(plan(), rows, pol, FLOOR)
        self.assertEqual(a, b)

    def test_source_has_no_argument_mutation(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.5, 100)]
        p = plan(tps=[101.0])
        snapshot = dict(p)
        policy = {"schema": exitpol.SCHEMA, "partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]}
        pol_snapshot = {"schema": exitpol.SCHEMA, "partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]}
        go(p, rows, policy, FLOOR)
        self.assertEqual(p, snapshot)
        self.assertEqual(policy, pol_snapshot)


class TestProperty10ReferenceSim(unittest.TestCase):
    """Эталон свойства 10: `exitsim(X-0001, S)` совпадает с `logic/sim.py` на прогонах без сработавших правил пола."""

    def check(self, p, rows, costs=COSTS):
        ref = sim.run(p, rows, -60, costs, {"latency_s": 60, "auto_protect": FAR})
        self.assertTrue(ref["filled"])
        q = dict(p)
        q["entry_px"] = ref["entry_px"]
        got = exitsim.run(q, rows, POL, FAR_FLOOR, costs, order="S")
        self.assertTrue(got["filled"])
        self.assertAlmostEqual(got["r_net"], ref["r_net"], places=9, msg=ref["exit_reason"])
        self.assertAlmostEqual(got["r_gross"], ref["r_gross"], places=9)
        self.assertAlmostEqual(got["fees_r"], ref["fees_r"], places=9)
        self.assertEqual(got["exit_reason"], ref["exit_reason"])
        self.assertEqual(len(got["parts"]), len(ref["parts"]))
        for a, b in zip(got["parts"], ref["parts"]):
            self.assertAlmostEqual(a["px"], b["px"], places=9)
            self.assertAlmostEqual(a["frac"], b["frac"], places=9)
        return got, ref

    def test_stop(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.5, 100), R(2, 100, 100.2, 98.9, 99)]
        got, ref = self.check(plan(), rows)
        self.assertEqual((got["exit_reason"], got["entry_ts"]), ("sl", 0))

    def test_take_profit(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.5, 100), R(2, 100, 102.01, 99.5, 102)]
        self.check(plan(tps=[102.0]), rows)

    def test_two_take_profits_with_gap(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.5, 100), R(2, 100, 101.01, 99.5, 101),
                R(3, 102, 103.02, 101.9, 103)]
        self.check(plan(tps=[101.0, 103.0], fracs=[0.5, 0.5]), rows)

    def test_stop_gap_on_open(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.2, 99.8, 100), R(2, 98.0, 98.4, 97.5, 98.1)]
        got, ref = self.check(plan(), rows)
        self.assertAlmostEqual(got["parts"][-1]["px"], 98.0)      # гэп через стоп: по открытию, а не по 99

    def test_short(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.5, 100), R(2, 100, 101.2, 99.9, 101)]
        self.check(plan(side="short", stop=101.0), rows)

    def test_stop_with_slippage_and_spread(self):
        costs = {"fee": 0.001, "maker_fee": 0.0005, "slip": 0.0002, "stop_slip_bps": 5.0, "spread_bps": 2.0}
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.5, 100), R(2, 100, 100.1, 98.9, 99)]
        self.check(plan(), rows, costs)


    def test_random_paths_match_sim_where_floor_rules_do_not_fire(self):
        rng = random.Random(4242)
        compared = 0
        for _ in range(60):
            side = rng.choice(["long", "short"])
            px, rows = 100.0, []
            for i in range(rng.randint(20, 60)):
                o = px
                c = max(1.0, o + rng.gauss(0, 0.35))
                h = max(o, c) + abs(rng.gauss(0, 0.2))
                l = min(o, c) - abs(rng.gauss(0, 0.2))
                rows.append((60 * i, round(o, 4), round(h, 4), round(l, 4), round(c, 4), 1.0))
                px = c
            stop = round(100.0 - rng.uniform(1.0, 2.0), 4) if side == "long" else round(100.0 + rng.uniform(1.0, 2.0), 4)
            tp = round(100.0 + rng.uniform(0.6, 3.0), 4) if side == "long" else round(100.0 - rng.uniform(0.6, 3.0), 4)
            p = {"side": side, "entry": {"kind": "market", "level": 100.0, "ttl_min": 30}, "stop": stop, "tps": [tp],
                 "fracs": [0.5], "max_hold_min": 30, "tick": 0.01}
            ref = sim.run(p, rows, -60, COSTS, {"latency_s": 60, "auto_protect": FAR})
            if not ref["filled"] or ref["exit_reason"] == "timeout":
                continue
            q = dict(p)
            q["entry_px"] = ref["entry_px"]
            got = exitsim.run(q, rows, POL, FAR_FLOOR, COSTS, order="S")
            compared += 1
            self.assertEqual(got["exit_reason"], ref["exit_reason"])
            self.assertAlmostEqual(got["r_net"], ref["r_net"], places=9)
        self.assertGreater(compared, 20)


class TestEntryCandle(unittest.TestCase):
    def test_stop_checked_in_entry_candle(self):
        rows = [R(0, 100, 100, 98.8, 99.2), R(1, 99.2, 99.4, 98.0, 98.4)]
        r = go(plan(stop=99.0), rows)
        self.assertEqual((r["exit_reason"], r["parts"][0]["ts"]), ("sl", 0))
        self.assertAlmostEqual(r["r_net"], -1.0 - 0.199)

    def test_market_entry_fills_at_open(self):
        r = go(plan(), flat(3))
        self.assertAlmostEqual(r["entry_px"], 100.0)
        self.assertEqual((r["entry_ts"], r["exit_reason"]), (0, "end"))

    def test_limit_entry_price_after_fill_used_only_for_stop(self):
        # лимит 99.5: свеча входа заходит за уровень (low 99.4), high 101.0 — но после заполнения эта цена только для стопа
        p = plan(kind="limit", level=99.5, stop=98.5, tps=[100.5])
        rows = [R(0, 100, 101.0, 99.4, 100.9), R(1, 100.9, 101.2, 100.4, 101.0)]
        r = go(p, rows)
        self.assertTrue(r["filled"])
        self.assertAlmostEqual(r["entry_px"], 99.5)
        self.assertEqual([x["why"] for x in r["parts"]], ["tp1"])
        self.assertEqual(r["parts"][0]["ts"], 60)
        self.assertAlmostEqual(r["mfe_r"], 1.7, places=12)     # MFE со свечи 1 (high 101.2), не со свечи входа

    def test_limit_entry_not_filled(self):
        p = plan(kind="limit", level=98.0, stop=97.0)
        rows = [R(0, 100, 100.2, 99.0, 99.5), R(1, 99.5, 99.8, 99.1, 99.6)]
        r = go(p, rows)
        self.assertFalse(r["filled"])
        self.assertEqual(r["exit_reason"], "no_fill")


class TestOrderAndAmbiguity(unittest.TestCase):
    def test_stop_and_take_profit_same_candle_order_s(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 102.5, 98.5, 99.0)]
        r = go(plan(tps=[102.0, 105.0], fracs=[0.5, 0.5]), rows, order="S")
        self.assertEqual(r["exit_reason"], "sl")
        self.assertEqual(len(r["parts"]), 1)
        self.assertAlmostEqual(r["parts"][0]["frac"], 1.0)
        self.assertTrue(r["amb"])

    def test_stop_and_take_profit_same_candle_order_t(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 102.5, 98.5, 99.0)]
        r = go(plan(tps=[102.0, 105.0], fracs=[0.5, 0.5]), rows, order="T")
        self.assertEqual([x["why"] for x in r["parts"]], ["tp1", "sl"])
        self.assertAlmostEqual(r["parts"][0]["frac"], 0.5)
        self.assertAlmostEqual(r["parts"][1]["frac"], 0.5)
        self.assertTrue(r["amb"])

    def test_unambiguous_path_s_equals_t(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.6, 99.4, 100.2), R(2, 100.2, 101.4, 99.9, 101.2),
                R(3, 101.2, 101.6, 100.4, 100.6)]
        pol = {"schema": exitpol.SCHEMA, "partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]}
        a, b = go(plan(), rows, pol, EMPTY, order="S"), go(plan(), rows, pol, EMPTY, order="T")
        self.assertFalse(a["amb"])
        self.assertAlmostEqual(a["r_net"], b["r_net"], places=12)
        self.assertEqual([x["px"] for x in a["parts"]], [x["px"] for x in b["parts"]])

    def test_new_stop_acts_inside_candle_only_in_t(self):
        # lock 1.0/0.5: свеча 1 даёт MFE 1.2R (high 101.2) и возвращается к 100.4 — новый стоп 100.6
        pol = {"schema": exitpol.SCHEMA, "lock": [{"at_r": 1.0, "keep": 0.5}]}
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 101.2, 100.4, 100.5), R(2, 100.5, 100.7, 99.5, 99.7)]
        t = go(plan(), rows, pol, FLOOR, order="T")
        s = go(plan(), rows, pol, FLOOR, order="S")
        self.assertEqual((t["exit_reason"], t["parts"][-1]["ts"]), ("trail", 60))
        self.assertAlmostEqual(t["parts"][-1]["px"], 100.6)       # 100 + 0.5 * 1.2R
        self.assertEqual((s["exit_reason"], s["parts"][-1]["ts"]), ("trail", 120))
        self.assertAlmostEqual(s["parts"][-1]["px"], 100.5)       # свеча 2 открылась ниже нового стопа: гэп по открытию
        self.assertTrue(t["amb"])
        self.assertTrue(s["amb"])

    def test_breakeven_fires_at_extreme_not_close(self):
        # pnl 0.8R (high 100.8) -> auto_be переносит стоп на пол прибыли; close 99.7 ниже старого стопа нет
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.8, 99.6, 99.7), R(2, 100.2, 100.3, 99.6, 99.8)]
        r = go(plan(), rows, POL, FLOOR, order="S")
        self.assertEqual(r["exit_reason"], "be")
        self.assertGreater(r["parts"][-1]["px"], 100.0)


class TestSplitReferenceParity(unittest.TestCase):
    """Requirement 3.10 (часть задачи 6): разбиение свечи на однозначные под-свечи в порядке S/T даёт тот же результат."""

    @staticmethod
    def split(rows, side, favourable_first):
        out = []
        for ts, o, h, l, c, v in rows:
            if side == "long":
                adv, fav = l, h
            else:
                adv, fav = h, l
            pts = [o, fav, adv, c] if favourable_first else [o, adv, fav, c]
            for a, b in zip(pts, pts[1:]):
                out.append((ts, a, max(a, b), min(a, b), b, v))
        return out

    def _compare(self, p, rows, order, floor=None):
        got = go(p, rows, POL, floor, order=order)
        sub = self.split(rows, p["side"], favourable_first=(order == "T"))
        ref = go(p, sub, POL, floor, order=order)
        self.assertTrue(got["filled"] and ref["filled"])
        self.assertAlmostEqual(got["r_net"], ref["r_net"], places=9, msg="%s %s" % (p["side"], order))
        self.assertEqual([x["why"] for x in got["parts"]], [x["why"] for x in ref["parts"]])
        for a, b in zip(got["parts"], ref["parts"]):
            self.assertAlmostEqual(a["px"], b["px"], places=9)
            self.assertAlmostEqual(a["frac"], b["frac"], places=9)

    def test_long_stop_before_fixation_like_split(self):
        rows = [R(0, 100, 100.2, 99.8, 100), R(1, 100, 101.2, 99.3, 100.4), R(2, 100.4, 100.8, 98.7, 99.1)]
        self._compare(plan(tps=[100.9]), rows, "S")
        self._compare(plan(), rows, "S")

    def test_long_favourable_first_like_split(self):
        rows = [R(0, 100, 100.2, 99.8, 100), R(1, 100, 101.2, 99.3, 100.4), R(2, 100.4, 100.8, 98.7, 99.1)]
        self._compare(plan(tps=[100.9]), rows, "T", FLOOR)
        self._compare(plan(), rows, "T", FLOOR)

    def test_short_both_orders(self):
        rows = [R(0, 100, 100.2, 99.8, 100), R(1, 100, 101.4, 99.1, 100.6), R(2, 100.6, 101.3, 99.9, 101.0)]
        p = plan(side="short", stop=101.0, tps=[99.2])
        self._compare(p, rows, "S")
        self._compare(p, rows, "T", FLOOR)


class TestFeesAndMaker(unittest.TestCase):
    def test_fees_equal_sum_of_parts(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.6, 99.5, 100.4), R(2, 100.4, 101.3, 100.1, 101.1)]
        pol = {"schema": exitpol.SCHEMA, "partial": [{"at_r": 1.0, "frac": 0.4, "exec": "maker"}]}
        r = go(plan(tps=[100.8], fracs=[1.0]), rows, pol, FLOOR)
        ru = abs(r["entry_px"] - 99.0)
        want = r["entry_px"] * COSTS["fee"] + sum(p["px"] * p["frac"] * (COSTS["maker_fee"] if p["exec"] == "maker" else COSTS["fee"])
                                                  for p in r["parts"])
        self.assertAlmostEqual(r["fees_r"], want / ru, places=12)

    def test_maker_fills_after_pass(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 101.03, 99.6, 100.8), R(2, 100.8, 101.0, 100.2, 100.4)]
        pol = {"schema": exitpol.SCHEMA, "partial": [{"at_r": 1.0, "frac": 0.4, "exec": "maker"}]}
        r = go(plan(), rows, pol, EMPTY)
        self.assertEqual(r["parts"][0]["why"], "rule")
        self.assertEqual(r["parts"][0]["exec"], "maker")
        self.assertAlmostEqual(r["parts"][0]["px"], 101.0)

    def test_maker_not_filled_on_touch_without_pass(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 101.01, 99.6, 100.8), R(2, 100.8, 100.95, 100.2, 100.4)]
        pol = {"schema": exitpol.SCHEMA, "partial": [{"at_r": 1.0, "frac": 0.4, "exec": "maker"}]}
        r = go(plan(), rows, pol, EMPTY)
        self.assertEqual([p["exec"] for p in r["parts"]], ["taker"])       # только выход по концу пути
        self.assertEqual(r["exit_reason"], "end")

    def test_maker_touch_without_pass_then_fills_next_candle(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 101.01, 99.6, 100.8), R(2, 100.8, 101.05, 100.2, 100.4)]
        pol = {"schema": exitpol.SCHEMA, "partial": [{"at_r": 1.0, "frac": 0.4, "exec": "maker"}]}
        r = go(plan(), rows, pol, EMPTY)
        self.assertEqual(r["parts"][0]["exec"], "maker")
        self.assertEqual(r["parts"][0]["ts"], 120)

    def test_taker_only_pays_no_maker_fee(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 101.1, 99.6, 100.8), R(2, 100.8, 101.0, 100.2, 100.4)]
        pol = {"schema": exitpol.SCHEMA, "partial": [{"at_r": 1.0, "frac": 0.4, "exec": "maker"}]}
        costs = dict(COSTS, slip=0.0002)
        r = go(plan(), rows, pol, EMPTY, costs=costs, taker_only=True)
        self.assertEqual([p["exec"] for p in r["parts"]], ["taker", "taker"])
        level = r["entry_px"] + abs(r["entry_px"] - 99.0)
        self.assertLess(r["parts"][0]["px"], level)
        self.assertGreater(r["fees_r"], go(plan(), rows, pol, EMPTY, costs=costs)["fees_r"])

    def test_timeout_maker_fills_next_candle(self):
        pol = {"schema": exitpol.SCHEMA, "timeout": {"hold_frac": 0.5, "exec": "maker"}}
        rows = [TR(i, 100, 100, 100, 100) for i in range(6)] + [TR(6, 100, 100.05, 99.95, 100.02)]
        r = go(plan(hold=12), rows, pol, EMPTY)
        self.assertEqual((r["exit_reason"], r["parts"][-1]["exec"]), ("timeout", "maker"))
        self.assertEqual(r["parts"][-1]["ts"], TB + 360)
        self.assertAlmostEqual(r["parts"][-1]["px"], 100.0)

    def test_timeout_maker_without_pass_goes_taker_at_next_close(self):
        pol = {"schema": exitpol.SCHEMA, "timeout": {"hold_frac": 0.5, "exec": "maker"}}
        rows = [TR(i, 100, 100, 100, 100) for i in range(6)] + [TR(6, 100, 100.01, 99.9, 99.95),
                                                               TR(7, 99.95, 100.0, 99.9, 99.92)]
        r = go(plan(hold=12), rows, pol, EMPTY)
        self.assertEqual((r["exit_reason"], r["parts"][-1]["exec"]), ("timeout", "taker"))
        self.assertEqual(r["parts"][-1]["ts"], TB + 420)              # закрытие следующей свечи
        self.assertAlmostEqual(r["parts"][-1]["px"], 99.95)


class TestProperty9NoMakerFeeWithoutPass(unittest.TestCase):
    def test_no_pass_no_maker_part(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.99, 99.6, 99.8)]
        pol = {"schema": exitpol.SCHEMA, "partial": [{"at_r": 1.0, "frac": 0.4, "exec": "maker"}]}
        r = go(plan(), rows, pol, FLOOR)
        self.assertFalse([p for p in r["parts"] if p["exec"] == "maker"])


class TestCutAndEnd(unittest.TestCase):
    def test_base_holds_to_path_end(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 101.0, 99.8, 100.9), R(2, 100.9, 101.0, 100.6, 100.8)]
        r = go(plan(), rows, EMPTY, EMPTY)
        self.assertEqual(r["exit_reason"], "end")
        self.assertEqual(r["parts"][-1]["ts"], 180)

    def test_pair_cut_when_one_side_reaches_end(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 102.6, 99.5, 102.2)]
        var = {"schema": exitpol.SCHEMA, "partial": [{"at_r": 2.0, "frac": 0.5, "exec": "taker"}]}
        res = exitsim.pair(plan(), rows, var, EMPTY, EMPTY, COSTS)
        self.assertTrue(res["cut"])
        self.assertIsNotNone(res["d"])
        self.assertAlmostEqual(res["d"], res["d_lo"], places=12)

    def test_pair_not_cut_when_both_exit(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.4, 98.5, 98.8)]
        res = exitsim.pair(plan(), rows, EMPTY, EMPTY, EMPTY, COSTS)
        self.assertFalse(res["cut"])
        self.assertAlmostEqual(res["d"], 0.0, places=12)

    def test_pair_unfilled_path_has_no_differences(self):
        p = plan(kind="limit", level=90.0, stop=88.0)
        rows = [R(0, 100, 100.1, 99.0, 99.5)]
        res = exitsim.pair(p, rows, EMPTY, EMPTY, EMPTY, COSTS)
        self.assertIsNone(res["d"])
        self.assertIsNone(res["d_lo"])


class TestProperty7LowerBound(unittest.TestCase):
    @staticmethod
    def path(rng, side, n=40):
        px, rows = 100.0, []
        for i in range(n):
            drift = rng.uniform(-0.6, 0.7)
            o = px
            c = max(1.0, o + drift)
            h = max(o, c) + rng.uniform(0.0, 0.5)
            l = min(o, c) - rng.uniform(0.0, 0.5)
            rows.append((60 * i, round(o, 4), round(h, 4), round(l, 4), round(c, 4), 1.0))
            px = c
        return rows

    POLICIES = ({}, {"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]},
                {"lock": [{"at_r": 1.0, "keep": 0.5}]},
                {"timeout": {"hold_frac": 0.5, "exec": "maker"}})

    def test_d_lo_le_d_and_equal_without_ambiguity(self):
        rng = random.Random(20261104)
        for side in ("long", "short"):
            for body in self.POLICIES:
                var = dict(body, schema=exitpol.SCHEMA)
                for k in range(8):
                    rows = self.path(rng, side)
                    p = plan(side=side, stop=97.0 if side == "long" else 103.0, hold=40)
                    res = exitsim.pair(p, rows, var, EMPTY, FLOOR, COSTS)
                    if res["d"] is None:
                        continue
                    self.assertLessEqual(res["d_lo"], res["d"] + 1e-12, "%s %s %d" % (side, body, k))
                    if not res["amb"]:
                        self.assertAlmostEqual(res["d_lo"], res["d"], places=12)
                        s = go(p, rows, var, FLOOR, order="S")
                        t = go(p, rows, var, FLOOR, order="T")
                        self.assertAlmostEqual(s["r_net"], t["r_net"], places=12)

    def test_all_amb_paths_have_finite_lower_bound(self):
        rng = random.Random(7)
        rows = self.path(rng, "long", 30)
        var = {"schema": exitpol.SCHEMA, "lock": [{"at_r": 1.0, "keep": 0.5}]}
        res = exitsim.pair(plan(stop=98.0, hold=30), rows, var, EMPTY, FLOOR, COSTS)
        self.assertLessEqual(res["d_lo"], res["d"] + 1e-12)


class TestProperty25SymmetricCut(unittest.TestCase):
    def test_variant_end_closes_whole_remaining(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.2, 99.9, 100.1)]
        r = go(plan(), rows, EMPTY, EMPTY)
        self.assertAlmostEqual(sum(p["frac"] for p in r["parts"]), 1.0)
        self.assertEqual(r["end_ts"], 120)

    def test_both_sides_closed_at_same_last_candle(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.9, 100.4), R(2, 100.4, 100.6, 100.1, 100.3)]
        var = {"schema": exitpol.SCHEMA, "timeout": {"hold_frac": 1.0, "exec": "taker"}}
        a = go(plan(hold=2), rows, var, EMPTY)
        b = go(plan(hold=2), rows, EMPTY, EMPTY)
        self.assertEqual(a["end_ts"], b["end_ts"])


class TestProperty27PathMfe(unittest.TestCase):
    def test_independent_of_policy(self):
        rows = [R(0, 100, 100.5, 99.5, 100), R(1, 100, 101.5, 99.0, 101)]
        self.assertEqual(exitsim.path_mfe(rows, 100.0, 99.0, "long", 240),
                         exitsim.path_mfe(rows, 100.0, 99.0, "long", 240))
        for body in TestProperty7LowerBound.POLICIES:
            exitsim.run(plan(), rows, dict(body, schema=exitpol.SCHEMA), FLOOR, COSTS)
            self.assertAlmostEqual(exitsim.path_mfe(rows, 100.0, 99.0, "long", 240), 1.5, places=12)

    def test_horizon_slices(self):
        rows = [R(0, 100, 100.2, 99.8, 100), R(1, 100, 100.4, 99.9, 100.2), R(2, 100.2, 102.2, 100.0, 102.0)]
        self.assertAlmostEqual(exitsim.path_mfe(rows, 100.0, 99.0, "long", 1), 0.2, places=12)
        self.assertAlmostEqual(exitsim.path_mfe(rows, 100.0, 99.0, "long", 2), 0.4, places=12)
        self.assertAlmostEqual(exitsim.path_mfe(rows, 100.0, 99.0, "long", 240), 2.2, places=12)

    def test_short_and_bad_input(self):
        rows = [R(0, 100, 100.2, 99.8, 100), R(1, 100, 100.1, 98.0, 98.5)]
        self.assertAlmostEqual(exitsim.path_mfe(rows, 100.0, 101.0, "short", 240), 2.0, places=12)
        self.assertEqual(exitsim.path_mfe([], 100.0, 99.0, "long", 240), 0.0)
        self.assertEqual(exitsim.path_mfe(rows, 100.0, 100.0, "long", 240), 0.0)


class TestExecutorLimitsFromCtx(unittest.TestCase):
    def test_min_step_ticks_from_ctx_blocks_small_trail_move(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.12, 99.97, 100.05), R(2, 100.05, 100.06, 99.92, 99.95)]
        pol = {"schema": exitpol.SCHEMA, "lock": [{"at_r": 1.0, "keep": 0.5}]}
        p = plan(stop=99.9)
        self.assertEqual(go(p, rows, pol, EMPTY, order="T")["exit_reason"], "trail")
        got = go(p, rows, pol, EMPTY, order="T", ctx={"stop_moves": {"min_step_ticks": 20}})
        self.assertEqual(got["exit_reason"], "end")


class TestLotRounding(unittest.TestCase):
    def test_close_qty_applied_to_partial(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 101.2, 99.9, 101.0)]
        pol = {"schema": exitpol.SCHEMA, "partial": [{"at_r": 1.0, "frac": 0.3, "exec": "taker"}]}
        lot = {"qty": 0.05, "qty_step": 0.01, "min_qty": 0.01}
        r = go(plan(), rows, pol, FLOOR, order="S", lot=lot)
        got, _full = qty.close_qty(0.3 * 0.05, 0.05, 0.01, 0.01)
        self.assertAlmostEqual(r["parts"][0]["frac"], got / 0.05, places=12)

    def test_without_lot_fraction_is_exact(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 101.2, 99.9, 101.0)]
        pol = {"schema": exitpol.SCHEMA, "partial": [{"at_r": 1.0, "frac": 0.3, "exec": "taker"}]}
        r = go(plan(), rows, pol, FLOOR)
        self.assertAlmostEqual(r["parts"][0]["frac"], 0.3, places=12)


class TestReplayBrain(unittest.TestCase):
    def test_brain_close_is_manual_reason(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.4, 99.8, 100.2), R(2, 100.2, 100.3, 99.9, 100.0)]
        r = exitsim.replay_brain(plan(), rows, EMPTY, EMPTY, COSTS, timeline=[{"ts": 90, "kind": "close", "price": 100.2}])
        self.assertEqual(r["exit_reason"], "manual")
        self.assertEqual(r["parts"][-1]["ts"], 60)

    def test_brain_stop_tightens(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.4, 98.9, 99.2)]
        r = exitsim.replay_brain(plan(), rows, EMPTY, EMPTY, COSTS,
                                 timeline=[{"ts": 30, "kind": "move_stop", "stop": 99.6}])
        self.assertEqual(r["exit_reason"], "brain")
        self.assertAlmostEqual(r["parts"][-1]["px"], 99.6)


if __name__ == "__main__":
    unittest.main()
