"""tb.learnloop.exits_search (R4, задача 11; Requirement 5, 6.2–6.3; design.md «Поиск: max-T»; свойства 14, 17).

Проверяется: max-T по знаковым перестановкам блоков (детерминизм, совпадение с перестановочным p одного теста, устойчивость к пропускам);
свойство 14 — доля ложных кандидатов при перекрывающихся путях контролируется на блоках суточной шкалы и превышает порог на часовой; шаг поиска:
`OK` с кандидатами, `NOOP`, `NO_CANDIDATE`, `LOW_N`, `BACKLOG`, `NOT_DUE`, `DISABLED`, чёрный список, `ROBUST_COST`, хвост; свойство 17 — будущие
пути и пути с `labeled_ts > cut_ts` не входят в выборку, таблицы `near_tp` считаются только по путям строго раньше окна; запись `exit_search`
и `learn_events`; справочная доля совпадения `X-0001` с `policy_runs`; критерий скорости 560 блоков × 23 варианта ≤ 120 с. Всё на временных
корнях, сеть не используется.
"""
import json
import math
import os
import random
import shutil
import time
import unittest

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.knowledge import store2
from tb.labels import paths as LP
from tb.labels import schema as LS
from tb.labels import store
from tb.learnloop import exits_conf, exits_eval, exits_search, exits_trial
from tb.logic import exitpol as XP
from tb.logic import exitsim as XS

NOW = 1793750400                     # 2026-11-04T00:00:00Z
DAY = 86400
BLOCK = DAY // 2                     # смена стороны блока: 28 блоков (сутки, сторона) за 14 суток окна
PERF_FACTOR = float(os.environ.get("TB_PERF_FACTOR") or 1.0)


# ---------------------------------------------------------------- max-T без базы

def _entries(d):
    """{(id, ver): d} или {id: d} -> [(id, ver, d)] (версия по умолчанию 1)."""
    out = []
    for k, v in (d or {}).items():
        out.append((k[0], int(k[1]), v) if isinstance(k, tuple) else (str(k), 1, v))
    return out


def _manual_p(vals, B, seed):
    """Перестановочный p одного теста: доля перестановок со знаком, где t не меньше наблюдаемого (сверка с maxt при одном варианте)."""
    rng = random.Random(seed)
    n = len(vals)
    sq = sum(v * v for v in vals)
    obs = _t_manual(sum(vals), sq, n)
    hits = 0
    for _ in range(B):
        eps = [1 if rng.getrandbits(1) else -1 for _ in range(n)]
        if _t_manual(sum(e * v for e, v in zip(eps, vals)), sq, n) >= obs:
            hits += 1
    return (1.0 + hits) / (1.0 + B)


def _pkey(pair):
    return str(pair[0]), int(pair[1])


def _t_manual(s, sq, n):
    mean = s / n
    var = (sq - s * s / n) / (n - 1)
    if var <= 0.0:
        return 0.0 if mean == 0.0 else math.copysign(math.inf, mean)
    return mean / math.sqrt(var / n)


class TestMaxt(unittest.TestCase):
    def test_deterministic(self):
        D = [[0.1, -0.2], [0.3, 0.0], [-0.1, 0.4], [0.2, -0.1], [0.05, 0.3]]
        a = exits_search.maxt(D, 300, 12345)
        b = exits_search.maxt(D, 300, 12345)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 2)

    def test_single_variant_is_plain_permutation_p(self):
        vals = [0.10, 0.12, 0.09, 0.11, 0.13, 0.08, 0.10, 0.12]
        p = exits_search.maxt([[v] for v in vals], 400, 777)[0]
        self.assertAlmostEqual(p, _manual_p(vals, 400, 777), places=12)

    def test_zero_effect_gives_p_one(self):
        D = [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]]
        self.assertEqual(exits_search.maxt(D, 100, 5), [1.0, 1.0])

    def test_incomplete_rows_dropped(self):
        a = exits_search.maxt([[0.1, 0.2], [0.2, 0.1], [0.3, 0.0]], 200, 9)
        b = exits_search.maxt([[0.1, 0.2], [0.2, 0.1], [0.3, 0.0], [None, 0.5], [0.1, None]], 200, 9)
        self.assertEqual(a, b)

    def test_too_few_rows_is_none(self):
        self.assertEqual(exits_search.maxt([[0.1, 0.2]], 50, 1), [None, None])
        self.assertEqual(exits_search.maxt([], 50, 1), [])

    def test_criterion_speed_560_blocks_23_variants(self):          # критерий задачи 11
        rng = random.Random(3)
        D = [[rng.gauss(0.0, 0.1) for _ in range(23)] for _ in range(560)]
        t0 = time.time()
        ps = exits_search.maxt(D, 2000, 20261104)
        took = time.time() - t0
        self.assertEqual(len(ps), 23)
        self.assertLessEqual(took, 120.0 * PERF_FACTOR, "max-T 560 × 23: %.1f с" % took)


# ---------------------------------------------------------------- свойство 14

def _ar(rng, n, rho):
    """Авторегрессия первого порядка: значения соседних часовых блоков коррелированы (перекрывающиеся пути)."""
    out, prev = [], 0.0
    scale = math.sqrt(max(0.0, 1.0 - rho * rho))
    for _ in range(n):
        prev = rho * prev + scale * rng.gauss(0.0, 1.0)
        out.append(prev)
    return out


def _false_positive_rates(reps_h, reps_d, days, k, rho, noise, B, alpha, seed=20261104):
    """Доля прогонов поиска с хотя бы одним кандидатом при истинном эффекте 0: часовые блоки и суточные (среднее 24 часов)."""
    rng = random.Random(seed)
    hit_h = hit_d = 0
    for r in range(reps_d):
        common = _ar(rng, days * 24, rho)
        D = [[common[i] + rng.gauss(0.0, noise) for _ in range(k)] for i in range(days * 24)]
        s = rng.getrandbits(64)
        if r < reps_h and any(p is not None and p < alpha for p in exits_search.maxt(D, B, s)):
            hit_h += 1
        daily = [[sum(D[d * 24 + j][c] for j in range(24)) / 24.0 for c in range(k)] for d in range(days)]
        if any(p is not None and p < alpha for p in exits_search.maxt(daily, B, (s + 1) % (1 << 64))):
            hit_d += 1
    return hit_h / float(reps_h), hit_d / float(reps_d)


class TestProperty14(unittest.TestCase):
    def test_max_t_controls_family_error_with_overlapping_paths(self):
        """Свойство 14: на блоках суток (правило Requirement 4.4) доля ложных кандидатов ≤ 0.06, на часовых блоках — выше.

        Масштаб повторов уменьшен против спеки (2 000 перестановок × 1 000 повторов не помещаются в юнит-тест): 1 000 суточных прогонов
        (ошибка Монте-Карло ≈ 0.7%) и 250 часовых; B = 200. Синтетика — AR(1) по часам с ρ = 0.25 (замер задачи 2: 0.12–0.20; пути соседних
        часов перекрываются окном 390 мин) и независимой вариантной ошибкой; истинный эффект всех вариантов — 0.
        """
        hourly, daily = _false_positive_rates(250, 1000, 14, 6, 0.25, 1.0, 200, 0.05)
        self.assertLessEqual(daily, 0.06, "суточные блоки: доля ложных кандидатов %.3f" % daily)
        self.assertGreater(hourly, 0.06, "часовые блоки: доля ложных кандидатов %.3f (правило блоков не нужно?)" % hourly)
        self.assertGreater(hourly, daily)


# ---------------------------------------------------------------- база

class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        for name in ("exits.json", "learnloop.json"):
            shutil.copy(os.path.join(H.PROJECT, "config", name), os.path.join(self.root, "config", name))
        self.conn = store.open_rw(self.root)
        self.addCleanup(self.conn.close)
        self.assertEqual(store2.migrate_v2a(self.conn, self.root, enabled=True, backup=False, now=NOW), 1)   # learn_events (события шага)
        self.assertEqual(store2.migrate_x1(self.conn, self.root, backup=False, now=NOW), 1)
        self.cfg = exits_conf.load_strict(self.root)
        self.inp = exits_eval.market_inputs(self.root)
        self.floor = self.inp["floor"]
        self.cost_h = self.inp["cost_h"]
        self.base_h = XP.policy_hash(XP.normalize(self.floor, self.floor))
        self.menu_h = dict((_pkey((v["var_id"], v["var_ver"])), v["policy_h"])
                           for v in exits_search.menu_variants(self.cfg, self.floor))
        self.add_version("X-0001", self.floor)
        self.win = exits_search.window(self.cfg, self.root, NOW)
        self.assertIsNotNone(self.win)

    # ---- данные
    def add_version(self, ver, policy, parent=None, reason="INIT"):
        pol = XP.normalize(policy, self.floor)
        return store2.append_row(self.conn, "exit_versions",
                                 {"ver": ver, "parent": parent, "reason_code": reason, "policy": pol,
                                  "policy_h": XP.policy_hash(pol), "floor_h": XP.policy_hash(self.floor),
                                  "numbers": {"n": 0}, "actor": "code", "ts": NOW})

    def h_of(self, vid, ver):
        if str(vid) == "X-0001" and int(ver) == 1:
            return self.base_h
        return self.menu_h.get(_pkey((vid, ver)), "0" * 16)

    def ev_row(self, key, vid, ver, t0, side, r_net, mfe, reason="sl", amb=0, labeled_ts=None, px_mode="fee", block_h=None, ph=None):
        bh = self.cfg["unit"]["block_h"] if block_h is None else int(block_h)
        return {"src": "run", "key": key, "var_id": vid, "var_ver": int(ver), "ord": "S", "px_mode": px_mode,
                "policy_h": self.h_of(vid, ver) if ph is None else ph, "cost_h": self.cost_h, "sim_ver": XS.SIM_VER, "t0": int(t0),
                "block": exits_eval.block_id(t0, side, bh),
                "labeled_ts": int(t0 + 600 if labeled_ts is None else labeled_ts),
                "r_net": float(r_net), "r_gross": float(r_net), "fees_r": 0.0, "mfe_r": float(mfe), "mae_r": 0.0,
                "path_mfe_r": float(mfe), "exit_reason": reason, "hold_min": 60.0, "end_ts": int(t0) + 3600,
                "amb": int(amb), "created_ts": NOW}

    def add_path(self, t0, side, d_by_var, base_r=0.0, mfe=1.0, reasons=None, amb=0, labeled_ts=None, key=None, taker=None,
                 ph_by=None):
        """Один путь: оценка базы и оценки вариантов (`d_by_var`: {id | (id, ver): d}); `taker` — оценки по цене taker;
        `ph_by` — policy_h вариантов, отличающихся от меню (таблица near_tp)."""
        key = key or "k%d:%s" % (int(t0), side)
        rk = exits_eval.run_key(key, "P2", 1)
        reasons = reasons or {}
        ph_by = ph_by or {}
        rows = [self.ev_row(rk, "X-0001", 1, t0, side, base_r, mfe, reasons.get("X-0001", "sl"), amb, labeled_ts)]
        for vid, ver, d in _entries(d_by_var):
            rows.append(self.ev_row(rk, vid, ver, t0, side, base_r + d, mfe, reasons.get(vid, "sl"), amb, labeled_ts,
                                    ph=ph_by.get(_pkey((vid, ver)))))
        for vid, ver, d in _entries(taker):
            rows.append(self.ev_row(rk, vid, ver, t0, side, base_r + d, mfe, reasons.get(vid, "sl"), amb, labeled_ts,
                                    px_mode="taker", ph=ph_by.get(_pkey((vid, ver)))))
        with store.tx(self.conn):
            store2.insert_rows(self.conn, "exit_evals", rows, ignore_dup=True)
        return rk

    def add_window(self, d_by_var=None, base_r=0.0, mfe=1.0, reasons=None, per_block=2, n=28, d_fn=None, amb=0, taker=None,
                   ph_by=None):
        """Блоки окна: n половин суток (сутки и сторона), per_block путей в блоке (`d_fn(i, j)`, `mfe(i, j)` — на путь)."""
        for i in range(n):
            t0 = self.win["a"] + i * BLOCK
            side = "long" if i % 2 == 0 else "short"
            for j in range(per_block):
                d = d_fn(i, j) if d_fn else dict(d_by_var or {})
                mt = mfe(i, j) if callable(mfe) else mfe
                self.add_path(t0 + j, side, d, base_r=base_r, mfe=mt, reasons=reasons, amb=amb, taker=taker, ph_by=ph_by)

    def add_run_path(self, key, t0, typ="range_5m", horizon="scalp"):
        """Минимальная строка run_paths: для таблиц near_tp нужны только вид сигнала и горизонт."""
        rows = [(int(t0), 100.0, 100.1, 99.9, 100.0, 10.0), (int(t0) + 60, 100.0, 100.1, 99.9, 100.0, 10.0)]
        rec = {"key": key, "policy_id": "P2", "policy_ver": 1, "t0": int(t0), "sym": "DOGEUSDT", "type": typ, "side": "long",
               "horizon": horizon, "entry_ts": int(t0), "entry_px": 100.0, "stop": 98.0, "tick": 0.01, "max_hold_min": 60.0,
               "atr5_pct": 0.5, "plan": json.dumps({"side": "long", "entry": {"kind": "market"}, "stop": 98.0, "tps": [], "fracs": [],
                                                    "tick": 0.01, "max_hold_min": 60.0, "entry_px": 100.0}),
               "rows": LP.encode(rows, 0.01, 100.0), "n_rows": len(rows), "end_ts": int(t0) + 120, "state": LS.PATH_COMPLETE,
               "flags": None, "aux": None, "aux_ver": None, "labeled_ts": int(t0) + 120, "complete_ts": NOW}
        with store.tx(self.conn):
            LP.insert_run_paths(self.conn, [rec])
        return exits_eval.run_key(key, "P2", 1)

    def add_policy_run(self, key, r_net, reason="sl"):
        with store.tx(self.conn):
            self.conn.execute("INSERT OR REPLACE INTO policy_runs(key, policy_id, policy_ver, geometry_ver, ctx_ver, t0, sym, type, side, plan,"
                              " code, filled, exit_reason, r_gross, r_net, fees_r, mfe_r, mae_r, hold_min, flags)"
                              " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (key, "P2", 1, "g", "c", NOW - 30 * DAY, "DOGEUSDT", "range_5m", "long", None, None, 1, reason,
                               float(r_net), float(r_net), 0.0, 1.0, 0.0, 60.0, None))

    def add_blacklist(self, vid, ver, until=None, reason="TRIAL_REJECT"):
        store2.insert_rows(self.conn, "exit_blacklist", [{"var_id": vid, "var_ver": int(ver), "until_ts": int(until or NOW + DAY),
                                                          "reason": reason, "ts": NOW}])

    # ---- шаг и чтение
    def run_search(self, force=True, cfg=None, now=NOW):
        return exits_search.run(self.conn, self.root, cfg if cfg is not None else self.cfg, now, force=force)

    def searches(self):
        cur = self.conn.execute("SELECT * FROM exit_search ORDER BY id")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def events(self, step="exits_search"):
        cur = self.conn.execute("SELECT seq, step, code, ref, body FROM learn_events WHERE step=? ORDER BY seq", (step,))
        return [{"seq": r[0], "step": r[1], "code": r[2], "ref": r[3], "body": json.loads(r[4]) if r[4] else None} for r in cur.fetchall()]

    def variant(self, rep, vid):
        return next((v for v in rep["results"]["variants"] if v["id"] == vid), None)

    def set_backlog(self, n):
        with store.tx(self.conn):
            self.conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('eval_backlog', ?)", (str(int(n)),))


# ---------------------------------------------------------------- критерий скорости шага

class TestCriterion(Base):
    def test_pass_560_blocks_23_variants(self):
        """Критерий задачи 11: проход поиска на 560 блоках × 23 вариантах ≤ 120 с (порог × TB_PERF_FACTOR).

        Блоки часовой шкалы: 280 часов × 2 стороны окна 14 суток (block_h = 1 — худший случай по числу блоков); в каждом блоке путь
        базы и оценки всех 23 вариантов меню.
        """
        cfg = json.loads(json.dumps(self.cfg))
        cfg["unit"]["block_h"] = 1
        menu = exits_search.menu_variants(cfg, self.floor)
        rows = []
        for b in range(560):
            t0 = self.win["a"] + (b // 2) * 3600
            side = "long" if b % 2 == 0 else "short"
            rk = exits_eval.run_key("perf%d" % b, "P2", 1)
            rows.append(self.ev_row(rk, "X-0001", 1, t0, side, 0.5, 2.0, block_h=1))
            for i, v in enumerate(menu):
                rows.append(self.ev_row(rk, v["var_id"], v["var_ver"], t0, side, 0.5 + 0.001 * (i + 1), 2.0, block_h=1))
        with store.tx(self.conn):
            store2.insert_rows(self.conn, "exit_evals", rows, ignore_dup=True)
        self.assertEqual(len(rows), 560 * 24)
        t0 = time.time()
        rep = self.run_search(cfg=cfg)
        took = time.time() - t0
        self.assertGreaterEqual(rep["results"]["n_blocks"], 560)
        self.assertLessEqual(took, 120.0 * PERF_FACTOR, "проход поиска 560 × 24: %.1f с" % took)


# ---------------------------------------------------------------- исходы шага

class TestCodes(Base):
    def test_ok_candidate_and_persist(self):
        self.add_window({"L1": 0.1}, base_r=0.5, mfe=2.0)          # 56 путей хвоста: хвост проходит
        rep = self.run_search()
        self.assertEqual(rep["code"], "OK")
        self.assertEqual([c["id"] for c in rep["candidates"]], ["L1"])
        cand = rep["candidates"][0]
        self.assertEqual(cand["ver"], 1)
        self.assertAlmostEqual(cand["numbers"]["mean_d"], 0.1, places=9)
        self.assertLess(cand["numbers"]["p"], 0.05)
        self.assertGreaterEqual(cand["numbers"]["n_blocks"], 23)
        rows = self.searches()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["code"], "OK")
        self.assertEqual((row["a"], row["b"], row["cut_ts"]), (self.win["a"], self.win["b"], self.win["cut_ts"]))
        self.assertEqual(row["block_h"], 24)
        self.assertEqual(row["base_ver"], "X-0001")
        self.assertEqual(row["menu_ver"], self.cfg["menu_ver"])
        self.assertEqual(row["sample_h"], rep["sample_h"])
        self.assertEqual(row["cfg_h"], exits_conf.config_hash(self.cfg))
        self.assertEqual(len(row["sample_h"]), 16)
        res = json.loads(row["results"])
        for key in ("cut_share", "amb_share", "x0001_policy_runs", "near_tp", "variants"):
            self.assertIn(key, res)
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["code"], "OK")
        self.assertEqual(events[0]["ref"], rep["sample_h"])

    def test_noop_when_variant_repeats_base(self):
        self.add_window({"L1": 0.0}, base_r=0.5, mfe=2.0)
        rep = self.run_search()
        self.assertEqual(rep["code"], "NOOP")
        self.assertEqual(rep["candidates"], [])
        self.assertEqual(rep["results"]["noop"], ["L1"])
        self.assertEqual([r["code"] for r in self.searches()], ["NOOP"])

    def test_no_candidate_when_nothing_passes(self):
        # d = 0, но причина выхода другая: вариант не NOOP и не проходит (p = 1)
        self.add_window({"L1": 0.0}, base_r=0.5, mfe=2.0, reasons={"L1": "tp"})
        rep = self.run_search()
        self.assertEqual(rep["code"], "NO_CANDIDATE")
        self.assertFalse(self.variant(rep, "L1")["noop"])

    def test_backlog_waits_and_writes_nothing(self):
        self.add_window({"L1": 0.1}, base_r=0.5, mfe=2.0)
        self.set_backlog(3)
        rep = self.run_search()
        self.assertEqual(rep["code"], "BACKLOG")
        self.assertEqual(self.searches(), [])
        self.assertEqual(self.events(), [])

    def test_low_n(self):
        self.add_window({"L1": 0.1}, base_r=0.5, mfe=2.0, n=10)   # 10 блоков < min_clusters 23
        rep = self.run_search()
        self.assertEqual(rep["code"], "LOW_N")
        self.assertEqual(rep["candidates"], [])
        self.assertEqual(len(rep["results"]["blocks"]), 10)
        self.assertEqual([r["code"] for r in self.searches()], ["LOW_N"])

    def test_blacklist_excludes_variant(self):
        self.add_window({"L1": 0.1}, base_r=0.5, mfe=2.0)
        self.add_blacklist("L1", 1)
        rep = self.run_search()
        self.assertEqual(rep["code"], "NO_CANDIDATE")
        self.assertEqual([b[0] for b in rep["results"]["blacklist"]], ["L1"])
        self.assertIsNone(self.variant(rep, "L1"))
        self.assertFalse([c for c in rep["candidates"] if c["id"] == "L1"])

    def test_not_due_after_run(self):
        self.add_window({"L1": 0.1}, base_r=0.5, mfe=2.0)
        self.assertEqual(self.run_search()["code"], "OK")
        rep = self.run_search(force=False)
        self.assertEqual(rep["code"], "NOT_DUE")
        self.assertEqual(len(self.searches()), 1)                 # второй строки нет

    def test_disabled_config_writes_nothing(self):
        self.add_window({"L1": 0.1}, base_r=0.5, mfe=2.0)
        cfg = dict(self.cfg, enabled=False)
        rep = self.run_search(cfg=cfg)
        self.assertEqual(rep["code"], "DISABLED")
        self.assertEqual(self.searches(), [])

    def test_deterministic_on_same_sample(self):
        rng = random.Random(11)
        d = [{"L1": 0.1 + 0.001 * rng.random(), "L2": -0.02 * rng.random()} for _ in range(28)]
        self.add_window(per_block=2, d_fn=lambda i, j: d[i], base_r=0.5, mfe=2.0)
        a = self.run_search()
        b = self.run_search()
        self.assertEqual(json.dumps(a["results"], sort_keys=True), json.dumps(b["results"], sort_keys=True))
        self.assertEqual([c["id"] for c in a["candidates"]], [c["id"] for c in b["candidates"]])
        self.assertEqual(self.searches()[0]["sample_h"], self.searches()[1]["sample_h"])

    def test_x0001_match_with_policy_runs(self):
        self.add_window({"L1": 0.1}, base_r=0.5, mfe=2.0, per_block=1, n=28)
        keys = [r[0] for r in self.conn.execute("SELECT key FROM exit_evals WHERE var_id='X-0001' ORDER BY key").fetchall()]
        for i, rk in enumerate(keys):
            raw = exits_eval.split_run_key(rk)[0]
            if i == 0:
                self.add_policy_run(raw, 0.5, "sl")                # совпало с базой
            elif i == 1:
                self.add_policy_run(raw, 0.4, "sl")                # разошлось
            else:
                self.add_policy_run(raw, 0.5, "timeout")           # вне знаменателя
        rep = self.run_search()
        match = rep["results"]["x0001_policy_runs"]
        self.assertEqual(match["n"], 2)                            # только причины не timeout
        self.assertEqual(match["match"], 1)
        self.assertEqual(match["share"], 0.5)


# ---------------------------------------------------------------- условия кандидата

class TestGates(Base):
    def test_robust_cost_rejects_maker_variant_that_lives_on_fees(self):
        # F2 (maker): по цене fee эффект есть, по цене taker — нуль
        self.add_window({"F2": 0.1}, base_r=0.5, mfe=2.0, taker={("F2", 1): 0.0})
        rep = self.run_search()
        v = self.variant(rep, "F2")
        self.assertTrue(v["conds"]["p"] and v["conds"]["effect"] and v["conds"]["n"] and v["conds"]["tail"])
        self.assertFalse(v["conds"]["cost"])
        self.assertEqual(rep["code"], "NO_CANDIDATE")
        self.assertAlmostEqual(v["taker_mean_d"], 0.0, places=9)

    def test_tail_gate_rejects_variant_that_cuts_the_tail(self):
        # в каждом блоке 2 пути хвоста (MFE 2.0, d = −0.3 к базе 0.3) и 1 обычный (d = +1.0)
        def d_fn(i, j):
            return {"L1": -0.3} if j < 2 else {"L1": 1.0}

        def mfe_fn(i, j):
            return 2.0 if j < 2 else 1.0

        self.add_window(per_block=3, n=28, d_fn=d_fn, mfe=mfe_fn, base_r=0.3)
        rep = self.run_search()
        v = self.variant(rep, "L1")
        self.assertGreaterEqual(v["tail"]["n"], self.cfg["tail"]["min_n"])
        self.assertLess(v["tail"]["ratio"], self.cfg["tail"]["keep"])
        self.assertTrue(v["conds"]["p"] and v["conds"]["effect"] and v["conds"]["n"])
        self.assertFalse(v["conds"]["tail"])
        self.assertEqual(rep["code"], "NO_CANDIDATE")


# ---------------------------------------------------------------- свойство 17

class TestNoFutureLeak(Base):
    def test_future_paths_and_late_labels_not_used(self):
        self.add_window({"L1": 0.1}, base_r=0.5, mfe=2.0)
        first = self.run_search()
        base = self.variant(first, "L1")
        extra = [
            (self.win["b"], None),                                # ровно граница b (t0 < b)
            (self.win["b"] + 3600, None),                         # после окна
            (self.win["a"] + 60, self.win["cut_ts"] + 1),         # в окне, но размечен позже cut_ts
        ]
        for t0, lab in extra:
            self.add_path(t0, "long", {"L1": 100.0}, base_r=0.5, mfe=2.0, labeled_ts=lab, key="future%d" % t0)
        second = self.run_search()
        got = self.variant(second, "L1")
        self.assertEqual(got["n_paths"], base["n_paths"])
        self.assertAlmostEqual(got["mean_d"], base["mean_d"], places=12)
        self.assertEqual(second["sample_h"], first["sample_h"])

    def test_near_tp_tables_only_from_paths_before_window(self):
        # история строго раньше окна: MFE 0.8, 1.0, 1.2 (вид сигнала range_5m, горизонт scalp)
        for i, mfe in enumerate((0.8, 1.0, 1.2)):
            t0 = self.win["a"] - (i + 1) * DAY
            rk = self.add_run_path("old%d" % i, t0)
            with store.tx(self.conn):
                store2.insert_rows(self.conn, "exit_evals", [self.ev_row(rk, "X-0001", 1, t0, "long", 0.5, mfe)], ignore_dup=True)
        late = self.win["a"] - 5 * DAY                           # раньше окна, но размечен позже cut_ts
        rk = self.add_run_path("old-late", late)
        with store.tx(self.conn):
            store2.insert_rows(self.conn, "exit_evals",
                               [self.ev_row(rk, "X-0001", 1, late, "long", 0.5, 10.0, labeled_ts=self.win["cut_ts"] + 1)], ignore_dup=True)
        # пути окна: MFE 5.0 не должен попасть в таблицу (t0 не раньше окна)
        self.add_window({"L1": 0.1}, base_r=0.5, mfe=5.0, per_block=1, n=28)
        rep = self.run_search()
        n1 = rep["results"]["near_tp"]["N1"]["range_5m"]["scalp"]
        n2 = rep["results"]["near_tp"]["N2"]["range_5m"]["scalp"]
        self.assertAlmostEqual(n1, 0.96, places=6)               # квантиль 0.4 от (0.8, 1.0, 1.2)
        self.assertAlmostEqual(n2, 1.0, places=6)                # квантиль 0.5

    def test_apply_near_tp_into_candidate_policy(self):
        policy = XP.normalize({"near_tp": {"q": 0.4, "frac": 0.3, "min_r": 0.5, "exec": "maker"}}, self.floor)
        table = {"range_5m": {"scalp": 0.96}}
        out = exits_search.apply_near_tp(policy, table)
        self.assertEqual(out["near_tp"]["table"], table)
        self.assertNotIn("table", policy["near_tp"])
        self.assertNotEqual(XP.policy_hash(out), XP.policy_hash(policy))
        self.assertEqual(exits_search.apply_near_tp(policy, {}), policy)


# ---------------------------------------------------------------- near_tp: вариант становится кандидатом

class TestNearTpCandidates(Base):
    def near_tp_n1(self):
        """Таблица N1 по истории строго до окна и policy_h политики с этой таблицей."""
        for i, mfe in enumerate((0.8, 1.0, 1.2)):
            t0 = self.win["a"] - (i + 1) * DAY
            rk = self.add_run_path("old%d" % i, t0)
            with store.tx(self.conn):
                store2.insert_rows(self.conn, "exit_evals", [self.ev_row(rk, "X-0001", 1, t0, "long", 0.5, mfe)], ignore_dup=True)
        base = exits_search.base_variant(self.conn, self.floor)
        hist = exits_search.near_tp_history(self.conn, base, self.win["a"], self.win["cut_ts"], self.cost_h)
        raw = [v for v in exits_search.menu_variants(self.cfg, self.floor) if v["var_id"] == "N1"]
        table = exits_search.near_tp_tables(hist, raw)[("N1", 1)]
        pol = XP.normalize(exits_search.apply_near_tp(raw[0]["policy"], table), self.floor)
        return table, XP.policy_hash(pol)

    def test_near_tp_with_effect_becomes_candidate_and_register_keeps_table(self):
        table, ph = self.near_tp_n1()
        self.assertTrue(table)
        self.add_window({"N1": 0.1}, base_r=0.5, mfe=2.0, taker={("N1", 1): 0.1}, ph_by={("N1", 1): ph})
        rep = self.run_search()
        self.assertEqual([c["id"] for c in rep["candidates"]], ["N1"])
        cand = rep["candidates"][0]
        self.assertEqual(cand["policy_h"], ph)
        self.assertEqual(cand["policy"]["near_tp"]["table"], table)
        self.assertEqual(rep["results"]["near_tp"]["N1"], table)
        tid = exits_trial.register(self.conn, cand, None, self.cfg, NOW, root=self.root)
        row = self.conn.execute("SELECT policy, policy_h FROM exit_variants WHERE id='N1' AND ver=1").fetchone()
        self.assertEqual(row[1], ph)
        self.assertEqual(json.loads(row[0])["near_tp"]["table"], table)
        crit = json.loads(self.conn.execute("SELECT criterion FROM exit_trials WHERE trial_id=?", (tid,)).fetchone()[0])
        self.assertEqual(crit["var"]["policy_h"], ph)

    def test_near_tp_without_signal_history_is_noop(self):
        self.add_window({"N1": 0.0}, base_r=0.5, mfe=2.0, ph_by={("N1", 1): self.menu_h[("N1", 1)]})
        rep = self.run_search()
        self.assertEqual(rep["code"], "NOOP")
        self.assertIn("N1", rep["results"]["noop"])
        self.assertEqual(rep["results"]["near_tp"], {})


# ---------------------------------------------------------------- вспомогательное

class TestHelpers(Base):
    def test_sample_hash_depends_on_rows_only(self):
        a = {"k1": {"t0": 10, "labeled_ts": 11}, "k2": {"t0": 20, "labeled_ts": 21}}
        b = {"k2": {"t0": 20, "labeled_ts": 21}, "k1": {"t0": 10, "labeled_ts": 11}}
        self.assertEqual(exits_search.sample_hash(a), exits_search.sample_hash(b))
        c = dict(a, k2={"t0": 20, "labeled_ts": 22})
        self.assertNotEqual(exits_search.sample_hash(a), exits_search.sample_hash(c))

    def test_quantile(self):
        self.assertAlmostEqual(exits_search.quantile([0.8, 1.0, 1.2], 0.4), 0.96, places=12)
        self.assertAlmostEqual(exits_search.quantile([0.8, 1.0, 1.2], 0.5), 1.0, places=12)
        self.assertIsNone(exits_search.quantile([], 0.5))
        self.assertEqual(exits_search.quantile([2.0], 0.4), 2.0)

    def test_maker_parts(self):
        self.assertTrue(exits_search.maker_parts({"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]}))
        self.assertFalse(exits_search.maker_parts({"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "taker"}]}))
        self.assertTrue(exits_search.maker_parts({"timeout": {"hold_frac": 1.0, "exec": "maker"}}))
        self.assertFalse(exits_search.maker_parts({"lock": [{"at_r": 1.0, "keep": 0.5}]}))


if __name__ == "__main__":
    unittest.main()
