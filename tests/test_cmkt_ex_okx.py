"""R1.7, задача 10: адаптер OKX (perp и spot) — разбор живых фикстур задачи 2, непрерывность `seqId`/`prevSeqId` (свойство 4), контрольная
сумма только при ненулевом значении, `ctVal`, OI, funding, ликвидации с дедупликацией и отбором по возрасту, уведомление 64010, порт 443
(Requirements 2.1, 3.4, 5.1, 5.2, 5.7, 5.9, 7.5, 9.1, 12.1). Внешней сети нет: REST подменён, WS — FakeServer на 127.0.0.1.
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
from tb.cmkt.ex import okx as O

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures_cmkt", "okx")


def swap(inst, ct_val, ccy, settle="USDT", ct_type="linear", state="live"):
    base, quote = inst.split("-")[:2]
    return {"instId": inst, "ctVal": ct_val, "ctValCcy": ccy, "ctType": ct_type, "settleCcy": settle, "state": state,
            "uly": "%s-%s" % (base, quote), "instFamily": "%s-%s" % (base, quote), "tickSz": "0.1"}


SWAPS = [swap("BTC-USDT-SWAP", "0.01", "BTC"), swap("ETH-USDT-SWAP", "0.1", "ETH"), swap("DOGE-USDT-SWAP", "1000", "DOGE"),
         swap("PEPE-USDT-SWAP", "10000000", "PEPE"), swap("ZEC-USDT-SWAP", "1", "ZEC"), swap("BTC-USDC-SWAP", "0.0001", "BTC", "USDC"),
         swap("BTC-USD-SWAP", "100", "USD", "BTC", "inverse"), swap("OLD-USDT-SWAP", "1", "OLD", state="suspend")]
SPOTS = [{"instId": i, "baseCcy": i.split("-")[0], "quoteCcy": i.split("-")[1], "state": "live", "tickSz": "0.01"}
         for i in ("BTC-USDT", "ETH-USDT", "DOGE-USDT", "PEPE-USDT", "BTC-USDC", "ETH-BTC")]


class FakeHttp(object):
    def __init__(self):
        self.calls = []

    def get_json(self, url, weight=1, kind="meta"):
        self.calls.append(url)
        if "instType=SWAP" in url:
            return {"code": "0", "data": SWAPS}
        if "instType=SPOT" in url:
            return {"code": "0", "data": SPOTS}
        if "/public/time" in url:
            return {"code": "0", "data": [{"ts": "1793784566123"}]}
        raise A.HttpError(404, url)


def adapter():
    a = O.OkxAdapter(C.defaults())
    a.load_meta(FakeHttp())
    return a


def fixture(kind):
    with open(os.path.join(FIX, kind + ".jsonl"), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def chan(r):
    try:
        m = json.loads(r["m"])
    except (KeyError, ValueError):
        return None, None
    a = m.get("arg") or {}
    return a.get("channel"), a.get("instId")


def book_run(a, kind, inst, drop=None):
    st = a.book_state(kind, "DOGE", "deep")
    res, k = [], 0
    for r in fixture(kind):
        ch, i = chan(r)
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
            res.append(st.apply_delta(e, r["t"], a.checksum))
    return st, res


class TestMeta(unittest.TestCase):
    def test_meta_selection_and_units(self):
        a = O.OkxAdapter(C.defaults())
        got = a.load_meta(FakeHttp())
        self.assertEqual(sorted(i.sym for i in got["perp"]), ["BTC", "DOGE", "ETH", "PEPE", "ZEC"])     # USDC, inverse, suspend — нет
        self.assertEqual(sorted(i.sym for i in got["spot"]), ["BTC", "DOGE", "ETH", "PEPE"])
        self.assertEqual(a.meta.get("okx_perp", "DOGE").contract_size, 1000.0)
        pepe = a.meta.get("okx_perp", "PEPE")
        self.assertEqual((pepe.native, pepe.coin_mult, pepe.contract_size), ("PEPE-USDT-SWAP", 1.0, 1e7))    # без префикса 1000
        self.assertEqual(a.server_time(FakeHttp()), 1793784566.123)

    def test_port_443_hosts_registry(self):
        cfg = C.defaults()
        a = ex.load("okx", cfg)
        self.assertEqual(a.urls("perp")["main"], "wss://ws.okx.com:443/ws/v5/public")
        self.assertIsNone(A.host_check(cfg, "okx", a.urls("spot")["main"]))
        for h in a.hosts()["ws"] + a.hosts()["rest"]:
            self.assertTrue(C.host_allowed(cfg, "okx", h), h)
        self.assertEqual(a.liq_partial, ("okx_perp",))
        self.assertEqual(O.SUB_REQ_PER_HOUR, 480)


class TestFixtureParse(unittest.TestCase):
    def test_fixtures_without_parse_errors_and_notice(self):
        for kind in ("perp", "spot"):
            a = adapter()
            evs = []
            for r in fixture(kind):
                evs.extend(a.parse(kind, r["m"], r["t"]))
            self.assertEqual(a.stats["unknown"], 0, kind)
            names = set(type(e).__name__ for e in evs)
            self.assertTrue({"Trade", "BookSnapshot", "BookDelta", "Ticker", "SubAck"} <= names, (kind, names))
            self.assertTrue(all(e.side in (1, -1) for e in evs if isinstance(e, A.Trade)))
            self.assertTrue(all(len(lv) == 2 for e in evs if isinstance(e, (A.BookSnapshot, A.BookDelta)) for lv in e.bids + e.asks))
            if kind == "perp":
                self.assertEqual(a.stats["notice"], 1)                            # 64010 (порт 8443) — учтено, разбор не рвётся
                self.assertEqual(a.stats["liq_old"], 1)                           # повтор события возрастом ~20 ч отброшен
                oi = [e for e in evs if isinstance(e, A.Ticker) and e.oi_coins is not None]
                self.assertTrue(oi)
                fr = [e for e in evs if isinstance(e, A.Ticker) and e.funding is not None]
                self.assertTrue(fr and all(e.funding_h == 8.0 for e in fr))       # nextFundingTime − fundingTime
                self.assertTrue(any(e.mark is not None for e in evs if isinstance(e, A.Ticker)))

    def test_units_ctval_and_oi(self):
        a = adapter()
        tr = [e for r in fixture("perp") if chan(r) == ("trades", "ETH-USDT-SWAP") for e in a.parse("perp", r["m"], r["t"])
              if isinstance(e, A.Trade)]
        self.assertTrue(tr)
        raw = json.loads(next(r["m"] for r in fixture("perp") if chan(r) == ("trades", "ETH-USDT-SWAP") and '"data"' in r["m"]))
        self.assertAlmostEqual(tr[0].coins, float(raw["data"][0]["sz"]) * 0.1, places=9)    # sz × ctVal
        sp = a.parse("spot", json.dumps({"arg": {"channel": "trades", "instId": "DOGE-USDT"},
                                         "data": [{"instId": "DOGE-USDT", "tradeId": "1", "px": "0.075", "sz": "1333", "side": "sell",
                                                   "ts": "1793784566709"}]}), 1.0)[0]
        self.assertEqual((sp.coins, sp.side), (1333.0, -1))                       # спот — в монетах
        oi = a.parse("perp", json.dumps({"arg": {"channel": "open-interest", "instId": "BTC-USDT-SWAP"},
                                         "data": [{"instId": "BTC-USDT-SWAP", "oi": "3516586.12", "oiCcy": "35165.8612", "ts": "1"}]}), 1.0)[0]
        self.assertAlmostEqual(oi.oi_coins, 35165.8612, places=6)
        # PEPE без множителя: размер контракта 10 000 000 монет
        pe = a.parse("perp", json.dumps({"arg": {"channel": "trades", "instId": "PEPE-USDT-SWAP"},
                                         "data": [{"instId": "PEPE-USDT-SWAP", "tradeId": "2", "px": "0.0000050791", "sz": "1.1",
                                                   "side": "buy", "ts": "1"}]}), 1.0)[0]
        self.assertAlmostEqual(pe.coins, 1.1e7, places=3)
        self.assertAlmostEqual(pe.px, 5.0791e-6, places=15)


class TestBook(unittest.TestCase):
    def test_property4_seqid_continuity_both_markets(self):
        a = adapter()
        for kind, inst in (("perp", "DOGE-USDT-SWAP"), ("spot", "DOGE-USDT")):
            st, res = book_run(a, kind, inst)
            self.assertEqual(st.status, "ok", (kind, st.reason))
            self.assertEqual(res, ["ok"] * len(res), kind)
            self.assertGreater(len(res), 90)
            bb, ba = st.best()
            self.assertLess(bb, ba)
            self.assertIsInstance(next(iter(st.bids)), str)                       # исходные строки уровней (контрольная сумма)
            self.assertGreater(len(st.bids), 300)

    def test_gap_gives_resync(self):
        a = adapter()
        st, res = book_run(a, "perp", "DOGE-USDT-SWAP", drop=30)
        self.assertEqual((st.status, st.reason), ("resync", "gap"))

    def test_checksum_zero_skipped_nonzero_checked(self):
        a = adapter()
        st, _ = book_run(a, "perp", "DOGE-USDT-SWAP")
        seq = st.seq
        good = a.checksum(st)
        self.assertNotEqual(good, 0)
        d = lambda s, cs: A.BookDelta("okx_perp", "DOGE", 1, 1, None, s + 1, s, cs, [], [])     # noqa: E731
        self.assertEqual(st.apply_delta(d(seq, 0), 1.0, a.checksum), "ok")      # 0 — не проверяется
        self.assertEqual(st.apply_delta(d(seq + 1, good), 1.0, a.checksum), "ok")
        self.assertEqual(st.apply_delta(d(seq + 2, good + 1), 1.0, a.checksum), "gap")
        self.assertEqual((st.status, st.reason), ("resync", "checksum"))
        # CRC по формату OKX: bid:size:ask:size по 25 уровням
        import binascii
        v = binascii.crc32(b"3366.1:7:3366.8:9:3366:6:3368:8") & 0xFFFFFFFF
        self.assertEqual(A.crc32_levels([("3366.1", "7"), ("3366", "6")], [("3366.8", "9"), ("3368", "8")]),
                         v - (1 << 32) if v >= (1 << 31) else v)

    def test_resub_books(self):
        a = adapter()
        subs = a.plan("perp", ["DOGE"], [])
        ops = [json.loads(t) for t in a.resub("perp", "DOGE", subs)]
        self.assertEqual([(o["op"], o["args"]) for o in ops], [("unsubscribe", [{"channel": "books", "instId": "DOGE-USDT-SWAP"}]),
                                                                 ("subscribe", [{"channel": "books", "instId": "DOGE-USDT-SWAP"}])])


class TestLiquidations(unittest.TestCase):
    def msg(self, inst, ts_ms, sz="11", pos="long"):
        return json.dumps({"arg": {"channel": "liquidation-orders", "instType": "SWAP"},
                           "data": [{"instId": inst, "instType": "SWAP", "uly": "x", "details": [
                               {"bkLoss": "0", "bkPx": "0.075", "posSide": pos, "side": "sell" if pos == "long" else "buy", "sz": sz,
                                "ts": str(ts_ms)}]}]})

    def test_dedup_age_and_usdc(self):
        a = adapter()
        a.offset_s = 40.2                                                         # часы биржи впереди локальных (exchanges.md, п. 0)
        t_rx = 1793784526.0
        ts = int((t_rx + 40.2 - 2) * 1000)
        l1 = a.parse("perp", self.msg("DOGE-USDT-SWAP", ts), t_rx)
        self.assertEqual(len(l1), 1)
        self.assertEqual((l1[0].liq_side, l1[0].coins), ("long", 11000.0))      # sz × ctVal
        self.assertEqual(a.parse("perp", self.msg("DOGE-USDT-SWAP", ts), t_rx + 1), [])       # повтор (instId, ts, sz)
        self.assertEqual(a.stats["liq_dup"], 1)
        self.assertEqual(len(a.parse("perp", self.msg("DOGE-USDT-SWAP", ts, "12"), t_rx)), 1)  # другой размер — другое событие
        self.assertEqual(a.parse("perp", self.msg("DOGE-USDT-SWAP", ts - 3600 * 1000), t_rx), [])   # старше LIQ_MAX_AGE_S
        self.assertEqual(a.stats["liq_old"], 1)
        self.assertEqual(a.parse("perp", self.msg("BTC-USDC-SWAP", ts), t_rx), [])
        self.assertEqual(a.parse("perp", self.msg("DOGE-USDT-SWAP", ts + 5, pos="short"), t_rx)[0].liq_side, "short")


class TestPlanAndSubs(unittest.TestCase):
    def test_plan_and_sub_msgs(self):
        a = adapter()
        subs = a.plan("perp", ["BTC"], ["DOGE"])
        by = dict((s.topic, s.kind) for s in subs)
        self.assertEqual(by["books:BTC-USDT-SWAP"], "book")
        self.assertEqual(by["books:DOGE-USDT-SWAP"], "book_part")              # books (400) и для light
        self.assertEqual(by["open-interest:DOGE-USDT-SWAP"], "oi")
        self.assertEqual(by[O.LIQ_TOPIC], "liq")
        self.assertEqual(len(subs), 2 * 6 + 1)
        spot = a.plan("spot", ["BTC"], [])
        self.assertEqual(sorted(s.topic for s in spot), ["books:BTC-USDT", "tickers:BTC-USDT", "trades:BTC-USDT"])
        msgs = [json.loads(m) for m in a.sub_msgs(subs + [A.Sub("main", "books5:BTC-USDT-SWAP", "book", "BTC")])]
        self.assertEqual([len(m["args"]) for m in msgs], [13])
        self.assertEqual(a.stats["invalid_topic"], 1)
        self.assertIn({"channel": "liquidation-orders", "instType": "SWAP"}, msgs[0]["args"])
        big = [A.Sub("main", "trades:C%03d-USDT" % i, "trades", "X") for i in range(45)]
        self.assertEqual([len(json.loads(m)["args"]) for m in a.sub_msgs(big)], [20, 20, 5])
        ack = a.parse("perp", json.dumps({"event": "subscribe", "arg": {"channel": "books", "instId": "BTC-USDT-SWAP"}, "connId": "c"}), 1)[0]
        self.assertEqual((ack.ok, ack.topics), (True, ("books:BTC-USDT-SWAP",)))
        err = a.parse("perp", json.dumps({"event": "error", "code": "60018", "connId": "c",
                                          "msg": "Wrong URL or channel:tickers,instId:NOPE-USDT-SWAP doesn't exist."}), 1)[0]
        self.assertEqual((err.ok, err.topics), (False, ("tickers:NOPE-USDT-SWAP",)))
        self.assertEqual(A.split_rejected(err.topics), ([], ["tickers:NOPE-USDT-SWAP"]))
        self.assertEqual(a.ping(), "ping")
        self.assertIsInstance(a.parse("perp", "pong", 1.0)[0], A.Pong)


class TestNetwork(unittest.IsolatedAsyncioTestCase):
    async def test_replay_text_ping_pong(self):
        """Фикстура через VenueConn на 127.0.0.1 и живость по текстовому ping/pong OKX."""
        _bootstrap.require_localhost()
        rows = [r["m"] for r in fixture("spot")]

        class OkxServer(F.FakeServer):
            async def on_text(self, peer, text):
                if text == "ping":
                    self.pings = getattr(self, "pings", 0) + 1
                    await peer.send("pong")
                    return
                m = json.loads(text)
                for arg in m.get("args") or ():
                    peer.topics.append(arg)
                    await peer.send(json.dumps({"event": m["op"], "arg": arg, "connId": "c"}))
                peer.sub_event.set()

        async def script(peer):
            await F.wait_topics(peer, 3)
            for t in rows:
                await peer.send(t)
            await asyncio.sleep(10)
        srv = await OkxServer(script=script).start()
        self.addAsyncCleanup(srv.stop)
        cfg = copy.deepcopy(C.defaults())
        cfg["sync"].update({"ping_s": 0.2, "silent_s": 6})
        a = adapter()
        events = []
        c = A.VenueConn(a, "spot", "main#0", srv.url, a.plan("spot", ["DOGE"], []), on_events=lambda evs, t: events.extend(evs), cfg=cfg,
                        allow_plain=True, rng=random.Random(1))
        task = asyncio.ensure_future(c.run())
        end = asyncio.get_event_loop().time() + 6
        while asyncio.get_event_loop().time() < end and not (c.stats["msgs"] >= len(rows) + 3 and getattr(srv, "pings", 0) >= 2):
            await asyncio.sleep(0.02)
        alive = c.alive()
        await c.close()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):                            # noqa: BLE001
            pass
        self.assertEqual(c.stats["parse_err"], 0)
        self.assertGreaterEqual(getattr(srv, "pings", 0), 2)
        self.assertTrue(alive)
        self.assertTrue(any(isinstance(e, A.BookDelta) for e in events))


if __name__ == "__main__":
    unittest.main()
