"""R4 (задача 3): tb.logic.qty.close_qty — перенос из executor/core.py без изменения поведения (Correctness Property 26)."""
import ast
import doctest
import os
import random
import unittest

import _bootstrap  # noqa: F401
from tb.executor import core
from tb.executor import gate as G
from tb.logic import qty

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "src", "tb", "logic", "qty.py")


def old_close_qty(want, remaining, step, min_qty=None, full_req=False):
    """Эталон: дословная копия executor/core.close_qty до переноса (коммит e202270, строки 34–53)."""
    step = float(step or 0) or 1e-12
    remaining = float(remaining)
    q = min(remaining, G.floor_step(want, step))
    full = bool(full_req) or q >= remaining - step / 2.0
    lot = max(step, float(min_qty or 0))
    if not full and remaining > 0 and q < lot - step / 2.0:
        if remaining < 2.0 * lot - step / 2.0:
            return remaining, True
        q = lot
    return (remaining if full else q), full


class TestQty(unittest.TestCase):
    def test_doctests(self):
        self.assertEqual(doctest.testmod(qty).failed, 0)

    def test_executor_uses_logic_qty(self):
        self.assertIs(core.close_qty, qty.close_qty)

    def test_floor_step_same_as_gate(self):
        rnd = random.Random(7)
        for _ in range(2000):
            step = rnd.choice([0, 0.001, 0.01, 0.1, 1, 0.5, 5, 1e-6])
            x = rnd.uniform(0, 50)
            self.assertEqual(qty.floor_step(x, step), G.floor_step(x, step))

    def test_property_26_random_inputs(self):
        rnd = random.Random(26)
        for _ in range(1000):
            step = rnd.choice([0.001, 0.01, 0.1, 1.0, 0.5, 10.0, 0, None])
            base = step or 0.01
            remaining = round(rnd.randint(0, 40) * base, 10) if rnd.random() < 0.8 else rnd.uniform(0, 5)
            want = remaining * rnd.choice([0.0, 0.1, 0.25, 0.3, 0.5, 0.7, 1.0, 1.3]) + (rnd.uniform(-1, 1) * base if rnd.random() < 0.3 else 0)
            want = max(0.0, want)
            mq = rnd.choice([None, 0, base, 2 * base, "0.2", 5 * base])
            fr = rnd.random() < 0.15
            self.assertEqual(qty.close_qty(want, remaining, step, mq, fr), old_close_qty(want, remaining, step, mq, fr),
                             (want, remaining, step, mq, fr))

    def test_cases_from_executor_tests(self):
        self.assertEqual(qty.close_qty(0.005, 0.01, 0.01), (0.01, True))
        self.assertEqual(qty.close_qty(0.05, 0.1, 0.1, "0.1"), (0.1, True))
        self.assertEqual(qty.close_qty(0.15, 0.3, 0.1, "0.2"), (0.3, True))
        self.assertEqual(qty.close_qty(0.003, 0.05, 0.01), (0.01, False))
        self.assertEqual(qty.close_qty(0.07, 0.14, 0.01, full_req=True), (0.14, True))

    def test_never_more_than_remaining(self):
        rnd = random.Random(3)
        for _ in range(1000):
            step = rnd.choice([0.01, 0.1, 1.0])
            rem = round(rnd.randint(1, 30) * step, 10)
            q, full = qty.close_qty(rnd.uniform(0, 2 * rem), rem, step, rnd.choice([None, step, 3 * step]))
            self.assertLessEqual(q, rem + 1e-12)
            self.assertTrue(q > 0)

    def test_pure_imports(self):
        tree = ast.parse(open(SRC, encoding="utf-8").read())
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                mods.add(n.module or "")
        self.assertEqual(mods, {"math"})


if __name__ == "__main__":
    unittest.main()
