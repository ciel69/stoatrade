"""Шаг rule_eval_import цикла (R2, задача 10; Requirement 7.7, design.md, «Записи rule_eval»): перенос записей decisions kind="note",
what="rule_eval" в rule_evals, идемпотентность, оборванная последняя строка активного файла, ротация журнала решений в сегмент между двумя
проходами без потери записей, курсор после восстановления базы из копии, доля решений без события (RULES_NO_EVENT). Временные корни."""
import copy
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
from tb import paths
from tb.core import decisions as DJ
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import cycle as CY

from test_learnloop_cycle import CFG, POLICY, T0

D = 86400


def rec(i, hits=(), evals=(), missing=(), event_seq=100, role="entry_scout", t=None, kind="note", what="rule_eval", why=None, ekind=None):
    r = {"actor": "brain:chat", "kind": kind, "what": what, "t": T0 + 60 * i if t is None else t, "plan": "PL-%d" % i,
         "basis": {"role": role, "cands_ok_n": 2, "event_seq": event_seq, "rule_hits": list(hits), "rule_eval_ids": list(evals),
                   "feat_missing_ids": list(missing), "rulebook_ver": "R-0002", "plan": "PL-%d" % i}}
    if why is not None:
        r["basis"].update(event_why=why, event_kind=ekind)
    return r


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbre_")
        self.addCleanup(shutil.rmtree, self.root, True)
        store2._STATUS_CACHE.clear()
        self.c = store.open_rw(self.root)
        store2.migrate_v2a(self.c, self.root, explicit=True, backup=False, now=T0)

    def tearDown(self):
        try:
            self.c.close()
        except Exception:  # noqa: BLE001
            pass

    def add(self, *a, **kw):
        return DJ.append(rec(*a, **kw), root=self.root)

    def imp(self):
        return CY.import_rule_evals(self.c, self.root)

    def rows(self):
        return self.c.execute("SELECT rec_seq, rule_id, hit, evaluated, missing, no_event FROM rule_evals ORDER BY rec_seq, rule_id").fetchall()


class TestImport(Base):
    def test_transfer(self):
        s1 = self.add(1, hits=["RL0001"], evals=["RL0001", "RL0002"])
        DJ.append({"actor": "brain:chat", "kind": "note", "what": "other", "t": T0}, root=self.root)
        DJ.append({"actor": "brain:chat", "kind": "rule", "what": "rule_eval", "t": T0}, root=self.root)      # не note: не переносится
        s2 = self.add(2, event_seq=None, missing=["RL0002"])
        s3 = self.add(3)
        r = self.imp()
        self.assertEqual((r["records"], r["cursor"]), (3, s3))
        self.assertEqual(self.rows(), [(s1, "", 1, 1, 0, 0), (s1, "RL0001", 1, 1, 0, 0), (s1, "RL0002", 0, 1, 0, 0),
                                       (s2, "", 0, 0, 1, 1), (s2, "RL0002", 0, 0, 1, 1), (s3, "", 0, 0, 0, 0)])
        row = self.c.execute("SELECT ts, role, event_seq, rulebook_ver, plan FROM rule_evals WHERE rec_seq=? AND rule_id=''", (s1,)).fetchone()
        self.assertEqual(row, (T0 + 60, "entry_scout", 100, "R-0002", "PL-1"))
        self.assertEqual(CY.get_cursor(self.c), s3)

    def test_idempotent(self):
        for i in range(5):
            self.add(i, evals=["RL0001"])
        self.imp()
        n = len(self.rows())
        self.assertEqual(self.imp()["rows"], 0)
        self.c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('rule_eval_cursor', '0')")      # курсор сброшен: повтор не создаёт строк
        self.assertEqual(self.imp()["rows"], 0)
        self.assertEqual(len(self.rows()), n)

    def test_no_journal(self):
        r = self.imp()
        self.assertEqual((r["records"], r["cursor"]), (0, 0))

    def test_torn_last_line_waits(self):
        s1 = self.add(1)
        self.imp()
        s2 = self.add(2)
        p = paths.decisions_file(self.root)
        with open(p, "rb") as f:
            data = f.read()
        with open(p, "wb") as f:
            f.write(data[:-1])                         # последняя запись без перевода строки (запись ещё идёт)
        r = self.imp()
        self.assertEqual((r["records"], r["cursor"]), (0, s1))
        with open(p, "wb") as f:
            f.write(data[:-1] + b"\n")                 # дописана
        r = self.imp()
        self.assertEqual((r["records"], r["cursor"]), (1, s2))
        with open(p, "ab") as f:
            f.write(b'{"seq": 99, "kind": "note", "what": "rule_e')       # оборванная строка процесса, который умер
        r = self.imp()
        self.assertEqual(r["records"], 0)
        s3 = self.add(3)                               # следующая запись начинается с "\n", обрывок пропускается
        r = self.imp()
        self.assertEqual((r["records"], r["cursor"]), (1, s3))
        self.assertEqual(sorted(set(x[0] for x in self.rows())), [s1, s2, s3])

    def test_rotation_between_passes(self):
        with mock.patch.object(DJ, "MAX_ACTIVE_BYTES", 600):
            seqs = [self.add(i, evals=["RL0001"]) for i in range(3)]
            self.imp()
            seqs += [self.add(i, evals=["RL0001"]) for i in range(3, 12)]      # ротации в сегменты между проходами
        self.assertGreaterEqual(len(DJ.segments(self.root)), 2)
        r = self.imp()
        self.assertEqual(r["cursor"], seqs[-1])
        self.assertEqual(sorted(set(x[0] for x in self.rows())), seqs)
        with mock.patch.object(DJ, "MAX_ACTIVE_BYTES", 600):
            seqs += [self.add(i) for i in range(12, 15)]
        self.imp()
        self.assertEqual(sorted(set(x[0] for x in self.rows())), seqs)

    def test_cursor_inside_segment(self):
        with mock.patch.object(DJ, "MAX_ACTIVE_BYTES", 10 ** 9):
            seqs = [self.add(i) for i in range(4)]
        self.c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('rule_eval_cursor', ?)", (str(seqs[1]),))
        with mock.patch.object(DJ, "MAX_ACTIVE_BYTES", 1):
            seqs.append(self.add(9))                 # активный файл ушёл в сегмент: курсор внутри сегмента
        self.imp()
        self.assertEqual(sorted(set(x[0] for x in self.rows())), seqs[2:])

    def test_cursor_after_restore(self):
        s = [self.add(i, evals=["RL0001"]) for i in range(3)]
        self.imp()
        store2.make_backup(self.root, "daily", now=T0 + D)
        s += [self.add(i, evals=["RL0001"]) for i in range(3, 6)]
        self.imp()
        self.assertEqual(CY.get_cursor(self.c), s[-1])
        store2.restore(self.root, auto=True, close=self.c.close, clock=lambda: T0 + 2 * D, sleep=lambda x: None)
        self.c = store.open_rw(self.root)
        self.assertEqual(CY.get_cursor(self.c), s[2])          # курсор из копии
        self.imp()
        self.assertEqual(sorted(set(x[0] for x in self.rows())), s)
        self.assertEqual(len(self.rows()), 2 * len(s))
        self.assertEqual(CY.get_cursor(self.c), s[-1])


class TestNoEvent(Base):
    def test_share_and_status(self):
        now = T0 + D
        for i in range(10):
            self.add(i, evals=["RL0001"], event_seq=None if i < 3 else 100, missing=["RL0001"] if i < 3 else (), t=now - 3600 + i)
        self.add(20, event_seq=None, t=now - 8 * D)               # старше 7 суток — не считается
        self.imp()
        n, share, miss = CY.no_event_share(self.c, now)
        self.assertEqual((n, round(share, 2)), (10, 0.3))
        self.assertAlmostEqual(miss, 0.3)
        cy = CY.Cycle(self.root, copy.deepcopy(CFG), steps=[], policy=POLICY)
        cy.load(self.c, now)
        cy.update_status(self.c, now, readiness=False)
        self.assertIn("RULES_NO_EVENT", cy.codes)
        cfg2 = copy.deepcopy(CFG)
        cfg2["apply"]["feat_missing_max"] = 0.5
        cy.cfg = cfg2
        cy.update_status(self.c, now, readiness=False)
        self.assertNotIn("RULES_NO_EVENT", cy.codes)

    def test_out_of_scope_does_not_light(self):
        """Решения на событиях вне области правил (patrol, book_wall) — норма: RULES_NO_EVENT не загорается; stale и no_event — загорается."""
        now = T0 + D
        for i in range(10):
            oos = i < 5
            self.add(i, evals=() if oos else ["RL0001"], event_seq=None if oos else 100, t=now - 3600 + i,
                     why="out_of_scope" if oos else "ok", ekind=("patrol" if i < 4 else "book_wall") if oos else "hot_enter")
        self.imp()
        self.assertEqual(self.c.execute("SELECT event_why, event_kind, no_event FROM rule_evals WHERE rule_id='' AND event_why='out_of_scope' "
                                        "ORDER BY rec_seq LIMIT 1").fetchone(), ("out_of_scope", "patrol", 1))
        n, share, miss = CY.no_event_share(self.c, now)
        self.assertEqual((n, share), (10, 0.0))
        cy = CY.Cycle(self.root, copy.deepcopy(CFG), steps=[], policy=POLICY)
        cy.load(self.c, now)
        cy.update_status(self.c, now, readiness=False)
        self.assertNotIn("RULES_NO_EVENT", cy.codes)
        cov = cy.no_event["coverage"]
        self.assertEqual((cov["n"], cov["out_of_scope"], cov["by_kind"], cov["share"]), (10, 5, {"patrol": 4, "book_wall": 1}, 0.5))
        self.assertEqual(cy.no_event["why"]["out_of_scope"], 5)
        for i in range(10, 13):                                    # 3 запоздавших события из 13 -> 23% > 20%
            self.add(i, event_seq=None, t=now - 3000 + i, why="stale", ekind="hot_enter")
        self.imp()
        cy.update_status(self.c, now, readiness=False)
        self.assertIn("RULES_NO_EVENT", cy.codes)

    def test_columns_added_by_import(self):
        self.assertFalse(store2.has_re_why(self.c))
        self.add(1, why="stale", ekind="hot_enter", event_seq=None)
        self.add(2)                                                # прежняя запись без причины -> NULL
        self.imp()
        self.assertTrue(store2.has_re_why(self.c))
        self.assertEqual(self.c.execute("SELECT event_why, event_kind FROM rule_evals WHERE rule_id='' ORDER BY rec_seq").fetchall(),
                         [("stale", "hot_enter"), (None, None)])

    def test_import_without_columns_on_migration_error(self):
        with mock.patch.object(store2, "migrate_re_why", side_effect=store2.KnowledgeMigrationError(None, "boom")):
            self.add(1, why="out_of_scope", ekind="patrol", event_seq=None)
            r = self.imp()
        self.assertEqual(r["records"], 1)
        self.assertFalse(store2.has_re_why(self.c))

    def test_step_in_every_pass(self):
        cy = CY.Cycle(self.root, copy.deepcopy(CFG), steps=[CY.Step("rule_eval_import", "pass", CY.step_rule_eval_import),
                                                            CY.Step("obs", "observe", lambda ctx: {"code": "OK"}, group="observe")], policy=POLICY)
        self.add(1)
        cy.run_pass(self.c, T0 + D)
        self.add(2)
        cy.run_pass(self.c, T0 + D + 900)
        self.assertEqual(len(set(x[0] for x in self.rows())), 2)


if __name__ == "__main__":
    unittest.main()
