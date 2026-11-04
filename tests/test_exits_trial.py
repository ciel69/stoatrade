"""tb.learnloop.exits_trial (R4, задача 12; Requirement 6; свойства 15, 16, 17).

Проверяется: критерий при регистрации и его неизменность (`criterion_h`, триггер); чистый вердикт — таблица истинности условий
`MEAN`, `ROBUST_COST`, `TAIL`, `ROBUST`, `BRAIN` и `promote` тогда и только тогда, когда выполнены все; один порог `BRAIN` при
n от 10 до 60; исходы `NO_VERDICT` (`N`, `BRAIN_N`), `COST_CHANGED`, `SIM_CHANGED`, `FLOOR_CHANGED`, `BASE_CHANGED`; вердикт не
раньше `cut_ts` и повтор на той же выборке; отсутствие утечки будущего (`t0`/`entry_ts` до `start_ts`, `labeled_ts` после
`cut_ts`), `eval_backlog`, чёрный список при `reject` и справочные доли. Всё на временных корнях, сеть не используется.
"""
import copy
import os
import shutil
import sqlite3
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.core import expstats
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import clusters as CL
from tb.learnloop import conf as LC
from tb.learnloop import exits_conf
from tb.learnloop import exits_eval
from tb.learnloop import exits_trial as T
from tb.logic import exitpol as XP
from tb.logic import exitsim as XS

NOW = 1793750400                     # 2026-11-04T00:00:00Z (граница суток и часа)
DAY = 86400
DAY0 = NOW // DAY


# ---------------------------------------------------------------- синтетические выборки (чистые)

def _blk(k, side="long"):
    return "%d:%s" % (DAY0 + k, side)


def _t0(k, i=0):
    return (DAY0 + k) * DAY + 3600 + i * 60


def row(k, d, lo=None, mfe=0.0, d_taker=None, r_base=0.0, cut=False, amb=False, i=0, side="long"):
    """Строка пути для чистого `verdict`: d = r_var − r_base, d_lo по умолчанию равен d."""
    return {"key": "k%d-%d" % (k, i), "t0": _t0(k, i), "block": _blk(k, side), "labeled_ts": _t0(k, i),
            "d": float(d), "d_lo": float(d if lo is None else lo), "d_taker": d_taker,
            "r_var": r_base + float(d), "r_base": float(r_base), "gross_var": r_base + float(d), "gross_base": float(r_base),
            "fees_var": 0.0, "fees_base": 0.0, "mfe_var": max(float(mfe), 0.0), "mfe_base": max(float(mfe), 0.0),
            "path_mfe_r": float(mfe), "cut": bool(cut), "amb": bool(amb)}


def good_rows(n=4, d=0.1, tail=2, r_tail_base=1.0):
    """Все условия выполнены: 2 пути на блок, у первых `tail` путей MFE хвоста."""
    out = []
    for k in range(n):
        for i in range(2):
            mfe = 2.0 if k * 2 + i < tail else 0.0
            out.append(row(k, d, mfe=mfe, r_base=(r_tail_base if mfe else 0.0), i=i))
    return out


def brain_rows(n, mean, sd=0.0):
    vals = [float(mean)] * n if sd == 0 else [mean + (sd if i % 2 else -sd) for i in range(n)]
    return [{"key": "j%d" % i, "entry_ts": NOW + 60 * (i + 1), "d_b": float(v)} for i, v in enumerate(vals)]


def crit(**kw):
    c = {"v": 1, "start_ts": NOW, "deadline_ts": NOW + 21 * DAY, "block_h": 24, "n_target": 4,
         "alpha": 0.05, "alpha_adj": 0.025, "max_parallel": 2, "min_effect_r": 0.03,
         "tail": {"mfe_r": 1.5, "keep": 0.8, "min_n": 2}, "robust": {"tol_r": 0.02},
         "brain": {"n": 60, "min_n": 10, "tol_r": 0.08, "mode": "paper"},
         "var": {"id": "L1", "ver": 1, "policy_h": "a" * 16, "maker": False},
         "base": {"ver": "X-0001", "policy_h": "b" * 16}}
    c.update(kw)
    return c


# ---------------------------------------------------------------- чистый вердикт

class TestVerdict(unittest.TestCase):
    def verdict(self, rows=None, brains=None, c=None):
        return T.verdict(c or crit(), good_rows() if rows is None else rows, brains if brains is not None else brain_rows(10, 0.02))

    def test_promote_when_all_conditions(self):        # свойство 16
        v = self.verdict()
        self.assertEqual(v["verdict"], T.PROMOTE)
        self.assertEqual(v["code"], T.CODE_READY)
        self.assertEqual(set(v["conds"]), set(T.CONDS))
        self.assertTrue(all(v["conds"].values()), v["conds"])
        for k in ("n_blocks", "mean_d", "mean_lo", "tail_ratio", "brain_lb", "cut_share", "amb_share"):
            self.assertIn(k, v["numbers"])

    def test_mean_condition(self):
        v = self.verdict(rows=good_rows(d=0.0))
        self.assertEqual(v["verdict"], T.REJECT)
        self.assertFalse(v["conds"]["MEAN"])
        self.assertTrue(v["conds"]["TAIL"] and v["conds"]["ROBUST"] and v["conds"]["BRAIN"] and v["conds"]["ROBUST_COST"])

    def test_robust_condition(self):
        rows = [dict(r, d_lo=-0.5) for r in good_rows()]
        v = self.verdict(rows=rows)
        self.assertEqual(v["verdict"], T.REJECT)
        self.assertFalse(v["conds"]["ROBUST"])
        self.assertTrue(v["conds"]["MEAN"] and v["conds"]["TAIL"] and v["conds"]["BRAIN"])

    def test_tail_condition(self):
        rows = []
        for k in range(4):                              # в каждом блоке хвостовой путь хуже базы, остальные лучше
            rows.append(row(k, -0.3, mfe=2.0, r_base=1.0, i=0))
            rows.append(row(k, 0.4, i=1))
            rows.append(row(k, 0.4, i=2))
        v = self.verdict(rows=rows)
        self.assertEqual(v["verdict"], T.REJECT)
        self.assertFalse(v["conds"]["TAIL"])
        self.assertTrue(v["conds"]["MEAN"], v["numbers"])
        self.assertTrue(v["conds"]["ROBUST"])
        self.assertEqual(v["numbers"]["tail_n"], 4)
        self.assertLess(v["numbers"]["tail_ratio"], 0.8)

    def test_robust_cost_only_for_maker_variants(self):
        rows = [dict(r, d_taker=-0.5) for r in good_rows()]
        v = self.verdict(rows=rows)                     # taker-вариант: условие выполнено по построению
        self.assertEqual(v["verdict"], T.PROMOTE)
        self.assertTrue(v["conds"]["ROBUST_COST"])
        c = crit(var={"id": "F2", "ver": 1, "policy_h": "a" * 16, "maker": True})
        v = self.verdict(rows=rows, c=c)                # maker-вариант: выигрыш держится только на комиссиях
        self.assertEqual(v["verdict"], T.REJECT)
        self.assertFalse(v["conds"]["ROBUST_COST"])
        self.assertTrue(v["conds"]["MEAN"])

    def test_brain_condition(self):
        v = self.verdict(brains=brain_rows(10, -0.5))
        self.assertEqual(v["verdict"], T.REJECT)
        self.assertFalse(v["conds"]["BRAIN"])
        self.assertLess(v["numbers"]["brain_lb"], -0.08)

    def test_promote_iff_all_conditions(self):
        rows = [dict(r, d_lo=-0.5) for r in good_rows(d=0.0)]
        v = self.verdict(rows=rows)
        self.assertEqual(v["verdict"], T.REJECT)
        self.assertFalse(v["conds"]["MEAN"])
        self.assertFalse(v["conds"]["ROBUST"])
        self.assertTrue(v["conds"]["TAIL"] and v["conds"]["BRAIN"])
        self.assertEqual(sorted(v["numbers"]["conds"]), sorted(T.CONDS))

    def test_no_verdict_reasons(self):
        v = self.verdict(rows=good_rows(n=3))
        self.assertIsNone(v["verdict"])
        self.assertEqual(v["numbers"]["reason"], T.R_N)
        v = self.verdict(brains=brain_rows(5, 0.02))    # min_n = 10
        self.assertIsNone(v["verdict"])
        self.assertEqual(v["numbers"]["reason"], T.R_BRAIN_N)
        v = self.verdict(rows=good_rows(tail=1))        # tail.min_n = 2
        self.assertIsNone(v["verdict"])
        self.assertEqual(v["numbers"]["reason"], T.R_N)

    def test_one_brain_threshold_for_any_n(self):       # свойство «порог один»
        for n in range(10, 61):
            vals = [(-0.02 + (0.05 if i % 2 else -0.05)) for i in range(n)]
            se = (sum((x - sum(vals) / n) ** 2 for x in vals) / (n - 1)) ** 0.5 / (n ** 0.5)
            lb = sum(vals) / n - expstats.t_quantile(0.90, n - 1) * se
            v = self.verdict(brains=brain_rows(n, -0.02, 0.05))
            self.assertEqual(v["conds"]["BRAIN"], lb >= -0.08, n)
            self.assertEqual(v["numbers"]["brain_tol_r"], 0.08, n)
            self.assertEqual(v["numbers"]["brain_n"], n)

    def test_brain_interval_boundary(self):
        v10 = self.verdict(brains=brain_rows(10, -0.07, 0.03))
        v60 = self.verdict(brains=brain_rows(60, -0.07, 0.03))
        self.assertFalse(v10["conds"]["BRAIN"])          # n = 10: интервал шире
        self.assertTrue(v60["conds"]["BRAIN"])           # n = 60: тот же порог, интервал уже
        v = self.verdict(brains=brain_rows(10, -0.08, 0.0))
        self.assertTrue(v["conds"]["BRAIN"])             # граница: нижняя граница ровно −0.08 проходит

    def test_skips_rows_before_start_and_missing_d(self):    # свойство 17 (чистая часть)
        pre = [row(-k, 5.0, i=i) for k in range(1, 4) for i in range(2)]
        v0 = self.verdict()
        v1 = self.verdict(rows=good_rows() + pre + [{"key": "bad", "t0": NOW + 10, "block": _blk(0), "d": None}])
        self.assertEqual(v0["numbers"]["n_blocks"], v1["numbers"]["n_blocks"])
        self.assertEqual(v0["numbers"]["mean_d"], v1["numbers"]["mean_d"])
        v = self.verdict(rows=pre)
        self.assertIsNone(v["verdict"])
        self.assertEqual(v["numbers"]["n_blocks"], 0)
        old = [{"key": "j", "entry_ts": NOW - 60, "d_b": 1.0}]
        v = self.verdict(brains=old)
        self.assertEqual(v["numbers"]["reason"], T.R_BRAIN_N)

    def test_repeat_gives_same_verdict(self):            # свойство 12 (чистая часть)
        c = crit()
        rows, brains = good_rows(), brain_rows(12, 0.01, 0.05)
        self.assertEqual(T.verdict(c, rows, brains), T.verdict(c, rows, brains))

    def test_numbers_are_reference_only(self):
        rows = good_rows()
        for r in rows[:2]:
            r["cut"] = True
            r["amb"] = True
            r["fees_var"], r["fees_base"] = 0.05, 0.04
            r["gross_var"], r["gross_base"] = 1.0, 0.9
        v = self.verdict(rows=rows)
        nums = v["numbers"]
        self.assertAlmostEqual(nums["cut_share"], 2 / 8.0, places=4)
        self.assertAlmostEqual(nums["amb_share"], 2 / 8.0, places=4)
        self.assertIsNotNone(nums["fees_share_var"])
        self.assertIsNotNone(nums["cap_var"])
        self.assertTrue(v["conds"]["MEAN"])              # доли в критерий не входят


# ---------------------------------------------------------------- критерий и регистрация

class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        for name in ("exits.json", "learnloop.json"):
            shutil.copy(os.path.join(H.PROJECT, "config", name), os.path.join(self.root, "config", name))
        self.conn = store.open_rw(self.root)
        self.addCleanup(self.conn.close)
        self.assertEqual(store2.migrate_x1(self.conn, self.root, backup=False, now=NOW), 1)
        store2.migrate_v2a(self.conn, self.root, enabled=True, backup=False, now=NOW)
        self.cfg = exits_conf.load_strict(self.root)
        self.inp = exits_eval.market_inputs(self.root)
        self.floor = self.inp["floor"]
        lc, errs = LC.load(self.root)
        self.assertEqual(errs, [])
        self.ccfg = {"clusters": {"cluster_close_max_h": lc["clusters"]["cluster_close_max_h"]},
                     "intervals": {"observe_s": lc["intervals"]["observe_s"]}}
        self.add_version("X-0001", self.floor)

    def add_version(self, ver, policy, parent=None, reason="INIT"):
        pol = XP.normalize(policy, self.floor)
        rec = {"ver": ver, "parent": parent, "reason_code": reason, "policy": pol, "policy_h": XP.policy_hash(pol),
               "floor_h": XP.policy_hash(self.floor), "numbers": {"n": 0}, "actor": "code", "ts": NOW}
        return store2.append_row(self.conn, "exit_versions", rec)

    def small_cfg(self):
        c = copy.deepcopy(self.cfg)
        c["trial"]["n_target"] = 3
        c["tail"]["min_n"] = 2
        c["brain"]["n"] = 5
        c["brain"]["min_n"] = 2
        return c

    def cand(self, cfg=None, vid="L1"):
        v = [x for x in exits_conf.variants(cfg or self.cfg, self.floor) if x["id"] == vid][0]
        return {"id": v["id"], "ver": v["ver"], "family": v["family"], "policy": v["policy"], "policy_h": v["policy_h"]}

    def add_eval(self, key, t0, var_id, var_ver, src="run", order="S", px="fee", r_net=0.0, r_gross=None, fees=0.0,
                 mfe=0.0, path_mfe=None, reason="end", amb=0, labeled_ts=None):
        rec = {"src": src, "key": key, "var_id": var_id, "var_ver": int(var_ver), "ord": order, "px_mode": px,
               "policy_h": "a" * 16, "cost_h": self.inp["cost_h"], "sim_ver": XS.SIM_VER, "t0": int(t0),
               "block": "%d:long" % (int(t0) // DAY), "labeled_ts": int(t0 if labeled_ts is None else labeled_ts),
               "r_net": float(r_net), "r_gross": float(r_net if r_gross is None else r_gross), "fees_r": float(fees),
               "mfe_r": float(mfe), "mae_r": 0.0, "path_mfe_r": float(mfe if path_mfe is None else path_mfe),
               "exit_reason": reason, "hold_min": 60.0, "end_ts": int(t0) + 3600, "amb": int(amb), "created_ts": NOW}
        with store.tx(self.conn):
            store2.insert_rows(self.conn, "exit_evals", [rec], ignore_dup=True)

    def add_pair(self, key, t0, d=0.2, mfe=0.0, var=("L1", 1), src="run", labeled_ts=None, base_r=None):
        rb = (1.0 if mfe else 0.0) if base_r is None else base_r
        self.add_eval(key, t0, "X-0001", 1, src=src, r_net=rb, mfe=0.0, path_mfe=mfe, labeled_ts=labeled_ts)
        self.add_eval(key, t0, var[0], var[1], src=src, r_net=rb + d, mfe=mfe, path_mfe=mfe, labeled_ts=labeled_ts)

    def build(self, cfg, d=0.2, tail=2, trades=5, n_blocks=3, labeled_ts=None):
        """Испытание L1 на 3 будущих суточных блоках, `trades` сделках мозга и `tail` путях хвоста -> (trial_id, start, b, cut)."""
        start = CL.hour_ceil(NOW - 10 * DAY)
        tid = T.register(self.conn, self.cand(cfg), None, cfg, start, root=self.root)
        for k in range(n_blocks):
            for i in range(2):
                t0 = start + k * DAY + 3600 + i * 60
                mfe = 2.0 if k * 2 + i < tail else 0.0
                self.add_pair("k%d-%d" % (k, i), t0, d=d, mfe=mfe,
                              labeled_ts=(None if labeled_ts is None or k < n_blocks - 1 else labeled_ts))
        for j in range(trades):
            self.add_pair("j%d" % j, start + 600 + j * 60, d=0.02, src="trade")
        b = CL.hour_ceil(start + (n_blocks - 1) * DAY + 3600 + 60)
        return tid, start, b, CL.cut_ts(b, self.ccfg)


class TestRegister(Base):
    def test_criterion_written_and_stable(self):        # свойство 15
        cfg = self.small_cfg()
        now = NOW - 5 * DAY
        tid = T.register(self.conn, self.cand(cfg), None, cfg, now, root=self.root)
        t = T.get(self.conn, tid)
        c = t["criterion"]
        self.assertEqual(t["state"], T.OPEN)
        self.assertEqual((t["var_id"], t["var_ver"]), ("L1", 1))
        self.assertEqual(t["base_ver"], "X-0001")
        self.assertEqual(c["block_h"], cfg["unit"]["block_h"])
        self.assertEqual(c["n_target"], 3)
        self.assertEqual(c["brain"]["n"], 5)
        self.assertEqual(c["brain"]["min_n"], 2)
        self.assertEqual(c["alpha_adj"], 0.025)
        self.assertEqual(c["tail"], {"mfe_r": 1.5, "keep": 0.8, "min_n": 2})
        self.assertEqual(c["robust"], {"tol_r": 0.02})
        self.assertEqual(c["start_ts"], CL.hour_ceil(now))
        self.assertEqual(c["deadline_ts"], c["start_ts"] + cfg["trial"]["max_days"] * DAY)
        self.assertEqual(c["floor_h"], XP.policy_hash(self.floor))
        self.assertEqual(len(c["cfg_h"]), 16)
        self.assertEqual(t["criterion_h"], T.crit_hash(c))
        self.assertTrue(T.criterion_ok(t))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM exit_variants WHERE id='L1' AND ver=1").fetchone()[0], 1)

    def test_criterion_immutable(self):                 # свойство 15 (триггер)
        cfg = self.small_cfg()
        tid = T.register(self.conn, self.cand(cfg), None, cfg, NOW, root=self.root)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE exit_trials SET criterion=? WHERE trial_id=?", ('{"v":2}', tid))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE exit_trials SET criterion_h=? WHERE trial_id=?", ("x" * 16, tid))
        with store.tx(self.conn):
            store2.update_row(self.conn, "exit_trials", tid, {"state": T.CLOSED, "verdict": T.REJECT})
        self.assertEqual(T.get(self.conn, tid)["state"], T.CLOSED)

    def test_register_open_trial_twice_is_idempotent(self):
        cfg = self.small_cfg()
        a = T.register(self.conn, self.cand(cfg), None, cfg, NOW, root=self.root)
        b = T.register(self.conn, self.cand(cfg), None, cfg, NOW + 3600, root=self.root)
        self.assertEqual(a, b)
        self.assertEqual(len(T.trials(self.conn)), 1)

    def test_register_requires_active_base(self):
        with self.assertRaises(ValueError):
            T.register(self.conn, self.cand(self.small_cfg()), "X-0999", self.small_cfg(), NOW, root=self.root)


# ---------------------------------------------------------------- шаг вердиктов

class TestVerdicts(Base):
    def test_no_verdict_before_cut_ts(self):             # Requirement 6.6
        cfg = self.small_cfg()
        tid, start, b, cut = self.build(cfg)
        self.assertEqual(T.verdicts(self.conn, cfg, cut - 1, root=self.root), [])
        self.assertEqual(T.get(self.conn, tid)["state"], T.OPEN)
        res = T.verdicts(self.conn, cfg, cut, root=self.root)
        self.assertEqual(len(res), 1)
        t = T.get(self.conn, tid)
        self.assertEqual(t["state"], T.CLOSED)
        self.assertEqual(t["verdict"], T.PROMOTE)
        self.assertEqual(t["cut_ts"], cut)
        self.assertEqual(t["numbers"]["n_blocks"], 3)
        self.assertGreaterEqual(t["numbers"]["mean_d"], 0.03)
        self.assertTrue(all(t["numbers"]["conds"].values()), t["numbers"]["conds"])
        self.assertEqual(T.verdicts(self.conn, cfg, cut + 3600, root=self.root), [])   # закрытое не трогается

    def test_repeat_same_sample_same_verdict(self):      # свойство 12 (свойство 6.6)
        cfg = self.small_cfg()
        tid, start, b, cut = self.build(cfg)
        trial = T.get(self.conn, tid)
        s1, w1 = T.find_window(self.conn, trial, cut)
        s2, w2 = T.find_window(self.conn, trial, cut)
        self.assertEqual((s1, w1), (s2, w2))
        self.assertEqual(T.verdict(trial, w1["rows"], w1["brain"]), T.verdict(trial, w2["rows"], w2["brain"]))
        self.assertEqual(w1["b"], b)

    def test_labeled_ts_after_cut_ts_is_not_used(self):  # Requirement 4.3
        cfg = self.small_cfg()
        start = CL.hour_ceil(NOW - 10 * DAY)
        tid = T.register(self.conn, self.cand(cfg), None, cfg, start, root=self.root)
        b = CL.hour_ceil(start + 2 * DAY + 3600 + 60)
        cut = CL.cut_ts(b, self.ccfg)
        for k in range(3):
            for i in range(2):
                t0 = start + k * DAY + 3600 + i * 60
                mfe = 2.0 if k * 2 + i < 2 else 0.0
                late = cut + 1 if k == 2 else None
                self.add_pair("k%d-%d" % (k, i), t0, d=0.2, mfe=mfe, labeled_ts=late)
        for j in range(5):
            self.add_pair("j%d" % j, start + 600 + j * 60, d=0.02, src="trade")
        self.assertEqual(T.verdicts(self.conn, cfg, cut, root=self.root), [])     # третьего блока ещё нет в выборке
        self.assertEqual(len(T.verdicts(self.conn, cfg, cut + 3600, root=self.root)), 1)

    def test_deadline_no_verdict_n(self):                # Requirement 6.7
        cfg = self.small_cfg()
        tid = T.register(self.conn, self.cand(cfg), None, cfg, NOW - 10 * DAY, root=self.root)
        deadline = T.get(self.conn, tid)["deadline_ts"]
        dcut = CL.cut_ts(deadline, self.ccfg)
        self.assertEqual(T.verdicts(self.conn, cfg, dcut - 1, root=self.root), [])
        res = T.verdicts(self.conn, cfg, dcut, root=self.root)
        self.assertEqual(len(res), 1)
        t = T.get(self.conn, tid)
        self.assertEqual((t["state"], t["verdict"], t["numbers"]["reason"]), (T.CLOSED, T.INCONCLUSIVE, T.R_N))
        self.assertEqual(t["numbers"]["n_blocks"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM exit_blacklist").fetchone()[0], 0)

    def test_deadline_brain_n(self):                     # Requirement 6.5
        cfg = self.small_cfg()
        tid, start, b, cut = self.build(cfg, trades=1)
        deadline = T.get(self.conn, tid)["deadline_ts"]
        res = T.verdicts(self.conn, cfg, CL.cut_ts(deadline, self.ccfg), root=self.root)
        self.assertEqual(len(res), 1)
        t = T.get(self.conn, tid)
        self.assertEqual(res[0]["verdict"], T.INCONCLUSIVE)
        self.assertEqual(t["numbers"]["reason"], T.R_BRAIN_N)
        self.assertEqual(t["numbers"]["brain_n"], 1)

    def test_deadline_tail_n(self):                      # Requirement 6.3
        cfg = self.small_cfg()
        tid, start, b, cut = self.build(cfg, tail=0)
        deadline = T.get(self.conn, tid)["deadline_ts"]
        self.assertEqual(T.verdicts(self.conn, cfg, cut, root=self.root), [])       # хвоста нет — ждёт дедлайна
        res = T.verdicts(self.conn, cfg, CL.cut_ts(deadline, self.ccfg), root=self.root)
        self.assertEqual(len(res), 1)
        t = T.get(self.conn, tid)
        self.assertEqual((t["verdict"], t["numbers"]["reason"]), (T.INCONCLUSIVE, T.R_N))
        self.assertEqual(t["numbers"]["tail_n"], 0)

    def test_no_future_leak_into_trial(self):            # свойство 17
        cfg = self.small_cfg()
        start = CL.hour_ceil(NOW - 10 * DAY)
        tid = T.register(self.conn, self.cand(cfg), None, cfg, start, root=self.root)
        for k in range(1, 4):                            # пути до старта испытания
            for i in range(2):
                self.add_pair("old%d-%d" % (k, i), start - k * DAY + 3600 + i * 60, d=5.0, mfe=2.0)
        for j in range(5):                               # сделки до старта испытания
            self.add_pair("oldj%d" % j, start - 3600 + j * 60, d=1.0, src="trade")
        deadline = T.get(self.conn, tid)["deadline_ts"]
        T.verdicts(self.conn, cfg, CL.cut_ts(deadline, self.ccfg), root=self.root)
        t = T.get(self.conn, tid)
        self.assertEqual(t["numbers"]["n_blocks"], 0)
        self.assertEqual(t["numbers"]["brain_n"], 0)
        self.assertEqual(t["numbers"]["reason"], T.R_N)

    def test_reject_goes_to_blacklist(self):             # Requirement 5.8
        cfg = self.small_cfg()
        tid, start, b, cut = self.build(cfg, d=0.0)
        res = T.verdicts(self.conn, cfg, cut, root=self.root)
        self.assertEqual(len(res), 1)
        t = T.get(self.conn, tid)
        self.assertEqual(t["verdict"], T.REJECT)
        self.assertEqual(res[0]["code"], T.CODE_REJECT)
        row = self.conn.execute("SELECT var_id, var_ver, until_ts, reason FROM exit_blacklist").fetchone()
        self.assertEqual(row[:2], ("L1", 1))
        self.assertEqual(row[2], cut + cfg["cooldown_days"] * DAY)
        self.assertEqual(row[3], T.CODE_REJECT)

    def test_backlog_blocks_verdicts(self):              # Requirement 4.5
        cfg = self.small_cfg()
        tid, start, b, cut = self.build(cfg)
        self.conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", ("eval_backlog", "3"))
        self.conn.commit()
        self.assertEqual(T.verdicts(self.conn, cfg, cut, root=self.root), [])
        self.assertEqual(T.get(self.conn, tid)["state"], T.OPEN)

    def test_disabled_config_does_nothing(self):
        cfg = self.small_cfg()
        tid, start, b, cut = self.build(cfg)
        off = copy.deepcopy(cfg)
        off["enabled"] = False
        self.assertEqual(T.verdicts(self.conn, off, cut, root=self.root), [])
        self.assertEqual(T.get(self.conn, tid)["state"], T.OPEN)

    def test_cost_changed(self):                         # Requirement 6.8
        cfg = self.small_cfg()
        tid = T.register(self.conn, self.cand(cfg), None, cfg, NOW - DAY, root=self.root)
        with mock.patch.object(exits_eval, "market_inputs", lambda root=None: dict(self.inp, cost_h="0" * 16)):
            res = T.verdicts(self.conn, cfg, NOW, root=self.root)
        self.assertEqual([r["code"] for r in res], [T.COST_CHANGED])
        self.assertEqual(T.get(self.conn, tid)["verdict"], T.INCONCLUSIVE)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM exit_blacklist").fetchone()[0], 0)

    def test_sim_changed(self):
        cfg = self.small_cfg()
        tid = T.register(self.conn, self.cand(cfg), None, cfg, NOW - DAY, root=self.root)
        with mock.patch.object(XS, "SIM_VER", "xs-test"):
            res = T.verdicts(self.conn, cfg, NOW, root=self.root)
        self.assertEqual([r["code"] for r in res], [T.SIM_CHANGED])
        self.assertEqual(T.get(self.conn, tid)["state"], T.CLOSED)

    def test_floor_changed(self):
        cfg = self.small_cfg()
        tid = T.register(self.conn, self.cand(cfg), None, cfg, NOW - DAY, root=self.root)
        other = XP.floor({"enabled": True, "be_r": 0.4, "trail_activate_r": 1.0, "trail_lock_frac": 0.5, "min_profit_r": 0.2})
        with mock.patch.object(exits_eval, "market_inputs", lambda root=None: dict(self.inp, floor=other)):
            res = T.verdicts(self.conn, cfg, NOW, root=self.root)
        self.assertEqual([r["code"] for r in res], [T.FLOOR_CHANGED])
        self.assertEqual(T.get(self.conn, tid)["state"], T.CLOSED)

    def test_base_changed(self):                         # Requirement 6.8: продвижение другой версии
        cfg = self.small_cfg()
        tid = T.register(self.conn, self.cand(cfg), None, cfg, NOW - DAY, root=self.root)
        self.add_version("X-0002", {"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]}, parent="X-0001", reason="PROMOTE")
        res = T.verdicts(self.conn, cfg, NOW, root=self.root)
        self.assertEqual([r["code"] for r in res], [T.BASE_CHANGED])
        self.assertEqual(T.get(self.conn, tid)["state"], T.CLOSED)

    def test_closed_trial_not_touched(self):
        cfg = self.small_cfg()
        tid, start, b, cut = self.build(cfg)
        T.verdicts(self.conn, cfg, cut, root=self.root)
        before = self.conn.execute("SELECT state, verdict, numbers, closed_ts FROM exit_trials WHERE trial_id=?", (tid,)).fetchone()
        T.verdicts(self.conn, cfg, cut + 7200, root=self.root)
        after = self.conn.execute("SELECT state, verdict, numbers, closed_ts FROM exit_trials WHERE trial_id=?", (tid,)).fetchone()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
