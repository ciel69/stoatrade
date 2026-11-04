"""R1.7, задача 20: блок `cmkt` досье события (Requirements 16.1–16.5; свойство 15).

Блок собирает tb.core.cmktdoc.brief_block по config/cmkt.json: brief; досье (brain/brief.py) кладёт его в scope=event по монете задания.
Снимок runtime/cmkt/snap.json пишется во временном корне фикстурами test_cmktdoc.
"""
import json
import os
import random
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import test_brief as TB
import test_cmktdoc as TD
from tb import paths
from tb.brain import brief
from tb.core import cmktdoc as D
from tb.logic import cmkt as L

NOW = TB.NOW
COMPACT = ("imb_025", "cvd_ps5", "oi5", "liq5_net", "fund_w", "prem_ps", "pull15")
FULL_ONLY = ("cvd5", "cvd15", "oi15", "oi_musd", "liq5", "liq1h", "prem_cb", "bands", "walls")


class Base(TB.Base):
    def setUp(self):
        super().setUp()
        D._cfg_cache.clear()

    def tearDown(self):
        D._cfg_cache.clear()
        super().tearDown()

    def snap(self, ts=None, syms=None):
        return TD.write_snap(self.root, NOW - 1 if ts is None else ts,
                             syms if syms is not None else {"SOL": TD.snap_row(), "BTC": TD.snap_row(), "ETH": TD.snap_row()})

    def conf(self, **brief_kw):
        """config/cmkt.json временного корня: только раздел brief (остальное — умолчания читателя)."""
        p = paths.config("cmkt.json", root=self.root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"brief": brief_kw}, f)

    def block(self, sym="SOL"):
        return brief._b_cmkt(brief._Ctx(self.root, NOW), sym)

    def event(self, sym="SOL"):
        return self.build("event", since=0, sym=sym, budget_tokens=6000)


class TestBlock(Base):
    def test_default_compact_within_90_tokens(self):
        doc = self.snap()
        blk = self.block()
        self.assertLessEqual(TD.tokens(blk), 90)
        TD.leaves_ok(self, blk)
        self.assertEqual(blk["age_s"], 1)
        self.assertEqual(list(blk["syms"]), ["SOL"])
        sol = blk["syms"]["SOL"]
        for k in COMPACT + ("n_ex", "cov", "lead", "flags"):
            self.assertIn(k, sol, k)
        for k in FULL_ONLY + ("imb_05", "pulled15_net"):
            self.assertNotIn(k, sol, k)
        f = L.snap_sym(doc, "SOL")["f"]
        self.assertEqual(sol["imb_025"], round(f["cm_imb_025_pct"], 1))
        self.assertEqual(sol["pull15"], round(f["cm_wall_pull_15m_pct"], 1))

    def test_full_and_400_tokens_by_config_only(self):
        self.snap()
        compact = self.block()
        self.conf(mode="full", max_tokens=400)
        blk = self.block()
        self.assertNotEqual(blk, compact)
        self.assertLessEqual(TD.tokens(blk), 400)
        self.assertGreater(TD.tokens(blk), 90)
        TD.leaves_ok(self, blk)
        sol = blk["syms"]["SOL"]
        for k in COMPACT:
            self.assertIn(k, sol, k)
        self.assertIn("cvd5", sol)                              # поля full идут после compact; хвост может быть усечён бюджетом
        self.assertEqual(dict((k, sol[k]) for k in compact["syms"]["SOL"]), compact["syms"]["SOL"])

    def test_stale_snapshot(self):
        self.snap(ts=NOW - 12)
        self.assertEqual(self.block(), {"stale_s": 12})
        self.snap(ts=NOW - 5)
        self.assertIn("syms", self.block())                    # ровно 5 с — ещё свежий

    def test_truncation_coins_then_fields(self):
        self.snap()
        self.conf(syms=3, max_tokens=600)
        self.assertEqual(list(self.block()["syms"]), ["SOL", "BTC", "ETH"])
        self.conf(syms=3, max_tokens=90)
        blk = self.block()
        self.assertLessEqual(TD.tokens(blk), 90)
        self.assertEqual(list(blk["syms"])[0], "SOL")
        self.assertLess(len(blk["syms"]), 3)                   # лишние монеты уходят первыми
        self.conf(mode="full", max_tokens=40)
        blk = self.block()
        self.assertLessEqual(TD.tokens(blk), 40)
        sol = list(blk["syms"]["SOL"])
        self.assertEqual(sol, list(COMPACT + ("n_ex", "cov", "lead", "flags") + FULL_ONLY)[:len(sol)])      # поля уходят с конца
        self.assertIn("imb_025", sol)

    def test_none_is_absent_key(self):
        f = TD.full_feats()
        for n in ("cm_imb_025_pct", "cm_wall_pull_15m_pct", "cm_liq_5m_short_musd"):
            f[n] = None
        self.snap(syms={"SOL": TD.snap_row(f=f)})
        sol = self.block()["syms"]["SOL"]
        for k in ("imb_025", "pull15", "liq5_net"):
            self.assertNotIn(k, sol, k)
        self.assertIn("cvd_ps5", sol)
        self.assertFalse([k for k, v in sol.items() if v is None])

    def test_property_15_random_snapshots(self):
        """Свойство 15: при любых признаках и пометках блок в бюджете, только числа, перечисления и идентификаторы, без None."""
        rng = random.Random(20)
        for i in range(60):
            syms = dict((s, TD.snap_row(f=TD.feats(rng, none_share=rng.random()), flags=rng.sample(list(D.BRIEF_FLAGS) + ["clock_back"], rng.randint(0, 3)),
                                        n_walls=rng.randint(0, 3)))
                        for s in ("SOL", "BTC", "ETH") if rng.random() < 0.9)
            self.snap(syms=syms)
            mode, mt, ns = rng.choice(("compact", "full")), rng.choice((40, 90, 200, 400, 600)), rng.randint(1, 3)
            self.conf(mode=mode, max_tokens=mt, syms=ns)
            blk = self.block()
            self.assertLessEqual(TD.tokens(blk), mt, (i, mode, mt, ns))
            TD.leaves_ok(self, blk)
            self.assertLessEqual(len(blk["syms"]), ns)

    def test_no_symbol_or_snapshot(self):
        self.assertIsNone(self.block())
        self.snap()
        self.assertIsNone(self.block(None))
        self.assertEqual(self.block("DOGE")["syms"], {"DOGE": {"warn": "NOT_FOCUS"}})


class TestBrief(Base):
    def test_event_brief_has_block_and_deriv_flow_unchanged(self):
        before = self.event()
        self.assertNotIn("cmkt", before)                       # снимка нет: блока нет, в missing не попадает
        self.assertNotIn("cmkt", before.get("missing") or [])
        self.snap()
        b = self.event()
        self.assertEqual(b["cmkt"], self.block())
        for k in ("deriv", "flow"):
            self.assertEqual(b.get(k), before.get(k), k)
        self.assertNotIn("cmkt", self.build("start"))           # блок только в досье события по монете задания
        self.assertNotIn("cmkt", self.build("event", since=0, budget_tokens=6000))

    def test_stale_in_brief(self):
        self.snap(ts=NOW - 30)
        self.assertEqual(self.event()["cmkt"], {"stale_s": 30})

    def test_read_failure_does_not_break_brief(self):
        self.snap()
        with mock.patch.object(D, "brief_block", side_effect=OSError("boom")):
            b = self.event()
        self.assertNotIn("cmkt", b)
        self.assertIn("cmkt", b["missing"])
        with mock.patch.object(D, "_read_snap", side_effect=RuntimeError("boom")):
            b = self.event()                                    # читатель сам глотает любые сбои
        self.assertNotIn("cmkt", b)

    def test_trim_step_registered(self):
        names = [n for n, _ in brief.TRIM_STEPS]
        self.assertIn("cmkt", names)
        self.assertLess(names.index("flow"), names.index("cmkt"))


if __name__ == "__main__":
    unittest.main()
