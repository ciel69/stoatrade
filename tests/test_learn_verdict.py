"""tb.learn.verdict (R0.2, задача 4): свойства 3, 4, 5; границы n = 29/30/59/60; trend; нулевой разброс; порядок причин; тексты."""
import datetime
import random
import unittest

import _bootstrap  # noqa: F401
from tb.learn import conf, verdict as V

BASE = 1_700_000_000
CFG = conf.defaults()
SEEDS = range(20)


def iso(e):
    return datetime.datetime.fromtimestamp(e, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mk(prev_rs, recent_rs, step_prev=6 * 3600, step_recent=6 * 3600):
    """Сделки: прежние с шагом step_prev, затем недавние с шагом step_recent. -> (trades, t_cut)."""
    out, t = [], BASE
    for i, r in enumerate(list(prev_rs) + list(recent_rs)):
        t += step_prev if i < len(prev_rs) else step_recent
        out.append({"id": "T%03d" % i, "r": r, "t_close": t, "ts_close": iso(t), "ts_open": iso(t - 60)})
    cut = out[len(prev_rs)]["t_close"] - 60 if recent_rs else None
    return out, cut


def mk_cases(cut, prev_no, prev_n, rec_no, rec_n):
    cs = []
    for i in range(prev_n):
        cs.append({"ts": iso(cut - 1000 - i), "tags": {"rule_followed": "no" if i < prev_no else "yes"}})
    for i in range(rec_n):
        cs.append({"ts": iso(cut + 1000 + i), "tags": {"rule_followed": "no" if i < rec_no else "yes"}})
    return cs


def gauss(rng, n, mu, sd=1.0):
    return [rng.gauss(mu, sd) for _ in range(n)]


def decide_for(seed, n_prev, n_rec, mu_prev=0.0, mu_rec=0.0, **kw):
    rng = random.Random(seed)
    tr, cut = mk(gauss(rng, n_prev, mu_prev), gauss(rng, n_rec, mu_rec), **kw)
    return tr, cut


class TestBoundaries(unittest.TestCase):
    def test_few_data_below_30(self):
        for seed in SEEDS:
            for n in (0, 1, 29):
                rng = random.Random(seed)
                tr, _ = mk([], gauss(rng, n, rng.uniform(-5, 5), rng.uniform(0, 10)))
                v = V.decide(tr, [], CFG)
                self.assertEqual((v["state"], v["reason"], v["trend"]), ("few_data", "n_lt_min", None))
                self.assertEqual(v["n"], n)

    def test_no_baseline_30_to_59(self):
        for seed in SEEDS:
            for n in (30, 31, 59):
                rng = random.Random(seed)
                tr, _ = mk([], gauss(rng, n, rng.uniform(-5, 5), rng.uniform(0, 10)))
                v = V.decide(tr, [], CFG)
                self.assertEqual((v["state"], v["reason"], v["trend"]), ("no_difference", "no_baseline", None))

    def test_60_splits_30_30(self):
        tr, _ = decide_for(1, 30, 30)
        v = V.decide(tr, [], CFG)
        self.assertEqual((v["n"], v["n_recent"], v["n_prev"]), (60, 30, 30))
        self.assertIn(v["state"], ("no_difference", "confirmed"))
        self.assertIn(v["trend"], ("up", "down", "flat"))
        self.assertAlmostEqual(v["alpha_adj"], 0.05)

    def test_recent_window_capped(self):
        tr, _ = decide_for(2, 150, 50)
        v = V.decide(tr, [], CFG)
        self.assertEqual((v["n_recent"], v["n_prev"]), (50, 150))
        tr, _ = decide_for(2, 40, 40)
        v = V.decide(tr, [], CFG)
        self.assertEqual((v["n_recent"], v["n_prev"]), (50, 30))

    def test_non_finite_r_not_counted(self):
        tr, _ = decide_for(3, 20, 20)
        tr += [{"id": "X1", "r": None, "t_close": BASE}, {"id": "X2", "r": float("nan"), "t_close": BASE}, {"id": "X3", "r": "a", "t_close": BASE}]
        self.assertEqual(V.decide(tr, [], CFG)["n"], 40)

    def test_empty(self):
        v = V.decide([], [], CFG)
        self.assertEqual((v["state"], v["n"]), ("few_data", 0))
        v = V.decide(None, None, None)
        self.assertEqual(v["state"], "few_data")


class TestMonotone(unittest.TestCase):
    def test_shift_recent_is_monotone(self):
        shifts = [-3, -1.5, -0.5, 0, 0.3, 0.7, 1.0, 1.5, 3]
        seen = set()
        for seed in SEEDS:
            rng = random.Random(seed)
            prev = gauss(rng, 100, rng.uniform(-0.3, 0.3), rng.uniform(0.5, 1.5))
            rec = gauss(rng, 50, 0.0, rng.uniform(0.5, 1.5))
            ranks = []
            for d in shifts:
                tr, _ = mk(prev, [r + d for r in rec])
                v = V.decide(tr, [], CFG)
                ranks.append(V.RANK[v["state"]])
                seen.add(v["state"])
            self.assertEqual(ranks, sorted(ranks), "seed %d: %s" % (seed, ranks))
        self.assertEqual(seen, {"no_difference", "confirmed"})

    def test_monotone_with_fixed_guards(self):
        for seed in SEEDS:
            rng = random.Random(seed)
            prev, rec = gauss(rng, 80, 0.0), gauss(rng, 50, 0.1)
            tr0, cut = mk(prev, rec)
            cs = mk_cases(cut, 5, 20, 3, 20)
            last = -1
            for d in (0, 0.2, 0.4, 0.8, 1.6):
                tr, _ = mk(prev, [r + d for r in rec])
                rk = V.RANK[V.decide(tr, cs, CFG)["state"]]
                self.assertGreaterEqual(rk, last)
                last = rk

    def test_order_invariant(self):
        for seed in SEEDS:
            tr, cut = decide_for(seed, 80, 50, 0.0, 0.5)
            cs = mk_cases(cut, 4, 20, 3, 20)
            a = V.decide(tr, cs, CFG)
            sh = list(tr)
            random.Random(seed).shuffle(sh)
            self.assertEqual(a, V.decide(sh, cs, CFG))
            self.assertEqual(a, V.decide(tr, cs, CFG))


class TestGuards(unittest.TestCase):
    def strong(self, seed=5, **kw):
        rng = random.Random(seed)
        return mk(gauss(rng, 100, 0.0), gauss(rng, 50, 1.5), **kw)

    def test_confirmed_baseline(self):
        tr, cut = self.strong()
        v = V.decide(tr, mk_cases(cut, 3, 20, 3, 20), CFG)
        self.assertEqual((v["state"], v["reason"], v["trend"]), ("confirmed", "improved", "up"))
        self.assertTrue(v["guards"]["viol"]["ok"] and v["guards"]["trades_day"]["ok"])
        self.assertLess(v["p"], v["alpha_adj"])
        self.assertGreater(v["diff_r"]["v"], 0)
        self.assertEqual(v["diff_r"]["lvl"], 0.95)
        self.assertLess(v["diff_r"]["lo"], v["diff_r"]["v"])

    def test_viol_guard_blocks(self):
        for seed in SEEDS:
            tr, cut = self.strong(seed)
            cs = mk_cases(cut, 2, 20, 14, 20)          # доля нарушений 0.1 -> 0.7
            v = V.decide(tr, cs, CFG)
            self.assertFalse(v["guards"]["viol"]["ok"])
            self.assertNotEqual(v["state"], "confirmed")
            self.assertEqual(v["reason"], "guard_viol")

    def test_viol_unknown_does_not_block(self):
        tr, cut = self.strong()
        for cs in ([], mk_cases(cut, 0, 9, 9, 9), mk_cases(cut, 0, 30, 9, 9), mk_cases(cut, 0, 9, 9, 30)):
            v = V.decide(tr, cs, CFG)
            self.assertEqual(v["state"], "confirmed")
            self.assertTrue(v["guards"]["viol"]["guard_unknown"])
            self.assertTrue(v["guards"]["viol"]["ok"])

    def test_viol_known_at_10(self):
        tr, cut = self.strong()
        v = V.decide(tr, mk_cases(cut, 0, 10, 10, 10), CFG)
        self.assertFalse(v["guards"]["viol"]["guard_unknown"])
        self.assertFalse(v["guards"]["viol"]["ok"])

    def test_viol_unknown_rule_ignored(self):
        tr, cut = self.strong()
        cs = mk_cases(cut, 2, 20, 2, 20) + [{"ts": iso(cut + 5), "tags": {"rule_followed": "na"}}, {"ts": iso(cut + 6), "tags": {"rule_followed": None}},
                                          {"ts": "bad", "tags": {"rule_followed": "no"}}, {"ts": iso(cut + 7)}]
        v = V.decide(tr, cs, CFG)
        self.assertEqual(v["guards"]["viol"]["n_recent"], 20)
        self.assertEqual(v["state"], "confirmed")

    def test_viol_share_not_higher_passes(self):
        tr, cut = self.strong()
        v = V.decide(tr, mk_cases(cut, 14, 20, 2, 20), CFG)      # нарушений стало меньше
        self.assertTrue(v["guards"]["viol"]["ok"])
        self.assertEqual(v["state"], "confirmed")

    def test_trades_guard_blocks(self):
        for seed in SEEDS:
            tr, cut = self.strong(seed, step_prev=3600, step_recent=10 * 3600)    # 24/сут против 2.4/сут
            v = V.decide(tr, mk_cases(cut, 3, 20, 3, 20), CFG)
            self.assertFalse(v["guards"]["trades_day"]["ok"])
            self.assertEqual((v["state"], v["reason"]), ("no_difference", "guard_trades"))
            self.assertLess(v["guards"]["trades_day"]["ratio"], 0.5)

    def test_trades_guard_boundary(self):
        # 50 недавних с шагом 12ч (49 сегментов -> 24 полных суток -> 2.08/сут), 100 прежних с шагом 6ч -> 4/сут; ratio ~0.52 проходит
        tr, _ = self.strong(step_recent=12 * 3600)
        v = V.decide(tr, [], CFG)
        self.assertTrue(v["guards"]["trades_day"]["ok"])
        cfg = conf.defaults()
        cfg["verdict"]["guard_trades_ratio"] = 0.6
        v = V.decide(tr, [], cfg)
        self.assertFalse(v["guards"]["trades_day"]["ok"])
        self.assertEqual(v["params"]["guard_trades_ratio"], 0.6)

    def test_reason_order_viol_before_trades_before_significance(self):
        tr, cut = self.strong(step_prev=3600, step_recent=10 * 3600)
        v = V.decide(tr, mk_cases(cut, 2, 20, 14, 20), CFG)
        self.assertEqual(v["reason"], "guard_viol")
        rng = random.Random(1)
        tr, cut = mk(gauss(rng, 100, 0.0), gauss(rng, 50, 0.0), step_prev=3600, step_recent=10 * 3600)
        v = V.decide(tr, [], CFG)
        self.assertEqual(v["reason"], "guard_trades")
        tr, cut = mk(gauss(rng, 100, 0.0), gauss(rng, 50, 0.0))
        v = V.decide(tr, [], CFG)
        self.assertEqual(v["reason"], "not_significant")

    def test_property5_never_confirmed(self):
        for seed in SEEDS:
            rng = random.Random(seed)
            prev, rec = gauss(rng, rng.randint(40, 150), rng.uniform(-1, 1)), gauss(rng, rng.randint(30, 60), rng.uniform(-1, 2))
            sp, sr = rng.choice([3600, 6 * 3600]), rng.choice([3600, 6 * 3600, 20 * 3600])
            tr, cut = mk(prev, rec, step_prev=sp, step_recent=sr)
            cs = mk_cases(cut, rng.randint(0, 10), 20, rng.randint(0, 20), 20)
            v = V.decide(tr, cs, CFG)
            if v["state"] == "confirmed":
                self.assertGreater(v["diff_r"]["v"], 0)
                self.assertLess(v["p"], v["alpha_adj"])
                self.assertTrue(v["guards"]["viol"]["ok"] and v["guards"]["trades_day"]["ok"])
            else:
                if v["n"] >= 60:
                    self.assertTrue(not v["guards"]["viol"]["ok"] or not v["guards"]["trades_day"]["ok"] or v["diff_r"]["v"] <= 0 or v["p"] is None or v["p"] >= v["alpha_adj"])

    def test_losing_recent_with_outlier_not_confirmed(self):
        rng = random.Random(7)
        rec = [-0.5] * 49 + [-0.1]
        tr, cut = mk(gauss(rng, 100, 0.0), rec)
        v = V.decide(tr, [], CFG)
        self.assertNotEqual(v["state"], "confirmed")
        self.assertEqual(v["trend"], "down")


class TestTrendAndText(unittest.TestCase):
    def test_trend_down(self):
        rng = random.Random(11)
        tr, cut = mk(gauss(rng, 100, 0.5), gauss(rng, 50, -1.0))
        v = V.decide(tr, mk_cases(cut, 2, 20, 2, 20), CFG)
        self.assertEqual((v["state"], v["trend"]), ("no_difference", "down"))
        self.assertLess(v["diff_r"]["v"], 0)
        self.assertIn("стало хуже", v["text"])
        self.assertLessEqual(len(v["text"]), 160)

    def test_trend_flat_and_up(self):
        rng = random.Random(12)
        tr, _ = mk(gauss(rng, 100, 0.0), gauss(rng, 50, 0.0))
        v = V.decide(tr, [], CFG)
        self.assertEqual(v["trend"], "flat")
        self.assertNotIn("стало хуже", v["text"])
        tr, _ = mk(gauss(rng, 100, 0.0), gauss(rng, 50, 2.0), step_recent=3600 * 30)
        self.assertEqual(V.decide(tr, [], CFG)["trend"], "up")

    def test_zero_variance(self):
        tr, _ = mk([0.2] * 100, [0.5] * 50)
        v = V.decide(tr, [], CFG)
        self.assertIsNone(v["p"])
        self.assertEqual((v["state"], v["reason"], v["trend"]), ("no_difference", "not_significant", "flat"))
        self.assertAlmostEqual(v["diff_r"]["v"], 0.3)
        self.assertIsNone(v["diff_r"]["lo"])
        self.assertIn("Сделок 150", v["text"])

    def test_texts_all_cases(self):
        seen = set()
        rng = random.Random(3)
        cases = []
        for n in (0, 29, 30, 59):
            tr, _ = mk([], gauss(rng, n, 0.0))
            cases.append((tr, []))
        for step_r, no_r, mu in ((6 * 3600, 3, 1.5), (6 * 3600, 3, 0.0), (6 * 3600, 14, 1.5), (40 * 3600, 3, 1.5), (6 * 3600, 3, -1.5)):
            tr, cut = mk(gauss(rng, 100, 0.0), gauss(rng, 50, mu), step_recent=step_r)
            cases.append((tr, mk_cases(cut, 2, 20, no_r, 20)))
        for tr, cs in cases:
            v = V.decide(tr, cs, CFG)
            seen.add((v["state"], v["reason"]))
            self.assertLessEqual(len(v["text"]), 160)
            self.assertIn(str(v["n"]), v["text"])
            self.assertNotIn("{", v["text"])
            self.assertEqual(("стало хуже" in v["text"]), v["trend"] == "down")
        self.assertEqual(seen, set(V.TEXTS))
        for key, tpl in V.TEXTS.items():
            full = tpl.format(n=9999, min_n=30, need=60, need2=60, diff="+10,00", lo="-10,00", hi="+10,00") + V.TEXT_DOWN
            self.assertLessEqual(len(full), 160, key)

    def test_params_and_cfg_fallback(self):
        tr, _ = decide_for(4, 100, 50)
        v = V.decide(tr, [], CFG)
        self.assertEqual(v["params"], {"min_n": 30, "alpha": 0.05, "k": 1, "recent_n": 50, "guard_trades_ratio": 0.5})
        cfg = {"verdict": {"recent_n": 40, "guard_trades_ratio": 0.7}}
        v = V.decide(tr, [], cfg)
        self.assertEqual((v["n_recent"], v["params"]["recent_n"], v["params"]["guard_trades_ratio"]), (40, 40, 0.7))
        v = V.decide(tr, [], {"verdict": {"recent_n": 5, "guard_trades_ratio": 9}})
        self.assertEqual((v["params"]["recent_n"], v["params"]["guard_trades_ratio"]), (50, 0.5))

    def test_verdict_has_required_fields(self):
        tr, cut = decide_for(4, 100, 50)
        v = V.decide(tr, mk_cases(cut, 2, 20, 2, 20), CFG)
        for k in ("state", "reason", "text", "n", "n_recent", "n_prev", "diff_r", "p", "alpha_adj", "trend", "guards", "params"):
            self.assertIn(k, v)
        for k in ("ok", "guard_unknown", "share_recent", "share_prev", "n_recent", "n_prev", "p"):
            self.assertIn(k, v["guards"]["viol"])
        for k in ("ok", "recent", "prev", "ratio"):
            self.assertIn(k, v["guards"]["trades_day"])


if __name__ == "__main__":
    unittest.main()
