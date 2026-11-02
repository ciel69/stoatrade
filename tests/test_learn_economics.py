"""tb.learn.economics (R0.2, задача 8): недели, отношения только при положительном знаменателе, нулевой расход, неделя без сделок,
отсутствие журнала, усечение журнала, отсутствие запрещённых полей, свойства на random.Random(seed)."""
import datetime
import json
import os
import random
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb.learn import economics as E, stats
from tb.llm import usage as llm_usage

NOW = int(datetime.datetime(2026, 11, 4, 12, 0, tzinfo=datetime.timezone.utc).timestamp())   # среда ISO-недели 2026-W45
CFG = {"weeks": 8}
FORBIDDEN = ("pnl", "balance", "equity", "capital", "risk", "usdt", "key", "secret", "token", "passw", "signature")


def weeks():
    return stats.week_range(NOW, 8)


def row(t, i=100, o=10, usd=0.01):
    return {"t": t, "in": i, "out": o, "usd": usd}


def trade(t_close, r):
    return {"t_close": t_close, "r": r, "pnl_usdt": 123.0, "balance": 999.0}      # посторонние поля в блок не должны попасть


def keys(obj, acc=None):
    acc = set() if acc is None else acc
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(k)
            keys(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            keys(v, acc)
    return acc


class TestCompute(unittest.TestCase):
    def test_week_sums_and_ratios(self):
        w = weeks()[-1]
        rows = [row(w["start"] + 10), row(w["start"] + 20, i=200, o=20, usd=0.02), row(w["start"] - 5)]      # последняя - прошлая неделя
        trades = [trade(w["start"] + 100, 1.5), trade(w["start"] + 200, 0.5)]
        b = E.compute(trades, rows, {}, CFG, NOW)
        self.assertEqual(b["status"], "ok")
        self.assertEqual(b["scope"], "platform")
        self.assertEqual(len(b["weeks"]), 8)
        cur = b["weeks"][-1]
        self.assertTrue(cur["partial"])
        self.assertEqual((cur["calls"], cur["tok_in"], cur["tok_out"]), (2, 300, 30))
        self.assertAlmostEqual(cur["llm_usd"], 0.03)
        self.assertEqual((cur["n_trades"], cur["r_sum"]), (2, 2.0))
        self.assertEqual(cur["tok_per_trade"], 165)
        self.assertAlmostEqual(cur["usd_per_trade"], 0.015)
        self.assertAlmostEqual(cur["usd_per_r"], 0.015)
        self.assertEqual(b["weeks"][-2]["calls"], 1)

    def test_week_without_trades_keeps_spend(self):
        w = weeks()[-1]
        b = E.compute([], [row(w["start"] + 1)], {}, CFG, NOW)
        cur = b["weeks"][-1]
        self.assertEqual((cur["n_trades"], cur["calls"]), (0, 1))
        self.assertGreater(cur["llm_usd"], 0)
        self.assertEqual((cur["tok_per_trade"], cur["usd_per_trade"], cur["usd_per_r"]), (None, None, None))

    def test_usd_per_r_only_when_r_sum_positive(self):
        w = weeks()[-1]
        for r, want_none in ((-1.0, True), (0.0, True), (0.5, False)):
            cur = E.compute([trade(w["start"] + 5, r)], [row(w["start"] + 1)], {}, CFG, NOW)["weeks"][-1]
            self.assertEqual(cur["usd_per_r"] is None, want_none, r)
            self.assertIsNotNone(cur["usd_per_trade"])

    def test_zero_spend_is_valid(self):
        w = weeks()[-1]
        rows = [row(w["start"] + 1, usd=0.0), row(w["start"] + 2, usd=0.0)]
        b = E.compute([trade(w["start"] + 5, 2.0)], rows, {}, CFG, NOW)
        cur = b["weeks"][-1]
        self.assertEqual(b["status"], "ok")
        self.assertEqual((cur["llm_usd"], cur["usd_per_r"], cur["usd_per_trade"]), (0.0, 0.0, 0.0))
        self.assertEqual(cur["tok_in"], 200)

    def test_no_usage(self):
        b = E.compute([trade(NOW, 1.0)], [], {}, CFG, NOW)
        self.assertEqual((b["status"], b["reason"]), ("no_data", "no_usage"))
        self.assertEqual(b["weeks"], [])
        self.assertIsNone(b["signal"])

    def test_truncated_warning(self):
        self.assertEqual(E.compute([], [row(NOW)], {"truncated": True}, CFG, NOW)["warnings"], ["usage_truncated"])
        self.assertEqual(E.compute([], [row(NOW)], {"truncated": False}, CFG, NOW)["warnings"], [])

    def test_bad_rows_do_not_break(self):
        w = weeks()[-1]
        rows = [{"t": w["start"] + 1, "in": "x", "out": None, "usd": float("nan")}, {"t": None}, {"in": 5}, row(w["start"] + 2, i=-5)]
        cur = E.compute([], rows, {}, CFG, NOW)["weeks"][-1]
        self.assertEqual((cur["calls"], cur["tok_in"], cur["tok_out"]), (2, 0, 10))
        self.assertEqual(cur["llm_usd"], 0.01)

    def test_no_forbidden_fields(self):
        w = weeks()[-1]
        b = E.compute([trade(w["start"] + 5, 1.0)], [row(w["start"] + 1)], {"truncated": True}, CFG, NOW)
        for k in keys(b):
            self.assertFalse(any(f in k.lower() for f in FORBIDDEN), k)
        self.assertNotIn("123.0", json.dumps(b))
        self.assertNotIn("999.0", json.dumps(b))

    def test_signal(self):
        ws = weeks()

        def mk(usd_old, usd_new, r_old=10.0, r_new=10.0, n=30):
            rows, trades = [], []
            for k, w in enumerate(ws):
                new = k >= 4
                rows.append(row(w["start"] + 1, usd=(usd_new if new else usd_old) / 4))
                for j in range(8):
                    trades.append(trade(w["start"] + 100 + j, (r_new if new else r_old) / 8))
            return E.compute(trades, rows, {}, CFG, NOW)
        self.assertEqual(mk(4.0, 2.0)["signal"], "falling")
        self.assertEqual(mk(2.0, 4.0)["signal"], "rising")
        self.assertEqual(mk(4.0, 4.1)["signal"], "flat")
        self.assertEqual(mk(0.0, 0.0)["signal"], "flat")
        self.assertEqual(mk(0.0, 1.0)["signal"], "rising")
        self.assertIsNone(mk(4.0, 2.0, r_new=-1.0)["signal"])         # r_sum <= 0 в любой половине
        self.assertIsNone(mk(4.0, 2.0, r_old=0.0)["signal"])
        few = E.compute([trade(ws[-1]["start"] + 5, 1.0)], [row(ws[0]["start"] + 1)], {}, CFG, NOW)
        self.assertIsNone(few["signal"])                               # сделок < 30
        self.assertIsNone(E.compute([], [row(NOW)], {}, {"weeks": 6}, NOW)["signal"])               # недель меньше 8


class TestProperties(unittest.TestCase):
    def test_totals_match_and_ratios(self):
        for seed in range(20):
            rng = random.Random(seed)
            ws = weeks()
            lo, hi = ws[0]["start"], ws[-1]["end"]
            rows = [row(rng.randint(lo, hi - 1), rng.randint(0, 5000), rng.randint(0, 800), round(rng.random() * 0.05, 6)) for _ in range(rng.randint(1, 80))]
            trades = [trade(rng.randint(lo, hi - 1), round(rng.uniform(-2, 3), 4)) for _ in range(rng.randint(0, 60))]
            outside = [row(lo - 1 - rng.randint(0, 99999)), row(hi + rng.randint(0, 99999))]
            rng.shuffle(rows)
            b = E.compute(trades, rows + outside, {}, CFG, NOW)
            self.assertEqual(sum(w["calls"] for w in b["weeks"]), len(rows))
            self.assertEqual(sum(w["tok_in"] for w in b["weeks"]), sum(r["in"] for r in rows))
            self.assertEqual(sum(w["tok_out"] for w in b["weeks"]), sum(r["out"] for r in rows))
            self.assertAlmostEqual(sum(w["llm_usd"] for w in b["weeks"]), sum(r["usd"] for r in rows), places=4)
            self.assertEqual(sum(w["n_trades"] for w in b["weeks"]), len(trades))
            for w in b["weeks"]:
                self.assertEqual(w["usd_per_r"] is None, not w["r_sum"] > 0)
                self.assertEqual(w["tok_per_trade"] is None, w["n_trades"] == 0)
                if w["usd_per_r"] is not None:
                    self.assertGreaterEqual(w["usd_per_r"], 0)
            # перестановка строк журнала не меняет результат
            rng.shuffle(rows)
            self.assertEqual(E.compute(trades, rows + outside, {}, CFG, NOW), b)


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tblearn_eco_")
        os.makedirs(os.path.join(self.root, "state"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def ctx(self, trades=()):
        return {"root": self.root, "mode": "paper", "now": NOW, "cfg": CFG, "trades": list(trades)}

    def test_no_journal_file(self):
        b = E.build(self.ctx())
        self.assertEqual((b["status"], b["reason"]), ("no_data", "no_usage"))

    def test_reads_journal(self):
        w = weeks()[-1]
        llm_usage.record({}, "p", "m", usd=0.5, root=self.root, now=w["start"] + 10)
        b = E.build(self.ctx([trade(w["start"] + 50, 1.0)]))
        self.assertEqual(b["status"], "ok")
        self.assertEqual(b["weeks"][-1]["calls"], 1)
        self.assertAlmostEqual(b["weeks"][-1]["usd_per_r"], 0.5)
        self.assertEqual(b["warnings"], [])

    def test_truncated_journal(self):
        w = weeks()[-1]
        for k in range(5):
            llm_usage.record({}, "p", "m", usd=0.1, root=self.root, now=w["start"] + 10 + k)
        old = llm_usage.MAX_READ
        llm_usage.MAX_READ = 100
        try:
            b = E.build(self.ctx())
        finally:
            llm_usage.MAX_READ = old
        self.assertIn("usage_truncated", b["warnings"])

    def test_ctx_object_with_attributes(self):
        class C:
            root, mode, now, cfg, trades = self.root, "paper", NOW, CFG, []
            usage, usage_meta = [row(NOW - 10)], {"truncated": True}
        b = E.build(C())
        self.assertEqual(b["status"], "ok")
        self.assertEqual(b["warnings"], ["usage_truncated"])


if __name__ == "__main__":
    unittest.main()
