"""R4 задача 7: схема меток v2 (run_paths, trade_paths, курсор bus_closed) и миграция цикла выходов x1 (Requirements 7.2, 6.1, 18.6, 1.1, 1.2, 1.6).

Миграция с фикстуры v1 без потери строк и идемпотентность; x1 с проверенной копией premigrate; триггер неизменности критерия exit_trials;
хеш-цепочка exit_versions (append_row, verify, check_copy, make_backup). Только временные корни.
"""
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
from tb.knowledge import schema2 as S2
from tb.knowledge import store2
from tb.labels import schema as LS
from tb.labels import store

NOW = 1793750400          # 2026-11-04T00:00:00Z
RUN_COLS = ["key", "policy_id", "policy_ver", "t0", "sym", "type", "side", "horizon", "entry_ts", "entry_px", "stop", "tick", "max_hold_min",
            "atr5_pct", "plan", "rows", "n_rows", "end_ts", "state", "flags", "aux", "aux_ver", "labeled_ts", "complete_ts"]
TRADE_COLS = ["journal_id", "plan_id", "sym", "side", "mode", "entry_ts", "entry_px", "stop0", "tick", "qty", "qty_step", "min_qty", "horizon",
              "max_hold_min", "atr5_pct", "sig_type", "exit_ver", "policy_h", "close_ts", "r", "parts", "timeline", "rows", "end_ts", "flags",
              "fetched_ts"]


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tb_exits_schema_")
        self.addCleanup(shutil.rmtree, self.root, True)
        store.LAST_ERROR = None
        store2._STATUS_CACHE.clear()
        self.conns = []

    def tearDown(self):
        for c in self.conns:
            try:
                c.close()
            except sqlite3.Error:
                pass

    def rw(self):
        c = store.open_rw(self.root)
        self.conns.append(c)
        return c

    def names(self, c, kind):
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))}

    def cols(self, c, t):
        return [r[1] for r in c.execute("PRAGMA table_info(%s)" % t)]

    def v1_fixture(self):
        """База меток версии 1 (как до R4) со строками signals / outcomes / policy_runs / cursor."""
        path = store.db_path(self.root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        c = sqlite3.connect(path, isolation_level=None)
        self.conns.append(c)
        self.assertEqual(store.migrate(c, [(1, LS.V1)]), 1)
        with store.tx(c):
            for i in range(5):
                c.execute("INSERT INTO signals(key, t0, sym, type, side, ev, status, mature_ts, next_try_ts, captured_ts, schema_ver) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,1)", ("k%d" % i, NOW + i, "BTCUSDT", "oi_spike", "long", "{}", "done", NOW, NOW, NOW))
                c.execute("INSERT INTO outcomes(key, h, side, ret_pct) VALUES(?,?,?,?)", ("k%d" % i, 60, "long", 0.1 * i))
                c.execute("INSERT INTO policy_runs(key, policy_id, policy_ver, geometry_ver, ctx_ver, t0, sym, type, filled, r_net) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?)", ("k%d" % i, "P2", 1, "g1", "c1", NOW + i, "BTCUSDT", "oi_spike", 1, 0.5))
            c.execute("INSERT INTO cursor(src, path, offset, head_sha, ts) VALUES('signals', 'x', 10, 'abc', 1)")
        return c

    def counts(self, c):
        return dict((t, c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]) for t in ("signals", "outcomes", "policy_runs", "cursor"))

    def x1(self):
        c = self.rw()
        self.assertEqual(store2.migrate_x1(c, self.root, now=NOW), 1)
        return c

    def version(self, ver, parent=None, reason="INIT", **kw):
        d = {"ver": ver, "parent": parent, "reason_code": reason, "policy": {"schema": "tb.exit_policy/1", "be": {"r": 0.7}},
             "policy_h": "a" * 16, "floor_h": "b" * 16, "numbers": {"n": 0}, "actor": "code", "ts": NOW}
        d.update(kw)
        return d


# ---------------------------------------------------------------- метки v2

class TestLabelsV2(Base):
    def test_v1_migrates_without_loss_and_idempotent(self):
        c = self.v1_fixture()
        before = self.counts(c)
        rows = c.execute("SELECT * FROM policy_runs ORDER BY key").fetchall()
        self.assertEqual(store.migrate(c), LS.SCHEMA_VER)
        self.assertEqual(LS.SCHEMA_VER, 2)
        self.assertEqual(self.counts(c), before)
        self.assertEqual(c.execute("SELECT * FROM policy_runs ORDER BY key").fetchall(), rows)
        self.assertEqual(c.execute("SELECT v FROM meta WHERE k=?", (LS.META_VER_KEY,)).fetchone()[0], "2")
        self.assertTrue({"run_paths", "trade_paths"} <= self.names(c, "table"))
        self.assertTrue(set(LS.V2_INDEXES) <= self.names(c, "index"))
        self.assertEqual(self.cols(c, "run_paths"), RUN_COLS)
        self.assertEqual(self.cols(c, "trade_paths"), TRADE_COLS)
        self.assertEqual(store.migrate(c), 2)                                    # повтор ничего не делает
        self.assertEqual(self.counts(c), before)

    def test_open_rw_new_db_is_v2(self):
        c = self.rw()
        self.assertEqual(c.execute("SELECT v FROM meta WHERE k=?", (LS.META_VER_KEY,)).fetchone()[0], str(LS.SCHEMA_VER))
        self.assertTrue({"run_paths", "trade_paths"} <= self.names(c, "table"))

    def test_open_rw_migrates_v1_file(self):
        c = self.v1_fixture()
        before = self.counts(c)
        c.close()
        self.conns.remove(c)
        c2 = self.rw()
        self.assertEqual(self.counts(c2), before)
        self.assertIn("run_paths", self.names(c2, "table"))

    def test_failed_v2_keeps_v1(self):
        c = self.v1_fixture()
        bad = [(1, LS.V1), (2, LS.V2[:2] + ["CREATE TABLE broken("])]
        with self.assertRaises(store.SchemaMigrationError):
            store.migrate(c, bad)
        self.assertNotIn("run_paths", self.names(c, "table"))
        self.assertEqual(c.execute("SELECT v FROM meta WHERE k=?", (LS.META_VER_KEY,)).fetchone()[0], "1")
        self.assertEqual(self.counts(c)["policy_runs"], 5)

    def test_run_path_rows_and_cursor(self):
        c = self.rw()
        row = {"key": "k1", "policy_id": "P2", "policy_ver": 1, "t0": NOW, "sym": "BTCUSDT", "type": "oi_spike", "side": "long",
               "horizon": "intraday", "entry_ts": NOW + 60, "entry_px": 100.0, "stop": 99.0, "tick": 0.1, "max_hold_min": 240.0,
               "atr5_pct": 0.4, "plan": "{}", "rows": b"\x00\x01", "n_rows": 2, "end_ts": NOW + 60 + 390 * 60, "state": LS.PATH_PARTIAL,
               "labeled_ts": NOW + 3600}
        ks = sorted(row)
        with store.tx(c):
            c.execute("INSERT INTO run_paths(%s) VALUES(%s)" % (",".join(ks), ",".join("?" * len(ks))), [row[k] for k in ks])
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO run_paths(%s) VALUES(%s)" % (",".join(ks), ",".join("?" * len(ks))), [row[k] for k in ks])
            c.execute("UPDATE run_paths SET state=?, complete_ts=? WHERE key='k1'", (LS.PATH_COMPLETE, NOW + 90000))
            c.execute("INSERT INTO cursor(src, path, offset, head_sha, ts) VALUES(?,?,?,?,?)", (LS.CURSOR_BUS_CLOSED, "bus", 0, None, NOW))
        self.assertEqual(c.execute("SELECT state, complete_ts FROM run_paths").fetchone(), ("complete", NOW + 90000))
        self.assertEqual(LS.PATH_STATES, ("partial", "complete"))
        self.assertEqual(c.execute("SELECT offset FROM cursor WHERE src='bus_closed'").fetchone()[0], 0)


# ---------------------------------------------------------------- миграция x1

class TestX1Migration(Base):
    def test_from_v2a_fixture_with_backup_and_idempotent(self):
        c = self.rw()
        store2.migrate_v2a(c, self.root, explicit=True, now=NOW - 10)
        store2.append_row(c, "rulebook_versions", {"ver": "R-0001", "parent": None, "reason_code": "INIT", "rule_ids": [], "numbers": {"n": 0}})
        store2.log_event(c, "x", now=NOW)
        n_ev = c.execute("SELECT COUNT(*) FROM learn_events").fetchone()[0]
        n_bk = len(store2.list_backups(self.root))
        self.assertFalse(store2.ready_x1(c))
        self.assertEqual(store2.migrate_x1(c, self.root, now=NOW), 1)
        self.assertTrue(store2.ready_x1(c))
        bks = store2.list_backups(self.root)
        self.assertEqual(len(bks), n_bk + 1)
        self.assertEqual(bks[-1]["reason"], "premigrate")
        ok, code, info = store2.check_copy(bks[-1]["path"])                     # копия до миграции: проверена, без таблиц x1
        self.assertTrue(ok, code)
        self.assertEqual(info["x1"], 0)
        self.assertTrue(set(S2.X1_TABLES) <= self.names(c, "table"))
        self.assertTrue(set(S2.X1_INDEXES) <= self.names(c, "index"))
        self.assertTrue(set(S2.X1_TRIGGERS) <= self.names(c, "trigger"))
        self.assertEqual(c.execute("SELECT v FROM meta WHERE k=?", (S2.META_X1_KEY,)).fetchone()[0], "1")
        self.assertEqual(c.execute("SELECT COUNT(*) FROM learn_events").fetchone()[0], n_ev)
        self.assertEqual(store2.verify_chain(c)["n"], 1)
        self.assertTrue(store2.ready(c))                                        # v2a не тронута
        self.assertEqual(store2.migrate_x1(c, self.root, now=NOW + 5), 1)       # повтор: без новой копии и изменений
        self.assertEqual(len(store2.list_backups(self.root)), n_bk + 1)
        self.assertIn("path_mfe_r", self.cols(c, "exit_evals"))
        self.assertIn("px_mode", self.cols(c, "exit_evals"))
        self.assertEqual(self.cols(c, "exit_versions")[-2:], ["prev_h", "row_h"])

    def test_v1_labels_fixture_migrates(self):
        c = self.v1_fixture()
        before = self.counts(c)
        store.migrate(c)
        self.assertEqual(store2.migrate_x1(c, self.root, now=NOW), 1)
        self.assertEqual(self.counts(c), before)
        self.assertFalse(store2.ready(c))                                       # x1 не требует v2a и не создаёт её
        self.assertTrue(store2.ready_x1(c))

    def test_failure_rolls_back(self):
        c = self.rw()
        with mock.patch.object(S2, "X1", list(S2.X1[:3]) + ["CREATE TABLE broken("]):
            with self.assertRaises(store2.KnowledgeMigrationError) as cm:
                store2.migrate_x1(c, self.root, now=NOW)
        self.assertEqual(cm.exception.code, "KNOWLEDGE_MIGRATION")
        self.assertFalse(c.in_transaction)
        self.assertNotIn("exit_variants", self.names(c, "table"))
        self.assertEqual(store2.version_x1(c), 0)

    def test_backup_failure_blocks_migration(self):
        c = self.rw()
        with mock.patch.object(store2, "make_backup", side_effect=store2.BackupError("SRC_CORRUPT", "x")):
            with self.assertRaises(store2.KnowledgeMigrationError):
                store2.migrate_x1(c, self.root, now=NOW)
        self.assertNotIn("exit_versions", self.names(c, "table"))

    def test_exit_evals_key_includes_policy_h(self):
        c = self.x1()
        row = {"src": "run", "key": "k1", "var_id": "L1", "var_ver": 1, "ord": "S", "px_mode": "fee", "policy_h": "a" * 16,
               "cost_h": "c" * 16, "sim_ver": "xs1", "t0": NOW, "block": "0:long", "labeled_ts": NOW, "amb": 0, "created_ts": NOW}
        with store.tx(c):
            self.assertEqual(store2.insert_rows(c, "exit_evals", [row], ignore_dup=True), 1)
            self.assertEqual(store2.insert_rows(c, "exit_evals", [dict(row)], ignore_dup=True), 0)
            self.assertEqual(store2.insert_rows(c, "exit_evals", [dict(row, policy_h="b" * 16)], ignore_dup=True), 1)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM exit_evals").fetchone()[0], 2)

    def test_not_in_labels_migrations(self):
        flat = " ".join(s for _, stmts in LS.MIGRATIONS for s in stmts)
        for t in S2.X1_TABLES:
            self.assertNotIn(t, flat)

    def test_write_before_migration_not_ready(self):
        c = self.rw()
        store2.migrate_v2a(c, self.root, explicit=True, now=NOW)
        with self.assertRaises(store2.NotReady):
            store2.append_row(c, "exit_versions", self.version("X-0001"))
        with self.assertRaises(store2.NotReady):
            store2.insert_rows(c, "exit_blacklist", [{"var_id": "L1", "var_ver": 1, "until_ts": NOW, "reason": "TRIAL_REJECT", "ts": NOW}])


# ---------------------------------------------------------------- триггеры и цепочка

class TestTrialsCriterion(Base):
    def trial(self, c):
        row = {"trial_id": "XT-0001", "var_id": "L1", "var_ver": 1, "base_ver": "X-0001", "criterion": {"n_target": 30, "block_h": 24},
               "criterion_h": "c" * 16, "state": "open", "start_ts": NOW, "deadline_ts": NOW + 21 * 86400}
        self.assertEqual(store2.insert_rows(c, "exit_trials", [row]), 1)

    def test_criterion_immutable(self):
        c = self.x1()
        self.trial(c)
        self.assertEqual(store2.update_row(c, "exit_trials", "XT-0001", {"state": "closed", "verdict": "reject", "closed_ts": NOW + 5}), 1)
        self.assertEqual(c.execute("SELECT state, verdict FROM exit_trials").fetchone(), ("closed", "reject"))
        for col, v in (("criterion", '{"n_target":10}'), ("criterion_h", "d" * 16)):
            with self.assertRaises(sqlite3.DatabaseError) as cm:
                c.execute("UPDATE exit_trials SET %s=? WHERE trial_id='XT-0001'" % col, (v,))
            self.assertIn("IMMUTABLE", str(cm.exception))
            with self.assertRaises(store2.RowRejected):
                store2.update_row(c, "exit_trials", "XT-0001", {col: v})
        with self.assertRaises(sqlite3.DatabaseError):
            c.execute("DELETE FROM exit_trials")
        crit = json.loads(c.execute("SELECT criterion FROM exit_trials").fetchone()[0])
        self.assertEqual(crit, {"block_h": 24, "n_target": 30})
        self.assertFalse(c.in_transaction)


class TestVersionsChain(Base):
    def test_chain(self):
        c = self.x1()
        r1 = store2.append_row(c, "exit_versions", self.version("X-0001"))
        r2 = store2.append_row(c, "exit_versions", self.version("X-0002", parent="X-0001", reason="PROMOTE", policy_h="e" * 16))
        self.assertEqual((r1["seq"], r2["seq"]), (1, 2))
        self.assertEqual(r1["prev_h"], store2.GENESIS)
        self.assertEqual(r2["prev_h"], r1["row_h"])
        self.assertEqual(store2.verify_chain(c, "exit_versions"), {"ok": True, "n": 2, "bad_seq": None})
        with self.assertRaises(store2.ChainBroken):                              # prev вызывающего не равен h последней строки
            store2.append_row(c, "exit_versions", self.version("X-0003", prev_h="f" * 16))
        for sql in ("UPDATE exit_versions SET reason_code='X'", "DELETE FROM exit_versions"):
            with self.assertRaises(sqlite3.DatabaseError) as cm:
                c.execute(sql)
            self.assertIn("APPEND_ONLY exit_versions", str(cm.exception))
        with self.assertRaises(store2.RowRejected):
            store2.insert_rows(c, "exit_versions", [self.version("X-0009")])     # цепочка только через append_row
        with self.assertRaises(sqlite3.IntegrityError):
            store2.append_row(c, "exit_versions", self.version("X-0002"))       # ver уникален
        self.assertEqual(store2.verify_chain(c, "exit_versions")["n"], 2)
        rep = store2.verify(self.root, now=NOW)
        self.assertTrue(rep["x1"])
        self.assertEqual(rep["chains"]["exit_versions"]["n"], 2)
        self.assertNotIn(S2.CHAIN_BROKEN, rep["codes"])
        self.assertNotIn(S2.SCHEMA_MISMATCH, rep["codes"])
        self.assertEqual(rep["tables"]["exit_versions"], 2)
        self.assertEqual(rep["tables"]["run_paths"], 0)

    def test_tampered_chain(self):
        c = self.x1()
        store2.append_row(c, "exit_versions", self.version("X-0001"))
        store2.append_row(c, "exit_versions", self.version("X-0002", parent="X-0001", reason="PROMOTE"))
        c.execute("DROP TRIGGER tr_exit_versions_nu")
        c.execute("UPDATE exit_versions SET reason_code='ROLLBACK' WHERE seq=1")
        self.assertEqual(store2.verify_chain(c, "exit_versions"), {"ok": False, "n": 0, "bad_seq": 1})
        with self.assertRaises(store2.ChainBroken):
            store2.append_row(c, "exit_versions", self.version("X-0003"))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM exit_versions").fetchone()[0], 2)    # запись отказана, чтение работает
        rep = store2.verify(self.root, now=NOW)
        self.assertIn(S2.CHAIN_BROKEN, rep["codes"])
        self.assertIn(S2.SCHEMA_MISMATCH, rep["codes"])                          # триггера нет
        self.assertEqual(rep["color"], "red")
        with self.assertRaises(store2.BackupError) as cm:
            store2.make_backup(self.root, "owner", now=NOW)
        self.assertEqual(cm.exception.code, S2.CHAIN_BROKEN)

    def test_rulebook_chain_unchanged(self):
        """Обобщение цепочки по колонкам не меняет хеши rulebook_versions (prev/h)."""
        c = self.rw()
        store2.migrate_v2a(c, self.root, explicit=True, now=NOW)
        r = store2.append_row(c, "rulebook_versions", {"ver": "R-0001", "parent": None, "reason_code": "INIT", "rule_ids": [], "numbers": {}})
        full = dict((k, v) for k, v in r.items() if k != "h")
        from tb.memory import chain as CH
        self.assertEqual(r["h"], CH.make_hash(store2.GENESIS, full))
        self.assertTrue(store2.verify_chain(c)["ok"])


class TestBackupIncludesExits(Base):
    def test_make_backup_has_x1_and_paths(self):
        c = self.x1()
        store2.append_row(c, "exit_versions", self.version("X-0001"))
        store2.insert_rows(c, "exit_variants", [{"id": "L1", "ver": 1, "family": "lock", "policy": {"lock": [{"at_r": 1.0, "keep": 0.5}]},
                                                 "policy_h": "a" * 16, "menu_ver": 1, "status": "menu", "created_ts": NOW}])
        store2.insert_rows(c, "exit_search", [{"ts": NOW, "code": "LOW_N", "cfg_h": "0" * 64, "results": {"n": 3}}])
        dst = store2.make_backup(self.root, "owner", now=NOW)
        ok, code, info = store2.check_copy(dst)
        self.assertTrue(ok, code)
        self.assertEqual(info["x1"], 1)
        b = sqlite3.connect(dst)
        try:
            self.assertEqual(b.execute("SELECT COUNT(*) FROM exit_versions").fetchone()[0], 1)
            self.assertEqual(b.execute("SELECT COUNT(*) FROM exit_variants").fetchone()[0], 1)
            self.assertEqual(b.execute("SELECT COUNT(*) FROM exit_search").fetchone()[0], 1)
            names = {r[0] for r in b.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"run_paths", "trade_paths"} | set(S2.X1_TABLES) <= names)
            self.assertEqual(b.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            b.close()

    def test_verify_integrity_and_missing_index(self):
        c = self.x1()
        rep = store2.verify(self.root, now=NOW)
        self.assertEqual(rep["integrity"], "ok")
        self.assertNotIn(S2.SCHEMA_MISMATCH, rep["codes"])
        c.execute("DROP INDEX ix_ee_var")
        rep = store2.verify(self.root, now=NOW)
        self.assertIn(S2.SCHEMA_MISMATCH, rep["codes"])

    def test_privacy_and_size(self):
        c = self.x1()
        with self.assertRaises(store2.RowRejected) as cm:
            store2.insert_rows(c, "exit_blacklist", [{"var_id": "L1", "var_ver": 1, "until_ts": NOW, "reason": "https://x.example/a", "ts": NOW}])
        self.assertEqual(cm.exception.code, "PRIVACY")
        with self.assertRaises(store2.RowRejected) as cm:
            store2.insert_rows(c, "exit_blacklist", [{"var_id": "L1", "var_ver": 1, "until_ts": NOW, "reason": "x " * 600, "ts": NOW}])
        self.assertEqual(cm.exception.code, "ROW_TOO_BIG")


if __name__ == "__main__":
    unittest.main()
