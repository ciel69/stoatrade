"""tb.knowledge.schema2/store2/markers/cli (R2, задача 4): свойства 2 и 3 design.md - триггеры append-only/insert-only (в том числе cluster_runs), неизменяемые
колонки trials, цепочка rulebook_versions и CHAIN_BROKEN (запись отказана, чтение работает), миграция v2a и откат, privacy_scan и лимиты, learn_events
и EVENTS_THROTTLED, API чёрного списка, make_backup (имя, проверка до переименования, без перезаписи, ротация по причинам, SRC_CORRUPT), протокол
восстановления (маркер, подтверждения и таймаут, переименование -wal/-shm, отказ при их наличии у целевого пути, PermissionError и исчерпание повторов,
copy_ts/restore_ts, AUTO_RESTORED), классификатор is_corruption, verify/stats/./tb status, параллельные читатель и писатель. Сеть не используется."""
import io
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import _bootstrap  # noqa: F401
from tb.knowledge import cli as KC
from tb.knowledge import markers as KM
from tb.knowledge import schema2 as S2
from tb.knowledge import store2
from tb.labels import schema as LS
from tb.labels import store

NOW = 1793664000          # 2026-11-03T00:00:00Z


def corrupt_file(path):
    with open(path, "wb") as f:
        f.write(b"this is not a sqlite database " * 200)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbknow_")
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

    def v2(self):
        c = self.rw()
        self.assertEqual(store2.migrate_v2a(c, self.root, explicit=True, now=NOW), 1)
        return c

    def close_all(self):
        for c in self.conns:
            try:
                c.close()
            except sqlite3.Error:
                pass
        self.conns = []

    def names(self, c, kind):
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))}

    def version_row(self, ver, reason="INIT", **kw):
        d = {"ver": ver, "parent": None, "reason_code": reason, "rule_ids": [], "numbers": {"n": 0}}
        d.update(kw)
        return d


# ---------------------------------------------------------------- миграция

class TestMigration(Base):
    def test_not_enabled_creates_nothing(self):
        c = self.rw()
        self.assertEqual(store2.migrate_v2a(c, self.root), 0)
        self.assertNotIn("cluster_runs", self.names(c, "table"))
        self.assertFalse(KM.has_v2(self.root))
        self.assertFalse(os.path.isdir(os.path.join(self.root, "state", "backups")))

    def test_explicit_creates_tables_indexes_triggers_marker(self):
        c = self.v2()
        tabs = self.names(c, "table")
        for t in S2.TABLES:
            self.assertIn(t, tabs)
        for t in ("observations", "trial_obs", "watch_obs"):
            self.assertNotIn(t, tabs)                                         # таблиц наблюдений нет
        self.assertTrue(set(S2.INDEXES) <= self.names(c, "index"))
        self.assertTrue(set(S2.TRIGGERS) <= self.names(c, "trigger"))
        self.assertEqual(c.execute("SELECT v FROM meta WHERE k='knowledge_v2_ver'").fetchone()[0], "1")
        self.assertEqual(c.execute("SELECT v FROM meta WHERE k=?", (LS.META_VER_KEY,)).fetchone()[0], "1")   # ключ R1 не тронут
        self.assertTrue(KM.has_v2(self.root))
        self.assertEqual([b["reason"] for b in store2.list_backups(self.root)], ["premigrate"])
        cols = [r[1] for r in c.execute("PRAGMA table_info(cluster_runs)")]
        self.assertEqual(cols, ["cluster", "hour", "side", "key", "t0", "type", "policy_id", "policy_ver", "ctx_ver", "env_norm", "filled", "r_net", "feats", "flags", "closed_ts"])
        self.assertIn("wait_since_ts", [r[1] for r in c.execute("PRAGMA table_info(rule_rows)")])
        self.assertIn("cut_ts", [r[1] for r in c.execute("PRAGMA table_info(trials)")])
        ix = c.execute("SELECT sql FROM sqlite_master WHERE name='ix_cr_t0'").fetchone()[0]
        self.assertIn("policy_id, ctx_ver, env_norm, t0", ix)

    def test_enabled_flag_also_migrates_and_rerun_is_noop(self):
        c = self.rw()
        self.assertEqual(store2.migrate_v2a(c, self.root, enabled=True), 1)
        n = len(store2.list_backups(self.root))
        self.assertEqual(store2.migrate_v2a(c, self.root, explicit=True), 1)
        self.assertEqual(len(store2.list_backups(self.root)), n)               # повтор без новой копии

    def test_not_in_labels_migrations(self):
        flat = " ".join(s for _, stmts in LS.MIGRATIONS for s in stmts)
        for t in S2.TABLES:
            self.assertNotIn(t, flat)

    def test_failure_rolls_back_and_labels_keep_working(self):
        c = self.rw()
        bad = list(S2.V2A[:3]) + ["CREATE TABLE broken("]
        with mock.patch.object(S2, "V2A", bad):
            with self.assertRaises(store2.KnowledgeMigrationError) as cm:
                store2.migrate_v2a(c, self.root, explicit=True)
        self.assertEqual(cm.exception.code, "KNOWLEDGE_MIGRATION")
        self.assertNotIn("cluster_runs", self.names(c, "table"))
        self.assertIsNone(c.execute("SELECT v FROM meta WHERE k='knowledge_v2_ver'").fetchone())
        self.assertFalse(KM.has_v2(self.root))
        self.assertFalse(c.in_transaction)
        self.close_all()
        c2 = self.rw()                                                         # сервис меток открывает базу как раньше (SchemaMigrationError нет)
        self.assertEqual(c2.execute("SELECT v FROM meta WHERE k=?", (LS.META_VER_KEY,)).fetchone()[0], "1")


# ---------------------------------------------------------------- свойство 2: неизменяемость

class TestImmutability(Base):
    def rows(self):
        return {
            "cluster_runs": {"cluster": "100:long", "hour": 100, "side": "long", "key": "K1", "t0": 360000, "type": "range_5m", "policy_id": "P2", "policy_ver": 1,
                             "ctx_ver": "c1", "env_norm": "mainnet", "filled": 1, "r_net": 0.5, "feats": {"spread_bps": 3.0}, "closed_ts": 400000},
            "rule_evals": {"rec_seq": 1, "rule_id": "", "ts": NOW, "no_event": 0, "hit": 0, "evaluated": 0, "missing": 0},
            "rule_rows": {"rule_id": "RL-1", "rev": 1, "kind": "skip_when", "status": "trial", "from_lesson": "L-1", "env_norm": "mainnet", "reason_code": "TRIAL",
                          "body": {"cond": [["spread_bps", ">", 10]]}},
            "blocklist_rows": {"cond_h": "a" * 64, "code": "TRIAL_REJECT", "until_ts": NOW + 86400, "actor": "code"},
            "learn_events": {"step": "observe", "code": "NO_DATA", "actor": "code"},
        }

    def test_update_delete_rejected(self):
        c = self.v2()
        for t, r in self.rows().items():
            store2.append_row(c, t, r)
        store2.append_row(c, "rulebook_versions", self.version_row("R-0001"))
        for t in S2.APPEND_ONLY + S2.INSERT_ONLY:
            with self.assertRaises(sqlite3.IntegrityError, msg=t):
                c.execute("UPDATE %s SET %s=%s" % (t, "ts" if t != "cluster_runs" else "t0", "ts" if t != "cluster_runs" else "t0"))
            with self.assertRaises(sqlite3.IntegrityError, msg=t):
                c.execute("DELETE FROM %s" % t)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0], 1, t)

    def test_cluster_runs_insert_once(self):
        c = self.v2()
        r = self.rows()["cluster_runs"]
        self.assertEqual(store2.insert_rows(c, "cluster_runs", [r]), 1)
        with self.assertRaises(sqlite3.IntegrityError):
            store2.insert_rows(c, "cluster_runs", [r])
        self.assertEqual(store2.insert_rows(c, "cluster_runs", [r], ignore_dup=True), 0)   # идемпотентный повтор шага
        self.assertEqual(c.execute("SELECT COUNT(*) FROM cluster_runs").fetchone()[0], 1)

    def trial(self, c, tid="T-1", rule="RL-1"):
        store2.insert_rows(c, "trials", [{"trial_id": tid, "rule_id": rule, "lesson_id": "L-1", "env_norm": "mainnet", "criterion": {"n_target": 150},
                                          "criterion_h": "f" * 64, "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1", "start_ts": NOW, "deadline_ts": NOW + 30 * 86400,
                                          "state": "open"}])

    def test_trials_immutable_columns(self):
        c = self.v2()
        self.trial(c)
        for col, v in (("criterion", "'{}'"), ("criterion_h", "'x'"), ("start_ts", "0"), ("rule_id", "'RL-2'"), ("env_norm", "'testnet'"), ("ctx_ver", "'c2'")):
            with self.assertRaises(sqlite3.IntegrityError, msg=col):
                c.execute("UPDATE trials SET %s=%s" % (col, v))
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("DELETE FROM trials")
        self.assertEqual(store2.update_row(c, "trials", "T-1", {"state": "closed", "verdict": "promote", "numbers": {"n": 150}, "cut_ts": NOW + 1, "closed_ts": NOW + 2}), 1)
        self.assertEqual(c.execute("SELECT state, verdict FROM trials").fetchone(), ("closed", "promote"))
        with self.assertRaises(store2.RowRejected):
            store2.update_row(c, "trials", "T-1", {"criterion": "{}"})
        with self.assertRaises(store2.RowRejected):
            store2.update_row(c, "rule_rows", 1, {"status": "active"})
        with self.assertRaises(sqlite3.IntegrityError):                       # UNIQUE(rule_id): одно испытание на правило
            self.trial(c, "T-2", "RL-1")

    def test_rule_and_trial_in_one_transaction(self):
        c = self.v2()
        with self.assertRaises(sqlite3.IntegrityError):
            with store.tx(c):
                store2.append_row(c, "rule_rows", self.rows()["rule_rows"])
                self.trial(c)
                self.trial(c, "T-2", "RL-1")
        self.assertEqual(c.execute("SELECT COUNT(*) FROM rule_rows").fetchone()[0], 0)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM trials").fetchone()[0], 0)


# ---------------------------------------------------------------- цепочка

class TestChain(Base):
    def test_chain_ok_and_fields(self):
        c = self.v2()
        a = store2.append_row(c, "rulebook_versions", self.version_row("R-0001"))
        b = store2.append_row(c, "rulebook_versions", self.version_row("R-0002", "PROMOTE", parent="R-0001", trial_id="T-1", rule_ids=["RL-1"]))
        self.assertEqual(a["prev"], store2.GENESIS)
        self.assertEqual(b["prev"], a["h"])
        self.assertEqual((a["seq"], b["seq"]), (1, 2))
        self.assertEqual(store2.verify_chain(c), {"ok": True, "n": 2, "bad_seq": None})

    def tamper(self, c, sql):
        c.execute("DROP TRIGGER tr_rulebook_versions_nu")                    # подмена в обход триггера
        c.execute(sql)
        c.execute(S2._guard("rulebook_versions", "nu", "UPDATE"))

    def test_tamper_blocks_write_but_read_works(self):
        c = self.v2()
        for i in range(1, 4):
            store2.append_row(c, "rulebook_versions", self.version_row("R-%04d" % i))
        self.tamper(c, "UPDATE rulebook_versions SET rule_ids='[\"RL-9\"]' WHERE seq=2")
        self.assertEqual(store2.verify_chain(c)["bad_seq"], 2)
        with self.assertRaises(store2.ChainBroken) as cm:
            store2.append_row(c, "rulebook_versions", self.version_row("R-0004"))
        self.assertEqual(cm.exception.code, "CHAIN_BROKEN")
        self.assertEqual(c.execute("SELECT COUNT(*) FROM rulebook_versions").fetchone()[0], 3)   # запись отказана
        self.assertEqual(c.execute("SELECT ver FROM rulebook_versions ORDER BY seq DESC LIMIT 1").fetchone()[0], "R-0003")  # чтение работает
        store2.append_row(c, "learn_events", {"step": "promote", "code": "CHAIN_BROKEN", "actor": "code"})   # другие таблицы пишутся
        rep = store2.verify(self.root)
        self.assertIn("CHAIN_BROKEN", rep["codes"])
        self.assertEqual(rep["color"], "red")

    def test_prev_mismatch(self):
        c = self.v2()
        store2.append_row(c, "rulebook_versions", self.version_row("R-0001"))
        with self.assertRaises(store2.ChainBroken):
            store2.append_row(c, "rulebook_versions", dict(self.version_row("R-0002"), prev="0" * 16))

    def test_backup_of_broken_chain_refused(self):
        c = self.v2()
        store2.append_row(c, "rulebook_versions", self.version_row("R-0001"))
        self.tamper(c, "UPDATE rulebook_versions SET reason_code='PROMOTE'")
        n = len(store2.list_backups(self.root))
        with self.assertRaises(store2.BackupError) as cm:
            store2.make_backup(self.root, "daily", now=NOW + 60)
        self.assertEqual(cm.exception.code, "CHAIN_BROKEN")
        self.assertEqual(len(store2.list_backups(self.root)), n)
        self.assertEqual([f for f in os.listdir(store2.backups_dir(self.root)) if f.endswith(".tmp")], [])


# ---------------------------------------------------------------- проверки записи

class TestRowChecks(Base):
    def test_not_ready(self):
        c = self.rw()
        with self.assertRaises(store2.NotReady) as cm:
            store2.append_row(c, "learn_events", {"step": "x", "actor": "code"})
        self.assertEqual(cm.exception.code, "NOT_READY")

    def test_privacy_scan(self):
        c = self.v2()
        for body in ({"note": "see https://example.com"}, {"pnl_usdt": 5}, {"x": "C:\\Users\\me\\key.txt"}, {"x": "sk-abcdefghijklmnopqrstuv"}):
            with self.assertRaises(store2.RowRejected) as cm:
                store2.append_row(c, "learn_events", {"step": "x", "actor": "code", "body": body})
            self.assertEqual(cm.exception.code, "PRIVACY")
        store2.blocklist_put(c, "e" * 64, "TRIAL_REJECT", NOW, "code", now=NOW)   # длинный хеш cond_h - не утечка
        self.assertEqual(c.execute("SELECT COUNT(*) FROM learn_events").fetchone()[0], 0)

    def test_size_limits(self):
        c = self.v2()
        r = {"rule_id": "RL-1", "rev": 1, "kind": "skip_when", "status": "trial", "from_lesson": "L-1", "env_norm": "mainnet", "body": {"pad": "x " * 1100}}
        with self.assertRaises(store2.RowRejected) as cm:
            store2.append_row(c, "rule_rows", r)
        self.assertEqual(cm.exception.code, "ROW_TOO_BIG")
        r["body"] = {"pad": "x " * 800}
        store2.append_row(c, "rule_rows", r)                                   # < 2 КБ проходит
        with self.assertRaises(store2.RowRejected):
            store2.append_row(c, "rulebook_versions", self.version_row("R-0001", numbers={"pad": "y " * 2100}))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM rulebook_versions").fetchone()[0], 0)

    def test_bad_column_and_table(self):
        c = self.v2()
        with self.assertRaises(store2.RowRejected):
            store2.append_row(c, "learn_events", {"step": "x", "actor": "code", "nope": 1})
        with self.assertRaises(store2.RowRejected):
            store2.append_row(c, "signals", {"key": "x"})
        with self.assertRaises(store2.RowRejected):
            store2.insert_rows(c, "rulebook_versions", [self.version_row("R-0001")])


# ---------------------------------------------------------------- learn_events

class TestEvents(Base):
    def test_throttle_and_errors(self):
        c = self.v2()
        got = [store2.log_event(c, "observe", "NOT_DUE", now=NOW + i, per_day=3) for i in range(5)]
        self.assertEqual(sum(1 for g in got if g), 3)
        self.assertTrue(store2.log_event(c, "observe", "STEP_ERROR", now=NOW + 10, per_day=3))
        self.assertTrue(store2.log_event(c, "rulebook", "OWNER", actor="OWNER", now=NOW + 11, per_day=3))
        self.assertTrue(store2.log_event(c, "x", None, error=True, now=NOW + 12, per_day=3))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM learn_events").fetchone()[0], 6)
        store2.log_event(c, "observe", "NOT_DUE", now=NOW + 86400 + 5, per_day=3)     # новые сутки: строка с числом пропущенных
        thr = c.execute("SELECT ref, body FROM learn_events WHERE code='EVENTS_THROTTLED'").fetchall()
        self.assertEqual(len(thr), 1)
        self.assertEqual(thr[0][0], "2026-11-03")
        self.assertEqual(json.loads(thr[0][1]), {"day": "2026-11-03", "skipped": 2})
        self.assertEqual(c.execute("SELECT COUNT(*) FROM learn_events").fetchone()[0], 8)

    def test_flush_force(self):
        c = self.v2()
        for i in range(3):
            store2.log_event(c, "observe", "NOT_DUE", now=NOW + i, per_day=1)
        self.assertIsNone(store2.flush_events(c, now=NOW + 5))
        self.assertTrue(store2.flush_events(c, now=NOW + 5, force=True))
        self.assertIsNone(store2.flush_events(c, now=NOW + 6, force=True))
        with self.assertRaises(store2.RowRejected):
            store2.log_event(c, "x", actor="model")

    def test_checkpoint(self):
        c = self.v2()
        self.assertEqual(len(store2.checkpoint(c)), 3)
        store2.checkpoint(c, "TRUNCATE")
        with self.assertRaises(ValueError):
            store2.checkpoint(c, "DROP")


# ---------------------------------------------------------------- чёрный список

class TestBlocklist(Base):
    def test_put_get(self):
        c = self.v2()
        h = "c" * 64
        self.assertIsNone(store2.blocklist_get(c, h, NOW))
        store2.blocklist_put(c, h, "TRIAL_REJECT", NOW + 14 * 86400, "code", now=NOW)
        self.assertEqual(store2.blocklist_get(c, h, NOW + 1), {"code": "TRIAL_REJECT", "until_ts": NOW + 14 * 86400})
        self.assertIsNone(store2.blocklist_get(c, h, NOW + 14 * 86400))        # истёкшая запись не действует
        store2.blocklist_put(c, h, "OWNER_REJECT", None, "OWNER", now=NOW + 2)
        self.assertEqual(store2.blocklist_get(c, h, NOW + 10 ** 9), {"code": "OWNER_REJECT", "until_ts": None})   # бессрочно
        store2.blocklist_put(c, h, "UNBLOCK", None, "OWNER", now=NOW + 3)
        self.assertIsNone(store2.blocklist_get(c, h, NOW + 4))                 # последняя строка: снято
        self.assertIsNone(store2.blocklist_get(c, "d" * 64, NOW))
        for bad in (("x", "LOSS_CAP", None, "code"), ("x", "CTX_CHANGED", None, "code"), ("x", "TRIAL_REJECT", None, "model"), ("", "TRIAL_REJECT", None, "code"),
                    ("x", "TRIAL_REJECT", "soon", "code")):
            with self.assertRaises(store2.RowRejected, msg=bad):
                store2.blocklist_put(c, *bad, now=NOW)

    def test_get_without_tables(self):
        c = self.rw()
        self.assertIsNone(store2.blocklist_get(c, "x", NOW))


# ---------------------------------------------------------------- копии

class TestBackup(Base):
    def test_name_verified_no_wal_no_overwrite(self):
        c = self.v2()
        store2.log_event(c, "x", "NOT_DUE", now=NOW)
        a = store2.make_backup(self.root, "daily", now=NOW + 3600)
        b = store2.make_backup(self.root, "daily", now=NOW + 3600)            # то же время: новый файл, старый не затирается
        self.assertEqual(os.path.basename(a), "knowledge-20261103T010000-daily.sqlite")
        self.assertNotEqual(a, b)
        self.assertTrue(os.path.exists(a) and os.path.exists(b))
        for p in (a, b):
            self.assertFalse(os.path.exists(p + "-wal") or os.path.exists(p + "-shm"))
            self.assertEqual(store2.check_copy(p)[:2], (True, None))
            k = sqlite3.connect(p)
            try:
                self.assertEqual(k.execute("SELECT COUNT(*) FROM learn_events").fetchone()[0], 1)
                self.assertEqual(k.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            finally:
                k.close()

    def test_premigrate_is_oldest_at_same_stamp(self):
        # premigrate снята до миграции: при равном времени restore должен выбрать копию после миграции, а не её
        self.v2()
        d = store2.backups_dir(self.root)
        stamp = "20261103T000000"
        for reason in ("daily", "premigrate", "promote"):
            open(os.path.join(d, "knowledge-%s-%s.sqlite" % (stamp, reason)), "wb").close()
        same = [b["reason"] for b in store2.list_backups(self.root) if b["stamp"] == stamp]
        self.assertEqual(same[0], "premigrate")
        self.assertNotEqual(same[-1], "premigrate")

    def test_rotation_by_reason(self):
        self.v2()
        r1 = store.backup(self.root, today="20261101")                         # копия R1 не трогается
        for i in range(9):
            store2.make_backup(self.root, "daily", now=NOW + i * 86400)
        for i in range(5):
            store2.make_backup(self.root, "owner", now=NOW + i)
        for i in range(4):
            store2.make_backup(self.root, "promote", now=NOW + i)
        by = {}
        for b in store2.list_backups(self.root):
            by.setdefault(b["reason"], []).append(b["stamp"])
        self.assertEqual(len(by["daily"]), 7)
        self.assertEqual(by["daily"][0], "20261105T000000")                   # остались 7 последних
        self.assertEqual(len(by["owner"]), 3)
        self.assertEqual(len(by["promote"]), 3)
        self.assertEqual(len(by["premigrate"]), 1)
        self.assertTrue(os.path.exists(r1))

    def test_src_corrupt_not_copied_and_keeps_good(self):
        self.v2()
        good = store2.make_backup(self.root, "daily", now=NOW)
        self.close_all()
        corrupt_file(store2.db_path(self.root))
        with self.assertRaises(store2.BackupError) as cm:
            store2.make_backup(self.root, "daily", now=NOW + 60)
        self.assertEqual(cm.exception.code, "SRC_CORRUPT")
        self.assertEqual([b["path"] for b in store2.list_backups(self.root) if b["reason"] == "daily"], [good])
        self.assertEqual([f for f in os.listdir(store2.backups_dir(self.root)) if f.endswith(".tmp")], [])

    def test_page_corruption_detected(self):
        c = self.v2()
        with store.tx(c):
            c.executemany("INSERT INTO signals(key, t0, sym, type, ev, status, mature_ts, next_try_ts, captured_ts, schema_ver) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          [("K%05d" % i, i, "BTC", "range_5m", "{\"pad\": \"%s\"}" % ("z" * 200), "done", 0, 0, 0, 1) for i in range(3000)])
        store2.checkpoint(c, "TRUNCATE")
        self.close_all()
        path = store2.db_path(self.root)
        with open(path, "r+b") as f:
            f.seek(4096 * 6)
            f.write(b"\xff" * 4096 * 4)
        with self.assertRaises(store2.BackupError) as cm:
            store2.make_backup(self.root, "daily", now=NOW + 60)
        self.assertEqual(cm.exception.code, "SRC_CORRUPT")

    def test_refusals(self):
        with self.assertRaises(FileNotFoundError):
            store2.make_backup(self.root, "daily")
        self.v2()
        with self.assertRaises(store2.BackupError):
            store2.make_backup(self.root, "weekly")
        KM.write_json(KM.restoring_path(self.root), {"pid": os.getpid()})
        with self.assertRaises(store2.BackupError) as cm:
            store2.make_backup(self.root, "owner")
        self.assertEqual(cm.exception.code, "RESTORING")

    def test_permission_retry_on_copy(self):
        self.v2()
        calls, orig = [], store2._copy_db

        def flaky(src, dst):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError("busy")
            return orig(src, dst)
        with mock.patch.object(store2, "_copy_db", flaky):
            p = store2.make_backup(self.root, "owner", now=NOW, sleep=lambda s: None)
        self.assertEqual(len(calls), 3)
        self.assertTrue(os.path.exists(p))


# ---------------------------------------------------------------- восстановление

class RestoreBase(Base):
    def prepare(self, extra=3):
        """База v2 с одной строкой событий -> копия daily; затем ещё extra строк (их в копии нет); соединения закрыты."""
        c = self.v2()
        store2.log_event(c, "observe", "NOT_DUE", now=NOW)
        self.copy = store2.make_backup(self.root, "daily", now=NOW + 60)
        for i in range(extra):
            store2.log_event(c, "observe", "NOT_DUE", now=NOW + 100 + i)
        self.close_all()
        return self.copy

    def damage(self, wal=True):
        path = store2.db_path(self.root)
        corrupt_file(path)
        if wal:
            for s in ("-wal", "-shm"):
                with open(path + s, "wb") as f:
                    f.write(b"old journal")
        return path

    def count_events(self):
        k = sqlite3.connect(store2.db_path(self.root))
        try:
            return k.execute("SELECT COUNT(*) FROM learn_events WHERE code='NOT_DUE'").fetchone()[0]
        finally:
            k.close()


class TestRestore(RestoreBase):
    def test_manual_restore_protocol(self):
        self.prepare()
        path = self.damage(wal=False)
        with self.assertRaises(store.DbCorruptError):
            store.open_rw(self.root)                                           # пустая база не создаётся
        self.assertEqual([f for f in os.listdir(os.path.dirname(path)) if f.startswith("knowledge.corrupt")], [])
        self.damage()                                                          # -wal/-shm старой базы рядом с файлом
        rep = store2.restore(self.root, clock=lambda: NOW + 1000, sleep=lambda s: None)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["src"], os.path.basename(self.copy))
        self.assertEqual(self.count_events(), 1)                               # состояние копии
        moved = sorted(os.path.basename(p) for p in rep["moved"])
        self.assertEqual(moved, ["knowledge.corrupt-%d.sqlite" % (NOW + 1000), "knowledge.corrupt-%d.sqlite-shm" % (NOW + 1000),
                                 "knowledge.corrupt-%d.sqlite-wal" % (NOW + 1000)])
        for p in rep["moved"]:
            self.assertTrue(os.path.exists(p))                                 # переименованы, не удалены
        self.assertFalse(KM.restoring(self.root))
        c = self.rw()
        meta = dict(c.execute("SELECT k, v FROM meta WHERE k IN ('copy_ts', 'restore_ts', 'restore_mode')").fetchall())
        self.assertEqual(meta, {"copy_ts": str(NOW + 60), "restore_ts": str(NOW + 1000), "restore_mode": "manual"})
        self.assertEqual(c.execute("SELECT actor FROM learn_events WHERE step='knowledge_restore'").fetchone()[0], "OWNER")
        self.assertTrue(os.path.exists(store2.db_path(self.root)))
        self.assertTrue(os.path.exists(path))
        self.assertNotIn("AUTO_RESTORED", store2.verify(self.root)["codes"])

    def test_auto_restore_marks_yellow(self):
        self.prepare()
        self.damage(wal=False)
        KM.write_corrupt(self.root, where="label_step")
        self.assertIn("DB_CORRUPT", store2.verify(self.root)["codes"])
        store2.restore(self.root, auto=True, clock=lambda: NOW + 2000, sleep=lambda s: None)
        self.assertIsNone(KM.read_corrupt(self.root))                          # сигнал снят после успеха
        rep = store2.verify(self.root, now=NOW + 2100)
        self.assertEqual(rep["codes"], ["AUTO_RESTORED"])
        self.assertEqual(rep["color"], "yellow")
        self.assertEqual(store2.verify(self.root, now=NOW + 2000 + 8 * 86400)["codes"], [])
        c = self.rw()
        self.assertEqual(c.execute("SELECT code, actor FROM learn_events WHERE step='knowledge_restore'").fetchone(), ("AUTO_RESTORED", "code"))

    def test_missing_file_with_marker(self):
        self.prepare()
        os.remove(store2.db_path(self.root))
        self.assertIn("DB_CORRUPT", store2.verify(self.root)["codes"])
        with self.assertRaises(store.DbCorruptError):
            store.open_rw(self.root)
        self.assertFalse(os.path.exists(store2.db_path(self.root)))
        store2.restore(self.root, sleep=lambda s: None)
        self.assertEqual(self.count_events(), 1)

    def test_wal_left_at_target_blocks(self):
        self.prepare()
        self.damage()
        real = os.rename

        def no_wal(a, b):
            if a.endswith("-wal"):
                return None                                                    # «переименование» не убрало -wal
            return real(a, b)
        with mock.patch.object(store2, "_rename", no_wal):
            with self.assertRaises(store2.RestoreError) as cm:
                store2.restore(self.root, sleep=lambda s: None)
        self.assertEqual(cm.exception.code, "WAL_LEFT")
        self.assertFalse(os.path.exists(store2.db_path(self.root)))           # копия не подставлена
        self.assertFalse(KM.restoring(self.root))                              # маркер снят
        rep = store2.verify(self.root)
        self.assertIn("RESTORE_FAILED", rep["codes"])
        self.assertEqual(rep["color"], "red")
        with self.assertRaises(store.DbCorruptError):
            store.open_rw(self.root)
        os.remove(store2.db_path(self.root) + "-wal")
        store2.restore(self.root, sleep=lambda s: None)                        # ручной повтор после уборки
        self.assertNotIn("RESTORE_FAILED", store2.verify(self.root)["codes"])

    def test_permission_error_on_rename_retried(self):
        self.prepare()
        self.damage()
        real, calls, sleeps = os.rename, [], []

        def flaky(a, b):
            calls.append(a)
            if len(calls) <= 2:
                raise PermissionError("locked")
            return real(a, b)
        with mock.patch.object(store2, "_rename", flaky):
            rep = store2.restore(self.root, sleep=sleeps.append)
        self.assertTrue(rep["ok"])
        self.assertEqual(sleeps[:2], [0.5, 1.0])

    def test_permission_error_on_replace_exhausted(self):
        self.prepare()
        self.damage()

        def locked(a, b):
            raise PermissionError("locked")
        with mock.patch.object(store2, "_replace", locked):
            with self.assertRaises(store2.RestoreError) as cm:
                store2.restore(self.root, sleep=lambda s: None)
        self.assertEqual(cm.exception.code, "RETRIES")
        self.assertFalse(KM.restoring(self.root))
        self.assertIn("RESTORE_FAILED", store2.verify(self.root)["codes"])
        store2.restore(self.root, sleep=lambda s: None)                        # без блокировки проходит
        self.assertEqual(self.count_events(), 1)

    def test_ack_timeout_and_ack(self):
        self.prepare()
        self.damage(wal=False)
        fake = 999999
        KM.register_writer(self.root, "labels", pid=fake)
        t = [NOW]

        def clock():
            return t[0]

        def sleep(s):
            t[0] += s
        with mock.patch.object(KM, "pid_alive", lambda p: True):
            with self.assertRaises(store2.RestoreError) as cm:
                store2.restore(self.root, clock=clock, sleep=sleep, ack_timeout_s=60)
            self.assertEqual(cm.exception.code, "ACK_TIMEOUT")
            self.assertGreaterEqual(t[0] - NOW, 60)
            self.assertFalse(KM.restoring(self.root))
            self.assertTrue(os.path.exists(store2.db_path(self.root)))        # повреждённый файл не тронут до подтверждений
            self.assertIn("RESTORE_FAILED", store2.verify(self.root)["codes"])

            def ack_sleep(s):
                t[0] += s
                self.assertTrue(KM.restoring(self.root))
                KM.ack(self.root, pid=fake)
            rep = store2.restore(self.root, clock=clock, sleep=ack_sleep, ack_timeout_s=60)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["waited"], [fake])
        self.assertEqual(KM.acks(self.root), set())

    def test_dead_writer_not_waited(self):
        self.prepare()
        self.damage(wal=False)
        KM.register_writer(self.root, "labels", pid=999998)
        with mock.patch.object(KM, "pid_alive", lambda p: False):
            rep = store2.restore(self.root, sleep=lambda s: None, ack_timeout_s=0.01)
        self.assertTrue(rep["ok"])
        self.assertFalse(os.path.exists(os.path.join(KM.writers_dir(self.root), "999998")))

    def test_bad_copy_and_no_copy(self):
        self.prepare()
        bad = os.path.join(self.root, "bad.sqlite")
        corrupt_file(bad)
        with self.assertRaises(store2.RestoreError) as cm:
            store2.restore(self.root, src=bad, sleep=lambda s: None)
        self.assertEqual(cm.exception.code, "COPY_BAD")
        self.assertIn(os.path.basename(self.copy), str(cm.exception))         # предлагает предыдущую
        self.assertEqual(self.count_events(), 4)                               # база не тронута
        for b in store2.list_backups(self.root):
            os.remove(b["path"])
        with self.assertRaises(store2.RestoreError) as cm:
            store2.restore(self.root, sleep=lambda s: None)
        self.assertEqual(cm.exception.code, "NO_COPY")

    def test_newest_verified_copy_chosen(self):
        self.prepare()
        newer = os.path.join(store2.backups_dir(self.root), "knowledge-20261110T000000-owner.sqlite")
        corrupt_file(newer)                                                    # подложная «свежая» копия с правильным именем
        self.damage(wal=False)
        rep = store2.restore(self.root, sleep=lambda s: None)
        self.assertEqual(rep["src"], os.path.basename(self.copy))

    def test_busy_and_stale_marker(self):
        self.prepare()
        KM.write_json(KM.restoring_path(self.root), {"pid": 999997, "ts": NOW})
        with mock.patch("tb.knowledge.store2.pid_alive", lambda p: True):
            with self.assertRaises(store2.RestoreError) as cm:
                store2.restore(self.root, sleep=lambda s: None)
        self.assertEqual(cm.exception.code, "RESTORE_BUSY")
        self.assertTrue(KM.restoring(self.root))                               # чужой маркер не снят
        with mock.patch("tb.knowledge.store2.pid_alive", lambda p: False):
            self.assertEqual(store2.recover_stale_marker(self.root), "completed")   # база цела: протокол завершается
        self.assertFalse(KM.restoring(self.root))
        self.assertIsNone(store2.recover_stale_marker(self.root))
        self.damage(wal=False)
        KM.write_json(KM.restoring_path(self.root), {"pid": 999997, "ts": NOW})
        with mock.patch("tb.knowledge.store2.pid_alive", lambda p: False):
            rep = store2.recover_stale_marker(self.root, sleep=lambda s: None)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["mode"], "auto")

    def test_restore_from_file(self):
        self.prepare()
        other = os.path.join(self.root, "manual.sqlite")
        shutil.copyfile(self.copy, other)
        os.utime(other, (NOW + 5, NOW + 5))
        self.damage(wal=False)
        rep = store2.restore(self.root, src=other, clock=lambda: NOW + 900, sleep=lambda s: None)
        self.assertEqual(rep["copy_ts"], NOW + 5)


# ---------------------------------------------------------------- классификатор

class TestIsCorruption(Base):
    def test_real_errors(self):
        path = os.path.join(self.root, "x.sqlite")
        corrupt_file(path)
        k = sqlite3.connect(path)
        try:
            with self.assertRaises(sqlite3.DatabaseError) as cm:
                k.execute("SELECT * FROM sqlite_master").fetchall()
        finally:
            k.close()
        self.assertTrue(store2.is_corruption(cm.exception))
        c = self.v2()
        store2.log_event(c, "observe", "NOT_DUE", now=NOW)
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            c.execute("DELETE FROM learn_events")                              # триггер append-only
        self.assertFalse(store2.is_corruption(cm.exception))
        TestImmutability.trial(None, c)
        with self.assertRaises(sqlite3.IntegrityError) as cm:
            TestImmutability.trial(None, c, "T-2", "RL-1")                     # UNIQUE в trials
        self.assertFalse(store2.is_corruption(cm.exception))
        k2 = sqlite3.connect(":memory:")
        k2.close()
        with self.assertRaises(sqlite3.ProgrammingError) as cm:
            k2.execute("SELECT 1")
        self.assertFalse(store2.is_corruption(cm.exception))

    def test_codes_text_and_chain(self):
        e = sqlite3.DatabaseError("database disk image is malformed")
        self.assertTrue(store2.is_corruption(e))                               # без sqlite_errorcode - по тексту
        e = sqlite3.DatabaseError("x")
        e.sqlite_errorcode = 11 | (1 << 8)                                     # SQLITE_CORRUPT_VTAB: основной код 11
        self.assertTrue(store2.is_corruption(e))
        e = sqlite3.OperationalError("database is locked")
        e.sqlite_errorcode = 5
        self.assertFalse(store2.is_corruption(e))
        self.assertFalse(store2.is_corruption(sqlite3.OperationalError("database is locked")))
        self.assertFalse(store2.is_corruption(ValueError("not a database")))
        try:
            try:
                raise sqlite3.DatabaseError("file is not a database")
            except sqlite3.DatabaseError as inner:
                raise store.SchemaMigrationError("миграция схемы не применена: %s" % inner)
        except store.SchemaMigrationError as wrapped:
            self.assertTrue(store2.is_corruption(wrapped))                     # по __context__
        self.assertTrue(store2.is_corruption(store.DbCorruptError("DB_CORRUPT")))
        self.assertFalse(store2.is_corruption(store.KnowledgeRestoring("x")))

    def test_integrity_check_failure_is_corruption(self):
        c = self.v2()
        self.assertTrue(store2.check_integrity(c))
        with mock.patch.object(store2, "_integrity", lambda conn, quick=False: ["*** in database main ***", "Page 5: btreeInitPage() returns error code 11"]):
            with self.assertRaises(store.DbCorruptError) as cm:
                store2.check_integrity(c)
        self.assertTrue(store2.is_corruption(cm.exception))


# ---------------------------------------------------------------- verify, stats, CLI, статус

class TestVerifyCli(Base):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = KC.main(list(argv), root=self.root)
        return code, out.getvalue(), err.getvalue()

    def test_verify_green_and_stats(self):
        c = self.v2()
        store2.append_row(c, "rulebook_versions", self.version_row("R-0001"))
        rep = store2.verify(self.root)
        self.assertEqual((rep["color"], rep["codes"], rep["integrity"], rep["ready"]), ("green", [], "ok", True))
        self.assertEqual(rep["tables"]["rulebook_versions"], 1)
        self.assertEqual(rep["chains"]["rulebook_versions"]["n"], 1)
        code, out, _ = self.run_cli("verify")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["color"], "green")
        code, out, _ = self.run_cli("stats")
        self.assertEqual(code, 0)
        st = json.loads(out)
        self.assertIn("learn_events", st["tables"])
        self.assertEqual([b["reason"] for b in st["backups"]], ["premigrate"])

    def test_verify_schema_mismatch_and_not_ready(self):
        c = self.v2()
        c.execute("DROP TRIGGER tr_learn_events_nd")
        rep = store2.verify(self.root)
        self.assertIn("SCHEMA_MISMATCH", rep["codes"])
        self.assertEqual(rep["color"], "red")
        self.assertEqual(self.run_cli("verify")[0], 1)
        c.execute("DELETE FROM meta WHERE k='knowledge_v2_ver'")
        rep = store2.verify(self.root)
        self.assertEqual((rep["codes"], rep["color"]), (["NOT_READY"], "yellow"))

    def test_restoring_marker(self):
        self.v2()
        KM.write_json(KM.restoring_path(self.root), {"pid": os.getpid()})
        rep = store2.verify(self.root)
        self.assertEqual((rep["codes"], rep["color"]), (["RESTORING"], "red"))
        self.assertEqual(self.run_cli("backup")[0], 1)

    def test_cli_backup_restore(self):
        c = self.v2()
        store2.log_event(c, "observe", "NOT_DUE", now=NOW)
        self.close_all()
        code, out, _ = self.run_cli("backup")
        self.assertEqual(code, 0)
        self.assertRegex(os.path.basename(json.loads(out)["path"]), r"^knowledge-\d{8}T\d{6}-owner(-\d+)?\.sqlite$")
        corrupt_file(store2.db_path(self.root))
        code, out, _ = self.run_cli("restore", "--text")
        self.assertEqual(code, 0, out)
        self.assertIn("restore:", out)
        self.assertEqual(self.run_cli("verify")[0], 0)
        code, out, err = self.run_cli("restore", "--from", os.path.join(self.root, "nope.sqlite"))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["code"], "COPY_BAD")

    def test_disable_v2(self):
        self.v2()
        self.close_all()
        os.makedirs(os.path.join(self.root, "config"), exist_ok=True)
        with open(os.path.join(self.root, "config", "learnloop.json"), "w", encoding="utf-8") as f:
            json.dump({"enabled": True}, f)
        code, _, err = self.run_cli("disable-v2")
        self.assertEqual(code, 1)
        self.assertIn("LEARNLOOP_ENABLED", err)
        self.assertTrue(KM.has_v2(self.root))
        with open(os.path.join(self.root, "config", "learnloop.json"), "w", encoding="utf-8") as f:
            f.write("{broken")                                                 # битый конфиг цикл не включает
        code, out, _ = self.run_cli("disable-v2")
        self.assertEqual(code, 0)
        self.assertFalse(KM.has_v2(self.root))
        self.assertTrue(os.path.exists(json.loads(out)["backup"]))
        corrupt_file(store2.db_path(self.root))
        c = self.rw()                                                          # без маркера - снова поведение R1 (карантин)
        self.assertEqual(store.LAST_ERROR, "DB_CORRUPT")
        self.assertIsNotNone(c)

    def test_cli_migrate(self):
        self.rw()
        self.close_all()
        code, out, _ = self.run_cli("migrate-v2a")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["knowledge_v2_ver"], 1)
        self.assertTrue(KM.has_v2(self.root))
        self.assertEqual(self.run_cli()[0], 2)

    def test_dispatcher_and_status(self):
        from tb import __main__ as M
        from tb.platform import status as ST
        self.assertEqual(M.V2["knowledge"], "tb.knowledge.cli")
        self.assertRegex(M.USAGE, r"(?m)^  knowledge ")
        st = ST.build_status(self.root)
        self.assertNotIn("knowledge", st)                                      # без маркера v2 статус как в R1
        self.v2()
        self.close_all()
        store2._STATUS_CACHE.clear()
        st = ST.build_status(self.root)
        self.assertEqual(st["knowledge"]["codes"], [])
        corrupt_file(store2.db_path(self.root))
        st = ST.build_status(self.root)                                        # дешёвые признаки сменились (кеш не мешает) ...
        self.assertEqual(st["knowledge"]["codes"], [])                         # ... но файл на месте: повреждение видит quick_check после TTL
        store2._STATUS_CACHE.clear()
        st = ST.build_status(self.root)
        self.assertIn("DB_CORRUPT", st["knowledge"]["codes"])
        self.assertEqual(st["color"], "off")                                   # платформа не запущена: цвет off, причина видна
        self.assertTrue(any("DB_CORRUPT" in r for r in st["reasons"]), st["reasons"])
        KM.write_json(KM.restoring_path(self.root), {"pid": os.getpid()})
        st = ST.build_status(self.root)
        self.assertEqual(st["knowledge"]["codes"], ["RESTORING"])


# ---------------------------------------------------------------- параллельные читатель и писатель

class TestConcurrency(Base):
    def test_reader_parallel_to_writer(self):
        c = self.v2()
        stop, errs, seen = threading.Event(), [], []

        def read():
            while not stop.is_set():
                try:
                    r = store.open_ro(self.root)
                    try:
                        seen.append(r.execute("SELECT COUNT(*) FROM learn_events").fetchone()[0])
                    finally:
                        r.close()
                except sqlite3.Error as e:
                    errs.append(e)
                    return
        th = threading.Thread(target=read)
        th.start()
        try:
            for i in range(150):
                store2.log_event(c, "observe", "NOT_DUE", now=NOW + i)
                store2.insert_rows(c, "rule_evals", [{"rec_seq": i, "rule_id": "", "ts": NOW + i, "no_event": 0, "hit": 0, "evaluated": 0, "missing": 0}])
        finally:
            stop.set()
            th.join(10)
        self.assertEqual(errs, [])
        self.assertTrue(seen)
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM learn_events").fetchone()[0], 150)


if __name__ == "__main__":
    unittest.main()
