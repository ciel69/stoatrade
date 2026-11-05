"""labels.store / service / cli при маркерах R2 (задача 4): без маркера поведение R1 не меняется; при state/knowledge.v2 open_rw не создаёт пустую базу
(DbCorruptError); при runtime/knowledge.restoring open_ro/open_rw не открывают соединение; сервис меток регистрируется писателем, ставит записи на паузу
на строке пачки, закрывает соединение и подтверждает; DbCorruptError -> сигнал runtime/knowledge.corrupt; IntegrityError (UNIQUE, триггер) повреждением не
считается; backfill/rerun: регистрация, отказ при маркере, прерывание с подтверждением; ./tb labels backup при маркерах. Сеть не используется."""
import contextlib
import io
import json
import os
import sqlite3
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.knowledge import markers as KM
from tb.knowledge import store2
from tb.labels import capture, cli, label, service, store
from tb.platform import services as S
from tb.platform import status as ST


def corrupt_file(path):
    with open(path, "wb") as f:
        f.write(b"this is not a sqlite database " * 200)


def knowledge_files(root):
    out = []
    for d in ("state", "runtime"):
        try:
            out += [n for n in os.listdir(os.path.join(root, d)) if n.startswith("knowledge.") and not n.startswith("knowledge.sqlite")]
        except OSError:
            pass
    return sorted(out)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        store.LAST_ERROR = None
        store2._STATUS_CACHE.clear()
        self.mk = H.Market()
        self.btc = H.FakeBtc(self.mk)
        self.logs = []
        self.svcs = []

    def tearDown(self):
        for s in self.svcs:
            s.close()

    def make_v2(self):
        c = store.open_rw(self.root)
        try:
            store2.migrate_v2a(c, self.root, explicit=True)
        finally:
            c.close()
        self.assertTrue(KM.has_v2(self.root))

    def mkservice(self, **kw):
        kw.setdefault("fetch", self.mk.fetch)
        kw.setdefault("btc_cache", self.btc)
        s = service.Service(self.root, log=self.logs.append, **kw)
        self.svcs.append(s)
        return s

    def restoring_on(self, pid=999990):
        KM.write_json(KM.restoring_path(self.root), {"pid": pid, "ts": H.T0})

    def raw_count(self, sql):
        k = sqlite3.connect(store.db_path(self.root))
        try:
            return k.execute(sql).fetchone()[0]
        finally:
            k.close()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv), root=self.root)
        return code, out.getvalue(), err.getvalue()

    def capture_two(self, s):
        H.write_events(self.root, [H.hot_event(0), H.hot_event(1, sym="SOLUSDT", direction="down")])
        s.tick(H.T0 + 10)
        self.assertEqual(s.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0], 2)
        return s.conn.execute("SELECT MAX(mature_ts) FROM signals").fetchone()[0] + 120


# ---------------------------------------------------------------- без маркера: R1

class TestR1Unchanged(Base):
    def test_quarantine_without_marker(self):
        path = store.db_path(self.root)
        os.makedirs(os.path.dirname(path))
        corrupt_file(path)
        c = store.open_rw(self.root)
        c.close()
        self.assertEqual(store.LAST_ERROR, "DB_CORRUPT")
        self.assertEqual(len([f for f in os.listdir(os.path.dirname(path)) if f.startswith("knowledge.corrupt-")]), 1)

    def test_service_creates_no_r2_files(self):
        s = self.mkservice()
        mature = self.capture_two(s)
        self.assertEqual(s.tick(mature), 2)
        s.close()
        self.assertEqual(knowledge_files(self.root), [])
        self.assertFalse(os.path.exists(KM.writers_dir(self.root)))
        _a, hb = S.read_hb("labels", self.root)
        self.assertNotIn("db_state", hb)

    def test_corruption_in_label_one_is_deferred_as_r1(self):
        s = self.mkservice()
        mature = self.capture_two(s)
        with mock.patch.object(label, "label_one", side_effect=sqlite3.DatabaseError("database disk image is malformed")):
            s.tick(mature)
        self.assertIsNotNone(s.conn)
        self.assertIsNone(KM.read_corrupt(self.root))
        self.assertEqual(s.last_error, "DatabaseError")

    def test_backup_r1_name(self):
        store.open_rw(self.root).close()
        code, out, _ = self.run_cli("backup")
        self.assertEqual(code, 0)
        self.assertRegex(os.path.basename(json.loads(out)["path"]), r"^knowledge-\d{8}\.sqlite$")


# ---------------------------------------------------------------- открытие при маркерах

class TestOpenV2(Base):
    def test_corrupt_raises_without_quarantine(self):
        self.make_v2()
        path = store.db_path(self.root)
        corrupt_file(path)
        with self.assertRaises(store.DbCorruptError) as cm:
            store.open_rw(self.root)
        self.assertEqual(cm.exception.code, "DB_CORRUPT")
        self.assertIsInstance(cm.exception, sqlite3.DatabaseError)
        self.assertEqual([f for f in os.listdir(os.path.dirname(path)) if f.startswith("knowledge.corrupt")], [])
        with open(path, "rb") as f:
            self.assertTrue(f.read().startswith(b"this is not a sqlite database"))   # пустая база не создана
        self.assertIsNone(store.LAST_ERROR)

    def test_missing_or_empty_file(self):
        self.make_v2()
        path = store.db_path(self.root)
        for suf in ("", "-wal", "-shm"):
            if os.path.exists(path + suf):
                os.remove(path + suf)
        with self.assertRaises(store.DbCorruptError):
            store.open_rw(self.root)
        self.assertFalse(os.path.exists(path))
        open(path, "wb").close()
        with self.assertRaises(store.DbCorruptError):
            store.open_rw(self.root)
        self.assertEqual(os.path.getsize(path), 0)

    def test_schema_migration_error_is_not_corruption(self):
        self.make_v2()
        from tb.labels import schema
        orig = schema.MIGRATIONS
        schema.MIGRATIONS = orig + [(schema.SCHEMA_VER + 1, ["CREATE TABLE broken("])]
        try:
            with self.assertRaises(store.SchemaMigrationError):
                store.open_rw(self.root)
        finally:
            schema.MIGRATIONS = orig
        store.open_rw(self.root).close()

    def test_restoring_marker_blocks_open(self):
        store.open_rw(self.root).close()
        self.restoring_on()
        for fn in (store.open_ro, store.open_rw):
            with self.assertRaises(store.KnowledgeRestoring) as cm:
                fn(self.root)
            self.assertIsInstance(cm.exception, sqlite3.OperationalError)
            self.assertFalse(store2.is_corruption(cm.exception))
        os.remove(KM.restoring_path(self.root))
        store.open_ro(self.root).close()

    def test_restoring_marker_without_db_creates_nothing(self):
        self.restoring_on()
        with self.assertRaises(store.KnowledgeRestoring):
            store.open_rw(self.root)
        self.assertFalse(os.path.exists(store.db_path(self.root)))


# ---------------------------------------------------------------- сервис меток

class TestServiceV2(Base):
    def test_registers_writer(self):
        self.make_v2()
        s = self.mkservice()
        self.assertEqual(KM.writers(self.root), [os.getpid()])
        s.tick(H.T0)
        _a, hb = S.read_hb("labels", self.root)
        self.assertEqual((hb.get("v2"), hb.get("db_state")), (True, None))
        s.close()
        self.svcs.remove(s)
        self.assertEqual(KM.writers(self.root), [])

    def test_pause_on_row_of_batch_and_resume(self):
        self.make_v2()
        s = self.mkservice()
        mature = self.capture_two(s)
        first = {"done": False}
        real = self.mk.fetch

        def fetch(*a):
            if not first["done"]:
                first["done"] = True
                self.restoring_on()                                            # маркер появился посреди разметки первой метки
            return real(*a)
        s.raw_fetch = fetch
        n = s.tick(mature)
        self.assertEqual(n, 1)                                                 # вторая метка пачки не размечена
        self.assertIsNone(s.conn)                                              # соединение закрыто
        self.assertTrue(KM.has_ack(self.root))
        self.assertEqual(s.db_state, "restoring")
        self.assertEqual(self.raw_count("SELECT COUNT(*) FROM signals WHERE status='done'"), 1)
        calls = len(self.mk.calls)
        self.assertEqual(s.tick(mature + 30), 0)                               # при маркере ничего не пишет и не ходит за свечами
        self.assertEqual(len(self.mk.calls), calls)
        self.assertEqual(self.raw_count("SELECT COUNT(*) FROM signals WHERE status='done'"), 1)
        _a, hb = S.read_hb("labels", self.root)
        self.assertEqual(hb["db_state"], "restoring")
        self.assertTrue(any("пауз" in r for r in ST.labels_reasons(hb)))
        os.remove(KM.restoring_path(self.root))
        self.assertEqual(s.tick(mature + 60), 1)                               # маркер снят: соединение открыто заново
        self.assertIsNotNone(s.conn)
        self.assertFalse(KM.has_ack(self.root))
        self.assertEqual(self.raw_count("SELECT COUNT(*) FROM signals WHERE status='done'"), 2)

    def test_marker_checked_in_capture_checkpoint_heartbeat(self):
        self.make_v2()
        s = self.mkservice()
        for step in (lambda: s.capture_step(H.T0 + 100), lambda: s.checkpoint_step(H.T0 + 10 ** 6), lambda: s.heartbeat(H.T0 + 200, force=True)):
            s.conn = s.conn or store.open_rw(self.root)
            self.restoring_on()
            step()
            self.assertIsNone(s.conn)
            self.assertTrue(KM.has_ack(self.root))
            os.remove(KM.restoring_path(self.root))
            KM.clear_acks(self.root)

    def test_protocol_with_service_ack(self):
        """Восстановление с живым сервисом меток: протокол ждёт его подтверждения (сервис закрывает соединение - на Windows иначе файл не переименовать)."""
        self.make_v2()
        s = self.mkservice()
        mature = self.capture_two(s)
        store2.make_backup(self.root, "daily")
        ticks = []

        def sleep(sec):
            ticks.append(sec)
            s.tick(mature)
        rep = store2.restore(self.root, sleep=sleep, pid=1, ack_timeout_s=30)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["waited"], [os.getpid()])
        self.assertTrue(ticks)
        self.assertEqual(s.tick(mature), 2)                                    # после снятия маркера сервис продолжил
        self.assertIsNotNone(s.conn)

    def test_protocol_times_out_without_ack(self):
        self.make_v2()
        s = self.mkservice()
        self.capture_two(s)
        store2.make_backup(self.root, "daily")
        t = [0.0]

        def clock():
            return t[0]

        def sleep(sec):
            t[0] += sec
        with self.assertRaises(store2.RestoreError) as cm:
            store2.restore(self.root, sleep=sleep, clock=clock, pid=1, ack_timeout_s=60)
        self.assertEqual(cm.exception.code, "ACK_TIMEOUT")
        self.assertFalse(KM.restoring(self.root))
        self.assertIsNotNone(s.conn)                                           # сервис не трогали; база на месте
        self.assertTrue(os.path.exists(store.db_path(self.root)))
        self.assertIn("RESTORE_FAILED", store2.verify(self.root)["codes"])

    def test_corrupt_at_start_signals_and_waits(self):
        self.make_v2()
        store2.make_backup(self.root, "daily")
        corrupt_file(store.db_path(self.root))
        s = self.mkservice()                                                   # не падает
        self.assertIsNone(s.conn)
        sig = KM.read_corrupt(self.root)
        self.assertEqual((sig["code"], sig["pid"], sig["where"]), ("DB_CORRUPT", os.getpid(), "open_rw"))
        self.assertIn("ts", sig)
        self.assertEqual(s.tick(H.T0), 0)
        _a, hb = S.read_hb("labels", self.root)
        self.assertEqual((hb["db_state"], hb["last_error"]), ("corrupt", "DB_CORRUPT"))
        self.assertTrue(any("остановлены" in r for r in ST.labels_reasons(hb)))
        self.assertFalse(os.path.exists(store.db_path(self.root) + "-wal"))
        self.assertIn("DB_CORRUPT", store2.verify(self.root)["codes"])
        store2.restore(self.root, auto=True, sleep=lambda x: s.tick(H.T0 + 15), pid=1)   # восстановление (задача 10 вызывает то же); сервис подтверждает в tick
        self.assertTrue(any("восстановление" in m for m in self.logs))
        self.assertIsNone(KM.read_corrupt(self.root))
        s.tick(H.T0 + 20)
        self.assertIsNotNone(s.conn)
        self.assertIsNone(s.db_state)

    def test_corruption_in_label_step_signals(self):
        self.make_v2()
        s = self.mkservice()
        mature = self.capture_two(s)
        defer = mock.MagicMock()
        with mock.patch.object(label, "label_one", side_effect=sqlite3.DatabaseError("database disk image is malformed")), \
                mock.patch.object(label, "defer", defer):
            self.assertEqual(s.tick(mature), 0)
        defer.assert_not_called()
        self.assertIsNone(s.conn)
        self.assertEqual(KM.read_corrupt(self.root)["where"], "label_step")
        self.assertFalse(KM.restoring(self.root))                              # восстанавливает только сервис цикла
        self.assertEqual(s.tick(mature + 10), 0)                               # ждёт: сигнал не снят

    def test_corruption_in_capture_step_signals(self):
        self.make_v2()
        s = self.mkservice()
        H.write_events(self.root, [H.hot_event(0)])
        with mock.patch.object(capture, "poll", side_effect=sqlite3.DatabaseError("file is not a database")):
            s.tick(H.T0 + 10)
        self.assertEqual(KM.read_corrupt(self.root)["where"], "capture_step")

    def test_integrity_error_is_not_corruption(self):
        self.make_v2()
        s = self.mkservice()
        mature = self.capture_two(s)
        for err in (sqlite3.IntegrityError("UNIQUE constraint failed: trials.rule_id"), sqlite3.IntegrityError("APPEND_ONLY learn_events"),
                    sqlite3.OperationalError("database is locked")):
            with mock.patch.object(label, "label_one", side_effect=err):
                s.tick(mature)
            self.assertIsNotNone(s.conn)
            self.assertIsNone(KM.read_corrupt(self.root))
            self.assertFalse(KM.restoring(self.root))
        self.assertEqual(s.conn.execute("SELECT COUNT(*) FROM signals WHERE status='pending' AND code IS NOT NULL").fetchone()[0], 2)   # отложены как в R1


# ---------------------------------------------------------------- ./tb labels backfill | rerun | backup

class TestCliV2(Base):
    def events(self, n=12):
        H.write_events(self.root, [H.hot_event(i, H.T0 + 60 * i, sym="C%dUSDT" % i) for i in range(n)])

    def test_backfill_refused_with_marker(self):
        self.make_v2()
        self.events()
        self.restoring_on()
        code, _, err = self.run_cli("backfill", "--since", "2026-11-01")
        self.assertEqual(code, 1)
        self.assertIn("knowledge.restoring", err)
        self.assertEqual(KM.writers(self.root), [])
        self.assertEqual(self.run_cli("rerun", "--policy", "P2", "--since", "2026-11-01")[0], 1)
        self.assertEqual(self.run_cli("backup")[0], 1)

    def test_backfill_registers_and_aborts_with_ack(self):
        self.make_v2()
        self.events()
        seen, real = [], capture.insert_signals

        def ins(conn, rows, *a, **kw):
            seen.append(KM.writers(self.root))
            if len(seen) == 2:
                self.restoring_on()                                            # маркер появился посреди догрузки
            return real(conn, rows, *a, **kw)
        with mock.patch.object(cli, "CHUNK", 600), mock.patch.object(capture, "insert_signals", ins):
            code, out, err = self.run_cli("backfill", "--since", "2026-11-01")
        self.assertEqual(code, 1, err)
        self.assertEqual(seen[0], [os.getpid()])                               # зарегистрирован на время работы
        self.assertEqual(len(seen), 2)                                         # следующий кусок не пишется
        rep = json.loads(out)
        self.assertEqual(rep["aborted"], "RESTORING")
        self.assertFalse(rep["done"])
        self.assertTrue(KM.has_ack(self.root))                                 # соединение закрыто, подтверждение есть
        self.assertEqual(KM.writers(self.root), [])
        os.remove(KM.restoring_path(self.root))
        code, out, err = self.run_cli("backfill", "--since", "2026-11-01")     # продолжение с места остановки
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["resumed"])
        self.assertEqual(self.raw_count("SELECT COUNT(*) FROM signals"), 12)

    def test_backfill_and_rerun_without_marker_register(self):
        self.make_v2()
        self.events(3)
        seen = []
        real = cli.backfill

        def bf(*a, **kw):
            seen.append((KM.writers(self.root), kw.get("stop") is not None))
            return real(*a, **kw)
        with mock.patch.object(cli, "backfill", bf):
            self.assertEqual(self.run_cli("backfill", "--since", "2026-11-01")[0], 0)
        self.assertEqual(seen, [([os.getpid()], True)])
        self.assertEqual(self.run_cli("rerun", "--policy", "P2", "--since", "2026-11-01")[0], 0)
        self.assertEqual(KM.writers(self.root), [])
        self.assertFalse(KM.has_ack(self.root))

    def test_backup_v2_uses_make_backup(self):
        self.make_v2()
        code, out, _ = self.run_cli("backup")
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertEqual(d["reason"], "owner")
        self.assertRegex(os.path.basename(d["path"]), r"^knowledge-\d{8}T\d{6}-owner(-\d+)?\.sqlite$")
        self.assertIn(d["path"], [b["path"] for b in store2.list_backups(self.root)])
        corrupt_file(store.db_path(self.root))
        code, _, err = self.run_cli("backup")
        self.assertEqual(code, 1)
        self.assertIn("SRC_CORRUPT", err)


if __name__ == "__main__":
    unittest.main()
