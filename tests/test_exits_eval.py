"""tb.learnloop.exits_eval (R4, задача 10; Requirement 4, 10.7, 14.3).

Проверяется: оценки только по путям complete без gap; ключи и размерности строк; порядок `T` только у неоднозначных путей; `px_mode="taker"`
у вариантов с maker-частями; `block` по `unit.block_h`; `path_mfe_r` на горизонте `max_hold_min`; идемпотентность (свойства 12, 13);
пересчёт при смене `cost_h` и `sim_ver`; лимит `max_per_pass` и `eval_backlog`; ротация оценок меню; окно поиска с `cut_ts`; срез пути сделки
до `entry_ts` и лот из `trade_paths`; справочный срез `pass_active`. Всё на временных корнях, сеть не используется.
"""
import copy
import json
import os
import shutil
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.core import exec_cfg
from tb.knowledge import store2
from tb.labels import paths as LP
from tb.labels import schema as LS
from tb.labels import store
from tb.learnloop import exits_conf, exits_eval
from tb.logic import exitpol as XP
from tb.logic import exitsim as XS

NOW = 1793750400                     # 2026-11-04T00:00:00Z
DAY = 86400
TICK = 0.01
ENTRY = 100.0
STOP = 98.0                          # R = 2.00


# ---------------------------------------------------------------- синтетические пути

def _bars(ts0, closes, lows=None, highs=None, open0=None):
    """Свечи по списку закрытий; low/high по умолчанию — закрытие ± 0.02."""
    rows, t, prev = [], ts0, (closes[0] if open0 is None else open0)
    for i, c in enumerate(closes):
        o = prev
        lo = c - 0.02 if lows is None else lows[i]
        hi = c + 0.02 if highs is None else highs[i]
        rows.append((t, o, max(o, c, hi), min(o, c, lo), c, 10.0))
        prev, t = c, t + 60
    return rows


def path_rise(ts0):
    """Монотонный рост до 103 (1.5R) и полка: стоп не задет, неоднозначных свечей нет."""
    closes = [100.0] * 6 + [100.0 + 0.14 * i for i in range(1, 21)] + [102.8] * 20
    return _bars(ts0, closes, open0=100.0)


def path_stop(ts0):
    """Рост до 103, затем падение через стоп."""
    up = [100.0] * 6 + [100.0 + 0.14 * i for i in range(1, 21)]
    down = [102.8 - 0.35 * i for i in range(1, 21)]
    return _bars(ts0, up + down, open0=100.0)


def path_wide(ts0):
    """Рост до 103 и одна широкая свеча, в диапазон которой попали и стоп (~101.5), и уровень фиксации (102): неоднозначная свеча."""
    base = [100.0] * 6 + [100.0 + 0.14 * i for i in range(1, 21)]
    rows = _bars(ts0, base, open0=100.0)
    t = rows[-1][0] + 60
    rows.append((t, 102.8, 103.10, 97.00, 99.00, 10.0))
    rows.extend(_bars(t + 60, [99.0] * 10, open0=99.0))
    return rows


def plan(side="long", stop=STOP, mh=60.0, horizon="scalp", tps=None):
    return {"side": side, "entry": {"kind": "market", "level": None, "ttl_min": 60}, "stop": stop,
            "tps": list(tps or []), "fracs": [1.0 / len(tps)] * len(tps) if tps else [],
            "max_hold_min": mh, "tick": TICK, "horizon": horizon}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        for name in ("exits.json", "learnloop.json"):
            shutil.copy(os.path.join(H.PROJECT, "config", name), os.path.join(self.root, "config", name))
        self.conn = store.open_rw(self.root)
        self.addCleanup(self.conn.close)
        self.assertEqual(store2.migrate_x1(self.conn, self.root, backup=False, now=NOW), 1)
        self.cfg = exits_conf.load_strict(self.root)
        self.floor = exits_eval.market_inputs(self.root)["floor"]
        self.add_version("X-0001", self.floor)

    # ---- данные
    def add_version(self, ver, policy, parent=None, reason="INIT"):
        pol = XP.normalize(policy, self.floor)
        rec = {"ver": ver, "parent": parent, "reason_code": reason, "policy": pol, "policy_h": XP.policy_hash(pol),
               "floor_h": XP.policy_hash(self.floor), "numbers": {"n": 0}, "actor": "code", "ts": NOW}
        return store2.append_row(self.conn, "exit_versions", rec)

    def add_run(self, key="k1", t0=None, rows=None, policy_id="P2", policy_ver=1, state=LS.PATH_COMPLETE,
                flags=None, labeled_ts=None, p=None):
        t0 = int(NOW - 30 * DAY) if t0 is None else int(t0)
        rows = path_rise(t0 + 60) if rows is None else rows
        p = plan() if p is None else p
        p = dict(p, entry_px=ENTRY)
        entry_ts = rows[0][0]
        rec = {"key": key, "policy_id": policy_id, "policy_ver": int(policy_ver), "t0": t0, "sym": "DOGEUSDT", "type": "range_5m",
               "side": p["side"], "horizon": p.get("horizon"), "entry_ts": entry_ts, "entry_px": ENTRY, "stop": p["stop"], "tick": TICK,
               "max_hold_min": p.get("max_hold_min"), "atr5_pct": 0.5, "plan": json.dumps(p), "rows": LP.encode(rows, TICK, ENTRY),
               "n_rows": len(rows), "end_ts": rows[-1][0] + 60, "state": state, "flags": flags, "aux": None, "aux_ver": None,
               "labeled_ts": int(t0 + 600 if labeled_ts is None else labeled_ts), "complete_ts": NOW}
        with store.tx(self.conn):
            LP.insert_run_paths(self.conn, [rec])
        return rec

    def add_trade(self, jid="j1", entry=None, rows=None, flags=None, qty=1.0, step=0.01, min_qty=0.01, timeline=None,
                  mode="paper"):
        entry = int(NOW - 20 * DAY) if entry is None else int(entry)
        if rows is None:
            # 30 минут ДО входа с ценами ниже стопа: без среза путь сразу даёт стоп
            pre = _bars(entry - 30 * 60, [82.0 + 0.1 * i for i in range(30)], open0=82.0)
            rows = pre + path_rise(entry)[1:]
        rec = {"journal_id": jid, "plan_id": "p1", "sym": "DOGEUSDT", "side": "long", "mode": mode, "entry_ts": entry,
               "entry_px": ENTRY, "stop0": STOP, "tick": TICK, "qty": qty, "qty_step": step, "min_qty": min_qty,
               "horizon": "scalp", "max_hold_min": 60.0, "atr5_pct": 0.5, "sig_type": "range_5m", "exit_ver": "X-0001",
               "policy_h": "a" * 16, "close_ts": rows[-1][0] + 60, "r": 0.5, "parts": "[]",
               "timeline": json.dumps(timeline or []), "rows": LP.encode(rows, TICK, ENTRY), "end_ts": rows[-1][0] + 60,
               "flags": flags, "fetched_ts": entry + 2 * DAY}
        with store.tx(self.conn):
            LP.insert_trade_path(self.conn, rec)
        return rec

    def add_trial(self, trial_id="XT-L1", var_id="L1", var_ver=1, state="open"):
        rec = {"trial_id": trial_id, "var_id": var_id, "var_ver": int(var_ver), "base_ver": "X-0001", "criterion": {},
               "criterion_h": "h" * 16, "state": state, "verdict": None, "numbers": None, "start_ts": NOW - 7 * DAY,
               "deadline_ts": NOW + 7 * DAY, "cut_ts": None, "closed_ts": None}
        store2.insert_rows(self.conn, "exit_trials", [rec])

    # ---- шаг и чтение
    def run_eval(self, now=NOW, limit=None, cfg=None):
        return exits_eval.eval_pass(self.conn, self.root, cfg if cfg is not None else self.cfg, now, limit=limit)

    def evals(self, sql="SELECT * FROM exit_evals", args=()):
        cur = self.conn.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def of(self, key, var=None, src="run", **kw):
        rows = [r for r in self.evals() if r["src"] == src and r["key"] == key]
        if var is not None:
            rows = [r for r in rows if (r["var_id"], r["var_ver"]) == var]
        for k, v in kw.items():
            rows = [r for r in rows if r.get(k) == v]
        return rows

    def meta(self, k):
        row = self.conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return None if row is None else row[0]


# ---------------------------------------------------------------- ключи и размерности

class TestKeys(unittest.TestCase):
    def test_run_key_roundtrip(self):
        self.assertEqual(exits_eval.run_key("abc", "P2", 3), "abc|P2|3")
        self.assertEqual(exits_eval.split_run_key("abc|P2|3"), ("abc", "P2", 3))
        self.assertIsNone(exits_eval.split_run_key("abc"))

    def test_version_var(self):
        self.assertEqual(exits_eval.version_var("X-0007"), ("X-0007", 7))
        self.assertEqual(exits_eval.version_var("X-0007", seq=3), ("X-0007", 7))
        self.assertEqual(exits_eval.version_var("LOCAL", seq=4), ("LOCAL", 4))

    def test_block_id(self):
        t0 = 1793750400
        self.assertEqual(exits_eval.block_id(t0, "long", 24), "%d:long" % (t0 // DAY))
        self.assertEqual(exits_eval.block_id(t0, "short", 1), "%d:short" % (t0 // 3600))

    def test_costs_hash_changes_with_fee(self):
        a = exits_eval.costs_hash({"fee": 0.001, "slip": 0.0002, "maker_fee": 0.00036, "stop_slip_bps": 5, "spread_bps": 2})
        b = exits_eval.costs_hash({"fee": 0.002, "slip": 0.0002, "maker_fee": 0.00036, "stop_slip_bps": 5, "spread_bps": 2})
        self.assertNotEqual(a, b)
        self.assertEqual(len(a), 16)


# ---------------------------------------------------------------- что оценивается

class TestWhat(Base):
    def test_only_complete_paths_without_gap(self):
        good = self.add_run("k1")
        self.add_run("k2", state=LS.PATH_PARTIAL)
        self.add_run("k3", flags=LS.FLAG_GAP)
        rep = self.run_eval()
        self.assertGreater(rep["n"], 0)
        key = exits_eval.run_key("k1", "P2", 1)
        self.assertTrue(self.of(key, ("X-0001", 1), ord="S", px_mode="fee"))
        self.assertFalse(self.of(exits_eval.run_key("k2", "P2", 1)))
        self.assertFalse(self.of(exits_eval.run_key("k3", "P2", 1)))

    def test_trade_path_adopted_and_gap_skipped(self):
        self.add_trade("j1")
        self.add_trade("j2", flags=LS.FLAG_ADOPTED)
        self.add_trade("j3", flags=LS.FLAG_GAP)
        self.run_eval()
        self.assertTrue(self.of("j1", ("X-0001", 1), src="trade"))
        self.assertFalse(self.of("j2", src="trade"))
        self.assertFalse(self.of("j3", src="trade"))

    def test_previous_version_evaluated(self):
        f2 = {"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]}
        self.add_version("X-0002", f2, parent="X-0001", reason="PROMOTE")
        self.add_run("k1")
        self.run_eval()
        key = exits_eval.run_key("k1", "P2", 1)
        self.assertTrue(self.of(key, ("X-0002", 2), ord="S", px_mode="fee"))
        self.assertTrue(self.of(key, ("X-0001", 1), ord="S", px_mode="fee"))

    def test_open_trial_variant_evaluated(self):
        self.add_run("k1")
        self.add_trial(var_id="L2", var_ver=1)
        self.run_eval()
        key = exits_eval.run_key("k1", "P2", 1)
        self.assertTrue(self.of(key, ("L2", 1), ord="S", px_mode="fee"))

    def test_closed_trial_variant_not_evaluated(self):
        self.add_run("k1")
        self.add_trial(var_id="L2", var_ver=1, state="closed")
        self.run_eval()
        key = exits_eval.run_key("k1", "P2", 1)
        self.assertFalse(self.of(key, ("L2", 1)))

    def test_row_fields_and_block(self):
        t0 = NOW - 30 * DAY
        self.add_run("k1", t0=t0)
        self.run_eval()
        row = self.of(exits_eval.run_key("k1", "P2", 1), ("X-0001", 1), ord="S", px_mode="fee")[0]
        self.assertEqual(row["sim_ver"], XS.SIM_VER)
        self.assertEqual(row["cost_h"], exits_eval.market_inputs(self.root)["cost_h"])
        self.assertEqual(row["policy_h"], XP.policy_hash(XP.normalize(self.floor, self.floor)))
        self.assertEqual(row["block"], exits_eval.block_id(t0, "long", 24))
        self.assertEqual(row["t0"], t0)
        self.assertAlmostEqual(row["path_mfe_r"], 1.41, places=2)     # high 102.82 при стопе 98 и входе 100

    def test_market_inputs_take_floor_and_stop_moves_from_exec_limits(self):
        inp = exits_eval.market_inputs(self.root)
        lim = exec_cfg.load(self.root)
        self.assertEqual(inp["stop_moves"], lim["stop_moves"])
        self.assertEqual(inp["floor"], XP.floor(lim["auto_protect"]))
        self.assertEqual(inp["costs"]["stop_slip_bps"], lim["auto_protect"]["stop_slip_bps"])
        self.assertIsNotNone(XP.policy_hash(inp["floor"]))

    def test_path_mfe_is_limited_by_max_hold_horizon(self):
        t0 = NOW - 30 * DAY
        rows = path_rise(t0 + 60)
        spike = _bars(rows[-1][0] + 60 + 20 * 60, [130.0] * 10, open0=130.0)   # пик после горизонта 60 мин
        rows = rows + spike
        self.add_run("k1", t0=t0, rows=rows)
        self.run_eval()
        row = self.of(exits_eval.run_key("k1", "P2", 1), ("X-0001", 1), ord="S", px_mode="fee")[0]
        self.assertLess(row["path_mfe_r"], 2.0)                                 # пик 130 не вошёл
        self.assertAlmostEqual(row["path_mfe_r"], 1.41, places=2)

    def test_disabled_config_does_nothing(self):
        self.add_run("k1")
        cfg = copy.deepcopy(self.cfg)
        cfg["enabled"] = False
        rep = self.run_eval(cfg=cfg)
        self.assertEqual(rep["n"], 0)
        self.assertEqual(self.evals(), [])


# ---------------------------------------------------------------- порядок T и px_mode

class TestOrders(Base):
    def setUp(self):
        Base.setUp(self)
        self.add_version("X-0002", {"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]}, parent="X-0001", reason="PROMOTE")

    def test_t_order_only_for_ambiguous_path(self):
        t0 = NOW - 30 * DAY
        self.add_run("rise", t0=t0, rows=path_rise(t0 + 60))
        self.add_run("wide", t0=t0 + 7200, rows=path_wide(t0 + 7200 + 60))
        self.run_eval()
        rise = self.of(exits_eval.run_key("rise", "P2", 1), ("X-0002", 2), px_mode="fee")
        wide = self.of(exits_eval.run_key("wide", "P2", 1), ("X-0002", 2))
        self.assertEqual([r["ord"] for r in rise], ["S"])
        self.assertEqual([r["ord"] for r in wide], ["S", "S", "T"])   # S/fee, S/taker, T/fee
        s = [r for r in wide if r["ord"] == "S" and r["px_mode"] == "fee"][0]
        t = [r for r in wide if r["ord"] == "T"][0]
        self.assertEqual(s["amb"], 1)
        self.assertEqual(t["amb"], 1)
        self.assertEqual(s["path_mfe_r"], t["path_mfe_r"])            # свойство пути, не варианта

    def test_t_row_matches_taker_simulator_in_order_t(self):
        t0 = NOW - 30 * DAY
        rows = path_wide(t0 + 60)
        self.add_run("wide", t0=t0, rows=rows)
        self.run_eval()
        row = self.of(exits_eval.run_key("wide", "P2", 1), ("X-0002", 2), ord="T")[0]
        inp = exits_eval.market_inputs(self.root)
        p = plan()
        p["entry_px"] = ENTRY
        res = XS.run(p, rows, XP.normalize({"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]}, self.floor),
                     self.floor, inp["costs"], order="T", maker=exits_eval.maker_cfg(self.cfg),
                     ctx={"max_hold_min": 60.0, "atr5_pct": 0.5, "sig_type": "range_5m", "horizon": "scalp", "entry_px": ENTRY,
                          "stop_moves": inp["stop_moves"]})
        self.assertAlmostEqual(row["r_net"], res["r_net"], places=12)

    def test_taker_rows_only_for_maker_variants(self):
        t0 = NOW - 30 * DAY
        self.add_run("k1", t0=t0)
        self.run_eval()
        key = exits_eval.run_key("k1", "P2", 1)
        modes = set(r["px_mode"] for r in self.of(key, ("X-0002", 2)))
        self.assertIn("fee", modes)
        self.assertIn("taker", modes)
        self.assertEqual(set(r["px_mode"] for r in self.of(key, ("X-0001", 1))), {"fee"})

    def test_taker_row_is_taker_only(self):
        t0 = NOW - 30 * DAY
        rows = path_rise(t0 + 60)
        self.add_run("k1", t0=t0, rows=rows)
        self.run_eval()
        row = self.of(exits_eval.run_key("k1", "P2", 1), ("X-0002", 2), px_mode="taker")[0]
        inp = exits_eval.market_inputs(self.root)
        p = dict(plan(), entry_px=ENTRY)
        res = XS.run(p, rows, XP.normalize({"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]}, self.floor),
                     self.floor, inp["costs"], order="S", maker=exits_eval.maker_cfg(self.cfg), taker_only=True,
                     ctx={"max_hold_min": 60.0, "atr5_pct": 0.5, "sig_type": "range_5m", "horizon": "scalp", "entry_px": ENTRY,
                          "stop_moves": inp["stop_moves"]})
        self.assertAlmostEqual(row["r_net"], res["r_net"], places=12)


# ---------------------------------------------------------------- идемпотентность и пересчёт

class TestIdempotentAndRecompute(Base):
    def test_second_pass_changes_nothing(self):        # свойство 13
        t0 = NOW - 30 * DAY
        self.add_run("k1", t0=t0, rows=path_wide(t0 + 60))
        self.add_trade("j1")
        self.add_trial(var_id="L2")
        self.run_eval()
        before = self.evals("SELECT * FROM exit_evals ORDER BY src, key, var_id, var_ver, ord, px_mode")
        rep = self.run_eval()
        after = self.evals("SELECT * FROM exit_evals ORDER BY src, key, var_id, var_ver, ord, px_mode")
        self.assertEqual(rep["n"], 0)
        self.assertEqual(rep["backlog"], 0)
        self.assertEqual(before, after)

    def test_same_window_same_numbers(self):           # свойство 12 (выборка окна неизменна)
        win1 = exits_eval.window(self.cfg, self.root, NOW)
        win2 = exits_eval.window(self.cfg, self.root, NOW)
        self.assertEqual(win1, win2)
        t0 = win1["b"] - 3 * DAY
        self.add_run("k1", t0=t0, labeled_ts=win1["cut_ts"])
        self.run_eval()
        first = self.evals("SELECT * FROM exit_evals ORDER BY src, key, var_id, var_ver, ord, px_mode")
        wins = [r for r in first if r["var_id"] != "X-0001"]
        self.assertTrue(wins)                                          # меню оценено в окне
        self.run_eval()
        self.assertEqual(first, self.evals("SELECT * FROM exit_evals ORDER BY src, key, var_id, var_ver, ord, px_mode"))

    def test_cost_change_recomputes_without_mixing(self):
        t0 = NOW - 30 * DAY
        self.add_run("k1", t0=t0)
        self.run_eval()
        old = self.evals("SELECT * FROM exit_evals")
        old_h = set(r["cost_h"] for r in old)
        path = os.path.join(self.root, "config", "risk_profile.json")
        with open(path, encoding="utf-8") as f:
            prof = json.load(f)
        prof["costs"]["bybit"]["fee"] = 0.002
        with open(os.path.join(self.root, "config", "risk_profile.json"), "w", encoding="utf-8") as f:
            json.dump(prof, f)
        rep = self.run_eval()
        self.assertGreater(rep["n"], 0)
        rows = self.evals("SELECT * FROM exit_evals")
        new_h = set(r["cost_h"] for r in rows)
        self.assertEqual(len(new_h), 2)                                 # прежние оценки не смешаны с новыми
        self.assertEqual(old_h, set([r["cost_h"] for r in rows if r["cost_h"] in old_h]))
        key = exits_eval.run_key("k1", "P2", 1)
        for h in new_h:
            self.assertTrue([r for r in rows if r["src"] == "run" and r["key"] == key and r["cost_h"] == h])
        same = [r for r in rows if r["key"] == key and (r["var_id"], r["var_ver"]) == ("X-0001", 1) and r["ord"] == "S"]
        self.assertEqual(len(same), 2)
        self.assertNotAlmostEqual(same[0]["r_net"], same[1]["r_net"], places=9)   # комиссия входит в R

    def test_sim_ver_change_recomputes(self):
        t0 = NOW - 30 * DAY
        self.add_run("k1", t0=t0)
        self.run_eval()
        with mock.patch.object(XS, "SIM_VER", "xs-test"):
            rep = self.run_eval()
        self.assertGreater(rep["n"], 0)
        self.assertEqual(rep["sim_ver"], "xs-test")
        vers = set(r["sim_ver"] for r in self.evals())
        self.assertEqual(vers, {XS.SIM_VER, "xs-test"})
        self.assertEqual(rep["backlog"], 0)


# ---------------------------------------------------------------- лимит и очередь

class TestLimitAndBacklog(Base):
    def test_max_per_pass_and_backlog(self):
        t0 = NOW - 30 * DAY
        for i in range(4):
            self.add_run("k%d" % i, t0=t0 + i * 3600)
        cfg = copy.deepcopy(self.cfg)
        cfg["eval"]["max_per_pass"] = 2
        rep = self.run_eval(cfg=cfg)
        self.assertEqual(rep["n"], 2)
        self.assertEqual(rep["backlog"], 2)
        self.assertEqual(self.meta(exits_eval.META_BACKLOG), "2")
        self.assertEqual(len(self.evals()), 2)
        rep = self.run_eval(cfg=cfg)
        self.assertEqual(rep["n"], 2)
        self.assertEqual(rep["backlog"], 0)
        self.assertEqual(self.meta(exits_eval.META_BACKLOG), "0")
        rep = self.run_eval(cfg=cfg)
        self.assertEqual(rep["n"], 0)
        self.assertEqual(len(self.evals()), 4)

    def test_limit_argument_overrides_config(self):
        for i in range(3):
            self.add_run("k%d" % i, t0=NOW - 30 * DAY + i * 3600)
        rep = self.run_eval(limit=1)
        self.assertEqual(rep["n"], 1)
        self.assertEqual(rep["backlog"], 2)


# ---------------------------------------------------------------- окно и cut_ts

class TestWindow(Base):
    def setUp(self):
        Base.setUp(self)
        self.win = exits_eval.window(self.cfg, self.root, NOW)
        self.assertIsNotNone(self.win)

    def test_menu_only_in_window_and_before_cut_ts(self):
        inside = self.win["a"] + 3600
        self.add_run("inside", t0=inside, labeled_ts=self.win["cut_ts"])
        self.add_run("late", t0=inside + 7200, labeled_ts=self.win["cut_ts"] + 1)
        self.add_run("edge", t0=self.win["b"], labeled_ts=self.win["cut_ts"])
        self.run_eval()
        self.assertTrue([r for r in self.of(exits_eval.run_key("inside", "P2", 1)) if r["var_id"] == "L1"])
        for k in ("late", "edge"):
            key = exits_eval.run_key(k, "P2", 1)
            self.assertFalse([r for r in self.of(key) if r["var_id"] == "L1"], k)
            self.assertTrue(self.of(key, ("X-0001", 1)), k)              # версия оценивается всегда

    def test_window_requires_cut_ts_not_in_future(self):
        self.assertLessEqual(self.win["cut_ts"], NOW)
        self.assertEqual(self.win["b"] - self.win["a"], self.cfg["search"]["window_days"] * DAY)

    def test_broken_learnloop_config_skips_menu(self):
        path = os.path.join(self.root, "config", "learnloop.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertEqual(exits_eval.window(self.cfg, self.root, NOW), None)
        t0 = NOW - 3 * DAY
        self.add_run("k1", t0=t0)
        rep = self.run_eval()
        self.assertIsNone(rep["window"])
        key = exits_eval.run_key("k1", "P2", 1)
        self.assertTrue(self.of(key, ("X-0001", 1)))
        self.assertFalse([r for r in self.of(key) if r["var_id"] == "L1"])


# ---------------------------------------------------------------- ротация оценок меню

class TestRotation(Base):
    def old_eval(self, var_id, var_ver, age_days=40, key="old-key", src="run"):
        inp = exits_eval.market_inputs(self.root)
        return {"src": src, "key": key, "var_id": var_id, "var_ver": int(var_ver), "ord": "S", "px_mode": "fee",
                "policy_h": "a" * 16, "cost_h": inp["cost_h"], "sim_ver": XS.SIM_VER, "t0": NOW - age_days * DAY,
                "block": "0:long", "labeled_ts": NOW - age_days * DAY, "r_net": 0.1, "r_gross": 0.2, "fees_r": 0.1,
                "mfe_r": 1.0, "mae_r": 0.1, "path_mfe_r": 1.0, "exit_reason": "end", "hold_min": 60.0, "end_ts": NOW - age_days * DAY,
                "amb": 0, "created_ts": int(NOW - age_days * DAY)}

    def test_old_menu_rows_rotated_but_versions_and_open_trials_kept(self):
        self.add_trial(var_id="L2")
        with store.tx(self.conn):
            store2.insert_rows(self.conn, "exit_evals", [self.old_eval("L1", 1), self.old_eval("L2", 1),
                                                         self.old_eval("X-0001", 1)], ignore_dup=True)
        cfg = copy.deepcopy(self.cfg)
        cfg["eval"]["keep_days_menu"] = 28
        rep = self.run_eval(cfg=cfg)
        self.assertGreaterEqual(rep["rotated"], 1)
        left = set((r["var_id"], r["var_ver"]) for r in self.evals("SELECT * FROM exit_evals WHERE key='old-key'"))
        self.assertNotIn(("L1", 1), left)                                # меню вне испытания удалено
        self.assertIn(("L2", 1), left)                                   # вариант открытого испытания сохранён
        self.assertIn(("X-0001", 1), left)                               # оценка версии сохранена

    def test_fresh_rows_not_rotated(self):
        with store.tx(self.conn):
            store2.insert_rows(self.conn, "exit_evals", [self.old_eval("L1", 1, age_days=1)], ignore_dup=True)
        self.run_eval()
        left = set(r["var_id"] for r in self.evals("SELECT * FROM exit_evals WHERE key='old-key'"))
        self.assertIn("L1", left)


# ---------------------------------------------------------------- пути сделок: срез и лот

class TestTradePaths(Base):
    def test_trade_rows_sliced_to_entry_ts(self):
        entry = NOW - 20 * DAY
        self.add_trade("j1", entry=entry)
        self.run_eval()
        row = self.of("j1", ("X-0001", 1), src="trade")[0]
        inp = exits_eval.market_inputs(self.root)
        # ожидание — прямой прогон по срезанному пути (первая свеча = свеча входа)
        all_rows = LP.decode(self.conn.execute("SELECT rows FROM trade_paths WHERE journal_id='j1'").fetchone()[0], TICK, ENTRY)
        sliced = [r for r in all_rows if r[0] >= entry]
        p = {"side": "long", "entry": {"kind": "market"}, "stop": STOP, "tps": [], "fracs": [], "tick": TICK,
             "max_hold_min": 60.0, "entry_px": ENTRY}
        ctx = {"max_hold_min": 60.0, "atr5_pct": 0.5, "sig_type": "range_5m", "horizon": "scalp", "entry_px": ENTRY,
               "stop_moves": inp["stop_moves"]}
        lot = {"qty": 1.0, "qty_step": 0.01, "min_qty": 0.01}
        want = XS.replay_brain(p, sliced, XP.normalize(self.floor, self.floor), self.floor, inp["costs"], [], ctx, lot)
        self.assertAlmostEqual(row["r_net"], want["r_net"], places=12)
        # без среза (путь с ценами ниже стопа до входа) результат другой: проверка, что срез есть
        whole = XS.replay_brain(p, all_rows, XP.normalize(self.floor, self.floor), self.floor, inp["costs"], [], ctx, lot)
        self.assertNotAlmostEqual(row["r_net"], whole["r_net"], places=6)

    def test_trade_lot_passed(self):
        # лот 0.001: доля частичной фиксации 0.3 у F2 округляется close_qty иначе, чем без лота
        self.add_version("X-0002", {"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]}, parent="X-0001", reason="PROMOTE")
        self.add_trade("j1", qty=0.003, step=0.001, min_qty=0.001)
        self.run_eval()
        row = self.of("j1", ("X-0002", 2), src="trade", ord="S", px_mode="fee")[0]
        inp = exits_eval.market_inputs(self.root)
        brows = self.conn.execute("SELECT rows FROM trade_paths WHERE journal_id='j1'").fetchone()[0]
        all_rows = LP.decode(brows, TICK, ENTRY)
        entry = self.conn.execute("SELECT entry_ts FROM trade_paths WHERE journal_id='j1'").fetchone()[0]
        sliced = [r for r in all_rows if r[0] >= entry]
        p = {"side": "long", "entry": {"kind": "market"}, "stop": STOP, "tps": [], "fracs": [], "tick": TICK,
             "max_hold_min": 60.0, "entry_px": ENTRY}
        ctx = {"max_hold_min": 60.0, "atr5_pct": 0.5, "sig_type": "range_5m", "horizon": "scalp", "entry_px": ENTRY,
               "stop_moves": inp["stop_moves"]}
        pol = XP.normalize({"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]}, self.floor)
        with_lot = XS.replay_brain(p, sliced, pol, self.floor, inp["costs"], [], ctx, {"qty": 0.003, "qty_step": 0.001, "min_qty": 0.001})
        no_lot = XS.replay_brain(p, sliced, pol, self.floor, inp["costs"], [], ctx, None)
        self.assertAlmostEqual(row["r_net"], with_lot["r_net"], places=12)
        self.assertNotAlmostEqual(with_lot["r_net"], no_lot["r_net"], places=12)

    def test_timeline_passed(self):
        self.add_trade("j1", timeline=[{"ts": NOW - 20 * DAY + 1200, "kind": "close", "price": 101.0, "why": "brain"}])
        self.run_eval()
        row = self.of("j1", ("X-0001", 1), src="trade", ord="S", px_mode="fee")[0]
        self.assertEqual(row["exit_reason"], "manual")


# ---------------------------------------------------------------- справочный pass_active

class TestPassActive(Base):
    RULE = {"rule_id": "RL0001", "kind": "skip_when", "cond": [{"feature": "ev_kind", "op": "=", "value": "range_5m"}],
            "param": {}, "scope": {"kinds": ["*"]}, "env_norm": "mainnet", "status": "active"}

    def setUp(self):
        Base.setUp(self)
        store2.migrate_v2a(self.conn, self.root, enabled=True, backup=False, now=NOW)

    def cluster_row(self, key):
        return {"cluster": "0:long", "hour": 0, "side": "long", "key": key, "t0": NOW - 30 * DAY, "type": "range_5m",
                "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c", "env_norm": "mainnet", "filled": 1, "r_net": 0.1,
                "feats": {}, "flags": None, "closed_ts": NOW}

    def test_pass_active_keys_cut_by_rule(self):
        with store.tx(self.conn):
            store2.insert_rows(self.conn, "cluster_runs", [self.cluster_row("k1")])
        cut = exits_eval.pass_active_keys(self.conn, active=[self.RULE])
        self.assertEqual(cut, {"k1"})
        self.assertIsNone(exits_eval.pass_active_keys(self.conn, active=[]))

    def test_evaluations_kept_for_all_paths_and_pass_active_counted(self):
        t0 = NOW - 30 * DAY
        self.add_run("k1", t0=t0)
        self.add_run("k2", t0=t0 + 3600)
        with store.tx(self.conn):
            store2.insert_rows(self.conn, "cluster_runs", [self.cluster_row("k1"), self.cluster_row("k2")])
        with mock.patch.object(exits_eval, "active_rules", lambda conn: [self.RULE]):
            rep = self.run_eval()
        self.assertEqual(rep["cut"], 2)                                   # оба прогона отсечены активным правилом
        self.assertEqual(rep["pass_active"], 0)
        # оценки записаны для обоих путей: отсечение справочное, а не фильтр
        self.assertTrue(self.of(exits_eval.run_key("k1", "P2", 1)))
        self.assertTrue(self.of(exits_eval.run_key("k2", "P2", 1)))

    def test_no_active_rules_all_paths_pass(self):
        self.add_run("k1")
        rep = self.run_eval()
        self.assertEqual(rep["cut"], 0)
        self.assertEqual(rep["pass_active"], 1)


if __name__ == "__main__":
    unittest.main()
