"""tb.learn.skips (R0.2, задача 7): недельные ряды, net_dir_pct = 0 как верный пропуск, mode_unknown -> no_data, упущенный ход в %, сигнал, свойства."""
import datetime
import json
import random
import types
import unittest

import _bootstrap  # noqa: F401
from tb.learn import conf, skips, stats

UTC = datetime.timezone.utc


def ep(y, m, d, h=12):
    return int(datetime.datetime(y, m, d, h, tzinfo=UTC).timestamp())


def iso(e):
    return datetime.datetime.fromtimestamp(e, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sk(e, pct):
    return {"type": "idea", "ts": iso(e), "whatif": {"net_dir_pct": pct}}


def ctx(cases, now, reason=None, trades=(), weeks=8):
    cfg = conf.defaults()
    cfg["weeks"] = weeks
    return types.SimpleNamespace(now=now, cfg=cfg, skips=(cases, reason), trades=list(trades))


NOW = ep(2026, 11, 2)


def tr(e):
    return {"t_close": e, "r": 1.0}


class TestBlock(unittest.TestCase):
    def test_no_data_reasons(self):
        for why in ("mode_unknown", "no_whatif"):
            b = skips.build(ctx([], NOW, why))
            self.assertEqual((b["status"], b["reason"]), ("no_data", why))
            self.assertEqual(b["weeks"], [])
        b = skips.build(ctx([], NOW, None))
        self.assertEqual((b["status"], b["reason"]), ("no_data", "no_whatif"))
        b = skips.build(types.SimpleNamespace(now=NOW, cfg=conf.defaults()))
        self.assertEqual(b["status"], "no_data")

    def test_mode_unknown_from_load_tuple_ignores_trades(self):
        b = skips.build(ctx([], NOW, "mode_unknown", trades=[tr(NOW)]))
        self.assertEqual(b["reason"], "mode_unknown")
        self.assertIsNone(b["total"])

    def test_zero_is_correct_and_missed_in_percent(self):
        cs = [sk(NOW, 0.0), sk(NOW, -0.4), sk(NOW, 0.5), sk(NOW, 1.5)]
        b = skips.build(ctx(cs, NOW, trades=[tr(NOW), tr(NOW - 86400 * 3)]))
        w = b["weeks"][-1]
        self.assertEqual(w["n"], 4)
        self.assertAlmostEqual(w["correct"]["v"], 0.5)
        self.assertAlmostEqual(w["missed_pct"]["v"], 1.0)
        self.assertEqual(w["missed_pct"]["n"], 2)
        self.assertEqual(w["trades"], 1)
        self.assertEqual(b["weeks"][-2]["trades"], 1)
        t = b["total"]
        self.assertEqual((t["n"], t["wrong_n"]), (4, 2))
        self.assertAlmostEqual(t["missed_pct_sum"], 2.0)
        self.assertAlmostEqual(t["missed_pct_mean"]["v"], 1.0)
        self.assertEqual(b["status"], "few_data")
        self.assertIn("не в R", b["note"])
        self.assertNotIn("missed_r", json.dumps(b))

    def test_week_without_wrong_has_empty_missed(self):
        b = skips.build(ctx([sk(NOW, -1.0), sk(NOW, 0.0)], NOW))
        w = b["weeks"][-1]
        self.assertEqual(w["correct"]["v"], 1.0)
        self.assertEqual((w["missed_pct"]["v"], w["missed_pct"]["n"]), (None, 0))
        self.assertEqual(b["total"]["wrong_n"], 0)
        self.assertEqual(b["total"]["missed_pct_sum"], 0)

    def test_bad_values_skipped(self):
        cs = [sk(NOW, float("nan")), sk(NOW, float("inf")), {"ts": iso(NOW), "whatif": {}}, {"ts": "bad", "whatif": {"net_dir_pct": 1.0}}, sk(NOW, 1.0)]
        b = skips.build(ctx(cs, NOW))
        self.assertEqual(b["total"]["n"], 1)

    def test_year_boundary(self):
        now = ep(2027, 1, 5)
        cs = [sk(ep(2026, 12, 30), 1.0), sk(ep(2027, 1, 4), -1.0), sk(ep(2026, 12, 10), 1.0)]
        b = skips.build(ctx(cs, now, weeks=3))
        self.assertEqual([w["week"] for w in b["weeks"]], ["2026-W52", "2026-W53", "2027-W01"])
        self.assertEqual([w["n"] for w in b["weeks"]], [0, 1, 1])
        self.assertEqual(b["total"]["n"], 2)


class TestSignal(unittest.TestCase):
    def gen(self, p_prev, p_recent, per=40, seed=3, trades=False):
        rng = random.Random(seed)
        cs = []
        for k in range(8):
            p = p_recent if k >= 4 else p_prev
            base = NOW - (7 - k) * 7 * 86400
            for i in range(per):
                cs.append(sk(base + i * 60, -0.5 if rng.random() < p else 0.5))
        return cs

    def test_directions(self):
        self.assertEqual(skips.build(ctx(self.gen(0.3, 0.8), NOW))["signal"], "improving")
        self.assertEqual(skips.build(ctx(self.gen(0.8, 0.3), NOW))["signal"], "worse")
        self.assertEqual(skips.build(ctx(self.gen(0.5, 0.5, seed=9), NOW))["signal"], "flat")

    def test_null_when_few_and_status(self):
        b = skips.build(ctx(self.gen(0.3, 0.8, per=3), NOW))
        self.assertIsNone(b["signal"])
        self.assertEqual(b["status"], "few_data")
        b = skips.build(ctx(self.gen(0.3, 0.8, per=10), NOW))
        self.assertIsNotNone(b["signal"])                # 40 в каждой половине >= 30 -> считается
        self.assertEqual(b["status"], "ok")


class TestProperties(unittest.TestCase):
    def test_random(self):
        for seed in range(20):
            rng = random.Random(seed)
            now = ep(2026, 1, 1) + rng.randrange(0, 400 * 86400)
            wr = stats.week_range(now, 8)
            cs = [sk(now - rng.randrange(-5 * 86400, 80 * 86400), rng.choice([0.0, rng.uniform(-3, 3)])) for _ in range(rng.randrange(0, 150))]
            tt = [tr(now - rng.randrange(0, 70 * 86400)) for _ in range(rng.randrange(0, 40))]
            inside = [c for c in cs if wr[0]["start"] <= stats.iso_epoch(c["ts"]) < wr[-1]["end"]]
            b = skips.build(ctx(cs, now, trades=tt))
            if not inside:
                self.assertEqual(b["status"], "no_data")
                continue
            self.assertEqual(sum(w["n"] for w in b["weeks"]), len(inside))
            self.assertEqual(b["total"]["n"], len(inside))
            wrong = [c["whatif"]["net_dir_pct"] for c in inside if c["whatif"]["net_dir_pct"] > 0]
            self.assertEqual(b["total"]["wrong_n"], len(wrong))
            self.assertAlmostEqual(b["total"]["missed_pct_sum"], sum(wrong))
            self.assertEqual(sum(w["trades"] for w in b["weeks"]), len([t for t in tt if wr[0]["start"] <= t["t_close"] < wr[-1]["end"]]))
            for w in b["weeks"]:
                e = w["correct"]
                if e["v"] is not None:
                    self.assertTrue(e["lo"] <= e["v"] <= e["hi"])
            self.assertEqual(b, skips.build(ctx(list(reversed(cs)), now, trades=tt)))


if __name__ == "__main__":
    unittest.main()
