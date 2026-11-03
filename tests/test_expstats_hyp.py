"""core/expstats.py по семействам гипотез (R2, Requirement 19.3, 19.4, свойство 31): нормализация hyp при чтении, K по семействам, `other` не подтверждается,
прочие разрезы без изменений. Временный корень, журнал не переписывается."""
import json
import os
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from test_core_hyps import HISTORY_2026_11_03
from tb.core import expstats as X

# число бумажных сделок по написаниям в журнале на 2026-11-03 (./tb exp stats --by hyp --mode paper: 20 строк, K=20)
COUNTS_2026_11_03 = {"breakout-retest": 2, "breakout_retest": 6, "fade-s5m-res-range": 1, "funding-squeeze-reclaim": 1, "funding_extreme_short": 1, "level_reclaim": 28,
                     "level_reject": 2, "patrol-breakout": 1, "patrol-retest": 1, "range-fade": 1, "range-fade-lower-edge": 1, "range-fade-s5m-res": 2,
                     "range-fade-sup-s5m": 1, "range-top-reject-lvl": 1, "range_fade": 1, "reclaim-funding": 1, "sweep_reject": 2, "trend-pullback": 1,
                     "trend_pullback": 7, "vol-reclaim": 1}


class Root(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbexph_")
        os.makedirs(os.path.join(self.root, "state"))
        self.recs, self.i = [], 0

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def add(self, hyp, rs, mode="paper", **extra):
        for r in rs:
            self.i += 1
            o = {"ev": "open", "id": "T%d" % self.i, "ts": "2026-11-01T10:00:00Z", "sym": "SOL", "side": "long", "mode": mode, "risk_usdt": 1.0, "tags": []}
            if hyp is not None:
                o["hyp"] = hyp
            o.update(extra)
            self.recs += [o, {"ev": "close", "id": o["id"], "ts_close": "2026-11-01T12:00:00Z", "pnl_usdt": r, "r": r, "fees_usdt": 0.01, "reason": "tp"}]

    def load(self, mode="all"):
        p = os.path.join(self.root, "state", "trades.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(x) + "\n" for x in self.recs))
        before = os.path.getmtime(p), os.path.getsize(p)
        out = X.load_trades(mode, self.root, with_rules=False)
        self.assertEqual((os.path.getmtime(p), os.path.getsize(p)), before)     # журнал не переписывается
        return out

    def history(self, rs_of=lambda n: [0.1, -0.1] * (n // 2) + [0.1] * (n % 2)):
        for raw, n in COUNTS_2026_11_03.items():
            self.add(raw, rs_of(n))


class TestNormalizeOnRead(Root):
    def test_history_with_spellings_reduces_to_families(self):
        self.history()
        self.add("Range_Fade", [0.5])
        self.add(None, [0.2])
        ts = self.load("paper")
        self.assertEqual({t["hyp"] for t in ts}, set(HISTORY_2026_11_03.values()) | {None})
        rows = X.group_stats(ts, "hyp")
        got = {r["key"]: r["n"] for r in rows}
        self.assertEqual(got, {"reclaim-level": 29, "breakout-retest": 10, "range-fade": 9, "trend-pullback": 8, "funding-crowded-reversal": 3,
                               "level-reject": 2, "sweep-reject": 2, "(нет)": 1})
        self.assertEqual(sum(got.values()), sum(COUNTS_2026_11_03.values()) + 2)

    def test_k_for_history_is_families_with_n30_not_20(self):
        self.history()
        ts = self.load("paper")
        raw_keys = len(COUNTS_2026_11_03)
        self.assertEqual(raw_keys, 20)
        rows = X.group_stats(ts, "hyp")
        self.assertEqual({r["k"] for r in rows}, {1})                        # ни одно семейство не набрало 30: K не меньше 1
        self.assertAlmostEqual(rows[0]["alpha_adj"], 0.05)
        self.add("Level_Reclaim", [0.1, 0.1])                               # reclaim-level: 28 + 1 + 2 = 31
        self.add("breakout_retest", [0.1] * 20)                             # breakout-retest: 10 + 20 = 30
        ts = self.load("paper")
        rows = X.group_stats(ts, "hyp")
        self.assertEqual({r["k"] for r in rows}, {2})
        self.assertEqual(X.hyp_k(ts), 2)
        self.assertAlmostEqual(rows[0]["alpha_adj"], 0.025)


class TestKByFamilies(Root):
    def test_other_and_untagged_are_not_counted_and_not_confirmed(self):
        good = [1.0, 1.5, 0.8, 1.2] * 8                                     # n = 32, средний R заметно выше нуля
        self.add("probe-x", good)                                           # -> other
        self.add(None, good)                                                # -> (нет)
        self.add("sweep_reject", good)
        ts = self.load()
        rows = {r["key"]: r for r in X.group_stats(ts, "hyp")}
        self.assertEqual(set(rows), {"other", "(нет)", "sweep-reject"})
        self.assertEqual(rows["sweep-reject"]["k"], 1)
        for k in ("other", "(нет)"):
            self.assertIsNotNone(rows[k]["p"])
            self.assertFalse(rows[k]["below_alpha"], k)
            self.assertFalse(X.hyp_confirmed(rows[k]), k)
        self.assertTrue(rows["sweep-reject"]["below_alpha"])
        self.assertTrue(X.hyp_confirmed(rows["sweep-reject"]))

    def test_only_paper_counts_for_k(self):
        self.add("range_fade", [0.1] * 30, mode="live")
        self.add("trend_pullback", [0.1] * 30)
        self.add("breakout-retest", [0.1] * 29)
        ts = self.load()
        self.assertEqual(X.hyp_k(ts), 1)
        self.assertEqual({r["k"] for r in X.group_stats(ts, "hyp")}, {1})

    def test_confirm_rule(self):
        row = {"key": "range-fade", "mode": "paper", "n": 30, "below_alpha": True, "avg_r": 0.2}
        self.assertTrue(X.hyp_confirmed(row))
        for patch in ({"mode": "live"}, {"key": "other"}, {"n": 29}, {"below_alpha": None}, {"avg_r": -0.1}, {"avg_r": None}):
            self.assertFalse(X.hyp_confirmed(dict(row, **patch)), patch)
        self.assertFalse(X.hyp_confirmed(None))

    def test_other_cuts_keep_k_as_number_of_groups(self):
        for i, s in enumerate(("level_reclaim", "range_fade", "sweep_reject")):
            self.add("probe-%d" % i, [0.1] * 30, setup=s)
        ts = self.load()
        rows = X.group_stats(ts, "setup")
        self.assertEqual(([r["key"] for r in rows], {r["k"] for r in rows}), (["level_reclaim", "range_fade", "sweep_reject"], {3}))
        hyp_rows = X.group_stats(ts, "hyp")
        self.assertEqual(([r["key"] for r in hyp_rows], {r["k"] for r in hyp_rows}), (["other"], {1}))


if __name__ == "__main__":
    unittest.main()
