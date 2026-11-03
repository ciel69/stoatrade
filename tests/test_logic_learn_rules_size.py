"""tb.logic.learn_rules, размерные правила (R2, задача 29; Requirement 8.2, 8.5, 8.6; решение владельца 3; ROADMAP R7.2).

При allow_size_rules=false size_cap и require_confirm не создаются, не применяются, при чтении — blocked, ответ apply как в первой итерации.
При true: size_cap {"size_mult": (0,1)} даёт множитель <= 1 (только снижение), вход не снимает и не добавляет; итоговый размер bounded_size_mult
не выше потолка уровня риска и размера без правил; require_confirm — NOT_AVAILABLE (нет парной политики). Испытание size_cap по снимкам —
trial.verdict_size (R хита снижается пропорционально множителю). Конфиг: allow_size_rules остаётся false; rulebook_view читает без флага.
"""
import itertools
import json
import os
import random
import unittest

import _bootstrap  # noqa: F401
import test_layering as TL
from tb.learnloop import trial as TR
from tb.logic import learn_cond as LC
from tb.logic import learn_rules as LR

P2_SCOPE = {"policy": "P2", "kinds": ["range_5m", "turnover_surge", "hot_enter", "oi_spike", "liq_cascade"], "side": "with_dir",
            "entry": "candle_confirm_limit"}
COND = [{"feature": "spread_bps", "op": ">=", "value": 5.0}]


def rule(rid, kind="skip_when", cond=None, param=None, **kw):
    r = {"rule_id": rid, "kind": kind, "cond": list(cond or COND), "param": {} if param is None else param, "scope": dict(P2_SCOPE),
         "env_norm": "mainnet", "status": "active"}
    r.update(kw)
    return r


def feats(**kw):
    f = dict((k, None) for k in LC.RULE_FEATURES)
    f.update(ev_kind="range_5m", side_rel="with_dir", side="long")
    f.update(kw)
    return f


CAP = rule("RL0010", "size_cap", param={"size_mult": 0.5})
CAP2 = rule("RL0011", "size_cap", cond=[{"feature": "range_5m_pct", "op": "<=", "value": 0.4}], param={"size_mult": 0.25})
CONF = rule("RL0012", "require_confirm", param={})
SKIP = rule("RL0001")
ON = {"allow_size_rules": True}


class TestFlagOff(unittest.TestCase):
    def test_not_created(self):
        for r in (CAP, CAP2, CONF):
            errs = LR.validate_rule(r)
            self.assertTrue(errs and "allow_size_rules=false" in errs[0], errs)
        self.assertEqual(LR.kinds(False), ("skip_when",))

    def test_not_applied_and_first_iteration_shape(self):
        f = feats(spread_bps=99.0, range_5m_pct=0.1)
        r = LR.apply([CAP, CAP2, CONF, SKIP], {"ok": True}, f)
        self.assertEqual(sorted(r), ["apply", "blocked", "eval", "fail", "hits", "missing", "ok"])     # ключей размера нет
        self.assertEqual((r["blocked"], r["hits"], r["fail"]), (["RL0010", "RL0011", "RL0012"], ["RL0001"], "RULE_RL0001"))
        r = LR.apply([CAP], {"ok": True}, f, {"allow_size_rules": False})
        self.assertEqual((r["ok"], r["blocked"], "size_mult" in r), (True, ["RL0010"], False))

    def test_not_tightening(self):
        self.assertFalse(LR.is_tightening([], [CAP]))
        self.assertFalse(LR.is_tightening([SKIP], [SKIP, CAP]))

    def test_config_flag_stays_false(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "learnloop.json")
        with open(path, encoding="utf-8") as fh:
            self.assertIs(json.load(fh)["allow_size_rules"], False)

    def test_view_reads_without_flag(self):
        """Чтение действующей версии (rulebook_view) проверяет правило без allow_size_rules: размерное правило там всегда invalid."""
        self.assertTrue(LR.validate_rule(CAP))


class TestFlagOn(unittest.TestCase):
    def test_validate_param(self):
        self.assertEqual(LR.validate_rule(CAP, True), [])
        self.assertEqual(LR.kinds(True), ("skip_when", "size_cap"))
        for bad in ({}, {"size_mult": 1.0}, {"size_mult": 1.5}, {"size_mult": 0}, {"size_mult": -0.2}, {"size_mult": True}, {"size_mult": "0.5"},
                    {"size_mult": float("nan")}, {"size_mult": 0.5, "leverage": 2}, None):
            self.assertTrue(LR.validate_rule(dict(CAP, param=bad), True), bad)
        self.assertTrue(LR.validate_rule(dict(SKIP, param={"size_mult": 0.5}), True))           # у skip_when параметров нет и при true

    def test_require_confirm_not_available(self):
        errs = LR.validate_rule(CONF, True)
        self.assertTrue(errs and LR.NOT_AVAILABLE in errs[0])
        r = LR.apply([CONF], {"ok": True}, feats(spread_bps=99.0), ON)
        self.assertEqual((r["ok"], r["blocked"], r["size_mult"]), (True, ["RL0012"], 1.0))
        self.assertFalse(LR.is_tightening([], [CONF], allow_size_rules=True))

    def test_apply_reduces_only(self):
        r = LR.apply([CAP, CAP2], {"ok": True}, feats(spread_bps=9.0, range_5m_pct=0.1), ON)
        self.assertEqual((r["ok"], r["fail"], r["hits"], r["size_hits"], r["size_mult"]), (True, None, [], ["RL0010", "RL0011"], 0.25))
        r = LR.apply([CAP, CAP2], {"ok": True}, feats(spread_bps=1.0, range_5m_pct=2.0), ON)
        self.assertEqual((r["size_hits"], r["size_mult"], r["eval"]), ([], 1.0, ["RL0010", "RL0011"]))
        r = LR.apply([CAP], {"ok": True}, feats(spread_bps=None), ON)
        self.assertEqual((r["missing"], r["size_mult"]), (["RL0010"], 1.0))
        r = LR.apply([CAP], {"ok": False}, feats(spread_bps=9.0), ON)                            # ok=false кандидата не снимается
        self.assertEqual((r["ok"], r["size_mult"]), (False, 0.5))
        r = LR.apply([CAP, SKIP], {"ok": True}, feats(spread_bps=9.0), ON)
        self.assertEqual((r["ok"], r["fail"], r["size_mult"]), (False, "RULE_RL0001", 0.5))

    def test_trial_env_and_no_apply(self):
        f = feats(spread_bps=9.0)
        self.assertEqual(LR.apply([dict(CAP, status="trial")], {"ok": True}, f, ON)["size_mult"], 1.0)
        self.assertEqual(LR.apply([CAP], {"ok": True}, f, dict(ON, env_norm="testnet"))["size_mult"], 1.0)
        r = LR.apply({"rules": [CAP], "apply": False}, {"ok": True}, f, ON)
        self.assertEqual((r["apply"], r["size_mult"], r["size_hits"]), (False, 1.0, []))

    def test_non_pure_handler_bounds_blocked(self):
        f = feats(spread_bps=9.0)
        orig = LR.HANDLERS["size_cap"]
        try:
            for bad in ({"size_mult": 1.5}, {"size_mult": 0.0}, {"size_mult": 0.5, "skip": False}, {"size_mult": True}, {"skip": True}, None,
                        {"size_mult": float("nan")}):
                LR.HANDLERS["size_cap"] = lambda r, c, x, _b=bad: _b
                res = LR.apply([CAP], {"ok": True}, f, ON)
                self.assertEqual((res["blocked"], res["size_mult"], res["ok"]), (["RL0010"], 1.0, True), bad)
        finally:
            LR.HANDLERS["size_cap"] = orig

    def test_property1_over_grid(self):
        """Перебор: размерные правила при true не меняют множество разрешённых входов, множитель всегда в (0, 1], добавление правила его не растит."""
        grid = {"spread_bps": (None, 1.0, 5.0, 9.0), "range_5m_pct": (None, 0.1, 0.4, 2.0)}
        pool = [CAP, CAP2, CONF, SKIP]
        for vals in itertools.product(*grid.values()):
            for ev, rel in (("range_5m", "with_dir"), ("range_5m", "against_dir"), (None, None), ("patrol", "with_dir")):
                f = feats(ev_kind=ev, side_rel=rel, **dict(zip(grid, vals)))
                for n in range(0, 4):
                    for rs in itertools.combinations(pool, n):
                        rs = list(rs)
                        r = LR.apply(rs, {"ok": True}, f, ON)
                        skip_only = [x for x in rs if x["kind"] == "skip_when"]
                        self.assertEqual(r["ok"], LR.allowed(skip_only, {"ok": True}, f))
                        self.assertTrue(0.0 < r["size_mult"] <= 1.0)
                        for extra in pool:
                            self.assertLessEqual(LR.apply(rs + [extra], {"ok": True}, f, ON)["size_mult"], r["size_mult"])

    def test_is_tightening(self):
        probes = [({"ok": True}, feats(spread_bps=s, range_5m_pct=g)) for s in (1.0, 9.0) for g in (0.1, 2.0)]
        self.assertTrue(LR.is_tightening([], [CAP], probes, allow_size_rules=True))
        self.assertTrue(LR.is_tightening([CAP], [CAP, CAP2, SKIP], probes, allow_size_rules=True))
        self.assertFalse(LR.is_tightening([CAP, CAP2], [CAP], probes, allow_size_rules=True))                 # снятие правила — ослабление
        looser = dict(CAP, param={"size_mult": 0.9})
        self.assertFalse(LR.is_tightening([CAP], [looser], probes, allow_size_rules=True))                    # подмена множителя вверх


class TestBoundedSize(unittest.TestCase):
    def test_not_above_ceiling_and_only_down(self):
        for sm, ceil, mult in itertools.product((0.0, 0.3, 0.5, 1.0, 2.0), (None, 0.0, 0.25, 0.5, 1.0), (0.1, 0.5, 0.99, 1.0)):
            out = LR.bounded_size_mult(sm, {"size_mult": mult}, ceil)
            top = sm if ceil is None else min(sm, ceil)
            self.assertLessEqual(out, top + 1e-12)
            self.assertLessEqual(out, sm + 1e-12)
            self.assertGreaterEqual(out, 0.0)
            self.assertAlmostEqual(out, top * mult)

    def test_bad_inputs(self):
        self.assertEqual(LR.bounded_size_mult(0.5, {"size_mult": 3.0}, 1.0), 0.5)        # множитель > 1 размер не растит
        self.assertEqual(LR.bounded_size_mult(0.5, {"size_mult": float("nan")}, 1.0), 0.5)
        self.assertEqual(LR.bounded_size_mult(0.5, 0.5, 0.4), 0.2)
        self.assertEqual(LR.bounded_size_mult(None, {"size_mult": 0.5}, 1.0), 0.0)
        self.assertEqual(LR.bounded_size_mult(-1, {"size_mult": 0.5}, 1.0), 0.0)
        self.assertEqual(LR.bounded_size_mult(0.8, {"size_mult": 0.5}, -1), 0.0)
        self.assertEqual(LR.bounded_size_mult(0.8, {}, 0.5), 0.5)                         # без срабатываний — потолок уровня

    def test_with_apply(self):
        res = LR.apply([CAP], {"ok": True}, feats(spread_bps=9.0), ON)
        self.assertEqual(LR.bounded_size_mult(1.0, res, 0.5), 0.25)
        off = LR.apply([CAP], {"ok": True}, feats(spread_bps=9.0))                        # флаг выключен: множителя нет
        self.assertEqual(LR.bounded_size_mult(1.0, off, 0.5), 0.5)


H = 3600


class TestTrialSize(unittest.TestCase):
    START = 1_800_000_000 // H * H

    def crit(self, kind="size_cap", mult=0.5, active=()):
        c = {"v": 1, "metric": "mean_r", "kind": kind, "test": "paired", "side": "lower", "src": {"policy": "P2", "policy_ver": 1, "ctx_ver": "c1",
             "env_norm": "mainnet"}, "cond": LR.canon_cond(COND), "scope": dict(P2_SCOPE), "active": list(active), "n_target": 150,
             "alpha": 0.05, "max_parallel": 5, "alpha_adj": 0.01, "min_effect_r": 0.1, "max_trade_loss_share": 0.3, "overlap_share_max": 0.5,
             "max_days": 30, "start_ts": self.START, "deadline_ts": self.START + 30 * 86400}
        if kind != "skip_when":
            c["param"] = {"size_mult": mult}
        return c

    def rows(self, hours=150, r_in=-0.5, seed=5):
        rng, out, n = random.Random(seed), [], 0
        for i in range(hours):
            h = self.START + i * H
            for spread, r, k in ((10.0, r_in, 1), (1.0, 0.0, 3)):
                for _ in range(k):
                    n += 1
                    out.append({"cluster": "%d:long" % (h // H), "hour": h // H, "side": "long", "key": "K%07d" % n, "t0": h + 60 + n, "type": "range_5m",
                                "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1", "env_norm": "mainnet", "filled": 1,
                                "r_net": r + rng.gauss(0, 0.3), "feats": {"spread_bps": spread, "range_5m_pct": 1.0, "coin_class": None}, "flags": None,
                                "closed_ts": h + H + 60})
        return out

    def test_promote_proportional(self):
        rows = self.rows()
        v = TR.verdict({"criterion": self.crit(mult=0.5)}, rows)
        n = v["numbers"]
        self.assertEqual((v["verdict"], n["kind"], n["n_tc"], n["n_hits"], n["n_rows"], n["loss_share"]), ("promote", "size_cap", 150, 150, 600, 0.125))
        self.assertAlmostEqual(n["in_with_mean"], 0.5 * n["in_mean"], places=5)
        self.assertAlmostEqual(n["gain_r"], -0.5 * n["in_mean"], places=5)
        skip = TR.verdict({"criterion": self.crit("skip_when")}, rows)                    # skip_when на тех же снимках — прежний расчёт
        self.assertEqual((skip["verdict"], skip["numbers"]["loss_share"], "gain_r" in skip["numbers"]), ("promote", 0.25, False))
        self.assertEqual(skip["numbers"]["p"], n["p"])

    def test_reject_small_effect_and_exposure(self):
        rows = self.rows()
        v = TR.verdict({"criterion": self.crit(mult=0.9)}, rows)                          # (1 - 0.9) × 0.5 = 0.05 < 0.1
        self.assertEqual((v["verdict"], v["code"], v["numbers"]["a_ok"]), ("reject", "TRIAL_REJECT", False))
        c = dict(self.crit(mult=0.1), max_trade_loss_share=0.2)                            # экспозиция 150 × 0.9 / 600 = 0.225 > 0.2
        v = TR.verdict({"criterion": c}, rows)
        self.assertEqual((v["verdict"], v["numbers"]["b_ok"], v["numbers"]["loss_share"]), ("reject", False, 0.225))

    def test_not_enough_and_bad_param(self):
        v = TR.verdict({"criterion": self.crit()}, self.rows(hours=20))
        self.assertEqual((v["verdict"], v["numbers"]["n_tc"]), (None, 20))
        for bad in (1.0, 0, None, 2.0):
            v = TR.verdict({"criterion": self.crit(mult=bad)}, self.rows(hours=20))
            self.assertEqual((v["verdict"], v["code"], v["numbers"]["bad_param"]), ("reject", "TRIAL_REJECT", True))

    def test_active_size_rule_does_not_cut_rows(self):
        act = [{"rule_id": "RL0099", "kind": "size_cap", "cond": LR.canon_cond(COND), "scope": dict(P2_SCOPE), "env_norm": "mainnet"}]
        rows = self.rows()
        v = TR.verdict({"criterion": self.crit("skip_when", active=act)}, rows)
        self.assertEqual((v["numbers"]["cut_active"], v["numbers"]["n_hits"]), (0, 150))

    def test_criterion_param_only_for_size(self):
        smp = {"policy": "P2", "policy_ver": 1, "ctx_ver": "c1", "env_norm": "mainnet", "a": 0, "b": H, "cut_ts": H, "h": "x"}
        cfg = {"trial": {"n_target": 150, "max_parallel": 5, "min_effect_r": 0.1, "max_trade_loss_share": 0.3, "overlap_share_max": 0.5, "max_days": 30},
               "verify": {"alpha": 0.05}, "clusters": {"cluster_close_max_h": 48}, "intervals": {"observe_s": 900}}
        base = {"kind": "skip_when", "cond": COND, "scope": dict(P2_SCOPE), "param": {}}
        self.assertNotIn("param", TR.criterion(base, smp, [], cfg, self.START))
        c = TR.criterion(dict(base, kind="size_cap", param={"size_mult": 0.5}), smp, [], cfg, self.START)
        self.assertEqual(c["param"], {"size_mult": 0.5})


class TestPurity(unittest.TestCase):
    def test_module_is_pure(self):
        path = os.path.join(TL.SRC, "logic", "learn_rules.py")
        self.assertEqual(TL.logic_violations(TL.read(path)), [])
        imps = TL.imports_of(TL.read(path), "tb.logic.learn_rules")
        self.assertEqual(sorted(i for i in imps if i.startswith("tb.") and not i.startswith("tb.logic")), [])


if __name__ == "__main__":
    unittest.main()
