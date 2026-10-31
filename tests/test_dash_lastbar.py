"""Карточка плана: последняя закрытая свеча 5м свечного условия (dash.model2.last_bar) и запись её исполнителем в levels.json (executor.mdsvc.levels)."""
import unittest

import _bootstrap  # noqa: F401
from tb.dash import model2

NOW = 1_800_000_000.0
BAR0 = NOW - NOW % 300 - 300            # начало последней закрытой свечи 5м


def trig(**kw):
    t = {"kind": "candle_close", "eta_tf": "5м", "level": 0.005162, "dir": "below"}
    t.update(kw)
    return t


class LastBar(unittest.TestCase):
    def test_pierced_and_closed_back(self):
        """BEAM 2026-10-31: шорт ниже 0.005162, свеча прокололась до 0.005131 и закрылась выше (0.0051644): pierced."""
        lv = {"bar_5m": {"start": BAR0, "h": 0.0052052, "l": 0.005131, "c": 0.0051644}}
        self.assertEqual(model2.last_bar(trig(), lv, NOW), {"h": 0.0052052, "l": 0.005131, "c": 0.0051644, "pierced": True})
        lv["bar_5m"]["l"] = 0.00517
        self.assertFalse(model2.last_bar(trig(), lv, NOW)["pierced"])                     # не дотянулась до уровня
        lv = {"bar_5m": {"start": BAR0, "h": 0.0055, "l": 0.0052, "c": 0.00515}}
        self.assertTrue(model2.last_bar(trig(level=0.00543, dir="above"), lv, NOW)["pierced"])     # лонг: выше 0.00543 внутри, закрытие ниже

    def test_only_fresh_candle_conditions(self):
        lv = {"bar_5m": {"start": BAR0, "h": 0.0052, "l": 0.005131, "c": 0.0051644}}
        self.assertIsNone(model2.last_bar(trig(kind="price_beyond"), lv, NOW))
        self.assertIsNone(model2.last_bar(trig(eta_tf="1ч"), lv, NOW))
        self.assertIsNone(model2.last_bar(trig(), {}, NOW))
        old = {"bar_5m": dict(lv["bar_5m"], start=BAR0 - 1800)}
        self.assertIsNone(model2.last_bar(trig(), old, NOW))                                 # свеча получасовой давности: уже не про текущий момент


class PartialFills(unittest.TestCase):
    def test_parts_count_as_closed_today(self):
        """Частичное закрытие открытой сделки: результат за вычетом комиссии в «за день» (если сегодня) и отдельная сделка; плавающая без него."""
        from tb.dash2 import topics
        start = NOW - NOW % 86400
        trades = [{"parts": [{"ts": start + 60, "pnl": 1.847, "fee": 0.054}, {"ts": start - 3600, "pnl": 0.5, "fee": 0.0}]}, {"parts": []}, {}]
        today, total, n = topics._parts_pnl(trades, start)
        self.assertAlmostEqual(today, 1.793)
        self.assertAlmostEqual(total, 2.293)
        self.assertEqual(n, 1)


class ExecutorLevels(unittest.TestCase):
    def test_levels_carry_last_closed_5m(self):
        from tb.executor import mdsvc
        from tb.logic.candles import CandleBook

        class Ex(object):
            pass
        b = CandleBook()
        b.seed(5, [(int((BAR0 - 300 * i) * 1000), 1.0, 1.2, 0.9, 1.1, 10.0, 11.0) for i in range(30, -1, -1)], now=BAR0 + 301)
        ex = Ex()
        ex.books, ex.clock = {"SOL": b}, (lambda: BAR0 + 301)
        bar = mdsvc.levels(ex)["SOL"]["bar_5m"]
        self.assertEqual((bar["start"], bar["h"], bar["l"], bar["c"]), (int(BAR0), 1.2, 0.9, 1.1))


if __name__ == "__main__":
    unittest.main()
