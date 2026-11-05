"""R1.7, задача 14: сборщик рынка tb.cmkt.collector (Requirements 1.3, 1.4, 4.1–4.4, 5.6, 5.10, 8.2, 11.3, 12.2, 12.3, 18.2).

Поддельная биржа fakex: синхронные тесты подают разобранные события прямо в сборщик; сетевые — через FakeServer на 127.0.0.1.
Файлы — во временном корне; боевые runtime/ и state/ не трогаются; внешней сети нет.
"""
import asyncio
import copy
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections import deque

import _bootstrap  # noqa: F401
from tb.cmkt import adapter as A
from tb.cmkt import collector as CO
from tb.cmkt import conf as C
from tb.cmkt import focus as FO
from tb.cmkt.ex import fakex as F
from tb.logic import cmkt as L

NOW = 1793750400.0
NEXT = {"perp": {"deep": "next", "light": "replace"}, "spot": {"deep": "next", "light": "replace"}}


def mkcfg(**sync):
    c = copy.deepcopy(C.defaults())
    c["venues"]["fakex"] = {"enabled": True, "markets": ["perp", "spot"], "hosts": ["fakex.test", "api.fakex.test"]}
    c["limits"]["rest_weight_per_min"]["fakex"] = 600
    c["sync"].update({"ping_s": 0.3, "silent_s": 1.0}, **sync)
    c["limits"].update({"backoff_s": 0.05, "backoff_max_s": 0.2, "sub_msgs_per_s": 10, "conn_per_s": 10})
    return c


def focus_doc(deep, light=(), now=NOW, why=None):
    return {"schema": FO.SCHEMA, "ts": now, "deep": list(deep), "light": list(light),
            "why": why or dict((s, "core") for s in deep), "since": dict((s, now) for s in list(deep) + list(light))}


def ms(t):
    return int(t * 1000)


def snap(native, u, t, step=0.05, n=20, size="1000", rest=False, mid=100.0):
    b = [["%.2f" % (mid - 0.01 - i * step), size] for i in range(n)]
    a = [["%.2f" % (mid + 0.01 + i * step), size] for i in range(n)]
    return {"ch": "book", "type": "snap", "s": native, "u": u, "b": b, "a": a, "E": ms(t), "rest": rest}


def delta(native, first, last, t, b=(), a=(), pu=None):
    d = {"ch": "book", "type": "delta", "s": native, "U": first, "u": last, "b": [list(x) for x in b], "a": [list(x) for x in a],
         "E": ms(t)}
    if pu is not None:
        d["pu"] = pu
    return d


def trade(native, t, px, q, side, tid, with_e=True):
    m = {"ch": "trade", "s": native, "p": str(px), "q": str(q), "S": side, "id": tid}
    if with_e:
        m["E"] = ms(t)
    return m


def ticker(native, t, mark=100.0, oi=5000000, fr=0.0001, turn=1e9):
    return {"ch": "ticker", "s": native, "mark": mark, "index": mark, "fr": fr, "oi": oi, "turn": turn, "E": ms(t)}


def liq(native, t, px, q, side):
    return {"ch": "liq", "s": native, "p": str(px), "q": str(q), "S": side, "E": ms(t)}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tb_cmkt_col_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def make(self, deep=("BTC",), light=(), rules=NEXT, kind="perp", instruments=None, cfg=None, now=NOW):
        cfg = cfg or mkcfg()
        a = F.FakexAdapter(cfg, rules=rules, instruments=instruments)
        col = CO.Collector("fakex", kind, cfg, root=self.root, adapter=a, clock=lambda: now)
        self.assertTrue(col.load_meta(now))
        col.apply_focus(focus_doc(deep, light, now), now)
        return a, col

    def feed(self, a, col, msg, t, kind="perp"):
        col.on_events(a.parse(kind, json.dumps(msg), t), t)


class TestNames(unittest.TestCase):
    def test_parse_market_and_service_name(self):
        for s in ("binance_perp", "binancep", "cmkt@binancep", " BINANCE_PERP "):
            self.assertEqual(CO.parse_market(s), ("binance", "perp"), s)
        self.assertEqual(CO.parse_market("coinbases"), ("coinbase", "spot"))
        for bad in ("", "binance", "binance_x", "cmkt@", "../x_perp", "b_inance_perp"):
            self.assertIsNone(CO.parse_market(bad), bad)
        self.assertEqual(CO.service_name("binance", "perp"), "cmkt@binancep")
        self.assertEqual(CO.service_name("coinbase", "spot"), "cmkt@coinbases")

    def test_scrub_env(self):
        env = {"PATH": "x", "BYBIT_API_KEY": "1", "ANTHROPIC_API_KEY": "2", "FOO_TOKEN": "3", "TB_ROOT": "r"}
        self.assertEqual(CO.scrub_env(env), 3)
        self.assertEqual(sorted(env), ["PATH", "TB_ROOT"])


class TestPublish(Base):
    def full_market(self):
        a, col = self.make(deep=("BTC",), light=("PEPE",))
        t = NOW + 0.2
        self.feed(a, col, snap("BTCUSDT", 1000, t), t)
        self.feed(a, col, delta("BTCUSDT", 1001, 1001, t + 0.1, b=[("99.99", "6000000")]), t + 0.1)   # стена 600 тыс. USD на bid
        self.feed(a, col, trade("BTCUSDT", t, 100, 2000, "buy", 1), t + 0.2)                       # 2 монеты × 100 = 200 USD
        self.feed(a, col, trade("BTCUSDT", t, 100, 1000, "sell", 2), t + 0.3)
        self.feed(a, col, trade("BTCUSDT", t, 100, 1000, "sell", 2), t + 0.3)                      # повтор id: один раз
        self.feed(a, col, ticker("BTCUSDT", t), t + 0.4)
        self.feed(a, col, liq("BTCUSDT", t, 100, 3000, "sell"), t + 0.5)                           # ликвидация лонга 300 USD
        self.feed(a, col, snap("1000PEPEUSDT", 7, t, step=0.01, mid=10.0), t + 0.5)                # light: частичный стакан снимками
        self.feed(a, col, trade("1000PEPEUSDT", t, 10, 100, "buy", 9), t + 0.6)
        self.feed(a, col, ticker("1000PEPEUSDT", t, mark=10.0, oi=1000), t + 0.6)
        self.feed(a, col, liq("1000PEPEUSDT", t, 10, 50, "buy"), t + 0.6)
        return a, col

    def test_full_row_format(self):
        a, col = self.full_market()
        T = int(NOW) + 1
        doc = col.publish(T, T + 0.01)
        disk = CO.read_ex("fakex_perp", self.root)
        self.assertEqual(disk, json.loads(json.dumps(doc)))
        self.assertEqual(disk["schema"], CO.SCHEMA)
        self.assertEqual((disk["T"], disk["mkt"], disk["kind"]), (T, "fakex_perp", "perp"))
        self.assertGreaterEqual(disk["t_wr"], T)
        self.assertEqual(disk["cols"], list(CO.COLS))
        r = dict(zip(CO.COLS, disk["syms"]["BTC"]))
        self.assertEqual((r["tier"], r["st"], r["q"]), ("d", "ok", "USDT"))
        self.assertAlmostEqual(r["mid"], 100.0)
        self.assertAlmostEqual(r["dp"], 0.96, places=2)
        self.assertIsNotNone(r["bands"][0])                                     # ±0.25 % покрыта (глубина 0.96 %)
        self.assertIsNotNone(r["bands"][1])
        self.assertIsNone(r["bands"][2])                                        # ±1 % не покрыта: None, а не число
        self.assertEqual([len(r["bk"][0]), len(r["bk"][1])], [8, 8])            # 25 б.п. до ±200 б.п.
        self.assertEqual(r["tape"][0][:2], [200, 100])                          # покупки 200, продажи 100 USD (повтор id не учтён)
        self.assertEqual(r["tk"][0], 100.0)
        self.assertEqual(r["tk"][5], 8)                                         # funding_h из метаданных
        self.assertAlmostEqual(r["oi"][0], 5000.0)                              # 5e6 контрактов × 0.001
        self.assertEqual(r["liq"][:2], [300, 0])
        self.assertEqual(r["w"], 1000000000)
        self.assertIsNone(r["walls"])                                           # стена ещё не устойчива (30 с)
        p = dict(zip(CO.COLS, disk["syms"]["PEPE"]))
        self.assertEqual((p["tier"], p["st"]), ("l", "ok"))
        self.assertIsNone(p["bk"])                                              # корзины только у deep
        self.assertAlmostEqual(p["mid"], 0.01)                                  # цена за монету (1000PEPE / 1000)
        for k in ("bands", "tape", "tk", "oi", "liq"):                          # ярус light даёт все поля
            self.assertIsNotNone(p[k], k)
        self.assertAlmostEqual(p["oi"][0], 1000000.0)
        self.assertEqual(p["liq"][1], 500)                                      # ликвидация шорта (ордер на покупку): 50 000 монет × 0.01
        self.assertEqual(p["tape"][0][0], 1000)

    def test_walls_absolute_price_and_market_state(self):
        a, col = self.full_market()
        t = NOW + 0.2
        for k in range(2, 40):                                                  # стакан держит стену 35+ с
            self.feed(a, col, delta("BTCUSDT", 1000 + k, 1000 + k, t + k, a=[("100.06", str(k))]), t + k)
            col.publish(int(NOW) + k, int(NOW) + k)
        doc = CO.read_ex("fakex_perp", self.root)
        r = dict(zip(CO.COLS, doc["syms"]["BTC"]))
        self.assertEqual(r["walls"][0][:3], ["b", 99.99, 599940])               # абсолютная цена уровня, USD
        self.assertGreaterEqual(r["walls"][0][3], 30)
        st = CO.market_state(doc, "BTC")
        self.assertEqual(st["walls"][0]["side"], "bid")
        self.assertEqual(st["kind"], "perp")
        mk = {"fakex_perp": st, "other_perp": dict(st)}
        out = L.features(mk, 1000, {"cov": {"min_cov": 0.6, "min_n_ex": 2, "liq_min_cov": 0.5, "mismatch_pct": 2.0}})
        self.assertIsNotNone(out["f"]["cm_imb_025_pct"])
        self.assertEqual(out["n_ex"], 2)

    def test_light_published_every_5s(self):
        a, col = self.full_market()
        T0 = int(NOW) - (int(NOW) % 5) + 1
        seen = []
        for T in range(T0, T0 + 11):
            seen.append("PEPE" in col.publish(T, T)["syms"])
        self.assertEqual(seen, [True, False, False, False, True, False, False, False, False, True, False])
        self.assertEqual(col.lt, T0 + 9)

    def test_no_lookahead(self):
        """Свойство 1: публикация T содержит только события с t_rx ≤ T; позже пришедшие ждут своей секунды."""
        a, col = self.make()
        T = int(NOW) + 1
        self.feed(a, col, snap("BTCUSDT", 1000, NOW), NOW + 0.1)
        col.cut = T
        self.feed(a, col, trade("BTCUSDT", NOW, 100, 1000, "buy", 1), T - 0.01)
        self.feed(a, col, trade("BTCUSDT", NOW, 100, 5000, "buy", 2), T + 0.3)      # пришло после границы T
        col.release(T)
        r = dict(zip(CO.COLS, col.publish(T, T + 0.31)["syms"]["BTC"]))
        self.assertEqual(r["tape"][0][0], 100)
        col.cut = T + 1
        col.release(T + 1)
        r = dict(zip(CO.COLS, col.publish(T + 1, T + 1.01)["syms"]["BTC"]))
        self.assertEqual(r["tape"][0][0], 600)
        self.assertIsNone(col.publish(T, T + 2))                                    # T не новее прошлой: не пишется
        self.assertEqual(col.clock_back, 1)

    def test_no_tex_and_side_unknown(self):
        a, col = self.make()
        self.feed(a, col, trade("BTCUSDT", NOW, 100, 1000, "?", 2), NOW + 0.1)
        self.feed(a, col, trade("BTCUSDT", NOW, 100, 1000, "buy", 1, with_e=False), NOW + 0.2)
        hb = col.heartbeat(NOW + 1)
        self.assertEqual(hb["no_tex"], 1)
        self.assertEqual(hb["side_unknown"], 1)
        r = dict(zip(CO.COLS, col.publish(int(NOW) + 1, NOW + 1)["syms"]["BTC"]))
        self.assertEqual(r["tape"][0][:2], [100, 0])                                # сделка без стороны не входит в CVD
        self.assertEqual(r["lag"], 0)                                               # t_ex = t_rx

    def test_not_listed_and_core_fallback(self):
        cfg = mkcfg()
        a = F.FakexAdapter(cfg, rules=NEXT)
        col = CO.Collector("fakex", "perp", cfg, root=self.root, adapter=a, clock=lambda: NOW)
        self.assertIsNone(col.apply_focus(None, NOW))                               # нет метаданных: подписок нет
        self.assertEqual(sorted(col.coins), ["BTC", "ETH", "SOL"])                  # нет focus.json — ядро
        self.assertEqual(col.publish(int(NOW), NOW)["st"], "meta")
        col.load_meta(NOW)
        out = col.apply_focus(None, NOW)
        self.assertTrue(out["open"])
        doc = col.publish(int(NOW) + 1, NOW + 1)
        self.assertEqual(doc["syms"]["SOL"][1], "nl")                               # SOL нет у fakex: NOT_LISTED
        self.assertFalse(any(s.sym == "SOL" for cid in col.planner.conns for s in col.planner.subs(cid)))

    def test_clock_skew_4_of_6(self):
        offs = [0.8, -0.9, 0.7, 0.6, 0.1, 0.0]
        pub = {}
        for i, o in enumerate(offs):
            cfg = mkcfg()
            col = CO.Collector("fakex", "perp", cfg, root=self.root, adapter=F.FakexAdapter(cfg), clock=time.time)
            col.offset.measure(lambda o=o: time.time() + o, time.time)
            pub["v%d" % i] = col.publish(int(time.time()) + i, time.time())["off_ms"]
        self.assertTrue(A.clock_skew(pub, 500, 4))
        pub["v3"] = 100
        self.assertFalse(A.clock_skew(pub, 500, 4))


class TestResync(Base):
    def test_rest_snapshot_queue_and_partial_book(self):
        """Стакан со снимком вне потока (правило binance_perp): первый снимок из очереди, дельты до снимка ждут; разрыв -> RESYNC,
        на время RESYNC — частичный стакан (st=part), повтор не раньше 10 с, затем снова ok."""
        rules = {"perp": {"deep": "binance_perp", "light": "replace"}}
        a, col = self.make(rules=rules)
        sc = F.book_script("binance_perp", random.Random(3), n=40)
        for m in sc["msgs"][:sc["rest_after"]]:
            self.feed(a, col, json.loads(m), NOW + 0.1)
        job = col.resync_jobs(NOW + 0.2)
        self.assertEqual(job, ("rest", "BTC", 1000))                                # ядро: limit 1000
        a.rest_books[("perp", "BTC")] = a.parse("perp", sc["rest"], NOW)[0]
        self.assertTrue(col.apply_rest("BTC", col.rest_snapshot("BTC", 1000), NOW + 0.3))
        for m in sc["msgs"][sc["rest_after"]:]:
            self.feed(a, col, json.loads(m), NOW + 0.4)
        b = col.coins["BTC"].book
        ref = F.bounded_ref(sc)
        self.assertEqual(b.status, "ok")
        self.assertEqual(dict((("%.2f" % p), s) for p, s in b.bids.items()), dict((p, float(s)) for p, s in ref["bid"].items()))
        self.assertEqual(col.budget.used(NOW + 1)["rest"], 20)
        # разрыв номеров
        self.feed(a, col, delta("BTCUSDT", b.seq + 5, b.seq + 6, NOW + 1, pu=b.seq + 4), NOW + 1)
        self.assertEqual(b.status, "resync")
        self.feed(a, col, snap("BTCUSDT", None, NOW + 1.1, rest=False), NOW + 1.1)  # частичный стакан @depth20
        r = dict(zip(CO.COLS, col.publish(int(NOW) + 2, NOW + 2)["syms"]["BTC"]))
        self.assertEqual(r["st"], "part")
        self.assertIsNotNone(r["bands"][0])
        self.assertIsNone(col.resync_jobs(NOW + 2))                                 # не чаще раза в 10 с на монету
        job = col.resync_jobs(NOW + 10.5)
        self.assertEqual(job[0], "rest")
        a.rest_books[("perp", "BTC")] = A.BookSnapshot("fakex_perp", "BTC", NOW, NOW, b.seq or 5000,
                                                       [("99.99", "5")], [("100.01", "5")], True)
        self.assertTrue(col.apply_rest("BTC", col.rest_snapshot("BTC", job[2]), NOW + 10.6))
        r = dict(zip(CO.COLS, col.publish(int(NOW) + 11, NOW + 11)["syms"]["BTC"]))
        self.assertEqual(r["st"], "ok")
        self.assertGreaterEqual(col.heartbeat(NOW + 11)["resyncs"], 1)

    def test_resync_plan_adds_partial_channel(self):
        """Адаптер с plan(..., resync=) (как Binance): монете в RESYNC — частичный канал; события part=True идут в частичный стакан;
        после восстановления канал снимается при следующем плане."""
        class RsAdapter(F.FakexAdapter):
            def plan(self, kind, syms_deep, syms_light, resync=()):
                out = [s for s in super().plan(kind, syms_deep, syms_light)]
                return out + [A.Sub("main", "book5." + self.native(kind, s), "book_part", s) for s in resync]
        cfg = mkcfg()
        a = RsAdapter(cfg, rules={"perp": {"deep": "binance_perp", "light": "replace"}})
        col = CO.Collector("fakex", "perp", cfg, root=self.root, adapter=a, clock=lambda: NOW)
        col.load_meta(NOW)
        out = col.apply_focus(focus_doc(["BTC"]), NOW)
        self.assertIn("book5.BTCUSDT", [s.topic for s in out["add"]["main#0"]])     # стакан ещё без снимка: RESYNC
        ev = a.parse("perp", json.dumps(snap("BTCUSDT", None, NOW)), NOW + 0.1)[0]._replace(part=True)
        col.on_events([ev], NOW + 0.1)
        self.assertEqual(col.coins["BTC"].part.status, "ok")
        self.assertEqual(col.coins["BTC"].book.status, "empty")
        self.assertEqual(dict(zip(CO.COLS, col.publish(int(NOW) + 1, NOW + 1)["syms"]["BTC"]))["st"], "part")
        col.apply_rest("BTC", A.BookSnapshot("fakex_perp", "BTC", NOW, NOW, 10, [("99.99", "5")], [("100.01", "5")], True), NOW + 1)
        self.assertEqual(col.resync_set(), frozenset())
        out = col.apply_focus(focus_doc(["BTC"]), NOW + 2)
        self.assertEqual([s.topic for s in out["remove"]["main#0"]], ["book5.BTCUSDT"])

    def test_ws_rule_gap_gives_resubscribe(self):
        a, col = self.make()
        self.feed(a, col, snap("BTCUSDT", 1000, NOW), NOW + 0.1)
        self.feed(a, col, delta("BTCUSDT", 1005, 1005, NOW + 0.2), NOW + 0.2)       # пропуск 1001–1004
        self.assertEqual(col.coins["BTC"].book.status, "resync")
        self.assertEqual(dict(zip(CO.COLS, col.publish(int(NOW) + 1, NOW + 1)["syms"]["BTC"]))["st"], "resync")
        self.assertEqual(col.resync_jobs(NOW + 0.3), ("ws", "BTC", None))
        texts = col.resub_texts("BTC")
        (cid, tx), = texts.items()
        self.assertEqual([json.loads(x)["op"] for x in tx], ["unsubscribe", "subscribe"])
        self.assertEqual(json.loads(tx[1])["args"], ["book.BTCUSDT"])
        self.feed(a, col, snap("BTCUSDT", 2000, NOW + 0.5), NOW + 0.5)
        self.assertEqual(col.coins["BTC"].book.status, "ok")

    def test_long_resync_requested_again(self):
        a, col = self.make()
        self.assertEqual(col.resync_jobs(NOW), None)                                # снимка ещё нет, но ждём его по подписке
        col.tick(NOW + 10.5)                                                        # нет снимка дольше resync_min_s -> повтор подписки
        self.assertEqual(col.resync_jobs(NOW + 10.5), ("ws", "BTC", None))


class TestRealAdapters(Base):
    """Реальные адаптеры задач 8–10 без сети: метаданные подставлены, REST закрыт (opener бросает OSError)."""

    @staticmethod
    def deny(req, timeout=None):
        raise OSError("внешняя сеть в тестах закрыта")

    def collector(self, venue, kind="perp", syms=("BTC",)):
        from tb.cmkt import ex
        cfg = mkcfg()
        a = ex.load(venue, cfg=cfg)
        sep = "-" if venue == "okx" else ""
        insts = [A.Inst("%s%sUSDT%s" % (s, sep, "-SWAP" if venue == "okx" and kind == "perp" else ""), s, kind, 1.0, 1.0, 0.1, 8.0, "USDT")
                 for s in syms]
        a.meta.update(a.mkt(kind), insts, NOW)
        col = CO.Collector(venue, kind, cfg, root=self.root, adapter=a, clock=lambda: NOW, opener=self.deny)
        self.assertTrue(col.load_meta(NOW))
        return a, col

    def test_binance_resync_partial_wsapi_and_global_liq(self):
        a, col = self.collector("binance")
        out = col.apply_focus(focus_doc(["BTC"]), NOW)
        topics = sorted(s.topic for subs in out["add"].values() for s in subs)
        self.assertIn("btcusdt@depth20@100ms", topics)                              # стакан без снимка: частичный канал
        self.assertIn("!forceOrder@arr", topics)
        self.assertIn("liq", col.coins["BTC"].kinds)                                # общий поток ликвидаций — монетам фокуса
        self.assertEqual(col.resync_jobs(NOW), ("rest", "BTC", 1000))
        key, text = a.book_req("perp", "BTC", 1000)
        self.assertEqual(key, "wsapi")
        rid = json.loads(text)["id"]
        reply = {"id": rid, "status": 200, "result": {"lastUpdateId": 77, "E": ms(NOW), "bids": [["99.9", "5"]], "asks": [["100.1", "5"]]}}
        col.on_events(a.parse("perp", json.dumps(reply), NOW + 0.2), NOW + 0.2)     # ответ WS API -> BookSnapshot(rest=True)
        self.assertEqual(col.coins["BTC"].book.status, "ok")
        self.assertEqual(col.resync_set(), frozenset())
        out = col.apply_focus(focus_doc(["BTC"]), NOW + 1)
        self.assertEqual([s.topic for subs in out["remove"].values() for s in subs], ["btcusdt@depth20@100ms"])
        self.assertTrue(col._has_poll)
        self.assertEqual(CO.Collector._oi_row(col, col.coins["BTC"], int(NOW)), None)    # OI ещё не опрошен

    def test_okx_offset_to_adapter_and_checksum(self):
        a, col = self.collector("okx")
        col.offset.measure(lambda: NOW + 0.7, lambda: NOW)
        col.adapter.offset_s = col.offset.offset
        self.assertAlmostEqual(a.offset_s, 0.7)
        self.assertTrue(col._crc)
        self.assertEqual(col.sub_req_per_hour, 480)
        hb = col.heartbeat(NOW)
        self.assertEqual(hb["oi_every_s"], 16.0)
        self.assertIn("unknown", hb["adapter"])

    def test_sub_requests_per_hour_limit(self):
        a, col = self.make(deep=("BTC",))

        class _Conn(object):
            def __init__(self):
                self.stats, self._queue, self.calls, self.connected = {"sub_msgs": 0}, deque(), [], True

            def subscribe(self, subs):
                self.calls.append(("subscribe", [s.topic for s in subs]))
                self.stats["sub_msgs"] += 1
        col.sub_req_per_hour = 2
        conn = col.conns["main#0"] = _Conn()
        subs = col.planner.subs("main#0")
        self.assertTrue(col._sub_op("main#0", "subscribe", subs[:1], NOW))
        self.assertTrue(col._sub_op("main#0", "subscribe", subs[1:2], NOW + 1))
        self.assertFalse(col._sub_op("main#0", "subscribe", subs[2:3], NOW + 2))   # предел запросов в час исчерпан: ждёт
        self.assertEqual(len(col.pending_subs), 1)
        col.flush_subs(NOW + 100)
        self.assertEqual(len(conn.calls), 2)
        col.flush_subs(NOW + 3700)                                                  # через час место освободилось
        self.assertEqual(len(conn.calls), 3)
        self.assertEqual(len(col.pending_subs), 0)


class TestDegrade(Base):
    def test_coin_failure_does_not_stop_others(self):
        a, col = self.make(deep=("BTC", "ETH"))
        self.feed(a, col, snap("BTCUSDT", 1000, NOW), NOW + 0.1)
        self.feed(a, col, snap("ETHUSDT", 1000, NOW), NOW + 0.1)

        def boom(*a_, **k):
            raise RuntimeError("x")
        col.coins["ETH"].book.apply_delta = boom
        self.feed(a, col, delta("ETHUSDT", 1001, 1001, NOW + 0.2), NOW + 0.2)       # сбой события монеты
        self.feed(a, col, delta("BTCUSDT", 1001, 1001, NOW + 0.2), NOW + 0.2)
        col.coins["ETH"].tape.window = boom                                        # сбой расчёта монеты
        doc = col.publish(int(NOW) + 1, NOW + 1)
        self.assertEqual(doc["syms"]["BTC"][1], "ok")
        self.assertEqual(doc["syms"]["ETH"][1], "err")
        self.assertGreaterEqual(col.counters["err"], 1)
        self.assertEqual(len(doc["syms"]["ETH"]), len(CO.COLS))

    def test_parse_errors_mark_market(self):
        a, col = self.make()

        class _C(object):
            stats = {"msgs": 0, "parse_err": 0, "bytes": 0, "sub_rejected": 0, "reconnects": 0}
            connected = True
            _t_last = None

            def alive(self, now=None):
                return True
        fake = _C()
        col.conns["main#0"] = fake
        col.cmeta["main#0"] = {"up": True}
        col.tick(NOW)
        fake.stats = dict(fake.stats, msgs=100, parse_err=10)
        col.tick(NOW + 30)
        self.assertEqual(col.publish(int(NOW) + 30, NOW + 30)["err"], "PARSE")

    def test_atomic_file_under_reader(self):
        a, col = self.make(deep=("BTC", "ETH"))
        self.feed(a, col, snap("BTCUSDT", 1000, NOW), NOW + 0.1)
        path = CO.ex_path("fakex_perp", self.root)
        bad, good, stop = [], [0], threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                except OSError:
                    continue
                try:
                    if json.loads(text)["schema"] != CO.SCHEMA:
                        bad.append(text[:80])
                    good[0] += 1
                except ValueError:
                    bad.append(text[:80])
        col.publish(int(NOW), NOW)
        th = threading.Thread(target=reader)
        th.start()
        try:
            for k in range(1, 300):
                col.publish(int(NOW) + k, NOW + k)
        finally:
            stop.set()
            th.join()
        self.assertEqual(bad, [])
        self.assertGreater(good[0], 0)
        self.assertEqual(sorted(os.listdir(os.path.dirname(path))), ["fakex_perp.json"])     # временных файлов не осталось


def synth(nd=20, nl=120):
    insts = [{"native": "C%03dUSDT" % i, "base": "C%03d" % i, "quote": "USDT", "settle": "USDT", "type": "linear_perp", "contract_size": 1,
              "funding_h": 8} for i in range(nd + nl)]
    deep = ["C%03d" % i for i in range(nd)]
    light = ["C%03d" % i for i in range(nd, nd + nl)]
    return {"perp": insts, "spot": []}, deep, light


class TestLoad(Base):
    """Компактность файла и CPU на синтетическом потоке (ориентиры design.md, «Нагрузка»: файл рынка ≈ 17 КБ/с при 20 deep и
    120 light; самый нагруженный процесс ≈ 0.08 ядра в среднем)."""

    def setup_market(self, nd=20, nl=120, levels=1000):
        insts, deep, light = synth(nd, nl)
        a, col = self.make(deep=deep, light=light, instruments=insts)
        rng = random.Random(5)
        for i, s in enumerate(deep):
            n = "%sUSDT" % s
            self.feed(a, col, snap(n, 1000, NOW, step=0.01, n=levels, size=str(rng.randint(1, 900))), NOW)
            self.feed(a, col, ticker(n, NOW, oi=1e6), NOW)
        for s in light:
            n = "%sUSDT" % s
            self.feed(a, col, snap(n, 1, NOW, step=0.02, n=50), NOW)
            self.feed(a, col, ticker(n, NOW, oi=1e5), NOW)
        return a, col, deep, light

    def test_compact_bytes_per_second(self):
        a, col, deep, light = self.setup_market()
        T0 = int(NOW) - int(NOW) % 5
        total, deep_rows = 0, []
        for k in range(10):
            T = T0 + k
            for s in deep:
                self.feed(a, col, trade("%sUSDT" % s, T, 100, 3, "buy" if k % 2 else "sell", "%s%d" % (s, k)), T - 0.5)
            doc = col.publish(T, T)
            total += len(json.dumps(doc, separators=(",", ":")).encode())
            deep_rows += [len(json.dumps(doc["syms"][s], separators=(",", ":"))) for s in deep]
        bps = total / 10.0
        avg_deep = sum(deep_rows) / float(len(deep_rows))
        sys.stderr.write("\n[cmkt-load] ex-файл: %.0f Б/с (20 deep + 120 light), строка deep %.0f Б, %.2f ГБ/сутки\n"
                         % (bps, avg_deep, bps * 86400 / 1e9))
        self.assertLess(bps, 30000)                                                 # оценка design: ≈ 17 КБ/с
        self.assertLess(avg_deep, 800)                                              # ≈ 300 + 200 Б корзин

    def test_cpu_synthetic_stream(self):
        """20 deep (стакан 1000 уровней, 10 дельт/с), 120 light (снимок раз в секунду), 40 сделок/с, тикеры; разбор адаптером
        включён. CPU на одну секунду потока."""
        a, col, deep, light = self.setup_market()
        rng = random.Random(7)
        seq = dict((s, 1000) for s in deep)
        T0 = int(NOW) + 1
        secs = 10
        msgs = []
        for k in range(secs):
            T = T0 + k
            sec = []
            for j in range(10):
                for s in deep:
                    seq[s] += 1
                    px = "%.2f" % (100 + rng.choice((-1, 1)) * (0.01 + rng.randint(0, 200) * 0.01))
                    side = "b" if float(px) < 100 else "a"
                    d = delta("%sUSDT" % s, seq[s], seq[s], T - 1 + j / 10.0, **{side: [(px, str(rng.randint(0, 900)))]})
                    sec.append((T - 1 + j / 10.0, json.dumps(d)))
            for j in range(40):
                s = deep[j % len(deep)]
                sec.append((T - 0.5, json.dumps(trade("%sUSDT" % s, T, 100, 2, "buy" if j % 3 else "sell", "%d-%d" % (k, j)))))
            for s in light:
                sec.append((T - 0.3, json.dumps(snap("%sUSDT" % s, 1, T, step=0.02, n=20))))
            for s in deep:
                sec.append((T - 0.2, json.dumps(ticker("%sUSDT" % s, T, oi=1e6 + k))))
            msgs.append((T, sec))
        n_msgs = sum(len(s) for _, s in msgs)
        c0 = time.process_time()
        for T, sec in msgs:
            for t, raw in sec:
                col.on_events(a.parse("perp", raw, t), t)
            col.publish(T, T)
            col.tick(T)
        cpu = time.process_time() - c0
        cores = cpu / secs
        sys.stderr.write("\n[cmkt-load] CPU: %.3f ядра на %d сообщ./с (deep 20 × 10 дельт, 120 light снимков, 40 сделок, 20 тикеров), "
                         "%.1f мкс на сообщение с публикацией\n" % (cores, n_msgs // secs, cpu / n_msgs * 1e6))
        for s in deep[:3]:
            self.assertEqual(col.coins[s].book.status, "ok")
        self.assertLess(cores, 0.8)                                                 # пик процесса по design ≤ 0.8 ядра


class TestExit(unittest.TestCase):
    def run_cli(self, root, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = _bootstrap.SRC
        env.pop("TB_ROOT", None)
        return subprocess.run([sys.executable, "-m", "tb.cmkt.collector", "run", "--root", root] + list(args), env=env,
                              capture_output=True, text=True, timeout=60)

    def test_bad_config_exit_2(self):
        root = tempfile.mkdtemp(prefix="tb_cmkt_col_cli_")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "config"))
        with open(os.path.join(root, "config", "cmkt.json"), "w", encoding="utf-8") as f:
            f.write('{"schema": "tb.cmkt_cfg/1", "bogus": 1}')
        p = self.run_cli(root, "--market", "binance_perp")
        self.assertEqual(p.returncode, 2, p.stderr)
        with open(os.path.join(root, "runtime", "platform", "cmkt@binancep.hb.json"), encoding="utf-8") as f:
            hb = json.load(f)
        self.assertEqual(hb["state"], "E_CONF")
        self.assertTrue(any(e.startswith("E_CONF:") for e in hb["errors"]))
        self.assertEqual(self.run_cli(root, "--market", "nope").returncode, 2)
        os.remove(os.path.join(root, "config", "cmkt.json"))                        # нет файла — умолчания; fakex в них нет
        self.assertEqual(self.run_cli(root, "--market", "fakex_perp").returncode, 2)


# ---------------------------------------------------------------- сеть: FakeServer на 127.0.0.1
class Feed(object):
    """Общий поток поддельной биржи для всех соединений: номера стакана общие (как у настоящей биржи), новое соединение получает
    снимок на текущем номере, затем те же дельты, что и остальные."""

    def __init__(self, srv, every=0.05):
        self.srv, self.every = srv, every
        self.seq, self.snapped, self.tid, self.k = {}, set(), 0, 0
        self.drop_at = {}                                  # индекс соединения -> раунд обрыва
        self.drop_t = None
        self.mute = set()                                  # соединения, которым ничего не шлём (тихий рынок)

    async def loop(self):
        while True:
            self.k += 1
            books = set()
            for p in list(self.srv.peers):
                books.update(t.split(".", 1)[1] for t in p.topics if t.startswith("book."))
            for n in books:
                self.seq[n] = self.seq.get(n, 1000) + 1
            now = time.time()
            for p in list(self.srv.peers):
                if p.closed or p.index in self.mute:
                    continue
                if self.drop_at.get(p.index) == self.k:
                    self.drop_t = time.time()
                    p.drop()
                    continue
                try:
                    for t in list(p.topics):
                        ch, _, n = t.partition(".")
                        if ch == "book":
                            if (p.index, n) not in self.snapped:
                                self.snapped.add((p.index, n))
                                await p.send(json.dumps(snap(n, self.seq[n], now)))
                            else:
                                lv = "%.2f" % (100.01 + (self.k % 10) * 0.05)
                                await p.send(json.dumps(delta(n, self.seq[n], self.seq[n], now, a=[(lv, str(1000 + self.k))])))
                        elif ch == "book5":
                            await p.send(json.dumps(snap(n, 1, now, n=10)))
                        elif ch == "trade":
                            self.tid += 1
                            await p.send(json.dumps(trade(n, now, 100, 1000, "buy" if self.k % 2 else "sell", self.tid)))
                        elif ch == "ticker" and self.k % 5 == 0:
                            await p.send(json.dumps(ticker(n, now, oi=5e6 + self.k)))
                        elif ch == "liq" and self.k % 10 == 0:
                            await p.send(json.dumps(liq(n, now, 100, 1000, "sell")))
                except (ConnectionError, OSError):
                    p.closed = True
            await asyncio.sleep(self.every)


class TestNet(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _bootstrap.require_localhost()
        self.root = tempfile.mkdtemp(prefix="tb_cmkt_col_net_")
        self.addCleanup(shutil.rmtree, self.root, True)

    async def start(self, deep=("BTC",), light=(), cfg=None, **srv_kw):
        srv = await F.FakeServer(**srv_kw).start()
        self.addAsyncCleanup(srv.stop)
        feed = Feed(srv)
        ft = asyncio.ensure_future(feed.loop())

        async def stop_feed():
            ft.cancel()
            try:
                await ft
            except (asyncio.CancelledError, Exception):    # noqa: BLE001
                pass
        self.addAsyncCleanup(stop_feed)
        cfg = cfg or mkcfg()
        FO.write(self.root, focus_doc(deep, light, time.time()))
        a = F.FakexAdapter(cfg, base_url=srv.url, rules=NEXT)
        col = CO.Collector("fakex", "perp", cfg, root=self.root, adapter=a, allow_plain=True, rng=random.Random(1))
        stop = asyncio.Event()
        task = asyncio.ensure_future(col.run(stop))

        async def stop_col():
            stop.set()
            try:
                await asyncio.wait_for(task, 10)
            except (asyncio.CancelledError, Exception):    # noqa: BLE001
                pass
        self.addAsyncCleanup(stop_col)
        return srv, feed, a, col

    async def until(self, pred, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if pred():
                    return True
            except (KeyError, TypeError, IndexError):
                pass
            await asyncio.sleep(0.05)
        return False

    def row(self, sym="BTC"):
        d = CO.read_ex("fakex_perp", self.root)
        return dict(zip(CO.COLS, d["syms"][sym])) if d and sym in d["syms"] else None

    async def test_full_cycle(self):
        srv, feed, a, col = await self.start(deep=("BTC",), light=("ETH",))
        self.assertTrue(await self.until(lambda: self.row()["st"] == "ok" and self.row()["tape"][0][0] > 0))
        d1 = CO.read_ex("fakex_perp", self.root)
        self.assertTrue(await self.until(lambda: CO.read_ex("fakex_perp", self.root)["T"] >= d1["T"] + 2))
        d2 = CO.read_ex("fakex_perp", self.root)
        self.assertEqual(d2["st"], "ok")
        self.assertEqual(d2["conns"], [1, 1])
        self.assertLessEqual(d2["T"], d2["t_wr"])
        self.assertLess(d2["t_wr"] - d2["T"], 1.0)
        r = self.row()
        self.assertLessEqual(r["a"], 1.0)
        self.assertIsNotNone(r["lag"])
        self.assertIsNotNone(r["tk"][0])
        self.assertIsNotNone(r["oi"])
        self.assertTrue(await self.until(lambda: "ETH" in CO.read_ex("fakex_perp", self.root)["syms"], 7.0))   # light раз в 5 с
        hb_path = os.path.join(self.root, "runtime", "platform", "cmkt@fakexp.hb.json")
        def hb_subs():
            with open(hb_path, encoding="utf-8") as f:
                return json.load(f).get("subs", 0)
        self.assertTrue(await self.until(lambda: hb_subs() >= 7, 7.0))
        with open(hb_path, encoding="utf-8") as f:
            hb = json.load(f)
        self.assertEqual(hb["conns"], 1)
        for k in ("cpu_cores", "rss_mb", "proc_ms_p99", "rest", "reconnects"):
            self.assertIn(k, hb)
        self.assertLessEqual(max(col.sub_lat), 10.0)                                # подписка новой монеты ≤ 10 с

    async def test_disconnect_reconnect_resync(self):
        srv, feed, a, col = await self.start()
        feed.drop_at[0] = 25                                                        # биржа рвёт первое соединение
        self.assertTrue(await self.until(lambda: len(srv.peers) >= 2 and col.coins["BTC"].book.status == "ok"
                                         and col.coins["BTC"].book.resyncs >= 1))
        self.assertGreaterEqual(col.conn_stats()["reconnects"], 1)
        b = col.coins["BTC"].book
        self.assertIn(b.reason, (None, "reconnect"))
        tape = col.coins["BTC"].tape                                                # разрыв ленты в секунду обрыва снижает покрытие
        near = [tape.cont[i] for i in range(tape.n) if abs(tape.sec[i] - feed.drop_t) <= 2]
        self.assertIn(0, near)
        self.assertTrue(await self.until(lambda: self.row()["st"] == "ok"))

    async def test_focus_change_subscribes_delta(self):
        srv, feed, a, col = await self.start(deep=("BTC",))
        self.assertTrue(await self.until(lambda: srv.peers and "book.BTCUSDT" in srv.peers[0].topics))
        t0 = time.time()
        FO.write(self.root, focus_doc(["BTC", "ETH"], (), time.time()))
        self.assertTrue(await self.until(lambda: "book.ETHUSDT" in srv.peers[0].topics, 10.0))
        self.assertLess(time.time() - t0, 10.0)                                     # подписка новой монеты ≤ 10 с
        self.assertTrue(await self.until(lambda: self.row("ETH")["st"] == "ok"))
        peer = srv.peers[0]
        subs_btc = [m for m in peer.received if '"subscribe"' in m and "book.BTCUSDT" in m]
        self.assertEqual(len(subs_btc), 1)                                          # BTC не переподписан: изменения дельтой
        FO.write(self.root, focus_doc(["ETH"], (), time.time()))
        self.assertTrue(await self.until(lambda: "book.BTCUSDT" not in peer.topics, 6.0))
        self.assertTrue(any('"unsubscribe"' in m and "trade.BTCUSDT" in m for m in peer.received))
        self.assertNotIn("BTC", col.coins)
        self.assertEqual(len(srv.peers), 1)                                         # то же соединение

    async def test_quiet_market_alive_by_pong(self):
        srv, feed, a, col = await self.start()
        feed.mute.add(0)                                                            # биржа молчит, но отвечает на пинг
        self.assertTrue(await self.until(lambda: srv.peers and col.conns))
        await asyncio.sleep(2.0)                                                    # дольше silent_s (1 с)
        self.assertEqual(len(srv.peers), 1)
        r = self.row()
        self.assertIsNotNone(r["a"])
        self.assertLessEqual(r["a"], 1.0)
        self.assertEqual(CO.read_ex("fakex_perp", self.root)["st"], "ok")

    async def test_silence_reconnects(self):
        srv, feed, a, col = await self.start(answer_ping=False)
        feed.mute.update(range(10))                                                 # ни данных, ни pong
        self.assertTrue(await self.until(lambda: len(srv.peers) >= 2, 8.0))         # молчание дольше silent_s -> переподключение
        self.assertTrue(await self.until(lambda: col.conn_stats()["reconnects"] >= 1 or col.counters["drops"] >= 1))

    async def test_real_adapter_bybit_full_cycle(self):
        """Настоящий адаптер Bybit (задача 9) против FakeServer: форматы Bybit, правило u + 1, публикация, разрыв и восстановление."""
        from tb.cmkt.ex import bybit as BY
        srv = await F.FakeServer().start()
        self.addAsyncCleanup(srv.stop)
        state = {"u": 5000, "tid": 0, "k": 0, "snapped": set(), "drop_at": 30}

        async def loop():
            while True:
                state["k"] += 1
                state["u"] += 1
                now = time.time()
                for p in list(srv.peers):
                    if p.closed:
                        continue
                    if p.index == 0 and state["k"] == state["drop_at"]:
                        p.drop()
                        continue
                    try:
                        for t in list(p.topics):
                            ch = t.split(".")[0]
                            if ch == "orderbook":
                                first = p.index not in state["snapped"]
                                state["snapped"].add(p.index)
                                lv = [["%.1f" % (100 - 0.1 * i), "50"] for i in range(1, 30)], [["%.1f" % (100 + 0.1 * i), "50"] for i in range(1, 30)]
                                d = {"s": "BTCUSDT", "b": lv[0], "a": lv[1], "u": state["u"], "seq": state["u"]} if first else \
                                    {"s": "BTCUSDT", "b": [["99.9", str(50 + state["k"])]], "a": [], "u": state["u"], "seq": state["u"]}
                                await p.send(json.dumps({"topic": t, "type": "snapshot" if first else "delta", "ts": ms(now),
                                                         "cts": ms(now), "data": d}))
                            elif ch == "publicTrade":
                                state["tid"] += 1
                                await p.send(json.dumps({"topic": t, "type": "snapshot", "ts": ms(now), "data": [
                                    {"T": ms(now), "s": "BTCUSDT", "S": "Buy" if state["k"] % 2 else "Sell", "v": "0.5", "p": "100",
                                     "i": "t%d" % state["tid"]}]}))
                            elif ch == "tickers" and state["k"] % 4 == 0:
                                await p.send(json.dumps({"topic": t, "type": "snapshot", "ts": ms(now), "data": {
                                    "symbol": "BTCUSDT", "markPrice": "100", "indexPrice": "100", "fundingRate": "0.0001",
                                    "fundingIntervalHour": "8", "openInterest": "12345", "turnover24h": "5000000000"}}))
                            elif ch == "allLiquidation" and state["k"] % 10 == 0:
                                await p.send(json.dumps({"topic": t, "type": "snapshot", "ts": ms(now), "data": [
                                    {"T": ms(now), "s": "BTCUSDT", "S": "Buy", "v": "2", "p": "100"}]}))
                    except (ConnectionError, OSError):
                        p.closed = True
                await asyncio.sleep(0.05)
        ft = asyncio.ensure_future(loop())

        async def stop_feed():
            ft.cancel()
            try:
                await ft
            except (asyncio.CancelledError, Exception):    # noqa: BLE001
                pass
        self.addAsyncCleanup(stop_feed)
        cfg = mkcfg()
        a = BY.BybitAdapter(cfg)
        a.meta.update("bybit_perp", [A.Inst("BTCUSDT", "BTC", "perp", 1.0, 1.0, 0.1, 8.0, "USDT")], time.time())
        FO.write(self.root, focus_doc(["BTC"], (), time.time()))
        col = CO.Collector("bybit", "perp", cfg, root=self.root, adapter=a, allow_plain=True, rng=random.Random(2),
                           opener=TestRealAdapters.deny, urls={"main": srv.url})
        stop = asyncio.Event()
        task = asyncio.ensure_future(col.run(stop))

        async def stop_col():
            stop.set()
            try:
                await asyncio.wait_for(task, 10)
            except (asyncio.CancelledError, Exception):    # noqa: BLE001
                pass
        self.addAsyncCleanup(stop_col)

        def row():
            d = CO.read_ex("bybit_perp", self.root)
            return dict(zip(CO.COLS, d["syms"]["BTC"]))
        self.assertTrue(await self.until(lambda: row()["st"] == "ok" and row()["tape"][0][0] > 0 and row()["oi"] is not None))
        r = row()
        self.assertAlmostEqual(r["mid"], 100.0)
        self.assertEqual(r["tk"][0], 100.0)
        self.assertEqual(r["oi"][0], 12345.0)
        self.assertEqual(r["w"], 5000000000)
        self.assertTrue(await self.until(lambda: len(srv.peers) >= 2 and col.coins["BTC"].book.resyncs >= 1
                                         and col.coins["BTC"].book.status == "ok", 8.0))      # разрыв -> RESYNC -> снимок подписки
        self.assertTrue(await self.until(lambda: row()["st"] == "ok" and (row()["liq"] or [0])[0] > 0, 6.0))
        self.assertEqual(col.conn_stats()["parse_err"], 0)
        self.assertEqual(a.stats["invalid_topic"], 0)

    async def test_connection_rotation_without_resync(self):
        srv, feed, a, col = await self.start()
        a.conn_max_s = 601.5
        col.rotate_lead_s = 600.0                                                   # смена через 1.5 с после подключения
        self.assertTrue(await self.until(lambda: col.counters["rotations"] >= 1, 8.0))
        self.assertTrue(await self.until(lambda: srv.peers[0].closed, 3.0))
        await asyncio.sleep(0.5)
        b = col.coins["BTC"].book
        self.assertEqual(b.resyncs, 0)                                              # стакан перенесён без RESYNC
        self.assertEqual(b.status, "ok")
        self.assertEqual(col.counters["drops"], 0)
        self.assertEqual(col.conn_stats()["reconnects"], 0)
        self.assertTrue(await self.until(lambda: self.row()["st"] == "ok" and self.row()["a"] is not None))


if __name__ == "__main__":
    unittest.main()
