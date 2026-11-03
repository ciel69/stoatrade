"""tb.learn.discipline (R0.2, задача 7): недельные ряды, неизвестный rule_followed вне знаменателя, граница года (свойство 12), теги, сигнал."""
import datetime
import random
import types
import unittest

import _bootstrap  # noqa: F401
from tb.learn import conf, discipline, stats

UTC = datetime.timezone.utc


def ep(y, m, d, h=12):
    return int(datetime.datetime(y, m, d, h, tzinfo=UTC).timestamp())


def iso(e):
    return datetime.datetime.fromtimestamp(e, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def case(e, rf=None, err=()):
    return {"ts": iso(e), "tags": {"err": list(err), "rule_followed": rf}}


def ctx(cases, now, weeks=8):
    cfg = conf.defaults()
    cfg["weeks"] = weeks
    return types.SimpleNamespace(now=now, cfg=cfg, cases=cases)


NOW = ep(2026, 11, 2)                                   # понедельник 2026-W45


class TestWeeks(unittest.TestCase):
    def test_empty(self):
        b = discipline.build(ctx([], NOW))
        self.assertEqual((b["status"], b["reason"]), ("no_data", "no_cases"))
        self.assertEqual(b["weeks"], [])

    def test_unknown_rf_outside_denominator(self):
        cs = [case(NOW, "no"), case(NOW, "yes"), case(NOW, None, ["late_entry"]), case(NOW, "na"), case(NOW, "partial")]
        b = discipline.build(ctx(cs, NOW))
        w = b["weeks"][-1]
        self.assertEqual((w["n_cases"], w["n_rf"]), (5, 3))
        self.assertAlmostEqual(w["viol"]["v"], 1 / 3)
        self.assertEqual(w["viol"]["n"], 3)
        self.assertEqual(w["err_any"]["n"], 5)
        self.assertAlmostEqual(w["err_any"]["v"], 0.2)
        self.assertEqual(w["err_top"], [["late_entry", 1]])
        self.assertTrue(w["partial"])
        self.assertEqual(b["status"], "few_data")

    def test_empty_week_has_empty_estimates(self):
        b = discipline.build(ctx([case(NOW, "yes")], NOW))
        w = b["weeks"][0]
        self.assertEqual((w["n_cases"], w["n_rf"], w["viol"]["v"], w["err_any"]["v"], w["err_top"]), (0, 0, None, None, []))
        self.assertEqual(len(b["weeks"]), 8)

    def test_tags_trimmed_filtered_and_sorted(self):
        long_tag = "x" * 40
        cs = [case(NOW, "yes", ["a_tag", "good", long_tag]), case(NOW, "yes", ["a_tag"]), case(NOW, "yes", ["b_tag", "good-process-bad-luck"]),
              case(NOW, "no", ["c_tag"]), case(NOW, "no", ["d_tag"])]
        b = discipline.build(ctx(cs, NOW))
        self.assertEqual(b["weeks"][-1]["err_top"], [["a_tag", 2], ["b_tag", 1], ["c_tag", 1]])
        tags = [t["tag"] for t in b["err_total"]]
        self.assertIn("x" * 32, tags)
        self.assertNotIn(long_tag, tags)
        self.assertNotIn("good", tags)
        self.assertLessEqual(len(tags), 8)
        self.assertEqual(b["err_total"][0]["tag"], "a_tag")
        self.assertAlmostEqual(b["err_total"][0]["share"]["v"], 2 / 5)

    def test_zero_violations_flat_signal(self):
        cs = [case(NOW - 7 * 86400 * (i % 8) + i * 60, "yes") for i in range(160)]
        b = discipline.build(ctx(cs, NOW))
        self.assertEqual(b["signal"], "flat")
        for w in b["weeks"]:
            if w["n_rf"]:
                self.assertEqual(w["viol"]["v"], 0.0)
                self.assertGreater(w["viol"]["hi"], 0.0)

    def test_year_boundary(self):
        now = ep(2027, 1, 1)                             # пятница 2026-W53
        self.assertEqual(stats.iso_week(now), "2026-W53")
        cs = [case(ep(2026, 12, 31), "no"), case(ep(2027, 1, 4), "yes"), case(ep(2026, 12, 20), "yes")]
        b = discipline.build(ctx(cs, now, weeks=4))
        self.assertEqual([w["week"] for w in b["weeks"]], ["2026-W50", "2026-W51", "2026-W52", "2026-W53"])
        self.assertEqual([w["n_cases"] for w in b["weeks"]], [0, 1, 0, 1])      # 4 января вне диапазона (будущая неделя)
        now2 = ep(2027, 1, 5)
        b2 = discipline.build(ctx(cs, now2, weeks=3))
        self.assertEqual([w["week"] for w in b2["weeks"]], ["2026-W52", "2026-W53", "2027-W01"])
        self.assertEqual([w["n_cases"] for w in b2["weeks"]], [0, 1, 1])


class TestSignal(unittest.TestCase):
    def mk_cases(self, p_prev, p_recent, per=40, seed=1):
        rng = random.Random(seed)
        cs = []
        for k in range(8):
            p = p_recent if k >= 4 else p_prev
            base = NOW - (7 - k) * 7 * 86400
            for i in range(per):
                cs.append(case(base + i * 60, "no" if rng.random() < p else "yes"))
        return cs

    def test_falling_rising_flat(self):
        self.assertEqual(discipline.build(ctx(self.mk_cases(0.6, 0.1), NOW))["signal"], "falling")
        self.assertEqual(discipline.build(ctx(self.mk_cases(0.1, 0.6), NOW))["signal"], "rising")
        self.assertEqual(discipline.build(ctx(self.mk_cases(0.3, 0.3, seed=5), NOW))["signal"], "flat")

    def test_null_when_few(self):
        cs = self.mk_cases(0.6, 0.1, per=3)              # 12 в каждой половине
        b = discipline.build(ctx(cs, NOW))
        self.assertIsNone(b["signal"])
        self.assertEqual(b["status"], "few_data")        # всего 24 < 30

    def test_few_total_status(self):
        b = discipline.build(ctx(self.mk_cases(0.5, 0.5, per=3), NOW))
        self.assertEqual(b["status"], "few_data")


class TestProperties(unittest.TestCase):
    def test_sum_n_cases_equals_in_range(self):
        for seed in range(20):
            rng = random.Random(seed)
            now = ep(2026, 1, 1) + rng.randrange(0, 400 * 86400)
            wr = stats.week_range(now, 8)
            cs = [case(now - rng.randrange(-5 * 86400, 80 * 86400), rng.choice(["yes", "no", None, "partial", "na"]),
                       rng.sample(["a", "b", "c", "good"], rng.randrange(0, 3))) for _ in range(rng.randrange(0, 120))]
            inside = [c for c in cs if wr[0]["start"] <= stats.iso_epoch(c["ts"]) < wr[-1]["end"]]
            b = discipline.build(ctx(cs, now))
            if not inside:
                self.assertEqual(b["status"], "no_data")
                continue
            self.assertEqual(sum(w["n_cases"] for w in b["weeks"]), len(inside))
            self.assertEqual(b["n_cases"], len(inside))
            self.assertEqual(len(b["weeks"]), 8)
            ws = [w["week"] for w in b["weeks"]]
            self.assertEqual(ws, [x["week"] for x in wr])
            self.assertTrue(b["weeks"][-1]["partial"])
            for w in b["weeks"]:
                self.assertLessEqual(w["n_rf"], w["n_cases"])
                self.assertLessEqual(len(w["err_top"]), 3)
                for e in (w["viol"], w["err_any"]):
                    if e["v"] is not None:
                        self.assertTrue(e["lo"] <= e["v"] <= e["hi"])
            b2 = discipline.build(ctx(list(reversed(cs)), now))
            self.assertEqual(b, b2)                      # порядок случаев не важен


if __name__ == "__main__":
    unittest.main()
