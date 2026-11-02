"""Блок «Память против базы» (R0.2, задача 10): свойство 17, max_trades, computing, n_eval = 0, no_data. Временный корень, без сети."""
import os
import random
import shutil
import tempfile
import threading
import types
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
from _memory_helpers import mk
from tb.learn import conf, memory_vs_base as M
from tb.memory import backtest, cases, cfg as memcfg


def gen(rng, n, mode="paper"):
    setups = ("level_reclaim", "sweep_reject", "breakout_retest", "range_fade")
    regs = ("range", "trend", "volatile")
    out = []
    for i in range(n):
        conf_ = rng.random() < 0.6
        r = round(rng.uniform(0.3, 2.0) if conf_ else rng.uniform(-1.5, -0.2), 2)
        out.append(mk(i, sym="SYM%d" % rng.randrange(5), side=rng.choice(("long", "short")), r=r, confirmed=conf_, setup=rng.choice(setups),
                      regime=rng.choice(regs), mode=mode))
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tblearn_")
        os.makedirs(os.path.join(self.root, "state"))
        os.makedirs(os.path.join(self.root, "config"))
        M.clear()

    def tearDown(self):
        M.clear()
        shutil.rmtree(self.root, ignore_errors=True)

    def put(self, lst):
        for c in lst:
            cases.append_new(c, root=self.root)

    def ctx(self, mode="paper", **mem):
        cfg, _ = conf.load(self.root)
        cfg["memory"].update(mem)
        return types.SimpleNamespace(root=self.root, mode=mode, now=0, cfg=cfg, cases=None)

    def direct(self, mode="paper"):
        mc = memcfg.load(self.root)
        return backtest.walk_forward(backtest.closed_trades(cases.load(self.root), mc, mode), mc)


class MemoryProperty(Base):
    def test_ready_equals_direct_walk_forward(self):
        """Свойство 17 (готовый результат): числа блока равны прямому walk_forward, d_brier = среднее ± t-интервал, better = lo > 0."""
        for seed in range(20):
            M.clear()
            shutil.rmtree(os.path.join(self.root, "state"))
            os.makedirs(os.path.join(self.root, "state"))
            rng = random.Random(seed)
            self.put(gen(rng, rng.randint(8, 45)))
            ref = self.direct()
            b = M.build(self.ctx(budget_s=30.0))
            self.assertEqual(b["n_trades"], ref["n_trades"], seed)
            self.assertEqual(b["n_eval"], ref["n_eval"], seed)
            self.assertEqual(b["history"]["status"], "stage")
            if ref["n_eval"]:
                for k in ("brier_mem", "brier_base", "acc_mem", "acc_base"):
                    self.assertEqual(b[k], ref[k], (seed, k))
                d = b["d_brier"]
                self.assertEqual(d["v"], ref["d_brier_mean"])
                self.assertEqual(d["n"], ref["n_eval"])
                if d["lo"] is not None:
                    self.assertLessEqual(d["lo"], d["v"])
                    self.assertGreaterEqual(d["hi"], d["v"])
                    self.assertEqual(b["better"], d["lo"] > 0)
                self.assertEqual(b["covered"]["v"], ref["n_eval"] / ref["n_trades"])
            self.assertEqual(b["status"], "ok" if ref["n_eval"] >= 30 else "few_data")

    def test_status_ok_with_enough_eval(self):
        self.put(gen(random.Random(11), 90))
        b = M.build(self.ctx(budget_s=60.0))
        self.assertGreaterEqual(b["n_eval"], 30)
        self.assertEqual(b["status"], "ok")
        self.assertIsNotNone(b["d_brier"]["lo"])

    def test_max_trades_blocks_walk_forward(self):
        for seed in range(20):
            rng = random.Random(seed)
            cap = rng.randint(10, 15)
            shutil.rmtree(os.path.join(self.root, "state"))
            os.makedirs(os.path.join(self.root, "state"))
            self.put(gen(rng, cap + rng.randint(1, 6)))
            with mock.patch.object(backtest, "walk_forward", side_effect=AssertionError("не должен вызываться")):
                b = M.build(self.ctx(max_trades=cap))
            self.assertEqual((b["status"], b["reason"]), ("no_data", "too_many_for_request"), seed)
            self.assertIn("этап 3", b["note"])
            self.assertIsNone(b["brier_mem"])

    def test_at_limit_still_computes(self):
        self.put(gen(random.Random(1), 12))
        b = M.build(self.ctx(max_trades=12, budget_s=30.0))
        self.assertNotEqual(b["reason"], "too_many_for_request")


class Computing(Base):
    def test_computing_then_ready_equals_direct(self):
        self.put(gen(random.Random(7), 40))
        ref = self.direct()
        gate = threading.Event()
        real = backtest.walk_forward

        def slow(*a, **kw):
            gate.wait(10)
            return real(*a, **kw)

        with mock.patch.object(backtest, "walk_forward", side_effect=slow) as m:
            b1 = M.build(self.ctx(budget_s=0.1))
            self.assertEqual(b1["status"], "computing")
            self.assertIsNone(b1["brier_mem"])
            self.assertEqual(b1["n_trades"], 40)
            b1b = M.build(self.ctx(budget_s=0.05))               # второй запрос: тот же расчёт, не новый
            self.assertEqual(b1b["status"], "computing")
            gate.set()
            key_fut = [f for f in [M.MEMORY.peek("mem|%s|paper|%s" % (self.root, M._sig(self.root)))] if f][0]
            key_fut.result(10)
            b2 = M.build(self.ctx(budget_s=0.1))
            self.assertEqual(m.call_count, 1)
        self.assertEqual(b2["n_eval"], ref["n_eval"])
        self.assertEqual(b2["brier_mem"], ref["brier_mem"])
        self.assertEqual(b2["d_brier"]["v"], ref["d_brier_mean"])

    def test_cache_hit_does_not_recompute(self):
        self.put(gen(random.Random(3), 20))
        M.build(self.ctx(budget_s=30.0))
        with mock.patch.object(backtest, "walk_forward", side_effect=AssertionError("кеш")):
            b = M.build(self.ctx(budget_s=30.0))
        self.assertIn(b["status"], ("ok", "few_data"))

    def test_signature_change_recomputes(self):
        self.put(gen(random.Random(4), 20))
        b1 = M.build(self.ctx(budget_s=30.0))
        self.put([mk(500, r=1.0, mode="paper")])
        b2 = M.build(self.ctx(budget_s=30.0))
        self.assertEqual(b2["n_trades"], b1["n_trades"] + 1)


class Empty(Base):
    def test_no_trades(self):
        self.put(gen(random.Random(0), 3))
        b = M.build(self.ctx())
        self.assertEqual((b["status"], b["reason"]), ("no_data", "no_trades"))
        self.assertEqual(b["history"]["roadmap"], "R3.4")

    def test_no_cases_file(self):
        b = M.build(self.ctx())
        self.assertEqual((b["status"], b["reason"], b["n_trades"]), ("no_data", "no_trades", 0))

    def test_other_mode_not_mixed(self):
        self.put(gen(random.Random(0), 30, mode="live"))
        b = M.build(self.ctx(mode="paper"))
        self.assertEqual((b["status"], b["n_trades"]), ("no_data", 0))

    def test_n_eval_zero(self):
        self.put(gen(random.Random(5), 12))
        mc = memcfg.load(self.root)
        mc["backtest"] = {"min_similar": 99}
        with mock.patch("tb.learn.load._mem_cfg", return_value=mc):
            b = M.build(self.ctx(budget_s=30.0))
        self.assertEqual((b["status"], b["reason"], b["n_eval"]), ("few_data", "n_eval_lt_min", 0))
        for k in ("brier_mem", "brier_base", "acc_mem", "acc_base", "d_brier", "better"):
            self.assertIsNone(b[k], k)
        self.assertEqual(b["covered"]["v"], 0.0)
        self.assertEqual(b["n_trades"], 12)


if __name__ == "__main__":
    unittest.main()
