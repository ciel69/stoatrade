"""tb.learnloop.exits_watch и подключение шагов выходов в цикл (R4, задача 15; Requirement 8.1–8.8, 16.7;
design.md «Сторож и расхождение с фактом», «Ревизии» п. 1, 3, 4; свойство 21).

Проверяется: точки 50/100/200 пересчитаны под блоки 24 ч (значения только в config/exits.json), решение только в точках и в недельную
точку, ложные откаты при истинной разности 0 не выше 0.05; откат по путям прогонов и по сделкам мозга (`d_b`) через
`exits_versions.rollback` — версия с политикой предыдущей, снимок, аренда, чёрный список, событие `exit_policy_rollback`; аренда после
успешной проверки и её отсутствие при ошибке шага; `EXIT_SIM_GAP` с паузой продвижения; состояние в `meta.exits_watch`; шаги выходов в
`learnloop.cycle` в порядке eval → search → verdicts → versions → watch, интервал оценок из `config/exits.json`, изоляция ошибки шага
выходов от шагов R2; идемпотентность. Временные корни, фейковые часы, сети нет."""
import copy
import json
import os
import random
import shutil
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.core import bus
from tb.core import exitdoc as XD
from tb.knowledge import store2
from tb.labels import paths as LP
from tb.labels import store
from tb.learnloop import conf as LC
from tb.learnloop import cycle as CY
from tb.learnloop import exits_conf as EC
from tb.learnloop import exits_eval as EE
from tb.learnloop import exits_versions as XV
from tb.learnloop import exits_watch as EW
from tb.logic import exitpol as XP
from tb.logic import exitsim as XS

NOW = 1793750400                     # 2026-11-04T00:00:00Z
DAY = 86400
HOUR = 3600
INIT_TS = NOW - 12 * DAY
PROMO = NOW - 10 * DAY
CFG_FILES = ("exits.json", "learnloop.json")


def shipped_watch():
    with open(os.path.join(H.PROJECT, "config", "exits.json"), encoding="utf-8") as f:
        return json.load(f)["watch"]


def new_policy(floor):
    return XP.normalize({"lock": [{"at_r": 1.0, "keep": 0.5}, {"at_r": 1.5, "keep": 0.65}, {"at_r": 2.0, "keep": 0.75}]}, floor)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        for name in CFG_FILES:
            shutil.copy(os.path.join(H.PROJECT, "config", name), os.path.join(self.root, "config", name))
        self.conn = store.open_rw(self.root)
        self.addCleanup(self.conn.close)
        store2.migrate_x1(self.conn, self.root, backup=False, now=NOW)
        store2.migrate_v2a(self.conn, self.root, enabled=True, backup=False, now=NOW)
        self.cfg = EC.load_strict(self.root)
        self.lcfg, errs = LC.load(self.root)
        self.assertEqual(errs, [])
        self.floor = EC.current_floor(self.root)
        self.assertEqual(XV.init(self.conn, self.floor, INIT_TS), "X-0001")
        self.promo = self.add_version(new_policy(self.floor), ts=PROMO, numbers={"var": "L1", "var_ver": 1})
        XD.clear_cache()

    def add_version(self, policy, ts=PROMO, numbers=None, reason="PROMOTE", parent=None):
        ver = XV.ver_name(len(XV.versions(self.conn)) + 1)
        store2.append_row(self.conn, "exit_versions", {
            "ver": ver, "parent": parent or XV.active_version(self.conn), "reason_code": reason, "policy": policy,
            "policy_h": XP.policy_hash(policy), "floor_h": XP.policy_hash(self.floor), "numbers": numbers,
            "actor": "code", "ts": int(ts)})
        return ver

    def eval_row(self, var, key, t0, block, r_net, src="run", labeled_ts=None):
        inp = EE.market_inputs(self.root)
        return {"src": src, "key": key, "var_id": var, "var_ver": XV.ver_num(var), "ord": "S", "px_mode": "fee",
                "policy_h": "a" * 16, "cost_h": inp["cost_h"], "sim_ver": XS.SIM_VER, "t0": int(t0), "block": block,
                "labeled_ts": int(t0 + HOUR if labeled_ts is None else labeled_ts), "r_net": float(r_net), "r_gross": float(r_net),
                "fees_r": 0.0, "mfe_r": 1.0, "mae_r": 0.1, "path_mfe_r": 1.0, "exit_reason": "end", "hold_min": 60.0,
                "end_ts": int(t0) + HOUR, "amb": 0, "created_ts": int(t0)}

    def run_pair(self, day, ra, rb, prev="X-0001"):
        """Пара путей действующей и предыдущей версии в блоке суток `day` (12:00 UTC) -> средняя разность ra − rb."""
        t0 = PROMO + int(day) * DAY + 12 * HOUR
        block = "%d:long" % (t0 // DAY)
        rows = [self.eval_row(self.promo, "k%d" % day, t0, block, ra), self.eval_row(prev, "k%d" % day, t0, block, rb)]
        with store.tx(self.conn):
            store2.insert_rows(self.conn, "exit_evals", rows, ignore_dup=True)

    def trade_pair(self, i, d_b, prev="X-0001", mode=None):
        """Пара сделок мозга действующей и предыдущей версии с разностью d_b (entry_ts ≥ продвижения)."""
        ts = PROMO + i * 3600
        key = "j%03d" % i
        rows = [self.eval_row(self.promo, key, ts, "%d:long" % (ts // DAY), d_b, src="trade"),
                self.eval_row(prev, key, ts, "%d:long" % (ts // DAY), 0.0, src="trade")]
        with store.tx(self.conn):
            store2.insert_rows(self.conn, "exit_evals", rows, ignore_dup=True)
        if mode is not None:
            with store.tx(self.conn):
                LP.insert_trade_path(self.conn, {"journal_id": key, "plan_id": None, "sym": "XUSDT", "side": "long", "mode": mode,
                                                 "entry_ts": ts, "entry_px": 100.0, "stop0": 99.0, "tick": 0.01, "qty": 1.0,
                                                 "qty_step": 0.001, "min_qty": 0.001, "horizon": "scalp", "max_hold_min": 60.0,
                                                 "atr5_pct": None, "sig_type": None, "exit_ver": self.promo, "policy_h": None,
                                                 "close_ts": ts + 600, "r": d_b, "parts": "[]", "timeline": "[]", "rows": b"",
                                                 "end_ts": ts + 600, "flags": "", "fetched_ts": ts + 600})

    def doc(self):
        row = self.conn.execute("SELECT v FROM meta WHERE k=?", (EW.META,)).fetchone()
        return json.loads(row[0]) if row else None

    def versions(self):
        return [(v["ver"], v["reason_code"], v["parent"]) for v in XV.versions(self.conn)]

    def rollback_events(self):
        return [e for e in bus.read(root=self.root) if e["type"] == "exit_policy_rollback"]

    def lease(self):
        try:
            with open(XD.lease_path(self.root), encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            return None


# ---------------------------------------------------------------- чистые функции: точки и решение

class TestPoints(unittest.TestCase):
    def setUp(self):
        self.cfg = {"watch": dict(shipped_watch())}

    def blocks(self, n, value=0.0):
        return [(i, "%d:long" % i, value) for i in range(n)]

    def test_looks_are_recalculated_for_daily_blocks(self):
        w = shipped_watch()
        self.assertEqual(w["looks"], [2, 4, 8])                      # 50/100/200 часовых блоков ≈ 1/2/4 суток
        self.assertEqual(w["weekly_window"], 8)                      # 200 часовых блоков ≈ 4 суток
        self.assertEqual([int(x) for x in w["looks"]], sorted(int(x) for x in w["looks"]))

    def test_points_fire_one_by_one(self):
        self.assertEqual(EW.points_due(self.blocks(1), 0, {"done": [], "week": 0}, self.cfg), [])
        due = EW.points_due(self.blocks(2), 0, {"done": [], "week": 0}, self.cfg)
        self.assertEqual([x[0] for x in due], ["L2"])
        self.assertEqual(len(due[0][1]), 2)
        due = EW.points_due(self.blocks(4), 0, {"done": [2], "week": 0}, self.cfg)
        self.assertEqual([x[0] for x in due], ["L4"])
        due = EW.points_due(self.blocks(8), 0, {"done": [2, 4], "week": 0}, self.cfg)
        self.assertEqual([x[0] for x in due], ["L8"])

    def test_weekly_after_last_point(self):
        blocks = self.blocks(8)
        b200 = (blocks[-1][0] + 1) * DAY
        st = {"done": [2, 4, 8], "week": 0}
        self.assertEqual(EW.points_due(blocks, b200 + 7 * DAY - 1, st, self.cfg), [])
        due = EW.points_due(blocks, b200 + 7 * DAY, st, self.cfg)
        self.assertEqual([x[0] for x in due], ["W1"])
        self.assertEqual(EW.points_due(blocks, b200 + 8 * DAY, dict(st, week=1), self.cfg), [])
        due = EW.points_due(blocks, b200 + 30 * DAY, dict(st, week=1), self.cfg)
        self.assertEqual([x[0] for x in due], ["W4"])                # проверяется только последняя наступившая неделя

    def test_decide_direction_and_dead_zone(self):
        bad = EW.decide([-0.2, -0.21, -0.19, -0.2], 0.05 / 3, 0.02)
        self.assertTrue(bad["rollback"])
        self.assertLess(bad["mean"], -0.02)
        flat = EW.decide([0.01, -0.01, 0.02, -0.02], 0.05 / 3, 0.02)
        self.assertFalse(flat["rollback"])
        mild = EW.decide([-0.03, -0.01, -0.02, -0.01], 0.05 / 3, 0.02)
        self.assertFalse(mild["rollback"])                           # мёртвая зона у −delta

    def test_false_rollback_share_at_zero_effect(self):              # свойство 21
        rnd = random.Random(7)
        reps, bad = 2000, 0
        for _ in range(reps):
            blocks = [(i, "%d:long" % i, rnd.gauss(0.0, 0.35)) for i in range(8)]
            st = {"done": [], "week": 0}
            hit = False
            for label, xs, thr in EW.points_due(blocks, 0, st, self.cfg):
                d = EW.decide(xs, thr, 0.02)
                if label[0] == "L":
                    st["done"] = sorted(set(st["done"]) | {int(label[1:])})
                hit = hit or d["rollback"]
            bad += 1 if hit else 0
        self.assertLessEqual(bad / float(reps), 0.05, bad)


# ---------------------------------------------------------------- откат и аренда на базе

class TestWatch(Base):
    def test_no_rollback_in_dead_zone_and_lease_written(self):
        for day in range(8):
            self.run_pair(day, ra=0.10, rb=0.11)                     # d_w = −0.01: мёртвая зона
        rep = EW.run(self.conn, self.root, self.cfg, NOW)
        self.assertEqual(rep["code"], EW.OK)
        self.assertIsNone(rep["rollback"])
        self.assertEqual([p["look"] for p in rep["points"]], ["L2", "L4", "L8"])
        self.assertEqual(self.versions()[-1][0], self.promo)
        lease = self.lease()
        self.assertEqual((lease["ts"], lease["ver"], lease["by"]), (NOW, self.promo, "watch"))
        self.assertEqual(lease["policy_h"], XV.last_version(self.conn)["policy_h"])
        self.assertEqual(self.doc()["ts"], NOW)
        self.assertEqual(self.doc()["state"]["done"], [2, 4, 8])

    def test_rollback_by_run_paths(self):                            # свойство 21, Requirement 8.3
        for day in range(8):
            self.run_pair(day, ra=0.10, rb=0.34)                     # d_w = −0.24
        rep = EW.run(self.conn, self.root, self.cfg, NOW)
        self.assertEqual(rep["code"], EW.ROLLBACK)
        ver = rep["rollback"]
        self.assertEqual(self.versions()[-1], (ver, "ROLLBACK", self.promo))
        row = XV.last_version(self.conn)
        self.assertEqual(row["policy"], XV.version_row(self.conn, "X-0001")["policy"])       # политика предыдущей версии
        self.assertEqual(row["numbers"]["n"], 2)
        self.assertLess(row["numbers"]["mean_r"], -0.02)
        self.assertLess(row["numbers"]["p"], 0.05 / 3)
        with open(XD.snapshot_path(self.root), encoding="utf-8") as f:
            snap = json.load(f)
        self.assertEqual((snap["ver"], snap["policy_h"]), (ver, row["policy_h"]))
        lease = self.lease()
        self.assertEqual((lease["ver"], lease["policy_h"], lease["by"]), (ver, row["policy_h"], "rollback"))
        evs = self.rollback_events()
        self.assertEqual(len(evs), 1)
        self.assertEqual((evs[0]["d"]["ver"], evs[0]["d"]["from"], evs[0]["d"]["code"]), (ver, self.promo, "ROLLBACK"))
        self.assertEqual(self.versions(), [("X-0001", "INIT", None), (self.promo, "PROMOTE", "X-0001"),
                                           (ver, "ROLLBACK", self.promo)])

    def test_rollback_by_brain_trades(self):                         # Requirement 8.4
        for i in range(50):
            self.trade_pair(i, -0.2 - (i % 5) * 0.01)
        rep = EW.run(self.conn, self.root, self.cfg, NOW)
        self.assertEqual(rep["code"], EW.ROLLBACK)
        self.assertIsNotNone(rep["brain"])
        self.assertEqual(rep["brain"]["n"], 50)
        self.assertLess(rep["brain"]["mean"], -0.05)
        self.assertLess(rep["brain"]["p"], 0.01)
        self.assertEqual(XV.last_version(self.conn)["numbers"]["look"], "BRAIN")

    def test_brain_needs_min_n(self):
        for i in range(49):
            self.trade_pair(i, -0.3)
        rep = EW.run(self.conn, self.root, self.cfg, NOW)
        self.assertIsNone(rep["rollback"])
        self.assertEqual(rep["brain"]["n"], 49)

    def test_points_reset_after_rollback_and_idempotent(self):
        for day in range(8):
            self.run_pair(day, ra=0.10, rb=0.34)
        first = EW.run(self.conn, self.root, self.cfg, NOW)
        n_ver, n_ev = len(XV.versions(self.conn)), len(self.rollback_events())
        second = EW.run(self.conn, self.root, self.cfg, NOW)
        self.assertIsNone(second["rollback"])                            # повторного отката нет
        self.assertEqual(second["ver"], first["rollback"])
        self.assertEqual(len(XV.versions(self.conn)), n_ver)             # повтор не создаёт версий
        self.assertEqual(len(self.rollback_events()), n_ev)
        self.assertEqual(second["points"], [])
        self.assertEqual(self.doc()["state"]["seg"], first["rollback"])
        self.assertEqual(self.doc()["state"]["done"], [])

    def test_points_only_after_promotion(self):
        rows = []
        for i in range(8):
            t0 = PROMO - 10 * DAY + i * DAY
            block = "%d:long" % (t0 // DAY)
            rows.append(self.eval_row(self.promo, "old%d" % i, t0, block, 0.1))
            rows.append(self.eval_row("X-0001", "old%d" % i, t0, block, 0.5))
        with store.tx(self.conn):
            store2.insert_rows(self.conn, "exit_evals", rows, ignore_dup=True)
        rep = EW.run(self.conn, self.root, self.cfg, NOW)
        self.assertEqual(rep["points"], [])                             # пути до продвижения в счёт не идут

    def test_backlog_skips_check_and_lease(self):
        self.conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (EE.META_BACKLOG, "5"))
        self.conn.commit()
        rep = EW.run(self.conn, self.root, self.cfg, NOW)
        self.assertEqual(rep["code"], EW.BACKLOG)
        self.assertIsNone(self.lease())
        self.assertIsNone(self.doc())

    def test_disabled_config_does_nothing(self):
        rep = EW.run(self.conn, self.root, EC.disabled(), NOW)
        self.assertEqual(rep["code"], EW.DISABLED)
        self.assertIsNone(self.lease())
        self.assertIsNone(self.doc())

    def test_no_versions(self):
        root = H.mk_root(self)
        for name in CFG_FILES:
            shutil.copy(os.path.join(H.PROJECT, "config", name), os.path.join(root, "config", name))
        conn = store.open_rw(root)
        self.addCleanup(conn.close)
        store2.migrate_x1(conn, root, backup=False, now=NOW)
        store2.migrate_v2a(conn, root, enabled=True, backup=False, now=NOW)
        rep = EW.run(conn, root, EC.load_strict(root), NOW)
        self.assertEqual(rep["code"], EW.NO_VERSION)
        self.assertFalse(os.path.exists(XD.lease_path(root)))
        self.assertEqual(EW.state_get(conn)["ts"], NOW)

    def test_watch_state_feeds_promotion_ready(self):
        self.cfg = dict(self.cfg, promotion=dict(self.cfg["promotion"], mode="auto"))
        self.run_pair(0, ra=0.1, rb=0.1)
        EW.run(self.conn, self.root, self.cfg, NOW)
        store2.make_backup(self.root, "daily", now=NOW)
        rd = XV.promotion_ready(self.conn, self.cfg, NOW, root=self.root)
        self.assertTrue(rd["ok"], rd)


# ---------------------------------------------------------------- EXIT_SIM_GAP

class TestSimGap(Base):
    def candles(self, entry_ts, n=40):
        return [(entry_ts + i * 60, 100.0, 100.01, 99.99, 100.0, 1.0) for i in range(n)]

    def row(self, i, r_fact, mode="paper"):
        ts = NOW - 5 * DAY + i * 3600
        return {"journal_id": "g%03d" % i, "plan_id": None, "sym": "XUSDT", "side": "long", "mode": mode, "entry_ts": ts,
                "entry_px": 100.0, "stop0": 99.0, "tick": 0.01, "qty": 1.0, "qty_step": 0.001, "min_qty": 0.001,
                "horizon": "scalp", "max_hold_min": 60.0, "atr5_pct": 0.01, "sig_type": None, "exit_ver": "X-0001",
                "policy_h": None, "close_ts": ts + 2400, "r": float(r_fact), "parts": "[]", "timeline": "[]",
                "rows": LP.encode(self.candles(ts), 0.01, 100.0), "end_ts": ts + 2400, "flags": "", "fetched_ts": ts + 2500}

    def trade(self, i, r_fact, mode="paper"):
        with store.tx(self.conn):
            LP.insert_trade_path(self.conn, self.row(i, r_fact, mode))

    def sim_r_net(self):
        """Симуляция той же сделки: r_net версии позиции с хронологией (факт, при котором gap = 0)."""
        gap = EW._one_gap(self.conn, self.row(0, 0.0), EE.market_inputs(self.root), {})
        return -float(gap)

    def test_sim_gap_status_pauses_promotion(self):
        for i in range(30):
            self.trade(i, 0.5)                                       # факт далеко от симуляции (≈0)
        rep = EW.run(self.conn, self.root, self.cfg, NOW)
        self.assertEqual(rep["code"], EW.SIM_GAP)
        self.assertEqual(rep["codes"], [EW.SIM_GAP])
        self.assertTrue(EW.promotion_paused(self.conn))
        self.assertEqual(self.doc()["sim_gap"]["n"], 30)
        self.assertIsNotNone(self.lease())
        self.assertIn("EXIT_SIM_GAP", [r[0] for r in self.conn.execute("SELECT code FROM learn_events WHERE step=?", (EW.STEP,))])

    def test_sim_gap_needs_min_n(self):
        for i in range(29):
            self.trade(i, 0.5)
        rep = EW.run(self.conn, self.root, self.cfg, NOW)
        self.assertEqual(rep["code"], EW.OK)
        self.assertFalse(EW.promotion_paused(self.conn))

    def test_sim_gap_status_lifts_when_gap_small(self):
        fact = self.sim_r_net()
        for i in range(30):
            self.trade(i, fact)
        rep = EW.run(self.conn, self.root, self.cfg, NOW)
        self.assertEqual(rep["code"], EW.OK)
        self.assertLess(abs(self.doc()["sim_gap"]["mean"]), self.cfg["watch"]["sim_gap_r"])
        self.assertFalse(EW.promotion_paused(self.conn))


# ---------------------------------------------------------------- шаги в цикле

class TestCycleSteps(Base):
    def test_steps_registered_in_order(self):
        names = [s.name for s in CY.default_steps()]
        for n in ("exits_eval", "exits_search", "exits_verdicts", "exits_promote", "exits_watch", "exits_after"):
            self.assertIn(n, names)
        cyc = CY.Cycle(self.root, copy.deepcopy(self.lcfg), steps=CY.default_steps())
        ordered = [s.name for s in cyc._ordered()]
        pos = [ordered.index(n) for n in ("exits_eval", "exits_search", "exits_verdicts", "exits_promote", "exits_watch")]
        self.assertEqual(pos, sorted(pos))
        self.assertLess(ordered.index("exits_eval"), ordered.index("exits_search"))

    def test_exits_eval_uses_config_interval(self):
        cyc = CY.Cycle(self.root, copy.deepcopy(self.lcfg), steps=CY.default_steps())
        ctx = CY.Ctx(self.root, self.conn, copy.deepcopy(self.lcfg), NOW, cycle=cyc)
        self.assertEqual(CY.step_exits_eval(ctx)["code"], "OK")
        self.assertEqual(CY.step_exits_eval(ctx)["code"], "NOT_DUE")           # eval.every_s = 3600
        self.assertEqual(CY.step_exits_eval(CY.Ctx(self.root, self.conn, self.lcfg, NOW + 3600, cycle=cyc))["code"], "OK")

    def test_exits_error_does_not_stop_r2_steps_and_writes_no_lease(self):
        calls = []

        def r2(ctx):
            calls.append("r2")
            return {"code": "OK"}

        steps = [CY.Step("exits_watch", "hourly", EW.step_watch), CY.Step("r2_after", "hourly", r2, group="verdict")]
        cyc = CY.Cycle(self.root, copy.deepcopy(self.lcfg), steps=steps)
        with mock.patch.object(EW, "run", side_effect=RuntimeError("сбой сторожа")):
            rep = cyc.run_pass(self.conn, NOW)
        self.assertEqual(rep["ran"]["r2_after"], "OK")                            # шаг R2 выполнен
        self.assertEqual(rep["errors"], {"exits_watch": CY.STEP_ERROR})
        self.assertEqual(calls, ["r2"])
        self.assertIsNone(self.lease())                                           # при ошибке шага аренда не пишется

    def test_promote_step_paused_by_sim_gap(self):
        self.conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)",
                          (EW.META, json.dumps({"ts": NOW, "sim_gap": {"active": True}})))
        self.conn.commit()
        ctx = CY.Ctx(self.root, self.conn, copy.deepcopy(self.lcfg), NOW)
        with mock.patch.object(XV, "run") as run:
            rep = CY.step_exits_promote(ctx)
        self.assertEqual(rep["code"], EW.SIM_GAP)
        self.assertFalse(run.called)
        self.conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (EW.META, json.dumps({"ts": NOW, "sim_gap": {"active": False}})))
        self.conn.commit()
        with mock.patch.object(XV, "run", return_value={"code": "OK", "promoted": [], "waiting": [], "rejected": [],
                                                        "floor_changed": None}) as run:
            self.assertEqual(CY.step_exits_promote(ctx)["code"], "OK")
        self.assertTrue(run.called)

    def test_full_pass_runs_exits_steps(self):
        cyc = CY.Cycle(self.root, copy.deepcopy(self.lcfg), steps=CY.default_steps())
        rep = cyc.run_pass(self.conn, NOW)
        self.assertEqual(rep["errors"], {})
        self.assertEqual(rep["ran"]["exits_eval"], "OK")
        self.assertEqual(rep["ran"]["exits_verdicts"], "OK")
        self.assertIn("exits_search", rep["ran"])
        self.assertEqual(rep["ran"]["exits_promote"], "OK")                   # версии есть, продвигать нечего
        self.assertEqual(rep["ran"]["exits_watch"], "OK")
        self.assertTrue(os.path.exists(XD.lease_path(self.root)))
        ctx = CY.Ctx(self.root, self.conn, copy.deepcopy(self.lcfg), NOW)
        self.assertEqual(CY.step_exits_after(ctx)["code"], "OK")

    def test_watch_step_report(self):
        for day in range(8):
            self.run_pair(day, ra=0.1, rb=0.1)
        ctx = CY.Ctx(self.root, self.conn, copy.deepcopy(self.lcfg), NOW)
        rep = CY.step_exits_watch(ctx)
        self.assertEqual(rep["code"], EW.OK)
        self.assertEqual(rep["points"], 3)
        self.assertEqual(rep["lease"], NOW)


if __name__ == "__main__":
    unittest.main()
