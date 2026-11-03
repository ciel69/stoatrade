"""tb.logic.bars: свечи для разметки (R1.1, задача 1): порядок и незакрытая свеча, агрегация по UTC, ATR, пропуски, MFE/MAE (свойство 4)."""
import random
import unittest

import _bootstrap  # noqa: F401
from tb.logic import bars

T0 = 1_700_000_040          # кратно 60 (и 1_700_000_040 % 300 == 240, поэтому границу 5м берём отдельно)
M5 = 1_700_000_100          # кратно 300 (1_700_000_100 / 300 = 5666667)


def bybit(ts_s, o, h, l, c, v=1):
    return [str(ts_s * 1000), str(o), str(h), str(l), str(c), str(v), "0"]


def row(ts, o, h, l, c, v=1.0):
    return (ts, float(o), float(h), float(l), float(c), float(v))


class TestFromBybit(unittest.TestCase):
    def test_reverse_order_sorted_ascending(self):
        raw = [bybit(M5 + 180, 4, 4, 4, 4), bybit(M5 + 120, 3, 3, 3, 3), bybit(M5 + 60, 2, 2, 2, 2), bybit(M5, 1, 1, 1, 1)]
        out = bars.from_bybit(raw, 60, M5 + 10_000)
        self.assertEqual([r[0] for r in out], [M5, M5 + 60, M5 + 120, M5 + 180])
        self.assertEqual(out[0], (M5, 1.0, 1.0, 1.0, 1.0, 1.0))

    def test_unclosed_candle_dropped(self):
        raw = [bybit(M5 + 120, 3, 3, 3, 3), bybit(M5 + 60, 2, 2, 2, 2), bybit(M5, 1, 1, 1, 1)]
        # now внутри свечи M5+120: она не закрыта
        self.assertEqual([r[0] for r in bars.from_bybit(raw, 60, M5 + 150)], [M5, M5 + 60])
        # now ровно на закрытии свечи M5+60 (open+tf == now): закрыта
        self.assertEqual([r[0] for r in bars.from_bybit(raw, 60, M5 + 120)], [M5, M5 + 60])
        self.assertEqual([r[0] for r in bars.from_bybit(raw, 60, M5 + 119)], [M5])

    def test_garbage_and_duplicates(self):
        raw = [bybit(M5, 1, 2, 1, 2), ["x", 1, 1, 1, 1, 1], bybit(M5 + 60, 0, 1, 1, 1), bybit(M5 + 120, 1, 1, 2, 1),
               bybit(M5 + 180, "nan", 1, 1, 1), [str(M5 * 1000)], None, bybit(M5, 1, 3, 1, 3)]
        out = bars.from_bybit(raw, 60, M5 + 10_000)
        self.assertEqual(out, [(M5, 1.0, 3.0, 1.0, 3.0, 1.0)])      # остаётся последняя из дублей
        self.assertEqual(bars.from_bybit(None, 60, 10), [])

    def test_closed_before(self):
        rs = [row(M5 + 60 * i, 1, 1, 1, 1) for i in range(5)]
        self.assertEqual([r[0] for r in bars.closed_before(rs, M5 + 120, 60)], [M5, M5 + 60])
        self.assertEqual([r[0] for r in bars.closed_before(rs, M5 + 119, 60)], [M5])
        self.assertEqual(bars.closed_before(rs, M5, 60), [])


class TestAgg(unittest.TestCase):
    def test_1m_to_5m_utc_alignment(self):
        rs = [row(M5 + 60 * i, 10 + i, 11 + i, 9 + i, 10.5 + i, 2) for i in range(10)]
        out = bars.agg(rs, 5)
        self.assertEqual([r[0] for r in out], [M5, M5 + 300])
        self.assertEqual(out[0], (M5, 10.0, 15.0, 9.0, 14.5, 10.0))
        self.assertEqual(out[1], (M5 + 300, 15.0, 20.0, 14.0, 19.5, 10.0))

    def test_misaligned_start_drops_partial_buckets(self):
        # старт на 2-й минуте корзины и обрыв на 3-й минуте следующей: полная корзина одна
        rs = [row(M5 + 60 * i, 1, 1 + i, 1, 1) for i in range(1, 9)]
        out = bars.agg(rs, 5)
        self.assertEqual([r[0] for r in out], [])
        rs = [row(M5 + 60 * i, 1, 1 + i, 1, 1) for i in range(1, 11)]
        self.assertEqual([r[0] for r in bars.agg(rs, 5)], [M5 + 300])

    def test_hole_inside_bucket_gives_no_candle(self):
        rs = [row(M5 + 60 * i, 1, 1, 1, 1) for i in range(10) if i != 7]
        self.assertEqual([r[0] for r in bars.agg(rs, 5)], [M5])

    def test_unsorted_input_and_15m(self):
        base = M5 - M5 % 900
        rs = [row(base + 60 * i, 1, 2 + i, 1, 1 + i) for i in range(15)]
        random.Random(7).shuffle(rs)
        out = bars.agg(rs, 15)
        self.assertEqual(out, [(base, 1.0, 16.0, 1.0, 15.0, 15.0)])


class TestAtrAndRatios(unittest.TestCase):
    def flat(self, n, h=11, l=9, c=10):
        return [row(M5 + 300 * i, c, h, l, c) for i in range(n)]

    def test_atr_known_series(self):
        rs = self.flat(15)                                         # 14 TR по 2
        self.assertAlmostEqual(bars.atr(rs, 14), 2.0)
        rs = self.flat(15) + [row(M5 + 300 * 15, 10, 13, 9, 10)]   # TR=4: (2*13 + 4) / 14
        self.assertAlmostEqual(bars.atr(rs, 14), 30 / 14)

    def test_atr_uses_prev_close_gap(self):
        rs = self.flat(14) + [row(M5 + 300 * 14, 20, 21, 19, 20)]   # TR = max(2, |21-10|, |19-10|) = 11
        self.assertAlmostEqual(bars.atr(rs, 14), (2 * 13 + 11) / 14)

    def test_atr_insufficient(self):
        self.assertIsNone(bars.atr(self.flat(14), 14))
        self.assertIsNone(bars.atr([], 14))

    def test_ret_and_range(self):
        rs = [row(M5 + 300 * i, 100, 101 + i, 99, 100 + i) for i in range(5)]   # close 100..104
        self.assertAlmostEqual(bars.ret_pct(rs, 15, 5), (104 / 101 - 1) * 100)
        self.assertAlmostEqual(bars.ret_pct(rs, 20, 5), 4.0)
        self.assertIsNone(bars.ret_pct(rs, 25, 5))              # нужно 6 свечей
        self.assertIsNone(bars.ret_pct(rs, 7, 5))               # не кратно
        self.assertAlmostEqual(bars.range_pct(rs, 15, 5), (105 - 99) / 104 * 100)
        self.assertAlmostEqual(bars.range_pct(rs, 25, 5), (105 - 99) / 104 * 100)
        self.assertIsNone(bars.range_pct(rs, 30, 5))


class TestGaps(unittest.TestCase):
    def test_gaps(self):
        rs = [row(M5 + 60 * i, 1, 1, 1, 1) for i in (0, 1, 2, 3, 4)]
        self.assertEqual(bars.gaps(rs), [])
        # три пропавшие минуты - ещё не пропуск, четыре - пропуск
        self.assertEqual(bars.gaps([rs[0], row(M5 + 240, 1, 1, 1, 1)]), [])
        self.assertEqual(bars.gaps([rs[0], row(M5 + 300, 1, 1, 1, 1)]), [(M5, M5 + 300, 4)])
        self.assertEqual(bars.gaps([rs[0], row(M5 + 300, 1, 1, 1, 1)], max_gap_min=5), [])
        self.assertEqual(bars.gaps([]), [])


class TestPathStats(unittest.TestCase):
    def test_long_manual(self):
        # px0=100, 5 минут: хай 103 на 2-й минуте, лой 98 на 4-й, закрытие 101
        rs = [row(T0, 100, 101, 99.5, 100.5), row(T0 + 60, 100.5, 103, 100, 102), row(T0 + 120, 102, 102, 100.5, 101),
              row(T0 + 180, 101, 101, 98, 99), row(T0 + 240, 99, 101.5, 99, 101)]
        s = bars.path_stats(rs, T0, 100.0, "long", 5, 0.5)
        self.assertAlmostEqual(s["ret_pct"], 1.0)
        self.assertAlmostEqual(s["mfe_pct"], 3.0)
        self.assertAlmostEqual(s["mae_pct"], -2.0)
        self.assertAlmostEqual(s["mfe_atr"], 6.0)
        self.assertAlmostEqual(s["mae_atr"], -4.0)
        self.assertEqual((s["t_mfe_min"], s["t_mae_min"]), (2.0, 4.0))
        self.assertFalse(s["gap"])
        self.assertFalse(s["halt"])

    def test_short_mirrors(self):
        rs = [row(T0, 100, 101, 99.5, 100.5), row(T0 + 60, 100.5, 103, 100, 102), row(T0 + 120, 102, 102, 100.5, 101),
              row(T0 + 180, 101, 101, 98, 99), row(T0 + 240, 99, 101.5, 99, 101)]
        s = bars.path_stats(rs, T0, 100.0, "short", 5, None)
        self.assertAlmostEqual(s["ret_pct"], -1.0)
        self.assertAlmostEqual(s["mfe_pct"], 2.0)
        self.assertAlmostEqual(s["mae_pct"], -3.0)
        self.assertEqual((s["t_mfe_min"], s["t_mae_min"]), (4.0, 2.0))
        self.assertIsNone(s["mfe_atr"])

    def test_window_selection_and_no_move(self):
        # свечи до старта и после окна игнорируются; ход только против позиции: mfe=0, t_mfe=0
        rs = [row(T0 - 60, 100, 200, 1, 100)] + [row(T0 + 60 * i, 100, 100, 99, 99) for i in range(3)] + [row(T0 + 180, 99, 500, 1, 99)]
        s = bars.path_stats(rs, T0, 100.0, "long", 3, 1.0)
        self.assertEqual((s["mfe_pct"], s["t_mfe_min"]), (0.0, 0.0))
        self.assertAlmostEqual(s["mae_pct"], -1.0)
        self.assertEqual(s["t_mae_min"], 1.0)

    def test_gap_halt_and_missing(self):
        full = [row(T0 + 60 * i, 100, 101, 99, 100) for i in range(60)]
        self.assertFalse(bars.path_stats(full, T0, 100.0, "long", 60, 1.0)["gap"])
        holed = [r for r in full if not 20 <= (r[0] - T0) // 60 < 25]
        self.assertTrue(bars.path_stats(holed, T0, 100.0, "long", 60, 1.0)["gap"])
        short = full[:50]                                       # край окна не добран
        self.assertTrue(bars.path_stats(short, T0, 100.0, "long", 60, 1.0)["gap"])
        dead = [row(T0 + 60 * i, 100, 100, 100, 100, 0.0 if 10 <= i < 41 else 1.0) for i in range(60)]
        self.assertTrue(bars.path_stats(dead, T0, 100.0, "long", 60, 1.0)["halt"])
        dead = [row(T0 + 60 * i, 100, 100, 100, 100, 0.0 if 10 <= i < 40 else 1.0) for i in range(60)]
        self.assertFalse(bars.path_stats(dead, T0, 100.0, "long", 60, 1.0)["halt"])

    def test_empty_and_bad_side(self):
        s = bars.path_stats([], T0, 100.0, "long", 15, 1.0)
        self.assertTrue(s["gap"])
        self.assertIsNone(s["ret_pct"])
        with self.assertRaises(ValueError):
            bars.path_stats([], T0, 100.0, "flat", 15, 1.0)

    def test_property4_random_paths(self):
        """Свойство 4: mfe >= max(0, ret), mae <= min(0, ret), 0 <= t_mfe, t_mae <= h; mfe/mae согласованы с экстремумами свечей."""
        rng = random.Random(20261102)
        for _ in range(300):
            h = rng.choice([15, 60, 240])
            px0 = rng.uniform(0.5, 5000)
            p = px0
            rs = []
            for i in range(h):
                o = p
                c = o * (1 + rng.gauss(0, 0.004))
                hi = max(o, c) * (1 + abs(rng.gauss(0, 0.002)))
                lo = min(o, c) * (1 - abs(rng.gauss(0, 0.002)))
                rs.append(row(T0 + 60 * i, o, hi, lo, c, rng.choice([0.0, 1.0, 5.0])))
                p = c
            if rng.random() < 0.3:
                del rs[rng.randrange(len(rs))]                  # случайная дыра не ломает свойство
            rng.shuffle(rs)
            for side in ("long", "short"):
                s = bars.path_stats(rs, T0, px0, side, h, 0.3)
                self.assertGreaterEqual(s["mfe_pct"], max(0.0, s["ret_pct"]) - 1e-12)
                self.assertLessEqual(s["mae_pct"], min(0.0, s["ret_pct"]) + 1e-12)
                for k in ("t_mfe_min", "t_mae_min"):
                    self.assertTrue(0 <= s[k] <= h, (k, s[k], h))
                sign = 1 if side == "long" else -1
                ext = [sign * (r[2 if side == "long" else 3] / px0 - 1) * 100 for r in rs]
                self.assertAlmostEqual(s["mfe_pct"], max(0.0, max(ext), s["ret_pct"]), places=9)
                self.assertAlmostEqual(s["mfe_atr"], s["mfe_pct"] / 0.3)


if __name__ == "__main__":
    unittest.main()
