"""R1.7, задача 13: адаптер Coinbase (spot: `ticker`, `matches`) — разбор живой фикстуры задачи 2, сторона агрессора (`side` `matches` —
сторона мейкера, сверка с `ticker` той же сделки), `last_match` не в CVD, только пары к USD, стакана нет (`nobook`), курс USDT-USD из
`ticker` и запасной REST раз в 10 с при котировке старше 10 с, публикация курса полем `fx` без заглядывания вперёд, ошибки подписки
(Requirements 2.1, 2.3, 5.9, 10.3). Внешней сети нет: REST подменён, WS — FakeServer на 127.0.0.1.
"""
import asyncio
import copy
import json
import os
import random
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb.cmkt import adapter as A
from tb.cmkt import collector as CO
from tb.cmkt import conf as C
from tb.cmkt import ex
from tb.cmkt import focus as FO
from tb.cmkt.ex import coinbase as CB
from tb.cmkt.ex import fakex as F

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures_cmkt", "coinbase")


def prod(pid, status="online", disabled=False):
    b, q = pid.split("-")
    return {"id": pid, "base_currency": b, "quote_currency": q, "status": status, "trading_disabled": disabled, "cancel_only": False,
            "quote_increment": "0.01"}


PRODUCTS = [prod(p) for p in ("BTC-USD", "ETH-USD", "DOGE-USD", "USDT-USD", "SOL-USD", "BTC-USDT", "ETH-USDC", "BTC-EUR")] + \
           [prod("OLD-USD", status="delisted"), prod("OFF-USD", disabled=True)]


class FakeHttp(object):
    def __init__(self, fx=("0.99853", "0.99855")):
        self.calls = []
        self.fx = fx

    def get_json(self, url, weight=1, kind="meta"):
        self.calls.append((url, kind))
        if url.endswith("/products"):
            return PRODUCTS
        if url.endswith("/time"):
            return {"iso": "2026-11-04T09:29:26.123Z", "epoch": 1793784566.123}
        if url.endswith("/products/USDT-USD/ticker"):
            if self.fx is None:
                raise A.HttpError(503, url)
            return {"bid": self.fx[0], "ask": self.fx[1], "price": "0.99854", "time": "2026-11-04T09:29:00.000000Z"}
        raise A.HttpError(404, url)


def adapter(clock=None):
    a = CB.CoinbaseAdapter(C.defaults(), clock=clock or (lambda: 0.0))
    a.load_meta(FakeHttp())
    return a


def fixture():
    with open(os.path.join(FIX, "spot.jsonl"), encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def msgs(ty=None):
    out = []
    for r in fixture():
        m = json.loads(r["m"])
        if ty is None or m.get("type") == ty:
            out.append((r["t"], m))
    return out


class TestMeta(unittest.TestCase):
    def test_usd_pairs_only(self):
        a = CB.CoinbaseAdapter(C.defaults())
        got = a.load_meta(FakeHttp())
        self.assertEqual(sorted(i.sym for i in got["spot"]), ["BTC", "DOGE", "ETH", "SOL", "USDT"])   # к USDT/USDC/EUR, off — нет
        self.assertTrue(all(i.quote == "USD" for i in got["spot"]))
        self.assertEqual(a.native("spot", "BTC"), "BTC-USD")
        self.assertEqual(a.native("spot", "USDT"), "USDT-USD")
        self.assertEqual(a.server_time(FakeHttp()), 1793784566.123)
        self.assertEqual(CB.iso_ts("2026-11-04T09:29:24.139118Z"), 1793784564.139118)
        self.assertEqual(CB.iso_ts("2026-11-04T09:29:24Z"), 1793784564.0)
        self.assertIsNone(CB.iso_ts("x"))

    def test_hosts_registry_no_book(self):
        cfg = C.defaults()
        a = ex.load("coinbase", cfg)
        self.assertEqual(a.markets, ("spot",))
        self.assertEqual(a.urls("spot"), {"main": "wss://ws-feed.exchange.coinbase.com"})
        for h in a.hosts()["ws"] + a.hosts()["rest"]:
            self.assertTrue(C.host_allowed(cfg, "coinbase", h), h)
        self.assertIsNone(a.ping())
        self.assertEqual(A.subs_per_conn(cfg, "coinbase"), 100)


class TestFixtureParse(unittest.TestCase):
    def test_fixture_without_parse_errors(self):
        a = adapter()
        evs = []
        for r in fixture():
            evs.extend(a.parse("spot", r["m"], r["t"]))
        self.assertEqual(a.stats["unknown"], 0)
        names = set(type(e).__name__ for e in evs)
        self.assertEqual(names, {"Trade", "Ticker", "SubAck"})                   # стакана нет
        self.assertEqual(a.stats["hist"], len(msgs("last_match")))                # last_match — повтор, не в CVD
        tr = [e for e in evs if isinstance(e, A.Trade)]
        self.assertEqual(len(tr), len(msgs("match")))
        self.assertTrue(all(e.side in (1, -1) and e.t_ex and e.coins > 0 for e in tr))
        tk = [e for e in evs if isinstance(e, A.Ticker) and e.sym != "USDT"]
        self.assertTrue(tk and all(e.bid < e.ask and e.turn24_usd > 0 for e in tk))
        ack = next(e for e in evs if isinstance(e, A.SubAck))
        self.assertIn("matches:BTC-USD", ack.topics)

    def test_aggressor_is_opposite_of_maker_side(self):
        """Эталон по фикстуре: у сделки, пришедшей и в `ticker` (side — сторона тейкера), и в `last_match`/`match` (side — мейкера),
        стороны противоположны; агрессор адаптера совпадает со стороной тейкера тикера."""
        a = adapter()
        tick_side = dict((m["trade_id"], m["side"]) for _, m in msgs("ticker"))
        both = [m for _, m in msgs() if m.get("type") in ("match", "last_match") and m.get("trade_id") in tick_side]
        self.assertGreaterEqual(len(both), 20)
        for m in both:
            self.assertNotEqual(m["side"], tick_side[m["trade_id"]])
        checked = 0
        for _, m in msgs("match"):
            if m["trade_id"] in tick_side:
                e = a.parse("spot", json.dumps(m), 1.0)[0]
                self.assertEqual(e.side, {"buy": 1, "sell": -1}[tick_side[m["trade_id"]]])
                checked += 1
        self.assertGreaterEqual(checked, 20)
        e = a.parse("spot", json.dumps({"type": "match", "trade_id": 9, "side": "sell", "size": "0.5", "price": "78248.31",
                                        "product_id": "BTC-USD", "time": "2026-11-04T09:29:25.885794Z"}), 1.0)[0]
        self.assertEqual((e.side, e.coins, e.px, e.tid), (1, 0.5, 78248.31, "9"))   # мейкер продавал — тейкер купил

    def test_fx_from_ticker_without_lookahead(self):
        a = adapter()
        fx = [(t, m) for t, m in msgs("ticker") if m["product_id"] == "USDT-USD"]
        self.assertGreaterEqual(len(fx), 2)
        for t, m in fx:
            a.parse("spot", json.dumps(m), t)
        t_last, m_last = fx[-1]
        mid = (float(m_last["best_bid"]) + float(m_last["best_ask"])) / 2
        self.assertTrue(0.998 < mid < 1.0)
        d = a.doc_extra("spot", int(t_last) + 3)["fx"]
        self.assertAlmostEqual(d["usdt_usd"], mid, places=7)
        self.assertEqual(d["src"], "ws")
        self.assertAlmostEqual(d["age_s"], int(t_last) + 3 - t_last, places=2)
        T = int(t_last)                                                           # котировка с t_rx > T не видна на T
        if T < t_last and fx[-2][0] <= T:
            m2 = fx[-2][1]
            self.assertAlmostEqual(a.doc_extra("spot", T)["fx"]["usdt_usd"], (float(m2["best_bid"]) + float(m2["best_ask"])) / 2, places=7)
        a2 = adapter()
        a2.parse("spot", json.dumps(m_last), 100.5)
        self.assertIsNone(a2.doc_extra("spot", 100)["fx"]["usdt_usd"])
        self.assertIsNotNone(a2.doc_extra("spot", 101)["fx"]["usdt_usd"])
        self.assertIsNone(adapter().doc_extra("spot", 10)["fx"]["usdt_usd"])


class TestFxRest(unittest.TestCase):
    def test_rest_fallback_every_10s_when_ticker_older_than_10s(self):
        now = [1000.0]
        a = adapter(lambda: now[0])
        http = FakeHttp()
        self.assertTrue(a.side_due("spot", now[0]))                               # курса ещё нет
        self.assertEqual(a.side_poll(http, "spot", now[0]), [])
        self.assertEqual(a.fx_at(now[0])[2], "rest")
        self.assertAlmostEqual(a.fx_at(now[0])[0], 0.99854)
        self.assertEqual(http.calls, [("https://api.exchange.coinbase.com/products/USDT-USD/ticker", "rate")])   # вес из остатка
        a.parse("spot", json.dumps({"type": "ticker", "product_id": "USDT-USD", "best_bid": "0.9985", "best_ask": "0.9986",
                                    "price": "0.9985", "volume_24h": "1", "time": "2026-11-04T09:29:24Z"}), 1002.0)
        for t, due in ((1005.0, False), (1010.5, False), (1011.5, True)):      # тикер 1002: свеж до 1011; REST не чаще раза в 10 с
            self.assertEqual(a.side_due("spot", t), due, t)
        now[0] = 1011.5
        a.side_poll(http, "spot", now[0])
        self.assertFalse(a.side_due("spot", 1015.0))
        self.assertFalse(a.side_due("spot", 1021.0))                              # котировка REST 1011.5 ещё свежа
        self.assertTrue(a.side_due("spot", 1021.6))
        # тишина WS: REST раз в 10 с, возраст на публикации ≤ 10 с + время ответа
        ages = []
        t = 1021.6
        while t < 1100:
            if a.side_due("spot", t):
                now[0] = t + 0.3
                a.side_poll(http, "spot", t)
            ages.append(a.doc_extra("spot", int(t))["fx"]["age_s"])
            t += 0.25
        self.assertLessEqual(max(ages), 10.0 + 0.6)
        self.assertEqual(a.stats["fx_rest"], 2 + 8)
        n_rest = sum(1 for _, k in http.calls if k == "rate")
        self.assertLessEqual(n_rest / ((1100 - 1000) / 60.0), 6.5)                 # ≈ 6 запросов в минуту из 30

    def test_rest_error_keeps_last(self):
        a = adapter(lambda: 50.0)
        a.side_poll(FakeHttp(), "spot", 50.0)
        a.side_poll(FakeHttp(fx=None), "spot", 61.0)
        self.assertEqual(a.stats["fx_err"], 1)
        self.assertEqual(a.fx_at(70)[1], 50.0)
        self.assertFalse(a.side_due("spot", 65.0))                                # повтор не раньше 10 с после неудачи
        self.assertFalse(CB.CoinbaseAdapter(C.defaults()).side_due("spot", 0))    # без метаданных USDT-USD — нет запроса


class TestPlanAndSubs(unittest.TestCase):
    def test_plan_and_messages(self):
        a = adapter()
        subs = a.plan("spot", ["BTC"], ["DOGE", "NOPE"])
        by = dict((s.topic, (s.kind, s.sym)) for s in subs)
        self.assertEqual(by["matches:BTC-USD"], ("trades", "BTC"))
        self.assertEqual(by["ticker:DOGE-USD"], ("ticker", "DOGE"))
        self.assertEqual(by["ticker:USDT-USD"][1], "USDT")
        self.assertEqual(by["heartbeat:USDT-USD"][1], "USDT")
        self.assertEqual(len(subs), 6)
        self.assertFalse(any(s.kind in ("book", "book_part") for s in subs))
        self.assertEqual(len(a.plan("spot", [], [])), 2)                           # курс USDT-USD — всегда
        m = [json.loads(x) for x in a.sub_msgs(subs + [A.Sub("main", "level2:BTC-USD", "book", "BTC")])]
        self.assertEqual(a.stats["invalid_topic"], 1)
        self.assertEqual([(x["type"], x["channels"][0]["name"], x["channels"][0]["product_ids"]) for x in m],
                         [("subscribe", "ticker", ["BTC-USD", "DOGE-USD", "USDT-USD"]), ("subscribe", "matches", ["BTC-USD", "DOGE-USD"]),
                          ("subscribe", "heartbeat", ["USDT-USD"])])
        big = [A.Sub("main", "matches:C%03d-USD" % i, "trades", "X") for i in range(120)]
        self.assertEqual([len(json.loads(x)["channels"][0]["product_ids"]) for x in a.sub_msgs(big)], [50, 50, 20])

    def test_error_maps_to_request_then_single_reject(self):
        a = adapter()
        a.sub_msgs([A.Sub("main", "matches:BTC-USD", "trades", "BTC"), A.Sub("main", "matches:NOPE-USD", "trades", "N"),
                    A.Sub("main", "ticker:BTC-USD", "ticker", "BTC")])
        self.assertEqual(len(a._pending), 2)
        err = {"type": "error", "message": "Failed to subscribe", "reason": "NOPE-USD is not a valid product"}
        e1 = a.parse("spot", json.dumps(err), 1.0)[0]                             # запрос matches (ticker без ошибки)
        self.assertEqual((e1.ok, e1.topics), (False, ("matches:BTC-USD", "matches:NOPE-USD")))
        retry, rej = A.split_rejected(e1.topics)
        for one in retry:
            a.sub_msgs([A.Sub("main", one[0], "trades", "X")])
        a.parse("spot", json.dumps({"type": "subscriptions", "channels": [{"name": "ticker", "product_ids": ["BTC-USD"]}]}), 1.0)
        a.parse("spot", json.dumps({"type": "subscriptions", "channels": [{"name": "matches", "product_ids": ["BTC-USD"]}]}), 1.0)
        e2 = a.parse("spot", json.dumps(err), 2.0)[0]
        self.assertEqual(e2.topics, ("matches:NOPE-USD",))                       # одна тема — отклонена окончательно
        self.assertEqual(A.split_rejected(e2.topics), ([], ["matches:NOPE-USD"]))
        self.assertEqual(a.parse("spot", json.dumps({"type": "heartbeat", "product_id": "USDT-USD"}), 1.0), [])


class TestCollectorPublish(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tb_cmkt_cb_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_nobook_rows_mid_from_ticker_and_fx(self):
        now = 1793784600.0
        cfg = C.defaults()
        a = adapter(lambda: now)
        col = CO.Collector("coinbase", "spot", cfg, root=self.root, adapter=a, clock=lambda: now)
        self.assertTrue(col._meta_done({}, now))
        doc = {"schema": FO.SCHEMA, "ts": now, "deep": ["BTC"], "light": ["DOGE"], "why": {"BTC": "core"}, "since": {"BTC": now, "DOGE": now}}
        col.apply_focus(doc, now)
        for t, m in msgs():
            col.on_events(a.parse("spot", json.dumps(m), now + 0.5), now + 0.5)
        out = col.publish(int(now) + 1, now + 1)
        self.assertEqual(out["syms"]["BTC"][1], "nobook")
        st = CO.market_state(out, "BTC")
        self.assertFalse(st["has_book"])
        self.assertEqual(st["quote"], "USD")
        self.assertTrue(70000 < st["mid"] < 90000)                                  # середина bid/ask тикера
        self.assertTrue(st["tape"]["1m"][0] + st["tape"]["1m"][1] > 0)
        self.assertIsNone(out["syms"]["BTC"][CO.COLS.index("liq")])
        self.assertTrue(0.998 < out["fx"]["usdt_usd"] < 1.0)
        self.assertEqual(out["fx"]["src"], "ws")
        self.assertNotIn("USDT", out["syms"])                                     # курс — поле рынка, не монета фокуса


class CoinbaseServer(F.FakeServer):
    async def on_text(self, peer, text):
        m = json.loads(text)
        for ch in m.get("channels") or ():
            if m["type"] == "subscribe":
                peer.topics.extend("%s:%s" % (ch["name"], p) for p in ch["product_ids"])
        await peer.send(json.dumps({"type": "subscriptions", "channels": m.get("channels")}))
        peer.sub_event.set()


class TestNetwork(unittest.IsolatedAsyncioTestCase):
    async def test_replay_and_frame_ping(self):
        """Фикстура через VenueConn на 127.0.0.1: разбор без parse_err, живость по pong на пинг кадром WS."""
        _bootstrap.require_localhost()
        rows = [r["m"] for r in fixture()]

        async def script(peer):
            await F.wait_topics(peer, 2)
            for t in rows:
                await peer.send(t)
            await asyncio.sleep(10)
        srv = await CoinbaseServer(script=script).start()
        self.addAsyncCleanup(srv.stop)
        cfg = copy.deepcopy(C.defaults())
        cfg["sync"].update({"ping_s": 0.2, "silent_s": 6})
        a = adapter()
        events = []
        c = A.VenueConn(a, "spot", "main#0", srv.url, a.plan("spot", ["BTC", "ETH", "DOGE"], []), on_events=lambda e, t: events.extend(e),
                        cfg=cfg, allow_plain=True, rng=random.Random(1))
        task = asyncio.ensure_future(c.run())
        loop = asyncio.get_event_loop()
        end = loop.time() + 6
        while loop.time() < end and not (c.stats["msgs"] >= len(rows) and c.live.last_pong is not None):
            await asyncio.sleep(0.02)
        alive = c.alive()
        await c.close()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):                            # noqa: BLE001
            pass
        self.assertEqual(c.stats["parse_err"], 0)
        self.assertEqual(c.stats["sub_rejected"], 0)
        self.assertTrue(alive)
        self.assertIsNotNone(c.live.last_pong)
        self.assertTrue(any(isinstance(e, A.Trade) for e in events))
        self.assertEqual(sorted(srv.peers[0].topics), sorted(s.topic for s in a.plan("spot", ["BTC", "ETH", "DOGE"], [])))


if __name__ == "__main__":
    unittest.main()
