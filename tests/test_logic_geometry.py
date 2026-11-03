"""R1.2: tb.logic.geometry. Регрессия поле в поле против фикстур, снятых со старого brain/cands.candidates до переноса (tests/fixtures_geometry/dossiers.json),
пороги правил, atr_plan, nearest_ok, config/geometry.json, реэкспорт в validate/entryplan, чистота модуля."""
import ast
import copy
import json
import os
import unittest

import _bootstrap  # noqa: F401
from tb.brain import cands, entryplan, validate as V
from tb.logic import geometry as G

ROOT = _bootstrap.ROOT
with open(os.path.join(ROOT, "tests", "fixtures_geometry", "dossiers.json"), encoding="utf-8") as _f:
    FIX = json.load(_f)
with open(os.path.join(ROOT, "config", "geometry.json"), encoding="utf-8") as _f:
    CFG = json.load(_f)


def strip_ver(rows):
    return None if rows is None else [{k: v for k, v in r.items() if k != "geometry_ver"} for r in rows]


def direct(f):
    """Геометрия напрямую: уровни и ctx из досье, как их собирает обёртка."""
    b = f["brief"]
    return G.candidates(V.level_pool(b, f["sym"]), V.entry_context(b, {"sym": f["sym"]}), CFG)


class Regression(unittest.TestCase):
    def test_fixture_set_is_wide(self):
        rows = [r for f in FIX for r in (f["expected"] or [])]
        self.assertGreaterEqual(len(FIX), 20)
        self.assertTrue(any(r.get("setup") == "momentum_chase" and r.get("ok") for r in rows))                  # chase
        self.assertTrue(any(r.get("setup") == "momentum_chase" and r.get("fail") for r in rows))
        self.assertTrue(any(r.get("entry_type") == "limit" and not r.get("setup") for r in rows))                # лимитный вход
        self.assertTrue(any(r.get("entry_type") is None and r.get("ok") for r in rows))                          # вход по рынку/без типа
        self.assertTrue({"rr_low", "stop_wide", "range_gt_stop"} <= {r["fail"].split(":")[0] for r in rows if r.get("fail")})   # отказы
        self.assertTrue(any(f["expected"] is None for f in FIX))
        self.assertTrue(any("size_mult" in r and not r.get("setup") for r in rows))

    def test_wrapper_matches_old_field_by_field(self):
        for f in FIX:
            got = cands.candidates(f["brief"], f["sym"])
            self.assertEqual(strip_ver(got), f["expected"], f["name"])
            for r in got or []:
                self.assertEqual(r["geometry_ver"], "g1", f["name"])

    def test_geometry_direct_matches_old(self):
        for f in FIX:
            self.assertEqual(direct(f), f["expected"], f["name"])

    def test_default_profiles_give_same_result_as_config(self):
        for f in FIX:
            b = f["brief"]
            self.assertEqual(G.candidates(V.level_pool(b, f["sym"]), V.entry_context(b, {"sym": f["sym"]})), f["expected"], f["name"])

    def test_input_not_mutated(self):
        f = FIX[0]
        b = copy.deepcopy(f["brief"])
        cands.candidates(b, f["sym"])
        self.assertEqual(b, f["brief"])


class Thresholds(unittest.TestCase):
    """Свойство 6: каждый кандидат подчиняется порогам профиля и пола стопа."""

    def test_rows_respect_profile(self):
        base = CFG["profiles"]["base"]
        for f in FIX:
            b = f["brief"]
            ctx = V.entry_context(b, {"sym": f["sym"]})
            rows = f["expected"] or []
            self.assertEqual([r["n"] for r in rows], list(range(1, len(rows) + 1)), f["name"])
            plain = [r for r in rows if not r.get("setup")]
            self.assertLessEqual(len([r for r in plain if r.get("ok")]), base["max_ok"], f["name"])
            self.assertLessEqual(len([r for r in plain if not r.get("ok")]), base["max_fail"], f["name"])
            oks = [r["rr_net"] for r in plain if r.get("ok")]
            self.assertEqual(oks, sorted(oks, reverse=True), f["name"])
            for r in plain:
                sign = 1 if r["side"] == "long" else -1
                px = ctx["px"]
                ref = max(r["level"], px) if sign > 0 else min(r["level"], px)
                risk = abs(ref - r["stop"])
                self.assertGreater((r["level"] - r["stop"]) * sign, 0, r)
                self.assertGreaterEqual((r["tp"][0] - ref) * sign / risk, base["tp1_min_r"] - 1e-3, (f["name"], r))
                self.assertLessEqual(r["dist_pct"], G.ENTRY_MAX_LEVEL_DIST_LOOSE_PCT if G._loose(ctx) else G.ENTRY_MAX_LEVEL_DIST_PCT, r)
                floor = G.stop_floor_pct(ctx, "limit")
                self.assertGreaterEqual(abs(r["level"] - r["stop"]) / r["level"] * 100.0, floor - 1e-2, (f["name"], r))
                if r["horizon"] == "scalp":
                    self.assertEqual(r.get("entry_type"), "limit")
                    if ctx.get("cost_rt_pct") is not None:
                        self.assertGreaterEqual(abs(r["tp"][0] - ref) / ref * 100.0, base["scalp_min_tp_costs"] * ctx["cost_rt_pct"] - 1e-3, r)
                if r.get("ok"):
                    self.assertNotIn("fail", r)
                    hz = (ctx.get("horizons") or {}).get(r["horizon"]) or {}
                    ms = hz.get("max_stop_pct") if hz else ctx.get("max_stop_pct")
                    self.assertLessEqual(r["stop_pct"], (G.ENTRY_MAX_STOP_PCT if ms is None else ms) + 1e-2, (f["name"], r))

    def test_chase_rows(self):
        ch = G.DEFAULT_PROFILES["chase"]
        for f in FIX:
            for r in f["expected"] or []:
                if r.get("setup") != "momentum_chase":
                    continue
                self.assertEqual((r["n"], r["confirm"], r["entry_type"], r["dist_pct"]), (1, "none", "market", 0.0), f["name"])
                self.assertLessEqual(r["size_mult"], G.ENTRY_SIZE_MULT_MAX)
                z = r["zone"]
                self.assertAlmostEqual((z["high"] - z["low"]) / r["level"] * 100.0 / 2.0, ch["zone_pct"], places=2)

    def test_profile_changes_change_result(self):
        f = next(x for x in FIX if x["name"] == "cap_first_target_1_5r")
        lv, ctx = V.level_pool(f["brief"], "SOL"), V.entry_context(f["brief"], {"sym": "SOL"})
        base = next(r for r in G.candidates(lv, ctx, CFG) if r["side"] == "long" and r["level"] == 145.0)
        cfg = copy.deepcopy(CFG)
        cfg["profiles"]["base"]["tp1_max_r"] = 1.2
        alt = next(r for r in G.candidates(lv, ctx, cfg) if r["side"] == "long" and r["level"] == 145.0)
        self.assertLess(alt["tp"][0], base["tp"][0])
        cfg = copy.deepcopy(CFG)
        cfg["profiles"]["base"]["max_ok"] = 1
        self.assertEqual(len([r for r in G.candidates(lv, ctx, cfg) if r.get("ok")]), 1)

    def test_horizon_unit(self):
        hz = {"scalp": {"max_stop_pct": 0.8, "min_rr": 1.0}, "intraday": {"max_stop_pct": 3.0, "min_rr": 1.5}, "swing": {"max_stop_pct": 15.0, "min_rr": 2.0}}
        ctx = {"horizons": hz, "cost_rt_pct": 0.28}
        self.assertNotEqual(G._horizon(ctx, 0.4, 3.0, 0.4, 0.5)[0], "scalp")                  # цель 0.5% < 3 издержек (0.84%)
        self.assertEqual(G._horizon(ctx, 0.4, 3.0, 0.4, 1.0)[::2], ("scalp", None))
        h, net, why = G._horizon(ctx, 0.4, 0.5, 0.4, 1.0)
        self.assertTrue(why and why.startswith("rr_low:need_"), why)
        self.assertEqual(G._horizon(ctx, 20.0, 3.0, 20.0, 5.0)[2], "stop_wide")
        self.assertEqual(G._horizon({"horizons": hz, "cost_rt_pct": 0.28, "max_horizon": "scalp"}, 1.5, 4.0, 1.5, 3.0)[2], "stop_wide")      # потолок горизонта плана дня
        self.assertEqual(G._horizon({"horizons": hz, "cost_rt_pct": 0.28, "max_horizon": "scalp", "off_plan_ok": True}, 1.5, 4.0, 1.5, 3.0)[0], "intraday")
        # без порогов сессии: ENTRY_MIN_RR и ENTRY_MAX_STOP_PCT
        self.assertEqual(G._horizon({"session_type": "intraday"}, 1.0, 2.0, 1.0, 2.0), ("intraday", 2.0, None))
        self.assertEqual(G._horizon({"session_type": "intraday"}, 1.0, 1.9, 1.0, 2.0)[2], "rr_low:need_2.00")

    def test_pool_clusters(self):
        self.assertEqual(G.pool([100.0, 100.1, 100.5, 0, -3, None, True, "x"]), [100.0, 100.5])       # 0.1% < 0.15% сливается, мусор отбрасывается
        self.assertEqual(G.pool([100.0, 100.1], 0.05), [100.0, 100.1])
        self.assertEqual(G.pool([]), [])
        self.assertEqual(G.pool(None), [])

    def test_no_cands_without_ctx_or_levels(self):
        self.assertIsNone(G.candidates([100.0, 101.0], None))
        self.assertIsNone(G.candidates([100.0, 101.0], {}))
        self.assertIsNone(G.candidates([100.0], {"px": 100.0}))


class Shadows(unittest.TestCase):
    CTX = {"px": 100.0, "a5_pct": 0.2, "spread_bps": 2.0, "cost_rt_pct": 0.24}

    def test_atr_plan_long_short(self):
        p = G.atr_plan("long", self.CTX, CFG["profiles"]["atr"])
        floor = max(0.1, 1.5 * 0.2, 3.0 * 2.0 / 100.0, 2.0 * 0.24)            # 0.48: пол по издержкам больше 1.5*ATR=0.3
        self.assertEqual(G.stop_floor_pct(self.CTX, "market"), floor)
        self.assertEqual((p["side"], p["entry_type"], p["level"], p["stop_pct"], p["rr"]), ("long", "market", 100.0, 0.48, 1.5))
        self.assertAlmostEqual(p["stop"], 99.52, places=4)
        self.assertAlmostEqual(p["tp"][0], 100.0 + 1.5 * 0.48, places=4)
        s = G.atr_plan("short", self.CTX, CFG["profiles"]["atr"])
        self.assertAlmostEqual(s["stop"], 100.48, places=4)
        self.assertAlmostEqual(s["tp"][0], 100.0 - 0.72, places=4)

    def test_atr_plan_atr_wins_and_profile_params(self):
        ctx = dict(self.CTX, a5_pct=0.6)                                       # 1.5*0.6 = 0.9 > пол 0.48
        p = G.atr_plan("long", ctx, {"k_atr": 1.5, "tp_r": 2.0})
        self.assertEqual(p["stop_pct"], 0.9)
        self.assertAlmostEqual(p["tp"][0], 100.0 + 2.0 * 0.9, places=4)
        self.assertEqual(G.atr_plan("long", ctx)["rr"], 1.5)                   # профиль по умолчанию
        self.assertGreater(G.atr_plan("long", dict(ctx, a5_pct=None))["stop_pct"], 0)

    def test_no_atr_plan_path(self):
        """NO_ATR_PLAN: нет цены или неверная сторона -> None (политика тени пишет отказ, плана нет)."""
        for side, ctx in (("long", {}), ("long", {"px": None}), ("short", {"px": -1.0}), ("buy", self.CTX), (None, self.CTX), ("long", None)):
            self.assertIsNone(G.atr_plan(side, ctx), (side, ctx))

    def test_atr_plan_bad_input(self):
        self.assertIsNone(G.atr_plan("up", self.CTX))
        self.assertIsNone(G.atr_plan("long", {"a5_pct": 0.2}))
        self.assertIsNone(G.atr_plan("long", dict(self.CTX, px=0)))

    def test_nearest_ok(self):
        rows = [{"n": 1, "side": "long", "level": 99.0, "ok": True}, {"n": 2, "side": "long", "level": 99.5, "fail": "rr_low:need_1.00"},
                {"n": 3, "side": "long", "level": 98.0, "ok": True}, {"n": 4, "side": "short", "level": 100.4, "ok": True},
                {"n": 5, "side": "short", "level": 101.0, "ok": True}]
        self.assertEqual(G.nearest_ok(rows, "long", 100.0)["n"], 1)               # fail-кандидат не берётся
        self.assertEqual(G.nearest_ok(rows, "short", 100.0)["n"], 4)
        self.assertEqual(G.nearest_ok(rows, "short", 100.8)["n"], 5)
        self.assertIsNone(G.nearest_ok([], "long", 100.0))
        self.assertIsNone(G.nearest_ok(None, "long", 100.0))
        self.assertIsNone(G.nearest_ok([rows[1]], "long", 100.0))
        tie = [{"n": 1, "side": "long", "level": 99.0, "ok": True}, {"n": 2, "side": "long", "level": 101.0, "ok": True}]
        self.assertEqual(G.nearest_ok(tie, "long", 100.0)["n"], 1)                # равенство: меньший n

    def test_nearest_ok_skips_chase_by_default(self):
        rows = [{"n": 1, "side": "short", "level": 100.0, "ok": True, "setup": "momentum_chase"}, {"n": 2, "side": "short", "level": 101.0, "ok": True}]
        self.assertEqual(G.nearest_ok(rows, "short", 100.0)["n"], 2)
        self.assertEqual(G.nearest_ok(rows, "short", 100.0, include_chase=True)["n"], 1)

    def test_nearest_ok_on_real_candidates(self):
        f = next(x for x in FIX if x["name"] == "day_basic")
        c = G.nearest_ok(f["expected"], "long", 145.2)
        self.assertIsNotNone(c)
        longs = [r for r in f["expected"] if r["side"] == "long" and r.get("ok")]
        self.assertEqual(c, min(longs, key=lambda r: (abs(r["level"] - 145.2), r["n"])))


class Wiring(unittest.TestCase):
    def test_config_equals_defaults(self):
        self.assertEqual(CFG["geometry_ver"], G.GEOMETRY_VER)
        self.assertEqual(CFG["cluster_pct"], G.DEFAULT_CLUSTER_PCT)
        self.assertEqual(CFG["profiles"], G.DEFAULT_PROFILES)
        self.assertEqual(cands.load_config()[1], "g1")

    def test_g1_parameters_pinned(self):
        """Любое изменение порога (в том числе surge_x/range_atr) требует нового geometry_ver: значения g1 зафиксированы здесь, правка = g2 + новые фикстуры."""
        self.assertEqual(CFG["geometry_ver"], "g1")
        self.assertEqual(CFG["profiles"]["chase"], {"zone_pct": 0.3, "stop_atr": 0.25, "tp_r": 2.5, "surge_x": 10.0, "range_atr": 4.0})
        self.assertEqual(CFG["profiles"]["base"], {"stop_buf_atr": 0.1, "tp1_min_r": 1.0, "tp1_max_r": 1.5, "scalp_min_tp_costs": 3.0, "max_ok": 3, "max_fail": 2})
        self.assertEqual(CFG["profiles"]["atr"], {"k_atr": 1.5, "tp_r": 1.5})
        self.assertEqual((G.CHASE_SURGE_X, G.CHASE_RANGE_ATR), (10.0, 4.0))
        self.assertEqual((V.CHASE_SURGE_X, V.CHASE_RANGE_ATR), (10.0, 4.0))

    def test_chase_thresholds_come_from_profile(self):
        ctx = {"surge_x": 6.0, "ret_5m": -1.0, "range_5m_pct": 0.9, "a5_pct": 0.3}
        self.assertFalse(G.chase_impulse(ctx, -1))                                           # 6x < 10x
        self.assertTrue(G.chase_impulse(ctx, -1, dict(G.DEFAULT_PROFILES["chase"], surge_x=5.0, range_atr=3.0)))
        self.assertFalse(G.chase_impulse(ctx, -1, dict(G.DEFAULT_PROFILES["chase"], surge_x=5.0, range_atr=3.5)))   # 0.9 < 3.5*0.3
        self.assertFalse(G.chase_impulse(ctx, 1, dict(G.DEFAULT_PROFILES["chase"], surge_x=5.0, range_atr=3.0)))    # против направления
        f = next(x for x in FIX if x["name"] == "chase_weak_surge")                          # surge 4x: по умолчанию chase нет, с порогом 3x появляется
        lv, c = V.level_pool(f["brief"], "SOL"), V.entry_context(f["brief"], {"sym": "SOL"})
        self.assertFalse([r for r in G.candidates(lv, c, CFG) or [] if r.get("setup")])
        cfg = copy.deepcopy(CFG)
        cfg["profiles"]["chase"]["surge_x"] = 3.0
        self.assertTrue([r for r in G.candidates(lv, c, cfg) or [] if r.get("setup")])

    def test_missing_or_broken_config_falls_back(self):
        self.assertEqual(cands.load_config(root=os.path.join(ROOT, "tests", "no_such_root_dir")), ({}, "g1"))

    def test_validate_and_entryplan_reexport(self):
        for name in ("stop_floor_pct", "exec_stop_pct", "_entry_rr", "chase_impulse", "_loose", "ENTRY_MAX_LEVEL_DIST_PCT", "ENTRY_MAX_LEVEL_DIST_LOOSE_PCT", "ENTRY_MAX_STOP_PCT",
                     "ENTRY_MIN_RR", "ENTRY_SIZE_MULT_MAX", "LOOSE_RISK_LEVEL", "CHASE_MIN_LEVEL", "CHASE_RANGE_ATR", "CHASE_SURGE_X", "CHASE_SETUP", "SESSION_ORDER",
                     "ENTRY_MIN_STOP_PCT", "ENTRY_STOP_ATR_MULT", "ENTRY_STOP_COST_MULT"):
            self.assertIs(getattr(V, name), getattr(G, name), name)
        self.assertIs(entryplan.MAX_HOLD_MIN, G.MAX_HOLD_MIN)
        self.assertEqual(G.MAX_HOLD_MIN, {"scalp": 60, "intraday": 240, "swing": 1440, "position": 4320})
        self.assertEqual(G.SESSION_ORDER, ("scalp", "intraday", "swing", "position"))

    def test_old_helper_values(self):
        self.assertAlmostEqual(G.stop_floor_pct({"cost_rt_pct": 0.28, "a5_pct": 0.25, "spread_bps": 1.8}), 0.56)
        self.assertAlmostEqual(G.stop_floor_pct({"cost_rt_pct": 0.28, "cost_rt_maker_pct": 0.18, "a5_pct": 0.1}, "limit"), 0.36)
        self.assertEqual(G.stop_floor_pct(None), 0.1)
        self.assertAlmostEqual(G.exec_stop_pct(100.0, 101.0, 99.2), 1.8 / 101.0 * 100.0)      # от цены шире, чем от уровня
        self.assertAlmostEqual(G.exec_stop_pct(100.0, 101.0, 99.2, "limit"), 0.8)
        self.assertAlmostEqual(G._entry_rr("long", 100.0, 100.5, 99.5, [102.0]), 1.5)
        self.assertIsNone(G._entry_rr("long", 100.0, 100.0, 101.0, [102.0]))

    def test_no_brain_import_and_pure(self):
        with open(os.path.join(ROOT, "src", "tb", "logic", "geometry.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        mods = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods += [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                mods.append(n.module or "")
        self.assertEqual(mods, [], "геометрия не импортирует ничего (в том числе tb.brain)")
        self.assertFalse([m for m in mods if m.startswith("tb.brain")])


if __name__ == "__main__":
    unittest.main()
