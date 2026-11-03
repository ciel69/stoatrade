"""tb.labels.store/schema (R1.3, задача 6): миграция и откат (свойство 11), читатель параллельно писателю, backup, порча файла, папка синхронизации, скорость агрегата."""
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest

import _bootstrap  # noqa: F401
from tb.labels import schema, store


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tblabels_")
        self.addCleanup(shutil.rmtree, self.root, True)
        store.LAST_ERROR = None
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


class TestOpen(Base):
    def test_pragmas_and_schema(self):
        c = self.rw()
        self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(c.execute("PRAGMA synchronous").fetchone()[0], 1)             # NORMAL
        self.assertEqual(c.execute("PRAGMA journal_size_limit").fetchone()[0], 64 * 1024 * 1024)
        self.assertGreaterEqual(c.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
        self.assertIsNone(c.isolation_level)
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
        for t in ("meta", "cursor", "signals", "outcomes", "policy_runs"):
            self.assertIn(t, names)
        for ix in ("ix_sig_t0", "ix_sig_sym", "ix_sig_type", "ix_sig_due", "ix_pr_pol", "ix_pr_type"):
            self.assertIn(ix, names)
        self.assertEqual(c.execute("SELECT v FROM meta WHERE k=?", (schema.META_VER_KEY,)).fetchone()[0], "1")

    def test_reopen_idempotent(self):
        self.rw().close()
        c = self.rw()
        self.assertEqual(c.execute("SELECT count(*) FROM meta").fetchone()[0], 1)

    def test_open_ro_missing_is_none(self):
        self.assertIsNone(store.open_ro(self.root))

    def test_open_ro_query_only(self):
        self.rw()
        r = store.open_ro(self.root)
        self.conns.append(r)
        self.assertEqual(r.execute("PRAGMA query_only").fetchone()[0], 1)
        with self.assertRaises(sqlite3.OperationalError):
            r.execute("INSERT INTO meta(k, v) VALUES('x', 'y')")

    def test_tx_commit_and_rollback(self):
        c = self.rw()
        with store.tx(c):
            c.execute("INSERT INTO meta(k, v) VALUES('a', '1')")
        with self.assertRaises(RuntimeError):
            with store.tx(c):
                c.execute("INSERT INTO meta(k, v) VALUES('b', '2')")
                raise RuntimeError("boom")
        self.assertEqual([r[0] for r in c.execute("SELECT k FROM meta WHERE k IN ('a','b')")], ["a"])
        self.assertFalse(c.in_transaction)


class TestMigration(Base):
    def test_failed_migration_rolls_back(self):
        """Свойство 11: сбой миграции оставляет прежнюю схему, код SCHEMA_MIGRATION."""
        path = store.db_path(self.root)
        os.makedirs(os.path.dirname(path))
        c = sqlite3.connect(path, isolation_level=None)
        self.conns.append(c)
        store.migrate(c)                                                       # v1
        bad = list(schema.MIGRATIONS) + [(2, ["CREATE TABLE t2(a)", "INSERT INTO meta(k, v) VALUES('labels_schema_ver', 'x')", "CREATE TABLE nope(", "CREATE TABLE t3(a)"])]
        with self.assertRaises(store.SchemaMigrationError) as cm:
            store.migrate(c, bad)
        self.assertEqual(cm.exception.code, "SCHEMA_MIGRATION")
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
        self.assertNotIn("t2", names)
        self.assertNotIn("t3", names)
        self.assertEqual(c.execute("SELECT v FROM meta WHERE k=?", (schema.META_VER_KEY,)).fetchone()[0], "1")
        self.assertFalse(c.in_transaction)

    def test_good_migration_step(self):
        c = self.rw()
        ok = list(schema.MIGRATIONS) + [(2, ["CREATE TABLE t2(a)"])]
        self.assertEqual(store.migrate(c, ok), 2)
        self.assertEqual(c.execute("SELECT v FROM meta WHERE k=?", (schema.META_VER_KEY,)).fetchone()[0], "2")
        self.assertEqual(store.migrate(c, ok), 2)                              # повтор ничего не делает

    def test_open_rw_failed_migration_keeps_file(self):
        orig = schema.MIGRATIONS
        schema.MIGRATIONS = [(1, ["CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)", "CREATE TABLE broken("])]
        try:
            with self.assertRaises(store.SchemaMigrationError):
                store.open_rw(self.root)
        finally:
            schema.MIGRATIONS = orig
        self.assertTrue(os.path.exists(store.db_path(self.root)))              # файл не считается повреждённым
        self.assertEqual([f for f in os.listdir(os.path.dirname(store.db_path(self.root))) if "corrupt" in f], [])
        c = self.rw()                                                          # нормальная миграция после сбоя проходит
        self.assertEqual(c.execute("SELECT v FROM meta WHERE k=?", (schema.META_VER_KEY,)).fetchone()[0], "1")


class TestConcurrency(Base):
    def test_reader_parallel_to_writer(self):
        w = self.rw()
        r = store.open_ro(self.root)
        self.conns.append(r)
        stop, errs, seen = threading.Event(), [], []

        def read():
            rc = sqlite3.connect(store.db_path(self.root), isolation_level=None, timeout=5)
            rc.execute("PRAGMA query_only=ON")
            try:
                while not stop.is_set():
                    seen.append(rc.execute("SELECT count(*) FROM meta").fetchone()[0])
            except sqlite3.Error as e:
                errs.append(e)
            finally:
                rc.close()
        th = threading.Thread(target=read)
        th.start()
        try:
            for i in range(200):
                with store.tx(w):
                    w.execute("INSERT INTO meta(k, v) VALUES(?, ?)", ("k%d" % i, "v"))
        finally:
            stop.set()
            th.join(10)
        self.assertEqual(errs, [])
        self.assertTrue(seen)
        self.assertEqual(seen, sorted(seen))                                   # читатель видит только целые коммиты, монотонно
        self.assertEqual(r.execute("SELECT count(*) FROM meta").fetchone()[0], 201)


class TestBackup(Base):
    def test_backup_copy(self):
        c = self.rw()
        with store.tx(c):
            c.execute("INSERT INTO meta(k, v) VALUES('x', 'y')")
        dst = store.backup(self.root, today="20261102")
        self.assertTrue(dst.endswith(os.path.join("backups", "knowledge-20261102.sqlite")))
        b = sqlite3.connect(dst)
        try:
            self.assertEqual(b.execute("SELECT v FROM meta WHERE k='x'").fetchone()[0], "y")
        finally:
            b.close()
        self.assertEqual(c.execute("SELECT count(*) FROM meta").fetchone()[0], 2)  # сервис (писатель) не остановлен
        with store.tx(c):
            c.execute("INSERT INTO meta(k, v) VALUES('z', 'y')")

    def test_retry_on_permission_error(self):
        self.rw()
        calls, orig = [], store._copy

        def flaky(src, dst):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError("busy")
            return orig(src, dst)
        store._copy = flaky
        try:
            dst = store.backup(self.root, sleep=lambda s: None, today="20261102")
        finally:
            store._copy = orig
        self.assertEqual(len(calls), 3)
        self.assertTrue(os.path.exists(dst))

    def test_gives_up_after_three(self):
        self.rw()
        calls, orig = [], store._copy

        def always(src, dst):
            calls.append(1)
            raise PermissionError("busy")
        store._copy = always
        try:
            with self.assertRaises(PermissionError):
                store.backup(self.root, sleep=lambda s: None, today="20261102")
        finally:
            store._copy = orig
        self.assertEqual(len(calls), 3)


class TestCorrupt(Base):
    def test_corrupt_file_renamed_and_recreated(self):
        path = store.db_path(self.root)
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as f:
            f.write(b"this is not a sqlite database " * 200)
        c = self.rw()
        self.assertEqual(store.LAST_ERROR, "DB_CORRUPT")
        sib = [f for f in os.listdir(os.path.dirname(path)) if f.startswith("knowledge.corrupt-") and f.endswith(".sqlite")]
        self.assertEqual(len(sib), 1)
        self.assertEqual(c.execute("SELECT v FROM meta WHERE k=?", (schema.META_VER_KEY,)).fetchone()[0], "1")


class TestSyncDir(unittest.TestCase):
    def test_markers(self):
        for p in ("C:\\Users\\a\\OneDrive\\proj", "/home/u/Dropbox/x", "/Users/u/iCloud Drive/x", "D:\\GOOGLE DRIVE\\p", "\\\\srv\\share\\p"):
            self.assertEqual(store.sync_dir_warning(p), "W_LABELS_SYNC_DIR", p)

    def test_plain_path_none(self):
        d = tempfile.mkdtemp(prefix="tblabels_")
        self.addCleanup(shutil.rmtree, d, True)
        if any(m in d.lower() for m in store.SYNC_MARKERS):
            self.skipTest("временный каталог сам в папке синхронизации")
        self.assertIsNone(store.sync_dir_warning(d))


class TestSpeed(Base):
    def _fill(self, n):
        c = self.rw()
        pols = [("P2", 1), ("P4", 1), ("P5", 1), ("P6", 1)]
        now = int(time.time())
        with store.tx(c):
            c.executemany(
                "INSERT INTO policy_runs(key, policy_id, policy_ver, geometry_ver, ctx_ver, t0, sym, type, side, filled, r_net) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ((("K%d" % i), pols[i % 4][0], pols[i % 4][1], "g1", "c1", now - (i % (40 * 86400)), "BTC", "range_5m", "long", 1, ((i * 7919) % 200 - 90) / 100.0) for i in range(n)))
        return c, now

    def _agg(self, c, now):
        t = time.perf_counter()
        row = c.execute("SELECT count(*), avg(r_net), avg(r_net > 0) FROM policy_runs WHERE policy_id=? AND policy_ver=? AND t0>=?", ("P2", 1, now - 30 * 86400)).fetchone()
        return row, (time.perf_counter() - t) * 1000

    def test_aggregate_100k(self):
        c, now = self._fill(100_000)
        self._agg(c, now)                                                      # прогрев страниц
        row, ms = self._agg(c, now)
        self.assertGreater(row[0], 10_000)
        self.assertLessEqual(ms, 150, "агрегат 100 000 строк: %.0f мс" % ms)

    @unittest.skipUnless(os.environ.get("TB_SLOW_TESTS") == "1", "TB_SLOW_TESTS=1")
    def test_aggregate_1m(self):
        c, now = self._fill(1_000_000)
        self._agg(c, now)
        row, ms = self._agg(c, now)
        self.assertGreater(row[0], 100_000)
        self.assertLessEqual(ms, 300, "агрегат 1 000 000 строк: %.0f мс" % ms)


if __name__ == "__main__":
    unittest.main()
