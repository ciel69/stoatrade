"""R1.7, задача 22: совместимый runtime/intel/xflow.json от сводного рынка (Requirements 16.5, 17.2–17.7; свойство 20).

Одна фикстура рынка (стаканы, сделки, тикеры, ликвидации OKX, Bitget, Binance USDT-M) подаётся обоим производителям:
`XStream.document` (фейковые биржи ccxt.pro, настоящий код потока) и писателю `cmkt` (документы рынков `ex/<рынок>.json` в формате
сборщика -> `Aggregator.tick` -> `tb.cmkt.compat.Writer`). Сравниваются: строки монет документа, `xflow.summary` (поля `deriv.x`),
`flowdoc.xlive_for`, данные сторожа позиции (`Executor._watch_ctx`: стены, ликвидации, толпа) и его коды (`BOOK_WALL`, сторона
ликвидаций). Плюс: охват (Bybit и спот не входят, ликвидации только Binance, обрезка стакана до глубины xstream), один писатель
(сервис intel не запускает XStream при флаге), поле `cx` сборщика, константы охвата равны оригиналам. Сети нет, корни временные.
"""
import asyncio
import copy
import json
import os
import shutil
import tempfile
import types
import unittest
from collections import deque
from unittest import mock

import _bootstrap  # noqa: F401
from _intel_helpers import IntelCase
from tb import paths
from tb.cmkt import aggregate as AG
from tb.cmkt import collector as CO
from tb.cmkt import compat as CX
from tb.cmkt import conf as C
from tb.cmkt import focus as FO
from tb.core import flowdoc
from tb.executor.core import Executor
from tb.intel import xflow
from tb.logic import cmkt as L
from tb.logic import watch as W
from tb.xdata import xstream as XS

TS = 1793750400
SYMS = ("BTC", "ETH", "SOL")                    # SOL: данных нет ни у кого; ETH: только OKX
EX = ("okx", "bitget", "binanceusdm")
MKT = {"okx": "okx_perp", "bitget": "bitget_perp", "binanceusdm": "binance_perp"}
CS = {"okx": 0.5, "bitget": 1.0, "binanceusdm": 1.0}   # contract_size: степени двойки — произведения в обоих путях точные
MID = {"okx": 100.0, "bitget": 100.02, "binanceusdm": 99.98}
FUND = {"okx": -0.0002, "bitget": 0.00005, "binanceusdm": 0.0001}


# ---------------------------------------------------------------- фикстура рынка (в контрактах, как у биржи)
def levels(eid, n=60, step=0.05):
    mid = MID[eid]
    bids = [[round(mid - step * (i + 1), 4), 40.0] for i in range(n)]
    asks = [[round(mid + step * (i + 1), 4), 40.0] for i in range(n)]
    if eid == "okx":
        bids[11][1] = 6000.0                     # стена bid ~0.6 %: 6000 × 0.5 × 99.4 ≈ 298 тыс. USD
    if eid == "bitget":
        asks[5][1] = 3000.0                      # стена ask ~0.3 %
    if eid == "binanceusdm":
        asks[30][1] = 90000.0                    # 1.55 % — за глубиной 20 уровней: стены быть не должно ни у кого
    return {"bids": bids, "asks": asks}


def fx_trades(eid):
    """Сделки (с, цена, контракты, сторона): каждые 10 с с TS−955 по TS−5 — границы окон 5 и 15 мин сделки не задевают."""
    out = []
    for k in range(96):
        t = TS - 5 - 10 * k
        side = "buy" if (k % 3 != 0) == (eid != "binanceusdm") else "sell"
        out.append((t, 100.0, 4.0 if eid == "okx" else 2.0, side))
    return out


OI = {"okx": (5000.0, 4900.0), "bitget": (8000.0, 8100.0), "binanceusdm": (12000.0, 11000.0)}   # контракты: сейчас и 300 с назад
LIQS = {"binanceusdm": [(TS - 60, "long", 250000.0), (TS - 100, "short", 10000.0), (TS - 1000, "long", 50000.0)],
        "okx": [(TS - 30, "short", 900000.0)]}            # у OKX ликвидации в документ не идут (у xstream канал выключен)
LISTED = {"BTC": EX, "ETH": ("okx",), "SOL": ()}


# ---------------------------------------------------------------- производитель 1: XStream на фейковых биржах ccxt.pro
class ProEx(object):
    def __init__(self, eid):
        self.eid = eid
        self.has = {"watchTrades": True, "watchOrderBook": True, "watchTicker": True, "watchLiquidations": True}
        self.markets = dict(("%s/USDT:USDT" % s, {"contractSize": CS[eid]}) for s in SYMS if eid in LISTED[s])
        self.n = {}
        self.limits = []

    async def load_markets(self):
        return self.markets

    async def _once(self, key, value):
        self.n[key] = self.n.get(key, 0) + 1
        if self.n[key] > 1:
            await asyncio.sleep(3600)
        return value

    async def watch_trades(self, m):
        return await self._once(("t", m), [{"id": "%s%d" % (self.eid, i), "timestamp": t * 1000, "price": p, "amount": a, "side": s}
                                           for i, (t, p, a, s) in enumerate(reversed(fx_trades(self.eid)))])

    async def watch_order_book(self, m, limit=None):
        self.limits.append(limit)
        ob = levels(self.eid)
        return await self._once(("b", m), {"bids": ob["bids"][:limit], "asks": ob["asks"][:limit]})     # выдача ccxt.pro глубины limit

    async def watch_ticker(self, m):
        return await self._once(("k", m), {"last": 100.0, "info": {"openInterest": str(OI[self.eid][0]), "fundingRate": str(FUND[self.eid])}})

    async def watch_liquidations(self, m):
        return await self._once(("l", m), [{"timestamp": t * 1000, "side": s, "quoteValue": u} for t, s, u in LIQS.get(self.eid, ())])

    async def close(self):
        pass


def xstream_doc(root):
    exs = dict((e, ProEx(e)) for e in EX)
    xs = XS.XStream(root, {}, clock=lambda: float(TS), factory=lambda eid: exs[eid], symbols_fn=lambda: list(SYMS))
    for e in EX:                                            # история OI: точка 300 с назад (ticker_row берёт её как базу oi5_pct)
        for s in SYMS:
            xs.oih[(e, s)] = deque([(TS - 300.0, OI[e][1] * CS[e] * 100.0)])

    async def go():
        xs._resync(asyncio.get_event_loop())
        for _ in range(8):
            await asyncio.sleep(0.01)
        for t in xs.tasks.values():
            t.cancel()
        await asyncio.sleep(0.01)
        return xs.document(float(TS))
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(go()), exs
    finally:
        loop.close()


# ---------------------------------------------------------------- производитель 2: документы рынков cmkt -> сводка -> Writer
def ex_row(eid, sym):
    if eid not in LISTED[sym]:
        return ["d", "nl"] + [None] * (len(CO.COLS) - 3) + ["USDT"]
    cs = CS[eid]
    tr = fx_trades(eid)
    tape = []
    for _, w in L.WINDOWS:
        b = sum(a * cs * p for t, p, a, s in tr if TS - w < t <= TS and s == "buy")
        s_ = sum(a * cs * p for t, p, a, s in tr if TS - w < t <= TS and s == "sell")
        tape.append([b, s_, 1.0])
    ob = levels(eid)
    bids = [(p, a * cs) for p, a in ob["bids"]]
    asks = [(p, a * cs) for p, a in ob["asks"]]
    mid = L.book_mid(bids, asks)
    tk = [100.0, 100.0, None, None, FUND[eid], 8, 1e9]
    oi = [OI[eid][0] * cs, OI[eid][0] * cs, OI[eid][1] * cs, OI[eid][1] * cs]
    lq = LIQS.get(eid)
    liq = None
    if lq is not None:
        l5 = [sum(u for t, s, u in lq if TS - t <= 300 and s == side) for side in ("long", "short")]
        l1 = [sum(u for t, s, u in lq if TS - t <= 3600 and s == side) for side in ("long", "short")]
        liq = [l5[0], l5[1], l1[0], l1[1], 0.0, 0.0]
    return ["d", "ok", 0.5, 100, TS, mid, None, 3.0, [None] * 4, None, None, [0, 0, 0, 0], tape, tk, oi, liq, 1e9, 3000, "USDT"]


def ex_doc(mkt, syms, cx=None):
    venue, kind = mkt.rsplit("_", 1)
    d = {"schema": CO.SCHEMA, "mkt": mkt, "venue": venue, "kind": kind, "T": TS, "t_wr": TS + 0.05, "lt": TS, "le": 5, "st": "ok", "err": None,
         "conns": [1, 1], "lag_ms": 100, "lag_p50": 100, "t_ex": TS, "off_ms": 10, "rtt_ms": 50.0, "off_age_s": 100, "liq_partial": False,
         "resync": 0, "cols": list(CO.COLS), "syms": syms}
    if cx is not None:
        d["cx"] = cx
    return d


def put(root, d):
    path = CO.ex_path(d["mkt"], root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f)


def cx_of(eid, sym):
    """Поле cx сборщика: стакан в монетах (контракты × contract_size), обрезка до глубины xstream (как Collector.compat_books)."""
    depth = dict((e, d) for _, e, d in L.COMPAT_MARKETS)[eid]
    ob = levels(eid)
    return L.compat_book_row([(p, a * CS[eid]) for p, a in ob["bids"]], [(p, a * CS[eid]) for p, a in ob["asks"]], depth)


def cmkt_cfg(on=True):
    c = copy.deepcopy(C.defaults())
    c["compat_xflow"] = on
    return c


def focus_doc(compat=SYMS):
    return {"schema": FO.SCHEMA, "ts": TS, "deep": list(SYMS), "light": [], "why": {}, "since": dict((s, TS - 5000) for s in SYMS),
            "warn": [], "compat": list(compat)}


def cmkt_doc(root, bybit_wall=True):
    for e in EX:
        put(root, ex_doc(MKT[e], dict((s, ex_row(e, s)) for s in SYMS), dict((s, cx_of(e, s)) for s in SYMS if e in LISTED[s])))
    if bybit_wall:                                       # Bybit и спот в совместимый документ не входят, даже со своим cx и ликвидациями
        put(root, ex_doc("bybit_perp", {"BTC": ex_row("binanceusdm", "BTC")}, {"BTC": cx_of("okx", "BTC")}))
        put(root, ex_doc("binance_spot", {"BTC": ex_row("bitget", "BTC")}, {"BTC": cx_of("bitget", "BTC")}))
    FO.write(root, focus_doc())
    a = AG.Aggregator(root, cmkt_cfg(), clock=lambda: TS + 0.3, sleep=lambda s: None)
    a.store.disk_free = lambda p: 10 ** 15
    try:
        snap = a.tick(TS, TS + 0.3)
        assert snap is not None
        assert isinstance(a.compat, CX.Writer) and a.stats["compat_err"] == 0, a.stats
    finally:
        a.close()
    with open(CX.xflow_path(root), encoding="utf-8") as f:
        return json.load(f)


def write_doc(root, doc):
    os.makedirs(os.path.dirname(xflow.xflow_path(root)), exist_ok=True)
    with open(xflow.xflow_path(root), "w", encoding="utf-8") as f:
        json.dump(doc, f)


def watch_ctx(root, sym, now):
    fake = types.SimpleNamespace(root=root)
    return Executor._watch_ctx(fake, sym, types.SimpleNamespace(last=100.0), now, None)


def liq_side(xl):
    lo, sh = float((xl or {}).get("liq5_long_usd") or 0), float((xl or {}).get("liq5_short_usd") or 0)
    if max(lo, sh) >= 100000.0 and max(lo, sh) >= 3 * max(min(lo, sh), 1.0):
        return "long" if lo > sh else "short"
    return None


def strip(doc):
    """Строки монет без полей, различие которых задокументировано (cvd_anchor: секунда снимка против последней сделки)."""
    out = {}
    for s, r in json.loads(json.dumps(doc["syms"])).items():
        r.pop("cvd_anchor", None)
        out[s] = r
    return out


class TestProperty20(IntelCase):
    """Свойство 20: одна фикстура рынка -> одинаковые коды сторожа, xlive_for и xflow.summary у XStream.document и писателя cmkt."""

    def setUp(self):
        super().setUp()
        self.root2 = tempfile.mkdtemp(prefix="tb_xflow_cmkt_")
        self.addCleanup(shutil.rmtree, self.root2, True)
        self.xs_doc, self.exs = xstream_doc(self.root)
        self.cm_doc = cmkt_doc(self.root2)
        write_doc(self.root, self.xs_doc)

    def test_documents_equal(self):
        self.assertEqual(self.xs_doc["src"], "xstream")
        self.assertEqual(self.cm_doc["src"], "cmkt")
        self.assertEqual(self.cm_doc["ts_epoch"], float(TS))
        self.assertEqual(sorted(self.cm_doc["syms"]), ["BTC", "ETH"])
        self.assertEqual(strip(self.cm_doc), strip(self.xs_doc))
        self.assertEqual(self.cm_doc["syms"]["BTC"]["cvd_anchor"], float(TS))
        self.assertEqual(sorted(self.cm_doc["ex"]), sorted(EX))
        for e in EX:
            self.assertEqual(sorted(self.cm_doc["ex"][e]), ["down", "err", "fails", "msgs", "nosup", "ok"])
            self.assertIs(self.cm_doc["ex"][e]["ok"], True)
            self.assertEqual(self.cm_doc["ex"][e]["nosup"], [] if e == "binanceusdm" else ["liq"])

    def test_scope_like_xstream(self):
        r = self.cm_doc["syms"]["BTC"]
        self.assertEqual(sorted(r["ob"]), sorted(EX))                                   # без Bybit и без спота
        self.assertEqual(sorted(r["live"]), sorted(EX))
        self.assertEqual(sorted(k for k, v in r["live"].items() if "liq5_long_usd" in v), ["binanceusdm"])   # ликвидации только Binance
        self.assertEqual(r["live"]["binanceusdm"]["liq5_long_usd"], 250000)
        self.assertNotIn("liq_long_usd", r["slow"]["okx"])
        self.assertEqual(set(self.exs["binanceusdm"].limits), {20})                     # xstream: стакан Binance 20 уровней
        self.assertEqual(set(self.exs["bitget"].limits), {15})
        self.assertEqual(set(self.exs["okx"].limits), {None})                           # OKX без предела
        self.assertLess(r["ob"]["binanceusdm"]["depth_pct"], 1.01)                      # обрезка: 20 уровней по 0.05 %
        self.assertIsNone(r["ob"]["binanceusdm"]["bands"]["2"])
        self.assertIsNotNone(r["ob"]["okx"]["bands"]["2"])
        sides = sorted((w["side"], w["n_ex"]) for w in r["walls"])
        self.assertIn(("ask", 3), sides)                                                # стена Bitget в корзине всех трёх бирж
        self.assertIn(("bid", 3), sides)                                                # стена OKX
        self.assertFalse(any(w["dist_pct"] > 1.4 for w in r["walls"]))                  # уровень Binance за 20 уровнями не виден

    def test_summary_equal(self):
        now = TS + 1.0
        s1, s2 = xflow.summary(self.xs_doc, now, {}), xflow.summary(self.cm_doc, now, {})
        self.assertEqual(s2, s1)
        self.assertEqual(sorted(s2), ["BTC", "ETH"])
        for k in ("cvd5_x_pct", "cvd15_x_usd", "ob05_x_imb_pct", "walls", "liq1h_long_x_usd", "fundh3_x_pct"):
            self.assertIn(k, s2["BTC"])
        for k in ("ls_x", "oi1h_x_pct", "oi24h_x_pct"):                                 # xstream их не даёт: отсутствуют, как сейчас
            self.assertNotIn(k, s2["BTC"])

    def test_xlive_and_sentinel_codes_equal(self):
        write_doc(self.root2, self.cm_doc)
        now = TS + 1.0
        for sym in SYMS:
            x1, x2 = flowdoc.xlive_for(self.root, sym, now), flowdoc.xlive_for(self.root2, sym, now)
            self.assertEqual(x2, x1, sym)
            c1, c2 = watch_ctx(self.root, sym, now), watch_ctx(self.root2, sym, now)
            self.assertEqual(c2.get("walls"), c1.get("walls"), sym)
            self.assertEqual(c2.get("xlive"), c1.get("xlive"), sym)
            self.assertEqual(c2.get("crowd"), c1.get("crowd"), sym)
            self.assertEqual(liq_side(c2.get("xlive")), liq_side(c1.get("xlive")), sym)
            for side in (1, -1):
                for a5 in (0.2, 0.5, 1.0):
                    k1 = W._market_codes({}, side, None, now, {}, dict(c1, a5_pct=a5))
                    k2 = W._market_codes({}, side, None, now, {}, dict(c2, a5_pct=a5))
                    self.assertEqual(k2, k1, (sym, side, a5))
        btc = watch_ctx(self.root2, "BTC", now)
        self.assertEqual(liq_side(btc["xlive"]), "long")                                 # 250 тыс. лонгов против 10 тыс. шортов на Binance
        self.assertIn("BOOK_WALL", W._market_codes({}, 1, None, now, {}, dict(btc, a5_pct=0.5)))    # стена ask 0.3 % против лонга
        self.assertIn("BOOK_WALL", W._market_codes({}, -1, None, now, {}, dict(btc, a5_pct=0.5)))   # стена bid 0.6 % против шорта
        self.assertNotIn("BOOK_WALL", W._market_codes({}, -1, None, now, {}, dict(btc, a5_pct=0.2)))
        self.assertIsNone(flowdoc.xlive_for(self.root2, "BTC", TS + 60))                # устарел: как у xstream


class TestWriter(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tb_xflow_w_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_every_2s_and_excluded_markets(self):
        """Запись раз в 2 с; рынок с кодом исключения (здесь — устаревший документ OKX) у монеты не участвует."""
        for e in EX:
            put(self.root, ex_doc(MKT[e], dict((s, ex_row(e, s)) for s in SYMS), dict((s, cx_of(e, s)) for s in SYMS if e in LISTED[s])))
        FO.write(self.root, focus_doc())
        a = AG.Aggregator(self.root, cmkt_cfg(), clock=lambda: TS + 0.3, sleep=lambda s: None)
        a.store.disk_free = lambda p: 10 ** 15
        self.addCleanup(a.close)
        a.tick(TS, TS + 0.3)
        mt = os.stat(CX.xflow_path(self.root)).st_mtime_ns
        self.assertEqual(a.compat.writes, 1)
        a.tick(TS + 1, TS + 1.3)
        self.assertEqual(a.compat.writes, 1)                                            # через 1 с не пишет
        self.assertEqual(os.stat(CX.xflow_path(self.root)).st_mtime_ns, mt)
        doc = a.tick(TS + 2, TS + 2.3)
        self.assertIsNotNone(doc)
        self.assertEqual(a.compat.writes, 2)
        with open(CX.xflow_path(self.root), encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual(d["ts_epoch"], float(TS + 2))
        self.assertEqual(sorted(d["syms"]), [])                                          # документы рынков на T старше 1 с: STALE у всех
        self.assertEqual(d["ex"]["okx"]["ok"], False)
        self.assertEqual(d["ex"]["okx"]["err"], "STALE")

    def test_flag_off_no_writer(self):
        a = AG.Aggregator(self.root, cmkt_cfg(on=False), clock=lambda: TS + 0.3, sleep=lambda s: None)
        self.addCleanup(a.close)
        self.assertIsNone(a.compat)
        a.store.disk_free = lambda p: 10 ** 15
        FO.write(self.root, focus_doc())
        a.tick(TS, TS + 0.3)
        self.assertFalse(os.path.exists(CX.xflow_path(self.root)))
        c = cmkt_cfg()
        c["enabled"] = False
        self.assertFalse(C.compat_active(c))                                             # выключенный сводный рынок не пишет и не глушит XStream
        self.assertTrue(C.compat_active(cmkt_cfg()))

    def test_symbols_from_focus_compat(self):
        for e in EX:
            put(self.root, ex_doc(MKT[e], dict((s, ex_row(e, s)) for s in SYMS), dict((s, cx_of(e, s)) for s in SYMS if e in LISTED[s])))
        FO.write(self.root, dict(focus_doc(), compat=["ETH"]))
        a = AG.Aggregator(self.root, cmkt_cfg(), clock=lambda: TS + 0.3, sleep=lambda s: None)
        a.store.disk_free = lambda p: 10 ** 15
        self.addCleanup(a.close)
        a.tick(TS, TS + 0.3)
        with open(CX.xflow_path(self.root), encoding="utf-8") as f:
            self.assertEqual(sorted(json.load(f)["syms"]), ["ETH"])


class TestOneWriter(IntelCase):
    """Сервис intel при compat_xflow=true не запускает XStream; без флага и с битым config/cmkt.json — запускает."""

    def svc(self):
        from tb.intel import service as SV
        from tb.intel import store
        started = []

        class Stub(object):
            def __init__(self, *a, **k):
                pass

            def start(self):
                started.append(1)

            def stop(self):
                pass
        with mock.patch.object(SV.xstream_mod, "XStream", Stub), mock.patch.object(SV.xstream_mod, "available", lambda: True):
            s = SV.Service(self.root, store.load_cfg(self.root), None, clock=lambda: float(TS), emit=lambda *a, **k: None, inline=True,
                           log=lambda m: None)
            s.close()
        return s, started

    def put_cfg(self, text):
        p = paths.config(C.FILE, root=self.root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def test_flag(self):
        s, started = self.svc()
        self.assertEqual((s.xflow_by_cmkt, len(started)), (False, 1))                   # нет config/cmkt.json: умолчание false
        self.put_cfg(json.dumps(cmkt_cfg(on=True)))
        s, started = self.svc()
        self.assertEqual((s.xflow_by_cmkt, len(started), s.xstream), (True, 0, None))
        self.put_cfg(json.dumps(dict(cmkt_cfg(on=True), compat_xflow="yes")))          # битый файл: cmkt не стартует, пишет xstream
        s, started = self.svc()
        self.assertEqual((s.xflow_by_cmkt, len(started)), (False, 1))


class TestCollectorCx(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tb_xflow_col_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def make(self, cx):
        from tb.cmkt.ex import fakex as F
        cfg = copy.deepcopy(C.defaults())
        cfg["venues"]["fakex"] = {"enabled": True, "markets": ["perp", "spot"], "hosts": ["fakex.test", "api.fakex.test"]}
        cfg["limits"]["rest_weight_per_min"]["fakex"] = 600
        a = F.FakexAdapter(cfg, rules={"perp": {"deep": "next", "light": "replace"}, "spot": {"deep": "next", "light": "replace"}})
        now = float(TS)
        col = CO.Collector("fakex", "perp", cfg, root=self.root, adapter=a, clock=lambda: now)
        self.assertTrue(col.load_meta(now))
        self.assertIsNone(col.cx)                                                         # fakex не входит в охват xstream
        col.cx = cx
        fd = {"schema": FO.SCHEMA, "ts": now, "deep": ["BTC", "ETH"], "light": [], "why": {"BTC": "core", "ETH": "core"},
              "since": {"BTC": now, "ETH": now}, "compat": ["BTC", "SOL"]}
        col.apply_focus(fd, now)
        b = [["%.2f" % (100.0 - 0.01 - i * 0.05), "1000"] for i in range(40)]
        s = [["%.2f" % (100.0 + 0.01 + i * 0.05), "1000"] for i in range(40)]
        for sym, native in (("BTC", "BTCUSDT"), ("ETH", "ETHUSDT")):
            m = {"ch": "book", "type": "snap", "s": native, "u": 10, "b": b, "a": s, "E": int((now + 0.2) * 1000), "rest": False}
            col.on_events(a.parse("perp", json.dumps(m), now + 0.2), now + 0.2)
        return col

    def test_cx_trimmed_book_of_compat_coins(self):
        col = self.make((True, 20))
        doc = col.publish(TS + 1, TS + 1.01)
        self.assertEqual(sorted(doc["cx"]), ["BTC"])                                     # только монеты focus.compat со стаканом
        c = col.coins["BTC"]
        bids, asks = c.book.levels(c.inst.contract_size, c.inst.coin_mult)
        self.assertEqual(len(bids), 40)
        self.assertEqual(doc["cx"]["BTC"], json.loads(json.dumps(L.compat_book_row(bids, asks, 20))))
        self.assertEqual(json.loads(json.dumps(doc))["cx"], CO.read_ex("fakex_perp", self.root)["cx"])
        self.assertLess(doc["cx"]["BTC"][1], 1.0)                                          # глубина 20 уровней × 0.05 %
        full = L.compat_book_row(bids, asks, None)
        self.assertGreater(full[1], doc["cx"]["BTC"][1])
        b = L.compat_book_unpack(json.loads(json.dumps(doc["cx"]["BTC"])))
        self.assertEqual(b["mid"], doc["cx"]["BTC"][0])
        self.assertTrue(all(isinstance(k[1], int) for k in b["buckets"]))

    def test_no_cx_without_flag(self):
        col = self.make(None)
        self.assertNotIn("cx", col.publish(TS + 1, TS + 1.01))


class TestConstants(unittest.TestCase):
    def test_scope_equals_originals(self):
        """Охват и параметры совместимого документа равны xstream.DEFAULTS и xflow.DEFAULTS (logic.cmkt их не импортирует)."""
        self.assertEqual([e for _, e, _ in L.COMPAT_MARKETS], XS.DEFAULTS["exchanges"])
        self.assertEqual(dict((e, d) for _, e, d in L.COMPAT_MARKETS), dict((e, XS.DEFAULTS["book_limit"][e]) for e in XS.DEFAULTS["exchanges"]))
        self.assertEqual(list(L.COMPAT_LIQ), XS.DEFAULTS["liq_exchanges"])
        self.assertEqual((L.COMPAT_N, L.COMPAT_PUBLISH_S), (XS.DEFAULTS["n_syms"], XS.DEFAULTS["publish_s"]))
        self.assertEqual((L.COMPAT_WALL_MULT, L.COMPAT_MIN_WALL_USD, L.COMPAT_BUCKET_PCT, L.COMPAT_STALE_S),
                         tuple(xflow.DEFAULTS[k] for k in ("wall_mult", "min_wall_usd", "bucket_pct", "stale_s")))
        self.assertEqual(L.COMPAT_KINDS, XS.KINDS)
        self.assertEqual(FO.COMPAT_N, L.COMPAT_N)
        self.assertEqual(CX.xflow_path("R"), xflow.xflow_path("R"))
        self.assertIs(C.defaults()["compat_xflow"], False)                                # поставка: xstream пишет, cmkt параллельно (Requirement 17.4)

    def test_no_xflow_class(self):
        self.assertFalse(hasattr(xflow, "XFlow"))
        for name in ("book_stats", "walls", "oi_changes", "summary", "read", "Tape", "fcfg"):
            self.assertTrue(callable(getattr(xflow, name)), name)


if __name__ == "__main__":
    unittest.main()
