"""Заглушки этапов lessons и versions (R0.2, задача 11): статус, тексты, отсутствие обращений к файловой системе."""
import builtins
import os
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
from tb.learn import stages


class StagesTest(unittest.TestCase):
    def test_fields(self):
        b = stages.build(None)
        self.assertEqual(sorted(b), ["lessons", "versions"])
        for bid, title in (("lessons", "Уроки"), ("versions", "Версии мозга до/после")):
            x = b[bid]
            self.assertEqual((x["id"], x["title"], x["status"], x["stage"], x["roadmap"]), (bid, title, "stage", 2, "R2.6"))
            self.assertEqual(x["note"], "появится на этапе 2")
            self.assertIn("reason", x)

    def test_independent_copies(self):
        a = stages.build()
        a["lessons"]["note"] = "x"
        self.assertEqual(stages.build()["lessons"]["note"], "появится на этапе 2")

    def test_ctx_ignored(self):
        self.assertEqual(stages.build(object()), stages.build(None))
        self.assertEqual(stages.lessons(1)["id"], "lessons")
        self.assertEqual(stages.versions(2)["id"], "versions")

    def test_no_filesystem_access(self):
        boom = AssertionError("обращение к файловой системе")
        with mock.patch.object(builtins, "open", side_effect=boom), mock.patch.object(os, "stat", side_effect=boom), \
                mock.patch.object(os, "listdir", side_effect=boom), mock.patch.object(os, "scandir", side_effect=boom), \
                mock.patch.object(os.path, "exists", side_effect=boom), mock.patch.object(os.path, "isfile", side_effect=boom):
            stages.build({"root": "/nonexistent"})
            stages.lessons()
            stages.versions()


if __name__ == "__main__":
    unittest.main()
