"""Блок «Выходы» страницы «Учится ли мозг» (R4, задача 22; Requirement 17.1–17.6) и команда кнопки «Вернуть границу».

Проверяется: пустое состояние «появится после первого продвижения»; вердикты блока (данных мало / разницы не видно / улучшение
подтверждено) по нижним границам 95% интервалов `d` (пути) и `d_b` (сделки мозга); `cap = Σ R / Σ MFE` фактом и у `X-0001`;
доли издержек, испытания и сторож; гейт этапа 4 одной формулой Requirement 17.3 (версия, N ≥ 100, CAP, R ≥ −0.02 односторонним
90% интервалом); режимы paper/testnet/live раздельно; база открывается только на чтение; аварийное действие `exits_rollback`
идёт через единое подтверждение рисков и вызывает `exits_cli.owner_rollback` (запись `actor=OWNER`).
Временные корни, сеть и платформа не используются."""
import contextlib
import io
import json
import os
import shutil
import sqlite3
import unittest

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.dash2 import memory_ops as MO
from tb.knowledge import exits_cli as XCLI
from tb.knowledge import store2
from tb.labels import store
from tb.learn import conf as LC
from tb.learn import exits as EX
from tb.learnloop import exits_conf as EC
from tb.learnloop import exits_eval as EV
from tb.learnloop import exits_report as ER
from tb.learnloop import exits_versions as XV
from tb.logic import exitpol as XP
from tb.logic import exitsim as XS

NOW = H.BASE + 12 * 3600
DAY = 86400
PROMO = NOW - 2 * DAY
ENTRY = PROMO + 3600
VERS = "X-0002"
PH = "a" * 16
EVAL_COLS = ("src", "key", "var_id", "var_ver", "ord", "px_mode", "policy_h", "cost_h", "sim_ver", "t0", "block", "labeled_ts",
             "r_net", "r_gross", "fees_r", "mfe_r", "mae_r", "path_mfe_r", "exit_reason", "hold_min", "end_ts", "amb", "created_ts")
TRADE_SQL = ("INSERT INTO trade_paths(journal_id, plan_id, sym, side, mode, entry_ts, entry_px, stop0, tick, qty, qty_step, min_qty,"
             " horizon, max_hold_min, atr5_pct, sig_type, exit_ver, policy_h, close_ts, r, parts, timeline, rows, end_ts, flags, fetched_ts)"
             " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


def ev(src, key, ver, num, t0, block, r, mfe=2.0, fees=0.02, gross=1.0):
    return {"src": src, "key": key, "var_id": ver, "var_ver": int(num), "ord": "S", "px_mode": "fee", "policy_h": PH,
            "cost_h": "", "sim_ver": XS.SIM_VER, "t0": int(t0), "block": block, "labeled_ts": int(t0), "r_net": float(r),
            "r_gross": float(gross), "fees_r": float(fees), "mfe_r": float(mfe), "mae_r": 0.1, "path_mfe_r": float(mfe),
            "exit_reason": "tp", "hold_min": 10.0, "end_ts": int(t0) + 600, "amb": 0, "created_ts": int(NOW)}


def wave(i):
    return 0.1 if i % 2 else -0.1


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        for name in ("exits.json", "learnloop.json"):
            shutil.copy(os.path.join(H.PROJECT, "config", name), os.path.join(self.root, "config", name))
        self.conn = store.open_rw(self.root)
        self.addCleanup(self.conn.close)
        self.assertEqual(store2.migrate_x1(self.conn, self.root, backup=False, now=NOW), 1)
        store2.migrate_v2a(self.conn, self.root, enabled=True, backup=False, now=NOW)
        self.cfg = EC.load_strict(self.root)
        self.floor = EC.current_floor(self.root)
        self.cost_h = EV.market_inputs(self.root)["cost_h"]
        self.ph_base = XP.policy_hash(self.floor)
        self.assertEqual(XV.init(self.conn, self.floor, PROMO), "X-0001")

    def promote(self, ts=PROMO, ver=VERS, parent="X-0001"):
        with store.tx(self.conn):
            store2.append_row(self.conn, "exit_versions", {
                "ver": ver, "parent": parent, "reason_code": "PROMOTE", "policy": self.floor,
                "policy_h": XP.policy_hash(self.floor), "floor_h": self.ph_base, "numbers": None, "actor": "code", "ts": int(ts)})
        return ver

    def rows(self, rows):
        rows = [dict(r, cost_h=self.cost_h) for r in rows]
        self.conn.executemany("INSERT INTO exit_evals(%s) VALUES(%s)" % (", ".join(EVAL_COLS), ",".join("?" * len(EVAL_COLS))),
                              [[r[c] for c in EVAL_COLS] for r in rows])
        self.conn.commit()

    def trade(self, jid, ts, r, mode="paper"):
        self.conn.execute(TRADE_SQL, (jid, None, "DOGEUSDT", "long", mode, int(ts), 100.0, 98.0, 0.01, 1.0, 0.1, 0.1, "intraday",
                                      60.0, 0.4, "oi", VERS, PH, int(ts) + 600, float(r), "[]", "[]", b"", int(ts) + 600, None, int(NOW)))

    def pairs(self, n=40, d=0.2, mfe=2.0, src="trade", mode="paper", base=1.0, prefix="J"):
        step = d if callable(d) else (lambda _i: d)
        out = []
        for i in range(n):
            key = ("%s%04d" % (prefix, i)) if src == "trade" else ("k%04d:P2:1" % i)
            t0 = ENTRY + i * 60
            delta = step(i)
            out.append(ev(src, key, "X-0001", 1, t0, "b%d" % i, base, mfe=mfe))
            out.append(ev(src, key, VERS, 2, t0, "b%d" % i, base + delta, mfe=mfe))
            if src == "trade":
                self.trade(key, t0, base + delta, mode=mode)
        self.rows(out)
        return out

    def block(self, **kw):
        kw.setdefault("mode", "paper")
        kw.setdefault("now", NOW)
        return ER.run_block(self.root, **kw)

    def gate(self, **kw):
        kw.setdefault("mode", "paper")
        kw.setdefault("now", NOW)
        return ER.run_gate(self.root, **kw)


# ---------------------------------------------------------------- пустое состояние и вердикты блока

class TestPageEmpty(Base):
    def test_no_version(self):
        root = H.mk_root(self)
        conn = store.open_rw(root)
        store2.migrate_x1(conn, root, backup=False, now=NOW)
        conn.close()
        rep = ER.run_block(root, mode="paper", now=NOW)
        self.assertEqual((rep["ok"], rep["status"], rep["reason"]), (True, "no_data", None))
        self.assertEqual(rep["note"], "Появится после первого продвижения.")
        self.assertIsNone(rep["version"])
        self.assertEqual(rep["verdict"]["state"], "few_data")

    def test_floor_only_is_empty_state(self):
        rep = self.block()
        self.assertEqual(rep["status"], "no_data")
        self.assertEqual(rep["version"]["ver"], "X-0001")
        self.assertIn("первого продвижения", rep["note"])

    def test_no_db(self):
        rep = ER.run_block(H.mk_root(self), mode="paper", now=NOW)
        self.assertEqual((rep["ok"], rep["db"]), (False, ER.NO_DB))
        self.assertIn("первого продвижения", rep["note"])
        self.assertEqual(rep["status"], "no_data")

    def test_learn_block_section(self):
        cfg, _ = LC.load(None)
        plain = EX.build({"cfg": cfg, "trades": []})
        self.assertEqual(plain["r4"]["status"], "no_data")
        self.assertIn("первого продвижения", plain["r4"]["note"])
        self.promote()
        with_db = EX.build({"cfg": cfg, "trades": [], "root": self.root, "mode": "paper", "now": NOW, "exits_loop": True})
        self.assertEqual(with_db["r4"]["version"]["ver"], VERS)
        self.assertNotIn("ok", with_db["r4"])
        self.assertNotIn("db", with_db["r4"])


class TestPageVerdict(Base):
    def setUp(self):
        Base.setUp(self)
        self.promote()

    def test_confirmed(self):
        self.pairs(40, d=0.2, src="run")
        self.pairs(40, d=0.2)
        rep = self.block()
        self.assertEqual((rep["status"], rep["verdict"]["state"]), ("ok", "confirmed"))
        self.assertEqual(rep["d"]["n_blocks"], 40)
        self.assertEqual(rep["d_b"]["n"], 40)
        self.assertGreater(rep["d"]["lo"], 0)
        self.assertGreater(rep["d_b"]["lo"], 0)
        self.assertEqual(rep["d"]["mean"], 0.2)
        self.assertEqual(rep["cap"]["fact"], 0.6)
        self.assertEqual(rep["cap"]["x0001"], 0.5)
        self.assertEqual(rep["cap"]["n"], 40)
        self.assertIsNotNone(rep["costs"]["cur"])

    def test_no_difference(self):
        self.pairs(40, d=wave, src="run")
        self.pairs(40, d=wave)
        rep = self.block()
        self.assertEqual((rep["status"], rep["verdict"]["state"]), ("ok", "no_difference"))
        self.assertLessEqual(rep["d_b"]["lo"], 0)
        self.assertGreaterEqual(rep["d_b"]["hi"], 0)
        self.assertLessEqual(rep["d"]["lo"], 0)

    def test_few_data(self):
        self.pairs(10, d=0.2, src="run")
        self.pairs(10, d=0.2)
        rep = self.block()
        self.assertEqual((rep["status"], rep["reason"], rep["verdict"]["state"]), ("few_data", "n_lt_min", "few_data"))
        self.assertIn("30", rep["verdict"]["text"])

    def test_modes_do_not_mix(self):
        self.pairs(40, d=0.2, src="run")
        self.pairs(40, d=0.2)
        self.pairs(3, d=0.2, mode="live", prefix="L")
        paper = self.block(mode="paper")
        live = self.block(mode="live")
        self.assertEqual(paper["d_b"]["n"], 40)
        self.assertEqual(live["d_b"]["n"], 3)
        self.assertIsNone(live["d"])
        self.assertIsNotNone(paper["d"])
        self.assertEqual(live["verdict"]["state"], "few_data")
        self.assertEqual(live["status"], "few_data")
        self.assertTrue(any("paper" in n for n in live["notes"]))

    def test_reads_only(self):
        self.pairs(40, d=0.2, src="run")
        self.pairs(40, d=0.2)
        before = tuple(self.conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in ("exit_versions", "exit_evals", "trade_paths"))
        conn = store.open_ro(self.root)
        try:
            rep = ER.page_block(conn, mode="paper", now=NOW, root=self.root)
            self.assertEqual(rep["verdict"]["state"], "confirmed")
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO meta(k, v) VALUES('probe', '1')")
        finally:
            conn.close()
        after = tuple(self.conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in ("exit_versions", "exit_evals", "trade_paths"))
        self.assertEqual(after, before)

    def test_trials_and_watch(self):
        self.conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('exits_watch', ?)",
                          (json.dumps({"ts": NOW - 3600, "ver": VERS, "prev": "X-0001", "codes": [], "state": {"points": [{"look": "L2"}]}}),))
        self.conn.commit()
        rep = self.block()
        self.assertEqual(rep["watch"]["ver"], VERS)
        self.assertEqual(rep["watch"]["points"], 1)
        self.assertEqual(rep["trials"], {"open": 0, "closed": 0, "rows": []})


# ---------------------------------------------------------------- гейт этапа 4 (Requirement 17.3)

class TestGate(Base):
    def test_passed(self):
        self.promote()
        self.pairs(120, d=0.4, mfe=2.0)
        rep = self.gate()
        self.assertEqual(rep["version"]["ver"], VERS)
        self.assertEqual(rep["n"], 100)
        self.assertEqual(rep["conds"], {"VERSION": True, "N": True, "CAP": True, "R": True})
        self.assertTrue(rep["passed"])
        self.assertEqual(rep["why"], [])
        self.assertEqual(rep["cap_fact"], 0.7)
        self.assertEqual(rep["cap_x0001"], 0.5)
        self.assertAlmostEqual(rep["lower90"], 0.4, places=4)
        self.assertGreaterEqual(rep["lower90"], -0.02)

    def test_cap_must_be_strictly_better(self):
        self.promote()
        self.pairs(120, d=0.0, mfe=2.0)
        rep = self.gate()
        self.assertEqual(rep["cap_fact"], rep["cap_x0001"])
        self.assertFalse(rep["conds"]["CAP"])
        self.assertTrue(rep["conds"]["R"])
        self.assertFalse(rep["passed"])
        self.assertEqual(rep["why"], ["CAP"])

    def test_r_not_worse(self):
        self.promote()
        for i in range(100):
            key = "J%04d" % i
            t0 = ENTRY + i * 60
            mfe = 1.0 if i < 20 else 0.0
            delta = 0.2 if i < 20 else -0.2
            self.rows([ev("trade", key, "X-0001", 1, t0, "b%d" % (i // 10), 1.0, mfe=mfe),
                       ev("trade", key, VERS, 2, t0, "b%d" % (i // 10), 1.0 + delta, mfe=mfe)])
            self.trade(key, t0, 1.0 + delta)
        self.conn.commit()
        rep = self.gate()
        self.assertTrue(rep["conds"]["CAP"])
        self.assertFalse(rep["conds"]["R"])
        self.assertLess(rep["lower90"], -0.02)
        self.assertEqual(rep["why"], ["R"])

    def test_needs_hundred_trades(self):
        self.promote()
        self.pairs(50, d=0.4, mfe=2.0)
        rep = self.gate()
        self.assertEqual(rep["n"], 50)
        self.assertFalse(rep["conds"]["N"])
        self.assertFalse(rep["passed"])
        self.assertEqual(rep["why"], ["N"])

    def test_floor_version_blocks(self):
        rep = self.gate()
        self.assertFalse(rep["conds"]["VERSION"])
        self.assertFalse(rep["passed"])
        self.assertEqual(rep["why"], ["VERSION"])
        self.assertIn("пол защиты", " ".join(rep["notes"]))

    def test_modes_are_separate(self):
        self.promote()
        self.pairs(120, d=0.4, mfe=2.0)
        self.pairs(3, d=0.1, mfe=2.0, mode="live", prefix="L")
        paper = self.gate(mode="paper")
        live = self.gate(mode="live")
        self.assertTrue(paper["passed"])
        self.assertEqual(live["n"], 3)
        self.assertFalse(live["conds"]["N"])
        self.assertNotEqual(live["cap_fact"], paper["cap_fact"])

    def test_no_db(self):
        rep = ER.run_gate(H.mk_root(self), now=NOW)
        self.assertEqual((rep["ok"], rep["db"]), (False, ER.NO_DB))
        self.assertFalse(rep["passed"])

    def test_render_text(self):
        self.promote()
        self.pairs(120, d=0.4, mfe=2.0)
        text = "\n".join(ER.render_gate(self.gate()))
        for word in ("VERSION", "CAP", "R:", "N:", "итог: гейт пройден"):
            self.assertIn(word, text)

    def test_cli_gate(self):
        self.promote()
        self.pairs(120, d=0.4, mfe=2.0)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(XCLI.main(["gate"], root=self.root), 0)
        self.assertTrue(json.loads(buf.getvalue())["passed"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(XCLI.main(["gate", "--text", "--mode", "paper"], root=self.root), 0)
        self.assertIn("итог: гейт пройден", buf.getvalue())
        self.assertEqual(XCLI.main(["gate", "--mode", "nope"], root=self.root), 2)


# ---------------------------------------------------------------- кнопка «Выходы: вернуть границу» (Requirement 16.6)

class WS(object):
    pass


class TestRollbackAction(Base):
    def setUp(self):
        Base.setUp(self)
        self.promote()
        self.acts = MO.MemoryActions(self.root, clock=lambda: NOW)
        self.ws = WS()
        self.hello = self.acts.open(self.ws)

    def act(self, **kw):
        m = dict({"op": "exits_rollback", "id": "m1", "csrf": self.hello["csrf"]}, **kw)
        return self.acts.handle(self.ws, m, now=NOW)

    def owner(self):
        return [tuple(r) for r in self.conn.execute("SELECT step, code, actor FROM learn_events WHERE actor='OWNER' ORDER BY seq").fetchall()]

    def test_action_is_registered_with_risk_dialog(self):
        self.assertIn("exits_rollback", self.hello["actions"])
        self.assertEqual(sorted(self.hello["confirm"]["exits_rollback"]), ["exits_floor", "exits_open_positions"])
        ch = self.act(kind="challenge")
        self.assertEqual((ch["ok"], ch["code"], ch["needs_confirm"]), (True, "CONFIRM", True))
        self.assertEqual(len(ch["warnings"]), 2)
        self.assertEqual(self.owner(), [])

    def test_requires_all_warnings(self):
        r = self.act()
        self.assertEqual((r["ok"], r["code"]), (False, "E_CONFIRM"))
        r = self.act(accepted=["exits_floor"])
        self.assertEqual(r["code"], "E_CONFIRM")
        self.assertEqual(XV.last_version(self.conn)["ver"], VERS)

    def test_rollback_writes_owner_version(self):
        r = self.act(accepted=self.hello["confirm"]["exits_rollback"])
        self.assertEqual((r["ok"], r["code"]), (True, "OK"))
        last = XV.last_version(self.conn)
        self.assertEqual(last["ver"], "X-0003")
        self.assertEqual(last["reason_code"], "OWNER_ROLLBACK")
        self.assertEqual(last["policy_h"], self.ph_base)
        self.assertEqual(self.owner(), [("exits", "OWNER_ROLLBACK", "OWNER")])
        self.assertIsNotNone(r["report"])

    def test_db_unavailable(self):
        self.conn.close()
        for name in ("knowledge.sqlite", "knowledge.sqlite-wal", "knowledge.sqlite-shm"):
            path = os.path.join(self.root, "state", name)
            if os.path.exists(path):
                os.remove(path)
        r = self.act(accepted=self.hello["confirm"]["exits_rollback"])
        self.assertEqual((r["ok"], r["code"]), (False, "E_DB"))


if __name__ == "__main__":
    unittest.main()
