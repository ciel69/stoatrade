"""tb.labels.policies (R1.5, задача 10): реестр, строгая валидация, shadow_ctx и ctx_ver, pick для P2/P4/P5/P6, коды отказов."""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb.core import expstats
from tb.labels import capture, policies
from tb.logic import costs as costs_mod
from tb.logic import geometry

PROJECT = _bootstrap.ROOT
# уровни, на которых геометрия даёт ok-кандидатов обеих сторон (px 100, ATR(5м) 0.2%, издержки 0.24%/0.18%, пороги intraday 3.0%/1.5)
LEVELS = [94.0, 98.8, 99.5, 100.5, 101.2, 106.0]
FEAT = {"px": 100.0, "atr5_pct": 0.2, "levels": LEVELS}
EV = {"spread_bps": 2.0, "range_5m_pct": 0.5, "ret_5m_pct": 0.4, "turn_surge_5m": 3.0}
CFG_FILES = ("shadow_policies.json", "risk_profile.json", "exec_limits.json", "labels.json")


def sig(side="long", typ="range_5m", key="S-20261102000000-DOGE-range_5m", tradable=1):
    return {"key": key, "type": typ, "side": side, "tradable": tradable}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbpol_")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "config"))
        for n in CFG_FILES:
            shutil.copy(os.path.join(PROJECT, "config", n), os.path.join(self.root, "config", n))
        self.cfg = capture.load_cfg(self.root)

    def path(self, name):
        return os.path.join(self.root, "config", name)

    def rd(self, name):
        with open(self.path(name), encoding="utf-8") as f:
            return json.load(f)

    def wr(self, name, d):
        with open(self.path(name), "w", encoding="utf-8") as f:
            json.dump(d, f)

    def pol(self, pid):
        return next(p for p in policies.load(self.root) if p["id"] == pid)

    def ctx(self, feat=None, ev=None):
        return policies.shadow_ctx(self.root, self.cfg, FEAT if feat is None else feat, EV if ev is None else ev)


class TestRegistry(Base):
    def test_start_registry_matches_design(self):
        pols = policies.load(self.root)
        self.assertEqual([(p["id"], p["ver"]) for p in pols], [("P2", 1), ("P4", 1), ("P5", 1), ("P6", 1)])
        p2, p4, p5, p6 = pols
        self.assertEqual((p2["name"], p2["side"], p2["entry"], p2["profile"], p2["max_horizon"], p2["entry_ttl_min"], p2["no_tp"]),
                         ("confirm_follow", "with_dir", "candle_confirm_limit", "base", "intraday", 30, False))
        self.assertEqual(p2["kinds"], ["range_5m", "turnover_surge", "hot_enter", "oi_spike", "liq_cascade"])
        self.assertEqual((p4["name"], p4["side"], p4["entry"], p4["profile"], p4["max_horizon"]), ("fade_confirm", "against_dir", "candle_confirm", "base", "intraday"))
        self.assertEqual(p4["kinds"], ["range_5m", "liq_cascade", "oi_flush"])
        self.assertEqual((p5["name"], p5["side"], p5["entry"], p5["profile"], p5["no_tp"], p5["max_horizon"], p5["entry_ttl_min"], p5["kinds"]),
                         ("time_exit_baseline", "with_dir", "market", "atr", True, "scalp", 2, ["*"]))
        self.assertEqual((p6["name"], p6["side"], p6["entry"], p6["profile"], p6["no_tp"], p6["max_horizon"]), ("random_side_baseline", "random", "market", "atr", False, "scalp"))
        self.assertTrue(all(p["enabled"] for p in pols))
        self.assertEqual(len(policies.active(pols)), 4)

    def test_mature_is_270_plus_grace(self):
        specs = capture.policy_specs(self.root)
        self.assertEqual(sorted(specs), sorted([(30.0, "intraday"), (30.0, "intraday"), (2.0, "scalp"), (2.0, "scalp")]))
        self.assertEqual(capture.mature_ts(1000, self.cfg, specs), 1000 + (270 + self.cfg["grace_min"]) * 60)

    def _bad(self, mutate, msg=None):
        shutil.copy(os.path.join(PROJECT, "config", "shadow_policies.json"), self.path("shadow_policies.json"))
        d = self.rd("shadow_policies.json")
        mutate(d)
        self.wr("shadow_policies.json", d)
        with self.assertRaises(ValueError) as cm:
            policies.load(self.root)
        if msg:
            self.assertIn(msg, str(cm.exception))

    def test_extra_field_rejected(self):
        self._bad(lambda d: d["policies"][0].update(foo=1), "лишние поля")
        self._bad(lambda d: d.update(extra=1), "лишние поля")

    def test_missing_field_rejected(self):
        self._bad(lambda d: d["policies"][1].pop("no_tp"), "нет полей")

    def test_unknown_values_rejected(self):
        self._bad(lambda d: d["policies"][0].update(side="up"))
        self._bad(lambda d: d["policies"][0].update(entry="limit"))
        self._bad(lambda d: d["policies"][0].update(profile="chase"))
        self._bad(lambda d: d["policies"][0].update(max_horizon="month"))
        self._bad(lambda d: d.update(schema="tb.shadow_policies/2"))
        self._bad(lambda d: d["policies"][2].update(entry="candle_confirm"), "atr")

    def test_bad_types_rejected(self):
        self._bad(lambda d: d["policies"][0].update(ver=0))
        self._bad(lambda d: d["policies"][0].update(ver=True))
        self._bad(lambda d: d["policies"][0].update(enabled=1))
        self._bad(lambda d: d["policies"][0].update(no_tp="no"))
        self._bad(lambda d: d["policies"][0].update(kinds=[]))
        self._bad(lambda d: d["policies"][0].update(kinds=["range_5m", "range_5m"]))
        self._bad(lambda d: d["policies"][0].update(entry_ttl_min=0))
        self._bad(lambda d: d["policies"][0].update(entry_ttl_min=True))
        self._bad(lambda d: d.update(policies=[]))

    def test_duplicate_id_ver_rejected(self):
        self._bad(lambda d: d["policies"].append(dict(d["policies"][0])), "дубль")

    def test_versions_do_not_mix(self):
        d = self.rd("shadow_policies.json")
        v2 = dict(d["policies"][0], ver=2, entry_ttl_min=45)
        d["policies"].append(v2)
        self.wr("shadow_policies.json", d)
        pols = policies.load(self.root)
        self.assertEqual([(p["id"], p["ver"]) for p in pols], [("P2", 1), ("P4", 1), ("P5", 1), ("P6", 1), ("P2", 2)])
        ctx = self.ctx()
        a, _ = policies.pick(pols[0], sig(), FEAT, ctx, None)
        b, _ = policies.pick(pols[4], sig(), FEAT, ctx, None)
        self.assertEqual(a["entry"]["ttl_min"], 30)
        self.assertEqual(b["entry"]["ttl_min"], 45)                     # каждая версия берёт свои поля
        self.assertEqual((a["stop"], a["tps"]), (b["stop"], b["tps"]))
        self.assertEqual(len(set((p["id"], p["ver"]) for p in pols)), 5)

    def test_load_missing_file(self):
        os.remove(self.path("shadow_policies.json"))
        with self.assertRaises(ValueError):
            policies.load(self.root)

    def test_load_returns_copies(self):
        a = policies.load(self.root)
        a[0]["kinds"].append("zzz")
        self.assertNotIn("zzz", policies.load(self.root)[0]["kinds"])


class TestApplicable(Base):
    def test_kinds_filter(self):
        p2, p4, p5 = self.pol("P2"), self.pol("P4"), self.pol("P5")
        self.assertTrue(policies.applicable(p2, sig(typ="range_5m")))
        self.assertTrue(policies.applicable(p2, sig(typ="oi_spike")))
        self.assertFalse(policies.applicable(p2, sig(typ="oi_flush")))
        self.assertTrue(policies.applicable(p4, sig(typ="oi_flush")))
        self.assertFalse(policies.applicable(p4, sig(typ="hot_enter")))
        self.assertTrue(policies.applicable(p5, sig(typ="book_wall")))             # '*'

    def test_tradable_false_skips_all(self):
        for p in policies.load(self.root):
            self.assertFalse(policies.applicable(p, sig(tradable=0)))
            self.assertFalse(policies.applicable(p, sig(tradable=False)))
            self.assertTrue(policies.applicable(p, sig(typ="range_5m", tradable=1)))

    def test_disabled(self):
        p = dict(self.pol("P5"), enabled=False)
        self.assertFalse(policies.applicable(p, sig()))


class TestShadowCtx(Base):
    def test_ctx_content(self):
        ctx = self.ctx()
        prof = self.rd("risk_profile.json")
        sc = self.cfg["shadow_ctx"]
        for k in ("risk_level", "session_type", "max_horizon", "off_plan_ok", "day_size_mult", "stop_streak", "no_confirm_allowed"):
            self.assertEqual(ctx[k], sc[k], k)
        self.assertEqual(ctx["px"], 100.0)
        self.assertEqual(ctx["a5_pct"], 0.2)
        self.assertEqual(ctx["spread_bps"], 2.0)
        self.assertEqual(ctx["range_5m_pct"], 0.5)
        self.assertEqual(ctx["surge_x"], 3.0)
        self.assertEqual(ctx["ret_5m"], 0.4)
        for h, c in prof["session_types"].items():                        # горизонты из профиля без поправок уровня риска
            self.assertEqual(ctx["horizons"][h], {"max_stop_pct": round(c["max_stop_pct"], 2), "min_rr": round(c["min_rr"], 2)})
        self.assertEqual(len(ctx["ctx_ver"]), 8)

    def test_default_spread_and_missing(self):
        ctx = self.ctx(feat={"px": 50.0}, ev={})
        self.assertEqual(ctx["spread_bps"], self.cfg["default_spread_bps"])
        self.assertIsNone(ctx["a5_pct"])
        self.assertIsNone(ctx["range_5m_pct"])
        self.assertIsNone(self.ctx(feat={}, ev={})["px"])

    def test_costs_equal_brief(self):
        from tb.brain import brief
        prof = self.rd("risk_profile.json")

        class C:
            active = {"venue": "bybit"}

        C.prof = prof
        ctx = self.ctx()
        self.assertEqual(ctx["cost_rt_pct"], brief._cost_rt_pct(C))
        self.assertEqual(ctx["cost_rt_maker_pct"], brief._cost_rt_pct(C, maker=True))
        self.assertEqual((ctx["cost_rt_pct"], ctx["cost_rt_maker_pct"]), (0.24, 0.18))
        # общий чистый расчёт: прежние числа при любых издержках, в том числе без площадки и без maker_fee
        for p, venue in ((prof, "bybit"), (prof, "platform"), ({"taker_fee_pct": 0.0009}, "x"), ({"costs": {"x": {"fee": 0.001}}}, "x"), ({}, "x")):
            C.prof, C.active = p, {"venue": venue}
            self.assertEqual(costs_mod.round_trip_pct(p, venue), brief._cost_rt_pct(C))
            self.assertEqual(costs_mod.round_trip_pct(p, venue, maker=True), brief._cost_rt_pct(C, maker=True))

    def test_ctx_ver_independent_of_signal(self):
        a = self.ctx()["ctx_ver"]
        self.assertEqual(a, self.ctx(feat={"px": 1.0, "levels": [1, 2]}, ev={"spread_bps": 9.0})["ctx_ver"])
        self.assertEqual(a, self.ctx()["ctx_ver"])

    def test_ctx_ver_changes_with_each_input(self):
        base = self.ctx()["ctx_ver"]
        seen = {base}

        def changed(mut_file, mut, cfg_mut=None):
            orig = {n: self.rd(n) for n in ("risk_profile.json", "exec_limits.json")}
            cfg0 = copy.deepcopy(self.cfg)
            d = copy.deepcopy(orig[mut_file]) if mut_file else None
            if mut:
                mut(d)
                self.wr(mut_file, d)
            if cfg_mut:
                cfg_mut(self.cfg)
            v = self.ctx()["ctx_ver"]
            for n, o in orig.items():
                self.wr(n, o)
            self.cfg = cfg0
            self.assertNotEqual(v, base)
            self.assertNotIn(v, seen)
            seen.add(v)
            self.assertEqual(self.ctx()["ctx_ver"], base)                  # вернули входы -> та же версия

        changed("risk_profile.json", lambda d: d["costs"]["bybit"].__setitem__("fee", 0.0011))
        changed("risk_profile.json", lambda d: d["costs"]["bybit"].__setitem__("maker_fee", 0.0004))
        changed("risk_profile.json", lambda d: d["session_types"]["scalp"].__setitem__("min_rr", 1.2))
        changed("risk_profile.json", lambda d: d["session_types"]["intraday"].__setitem__("max_stop_pct", 2.5))
        changed("exec_limits.json", lambda d: d["auto_protect"].__setitem__("be_r", 0.8))
        changed("exec_limits.json", lambda d: d.__setitem__("stop_slip_bps", 7))
        changed(None, None, lambda c: c.__setitem__("latency_s", 90))
        changed(None, None, lambda c: c["shadow_ctx"].__setitem__("risk_level", 4))
        changed(None, None, lambda c: c["shadow_ctx"].__setitem__("max_horizon", "scalp"))

    def test_ctx_ver_ignores_unrelated(self):
        base = self.ctx()["ctx_ver"]
        d = self.rd("risk_profile.json")
        d["max_leverage"] = 20
        self.wr("risk_profile.json", d)
        self.assertEqual(self.ctx()["ctx_ver"], base)

    def test_sim_inputs(self):
        costs, manage = policies.sim_inputs(self.root, self.cfg, {"spread_bps": 4.0})
        b = self.rd("risk_profile.json")["costs"]["bybit"]
        self.assertEqual((costs["fee"], costs["maker_fee"], costs["slip"], costs["spread_bps"]), (b["fee"], b["maker_fee"], b["slip"], 4.0))
        self.assertEqual(manage["latency_s"], self.cfg["latency_s"])
        self.assertEqual(manage["auto_protect"], self.rd("exec_limits.json")["auto_protect"])
        self.assertEqual(policies.sim_inputs(self.root, self.cfg)[0]["spread_bps"], self.cfg["default_spread_bps"])


class TestPick(Base):
    def setUp(self):
        super().setUp()
        self.ctx0 = self.ctx()

    def pick(self, pid, s, feat=None, ctx=None, cfg=None):
        return policies.pick(self.pol(pid), s, FEAT if feat is None else feat, self.ctx0 if ctx is None else ctx, cfg)

    def test_levels_give_ok_candidates_both_sides(self):
        g = geometry.candidates(LEVELS, dict(self.ctx0, max_horizon="intraday", off_plan_ok=False), None)
        self.assertEqual(sorted(c["side"] for c in g if c.get("ok")), ["long", "long", "short"])

    def test_p2_both_sides(self):
        plan, code = self.pick("P2", sig("long"))
        self.assertIsNone(code)
        self.assertEqual(plan, {"side": "long", "entry": {"kind": "candle_confirm_limit", "level": 100.5, "ttl_min": 30}, "stop": 99.4801,
                                "tps": [102.03, 106.0], "fracs": [0.5, 0.5], "max_hold_min": 240, "tick": 0.001, "horizon": "intraday"})
        plan, code = self.pick("P2", sig("short"))
        self.assertIsNone(code)
        self.assertEqual(plan, {"side": "short", "entry": {"kind": "candle_confirm_limit", "level": 99.5, "ttl_min": 30}, "stop": 100.52,
                                "tps": [97.97, 94.0], "fracs": [0.5, 0.5], "max_hold_min": 240, "tick": 0.001, "horizon": "intraday"})

    def test_p4_fades_direction(self):
        up, c1 = self.pick("P4", sig("long", "oi_flush"))              # событие вверх -> вход против, short
        self.assertIsNone(c1)
        self.assertEqual((up["side"], up["entry"]), ("short", {"kind": "candle_confirm", "level": 99.5, "ttl_min": 30}))
        self.assertEqual((up["stop"], up["tps"]), (100.52, [97.97, 94.0]))
        down, c2 = self.pick("P4", sig("short", "oi_flush"))
        self.assertIsNone(c2)
        self.assertEqual((down["side"], down["entry"]["level"], down["stop"], down["tps"]), ("long", 100.5, 99.4801, [102.03, 106.0]))

    def test_p5_atr_market_no_tp(self):
        for s, stop in (("long", 99.52), ("short", 100.48)):
            plan, code = self.pick("P5", sig(s))
            self.assertIsNone(code)
            self.assertEqual(plan, {"side": s, "entry": {"kind": "market", "level": 100.0, "ttl_min": 2}, "stop": stop, "tps": [], "fracs": [],
                                    "max_hold_min": 60, "tick": 0.001, "horizon": "scalp"})

    def test_p5_stop_is_atr_plan(self):
        ap = geometry.atr_plan("long", self.ctx0, geometry.DEFAULT_PROFILES["atr"])
        plan, _ = self.pick("P5", sig("long"))
        self.assertEqual(plan["stop"], ap["stop"])
        self.assertEqual(ap["stop_pct"], 0.48)                          # пол по рынку 2 x 0.24 = 0.48 больше 1.5 x 0.2

    def test_p6_random_side_uses_assign_variant(self):
        sides = set()
        for i in range(40):
            k = "S-20261102%06d-DOGE-range_5m" % i
            for dirn in ("long", "short", None):
                plan, code = self.pick("P6", sig(dirn, key=k))
                self.assertIsNone(code)                                   # направление события не нужно
                want = expstats.assign_variant(k, ["long", "short"], exp="P6")
                self.assertEqual(plan["side"], want)
                sides.add(plan["side"])
                self.assertEqual(plan["tps"], [plan["tps"][0]])           # тейк на месте (no_tp = false)
                self.assertEqual(plan["fracs"], [1.0])
        self.assertEqual(sides, {"long", "short"})
        a, _ = self.pick("P6", sig(key="K-same"))
        b, _ = self.pick("P6", sig(key="K-same"))
        self.assertEqual(a, b)

    def test_p6_tp_geometry(self):
        for s in ("long", "short"):
            k = next("K%d" % i for i in range(100) if expstats.assign_variant("K%d" % i, ["long", "short"], exp="P6") == s)
            plan, _ = self.pick("P6", sig(key=k))
            sign = 1 if s == "long" else -1
            risk = abs(plan["entry"]["level"] - plan["stop"])
            self.assertAlmostEqual((plan["tps"][0] - plan["entry"]["level"]) * sign, 1.5 * risk, places=2)      # tp_r = 1.5

    def test_no_dir(self):
        for pid in ("P2", "P4", "P5"):
            self.assertEqual(self.pick(pid, sig(None)), (None, "NO_DIR"))
        self.assertIsNotNone(self.pick("P6", sig(None))[0])

    def test_no_level(self):
        for lv in ([], None):
            self.assertEqual(self.pick("P2", sig(), feat=dict(FEAT, levels=lv)), (None, "NO_LEVEL"))
            self.assertEqual(self.pick("P4", sig(), feat=dict(FEAT, levels=lv)), (None, "NO_LEVEL"))
        self.assertEqual(self.pick("P2", sig(), feat={"px": 100.0, "levels": ["x", None, -1, 0]}), (None, "NO_LEVEL"))
        self.assertIsNotNone(self.pick("P5", sig(), feat=dict(FEAT, levels=[]))[0])         # atr уровней не требует

    def test_no_atr_plan(self):
        ctx = dict(self.ctx0, px=None)
        for pid in ("P5", "P6"):
            self.assertEqual(self.pick(pid, sig(), ctx=ctx), (None, "NO_ATR_PLAN"))
        self.assertEqual(self.pick("P5", sig(), ctx=dict(self.ctx0, px=0)), (None, "NO_ATR_PLAN"))

    def test_no_px_for_base(self):
        self.assertEqual(self.pick("P2", sig(), ctx=dict(self.ctx0, px=None)), (None, "NO_PX"))

    def test_geometry_refusal_codes(self):
        low = {"px": 100.0, "atr5_pct": 0.2, "levels": [98.2, 99.2, 100.8, 101.8, 103.5]}       # RR после издержек ниже порога intraday
        self.assertEqual(self.pick("P2", sig("long"), feat=low), (None, "GEO_RR_LOW"))
        self.assertEqual(self.pick("P4", sig("short"), feat=low), (None, "GEO_RR_LOW"))   # против short = long
        self.assertEqual(self.pick("P4", sig("long"), feat=low), (None, "GEO_NO_CAND"))
        one = {"px": 100.0, "atr5_pct": 0.2, "levels": [98.8, 99.5, 100.5, 101.2, 106.0]}       # коротких кандидатов нет вовсе
        self.assertEqual(self.pick("P2", sig("short"), feat=one), (None, "GEO_NO_CAND"))
        self.assertEqual(self.pick("P2", sig("long"), feat=one)[1], None)
        far = {"px": 100.0, "atr5_pct": 0.2, "levels": [50.0, 150.0]}                           # уровни дальше допустимой дистанции
        self.assertEqual(self.pick("P2", sig("long"), feat=far), (None, "GEO_NO_CAND"))

    def test_horizon_not_above_policy_max(self):
        p = dict(self.pol("P2"), max_horizon="scalp")                    # стоп 1% шире лимита scalp (0.8%): intraday недоступен
        plan, code = policies.pick(p, sig("long"), FEAT, self.ctx0, None)
        self.assertIsNone(plan)
        self.assertEqual(code, "GEO_STOP_WIDE")
        p = dict(self.pol("P2"), max_horizon="swing")
        plan, _ = policies.pick(p, sig("long"), FEAT, self.ctx0, None)
        self.assertEqual(plan["horizon"], "intraday")                     # самый короткий подходящий горизонт, не max_horizon
        self.assertEqual(plan["max_hold_min"], geometry.MAX_HOLD_MIN["intraday"])

    def test_no_tp_removes_targets_for_base(self):
        p = dict(self.pol("P2"), no_tp=True)
        plan, _ = policies.pick(p, sig("long"), FEAT, self.ctx0, None)
        self.assertEqual((plan["tps"], plan["fracs"]), ([], []))
        self.assertEqual(plan["stop"], 99.4801)

    def test_chase_never_picked(self):
        ctx = dict(self.ctx0, risk_level=5, no_confirm_allowed=True, surge_x=15.0, range_5m_pct=1.5, ret_5m=1.0)
        g = geometry.candidates(LEVELS, dict(ctx, max_horizon="intraday", off_plan_ok=False), None)
        self.assertTrue(any(c.get("setup") == geometry.CHASE_SETUP for c in g))                   # импульс есть, chase в списке
        plan, _ = self.pick("P2", sig("long"), ctx=ctx)
        self.assertEqual(plan["entry"]["level"], 100.5)                                           # но тень входит на уровне

    def test_geom_cfg_file_and_profiles(self):
        with open(os.path.join(PROJECT, "config", "geometry.json"), encoding="utf-8") as f:
            gcfg = json.load(f)
        self.assertEqual(self.pick("P2", sig("long"), cfg=gcfg), self.pick("P2", sig("long"), cfg=None))
        self.assertEqual(self.pick("P5", sig("long"), cfg=gcfg), self.pick("P5", sig("long"), cfg=gcfg["profiles"]))
        g2 = copy.deepcopy(gcfg)
        g2["profiles"]["atr"]["k_atr"] = 5.0                               # широкий стоп: 1.0%
        plan, _ = self.pick("P5", sig("long"), cfg=g2)
        self.assertEqual(plan["stop"], 99.0)

    def test_plan_runs_in_sim(self):
        from tb.logic import sim
        rows = [(1_000_000 + 60 * i, 100.0, 100.2, 99.9, 100.0, 1.0) for i in range(300)]
        costs, manage = policies.sim_inputs(self.root, self.cfg, EV)
        for pid in ("P2", "P4", "P5", "P6"):
            plan, _ = self.pick(pid, sig("long"))
            r = sim.run(plan, rows, 1_000_000, costs, manage)
            self.assertNotIn("error", r)

    def test_package_import_layering(self):
        code = ("import sys; sys.path.insert(0, %r); import tb.labels.policies; "
                "bad=[m for m in sys.modules if m.split('.')[:2] in (['tb','venues'],['tb','executor'],['tb','gateway'],['tb','llm'],['tb','brain'],['tb','control']) or m=='tb.intel.items']; "
                "print(sorted(bad)); sys.exit(1 if bad else 0)" % os.path.join(PROJECT, "src"))
        r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
