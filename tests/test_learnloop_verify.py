"""tb.learnloop.stat/verify/menu/candidates (R2, задача 7): свойства 5, 6, 7, 8, 9, 12 design.md (и 10 в сквозном пути) — ручные эталоны парного t
и Бонферрони, общий сдвиг часа не меняет парную статистику, кластер с вкладом в обе группы, меню q80/q20 и coin_class (K ≤ 17, без side и
utc_session, TOO_BROAD, COVERAGE_LOW, BLOCKED), min_n_menu по формуле, effect, детерминизм и порядок строк, holdout ровно 7 суток с одним тестом
в cut_ts, коды N_TOO_SMALL/NO_OOS_SIGN/NOT_SIGNIFICANT, симуляции 400 наборов и 200 серий × 8 меню на нулевых данных (фиксированный seed), сквозной
путь на SQLite с эффектом до урока hyp, NOT_DUE, NO_MENU, NO_DATA, CTX_CHANGED, DUP_ACTIVE по правилу trial и уроку, RESTORED, SAMPLE_CHANGED,
BUDGET, чёрный список, лимит приёма. Временные корни, сети нет."""
import json
import math
import os
import random
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from test_learnloop_clusters import CFG, D, H, MCFG, POLICY, T0
from tb.core.expstats import t_two_sided_p
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import candidates as CA
from tb.learnloop import clusters as CL
from tb.learnloop import conf as LC
from tb.learnloop import menu as MN
from tb.learnloop import stat as ST
from tb.learnloop import verify as VF
from tb.logic import learn_cond
from tb.logic import learn_rules as LR
from tb.learnloop import rules as RU
from tb.memory import lessons as LS

COINS = (("meme", 0.25), ("alt_small", 0.5), ("alt_mid", 0.7), ("stock_token", 0.85), ("major", 0.95), (None, 1.0))
NUM = learn_cond.MENU_NUM


def coin(u):
    for c, edge in COINS:
        if u < edge:
            return c
    return None


def gen_rows(rnd, h0, hours, runs=3, side="long", effect=0.0, eff_feat="range_5m_pct", eff_thr=0.8):
    """Синтетические строки выборки окна: один кластер на час, общий сдвиг часа, признаки независимы от R (кроме эффекта)."""
    rows = []
    rand, gauss = rnd.random, rnd.gauss
    for h in range(h0, h0 + hours):
        shift = gauss(0.0, 0.5)
        cl = "%d:%s" % (h, side)
        for i in range(runs):
            f = {"range_5m_pct": rand(), "ret_5m_pct": rand(), "turn_surge_5m": rand(), "chg_24h_pct": rand(), "spread_bps": rand(),
                 "funding_pct": rand(), "coin_class": coin(rand()), "utc_session": "asia", "side": side}
            r = shift + gauss(0.0, 1.0) + (effect if f[eff_feat] >= eff_thr else 0.0)
            rows.append({"cluster": cl, "hour": h, "side": side, "key": "k%d-%d" % (h, i), "t0": h * H + i * 60, "filled": 1, "r_net": r,
                         "feats": f, "flags": None})
    return rows


def win_of(rows, a, b):
    return {"rows": rows, "a": a, "b": b}


def pair_rows(diffs, h0=T0 // H, extra_shift=None):
    """По кластеру на разность: один прогон в условии (range_5m_pct=1, R = shift + d), один вне (0, R = shift)."""
    rows = []
    for i, d in enumerate(diffs):
        h = h0 + i
        s = (extra_shift or {}).get(i, 0.0)
        for j, (x, r) in enumerate(((1.0, s + d), (0.0, s))):
            rows.append({"cluster": "%d:long" % h, "hour": h, "side": "long", "key": "p%d-%d" % (i, j), "t0": h * H + j, "filled": 1, "r_net": r,
                         "feats": {"range_5m_pct": x}, "flags": None})
    return rows


COND = [{"feature": "range_5m_pct", "op": ">=", "value": 0.5}]


# ---------------------------------------------------------------- статистика

class TestStat(unittest.TestCase):
    def test_manual_paired_t(self):
        d = [-1.0, -2.0, 0.0, -3.0, -1.0]
        st = ST.paired(d, "lower")
        self.assertAlmostEqual(st["mean"], -1.4)
        self.assertAlmostEqual(st["sd"], math.sqrt(1.3))
        self.assertAlmostEqual(st["t"], -1.4 / (math.sqrt(1.3) / math.sqrt(5)), places=9)
        self.assertEqual(st["df"], 4)
        self.assertAlmostEqual(st["p2"], t_two_sided_p(st["t"], 4))
        self.assertAlmostEqual(st["p"], st["p2"] / 2.0)
        self.assertAlmostEqual(ST.paired_pvalue(d, "higher"), 1.0 - st["p2"] / 2.0)
        self.assertTrue(0.05 < st["p2"] < 0.06)                     # t = 2.7456 при df = 4 чуть ниже критического 2.7764

    def test_t_reference_values(self):
        self.assertAlmostEqual(t_two_sided_p(2.776445, 4), 0.05, places=5)
        self.assertAlmostEqual(t_two_sided_p(2.228139, 10), 0.05, places=5)
        self.assertAlmostEqual(t_two_sided_p(1.983972, 100), 0.05, places=5)

    def test_edges(self):
        self.assertIsNone(ST.paired_pvalue([1.0], "lower"))
        self.assertIsNone(ST.paired_pvalue([], "lower"))
        self.assertEqual(ST.paired_pvalue([0.0, 0.0, 0.0], "lower"), 0.5)
        self.assertEqual(ST.paired_pvalue([-1.0, -1.0], "lower"), 0.0)
        self.assertEqual(ST.paired_pvalue([-1.0, -1.0], "higher"), 1.0)
        with self.assertRaises(ValueError):
            ST.paired_pvalue([1.0, float("nan")])
        with self.assertRaises(ValueError):
            ST.one_sided(1.0, 0.5, "up")

    def test_t1_against_level(self):
        xs = [0.3, 0.1, 0.5, 0.2]
        self.assertAlmostEqual(ST.t1_pvalue(xs, 0.1, "higher"), ST.paired_pvalue([x - 0.1 for x in xs], "higher"))


# ---------------------------------------------------------------- кластеры теста и тесты

class TestVerifyPure(unittest.TestCase):
    def test_groups_and_test_clusters(self):
        rows = pair_rows([-0.5, 0.25])
        rows.append({"cluster": "%d:long" % (T0 // H + 5), "hour": T0 // H + 5, "side": "long", "key": "only-in", "t0": (T0 // H + 5) * H,
                     "filled": 1, "r_net": 1.0, "feats": {"range_5m_pct": 1.0}, "flags": None})
        tc = VF.test_clusters(rows, COND)
        self.assertEqual([x["diff"] for x in tc], [-0.5, 0.25])
        self.assertEqual([(x["n_in"], x["n_out"]) for x in tc], [(1, 1), (1, 1)])
        self.assertEqual([x["t0"] for x in tc], [T0, T0 + H])

    def test_each_run_in_one_group(self):
        rows = gen_rows(random.Random(3), T0 // H, 50, runs=6)
        tc = VF.test_clusters(rows, [{"feature": "ret_5m_pct", "op": ">=", "value": 0.6}])
        per = {}
        for r in rows:
            per[r["cluster"]] = per.get(r["cluster"], 0) + 1
        for x in tc:
            self.assertEqual(x["n_in"] + x["n_out"], per[x["cluster"]])

    def test_hit_vector_matches_matches_features(self):
        rows = gen_rows(random.Random(4), T0 // H, 30)
        rows[0]["feats"]["range_5m_pct"] = None
        rows[1]["feats"]["range_5m_pct"] = True
        p = VF.prepared(rows)
        for cond in ([{"feature": "range_5m_pct", "op": ">=", "value": 0.5}], [{"feature": "range_5m_pct", "op": "<=", "value": 0.5}],
                     [{"feature": "coin_class", "op": "=", "value": "meme"}], [{"feature": "coin_class", "op": "in", "value": ["meme", "major"]}],
                     [{"feature": "range_5m_pct", "op": ">=", "value": 0.2}, {"feature": "coin_class", "op": "=", "value": "meme"}]):
            self.assertEqual(VF.hit_vector(p, cond), [learn_cond.matches_features(cond, r["feats"]) for r in p["m"]])

    def test_active_set_removes_runs_first(self):
        rows = pair_rows([-1.0] * 3)
        tc = VF.test_clusters(rows, COND, active=[[{"feature": "range_5m_pct", "op": "<=", "value": 0.0}]])
        self.assertEqual(tc, [], "активный набор отсёк группу вне условия: кластеров теста нет")
        self.assertEqual(len(VF.test_clusters(rows, COND, active=[{"cond": [{"feature": "range_5m_pct", "op": ">=", "value": 9}]}])), 3)

    def test_common_hour_shift_does_not_change_paired_stat(self):
        rnd = random.Random(11)
        rows = gen_rows(rnd, T0 // H, 200, runs=5)
        cond = [{"feature": "spread_bps", "op": ">=", "value": 0.7}]
        base = VF.test_clusters(rows, cond)
        shifted = [dict(r, r_net=r["r_net"] + (5.0 if r["hour"] % 3 == 0 else -2.5)) for r in rows]
        sh = VF.test_clusters(shifted, cond)
        self.assertEqual(len(base), len(sh))
        for x, y in zip(base, sh):
            self.assertAlmostEqual(x["diff"], y["diff"], places=9)
        self.assertAlmostEqual(VF.numbers(base)["p"], VF.numbers(sh)["p"], places=8)

    def test_effect(self):
        self.assertEqual(VF.effect([]), 0.0)
        self.assertEqual(VF.effect([{"diff": -0.123}, {"diff": -0.125}]), 0.12)
        self.assertEqual(VF.effect([{"diff": -40.0}]), 10.0)

    def test_bonferroni_screen(self):
        rnd = random.Random(5)
        diffs = [-0.2 + rnd.gauss(0, 0.9) for _ in range(120)]
        rows = pair_rows(diffs)
        w = win_of(rows, T0, T0 + 120 * H)
        p = VF.numbers(VF.test_clusters(rows, COND))["p"]
        self.assertTrue(0.0005 < p < 0.04, p)
        k1 = int(CFG["verify"]["alpha"] / p)
        self.assertTrue(1 <= k1 <= 17)
        ok = VF.screen(w, COND, k1, CFG)
        self.assertTrue(ok["ok"], ok)
        self.assertAlmostEqual(ok["thr"], 0.05 / k1)
        bad = VF.screen(w, COND, k1 + 1, CFG)
        self.assertEqual(bad["code"], "NOT_SIGNIFICANT")

    def test_screen_codes(self):
        w = win_of(pair_rows([-1.0 + 0.01 * i for i in range(50)]), T0, T0 + 50 * H)
        self.assertEqual(VF.screen(w, COND, 1, CFG)["code"], "BELOW_N")
        diffs = [-0.8] * 60 + [0.3] * 60                         # эффект только в первой половине окна
        w = win_of(pair_rows([d + 0.001 * (i % 7) for i, d in enumerate(diffs)]), T0, T0 + 120 * H)
        self.assertEqual(VF.screen(w, COND, 1, CFG)["code"], "NO_OOS_SIGN")

    def test_holdout_codes_and_threshold(self):
        kh = VF.k_h(CFG)
        self.assertEqual(kh, 3)
        st, code, nums = VF.holdout(win_of(pair_rows([-1.0] * 29), T0, T0 + 29 * H), COND, kh, CFG)
        self.assertEqual((st, code), ("dropped", "N_TOO_SMALL"))
        st, code, _ = VF.holdout(win_of(pair_rows([0.5 + 0.01 * i for i in range(40)]), T0, T0 + 40 * H), COND, kh, CFG)
        self.assertEqual((st, code), ("dropped", "NO_OOS_SIGN"))
        diffs = [-1.1 if i % 2 else 0.9 for i in range(40)]            # среднее −0.1 при sd около 1: знак верный, p велико
        st, code, nums = VF.holdout(win_of(pair_rows(diffs), T0, T0 + 40 * H), COND, kh, CFG)
        self.assertAlmostEqual(nums["thr"], 0.05 / 3)
        self.assertEqual((st, code), ("dropped", "NOT_SIGNIFICANT"))
        # порог alpha / K_h: p между 0.05 / 3 и 0.05 проходит при K_h = 1 и отклоняется при K_h = 3
        diffs = [-0.35 + (1.0 if i % 2 else -1.0) for i in range(40)]
        p = VF.numbers(VF.test_clusters(pair_rows(diffs), COND))["p"]
        self.assertTrue(0.05 / 3 < p < 0.05, p)
        self.assertEqual(VF.holdout(win_of(pair_rows(diffs), T0, T0 + 40 * H), COND, 1, CFG)[0], "hyp")
        self.assertEqual(VF.holdout(win_of(pair_rows(diffs), T0, T0 + 40 * H), COND, kh, CFG)[1], "NOT_SIGNIFICANT")
        st, code, nums = VF.holdout(win_of(pair_rows([-1.0 + 0.01 * (i % 5) for i in range(40)]), T0, T0 + 40 * H), COND, kh, CFG)
        self.assertEqual((st, code), ("hyp", None))
        self.assertEqual(len(nums["clusters"]), 5)

    def test_cond_h(self):
        a = [{"feature": "range_5m_pct", "op": ">=", "value": 1.0}]
        self.assertEqual(LR.cond_h(a, "lower"), LR.cond_h([{"value": 1.0, "op": ">=", "feature": "range_5m_pct"}], "lower"))
        self.assertNotEqual(LR.cond_h(a, "lower"), LR.cond_h(a, "higher"))
        self.assertEqual(len(LR.cond_h(a)), 16)
        cand = {"cond": a, "claim": {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.2}}
        self.assertEqual(CA.cand_h(cand), LR.cond_h(a, "lower"))
        m = MN.build_menu(win_of(gen_rows(random.Random(1), T0 // H, 14 * 24), T0, T0 + 14 * D), CFG)
        for it in m["items"]:
            self.assertEqual(it["cond_h"], LR.cond_h(it["cond"], "lower"))


# ---------------------------------------------------------------- меню

class TestMenuPure(unittest.TestCase):
    def test_min_n_menu_formula(self):
        self.assertEqual(LC.min_n_menu_formula(150, 0.8, 30, 14), 88)
        self.assertEqual(CFG["verify"]["min_n_menu"], 88)
        self.assertEqual(MN.K_MAX, 17)

    def test_quantile(self):
        self.assertIsNone(MN.quantile([], 0.8))
        self.assertAlmostEqual(MN.quantile([4, 1, 3, 2, 5], 0.8), 4.2)
        self.assertAlmostEqual(MN.quantile([4, 1, 3, 2, 5], 0.2), 1.8)

    def test_menu_shape(self):
        rows = gen_rows(random.Random(21), T0 // H, 14 * 24, runs=4)
        m = MN.build_menu(win_of(rows, T0, T0 + 14 * D), CFG)
        self.assertLessEqual(m["k"], 17)
        feats = [it["cond"][0]["feature"] for it in m["items"]]
        self.assertNotIn("side", feats)
        self.assertNotIn("utc_session", feats)
        ops = sorted((it["cond"][0]["feature"], it["cond"][0]["op"]) for it in m["items"] if it["cond"][0]["feature"] in NUM)
        self.assertEqual(ops, sorted((f, o) for f in NUM for o in (">=", "<=")))
        coins = sorted(it["cond"][0]["value"] for it in m["items"] + m["dropped"] if it.get("cond") and it["cond"][0]["feature"] == "coin_class")
        self.assertEqual(coins, sorted(learn_cond.menu_cat_values("coin_class")))
        self.assertNotIn("unknown", coins)
        q = [it["cond"][0]["value"] for it in m["items"] if it["cond"] == [{"feature": "range_5m_pct", "op": ">=", "value": it["cond"][0]["value"]}]]
        xs = [r["feats"]["range_5m_pct"] for r in rows]
        self.assertAlmostEqual(q[0], MN.quantile(xs, 0.8), places=5)
        for it in m["items"]:
            self.assertEqual(learn_cond.validate_cond(it["cond"]), [])
            self.assertEqual(LS.validate_cond(it["cond"]), [])
            self.assertEqual(LS.validate_claim(it["claim"]), [])
            self.assertEqual(set(it["claim"]), {"metric", "dir", "vs", "effect"})
            self.assertEqual((it["claim"]["dir"], it["claim"]["vs"]), ("lower", "base"))
            self.assertGreaterEqual(it["n"], 88)
            self.assertLessEqual(it["share"], 0.3)
        self.assertEqual({d["code"] for d in m["dropped"] if d.get("cond") and d["cond"][0]["feature"] == "coin_class"} - {"BELOW_N"}, set())

    def test_too_broad_coverage_low_blocked(self):
        rows = gen_rows(random.Random(22), T0 // H, 14 * 24, runs=4)
        for i, r in enumerate(rows):
            r["feats"]["funding_pct"] = 0.005 if i % 2 else round(r["feats"]["funding_pct"] * 0.004, 6)   # общая ставка: q80 вырожден
            r["feats"]["spread_bps"] = None if i % 4 else r["feats"]["spread_bps"]                  # заполнение 25% < 0.8
        w = win_of(rows, T0, T0 + 14 * D)
        m = MN.build_menu(w, CFG)
        codes = dict((d["cond"][0]["feature"] + d["cond"][0]["op"] if d.get("cond") else d["feature"], d["code"]) for d in m["dropped"])
        self.assertEqual(codes.get("funding_pct>="), "TOO_BROAD")
        self.assertEqual(codes.get("spread_bps"), "COVERAGE_LOW")
        self.assertNotIn("spread_bps", [it["cond"][0]["feature"] for it in m["items"]])
        h = m["items"][0]["cond_h"]
        m2 = MN.build_menu(win_of(rows, T0, T0 + 14 * D), CFG, blocked={h})
        self.assertEqual(m2["k"], m["k"] - 1)
        self.assertIn("BLOCKED", [d["code"] for d in m2["dropped"]])
        m3 = MN.build_menu(win_of(rows, T0, T0 + 14 * D), CFG, blocked=lambda x: x == h)
        self.assertEqual(m3["k"], m["k"] - 1)

    def test_deterministic_and_order_independent(self):
        rows = gen_rows(random.Random(23), T0 // H, 14 * 24, runs=3, effect=-0.3)
        res = []
        for seed in (None, 1, 2):
            rs = [dict(r, feats=dict(r["feats"])) for r in rows]
            if seed is not None:
                random.Random(seed).shuffle(rs)
            w = win_of(rs, T0, T0 + 14 * D)
            m = MN.build_menu(w, CFG)
            res.append([(json.dumps(it["cond"]), it["claim"]["effect"], it["n"], sc["p"], sc["ok"]) for it, sc in MN.screen_menu(w, m, CFG)])
        self.assertEqual(res[0], res[1])
        self.assertEqual(res[0], res[2])


# ---------------------------------------------------------------- симуляции ложных находок

def one_menu(disc_rows, a, b, hold_fn):
    """Меню -> скрининг -> до K_h принятых по p -> один holdout на свежих данных. -> число hyp."""
    w = win_of(disc_rows, a, b)
    m = MN.build_menu(w, CFG)
    if not m["k"]:
        return 0
    passed = sorted((sc["p"], i, it) for i, (it, sc) in enumerate(MN.screen_menu(w, m, CFG)) if sc["ok"])[:VF.k_h(CFG)]
    if not passed:
        return 0
    hw = hold_fn()
    return sum(1 for _p, _i, it in passed if VF.holdout(hw, it["cond"], VF.k_h(CFG), CFG)[0] == "hyp")


class TestFalseDiscovery(unittest.TestCase):
    def test_property5_400_null_sets(self):
        rnd = random.Random(20261103)
        h0 = T0 // H
        hyps, screened = 0, 0
        for _ in range(400):
            disc = gen_rows(rnd, h0, 14 * 24, runs=3)
            hyps += one_menu(disc, T0, T0 + 14 * D, lambda: win_of(gen_rows(rnd, h0 + 16 * 24, 7 * 24, runs=3), T0 + 16 * D, T0 + 23 * D))
        self.assertLessEqual(hyps, 5)

    def test_property6_200_series_8_menus(self):
        rnd = random.Random(6)
        h0 = T0 // H
        week = 7 * 24
        series_with_hyp = 0
        for _ in range(200):
            weeks = [gen_rows(rnd, h0 + w * week, week, runs=2) for w in range(10)]
            got = 0
            for m in range(8):
                disc = weeks[m] + weeks[m + 1]
                got += one_menu(disc, T0 + m * 7 * D, T0 + (m + 2) * 7 * D, lambda m=m: win_of(weeks[m + 2], T0 + (m + 2) * 7 * D, T0 + (m + 3) * 7 * D))
            series_with_hyp += 1 if got else 0
        self.assertLessEqual(series_with_hyp, 5)

    def test_effect_found_pure(self):
        rnd = random.Random(9)
        h0 = T0 // H
        disc = gen_rows(rnd, h0, 14 * 24, runs=4, effect=-0.6)
        hold = gen_rows(rnd, h0 + 16 * 24, 7 * 24, runs=4, effect=-0.6)
        w = win_of(disc, T0, T0 + 14 * D)
        m = MN.build_menu(w, CFG)
        ok = [it for it, sc in MN.screen_menu(w, m, CFG) if sc["ok"]]
        self.assertIn([{"feature": "range_5m_pct", "op": ">=", "value": ok[0]["cond"][0]["value"]}], [it["cond"] for it in ok])
        it = [x for x in ok if x["cond"][0]["feature"] == "range_5m_pct" and x["cond"][0]["op"] == ">="][0]
        self.assertEqual(VF.holdout(win_of(hold, T0 + 16 * D, T0 + 23 * D), it["cond"], 3, CFG)[0], "hyp")


# ---------------------------------------------------------------- сквозной путь на SQLite

_SIG = ("INSERT INTO signals(key, t0, sym, type, side, tradable, env, ev, status, attempts, mature_ts, next_try_ts, captured_ts, labeled_ts, schema_ver)"
        " VALUES(?,?,?,?,?,1,'mainnet',?,'done',0,?,?,?,?,1)")
_RUN = ("INSERT INTO policy_runs(key, policy_id, policy_ver, geometry_ver, ctx_ver, t0, sym, type, side, plan, code, filled, r_net, flags)"
        " VALUES(?,'P2',1,'g1',?,?,?,?,?,NULL,NULL,1,?,NULL)")
DAYS = 23
NOW_M = T0 + 16 * D + 20 * 60          # суточный проход 00:20: b = T0 + 14 сут
NOW_H = T0 + 25 * D + 20 * 60          # cut_ts holdout [T0 + 16, T0 + 23) = T0 + 25 сут 00:15


def build_db(root, effect=-0.8, seed=7, runs=4, days=DAYS):
    c = store.open_rw(root)
    store2.migrate_v2a(c, root, explicit=True, backup=False, now=T0)
    rnd = random.Random(seed)
    sigs, runs_ = [], []
    for h in range(days * 24):
        for si, side in enumerate(("long", "short")):
            shift = rnd.gauss(0, 0.5)
            for i in range(runs):
                t0 = T0 + h * H + i * 700 + si * 300
                sym = "Q%02dUSDT" % rnd.randrange(40)
                typ = ("range_5m", "turnover_surge", "hot_enter", "oi_spike")[rnd.randrange(4)]
                ev = {"range_5m_pct": round(rnd.uniform(0, 2), 4), "ret_5m_pct": round(rnd.gauss(0, 1), 4), "turn_surge_5m": round(rnd.uniform(0.5, 3), 4),
                      "chg_24h_pct": round(rnd.gauss(0, 8), 4), "spread_bps": round(rnd.uniform(0.5, 8), 4), "funding_pct": round(rnd.gauss(0, 0.01), 6),
                      "turn_24h_musd": round((5, 50, 500)[rnd.randrange(3)] * rnd.uniform(0.6, 1.4), 3)}
                r = round(shift + rnd.gauss(0, 1) + (effect if ev["range_5m_pct"] >= 1.6 else 0.0), 4)
                key = "S-%d-%s-%s" % (t0, sym, typ)
                sigs.append((key, t0, sym, typ, side, json.dumps(ev), t0 + 4 * H, t0 + 4 * H, t0 + 30, t0 + int(4.5 * H)))
                runs_.append((key, "c1", t0, sym, typ, side, r))
    c.executemany(_SIG, sigs)
    c.executemany(_RUN, runs_)
    t = T0 + 6 * H
    while t <= T0 + (days + 2) * D:
        CL.close_clusters(c, t, CFG, policy=POLICY, mcfg=MCFG, root=root)
        t += 6 * H
    c.close()


class TestFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpl = tempfile.mkdtemp(prefix="tbll_tmpl_")
        build_db(cls.tmpl)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpl, True)

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbll_flow_")
        self.addCleanup(shutil.rmtree, self.root, True)
        shutil.copytree(os.path.join(self.tmpl, "state"), os.path.join(self.root, "state"))
        store2._STATUS_CACHE.clear()
        self.c = store.open_rw(self.root)
        self.addCleanup(self.c.close)

    def menu(self, now=NOW_M, **kw):
        return CA.run_menu(self.c, self.root, now, CFG, policy=POLICY, **kw)

    def accept(self, now=NOW_M):
        return CA.accept(self.c, self.root, now, CFG, policy=POLICY)

    def hold(self, now=NOW_H):
        return CA.check_holdouts(self.c, self.root, now, CFG, policy=POLICY, mcfg=MCFG)

    def eff_cond(self, rep):
        conds = [c for c in rep["added"] + rep["dup"] if c[0]["feature"] == "range_5m_pct" and c[0]["op"] == ">="]
        self.assertEqual(len(conds), 1, rep)
        return conds[0]

    def add_run(self, t0, ctx="c2", key=None):
        store2.insert_rows(self.c, "cluster_runs", [{"cluster": "%d:long" % (t0 // H), "hour": t0 // H, "side": "long", "key": key or "X-%d" % t0, "t0": t0,
                                                     "type": "range_5m", "policy_id": "P2", "policy_ver": 1, "ctx_ver": ctx, "env_norm": "mainnet",
                                                     "filled": 1, "r_net": 0.0, "feats": {}, "flags": None, "closed_ts": t0 + 6 * H}])

    def test_full_path_to_hyp(self):
        rep = self.menu()
        self.assertEqual(rep["code"], "MENU")
        self.assertTrue(1 <= rep["k"] <= 17)
        cond = self.eff_cond(rep)
        new = CA.candidates(self.c, status=("new",))
        ev = new[0]["evidence"]["disc"]
        self.assertEqual((ev["a"], ev["b"], ev["cut_ts"]), (T0, T0 + 14 * D, T0 + 16 * D + 900))
        self.assertEqual(len(ev["h"]), 16)
        self.assertEqual(new[0]["k_space"], rep["k"])
        self.assertEqual(new[0]["src"], "code")
        acc = self.accept()
        self.assertEqual([a[1] for a in acc], ["holdout"] * len(acc))
        c = [x for x in CA.candidates(self.c, status=("holdout",)) if x["cond"] == cond][0]
        self.assertEqual(c["holdout_start"], T0 + 16 * D)
        self.assertEqual(c["holdout_end"] - c["holdout_start"], 7 * D)
        self.assertGreaterEqual(c["holdout_start"], ev["b"], "окна поиска и holdout не пересекаются")
        self.assertEqual(self.hold(T0 + 20 * D), [])
        self.assertEqual(self.hold(NOW_H - 600), [], "тест holdout только в cut_ts")
        out = self.hold()
        hyp = [o for o in out if o[0] == c["id"]]
        self.assertEqual(hyp[0][1], "hyp")
        self.assertEqual(self.hold(NOW_H + 3600), [], "тест один, повтор ничего не меняет")
        l = LS.read_all(self.root)[hyp[0][2]]
        self.assertEqual((l["status"], l["created_by"], l["evidence"]["src"]), ("hyp", "code", "labels"))
        self.assertIsNone(l["expires_ts"])
        self.assertEqual(l["cond"], cond)
        self.assertEqual(LS.validate_claim(l["claim"]), [])
        self.assertGreater(l["claim"]["effect"], 0.3)
        smp = l["evidence"]["sample"]
        self.assertEqual((smp["hold"]["a"], smp["hold"]["b"], smp["hold"]["cut_ts"]), (T0 + 16 * D, T0 + 23 * D, T0 + 25 * D + 900))
        self.assertEqual(len(smp["hold"]["h"]), 16)
        self.assertGreaterEqual(l["evidence"]["n_closed"], 30)
        self.assertLess(l["evidence"]["diff"], 0)
        self.assertLessEqual(len(l["evidence"]["clusters"]), 5)
        with open(LS.lessons_path(self.root), "rb") as f:
            for line in f:
                self.assertLessEqual(len(line.rstrip(b"\n")), LS.LINE_MAX)
        row = CA.candidates(self.c, status=("hyp",))[0]
        self.assertEqual(row["lesson_id"], l["lesson_id"])
        rep2 = self.menu(NOW_M, force=True)                          # урок активен: то же условие (тот же порог) — DUP_ACTIVE
        self.assertEqual(rep2["code"], "MENU")
        self.assertIn(cond, rep2["dup"])

    def test_restart_after_lesson_written(self):
        self.menu()
        self.accept()
        c = [x for x in CA.candidates(self.c, status=("holdout",)) if x["cond"][0]["feature"] == "range_5m_pct"][0]
        disc = c["evidence"]["disc"]
        hw = CL.window(self.c, "P2", 1, "c1", "mainnet", c["holdout_start"], c["holdout_end"], CFG, NOW_H)
        st, _code, nums = VF.holdout(hw, c["cond"], 3, CFG)
        self.assertEqual(st, "hyp")
        l = LS.create(self.root, c["cond"], c["claim"], "code", [], MCFG, NOW_H, evidence_src="labels", evidence_extra=CA.lesson_evidence(c, disc, hw, nums))
        out = [o for o in self.hold() if o[0] == c["id"]]                 # падение между записью урока и строки кандидата
        self.assertEqual(out[0], (c["id"], "hyp", l["lesson_id"]))
        self.assertEqual(len([x for x in LS.read_all(self.root).values() if x["cond"] == c["cond"]]), 1)

    def test_not_due_budget_and_idempotent(self):
        rep = self.menu()
        n = len(CA.candidates(self.c))
        self.assertEqual(self.menu(NOW_M + 3600)["code"], "NOT_DUE")
        self.assertEqual(self.menu(NOW_M + 6 * D)["code"], "NOT_DUE")
        self.menu(NOW_M, force=True)                                 # тот же b: INSERT OR IGNORE
        self.assertEqual(len(CA.candidates(self.c)), n)
        rep2 = self.menu(NOW_M + 7 * D)
        self.assertEqual(rep2["code"], "MENU")
        self.assertEqual(rep2["budget"], len(rep["added"]))
        old = [x for x in CA.candidates(self.c) if x["menu_id"] == rep["menu_id"]]
        self.assertEqual({(x["status"], x["code"]) for x in old if x["code"] != "DUP_ACTIVE"}, {("dropped", "BUDGET")})

    def test_no_menu_and_no_data(self):
        self.assertEqual(self.menu(T0 + 8 * D + 20 * 60)["code"], "NO_MENU")    # b = T0 + 6 сут: меньше 7 суток данных
        self.assertEqual(self.menu(T0 + 9 * D + 20 * 60)["code"], "MENU")
        root = tempfile.mkdtemp(prefix="tbll_empty_")
        self.addCleanup(shutil.rmtree, root, True)
        c = store.open_rw(root)
        self.addCleanup(c.close)
        store2.migrate_v2a(c, root, explicit=True, backup=False, now=T0)
        self.assertEqual(CA.run_menu(c, root, NOW_M, CFG, policy=POLICY)["code"], "NO_DATA")

    def test_no_menu_after_ctx_change(self):
        self.add_run(T0 + 14 * D - 2 * H)
        self.assertEqual(self.menu()["code"], "NO_MENU")

    def test_ctx_changed_during_holdout(self):
        self.menu()
        self.accept()
        self.add_run(T0 + 18 * D)
        out = self.hold(T0 + 19 * D)
        self.assertTrue(out)
        self.assertEqual({o[2] for o in out}, {"CTX_CHANGED"})
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM blocklist_rows").fetchone()[0], 0)

    def test_ctx_changed_before_accept(self):
        self.menu()
        self.add_run(NOW_M - 10)                                     # последняя строка до момента приёма — новой версии контекста
        acc = self.accept()
        self.assertEqual({a[2] for a in acc}, {"CTX_CHANGED"})

    def test_dup_active_by_trial_rule(self):
        rep0 = self.menu()
        cond = self.eff_cond(rep0)
        root = tempfile.mkdtemp(prefix="tbll_dup_")
        self.addCleanup(shutil.rmtree, root, True)
        shutil.copytree(os.path.join(self.tmpl, "state"), os.path.join(root, "state"))
        c = store.open_rw(root)
        self.addCleanup(c.close)
        store2.append_row(c, "rule_rows", {"rule_id": "RU-0001", "rev": 1, "kind": "skip_when", "status": "trial", "from_lesson": "L-0001",
                                           "env_norm": "mainnet", "reason_code": "TRIAL", "body": {"cond": cond, "dir": "lower"}, "ts": store2.iso(NOW_M)})
        rep = CA.run_menu(c, root, NOW_M, CFG, policy=POLICY)
        self.assertIn(cond, rep["dup"])
        dropped = [x for x in CA.candidates(c) if x["cond"] == cond]
        self.assertEqual((dropped[0]["status"], dropped[0]["code"]), ("dropped", "DUP_ACTIVE"))
        store2.append_row(c, "rule_rows", {"rule_id": "RU-0001", "rev": 2, "kind": "skip_when", "status": "rejected", "from_lesson": "L-0001",
                                           "env_norm": "mainnet", "reason_code": "TRIAL_REJECT", "body": {"cond": cond, "dir": "lower"}, "ts": store2.iso(NOW_M)})
        self.assertNotIn(LR.cond_h(cond), CA.active_hashes(c, root))

    def test_dup_active_by_family_of_active_rule(self):
        """DUP_ACTIVE по семейству (2026-11-03, Requirement 4.5): активное правило `f >= X` — меню с порогом X' != X даёт DUP_ACTIVE, а не новый
        кандидат (иначе слот испытания тратится до OVERLAP); противоположный оператор того же признака — не дубль; то же при приёме."""
        rep0 = self.menu()
        cond = self.eff_cond(rep0)

        def with_rule(rule_cond):
            root = tempfile.mkdtemp(prefix="tbll_dupf_")
            self.addCleanup(shutil.rmtree, root, True)
            shutil.copytree(os.path.join(self.tmpl, "state"), os.path.join(root, "state"))
            c = store.open_rw(root)
            self.addCleanup(c.close)
            store2.append_row(c, "rule_rows", {"rule_id": "RU-0001", "rev": 1, "kind": "skip_when", "status": "active", "from_lesson": "L-0001",
                                               "env_norm": "mainnet", "reason_code": "PROMOTE", "body": {"cond": rule_cond, "cond_h": LR.cond_h(rule_cond)},
                                               "ts": store2.iso(NOW_M)})
            return c, root, CA.run_menu(c, root, NOW_M, CFG, policy=POLICY)

        _c, _r, rep = with_rule([dict(cond[0], op="<=")])                              # противоположный оператор того же признака — не дубль
        self.assertIn(cond, rep["added"])
        rule_cond = [dict(cond[0], value=round(cond[0]["value"] * 1.031 + 0.0007, 6))]
        self.assertNotEqual(LR.cond_h(rule_cond), LR.cond_h(cond))
        c, root, rep = with_rule(rule_cond)
        self.assertIn(LR.block_h(cond), CA.active_hashes(c, root))
        self.assertIn(cond, rep["dup"])
        self.assertNotIn(cond, rep["added"])
        self.assertEqual([(x["status"], x["code"]) for x in CA.candidates(c) if x["cond"] == cond], [("dropped", "DUP_ACTIVE")])
        store2.insert_rows(c, "lesson_candidates", [{"menu_id": "M-mainnet-x", "src": "code", "cond": cond, "claim": {"metric": "mean_r", "dir": "lower",
                           "vs": "base", "effect": 0.1}, "env_norm": "mainnet", "ctx_ver": CA.candidates(self.c)[0]["ctx_ver"], "k_space": 5, "p_screen": 0.0001,
                           "status": "new", "evidence": {}, "ts": NOW_M}])
        acc = CA.accept(c, root, NOW_M, CFG, policy=POLICY)
        self.assertIn("DUP_ACTIVE", [a[2] for a in acc if [x for x in CA.candidates(c) if x["id"] == a[0]][0]["cond"] == cond])

    def test_blocklisted_not_in_menu(self):
        rep0 = self.menu()
        cond = self.eff_cond(rep0)
        root = tempfile.mkdtemp(prefix="tbll_blk_")
        self.addCleanup(shutil.rmtree, root, True)
        shutil.copytree(os.path.join(self.tmpl, "state"), os.path.join(root, "state"))
        c = store.open_rw(root)
        self.addCleanup(c.close)
        store2.blocklist_put(c, LR.cond_h(cond), "TRIAL_REJECT", NOW_M + 14 * D, "code", now=NOW_M)
        rep = CA.run_menu(c, root, NOW_M, CFG, policy=POLICY)
        self.assertEqual(rep["k"], rep0["k"] - 1)
        self.assertNotIn(cond, rep["added"] + rep["dup"])
        self.assertIn("BLOCKED", [d["code"] for d in rep["dropped"] if d.get("cond") == cond])

    def test_rejected_family_not_back_with_shifted_threshold(self):
        """Регрессия (сквозной тест задачи 22, Requirement 7.14): после TRIAL_REJECT условия `f >= X` меню с порогом X' != X (квантиль окна
        сдвинулся) не берёт условие — BLOCKED по семейству block_h; через 14 суток запрет истекает."""
        rep0 = self.menu()
        cond = self.eff_cond(rep0)
        old = [dict(cond[0], value=round(cond[0]["value"] * 1.0127 + 0.0003, 6))]          # прежний порог того же семейства
        self.assertNotEqual(LR.cond_h(old), LR.cond_h(cond))
        root = tempfile.mkdtemp(prefix="tbll_fam_")
        self.addCleanup(shutil.rmtree, root, True)
        shutil.copytree(os.path.join(self.tmpl, "state"), os.path.join(root, "state"))
        c = store.open_rw(root)
        self.addCleanup(c.close)
        self.assertIsNotNone(RU.block(c, old, "TRIAL_REJECT", NOW_M - 3 * D, CFG))
        self.assertEqual(c.execute("SELECT cond_h FROM blocklist_rows").fetchall(), [(LR.block_h(old),)])
        rep = CA.run_menu(c, root, NOW_M, CFG, policy=POLICY)
        self.assertEqual(rep["k"], rep0["k"] - 1)
        self.assertNotIn(cond, rep["added"] + rep["dup"])
        self.assertIn("BLOCKED", [d["code"] for d in rep["dropped"] if d.get("cond") == cond])
        other = [x for x in rep0["added"] + rep0["dup"] if x[0]["feature"] == "range_5m_pct" and x[0]["op"] == "<="]
        for x in other:                                                               # другой оператор того же признака — другое семейство
            self.assertIn(x, rep["added"] + rep["dup"])
        store2.insert_rows(c, "lesson_candidates", [{"menu_id": "M-mainnet-x", "src": "code", "cond": cond, "claim": {"metric": "mean_r", "dir": "lower",
                           "vs": "base", "effect": 0.1}, "env_norm": "mainnet", "ctx_ver": CA.candidates(self.c)[0]["ctx_ver"], "k_space": 5, "p_screen": 0.0001,
                           "status": "new", "evidence": {}, "ts": NOW_M}])
        acc = dict((a[0], a) for a in CA.accept(c, root, NOW_M, CFG, policy=POLICY))
        self.assertIn("TRIAL_REJECT", [a[2] for a in acc.values()])                   # приём тоже проверяет семейство
        self.assertIsNotNone(RU.blocked(c, cond, NOW_M - 3 * D + 14 * D - 1))
        self.assertIsNone(RU.blocked(c, cond, NOW_M - 3 * D + 14 * D))                # фиксированный cooldown: потом семейство может вернуться

    def test_rule_cond_h_matches_and_rule_block_cuts_menu(self):
        rep0 = self.menu()
        cand = [x for x in CA.candidates(self.c, status=("new",)) if x["cond"] == self.eff_cond(rep0)][0]
        lesson = {"lesson_id": "L-0001", "status": "hyp", "cond": cand["cond"], "claim": cand["claim"], "evidence": {"src": "labels", "env_norm": "mainnet"}}
        rule = RU.derive(lesson, POLICY)
        self.assertIsNotNone(rule)
        self.assertEqual(rule["cond_h"], CA.cand_h(cand))
        root = tempfile.mkdtemp(prefix="tbll_rb_")
        self.addCleanup(shutil.rmtree, root, True)
        shutil.copytree(os.path.join(self.tmpl, "state"), os.path.join(root, "state"))
        c = store.open_rw(root)
        self.addCleanup(c.close)
        self.assertIsNotNone(RU.block(c, rule["cond_h"], "TRIAL_REJECT", NOW_M, CFG))       # запись чёрного списка путём правил (задача 11)
        rep = CA.run_menu(c, root, NOW_M, CFG, policy=POLICY)
        self.assertNotIn(cand["cond"], rep["added"] + rep["dup"])
        self.assertIn("BLOCKED", [d["code"] for d in rep["dropped"] if d.get("cond") == cand["cond"]])
        self.assertIsNotNone(RU.derive_reason(lesson, c, NOW_M))

    def test_restored_window(self):
        self.menu()
        self.accept()
        c = CA.candidates(self.c, status=("holdout",))[0]
        cut = c["evidence"]["disc"]["cut_ts"]
        self.c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('copy_ts', ?)", (str(cut - 60),))
        self.c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('restore_ts', ?)", (str(cut + 60),))
        out = self.hold()
        self.assertEqual({o[2] for o in out}, {"RESTORED"})
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM blocklist_rows").fetchone()[0], 0)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM learn_events WHERE code='SAMPLE_CHANGED'").fetchone()[0], 0)

    def test_sample_changed_blocklists(self):
        self.menu()
        self.accept()
        cands = CA.candidates(self.c, status=("holdout",))
        self.c.execute("DROP TRIGGER tr_cluster_runs_nu")
        self.c.execute("UPDATE cluster_runs SET r_net = r_net + 1 WHERE t0 = (SELECT MIN(t0) FROM cluster_runs)")
        out = self.hold()
        self.assertEqual({o[2] for o in out}, {"SAMPLE_CHANGED"})
        for x in cands:
            b = store2.blocklist_get(self.c, LR.block_h(x["cond"]), NOW_H)
            self.assertEqual(b, {"code": "SAMPLE_CHANGED", "until_ts": NOW_H + 14 * D})
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM learn_events WHERE code='SAMPLE_CHANGED'").fetchone()[0], len(cands))
        self.assertEqual(LS.read_all(self.root), {})

    def test_holdout_failure_blocklists(self):
        self.menu()
        acc = self.accept()
        self.assertTrue(acc)
        bad = [{"feature": "funding_pct", "op": ">=", "value": 99.0}]       # подменённое условие в holdout: кластеров теста нет -> N_TOO_SMALL
        cid = [a[0] for a in acc][0]
        self.c.execute("UPDATE lesson_candidates SET cond=? WHERE id=?", (json.dumps(bad), cid))
        out = dict((o[0], o) for o in self.hold())
        self.assertEqual(out[cid][1:], ("dropped", "N_TOO_SMALL"))
        self.assertEqual(store2.blocklist_get(self.c, LR.block_h(bad), NOW_H)["code"], "N_TOO_SMALL")

    def test_accept_limit_per_day(self):
        rows = []
        fams = [("spread_bps", ">="), ("spread_bps", "<="), ("range_5m_pct", ">="), ("range_5m_pct", "<="), ("funding_pct", ">=")]   # разные семейства
        for i in range(5):
            rows.append({"menu_id": "M-mainnet-test", "src": "code", "cond": [{"feature": fams[i][0], "op": fams[i][1], "value": 10.0 + i}],
                         "claim": {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.1}, "env_norm": "mainnet", "ctx_ver": "c1",
                         "k_space": 5, "p_screen": 0.001 * (5 - i), "status": "new", "evidence": {}, "ts": NOW_M})
        store2.insert_rows(self.c, "lesson_candidates", rows)
        a1 = self.accept()
        self.assertEqual(len(a1), 3)
        got = CA.candidates(self.c, status=("holdout",))
        self.assertEqual(sorted(x["p_screen"] for x in got), [0.001, 0.002, 0.003])
        self.assertEqual(self.accept(NOW_M + 3600), [])
        self.assertEqual(len(self.accept(NOW_M + D)), 2)


if __name__ == "__main__":
    unittest.main()
