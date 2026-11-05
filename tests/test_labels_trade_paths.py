"""tb.labels.paths.trade_paths_step (R4, задача 9): ожидание окна после закрытия, курсор position_closed, повтор после сбоя REST,
флаг adopted, хронология вмешательств и поля лота инструмента. Сеть не используется: поддельный клиент свечей."""
import json
import os
import shutil
import unittest

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.labels import klines, paths, store

PLAN = "PL-2611-DOGE-01"
JID = "BB-P-DOGEUSDT-1102-120037"
ENTRY = H.T0 + 600
CLOSE = ENTRY + 90 * 60
POST_CLOSE_MIN = 240
GRACE_S = 600
OPEN_AT = CLOSE + POST_CLOSE_MIN * 60 + GRACE_S
RETRY_S = 24 * 3600


def iso(t):
    return H.iso(t)


def write_json(root, rel, obj):
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def append_jsonl(root, rel, rows):
    p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        shutil.copy(os.path.join(H.PROJECT, "config", "exits.json"), os.path.join(self.root, "config", "exits.json"))
        self.conn = store.open_rw(self.root)
        self.addCleanup(self.conn.close)
        self.mk = H.Market(rows=H.walk(ENTRY - 3600, CLOSE + 300 * 60))
        self.write_fixtures()

    def write_fixtures(self, decisions=None, flags=None):
        append_jsonl(self.root, "state/trades.jsonl", [
            {"ev": "open", "id": JID, "ts_open": iso(ENTRY), "sym": "DOGEUSDT", "side": "long", "entry": 100.0, "lev": 3,
             "margin": 100.0, "notional": 300.0, "sl": 98.0, "tp": 104.0, "mode": "paper", "plan_id": PLAN, "tags": ["plan:%s" % PLAN]},
            {"ev": "adjust", "id": JID, "ts": iso(ENTRY + 1800), "sl": 100.5, "tp": None, "by": "cmd"},
            {"ev": "part", "id": JID, "ts": iso(ENTRY + 2400), "px": 101.0, "qty": 1.0, "fee": 0.01, "pnl": 1.0},
            {"ev": "close", "id": JID, "ts_close": iso(CLOSE), "exit": 102.0, "reason": "manual", "pnl_usdt": 5.0,
             "fees_usdt": 0.05, "r": 1.2},
        ])
        append_jsonl(self.root, "state/decisions.jsonl", decisions if decisions is not None else [
            {"seq": 1, "ts": iso(ENTRY + 900), "t": ENTRY + 900, "actor": "brain:chat", "kind": "cmd", "what": "stop", "plan": PLAN,
             "why": "CMD:c1 move stop", "args": {"to": 100.5}},
            {"seq": 2, "ts": iso(ENTRY + 1500), "t": ENTRY + 1500, "actor": "exec", "kind": "rule", "what": "move_stop", "plan": PLAN,
             "why": "RULE:xp_lock", "args": {"to": 100.4}, "res": {"ok": True, "code": "OK"}},
        ])
        write_json(self.root, "state/plans/%s/spec.json" % PLAN, {
            "id": PLAN, "symbol": "DOGEUSDT", "side": "long", "session_type": "intraday",
            "lifecycle": {"ttl_min": 30, "max_hold_min": 60}, "source": {"kind": "oi_spike"},
        })
        write_json(self.root, "state/plans/%s/runtime.json" % PLAN, {
            "pos": {"exit": {"ver": "X-0002", "policy_h": "abc123", "horizon": "intraday", "max_hold_min": 60, "atr5_pct": 0.4,
                             "sig_type": "oi_spike"}},
            "realized": {"parts": [{"ts": ENTRY + 2400, "px": 101.0, "qty": 1.0, "fee": 0.01, "pnl": 1.0, "stop_type": "tp"}]},
        })
        write_json(self.root, "runtime/instruments.json", {"ts_epoch": ENTRY, "symbols": {
            "DOGEUSDT": {"tick": "0.01", "qty_step": "0.1", "min_qty": "0.1", "min_notional": "5", "max_leverage": 50}}})
        d = {"reason": "manual", "pnl_usdt": 5.0, "fees": 0.05, "r": 1.2, "journal_id": JID, "mae_r": -0.1, "mfe_r": 1.4}
        ev = {"seq": 7, "ts": iso(CLOSE + 1), "type": "position_closed", "pri": "wake", "plan": PLAN, "sym": "DOGEUSDT", "d": d}
        if flags:
            ev.update(flags)
        append_jsonl(self.root, "state/bus/events.jsonl", [ev])

    def cfg(self):
        return paths.load_cfg(self.root)

    def step(self, now, fetch=None):
        return paths.trade_paths_step(self.conn, self.root, self.cfg(), fetch or self.mk.fetch, now)

    def row(self):
        cols = paths.TRADE_PATH_COLS
        r = self.conn.execute("SELECT %s FROM trade_paths" % ",".join(cols)).fetchone()
        return dict(zip(cols, r)) if r else None

    def cursor(self):
        r = self.conn.execute("SELECT offset FROM cursor WHERE src=?", (paths.schema.CURSOR_BUS_CLOSED,)).fetchone()
        return int(r[0]) if r else None


class TestTradePaths(Base):
    def test_waits_for_window_after_close(self):
        rep = self.step(OPEN_AT - 1)
        self.assertEqual(rep["waiting"], 1)
        self.assertEqual(rep["written"], 0)
        self.assertIsNone(self.row())
        self.assertEqual(self.cursor(), 0)

    def test_writes_path_and_fields(self):
        rep = self.step(OPEN_AT + 1)
        self.assertEqual(rep["written"], 1)
        r = self.row()
        self.assertEqual((r["journal_id"], r["plan_id"], r["sym"], r["side"], r["mode"]), (JID, PLAN, "DOGEUSDT", "long", "paper"))
        self.assertEqual((r["entry_ts"], r["entry_px"], r["stop0"], r["tick"]), (ENTRY, 100.0, 98.0, 0.01))
        self.assertEqual((r["qty"], r["qty_step"], r["min_qty"]), (3.0, 0.1, 0.1))
        self.assertEqual((r["horizon"], r["max_hold_min"], r["atr5_pct"], r["sig_type"]), ("intraday", 60.0, 0.4, "oi_spike"))
        self.assertEqual((r["exit_ver"], r["policy_h"]), ("X-0002", "abc123"))
        self.assertEqual((r["close_ts"], r["r"]), (CLOSE, 1.2))
        self.assertIsNone(r["flags"])
        parts = json.loads(r["parts"])
        self.assertEqual(len(parts), 1)
        self.assertEqual((parts[0]["ts"], parts[0]["px"], parts[0]["qty"], parts[0]["stop_type"]), (ENTRY + 2400, 101.0, 1.0, "tp"))
        self.assertAlmostEqual(parts[0]["frac"], 1.0 / 3.0, places=7)
        rows = paths.decode(r["rows"], r["tick"], r["entry_px"])
        self.assertGreaterEqual(rows[0][0], ENTRY - 1800)
        self.assertLess(rows[0][0], ENTRY - 1800 + 60)
        self.assertEqual(r["end_ts"], rows[-1][0] + 60)
        self.assertEqual(self.cursor(), 7)

    def test_timeline_is_exitsim_format(self):
        self.step(OPEN_AT + 1)
        tl = json.loads(self.row()["timeline"])
        self.assertEqual(tl, [{"ts": ENTRY + 1800, "kind": "move_stop", "stop": 100.5, "why": "brain"}])

    def test_timeline_partial_close(self):
        append_jsonl(self.root, "state/decisions.jsonl", [
            {"seq": 4, "ts": iso(ENTRY + 2400), "t": ENTRY + 2400, "actor": "exec", "kind": "rule", "what": "close", "plan": PLAN,
             "why": "CMD:c9 partial", "args": {"qty": 1.0}, "res": {"ok": True, "code": "OK"}}])
        self.step(OPEN_AT + 1)
        tl = json.loads(self.row()["timeline"])
        self.assertEqual(tl[0]["kind"], "move_stop")
        self.assertEqual(tl[1]["kind"], "partial_close")
        self.assertEqual((tl[1]["ts"], tl[1]["price"], tl[1]["why"]), (ENTRY + 2400, 101.0, "CMD:c9 partial"))
        self.assertAlmostEqual(tl[1]["frac"], 1.0 / 3.0, places=7)

    def test_timeline_full_close(self):
        append_jsonl(self.root, "state/decisions.jsonl", [
            {"seq": 5, "ts": iso(CLOSE), "t": CLOSE, "actor": "exec", "kind": "rule", "what": "close", "plan": PLAN,
             "why": "CMD:c10 close", "args": {"qty": 2.0}, "res": {"ok": True, "code": "OK"}}])
        self.step(OPEN_AT + 1)
        tl = json.loads(self.row()["timeline"])
        self.assertEqual(tl[-1], {"ts": CLOSE, "kind": "close", "price": 102.0, "frac": 1.0, "why": "CMD:c10 close"})

    def test_adopted_flag(self):
        self.conn.execute("DELETE FROM trade_paths")
        append_jsonl(self.root, "state/decisions.jsonl", [
            {"seq": 3, "ts": iso(ENTRY + 60), "t": ENTRY + 60, "actor": "owner", "kind": "cmd", "what": "adopt", "plan": PLAN,
             "why": "CMD:c2 adopt"}])
        self.assertEqual(self.step(OPEN_AT + 1)["written"], 1)
        self.assertIn("adopted", (self.row()["flags"] or "").split(","))

    def test_second_pass_is_idempotent(self):
        self.step(OPEN_AT + 1)
        rep = self.step(OPEN_AT + 120)
        self.assertEqual((rep["written"], rep["errors"]), (0, 0))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM trade_paths").fetchone()[0], 1)

    def test_rest_failure_waits_then_retries(self):
        def bad(*a, **k):
            raise klines.FetchError("network")
        rep = self.step(OPEN_AT + 60, fetch=bad)
        self.assertEqual(rep["errors"], 1)
        self.assertIsNone(self.row())
        self.assertEqual(self.cursor(), 0)
        self.assertEqual(self.step(OPEN_AT + 120)["written"], 1)

    def test_missing_after_retry_window(self):
        def bad(*a, **k):
            raise klines.FetchError("network")
        rep = self.step(OPEN_AT + RETRY_S + 1, fetch=bad)
        self.assertEqual(rep["missing"], 1)
        self.assertIsNone(self.row())
        self.assertEqual(self.cursor(), 7)
        self.assertEqual(self.conn.execute("SELECT v FROM meta WHERE k='trade_path_missing'").fetchone()[0], "1")

    def test_missing_instrument_waits_and_does_not_crash(self):
        os.remove(os.path.join(self.root, "runtime", "instruments.json"))
        rep = self.step(OPEN_AT + 60)
        self.assertEqual(rep["errors"], 1)
        self.assertIsNone(self.row())

    def test_event_without_journal_id_is_skipped(self):
        append_jsonl(self.root, "state/bus/events.jsonl", [
            {"seq": 8, "ts": iso(CLOSE + 2), "type": "position_closed", "pri": "wake", "d": {"reason": "sl"}}])
        self.step(OPEN_AT + 1)
        self.assertEqual(self.cursor(), 8)

    def test_gap_flag(self):
        self.mk.drop(ENTRY + 20 * 60, ENTRY + 25 * 60)
        self.step(OPEN_AT + 1)
        self.assertIn("gap", (self.row()["flags"] or "").split(","))


if __name__ == "__main__":
    unittest.main()
