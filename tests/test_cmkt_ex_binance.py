"""R1.7, задача 8: адаптер Binance (perp и spot) — разбор живых фикстур задачи 2, восстановление стакана (свойство 4), приведение единиц,
план подписок, снимок WS API, OI по REST с запасным хостом, ресинхронизация в пределах веса (Requirements 2.1, 5.1, 5.3, 5.5, 5.6, 5.9,
7.5, 9.1, 12.4). Внешней сети нет: REST подменён, WS — FakeServer на 127.0.0.1.
"""
import asyncio
import copy
import json
import os
import random
import unittest

import _bootstrap  # noqa: F401
from tb.cmkt import adapter as A
from tb.cmkt import conf as C
from tb.cmkt import ex
from tb.cmkt.ex import binance as B
from tb.cmkt.ex import fakex as F

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures_cmkt", "binance")

PERP_INFO = {"symbols": [
    {"symbol": s, "contractType": ct, "status": st, "baseAsset": b, "quoteAsset": q, "marginAsset": q,
     "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.01"}]}
    for s, ct, st, b, q in (("BTCUSDT", "PERPETUAL", "TRADING", "BTC", "USDT"), ("ETHUSDT", "PERPETUAL", "TRADING", "ETH", "USDT"),
                            ("DOGEUSDT", "PERPETUAL", "TRADING", "DOGE", "USDT"),
                            ("1000PEPEUSDT", "PERPETUAL", "TRADING", "1000PEPE", "USDT"),
                            ("BTCUSDC", "PERPETUAL", "TRADING", "BTC", "USDC"), ("BTCUSDT_261225", "CURRENT_QUARTER", "TRADING", "BTC", "USDT"),
                            ("XAUUSDT", "TRADIFI_PERPETUAL", "TRADING", "XAU", "USDT"), ("OLDUSDT", "PERPETUAL", "SETTLING", "OLD", "USDT"))]}
FUNDING = [{"symbol": "ETHUSDT", "fundingIntervalHours": 4}, {"symbol": "DOGEUSDT", "fundingIntervalHours": 4}]
SPOT_INFO = {"symbols": [{"symbol": s, "status": "TRADING", "baseAsset": b, "quoteAsset": q}
                         for s, b, q in (("BTCUSDT", "BTC", "USDT"), ("ETHUSDT", "ETH", "USDT"), ("DOGEUSDT", "DOGE", "USDT"),
                                         ("PEPEUSDT", "PEPE", "USDT"), ("BTCFDUSD", "BTC", "FDUSD"), ("BTCUSDC", "BTC", "USDC"),
                                         ("ETHBTC", "ETH", "BTC"))]}


class FakeHttp(object):
    """Подменённый REST: хосты из списка down отвечают 451 (как fapi.binance.com и api.binance.com с IP машины)."""

    def __init__(self, routes, down=("fapi.binance.com", "api.binance.com")):
        self.routes, self.down, self.calls = routes, set(down), []

    def get_json(self, url, weight=1, kind="meta"):
        self.calls.append((url, weight, kind))
        host = url.split("/")[2]
        if host in self.down:
            raise A.HttpError(451, "restricted location")
        for k, v in self.routes.items():
            if k in url:
                return v(url) if callable(v) else copy.deepcopy(v)
        raise A.HttpError(404, url)


def adapter():
    a = B.BinanceAdapter(C.defaults())
    a.load_meta(FakeHttp({"/fapi/v1/exchangeInfo": PERP_INFO, "/fapi/v1/fundingInfo": FUNDING, "/api/v3/exchangeInfo": SPOT_INFO}))
    return a


def fixture(kind):
    out = []
    with open(os.path.join(FIX, kind + ".jsonl"), encoding="utf-8") as f:
        for line in f:
            out.append(json.loads(line))
    return out


def stream_of(r):
    try:
        return json.loads(r["m"]).get("stream")
    except (KeyError, ValueError, AttributeError):
        return None


class TestMeta(unittest.TestCase):
    def test_meta_fallback_host_selection_and_units(self):
        http = FakeHttp({"/fapi/v1/exchangeInfo": PERP_INFO, "/fapi/v1/fundingInfo": FUNDING, "/api/v3/exchangeInfo": SPOT_INFO})
        a = B.BinanceAdapter(C.defaults())
        got = a.load_meta(http)
        self.assertEqual(sorted(i.sym for i in got["perp"]), ["BTC", "DOGE", "ETH", "PEPE"])     # USDC, срочный, TradFi, SETTLING — нет
        self.assertEqual(sorted(i.sym for i in got["spot"]), ["BTC", "DOGE", "ETH", "PEPE"])     # FDUSD, USDC, BTC-котировка — нет
        pepe = a.meta.get("binance_perp", "PEPE")
        self.assertEqual((pepe.native, pepe.coin_mult, pepe.contract_size), ("1000PEPEUSDT", 1000.0, 1.0))
        self.assertEqual(a.meta.get("binance_perp", "ETH").funding_h, 4.0)
        self.assertEqual(a.meta.get("binance_perp", "BTC").funding_h, 8.0)                       # нет в fundingInfo — 8 ч
        self.assertEqual((a.rest["perp"].good, a.rest["spot"].good), ("www.binance.com", "data-api.binance.vision"))
        # кеш 6 ч: повтор без запросов
        n = len(http.calls)
        self.assertEqual(a.load_meta(http, now=3600.0), {})
        self.assertEqual(len(http.calls), n)
        self.assertNotEqual(a.load_meta(http, now=7 * 3600.0), {})

    def test_hosts_in_whitelist_and_registry(self):
        cfg = C.defaults()
        a = ex.load("binance", cfg)
        self.assertIsInstance(a, B.BinanceAdapter)
        for h in a.hosts()["ws"] + a.hosts()["rest"]:
            self.assertTrue(C.host_allowed(cfg, "binance", h), h)
        for kind in ("perp", "spot"):
            for url in a.urls(kind).values():
                self.assertIsNone(A.host_check(cfg, "binance", url), url)
        self.assertEqual(a.conn_max_s, 86400.0)

    def test_server_time(self):
        a = adapter()
        http = FakeHttp({"/fapi/v1/time": {"serverTime": 1793784566000}, "/api/v3/time": {"serverTime": 1793784567000}})
        self.assertEqual(a.server_time(http), 1793784566.0)
        self.assertEqual(a.server_time(http, "spot"), 1793784567.0)
        co = A.ClockOffset()
        co.measure(lambda: a.server_time(http), clock=lambda: 1793784526.0)
        self.assertAlmostEqual(co.offset, 40.0, places=3)


class TestFixtureParse(unittest.TestCase):
    def test_perp_fixture_without_parse_errors(self):
        a = adapter()
        evs = []
        for r in fixture("perp"):
            if "m" in r:
                evs.extend(a.parse("perp", r["m"], r["t"]))
        self.assertEqual(a.stats["unknown"], 0)
        kinds = {}
        for e in evs:
            kinds[type(e).__name__] = kinds.get(type(e).__name__, 0) + 1
        self.assertGreater(kinds.get("BookDelta", 0), 150)
        self.assertGreater(kinds.get("Trade", 0), 50)
        self.assertGreater(kinds.get("Ticker", 0), 20)
        self.assertEqual(kinds.get("BookSnapshot"), 11)                       # @depth20@100ms — частичный стакан
        self.assertTrue(all(e.part for e in evs if isinstance(e, A.BookSnapshot)))
        self.assertGreater(a.stats["skip"], 0)                                  # ликвидации монет вне метаданных
        for e in evs:
            if isinstance(e, A.Trade):
                self.assertIn(e.side, (1, -1))
                self.assertGreater(e.coins, 0)
                self.assertLess(abs(e.t_rx + 40.2 - e.t_ex), 3.0)              # локальные часы отстают на ~40 с (exchanges.md, п. 0)
            if isinstance(e, A.Ticker):
                self.assertEqual(e.funding_h, 4.0 if e.sym in ("ETH", "DOGE") else 8.0)
                self.assertIsNotNone(e.mark)

    def test_aggressor_side(self):
        a = adapter()
        m = lambda maker: json.dumps({"stream": "btcusdt@aggTrade", "data": {"e": "aggTrade", "E": 1, "a": 7, "s": "BTCUSDT", "p": "100",   # noqa: E731
                                                                              "q": "2", "T": 2, "m": maker}})
        self.assertEqual(a.parse("perp", m(True), 1.0)[0].side, -1)             # покупатель мейкер — агрессор продаёт
        self.assertEqual(a.parse("perp", m(False), 1.0)[0].side, 1)
        self.assertEqual(a.parse("perp", m(None), 1.0)[0].side, 0)              # сторона не распознана (side_unknown)

    def test_spot_fixture_without_parse_errors(self):
        a = adapter()
        evs = []
        for r in fixture("spot"):
            if "m" in r:
                evs.extend(a.parse("spot", r["m"], r["t"]))
        self.assertEqual(a.stats["unknown"], 0)
        parts = [e for e in evs if isinstance(e, A.BookSnapshot)]
        self.assertTrue(parts and all(e.part and e.t_ex is None for e in parts))     # @depth20 спота без времени -> no_tex
        self.assertGreater(len([e for e in evs if isinstance(e, A.Trade)]), 50)

    def test_units_1000pepe_and_liquidation(self):
        a = adapter()
        t = a.parse("perp", json.dumps({"stream": "1000pepeusdt@aggTrade", "data": {"s": "1000PEPEUSDT", "p": "0.00508", "q": "1000",
                                                                                 "T": 1793784566000, "m": False, "a": 1}}), 1.0)[0]
        self.assertAlmostEqual(t.px, 5.08e-6, places=15)                         # цена за монету
        self.assertEqual(t.coins, 1e6)
        self.assertAlmostEqual(t.px * t.coins, 0.00508 * 1000, places=9)        # USD не меняется
        mk = a.parse("perp", json.dumps({"stream": "1000pepeusdt@markPrice@1s", "data": {"E": 1, "s": "1000PEPEUSDT", "p": "0.00508",
                                                                                      "i": "0.00509", "r": "0.0001", "T": 2}}), 1.0)[0]
        self.assertAlmostEqual(mk.mark, 5.08e-6, places=15)
        self.assertEqual(mk.funding, 0.0001)
        liq = lambda s, side: json.dumps({"stream": "!forceOrder@arr", "data": {"E": 5, "o": {"s": s, "S": side, "q": "10", "p": "0.005",   # noqa: E731
                                                                                          "ap": "0.0051", "z": "10", "T": 4}}})
        lq = a.parse("perp", liq("1000PEPEUSDT", "SELL"), 1.0)[0]
        self.assertEqual((lq.liq_side, lq.coins), ("long", 10000.0))
        self.assertAlmostEqual(lq.px, 5.1e-6, places=15)
        self.assertEqual(a.parse("perp", liq("BTCUSDT", "BUY"), 1.0)[0].liq_side, "short")
        self.assertEqual(a.parse("perp", liq("BTCUSDC", "BUY"), 1.0), [])         # *USDC отбрасываются
        self.assertIn("binance_perp", a.liq_partial)


def perp_book_run(a, rows, sym="DOGE", drop=None):
    """Поток стакана монеты из фикстуры: дельты до снимка буферизуются, снимок WS API (по book_req), далее дельты.
    -> (BookState, [(seq, snapshot depth20)] для сверки, результаты apply_delta)."""
    st = a.book_state("perp", sym, "deep")
    st.resync("start", 0.0)
    refs, res, k = [], [], 0
    for r in rows:
        if r.get("src", "").startswith("ws-fapi depth"):
            conn, text = a.book_req("perp", sym, 1000)
            self_id = json.loads(text)["id"]
            body = json.loads(r["m"])
            body["id"] = self_id
            snap = a.parse("perp", json.dumps(body), r["t"])[0]
            assert conn == "wsapi" and snap.rest
            st.apply_snapshot(snap, r["t"])
            continue
        s = stream_of(r)
        if not s or not s.startswith(a.native("perp", sym).lower() + "@depth"):
            continue
        for e in a.parse("perp", r["m"], r["t"]):
            if isinstance(e, A.BookSnapshot):
                refs.append((e.seq, e))
            else:
                k += 1
                if drop is not None and k == drop:
                    continue
                res.append(st.apply_delta(e, r["t"]))
    return st, refs, res


def top(st, side, n=20):
    d = st.bids if side == "bid" else st.asks
    ks = sorted(d, key=lambda p: -float(p) if side == "bid" else float(p))[:n]
    return [(float(p), float(d[p])) for p in ks]


class TestBookPerp(unittest.TestCase):
    def test_property4_fixture_continuity(self):
        """Свойство 4 на живой фикстуре: снимок WS API (lastUpdateId = u дельты в буфере) и все дельты после него без разрывов;
        стакан не перекрещён, середина рядом с маркой DOGE. (Частичные @depth20 фикстуры записаны до снимка — сверка верха стакана
        с эталоном сделана на fakex, test_cmkt_adapter.)"""
        a = adapter()
        rows = fixture("perp")
        st, refs, res = perp_book_run(a, rows)
        self.assertEqual(st.status, "ok", st.reason)
        self.assertIn("buffered", res)
        self.assertGreater(res.count("ok"), 100)
        self.assertNotIn("gap", res)
        self.assertGreater(st.stale, 0)                                          # дельты до снимка отброшены (u < lastUpdateId)
        bb, ba = st.best()
        self.assertLess(bb, ba)
        marks = [e.mark for r in rows if stream_of(r) == "dogeusdt@markPrice@1s" for e in a.parse("perp", r["m"], r["t"])]
        if marks:
            self.assertLess(abs((bb + ba) / 2 / marks[-1] - 1), 0.002)
        self.assertGreater(len(st.bids), 300)                                    # 1000 уровней снимка, без обрезки
        # граница снимка: уровней дальше границы нет; глубина выдачи — до границы
        self.assertTrue(all(p >= st.bound_bid for p in st.bids))
        self.assertTrue(all(p <= st.bound_ask for p in st.asks))
        self.assertIsNotNone(st.depth())

    def test_gap_in_pu_gives_resync(self):
        a = adapter()
        st, _, res = perp_book_run(a, fixture("perp"), drop=90)               # дельта после снимка (снимок — около 57-й дельты)
        self.assertEqual((st.status, st.reason), ("resync", "gap"))
        self.assertIn("gap", res)

    def test_connection_rotation_without_resync(self):
        """Смена соединения до разрыва 24 ч: поток нового соединения начинается с перекрытием — повторы отбрасываются (stale),
        номера непрерывны, стакан не уходит в RESYNC (Requirement 5.6)."""
        a = adapter()
        rows = fixture("perp")
        st, _, _ = perp_book_run(a, rows)
        deltas = []
        for r in rows:
            if stream_of(r) == "dogeusdt@depth@100ms":
                deltas.extend(a.parse("perp", r["m"], r["t"]))
        # стакан с того же снимка; старое соединение отдаёт дельты до k, новое — с k − 5
        base = a.book_state("perp", "DOGE", "deep")
        snap_row = next(r for r in rows if r.get("src"))
        conn, text = a.book_req("perp", "DOGE")
        body = json.loads(snap_row["m"])
        body["id"] = json.loads(text)["id"]
        snap = a.parse("perp", json.dumps(body), 1.0)[0]
        base.apply_snapshot(snap, 1.0)
        after = [d for d in deltas if d.last >= snap.seq]
        k = len(after) // 2
        for d in after[:k]:                                                     # старое соединение
            base.apply_delta(d, 1.0)
        out = [base.apply_delta(d, 1.0) for d in after[k - 5:]]                 # новое соединение с перекрытием 5 дельт
        self.assertEqual(base.status, "ok")
        self.assertEqual(out[:5], ["stale"] * 5)
        self.assertNotIn("gap", out)
        self.assertEqual(base.seq, st.seq)


class TestBookSpot(unittest.TestCase):
    def run_spot(self, a, drop=None):
        st = a.book_state("spot", "DOGE", "deep")
        st.resync("start", 0.0)
        refs, res, k = {}, [], 0
        matched = 0
        for r in fixture("spot"):
            if "rest" in r:
                snap = a._snap("spot", "DOGE", r["body"], r["t"])
                self.assertTrue(snap.rest)
                st.apply_snapshot(snap, r["t"])
                continue
            s = stream_of(r)
            if not s or not s.startswith("dogeusdt@depth"):
                continue
            for e in a.parse("spot", r["m"], r["t"]):
                if isinstance(e, A.BookSnapshot):
                    refs[e.seq] = e
                    if st.status == "ok" and st.seq == e.seq:
                        self.assertEqual(top(st, "bid"), [(float(p), float(q)) for p, q in e.bids][:20])
                        self.assertEqual(top(st, "ask"), [(float(p), float(q)) for p, q in e.asks][:20])
                        matched += 1
                    continue
                k += 1
                if drop is not None and k == drop:
                    continue
                res.append(st.apply_delta(e, r["t"]))
                if st.status == "ok" and st.seq in refs:
                    e2 = refs[st.seq]
                    self.assertEqual(top(st, "bid"), [(float(p), float(q)) for p, q in e2.bids][:20])
                    matched += 1
        return st, res, matched

    def test_property4_spot_rest_snapshot_rule(self):
        """Спот: первая дельта после REST-снимка U ≤ lastUpdateId + 1 ≤ u, далее U = прошлый u + 1; верх совпадает с @depth20."""
        a = adapter()
        st, res, matched = self.run_spot(a)
        self.assertEqual(st.status, "ok", st.reason)
        self.assertNotIn("gap", res)
        self.assertGreater(res.count("ok"), 50)
        self.assertGreater(st.stale, 0)
        bb, ba = st.best()
        self.assertLess(bb, ba)
        self.assertTrue(all(p >= st.bound_bid for p in st.bids))

    def test_spot_gap_gives_resync(self):
        a = adapter()
        st, res, _ = self.run_spot(a, drop=200)
        self.assertEqual((st.status, st.reason), ("resync", "gap"))

    def test_book_rest_spot_and_perp_fallback(self):
        a = adapter()
        body = next(r for r in fixture("spot") if "rest" in r)["body"]
        http = FakeHttp({"/api/v3/depth": body, "/fapi/v1/depth": dict(body, E=1793784570720)})
        s = a.book_rest(http, "spot", "DOGE", 1000)
        self.assertEqual((s.mkt, s.sym, s.seq, s.rest), ("binance_spot", "DOGE", body["lastUpdateId"], True))
        self.assertIn("https://data-api.binance.vision/api/v3/depth?symbol=DOGEUSDT&limit=1000", [c[0] for c in http.calls])
        self.assertEqual(http.calls[-1][1:], (0, "snap"))                       # вес списан очередью восстановления, не повторно
        p = a.book_rest(http, "perp", "DOGE", 500)
        self.assertEqual((p.mkt, p.t_ex), ("binance_perp", 1793784570.72))
        self.assertIn("https://www.binance.com/fapi/v1/depth?symbol=DOGEUSDT&limit=500", [c[0] for c in http.calls])
        self.assertIsNone(a.book_rest(FakeHttp({}), "spot", "DOGE"))
        self.assertGreater(a.stats["snap_err"], 0)


class TestPlanAndSubs(unittest.TestCase):
    def test_plan_routes_and_resync_part(self):
        a = adapter()
        subs = a.plan("perp", ["BTC", "PEPE"], ["DOGE"], resync=["BTC"])
        by = dict((s.topic, s) for s in subs)
        self.assertEqual(by["btcusdt@depth@100ms"].conn_key, "public")
        self.assertEqual(by["btcusdt@depth20@100ms"].kind, "book_part")       # частичный стакан на время RESYNC
        self.assertNotIn("1000pepeusdt@depth20@100ms", by)
        self.assertEqual(by["dogeusdt@depth20@500ms"].kind, "book_part")
        self.assertEqual(by["1000pepeusdt@aggTrade"].conn_key, "market")
        self.assertEqual(by["btcusdt@markPrice@1s"].conn_key, "market")
        self.assertEqual(by[B.LIQ_TOPIC].conn_key, "market")
        self.assertNotIn("dogeusdt@depth@100ms", by)
        self.assertEqual(a.plan("perp", ["NOPE"], []), [])                      # монета без метаданных не подписывается
        spot = a.plan("spot", ["BTC"], ["DOGE"])
        self.assertEqual(sorted(s.topic for s in spot), ["btcusdt@aggTrade", "btcusdt@depth@100ms", "dogeusdt@aggTrade",
                                                         "dogeusdt@depth20@1000ms"])
        self.assertTrue(all(s.conn_key == "main" for s in spot))
        self.assertEqual(set(a.urls("perp")), {"public", "market", "wsapi"})
        # планировщик: темы маршрута на своих соединениях
        p = A.SubPlanner(A.subs_per_conn(a.cfg, "binance"))
        ch = p.update(subs)
        self.assertEqual(sorted(ch["open"]), ["market#0", "public#0"])

    def test_sub_msgs_ack_and_invalid(self):
        a = adapter()
        subs = [A.Sub("market", "s%03dusdt@aggTrade" % i, "trades", "X") for i in range(120)] + [A.Sub("public", "btcusdt@depth@250ms",
                                                                                                      "book", "BTC")]
        msgs = a.sub_msgs(subs)
        self.assertEqual(len(msgs), 3)                                           # по 50 потоков
        self.assertEqual(a.stats["invalid_topic"], 1)                            # тема вне списка допустимых не отправляется
        m0 = json.loads(msgs[0])
        self.assertEqual((m0["method"], len(m0["params"])), ("SUBSCRIBE", 50))
        ok = a.parse("perp", json.dumps({"result": None, "id": m0["id"]}), 1.0)[0]
        self.assertEqual((ok.ok, len(ok.topics)), (True, 50))
        m1 = json.loads(msgs[1])
        bad = a.parse("perp", json.dumps({"error": {"code": 2, "msg": "Invalid request"}, "id": m1["id"]}), 1.0)[0]
        self.assertFalse(bad.ok)
        self.assertEqual(A.split_rejected(bad.topics)[0][0], [m1["params"][0]])
        un = json.loads(a.sub_msgs(subs[:1], "unsubscribe")[0])
        self.assertEqual(un["method"], "UNSUBSCRIBE")
        self.assertEqual(a.parse("perp", json.dumps({"result": None, "id": un["id"]}), 1.0), [])
        self.assertIsNone(a.ping())
        self.assertEqual(a.resub("perp", "BTC", subs), [])                       # восстановление — снимком, не переподпиской

    def test_book_req_and_errors(self):
        a = adapter()
        conn, text = a.book_req("perp", "BTC", 500)
        m = json.loads(text)
        self.assertEqual((conn, m["method"], m["params"]), ("wsapi", "depth", {"symbol": "BTCUSDT", "limit": 500}))
        self.assertIsNone(a.book_req("spot", "BTC"))
        err = a.parse("perp", json.dumps({"id": m["id"], "status": 429, "error": {"code": -1003, "msg": "Too many"}}), 1.0)
        self.assertEqual(err, [])
        self.assertEqual(a.stats["snap_err"], 1)
        self.assertEqual(a.snapshot_cost("perp", A.PRIO["pos"]), (1000, 20))
        self.assertEqual(a.snapshot_cost("perp", A.PRIO["core"]), (1000, 20))
        self.assertEqual(a.snapshot_cost("perp", A.PRIO["deep"]), (500, 10))
        self.assertEqual(a.snapshot_cost("spot", A.PRIO["deep"]), (1000, 50))


class TestRestOiAndResync(unittest.TestCase):
    def test_poll_oi_fallback_and_failure(self):
        a = adapter()
        http = FakeHttp({"/fapi/v1/openInterest": lambda url: {"openInterest": "12.5", "symbol": url.split("=")[1], "time": 1793784566000}})
        b = A.RestBudget(400, 240)
        got = a.poll(http, "perp", ["BTC", "PEPE", "NOPE"], 100.0)
        self.assertEqual([(t.sym, t.oi_coins) for t in got], [("BTC", 12.5), ("PEPE", 12500.0)])
        self.assertTrue(all(c[2] == "oi" and c[1] == 1 for c in http.calls))
        self.assertEqual(a.rest["perp"].good, "www.binance.com")
        self.assertEqual(a.poll(http, "spot", ["BTC"], 1.0), [])
        # оба хоста отказали: OI нет (None по свежести), ошибка считается
        a2 = adapter()
        got2 = a2.poll(FakeHttp({}, down=("fapi.binance.com", "www.binance.com")), "perp", ["BTC", "ETH"], 1.0)
        self.assertEqual(got2, [])
        self.assertEqual(a2.stats["oi_err"], 1)

        class Budgeted(FakeHttp):
            def get_json(self, url, weight=1, kind="meta"):
                if not b.spend(kind, weight, 0.0):
                    raise A.BudgetExceeded(url)
                return FakeHttp.get_json(self, url, weight, kind)
        b.rows.clear()
        for _ in range(239):
            b.spend("oi", 1, 0.0)
        got3 = adapter().poll(Budgeted(http.routes), "perp", ["BTC", "ETH", "DOGE"], 0.0)
        self.assertEqual(len(got3), 1)                                           # резерв OI кончился — остальные в следующий раз

    def test_resync_20_books_within_limits(self):
        """Ресинхронизация 20 стаканов deep на подменённом снимке: perp ≤ 150 с (первые 8 — позиции, планы, ядро — ≤ 60 с),
        spot ≤ 60 с; вес из остатка RestBudget после резерва OI; пауза OI light при RESYNC больше 4 стаканов (Requirement 5.5)."""
        a = adapter()
        cfg = C.defaults()
        for kind, limit_all, limit_first in (("perp", 150, 60), ("spot", 60, 60)):
            b = A.RestBudget.from_cfg(cfg, "binance", kind, n_light=120)
            q = A.ResyncQueue()
            prios = ["pos"] * 2 + ["plan"] * 2 + ["core"] * 4 + ["deep"] * 12
            syms = ["S%02d" % i for i in range(20)]
            for s, p in zip(syms, prios):
                q.request(s, p, 0.0)
            b.set_resync(20)
            if kind == "perp":
                self.assertTrue(b.light_paused)
            # фоновый опрос OI deep: 20 монет раз в 10 с, вес из резерва
            done = {}
            t = 0.0
            while q.pending() and t < 400:
                if kind == "perp" and int(t) % 10 == 0:
                    for _ in range(20):
                        b.spend("oi", 1, t)
                while True:
                    nx = q.next(t, b, lambda p: a.snapshot_cost(kind, p))
                    if nx is None:
                        break
                    q.done(nx[0])
                    done[nx[0]] = t
                t += 1.0
            self.assertEqual(len(done), 20, kind)
            self.assertLessEqual(max(done.values()), limit_all, kind)
            self.assertLessEqual(max(done[s] for s in syms[:8]), limit_first, kind)
            self.assertLessEqual(max(done[s] for s in syms[:8]), min(done[s] for s in syms[8:]), kind)


class TestNetwork(unittest.IsolatedAsyncioTestCase):
    async def test_fixture_replay_over_localhost(self):
        """Поток фикстуры через VenueConn на 127.0.0.1: подписка уходит, все сообщения разобраны адаптером без parse_err."""
        _bootstrap.require_localhost()
        rows = [r for r in fixture("perp") if "m" in r and not r.get("src")]

        async def replay(peer):
            for _ in range(100):
                if peer.received:
                    break
                await asyncio.sleep(0.02)
            for r in rows:
                await peer.send(r["m"])
            await asyncio.sleep(10)
        srv = await F.FakeServer(script=replay).start()
        self.addAsyncCleanup(srv.stop)
        cfg = copy.deepcopy(C.defaults())
        cfg["sync"].update({"ping_s": 1, "silent_s": 6})
        a = adapter()
        events = []
        c = A.VenueConn(a, "perp", "market#0", srv.url, a.plan("perp", ["BTC"], []), on_events=lambda evs, t: events.extend(evs), cfg=cfg,
                        allow_plain=True, rng=random.Random(1))
        task = asyncio.ensure_future(c.run())
        end = asyncio.get_event_loop().time() + 5
        while c.stats["msgs"] < len(rows) and asyncio.get_event_loop().time() < end:
            await asyncio.sleep(0.02)
        await c.close()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):                            # noqa: BLE001
            pass
        self.assertEqual(c.stats["msgs"], len(rows))
        self.assertEqual(c.stats["parse_err"], 0)
        self.assertTrue(any(isinstance(e, A.Trade) for e in events))
        sent = json.loads(srv.peers[0].received[0])
        self.assertEqual(sent["method"], "SUBSCRIBE")
        self.assertIn("btcusdt@aggTrade", sent["params"])


if __name__ == "__main__":
    unittest.main()
