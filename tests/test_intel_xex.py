"""Кросс-биржевые данные intel (ccxt подменён фейком, живой сети нет): кэш по биржам, сбой одной биржи, сравнение с Bybit, facts и бриф."""
import unittest

import _bootstrap  # noqa: F401
from _intel_helpers import IntelCase, NOW
from tb.intel import fetch as F
from tb.intel import store
from tb.intel import xex as X


class FakeEx(object):
    def __init__(self, rows, fund=None, oi=None, boom=False, cs=None):
        self.rows, self.fund, self.oi, self.boom = rows, fund or {}, oi or {}, boom
        self.markets = {"%s/USDT:USDT" % s: {"contractSize": (cs or {}).get(s, 1.0)} for s in rows}
        self.has = {"fetchOpenInterest": bool(oi)}
        self.loads = 0

    def load_markets(self):
        self.loads += 1
        if self.boom:
            raise RuntimeError("down")

    def fetch_tickers(self, syms):
        return {m: self.rows[m.split("/")[0]] for m in syms}

    def fetch_funding_rates(self, syms):
        return {m: {"fundingRate": self.fund[m.split("/")[0]]} for m in syms if m.split("/")[0] in self.fund}

    def fetch_open_interest(self, m):
        return {"openInterestAmount": self.oi[m.split("/")[0]]}


def exchanges():
    return {"bybit": FakeEx({"BTC": {"last": 100.5, "quoteVolume": 200.0}}, {"BTC": 0.0003}, {"BTC": 10.0}),
            "binanceusdm": FakeEx({"BTC": {"last": 100.0, "quoteVolume": 600.0}}, {"BTC": 0.0001}, {"BTC": 20.0}),
            "okx": FakeEx({"BTC": {"last": 100.0, "baseVolume": 200.0}}, {"BTC": 0.0001}, {"BTC": 10.0}, cs={"BTC": 0.01}),
            "bitget": FakeEx({}, boom=True)}


class TestCollector(IntelCase):
    def mk(self, exs=None):
        self.exs = exs or exchanges()
        return X.Collector(self.root, {}, lambda: NOW, factory=lambda eid: self.exs[eid])

    def test_step_writes_cache_per_exchange_and_survives_one_down(self):
        c = self.mk()
        self.assertEqual(c.step(["BTC", "ETH"], NOW), ["xex_bybit", "xex_binanceusdm", "xex_okx"])
        bb = F.read_cache("xex_bybit", self.root)
        self.assertEqual(bb["data"]["symbols"]["BTC"], {"px": 100.5, "vol_usd": 200.0, "fund_pct": 0.03, "oi_usd": 1005.0})
        self.assertEqual(F.read_cache("xex_okx", self.root)["data"]["symbols"]["BTC"]["vol_usd"], 200.0)     # контракты x размер контракта x цена
        bg = F.read_cache("xex_bitget", self.root)
        self.assertEqual((bg["ok"], bg["fails"], bg["last_error"]), (False, 1, "RuntimeError"))
        self.assertEqual(c.step(["BTC"], NOW + 10), [])                                          # интервал не прошёл
        self.assertEqual(self.exs["bybit"].loads, 1)

    def test_cross_rows_against_bybit(self):
        c = self.mk()
        c.step(["BTC"], NOW)
        by = {s["exchange"]: (F.read_cache(s["id"], self.root).get("data") or {}).get("symbols") or {} for s in X.source_defs({})}
        r = X.cross_rows(by, ["BTC", "ETH"])
        self.assertEqual(set(r), {"BTC"})
        self.assertEqual(r["BTC"], {"n": 2, "xfund_pct": 0.01, "basis_bps": 50.0, "bybit_vol_pct": 20.0, "bybit_oi_pct": 25.1, "lead": "binanceusdm"})

    def test_facts_and_brief_carry_cross_fields(self):
        c = self.mk()
        c.step(["BTC"], NOW)
        srcs = X.source_defs({})
        f = store.build_facts(self.root, store.load_cfg(self.root), NOW + 5, srcs, ["BTC"])
        self.assertEqual(f["deriv"]["BTC"]["x"]["basis_bps"], 50.0)
        self.assertIn("xex_bitget", f["sources"])
        store.write_facts(self.root, f)
        from tb.brain import brief
        row = brief.intel_block(self.root, NOW + 5, ["BTC"])["deriv"]["BTC"]
        self.assertEqual((row["xfund_pct"], row["basis_bps"], row["bybit_vol_pct"], row["lead"]), (0.01, 50.0, 20, "binanceusdm"))

    def test_disabled_and_service_with_fake_transport_has_no_ccxt(self):
        self.assertEqual(X.source_defs({"xex": {"enabled": False}}), [])
        from tb.intel import service
        from _intel_helpers import FakeNet
        svc = service.Service(self.root, store.load_cfg(self.root), FakeNet(), lambda: NOW, emit=lambda *a, **k: None, inline=True, log=lambda m: None)
        self.assertIsNone(svc.xex)


if __name__ == "__main__":
    unittest.main()
