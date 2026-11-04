"""tb.logic.cmkt: чистая математика сводного рынка (R1.7–R1.9, задача 3; свойства 5–11, 16, 19; Requirements 5.7, 6–11, 17.6)."""
import json
import math
import os
import random
import shutil
import tempfile
import time
import unittest

import _bootstrap  # noqa: F401
from tb.logic import cmkt as L


def mkt(kind="perp", w=1e8, mid=100.0, code=None, quote="USDT", **kw):
    s = {"kind": kind, "w": w, "mid": mid, "code": code, "quote": quote}
    s.update(kw)
    return s


def book(mid, n=40, step_bps=10, coins_per_level=1.0, cs=1.0, mult=1.0):
    """Уровни стакана вокруг mid (цена за монету): n уровней на сторону через step_bps; в единицах инструмента (contract_size, coin_mult)."""
    bids = [(mid * (1 - (i + 1) * step_bps / 1e4) * mult, coins_per_level / cs / mult) for i in range(n)]
    asks = [(mid * (1 + (i + 1) * step_bps / 1e4) * mult, coins_per_level / cs / mult) for i in range(n)]
    return bids, asks


def full_state(rng, kind="perp", mid=100.0, w=None):
    """Случайное состояние включённого рынка со всеми полями."""
    bids, asks = book(mid, coins_per_level=rng.uniform(100, 2000))
    bl, al = L.norm_levels(bids), L.norm_levels(asks)
    m = L.book_mid(bl, al)
    s = mkt(kind, w=w or rng.uniform(1e7, 1e9), mid=m, bands=L.bands(bl, al, m, L.depth_pct(bl, al, m)),
            tape={lab: [rng.uniform(1e5, 1e7), rng.uniform(1e5, 1e7), 1.0] for lab, _ in L.WINDOWS},
            pulled={"bid": [rng.randint(0, 3), rng.uniform(0, 1e6)], "ask": [rng.randint(0, 3), rng.uniform(0, 1e6)]},
            walls=[{"side": "bid", "px": m * 0.99, "usd": rng.uniform(5e5, 2e6), "age_s": 60}])
    if kind == "perp":
        now = rng.uniform(1e4, 1e6)
        s.update(oi={"now": now, "1m": now * rng.uniform(0.98, 1.02), "5m": now * rng.uniform(0.95, 1.05), "15m": now * rng.uniform(0.9, 1.1)},
                 mark=m, fund=rng.uniform(-1e-3, 1e-3), fund_h=rng.choice([1, 4, 8]),
                 liq={"has": True, "partial": False, "5m": [rng.uniform(0, 1e6), rng.uniform(0, 1e6)],
                      "1h": [rng.uniform(0, 1e7), rng.uniform(0, 1e7)], "5m_coins": [rng.uniform(0, 100), rng.uniform(0, 100)]})
    return s


def full_mk(seed=1, n_perp=3, n_spot=2):
    rng = random.Random(seed)
    mk = {}
    names_p = ["binance_perp", "bybit_perp", "okx_perp", "bitget_perp", "gate_perp"][:n_perp]
    names_s = ["binance_spot", "bybit_spot", "okx_spot", "bitget_spot", "gate_spot"][:n_spot]
    for m in names_p:
        mk[m] = full_state(rng, "perp", 100.0 * rng.uniform(0.999, 1.001))
    for m in names_s:
        mk[m] = full_state(rng, "spot", 100.0 * rng.uniform(0.999, 1.001))
    return mk


class TestConstants(unittest.TestCase):
    def test_features_list(self):
        self.assertEqual(len(L.CM_FEATURES), 40)
        self.assertEqual(len(set(L.CM_FEATURES)), 40)
        self.assertTrue(all(n.startswith("cm_") for n in L.CM_FEATURES))
        self.assertEqual(len(L.FIRST_WAVE), 4)
        self.assertTrue(set(L.FIRST_WAVE) <= set(L.CM_FEATURES))
        self.assertTrue(set(L.FILL_ORDER) <= set(L.CM_FEATURES) and not set(L.FILL_ORDER) & set(L.FIRST_WAVE))
        self.assertEqual(len(L.MARKETS), 11)
        self.assertEqual(L.MARKET_INDEX["coinbase_spot"], 10)
        self.assertRegex(L.CM_VER, r"^[0-9a-f]{8}$")

    def test_first_wave_band_replacement(self):
        self.assertEqual(L.first_wave(), L.FIRST_WAVE)
        self.assertEqual(L.first_wave(0.25), L.FIRST_WAVE)
        self.assertIn("cm_imb_025_pct", L.FIRST_WAVE)                            # замена Requirement 6.3 включена задачей 2
        self.assertNotIn("cm_imb_05_pct", L.FIRST_WAVE)
        fw = L.first_wave(0.5)
        self.assertIn("cm_imb_05_pct", fw)
        self.assertNotIn("cm_imb_025_pct", fw)
        self.assertEqual(len(fw), 4)

    def test_first_wave_fill_replacement(self):
        """Задача 2: cm_cvd_ps_5m_pct (0.76) и cm_prem_ps_bps (0.78) ниже 0.8 вынесены в FILL_ORDER; первая волна — по одному
        признаку от семейства (дубли cm_oi_chg_5m_pct и cm_cvd_5m_pct — в FILL_ORDER), 2026-11-04."""
        self.assertEqual(L.FIRST_WAVE, ("cm_imb_025_pct", "cm_oi_liq_5m_pct", "cm_cvd_5m_perp_pct", "cm_wall_pull_15m_pct"))
        self.assertEqual(L.FILL_ORDER[:2], ("cm_cvd_ps_5m_pct", "cm_prem_ps_bps"))
        self.assertTrue({"cm_oi_chg_5m_pct", "cm_cvd_5m_pct"} <= set(L.FILL_ORDER))

    def test_property_16_menu_bound(self):
        """Свойство 16 (часть логики): K_MAX = 17 + 4 × 2 = 25."""
        self.assertEqual(L.wave_k_max(), 25)
        self.assertEqual(L.wave_k_max(()), 17)


class TestUnits(unittest.TestCase):
    def test_property_5_contract_size(self):
        """Свойство 5: стакан в контрактах с contract_size и в монетах дают одинаковые USD полос, корзин и OI (1e-9)."""
        rng = random.Random(5)
        for _ in range(50):
            cs = rng.choice([0.001, 0.01, 0.1, 1, 10, 100])
            mid = rng.uniform(0.0001, 100000)
            b1, a1 = book(mid, cs=1.0)
            b2, a2 = book(mid, cs=cs)
            l1 = (L.norm_levels(b1), L.norm_levels(a1))
            l2 = (L.norm_levels(b2, cs), L.norm_levels(a2, cs))
            m1, m2 = L.book_mid(*l1), L.book_mid(*l2)
            self.assertAlmostEqual(m1 / m2, 1.0, delta=1e-12)
            bd1 = L.bands(l1[0], l1[1], m1, L.depth_pct(l1[0], l1[1], m1))
            bd2 = L.bands(l2[0], l2[1], m2, L.depth_pct(l2[0], l2[1], m2))
            for k, v in bd1.items():
                if v is None:
                    self.assertIsNone(bd2[k])
                    continue
                for x, y in zip(v, bd2[k]):
                    self.assertAlmostEqual(x / y, 1.0, delta=1e-9)
            k1, k2 = L.buckets(l1[0], l1[1], m1), L.buckets(l2[0], l2[1], m2)
            for side in ("bid", "ask"):
                for x, y in zip(k1[side], k2[side]):
                    self.assertTrue(abs(x - y) <= 1e-9 * max(abs(x), 1e-300))
            qty = rng.uniform(1, 1e6)
            self.assertAlmostEqual(L.coins(qty / cs, cs) * mid, qty * mid, delta=1e-9 * qty * mid)

    def test_property_5_1000pepe(self):
        """1000PEPE (цена за 1000 монет, coin_mult 1000) и PEPE с той же ценой за монету: одинаковые цена, USD, без MISMATCH."""
        px = 0.000012
        b_pepe, a_pepe = book(px, coins_per_level=1e9)
        b_k, a_k = book(px, coins_per_level=1e9, mult=1000.0)
        self.assertAlmostEqual(b_k[0][0], px * 1000 * (1 - 10 / 1e4))
        lp = (L.norm_levels(b_pepe), L.norm_levels(a_pepe))
        lk = (L.norm_levels(b_k, 1.0, 1000.0), L.norm_levels(a_k, 1.0, 1000.0))
        mp, mk_ = L.book_mid(*lp), L.book_mid(*lk)
        self.assertAlmostEqual(mp / mk_, 1.0, delta=1e-12)
        bp = L.bands(lp[0], lp[1], mp, L.depth_pct(lp[0], lp[1], mp))
        bk = L.bands(lk[0], lk[1], mk_, L.depth_pct(lk[0], lk[1], mk_))
        for k in bp:
            for x, y in zip(bp[k], bk[k]):
                self.assertAlmostEqual(x / y, 1.0, delta=1e-9)
        self.assertAlmostEqual(L.coin_px(0.012, 1000), px)
        self.assertEqual(L.coins(5, 1, 1000), 5000)
        bad, unchecked = L.mismatch({"bybit_perp": L.coin_px(0.012, 1000), "binance_perp": px, "okx_perp": px * 1.001})
        self.assertEqual(bad, set())
        self.assertFalse(unchecked)
        # без деления на множитель цена отличалась бы в 1000 раз -> MISMATCH
        bad, _ = L.mismatch({"bybit_perp": 0.012, "binance_perp": px, "okx_perp": px})
        self.assertEqual(bad, {"bybit_perp"})

    def test_three_markets_with_different_contract_sizes(self):
        """Эталон: три рынка с contract_size 1, 0.01, 10 и одинаковым стаканом в монетах дают сумму полос = 3 × одного."""
        mid = 50000.0
        st = {}
        for name, cs in (("binance_perp", 1.0), ("okx_perp", 0.01), ("gate_perp", 10.0)):
            b, a = book(mid, coins_per_level=2.0, cs=cs)
            bl, al = L.norm_levels(b, cs), L.norm_levels(a, cs)
            m = L.book_mid(bl, al)
            st[name] = mkt("perp", w=1e8, mid=m, bands=L.bands(bl, al, m, L.depth_pct(bl, al, m)))
        one = st["binance_perp"]["bands"]["05"]
        ag = L.agg_bands(st)["all"]["05"]
        self.assertAlmostEqual(ag["bid"], 3 * one[0], delta=1e-6)
        self.assertAlmostEqual(ag["ask"], 3 * one[1], delta=1e-6)
        self.assertEqual(ag["n_ex"], 3)
        # 5 уровней в ±0.5 % по 2 монеты: bid = Σ 2 × px
        exp = sum(2 * mid * (1 - (i + 1) * 10 / 1e4) for i in range(5))
        self.assertAlmostEqual(one[0], exp, delta=1e-6)

    def test_levels_from_strings(self):
        self.assertEqual(L.norm_levels([["100.5", "2"], ["x", "1"], ["99", "0"], [98]]), [(100.5, 2.0)])


class TestBook(unittest.TestCase):
    def test_band_coverage(self):
        bids, asks = book(100.0, n=6, step_bps=10)                 # выдача ±0.6 %
        bl, al = L.norm_levels(bids), L.norm_levels(asks)
        m = L.book_mid(bl, al)
        d = L.depth_pct(bl, al, m)
        self.assertAlmostEqual(d, 0.6, places=9)
        bd = L.bands(bl, al, m, d)
        self.assertIsNotNone(bd["025"])
        self.assertIsNotNone(bd["05"])
        self.assertIsNone(bd["1"])
        self.assertIsNone(bd["2"])
        # граница REST-снимка: глубина выдачи = граница
        self.assertAlmostEqual(L.depth_pct(bl, al, m, bound_bid=99.0, bound_ask=101.5), 1.0)
        self.assertIsNone(L.book_mid([(101, 1)], [(100, 1)]))                # перекрёст
        self.assertIsNone(L.book_mid([], [(100, 1)]))

    def test_property_6_attribution_and_bounds(self):
        """Свойство 6: сумма атрибуции = USD; дисбаланс в [−100, 100]; рынок с пустым стаканом не меняет корзин."""
        rng = random.Random(6)
        for seed in range(30):
            mk = full_mk(seed)
            for m in mk:
                bl, al = L.norm_levels(book(mk[m]["mid"], coins_per_level=rng.uniform(1, 100))[0]), \
                    L.norm_levels(book(mk[m]["mid"], coins_per_level=rng.uniform(1, 100))[1])
                mk[m]["buckets"] = L.buckets(bl, al, L.book_mid(bl, al))
            bd = L.agg_bands(mk)
            for kind, row in bd.items():
                for label, r in row.items():
                    if r is None:
                        continue
                    self.assertAlmostEqual(sum(v[0] for v in r["by"].values()), r["bid"], delta=1e-6)
                    self.assertAlmostEqual(sum(v[1] for v in r["by"].values()), r["ask"], delta=1e-6)
                    self.assertTrue(-100 <= r["imb"] <= 100)
            bk = L.agg_buckets(mk)
            for kind, row in bk.items():
                for side, lst in row.items():
                    for cell in lst:
                        self.assertAlmostEqual(sum(cell["by"].values()), cell["usd"], delta=1e-6)
            mk2 = dict(mk)
            mk2["gate_perp"] = mkt("perp", w=1e8, mid=None, bands={lab: None for lab, _ in L.BANDS}, buckets={"bid": [], "ask": []})
            self.assertEqual(L.agg_buckets(mk2), bk)
            b2 = L.agg_bands(mk2)
            for kind in bd:
                for label in bd[kind]:
                    if bd[kind][label] is None:
                        continue
                    self.assertEqual(b2[kind][label]["bid"], bd[kind][label]["bid"])
                    self.assertEqual(b2[kind][label]["by"], bd[kind][label]["by"])
        for _ in range(500):
            b, a = rng.uniform(0, 1e9), rng.uniform(0, 1e9)
            v = L.imbalance(b, a)
            self.assertTrue(-100 <= v <= 100)
        self.assertEqual(L.imbalance(1, 0), 100)
        self.assertIsNone(L.imbalance(0, 0))

    def test_buckets_layout(self):
        mid = 100.0
        bk = L.buckets([(99.9, 1.0), (98.0, 1.0), (97.0, 1.0)], [(100.3, 2.0)], mid, 25, 200)
        self.assertEqual(len(bk["bid"]), 8)
        self.assertAlmostEqual(bk["bid"][0], 99.9)          # 10 б.п.
        self.assertAlmostEqual(bk["bid"][7], 98.0)          # ровно 200 б.п. -> последняя корзина
        self.assertAlmostEqual(sum(bk["bid"]), 99.9 + 98.0)  # 300 б.п. — за пределом
        self.assertAlmostEqual(bk["ask"][1], 200.6)         # 30 б.п.

    def test_only_covering_markets_enter_band(self):
        mk = {"a_perp": mkt("perp", w=3e8, bands={"05": [10.0, 5.0]}), "b_perp": mkt("perp", w=1e8, bands={"05": None}),
              "c_spot": mkt("spot", w=1e8, bands={"05": [1.0, 1.0]}), "d_spot": mkt("spot", w=1e8, code="LAG", bands={"05": [100.0, 0.0]}),
              "cb": mkt("spot", w=5e8, quote="USD", has_book=False)}
        bd = L.agg_bands(mk)
        self.assertEqual(bd["perp"]["05"]["n_ex"], 1)
        self.assertAlmostEqual(bd["perp"]["05"]["cov"], 0.75)
        self.assertEqual(bd["spot"]["05"]["by"], {"c_spot": [1.0, 1.0]})       # исключённый не входит
        self.assertAlmostEqual(bd["spot"]["05"]["cov"], 0.5)                    # Coinbase без стакана вне знаменателя
        self.assertEqual(bd["all"]["05"]["bid"], 11.0)
        self.assertIsNone(bd["perp"]["1"])

    def test_only_one_kind(self):
        """Requirement 6.7: только perp — стакан по нему, spot — None."""
        mk = full_mk(3, n_perp=3, n_spot=0)
        r = L.features(mk, 1000)
        self.assertIsNotNone(r["f"]["cm_imb_05_pct"])
        self.assertIsNotNone(r["f"]["cm_imb_05_perp_pct"])
        self.assertIsNone(r["f"]["cm_imb_05_spot_pct"])
        self.assertIsNone(r["f"]["cm_cvd_5m_spot_pct"])
        self.assertIsNone(r["f"]["cm_prem_ps_bps"])


class TestWalls(unittest.TestCase):
    def test_candidates(self):
        mid = 100.0
        bids = [(99.9 - i * 0.05, 1000.0) for i in range(20)]
        bids[3] = (bids[3][0], 10000.0)                                     # ≈ 1 млн USD при медиане ≈ 0.1 млн
        asks = [(100.1 + i * 0.05, 1000.0) for i in range(20)]
        c = L.wall_candidates(bids, asks, mid, 100000, 6)
        self.assertEqual(list(c), [("bid", bids[3][0])])
        self.assertEqual(L.wall_candidates(bids, asks, mid, 2e6, 6), {})        # порог по абсолютному USD

    def test_property_11_classification(self):
        """Свойство 11: каждый конец устойчивой стены — ровно один код; стена, которой коснулись, никогда не pulled."""
        rng = random.Random(11)
        for _ in range(400):
            tr = L.WallTracker(persist_s=30, touch_bps=5, drop_share=0.7, window_s=5)
            px, key = 100.0, ("bid", 100.0)
            usd0 = rng.uniform(5e5, 5e6)
            life = rng.randint(1, 80)
            touched = False
            ends = []
            for t in range(life):
                ends += tr.update(t, {key: usd0 * rng.uniform(0.8, 1.0)})
                if rng.random() < 0.05:                                  # сделка, пока стена стоит
                    tp = px * (1 + rng.uniform(-8, 8) / 1e4)
                    tr.trade(t, tp)
                    touched = touched or abs(tp - px) / px * 1e4 <= 5
            tail = rng.choice(["pull", "fade"])
            t = life
            if tail == "fade":
                for k in range(10):
                    ends += tr.update(t + k, {key: usd0 * (0.8 - 0.06 * k)})
                t += 10
            ends += tr.update(t, {}, lookup=lambda s, p: 0.0)
            self.assertLessEqual(len(ends), 1)
            if t - 1 < 30:                                              # последний кадр стены — t − 1
                self.assertEqual(ends, [])                               # не устойчивая — конца нет
                continue
            self.assertEqual(len(ends), 1)
            code = ends[0][4]
            self.assertIn(code, ("touched", "pulled", "faded"))
            if touched:
                self.assertEqual(code, "touched")
            elif tail == "pull":
                self.assertEqual(code, "pulled")

    def test_pulled_vs_faded_and_ends(self):
        tr = L.WallTracker()
        key = ("ask", 101.0)
        for t in range(40):
            tr.update(t, {key: 1e6})
        self.assertEqual(tr.stable(39), [{"side": "ask", "px": 101.0, "usd": 1e6, "age_s": 39}])
        ends = tr.update(40, {}, lookup=lambda s, p: 2e5)                 # −80 % за 1 с, касаний нет
        self.assertEqual(ends[0][4], "pulled")
        self.assertEqual(tr.ends(41), {"bid": [0, 0.0], "ask": [1, 1e6]})
        self.assertEqual(tr.ends(41 + 900), {"bid": [0, 0.0], "ask": [0, 0.0]})
        tr = L.WallTracker()
        for t in range(40):
            tr.update(t, {key: 1e6})
        ends = tr.update(40, {}, lookup=lambda s, p: 5e5)                 # −50 %: faded
        self.assertEqual(ends[0][4], "faded")
        tr = L.WallTracker()
        for t in range(40):
            tr.update(t, {key: 1e6})
            if t == 20:
                tr.trade(t, 101.04)                                       # 4 б.п.: касание
        self.assertEqual(tr.update(40, {}, lookup=lambda s, p: 0.0)[0][4], "touched")

    def test_aggregate_walls(self):
        walls = {"binance_perp": [{"side": "bid", "px": 99.0, "usd": 300000, "age_s": 40}],
                 "okx_perp": [{"side": "bid", "px": 99.04, "usd": 200000, "age_s": 100}],      # 4 б.п. — та же стена
                 "bybit_perp": [{"side": "bid", "px": 98.0, "usd": 350000, "age_s": 50},
                                {"side": "ask", "px": 102.0, "usd": 900000, "age_s": 35}]}
        out = L.agg_walls(walls, 100.0, 5, 400000)
        bid = [w for w in out if w["side"] == "bid"]
        self.assertEqual(len(bid), 1)                                     # 350 000 у 98.0 ниже порога
        self.assertEqual(bid[0]["usd"], 500000)
        self.assertEqual(bid[0]["n_ex"], 2)
        self.assertEqual(bid[0]["mkts"], ["binance_perp", "okx_perp"])
        self.assertEqual(bid[0]["px"], 99.0)
        self.assertAlmostEqual(bid[0]["dist_pct"], 1.0)
        self.assertEqual(bid[0]["age_s"], 100)
        ask = [w for w in out if w["side"] == "ask"]
        self.assertEqual(ask[0]["n_ex"], 1)


class TestInclusion(unittest.TestCase):
    def test_codes(self):
        T = 1000
        self.assertIsNone(L.market_code(T, T, 1.0, 300))
        self.assertEqual(L.market_code(T, T, 1.0, 300, enabled=False), "OFF")
        self.assertEqual(L.market_code(T, T, 1.0, 300, listed=False), "NOT_LISTED")
        self.assertEqual(L.market_code(T, T, 1.0, 300, connected=False), "DOWN")
        self.assertEqual(L.market_code(T, T, 16.0, 300), "SILENT")
        self.assertIsNone(L.market_code(T, T, 15.0, 300))                    # тихий рынок, пинг отвечает
        self.assertEqual(L.market_code(T, T - 2, 1.0, 300), "STALE")
        self.assertIsNone(L.market_code(T, T - 1, 1.0, 300))
        self.assertEqual(L.market_code(T, T + 1, 1.0, 300), "STALE")         # публикация из будущего не берётся
        self.assertEqual(L.market_code(T, T, 1.0, 300, book_status="resync"), "RESYNC")
        self.assertIsNone(L.market_code(T, T, 1.0, 300, book_status="book_part"))
        self.assertEqual(L.market_code(T, T, 1.0, 1001), "LAG")
        self.assertIsNone(L.market_code(T, T, 1.0, 1500, sync={"lag_max_ms_venue": {"okx": 1600}}, venue="okx"))
        self.assertEqual(L.market_code(T, T, 1.0, 1500, sync={"lag_max_ms_venue": {"okx": 1600}}, venue="gate"), "LAG")
        self.assertEqual(L.lag_max_ms({"lag_max_ms_venue": {"okx": 5000}}, "okx"), 2000)
        # light: от последней отметки, кратной 5
        self.assertIsNone(L.market_code(1004, 1000, 1.0, 300, light=True))
        self.assertEqual(L.market_code(1005, 1000, 1.0, 300, light=True), "STALE")
        self.assertIsNone(L.market_code(1005, 1005, 1.0, 300, light=True))

    def test_property_19_skew(self):
        """Свойство 19: у включённых рынков skew_ms ≤ 1000 + lag_max_ms."""
        rng = random.Random(19)
        for _ in range(300):
            T = 10 ** 9 + rng.randint(0, 1000)
            sync = {"lag_max_ms_venue": {"okx": rng.randint(1000, 2000)}}
            inc = []
            for v in ("binance", "okx", "gate"):
                t_pub, lag = T - rng.randint(0, 3), rng.uniform(0, 3000)
                if L.market_code(T, t_pub, 1.0, lag, sync=sync, venue=v) is None:
                    inc.append((T - t_pub, lag, L.lag_max_ms(sync, v)))
            sk = L.skew_ms([(d, lg) for d, lg, _ in inc])
            if inc:
                self.assertLessEqual(sk, 1000 + max(m for _, _, m in inc))
            else:
                self.assertIsNone(sk)

    def test_property_19_lead_needs_offset_quality(self):
        series, qual = self.lead_fixture()
        self.assertEqual(L.lead_market(series, 1000, qual)["mkt"], "binance_perp")
        for bad in ((101, 10), (50, 901), (None, 10)):
            q = dict(qual)
            q["okx_perp"] = bad
            self.assertIsNone(L.lead_market(series, 1000, q), bad)
        r = L.features(full_mk(2), 1000, lead=L.lead_market(series, 1000, qual))
        self.assertEqual(r["f"]["cm_lead_ex"], 0)
        self.assertEqual(r["f"]["cm_lead_lag_s"], 2)
        self.assertIsNone(L.features(full_mk(2), 1000, lead=None)["f"]["cm_lead_ex"])
        self.assertIsNone(L.features(full_mk(2), 200, lead=L.lead_market(series, 1000, qual))["f"]["cm_lead_ex"])   # прогрев

    @staticmethod
    def lead_fixture():
        rng = random.Random(7)
        T = 1000
        lead_px = [100.0]
        for _ in range(T + 5):
            lead_px.append(lead_px[-1] * (1 + rng.gauss(0, 1e-4)))
        series = {"binance_perp": {}, "okx_perp": {}, "bybit_perp": {}}
        for t in range(T - 320, T + 1):
            series["binance_perp"][t] = lead_px[t]
            series["okx_perp"][t] = lead_px[t - 2] * (1 + rng.gauss(0, 2e-6))       # отстаёт на 2 с
            series["bybit_perp"][t] = lead_px[t - 2] * (1 + rng.gauss(0, 2e-6))
        qual = {m: (40.0, 100.0) for m in series}
        return series, qual

    def test_lead_none_without_enough_returns(self):
        series = {"a": {t: 100.0 for t in range(0, 301)}, "b": {t: 100.0 for t in range(0, 301)}}
        self.assertIsNone(L.lead_market(series, 300, {"a": (1, 1), "b": (1, 1)}))
        self.assertIsNone(L.lead_market({"a": {1: 1.0}}, 300, {"a": (1, 1)}))

    def test_mismatch(self):
        bad, un = L.mismatch({"a": 100.0, "b": 100.5, "c": 103.0})
        self.assertEqual((bad, un), ({"c"}, False))
        bad, un = L.mismatch({"a": 100.0, "b": 103.0})
        self.assertEqual((bad, un), (set(), True))                               # 2 рынка без цены Bybit: mm_unchecked
        bad, un = L.mismatch({"a": 100.0, "b": 103.0}, ref_px=100.2)
        self.assertEqual((bad, un), ({"b"}, False))
        self.assertEqual(L.mismatch({}), (set(), False))


class TestFlows(unittest.TestCase):
    def test_property_7_oi_price_invariance(self):
        """Свойство 7: умножение всех цен на константу при тех же монетах OI не меняет cm_oi_chg_* и cm_oi_liq_5m_pct;
        рынок без потока ликвидаций не входит в cm_oi_liq_5m_pct."""
        for seed in range(40):
            mk = full_mk(seed, n_perp=4, n_spot=1)
            r1 = L.features(mk, 1000)["f"]
            k = random.Random(seed).uniform(0.01, 100)
            mk2 = json.loads(json.dumps(mk))
            for s in mk2.values():
                s["mid"] *= k
                if "mark" in s:
                    s["mark"] *= k
            r2 = L.features(mk2, 1000)["f"]
            for n in ("cm_oi_chg_1m_pct", "cm_oi_chg_5m_pct", "cm_oi_chg_15m_pct", "cm_oi_liq_5m_pct"):
                self.assertIsNotNone(r1[n], n)
                self.assertAlmostEqual(r1[n], r2[n], delta=1e-9 * max(1.0, abs(r1[n])), msg=n)
        mk = full_mk(1, n_perp=3, n_spot=0)
        base = L.oi_liq(mk)
        mk["gate_perp"] = full_state(random.Random(9), "perp", 100.0)
        mk["gate_perp"]["liq"]["has"] = False
        mk["gate_perp"]["oi"]["now"] *= 0.001                                     # мал: покрытие набора не падает
        self.assertAlmostEqual(L.oi_liq(mk), base)
        mk["gate_perp"]["oi"]["5m"] = 1.0                                        # огромное ΔOI вне набора не влияет
        self.assertAlmostEqual(L.oi_liq(mk), base)

    def test_oi_liq_formula(self):
        mk = {"a": mkt("perp", mark=10.0, oi={"now": 1000.0, "5m": 900.0}, liq={"has": True, "5m_coins": [30.0, 20.0]}),
              "b": mkt("perp", mark=10.0, oi={"now": 1000.0, "5m": 1000.0}, liq={"has": True, "5m_coins": [0.0, 0.0]}),
              "c": mkt("perp", mark=10.0, oi={"now": 2000.0, "5m": 1000.0}, liq={"has": False})}
        self.assertAlmostEqual(L.oi_liq(mk), (100 + 50) / 2000 * 100)            # c вне набора; 2000/4000 = 0.5 ≥ liq_min_cov
        mk["c"]["oi"]["now"] = 2100.0
        self.assertIsNone(L.oi_liq(mk))                                          # покрытие 2000/4100 < 0.5
        chg = L.oi_change({"a": mk["a"], "b": mk["b"]}, "5m")
        self.assertAlmostEqual(chg[0], (2000 / 1900 - 1) * 100)

    def test_oi_change_set_and_cov(self):
        mk = {"a": mkt("perp", mark=1.0, oi={"now": 100.0, "5m": 100.0}), "b": mkt("perp", mark=1.0, oi={"now": 100.0, "5m": None}),
              "c": mkt("perp", mark=1.0, oi={"now": 100.0, "5m": 50.0}), "d": mkt("perp", code="DOWN", oi_usd=100.0)}
        v, n, cov = L.oi_change(mk, "5m")
        self.assertEqual(n, 2)
        self.assertAlmostEqual(cov, 0.5)                                         # исключённый d — в знаменателе по последнему OI
        self.assertIsNone(v)                                                     # cov 0.5 < 0.6
        del mk["d"]
        v, n, cov = L.oi_change(mk, "5m")
        self.assertAlmostEqual(v, (200 / 150 - 1) * 100)

    def test_property_8_cvd_decomposition(self):
        """Свойство 8: net_all = net_perp + net_spot в USD каждого окна; cm_cvd_ps_5m_pct = perp − spot."""
        for seed in range(40):
            mk = full_mk(seed, n_perp=3, n_spot=3)
            rng = random.Random(seed)
            for s in mk.values():
                if rng.random() < 0.3:
                    s["tape"]["5m"][2] = 0.5                                     # разрыв ленты: рынок вне суммы
            for lab, _ in L.WINDOWS:
                c = L.agg_cvd(mk, lab)
                self.assertAlmostEqual(c["all"]["net"], c["perp"]["net"] + c["spot"]["net"], delta=1e-6)
                self.assertEqual(c["all"]["n_ex"], c["perp"]["n_ex"] + c["spot"]["n_ex"])
            f = L.features(mk, 1000)["f"]
            if f["cm_cvd_5m_perp_pct"] is not None and f["cm_cvd_5m_spot_pct"] is not None:
                self.assertAlmostEqual(f["cm_cvd_ps_5m_pct"], f["cm_cvd_5m_perp_pct"] - f["cm_cvd_5m_spot_pct"])
            else:
                self.assertIsNone(f["cm_cvd_ps_5m_pct"])

    def test_cvd_gap_lowers_coverage(self):
        mk = {"a": mkt("perp", w=1e8, tape={"5m": [10.0, 5.0, 1.0]}), "b": mkt("perp", w=3e8, tape={"5m": [10.0, 5.0, 0.8]})}
        c = L.agg_cvd(mk, "5m")["perp"]
        self.assertEqual(c["n_ex"], 1)
        self.assertAlmostEqual(c["cov"], 0.25)
        self.assertEqual(c["net"], 5.0)

    def test_property_9_coverage_thresholds(self):
        """Свойство 9: n_ex < min_n_ex или cov < min_cov -> None; liq_cov < liq_min_cov -> признаки ликвидаций None, а не 0."""
        mk = full_mk(4, n_perp=1, n_spot=0)
        f = L.features(mk, 5000)["f"]
        for n in ("cm_imb_05_pct", "cm_cvd_5m_pct", "cm_oi_musd", "cm_oi_chg_5m_pct", "cm_fund_w_pct", "cm_liq_5m_long_musd",
                  "cm_wall_bid_musd", "cm_depth_05_musd"):
            self.assertIsNone(f[n], n)                                           # один рынок
        mk = full_mk(4, n_perp=3, n_spot=0)
        for m in list(mk)[:2]:
            mk[m]["w"] = 1.0
        mk["okx_perp"]["w"] = 1e9
        mk["okx_perp"]["code"] = "LAG"                                           # cov включённых ≈ 0
        f = L.features(mk, 5000)["f"]
        self.assertIsNone(f["cm_imb_05_pct"])
        self.assertIsNone(f["cm_cvd_5m_pct"])
        mk = full_mk(4, n_perp=3, n_spot=0)
        for m in mk:
            mk[m]["liq"]["has"] = False
        f = L.features(mk, 5000)["f"]
        self.assertEqual(f["cm_liq_cov"], 0.0)
        for n in ("cm_liq_5m_long_musd", "cm_liq_5m_short_musd", "cm_liq_1h_net_musd", "cm_oi_liq_5m_pct"):
            self.assertIsNone(f[n], n)
        mk = full_mk(4, n_perp=3, n_spot=0)
        f = L.features(mk, 5000)["f"]
        self.assertIsNotNone(f["cm_liq_5m_long_musd"])
        self.assertIsNotNone(f["cm_liq_1h_net_musd"])

    def test_warmup(self):
        mk = full_mk(5)
        f = L.features(mk, 100)["f"]
        for n in ("cm_cvd_5m_pct", "cm_cvd_15m_pct", "cm_oi_chg_5m_pct", "cm_oi_chg_15m_pct", "cm_oi_liq_5m_pct", "cm_wall_pull_15m_pct",
                  "cm_pulled_15m_n", "cm_liq_5m_long_musd", "cm_cvd_ps_5m_pct"):
            self.assertIsNone(f[n], n)
        self.assertIsNotNone(f["cm_cvd_1m_pct"])
        self.assertIsNotNone(f["cm_oi_chg_1m_pct"])
        self.assertIsNotNone(f["cm_imb_05_pct"])
        self.assertIn("warmup", L.features(mk, 100)["flags"])
        f = L.features(mk, 900)["f"]
        self.assertIsNotNone(f["cm_wall_pull_15m_pct"])
        self.assertIsNone(f["cm_liq_1h_net_musd"])
        self.assertNotIn("warmup", L.features(mk, 900)["flags"])

    def test_all_excluded(self):
        mk = full_mk(6)
        for s in mk.values():
            s["code"] = "DOWN"
        r = L.features(mk, 5000)
        self.assertEqual(r["f"], dict.fromkeys(L.CM_FEATURES))
        self.assertEqual(r["n_ex"], 0)
        self.assertEqual(L.features({}, 5000)["f"], dict.fromkeys(L.CM_FEATURES))

    def test_property_2_excluded_market_ignored(self):
        """Изменение данных исключённого рынка не меняет признаков (кроме знаменателей по его весу, который не меняем)."""
        mk = full_mk(8, n_perp=4, n_spot=3)
        mk["okx_perp"]["code"] = "LAG"
        f1 = L.features(mk, 5000)["f"]
        mk2 = json.loads(json.dumps(mk))
        x = mk2["okx_perp"]
        x["mid"] *= 1.5
        x["bands"] = {k: [9e9, 1.0] for k in x["bands"]}
        x["tape"] = {k: [9e9, 0.0, 1.0] for k in x["tape"]}
        x["oi"] = {"now": x["oi"]["now"], "1m": 1.0, "5m": 1.0, "15m": 1.0}
        x["fund"] = 0.5
        x["liq"]["5m"] = [9e9, 9e9]
        x["walls"] = [{"side": "ask", "px": 1.0, "usd": 9e9, "age_s": 999}]
        x["pulled"] = {"bid": [99, 9e9], "ask": [0, 0.0]}
        self.assertEqual(L.features(mk2, 5000)["f"], f1)


class TestFundingPremia(unittest.TestCase):
    def test_property_10(self):
        """Свойство 10: веса funding ≥ 0 и в сумме 1; f за 4 ч = 2f за 8 ч; перестановка рынков не меняет премию; середина двух — среднее;
        при курсе 1 и равных серединах премия Coinbase 0."""
        for seed in range(30):
            mk = full_mk(seed, n_perp=4, n_spot=3)
            w = L.fund_weights(mk)
            self.assertTrue(all(v >= 0 for v in w.values()))
            self.assertAlmostEqual(sum(w.values()), 1.0)
            items = list(mk.items())
            random.Random(seed).shuffle(items)
            self.assertEqual(L.prem_ps(dict(items)), L.prem_ps(mk))
        self.assertAlmostEqual(L.fund_8h(0.0001, 4), 0.0002)
        self.assertAlmostEqual(L.fund_8h(0.0001, 8), 0.0001)
        self.assertAlmostEqual(L.fund_8h(0.0001, 1), 0.0008)
        self.assertIsNone(L.fund_8h(0.0001, 0))
        self.assertEqual(L.consensus_mid([100.0, 102.0]), 101.0)
        self.assertEqual(L.consensus_mid([100.0, 102.0, 150.0]), 102.0)
        self.assertEqual(L.consensus_mid([7.0]), 7.0)
        self.assertIsNone(L.consensus_mid([None]))
        mk = {"binance_spot": mkt("spot", mid=100.0), "coinbase_spot": mkt("spot", mid=100.0, quote="USD", has_book=False)}
        self.assertAlmostEqual(L.prem_cb(mk, 1.0), 0.0)

    def test_funding_4h_and_8h(self):
        """Эталон: Binance 8 ч 0.01 %, OKX 4 ч 0.01 % (= 0.02 % за 8 ч), равный OI -> взвешенный 0.015 %, разброс 0.01 %."""
        mk = {"binance_perp": mkt("perp", mark=100.0, oi={"now": 1000.0}, fund=0.0001, fund_h=8),
              "okx_perp": mkt("perp", mark=100.0, oi={"now": 1000.0}, fund=0.0001, fund_h=4)}
        fw, sp, n, cov = L.agg_funding(mk)
        self.assertAlmostEqual(fw, 0.015)
        self.assertAlmostEqual(sp, 0.01)
        self.assertEqual(n, 2)
        mk["okx_perp"]["oi"]["now"] = 3000.0
        self.assertAlmostEqual(L.agg_funding(mk)[0], (0.01 * 1 + 0.02 * 3) / 4)

    def test_coinbase_premium_rate_0999(self):
        """Эталон: Coinbase BTC-USD 100 000, спот к USDT 100 000, курс USDT/USD 0.999 -> (100000 / 99900 − 1) × 1e4 ≈ 10.01 б.п."""
        mk = {"binance_spot": mkt("spot", mid=100000.0), "okx_spot": mkt("spot", mid=100000.0),
              "coinbase_spot": mkt("spot", mid=100000.0, quote="USD", has_book=False)}
        self.assertAlmostEqual(L.prem_cb(mk, 0.999), (100000 / 99900 - 1) * 1e4)
        self.assertIsNone(L.prem_cb(mk, None))                                   # курса нет (или старше 10 с)
        del mk["coinbase_spot"]
        self.assertIsNone(L.prem_cb(mk, 0.999))

    def test_premium_two_markets(self):
        mk = {"binance_perp": mkt("perp", mid=101.0), "okx_perp": mkt("perp", mid=103.0), "binance_spot": mkt("spot", mid=100.0),
              "coinbase_spot": mkt("spot", mid=90.0, quote="USD")}
        self.assertAlmostEqual(L.prem_ps(mk), (102.0 / 100.0 - 1) * 1e4)           # Coinbase (USD) не в середине спота
        self.assertIsNone(L.prem_ps({"a": mkt("perp", mid=1.0)}))
        mk["binance_spot"]["code"] = "STALE"
        self.assertIsNone(L.prem_ps(mk))


class TestEncoding(unittest.TestCase):
    def test_f_roundtrip_all_fields(self):
        rng = random.Random(12)
        for _ in range(200):
            vals = {}
            for n in L.CM_FEATURES:
                r = rng.random()
                if r < 0.2:
                    vals[n] = None
                elif n in L.INT_FEATURES:
                    vals[n] = rng.randint(-50, 5000) if n not in ("cm_n_perp", "cm_n_spot") else rng.randint(0, 6)
                else:
                    vals[n] = rng.uniform(-1e4, 1e4) * rng.choice([1e-6, 1e-3, 1, 1e3])
            blob = L.encode_f(vals)
            back = L.decode_f(blob)
            self.assertEqual(set(back), set(L.CM_FEATURES))
            q = L.quantized(vals)
            self.assertEqual(back, q)
            for n in L.CM_FEATURES:
                if vals[n] is None:
                    self.assertIsNone(back[n])
                else:
                    self.assertLessEqual(abs(back[n] - vals[n]), 0.5 / L.SCALES[n] + 1e-9, n)
            self.assertEqual(L.encode_f(back), blob)                               # идемпотентно
        self.assertEqual(L.encode_f({}), b"\x00" * 40)
        self.assertEqual(L.decode_f(b"\x00" * 40), dict.fromkeys(L.CM_FEATURES))
        with self.assertRaises(ValueError):
            L.decode_f(b"\x00" * 39)
        with self.assertRaises(ValueError):
            L.decode_f(b"\x00" * 41)

    def test_features_encode(self):
        f = L.features(full_mk(13), 5000)["f"]
        self.assertEqual(L.decode_f(L.encode_f(f)), L.quantized(f))

    def test_by_ex_roundtrip(self):
        ids = {m: i + 1 for i, m in enumerate(L.MARKETS)}
        names = {v: k for k, v in ids.items()}
        rows = L.by_ex_rows(full_mk(14, 3, 2))
        self.assertEqual(len(rows), 5)
        back = L.decode_by_ex(L.encode_by_ex(rows, ids), names)
        self.assertEqual([r["mkt"] for r in back], [r["mkt"] for r in rows])
        for a, b in zip(rows, back):
            for fld in ("cvd5", "liq5_long", "liq5_short"):
                if a[fld] is None:
                    self.assertIsNone(b[fld])
                else:
                    self.assertLessEqual(abs(a[fld] - b[fld]), 500.0 + 1e-6)
            self.assertEqual(a["pulled15"], b["pulled15"])
            if a["doi5"] is None:
                self.assertIsNone(b["doi5"])
            else:
                self.assertLessEqual(abs(a["doi5"] - b["doi5"]), max(abs(r["doi5"] or 0) for r in rows) * 1e-3)
        spot = [r for r in back if r["mkt"].endswith("_spot")]
        self.assertTrue(all(r["doi5"] is None and r["liq5_long"] is None for r in spot))
        none = [{"mkt": "okx_perp", "cvd5": None, "doi5": None, "liq5_long": None, "liq5_short": None, "pulled15": None}]
        self.assertEqual(L.decode_by_ex(L.encode_by_ex(none, ids), names), none)
        self.assertEqual(L.decode_by_ex(b"", names), [])
        big = [{"mkt": "binance_perp", "cvd5": -1.2e9, "doi5": -3.4e9, "liq5_long": 0.0, "liq5_short": 1e6, "pulled15": 7}]
        r = L.decode_by_ex(L.encode_by_ex(big, ids), names)[0]
        self.assertAlmostEqual(r["doi5"] / -3.4e9, 1.0, places=3)
        self.assertEqual(r["cvd5"], -1.2e9)

    def test_flags(self):
        self.assertEqual(L.flags_of(L.flag_bits(["warmup", "liq_partial"])), ["liq_partial", "warmup"])
        self.assertEqual(L.flag_bits([]), 0)


class TestEvCm(unittest.TestCase):
    def test_ev_cm_shape_and_limit(self):
        f = L.quantized(L.features(full_mk(15), 5000)["f"])
        ev = L.ev_cm(f, "deep", 5000, 1699999999)
        self.assertEqual(ev["v"], L.CM_VER)
        self.assertEqual((ev["tier"], ev["focus_s"], ev["row_ts"]), ("deep", 5000, 1699999999))
        self.assertFalse([k for k, v in ev.items() if v is None])
        self.assertLessEqual(len(json.dumps(ev, separators=(",", ":")).encode("utf-8")), 1000)
        worst = {n: (-123456789.123456 if n not in L.INT_FEATURES else 123456) for n in L.CM_FEATURES}
        ev = L.ev_cm(worst, "light", 10 ** 6, 10 ** 10, max_bytes=600)
        self.assertLessEqual(len(json.dumps(ev, separators=(",", ":")).encode("utf-8")), 600)
        self.assertTrue(set(L.FIRST_WAVE) <= set(ev))                              # первая волна не отбрасывается
        self.assertNotIn("cm_lead_lag_s", ev)                                      # отбрасывание с конца

    def test_row_selection(self):
        """Свойство 1 (часть логики): строка признаков строго раньше t0."""
        rng = random.Random(1)
        for _ in range(1000):
            t0 = rng.randint(10 ** 9, 2 * 10 ** 9)
            for step in (1, 5):
                r = L.row_ts(t0, step)
                self.assertLess(r, t0)
                self.assertEqual(r % step, 0)
                self.assertGreaterEqual(r, t0 - 2 * step)
        self.assertEqual(L.row_ts(1003, 5), 995)
        self.assertEqual(L.row_ts(1005, 5), 1000)
        self.assertEqual(L.row_ts(1005, 1), 1004)
        self.assertEqual(L.row_deadline(1004, 3), 1008)
        self.assertTrue(L.row_on_time(1004, 1007.0))
        self.assertFalse(L.row_on_time(1004, 1007.5))


class TestPurity(unittest.TestCase):
    def test_module_is_pure(self):
        import test_layering as TL
        path = os.path.join(_bootstrap.SRC, "tb", "logic", "cmkt.py")
        src = TL.read(path)
        self.assertEqual(TL.logic_violations(src), [])
        self.assertEqual([i for i in TL.imports_of(src, "tb.logic.cmkt") if i.startswith("tb")], [])


class TestCompat(unittest.TestCase):
    """compat_* равны оригиналам xstream.symbol_set, xflow.book_stats, xflow.walls на одной фикстуре (тест импортирует оригиналы)."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tb-cmkt-compat-")
        os.makedirs(os.path.join(self.root, "runtime"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_symbols(self):
        from tb import paths
        from tb.xdata import xstream
        now = time.time()
        with open(paths.exec_snapshot_file(root=self.root), "w", encoding="utf-8") as f:
            json.dump({"positions": [{"sym": "DOGE"}, {"sym": "ETH"}], "plans": [{"sym": "ARB"}, {"sym": "DOGE"}]}, f)
        with open(paths.runtime("structure_req.json", root=self.root), "w", encoding="utf-8") as f:
            json.dump({"req": {"WIF": now - 10, "SOL": now - 5, "OLD": now - 4000}}, f)
        for extra in ([], ["PEPE", "BTC", "SUI", "TIA", "SEI", "APT", "NEAR"], None, ["X", 5]):
            for n in (3, 8, 20):
                exp = xstream.symbol_set(self.root, extra, n, now)
                got = L.compat_symbols(xstream.position_symbols(self.root), xstream.requested_symbols(self.root, now), extra, n)
                self.assertEqual(got, exp, (extra, n))

    def ob_fixture(self, seed):
        rng = random.Random(seed)
        mid = rng.uniform(0.01, 50000)
        bids = [[mid * (1 - (i + 1) * rng.uniform(0.0002, 0.002)), rng.uniform(0.01, 100) * (20 if rng.random() < 0.05 else 1)]
                for i in range(rng.randint(5, 300))]
        asks = [[mid * (1 + (i + 1) * rng.uniform(0.0002, 0.002)), rng.uniform(0.01, 100) * (20 if rng.random() < 0.05 else 1)]
                for i in range(rng.randint(5, 300))]
        if rng.random() < 0.1:
            bids.append(["bad", 1])
        return {"bids": bids, "asks": asks}, rng.choice([1.0, 0.01, 10.0])

    def test_book_stats_and_walls(self):
        from tb.intel import xflow
        for seed in range(60):
            ob, cs = self.ob_fixture(seed)
            b1, b2 = {}, {}
            r1 = xflow.book_stats(ob, cs, b1, 0.1)
            r2 = L.compat_book_stats(ob, cs, b2, 0.1)
            self.assertEqual(r1, r2)
            self.assertEqual({k: v[:3] for k, v in b1.items()}, {k: v[:3] for k, v in b2.items()})
            if r1 is None:
                continue
            for mult, mn in ((5.0, 200000.0), (3.0, 0.0), (5.0, 1e12)):
                self.assertEqual(xflow.walls(b1, r1["mid"], mult, mn, 0.1), L.compat_walls(b2, r2["mid"], mult, mn, 0.1))
        self.assertEqual(xflow.book_stats({"bids": [], "asks": [[1, 1]]}, 1.0), L.compat_book_stats({"bids": [], "asks": [[1, 1]]}, 1.0))
        self.assertEqual(xflow.book_stats({"bids": [[2, 1]], "asks": [[1, 1]]}, 1.0), L.compat_book_stats({"bids": [[2, 1]], "asks": [[1, 1]]}, 1.0))

    def test_merge_like_document(self):
        """Сводные корзины нескольких бирж (как XStream.document) -> одинаковые стены у оригинала и копии."""
        from tb.intel import xflow
        books1, books2 = {}, {}
        for i, e in enumerate(("okx", "bitget", "binanceusdm")):
            ob, cs = self.ob_fixture(100 + i)
            ob = {"bids": [[p * 1.0, a] for p, a in ob["bids"] if isinstance(p, float)], "asks": ob["asks"]}
            b1, b2 = {}, {}
            r1, r2 = xflow.book_stats(ob, cs, b1), L.compat_book_stats(ob, cs, b2)
            books1[e] = {"mid": r1["mid"], "buckets": b1}
            books2[e] = {"mid": r2["mid"], "buckets": b2}
        mid, agg = L.compat_merge(books2)
        mids = sorted(b["mid"] for b in books1.values())
        self.assertEqual(mid, mids[len(mids) // 2])
        exp = {}
        for e, b in books1.items():
            for k, v in b["buckets"].items():
                a = exp.setdefault(k, [0.0, 0.0, v[2], set()])
                a[0] += v[0]
                if v[1] > a[1]:
                    a[1], a[2] = v[1], v[2]
                a[3].add(e)
        self.assertEqual(xflow.walls(exp, mid, 5.0, 200000.0, 0.1), L.compat_walls(agg, mid, 5.0, 200000.0, 0.1))


if __name__ == "__main__":
    unittest.main()
