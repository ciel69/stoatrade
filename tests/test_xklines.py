"""Запасные свечи с других бирж через ccxt (marketdata/xklines) и их подхват структурой, когда REST Bybit молчит. Биржи фейковые, сети нет."""
import unittest

import _bootstrap  # noqa: F401
from tb.logic.candles import CandleBook
from tb.marketdata import structure as S
from tb.marketdata import xklines as XK

NOW = 1793181300.0


class FakeEx(object):
    def __init__(self, rows, cs=1.0, boom=False, markets=("BTC",)):
        self.rows, self.boom = rows, boom
        self.markets = {"%s/USDT:USDT" % s: {"contractSize": cs} for s in markets}
        self.calls = 0

    def load_markets(self):
        return self.markets

    def fetch_ohlcv(self, m, tf, since, limit):
        self.calls += 1
        if self.boom:
            raise RuntimeError("down")
        return self.rows[:limit]


def ohlcv(n=5, start_ms=1793180000000, step_ms=300000):
    return [[start_ms + i * step_ms, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0] for i in range(n)]


class XKlinesTest(unittest.TestCase):
    def setUp(self):
        XK._ex.clear()
        XK._loaded.clear()

    def test_parse_and_first_answering_exchange_wins(self):
        exs = {"okx": FakeEx(ohlcv(), cs=0.01, boom=True), "bitget": FakeEx(ohlcv(), cs=1.0), "binanceusdm": FakeEx(ohlcv())}
        rows, eid = XK.rows("BTC", 5, 3, factory=lambda e: exs[e], now=NOW)
        self.assertEqual(eid, "bitget")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], (1793180000000, 100.0, 101.0, 99.0, 100.5, 10.0, 1005.0))      # объём x контракт, оборот = объём x close
        self.assertEqual(exs["binanceusdm"].calls, 0)
        self.assertEqual(XK.parse([[1, 1, 2, 0.5, 1.5, 4]], cs=0.01)[0][5], 0.04)
        XK._ex.clear()                                                                           # объекты бирж кешируются между вызовами
        with self.assertRaises(RuntimeError):
            XK.rows("BTC", 5, 3, factory=lambda e: FakeEx(ohlcv(), boom=True), now=NOW)
        with self.assertRaises(ValueError):
            XK.rows("BTC", 7, 3, factory=lambda e: exs[e], now=NOW)
        XK._ex.clear()
        self.assertEqual(XK.rows("ETH", 5, 3, exchanges=("okx",), factory=lambda e: FakeEx(ohlcv(), markets=("ETH",)), now=NOW)[1], "okx")

    def test_structure_falls_back_to_ccxt_when_bybit_rest_fails(self):
        """Структура монеты не остаётся пустой при сбое REST Bybit: свечи берутся с другой биржи, источник помечен (UAI 2026-11-01)."""
        class BoomClient(object):
            def call(self, *a, **k):
                raise RuntimeError("403")
        import tempfile
        root = tempfile.mkdtemp(prefix="tbx_")
        st = S.Collector(BoomClient(), root=root, clock=lambda: NOW, sleep=lambda s: None)
        self.assertIsNone(st.xklines)                                                            # по умолчанию класс в сеть ccxt не ходит (включает сервис)
        fake = FakeEx(ohlcv(n=60, step_ms=300000))
        st.xklines = lambda sym, interval, limit: XK.rows(sym, interval, limit, exchanges=("bitget",), factory=lambda e: fake, now=NOW)
        rows = st._rows_any("BTC", "BTCUSDT", 5, 60)
        self.assertEqual(len(rows), 60)
        self.assertEqual((st.src["BTC"], st.xfallbacks), ("bitget", 1))
        book = CandleBook()
        book.seed(5, rows, now=NOW)
        self.assertTrue(book.last_closed(5, 1))
        st.xklines = None
        with self.assertRaises(RuntimeError):
            st._rows_any("BTC", "BTCUSDT", 5, 60)                                  # без запасного источника исходная ошибка Bybit наружу


if __name__ == "__main__":
    unittest.main()
