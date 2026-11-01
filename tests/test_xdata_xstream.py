"""Потоковые кросс-биржевые данные по сокетам ccxt.pro (xdata/xstream): фейковая биржа, без сети. Ленты, стакан, тикер, ликвидации, документ xflow.json, live_for."""
import asyncio
import json
import os
import unittest

import _bootstrap  # noqa: F401
from _intel_helpers import IntelCase, NOW
from tb import paths
from tb.intel import xflow
from tb.xdata import xstream as XS


class FakeProEx(object):
    """Фейк биржи ccxt.pro: один ответ на каждый watch_*, дальше поток «ждёт» (asyncio.sleep)."""

    def __init__(self, has=None, trades=None, book=None, ticker=None, liq=None, cs=1.0, boom=None):
        self.has = dict({"watchTrades": True, "watchOrderBook": True, "watchTicker": True, "watchLiquidations": True}, **(has or {}))
        self.markets = {"BTC/USDT:USDT": {"contractSize": cs}, "ETH/USDT:USDT": {"contractSize": cs}}
        self.trades, self.book, self.ticker, self.liq, self.boom = trades or [], book, ticker, liq or [], boom or set()
        self.calls = {"trades": 0, "book": 0, "ticker": 0, "liq": 0}
        self.closed = False

    async def load_markets(self):
        return self.markets

    async def _one(self, kind, value):
        self.calls[kind] += 1
        if kind in self.boom:
            raise RuntimeError("down")
        if self.calls[kind] > 1:
            await asyncio.sleep(3600)
        return value

    async def watch_trades(self, m):
        return await self._one("trades", self.trades)

    async def watch_order_book(self, m, limit=None):
        return await self._one("book", self.book)

    async def watch_ticker(self, m):
        return await self._one("ticker", self.ticker)

    async def watch_liquidations(self, m):
        return await self._one("liq", self.liq)

    async def close(self):
        self.closed = True


def trades(now_ms, n=6, side="buy", px=100.0, amt=10.0):
    return [{"id": "t%d" % i, "timestamp": now_ms - (n - i) * 60000, "price": px, "amount": amt, "side": side} for i in range(n)]      # 6 сделок за 6 минут: окно 5 мин покрыто


def book(mid=100.0):
    bids = [[mid - 0.1 * (i + 1), 50.0 if i != 3 else 5000.0] for i in range(30)]
    asks = [[mid + 0.1 * (i + 1), 50.0] for i in range(30)]
    return {"bids": bids, "asks": asks}


class TestXStream(IntelCase):
    def run_once(self, exs, symbols=("BTC",), cfg=None, now=NOW, rounds=6):
        cfg = cfg or {"xflow": {"min_wall_usd": 1000.0, "wall_mult": 3.0}, "xstream": {"exchanges": list(exs), "liq_exchanges": list(exs), "publish_s": 0.1, "resync_s": 1000.0}}
        xs = XS.XStream(self.root, cfg, clock=lambda: now, factory=lambda eid: exs[eid], symbols_fn=lambda: list(symbols))

        async def go():
            loop = asyncio.get_event_loop()
            xs._resync(loop)
            for _ in range(rounds):
                await asyncio.sleep(0.01)
            for t in xs.tasks.values():
                t.cancel()
            await asyncio.sleep(0.01)
            return xs.document(now)
        loop = asyncio.new_event_loop()
        try:
            doc = loop.run_until_complete(go())
        finally:
            loop.close()
        return xs, doc

    def test_tapes_books_tickers_liquidations_into_document(self):
        now_ms = int(NOW * 1000)
        exs = {"binanceusdm": FakeProEx(trades=trades(now_ms, side="sell", amt=20.0), book=book(), ticker={"last": 100.0, "info": {"openInterest": "5000", "fundingRate": "0.0002"}},
                                        liq=[{"timestamp": now_ms - 1000, "side": "long", "quoteValue": 250000.0}]),
               "okx": FakeProEx(trades=trades(now_ms, side="buy"), book=book(), ticker={"last": 100.2, "info": {}}, liq=[], has={"watchLiquidations": "emulated"}, cs=0.01)}
        xs, doc = self.run_once(exs)
        self.assertEqual(doc["src"], "xstream")
        r = doc["syms"]["BTC"]
        self.assertLess(r["cvd"]["binanceusdm"]["w5"]["net"], 0)                     # продажи на Binance
        self.assertGreater(r["cvd"]["okx"]["w5"]["net"], 0)
        self.assertEqual(r["cvd"]["okx"]["w5"]["buy"], 50.0)                        # 5 сделок в окне 5 мин x 10 контрактов x 0.01 x 100
        self.assertIn("binanceusdm", r["ob"])
        self.assertTrue(any(w["side"] == "bid" for w in r["walls"]))                 # крупная корзина bid
        self.assertEqual(r["live"]["binanceusdm"]["oi_usd"], 500000.0)              # 5000 x 1 x 100
        self.assertEqual(r["live"]["binanceusdm"]["fund_pct"], 0.02)
        self.assertEqual(r["live"]["binanceusdm"]["liq5_long_usd"], 250000.0)
        self.assertEqual(r["slow"]["binanceusdm"]["liq_long_usd"], 250000.0)
        self.assertEqual(r["slow"]["binanceusdm"]["oi_usd"], 500000.0)
        self.assertEqual(r["live"]["okx"]["liq5_long_usd"], 0)                       # okx: канал есть (emulated), ликвидаций в фейке нет
        s = xflow.summary(doc, NOW, {})
        self.assertEqual(s["BTC"]["liq1h_long_x_usd"], 250000.0)                    # сводка для facts deriv[SYM]["x"] работает без изменений
        self.assertIn("cvd5_x_pct", s["BTC"])

    def test_one_exchange_down_and_unsupported_method_do_not_break_others(self):
        now_ms = int(NOW * 1000)
        exs = {"binanceusdm": FakeProEx(trades=trades(now_ms), book=book(), ticker={"last": 100.0, "info": {}}, boom={"trades", "book", "ticker", "liq"}),
               "bitget": FakeProEx(trades=trades(now_ms), book=book(), ticker={"last": 100.0, "info": {}}, has={"watchLiquidations": None})}
        xs, doc = self.run_once(exs)
        self.assertEqual(doc["ex"]["binanceusdm"]["ok"], False)
        self.assertEqual(doc["ex"]["binanceusdm"]["err"], "RuntimeError")
        self.assertEqual(doc["ex"]["binanceusdm"]["down"], ["book", "liq", "ticker", "trades"])
        self.assertIn("liq", doc["ex"]["bitget"]["nosup"])
        self.assertEqual(doc["ex"]["bitget"]["down"], [])
        # сбой одного канала не глушит остальные каналы той же биржи
        exs2 = {"okx": FakeProEx(trades=trades(now_ms), book=book(), ticker={"last": 100.0, "info": {}}, boom={"book"})}
        xs2, doc2 = self.run_once(exs2)
        self.assertEqual(doc2["ex"]["okx"]["ok"], True)
        self.assertEqual(doc2["ex"]["okx"]["down"], ["book"])
        self.assertIn("okx", doc2["syms"]["BTC"]["cvd"])
        self.assertIn("bitget", doc["syms"]["BTC"]["cvd"])
        self.assertEqual(exs["bitget"].calls["liq"], 0)                              # метода нет: не вызывался

    def test_live_for_sums_exchanges_and_reads_walls(self):
        now_ms = int(NOW * 1000)
        exs = {"binanceusdm": FakeProEx(trades=trades(now_ms), book=book(), ticker={"last": 100.0, "info": {"openInterest": "5000"}},
                                        liq=[{"timestamp": now_ms - 1000, "side": "long", "quoteValue": 250000.0}]),
               "okx": FakeProEx(trades=trades(now_ms), book=book(), ticker={"last": 100.0, "info": {}}, liq=[{"timestamp": now_ms - 1000, "side": "short", "quoteValue": 50000.0}])}
        xs, doc = self.run_once(exs)
        os.makedirs(os.path.dirname(xflow.xflow_path(self.root)), exist_ok=True)
        with open(xflow.xflow_path(self.root), "w", encoding="utf-8") as f:
            json.dump(doc, f)
        lv = XS.live_for(self.root, "BTC", NOW + 5)
        self.assertEqual((lv["liq5_long_usd"], lv["liq5_short_usd"], lv["liq1h_long_usd"], lv["n_ex"]), (250000.0, 50000.0, 250000.0, 2))
        self.assertTrue(lv["walls"])
        self.assertIsNone(XS.live_for(self.root, "BTC", NOW + 500))                  # устарел
        self.assertIsNone(XS.live_for(self.root, "ETH", NOW + 5))                    # монеты нет

    def test_symbol_set_puts_positions_first(self):
        snap = {"positions": [{"sym": "LSK"}], "plans": [{"sym": "AKE"}, {"sym": "LSK"}]}
        os.makedirs(os.path.dirname(paths.exec_snapshot_file(root=self.root)), exist_ok=True)
        with open(paths.exec_snapshot_file(root=self.root), "w", encoding="utf-8") as f:
            json.dump(snap, f)
        self.assertEqual(XS.symbol_set(self.root, ["BIO", "BTC", "ZRO"], 6), ["LSK", "AKE", "BTC", "ETH", "SOL", "BIO"])
        self.assertEqual(XS.position_symbols(self.root), ["LSK", "AKE"])

    def test_ticker_row_and_liq_ring(self):
        from collections import deque
        h = deque()
        r1 = XS.ticker_row({"last": 100.0, "info": {"openInterestValue": "1000000", "fundingRate": "-0.0006"}}, 1.0, NOW, h)
        self.assertEqual((r1["px"], r1["oi_usd"], r1["fund_pct"]), (100.0, 1000000.0, -0.06))
        self.assertNotIn("oi5_pct", r1)
        r2 = XS.ticker_row({"last": 101.0, "info": {"openInterestValue": "1100000"}}, 1.0, NOW + 301, h)
        self.assertEqual(r2["oi5_pct"], 10.0)
        self.assertIsNone(XS.ticker_row({"info": {}}, 1.0, NOW, h))
        ring = XS.LiqRing(3600.0)
        ring.add([{"timestamp": (NOW - 10) * 1000, "side": "sell", "contracts": 2, "price": 100.0}, {"timestamp": (NOW - 4000) * 1000, "side": "short", "quoteValue": 999.0},
                  {"timestamp": (NOW - 100) * 1000, "side": "buy", "quoteValue": 300.0}], 1.0, NOW)
        self.assertEqual(ring.window(NOW, 300.0), (200.0, 300.0))                   # старая запись (4000 с) выброшена из кольца


if __name__ == "__main__":
    unittest.main()
