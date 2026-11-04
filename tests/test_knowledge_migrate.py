"""R2.9b (задача 24): перенос случаев и уроков в базу знаний, фасад memory при store=jsonl|sqlite, экспорт, очередь записей.

1. Прежние тесты памяти (tests/test_memory*.py) ещё раз в режиме store="sqlite" (классы-обёртки Sqlite_*): без правок самих тестов.
   Не повторяются только проверки самого формата файла JSONL (EXCLUDE: они читают/портят state/cases.jsonl напрямую).
2. Свойство 4 (совместимость фасада) для обоих режимов: одинаковый сценарий даёт одинаковые случаи, строки, уроки и проверку цепочки.
3. migrate --dry-run ничего не пишет; migrate: PLATFORM_RUNNING, перенос, сверка fold, откат при расхождении, *.pre-r29, store=sqlite,
   JSONL не появляются; параллельный писатель во время переноса пишет в базу; очередь при недоступной базе, чтение из копии, replay-spool
   (идемпотентно), CHAIN_BROKEN -> restore + replay-spool; export-jsonl после новых записей и возврат на jsonl; повторный перенос.
"""
import contextlib
import glob
import hashlib
import importlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
from _memory_helpers import NOW, mk, synthetic
from tb import paths
from tb.knowledge import cli as KCLI
from tb.knowledge import markers as KM
from tb.knowledge import memstore as MS
from tb.knowledge import migrate2 as M2
from tb.knowledge import schema2 as S2
from tb.knowledge import store2
from tb.labels import store as LST
from tb.memory import cases, chain, lessons
from tb.memory import cfg as memcfg

HERE = os.path.dirname(os.path.abspath(__file__))


@contextlib.contextmanager
def sqlite_mode():
    """store="sqlite" для временных корней тестов (база и таблицы создаются при первой записи)."""
    old = (MS._FORCE, MS._AUTOCREATE)
    MS._FORCE, MS._AUTOCREATE = "sqlite", True
    try:
        yield
    finally:
        MS._FORCE, MS._AUTOCREATE = old


# ---------------------------------------------------------------- 1. прежние тесты памяти при store=sqlite

# классы и тесты, предмет которых - сам файл JSONL (читают, дописывают или портят state/*.jsonl в обход фасада)
EXCLUDE = {
    "test_memory_cases.TestChain": "читает, портит и обрывает строки state/cases.jsonl (цепочка базы - TestSpool здесь)",
    "test_memory_cache": "кеш чтения cases.jsonl по размеру/mtime/первым 4 КБ; тесты пишут файл в обход фасада",
    "test_memory_cli.TestCli.test_verify_ok_and_broken": "дописывает в cases.jsonl в обход фасада",
    "test_memory_migrate.TestMigrate.test_idempotent": "сверяет байты cases.jsonl",
    "test_memory_migrate.TestMigrate.test_no_absolute_amounts_or_keys": "читает текст cases.jsonl",
    "test_memory_migrate.TestMigrate.test_post_lesson_text_not_migrated": "читает текст cases.jsonl",
    "test_memory_migrate.TestRulebookVer.test_existing_case_not_rewritten": "сверяет байты cases.jsonl",
    "test_memory_tick.TestSync.test_no_money_or_keys_in_case": "читает текст cases.jsonl",
    "test_memory_lessons_r2.TestCreateLabels.test_row_size_truncates_ids": "читает строки lessons.jsonl через chain.read",
    "test_memory_lessons_r2.TestConcurrentCreate.test_thread_and_process": "второй процесс без тестового хука режима (store в нём из memory.json)",
    "test_memory_diagnose.TestEdges.test_truncated_last_line": "дописывает оборванную строку в cases.jsonl",
}


class SqliteMode(object):
    def run(self, result=None):
        with sqlite_mode():
            return super(SqliteMode, self).run(result)


def _reason(*keys):
    """Причина исключения по первому совпавшему ключу: "модуль", "модуль.Класс", "модуль.Класс.тест"."""
    for k in keys:
        if k in EXCLUDE:
            return EXCLUDE[k]
    return None


def _build_variants():
    out = {}
    for path in sorted(glob.glob(os.path.join(HERE, "test_memory*.py"))):
        modname = os.path.basename(path)[:-3]
        mod = importlib.import_module(modname)
        for attr, obj in sorted(vars(mod).items()):
            if not (isinstance(obj, type) and issubclass(obj, unittest.TestCase) and obj.__module__ == mod.__name__):
                continue
            names = unittest.defaultTestLoader.getTestCaseNames(obj)
            if not names or _reason(modname, "%s.%s" % (modname, attr)):
                continue
            name = "Sqlite_%s_%s" % (modname[len("test_"):], attr)
            body = {"__module__": __name__, "__qualname__": name}
            for t in names:
                why = _reason("%s.%s.%s" % (modname, attr, t))
                if why:
                    body[t] = unittest.skip("формат JSONL: " + why)(lambda self: None)
            out[name] = type(name, (SqliteMode, obj), body)
    return out


globals().update(_build_variants())


# ---------------------------------------------------------------- общая оснастка

class Root(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbkm_")
        os.makedirs(os.path.join(self.root, "state"))
        os.makedirs(os.path.join(self.root, "config"))
        self.cfg = memcfg.load()

    def tearDown(self):
        MS.reset_caches()
        shutil.rmtree(self.root, ignore_errors=True)

    def st(self, *p):
        return os.path.join(self.root, "state", *p)

    def fill(self, n=12, with_lessons=True):
        """Случаи, update, урок мозга и урок меток с переходом - в текущем режиме."""
        for c in synthetic(n):
            cases.append_new(c, root=self.root)
        first = cases.load(self.root)[0]["case_id"]
        cases.append_update(first, {"flags": ["backfill"], "exclude_from_stats": True}, root=self.root, ts="2026-10-27T00:00:00Z")
        cases.append_update(first, {"refs": {"plan": "P-1"}}, root=self.root, ts="2026-10-27T01:00:00Z")
        if with_lessons:
            all_c = cases.load(self.root)
            lessons.create(self.root, [{"feature": "confirmed", "op": "=", "value": False}],
                           {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.5}, "code", all_c, self.cfg, NOW)
            l2 = lessons.create(self.root, [{"feature": "range_5m_pct", "op": ">=", "value": 1.5}],
                                {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.3}, "code", all_c, self.cfg, NOW + 5,
                                evidence_src="labels", evidence_extra={"n_closed": 40, "diff": -0.3, "se": 0.1, "clusters": ["k1", "k2"]})
            lessons.transition(self.root, l2["lesson_id"], "confirmed", "TRIAL_PROMOTE", NOW + 10, cfg=self.cfg)
            lessons.refresh(self.root, cases.load(self.root), self.cfg, NOW + 86400)

    def snapshot(self):
        return {"cases": cases.load(self.root, copy=True), "raw": cases.read_raw(self.root), "lessons": lessons.read_all(self.root),
                "verify": cases.verify(self.root)}

    def tree(self):
        out = {}
        for d, _, fs in os.walk(self.root):
            for f in fs:
                p = os.path.join(d, f)
                with open(p, "rb") as fh:
                    out[os.path.relpath(p, self.root)] = hashlib.sha256(fh.read()).hexdigest()
        return out

    def migrate(self, **kw):
        kw.setdefault("settle_s", 0)
        kw.setdefault("running", lambda root, now: [])
        return M2.migrate(self.root, **kw)


def strip_chain(d):
    if isinstance(d, dict):
        return dict((k, strip_chain(v)) for k, v in d.items() if k not in ("prev", "h"))
    if isinstance(d, list):
        return [strip_chain(x) for x in d]
    return d


# ---------------------------------------------------------------- 2. свойство 4: фасад одинаков в обоих режимах

class TestFacadeParity(Root):
    def run_scenario(self, sqlite):
        MS.reset_caches()
        with (sqlite_mode() if sqlite else contextlib.nullcontext()):
            self.fill()
            return self.snapshot()

    def test_same_results_jsonl_and_sqlite(self):
        a = self.run_scenario(False)
        jsonl_root = self.root
        self.root = tempfile.mkdtemp(prefix="tbkm_")
        os.makedirs(self.st())
        try:
            b = self.run_scenario(True)
            self.assertFalse(os.path.exists(self.st("cases.jsonl")))          # при sqlite JSONL не открывается
            self.assertFalse(os.path.exists(self.st("lessons.jsonl")))
            self.assertTrue(os.path.exists(self.st("knowledge.sqlite")))
        finally:
            shutil.rmtree(jsonl_root, ignore_errors=True)
        self.assertEqual(a["cases"], b["cases"])
        self.assertEqual(a["raw"], b["raw"])                                   # та же цепочка: seq, prev, h совпадают
        self.assertEqual(a["lessons"], b["lessons"])
        self.assertEqual(a["verify"], b["verify"])
        self.assertTrue(b["verify"]["ok"])
        self.assertEqual(len(a["lessons"]), 2)

    def test_store_read_from_marker_each_write(self):
        """Писатель перечитывает store (кеш по mtime): смена маркера state/memory.store корня действует на следующую запись."""
        cases.append_new(mk(0), root=self.root)
        self.assertTrue(os.path.exists(self.st("cases.jsonl")))
        self.assertEqual(MS.mode(self.root), "jsonl")
        conn = LST.open_rw(self.root)
        with LST.tx(conn):
            store2.apply_v2b(conn)
        conn.close()
        MS.write_marker(self.root, "sqlite", "test")
        os.rename(self.st("cases.jsonl"), self.st("cases.jsonl.pre-r29"))
        self.assertEqual(MS.mode_info(self.root), ("sqlite", "marker"))
        cases.append_new(mk(1), root=self.root)
        self.assertFalse(os.path.exists(self.st("cases.jsonl")))
        self.assertEqual([c["case_id"] for c in cases.load(self.root)], [mk(1)["case_id"]])

    def test_broken_marker_keeps_last_mode(self):
        MS.write_marker(self.root, "sqlite", "test")
        self.assertEqual(MS.mode(self.root), "sqlite")
        with open(MS.marker_path(self.root), "a", encoding="utf-8") as f:
            f.write("{broken")
        self.assertEqual(MS.mode(self.root), "sqlite")

    def test_marker_format_and_bad_value(self):
        p = MS.write_marker(self.root, "sqlite", "migrate", now=NOW)
        self.assertEqual(p, self.st("memory.store"))
        d = MS.read_marker(self.root)
        self.assertEqual((d["store"], d["by"]), ("sqlite", "migrate"))
        self.assertTrue(d["ts"].endswith("Z"))
        with self.assertRaises(ValueError):
            MS.write_marker(self.root, "csv", "test")
        self.assertEqual(MS.mode(self.root), "sqlite")

    def test_shipped_store_value_valid(self):
        """Ключ store в config/memory.json проекта - только переходная совместимость; значение (если есть) из допустимых."""
        self.assertIn(memcfg.load().get("store", "jsonl"), S2.STORES)

    def test_parallel_writers_sqlite(self):
        with sqlite_mode():
            errs = []

            def w(k):
                try:
                    for i in range(5):
                        cases.append_new(mk(k * 10 + i, sym="P%d" % k), root=self.root)
                except Exception as e:                              # noqa: BLE001
                    errs.append(e)
            ts = [threading.Thread(target=w, args=(k,)) for k in range(4)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            self.assertEqual(errs, [])
            v = cases.verify(self.root)
            self.assertTrue(v["ok"], v)
            self.assertEqual(v["n"], 20)
            self.assertEqual(len(cases.load(self.root)), 20)


class TestStoreSource(Root):
    """store - свойство данных корня (маркер state/memory.store), а не config/memory.json репозитория."""

    def write_cfg(self, root, store):
        with open(paths.config("memory.json", root=paths.DEFAULT_ROOT), encoding="utf-8") as f:
            d = json.load(f)
        d["store"] = store
        os.makedirs(os.path.join(root, "config"), exist_ok=True)
        with open(paths.config("memory.json", root=root), "w", encoding="utf-8") as f:
            json.dump(d, f)

    def test_temp_root_ignores_config_store(self):
        self.write_cfg(self.root, "sqlite")                                  # копия конфига со store=sqlite
        self.assertEqual(MS.mode_info(self.root), ("jsonl", "default"))
        cases.append_new(mk(0), root=self.root)
        self.assertTrue(os.path.exists(self.st("cases.jsonl")))
        self.assertTrue(cases.verify(self.root)["ok"])
        v = store2.verify(self.root)
        self.assertEqual((v["store"], v["store_src"]), ("jsonl", "default"))
        self.assertNotIn(S2.STORE_IN_CONFIG, v["codes"])

    def test_tb_root_temp_is_not_project_root(self):
        self.write_cfg(self.root, "sqlite")
        with mock.patch.dict(os.environ, {"TB_ROOT": self.root}):
            self.assertFalse(MS.is_project_root(None))
            self.assertEqual(MS.mode(None), "jsonl")

    def project(self, cfg_store, marker=None):
        """Корень проекта подменяется временным каталогом (paths.DEFAULT_ROOT), чтобы не трогать боевые state/ и config/."""
        self.write_cfg(self.root, cfg_store)
        if marker:
            MS.write_marker(self.root, marker, "test")
        return mock.patch.object(paths, "DEFAULT_ROOT", self.root)

    def test_project_root_config_compat(self):
        with self.project("sqlite"):
            MS.reset_caches()
            self.assertTrue(MS.is_project_root(self.root))
            self.assertEqual(MS.mode_info(self.root), ("sqlite", "config-compat"))
            v = store2.verify(self.root)
            self.assertEqual((v["store"], v["store_src"]), ("sqlite", "config-compat"))
            self.assertIn(S2.STORE_IN_CONFIG, v["codes"])
            self.assertEqual(v["color"], "yellow")
            self.assertTrue(any("store-marker" in r for r in v["reasons"]))

    def test_project_root_marker_wins(self):
        with self.project("sqlite", marker="jsonl"):
            MS.reset_caches()
            self.assertEqual(MS.mode_info(self.root), ("jsonl", "marker"))
            self.assertNotIn(S2.STORE_IN_CONFIG, store2.verify(self.root)["codes"])

    def test_project_root_config_jsonl_is_default(self):
        with self.project("jsonl"):
            MS.reset_caches()
            self.assertEqual(MS.mode_info(self.root), ("jsonl", "default"))

    def test_store_marker_command_idempotent(self):
        with self.project("sqlite"):
            MS.reset_caches()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = KCLI.main(["store-marker"], root=self.root)
            rep = json.loads(buf.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual((rep["store"], rep["src_before"], rep["created"]), ("sqlite", "config-compat", True))
            self.assertEqual(MS.read_marker(self.root)["by"], "store-marker")
            self.assertEqual(MS.mode_info(self.root), ("sqlite", "marker"))
            self.write_cfg(self.root, "jsonl")                              # после маркера config на store не влияет
            self.assertEqual(MS.mode_info(self.root), ("sqlite", "marker"))
            with open(MS.marker_path(self.root), "rb") as f:
                before = f.read()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = KCLI.main(["store-marker"], root=self.root)
            self.assertEqual(code, 0)
            self.assertFalse(json.loads(buf.getvalue())["created"])
            with open(MS.marker_path(self.root), "rb") as f:
                self.assertEqual(f.read(), before)

    def test_store_marker_default_root(self):
        rep = MS.ensure_marker(self.root)
        self.assertEqual((rep["store"], rep["src_before"], rep["created"], rep["ok"]), ("jsonl", "default", True, True))


# ---------------------------------------------------------------- 3. перенос

class TestDryRun(Root):
    def test_dry_run_writes_nothing(self):
        self.fill()
        before = self.tree()
        rep = M2.dry_run(self.root)
        self.assertEqual(self.tree(), before)
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(rep["cases"], 12)
        self.assertEqual(rep["lessons"], 2)
        t = rep["tables"]["case_rows"]
        self.assertEqual((t["rows"], t["broken"], t["hash_mismatch"]), (14, 0, 0))

    def test_missing_lessons_is_empty(self):
        for c in synthetic(3):
            cases.append_new(c, root=self.root)
        rep = M2.dry_run(self.root)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["lessons"], 0)
        self.assertFalse(rep["tables"]["lesson_rows"]["exists"])

    def test_report_broken_and_hash_mismatch(self):
        self.fill(4, with_lessons=False)
        with open(self.st("cases.jsonl"), "ab") as f:
            f.write(b"not json\n")
        rows, _ = chain.read(self.st("cases.jsonl"))
        lines = [chain.canon(r) for r in rows]
        d = json.loads(lines[1])
        d["decision"]["why"] = "иначе"
        lines[1] = chain.canon(d)
        with open(self.st("cases.jsonl"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\nnot json\n")
        rep = M2.dry_run(self.root)
        t = rep["tables"]["case_rows"]
        self.assertEqual(t["broken"], 1)
        self.assertEqual(t["hash_mismatch"], 1)
        self.assertEqual(t["first_bad_seq"], 2)

    def test_cli_dry_run(self):
        self.fill(3, with_lessons=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = KCLI.main(["migrate", "--dry-run"], root=self.root)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(buf.getvalue())["dry_run"])
        self.assertFalse(os.path.exists(self.st("knowledge.sqlite")))


class TestMigrate(Root):
    def test_platform_running_refused(self):
        self.fill(3, with_lessons=False)
        before = self.tree()
        with self.assertRaises(M2.MigrateError) as cm:
            M2.migrate(self.root, settle_s=0, running=lambda r, n: ["supervisor pid 1"])
        self.assertEqual(cm.exception.code, S2.PLATFORM_RUNNING)
        self.assertEqual(self.tree(), before)

    def test_platform_running_detects_files(self):
        pdir = paths.runtime("platform", root=self.root)
        os.makedirs(pdir)
        self.assertEqual(M2.platform_running(self.root), [])
        with open(os.path.join(pdir, "supervisor.lock"), "w") as f:
            json.dump({"pid": 4242}, f)
        self.assertEqual(M2.platform_running(self.root, alive=lambda p: False), [])
        self.assertTrue(M2.platform_running(self.root, alive=lambda p: True))
        os.remove(os.path.join(pdir, "supervisor.lock"))
        with open(os.path.join(pdir, "labels.hb.json"), "w") as f:
            json.dump({"pid": 4243, "ts_epoch": 1000.0}, f)
        self.assertTrue(M2.platform_running(self.root, now=1010.0, alive=lambda p: True))
        self.assertEqual(M2.platform_running(self.root, now=5000.0, alive=lambda p: True), [])         # устаревший heartbeat
        KM.register_writer(self.root, pid=4244)
        self.assertTrue(any("4244" in x for x in M2.platform_running(self.root, now=5000.0, alive=lambda p: True)))

    def test_migrate_moves_and_switches(self):
        self.fill()
        before = self.snapshot()
        rep = self.migrate()
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(MS.mode(self.root), "sqlite")
        self.assertEqual(MS.mode_info(self.root), ("sqlite", "marker"))
        self.assertEqual(MS.read_marker(self.root)["by"], "migrate")
        self.assertFalse(os.path.exists(paths.config("memory.json", root=self.root)))      # config/ не пишется
        self.assertEqual(sorted(rep["renamed"]), ["cases.jsonl.pre-r29", "lessons.jsonl.pre-r29"])
        self.assertEqual(M2.jsonl_present(self.root), [])
        self.assertTrue(os.path.isfile(os.path.join(rep["backup_files"], "cases.jsonl")))
        self.assertTrue(rep["backup_after"])
        after = self.snapshot()
        self.assertEqual(after, before)                          # чтение после переноса то же, включая seq/prev/h
        v = store2.verify(self.root)
        self.assertEqual(v["color"], "green", v)
        self.assertEqual(v["tables"]["case_rows"], 14)
        self.assertTrue(v["chains"]["case_rows"]["ok"])
        # новые записи - в базу, JSONL не появляются
        cases.append_new(mk(99, sym="NEWX"), root=self.root)
        lessons.create(self.root, [{"feature": "spread_bps", "op": ">=", "value": 5.0}], {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.2},
                       "owner", cases.load(self.root), self.cfg, NOW + 100)
        self.assertEqual(M2.jsonl_present(self.root), [])
        self.assertEqual(len(cases.load(self.root)), 13)
        self.assertEqual(len(lessons.read_all(self.root)), 3)
        self.assertTrue(cases.verify(self.root)["ok"])
        with self.assertRaises(M2.MigrateError) as cm:
            self.migrate()
        self.assertEqual(cm.exception.code, "ALREADY_SQLITE")

    def test_fold_mismatch_rolls_back(self):
        self.fill()
        before = self.tree()
        real = MS._insert

        def bad(conn, table, cols):
            if table == "case_rows" and cols["seq"] == 1:
                d = json.loads(cols["body"])
                d["decision"]["why"] = "искажено"
                cols = dict(cols, body=chain.canon(d))
            return real(conn, table, cols)
        with mock.patch.object(MS, "_insert", bad):
            with self.assertRaises(M2.MigrateError) as cm:
                self.migrate()
        self.assertEqual(cm.exception.code, S2.FOLD_MISMATCH)
        self.assertEqual(MS.mode(self.root), "jsonl")
        self.assertTrue(os.path.exists(self.st("cases.jsonl")))
        self.assertTrue(os.path.exists(self.st("lessons.jsonl")))
        self.assertFalse(os.path.exists(self.st("cases.jsonl.pre-r29")))
        self.assertFalse(os.path.exists(paths.config("memory.json", root=self.root)))
        self.assertFalse(os.path.exists(MS.marker_path(self.root)))
        conn = LST.open_ro(self.root)
        try:
            self.assertFalse(store2.ready_v2b(conn))                 # DDL откатилась вместе со строками
        finally:
            conn.close()
        changed = set(k for k in set(self.tree()) | set(before) if self.tree().get(k) != before.get(k))
        self.assertTrue(all(k.startswith(os.path.join("state", "backups")) or k.startswith(os.path.join("state", "knowledge.sqlite")) for k in changed), changed)

    def test_failure_after_rename_restores_files(self):
        self.fill(3)
        with mock.patch.object(MS, "write_marker", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.migrate()
        self.assertEqual(MS.mode(self.root), "jsonl")
        self.assertTrue(os.path.exists(self.st("cases.jsonl")))
        self.assertFalse(os.path.exists(self.st("cases.jsonl.pre-r29")))
        self.assertFalse(os.path.exists(MS.marker_path(self.root)))

    def test_failure_after_marker_rolls_marker_back(self):
        """Сбой после записи маркера (до COMMIT): маркер возвращается к прежнему состоянию (не было - удаляется)."""
        self.fill(3)
        with mock.patch.object(MS, "mode", return_value="jsonl"):          # маркер записан, но не читается
            with self.assertRaises(M2.MigrateError) as cm:
                self.migrate()
        self.assertEqual(cm.exception.code, "STORE_NOT_SET")
        self.assertFalse(os.path.exists(MS.marker_path(self.root)))
        self.assertEqual(MS.mode(self.root), "jsonl")
        self.assertTrue(os.path.exists(self.st("cases.jsonl")))

    def test_parallel_writer_during_migrate_goes_to_db(self):
        """Писатель, вставший в очередь на cases.lock во время переноса, после переключения пишет в базу, а не в JSONL."""
        self.fill(3, with_lessons=False)
        real = MS.write_marker
        started, errs = threading.Event(), []

        def writer():
            started.set()
            try:
                cases.append_new(mk(77, sym="PARX"), root=self.root)
            except Exception as e:                                   # noqa: BLE001
                errs.append(e)
        th = threading.Thread(target=writer)

        def slow_set(root, value, by, now=None):
            p = real(root, value, by, now=now)
            if value == "sqlite" and not th.is_alive() and not started.is_set():
                th.start()
                started.wait(5)
            return p
        with mock.patch.object(MS, "write_marker", slow_set):
            rep = self.migrate()
        th.join(15)
        self.assertEqual(errs, [])
        self.assertTrue(rep["ok"])
        self.assertEqual(M2.jsonl_present(self.root), [])
        self.assertIn(mk(77, sym="PARX")["case_id"], [c["case_id"] for c in cases.load(self.root)])
        self.assertTrue(cases.verify(self.root)["ok"])

    def test_jsonl_appeared_reported(self):
        self.fill(2, with_lessons=False)

        def sneaky(_s):
            with open(self.st("lessons.jsonl"), "w") as f:
                f.write("")
        rep = self.migrate(settle_s=0.01, sleep=sneaky)
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["code"], "JSONL_APPEARED")
        self.assertEqual(rep["jsonl_after_settle"], ["lessons.jsonl"])

    def test_pre_exists_refused(self):
        self.fill(2, with_lessons=False)
        open(self.st("cases.jsonl.pre-r29"), "w").close()
        with self.assertRaises(M2.MigrateError) as cm:
            self.migrate()
        self.assertEqual(cm.exception.code, "PRE_EXISTS")

    def test_dup_rev_conflict_blocks(self):
        self.fill(2, with_lessons=False)
        rows, _ = chain.read(self.st("cases.jsonl"))
        dup = dict(rows[-1])
        dup.pop("seq"), dup.pop("prev"), dup.pop("h")
        dup["decision"] = {"action": "skip", "why": "другое"}
        chain.append(self.st("cases.jsonl"), self.st("cases.lock"), dup)
        self.assertIn("DUP_REV:case_rows", M2.dry_run(self.root)["blocking"])
        with self.assertRaises(M2.MigrateError) as cm:
            self.migrate()
        self.assertEqual(cm.exception.code, "BLOCKED")
        self.assertEqual(MS.mode(self.root), "jsonl")

    def test_rules_untouched(self):
        """Таблицы цикла (версии rulebook) переносом и возвратом не меняются."""
        self.fill(2, with_lessons=False)
        conn = LST.open_rw(self.root)
        store2.migrate_v2a(conn, self.root, explicit=True, backup=False)
        store2.append_row(conn, "rulebook_versions", {"ver": "R-0001", "parent": None, "reason_code": "INIT", "rule_ids": [], "ts": "2026-10-28T00:00:00Z"})
        conn.close()

        def rb():
            c = LST.open_ro(self.root)
            try:
                return c.execute("SELECT * FROM rulebook_versions ORDER BY seq").fetchall()
            finally:
                c.close()
        r0 = rb()
        self.migrate()
        M2.export_jsonl(self.root, running=lambda r, n: [])
        self.assertEqual(rb(), r0)
        self.assertEqual(MS.mode(self.root), "jsonl")


class TestExport(Root):
    def test_export_after_new_records_and_return(self):
        self.fill()
        self.migrate()
        cases.append_new(mk(50, sym="AFTR"), root=self.root)
        cases.append_update(mk(50, sym="AFTR")["case_id"], {"flags": ["artifact"]}, root=self.root, ts="2026-10-29T00:00:00Z")
        lessons.create(self.root, [{"feature": "spread_bps", "op": ">=", "value": 5.0}], {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.2},
                       "owner", cases.load(self.root), self.cfg, NOW + 100)
        before = self.snapshot()
        # проверочный экспорт в каталог: store не меняется
        out = os.path.join(self.root, "exp")
        rep = M2.export_jsonl(self.root, out_dir=out)
        self.assertTrue(rep["ok"])
        self.assertEqual(MS.mode(self.root), "sqlite")
        self.assertTrue(chain.verify(os.path.join(out, "cases.jsonl"))["ok"])
        # возврат: только при остановленной платформе
        with self.assertRaises(M2.MigrateError) as cm:
            M2.export_jsonl(self.root, running=lambda r, n: ["supervisor pid 1"])
        self.assertEqual(cm.exception.code, S2.PLATFORM_RUNNING)
        rep = M2.export_jsonl(self.root, running=lambda r, n: [])
        self.assertTrue(rep["ok"])
        self.assertEqual(MS.mode(self.root), "jsonl")
        self.assertTrue(os.path.exists(self.st("cases.jsonl")))
        after = self.snapshot()
        self.assertEqual(after["cases"], before["cases"])
        self.assertEqual(after["lessons"], before["lessons"])
        self.assertTrue(after["verify"]["ok"])
        self.assertIn(mk(50, sym="AFTR")["case_id"], [c["case_id"] for c in after["cases"]])
        self.assertEqual(rep["tables"]["case_rows"]["items"], 13)
        # после возврата прежний код пишет в JSONL, повторный перенос дописывает только новые строки
        cases.append_new(mk(51, sym="BACK"), root=self.root)
        os.remove(self.st("cases.jsonl.pre-r29"))
        os.remove(self.st("lessons.jsonl.pre-r29"))
        rep = self.migrate()
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(rep["tables"]["case_rows"]["inserted"], 1)
        self.assertEqual(len(cases.load(self.root)), 14)
        self.assertTrue(cases.verify(self.root)["ok"])

    def test_export_refuses_existing_files_and_spool(self):
        self.fill(2, with_lessons=False)
        self.migrate()
        open(self.st("cases.jsonl"), "w").close()
        with self.assertRaises(M2.MigrateError) as cm:
            M2.export_jsonl(self.root, running=lambda r, n: [])
        self.assertEqual(cm.exception.code, "FILES_EXIST")
        os.remove(self.st("cases.jsonl"))
        with open(store2.spool_path(self.root), "w") as f:
            f.write("{}\n")
        with self.assertRaises(M2.MigrateError) as cm:
            M2.export_jsonl(self.root, running=lambda r, n: [])
        self.assertEqual(cm.exception.code, S2.SPOOL_PENDING)
        self.assertEqual(MS.mode(self.root), "sqlite")


class TestSpool(Root):
    def setUp(self):
        Root.setUp(self)
        self.fill(4)
        self.migrate()

    def restoring(self, on):
        p = KM.restoring_path(self.root)
        if on:
            KM.write_json(p, {"pid": 0, "ts": 0})
        else:
            os.remove(p)

    def test_spool_when_db_unavailable_then_replay(self):
        store2.make_backup(self.root, "owner")
        n0 = len(cases.load(self.root))
        self.restoring(True)
        row = cases.append_new(mk(60, sym="SPOL"), root=self.root)
        self.assertEqual(row["case_id"], mk(60, sym="SPOL")["case_id"])
        cases.append_update(row["case_id"], {"flags": ["orphan"]}, root=self.root, ts="2026-10-30T00:00:00Z")
        lessons.create(self.root, [{"feature": "spread_bps", "op": ">=", "value": 5.0}], {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.2},
                       "owner", cases.load(self.root), self.cfg, NOW + 100)
        self.assertTrue(store2.spool_pending(self.root))
        self.assertEqual(M2.jsonl_present(self.root), [])
        # чтение - из последней копии плюс очередь
        got = cases.get(row["case_id"], root=self.root)
        self.assertEqual(got["flags"], ["orphan"])
        self.assertEqual(len(cases.load(self.root)), n0 + 1)
        with open(store2.spool_path(self.root), encoding="utf-8") as f:
            line = json.loads(f.readline())
        self.assertEqual(line["table"], "case_rows")
        self.assertTrue({"seq", "case_id", "rev", "kind", "ts", "body", "prev", "h"} <= set(line))
        self.restoring(False)
        self.assertIn(S2.SPOOL_PENDING, store2.verify(self.root)["codes"])
        rep = MS.replay(self.root)
        self.assertEqual((rep["applied"], rep["conflicts"], rep["left"]), (3, 0, 0))
        self.assertFalse(store2.spool_pending(self.root))
        self.assertEqual(cases.get(row["case_id"], root=self.root)["flags"], ["orphan"])
        self.assertTrue(cases.verify(self.root)["ok"])
        self.assertEqual(store2.verify(self.root)["color"], "green")
        self.assertEqual(MS.replay(self.root)["applied"], 0)                  # повтор ничего не делает

    def test_replay_idempotent_by_key(self):
        self.restoring(True)
        cases.append_new(mk(61, sym="IDEM"), root=self.root)
        self.restoring(False)
        with open(store2.spool_path(self.root), encoding="utf-8") as f:
            line = f.read()
        with open(store2.spool_path(self.root), "a", encoding="utf-8") as f:
            f.write(line)                                                     # та же строка дважды
        rep = MS.replay(self.root)
        self.assertEqual((rep["applied"], rep["skipped"], rep["conflicts"]), (1, 1, 0))
        self.assertEqual(sum(1 for c in cases.load(self.root) if c["case_id"] == mk(61, sym="IDEM")["case_id"]), 1)

    def test_writer_replays_own_table_first(self):
        self.restoring(True)
        cases.append_new(mk(62, sym="AUTO"), root=self.root)
        self.restoring(False)
        cases.append_new(mk(63, sym="AUTO"), root=self.root)                 # запись сначала применяет очередь своей таблицы
        self.assertFalse(store2.spool_pending(self.root))
        ids = [r["case_id"] for r in cases.read_raw(self.root)[0]]
        self.assertLess(ids.index(mk(62, sym="AUTO")["case_id"]), ids.index(mk(63, sym="AUTO")["case_id"]))
        self.assertTrue(cases.verify(self.root)["ok"])

    def test_cli_replay_spool(self):
        self.restoring(True)
        cases.append_new(mk(64, sym="CLIX"), root=self.root)
        self.restoring(False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = KCLI.main(["replay-spool"], root=self.root)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buf.getvalue())["applied"], 1)

    def test_chain_broken_restore_and_replay(self):
        good = store2.make_backup(self.root, "owner")
        conn = sqlite3.connect(LST.db_path(self.root), isolation_level=None)
        conn.execute("DROP TRIGGER tr_case_rows_nu")
        conn.execute("UPDATE case_rows SET body = replace(body, '\"plan\":\"P-1\"', '\"plan\":\"P-2\"') WHERE seq = (SELECT MAX(seq) FROM case_rows)")
        conn.close()
        v = store2.verify(self.root)
        self.assertIn(S2.CHAIN_BROKEN, v["codes"])
        self.assertEqual(v["color"], "red")
        row = cases.append_new(mk(65, sym="BRKN"), root=self.root)            # запись при разорванной цепочке - в очередь
        self.assertTrue(store2.spool_pending(self.root))
        store2.restore(self.root, src=good, ack_timeout_s=0.1)
        rep = MS.replay(self.root)
        self.assertEqual(rep["applied"], 1)
        self.assertEqual(store2.verify(self.root)["color"], "green")
        self.assertIn(row["case_id"], [c["case_id"] for c in cases.load(self.root)])
        self.assertTrue(cases.verify(self.root)["ok"])

    def test_bad_copy_with_broken_chain_rejected(self):
        conn = sqlite3.connect(LST.db_path(self.root), isolation_level=None)
        conn.execute("DROP TRIGGER tr_lesson_rows_nu")
        conn.execute("UPDATE lesson_rows SET h = '0000000000000000' WHERE seq = 1")
        conn.close()
        ok, code, _d = store2.check_copy(LST.db_path(self.root))
        self.assertFalse(ok)
        self.assertEqual(code, S2.CHAIN_BROKEN)


if __name__ == "__main__":
    unittest.main()
