"""Скользящее окно plans_per_hour: next_slot освобождается, когда старейшая заявка выходит из 60 минут."""
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb.gateway import audit


class NextSlot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.now = 1_800_000_000.0

    def tearDown(self):
        self.tmp.cleanup()

    def rec(self, ts):
        audit.record("api", "submit_plan", {}, True, "OK", 1, root=self.root, now=ts)

    def test_slot_frees_when_oldest_entry_expires(self):
        for i in range(8):
            self.rec(self.now - i * 60.0)                 # 8 заявок: 0..7 минут назад
        self.assertEqual(audit.count_recent("api", "submit_plan", 3600.0, root=self.root, now=self.now), 8)
        self.assertAlmostEqual(audit.next_slot("api", "submit_plan", 8, 3600.0, root=self.root, now=self.now), 53 * 60.0, delta=1.0)
        self.assertEqual(audit.next_slot("api", "submit_plan", 9, 3600.0, root=self.root, now=self.now), 0.0)

    def test_wait_text_is_compact(self):
        self.assertEqual(audit.wait_text(0), "0 с")
        self.assertEqual(audit.wait_text(65), "1 мин 05 с")
        self.assertEqual(audit.wait_text(3600), "60 мин 00 с")


if __name__ == "__main__":
    unittest.main()
