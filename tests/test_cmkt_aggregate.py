"""R1.7, задача 15: сводка tb.cmkt.aggregate (Requirements 1.3, 4.4–4.7, 5.8, 11.1, 11.2, 13.2; свойства 2, 3, 13, 19).

Синтетические runtime/cmkt/ex/<рынок>.json во временном корне (и документы настоящего сборщика на fakex); боевые runtime/ и state/
не трогаются; сети нет.
"""
import copy
import json
import os
import shutil
import statistics
import tempfile
import time
import unittest

import _bootstrap  # noqa: F401
from tb.cmkt import aggregate as AG
from tb.cmkt import collector as CO
from tb.cmkt import conf as C
from tb.cmkt import focus as FO
from tb.cmkt import store as ST
from tb.cmkt.ex import fakex as F
from tb.logic import cmkt as L

TS = 1793750400                         # кратно 5
PERP = ("binance_perp", "bybit_perp", "okx_perp")
SPOT = ("binance_spot", "bybit_spot")


def mkcfg(fakex=False, enabled=None):
    c = copy.deepcopy(C.defaults())
    if fakex:
        c["venues"]["fakex"] = {"enabled": True, "markets": ["perp", "spot"], "hosts": ["fakex.test", "api.fakex.test"]}
        c["limits"]["rest_weight_per_min"]["fakex"] = 600
    if enabled is not None:
        for v, ent in c["venues"].items():
            ent["enabled"] = v in enabled
    return c


def row(tier="d", st="ok", a=0.5, lag=100, mid=100.0, imb=(1000000, 800000), tape=(600000, 400000), oi=(5000.0, 4990.0, 4950.0, 4900.0),
        liq=(30000, 10000, 90000, 40000, 300.0, 100.0), w=1e9, fs=1000, q="USDT", kind="perp", walls=None, mx=None, fund=0.0001):
    bands = [[imb[0] * k, imb[1] * k] for k in (1, 2, 4, 8)]
    bk = [[100000] * 8, [90000] * 8] if tier == "d" else None
    tk = [mid, mid, None, None, fund if kind == "perp" else None, 8 if kind == "perp" else None, w]
    t = [[tape[0] / 5, tape[1] / 5, 1.0], [tape[0], tape[1], 1.0], [tape[0] * 3, tape[1] * 3, 1.0]]
    return [tier, st, a, lag, TS, mid, mx, 2.5, bands, bk, walls, [1, 500000, 0, 0], t, tk,
            list(oi) if kind == "perp" else None, list(liq) if kind == "perp" else None, w, fs, q]


def doc(mkt, T, syms, lt=None, st="ok", lag=100, off_ms=10, rtt=50.0, off_age=100, liq_partial=False):
    venue, kind = mkt.rsplit("_", 1)
    return {"schema": CO.SCHEMA, "mkt": mkt, "venue": venue, "kind": kind, "T": T, "t_wr": T + 0.05, "lt": T if lt is None else lt, "le": 5,
            "st": st, "err": None, "conns": [1, 1], "lag_ms": lag, "lag_p50": lag, "t_ex": T, "off_ms": off_ms, "rtt_ms": rtt,
            "off_age_s": off_age, "liq_partial": liq_partial, "resync": 0, "cols": list(CO.COLS), "syms": syms}


def focus_doc(deep, light=(), since=TS - 2000):
    return {"schema": FO.SCHEMA, "ts": TS, "deep": list(deep), "light": list(light), "why": {}, "since": dict((s, since) for s in list(deep) + list(light)),
            "warn": []}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tb_cmkt_agg_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.now = float(TS) + 0.3

    def agg(self, cfg=None, deep=("BTC",), light=()):
        cfg = cfg or mkcfg()
        FO.write(self.root, focus_doc(deep, light))
        a = AG.Aggregator(self.root, cfg, clock=lambda: self.now, sleep=lambda s: None)
        a.store.disk_free = lambda p: 10 ** 15
        self.addCleanup(a.close)
        return a

    def put(self, d):
        path = CO.ex_path(d["mkt"], self.root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f)
        st = os.stat(path)                                      # mtime мог не смениться за долю секунды: сдвиг вручную
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1000 + int(d["T"]) * 1000))

    def market(self, T, sym_rows, mkts=PERP + SPOT, **kw):
        for m in mkts:
            kind = m.rsplit("_", 1)[1]
            self.put(doc(m, T, dict((s, r(kind) if callable(r) else r) for s, r in sym_rows.items()), **kw))

    def tick(self, a, ts):
        self.now = ts + 0.3
        return a.tick(ts, self.now)


def f_of(snap, sym):
    return L.snap_sym(snap, sym)["f"]


class TestSummary(Base):
    def test_several_markets(self):
        a = self.agg()
        self.market(TS, {"BTC": lambda k: row(kind=k)})
        snap = self.tick(a, TS)
        self.assertEqual(snap["schema"], L.SNAP_SCHEMA)
        self.assertEqual(snap["n_ex"], {"perp": 3, "spot": 2})
        self.assertEqual(snap["ex"]["gate_perp"][0], "DOWN")                       # нет файла
        f = f_of(snap, "BTC")
        self.assertEqual((f["cm_n_perp"], f["cm_n_spot"]), (3, 2))
        self.assertAlmostEqual(f["cm_imb_025_pct"], round((1.0 - 0.8) / 1.8 * 100, 1), places=1)
        self.assertAlmostEqual(f["cm_cvd_5m_pct"], 20.0, places=1)
        self.assertAlmostEqual(f["cm_oi_chg_5m_pct"], round((5000 / 4950 - 1) * 100, 3), places=3)
        self.assertIsNotNone(f["cm_oi_liq_5m_pct"])
        self.assertAlmostEqual(f["cm_fund_w_pct"], 0.01, places=5)
        self.assertEqual(snap["skew_ms"], 100)                                     # (T − T_pub) × 1000 + lag
        s = L.snap_sym(json.loads(json.dumps(snap)), "BTC")
        self.assertEqual(s["tier"], "deep")
        self.assertEqual(sorted(k for k, v in s["mk"].items() if v["code"] is None), sorted(PERP + SPOT))
        self.assertEqual(s["mk"]["binance_perp"]["oi_usd"], 500000)
        disk = AG.read_snap(self.root)
        self.assertEqual(disk["ts"], TS)
        self.assertGreaterEqual(disk["t_wr"], TS)
        r = ST.read_row(self.root, "BTC", TS, a.cfg)
        self.assertEqual((r["ts"], r["tier"]), (TS, "deep"))
        self.assertEqual(r["f"]["cm_n_perp"], 3)
        self.assertEqual(r["f"], L.quantized(f))                                   # снимок и ряд — одни и те же значения
        self.assertEqual(sorted(x["mkt"] for x in r["by_ex"]), sorted(PERP + SPOT))
        bp = next(x for x in r["by_ex"] if x["mkt"] == "binance_perp")
        self.assertEqual(bp["cvd5"], 200000.0)
        self.assertAlmostEqual(bp["doi5"], 50.0)
        self.assertEqual((bp["liq5_long"], bp["liq5_short"]), (30000.0, 10000.0))
        self.assertGreaterEqual(r["wr_ts"], TS)
        self.assertEqual(r["skew_ms"], 100)

    def test_subset_of_venues(self):
        """Первая очередь без Bitget, Gate, Coinbase: выключенные — OFF, сводка по остальным."""
        a = self.agg(mkcfg(enabled=("binance", "bybit", "okx")))
        self.market(TS, {"BTC": lambda k: row(kind=k)})
        snap = self.tick(a, TS)
        for m in ("bitget_perp", "gate_spot", "coinbase_spot"):
            self.assertEqual(snap["ex"][m][0], "OFF")
        self.assertEqual(snap["n_ex"], {"perp": 3, "spot": 2})
        self.assertNotIn("gate_perp", L.snap_sym(snap, "BTC")["mk"])               # OFF в строке монеты не повторяется

    def test_exclusion_codes(self):
        a = self.agg()
        T = TS
        self.put(doc("binance_perp", T, {"BTC": row(lag=1500)}))                   # LAG
        self.put(doc("bybit_perp", T, {"BTC": row(a=20)}))                         # SILENT
        self.put(doc("okx_perp", T - 2, {"BTC": row()}))                           # STALE
        self.put(doc("bitget_perp", T, {"BTC": row(st="resync")}))                 # RESYNC
        self.put(doc("gate_perp", T, {"BTC": row(mid=105.0)}))                     # MISMATCH (медиана 100)
        self.put(doc("binance_spot", T, {"BTC": row(kind="spot", st="nl")}))       # NOT_LISTED
        self.put(doc("bybit_spot", T, {"BTC": row(kind="spot")}, st="down"))        # DOWN
        self.put(doc("okx_spot", T, {"BTC": row(kind="spot")}))
        self.put(doc("bitget_spot", T, {"BTC": row(kind="spot", mid=100.2)}))
        self.put(doc("gate_spot", T, {"ETH": row(kind="spot")}))                   # монеты нет в файле — STALE
        snap = self.tick(a, T)
        mk = L.snap_sym(snap, "BTC")["mk"]
        want = {"binance_perp": "LAG", "bybit_perp": "SILENT", "okx_perp": "STALE", "bitget_perp": "RESYNC", "gate_perp": "MISMATCH",
                "binance_spot": "NOT_LISTED", "bybit_spot": "DOWN", "okx_spot": None, "bitget_spot": None, "gate_spot": "STALE",
                "coinbase_spot": "DOWN"}
        self.assertEqual(dict((m, v["code"]) for m, v in mk.items()), want)
        self.assertEqual(mk["binance_perp"]["lag_ms"], 1500)                       # исключённый — с кодом и lag_ms
        self.assertEqual(snap["ex"]["okx_perp"][0], "STALE")
        self.assertEqual(snap["ex"]["bybit_spot"][0], "DOWN")
        f = f_of(snap, "BTC")
        self.assertEqual((f["cm_n_perp"], f["cm_n_spot"]), (0, 2))
        self.assertIsNone(f["cm_oi_chg_5m_pct"])                                   # нет включённых perp — None, а не 0

    def test_coinbase_premium_with_fx(self):
        a = self.agg()
        self.market(TS, {"BTC": lambda k: row(kind=k)})
        cb = doc("coinbase_spot", TS, {"BTC": row(kind="spot", q="USD", mid=round(100 * 0.999 * 1.001, 6))})
        cb["fx"] = {"usdt_usd": 0.999, "age_s": 2.0, "src": "ticker"}
        self.put(cb)
        snap = self.tick(a, TS)
        self.assertEqual(snap["usdt_usd"], 0.999)
        s = L.snap_sym(snap, "BTC")
        self.assertIsNone(s["mk"]["coinbase_spot"]["code"])                        # цена в USD, сверка по курсу — не MISMATCH
        self.assertAlmostEqual(s["f"]["cm_prem_cb_bps"], 10.0, places=1)
        cb2 = dict(cb, T=TS + 1, fx={"usdt_usd": 0.999, "age_s": 12.0})            # курс старше USDT_RATE_MAX_AGE_S (11 с) — премия None
        self.market(TS + 1, {"BTC": lambda k: row(kind=k)})
        self.put(cb2)
        self.assertIsNone(f_of(self.tick(a, TS + 1), "BTC")["cm_prem_cb_bps"])

    def test_lag_override_by_venue(self):
        cfg = mkcfg()
        cfg["sync"]["lag_max_ms_venue"] = {"binance": 2000}
        a = self.agg(cfg)
        self.put(doc("binance_perp", TS, {"BTC": row(lag=1500)}))
        self.put(doc("bybit_perp", TS, {"BTC": row(lag=1500)}))
        mk = L.snap_sym(self.tick(a, TS), "BTC")["mk"]
        self.assertEqual((mk["binance_perp"]["code"], mk["bybit_perp"]["code"]), (None, "LAG"))

    def test_excluded_market_does_not_change_features(self):
        """Свойство 2: данные рынка с кодом исключения не меняют признаков (кроме знаменателей покрытия — тот же вес)."""
        outs = []
        for k, imb in enumerate(((1000000, 800000), (1, 9000000))):
            self.setUp()
            a = self.agg()
            self.market(TS, {"BTC": lambda kd: row(kind=kd)})
            other = {} if k == 0 else {"imb": imb, "tape": (1, 9e9), "fund": 0.01, "liq": (9e9, 0, 9e9, 0, 9e9, 0), "mid": 130.0,
                                       "walls": [["b", 99.0, 9e9, 600]]}
            self.put(doc("gate_perp", TS, {"BTC": row(lag=5000, **other)}))
            outs.append(f_of(self.tick(a, TS), "BTC"))
        self.assertEqual(outs[0], outs[1])

    def test_mismatch_two_markets_with_prices_json(self):
        a = self.agg()
        self.put(doc("binance_perp", TS, {"BTC": row(mid=100.0)}))
        self.put(doc("bybit_perp", TS, {"BTC": row(mid=110.0)}))
        snap = self.tick(a, TS)
        self.assertIn("mm_unchecked", L.snap_sym(snap, "BTC")["flags"])            # без prices.json — пометка, оба включены
        os.makedirs(os.path.join(self.root, "runtime"), exist_ok=True)
        with open(os.path.join(self.root, "runtime", "prices.json"), "w") as f:
            json.dump({"ts_epoch": TS + 1, "env": "mainnet", "prices": {"BTC": 110.0}}, f)
        self.put(doc("binance_perp", TS + 1, {"BTC": row(mid=100.0)}))
        self.put(doc("bybit_perp", TS + 1, {"BTC": row(mid=110.0)}))
        s = L.snap_sym(self.tick(a, TS + 1), "BTC")
        self.assertEqual((s["mk"]["binance_perp"]["code"], s["mk"]["bybit_perp"]["code"]), ("MISMATCH", None))
        self.assertNotIn("mm_unchecked", s["flags"])


class TestDegrade(Base):
    def test_all_venues_off_snapshot_written(self):
        a = self.agg(mkcfg(enabled=()))
        a.snap_every = 2
        for ts in (TS, TS + 1, TS + 2):
            snap = self.tick(a, ts)
            self.assertEqual(AG.read_snap(self.root)["ts"], ts)                    # n_ex = 0: снимок на каждом такте
        self.assertEqual(snap["n_ex"], {"perp": 0, "spot": 0})
        self.assertTrue(all(v[0] == "OFF" for v in snap["ex"].values()))
        f = f_of(snap, "BTC")
        self.assertTrue(all(v is None for k, v in f.items() if k not in ("cm_n_perp", "cm_n_spot")), f)
        self.assertEqual(ST.read_row(self.root, "BTC", TS + 2, a.cfg)["tier"], "deep")   # строка ряда есть (все признаки None)

    def test_no_files_down_and_snap_every_2s(self):
        a = self.agg()
        self.market(TS, {"BTC": lambda k: row(kind=k)})
        self.tick(a, TS)
        self.market(TS + 1, {"BTC": lambda k: row(kind=k)})
        self.tick(a, TS + 1)
        self.assertEqual(AG.read_snap(self.root)["ts"], TS)                        # раз в 2 с, когда рынки включены
        self.market(TS + 2, {"BTC": lambda k: row(kind=k)})
        self.tick(a, TS + 2)
        self.assertEqual(AG.read_snap(self.root)["ts"], TS + 2)

    def test_read_retry_on_permission_error(self):
        a = self.agg()
        self.market(TS, {"BTC": lambda k: row(kind=k)}, mkts=("binance_perp",))
        real_open = open
        calls = {"n": 0}

        def flaky(path, *args, **kw):
            if str(path).endswith("binance_perp.json") and calls["n"] < 2:
                calls["n"] += 1
                raise PermissionError("locked")
            return real_open(path, *args, **kw)
        import builtins
        builtins.open = flaky
        try:
            snap = self.tick(a, TS)
        finally:
            builtins.open = real_open
        self.assertEqual(calls["n"], 2)
        self.assertIsNone(L.snap_sym(snap, "BTC")["mk"]["binance_perp"]["code"])

    def test_broken_file_is_stale_not_crash(self):
        a = self.agg()
        path = CO.ex_path("binance_perp", self.root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{broken")
        snap = self.tick(a, TS)
        self.assertEqual(snap["ex"]["binance_perp"][0], "DOWN")
        self.assertEqual(a.stats["read_err"], 1)


class TestTime(Base):
    def test_no_lookahead(self):
        """Свойство 1 для сводки: снимок ts берёт публикацию с T_pub ≤ ts, даже если на диске уже T + 1."""
        a = self.agg()
        self.market(TS, {"BTC": lambda k: row(kind=k, tape=(600000, 400000))})
        a.read_all()
        self.market(TS + 1, {"BTC": lambda k: row(kind=k, tape=(100000, 900000))})
        f = f_of(self.tick(a, TS), "BTC")
        self.assertAlmostEqual(f["cm_cvd_5m_pct"], 20.0, places=1)
        f = f_of(self.tick(a, TS + 1), "BTC")
        self.assertAlmostEqual(f["cm_cvd_5m_pct"], -80.0, places=1)

    def test_only_future_publication_is_stale(self):
        a = self.agg()
        self.market(TS + 1, {"BTC": lambda k: row(kind=k)})
        snap = self.tick(a, TS)
        self.assertEqual(snap["ex"]["binance_perp"][0], "STALE")
        self.assertEqual(snap["n_ex"], {"perp": 0, "spot": 0})

    def test_clock_back_and_monotonic_rows(self):
        """Свойство 3: ts снимков и рядов строго растут при шаге часов назад."""
        a = self.agg()
        for ts in (TS, TS + 1):
            self.market(ts, {"BTC": lambda k: row(kind=k)})
            self.assertIsNotNone(self.tick(a, ts))
        self.assertIsNone(self.tick(a, TS))                                        # часы назад
        self.assertIsNone(self.tick(a, TS + 1))
        self.assertEqual(a.clock_back, 2)
        self.market(TS + 2, {"BTC": lambda k: row(kind=k)})
        snap = self.tick(a, TS + 2)
        self.assertEqual(snap["clock_back"], 2)
        rows = ST.read_rows(self.root, "BTC", TS - 10, TS + 10, a.cfg)
        self.assertEqual([r["ts"] for r in rows], [TS, TS + 1, TS + 2])
        self.assertIn("clock_back", rows[-1]["flags"])
        self.assertNotIn("clock_back", rows[0]["flags"])

    def test_gap_s(self):
        a = self.agg(deep=("BTC",), light=("ETH",))
        self.market(TS, {"BTC": lambda k: row(kind=k), "ETH": lambda k: row(kind=k, tier="l")})
        self.tick(a, TS)
        self.market(TS + 3, {"BTC": lambda k: row(kind=k)}, lt=TS)
        snap = self.tick(a, TS + 3)
        self.assertEqual(snap["gap_s"], 2)
        r = ST.read_row(self.root, "BTC", TS + 3, a.cfg)
        self.assertEqual(r["gap_s"], 2)
        self.market(TS + 10, {"BTC": lambda k: row(kind=k), "ETH": lambda k: row(kind=k, tier="l")})
        self.tick(a, TS + 10)
        e = ST.read_row(self.root, "ETH", TS + 10, a.cfg)
        self.assertEqual((e["tier"], e["gap_s"]), ("light", 5))                    # пропущена строка light TS + 5


class TestTiers(Base):
    def test_deep_every_second_light_every_5s_from_cache(self):
        a = self.agg(deep=("BTC",), light=("ETH",))
        for ts in range(TS, TS + 7):
            syms = {"BTC": lambda k: row(kind=k)}
            if ts % 5 == 0:
                syms["ETH"] = lambda k: row(kind=k, tier="l", mid=2000.0)
            self.market(ts, syms, lt=ts - ts % 5)
            snap = self.tick(a, ts)
            e = L.snap_sym(snap, "ETH")
            self.assertEqual(e["tier"], "light")
            self.assertEqual(e["n_ex"], 5, ts)                                     # строки light — из публикации TS (кэш сводки)
            self.assertAlmostEqual(e["mid"], 2000.0)
        deep = ST.read_rows(self.root, "BTC", TS, TS + 10, a.cfg)
        light = ST.read_rows(self.root, "ETH", TS, TS + 10, a.cfg)
        self.assertEqual([r["ts"] for r in deep], list(range(TS, TS + 7)))
        self.assertEqual([(r["ts"], r["tier"]) for r in light], [(TS, "light"), (TS + 5, "light")])
        self.assertTrue(all(r["tier"] == "deep" for r in deep))
        self.assertEqual(light[1]["skew_ms"], 100)

    def test_light_stale_after_missed_publication(self):
        a = self.agg(deep=("BTC",), light=("ETH",))
        self.market(TS, {"BTC": lambda k: row(kind=k), "ETH": lambda k: row(kind=k, tier="l")})
        self.tick(a, TS)
        self.market(TS + 5, {"BTC": lambda k: row(kind=k)}, lt=TS)               # публикации light на TS + 5 нет
        e = L.snap_sym(self.tick(a, TS + 5), "ETH")
        self.assertEqual(e["n_ex"], 0)
        self.assertTrue(all(v["code"] == "STALE" for m, v in e["mk"].items() if m in PERP + SPOT))


class TestNewVenue(Base):
    def test_market_outside_markets_in_all_aggregates(self):
        """Новая биржа одной записью конфига: рынки fakex_* во всех агрегатах, код cm_lead_ex 11/12 без правки кода."""
        cfg = mkcfg(fakex=True)
        a = self.agg(cfg)
        self.assertEqual((a.codes["fakex_perp"], a.codes["fakex_spot"]), (11, 12))
        self.assertEqual(a.codes["coinbase_spot"], 10)
        self.put(doc("binance_perp", TS, {"BTC": row()}))
        self.put(doc("fakex_perp", TS, {"BTC": row(imb=(3000000, 0), oi=(1000.0, 1000.0, 900.0, 900.0))}))
        self.put(doc("fakex_spot", TS, {"BTC": row(kind="spot")}))
        snap = self.tick(a, TS)
        self.assertEqual(snap["mkts"][11], "fakex_perp")
        s = L.snap_sym(snap, "BTC")
        self.assertIsNone(s["mk"]["fakex_perp"]["code"])
        f = s["f"]
        self.assertEqual((f["cm_n_perp"], f["cm_n_spot"]), (2, 1))
        bid, ask = 1000000 + 3000000 + 1000000, 800000 + 0 + 800000
        self.assertAlmostEqual(f["cm_imb_025_pct"], round((bid - ask) / (bid + ask) * 100, 1), places=1)
        self.assertAlmostEqual(f["cm_oi_chg_5m_pct"], round(((5000 + 1000) / (4950 + 900) - 1) * 100, 3), places=3)
        r = ST.read_row(self.root, "BTC", TS, cfg)
        self.assertIn("fakex_perp", [x["mkt"] for x in r["by_ex"]])
        two = {"fakex_perp": {"kind": "perp", "code": None}, "binance_perp": {"kind": "perp", "code": None}}
        out = L.features(two, 1000, lead={"mkt": "fakex_perp", "lag_s": 2}, mkt_index=a.codes)
        self.assertEqual((out["f"]["cm_lead_ex"], out["f"]["cm_lead_lag_s"]), (11, 2))
        snap2 = dict(snap, syms={"BTC": list(snap["syms"]["BTC"])})
        vals = list(snap2["syms"]["BTC"][2])
        vals[L.CM_FEATURES.index("cm_lead_ex")] = 11
        snap2["syms"]["BTC"][2] = vals
        self.assertEqual(L.snap_sym(snap2, "BTC")["lead"], "fakex_perp")

    def test_lead_through_aggregator(self):
        """Лидерство по точкам mx: рынок, опережающий остальных на 1 с, получает свой код (в т.ч. вне MARKETS)."""
        import random
        rnd = random.Random(7)
        cfg = mkcfg(fakex=True)
        a = self.agg(cfg)
        px = [100.0]
        for _ in range(400):
            px.append(px[-1] * (1 + rnd.gauss(0, 0.001)))
        T0 = TS - 360
        for k in range(0, 340):
            ts = T0 + k
            lead_mx = [[ts, round(px[k + 1], 8)]]
            lag_mx = [[ts, round(px[k], 8)]]
            self.put(doc("fakex_perp", ts, {"BTC": row(mx=lead_mx)}))
            self.put(doc("binance_perp", ts, {"BTC": row(mx=lag_mx)}))
            self.put(doc("bybit_perp", ts, {"BTC": row(mx=[[ts, round(px[k] * 1.0001, 8)]])}))
            self.tick(a, ts)
        found = [x for x in a.lead.values() if x[1]]
        self.assertTrue(found)
        self.assertEqual(found[-1][1]["mkt"], "fakex_perp")
        last = ST.read_row(self.root, "BTC", a.lead["BTC"][0], cfg)
        self.assertEqual(last["f"]["cm_lead_ex"], 11)


class TestCollectorDocs(Base):
    def test_collector_fakex_documents(self):
        """Документы настоящего сборщика на fakex (perp и spot) сводятся в снимок."""
        cfg = mkcfg(fakex=True, enabled=("fakex",))
        now = float(TS) - 0.5
        docs = {}
        for kind in ("perp", "spot"):
            ad = F.FakexAdapter(cfg, rules={"perp": {"deep": "next", "light": "replace"}, "spot": {"deep": "next", "light": "replace"}})
            col = CO.Collector("fakex", kind, cfg, root=self.root, adapter=ad, clock=lambda: now)
            self.assertTrue(col.load_meta(now))
            col.apply_focus({"schema": FO.SCHEMA, "ts": now, "deep": ["BTC"], "light": [], "why": {"BTC": "core"}, "since": {"BTC": now - 2000}},
                            now)
            col._conn_age = lambda c, t: 0.5                                     # без сети: соединение «живо»
            col._market_lag = lambda: (100, 100)
            nat = "BTCUSDT"
            b = [["%.2f" % (99.99 - i * 0.05), "1000"] for i in range(20)]
            s = [["%.2f" % (100.01 + i * 0.05), "1000"] for i in range(20)]
            msgs = [{"ch": "book", "type": "snap", "s": nat, "u": 1, "b": b, "a": s, "E": int(now * 1000)},
                    {"ch": "trade", "s": nat, "p": "100", "q": "2000", "S": "buy", "id": 1, "E": int(now * 1000)},
                    {"ch": "ticker", "s": nat, "mark": 100.0, "index": 100.0, "fr": 0.0001, "oi": 5000000, "turn": 1e9, "E": int(now * 1000)}]
            for msg in msgs:
                col.on_events(ad.parse(kind, json.dumps(msg), now + 0.1), now + 0.1)
            docs[kind] = col.publish(TS, TS + 0.05)
        self.assertIsNotNone(CO.read_ex("fakex_perp", self.root))
        a = self.agg(cfg)
        snap = self.tick(a, TS)
        self.assertEqual(snap["n_ex"], {"perp": 1, "spot": 1})
        s = L.snap_sym(snap, "BTC")
        self.assertEqual((s["mk"]["fakex_perp"]["code"], s["mk"]["fakex_spot"]["code"]), (None, None))
        self.assertEqual((s["f"]["cm_n_perp"], s["f"]["cm_n_spot"]), (1, 1))
        self.assertAlmostEqual(s["mid"], 100.0)
        self.assertEqual(snap["ex"]["binance_perp"][0], "OFF")
        self.assertEqual(s["mk"]["fakex_perp"]["oi_usd"], 500000)                 # 5e6 контрактов × 0.001 × 100


class TestPerf(Base):
    def test_140_coins_under_100ms(self):
        deep = ["D%02d" % i for i in range(20)]
        light = ["L%03d" % i for i in range(120)]
        a = self.agg(deep=deep, light=light)
        mkts = PERP + SPOT + ("bitget_perp", "bitget_spot", "gate_perp", "gate_spot", "coinbase_spot")
        walls = [["b", 99.5, 900000, 60], ["a", 100.5, 700000, 45]]
        times = []
        for k in range(12):
            ts = TS + k
            for m in mkts:
                kind = m.rsplit("_", 1)[1]
                syms = dict((s, row(kind=kind, walls=walls, mx=[[ts, 100.0]])) for s in deep)
                if ts % 5 == 0:
                    syms.update((s, row(kind=kind, tier="l")) for s in light)
                self.put(doc(m, ts, syms, lt=ts - ts % 5))
            self.now = ts + 0.3
            t0 = time.perf_counter()
            snap = a.tick(ts, self.now)
            times.append((time.perf_counter() - t0) * 1000.0)
            self.assertEqual(len(snap["syms"]), 140)
        worst5 = times[10]                                                          # такт с публикацией light (ts % 5 = 0)
        med = statistics.median(times[1:])
        print("\n[cmkt aggregate] такт 140 монет × 11 рынков: медиана %.1f мс, с light %.1f мс, max %.1f мс" % (med, worst5, max(times[1:])))
        pf = float(os.environ.get("TB_PERF_FACTOR") or 1.0)                        # точный замер: ./tb test --slow -j 1
        self.assertLessEqual(med, 100.0 * pf)
        self.assertLessEqual(worst5, 150.0 * pf)


class TestProcess(Base):
    def test_run_and_heartbeat(self):
        cfg = mkcfg()
        a = AG.Aggregator(self.root, cfg, focus=FO.Focus(self.root, cfg))
        a.store.disk_free = lambda p: 10 ** 15
        a.run(max_seconds=1.6)
        from tb.platform import services as S
        age, hb = S.read_hb("cmkt", self.root)
        self.assertTrue(hb.get("stopped"))
        self.assertGreaterEqual(hb["ticks"], 1)
        self.assertIsNotNone(AG.read_snap(self.root))
        self.assertIsNotNone(FO.read(self.root))                                   # фокус пишет процесс сводки

    def test_bad_config_exit_2(self):
        os.makedirs(os.path.join(self.root, "config"), exist_ok=True)
        with open(os.path.join(self.root, "config", "cmkt.json"), "w") as f:
            f.write('{"schema": "x"}')
        self.assertEqual(AG.main(["run", "--root", self.root]), AG.EXIT_CONF)


if __name__ == "__main__":
    unittest.main()
