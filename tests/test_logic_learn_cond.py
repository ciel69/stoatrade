"""R2 задача 6: чистый модуль условий tb.logic.learn_cond: словарь RULE_FEATURES/MENU, перенесённые сравнения, границы, bool, категориальные."""
import ast
import os
import unittest

import _bootstrap  # noqa: F401
from tb.logic import learn_cond as lc
from tb.memory import cases, lessons


class TestDictionary(unittest.TestCase):
    def test_features_and_menu(self):
        self.assertEqual(lc.BASE_RULE_FEATURES, ("coin_class", "utc_session", "side", "range_5m_pct", "ret_5m_pct", "turn_surge_5m", "chg_24h_pct",
                                                 "spread_bps", "funding_pct"))
        self.assertEqual(lc.BASE_MENU_NUM, ("range_5m_pct", "ret_5m_pct", "turn_surge_5m", "chg_24h_pct", "spread_bps", "funding_pct"))
        self.assertEqual(lc.RULE_FEATURES, lc.BASE_RULE_FEATURES + lc.CM_WAVE)          # R1.7 первая волна (Requirement 15.1)
        self.assertEqual(lc.MENU_NUM, lc.BASE_MENU_NUM + lc.CM_WAVE)
        self.assertEqual(lc.MENU_CAT, ("coin_class",))
        self.assertEqual(len(lc.MENU_NUM) + len(lc.MENU_CAT), 11)         # без side и utc_session
        self.assertTrue(set(lc.MENU_NUM + lc.MENU_CAT) <= set(lc.RULE_FEATURES))
        self.assertEqual(lc.NUM_FEATURES, lc.MENU_NUM)
        self.assertEqual(lc.menu_cat_values("coin_class"), ("meme", "major", "alt_mid", "alt_small", "stock_token"))
        self.assertEqual(len(lc.MENU_NUM) * 2 + len(lc.menu_cat_values("coin_class")), 25)   # верхняя граница K (было 17)

    def test_dictionary_matches_memory(self):
        """Словарь не расходится с tb.memory.cases и признаками уроков."""
        self.assertEqual(lc.CAT_VALUES["coin_class"], cases.COIN_CLASSES)
        self.assertEqual(lc.CAT_VALUES["utc_session"], cases.SESSIONS)
        self.assertTrue(set(lc.CAT_VALUES["side"]) <= set(cases.SIDES))
        self.assertTrue(set(lc.RULE_FEATURES) <= set(lessons.FEATURES))
        for f in lc.NUM_FEATURES:
            self.assertIn(f, lessons.NUM_FEATURES)
        for f in lc.CAT_VALUES:
            self.assertIn(f, lessons.CAT_FEATURES)
        self.assertEqual(lc.OPS, lessons.OPS)

    def test_non_rule_features_excluded(self):
        for f in ("regime", "setup", "confirm_kind", "confirmed", "no_confirm_tag", "blackout", "stop_pct", "stop_dist_vs_range5", "rr_planned", "size_mult",
                  "range_15m_pct", "btc_ret_1h_pct"):
            self.assertNotIn(f, lc.RULE_FEATURES)
            self.assertTrue(lc.validate_cond([{"feature": f, "op": "=", "value": 1}]), f)

    def test_validate_cond(self):
        ok = [{"feature": "coin_class", "op": "=", "value": "meme"}, {"feature": "spread_bps", "op": ">=", "value": 5.0},
              {"feature": "side", "op": "in", "value": ["long", "short"]}]
        self.assertEqual(lc.validate_cond(ok), [])
        self.assertEqual(lc.validate_cond([{"feature": "funding_pct", "op": "<=", "value": -1}]), [])
        bad = (
            [],
            ok + [{"feature": "ret_5m_pct", "op": ">=", "value": 1}],                     # длиннее 3
            [{"feature": "coin_class", "op": ">=", "value": "meme"}],                     # порядок для категориального
            [{"feature": "coin_class", "op": "=", "value": "doge"}],                      # вне словаря
            [{"feature": "side", "op": "=", "value": "watch"}],
            [{"feature": "coin_class", "op": "in", "value": []}],
            [{"feature": "coin_class", "op": "in", "value": "meme"}],
            [{"feature": "spread_bps", "op": "=", "value": 5}],                           # = для числового
            [{"feature": "spread_bps", "op": ">=", "value": True}],                       # bool не число
            [{"feature": "spread_bps", "op": ">=", "value": "5"}],
            [{"feature": "spread_bps", "op": ">=", "value": float("nan")}],
            [{"feature": "spread_bps", "op": ">=", "value": float("inf")}],
            [{"feature": "spread_bps", "op": ">=", "value": 1}, {"feature": "spread_bps", "op": "<=", "value": 9}],   # повтор
            [{"feature": "spread_bps", "op": ">=", "value": 1, "x": 1}],
            "spread_bps>=1",
        )
        for c in bad:
            self.assertTrue(lc.validate_cond(c), c)
        self.assertEqual(lc.validate_cond([{"feature": "spread_bps", "op": ">=", "value": 1}] * 1, max_len=1), [])


class TestCompare(unittest.TestCase):
    def test_bounds_inclusive(self):
        self.assertTrue(lc.cmp_val(">=", 1.5, 1.5))
        self.assertTrue(lc.cmp_val("<=", 1.5, 1.5))
        self.assertFalse(lc.cmp_val(">=", 1.5, 1.4999))
        self.assertFalse(lc.cmp_val("<=", 1.5, 1.5001))
        self.assertTrue(lc.cmp_val(">=", -0.2, 0))
        self.assertTrue(lc.cmp_val("<=", 0, -0.01))

    def test_none_and_non_numbers(self):
        for op, v in (("=", "meme"), ("in", ["meme"]), (">=", 1), ("<=", 1)):
            self.assertFalse(lc.cmp_val(op, v, None))
        self.assertFalse(lc.cmp_val(">=", 1, "5"))
        self.assertFalse(lc.cmp_val("<=", 1, "0"))

    def test_bool(self):
        self.assertTrue(lc.cmp_val("=", False, False))
        self.assertFalse(lc.cmp_val("=", False, True))
        self.assertFalse(lc.cmp_val(">=", 0, True))      # bool не число для порядка
        self.assertFalse(lc.cmp_val("<=", 1, False))
        self.assertTrue(lc.cmp_val("in", [True], True))

    def test_categorical(self):
        self.assertTrue(lc.cmp_val("=", "meme", "meme"))
        self.assertFalse(lc.cmp_val("=", "meme", "major"))
        self.assertTrue(lc.cmp_val("in", ["meme", "major"], "major"))
        self.assertFalse(lc.cmp_val("in", ["meme", "major"], "alt_mid"))

    def test_matches_features_and_missing(self):
        cond = [{"feature": "coin_class", "op": "=", "value": "meme"}, {"feature": "spread_bps", "op": ">=", "value": 5.0}]
        self.assertTrue(lc.matches_features(cond, {"coin_class": "meme", "spread_bps": 5.0}))
        self.assertFalse(lc.matches_features(cond, {"coin_class": "meme", "spread_bps": 4.9}))
        self.assertFalse(lc.matches_features(cond, {"coin_class": "meme"}))
        self.assertFalse(lc.matches_features(cond, {"coin_class": "meme", "spread_bps": None}))
        self.assertTrue(lc.matches_features([], {}))
        self.assertEqual(lc.missing(cond, {"coin_class": "meme", "spread_bps": None}), ["spread_bps"])
        self.assertEqual(lc.missing(cond, {"coin_class": "meme", "spread_bps": 1}), [])


class TestTransfer(unittest.TestCase):
    def test_lessons_reexports_same_functions(self):
        self.assertIs(lessons.cmp_val, lc.cmp_val)
        self.assertIs(lessons._cmp_val, lc.cmp_val)
        self.assertIs(lessons.matches_features, lc.matches_features)

    def test_lessons_matches_uses_vector(self):
        from _memory_helpers import mk
        c = mk(1, confirmed=False)
        self.assertTrue(lessons.matches([{"feature": "confirmed", "op": "=", "value": False}], c))
        self.assertFalse(lessons.matches([{"feature": "confirmed", "op": "=", "value": True}], c))

    def test_stdlib_only(self):
        path = os.path.join(_bootstrap.SRC, "tb", "logic", "learn_cond.py")
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        names += [n.module or "." for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        self.assertEqual(names, [])                                     # даже stdlib не нужен


if __name__ == "__main__":
    unittest.main()
