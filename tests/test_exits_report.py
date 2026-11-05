"""tb.learnloop.exits_report (R4, задачи 19 и 20): R4.3 «мозг против алгоритма» и писатель полей после закрытия.

Проверяется: свойство 22 (`brain_delta = 0` без вмешательств и при вмешательстве, не изменившем частей закрытия), вердикты
Requirement 12.2 по границам 95% интервала, отчёт на фикстуре из 40 сделок против ручного расчёта, разделение режимов,
разрезы по типам вмешательства и кодам пробуждения, доля `close_qty`, `write_after` (запись, идемпотентность, отсутствие
записи без готового пути). Сеть не используется.
"""
import json
import os
import shutil
import unittest

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.knowledge import store2
from tb.labels import paths as LP
from tb.labels import store
from tb.learnloop import exits_report as ER
from tb.memory import cases as MC

ENTRY = H.T0
STOP_TS = ENTRY + 5 * 60
T1 = 104.0
PLAN = "PL-2611-DOGE-20"
JID = "BB-P-DOGEUSDT-1103-000020"
NOW = ENTRY + 6 * 3600


def path_flat_rise(entry_ts, n=12):
    """Минутки: вход 100, на 2-й свече ход до 103, дальше 102; стоп 95 (R = 5) не задет."""
    rows = []
    for i in range(n):
        ts = entry_ts + i * 60
        if i == 0:
            o, hi, lo, c = 100.0, 100.5, 99.5, 100.0
        elif i == 1:
            o, hi, lo, c = 100.0, 101.5, 99.8, 101.0
        elif i == 2:
            o, hi, lo, c = 101.0, 103.0, 100.8, 102.0
        else:
            o, hi, lo, c = 102.0, 102.2, 101.8, 102.0
        rows.append((ts, o, hi, lo, c))
    return rows


def path_stop_then_t1(entry_ts, n=40):
    """Стоп на 5-й свече (low 97.9), затем цена доходит до t1 = 104."""
    rows = []
    for i in range(n):
        ts = entry_ts + i * 60
        o = c = 100.0
        hi, lo = 100.05, 99.95
        if i == 5:
            o, hi, lo, c = 99.5, 99.6, 97.9, 98.1
        elif i == 30:
            o, hi, lo, c = 101.0, T1, 100.9, 103.8
        rows.append((ts, o, hi, lo, c))
    return rows


def trade_rec(**over):
    rows = over.pop("rows", None) or path_flat_rise(ENTRY)
    jid = over.pop("journal_id", JID)
    timeline = over.pop("timeline", [])
    rec = {"journal_id": jid, "plan_id": PLAN, "sym": "DOGEUSDT", "side": "long", "mode": over.pop("mode", "paper"),
           "entry_ts": rows[0][0], "entry_px": 100.0, "stop0": over.pop("stop0", 95.0), "tick": 0.01,
           "qty": over.pop("qty", 1.0), "qty_step": over.pop("qty_step", 0.1), "min_qty": over.pop("min_qty", 0.1),
           "horizon": "intraday", "max_hold_min": 60.0, "atr5_pct": 0.4, "sig_type": "oi_spike",
           "exit_ver": over.pop("exit_ver", "X-0001"), "policy_h": "abc123", "close_ts": rows[-1][0] + 60, "r": 0.0,
           "parts": "[]", "timeline": json.dumps(timeline), "rows": LP.encode(rows, 0.01, 100.0),
           "end_ts": rows[-1][0] + 60, "flags": over.pop("flags", None), "fetched_ts": NOW}
    rec.update(over)
    return rec


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        shutil.copy(os.path.join(H.PROJECT, "config", "exits.json"), os.path.join(self.root, "config", "exits.json"))
        self.conn = store.open_rw(self.root)
        self.addCleanup(self.conn.close)
        store2.migrate_x1(self.conn, self.root, backup=False, now=NOW)

    def add_trade(self, **kw):
        rec = trade_rec(**kw)
        LP.insert_trade_path(self.conn, rec)
        return rec

    def report(self, **kw):
        kw.setdefault("mode", "paper")
        kw.setdefault("now", NOW)
        return ER.run_brain(self.root, **kw)

    def spec(self, plan_id, obj):
        p = os.path.join(self.root, "state", "plans", plan_id, "spec.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)


class TestProperty22(Base):
    def test_no_intervention_gives_zero(self):
        self.add_trade(timeline=[])
        rep = self.report()
        self.assertEqual(rep["n"], 1)
        self.assertEqual(rep["rows"][0]["brain_delta"], 0.0)
        self.assertEqual(rep["rows"][0]["interventions"], [])
        self.assertEqual(rep["n_intervened"], 0)
        self.assertEqual(rep["verdict"], ER.VERDICT_LOW)

    def test_intervention_without_changed_parts_gives_zero(self):
        self.add_trade(timeline=[{"ts": ENTRY + 60, "kind": "move_stop", "stop": 99.0, "why": "brain"}])
        rep = self.report()
        row = rep["rows"][0]
        self.assertEqual(row["interventions"], ["move_stop"])
        self.assertEqual(row["brain_delta"], 0.0)
        self.assertFalse(row["close_qty_changed"])


class TestVerdicts(unittest.TestCase):
    def test_boundaries(self):
        cases = [(29, 0.1, 0.2, ER.VERDICT_LOW), (30, 0.0, 0.1, ER.VERDICT_FLAT),
                 (30, 0.0001, 0.5, ER.VERDICT_ADDS), (30, -0.5, -0.0001, ER.VERDICT_HARMS),
                 (30, -0.1, 0.1, ER.VERDICT_FLAT), (30, 0.0, 0.0, ER.VERDICT_FLAT)]
        for n, lo, hi, want in cases:
            self.assertEqual(ER.brain_verdict(n, lo, hi), want, (n, lo, hi))
        self.assertEqual(ER.brain_verdict(100, None, None), ER.VERDICT_LOW)


class TestReportFortyTrades(Base):
    def test_matches_manual_calculation(self):
        """40 одинаковых сделок: частичное закрытие 50% по 103, остаток по 102, стоп 95 (R = 5)."""
        for i in range(40):
            self.add_trade(journal_id="J-%03d" % i, rows=path_flat_rise(ENTRY + i * 3600),
                           timeline=[{"ts": ENTRY + i * 3600 + 120, "kind": "partial_close", "price": 103.0,
                                      "frac": 0.5, "why": "brain"}])
        rep = self.report()
        fee, slip, ru = 0.001, 0.0002, 5.0
        px_brain, px_end, entry_fee = 103.0 * (1 - slip), 102.0 * (1 - slip), 100.0 * fee
        r_with = 0.5 * (px_brain - 100.0) / ru + 0.5 * (px_end - 100.0) / ru - (
            entry_fee + fee * (0.5 * px_brain + 0.5 * px_end)) / ru
        r_without = (px_end - 100.0) / ru - (entry_fee + fee * px_end) / ru
        expected = round(r_with - r_without, 4)
        self.assertEqual(rep["n"], 40)
        self.assertEqual(rep["n_intervened"], 40)
        self.assertEqual(rep["share_intervened"], 1.0)
        self.assertAlmostEqual(rep["mean_delta"], expected, places=4)
        self.assertGreater(rep["ci_lo"], 0.0)
        self.assertEqual(rep["verdict"], ER.VERDICT_ADDS)
        self.assertEqual(rep["by_type"]["partial_close"]["n"], 40)
        self.assertEqual(rep["close_qty"]["n"], 0)
        self.assertEqual(rep["tokens"]["calls"], 0)


class TestSplits(Base):
    def test_mode_and_type_split(self):
        self.add_trade(journal_id="J-PAPER", timeline=[{"ts": ENTRY + 120, "kind": "partial_close", "price": 103.0,
                                                         "frac": 0.5}])
        self.add_trade(journal_id="J-LIVE", mode="live", timeline=[{"ts": ENTRY + 60, "kind": "move_stop", "stop": 99.0}])
        paper = self.report(mode="paper")
        live = self.report(mode="live")
        self.assertEqual(paper["n"], 1)
        self.assertEqual(paper["by_type"]["partial_close"]["n"], 1)
        self.assertNotIn("move_stop", paper["by_type"])
        self.assertEqual(live["n"], 1)
        self.assertEqual(live["by_type"]["move_stop"]["n"], 1)
        self.assertNotIn("partial_close", live["by_type"])

    def test_close_qty_share(self):
        self.add_trade(qty=0.3, qty_step=0.1, min_qty=0.1,
                       timeline=[{"ts": ENTRY + 120, "kind": "partial_close", "price": 103.0, "frac": 0.5}])
        rep = self.report()
        self.assertEqual(rep["close_qty"]["n"], 1)
        self.assertEqual(rep["close_qty"]["share"], 1.0)

    def test_wake_code_split(self):
        from tb.core import decisions as DEC
        self.add_trade(timeline=[{"ts": ENTRY + 120, "kind": "partial_close", "price": 103.0, "frac": 0.5}])
        DEC.append({"t": ENTRY + 120, "actor": "exec", "kind": "event", "what": "watch_wake", "plan": PLAN,
                    "args": {"code": "STALLED"}, "why": "WATCH:STALLED"}, root=self.root)
        rep = self.report()
        self.assertEqual(rep["by_wake"]["STALLED"]["n"], 1)
        self.assertEqual(rep["rows"][0]["wake_codes"], ["STALLED"])

    def test_tokens_from_llm_calls(self):
        self.add_trade()
        p = os.path.join(self.root, "state", "llm_calls.jsonl")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"t": ENTRY + 120, "role": "position_manager", "in": 100, "out": 20, "usd": 0.01}) + "\n")
            f.write(json.dumps({"t": ENTRY + 121, "role": "entry_scout", "in": 1000, "out": 100, "usd": 1.0}) + "\n")
        rep = self.report()
        self.assertEqual(rep["tokens"]["calls"], 1)
        self.assertEqual(rep["tokens"]["tokens"], 120)
        self.assertAlmostEqual(rep["tokens"]["usd"], 0.01, places=6)
        self.assertEqual(rep["tokens"]["tokens_per_trade"], 120)


class TestWriteAfter(Base):
    def setUp(self):
        super(TestWriteAfter, self).setUp()
        self.spec(PLAN, {"id": PLAN, "symbol": "DOGEUSDT", "side": "long", "session_type": "intraday",
                         "targets": [{"id": "t1", "price": T1, "frac": 1.0, "exec": "resting"}],
                         "lifecycle": {"ttl_min": 30, "max_hold_min": 60}})
        rows = path_stop_then_t1(ENTRY)
        self.add_trade(rows=rows, timeline=[], stop0=98.0,
                       parts=json.dumps([{"ts": STOP_TS, "px": 98.0, "qty": 3.0, "frac": 1.0, "fee": 0.02,
                                          "stop_type": "StopLoss"}]),
                       close_ts=STOP_TS)

    def add_case(self, jid, cid):
        rec = {"case_id": cid, "type": "trade", "ts": H.iso(ENTRY),
               "src": {"mode": "paper", "rules_ver": "R-0001", "role": "position_manager"},
               "situation": {"sym": "DOGEUSDT", "side": "long"}, "decision": {"action": "enter"},
               "refs": {"journal_id": jid, "plan": PLAN}, "result": {"status": "closed", "r": -1.0}}
        MC.append_new(rec, root=self.root)
        return cid

    def raw_rows(self):
        return len(MC.read_raw(self.root)[0])

    def test_writes_fields_and_keeps_rules_ver(self):
        cid = self.add_case(JID, MC.case_id_for(H.iso(ENTRY), "DOGEUSDT", JID))
        rep = ER.write_after(self.root, now=NOW)
        self.assertEqual((rep["ok"], rep["db"]), (True, "OK"))
        self.assertEqual((rep["scanned"], rep["updated"]), (1, 1))
        c = MC.get(cid, root=self.root)
        self.assertEqual(c["result"]["after_mfe_r"], 3.0)
        self.assertEqual(c["result"]["after_t1"], 1.0)
        self.assertEqual(c["src"]["rules_ver"], "R-0001")

    def test_idempotent(self):
        cid = self.add_case(JID, MC.case_id_for(H.iso(ENTRY), "DOGEUSDT", JID))
        ER.write_after(self.root, now=NOW)
        before = self.raw_rows()
        rep = ER.write_after(self.root, now=NOW)
        self.assertEqual(rep["updated"], 0)
        self.assertEqual(rep["unchanged"], 1)
        self.assertEqual(self.raw_rows(), before)
        self.assertEqual(MC.get(cid, root=self.root)["result"]["after_t1"], 1.0)

    def test_no_write_without_ready_path(self):
        cid = self.add_case("NO-PATH", "C-20261102-DOGEUSDT-abcdef")
        rep = ER.write_after(self.root, now=NOW)
        self.assertGreaterEqual(rep["no_case"], 1)
        c = MC.get(cid, root=self.root)
        self.assertNotIn("after_mfe_r", c["result"])
        self.assertNotIn("after_t1", c["result"])


if __name__ == "__main__":
    unittest.main()
