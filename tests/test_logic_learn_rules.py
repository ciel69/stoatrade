"""tb.logic.learn_rules (R2, задача 11): свойства 1 (только ужесточение: перебор комбинаций признаков и наборов правил, размерные виды при
allow_size_rules=false не создаются и не применяются, неизвестный вид отклоняется на записи и при чтении, не чистый запрет -> blocked) и 16 (откат
восстанавливает множество разрешённых входов), validate_rule, cond_h, область применимости (паритет с memory.features.in_scope), is_tightening."""
import itertools
import os
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import test_layering as TL
from tb.logic import learn_cond as LC
from tb.logic import learn_rules as LR
from tb.memory import features as MF

P2_SCOPE = {"policy": "P2", "kinds": ["range_5m", "turnover_surge", "hot_enter", "oi_spike", "liq_cascade"], "side": "with_dir",
            "entry": "candle_confirm_limit"}


def rule(rid, cond, kind="skip_when", scope=None, **kw):
    r = {"rule_id": rid, "kind": kind, "cond": cond, "param": {}, "scope": dict(scope or P2_SCOPE), "env_norm": "mainnet", "status": "active"}
    r.update(kw)
    return r


def feats(ev_kind="range_5m", side_rel="with_dir", **kw):
    f = dict((k, None) for k in LC.RULE_FEATURES)
    f.update(ev_kind=ev_kind, side_rel=side_rel, side="long")
    f.update(kw)
    return f


R_SPREAD = rule("RL0001", [{"feature": "spread_bps", "op": ">=", "value": 5.0}])
R_RANGE = rule("RL0002", [{"feature": "range_5m_pct", "op": "<=", "value": 0.4}])
R_MEME = rule("RL0003", [{"feature": "coin_class", "op": "=", "value": "meme"}])
R_COMBO = rule("RL0004", [{"feature": "funding_pct", "op": ">=", "value": 0.01}, {"feature": "ret_5m_pct", "op": "<=", "value": -0.5}])


class TestValidate(unittest.TestCase):
    def test_good_rule(self):
        self.assertEqual(LR.validate_rule(R_SPREAD), [])

    def test_unknown_kind_rejected(self):
        self.assertTrue(LR.validate_rule(dict(R_SPREAD, kind="boost_when")))

    def test_size_kinds_rejected_both_ways(self):
        for kind in LR.SIZE_KINDS:
            off = LR.validate_rule(dict(R_SPREAD, kind=kind))
            self.assertTrue(off and "allow_size_rules" in off[0])
            self.assertTrue(LR.validate_rule(dict(R_SPREAD, kind=kind), allow_size_rules=True))      # не реализованы во второй итерации
        self.assertEqual(LR.RULE_KINDS, ("skip_when",))

    def test_cond_outside_rule_features(self):
        self.assertTrue(LR.validate_rule(rule("RL0001", [{"feature": "stop_pct", "op": ">=", "value": 2.0}])))
        self.assertTrue(LR.validate_rule(rule("RL0001", [{"feature": "coin_class", "op": "=", "value": "doge"}])))
        self.assertTrue(LR.validate_rule(rule("RL0001", [])))

    def test_param_scope_env_id(self):
        self.assertTrue(LR.validate_rule(dict(R_SPREAD, param={"size_mult": 0.5})))
        self.assertTrue(LR.validate_rule(dict(R_SPREAD, scope={"kinds": [], "side": "with_dir"})))
        self.assertTrue(LR.validate_rule(dict(R_SPREAD, scope=dict(P2_SCOPE, side="random"))))
        self.assertTrue(LR.validate_rule(dict(R_SPREAD, env_norm="paper")))
        self.assertTrue(LR.validate_rule(dict(R_SPREAD, rule_id="R-0001")))
        self.assertTrue(LR.validate_rule("x"))


class TestCondHash(unittest.TestCase):
    def test_order_independent_dir_sensitive(self):
        a = [{"feature": "spread_bps", "op": ">=", "value": 5}, {"feature": "coin_class", "op": "in", "value": ["meme", "alt_small"]}]
        b = [{"feature": "coin_class", "op": "in", "value": ["alt_small", "meme"]}, {"feature": "spread_bps", "op": ">=", "value": 5}]
        self.assertEqual(LR.cond_h(a, "lower"), LR.cond_h(b, "lower"))
        self.assertNotEqual(LR.cond_h(a, "lower"), LR.cond_h(a, "higher"))
        self.assertRegex(LR.cond_h(a), r"^[0-9a-f]{16}$")

    def test_codes(self):
        self.assertEqual(LR.fail_code("RL0001"), "RULE_RL0001")
        self.assertEqual(LR.plan_code("RL0001"), "E_RULE_RL0001")


class TestApply(unittest.TestCase):
    def test_hit_pass_missing_out(self):
        rs = [R_SPREAD, R_RANGE]
        r = LR.apply(rs, {"ok": True}, feats(spread_bps=7.0, range_5m_pct=0.9))
        self.assertEqual((r["ok"], r["fail"], r["hits"], r["eval"], r["missing"]), (False, "RULE_RL0001", ["RL0001"], ["RL0001", "RL0002"], []))
        r = LR.apply(rs, {"ok": True}, feats(spread_bps=1.0))
        self.assertEqual((r["ok"], r["hits"], r["eval"], r["missing"]), (True, [], ["RL0001"], ["RL0002"]))

    def test_no_event_puts_rules_into_missing(self):
        r = LR.apply([R_SPREAD, R_MEME], {"ok": True}, MF.rule_features(None, {"side": "long"}, {}))
        self.assertEqual((r["ok"], r["missing"], r["eval"]), (True, ["RL0001", "RL0003"], []))

    def test_out_of_scope_silent(self):
        r = LR.apply([R_SPREAD], None, feats(ev_kind="patrol", spread_bps=99.0))
        self.assertEqual((r["ok"], r["eval"], r["missing"], r["hits"]), (True, [], [], []))
        r = LR.apply([R_SPREAD], None, feats(side_rel="against_dir", spread_bps=99.0))
        self.assertEqual((r["ok"], r["eval"]), (True, []))
        r = LR.apply([R_SPREAD], None, feats(side_rel=None, spread_bps=99.0))         # сторону не определить: правило не срабатывает
        self.assertEqual((r["ok"], r["missing"]), (True, ["RL0001"]))

    def test_no_apply_mode(self):
        f = feats(spread_bps=99.0)
        for rs, ctx in (([R_SPREAD], {"apply": False}), ({"rules": [R_SPREAD], "apply": False, "why": "LEASE"}, None)):
            r = LR.apply(rs, {"ok": True}, f, ctx)
            self.assertEqual((r["ok"], r["hits"], r["eval"], r["missing"], r["apply"]), (True, [], [], [], False))
        self.assertFalse(LR.apply({"rules": [R_SPREAD], "apply": True}, {"ok": True}, f)["ok"])

    def test_trial_and_env_not_applied(self):
        f = feats(spread_bps=99.0)
        self.assertTrue(LR.allowed([dict(R_SPREAD, status="trial")], {"ok": True}, f))
        self.assertTrue(LR.allowed([R_SPREAD], {"ok": True}, f, {"env_norm": "testnet"}))
        self.assertFalse(LR.allowed([R_SPREAD], {"ok": True}, f, {"env_norm": "mainnet"}))

    def test_unknown_and_size_kinds_never_applied(self):
        f = feats(spread_bps=99.0)
        for kind in ("boost_when",) + LR.SIZE_KINDS:
            r = LR.apply([dict(R_SPREAD, kind=kind)], {"ok": True}, f, {"allow_size_rules": False})
            self.assertEqual((r["ok"], r["blocked"], r["hits"]), (True, ["RL0001"], []))
            r = LR.apply([dict(R_SPREAD, kind=kind)], {"ok": True}, f, {"allow_size_rules": True})
            self.assertEqual((r["ok"], r["blocked"]), (True, ["RL0001"]))

    def test_non_pure_handler_is_bounds_blocked(self):
        """Обработчик, который вернул бы изменение размера или разрешение входа, не применяется (BOUNDS_BLOCKED)."""
        f = feats(spread_bps=99.0)
        for bad in ({"skip": True, "size_mult": 0.5}, {"skip": "yes"}, {"allow": True}, None):
            with mock.patch.dict(LR.HANDLERS, {"skip_when": lambda r, c, x, _b=bad: _b}):
                res = LR.apply([R_SPREAD], {"ok": False}, f)
            self.assertEqual((res["blocked"], res["hits"], res["ok"]), (["RL0001"], [], False))   # ok=false кандидата не снимается

    def test_plan_without_ok(self):
        self.assertTrue(LR.allowed([R_SPREAD], {"side": "long"}, feats(spread_bps=1.0)))
        self.assertFalse(LR.allowed([R_SPREAD], {"side": "long"}, feats(spread_bps=9.0)))

    def test_first_hit_by_rule_id(self):
        r = LR.apply([R_MEME, R_SPREAD], {}, feats(spread_bps=9.0, coin_class="meme"))
        self.assertEqual((r["fail"], r["hits"]), ("RULE_RL0001", ["RL0001", "RL0003"]))


GRID = {"spread_bps": (None, 1.0, 5.0, 9.0), "range_5m_pct": (None, 0.1, 0.4, 2.0), "coin_class": (None, "meme", "major"),
        "funding_pct": (None, 0.0, 0.02), "ret_5m_pct": (None, -1.0, 0.3)}
POOL = [R_SPREAD, R_RANGE, R_MEME, R_COMBO, dict(R_SPREAD, rule_id="RL0005", kind="size_cap"), dict(R_RANGE, rule_id="RL0006", kind="boost_when"),
        dict(R_MEME, rule_id="RL0007", status="trial")]


def grid_feats():
    keys = sorted(GRID)
    for vals in itertools.product(*(GRID[k] for k in keys)):
        for ev_kind, side_rel in (("range_5m", "with_dir"), ("oi_spike", "against_dir"), (None, None), ("patrol", "with_dir")):
            yield feats(ev_kind=ev_kind, side_rel=side_rel, **dict(zip(keys, vals)))


class TestProperty1OnlyTightening(unittest.TestCase):
    def test_subset_over_combinations(self):
        """Для любого кандидата и любого поднабора правил: разрешённые с правилами ⊆ без правил; добавление правила не расширяет множество."""
        fs = list(grid_feats())
        sets = [list(c) for n in range(0, 4) for c in itertools.combinations(POOL, n)]
        checked = 0
        for f in fs[::7]:
            for base_ok in (True, False):
                cand = {"ok": base_ok, "side": "long"}
                for rs in sets:
                    a = LR.allowed(rs, cand, f)
                    if a:
                        self.assertTrue(base_ok)
                    for extra in POOL:
                        if LR.allowed(rs + [extra], cand, f):
                            self.assertTrue(a)
                    checked += 1
        self.assertGreater(checked, 1000)

    def test_size_kinds_cut_nothing(self):
        sz = [r for r in POOL if r["kind"] != "skip_when"]
        for f in list(grid_feats())[::11]:
            self.assertTrue(LR.allowed(sz, {"ok": True}, f, {"allow_size_rules": False}))


class TestTightening(unittest.TestCase):
    def test_is_tightening(self):
        self.assertTrue(LR.is_tightening([], [R_SPREAD]))
        self.assertTrue(LR.is_tightening([R_SPREAD], [R_SPREAD, R_RANGE]))
        self.assertFalse(LR.is_tightening([R_SPREAD, R_RANGE], [R_SPREAD]))                    # убрали правило — ослабление
        moved = dict(R_SPREAD, cond=[{"feature": "spread_bps", "op": ">=", "value": 50.0}])
        self.assertFalse(LR.is_tightening([R_SPREAD], [moved]))                                   # правило ослаблено подменой порога
        self.assertFalse(LR.is_tightening([], [dict(R_SPREAD, kind="size_cap")]))
        self.assertFalse(LR.is_tightening([], [dict(R_SPREAD, kind="size_cap")], allow_size_rules=True))
        probes = [({"ok": True}, f) for f in list(grid_feats())[::13]]
        self.assertTrue(LR.is_tightening([R_SPREAD], [R_SPREAD, R_COMBO], probes))

    def test_property16_rollback_restores_allowed_set(self):
        """Набор до продвижения S, после S + r, откат -> S: множество разрешённых входов после отката равно множеству до продвижения."""
        before = [R_SPREAD, R_MEME]
        promoted = before + [R_COMBO]
        rolled = [r for r in promoted if r["rule_id"] != R_COMBO["rule_id"]]
        fs = list(grid_feats())
        allow = lambda rs: [LR.allowed(rs, {"ok": True}, f) for f in fs]        # noqa: E731
        self.assertEqual(allow(rolled), allow(before))
        self.assertNotEqual(allow(promoted), allow(before))
        self.assertTrue(LR.is_tightening(before, promoted))
        self.assertFalse(LR.is_tightening(promoted, rolled))

    def test_scope_kinds(self):
        self.assertEqual(LR.scope_kinds([R_SPREAD]), sorted(P2_SCOPE["kinds"]))
        self.assertEqual(LR.scope_kinds({"rules": [], "apply": True}), [])


class TestScopeParity(unittest.TestCase):
    def test_scope_ok_matches_features_in_scope(self):
        for kind in (None, "range_5m", "patrol"):
            for rel in (None, "with_dir", "against_dir"):
                for side in ("with_dir", "against_dir", "any"):
                    for kinds in (P2_SCOPE["kinds"], ["*"]):
                        sc = {"policy": "P2", "kinds": kinds, "side": side, "entry": None}
                        f = {"ev_kind": kind, "side_rel": rel}
                        self.assertEqual(MF.in_scope(f, sc), LR.scope_ok(f, sc) is True, (kind, rel, side, kinds))


class TestPurity(unittest.TestCase):
    def test_module_is_pure(self):
        path = os.path.join(TL.SRC, "logic", "learn_rules.py")
        self.assertEqual(TL.logic_violations(TL.read(path)), [])
        imps = TL.imports_of(TL.read(path), "tb.logic.learn_rules")
        self.assertEqual(sorted(i for i in imps if i.startswith("tb.") and not i.startswith("tb.logic")), [])


if __name__ == "__main__":
    unittest.main()
