"""R2.9b (задача 26): переключение боевых данных на SQLite в порядке RUNBOOK на фикстуре временного корня.

Фикстура: копия структуры state/ - cases.jsonl, lessons.jsonl (через фасад в режиме jsonl), knowledge.sqlite с таблицами v2a, маркером
state/knowledge.v2 и метками (signals). Порядок: dry-run (ничего не пишет) -> migrate при работающей платформе (PLATFORM_RUNNING) ->
остановка -> migrate (с индексом поиска похожих) -> index-similar (идемпотентно) -> verify (knowledge и memory) -> чтение досье, уроков,
диагноза, страниц «Учится ли мозг» и «Память» через фасад (те же ответы, что до переноса) -> новые записи -> export-jsonl (проверочный и возврат)
-> повторный migrate. При store="sqlite" ни один читатель не открывает cases.jsonl / lessons.jsonl (перехват open), ключи кешей читателей
меняются от записей в базу, периодический писатель памяти не запускает memory.migrate каждую минуту.
"""
import builtins
import contextlib
import copy
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
from _memory_helpers import NOW, mk, synthetic
from test_knowledge_similar import insert_signals, sig_row
from tb import paths
from tb.dash2 import memory_ops
from tb.knowledge import cli as KCLI
from tb.knowledge import markers as KM
from tb.knowledge import memstore as MS
from tb.knowledge import migrate2 as M2
from tb.knowledge import schema2 as S2
from tb.knowledge import similar as KS
from tb.knowledge import store2
from tb.labels import store as LST
from tb.learn import load as LL
from tb.learn import memory_vs_base as MVB
from tb.learn import report as LR
from tb.memory import blocks, cases, diagnose, lessons, tick
from tb.memory import cfg as memcfg
from tb.memory import cli as MCLI

JSONL = tuple(S2.MEM_FILE.values())


@contextlib.contextmanager
def no_jsonl_open(test):
    """Перехват open (builtins и io): любое открытие cases.jsonl / lessons.jsonl (и их .export.tmp вне экспорта) - провал теста."""
    seen = []
    real_open, real_io_open = builtins.open, io.open

    def check(f):
        name = os.path.basename(f) if isinstance(f, (str, bytes, os.PathLike)) else ""
        name = os.fsdecode(name) if name else ""
        if name in JSONL:
            seen.append(os.fspath(f))

    def fake_open(f, *a, **kw):
        check(f)
        return real_open(f, *a, **kw)

    def fake_io_open(f, *a, **kw):
        check(f)
        return real_io_open(f, *a, **kw)
    with mock.patch.object(builtins, "open", fake_open), mock.patch.object(io, "open", fake_io_open):
        yield seen
    test.assertEqual(seen, [], "прямое чтение JSONL при store=sqlite: %s" % seen)


def run_cli(main, argv, root):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        code = main(argv, root=root)
    out = buf.getvalue().strip().splitlines()
    return code, (json.loads(out[-1]) if out else None)


class Cutover(unittest.TestCase):
    def setUp(self):
        MS.reset_caches()
        self.root = tempfile.mkdtemp(prefix="tbcut_")
        for d in ("state", "config", "runtime"):
            os.makedirs(os.path.join(self.root, d))
        self.cfg = memcfg.load()
        self.build_fixture()

    def tearDown(self):
        MS.reset_caches()
        MVB.clear()
        shutil.rmtree(self.root, ignore_errors=True)

    def st(self, *p):
        return os.path.join(self.root, "state", *p)

    # ---------------------------------------------------------------- фикстура «боевого» state/

    def build_fixture(self):
        for c in synthetic(16):
            c = copy.deepcopy(c)
            c["src"]["mode"] = "paper"
            cases.append_new(c, root=self.root)
        first = cases.load(self.root)[0]["case_id"]
        cases.append_update(first, {"flags": ["backfill"]}, root=self.root, ts="2026-10-27T00:00:00Z")
        all_c = cases.load(self.root)
        lessons.create(self.root, [{"feature": "confirmed", "op": "=", "value": False}], {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.5},
                       "code", all_c, self.cfg, NOW)
        l2 = lessons.create(self.root, [{"feature": "range_5m_pct", "op": ">=", "value": 1.5}], {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.3},
                            "code", all_c, self.cfg, NOW + 5, evidence_src="labels", evidence_extra={"n_closed": 40, "diff": -0.3, "se": 0.1, "clusters": ["k1"]})
        lessons.transition(self.root, l2["lesson_id"], "confirmed", "TRIAL_PROMOTE", NOW + 10, cfg=self.cfg)
        conn = LST.open_rw(self.root)
        try:
            store2.migrate_v2a(conn, self.root, explicit=True, backup=False)
            store2.append_row(conn, "rulebook_versions", {"ver": "R-0001", "parent": None, "reason_code": "INIT", "rule_ids": [], "ts": "2026-10-28T00:00:00Z"})
            insert_signals(conn, [sig_row(i, NOW - 3600 * i, side="long" if i % 2 else "short", feat={"coin_class": "meme"}, ev={"range_5m_pct": 1.0 + i / 10.0})
                                  for i in range(30)])
        finally:
            conn.close()
        self.assertTrue(KM.has_v2(self.root))

    # ---------------------------------------------------------------- чтения, которые должны совпасть до и после

    def reads(self):
        now = NOW + 86400
        rep = LR.build(self.root, mode="paper", now=now)
        return {
            "cases": cases.load(self.root, copy=True),
            "raw": cases.read_raw(self.root),
            "lessons": lessons.read_all(self.root),
            "lessons_raw": lessons.read_raw(self.root),
            "verify_cases": cases.verify(self.root),
            "verify_lessons": lessons.verify(self.root),
            "diagnose": diagnose.diagnose(self.root, self.cfg, now),
            "brief": blocks.brief_block(self.root, now, "start", mode="paper", cfg=self.cfg),
            "list_lessons": blocks.lessons_payload(self.root, None, 10, now, self.cfg),
            "page_lessons": rep["blocks"]["lessons"],
            "page_sources": rep["meta"]["sources"]["cases"],
            "dash_cases": memory_ops.memory_cases(self.root, now=now),
            "dash_lessons": memory_ops.memory_lessons(self.root, now=now),
        }

    def assertSameReads(self, a, b):
        for k in a:
            self.assertEqual(a[k], b[k], k)

    def running_lock(self, on):
        pdir = paths.runtime("platform", root=self.root)
        os.makedirs(pdir, exist_ok=True)
        p = os.path.join(pdir, "supervisor.lock")
        if on:
            with open(p, "w") as f:
                json.dump({"pid": os.getppid()}, f)               # живой чужой процесс: «супервизор работает»
        elif os.path.exists(p):
            os.remove(p)

    # ---------------------------------------------------------------- полный порядок RUNBOOK

    def test_full_order(self):
        before = self.reads()
        self.assertEqual(len(before["cases"]), 16)
        self.assertEqual(len(before["lessons"]), 2)
        self.assertTrue(before["verify_cases"]["ok"] and before["verify_lessons"]["ok"])

        # 1. dry-run: отчёт, ничего не пишет (в том числе индекс)
        code, dr = run_cli(KCLI.main, ["migrate", "--dry-run"], self.root)
        self.assertEqual(code, 0, dr)
        self.assertEqual((dr["cases"], dr["lessons"], dr["store"]), (16, 2, "jsonl"))
        self.assertIs(dr["similar_index"], False)
        self.assertTrue(dr["db"]["v2a"])
        self.assertFalse(dr["db"]["v2b"])

        # 2. платформа работает -> PLATFORM_RUNNING, ничего не изменено
        self.running_lock(True)
        self.assertTrue(M2.platform_running(self.root))
        code, rep = run_cli(KCLI.main, ["migrate", "--settle", "0"], self.root)
        self.assertEqual((code, rep["code"]), (1, S2.PLATFORM_RUNNING))
        code, rep = run_cli(KCLI.main, ["index-similar"], self.root)
        self.assertEqual((code, rep["code"]), (1, S2.PLATFORM_RUNNING))
        self.assertEqual(MS.mode(self.root), "jsonl")
        self.assertEqual(sorted(M2.jsonl_present(self.root)), sorted(JSONL))

        # 3. остановка платформы (./tb down) -> migrate: перенос, индекс похожих, копии
        self.running_lock(False)
        code, rep = run_cli(KCLI.main, ["migrate", "--settle", "0"], self.root)
        self.assertEqual(code, 0, rep)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["store"], "sqlite")
        self.assertEqual(sorted(rep["renamed"]), sorted(n + S2.PRE_SUFFIX for n in JSONL))
        self.assertTrue(rep["similar_index"]["ok"] and rep["similar_index"]["created"], rep["similar_index"])
        self.assertTrue(rep["backup_db"] and rep["backup_files"] and rep["backup_after"])
        self.assertEqual(M2.jsonl_present(self.root), [])
        self.assertEqual(MS.mode(self.root), "sqlite")

        # 4. индекс - идемпотентно (повтор ничего не меняет)
        code, ix = run_cli(KCLI.main, ["index-similar"], self.root)
        self.assertEqual(code, 0, ix)
        self.assertFalse(ix["created"])
        conn = LST.open_ro(self.root)
        try:
            self.assertTrue(KS.has_index(conn))
        finally:
            conn.close()

        # 5. verify: база знаний зелёная, цепочки случаев и уроков в базе; ./tb memory verify - по активному хранилищу
        code, kv = run_cli(KCLI.main, ["verify"], self.root)
        self.assertEqual(code, 0)
        self.assertEqual((kv["color"], kv["store"]), ("green", "sqlite"), kv)
        self.assertTrue(kv["chains"]["case_rows"]["ok"] and kv["chains"]["lesson_rows"]["ok"])
        self.assertEqual(kv["tables"]["case_rows"], 17)
        self.assertEqual(kv["tables"]["lesson_rows"], 3)
        with no_jsonl_open(self):
            code, mv = run_cli(MCLI.main, ["verify"], self.root)
        self.assertEqual(code, 0, mv)
        self.assertEqual((mv["cases"]["n"], mv["lessons"]["n"]), (17, 3))

        # 6. чтения через фасад: те же ответы, и ни одного открытия JSONL
        MVB.clear()
        with no_jsonl_open(self):
            after = self.reads()
            sig = LL.source_signature(self.root)
            fk = MVB.flight_key(self.root, "paper")
        self.assertSameReads(before, after)
        self.assertEqual(sig[1][0], "sqlite")
        self.assertEqual(sig[5][0], "sqlite")

        # 7. периодический писатель памяти: при неизменных источниках второй шаг пропускается (не запускает memory.migrate)
        with no_jsonl_open(self):
            tick.sync_now(self.root, now=NOW + 86400)
            with mock.patch.object(tick.migrate, "migrate", side_effect=AssertionError("memory.migrate при неизменных источниках")):
                self.assertTrue(tick.sync_now(self.root, now=NOW + 86460)["skipped"])

        # 8. новые записи - в базу; JSONL не появляются; ключи кешей читателей меняются
        with no_jsonl_open(self):
            row = cases.append_new(mk(90, sym="CUTX", mode="paper"), root=self.root)
            cases.append_update(row["case_id"], {"flags": ["artifact"]}, root=self.root, ts="2026-10-29T00:00:00Z")
            lessons.create(self.root, [{"feature": "spread_bps", "op": ">=", "value": 5.0}], {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.2},
                           "owner", cases.load(self.root), self.cfg, NOW + 100)
            sig2 = LL.source_signature(self.root)
            fk2 = MVB.flight_key(self.root, "paper")
            new_state = {"cases": cases.load(self.root, copy=True), "lessons": lessons.read_all(self.root)}
        self.assertNotEqual(sig2[1], sig[1])
        self.assertNotEqual(sig2[5], sig[5])
        self.assertNotEqual(fk2, fk)
        self.assertEqual(M2.jsonl_present(self.root), [])
        self.assertEqual(len(new_state["cases"]), 17)
        self.assertEqual(len(new_state["lessons"]), 3)
        self.assertTrue(cases.verify(self.root)["ok"] and lessons.verify(self.root)["ok"])

        # 9. проверочный экспорт в каталог (store не меняется), затем возврат: при работающей платформе - отказ, после остановки - успех
        out = os.path.join(self.root, "exp")
        code, ex = run_cli(KCLI.main, ["export-jsonl", "--out", out], self.root)
        self.assertEqual(code, 0, ex)
        self.assertEqual(MS.mode(self.root), "sqlite")
        self.assertEqual(ex["tables"]["case_rows"]["items"], 17)
        self.running_lock(True)
        code, ex = run_cli(KCLI.main, ["export-jsonl"], self.root)
        self.assertEqual((code, ex["code"]), (1, S2.PLATFORM_RUNNING))
        self.running_lock(False)
        code, ex = run_cli(KCLI.main, ["export-jsonl"], self.root)
        self.assertEqual(code, 0, ex)
        self.assertEqual(ex["store"], "jsonl")
        self.assertEqual(sorted(M2.jsonl_present(self.root)), sorted(JSONL))
        back = {"cases": cases.load(self.root, copy=True), "lessons": lessons.read_all(self.root)}
        self.assertEqual(back["cases"], new_state["cases"])
        self.assertEqual(back["lessons"], new_state["lessons"])
        self.assertTrue(cases.verify(self.root)["ok"] and lessons.verify(self.root)["ok"])
        self.assertEqual(LL.source_signature(self.root)[1], LL._stat(self.st("cases.jsonl")))     # jsonl: прежняя подпись файла
        conn = LST.open_ro(self.root)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM rulebook_versions").fetchone()[0], 1)   # правила от store не зависят
        finally:
            conn.close()

        # 10. после возврата пишет прежний код в JSONL; повторный перенос (прежние *.pre-r29 убраны в копию) дописывает только новое
        cases.append_new(mk(91, sym="BACK", mode="paper"), root=self.root)
        self.assertEqual(M2.dry_run(self.root)["blocking"], ["PRE_EXISTS"])
        keep = os.path.join(self.root, "pre-r29-keep")
        os.makedirs(keep)
        for n in JSONL:
            shutil.move(self.st(n + S2.PRE_SUFFIX), os.path.join(keep, n + S2.PRE_SUFFIX))
        code, rep = run_cli(KCLI.main, ["migrate", "--settle", "0"], self.root)
        self.assertEqual(code, 0, rep)
        self.assertEqual(rep["tables"]["case_rows"]["inserted"], 1)
        self.assertEqual(rep["tables"]["lesson_rows"]["inserted"], 0)
        self.assertFalse(rep["similar_index"]["created"])
        with no_jsonl_open(self):
            self.assertEqual(len(cases.load(self.root)), 18)
            self.assertTrue(cases.verify(self.root)["ok"] and lessons.verify(self.root)["ok"])
        code, kv = run_cli(KCLI.main, ["verify"], self.root)
        self.assertEqual(kv["color"], "green", kv)


class Signals(unittest.TestCase):
    """verify замечает JSONL после переключения и store=sqlite без таблиц; dry-run показывает индекс."""

    def setUp(self):
        MS.reset_caches()
        self.root = tempfile.mkdtemp(prefix="tbcut_")
        for d in ("state", "config"):
            os.makedirs(os.path.join(self.root, d))

    def tearDown(self):
        MS.reset_caches()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_jsonl_appeared_and_not_ready(self):
        cases.append_new(mk(1), root=self.root)
        conn = LST.open_rw(self.root)
        conn.close()
        memcfg.set_store(self.root, "sqlite")                         # переключено без таблиц v2b
        v = store2.verify(self.root)
        self.assertIn(S2.NOT_READY, v["codes"])
        self.assertIn(S2.JSONL_APPEARED, v["codes"])
        self.assertEqual(v["color"], "yellow")
        memcfg.set_store(self.root, "jsonl")
        v = store2.verify(self.root)
        self.assertNotIn(S2.JSONL_APPEARED, v["codes"])
        self.assertEqual(v["store"], "jsonl")

    def test_index_without_db(self):
        with self.assertRaises(M2.MigrateError) as cm:
            M2.similar_index(self.root, running=lambda r, n: [])
        self.assertEqual(cm.exception.code, "NO_DB")
        self.assertIsNone(M2.dry_run(self.root)["similar_index"])

    def test_jsonl_mode_unchanged(self):
        """store=jsonl: подписи, наличие и проверка цепочки уроков - прежние значения поле в поле."""
        self.assertFalse(cases.present(self.root))
        self.assertFalse(lessons.present(self.root))
        self.assertIsNone(cases.signature(self.root))
        cases.append_new(mk(1), root=self.root)
        self.assertTrue(cases.present(self.root))
        self.assertEqual(cases.signature(self.root), LL._stat(cases.cases_path(self.root)))
        self.assertEqual(lessons.signature(self.root), LL._stat(lessons.lessons_path(self.root)))
        from tb.memory import chain
        self.assertEqual(lessons.verify(self.root), chain.verify(lessons.lessons_path(self.root)))
        self.assertEqual(lessons.read_raw(self.root), chain.read(lessons.lessons_path(self.root)))


if __name__ == "__main__":
    unittest.main()
