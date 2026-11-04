"""R1.7, задача 12: адаптер Gate (perp и spot) — разбор живых фикстур задачи 2, стакан Order Book V2 (`obu`: снимок по WS, `U` = прошлый
`u` + 1, свойство 4) и разрыв -> RESYNC с восстановлением повторной подпиской, частичный `order_book`, `quanto_multiplier` (сделки, OI,
ликвидации), знак `size` ликвидаций (сверка с REST `liq_orders`), funding с интервалом тикера, пинг `futures.ping`/`spot.ping`
(Requirements 2.1, 5.1, 5.3, 5.5, 5.7, 5.9, 9.1). Внешней сети нет: REST подменён, WS — FakeServer на 127.0.0.1.
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
from tb.cmkt.ex import fakex as F
from tb.cmkt.ex import gate as G

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures_cmkt", "gate")


def fut(name, q, typ="direct", status="trading", fi=28800):
    return {"name": name, "type": typ, "quanto_multiplier": q, "order_price_round": "0.00001", "funding_interval": fi, "status": status,
            "in_delisting": False}


CONTRACTS = [fut("BTC_USDT", "0.0001"), fut("ETH_USDT", "0.01"), fut("DOGE_USDT", "10", fi=14400), fut("PEPE_USDT", "10000000"),
             fut("BIRB_USDT", "1"), fut("BTC_USD", "1", typ="inverse"), fut("OLD_USDT", "1", status="delisted")]
PAIRS = [{"id": p, "base": p.split("_")[0], "quote": p.split("_")[1], "trade_status": st, "precision": 5}
         for p, st in (("BTC_USDT", "tradable"), ("ETH_USDT", "tradable"), ("DOGE_USDT", "tradable"), ("BTC_USDC", "tradable"),
                       ("ETH_BTC", "tradable"), ("X_USDT", "untradable"))]


class FakeHttp(object):
    def __init__(self):
        self.calls = []

    def get_json(self, url, weight=1, kind="meta", timeout=None):
        self.calls.append(url)
        self.timeouts = getattr(self, "timeouts", []) + [timeout]
        if "/futures/usdt/contracts" in url:
            return CONTRACTS
        if "/spot/currency_pairs" in url:
            return PAIRS
        if "/spot/time" in url:
            return {"server_time": 1793784566123}
        raise A.HttpError(404, url)


def adapter():
    a = G.GateAdapter(C.defaults())
    a.load_meta(FakeHttp())
    return a


def fixture(kind):
    with open(os.path.join(FIX, kind + ".jsonl"), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def msg(r):
    try:
        return json.loads(r["m"])
    except (KeyError, ValueError):
        return {}


def obu_rows(kind):
    return [r for r in fixture(kind) if msg(r).get("channel", "").endswith(".obu") and msg(r).get("event") == "update"]


def book_run(a, kind, drop=None):
    st = a.book_state(kind, "DOGE", "deep")
    res, k = [], 0
    for r in obu_rows(kind):
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
    def test_meta_selection_and_quanto(self):
        a = G.GateAdapter(C.defaults())
        http = FakeHttp()
        got = a.load_meta(http)
        self.assertEqual(http.timeouts, [G.META_TIMEOUT_S, G.META_TIMEOUT_S])    # списки 1.1–1.4 МБ: свой предел, не 5 с
        only = FakeHttp()
        self.assertEqual(list(G.GateAdapter(C.defaults()).load_meta(only, kind="spot")), ["spot"])     # сборщик рынка — свой список
        self.assertEqual(len(only.calls), 1)
        from tb.cmkt import collector as CO
        col = CO.Collector("gate", "spot", C.defaults(), root=None, adapter=G.GateAdapter(C.defaults()))
        col.http = FakeHttp()
        self.assertEqual(list(col._call_meta(0.0)), ["spot"])                     # сборщик передаёт свой вид рынка
        self.assertEqual(len(col.http.calls), 1)
        self.assertEqual(sorted(i.sym for i in got["perp"]), ["BIRB", "BTC", "DOGE", "ETH", "PEPE"])     # inverse, delisted — нет
        self.assertEqual(sorted(i.sym for i in got["spot"]), ["BTC", "DOGE", "ETH"])
        self.assertEqual(a.meta.get("gate_perp", "BTC").contract_size, 0.0001)
        doge = a.meta.get("gate_perp", "DOGE")
        self.assertEqual((doge.contract_size, doge.funding_h), (10.0, 4.0))
        pepe = a.meta.get("gate_perp", "PEPE")
        self.assertEqual((pepe.native, pepe.coin_mult, pepe.contract_size), ("PEPE_USDT", 1.0, 1e7))
        self.assertEqual(a.server_time(FakeHttp()), 1793784566.123)

    def test_hosts_registry_no_rest_snapshot(self):
        cfg = C.defaults()
        a = ex.load("gate", cfg)
        self.assertEqual(a.urls("perp")["main"], "wss://fx-ws.gateio.ws/v4/ws/usdt")
        self.assertEqual(a.urls("spot")["main"], "wss://api.gateio.ws/ws/v4/")
        for h in a.hosts()["ws"] + a.hosts()["rest"]:
            self.assertTrue(C.host_allowed(cfg, "gate", h), h)
        self.assertEqual(a.snapshot_cost("perp", "core"), (None, 0))            # снимок по WS: REST-веса нет
        self.assertFalse(a.book_state("perp", "BTC").rest)
        self.assertEqual(A.subs_per_conn(cfg, "gate"), 100)
        self.assertEqual(json.loads(A.ping_text(a, "perp"))["channel"], "futures.ping")
        self.assertEqual(json.loads(A.ping_text(a, "spot"))["channel"], "spot.ping")
        c = A.VenueConn(a, "perp", "main#0", "ws://127.0.0.1:1/ws", [], allow_plain=True)
        self.assertEqual(c.client.extra_headers, {"X-Gate-Size-Decimal": "1"})        # заголовок рукопожатия фьючерсов
        self.assertEqual(json.loads(c.client.keepalive_text)["channel"], "futures.ping")
        self.assertEqual(A.VenueConn(a, "spot", "main#0", "ws://127.0.0.1:1/ws", [], allow_plain=True).client.extra_headers, {})


class TestFixtureParse(unittest.TestCase):
    def test_fixtures_without_parse_errors(self):
        for kind in ("perp", "spot"):
            a = adapter()
            evs = []
            for r in fixture(kind):
                evs.extend(a.parse(kind, r["m"], r["t"]))
            self.assertEqual(a.stats["unknown"], 0, kind)
            names = set(type(e).__name__ for e in evs)
            self.assertTrue({"Trade", "BookSnapshot", "BookDelta", "Ticker", "SubAck"} <= names, (kind, names))
            self.assertTrue(all(e.side in (1, -1) for e in evs if isinstance(e, A.Trade)))
            self.assertTrue(all(e.ok for e in evs if isinstance(e, A.SubAck)))
            parts = [e for e in evs if isinstance(e, A.BookSnapshot) and e.part]
            self.assertGreater(len(parts), 10)                                    # order_book 20 — частичный стакан снимками
            self.assertTrue(all(len(e.bids) == 20 and len(e.asks) == 20 for e in parts))
            full = [e for e in evs if isinstance(e, A.BookSnapshot) and not e.part]
            self.assertEqual(len(full), 1)
            self.assertEqual(len(full[0].bids), 400)
            if kind == "perp":
                lq = [e for e in evs if isinstance(e, A.Liquidation)]
                self.assertEqual(len(lq), 3)                                      # 4 события, одно с size 0 отброшено
                self.assertEqual(a.stats["liq_zero"], 1)

    def test_trades_units_and_side(self):
        a = adapter()
        tr = [e for r in fixture("perp") for e in a.parse("perp", r["m"], r["t"]) if isinstance(e, A.Trade)]
        raws = [t for r in fixture("perp") if msg(r).get("channel") == "futures.trades" and msg(r).get("event") == "update"
                for t in msg(r)["result"]]
        self.assertEqual(a.stats["size0"], sum(1 for t in raws if t["size"] == 0))   # фикстура без X-Gate-Size-Decimal: 6 из 70
        raws = [t for t in raws if t["size"] != 0]
        self.assertEqual(len(tr), len(raws))
        q = {"BTC_USDT": 1e-4, "ETH_USDT": 0.01, "DOGE_USDT": 10.0, "PEPE_USDT": 1e7}
        for e, t in zip(tr, raws):
            self.assertAlmostEqual(e.coins, abs(t["size"]) * q[t["contract"]], places=9)    # контракты × quanto_multiplier
            self.assertEqual(e.side, 1 if t["size"] > 0 else -1)                            # size > 0 — покупка тейкера
            self.assertAlmostEqual(e.t_ex, t["create_time_ms"] / 1000.0)
        self.assertTrue(any(e.side == 1 for e in tr) and any(e.side == -1 for e in tr))
        dec = a.parse("perp", json.dumps({"time": 1, "time_ms": 1, "channel": "futures.trades", "event": "update", "result": [
            {"id": 1, "size": "-0.1", "create_time_ms": 1793784568544, "price": "2315.65", "contract": "ETH_USDT"}]}), 1.0)[0]
        self.assertAlmostEqual(dec.coins, 0.001)                                  # с X-Gate-Size-Decimal: 0.1 контракта × 0.01
        self.assertEqual(dec.side, -1)
        self.assertEqual(a.ws_headers("perp"), {"X-Gate-Size-Decimal": "1"})
        self.assertIsNone(a.ws_headers("spot"))
        sp = [e for r in fixture("spot") for e in a.parse("spot", r["m"], r["t"]) if isinstance(e, A.Trade)]
        raw = next(msg(r)["result"] for r in fixture("spot") if msg(r).get("channel") == "spot.trades" and msg(r).get("event") == "update")
        self.assertEqual((sp[0].coins, sp[0].side), (float(raw["amount"]), {"buy": 1, "sell": -1}[raw["side"]]))
        self.assertAlmostEqual(sp[0].t_ex, float(raw["create_time_ms"]) / 1000.0)

    def test_tickers_oi_funding(self):
        a = adapter()
        tk = [e for r in fixture("perp") for e in a.parse("perp", r["m"], r["t"]) if isinstance(e, A.Ticker)]
        d = next(x for r in fixture("perp") if msg(r).get("channel") == "futures.tickers" and msg(r).get("event") == "update"
                 for x in msg(r)["result"] if x["contract"] == "DOGE_USDT")
        doge = next(e for e in tk if e.sym == "DOGE")
        self.assertAlmostEqual(doge.oi_coins, float(d["total_size"]) * 10.0)     # total_size (контракты) × quanto_multiplier
        self.assertEqual((doge.funding, doge.funding_h), (float(d["funding_rate"]), 8.0))   # интервал тикера (28 800 с)
        self.assertEqual((doge.mark, doge.index), (float(d["mark_price"]), float(d["index_price"])))
        self.assertEqual(doge.turn24_usd, float(d["volume_24h_quote"]))
        pepe = next(e for e in tk if e.sym == "PEPE")
        self.assertGreater(pepe.oi_coins, 1e12)                                   # PEPE: 1 контракт = 10 000 000 монет
        sp = [e for r in fixture("spot") for e in a.parse("spot", r["m"], r["t"]) if isinstance(e, A.Ticker)]
        self.assertTrue(sp and all(e.bid <= e.ask and e.oi_coins is None for e in sp))

    def test_liquidation_sign_matches_rest(self):
        """Сверка 2026-11-04: события WS фикстуры против REST /futures/usdt/liq_orders тех же секунд — WS size = order_size REST
        (BIRB −218 при size +218 позиции: продажа, ликвидирован лонг; PEPE +6 при order_size 6.8: покупка, ликвидирован шорт)."""
        a = adapter()
        lq = [e for r in fixture("perp") for e in a.parse("perp", r["m"], r["t"]) if isinstance(e, A.Liquidation)]
        birb = [e for e in lq if e.sym == "BIRB"]
        self.assertEqual([(e.liq_side, e.coins, e.px) for e in birb], [("long", 218.0, 0.08704), ("long", 434.0, 0.08693)])
        pepe = [e for e in lq if e.sym == "PEPE"]
        self.assertEqual([(e.liq_side, e.coins) for e in pepe], [("short", 6e7)])
        self.assertEqual(a.liq_partial, ())                                      # до 10 событий на контракт в секунду (живая проверка)


class TestBook(unittest.TestCase):
    def test_property4_obu_next_both_markets(self):
        a = adapter()
        for kind in ("perp", "spot"):
            st, res = book_run(a, kind)
            self.assertEqual(st.status, "ok", (kind, st.reason))
            self.assertEqual(res, ["ok"] * len(res), kind)
            self.assertGreater(len(res), 200)
            self.assertEqual(st.rule, "next")
            bb, ba = st.best()
            self.assertLess(bb, ba)
            self.assertGreater(len(st.bids), 300)

    def test_gap_gives_resync_and_resub(self):
        a = adapter()
        st, res = book_run(a, "spot", drop=50)
        self.assertEqual((st.status, st.reason), ("resync", "gap"))
        self.assertEqual(res[49], "gap")
        subs = a.plan("spot", ["DOGE"], [])
        ops = [json.loads(t) for t in a.resub("spot", "DOGE", subs)]
        self.assertEqual([(o["channel"], o["event"], o["payload"]) for o in ops],
                         [("spot.obu", "unsubscribe", ["ob.DOGE_USDT.400"]), ("spot.obu", "subscribe", ["ob.DOGE_USDT.400"])])

    def test_perp_levels_in_coins(self):
        a = adapter()
        st, _ = book_run(a, "perp")
        i = a.meta.get("gate_perp", "DOGE")
        bids, asks = st.levels(i.contract_size, i.coin_mult)
        raw = dict(st.bids)
        bb = max(raw)
        self.assertAlmostEqual(bids[0][1], raw[bb] * 10.0)                        # контракты × quanto_multiplier
        self.assertGreater(st.depth(i.contract_size, i.coin_mult), 1.0)

    def test_light_futures_order_book_dict_levels(self):
        a = adapter()
        st = a.book_state("perp", "DOGE", "light")
        self.assertEqual(st.rule, "replace")
        for r in fixture("perp"):
            if msg(r).get("channel") == "futures.order_book":
                for e in a.parse("perp", r["m"], r["t"]):
                    self.assertTrue(e.part)
                    st.apply_snapshot(e, r["t"])
        self.assertEqual(st.status, "ok")
        self.assertEqual(len(st.bids), 20)


class TestPlanAndSubs(unittest.TestCase):
    def test_plan_and_messages(self):
        a = adapter()
        subs = a.plan("perp", ["BTC", "ETH"], ["DOGE", "NOPE"])
        by = dict((s.topic, s.kind) for s in subs)
        self.assertEqual(by["futures.obu:ob.BTC_USDT.400"], "book")
        self.assertEqual(by["futures.order_book:DOGE_USDT"], "book_part")
        self.assertEqual(by[G.LIQ_TOPIC], "liq")
        self.assertEqual(len(subs), 3 * 3 + 1)
        msgs = [json.loads(m) for m in a.sub_msgs(subs + [A.Sub("main", "futures.obu:ob.BTC_USDT.20", "book", "BTC")])]
        self.assertEqual(a.stats["invalid_topic"], 1)
        ch = dict((m["channel"], m["payload"]) for m in msgs)
        self.assertEqual(ch["futures.obu"], ["ob.BTC_USDT.400", "ob.ETH_USDT.400"])
        self.assertEqual(ch["futures.trades"], ["BTC_USDT", "ETH_USDT", "DOGE_USDT"])
        self.assertEqual(ch["futures.order_book"], ["DOGE_USDT", "20", "0"])
        self.assertEqual(ch["futures.public_liquidates"], ["!all"])
        self.assertTrue(all(m["event"] == "subscribe" and isinstance(m["id"], int) and isinstance(m["time"], int) for m in msgs))
        sp = dict((json.loads(m)["channel"], json.loads(m)["payload"]) for m in a.sub_msgs(a.plan("spot", [], ["BTC"])))
        self.assertEqual(sp["spot.order_book"], ["BTC_USDT", "20", "100ms"])
        self.assertNotIn("futures.public_liquidates", sp)
        big = [A.Sub("main", "spot.trades:C%03d_USDT" % i, "trades", "X") for i in range(45)]
        self.assertEqual([len(json.loads(m)["payload"]) for m in a.sub_msgs(big)], [20, 20, 5])

    def test_acks_by_id_and_payload_errors_pong(self):
        a = adapter()
        t = a.sub_msgs([A.Sub("main", "futures.trades:BTC_USDT", "trades", "BTC"), A.Sub("main", "futures.trades:NOPE_USDT", "trades", "N")])
        rid = json.loads(t[0])["id"]
        err = a.parse("perp", json.dumps({"time": 1, "id": rid, "channel": "futures.trades", "event": "subscribe",
                                          "error": {"code": 2, "message": "unknown contract NOPE_USDT"}, "result": {"status": "failed"}}), 1)[0]
        self.assertEqual((err.ok, err.topics), (False, ("futures.trades:BTC_USDT", "futures.trades:NOPE_USDT")))
        self.assertEqual(A.split_rejected(err.topics)[0], [["futures.trades:BTC_USDT"], ["futures.trades:NOPE_USDT"]])
        ok = a.parse("spot", json.dumps({"time": 1, "channel": "spot.obu", "event": "subscribe", "payload": ["ob.BTC_USDT.400"],
                                         "result": {"status": "success"}}), 1)[0]
        self.assertEqual((ok.ok, ok.topics), (True, ("spot.obu:ob.BTC_USDT.400",)))
        self.assertIsInstance(a.parse("perp", json.dumps({"time": 1, "channel": "futures.pong", "event": "", "result": None}), 1)[0], A.Pong)
        self.assertIsInstance(a.parse("spot", json.dumps({"time": 1, "channel": "spot.pong", "event": "", "result": None}), 1)[0], A.Pong)


class GateServer(F.FakeServer):
    """Поддельный Gate: *.ping -> *.pong, подписка {channel, event, payload, id} с ответом по id."""

    async def on_text(self, peer, text):
        m = json.loads(text)
        ch = m.get("channel", "")
        if ch.endswith(".ping"):
            self.pings = getattr(self, "pings", 0) + 1
            await peer.send(json.dumps({"time": 1, "channel": ch[:-5] + ".pong", "event": "", "result": None}))
            return
        if m.get("event") in ("subscribe", "unsubscribe"):
            if m["event"] == "subscribe":
                peer.topics.extend("%s:%s" % (ch, p) for p in m["payload"])
            await peer.send(json.dumps({"time": 1, "id": m.get("id"), "channel": ch, "event": m["event"], "payload": m["payload"],
                                        "result": {"status": "success"}}))
            peer.sub_event.set()


class TestNetwork(unittest.IsolatedAsyncioTestCase):
    async def test_obu_gap_resync_by_resubscribe_and_ping(self):
        """Фикстура obu через VenueConn на 127.0.0.1: пропуск обновления -> RESYNC -> переподписка -> снимок full -> ok (≤ 10 с);
        пинг spot.ping -> spot.pong."""
        _bootstrap.require_localhost()
        rows = [r["m"] for r in obu_rows("spot")]
        snap = rows[0]

        async def script(peer):
            await F.wait_topics(peer, 1)
            for t in rows[0:12] + rows[13:16]:
                await peer.send(t)
            for _ in range(250):
                if sum(1 for x in peer.received if '"event":"subscribe"' in x) >= 2:
                    break
                await asyncio.sleep(0.02)
            await peer.send(snap)
            await asyncio.sleep(10)
        srv = await GateServer(script=script).start()
        self.addAsyncCleanup(srv.stop)
        cfg = copy.deepcopy(C.defaults())
        cfg["sync"].update({"ping_s": 0.2, "silent_s": 6})
        a = adapter()
        subs = [s for s in a.plan("spot", ["DOGE"], []) if s.kind == "book"]
        st = a.book_state("spot", "DOGE", "deep")
        box = {}

        def on_events(evs, t):
            for e in evs:
                if isinstance(e, A.BookSnapshot):
                    st.apply_snapshot(e, t)
                elif isinstance(e, A.BookDelta) and st.apply_delta(e, t) == "gap":
                    box["c"].send_later(a.resub("spot", "DOGE", subs))
                    box["t_gap"] = asyncio.get_event_loop().time()
        c = A.VenueConn(a, "spot", "main#0", srv.url, subs, on_events=on_events, cfg=cfg, allow_plain=True, rng=random.Random(1))
        box["c"] = c
        task = asyncio.ensure_future(c.run())
        loop = asyncio.get_event_loop()
        end = loop.time() + 10
        while loop.time() < end and not ("t_gap" in box and st.status == "ok" and getattr(srv, "pings", 0) >= 2):
            await asyncio.sleep(0.02)
        t_ok = loop.time()
        await c.close()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):                            # noqa: BLE001
            pass
        self.assertEqual(c.stats["parse_err"], 0)
        self.assertIn("t_gap", box)
        self.assertEqual((st.status, st.gaps), ("ok", 1))
        self.assertLess(t_ok - box["t_gap"], 10.0)
        self.assertGreaterEqual(getattr(srv, "pings", 0), 2)
        self.assertIsNotNone(c.live.last_pong)
        self.assertTrue(all(json.loads(x)["channel"] != "futures.ping" for x in srv.peers[0].received))


if __name__ == "__main__":
    unittest.main()
