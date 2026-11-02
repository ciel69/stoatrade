"""Кросс-биржевые данные intel (ccxt подменён фейком, живой сети нет): кэш по биржам, сбой одной биржи, сравнение с Bybit, facts и бриф."""
import unittest

import _bootstrap  # noqa: F401
from _intel_helpers import IntelCase, NOW
from tb.intel import fetch as F
from tb.intel import store
from tb.intel import xex as X
from tb.marketdata import xvol as XV


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

    def fetch_tickers(self, syms=None):
        return {"%s/USDT:USDT" % s: r for s, r in self.rows.items() if syms is None or "%s/USDT:USDT" % s in syms}

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

    def test_xvol_feeds_heat_map(self):
        exs = exchanges()
        exs["binanceusdm"].rows["DOGE"] = {"last": 0.2, "quoteVolume": 3e6}
        c = self.mk(exs)
        c.step(["BTC"], NOW)
        import json
        import os
        from tb.marketdata import hotscan
        xv = json.load(open(os.path.join(self.root, "runtime", "xvol.json"), encoding="utf-8"))
        self.assertEqual(set(xv["ex"]), {"binanceusdm", "okx"})                       # Bybit берёт сам сканер, упавшая биржа не попадает
        h = hotscan.build_heat([{"sym": "DOGE", "turn_24h_musd": 1.0, "tradable": True, "price": 0.201}, {"sym": "PEPE", "turn_24h_musd": 2.0, "price": 1.0}], "mainnet", NOW + 5, xvol=xv)
        rows = {r["sym"]: r for r in h["rows"]}
        self.assertEqual((rows["DOGE"]["xturn_24h_musd"], rows["DOGE"]["bybit_share_pct"], rows["DOGE"]["xlead"], rows["DOGE"]["xn"]), (4.0, 25.0, "binanceusdm", 2))
        self.assertIsNone(rows["PEPE"]["bybit_share_pct"])                             # монеты нет на других биржах
        self.assertIsNone(hotscan.build_heat([{"sym": "DOGE", "turn_24h_musd": 1.0, "price": 0.2}], "mainnet", NOW + 2000, xvol=xv)["rows"][0]["xturn_24h_musd"])   # старые данные

    def test_match_multiplier_and_ticker_collision(self):
        vols = {"PEPE": [5e6, 0.00001], "XOM": [1e6, 2.5]}
        self.assertEqual(XV._match(vols, "1000PEPE", 0.0100), 5e6)          # Bybit 1000PEPE = OKX PEPE x 1000
        self.assertIsNone(XV._match(vols, "XOM", 110.0))                      # тот же тикер, другой токен (цена не сходится)

    def test_disabled_and_service_with_fake_transport_has_no_ccxt(self):
        self.assertEqual(X.source_defs({"xex": {"enabled": False}}), [])
        from tb.intel import service
        from _intel_helpers import FakeNet
        svc = service.Service(self.root, store.load_cfg(self.root), FakeNet(), lambda: NOW, emit=lambda *a, **k: None, inline=True, log=lambda m: None)
        self.assertIsNone(svc.xex)


if __name__ == "__main__":
    unittest.main()
