"""R2, задача 13 (Requirement 7.4): блок досье `rulebook` — во всех трёх перечнях блоков (brain/brief.py, gateway get_brief, dash/model2),
до 5 правил и <= 300 токенов (render_cond), сжимается раньше playbook, но не раньше lessons; блок playbook не меняется; при пустом
rulebook и недоступной базе блока нет и досье прежнее. Всё во временном корне."""
import time
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import test_brain_rules as TR
from tb.brain import brief as brief_mod
from tb.dash import model2
from tb.gateway import catalog
from tb.knowledge import rulebook_view as RV


def rule(i, n_cond=3):
    feats = ["spread_bps", "range_5m_pct", "funding_pct", "ret_5m_pct", "turn_surge_5m"]
    return {"rule_id": "RL%04d" % i, "kind": "skip_when", "status": "active",
            "cond": [{"feature": feats[k], "op": ">=", "value": 1.2345678 + k} for k in range(n_cond)],
            "scope": {"policy": "P2", "kinds": TR.P2_KINDS, "side": "with_dir", "entry": "candle_confirm_limit"}}


class _C(object):
    def __init__(self, view):
        self.root, self.now, self._rule_view = "x", time.time(), view


class TestLists(unittest.TestCase):
    def test_block_in_all_three_lists(self):
        self.assertIn("rulebook", [n for n, _ in brief_mod.TRIM_STEPS])
        self.assertIn("rulebook", catalog.TOOLS["get_brief"].out)
        self.assertIn("rulebook", model2._BRIEF_ALLOW)
        self.assertIn("playbook", catalog.TOOLS["get_brief"].out)
        self.assertIn("playbook", model2._BRIEF_ALLOW)

    def test_trim_order(self):
        names = [n for n, _ in brief_mod.TRIM_STEPS]
        self.assertLess(names.index("lessons"), names.index("rulebook"))
        self.assertLess(names.index("rulebook"), names.index("playbook"))


class TestBlock(unittest.TestCase):
    def test_limits(self):
        view = {"ver": "R-0009", "apply": True, "why": "OK", "rules": [rule(i) for i in range(1, 9)]}
        b = brief_mod._b_rulebook(_C(view))
        self.assertLessEqual(len(b["rules"]), 5)
        self.assertLessEqual(brief_mod.tokens(b), brief_mod.RULEBOOK_MAX_TOKENS)
        self.assertEqual(b["ver"], "R-0009")
        self.assertEqual(b["rules"][0]["id"], "RL0001")
        self.assertEqual(b["rules"][0]["code"], "RULE_RL0001")
        self.assertNotIn("why", b)

    def test_no_apply_shows_why_and_empty_is_none(self):
        b = brief_mod._b_rulebook(_C({"ver": "R-0002", "apply": False, "why": "LEASE", "rules": [rule(1, 1)]}))
        self.assertEqual((b["apply"], b["why"], len(b["rules"])), (False, "LEASE", 1))
        self.assertIn("1.23", b["rules"][0]["if"])
        self.assertIsNone(brief_mod._b_rulebook(_C({"ver": "R-0001", "apply": True, "rules": []})))
        self.assertIsNone(brief_mod._b_rulebook(_C(None)))


class TestBriefBuild(TR.RulesBase):
    def build(self, scope="start", **kw):
        RV.clear_cache()
        b = brief_mod.build(scope, root=self.root, now=self.now, **kw)
        b.pop("brief_id", None)
        return b

    def test_block_in_start_and_event(self):
        base_start, base_event = self.build(), self.build("event", sym="SOL")
        self.assertNotIn("rulebook", base_start)
        TR.make_db(self.root, self.now, with_rule=False)
        TR.lease(self.root, self.now)
        self.assertEqual(self.build(), base_start)                                       # пустой rulebook: досье прежнее поле в поле
        self.assertEqual(self.build("event", sym="SOL"), base_event)
        now0 = self.now
        self.setUp()
        self.now = now0                         # то же «сейчас», что у base_start: метки времени внутри блоков досье не переходят через границу секунды
        self.active_rule()
        b = self.build()
        self.assertEqual((b["rulebook"]["ver"], b["rulebook"]["apply"], [r["id"] for r in b["rulebook"]["rules"]]), ("R-0002", True, ["RL0001"]))
        self.assertEqual({k: v for k, v in b.items() if k not in ("rulebook", "ts")}, {k: v for k, v in base_start.items() if k != "ts"})
        self.assertIn("rulebook", self.build("event", sym="SOL"))

    def test_db_unavailable_no_block(self):
        self.active_rule()
        with mock.patch.object(RV, "active", side_effect=OSError("boom")):
            self.assertNotIn("rulebook", self.build())

    def test_gateway_get_brief_returns_block(self):
        self.active_rule()
        ctx = catalog.Ctx(self.root, mock.Mock(consumer="chat"), now=self.now)
        out = catalog.finish(catalog.TOOLS["get_brief"], catalog.h_brief(ctx, {"scope": "start"}))
        self.assertEqual(out["rulebook"]["rules"][0]["id"], "RL0001")


if __name__ == "__main__":
    unittest.main()
