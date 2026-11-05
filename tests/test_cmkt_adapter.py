"""R1.7, задача 6: контракт адаптера и общая механика tb.cmkt.adapter, поддельная биржа fakex (свойства 4, 5; Requirements 2.2, 2.3, 3.4,
5.1–5.5, 5.7, 5.8, 5.10, 5.11, 12.1, 12.2, 12.4).

Сеть — только 127.0.0.1 (FakeServer); внешних соединений нет.
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
from tb.logic import cmkt as L

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures_cmkt")
RULES = ("binance_perp", "binance_spot", "next", "prev", "bybit")


def fake(rule="binance_perp", raw=False):
    a = F.FakexAdapter(rules={"perp": {"deep": rule, "light": "replace"}}, raw_books=("fakex_perp",) if raw else ())
    a.load_meta()
    return a


def run_script(a, script, t=1000.0, crc=False):
    """Подаёт сценарий стакану: поток до снимка вне потока, снимок, остаток потока. -> BookState."""
    st = a.book_state("perp", "BTC", "deep")
    crc_fn = a.checksum if crc else None
    msgs = script["msgs"]
    cut = script["rest_after"] if script["rest"] else 0
    for raw in msgs[:cut]:
        for e in a.parse("perp", raw, t):
            st.apply_delta(e, t, crc_fn)
    if script["rest"]:
        st.apply_snapshot(a.parse("perp", script["rest"], t)[0], t)
    for raw in msgs[cut:]:
        t += 0.1
        for e in a.parse("perp", raw, t):
            if isinstance(e, A.BookSnapshot):
                st.apply_snapshot(e, t)
            else:
                st.apply_delta(e, t, crc_fn)
    return st


class TestBookContinuity(unittest.TestCase):
    def test_property4_no_gap_equals_reference(self):
        """Свойство 4: без разрыва стакан совпадает с эталоном сценария; уровней дальше границы REST-снимка нет."""
        for rule in RULES:
            for seed in range(6):
                a = fake(rule)
                sc = F.book_script(rule, random.Random(seed), n=150)
                st = run_script(a, sc)
                self.assertEqual(st.status, "ok", (rule, seed, st.reason))
                ref = F.bounded_ref(sc)
                got_b = dict((p, s) for p, s in st.raw_sorted()[0])
                got_a = dict((p, s) for p, s in st.raw_sorted()[1])
                self.assertEqual({float(k): float(v) for k, v in got_b.items()}, {float(k): float(v) for k, v in ref["bid"].items()}, (rule, seed))
                self.assertEqual({float(k): float(v) for k, v in got_a.items()}, {float(k): float(v) for k, v in ref["ask"].items()}, (rule, seed))
                if sc["bounds"]:
                    self.assertTrue(all(float(p) >= sc["bounds"][0] for p in st.bids), rule)
                    self.assertTrue(all(float(p) <= sc["bounds"][1] for p in st.asks), rule)
                    self.assertTrue(any(float(p) < sc["bounds"][0] for p in sc["ref"]["bid"]), "сценарий без уровней за границей")

    def test_property4_gap_gives_resync(self):
        """Свойство 4: пропуск номера по любому правилу переводит стакан в RESYNC."""
        for rule in RULES:
            for seed in range(6):
                rng = random.Random(100 + seed)
                gap = rng.randint(6, 100)
                st = run_script(fake(rule), F.book_script(rule, rng, n=120, gap_at=gap))
                self.assertEqual(st.status, "resync", (rule, seed, gap))
                self.assertEqual(st.reason, "gap")
                self.assertEqual((st.bids, st.asks), ({}, {}))

    def test_rest_snapshot_first_delta_rules(self):
        """Binance perp: первая дельта U ≤ lastUpdateId ≤ u; spot: U ≤ id + 1 ≤ u; дельты до снимка отбрасываются как устаревшие."""
        for rule in ("binance_perp", "binance_spot"):
            a = fake(rule)
            st = a.book_state("perp", "BTC", "deep")
            snap = A.BookSnapshot("fakex_perp", "BTC", 1.0, 1.0, 100, [("99", "1")], [("101", "1")], True)
            for d in (A.BookDelta("fakex_perp", "BTC", 1, 1, 90, 95, 89, None, [("98", "1")], []),
                      A.BookDelta("fakex_perp", "BTC", 1, 1, 96, 100, 95, None, [("99", "2")], []),
                      A.BookDelta("fakex_perp", "BTC", 1, 1, 101, 104, 100, None, [("99", "3")], [])):
                self.assertEqual(st.apply_delta(d, 1.0), "buffered")
            st.apply_snapshot(snap, 1.0)
            self.assertEqual(st.status, "ok", rule)
            self.assertEqual(st.seq, 104)
            self.assertEqual(st.bids, {99.0: 3.0})             # 98 за границей снимка (99) не появился
            # первая дельта, не перекрывающая снимок -> RESYNC
            st2 = a.book_state("perp", "BTC", "deep")
            st2.apply_snapshot(snap, 1.0)
            st2.apply_delta(A.BookDelta("fakex_perp", "BTC", 1, 1, 105, 110, 104, None, [], []), 1.0)
            self.assertEqual(st2.status, "resync", rule)

    def test_checksum_only_when_sent(self):
        a = fake("prev", raw=True)
        ok = run_script(a, F.book_script("prev", random.Random(5), n=80, crc=True, far_every=0), crc=True)
        self.assertEqual(ok.status, "ok")
        bad = run_script(a, F.book_script("prev", random.Random(5), n=80, crc=True, bad_crc_at=40, far_every=0), crc=True)
        self.assertEqual((bad.status, bad.reason), ("resync", "checksum"))
        # нулевая или отсутствующая сумма не проверяется (OKX шлёт 0, у Bitget поля нет)
        st = a.book_state("perp", "BTC", "deep")
        st.apply_snapshot(A.BookSnapshot("fakex_perp", "BTC", 1, 1, 10, [("99.00", "1")], [("101.00", "1")]), 1.0)
        for i, cs in enumerate((0, None)):
            r = st.apply_delta(A.BookDelta("fakex_perp", "BTC", 1, 1, None, 11 + i, 10 + i, cs, [("99.50", "2")], []), 1.0, a.checksum)
            self.assertEqual(r, "ok")
        self.assertIsInstance(next(iter(st.bids)), str)          # raw: исходные строки уровней

    def test_cross_and_empty_side(self):
        a = fake("next")
        st = a.book_state("perp", "BTC", "deep")
        st.apply_snapshot(A.BookSnapshot("fakex_perp", "BTC", 1, 1, 10, [("100", "1"), ("99", "1")], [("101", "1")]), 0.0)
        st.apply_delta(A.BookDelta("fakex_perp", "BTC", 1, 1, 11, 11, None, None, [("101.5", "1")], []), 1.0)    # перекрёст
        self.assertEqual(st.status, "ok")
        st.tick(2.9)
        self.assertEqual(st.status, "ok")
        st.tick(3.1)                                              # дольше 2 с
        self.assertEqual((st.status, st.reason), ("resync", "cross"))
        st.apply_snapshot(A.BookSnapshot("fakex_perp", "BTC", 1, 1, 20, [("99", "1")], [("101", "1")]), 5.0)
        st.apply_delta(A.BookDelta("fakex_perp", "BTC", 1, 1, 21, 21, None, None, [], [("101", "0")]), 5.1)       # пустая сторона
        self.assertEqual((st.status, st.reason), ("resync", "empty_side"))

    def test_bybit_u1_reset_and_snapshot_recovers(self):
        a = fake("bybit")
        st = a.book_state("perp", "BTC", "deep")
        st.apply_snapshot(A.BookSnapshot("fakex_perp", "BTC", 1, 1, 50, [("99", "1")], [("101", "1")]), 0.0)
        self.assertEqual(st.apply_delta(A.BookDelta("fakex_perp", "BTC", 1, 1, None, 51, None, None, [], []), 0.1), "ok")
        self.assertEqual(st.apply_delta(A.BookDelta("fakex_perp", "BTC", 1, 1, None, 1, None, None, [], []), 0.2), "gap")
        self.assertEqual(st.apply_delta(A.BookDelta("fakex_perp", "BTC", 1, 1, None, 2, None, None, [], []), 0.3), "ignored")
        st.apply_snapshot(A.BookSnapshot("fakex_perp", "BTC", 1, 1, 1, [("99", "1")], [("101", "1")]), 0.4)     # снимок после сброса
        self.assertEqual(st.status, "ok")
        self.assertEqual(st.apply_delta(A.BookDelta("fakex_perp", "BTC", 1, 1, None, 2, None, None, [], []), 0.5), "ok")


class TestBookBest(unittest.TestCase):
    """Задача 23: `best()` читает вершины куч с ленивым удалением вместо прохода по всем уровням. Свойство: на любой смеси операций
    (снимок, дельты, удаления уровней нулевым размером, повторная постановка цены, RESYNC) результат совпадает с наивным max/min по
    словарю уровней — куча не может «забыть» живой уровень и не может вернуть снятый."""

    def test_best_equals_naive_scan_on_2000_random_ops(self):
        for raw in (False, True):
            rng = random.Random(20261104)
            st = A.BookState("fakex_perp", "BTC", "bybit", raw=raw)
            seq = [1]
            mid = [50000.0]

            def snapshot(px):
                seq[0] += 1
                bids = [("%.2f" % (px - 0.5 * (i + 1)), "%.3f" % rng.uniform(0.1, 5.0)) for i in range(rng.randint(20, 60))]
                asks = [("%.2f" % (px + 0.5 * (i + 1)), "%.3f" % rng.uniform(0.1, 5.0)) for i in range(rng.randint(20, 60))]
                st.apply_snapshot(A.BookSnapshot("fakex_perp", "BTC", 1.0, 1.0, seq[0], bids, asks), 1.0)
                mid[0] = px

            def naive():
                return (max((float(p) for p in st.bids), default=None), min((float(p) for p in st.asks), default=None))

            snapshot(mid[0])
            for k in range(2000):
                r = rng.random()
                if r < 0.04:                                   # новый снимок: замена всех уровней
                    snapshot(mid[0] + rng.uniform(-200, 200))
                elif r < 0.07:                                 # RESYNC: уровни сброшены
                    st.resync("test", 1.0)
                elif r < 0.10:                                 # восстановление после RESYNC
                    if st.status != "ok":
                        snapshot(mid[0])
                else:
                    seq[0] += 1
                    lo, hi = mid[0] - rng.uniform(0, 50), mid[0] + rng.uniform(0, 50)
                    bids = [("%.2f" % (lo - 0.5 * rng.randint(0, 80)), "0" if rng.random() < 0.35 else
                             "%.3f" % rng.uniform(0.1, 5.0)) for _ in range(rng.randint(1, 8))]     # 0 — удаление уровня
                    asks = [("%.2f" % (hi + 0.5 * rng.randint(0, 80)), "0" if rng.random() < 0.35 else
                             "%.3f" % rng.uniform(0.1, 5.0)) for _ in range(rng.randint(1, 8))]
                    st.apply_delta(A.BookDelta("fakex_perp", "BTC", 1.0, 1.0, None, seq[0], None, None, bids, asks), 1.0)
                self.assertEqual(st.best(), naive(), (raw, k, st.status))


class TestUnitsAndMeta(unittest.TestCase):
    def test_instrument_selection(self):
        a = F.FakexAdapter()
        m = a.load_meta()
        perp = dict((i.native, i) for i in m["perp"])
        spot = dict((i.native, i) for i in m["spot"])
        self.assertEqual(sorted(perp), ["1000PEPEUSDT", "BTCUSDT", "ETHUSDT", "XYZUSDT"])     # inverse, USDC, срочный, делистинг — нет
        self.assertEqual(sorted(spot), ["BTCUSDT", "ETHUSDT", "PEPEUSDT"])                   # FDUSD, USDC, к BTC, к USD — нет
        self.assertEqual((perp["1000PEPEUSDT"].sym, perp["1000PEPEUSDT"].coin_mult), ("PEPE", 1000.0))
        self.assertEqual(a.meta.code("fakex_perp", "XYZ"), "NOT_LISTED")                    # размер контракта неизвестен
        self.assertEqual(a.meta.code("fakex_perp", "NOPE"), "NOT_LISTED")
        self.assertIsNone(a.meta.code("fakex_perp", "BTC"))
        usd = A.select_instruments(F.INSTRUMENTS["spot"], "spot", allow_usd=True)
        self.assertIn(("BTC", "USD"), [(i.sym, i.quote) for i in usd])                     # Coinbase: BASE-USD
        for base, want in (("1000PEPE", ("PEPE", 1000.0)), ("1MBABYDOGE", ("BABYDOGE", 1e6)), ("10000SATS", ("SATS", 10000.0)),
                           ("1INCH", ("1INCH", 1.0)), ("BTC", ("BTC", 1.0)), ("1000000MOG", ("MOG", 1e6))):
            self.assertEqual(A.base_coin(base), want, base)
        self.assertTrue(a.meta.due("fakex_perp", 6 * 3600.0))
        self.assertFalse(a.meta.due("fakex_perp", 6 * 3600.0 - 1))

    def test_property5_1000pepe_and_contract_size(self):
        """Свойство 5: стакан в контрактах с contract_size и в монетах дают одни USD; 1000PEPE и PEPE с одной ценой за монету — одна
        цена, одни USD и без MISMATCH."""
        a = F.FakexAdapter()
        a.load_meta()
        st = A.BookState("x", "BTC", "next")
        st.apply_snapshot(A.BookSnapshot("x", "BTC", 1, 1, 1, [("100", "2000")], [("101", "3000")]), 0)        # контракты по 0.001
        st2 = A.BookState("x", "BTC", "next")
        st2.apply_snapshot(A.BookSnapshot("x", "BTC", 1, 1, 1, [("100", "2")], [("101", "3")]), 0)              # монеты
        u1 = [p * q for p, q in st.levels(0.001)[0] + st.levels(0.001)[1]]
        u2 = [p * q for p, q in st2.levels()[0] + st2.levels()[1]]
        for x, y in zip(u1, u2):
            self.assertAlmostEqual(x, y, delta=abs(y) * 1e-9)
        pp = A.BookState("x", "PEPE", "next")
        pp.apply_snapshot(A.BookSnapshot("x", "PEPE", 1, 1, 1, [("0.00508", "100")], [("0.00509", "100")]), 0)   # цена за 1000 монет
        sp = A.BookState("x", "PEPE", "next")
        sp.apply_snapshot(A.BookSnapshot("x", "PEPE", 1, 1, 1, [("0.00000508", "100000")], [("0.00000509", "100000")]), 0)
        (pb, pa), (sb, sa) = pp.levels(1.0, 1000.0), sp.levels(1.0, 1.0)
        self.assertAlmostEqual(pb[0][0], sb[0][0], delta=1e-15)
        self.assertAlmostEqual(pb[0][0] * pb[0][1], sb[0][0] * sb[0][1], delta=1e-9)
        px_perp = a.meta.coin_px("fakex_perp", "PEPE", 0.00508)
        px_spot = a.meta.coin_px("fakex_spot", "PEPE", 0.00000508)
        self.assertAlmostEqual(px_perp, px_spot, delta=1e-15)
        bad, unchecked = A.match_markets({"fakex_perp": px_perp, "fakex_spot": px_spot, "other": 5.08e-6})
        self.assertEqual((bad, unchecked), (set(), False))
        tr = a.parse("perp", json.dumps({"ch": "trade", "s": "1000PEPEUSDT", "p": "0.00508", "q": "3", "S": "buy", "id": 1, "E": 1}), 2.0)[0]
        self.assertAlmostEqual(tr.px, 5.08e-6, delta=1e-15)
        self.assertEqual(tr.coins, 3000.0)

    def test_mismatch_three_and_two_markets(self):
        bad, un = A.match_markets({"a": 100.0, "b": 100.5, "c": 100000.0})                     # ×1000 у одного из трёх
        self.assertEqual((bad, un), ({"c"}, False))
        bad, un = A.match_markets({"a": 100.0, "b": 103.0}, ref_px=100.2, ref_age_s=30)       # 2 рынка: сверка с ценой Bybit
        self.assertEqual((bad, un), ({"b"}, False))
        bad, un = A.match_markets({"a": 100.0, "b": 103.0}, ref_px=100.2, ref_age_s=151)      # цена Bybit старше 150 с
        self.assertEqual((bad, un), (set(), True))


class TestTape(unittest.TestCase):
    def test_dedup_side_and_no_lookahead(self):
        tb = A.TapeBuckets()
        tb.set_up(True, 999.5)
        tb.tick(1000)
        self.assertTrue(tb.add(1000.2, 100.0, 1, "t1"))
        self.assertFalse(tb.add(1000.3, 100.0, 1, "t1"))                     # повтор id
        self.assertFalse(tb.add(1000.4, 50.0, 0, "t2"))                      # сторона не распознана
        self.assertTrue(tb.add(1001.0, 30.0, -1, "t3"))
        self.assertTrue(tb.add(1001.01, 7.0, -1, "t4"))                      # t_rx > 1001: в корзину 1002
        self.assertEqual((tb.dups, tb.side_unknown), (1, 1))
        b, s, _ = tb.window(1001, 60)
        self.assertEqual((b, s), (100.0, 30.0))                              # сделка с t_rx > T не входит
        self.assertEqual(tb.window(1002, 60)[1], 37.0)

    def test_gap_lowers_coverage(self):
        tb = A.TapeBuckets()
        tb.set_up(True, 0.5)
        tb.tick(300)
        full = tb.window(300, 60)[2]
        self.assertEqual(full, 1.0)
        tb.set_up(False, 310.2)                                              # переподключение
        tb.tick(320)
        tb.set_up(True, 320.5)
        tb.tick(360)
        cov = tb.window(360, 60)[2]
        self.assertAlmostEqual(cov, (60 - 11) / 60.0)                        # 311..321 — разрыв (11 секунд)
        self.assertLess(cov, L.TAPE_FULL)


class TestPlannerBudget(unittest.TestCase):
    def test_planner_3350_topics(self):
        """Оценка нагрузки design.md: около 3 350 тем, около 35 соединений при пределах 200 / Bitget 50 / OKX 150 / Gate и Coinbase 100."""
        cfg = C.defaults()
        rng = random.Random(7)
        per_mkt = {"binance": (5, 4), "bybit": (4, 2), "okx": (4, 2), "bitget": (4, 2), "gate": (4, 2), "coinbase": (None, 2)}
        total_topics = total_conns = 0
        for venue, (perp_n, spot_n) in per_mkt.items():
            for kind, n_top in (("perp", perp_n), ("spot", spot_n)):
                if n_top is None:
                    continue
                listed = 0.3 if venue == "coinbase" else (0.8 if kind == "perp" else 0.6)     # листинг монет light (допущение design.md)
                syms = ["D%02d" % i for i in range(20)] + ["L%03d" % i for i in range(120) if rng.random() < listed]
                subs = [A.Sub("main", "%s.%s.%d" % (kind, s, k), "x", s) for s in syms for k in range(n_top)]
                p = A.SubPlanner(A.subs_per_conn(cfg, venue))
                r = p.update(subs)
                c = p.counts()
                self.assertEqual(c["topics"], len(subs))
                self.assertLessEqual(c["max_per_conn"], A.subs_per_conn(cfg, venue))
                self.assertEqual(len(r["open"]), c["conns"])
                total_topics += c["topics"]
                total_conns += c["conns"]
        self.assertGreater(total_topics, 3000)
        self.assertLess(total_topics, 3800)
        self.assertLessEqual(total_conns, 45)

    def test_planner_delta_is_stable(self):
        p = A.SubPlanner(3)
        subs = [A.Sub("m", "t%d" % i, "x", "S%d" % i) for i in range(7)]
        r = p.update(subs)
        self.assertEqual(sorted(r["open"]), ["m#0", "m#1", "m#2"])
        where = dict(p.where)
        r = p.update(subs[2:] + [A.Sub("m", "t9", "x", "S9")])
        self.assertEqual(sorted(t.topic for ts in r["remove"].values() for t in ts), ["t0", "t1"])
        self.assertEqual([t.topic for ts in r["add"].values() for t in ts], ["t9"])
        for t in ("t2", "t3", "t4", "t5", "t6"):
            self.assertEqual(p.where[t], where[t])                           # оставшиеся темы не переезжают
        r = p.update([])
        self.assertEqual(sorted(r["close"]), sorted(set(where.values())))
        self.assertEqual(A.split_rejected(["a", "b", "c"]), ([["a"], ["b"], ["c"]], []))
        self.assertEqual(A.split_rejected(["b"]), ([], ["b"]))

    def test_rate_limiter(self):
        rl = A.RateLimiter(5)
        sent = [t / 100.0 for t in range(0, 300) if rl.take(t / 100.0)]
        per_s = max(sum(1 for x in sent if k <= x < k + 1) for k in range(3))
        self.assertLessEqual(per_s, 10)                                      # запас 5 + темп 5 в первую секунду
        self.assertLessEqual(sum(1 for x in sent if 1 <= x < 2), 5)

    def test_rest_budget_reserve_and_penalty(self):
        cfg = C.defaults()
        b = A.RestBudget.from_cfg(cfg, "binance", "perp", n_light=120)
        self.assertEqual((b.total, b.reserve, b.oi_light), (400, 240, 120))
        self.assertEqual(b.avail("snap", 0), 160)                            # снимки — только остаток после резерва OI
        self.assertTrue(b.spend("oi", 240, 0))
        self.assertFalse(b.spend("oi", 1, 1))
        self.assertTrue(b.oi_allowed("light"))
        b.set_resync(5)                                                      # больше 4 стаканов в RESYNC
        self.assertFalse(b.oi_allowed("light"))
        self.assertTrue(b.oi_allowed("deep"))
        self.assertEqual(b.avail("snap", 1), 280)
        b.penalize(2)                                                        # 429
        self.assertFalse(b.spend("snap", 1, 30))
        self.assertTrue(b.spend("snap", 1, 63))
        self.assertTrue(b.spend("oi", 120, 64))                              # окно 60 с освободилось, пауза кончилась

    def test_binance_perp_resync_20_books(self):
        """Requirement 5.5: 20 стаканов deep Binance perp восстанавливаются ≤ 150 с, первые 8 (позиции, планы, ядро) ≤ 60 с при весе 400,
        резерве OI 240 и паузе OI light; снимки — по приоритету."""
        cfg = C.defaults()
        a = fake("binance_perp")
        b = A.RestBudget.from_cfg(cfg, "binance", "perp", n_light=120)
        q = A.ResyncQueue(cfg["book"]["resync_min_s"])
        prios = ["pos"] * 2 + ["plan"] * 3 + ["core"] * 3 + ["deep"] * 12
        syms = ["S%02d" % i for i in range(20)]
        rng = random.Random(3)
        order = list(range(20))
        rng.shuffle(order)
        for i in order:
            q.request(syms[i], prios[i], 0.0)
        b.set_resync(q.pending())
        self.assertTrue(b.spend("meta", 10, 0.0))                            # метаданные и время
        done, got = {}, []
        for t in range(0, 300):
            if t % 10 == 0:
                for _ in range(20):                                          # OI deep: 20 монет раз в 10 с, вес 1
                    b.spend("oi", 1, float(t))
            while True:
                r = q.next(float(t), b, lambda p: a.snapshot_cost("perp", p))
                if r is None:
                    break
                q.done(r[0])
                done[r[0]] = t
                got.append((r[1], r[2]))
            b.set_resync(q.pending())
            if len(done) == 20:
                break
        self.assertEqual(len(done), 20)
        first8 = [done[syms[i]] for i in range(8)]
        self.assertLessEqual(max(first8), 60)
        self.assertLessEqual(max(done.values()), 150)
        self.assertEqual([p for p, _ in got], sorted(p for p, _ in got))     # по приоритету
        self.assertTrue(all(lim == 1000 for p, lim in got if p <= A.PRIO["core"]))
        self.assertTrue(all(lim == 500 for p, lim in got if p > A.PRIO["core"]))
        self.assertFalse(b.light_paused)

    def test_resync_min_gap_per_book(self):
        q = A.ResyncQueue(10)
        q.request("BTC", "core", 0)
        self.assertEqual(q.next(0)[0], "BTC")
        q.done("BTC", ok=False)                                              # снимок не удался: остаётся в очереди
        self.assertIsNone(q.next(5))
        self.assertEqual(q.next(10.0)[0], "BTC")


class TestHttpTimeHosts(unittest.TestCase):
    def test_host_denied_without_network(self):
        cfg = C.defaults()
        calls = []
        h = A.Http(cfg, "binance", opener=lambda *a, **k: calls.append(a))
        for url in ("https://evil.example.com/x", "https://api.coinglass.com/x", "http://fstream.binance.com/x"):
            with self.assertRaises(A.HostDenied) as cm:
                h.get(url)
            self.assertEqual(cm.exception.code, "HOST_DENIED")
        self.assertEqual(calls, [])
        self.assertIsNone(A.host_check(cfg, "binance", "wss://fstream.binance.com/public/stream"))
        self.assertEqual(A.host_check(cfg, "binance", "wss://ws.okx.com:443/ws/v5/public"), "HOST_DENIED")    # чужая биржа
        with self.assertRaises(A.HostDenied):
            A.VenueConn(F.FakexAdapter(cfg), "perp", "main#0", "wss://evil.example.com/ws", cfg=cfg)

    def test_http_budget_and_429(self):
        import io
        import urllib.error
        cfg = C.defaults()
        b = A.RestBudget(10)

        class R(io.BytesIO):
            status = 200
            headers = {"x-mbx-used-weight-1m": "7"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def ok_opener(req, timeout):
            self.assertEqual(timeout, 5.0)
            self.assertNotIn("X-mbx-apikey", req.headers)
            return R(b'{"serverTime": 123}')
        h = A.Http(cfg, "binance", budget=b, opener=ok_opener, clock=lambda: 0.0)
        self.assertEqual(h.get_json("https://www.binance.com/fapi/v1/time", weight=1), {"serverTime": 123})
        self.assertEqual(h.last_weight_hdr, "7")
        with self.assertRaises(A.BudgetExceeded):
            h.get("https://www.binance.com/fapi/v1/depth", weight=20)

        def e429(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, None)
        h2 = A.Http(cfg, "binance", budget=b, opener=e429, clock=lambda: 1.0)
        with self.assertRaises(A.HttpError):
            h2.get("https://www.binance.com/fapi/v1/time")
        self.assertEqual(b.avail("meta", 30.0), 0)                           # пауза 60 с

    def test_http_gzip_and_own_timeout(self):
        """Тело запрашивается сжатым и распаковывается по Content-Encoding (задачи 11–13: списки инструментов Gate, Bitget до 1.7 МБ);
        свой предел запроса передаётся в opener, без него — 5 с."""
        import gzip
        import io
        cfg = C.defaults()
        seen = []

        def opener(req, timeout):
            seen.append((timeout, req.headers.get("Accept-encoding")))
            gz = "gz" in req.full_url

            class R(io.BytesIO):
                status = 200
                headers = {"Content-Encoding": "gzip"} if gz else {}

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False
            body = b'[{"name": "BTC_USDT"}]'
            return R(gzip.compress(body) if gz else body)
        h = A.Http(cfg, "gate", opener=opener)
        self.assertEqual(h.get_json("https://api.gateio.ws/api/v4/gz", timeout=30), [{"name": "BTC_USDT"}])
        self.assertEqual(h.get_json("https://api.gateio.ws/api/v4/plain"), [{"name": "BTC_USDT"}])
        self.assertEqual(seen, [(30.0, "gzip"), (5.0, "gzip")])

    def test_clock_offset_and_skew(self):
        off, rtt = A.best_offset([(0.0, 40.5, 0.4), (1.0, 41.05, 1.1), (2.0, 43.0, 3.0)])
        self.assertAlmostEqual(off, 40.0, places=6)                          # запрос с наименьшим RTT (0.1 с)
        self.assertAlmostEqual(rtt, 0.1, places=6)
        co = A.ClockOffset(600)
        t = [100.0]

        def clock():
            t[0] += 0.01
            return t[0]
        co.measure(lambda: t[0] + 40.0, clock)
        self.assertAlmostEqual(co.offset, 39.995, places=3)
        self.assertFalse(co.due(t[0] + 599))
        self.assertTrue(co.due(t[0] + 601))
        self.assertTrue(A.clock_skew({"a": 600, "b": -700, "c": 501, "d": 40200, "e": 10, "f": 0}))
        self.assertFalse(A.clock_skew({"a": 600, "b": -700, "c": 501, "d": 499, "e": 10, "f": 0}))
        lm = A.LagMeter()
        lm.on_msg(10.0, None)
        self.assertEqual((lm.no_tex, lm.last_ms), (1, 0.0))
        self.assertAlmostEqual(lm.on_msg(10.5, 50.2, 40.0), 300.0, places=6)

    def test_backoff_and_liveness(self):
        bo = A.Backoff(1, 60, 0.2, random.Random(1))
        ds = [bo.next() for _ in range(9)]
        for d, nominal in zip(ds, (1, 2, 4, 8, 16, 32, 60, 60, 60)):
            self.assertGreaterEqual(d, nominal * 0.8 - 1e-9)
            self.assertLessEqual(d, nominal * 1.2 + 1e-9)
        lv = A.Liveness(10, 15)
        lv.on_msg(0.0)
        self.assertTrue(lv.alive(15.0))
        self.assertFalse(lv.alive(15.1))
        lv.on_pong(20.0)
        self.assertTrue(lv.alive(30.0))


class TestRegistry(unittest.TestCase):
    def test_new_venue_is_module_plus_config(self):
        """Новая биржа — модуль tb.cmkt.ex.<venue> и запись конфига: fakex проходит загрузчик конфига и реестр без правки сводки."""
        a = ex.load("fakex")
        self.assertIsInstance(a, A.VenueAdapter)
        self.assertEqual(a.mkt("perp"), "fakex_perp")
        for bad in ("../x", "Binance", "", "os", "fakex.y"):
            with self.assertRaises(ValueError):
                ex.load(bad)
        cfg = C.defaults()
        cfg["venues"]["fakex"] = {"enabled": True, "markets": ["perp", "spot"], "hosts": ["fakex.test", "api.fakex.test"]}
        cfg["limits"]["rest_weight_per_min"]["fakex"] = 60
        self.assertEqual(C.check(json.loads(json.dumps(cfg))), [])
        self.assertIn(("fakex", "perp"), C.markets(cfg))
        for h in a.hosts()["ws"] + a.hosts()["rest"]:
            self.assertTrue(C.host_allowed(cfg, "fakex", h), h)


class TestConn(unittest.IsolatedAsyncioTestCase):
    def cfg(self, **sync):
        c = copy.deepcopy(C.defaults())
        c["sync"].update({"ping_s": 0.2, "silent_s": 0.8}, **sync)
        c["limits"].update({"backoff_s": 0.05, "backoff_max_s": 0.2, "sub_msgs_per_s": 5})
        return c

    async def start(self, **kw):
        _bootstrap.require_localhost()
        srv = await F.FakeServer(**kw).start()
        self.addAsyncCleanup(srv.stop)
        return srv

    async def conn(self, srv, subs, cfg, events=None, status=None):
        a = F.FakexAdapter(cfg, base_url=srv.url)
        a.load_meta()
        c = A.VenueConn(a, "perp", "main#0", srv.url, subs, on_events=(lambda evs, t: events.extend(evs)) if events is not None else None,
                        on_status=(lambda ev, info: status.append(ev)) if status is not None else None, cfg=cfg, allow_plain=True,
                        rng=random.Random(1))
        task = asyncio.ensure_future(c.run())

        async def stop():
            await c.close()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):                      # noqa: BLE001
                pass
        self.addAsyncCleanup(stop)
        return a, c

    async def until(self, pred, timeout=5.0):
        end = asyncio.get_event_loop().time() + timeout
        while not pred():
            if asyncio.get_event_loop().time() > end:
                return False
            await asyncio.sleep(0.02)
        return True

    async def test_paced_subscribe_reject_retry_and_events(self):
        srv = await self.start(reject={"ticker.ETHUSDT"})
        cfg = self.cfg()
        a = F.FakexAdapter(cfg)
        a.load_meta()
        subs = a.plan("perp", ["BTC", "ETH"], ["PEPE"])
        events, status = [], []
        _, c = await self.conn(srv, subs, cfg, events, status)
        self.assertTrue(await self.until(lambda: srv.peers and len(srv.peers[0].topics) >= len(subs) - 1))
        peer = srv.peers[0]
        self.assertNotIn("ticker.ETHUSDT", peer.topics)
        self.assertIn("book.BTCUSDT", peer.topics)
        self.assertEqual(c.stats["sub_rejected"], 1)                         # отклонена одна тема, остальные запроса подписаны
        self.assertIn("rejected", status)
        await peer.send(json.dumps({"ch": "trade", "s": "BTCUSDT", "p": "100", "q": "2000", "S": "sell", "id": 5, "E": 1793750400000}))
        self.assertTrue(await self.until(lambda: events))
        self.assertEqual(events[0].coins, 2.0)
        self.assertEqual(events[0].side, -1)
        self.assertEqual(status[0], "connect")

    async def test_reconnect_resubscribes_and_reports(self):
        async def script(peer):
            if peer.index == 0:
                await F.wait_topics(peer, 4)
                await asyncio.sleep(0.05)
                peer.drop()                                                  # обрыв соединения биржей
                return
            await asyncio.sleep(10)
        srv = await self.start(script=script)
        cfg = self.cfg()
        a = F.FakexAdapter(cfg)
        a.load_meta()
        status = []
        _, c = await self.conn(srv, a.plan("perp", ["BTC"], []), cfg, status=status)
        self.assertTrue(await self.until(lambda: len(srv.peers) >= 2 and len(srv.peers[1].topics) >= 4))
        self.assertEqual(status[:2], ["connect", "reconnect"])
        self.assertEqual(c.stats["reconnects"], 1)

    async def test_quiet_market_alive_by_pong_and_silence_reconnects(self):
        async def quiet(peer):
            await asyncio.sleep(10)
        srv = await self.start(script=quiet)
        cfg = self.cfg()
        _, c = await self.conn(srv, [], cfg)
        self.assertTrue(await self.until(lambda: c.connected))
        await asyncio.sleep(1.2)                                             # дольше silent_s: живо только за счёт pong на пинг
        self.assertTrue(c.alive())
        self.assertEqual(len(srv.peers), 1)
        srv2 = await self.start(script=quiet, answer_ping=False)
        status = []
        _, c2 = await self.conn(srv2, [], cfg, status=status)
        self.assertTrue(await self.until(lambda: "reconnect" in status, 5.0))   # молчание дольше silent_s -> переподключение

    async def test_fixture_replay(self):
        """fakex воспроизводит фикстуру живой биржи: все сообщения доходят до соединения (разбор — адаптер биржи, задачи 8–13)."""
        lines = F.load_fixture(os.path.join(FIX, "binance", "perp.jsonl"))[:200]
        self.assertEqual(len(lines), 200)

        async def replay(peer):
            await asyncio.sleep(0.05)
            for _, m in lines:
                await peer.send(m)
            await asyncio.sleep(10)
        srv = await self.start(script=replay)
        _, c = await self.conn(srv, [], self.cfg(silent_s=5.0))
        self.assertTrue(await self.until(lambda: c.stats["msgs"] >= 200))
        self.assertEqual(c.stats["parse_err"], 0)


if __name__ == "__main__":
    unittest.main()
