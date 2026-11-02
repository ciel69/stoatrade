import json
import os
import random
import subprocess
import sys
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb.labels import features
from tb.logic import bars

DAY = 86400
BASE = 1793577600            # 2026-11-02 00:00 UTC (понедельник)
CFG = {"coin_class": {"major": ["BTC", "ETH"], "meme": ["DOGE"], "alt_mid": ["SOL"], "turnover_mid_usd": 2e8, "turnover_small_usd": 2e7}}


def flat5(n, t0, px=100.0, hi=101.0, lo=99.0):
    """n закрытых 5м свечей, последняя закрывается ровно в t0."""
    return [(t0 - (n - i) * 300, px, hi, lo, px, 10.0) for i in range(n)]


def walk(rng, start_s, n, step, px=100.0):
    rows = []
    for i in range(n):
        o = px
        c = o * (1 + rng.uniform(-0.002, 0.002))
        rows.append((start_s + i * step, o, max(o, c) * (1 + rng.uniform(0, 0.001)), min(o, c) * (1 - rng.uniform(0, 0.001)), c, rng.uniform(1, 50)))
        px = c
    return rows


def comp(r1=(), r5=(), r1h=(), btc=(), t0=BASE + 12 * 3600, snap=None, sym="SOLUSDT"):
    return features.compute(list(r1), list(r5), list(r1h), list(btc), t0, sym, {}, snap, mem_cfg=CFG)


class Manual(unittest.TestCase):
    def test_flat_series_values(self):
        t0 = BASE + 12 * 3600
        f, miss = comp(r5=flat5(60, t0), btc=flat5(60, t0, 50000.0, 50050.0, 49950.0), t0=t0)
        self.assertAlmostEqual(f["atr5_pct"], 2.0)          # TR = 2 при цене 100
        self.assertAlmostEqual(f["range_15m_pct"], 2.0)
        self.assertAlmostEqual(f["ret_15m_pct"], 0.0)
        self.assertAlmostEqual(f["ret_4h_pct"], 0.0)
        self.assertAlmostEqual(f["vol_rel"], 1.0)
        self.assertAlmostEqual(f["btc_atr5_pct"], 0.2)
        self.assertEqual((f["btc_ret_1h_pct"], f["btc_ret_4h_pct"]), (0.0, 0.0))
        self.assertEqual((f["utc_hour"], f["dow"], f["utc_session"]), (12, 0, "europe"))
        self.assertEqual(f["coin_class"], "alt_mid")
        self.assertEqual(f["levels"], [])
        self.assertEqual(f["n_levels_3atr"], 0)

    def test_returns_hand_computed(self):
        t0 = BASE + 12 * 3600
        r5 = [(t0 - (60 - i) * 300, 100.0, 101.0, 99.0, 100.0 + i, 5.0) for i in range(60)]   # close = 100..159
        f, _ = comp(r5=r5, t0=t0)
        self.assertAlmostEqual(f["ret_15m_pct"], (159 / 156 - 1) * 100)
        self.assertAlmostEqual(f["ret_1h_pct"], (159 / 147 - 1) * 100)
        self.assertAlmostEqual(f["ret_4h_pct"], (159 / 111 - 1) * 100)      # 48 свечей назад: close 111
        self.assertIsNone(comp(r5=r5[-48:], t0=t0)[0]["ret_4h_pct"])        # нужно 49 свечей

    def test_levels_distances(self):
        t0 = BASE + 12 * 3600
        r5 = flat5(60, t0)
        r5[30] = (r5[30][0], 100.0, 110.0, 99.0, 100.0, 10.0)    # пик 110
        r5[40] = (r5[40][0], 100.0, 101.0, 95.0, 100.0, 10.0)    # провал 95
        f, miss = comp(r5=r5, t0=t0)
        self.assertEqual(f["levels"], [95.0, 110.0])
        self.assertAlmostEqual(f["dist_sup_pct"], 5.0)
        self.assertAlmostEqual(f["dist_res_pct"], 10.0)
        self.assertAlmostEqual(f["dist_sup_atr"], 5.0 / f["atr5_pct"])
        self.assertAlmostEqual(f["dist_res_atr"], 10.0 / f["atr5_pct"])
        self.assertEqual(f["n_levels_3atr"], sum(1 for p in (95.0, 110.0) if abs(p - 100) <= 3 * f["atr5_pct"]))
        self.assertNotIn("dist_sup_pct", miss)

    def test_levels_capped_at_12(self):
        rng = random.Random(5)
        t0 = BASE + 2 * DAY + 12 * 3600
        r1 = walk(rng, BASE, (t0 - BASE) // 60, 60)
        f, _ = comp(r1=r1, r5=bars.agg(r1, 5), r1h=bars.agg(r1, 60), t0=t0)
        self.assertLessEqual(len(f["levels"]), 12)
        self.assertGreater(len(f["levels"]), 0)
        self.assertEqual(f["levels"], sorted(f["levels"]))


class Edge(unittest.TestCase):
    def test_new_coin_few_candles(self):
        t0 = BASE + 12 * 3600
        f, miss = comp(r5=flat5(20, t0), t0=t0)
        self.assertAlmostEqual(f["atr5_pct"], 2.0)
        self.assertIsNotNone(f["ret_1h_pct"])
        self.assertIsNone(f["ret_4h_pct"])
        self.assertIsNone(f["vol_rel"])
        self.assertIn("ret_4h_pct", miss)
        self.assertIn("vol_rel", miss)
        self.assertNotIn("atr5_pct", miss)

    def test_no_data_at_all(self):
        f, miss = comp()
        for k in ("atr5_pct", "ret_1h_pct", "dist_sup_pct", "dist_res_pct", "dist_sup_atr", "n_levels_3atr", "btc_ret_1h_pct", "fng"):
            self.assertIsNone(f[k])
            self.assertIn(k, miss)
        self.assertEqual(f["levels"], [])
        self.assertEqual(f["utc_hour"], 12)

    def test_no_levels_distances_null(self):
        t0 = BASE + 12 * 3600
        f, miss = comp(r5=flat5(60, t0), t0=t0)
        for k in ("dist_sup_pct", "dist_res_pct", "dist_sup_atr", "dist_res_atr"):
            self.assertIsNone(f[k])
            self.assertIn(k, miss)
        self.assertEqual(f["levels"], [])

    def test_only_one_side(self):
        t0 = BASE + 12 * 3600
        r5 = flat5(60, t0)
        r5[30] = (r5[30][0], 100.0, 110.0, 99.0, 100.0, 10.0)
        f, miss = comp(r5=r5, t0=t0)
        self.assertIsNone(f["dist_sup_pct"])
        self.assertAlmostEqual(f["dist_res_pct"], 10.0)
        self.assertIn("dist_sup_atr", miss)

    def test_missing_lists_exactly_nulls(self):
        t0 = BASE + 12 * 3600
        f, miss = comp(r5=flat5(20, t0), t0=t0)
        self.assertEqual(sorted(miss), sorted(k for k in features.FEATURE_KEYS if f[k] is None))


class Ctx(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tblf_")
        os.makedirs(os.path.join(self.root, "runtime"))
        self.path = os.path.join(self.root, "runtime", "context.json")

    def write(self, ts_epoch, **kw):
        d = {"ts_epoch": ts_epoch, "risk_off": True, "sources": {"fear_greed": {"value": 37.0}, "dvol_ETH": {"value": 53.06}}}
        d.update(kw)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(d, f)

    def test_fresh(self):
        self.write(1000.0)
        s = features.read_ctx_snapshot(self.root, 1000.0 + 899, 5000, 5000 + 120)
        self.assertEqual(s, {"fng": 37.0, "dvol": 53.06, "risk_off": True})
        f, miss = comp(snap=s)
        self.assertEqual((f["fng"], f["dvol"], f["risk_off"]), (37.0, 53.06, True))
        self.assertNotIn("fng", miss)

    def test_stale_context(self):
        self.write(1000.0)
        s = features.read_ctx_snapshot(self.root, 1000.0 + 901, 5000, 5000)
        self.assertIsNone(s)
        f, miss = comp(snap=s)
        self.assertEqual((f["fng"], f["dvol"], f["risk_off"]), (None, None, None))
        for k in ("fng", "dvol", "risk_off"):
            self.assertIn(k, miss)

    def test_late_capture(self):
        self.write(1000.0)
        self.assertIsNone(features.read_ctx_snapshot(self.root, 1001.0, 5000 + 121, 5000))
        self.assertIsNotNone(features.read_ctx_snapshot(self.root, 1001.0, 5000 + 120, 5000))

    def test_missing_or_broken_file(self):
        self.assertIsNone(features.read_ctx_snapshot(self.root, 1.0, 1, 1))
        with open(self.path, "w") as f:
            f.write("{oops")
        self.assertIsNone(features.read_ctx_snapshot(self.root, 1.0, 1, 1))

    def test_partial_sources(self):
        self.write(1000.0, sources={"fear_greed": {"value": 20}}, risk_off="x")
        s = features.read_ctx_snapshot(self.root, 1000.0, 1, 1)
        self.assertEqual(s, {"fng": 20.0, "dvol": None, "risk_off": None})


class NoFuture(unittest.TestCase):
    """Свойство 3: замена свечей с закрытием после t0 экстремальными значениями не меняет признаки."""

    def run_seed(self, seed):
        rng = random.Random(seed)
        t0 = BASE + 2 * DAY + 13 * 3600 + 37            # не кратно минуте: открытая 1м свеча закрывается после t0
        n_tail = 300
        r1 = walk(rng, BASE - DAY, (t0 - (BASE - DAY)) // 60 + n_tail, 60)
        btc1 = walk(rng, BASE - DAY, len(r1), 60, 50000.0)
        r5, r1h, b5 = bars.agg(r1, 5), bars.agg(r1, 60), bars.agg(btc1, 5)
        snap = {"fng": 40.0, "dvol": 50.0, "risk_off": False}
        a, ma = features.compute(r1, r5, r1h, b5, t0, "SOLUSDT", {}, snap, mem_cfg=CFG)

        def wreck(rows, tf):
            return [(r[0], 9e5 * rng.random() + 1, 1e7, 1e-3, 5e5 * rng.random() + 1, 1e9) if r[0] + tf > t0 else r for r in rows]
        b, mb = features.compute(wreck(r1, 60), wreck(r5, 300), wreck(r1h, 3600), wreck(b5, 300), t0, "SOLUSDT", {}, snap, mem_cfg=CFG)
        self.assertEqual(a, b)
        self.assertEqual(ma, mb)
        cut = lambda rows, tf: [r for r in rows if r[0] + tf <= t0]
        c, _ = features.compute(cut(r1, 60), cut(r5, 300), cut(r1h, 3600), cut(b5, 300), t0, "SOLUSDT", {}, snap, mem_cfg=CFG)
        self.assertEqual(a, c)
        return a

    def test_property_3(self):
        for seed in (1, 2, 3, 4):
            a = self.run_seed(seed)
            for k in ("atr5_pct", "ret_4h_pct", "vol_rel", "btc_ret_4h_pct"):
                self.assertIsNotNone(a[k], k)
            self.assertGreater(len(a["levels"]), 0)


class Isolation(unittest.TestCase):
    def test_no_forbidden_imports(self):
        code = ("import sys; import tb.labels.features; "
                "bad=[m for m in sys.modules if m.startswith(('tb.venues','tb.executor','tb.gateway','tb.llm','tb.brain','tb.control','tb.intel.items'))]; "
                "print(bad)")
        env = dict(os.environ, PYTHONPATH=os.path.join(_bootstrap.ROOT, "src"))
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), "[]", out.stderr)


if __name__ == "__main__":
    unittest.main()
