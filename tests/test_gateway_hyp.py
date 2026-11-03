"""Нормализация гипотезы при подаче плана (R2, задача 35, Requirement 19.2): h_submit заменяет tags.hypothesis семейством словаря,
неизвестный тег -> `other` и tags.hyp_raw, план из-за гипотезы не отклоняется; запись rule_eval идёт после нормализации. Временный корень, без сети."""
import os
import shutil

import _bootstrap  # noqa: F401
import _gw
from _gw import GwBase, plan
from tb import paths
from tb.core import decisions, hyps, plan_schema
from tb.gateway import catalog

ROOT = paths.DEFAULT_ROOT


class GatewayHyp(GwBase):
    def setUp(self):
        GwBase.setUp(self)
        shutil.copy(os.path.join(ROOT, "config", "hypotheses.json"), paths.config("hypotheses.json", root=self.root))   # словарь общий: из корня проекта
        cfg = _gw.agents_cfg()
        cfg["agents"]["bot"]["limits"]["plans_per_hour"] = 50
        self.write_agents(cfg)

    def submit(self, tags):
        p = plan(mode="paper", tags=tags)
        return self.call("bot", "submit_plan", {"plan": p, "reason": "тест"})

    def tags(self):
        return self.inbox()[-1]["payload"]["tags"]

    def test_variant_spelling_becomes_family(self):
        r = self.submit({"setup": "range_fade", "hypothesis": "Range_Fade"})
        self.assertNotEqual(r.get("status"), "rejected_local", r)
        t = self.tags()
        self.assertEqual(t["hypothesis"], "range-fade")
        self.assertNotIn("hyp_raw", t)
        self.submit({"hypothesis": "range-fade-lower-edge"})
        self.assertEqual(self.tags()["hypothesis"], "range-fade")

    def test_unknown_tag_is_other_with_raw_and_not_rejected(self):
        r = self.submit({"hypothesis": "H3"})
        self.assertNotEqual(r.get("status"), "rejected_local", r)
        self.assertEqual(len(self.inbox()), 1)
        t = self.tags()
        self.assertEqual((t["hypothesis"], t["hyp_raw"]), ("other", "h3"))
        self.assertTrue(set(t["hyp_raw"]) <= set("abcdefghijklmnopqrstuvwxyz0123456789._-"))

    def test_bad_spelling_is_sanitized_not_rejected(self):
        """Тег вне шаблона RE_TAG (пробелы, кириллица, длина) раньше отклонялся схемой; теперь план принимается с `other`, hyp_raw усечён под RE_TAG."""
        r = self.submit({"hypothesis": "My Weird Hypothesis: возврат " + "x" * 80})
        self.assertNotEqual(r.get("status"), "rejected_local", r)
        t = self.tags()
        self.assertEqual(t["hypothesis"], "other")
        self.assertRegex(t["hyp_raw"], plan_schema.RE_TAG)
        self.assertTrue(t["hyp_raw"].startswith("my-weird-hypothesis"))
        self.submit({"hypothesis": "!"})                                   # от исходника ничего не осталось: other без hyp_raw
        self.assertEqual(self.tags()["hypothesis"], "other")
        self.assertNotIn("hyp_raw", self.tags())

    def test_model_hyp_raw_ignored_and_empty_hypothesis_dropped(self):
        self.submit({"hypothesis": "breakout_retest", "hyp_raw": "forged"})
        t = self.tags()
        self.assertEqual(t["hypothesis"], "breakout-retest")
        self.assertNotIn("hyp_raw", t)
        r = self.submit({"setup": "range_fade", "hypothesis": ""})
        self.assertNotEqual(r.get("status"), "rejected_local", r)
        self.assertNotIn("hypothesis", self.tags())

    def test_rule_gate_sees_normalized_plan(self):
        seen = []
        orig = catalog._rule_gate

        def spy(ctx, a, p):
            seen.append(dict(p.get("tags") or {}))
            return orig(ctx, a, p)
        catalog._rule_gate = spy
        try:
            self.submit({"hypothesis": "Trend_Pullback"})
        finally:
            catalog._rule_gate = orig
        self.assertEqual(seen[-1]["hypothesis"], "trend-pullback")
        rows = [d for d in decisions.read(root=self.root) if d.get("what") == "rule_eval"]
        self.assertEqual(len(rows), 1)                                     # запись rule_eval по-прежнему одна на подачу

    def test_description_lists_families(self):
        d = catalog.TOOLS["submit_plan"].desc
        for f in hyps.HYP_FAMILIES:
            self.assertIn(f, d)


if __name__ == "__main__":
    import unittest
    unittest.main()
