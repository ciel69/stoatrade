import builtins
import json
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

import _bootstrap  # noqa: F401
from tb.labels import klines


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def mk(clk, rps=5.0):
    return klines.Limiter(rps, clk.clock, clk.sleep)


def body(rows, code=0):
    return json.dumps({"retCode": code, "result": {"list": rows}})


def candle(ts_s):
    return [str(ts_s * 1000), "1", "2", "1", "1.5", "10", "15"]


class FakeSeries:
    """Фейковый Bybit: свечи с шагом step_s в [first, last], отдаёт новые первыми, учитывает start/end/limit."""

    def __init__(self, first, last, step_s=60):
        self.ts = list(range(first, last + 1, step_s))
        self.calls = []

    def __call__(self, url):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.calls.append(q)
        s, e, n = int(q["start"]), int(q["end"]), int(q["limit"])
        sel = [t for t in self.ts if s <= t * 1000 <= e]
        sel.sort(reverse=True)
        return 200, body([candle(t) for t in sel[:n]])


class FetchTest(unittest.TestCase):
    def test_pagination_and_order(self):
        clk = Clock()
        series = FakeSeries(0, 2499 * 60)
        rows = klines.fetch("ETHUSDT", "1", 0, 2499 * 60, mk(clk), series, now_s=10 ** 7)
        self.assertEqual(len(series.calls), 3)
        self.assertEqual([r[0] for r in rows], series.ts)
        self.assertTrue(all(c["category"] == "linear" and c["symbol"] == "ETHUSDT" and c["limit"] == "1000" for c in series.calls))

    def test_scanner_ticker_becomes_contract_symbol(self):
        """Сигналы хранят тикер сканера ('BEAT', '1000PEPE'); Bybit ждёт символ контракта: без этого каждый запрос шёл бы с неверным symbol."""
        for given, sent in (("BEAT", "BEATUSDT"), ("1000PEPE", "1000PEPEUSDT"), ("btc", "BTCUSDT"), ("ETHUSDT", "ETHUSDT"), ("XUSDC", "XUSDC")):
            self.assertEqual(klines.bybit_symbol(given), sent)
            series = FakeSeries(0, 600, 60)
            klines.fetch(given, "1", 0, 600, mk(Clock()), series, now_s=10 ** 6)
            self.assertTrue(series.calls and all(c["symbol"] == sent for c in series.calls), given)

    def test_unclosed_candle_dropped(self):
        clk = Clock()
        series = FakeSeries(0, 600, 60)
        rows = klines.fetch("X", "1", 0, 600, mk(clk), series, now_s=600 + 30)
        self.assertEqual(rows[-1][0], 540)
        self.assertEqual(len(rows), 10)

    def test_empty(self):
        clk = Clock()
        rows = klines.fetch("X", "1", 0, 600, mk(clk), lambda u: (200, body([])), now_s=10 ** 6)
        self.assertEqual(rows, [])

    def test_retry_pauses_then_success(self):
        clk = Clock()
        seq = [OSError("boom"), (500, ""), (200, body([], 10002)), (200, body([candle(0)]))]

        def http(url):
            x = seq.pop(0)
            if isinstance(x, Exception):
                raise x
            return x
        rows = klines.fetch("X", "1", 0, 60, mk(clk, rps=1000), http, now_s=10 ** 6)
        self.assertEqual(len(rows), 1)
        self.assertEqual(clk.sleeps, [2, 5, 15])

    def test_retry_exhausted(self):
        clk = Clock()
        n = []

        def http(url):
            n.append(1)
            return 503, ""
        with self.assertRaises(klines.FetchError):
            klines.fetch("X", "1", 0, 60, mk(clk, rps=1000), http, now_s=10 ** 6)
        self.assertEqual(len(n), 4)
        self.assertEqual(clk.sleeps, [2, 5, 15])

    def test_other_retcode_no_retry(self):
        clk = Clock()
        n = []

        def http(url):
            n.append(1)
            return 200, body([], 10001)
        with self.assertRaises(klines.FetchError):
            klines.fetch("X", "1", 0, 60, mk(clk), http, now_s=10 ** 6)
        self.assertEqual(len(n), 1)

    def test_rate_limited_pauses_everything(self):
        for code in (403, 429):
            clk = Clock()
            lim = mk(clk, rps=1000)
            with self.assertRaises(klines.RateLimited):
                klines.fetch("X", "1", 0, 60, lim, lambda u: (code, ""), now_s=10 ** 6)
            self.assertEqual(lim.rate_limited, 1)
            self.assertEqual(clk.sleeps, [])
            t_before = clk.t
            rows = klines.fetch("Y", "1", 0, 60, lim, lambda u: (200, body([candle(0)])), now_s=10 ** 6)
            self.assertEqual(len(rows), 1)
            self.assertGreaterEqual(sum(clk.sleeps), 60 - 1e-9)
            self.assertGreaterEqual(clk.t - t_before, 60 - 1e-9)


class LimiterTest(unittest.TestCase):
    def test_rps(self):
        clk = Clock()
        lim = mk(clk, rps=5)
        t0 = clk.t
        for _ in range(6):
            lim.wait()
        self.assertAlmostEqual(clk.t - t0, 5 * 0.2, places=6)
        self.assertEqual(lim.requests, 6)

    def test_no_wait_when_idle(self):
        clk = Clock()
        lim = mk(clk, rps=5)
        lim.wait()
        clk.t += 10
        lim.wait()
        self.assertEqual(clk.sleeps, [])


class BtcCacheTest(unittest.TestCase):
    def test_one_request_per_bucket(self):
        clk = Clock()
        t_bucket = 1_700_000_100
        series = FakeSeries(t_bucket - 400 * 300, t_bucket + 10 * 300, 300)
        cache = klines.BtcCache(mk(clk), series, now_s=t_bucket + 10 ** 5)
        a = cache.get(t_bucket + 10)
        n = len(series.calls)
        b = cache.get(t_bucket + 250)
        self.assertEqual(len(series.calls), n)
        self.assertEqual(a, b)
        self.assertTrue(a and all(r[0] + 300 <= t_bucket for r in a))
        self.assertEqual(series.calls[0]["symbol"], "BTCUSDT")
        self.assertEqual(series.calls[0]["interval"], "5")
        cache.get(t_bucket + 300)
        self.assertGreater(len(series.calls), n)


class NoFilesTest(unittest.TestCase):
    def test_no_file_access(self):
        clk = Clock()
        series = FakeSeries(0, 600)
        with mock.patch.object(builtins, "open", side_effect=AssertionError("open")), \
                mock.patch("os.listdir", side_effect=AssertionError("listdir")), \
                mock.patch("os.path.expanduser", side_effect=AssertionError("expanduser")), \
                mock.patch("pathlib.Path.home", side_effect=AssertionError("home")):
            klines.fetch("X", "1", 0, 600, mk(clk), series, now_s=10 ** 6)
            klines.BtcCache(mk(clk), series, now_s=10 ** 9).get(3000)

    def test_source_has_no_key_dir_reference(self):
        import inspect
        src = inspect.getsource(klines)
        for bad in (".config", "traider-bot", "api_key", "secret", "tb.venues", "tb.executor", "tb.gateway", "tb.llm", "tb.brain"):
            self.assertNotIn(bad, src)


if __name__ == "__main__":
    unittest.main()
