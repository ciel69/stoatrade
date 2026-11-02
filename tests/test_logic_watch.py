"""Сторож позиции: STALLED (боковик с плюсом) и FLOW_TURN (ордерфлоу против), лимиты пробуждений."""
import doctest
import unittest

import _bootstrap  # noqa: F401
from tb.logic import watch as W

CFG = {"stalled_min": 15, "stalled_min_r": 0.5, "flow_turn_s": 30, "max_wakes_per_hour": 3}
AGAINST_LONG = {"cvd_1m_usd": -5000.0, "imb_pct": -20.0}
WITH_LONG = {"cvd_1m_usd": 5000.0, "imb_pct": 20.0}


class WatchTest(unittest.TestCase):
    def test_doctests(self):
        self.assertEqual(doctest.testmod(W).failed, 0)

    def test_stalled_only_with_profit(self):
        st = {}
        W.evaluate(st, 1, 0.3, None, 0.0, CFG)
        self.assertIsNone(W.evaluate(st, 1, 0.3, None, 2000.0, CFG))        # прибыли мало: будить нечего

    def test_new_peak_resets_timer(self):
        st = {}
        W.evaluate(st, 1, 0.8, None, 0.0, CFG)
        W.evaluate(st, 1, 1.2, None, 800.0, CFG)
        self.assertIsNone(W.evaluate(st, 1, 1.1, None, 1500.0, CFG))        # 700 с после нового пика
        self.assertEqual(W.evaluate(st, 1, 1.1, None, 800.0 + 901.0, CFG), "STALLED")

    def test_flow_turn_needs_duration_and_both_signals(self):
        st = {}
        self.assertIsNone(W.evaluate(st, 1, 1.0, AGAINST_LONG, 0.0, CFG))
        self.assertIsNone(W.evaluate(st, 1, 1.0, AGAINST_LONG, 20.0, CFG))
        self.assertEqual(W.evaluate(st, 1, 1.0, AGAINST_LONG, 31.0, CFG), "FLOW_TURN")
        st = {}
        W.evaluate(st, 1, 1.0, AGAINST_LONG, 0.0, CFG)
        W.evaluate(st, 1, 1.0, WITH_LONG, 20.0, CFG)                         # поток вернулся: отсчёт сброшен
        self.assertIsNone(W.evaluate(st, 1, 1.0, AGAINST_LONG, 40.0, CFG))
        self.assertIsNone(W.evaluate(st, 1, 1.0, {"cvd_1m_usd": -1.0, "imb_pct": 5.0}, 100.0, CFG))   # перекос стакана за позицию

    def test_short_side(self):
        st = {}
        W.evaluate(st, -1, 1.0, WITH_LONG, 0.0, CFG)                         # для short рост покупок = против
        self.assertEqual(W.evaluate(st, -1, 1.0, WITH_LONG, 31.0, CFG), "FLOW_TURN")

    def test_market_codes_wake_without_profit_gate(self):
        """Надзор за монетой в позиции (указание владельца 2026-11-01): новости, резкий ход, пробой уровня, разворот толпы, стена, оборот, смена режима будят мозг
        независимо от прибыли; порядок важности NEWS > FAST_MOVE > STRUCT_BREAK > CROWD_TURN > BOOK_WALL > VOL_SPIKE > REGIME_CHANGE; каждая новость один раз."""
        cfg = dict(CFG, max_wakes_per_hour=12)
        st = {}
        base = {"last": 100.0, "a5_pct": 0.5, "reg": "range", "sup": [99.0], "res": [101.5], "tx": 1.0}
        self.assertIsNone(W.evaluate(st, 1, -0.2, None, 0.0, cfg, base))                                    # спокойно: ничего
        self.assertEqual(W.evaluate(st, 1, -0.2, None, 10.0, cfg, dict(base, news=[{"id": "n1", "age_s": 30}])), "NEWS")
        self.assertIsNone(W.evaluate(st, 1, -0.2, None, 11.0, cfg, dict(base, news=[{"id": "n1", "age_s": 31}])))      # та же новость не повторяется
        self.assertEqual(W.evaluate(st, 1, -0.2, None, 12.0, cfg, dict(base, news=[{"id": "n2", "age_s": 5}])), "NEWS")
        self.assertIsNone(W.evaluate(st, 1, -0.2, None, 13.0, cfg, dict(base, news=[{"id": "n3", "age_s": 600}])))     # старая новость не будит
        W.evaluate(st, 1, -0.2, None, 100.0, cfg, dict(base, last=100.0))
        self.assertEqual(W.evaluate(st, 1, -0.2, None, 160.0, cfg, dict(base, last=98.9)), "FAST_MOVE")             # -1.1% за минуту при ATR 0.5% = 2.2 ATR
        self.assertIsNone(W.evaluate(st, 1, -0.2, None, 170.0, cfg, dict(base, last=98.85)))                         # пауза 5 мин на код
        # пробой поддержки против лонга: 30 с подряд ниже уровня с буфером max(0.1%, 0.5 ATR(5м)) = 0.25% (уровень 98.7525)
        st2 = {}
        W.evaluate(st2, 1, 0.1, None, 0.0, cfg, dict(base, last=98.7))
        self.assertIsNone(W.evaluate(st2, 1, 0.1, None, 20.0, cfg, dict(base, last=98.7)))
        self.assertEqual(W.evaluate(st2, 1, 0.1, None, 31.0, cfg, dict(base, last=98.7)), "STRUCT_BREAK")
        st3 = {}
        W.evaluate(st3, -1, 0.1, None, 0.0, cfg, dict(base, last=101.8))
        self.assertEqual(W.evaluate(st3, -1, 0.1, None, 31.0, cfg, dict(base, last=101.8)), "STRUCT_BREAK")          # шорт: выше сопротивления (101.754)
        # шум внутри 0.5 ATR за уровнем не будит (разбор POWR 2026-11-02); без ATR остаётся буфер 0.1%
        st2n = {}
        W.evaluate(st2n, 1, 0.1, None, 0.0, cfg, dict(base, last=98.85))
        self.assertIsNone(W.evaluate(st2n, 1, 0.1, None, 61.0, cfg, dict(base, last=98.85)))
        st2a = {}
        W.evaluate(st2a, 1, 0.1, None, 0.0, cfg, dict(base, last=98.85, a5_pct=None))
        self.assertEqual(W.evaluate(st2a, 1, 0.1, None, 31.0, cfg, dict(base, last=98.85, a5_pct=None)), "STRUCT_BREAK")
        # толпа развернулась против лонга (паника) один раз на смену состояния
        st4 = {}
        self.assertEqual(W.evaluate(st4, 1, 0.1, None, 0.0, cfg, dict(base, crowd={"mood": "panic", "bias": "short"})), "CROWD_TURN")
        self.assertIsNone(W.evaluate(st4, 1, 0.1, None, 1.0, cfg, dict(base, crowd={"mood": "panic", "bias": "short"})))
        self.assertIsNone(W.evaluate(st4, -1, 0.1, None, 2.0, cfg, dict(base, crowd={"mood": "panic", "bias": "short"})))   # для шорта паника не против
        # стена ask ближе 1.5 ATR против лонга; стена bid лонгу не мешает
        st5 = {}
        self.assertEqual(W.evaluate(st5, 1, 0.1, None, 0.0, cfg, dict(base, walls=[{"side": "ask", "usd": 500000, "dist_pct": 0.6}])), "BOOK_WALL")
        self.assertIsNone(W.evaluate({}, 1, 0.1, None, 0.0, cfg, dict(base, walls=[{"side": "bid", "usd": 500000, "dist_pct": 0.6}])))
        self.assertIsNone(W.evaluate({}, 1, 0.1, None, 0.0, cfg, dict(base, walls=[{"side": "ask", "usd": 500000, "dist_pct": 2.0}])))
        # оборот: 3x нормы и в полтора раза выше, чем при входе; смена режима относительно входа
        st6 = {}
        W.evaluate(st6, 1, 0.1, None, 0.0, cfg, dict(base, tx=1.0))
        self.assertEqual(W.evaluate(st6, 1, 0.1, None, 1.0, cfg, dict(base, tx=3.5)), "VOL_SPIKE")
        st7 = {}
        W.evaluate(st7, 1, 0.1, None, 0.0, cfg, dict(base, reg="range"))
        self.assertEqual(W.evaluate(st7, 1, 0.1, None, 1.0, cfg, dict(base, reg="trend_down")), "REGIME_CHANGE")
        # приоритет: новость важнее пробоя
        st8 = {}
        W.evaluate(st8, 1, 0.1, None, 0.0, cfg, dict(base, last=98.7))
        self.assertEqual(W.evaluate(st8, 1, 0.1, None, 31.0, cfg, dict(base, last=98.7, news=[{"id": "n9", "age_s": 1}])), "NEWS")
        self.assertEqual(W.evaluate(st8, 1, 0.1, None, 32.0, cfg, dict(base, last=98.7)), "STRUCT_BREAK")           # следующий тик отдаёт пробой
        # прежние коды без ctx работают как раньше (совместимость)
        st9 = {}
        W.evaluate(st9, 1, 0.8, None, 0.0, CFG)
        self.assertEqual(W.evaluate(st9, 1, 0.8, None, 15 * 60 + 1, CFG), "STALLED")

    def test_cooldown_and_hourly_cap(self):
        st = {}
        W.evaluate(st, 1, 1.0, AGAINST_LONG, 0.0, CFG)
        self.assertEqual(W.evaluate(st, 1, 1.0, AGAINST_LONG, 31.0, CFG), "FLOW_TURN")
        self.assertIsNone(W.evaluate(st, 1, 1.0, AGAINST_LONG, 200.0, CFG))  # пауза между повторами 10 мин
        self.assertEqual(W.evaluate(st, 1, 1.0, AGAINST_LONG, 700.0, CFG), "FLOW_TURN")
        self.assertEqual(W.evaluate(st, 1, 1.0, AGAINST_LONG, 1400.0, CFG), "FLOW_TURN")
        self.assertIsNone(W.evaluate(st, 1, 1.0, AGAINST_LONG, 2100.0, CFG))  # четвёртое за час: лимит 3


if __name__ == "__main__":
    unittest.main()
