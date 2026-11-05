"""tb.learnloop.exits_report (R4, задача 20): эталон «стоп выбит, затем t1 достигнут», интервал Уилсона, пустые данные,
отчёт только читает базу (Requirement 13.1–13.5). Сеть и платформа не используются: строки путей пишутся в тестовую базу."""
import contextlib
import io
import json
import os
import shutil
import sqlite3
import unittest

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.knowledge import exits_cli as EC
from tb.labels import paths as LP
from tb.labels import store
from tb.learnloop import exits_report as ER
from tb.memory import cases

ENTRY = H.T0
STOP_IDX = 5
STOP_TS = ENTRY + STOP_IDX * 60
T1_IDX = 30
T1_TS = ENTRY + T1_IDX * 60
T1 = 104.0
PLAN = "PL-2611-DOGE-20"
JID = "BB-P-DOGEUSDT-1103-000020"
NOW = ENTRY + 6 * 3600


def minutes(entry_ts, n=40, stop_idx=STOP_IDX, t1_idx=T1_IDX):
    """Детерминированные минутные свечи: ровно 100, свеча стопа (провал к 97.9), свеча t1 (хай 104), дальше 100."""
    out = []
    for i in range(n):
        ts = entry_ts + i * 60
        o = c = 100.0
        hi, lo = 100.05, 99.95
        if i == stop_idx:
            o, hi, lo, c = 99.5, 99.6, 97.9, 98.1
        elif i == t1_idx:
            o, hi, lo, c = 101.0, T1, 100.9, 103.8
        out.append((ts, o, hi, lo, c))
    return out


def trade_rec(**over):
    rows = minutes(ENTRY)
    rec = {"journal_id": JID, "plan_id": PLAN, "sym": "DOGEUSDT", "side": "long", "mode": "paper", "entry_ts": ENTRY,
           "entry_px": 100.0, "stop0": 98.0, "tick": 0.01, "qty": 3.0, "qty_step": 0.1, "min_qty": 0.1, "horizon": "intraday",
           "max_hold_min": 60.0, "atr5_pct": 0.4, "sig_type": "oi_spike", "exit_ver": "X-0001", "policy_h": "abc123",
           "close_ts": STOP_TS, "r": -1.0,
           "parts": json.dumps([{"ts": STOP_TS, "px": 98.0, "qty": 3.0, "frac": 1.0, "fee": 0.02, "stop_type": "StopLoss"}]),
           "timeline": "[]", "rows": LP.encode(rows, 0.01, 100.0), "end_ts": rows[-1][0] + 60, "flags": None, "fetched_ts": NOW}
    rec.update(over)
    return rec


def run_rec(**over):
    rows = minutes(ENTRY)
    plan = {"side": "long", "entry": {"kind": "market"}, "stop": 98.0, "tps": [T1], "fracs": [1.0], "max_hold_min": 60.0,
            "tick": 0.01, "entry_px": 100.0}
    rec = {"key": "k-%s" % ENTRY, "policy_id": "P2", "policy_ver": 1, "t0": ENTRY, "sym": "DOGEUSDT", "type": "range_5m", "side": "long",
           "horizon": "intraday", "entry_ts": ENTRY, "entry_px": 100.0, "stop": 98.0, "tick": 0.01, "max_hold_min": 60.0,
           "atr5_pct": 0.4, "plan": json.dumps(plan), "rows": LP.encode(rows, 0.01, 100.0), "n_rows": len(rows),
           "end_ts": rows[-1][0] + 60, "state": "complete", "flags": None, "aux": None, "aux_ver": None, "labeled_ts": ENTRY,
           "complete_ts": NOW}
    rec.update(over)
    return rec


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        shutil.copy(os.path.join(H.PROJECT, "config", "exits.json"), os.path.join(self.root, "config", "exits.json"))
        self.conn = store.open_rw(self.root)
        self.addCleanup(self.conn.close)
        self.spec(PLAN, {"id": PLAN, "symbol": "DOGEUSDT", "side": "long", "session_type": "intraday",
                         "targets": [{"id": "t1", "price": T1, "frac": 1.0, "exec": "resting"}],
                         "lifecycle": {"ttl_min": 30, "max_hold_min": 60}})

    def spec(self, plan_id, obj):
        p = os.path.join(self.root, "state", "plans", plan_id, "spec.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def counts(self):
        return tuple(self.conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in ("trade_paths", "run_paths", "meta"))

    def report(self, **kw):
        kw.setdefault("mode", "paper")
        kw.setdefault("now", NOW)
        return ER.run(self.root, **kw)


class TestAfterReference(Base):
    def test_stop_then_t1_reached(self):
        LP.insert_trade_path(self.conn, trade_rec())
        rep = self.report()
        self.assertEqual((rep["db"], rep["ok"], rep["mode"]), ("OK", True, "paper"))
        self.assertEqual(rep["window_min"], 240)
        self.assertEqual(rep["trade"]["n"], 1)
        row = rep["trade"]["rows"][0]
        self.assertEqual((row["src"], row["key"], row["exit_reason"]), ("trade", JID, "sl"))
        self.assertEqual((row["exit_ts"], row["exit_px"], row["t1"]), (STOP_TS, 98.0, T1))
        self.assertAlmostEqual(row["after_mfe_r"], 3.0, places=6)                 # (104 − 98) / 2
        self.assertAlmostEqual(row["after_mfe_from_entry_r"], 2.0, places=6)      # (104 − 100) / 2
        self.assertAlmostEqual(row["after_mae_r"], 0.05, places=6)                # (98 − 97.9) / 2
        self.assertEqual((row["after_t1"], row["after_t1_min"]), (1.0, 25.0))
        agg = rep["trade"]["by_reason"]["sl"]
        self.assertEqual((agg["n"], agg["n_rows"], agg["after_t1_n"]), (1, 1, 1))
        self.assertEqual(agg["share"], 1.0)
        self.assertAlmostEqual(agg["wilson_lo"], 0.2065, delta=0.002)
        self.assertEqual(agg["wilson_hi"], 1.0)
        self.assertAlmostEqual(agg["mean_after_t1_min"], 25.0, places=6)

    def test_t1_not_reached_stays_zero(self):
        LP.insert_trade_path(self.conn, trade_rec(rows=LP.encode(minutes(ENTRY, t1_idx=99), 0.01, 100.0)))
        row = self.report()["trade"]["rows"][0]
        self.assertEqual(row["after_t1"], 0.0)
        self.assertIsNone(row["after_t1_min"])
        agg = self.report()["trade"]["by_reason"]["sl"]
        self.assertEqual((agg["after_t1_n"], agg["share"], agg["wilson_lo"]), (0, 0.0, 0.0))

    def test_window_cut_by_post_close(self):
        """Цель достигнута ПОЗЖЕ окна post_close_min: в долю не входит (Requirement 13.1)."""
        LP.insert_trade_path(self.conn, trade_rec(rows=LP.encode(minutes(ENTRY, n=300, t1_idx=260), 0.01, 100.0)))
        row = self.report()["trade"]["rows"][0]
        self.assertEqual(row["after_t1"], 0.0)
        self.assertLess(row["after_mfe_r"], 3.0)

    def test_unknown_t1_excluded_from_share(self):
        os.remove(os.path.join(self.root, "state", "plans", PLAN, "spec.json"))
        LP.insert_trade_path(self.conn, trade_rec())
        rep = self.report()
        row = rep["trade"]["rows"][0]
        self.assertIsNone(row["after_t1"])
        agg = rep["trade"]["by_reason"]["sl"]
        self.assertEqual((agg["n"], agg["n_rows"], agg["unknown_t1"]), (0, 1, 1))
        self.assertIsNone(agg["share"])

    def test_manual_close_reason_from_timeline(self):
        tl = json.dumps([{"ts": STOP_TS, "kind": "close", "price": 98.0, "frac": 1.0, "why": "brain"}])
        parts = json.dumps([{"ts": STOP_TS, "px": 98.0, "qty": 3.0, "frac": 1.0, "fee": 0.02, "stop_type": ""}])
        LP.insert_trade_path(self.conn, trade_rec(parts=parts, timeline=tl))
        row = self.report()["trade"]["rows"][0]
        self.assertEqual(row["exit_reason"], "manual")
        self.assertIn("manual", self.report()["trade"]["by_reason"])

    def test_mode_filter(self):
        LP.insert_trade_path(self.conn, trade_rec())
        LP.insert_trade_path(self.conn, trade_rec(journal_id=JID + "-L", mode="live"))
        self.assertEqual(self.report(mode="paper")["trade"]["n"], 1)
        rep_live = self.report(mode="live")
        self.assertEqual(rep_live["trade"]["rows"][0]["mode"], "live")
        self.assertEqual(rep_live["run"]["n"], 0)
        self.assertTrue(any("paper" in n for n in rep_live["notes"]))


class TestRunPaths(Base):
    def test_base_stop_then_t1(self):
        LP.insert_run_paths(self.conn, [run_rec()])
        rep = self.report()
        self.assertEqual(rep["run"]["n"], 1)
        row = rep["run"]["rows"][0]
        self.assertEqual((row["src"], row["mode"], row["exit_reason"]), ("run", "paper", "sl"))
        self.assertAlmostEqual(row["exit_px"], 98.0 * (1 - 5e-4), delta=0.01)
        self.assertGreater(row["after_mfe_r"], 2.9)
        self.assertEqual((row["after_t1"], row["after_t1_min"]), (1.0, 25.0))
        agg = rep["run"]["by_reason"]["sl"]
        self.assertEqual((agg["n"], agg["after_t1_n"]), (1, 1))

    def test_gap_and_partial_paths_skipped(self):
        LP.insert_run_paths(self.conn, [run_rec(key="gap", flags="gap"), run_rec(key="part", state="partial")])
        rep = self.report()
        self.assertEqual((rep["run"]["n"], rep["run"]["skipped"]), (0, 0))


class TestWilson(unittest.TestCase):
    def test_bounds(self):
        lo, hi = ER.wilson(1, 1)
        self.assertAlmostEqual(lo, 0.2065, delta=0.002)
        self.assertEqual(hi, 1.0)
        lo, hi = ER.wilson(0, 3)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.5614, delta=0.002)
        lo, hi = ER.wilson(2, 4)
        self.assertLess(lo, 0.5)
        self.assertGreater(hi, 0.5)
        self.assertEqual(ER.wilson(0, 0), (None, None))
        self.assertEqual(ER.wilson(5, 2), ER.wilson(2, 2))          # k обрезается по n
        self.assertEqual(ER.wilson("x", 3), (None, None))

    def test_reason_groups(self):
        self.assertEqual([ER.reason_group(x) for x in ("StopLoss", "sl", "PartialTakeProfit", "xp_lock", "xp_timeout")],
                         ["sl", "sl", "tp", "rule", "rule"])
        self.assertEqual(ER.reason_group("manual"), "manual")
        self.assertIsNone(ER.reason_group("liquidation"))
        self.assertIsNone(ER.reason_group(None))


class TestEmptyAndReadOnly(Base):
    def test_empty_db(self):
        rep = self.report()
        self.assertEqual((rep["db"], rep["ok"]), ("OK", True))
        self.assertEqual((rep["trade"]["n"], rep["run"]["n"]), (0, 0))
        self.assertEqual(rep["trade"]["by_reason"], {})
        text = "\n".join(ER.render(rep))
        self.assertIn("данных нет", text)
        self.assertIn("справочный", text)

    def test_no_db(self):
        root = H.mk_root(self)
        rep = ER.run(root, now=NOW)
        self.assertEqual((rep["db"], rep["ok"]), ("NO_DB", False))
        self.assertEqual((rep["trade"]["n"], rep["run"]["n"]), (0, 0))

    def test_report_reads_only(self):
        LP.insert_trade_path(self.conn, trade_rec())
        LP.insert_run_paths(self.conn, [run_rec()])
        before = self.counts()
        conn = store.open_ro(self.root)
        try:
            rep = ER.after(conn, mode="paper", now=NOW, root=self.root)
            self.assertEqual((rep["trade"]["n"], rep["run"]["n"]), (1, 1))
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO meta(k, v) VALUES('probe', '1')")
        finally:
            conn.close()
        self.assertEqual(self.counts(), before)

    def test_cli_after_reads_only(self):
        LP.insert_trade_path(self.conn, trade_rec())
        before = self.counts()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = EC.main(["after"], root=self.root)
        self.assertEqual(code, 0)
        rep = json.loads(buf.getvalue())
        self.assertEqual(rep["trade"]["n"], 1)
        self.assertEqual(self.counts(), before)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(EC.main(["after", "--text", "--days", "7"], root=self.root), 0)
        self.assertEqual(EC.main([], root=self.root), 2)


    def test_group_registered(self):
        from tb import __main__ as M
        self.assertEqual(M.V2.get("exits"), "tb.knowledge.exits_cli")


class TestCaseFields(unittest.TestCase):
    def test_case_schema_accepts_after_fields(self):
        row = {"after_mfe_r": 3.0, "after_t1": 1.0}
        self.assertEqual(ER.case_fields(row), {"after_mfe_r": 3.0, "after_t1": 1.0})
        self.assertEqual(ER.case_fields({}), {})
        rec = {"case_id": "C-20261104-DOGEUSDT-abc123", "type": "trade", "ts": "2026-11-04T12:00:00Z",
               "src": {"actor": "system", "rules_ver": "R-0007"}, "situation": {"sym": "DOGEUSDT", "side": "long"},
               "decision": {"action": "enter"}, "result": dict({"status": "closed", "r": 1.0}, **ER.case_fields(row))}
        norm, errs = cases.validate(rec)
        self.assertEqual(errs, [])
        self.assertEqual(norm["result"]["after_mfe_r"], 3.0)
        self.assertEqual(norm["result"]["after_t1"], 1.0)
        self.assertEqual(norm["src"]["rules_ver"], "R-0007")          # rules_ver не меняется
        old = {"case_id": "C-20261103-DOGEUSDT-def456", "type": "trade", "ts": "2026-11-03T12:00:00Z",
               "src": {"actor": "system"}, "situation": {"sym": "DOGEUSDT", "side": "long"},
               "decision": {"action": "enter"}, "result": {"status": "closed", "r": -1.0}}
        self.assertEqual(cases.validate(old)[1], [])                   # прежние случаи без новых полей валидны


if __name__ == "__main__":
    unittest.main()
