"""Тема `exits_policy` (tb.dash2.topics.exits_policy; R4): карточка политики выхода на «Система → Контроль» отдаёт тот же отчёт,
что `./tb exits status`, только читает и не падает без базы знаний (файловые поля остаются, испытания пустые, код причины в db).
Временные корни, сети нет."""
import os
import shutil
import unittest

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.core import exitdoc as XD
from tb.dash2 import topics
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import exits_conf as CONF
from tb.learnloop import exits_versions as XV

NOW = 1793750400                     # 2026-11-04T00:00:00Z
CFG_FILES = ("exits.json", "learnloop.json")


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        for name in CFG_FILES:
            shutil.copy(os.path.join(H.PROJECT, "config", name), os.path.join(self.root, "config", name))
        XD.clear_cache()


class TestPolicy(Base):
    def test_without_db_keeps_file_state(self):
        rep = topics.exits_policy(self.root, NOW)
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["db"], "NO_DB")
        self.assertEqual(rep["promotion"]["mode"], "auto")
        self.assertTrue(rep["switch"]["apply"])
        self.assertIsNone(rep["version"])
        self.assertEqual((rep["versions"], rep["trials"]), (0, {"open": 0, "closed": 0, "rows": []}))
        self.assertIsNone(rep["watch"])
        self.assertIsNone(rep["effective"])
        self.assertEqual(rep["config"]["errors"], [])

    def test_with_cycle_tables(self):
        conn = store.open_rw(self.root)
        try:
            self.assertEqual(store2.migrate_x1(conn, self.root, backup=False, now=NOW), 1)
            self.assertEqual(XV.init(conn, CONF.current_floor(self.root), NOW), "X-0001")
        finally:
            conn.close()
        rep = topics.exits_policy(self.root, NOW)
        self.assertTrue(rep["ok"], rep.get("error"))
        self.assertEqual((rep["db"], rep["versions"]), ("OK", 1))
        self.assertEqual((rep["version"]["ver"], rep["version"]["reason_code"]), ("X-0001", "INIT"))
        self.assertEqual((rep["effective"]["ver"], rep["effective"]["why"]), ("X-0001", "SNAPSHOT"))
        self.assertEqual((rep["trials"]["open"], rep["trials"]["closed"]), (0, 0))

    def test_broken_config_is_reported(self):
        with open(os.path.join(self.root, "config", "exits.json"), "w", encoding="utf-8") as f:
            f.write("{ not json")
        rep = topics.exits_policy(self.root, NOW)
        self.assertFalse(rep["ok"])
        self.assertTrue(rep["config"]["errors"])


if __name__ == "__main__":
    unittest.main()
