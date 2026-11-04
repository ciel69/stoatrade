"""R1.7, задача 9: адаптер Bybit (perp и spot) — разбор живых фикстур задачи 2, стакан `u + 1` (свойство 4), сброс `u = 1`, проверка тем
до отправки и повтор по одной при отказе всего запроса, тикер-дельты, ликвидации, единицы `1000PEPE`, REST через запасной хост
(Requirements 2.1, 5.1, 5.5, 5.9, 5.11, 6.3, 9.1). Внешней сети нет: REST подменён, WS — FakeServer на 127.0.0.1.
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
from tb.cmkt.ex import bybit as BY
from tb.cmkt.ex import fakex as F

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures_cmkt", "bybit")


def lin(sym, base, quote="USDT", ct="LinearPerpetual", status="Trading", fi=480):
    return {"symbol": sym, "baseCoin": base, "quoteCoin": quote, "settleCoin": quote, "contractType": ct, "status": status,
            "fundingInterval": fi, "priceFilter": {"tickSize": "0.01"}}


LINEAR = [lin("BTCUSDT", "BTC"), lin("ETHUSDT", "ETH", fi=240), lin("DOGEUSDT", "DOGE"), lin("1000PEPEUSDT", "1000PEPE"),
          lin("NOPEUSDT", "NOPE"), lin("BTCPERP", "BTC", "USDC"), lin("BTC-26DEC26", "BTC", ct="LinearFutures"),
          lin("NEWUSDT", "NEW", status="PreLaunch")]
SPOT = [{"symbol": s, "baseCoin": b, "quoteCoin": q, "status": "Trading"}
        for s, b, q in (("BTCUSDT", "BTC", "USDT"), ("ETHUSDT", "ETH", "USDT"), ("DOGEUSDT", "DOGE", "USDT"), ("PEPEUSDT", "PEPE", "USDT"),
                        ("BTCUSDC", "BTC", "USDC"))]


class FakeHttp(object):
    """api.bybit.com с IP машины отвечает 403 — работает api.bybitglobal.com; instruments-info постранично (cursor)."""

    def __init__(self, down=("api.bybit.com",)):
        self.down, self.calls = set(down), []

    def get_json(self, url, weight=1, kind="meta"):
        self.calls.append(url)
        if url.split("/")[2] in self.down:
            raise A.HttpError(403, "block access from your country")
        if "/v5/market/time" in url:
            return {"retCode": 0, "result": {"timeSecond": "1793784566", "timeNano": "1793784566123456789"}, "time": 1793784566123}
        if "category=linear" in url:
            if "cursor=p2" in url:
                return {"retCode": 0, "result": {"list": LINEAR[4:], "nextPageCursor": ""}}
            return {"retCode": 0, "result": {"list": LINEAR[:4], "nextPageCursor": "p2"}}
        if "category=spot" in url:
            return {"retCode": 0, "result": {"list": SPOT, "nextPageCursor": ""}}
        raise A.HttpError(404, url)


def adapter():
    a = BY.BybitAdapter(C.defaults())
    a.load_meta(FakeHttp())
    return a


def fixture(kind):
    with open(os.path.join(FIX, kind + ".jsonl"), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def topic_of(r):
    try:
        return json.loads(r["m"]).get("topic")
    except (KeyError, ValueError, AttributeError):
        return None


def book_run(a, kind, topic, drop=None, tier="deep"):
    st = a.book_state(kind, "DOGE", tier)
    res, k = [], 0
    for r in fixture(kind):
        if topic_of(r) != topic:
            continue
        for e in a.parse(kind, r["m"], r["t"]):
            if isinstance(e, A.BookSnapshot):
                st.apply_snapshot(e, r["t"])
                continue
            k += 1
            if drop is not None and k == drop:
                continue
            res.append(st.apply_delta(e, r["t"]))
    return st, res


class TestMeta(unittest.TestCase):
    def test_meta_via_bybitglobal_pagination_and_selection(self):
        http = FakeHttp()
        a = BY.BybitAdapter(C.defaults())
        got = a.load_meta(http)
        self.assertEqual(sorted(i.sym for i in got["perp"]), ["BTC", "DOGE", "ETH", "NOPE", "PEPE"])   # USDC, срочный, PreLaunch — нет
        self.assertEqual(sorted(i.sym for i in got["spot"]), ["BTC", "DOGE", "ETH", "PEPE"])
        self.assertEqual(a.rest.good, "api.bybitglobal.com")
        self.assertTrue(any("cursor=p2" in u for u in http.calls))
        pepe = a.meta.get("bybit_perp", "PEPE")
        self.assertEqual((pepe.native, pepe.coin_mult), ("1000PEPEUSDT", 1000.0))
        self.assertEqual(a.meta.get("bybit_perp", "ETH").funding_h, 4.0)
        self.assertAlmostEqual(a.server_time(http), 1793784566.123456789, places=5)

    def test_hosts_and_registry(self):
        cfg = C.defaults()
        a = ex.load("bybit", cfg)
        for h in a.hosts()["ws"] + a.hosts()["rest"]:
            self.assertTrue(C.host_allowed(cfg, "bybit", h), h)
        for kind in ("perp", "spot"):
            self.assertIsNone(A.host_check(cfg, "bybit", a.urls(kind)["main"]))
        self.assertEqual(a.liq_partial, ())                                     # до 3 ликвидаций в секунду на символ


class TestFixtureParse(unittest.TestCase):
    def test_fixtures_without_parse_errors(self):
        for kind in ("perp", "spot"):
            a = adapter()
            evs = []
            for r in fixture(kind):
                evs.extend(a.parse(kind, r["m"], r["t"]))
            self.assertEqual(a.stats["unknown"], 0, kind)
            names = set(type(e).__name__ for e in evs)
            self.assertTrue({"Trade", "BookSnapshot", "BookDelta", "Ticker"} <= names, (kind, names))   # ответы чужих req_id — []
            if kind == "perp":
                self.assertIn("Liquidation", names)
            self.assertTrue(all(e.side in (1, -1) for e in evs if isinstance(e, A.Trade)))
            if kind == "spot":
                self.assertIn("Pong", names)
                self.assertTrue(all(e.bid is None and e.ask is None for e in evs if isinstance(e, A.Ticker)))   # тикер спота без bid/ask

    def test_ticker_snapshot_and_delta(self):
        a = adapter()
        tks = [e for r in fixture("perp") if (topic_of(r) or "").startswith("tickers.") for e in a.parse("perp", r["m"], r["t"])]
        snap = [t for t in tks if t.oi_coins is not None]
        self.assertTrue(snap)
        s0 = snap[0]
        self.assertEqual(s0.funding_h, 8.0)                                       # fundingIntervalHour из тикера
        self.assertIsNotNone(s0.mark)
        self.assertIsNotNone(s0.funding)
        partial = [t for t in tks if t.mark is None and t.bid is not None]
        self.assertTrue(partial)                                                  # дельта тикера: только изменившиеся поля, прочие None
        self.assertIsNone(partial[0].funding_h)

    def test_units_1000pepe_side_liquidation(self):
        a = adapter()
        tr = a.parse("perp", json.dumps({"topic": "publicTrade.1000PEPEUSDT", "type": "snapshot", "ts": 1,
                                         "data": [{"T": 1793784566000, "s": "1000PEPEUSDT", "S": "Sell", "v": "200", "p": "0.00508",
                                                   "i": "x1"}]}), 1.0)[0]
        self.assertAlmostEqual(tr.px, 5.08e-6, places=15)
        self.assertEqual((tr.coins, tr.side, tr.tid), (200000.0, -1, "x1"))
        tk = a.parse("perp", json.dumps({"topic": "tickers.1000PEPEUSDT", "type": "snapshot", "ts": 2,
                                         "data": {"symbol": "1000PEPEUSDT", "markPrice": "0.00508", "openInterest": "1000",
                                                  "fundingRate": "0.0001", "fundingIntervalHour": "4"}}), 1.0)[0]
        self.assertEqual((tk.oi_coins, tk.funding_h), (1e6, 4.0))
        self.assertAlmostEqual(tk.mark, 5.08e-6, places=15)
        liq = lambda side: json.dumps({"topic": "allLiquidation.BTCUSDT", "type": "snapshot", "ts": 3,     # noqa: E731
                                       "data": [{"T": 3, "s": "BTCUSDT", "S": side, "v": "0.5", "p": "78000"}]})
        self.assertEqual(a.parse("perp", liq("Buy"), 1.0)[0].liq_side, "long")    # S = Buy — ликвидирован лонг (документация v5)
        self.assertEqual(a.parse("perp", liq("Sell"), 1.0)[0].liq_side, "short")


class TestBook(unittest.TestCase):
    def test_property4_deep_1000_and_light_50(self):
        """Свойство 4 на живой фикстуре: снимок при подписке, далее u + 1 без пропусков; стакан не перекрещён; light — part=True."""
        a = adapter()
        for topic, tier in (("orderbook.1000.DOGEUSDT", "deep"), ("orderbook.50.DOGEUSDT", "light")):
            st, res = book_run(a, "perp", topic, tier=tier)
            self.assertEqual(st.status, "ok", (topic, st.reason))
            self.assertEqual(res, ["ok"] * len(res))
            self.assertGreater(len(res), 30)
            bb, ba = st.best()
            self.assertLess(bb, ba)
        evs = [e for r in fixture("perp") if topic_of(r) == "orderbook.50.DOGEUSDT" for e in a.parse("perp", r["m"], r["t"])]
        self.assertTrue(all(e.part for e in evs))
        evs = [e for r in fixture("perp") if topic_of(r) == "orderbook.1000.DOGEUSDT" for e in a.parse("perp", r["m"], r["t"])]
        self.assertFalse(any(e.part for e in evs))
        self.assertGreater(len(st.bids), 40)
        st, res = book_run(a, "spot", "orderbook.200.DOGEUSDT")
        self.assertEqual((st.status, res.count("gap")), ("ok", 0))

    def test_gap_and_u1_reset(self):
        a = adapter()
        st, res = book_run(a, "perp", "orderbook.1000.DOGEUSDT", drop=10)
        self.assertEqual((st.status, st.reason), ("resync", "gap"))
        self.assertEqual(res[9:], ["gap"] + ["ignored"] * (len(res) - 10))                                             # до новой подписки дельты не применяются
        # u = 1: биржа сбросила номера — дельта с u=1 даёт RESYNC, новый снимок (u=1) восстанавливает
        st2, _ = book_run(a, "perp", "orderbook.1000.DOGEUSDT")
        d1 = A.BookDelta("bybit_perp", "DOGE", 1, 1, None, 1, None, None, [], [])
        self.assertEqual(st2.apply_delta(d1, 1.0), "gap")
        snap = a.parse("perp", json.dumps({"topic": "orderbook.1000.DOGEUSDT", "type": "snapshot", "ts": 5,
                                           "data": {"s": "DOGEUSDT", "b": [["0.0750", "10"]], "a": [["0.0751", "10"]], "u": 1, "seq": 9}}),
                       2.0)[0]
        st2.apply_snapshot(snap, 2.0)
        self.assertEqual(st2.status, "ok")
        d2 = a.parse("perp", json.dumps({"topic": "orderbook.1000.DOGEUSDT", "type": "delta", "ts": 6,
                                         "data": {"s": "DOGEUSDT", "b": [["0.0749", "5"]], "a": [], "u": 2, "seq": 10}}), 2.1)[0]
        self.assertEqual(st2.apply_delta(d2, 2.1), "ok")

    def test_resub_restores_by_resubscribe(self):
        a = adapter()
        subs = a.plan("perp", ["DOGE"], [])
        texts = a.resub("perp", "DOGE", subs)
        ops = [json.loads(t) for t in texts]
        self.assertEqual([(o["op"], o["args"]) for o in ops], [("unsubscribe", ["orderbook.1000.DOGEUSDT"]),
                                                                 ("subscribe", ["orderbook.1000.DOGEUSDT"])])
        self.assertEqual(a.snapshot_cost("perp", 0), (None, 0))                  # снимок по WS — REST-веса нет


class TestPlanAndSubs(unittest.TestCase):
    def test_plan_tiers_and_valid_topics(self):
        a = adapter()
        subs = a.plan("perp", ["BTC"], ["PEPE"])
        self.assertEqual(sorted((s.topic, s.kind) for s in subs), [
            ("allLiquidation.1000PEPEUSDT", "liq"), ("allLiquidation.BTCUSDT", "liq"), ("orderbook.1000.BTCUSDT", "book"),
            ("orderbook.50.1000PEPEUSDT", "book_part"), ("publicTrade.1000PEPEUSDT", "trades"), ("publicTrade.BTCUSDT", "trades"),
            ("tickers.1000PEPEUSDT", "ticker"), ("tickers.BTCUSDT", "ticker")])
        spot = a.plan("spot", ["BTC"], ["DOGE"])
        self.assertFalse(any(s.kind == "liq" for s in spot))
        self.assertIn("orderbook.1000.BTCUSDT", [s.topic for s in spot])
        for t in ("orderbook.500.BTCUSDT", "orderbook.25.BTCUSDT", "kline.1.BTCUSDT", "publicTrade.btcusdt", ""):
            self.assertFalse(a.valid_topic(t), t)                                 # orderbook.500 у Bybit нет
        self.assertFalse(a.valid_topic("allLiquidation.BTCUSDT", "spot"))
        self.assertTrue(a.valid_topic("orderbook.1000.BTCUSDT"))

    def test_sub_msgs_chunks_of_10_and_reject_all(self):
        a = adapter()
        subs = a.plan("perp", ["BTC", "ETH", "DOGE"], ["PEPE"]) + [A.Sub("main", "orderbook.500.BTCUSDT", "book", "BTC")]
        msgs = [json.loads(m) for m in a.sub_msgs(subs)]
        self.assertEqual([len(m["args"]) for m in msgs], [10, 6])                 # по 10 тем; недопустимая тема не отправлена
        self.assertEqual(a.stats["invalid_topic"], 1)
        bad = a.parse("perp", json.dumps({"success": False, "ret_msg": "error:handler not found,topic:x", "conn_id": "c",
                                          "req_id": msgs[0]["req_id"], "op": "subscribe"}), 1.0)[0]
        self.assertFalse(bad.ok)
        self.assertEqual(len(bad.topics), 10)                                     # отклонён весь запрос — повтор по одной
        self.assertEqual(len(A.split_rejected(bad.topics)[0]), 10)
        ok = a.parse("perp", json.dumps({"success": True, "ret_msg": "", "req_id": msgs[1]["req_id"], "op": "subscribe"}), 1.0)[0]
        self.assertEqual((ok.ok, len(ok.topics)), (True, 6))
        self.assertEqual(json.loads(a.ping()), {"op": "ping"})
        for pong in ({"success": True, "ret_msg": "pong", "conn_id": "c", "req_id": "", "op": "ping"},
                     {"req_id": "", "op": "pong", "args": ["1793784566000"], "conn_id": "c"}):
            self.assertIsInstance(a.parse("spot", json.dumps(pong), 1.0)[0], A.Pong)


class BybitServer(F.FakeServer):
    """Поддельный Bybit на 127.0.0.1: одна отклонённая тема отклоняет весь запрос (как у биржи)."""

    async def on_text(self, peer, text):
        m = json.loads(text)
        op = m.get("op")
        if op == "ping":
            await peer.send(json.dumps({"success": True, "ret_msg": "pong", "conn_id": "c", "req_id": "", "op": "ping"}))
            return
        if op in ("subscribe", "unsubscribe"):
            args = list(m.get("args") or ())
            bad = [x for x in args if x in self.reject]
            if not bad and op == "subscribe":
                peer.topics.extend(x for x in args if x not in peer.topics)
                peer.sub_event.set()
            await peer.send(json.dumps({"success": not bad, "ret_msg": "" if not bad else "error:handler not found,topic:%s" % bad[0],
                                        "conn_id": "c", "req_id": m.get("req_id"), "op": op}))


class TestNetwork(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_topic_retried_one_by_one(self):
        _bootstrap.require_localhost()
        srv = await BybitServer(reject={"orderbook.1000.NOPEUSDT"}).start()
        self.addAsyncCleanup(srv.stop)
        cfg = copy.deepcopy(C.defaults())
        cfg["sync"].update({"ping_s": 1, "silent_s": 6})
        cfg["limits"]["sub_msgs_per_s"] = 10
        a = adapter()
        subs = a.plan("perp", ["BTC", "NOPE", "ETH"], [])
        status = []
        c = A.VenueConn(a, "perp", "main#0", srv.url, subs, on_status=lambda ev, info: status.append((ev, info)), cfg=cfg,
                        allow_plain=True, rng=random.Random(1))
        task = asyncio.ensure_future(c.run())
        end = asyncio.get_event_loop().time() + 8
        while asyncio.get_event_loop().time() < end and not (srv.peers and len(srv.peers[0].topics) >= len(subs) - 1
                                                             and c.stats["sub_rejected"]):
            await asyncio.sleep(0.02)
        await c.close()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):                            # noqa: BLE001
            pass
        self.assertEqual(sorted(srv.peers[0].topics), sorted(s.topic for s in subs if s.topic != "orderbook.1000.NOPEUSDT"))
        self.assertEqual(c.stats["sub_rejected"], 1)
        self.assertIn(("rejected", {"topic": "orderbook.1000.NOPEUSDT", "reason": "error:handler not found,topic:orderbook.1000.NOPEUSDT"}),
                      status)

    async def test_resync_by_resubscribe_within_10s(self):
        """Разрыв u -> RESYNC -> повторная подписка темы -> биржа присылает снимок -> стакан снова ok (≤ 10 с)."""
        _bootstrap.require_localhost()
        rows = [r["m"] for r in fixture("perp") if topic_of(r) == "orderbook.1000.DOGEUSDT"]
        snap_text = rows[0]

        async def script(peer):
            await F.wait_topics(peer, 1)
            for t in rows[0:6] + rows[7:9]:                                     # снимок, дельты, пропуск дельты rows[6]
                await peer.send(t)
            for _ in range(250):                                                # ждём unsubscribe + subscribe темы
                if sum(1 for x in peer.received if '"op":"subscribe"' in x) >= 2:
                    break
                await asyncio.sleep(0.02)
            await peer.send(snap_text)                                          # новый снимок после переподписки
            await asyncio.sleep(10)
        srv = await BybitServer(script=script).start()
        self.addAsyncCleanup(srv.stop)
        cfg = copy.deepcopy(C.defaults())
        cfg["sync"].update({"ping_s": 1, "silent_s": 6})
        a = adapter()
        subs = [s for s in a.plan("perp", ["DOGE"], []) if s.kind == "book"]
        st = a.book_state("perp", "DOGE", "deep")
        st.resync("start", 0.0)
        conn = {}

        def on_events(evs, t):
            for e in evs:
                if isinstance(e, A.BookSnapshot):
                    st.apply_snapshot(e, t)
                elif isinstance(e, A.BookDelta) and st.apply_delta(e, t) == "gap":
                    conn["c"].send_later(a.resub("perp", "DOGE", subs))
                    conn["t_gap"] = asyncio.get_event_loop().time()
        c = A.VenueConn(a, "perp", "main#0", srv.url, subs, on_events=on_events, cfg=cfg, allow_plain=True, rng=random.Random(1))
        conn["c"] = c
        task = asyncio.ensure_future(c.run())
        end = asyncio.get_event_loop().time() + 10
        while asyncio.get_event_loop().time() < end and not ("t_gap" in conn and st.status == "ok"):
            await asyncio.sleep(0.02)
        t_ok = asyncio.get_event_loop().time()
        await c.close()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):                            # noqa: BLE001
            pass
        self.assertIn("t_gap", conn)
        self.assertEqual(st.status, "ok")
        self.assertLess(t_ok - conn["t_gap"], 10.0)
        self.assertEqual(st.gaps, 1)


if __name__ == "__main__":
    unittest.main()
