"""tb.learn.vs_policy (R0.2, задача 9): свойство 15 (few_data при n < 30, better только при diff > 0 и p < alpha/K, версии не смешиваются, свежая ctx_ver),
Уэлч по сводкам, K и Бонферрони, итог summary по базовым политикам, labels_off / E_REGISTRY, только чтение через open_ro. Временная база на временном корне."""
import hashlib
import json
import math
import os
import random
import sqlite3
import types
import unittest

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.core import expstats
from tb.labels import store
from tb.learn import vs_policy as V
from tb.learn.stats import DAY

NOW = H.T0 + 3600
SINCE = NOW - 14 * DAY
COLS = "key, policy_id, policy_ver, geometry_ver, ctx_ver, t0, sym, type, side, plan, code, filled, exit_reason, r_gross, r_net, fees_r, mfe_r, mae_r, hold_min, flags"
CFG = {"policy_days": 14, "baseline_policies": ["P5", "P6"]}


def run(key, pid, ver, t0, r, ctx="aaaa0001"):
    """Прогон политики: r=None - не заполнен (no_fill)."""
    f = 0 if r is None else 1
    return (key, pid, ver, "g1", ctx, t0, "AAA", "range_5m", "long", None, None, f, "tp" if f else "no_fill", r, r, 0.1 if f else None, None, None, 10.0 if f else None, None)


def gauss(rng, n, mu, sd):
    return [rng.gauss(mu, sd) for _ in range(n)]


def brain(rs, t_close=None):
    return [{"r": r, "t_close": NOW - 100 if t_close is None else t_close} for r in rs]


def welch_p(a, b):
    """Независимая проверка: Уэлч-Саттертвейт по выборкам -> (diff, p)."""
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1) / len(a)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1) / len(b)
    se = math.sqrt(va + vb)
    df = (va + vb) ** 2 / (va * va / (len(a) - 1) + vb * vb / (len(b) - 1))
    return ma - mb, expstats.t_two_sided_p((ma - mb) / se, df)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        self.n = 0

    def conn(self):
        c = store.open_rw(self.root)
        self.addCleanup(c.close)
        return c

    def put(self, pid, rs, ver=1, ctx="aaaa0001", t0=None):
        c = self.conn()
        rows = []
        for r in rs:
            self.n += 1
            rows.append(run("k%d" % self.n, pid, ver, (NOW - 3600 - self.n) if t0 is None else t0 + self.n, r, ctx))
        c.executemany("INSERT INTO policy_runs(%s) VALUES(%s)" % (COLS, ",".join("?" * 20)), rows)

    def build(self, rs_brain, mode="paper", cfg=None):
        ctx = types.SimpleNamespace(root=self.root, mode=mode, now=NOW, cfg=cfg or CFG, trades=brain(rs_brain))
        return V.build(ctx)

    def reg(self, pols):
        with open(os.path.join(self.root, "config", "shadow_policies.json"), "w", encoding="utf-8") as f:
            json.dump({"schema": "tb.shadow_policies/1", "policies": pols}, f)

    @staticmethod
    def pol(pid, ver=1, enabled=True):
        return {"id": pid, "ver": ver, "name": "n_" + pid, "kinds": ["*"], "side": "random", "entry": "market", "profile": "atr", "no_tp": False,
                "max_horizon": "scalp", "entry_ttl_min": 2, "enabled": enabled}

    @staticmethod
    def row(b, pid):
        return next(r for r in b["rows"] if r["id"] == pid)


class TestNoData(Base):
    def test_no_db(self):
        b = self.build([1.0] * 40)
        self.assertEqual((b["status"], b["reason"], b["rows"], b["summary"]), ("no_data", "labels_off", [], "few_data"))
        self.assertEqual(b["brain"]["n"], 40)
        self.assertFalse(os.path.exists(store.db_path(self.root)))          # блок не создал базу

    def test_empty_db(self):
        self.conn()
        b = self.build([1.0] * 40)
        self.assertEqual((b["status"], b["reason"]), ("no_data", "labels_off"))

    def test_no_policy_runs_table(self):
        os.makedirs(os.path.join(self.root, "state"))
        c = sqlite3.connect(store.db_path(self.root))
        c.execute("CREATE TABLE meta(k TEXT, v TEXT)")
        c.commit()
        c.close()
        b = self.build([1.0] * 40)
        self.assertEqual((b["status"], b["reason"]), ("no_data", "labels_off"))

    def test_runs_only_outside_window(self):
        self.put("P6", [0.1] * 40, t0=SINCE - 10 * DAY)
        b = self.build([1.0] * 40)
        self.assertEqual((b["status"], b["reason"]), ("no_data", "labels_off"))

    def test_bad_registry(self):
        with open(os.path.join(self.root, "config", "shadow_policies.json"), "w", encoding="utf-8") as f:
            f.write("{bad")
        b = self.build([1.0] * 40)
        self.assertEqual((b["status"], b["reason"], b["rows"]), ("error", "E_REGISTRY", []))
        self.reg([dict(self.pol("P6"), extra=1)])
        self.assertEqual(self.build([])["reason"], "E_REGISTRY")


class TestVerdict(Base):
    def test_few_data_either_side(self):
        rng = random.Random(1)
        self.put("P6", gauss(rng, 40, -1.0, 0.3))
        b = self.build(gauss(rng, 29, 1.0, 0.3))
        self.assertEqual(self.row(b, "P6")["verdict"], "few_data")
        self.assertEqual((b["summary"], b["status"]), ("few_data", "few_data"))
        b = self.build(gauss(rng, 30, 1.0, 0.3))
        self.assertEqual(self.row(b, "P6")["verdict"], "better")              # граница n = 30 уже достаточна
        self.put("P5", gauss(rng, 29, -1.0, 0.3))
        self.assertEqual(self.row(self.build(gauss(rng, 40, 1.0, 0.3)), "P5")["verdict"], "few_data")

    def test_zero_fills_and_missing_policy(self):
        self.put("P6", [None] * 50)
        b = self.build([1.0] * 40)
        r = self.row(b, "P6")
        self.assertEqual((r["n_filled"], r["n_runs"], r["fill_rate"], r["r"]["v"], r["verdict"]), (0, 50, 0.0, None, "few_data"))
        self.assertIsNone(r["diff"]["v"])
        p4 = self.row(b, "P4")                                                 # политика без строк: пустая строка, не исключение
        self.assertEqual((p4["n_filled"], p4["verdict"], p4["r"]["v"]), (0, "few_data", None))
        self.assertEqual([r["id"] for r in b["rows"]], ["P2", "P4", "P5", "P6"])

    def test_better_worse_unclear_and_summary(self):
        rng = random.Random(7)
        self.put("P5", gauss(rng, 60, -0.6, 0.4))
        self.put("P6", gauss(rng, 60, 0.0, 0.4))
        b = self.build(gauss(rng, 60, 0.9, 0.4))
        self.assertEqual((self.row(b, "P5")["verdict"], self.row(b, "P6")["verdict"]), ("better", "better"))
        self.assertEqual((b["summary"], b["status"]), ("above_baseline", "ok"))
        self.assertEqual(b["brain"]["n"], 60)
        # мозг хуже P6 -> below_baseline, даже если P5 побеждён
        b = self.build(gauss(rng, 60, -0.9, 0.4))
        self.assertEqual((self.row(b, "P5")["verdict"], self.row(b, "P6")["verdict"], b["summary"]), ("worse", "worse", "below_baseline"))

    def test_summary_unclear_and_non_baseline_ignored(self):
        rng = random.Random(3)
        self.put("P2", gauss(rng, 60, -2.0, 0.2))                              # небазовая: на итог не влияет
        self.put("P5", gauss(rng, 60, -0.6, 0.4))
        self.put("P6", gauss(rng, 60, 0.5, 0.5))
        b = self.build(gauss(rng, 60, 0.5, 0.5))
        self.assertEqual(self.row(b, "P6")["verdict"], "unclear")
        self.assertEqual(b["summary"], "unclear")
        self.assertTrue(self.row(b, "P5")["baseline"] and not self.row(b, "P2")["baseline"])

    def test_summary_ignores_few_data_baseline(self):
        rng = random.Random(4)
        self.put("P5", gauss(rng, 10, 5.0, 0.1))                               # мало данных - не учитывается
        self.put("P6", gauss(rng, 60, -0.6, 0.4))
        b = self.build(gauss(rng, 60, 0.9, 0.4))
        self.assertEqual((self.row(b, "P5")["verdict"], b["summary"]), ("few_data", "above_baseline"))

    def test_property_15_random(self):
        """Вердикт совпадает с независимым расчётом Уэлча; better лишь при diff > 0 и p < alpha/K; few_data при n < 30."""
        for seed in range(25):
            rng = random.Random(seed)
            nb, nf = rng.randint(5, 70), rng.randint(5, 70)
            mu_b, mu_p = rng.uniform(-1, 1), rng.uniform(-1, 1)
            rb, rp = gauss(rng, nb, mu_b, rng.uniform(0.2, 1)), gauss(rng, nf, mu_p, rng.uniform(0.2, 1))
            self.setUp()
            self.put("P6", rp)
            b = self.build(rb)
            r = self.row(b, "P6")
            self.assertEqual(b["k"], 4)
            self.assertAlmostEqual(b["alpha_adj"], 0.0125)
            self.assertEqual(r["n_filled"], nf)
            self.assertAlmostEqual(r["r"]["v"], sum(rp) / nf, places=9)
            diff, p = welch_p(rb, rp)
            self.assertAlmostEqual(r["diff"]["v"], diff, places=9)
            self.assertAlmostEqual(r["p"], p, places=6)
            if nb < 30 or nf < 30:
                self.assertEqual(r["verdict"], "few_data", seed)
            elif abs(p - 0.0125) > 1e-6:
                want = "unclear" if p >= 0.0125 else ("better" if diff > 0 else "worse")
                self.assertEqual(r["verdict"], want, seed)
            if r["verdict"] == "better":
                self.assertTrue(r["diff"]["v"] > 0 and r["p"] < b["alpha_adj"])
            lo, hi = r["diff"]["lo"], r["diff"]["hi"]
            if lo is not None:
                self.assertTrue(lo <= r["diff"]["v"] <= hi)
                self.assertAlmostEqual(r["diff"]["lvl"], 1 - 0.0125)

    def test_ci_excludes_zero_iff_significant(self):
        rng = random.Random(11)
        self.put("P6", gauss(rng, 50, 0.0, 0.5))
        r = self.row(self.build(gauss(rng, 50, 0.5, 0.5)), "P6")
        sig = r["p"] < 0.0125
        self.assertEqual(sig, r["diff"]["lo"] > 0 or r["diff"]["hi"] < 0)

    def test_pf_inf_and_pf(self):
        self.put("P6", [0.5] * 40)                                             # нет убытков
        r = self.row(self.build([1.0] * 40), "P6")
        self.assertTrue(r["pf_inf"] and r["pf"] is None)
        self.assertEqual(r["p"] is None or r["p"] >= 0, True)


class TestK(Base):
    def test_bonferroni_by_enabled_count(self):
        rng = random.Random(5)
        rp = gauss(rng, 50, 0.0, 0.5)
        rb = None
        for shift in [i * 0.01 for i in range(1, 200)]:                         # ищем p между alpha/4 и alpha/2
            cand = gauss(random.Random(99), 50, shift, 0.5)
            p = welch_p(cand, rp)[1]
            if 0.0125 < p < 0.025 and sum(cand) > sum(rp):
                rb = cand
                break
        self.assertIsNotNone(rb)
        self.put("P6", rp)
        b4 = self.build(rb)
        self.assertEqual((b4["k"], self.row(b4, "P6")["verdict"]), (4, "unclear"))
        self.reg([self.pol("P5"), self.pol("P6"), self.pol("P2", enabled=False), self.pol("P4", enabled=False)])
        b2 = self.build(rb)
        self.assertEqual((b2["k"], b2["alpha_adj"]), (2, 0.025))
        self.assertEqual(self.row(b2, "P6")["verdict"], "better")
        self.assertEqual([r["id"] for r in b2["rows"]], ["P5", "P6"])          # выключенные не показываются
        self.assertAlmostEqual(self.row(b2, "P6")["diff"]["lvl"], 0.975)


class TestVersions(Base):
    def test_old_version_not_mixed_or_shown(self):
        rng = random.Random(8)
        self.reg([self.pol("P6", 1, enabled=False), self.pol("P6", 2), self.pol("P5")])
        self.put("P6", gauss(rng, 80, 5.0, 0.1), ver=1)                         # старая версия с огромным R
        self.put("P6", gauss(rng, 40, -0.5, 0.3), ver=2)
        b = self.build(gauss(rng, 40, 0.5, 0.3))
        self.assertEqual([(r["id"], r["ver"]) for r in b["rows"]], [("P5", 1), ("P6", 2)])
        r = self.row(b, "P6")
        self.assertEqual((r["n_filled"], r["verdict"]), (40, "better"))
        self.assertTrue(r["r"]["v"] < 0)

    def test_highest_enabled_version_is_current(self):
        rng = random.Random(9)
        self.reg([self.pol("P6", 1), self.pol("P6", 2)])
        self.put("P6", gauss(rng, 40, 3.0, 0.1), ver=1)
        self.put("P6", gauss(rng, 35, 0.0, 0.3), ver=2)
        b = self.build(gauss(rng, 40, 0.0, 0.3))
        self.assertEqual([(r["id"], r["ver"], r["n_filled"]) for r in b["rows"]], [("P6", 2, 35)])

    def test_ctx_changed_takes_fresh_only(self):
        rng = random.Random(10)
        self.put("P6", gauss(rng, 60, 4.0, 0.1), ctx="old00001", t0=NOW - 5 * DAY)
        self.put("P6", gauss(rng, 40, -0.4, 0.3), ctx="new00002", t0=NOW - 2 * DAY)
        b = self.build(gauss(rng, 40, 0.5, 0.3))
        r = self.row(b, "P6")
        self.assertTrue(r["ctx_changed"])
        self.assertEqual(r["n_filled"], 40)
        self.assertTrue(r["r"]["v"] < 0)
        self.assertEqual(r["verdict"], "better")

    def test_single_ctx_not_changed(self):
        self.put("P6", [0.1, -0.1] * 20)
        self.assertFalse(self.row(self.build([0.2] * 40), "P6")["ctx_changed"])

    def test_ctx_fresh_is_by_max_t0_not_by_name(self):
        rng = random.Random(12)
        self.put("P6", gauss(rng, 40, 3.0, 0.1), ctx="zzzz9999", t0=NOW - 6 * DAY)    # "больше" по имени, но старее
        self.put("P6", gauss(rng, 40, 0.0, 0.3), ctx="aaaa0001", t0=NOW - 1 * DAY)
        r = self.row(self.build(gauss(rng, 40, 0.0, 0.3)), "P6")
        self.assertTrue(abs(r["r"]["v"]) < 0.5)


class TestWindowAndModes(Base):
    def test_brain_window(self):
        rng = random.Random(13)
        self.put("P6", gauss(rng, 40, 0.0, 0.3))
        old = brain([9.0] * 50, t_close=SINCE - 5)
        new = brain(gauss(rng, 35, 0.0, 0.3))
        ctx = types.SimpleNamespace(root=self.root, mode="paper", now=NOW, cfg=CFG, trades=old + new)
        b = V.build(ctx)
        self.assertEqual(b["brain"]["n"], 35)
        self.assertEqual(b["since"], SINCE)
        # ts_close без t_close
        t = [{"r": 1.0, "ts_close": H.iso(NOW - 60)}, {"r": 1.0, "ts_close": "мусор"}, {"r": None, "t_close": NOW}, {"r": True, "t_close": NOW}]
        ctx.trades = t
        self.assertEqual(V.build(ctx)["brain"]["n"], 1)

    def test_runs_outside_window_ignored(self):
        rng = random.Random(14)
        self.put("P6", gauss(rng, 40, 0.0, 0.3))
        self.put("P6", gauss(rng, 100, 9.0, 0.1), t0=SINCE - 3 * DAY)
        self.assertEqual(self.row(self.build(gauss(rng, 40, 0.0, 0.3)), "P6")["n_filled"], 40)

    def test_caveats(self):
        self.put("P6", [0.1, -0.1] * 20)
        for m, extra in (("paper", False), ("testnet", True), ("live", True)):
            c = self.build([0.0] * 5, mode=m)["caveats"]
            self.assertEqual(c[:4], ["unpaired", "selection", "costs_model", "correlated"])
            self.assertEqual("sim_vs_real" in c, extra)
        self.assertIn("sim_vs_real", V.build(types.SimpleNamespace(root=self.root, mode="live", now=NOW, cfg=CFG, trades=[]))["caveats"])

    def test_default_cfg_and_dead_reasons(self):
        self.put("P6", [0.1, -0.1] * 20)
        b = V.build(types.SimpleNamespace(root=self.root, mode="paper", now=NOW, trades=None))     # cfg из tb.learn.conf (умолчания)
        self.assertEqual(b["since"], SINCE)
        self.assertEqual(b["brain"]["n"], 0)
        self.assertEqual(b["id"], "vs_policy")


class TestReadOnly(Base):
    def digest(self):
        with open(store.db_path(self.root), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def test_open_ro_rejects_writes(self):
        self.put("P6", [0.1] * 5)
        ro = store.open_ro(self.root)
        self.addCleanup(ro.close)
        with self.assertRaises(sqlite3.OperationalError):
            ro.execute("DELETE FROM policy_runs")

    def test_block_reads_through_open_ro_only_select(self):
        self.put("P6", [0.1, -0.1] * 20)
        h = self.digest()
        seen = []
        real = store.open_ro

        class Spy(object):
            def __init__(self, c):
                self.c = c

            def execute(self, sql, *a):
                seen.append(sql.strip().split()[0].upper())
                return self.c.execute(sql, *a)

            def close(self):
                self.c.close()

        def spy(root=None):
            c = real(root)
            return Spy(c) if c is not None else None

        store.open_ro = spy
        try:
            b = self.build([0.0] * 40)
        finally:
            store.open_ro = real
        self.assertEqual(b["status"], "few_data" if b["summary"] == "few_data" else "ok")
        self.assertTrue(seen)
        self.assertEqual(set(seen), {"SELECT"})
        # соединений-писателей не открывалось: хэш файла основной базы не изменился (WAL без записи не трогает основной файл)
        self.assertEqual(h, self.digest())


if __name__ == "__main__":
    unittest.main()
