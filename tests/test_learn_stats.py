"""tb.learn.stats и tb.learn.conf (R0.2, задача 1): свойства 7, 8, 12; сверка с expstats; бутстреп; граница года; битый конфиг."""
import datetime
import json
import math
import os
import random
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb.core import expstats as X
from tb.learn import conf, stats as S


def ep(y, m, d, hh=0, mm=0, ss=0):
    return int(datetime.datetime(y, m, d, hh, mm, ss, tzinfo=datetime.timezone.utc).timestamp())


def trade(i, r, pnl=None, fees=0.01, mfe=None, mode="paper"):
    pnl = r if pnl is None else pnl
    return {"id": "T%d" % i, "plan_id": "P%d" % i, "parent": "P%d" % i, "plan_rev": None, "rules_key": None, "exp": None, "var": None,
            "setup": "s", "hyp": None, "agent": None, "model": None, "mem": None, "blackout": "обычные", "mode": mode, "sym": "SOL",
            "stype": "intraday", "side": "long", "r": r, "pnl": pnl, "fees": fees, "risk": 1.0, "reason": "sl", "mae_r": None,
            "mfe_r": mfe, "ts_open": None, "ts_close": None, "shadow": False, "rules": []}


def rand_trades(seed, n):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        r = rng.gauss(0.1, 1.0)
        out.append(trade(i, r, mfe=abs(rng.gauss(1.0, 0.5)) + 0.1))
    return out


class TestEst(unittest.TestCase):
    def test_non_finite_to_none(self):
        for bad in (float("nan"), float("inf"), "x", None, True):
            e = S.est(bad, 0.0, 1.0, 5)
            self.assertIsNone(e["v"])
        e = S.est(1.0, float("nan"), 2.0, 5)
        self.assertEqual((e["lo"], e["hi"]), (None, None))
        self.assertEqual(S.est(1, n=-3)["n"], 0)

    def test_mean_est_interval_contains_value(self):
        for seed in range(20):
            rng = random.Random(seed)
            xs = [rng.gauss(0.2, 1.0) for _ in range(rng.randint(2, 80))]
            e = S.mean_est(xs)
            self.assertEqual(e["n"], len(xs))
            self.assertLessEqual(e["lo"], e["v"])
            self.assertGreaterEqual(e["hi"], e["v"])

    def test_mean_est_matches_formula(self):
        xs = [1.0, 2.0, 4.0, 3.0, 5.0]
        e = S.mean_est(xs)
        m, sd = X._mean_sd(xs)
        h = X.t_quantile(0.975, 4) * sd / math.sqrt(5)
        self.assertAlmostEqual(e["lo"], m - h, places=12)
        self.assertAlmostEqual(e["hi"], m + h, places=12)

    def test_mean_est_small_n(self):
        e = S.mean_est([2.0])
        self.assertEqual((e["v"], e["lo"], e["hi"], e["n"]), (2.0, None, None, 1))
        e = S.mean_est([])
        self.assertEqual((e["v"], e["lo"], e["hi"], e["n"]), (None, None, None, 0))
        e = S.mean_est([1.0, float("nan"), float("inf"), 3.0])
        self.assertEqual(e["n"], 2)

    def test_width_shrinks_with_n(self):
        base = [-1.0, 1.0] * 5
        w_prev = None
        for rep in (1, 2, 4, 8, 16):
            e = S.mean_est(base * rep)
            w = e["hi"] - e["lo"]
            if w_prev is not None:
                self.assertLess(w, w_prev)
            w_prev = w

    def test_wilson_edges(self):
        e = S.wilson_est(0, 20)
        self.assertEqual(e["v"], 0.0)
        self.assertEqual(e["lo"], 0.0)
        self.assertTrue(0.0 < e["hi"] < 0.3)
        e = S.wilson_est(20, 20)
        self.assertEqual(e["v"], 1.0)
        self.assertEqual(e["hi"], 1.0)
        self.assertTrue(0.7 < e["lo"] < 1.0)
        e = S.wilson_est(0, 0)
        self.assertEqual((e["v"], e["lo"], e["hi"], e["n"]), (None, None, None, 0))
        e = S.wilson_est(1, 1)
        self.assertEqual(e["n"], 1)

    def test_wilson_known_value(self):
        e = S.wilson_est(10, 20)                  # эталон: 0.2993 .. 0.7007
        self.assertAlmostEqual(e["lo"], 0.2993, places=3)
        self.assertAlmostEqual(e["hi"], 0.7007, places=3)

    def test_wilson_contains_and_shrinks(self):
        prev = None
        for n in (10, 40, 160, 640):
            e = S.wilson_est(n // 4, n)
            self.assertLessEqual(e["lo"], e["v"])
            self.assertGreaterEqual(e["hi"], e["v"])
            if prev is not None:
                self.assertLess(e["hi"] - e["lo"], prev)
            prev = e["hi"] - e["lo"]


class TestWelch(unittest.TestCase):
    def test_matches_expstats_welch(self):
        for seed in range(20):
            rng = random.Random(seed)
            xs = [rng.gauss(0.3, 1.0) for _ in range(rng.randint(5, 90))]
            ys = [rng.gauss(0.0, 1.5) for _ in range(rng.randint(5, 90))]
            ref = X._welch(xs, ys)
            ma, sa = X._mean_sd(xs)
            mb, sb = X._mean_sd(ys)
            w = S.welch_summary(ma, sa / math.sqrt(len(xs)), len(xs), mb, sb / math.sqrt(len(ys)), len(ys))
            for k in ("diff", "se", "df", "t", "p"):
                self.assertAlmostEqual(w[k], ref[k], delta=1e-9, msg=k)
            ci = X._ci(ref["diff"], ref["se"], ref["df"], 0.95)
            self.assertAlmostEqual(w["lo"], ci[0], delta=1e-9)
            self.assertAlmostEqual(w["hi"], ci[1], delta=1e-9)

    def test_zero_se_and_small_n(self):
        w = S.welch_summary(1.0, 0.0, 30, 0.5, 0.0, 30)
        self.assertEqual(w["diff"], 0.5)
        self.assertEqual(w["se"], 0.0)
        self.assertIsNone(w["p"])
        self.assertIsNone(w["lo"])
        w = S.welch_summary(1.0, 0.2, 1, 0.5, 0.2, 30)
        self.assertIsNone(w["p"])
        self.assertEqual(w["diff"], 0.5)
        w = S.welch_summary(None, 0.2, 30, 0.5, 0.2, 30)
        self.assertIsNone(w["diff"])
        w = S.welch_summary(float("nan"), 0.2, 30, 0.5, 0.2, 30)
        self.assertIsNone(w["diff"])

    def test_interval_contains_diff(self):
        w = S.welch_summary(0.4, 0.1, 40, 0.1, 0.12, 50)
        self.assertLess(w["lo"], w["diff"])
        self.assertGreater(w["hi"], w["diff"])


class TestTwoProp(unittest.TestCase):
    def test_known(self):
        # 40/100 против 25/100: объединённая 0.325, z = 0.15/sqrt(0.325*0.675*0.02)=2.2646
        r = S.two_prop(40, 100, 25, 100)
        self.assertAlmostEqual(r["z"], 2.2646, places=3)
        self.assertAlmostEqual(r["p"], 0.0235, places=3)
        g = S.two_prop(40, 100, 25, 100, one_sided="greater")
        self.assertAlmostEqual(g["p"], r["p"] / 2, places=9)
        ls = S.two_prop(40, 100, 25, 100, one_sided="less")
        self.assertAlmostEqual(ls["p"], 1 - g["p"], places=9)

    def test_symmetry_and_degenerate(self):
        a, b = S.two_prop(30, 100, 50, 120), S.two_prop(50, 120, 30, 100)
        self.assertAlmostEqual(a["p"], b["p"], places=12)
        self.assertAlmostEqual(a["z"], -b["z"], places=12)
        z = S.two_prop(0, 30, 0, 40)
        self.assertEqual((z["z"], z["p"]), (0.0, 1.0))
        self.assertIsNone(S.two_prop(1, 0, 1, 5)["p"])
        self.assertIsNone(S.two_prop(7, 5, 1, 5)["p"])


class TestPf(unittest.TestCase):
    PNL = [1.5, -1.0, 2.0, -1.0, -0.5, 3.0, -1.0, 0.8, -1.0, 1.2] * 5

    def test_deterministic(self):
        a = S.pf_est(self.PNL, 0.9, 500, 7)
        b = S.pf_est(self.PNL, 0.9, 500, 7)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        c = S.pf_est(self.PNL, 0.9, 500, 8)
        self.assertEqual(a["v"], c["v"])
        self.assertNotEqual((a["lo"], a["hi"]), (c["lo"], c["hi"]))

    def test_point_and_interval(self):
        e = S.pf_est(self.PNL, 0.9, 500, 1)
        gw = sum(p for p in self.PNL if p > 0)
        gl = -sum(p for p in self.PNL if p <= 0)
        self.assertAlmostEqual(e["v"], gw / gl, places=12)
        self.assertFalse(e["pf_inf"])
        self.assertEqual(e["n"], len(self.PNL))
        self.assertEqual(e["lvl"], 0.9)
        self.assertLess(e["lo"], e["v"])
        self.assertGreater(e["hi"], e["v"])

    def test_no_losses_is_inf(self):
        e = S.pf_est([1.0, 2.0, 0.5, 3.0], 0.9, 500, 1)
        self.assertTrue(e["pf_inf"])
        self.assertIsNone(e["v"])
        self.assertIsNone(e["hi"])
        json.dumps(e)

    def test_degenerate(self):
        e = S.pf_est([], 0.9, 500, 1)
        self.assertEqual((e["v"], e["lo"], e["hi"], e["n"], e["pf_inf"]), (None, None, None, 0, False))
        e = S.pf_est([-1.0, -2.0, -0.5], 0.9, 500, 1)
        self.assertEqual(e["v"], 0.0)
        e = S.pf_est([0.0, 0.0], 0.9, 500, 1)
        self.assertIsNone(e["v"])
        e = S.pf_est([2.0, float("nan"), -1.0], 0.9, 500, 1)
        self.assertEqual(e["n"], 2)

    def test_does_not_touch_global_random(self):
        random.seed(5)
        want = random.random()
        random.seed(5)
        S.pf_est(self.PNL, 0.9, 200, 3)
        self.assertEqual(random.random(), want)


class TestWindowRow(unittest.TestCase):
    def test_matches_group_stats(self):
        for seed in range(5):
            ts = rand_trades(seed, 40 + seed * 10)
            ref = X.group_stats([dict(t, setup="_w") for t in ts], by="setup")[0]
            row = S.window_row(ts)
            for k in ("n", "n_r", "wins", "avg_r", "sd_r", "se_r", "pf", "pf_inf", "win_rate", "fees_share", "mfe_capture", "small"):
                self.assertEqual(row[k], ref[k], k)

    def test_mixed_modes_one_row(self):
        ts = rand_trades(1, 35)
        ts[3]["mode"] = "live"
        self.assertEqual(S.window_row(ts)["n"], 35)

    def test_small_and_empty(self):
        self.assertTrue(S.window_row(rand_trades(1, 10))["small"])
        self.assertFalse(S.window_row(rand_trades(1, 30))["small"])
        e = S.window_row([])
        self.assertEqual((e["n"], e["avg_r"], e["pf"], e["small"]), (0, None, None, True))

    def test_does_not_mutate_input(self):
        ts = rand_trades(2, 31)
        snap = json.dumps(ts, sort_keys=True)
        S.window_row(ts)
        self.assertEqual(json.dumps(ts, sort_keys=True), snap)


class TestRolling(unittest.TestCase):
    @staticmethod
    def avg(win):
        return sum(t["r"] for t in win) / len(win)

    def test_window_mean_property(self):
        for seed in range(10):
            rng = random.Random(seed)
            n = rng.randint(30, 150)
            w = rng.choice([30, 50, 200])
            ts = rand_trades(seed, n)
            ser = S.rolling(ts, w, self.avg, min_points=30, max_points=1000)
            self.assertEqual(len(ser), n - 29)
            for p in ser:
                i = p["i"]
                last = [t["r"] for t in ts[max(0, i - w):i]]
                self.assertEqual(len(last), min(w, i))
                self.assertAlmostEqual(p["v"], sum(last) / len(last), places=12)
            self.assertEqual(ser[0]["i"], 30)
            self.assertEqual(ser[-1]["i"], n)

    def test_thinning_keeps_last_and_first(self):
        ts = rand_trades(3, 500)
        ser = S.rolling(ts, 50, self.avg, min_points=30, max_points=120)
        self.assertLessEqual(len(ser), 120)
        self.assertEqual(ser[0]["i"], 30)
        self.assertEqual(ser[-1]["i"], 500)
        ix = [p["i"] for p in ser]
        self.assertEqual(ix, sorted(set(ix)))
        self.assertTrue(all(30 <= i <= 500 for i in ix))

    def test_too_few(self):
        self.assertEqual(S.rolling(rand_trades(1, 29), 50, self.avg), [])
        self.assertEqual(S.rolling([], 50, self.avg), [])
        self.assertEqual(len(S.rolling(rand_trades(1, 30), 50, self.avg)), 1)
        self.assertEqual(S.rolling(rand_trades(1, 40), 0, self.avg), [])


class TestWeeks(unittest.TestCase):
    def test_iso_week_year_boundary(self):
        self.assertEqual(S.iso_week(ep(2026, 10, 27, 8, 59, 35)), "2026-W44")
        self.assertEqual(S.iso_week(ep(2026, 1, 1)), "2026-W01")
        self.assertEqual(S.iso_week(ep(2021, 1, 1)), "2020-W53")          # 1 января принадлежит 53-й неделе прошлого ISO-года
        self.assertEqual(S.iso_week(ep(2020, 12, 31, 23, 59, 59)), "2020-W53")
        self.assertEqual(S.iso_week(ep(2024, 12, 30)), "2025-W01")

    def test_week_range_properties(self):
        rng = random.Random(12)
        for _ in range(200):
            asof = rng.randint(ep(2019, 1, 1), ep(2032, 1, 1))
            n = rng.randint(1, 60)
            ws = S.week_range(asof, n)
            self.assertEqual(len(ws), n)
            self.assertTrue(ws[-1]["partial"])
            self.assertFalse(any(w["partial"] for w in ws[:-1]))
            self.assertTrue(ws[-1]["start"] <= asof < ws[-1]["end"])
            for a, b in zip(ws, ws[1:]):
                self.assertEqual(a["end"], b["start"])
            for w in ws:
                self.assertEqual(w["end"] - w["start"], 7 * 86400)
                self.assertEqual(w["week"], S.iso_week(w["start"]))
                self.assertEqual(w["week"], S.iso_week(w["end"] - 1))
                self.assertEqual(datetime.datetime.fromtimestamp(w["start"], datetime.timezone.utc).weekday(), 0)
            names = [w["week"] for w in ws]
            self.assertEqual(len(set(names)), n)

    def test_week_range_across_new_year(self):
        ws = S.week_range(ep(2021, 1, 5, 12), 3)
        self.assertEqual([w["week"] for w in ws], ["2020-W52", "2020-W53", "2021-W01"])
        ws = S.week_range(ep(2026, 1, 1), 2)
        self.assertEqual([w["week"] for w in ws], ["2025-W52", "2026-W01"])

    def test_cases_sum_equals_range(self):
        asof = ep(2021, 1, 6, 10)
        ws = S.week_range(asof, 4)
        rng = random.Random(1)
        pts = [rng.randint(ep(2020, 11, 1), asof) for _ in range(300)]
        lo, hi = ws[0]["start"], ws[-1]["end"]
        inside = [p for p in pts if lo <= p < hi]
        by = {}
        for p in inside:
            by[S.iso_week(p)] = by.get(S.iso_week(p), 0) + 1
        self.assertEqual(sum(by.get(w["week"], 0) for w in ws), len(inside))

    def test_iso_epoch(self):
        self.assertEqual(S.iso_epoch("2026-10-27T08:59:35Z"), ep(2026, 10, 27, 8, 59, 35))
        self.assertEqual(S.iso_epoch("2026-10-27T08:59:35"), ep(2026, 10, 27, 8, 59, 35))
        self.assertEqual(S.iso_epoch("2026-10-27T10:59:35+02:00"), ep(2026, 10, 27, 8, 59, 35))
        for bad in (None, "", "мусор", 5, "2026-13-40T00:00:00Z"):
            self.assertIsNone(S.iso_epoch(bad))


class TestConf(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tblearn_")
        os.makedirs(os.path.join(self.root, "config"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def put(self, text):
        with open(os.path.join(self.root, "config", "learning.json"), "w", encoding="utf-8") as f:
            f.write(text if isinstance(text, str) else json.dumps(text))

    def test_no_file_is_defaults(self):
        cfg, w = conf.load(self.root)
        self.assertEqual(cfg, conf.DEFAULTS)
        self.assertEqual(w, [])
        cfg["windows"].append(999)                        # копия, умолчания не портятся
        self.assertEqual(conf.DEFAULTS["windows"], [50, 200])

    def test_shipped_file_equals_defaults(self):
        import tb.paths
        cfg, w = conf.load(tb.paths.DEFAULT_ROOT)
        self.assertEqual(w, [])
        self.assertEqual(cfg, conf.DEFAULTS)
        with open(tb.paths.config("learning.json", root=tb.paths.DEFAULT_ROOT), encoding="utf-8") as f:
            self.assertEqual(json.load(f), conf.DEFAULTS)

    def test_partial_override(self):
        self.put({"verdict": {"recent_n": 40}, "weeks": 12, "windows": [200, 100]})
        cfg, w = conf.load(self.root)
        self.assertEqual(w, [])
        self.assertEqual(cfg["verdict"], {"recent_n": 40, "guard_trades_ratio": 0.5})
        self.assertEqual(cfg["weeks"], 12)
        self.assertEqual(cfg["windows"], [100, 200])
        self.assertEqual(cfg["cache"], conf.DEFAULTS["cache"])

    def test_bad_file(self):
        for text in ("{не json", "[1, 2]", "", "null"):
            self.put(text)
            cfg, w = conf.load(self.root)
            self.assertEqual(cfg, conf.DEFAULTS)
            self.assertEqual(w, ["E_CONF:file"])

    def test_bad_values_fall_back_per_field(self):
        self.put({"verdict": {"recent_n": 10, "guard_trades_ratio": 1.5}, "windows": [10, 5000], "series_points": "x", "weeks": True,
                  "policy_days": 0, "baseline_policies": [], "memory": {"max_trades": -1, "budget_s": float("nan")},
                  "cache": {"ttl_s": 9999}, "pf_bootstrap": {"b": 10, "seed": 1.5, "level": 1.0}})
        cfg, w = conf.load(self.root)
        self.assertEqual(cfg, conf.DEFAULTS)
        for f in ("verdict.recent_n", "verdict.guard_trades_ratio", "windows", "series_points", "weeks", "policy_days", "baseline_policies",
                  "memory.max_trades", "memory.budget_s", "cache.ttl_s", "pf_bootstrap.b", "pf_bootstrap.seed", "pf_bootstrap.level"):
            self.assertIn("E_CONF:" + f, w)

    def test_good_field_survives_bad_neighbour(self):
        self.put({"weeks": 10, "policy_days": -3})
        cfg, w = conf.load(self.root)
        self.assertEqual(cfg["weeks"], 10)
        self.assertEqual(cfg["policy_days"], 14)
        self.assertEqual(w, ["E_CONF:policy_days"])

    def test_unknown_keys_and_not_configurable(self):
        self.put({"MIN_N": 5, "ALPHA": 0.5, "verdict": {"min_n": 3}, "extra": 1, "schema": "other/9", "memory": 5})
        cfg, w = conf.load(self.root)
        self.assertEqual(cfg, conf.DEFAULTS)
        for f in ("E_CONF:MIN_N", "E_CONF:ALPHA", "E_CONF:verdict.min_n", "E_CONF:extra", "E_CONF:schema", "E_CONF:memory"):
            self.assertIn(f, w)
        self.assertNotIn("MIN_N", cfg)
        self.assertNotIn("ALPHA", cfg)

    def test_baseline_policies_validation(self):
        self.put({"baseline_policies": ["P1", "P1"]})
        self.assertEqual(conf.load(self.root)[1], ["E_CONF:baseline_policies"])
        self.put({"baseline_policies": ["P1", "P2"]})
        cfg, w = conf.load(self.root)
        self.assertEqual((cfg["baseline_policies"], w), (["P1", "P2"], []))


if __name__ == "__main__":
    unittest.main()
