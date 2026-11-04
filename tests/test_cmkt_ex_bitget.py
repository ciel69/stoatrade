"""R1.7, задача 11: адаптер Bitget (perp и spot) — разбор живых фикстур задачи 2, непрерывность `seq`/`pseq` (свойство 4) и разрыв ->
RESYNC, восстановление повторной подпиской, `books15` как частичный стакан, история сделок при подписке, OI `holdingAmount`, funding
с интервалом из `contracts`, ликвидаций нет, свой текстовый пинг (Requirements 2.1, 5.1, 5.2, 5.9, 9.1, 12.1). Внешней сети нет: REST
подменён, WS — FakeServer на 127.0.0.1.
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
from tb.cmkt.ex import bitget as B
from tb.cmkt.ex import fakex as F

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures_cmkt", "bitget")


def contract(sym, base, quote="USDT", st="perpetual", status="normal", fi="8"):
    return {"symbol": sym, "baseCoin": base, "quoteCoin": quote, "symbolType": st, "symbolStatus": status, "fundInterval": fi,
            "pricePlace": "5", "priceEndStep": "1", "sizeMultiplier": "1", "supportMarginCoins": [quote]}


CONTRACTS = [contract("BTCUSDT", "BTC"), contract("ETHUSDT", "ETH", fi="8"), contract("DOGEUSDT", "DOGE", fi="4"),
             contract("PEPEUSDT", "PEPE"), contract("BTCUSDT_261225", "BTC", st="delivery"), contract("OLDUSDT", "OLD", status="off"),
             contract("BTCPERP", "BTC", quote="USDC")]
SPOTS = [{"symbol": s, "baseCoin": b, "quoteCoin": q, "status": st, "pricePrecision": "4"}
         for s, b, q, st in (("BTCUSDT", "BTC", "USDT", "online"), ("ETHUSDT", "ETH", "USDT", "online"), ("DOGEUSDT", "DOGE", "USDT", "online"),
                             ("BTCUSDC", "BTC", "USDC", "online"), ("ETHBTC", "ETH", "BTC", "online"), ("XUSDT", "X", "USDT", "offline"))]


class FakeHttp(object):
    def __init__(self):
        self.calls = []

    def get_json(self, url, weight=1, kind="meta"):
        self.calls.append(url)
        if "/mix/market/contracts" in url:
            return {"code": "00000", "data": CONTRACTS}
        if "/spot/public/symbols" in url:
            return {"code": "00000", "data": SPOTS}
        if "/public/time" in url:
            return {"code": "00000", "data": {"serverTime": "1793784566123"}}
        raise A.HttpError(404, url)


def adapter():
    a = B.BitgetAdapter(C.defaults())
    a.load_meta(FakeHttp())
    return a


def fixture(kind):
    with open(os.path.join(FIX, kind + ".jsonl"), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def chan(r):
    try:
        m = json.loads(r["m"])
    except (KeyError, ValueError):
        return None, None, None
    a = m.get("arg") or {}
    return a.get("channel"), a.get("instId"), m.get("action")


def book_run(a, kind, inst, drop=None):
    st = a.book_state(kind, "DOGE", "deep")
    res, k = [], 0
    for r in fixture(kind):
        ch, i, _ = chan(r)
        if ch != "books" or i != inst:
            continue
        for e in a.parse(kind, r["m"], r["t"]):
            if isinstance(e, A.SubAck):
                continue
            if isinstance(e, A.BookSnapshot):
                st.apply_snapshot(e, r["t"])
                continue
            k += 1
            if drop is not None and k == drop:
                continue
            res.append(st.apply_delta(e, r["t"]))
    return st, res


class TestMeta(unittest.TestCase):
    def test_meta_selection_units_and_time(self):
        a = B.BitgetAdapter(C.defaults())
        got = a.load_meta(FakeHttp())
        self.assertEqual(sorted(i.sym for i in got["perp"]), ["BTC", "DOGE", "ETH", "PEPE"])   # срочный, off, USDC — нет
        self.assertEqual(sorted(i.sym for i in got["spot"]), ["BTC", "DOGE", "ETH"])
        doge = a.meta.get("bitget_perp", "DOGE")
        self.assertEqual((doge.contract_size, doge.coin_mult, doge.funding_h), (1.0, 1.0, 4.0))   # размеры в монетах; fundInterval
        self.assertAlmostEqual(doge.tick, 1e-5)
        self.assertEqual(a.meta.get("bitget_perp", "PEPE").native, "PEPEUSDT")                     # без префикса 1000
        self.assertEqual(a.server_time(FakeHttp()), 1793784566.123)

    def test_hosts_registry_and_contract(self):
        cfg = C.defaults()
        a = ex.load("bitget", cfg)
        self.assertEqual(a.urls("perp"), {"mix": "wss://ws.bitget.com/v2/ws/public"})
        self.assertEqual(a.urls("spot"), {"spot": "wss://ws.bitget.com/v2/ws/public"})
        for h in a.hosts()["ws"] + a.hosts()["rest"]:
            self.assertTrue(C.host_allowed(cfg, "bitget", h), h)
        self.assertEqual(a.liq_partial, ())
        self.assertEqual(a.checksum(a.book_state("perp", "BTC")), None)                           # поля checksum нет — не проверяется
        self.assertEqual(A.subs_per_conn(cfg, "bitget"), 50)
        self.assertEqual(a.ping(), "ping")
        self.assertEqual(A.ping_text(a, "perp"), "ping")


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
            self.assertFalse(any(isinstance(e, A.Liquidation) for e in evs))
            self.assertTrue(all(e.side in (1, -1) for e in evs if isinstance(e, A.Trade)))
            self.assertTrue(all(len(lv) == 2 for e in evs if isinstance(e, (A.BookSnapshot, A.BookDelta)) for lv in e.bids + e.asks))
            self.assertTrue(all(e.checksum is None for e in evs if isinstance(e, A.BookDelta)))
            self.assertTrue(all(e.ok for e in evs if isinstance(e, A.SubAck)))
            parts = [e for e in evs if isinstance(e, A.BookSnapshot) and e.part]
            self.assertGreater(len(parts), 10)                                    # books15 — частичный стакан снимками
            self.assertTrue(all(len(e.bids) <= 15 and len(e.asks) <= 15 for e in parts))
            # история сделок при подписке (snapshot) отброшена: все сделки — из update и не старше подписки
            n_hist = sum(len(json.loads(r["m"])["data"]) for r in fixture(kind) if chan(r)[0] == "trade" and chan(r)[2] == "snapshot")
            self.assertEqual(a.stats["hist"], n_hist)
            self.assertGreater(n_hist, 10)
            n_upd = sum(len(json.loads(r["m"])["data"]) for r in fixture(kind) if chan(r)[0] == "trade" and chan(r)[2] == "update")
            self.assertEqual(sum(1 for e in evs if isinstance(e, A.Trade)), n_upd)

    def test_ticker_oi_funding_and_units(self):
        a = adapter()
        tks = [e for r in fixture("perp") if chan(r)[0] == "ticker" for e in a.parse("perp", r["m"], r["t"]) if isinstance(e, A.Ticker)]
        doge = [e for e in tks if e.sym == "DOGE"]
        self.assertTrue(doge)
        raw = json.loads(next(r["m"] for r in fixture("perp") if chan(r) == ("ticker", "DOGEUSDT", "snapshot")))["data"][0]
        t = doge[0]
        self.assertEqual(t.oi_coins, float(raw["holdingAmount"]))                  # OI в монетах
        self.assertEqual((t.funding, t.funding_h), (float(raw["fundingRate"]), 4.0))   # интервал — из contracts
        self.assertEqual((t.mark, t.bid, t.ask), (float(raw["markPrice"]), float(raw["bidPr"]), float(raw["askPr"])))
        self.assertEqual(t.turn24_usd, float(raw["quoteVolume"]))
        self.assertAlmostEqual(t.t_ex, float(raw["ts"]) / 1000.0)
        sp = [e for r in fixture("spot") if chan(r)[0] == "ticker" for e in a.parse("spot", r["m"], r["t"]) if isinstance(e, A.Ticker)]
        self.assertTrue(sp and all(e.oi_coins is None and e.funding is None and e.bid < e.ask for e in sp))
        tr = a.parse("spot", json.dumps({"action": "update", "arg": {"instType": "SPOT", "channel": "trade", "instId": "DOGEUSDT"},
                                         "data": [{"ts": "1793784566764", "price": "0.07504", "size": "486.0452", "side": "sell",
                                                   "tradeId": "7"}], "ts": 1}), 1.0)[0]
        self.assertEqual((tr.coins, tr.side, tr.px, tr.tid), (486.0452, -1, 0.07504, "7"))


class TestBook(unittest.TestCase):
    def test_property4_seq_pseq_both_markets(self):
        a = adapter()
        for kind in ("perp", "spot"):
            st, res = book_run(a, kind, "DOGEUSDT")
            self.assertEqual(st.status, "ok", (kind, st.reason))
            self.assertEqual(res, ["ok"] * len(res), kind)
            self.assertGreater(len(res), 200)
            bb, ba = st.best()
            self.assertLess(bb, ba)
            self.assertGreater(len(st.bids), 100)

    def test_gap_gives_resync_and_resub(self):
        a = adapter()
        st, res = book_run(a, "perp", "DOGEUSDT", drop=40)
        self.assertEqual((st.status, st.reason), ("resync", "gap"))
        self.assertEqual(res[39], "gap")
        subs = a.plan("perp", ["DOGE"], [])
        ops = [json.loads(t) for t in a.resub("perp", "DOGE", subs)]
        arg = {"instType": "USDT-FUTURES", "channel": "books", "instId": "DOGEUSDT"}
        self.assertEqual([(o["op"], o["args"]) for o in ops], [("unsubscribe", [arg]), ("subscribe", [arg])])

    def test_light_books15_replace(self):
        a = adapter()
        st = a.book_state("spot", "DOGE", "light")
        self.assertEqual(st.rule, "replace")
        n = 0
        for r in fixture("spot"):
            if chan(r)[0] == "books15" and chan(r)[2]:
                for e in a.parse("spot", r["m"], r["t"]):
                    st.apply_snapshot(e, r["t"])
                    n += 1
        self.assertGreater(n, 10)
        self.assertEqual(st.status, "ok")
        self.assertLessEqual(len(st.bids), 15)


class TestPlanAndSubs(unittest.TestCase):
    def test_plan_tiers_and_messages(self):
        a = adapter()
        subs = a.plan("perp", ["BTC"], ["DOGE", "NOPE"])
        by = dict((s.topic, (s.kind, s.conn_key)) for s in subs)
        self.assertEqual(by["books:BTCUSDT"], ("book", "mix"))
        self.assertEqual(by["books15:DOGEUSDT"], ("book_part", "mix"))
        self.assertEqual(len(subs), 6)                                            # NOPE не в листинге; liq нет
        self.assertFalse(any(s.kind in ("liq", "oi") for s in subs))
        msgs = [json.loads(m) for m in a.sub_msgs(subs + [A.Sub("mix", "books5:BTCUSDT", "book", "BTC")])]
        self.assertEqual([len(m["args"]) for m in msgs], [6])
        self.assertEqual(a.stats["invalid_topic"], 1)
        self.assertIn({"instType": "USDT-FUTURES", "channel": "ticker", "instId": "DOGEUSDT"}, msgs[0]["args"])
        sp = [json.loads(m) for m in a.sub_msgs(a.plan("spot", ["BTC"], []))]
        self.assertTrue(all(x["instType"] == "SPOT" for x in sp[0]["args"]))
        big = [A.Sub("mix", "trade:C%03dUSDT" % i, "trades", "X") for i in range(45)]
        self.assertEqual([len(json.loads(m)["args"]) for m in a.sub_msgs(big)], [20, 20, 5])
        self.assertTrue(all(len(m) <= 4096 for m in a.sub_msgs(big)))
        # планировщик: 50 тем на соединение
        pl = A.SubPlanner(A.subs_per_conn(C.defaults(), "bitget"))
        out = pl.update(a.plan("perp", ["BTC", "ETH", "DOGE", "PEPE"] * 1, []) + big)
        self.assertEqual(pl.counts()["max_per_conn"], 50)
        self.assertEqual(len(out["open"]), 2)

    def test_acks_errors_pong(self):
        a = adapter()
        ok = a.parse("perp", json.dumps({"event": "subscribe", "arg": {"instType": "USDT-FUTURES", "channel": "books", "instId": "BTCUSDT"}}),
                     1.0)[0]
        self.assertEqual((ok.ok, ok.topics), (True, ("books:BTCUSDT",)))
        err = a.parse("perp", json.dumps({"event": "error", "code": 30001, "op": "subscribe",
                                          "arg": {"instType": "USDT-FUTURES", "channel": "ticker", "instId": "NOPEUSDT"},
                                          "msg": "instType:USDT-FUTURES,channel:ticker,instId:NOPEUSDT doesn't exist"}), 1.0)[0]
        self.assertEqual((err.ok, err.topics), (False, ("ticker:NOPEUSDT",)))
        err2 = a.parse("perp", json.dumps({"event": "error", "code": 30001,
                                           "msg": "instType:USDT-FUTURES,channel:books,instId:NOPEUSDT doesn't exist"}), 1.0)[0]
        self.assertEqual(err2.topics, ("books:NOPEUSDT",))
        self.assertIsInstance(a.parse("perp", "pong", 1.0)[0], A.Pong)


class BitgetServer(F.FakeServer):
    """Поддельный Bitget: текстовый ping -> pong, подписка аргументами {instType, channel, instId}."""

    async def on_text(self, peer, text):
        if text == "ping":
            self.pings = getattr(self, "pings", 0) + 1
            if self.answer_ping:
                await peer.send("pong")
            return
        m = json.loads(text)
        for arg in m.get("args") or ():
            t = "%s:%s" % (arg["channel"], arg["instId"])
            if m["op"] == "subscribe":
                peer.topics.append(t)
            await peer.send(json.dumps({"event": m["op"], "arg": arg}))
        peer.sub_event.set()


class TestNetwork(unittest.IsolatedAsyncioTestCase):
    async def _conn(self, srv, a, subs, on_events=None, ping_s=0.2, silent_s=6):
        cfg = copy.deepcopy(C.defaults())
        cfg["sync"].update({"ping_s": ping_s, "silent_s": silent_s})
        c = A.VenueConn(a, "perp", "mix#0", srv.url, subs, on_events=on_events, cfg=cfg, allow_plain=True, rng=random.Random(1))
        return c, asyncio.ensure_future(c.run())

    async def _stop(self, c, task):
        await c.close()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):                            # noqa: BLE001
            pass

    async def test_text_ping_pong_liveness(self):
        """Живость по pong на свой текстовый ping (у ccxt.pro Bitget рвался на пинг-понге): тихий рынок остаётся живым."""
        _bootstrap.require_localhost()
        srv = await BitgetServer().start()
        self.addAsyncCleanup(srv.stop)
        a = adapter()
        c, task = await self._conn(srv, a, a.plan("perp", ["BTC"], []), ping_s=0.2, silent_s=1.0)
        loop = asyncio.get_event_loop()
        end = loop.time() + 4
        while loop.time() < end and getattr(srv, "pings", 0) < 8:
            await asyncio.sleep(0.05)
        alive = c.alive()
        await self._stop(c, task)
        self.assertGreaterEqual(getattr(srv, "pings", 0), 8)                    # дольше silent_s без рыночных сообщений
        self.assertTrue(alive)
        self.assertIsNotNone(c.live.last_pong)

    async def test_gap_resync_by_resubscribe(self):
        """Фикстура через VenueConn на 127.0.0.1: пропуск обновления -> RESYNC -> переподписка books -> новый снимок -> ok (≤ 10 с)."""
        _bootstrap.require_localhost()
        rows = [r["m"] for r in fixture("perp") if chan(r)[:2] == ("books", "DOGEUSDT") and chan(r)[2]]
        snap = rows[0]

        async def script(peer):
            await F.wait_topics(peer, 1)
            for t in rows[0:10] + rows[11:14]:
                await peer.send(t)
            for _ in range(250):
                if sum(1 for x in peer.received if '"op":"subscribe"' in x) >= 2:
                    break
                await asyncio.sleep(0.02)
            await peer.send(snap)
            await asyncio.sleep(10)
        srv = await BitgetServer(script=script).start()
        self.addAsyncCleanup(srv.stop)
        a = adapter()
        subs = [s for s in a.plan("perp", ["DOGE"], []) if s.kind == "book"]
        st = a.book_state("perp", "DOGE", "deep")
        box = {}

        def on_events(evs, t):
            for e in evs:
                if isinstance(e, A.BookSnapshot):
                    st.apply_snapshot(e, t)
                elif isinstance(e, A.BookDelta) and st.apply_delta(e, t) == "gap":
                    box["c"].send_later(a.resub("perp", "DOGE", subs))
                    box["t_gap"] = asyncio.get_event_loop().time()
        c, task = await self._conn(srv, a, subs, on_events)
        box["c"] = c
        loop = asyncio.get_event_loop()
        end = loop.time() + 10
        while loop.time() < end and not ("t_gap" in box and st.status == "ok"):
            await asyncio.sleep(0.02)
        t_ok = loop.time()
        await self._stop(c, task)
        self.assertEqual(c.stats["parse_err"], 0)
        self.assertIn("t_gap", box)
        self.assertEqual((st.status, st.gaps), ("ok", 1))
        self.assertLess(t_ok - box["t_gap"], 10.0)


if __name__ == "__main__":
    unittest.main()
