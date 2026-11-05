"""R4 задача 5: условия mfe_r / since_peak_min, режимы трейла mfe_tiers / atr_entry / pnl_keep, exitpol.compile в rules.evaluate_manage.

Свойства 1-3 design.md (не слабее пола, монотонность стопа, закрытие не больше позиции) — через evaluate_manage на случайных путях:
прогон «пол» (limits без exit_policy, прежний _auto_rules) и прогон «политика» (limits с exit_policy и exit_ctx) на одних тиках.
Ручные эталоны новых режимов; сроки stall / decay / timeout считаются от exit_ctx.max_hold_min, а не от lifecycle.max_hold_min.
"""
import copy
import json
import os
import random
import unittest

import _bootstrap  # noqa: F401
from tb.core import plan_schema
from tb.logic import conds as C
from tb.logic import exitpol as X
from tb.logic import qty as Q
from tb.logic import rules
from tb.logic.candles import MarketView

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir)
with open(os.path.join(ROOT, "config", "exec_limits.json"), encoding="utf-8") as _f:
    LIMITS = json.load(_f)
AP = LIMITS["auto_protect"]
F = X.floor(AP)
CTX = {"max_hold_min": 60.0, "atr5_pct": 0.4, "sig_type": "oi_spike", "horizon": "scalp"}

L1 = {"lock": [{"at_r": 1.0, "keep": 0.5}, {"at_r": 1.5, "keep": 0.65}, {"at_r": 2.0, "keep": 0.75}]}
MENU = [
    L1, {"lock": [{"at_r": 1.0, "keep": 0.6}, {"at_r": 1.5, "keep": 0.7}, {"at_r": 2.5, "keep": 0.8}]},
    {"be": {"r": 0.5}},
    {"partial": [{"at_r": 0.7, "frac": 0.3, "exec": "maker"}]}, {"partial": [{"at_r": 1.0, "frac": 0.5, "exec": "taker"}]},
    {"trail_atr": {"activate_r": 1.0, "mult": 2.0}}, {"trail_atr": {"activate_r": 1.5, "mult": 2.5}},
    {"stall": {"hold_frac": 0.33, "min_r": 0.5, "keep": 0.8}},
    {"decay": {"hold_frac": 0.5, "max_mfe_r": 0.5, "max_pnl_r": 0.2, "exec": "maker"}},
    {"timeout": {"hold_frac": 1.0, "exec": "maker"}},
    {"near_tp": {"q": 0.4, "frac": 0.5, "min_r": 0.5, "exec": "maker", "table": {"oi_spike": {"scalp": 0.8}}}},
]


def _limits(policy=None, ctx=None, fast=True):
    lim = copy.deepcopy(LIMITS)
    if fast:                                  # без задержки между переносами: оба прогона оценивают каждое правило на каждом тике
        lim["stop_moves"]["min_interval_s"] = 0
    if policy is not None:
        lim["exit_policy"] = policy
        lim["exit_ctx"] = CTX if ctx is None else ctx
    return lim


def _rt(side, t0=1000.0):
    s = 1 if side == "long" else -1
    return {"status": "managing", "ts": {"entered": t0}, "targets": {}, "rules": {},
            "pos": {"qty": 1.0, "avg": 100.0, "initial_stop": 100.0 - s, "cur_stop": 100.0 - s, "r_unit": 1.0,
                    "best_px": 100.0, "t_best": t0}}


def _view(px, now, ts=None):
    return MarketView("X", px, px - 0.01, px + 0.01, px, now if ts is None else ts, None, now)


def run_path(side, prices, limits, plan_hold=60, dt=10.0):
    """Тики по prices; best_px/t_best обновляются до правил (как Executor._track_path). -> журнал [(стоп, остаток, действия)].
    Остановка при касании стопа или полном закрытии."""
    s = 1 if side == "long" else -1
    rt = _rt(side)
    plan = {"side": side, "manage": [], "lifecycle": {"max_hold_min": plan_hold}}
    now, log = rt["ts"]["entered"], []
    for px in prices:
        now += dt
        p = rt["pos"]
        if s * (px - p["cur_stop"]) <= 0:
            break
        if s * (px - p["best_px"]) > 0:
            p["best_px"], p["t_best"] = px, now
        res = rules.evaluate_manage(plan, rt, _view(px, now), now, limits)
        for a in res["actions"]:
            if a["type"] == "move_stop":
                p["cur_stop"] = a["to"]
            elif a["type"] in ("partial_close", "close"):
                rem = p["qty"]
                want = a["frac_initial"] * 1.0 if "frac_initial" in a else a.get("frac_remaining", a.get("frac", 1.0)) * rem
                q, full = Q.close_qty(want, rem, 1e-9)
                p["qty"] = 0.0 if full else round(rem - q, 12)
        for rid, st in (res["rt_patch"].get("rules") or {}).items():
            rt["rules"][rid] = st
        log.append((p["cur_stop"], p["qty"], res["actions"]))
        if p["qty"] <= 0:
            break
    return log


def rand_prices(rnd, n=300):
    px, out = 100.0, []
    drift = rnd.uniform(-0.01, 0.03)
    for _ in range(n):
        px = max(95.0, min(106.0, px + drift + rnd.gauss(0, 0.12)))
        out.append(round(px, 4))
    return out


def rand_policy(rnd):
    p = {}
    for comp in rnd.sample(MENU, rnd.randint(1, 3)):
        for k, v in comp.items():
            if k not in p:
                p[k] = copy.deepcopy(v)
    if "partial" in p and "near_tp" in p:
        p["near_tp"]["frac"] = 0.3
    return p


class TestConds(unittest.TestCase):
    def test_doctests(self):
        import doctest
        self.assertEqual(doctest.testmod(C).failed, 0)
        self.assertEqual(doctest.testmod(rules).failed, 0)

    def test_mfe_r(self):
        pos = {"qty": 1, "avg": 100.0, "initial_stop": 99.0, "r_unit": 1.0, "best_px": 101.6}
        v = _view(100.5, 50.0)
        ctx = {"pos": pos, "side": "long"}
        self.assertTrue(C.eval_cond({"kind": "mfe_r", "gte": 1.6}, v, {}, ctx)[0])
        self.assertFalse(C.eval_cond({"kind": "mfe_r", "lt": 1.6}, v, {}, ctx)[0])
        self.assertTrue(C.eval_cond({"kind": "mfe_r", "gte": 1.9}, _view(101.9, 50.0), {}, ctx)[0])     # текущая цена выше пика
        self.assertAlmostEqual(C.mfe_r(pos, None), 1.6)
        short = {"qty": 1, "avg": 100.0, "initial_stop": 101.0, "r_unit": 1.0, "best_px": 98.8}
        self.assertAlmostEqual(C.mfe_r(short, 99.5, {"side": "short"}), 1.2)
        self.assertIsNone(C.mfe_r(None, 100.0))
        self.assertFalse(C.eval_cond({"kind": "mfe_r", "gte": "x"}, v, {}, ctx)[0])

    def test_since_peak_min(self):
        pos = {"qty": 1, "avg": 100.0, "initial_stop": 99.0, "r_unit": 1.0, "best_px": 101.0, "t_best": 1000.0}
        ctx = {"pos": pos, "side": "long", "entered_ms": 400000.0}
        v = _view(100.6, 1000.0 + 20 * 60)
        self.assertAlmostEqual(C.since_peak_min(pos, v, ctx), 20.0)
        self.assertTrue(C.eval_cond({"kind": "since_peak_min", "gte": 19.8}, v, {}, ctx)[0])
        self.assertFalse(C.eval_cond({"kind": "since_peak_min", "gte": 20.5}, v, {}, ctx)[0])
        self.assertEqual(C.since_peak_min(pos, _view(101.2, 2000.0), ctx), 0.0)      # новый пик прямо сейчас
        nobest = dict(pos, t_best=None)
        self.assertAlmostEqual(C.since_peak_min(nobest, _view(100.6, 700.0), ctx), 5.0)  # нет t_best -> от входа
        self.assertIsNone(C.since_peak_min(dict(nobest), _view(100.6, 700.0), {"side": "long"}))
        self.assertIn("без нового пика", C.describe({"kind": "since_peak_min", "gte": 5}, v, {}, ctx)["text"])

    def test_plan_dictionary_unchanged(self):
        """Словарь условий и режимов трейла плана (валидатор мозга) новых видов не содержит (Requirement 9.6)."""
        self.assertNotIn("mfe_r", plan_schema.COND_KINDS)
        self.assertNotIn("since_peak_min", plan_schema.COND_KINDS)
        for m in ("mfe_tiers", "atr_entry", "pnl_keep", "mfe_lock"):
            self.assertNotIn(m, plan_schema.TRAIL_MODES)


class TestTrailModes(unittest.TestCase):
    def pos(self, side, best):
        s = 1 if side == "long" else -1
        return {"pos": {"qty": 1, "avg": 100.0, "initial_stop": 100.0 - s, "r_unit": 1.0, "best_px": best}}

    def test_mfe_tiers(self):
        do = {"action": "trail", "mode": "mfe_tiers", "tiers": [[1.0, 0.5], [1.5, 0.65], [2.0, 0.75]]}
        self.assertAlmostEqual(rules.trail_candidate(do, {"side": "long"}, self.pos("long", 101.8), _view(101.0, 1.0)), 101.17)
        self.assertAlmostEqual(rules.trail_candidate(do, {"side": "long"}, self.pos("long", 102.4), _view(101.0, 1.0)), 101.8)
        self.assertAlmostEqual(rules.trail_candidate(do, {"side": "short"}, self.pos("short", 98.2), _view(99.0, 1.0)), 98.83)
        self.assertIsNone(rules.trail_candidate(do, {"side": "long"}, self.pos("long", 100.9), _view(100.5, 1.0)))
        bad = dict(do, tiers=[[1.0, 1.2]])
        self.assertIsNone(rules.trail_candidate(bad, {"side": "long"}, self.pos("long", 101.8), _view(101.0, 1.0)))

    def test_atr_entry(self):
        do = {"action": "trail", "mode": "atr_entry", "mult": 2.0, "atr_pct": 0.4}
        self.assertAlmostEqual(rules.trail_candidate(do, {"side": "long"}, self.pos("long", 102.0), _view(101.5, 1.0)), 101.2)
        self.assertAlmostEqual(rules.trail_candidate(do, {"side": "short"}, self.pos("short", 98.0), _view(98.5, 1.0)), 98.8)
        self.assertAlmostEqual(rules.trail_candidate(do, {"side": "long"}, self.pos("long", 101.0), _view(102.5, 1.0)), 101.7)  # цена лучше best_px
        self.assertIsNone(rules.trail_candidate(dict(do, atr_pct=None), {"side": "long"}, self.pos("long", 102.0), _view(101.5, 1.0)))

    def test_pnl_keep(self):
        do = {"action": "trail", "mode": "pnl_keep", "keep": 0.8}
        self.assertAlmostEqual(rules.trail_candidate(do, {"side": "long"}, self.pos("long", 102.0), _view(101.0, 1.0)), 100.8)
        self.assertAlmostEqual(rules.trail_candidate(do, {"side": "short"}, self.pos("short", 98.0), _view(99.0, 1.0)), 99.2)
        self.assertIsNone(rules.trail_candidate(do, {"side": "long"}, self.pos("long", 102.0), _view(99.9, 1.0)))

    def test_old_modes_unchanged(self):
        rt = {"pos": {"qty": 1, "avg": 100.0, "initial_stop": 95.0, "r_unit": 5.0, "best_px": 112.0}}
        self.assertAlmostEqual(rules.trail_candidate({"do": {"action": "trail", "mode": "percent", "pct": 5.0}}, {"side": "long"}, rt,
                                                     _view(110.0, 1.0)), 106.4)
        self.assertAlmostEqual(rules.trail_candidate({"mode": "mfe_lock", "activate_r": 1.0, "lock_frac": 0.5}, {"side": "long"}, rt,
                                                     _view(110.0, 1.0)), 106.0)


class TestEvaluateManage(unittest.TestCase):
    def ev(self, policy, rt, px, now, plan_hold=60, ctx=None, ts=None, side="long"):
        plan = {"side": side, "manage": [], "lifecycle": {"max_hold_min": plan_hold}}
        return rules.evaluate_manage(plan, rt, _view(px, now, ts), now, _limits(policy, ctx))

    def test_xp_lock_strictest_wins(self):
        rt = _rt("long")
        rt["pos"]["best_px"] = 101.8
        res = self.ev(L1, rt, 101.8, 1100.0)
        mv = [a for a in res["actions"] if a["type"] == "move_stop"]
        self.assertEqual(len(mv), 1)
        self.assertEqual(mv[0]["rule"], "xp_lock")
        self.assertAlmostEqual(mv[0]["to"], 101.17)
        self.assertEqual(set(res["rt_patch"]["rules"]), {"auto_be", "auto_lock", "xp_lock"})   # схлопнутые тоже отмечены

    def test_policy_trail_respects_profit_floor(self):
        """Стоповые правила xp_* не двигают стоп ниже пола прибыли композиции (do.min_profit_r)."""
        rt = _rt("long")
        rt["pos"]["best_px"] = 100.5
        res = self.ev({"trail_atr": {"activate_r": 0.5, "mult": 1.0}, "min_profit_r": 0.4}, rt, 100.5, 1100.0)
        self.assertEqual([a for a in res["actions"] if a["type"] == "move_stop"], [])         # 100.1 ниже пола 100.23 + 0.4R
        rt["pos"]["best_px"] = 101.5
        res = self.ev({"trail_atr": {"activate_r": 0.5, "mult": 1.0}, "min_profit_r": 0.4}, rt, 101.5, 1100.0)
        mv = [a for a in res["actions"] if a["type"] == "move_stop"]
        self.assertEqual(mv[0]["rule"], "xp_trail")
        self.assertAlmostEqual(mv[0]["to"], 101.1)
        self.assertAlmostEqual(rules.profit_floor(rt, _limits(), 1, 0.4), 100.0 * (1 + 23e-4) + 0.4)
        self.assertAlmostEqual(rules.profit_floor(rt, _limits(), 1), 100.0 * (1 + 23e-4) + 0.2)

    def test_timeout_from_exit_ctx_not_lifecycle(self):
        """O1 (1.0 x срока на входе 60 мин): закрытие на 60-й минуте при любом lifecycle.max_hold_min (продление мозгом не сдвигает)."""
        pol = {"timeout": {"hold_frac": 1.0, "exec": "maker"}}
        for hold in (30, 60, 240):
            rt = _rt("long")
            self.assertEqual([a for a in self.ev(pol, rt, 100.3, 1000.0 + 59.9 * 60, plan_hold=hold)["actions"] if a["type"] == "close"], [])
            cl = [a for a in self.ev(pol, rt, 100.3, 1000.0 + 60 * 60, plan_hold=hold)["actions"] if a["type"] == "close"]
            self.assertEqual(len(cl), 1, hold)
            self.assertEqual((cl[0]["rule"], cl[0]["reason"], cl[0]["exec"], cl[0]["frac"]), ("xp_timeout", "rule", "maker", 1.0))
        rt = _rt("long")
        late = self.ev(pol, rt, 100.3, 1000.0 + 61 * 60, ctx=dict(CTX, max_hold_min=120.0))
        self.assertEqual([a for a in late["actions"] if a["type"] == "close"], [])
        nctx = self.ev(pol, _rt("long"), 100.3, 1000.0 + 600 * 60, ctx={})                 # нет срока на входе -> компонент не действует
        self.assertEqual([a for a in nctx["actions"] if a["type"] == "close"], [])

    def test_decay(self):
        pol = {"decay": {"hold_frac": 0.5, "max_mfe_r": 0.5, "max_pnl_r": 0.2, "exec": "taker"}}
        rt = _rt("long")
        rt["pos"]["best_px"] = 100.3
        cl = [a for a in self.ev(pol, rt, 100.1, 1000.0 + 30 * 60)["actions"] if a["type"] == "close"]
        self.assertEqual((cl[0]["rule"], cl[0]["exec"]), ("xp_decay", "taker"))
        self.assertEqual([a for a in self.ev(pol, rt, 100.1, 1000.0 + 29 * 60)["actions"] if a["type"] == "close"], [])
        rt["pos"]["best_px"] = 100.6                                                            # MFE 0.6 >= 0.5: затухания нет
        self.assertEqual([a for a in self.ev(pol, rt, 100.1, 1000.0 + 31 * 60)["actions"] if a["type"] == "close"], [])

    def test_stall(self):
        pol = {"stall": {"hold_frac": 0.33, "min_r": 0.5, "keep": 0.8}}
        rt = _rt("long")
        rt["pos"].update(best_px=100.9, t_best=1000.0 + 60)
        early = self.ev(pol, rt, 100.7, 1000.0 + 60 + 19 * 60)
        self.assertNotIn("xp_stall", [a.get("rule") for a in early["actions"]])
        res = self.ev(pol, rt, 100.7, 1000.0 + 60 + 20 * 60)
        mv = [a for a in res["actions"] if a["type"] == "move_stop"]
        self.assertEqual(mv[0]["rule"], "xp_stall")
        self.assertAlmostEqual(mv[0]["to"], 100.56)                                             # вход + 0.8 x 0.7

    def test_partial_exec_and_at_r(self):
        rt = _rt("long")
        rt["pos"]["best_px"] = 100.75
        res = self.ev({"partial": [{"at_r": 0.7, "frac": 0.3, "exec": "maker"}]}, rt, 100.75, 1100.0)
        pc = [a for a in res["actions"] if a["type"] == "partial_close"]
        self.assertEqual(pc, [{"type": "partial_close", "rule": "xp_partial_0", "why": "RULE:xp_partial_0", "frac_initial": 0.3,
                               "exec": "maker", "at_r": 0.7}])
        rt["rules"].update(res["rt_patch"]["rules"])
        again = self.ev({"partial": [{"at_r": 0.7, "frac": 0.3, "exec": "maker"}]}, rt, 100.8, 1110.0)
        self.assertEqual([a for a in again["actions"] if a["type"] == "partial_close"], [])     # once

    def test_stale_price_policy_does_not_close(self):
        pol = {"timeout": {"hold_frac": 1.0, "exec": "maker"}}
        res = self.ev(pol, _rt("long"), 100.3, 1000.0 + 61 * 60, ts=1000.0 + 61 * 60 - 60)
        self.assertEqual([a for a in res["actions"] if a["type"] == "close"], [])
        self.assertIn({"rule": "xp_timeout", "code": "NO_DATA"}, res["skipped"])

    def test_plan_rules_unchanged_with_policy(self):
        """Правила плана идут после правил кода и не получают полей exec/at_r политики."""
        rt = _rt("long")
        rt["pos"]["best_px"] = 101.2
        plan = {"side": "long", "manage": [{"id": "pc", "when": {"kind": "pnl_r", "gte": 1.0}, "do": {"action": "partial_close",
                                                                                                         "frac_of_remaining": 0.5, "exec": "market"}}]}
        res = rules.evaluate_manage(plan, rt, _view(101.2, 1100.0), 1100.0, _limits(L1))
        pc = [a for a in res["actions"] if a["type"] == "partial_close"]
        self.assertEqual(pc, [{"type": "partial_close", "rule": "pc", "why": "RULE:pc", "frac_remaining": 0.5}])

    def test_floor_policy_same_as_no_policy(self):
        """exit_policy, равная полу ({} или сам пол), даёт те же действия и rt_patch, что прежний _auto_rules (200 путей, обе стороны)."""
        rnd = random.Random(11)
        for n in range(200):
            side = rnd.choice(["long", "short"])
            s = 1 if side == "long" else -1
            prices = [100.0 + s * (p - 100.0) for p in rand_prices(rnd, 150)]
            base = run_path(side, prices, _limits(fast=False))
            for pol in ({}, copy.deepcopy(F)):
                self.assertEqual(run_path(side, prices, _limits(pol, fast=False)), base, (n, pol))


class TestProperties(unittest.TestCase):
    """Свойства 1-3 design.md через evaluate_manage (перебор случайных путей и политик меню)."""

    def test_properties_1_2_3(self):
        rnd = random.Random(2026)
        n_paths = 160
        closed_any = moved_more = 0
        for n in range(n_paths):
            side = rnd.choice(["long", "short"])
            s = 1 if side == "long" else -1
            prices = [round(100.0 + s * (p - 100.0), 4) for p in rand_prices(rnd)]
            pol = rand_policy(rnd)
            self.assertEqual(X.validate(X.normalize(pol, F)), [], pol)
            self.assertEqual(X.weaker_than(X.normalize(pol, F), F), [], pol)
            fl = run_path(side, prices, _limits())
            pl = run_path(side, prices, _limits(pol))
            init = 100.0 - s
            prev = init
            for i, (stop, q, acts) in enumerate(pl):
                self.assertGreaterEqual(s * (stop - prev), -1e-12, (n, i, pol))                 # свойство 2: стоп только строже
                prev = stop
                if i < len(fl):
                    self.assertGreaterEqual(s * (stop - fl[i][0]), -1e-12, (n, i, pol))          # свойство 1: не слабее пола
                    self.assertLessEqual(q, fl[i][1] + 1e-12)                                     # остаток не больше, чем при поле
                    moved_more += s * (stop - fl[i][0]) > 1e-9
                self.assertGreaterEqual(q, -1e-12)                                                # свойство 3: закрыто не больше позиции
                for a in acts:
                    self.assertIn(a["type"], ("move_stop", "partial_close", "close"), (n, a))     # политика не добавляет и не меняет размер
            closed_any += bool(pl) and pl[-1][1] < 1.0
        self.assertGreater(closed_any, 10)          # перебор действительно задевает фиксации и закрытия
        self.assertGreater(moved_more, 10)          # и стопы строже пола


if __name__ == "__main__":
    unittest.main()
