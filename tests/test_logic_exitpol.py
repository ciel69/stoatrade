"""R4 (задача 3): политика выхода tb.logic.exitpol — схема, normalize, пол, weaker_than, policy_hash, compile, apply_state, summary.

Свойства design.md: 4 (статическая проверка согласована с композицией для нормализованной политики), 5 (пол из конфига = прежний
_auto_rules по составу и на 500 случайных путях через rules.evaluate_manage). Модель стопа в R (apply_state): безубыток -> уровень пола
прибыли, ступени удержания -> keep(MFE) × MFE, если не ниже пола прибыли; стоп монотонен, поэтому зависит только от MFE пути.
"""
import ast
import copy
import doctest
import json
import os
import random
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
from tb.logic import exitpol as X
from tb.logic import rules
from tb.logic.candles import MarketView

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir)
AP = {"enabled": True, "be_r": 0.7, "trail_activate_r": 1.0, "trail_lock_frac": 0.5, "min_profit_r": 0.2, "stop_slip_bps": 5}
F = X.floor(AP)
EMPTY = {"schema": X.SCHEMA}
CTX = {"max_hold_min": 240.0, "atr5_pct": 0.4, "sig_type": "oi_spike", "horizon": "intraday"}

# меню первой итерации (design.md «Меню первой итерации»); у N1/N2 таблица квантилей появляется при регистрации
MENU = {
    "L1": {"lock": [{"at_r": 1.0, "keep": 0.5}, {"at_r": 1.5, "keep": 0.65}, {"at_r": 2.0, "keep": 0.75}]},
    "L2": {"lock": [{"at_r": 1.0, "keep": 0.5}, {"at_r": 2.0, "keep": 0.65}, {"at_r": 3.0, "keep": 0.75}]},
    "L3": {"lock": [{"at_r": 1.0, "keep": 0.6}, {"at_r": 1.5, "keep": 0.7}, {"at_r": 2.5, "keep": 0.8}]},
    "B1": {"be": {"r": 0.5}},
    "F1": {"partial": [{"at_r": 0.7, "frac": 0.3, "exec": "maker"}]},
    "F2": {"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]},
    "F3": {"partial": [{"at_r": 0.8, "frac": 0.4, "exec": "maker"}]},
    "F4": {"partial": [{"at_r": 1.0, "frac": 0.5, "exec": "maker"}]},
    "F5": {"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "taker"}]},
    "T1": {"trail_atr": {"activate_r": 1.0, "mult": 2.0}},
    "T2": {"trail_atr": {"activate_r": 1.0, "mult": 3.0}},
    "T3": {"trail_atr": {"activate_r": 1.5, "mult": 2.5}},
    "S1": {"stall": {"hold_frac": 0.33, "min_r": 0.5, "keep": 0.8}},
    "D1": {"decay": {"hold_frac": 0.5, "max_mfe_r": 0.5, "max_pnl_r": 0.2, "exec": "maker"}},
    "O1": {"timeout": {"hold_frac": 1.0, "exec": "maker"}},
    "O2": {"timeout": {"hold_frac": 1.5, "exec": "maker"}},
    "N1": {"near_tp": {"q": 0.4, "frac": 0.3, "min_r": 0.5, "exec": "maker"}},
    "N2": {"near_tp": {"q": 0.5, "frac": 0.3, "min_r": 0.5, "exec": "maker"}},
}
MENU["C1"] = dict(MENU["F2"], **MENU["L1"])
MENU["C2"] = dict(MENU["F1"], **MENU["T1"])
MENU["C3"] = dict(MENU["L1"], **MENU["D1"])
MENU["C4"] = dict(MENU["F3"], **MENU["S1"])
MENU["C5"] = dict(MENU["L1"], **MENU["O1"])

FULL = {"schema": X.SCHEMA, "scope": "all", "be": {"r": 0.7},
        "lock": [{"at_r": 1.0, "keep": 0.5}, {"at_r": 1.5, "keep": 0.65}, {"at_r": 2.0, "keep": 0.75}],
        "partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}],
        "trail_atr": {"activate_r": 1.0, "mult": 2.0},
        "stall": {"hold_frac": 0.33, "min_r": 0.5, "keep": 0.8},
        "decay": {"hold_frac": 0.5, "max_mfe_r": 0.5, "max_pnl_r": 0.2, "exec": "maker"},
        "timeout": {"hold_frac": 1.0, "exec": "maker"},
        "near_tp": {"q": 0.4, "frac": 0.3, "min_r": 0.5, "exec": "maker", "table": {"oi_spike": {"intraday": 0.92}}},
        "min_profit_r": 0.2}

# (путь к числу в FULL, ключ диапазона) — проверка каждой границы схемы
BOUNDS = [
    (("be", "r"), "be.r"),
    (("lock", 0, "at_r"), "lock.at_r"), (("lock", 0, "keep"), "lock.keep"),
    (("partial", 0, "at_r"), "partial.at_r"), (("partial", 0, "frac"), "partial.frac"),
    (("trail_atr", "activate_r"), "trail_atr.activate_r"), (("trail_atr", "mult"), "trail_atr.mult"),
    (("stall", "hold_frac"), "stall.hold_frac"), (("stall", "min_r"), "stall.min_r"), (("stall", "keep"), "stall.keep"),
    (("decay", "hold_frac"), "decay.hold_frac"), (("decay", "max_mfe_r"), "decay.max_mfe_r"), (("decay", "max_pnl_r"), "decay.max_pnl_r"),
    (("timeout", "hold_frac"), "timeout.hold_frac"),
    (("near_tp", "q"), "near_tp.q"), (("near_tp", "frac"), "near_tp.frac"), (("near_tp", "min_r"), "near_tp.min_r"),
    (("min_profit_r",), "min_profit_r"),
]


def _set(d, path, v):
    for k in path[:-1]:
        d = d[k]
    d[path[-1]] = v


def _errpath(path):
    s = ""
    for k in path:
        s += ("[%d]" % k) if isinstance(k, int) else (("." if s else "") + k)
    return "E_EXIT_SCHEMA:" + s


def stop_r(policy, floor_policy, mfe):
    return X.apply_state(policy, floor_policy, CTX, {"mfe_r": mfe, "pnl_r": mfe})["stop_r"]


GRID = [round(i * 0.02, 2) for i in range(0, 301)]


def rand_policy(rnd, floor_policy, weaker=None):
    """Случайная политика в диапазонах схемы. weaker: None (любая), "" (не слабее пола) или имя компонента, который сделать слабее."""
    p = {}
    fbe = floor_policy["be"]["r"]
    fmp = floor_policy["min_profit_r"]
    fa, fk = floor_policy["lock"][0]["at_r"], floor_policy["lock"][0]["keep"]
    if rnd.random() < 0.6 or weaker == "be":
        p["be"] = {"r": round(rnd.uniform(fbe + 0.05, 1.5), 2) if weaker == "be" else round(rnd.uniform(0.3, fbe), 2)}
    if (rnd.random() < 0.6 and weaker not in ("be", "min_profit_r")) or weaker == "lock":
        n = rnd.randint(1, 4)
        if weaker == "lock":
            ats = sorted(rnd.sample([round(0.5 + 0.25 * i, 2) for i in range(18)], n))
            kp = sorted(round(rnd.uniform(0.3, 0.95), 2) for _ in range(n))
            # хотя бы при одном MFE >= fa доля ниже пола: первая ступень от fa и выше получает keep < fk
            i0 = next((i for i, a in enumerate(ats) if a >= fa), None)
            if i0 is None:
                kp = [min(k, round(fk - 0.1, 2)) for k in kp]
            else:
                for i in range(0, i0 + 1):
                    kp[i] = min(kp[i], round(fk - 0.1, 2))
            p["lock"] = [{"at_r": a, "keep": k} for a, k in zip(ats, sorted(kp))]
        else:
            first = round(rnd.uniform(0.5, fa), 2)
            ats = [first] + sorted(rnd.sample([round(fa + 0.25 * i, 2) for i in range(1, 16)], n - 1))
            kp = sorted(round(rnd.uniform(fk, 0.95), 2) for _ in range(n))
            p["lock"] = [{"at_r": a, "keep": k} for a, k in zip(ats, kp)]
    if rnd.random() < 0.5 or weaker == "min_profit_r":
        p["min_profit_r"] = round(rnd.uniform(0.0, fmp - 0.05), 2) if weaker == "min_profit_r" else fmp
    for name, comp in (("partial", MENU["F3"]["partial"]), ("trail_atr", MENU["T2"]["trail_atr"]), ("stall", MENU["S1"]["stall"]),
                       ("decay", MENU["D1"]["decay"]), ("timeout", MENU["O2"]["timeout"])):
        if rnd.random() < 0.3:
            p[name] = copy.deepcopy(comp)
    return p


class TestSchema(unittest.TestCase):
    def test_doctests(self):
        self.assertEqual(doctest.testmod(X).failed, 0)

    def test_full_policy_valid(self):
        self.assertEqual(X.validate(FULL), [])

    def test_each_bound(self):
        for path, key in BOUNDS:
            lo, hi = X.RANGES[key]
            for v, ok in ((lo, True), (hi, True), (lo - 0.001, False), (hi + 0.001, False)):
                p = copy.deepcopy(FULL)
                if path[0] == "lock":
                    p["lock"] = [{"at_r": 1.0, "keep": 0.5}]
                if path[0] == "partial":
                    p["partial"] = [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]
                _set(p, path, v)
                errs = X.validate(p)
                if ok:
                    self.assertEqual(errs, [], (path, v))
                else:
                    self.assertIn(_errpath(path), errs, (path, v))

    def test_near_tp_level_bound(self):
        for v, ok in ((0.92, True), (20.0, True), (0.0, False), (-1, False), (20.5, False), ("x", False)):
            p = copy.deepcopy(FULL)
            p["near_tp"]["table"]["oi_spike"]["intraday"] = v
            self.assertEqual(X.validate(p) == [], ok, v)
        p = copy.deepcopy(FULL)
        del p["near_tp"]["table"]
        self.assertEqual(X.validate(p), [])
        p["near_tp"]["table"] = []
        self.assertIn("E_EXIT_SCHEMA:near_tp.table", X.validate(p))

    def test_counts_and_order(self):
        def errs(**kw):
            p = copy.deepcopy(FULL)
            p.update(kw)
            return X.validate(p)
        self.assertIn("E_EXIT_SCHEMA:lock", errs(lock=[]))
        self.assertIn("E_EXIT_SCHEMA:lock", errs(lock=[{"at_r": 1.0 + i, "keep": 0.5} for i in range(5)]))
        self.assertEqual(errs(lock=[{"at_r": 1.0 + i * 0.5, "keep": 0.5} for i in range(4)]), [])
        self.assertIn("E_EXIT_SCHEMA:lock[1].at_r", errs(lock=[{"at_r": 1.5, "keep": 0.5}, {"at_r": 1.0, "keep": 0.6}]))
        self.assertIn("E_EXIT_SCHEMA:lock[1].at_r", errs(lock=[{"at_r": 1.0, "keep": 0.5}, {"at_r": 1.0, "keep": 0.6}]))
        self.assertIn("E_EXIT_SCHEMA:lock[1].keep", errs(lock=[{"at_r": 1.0, "keep": 0.6}, {"at_r": 1.5, "keep": 0.5}]))
        self.assertEqual(errs(lock=[{"at_r": 1.0, "keep": 0.6}, {"at_r": 1.5, "keep": 0.6}]), [])
        self.assertIn("E_EXIT_SCHEMA:partial", errs(partial=[]))
        self.assertIn("E_EXIT_SCHEMA:partial", errs(partial=[{"at_r": 0.5 + i * 0.1, "frac": 0.1, "exec": "maker"} for i in range(4)]))
        self.assertEqual(errs(partial=[{"at_r": 0.5, "frac": 0.3, "exec": "maker"}, {"at_r": 1.0, "frac": 0.3, "exec": "taker"}]), [])
        self.assertIn("E_EXIT_SCHEMA:partial.sum",
                      errs(partial=[{"at_r": 0.5, "frac": 0.3, "exec": "maker"}, {"at_r": 1.0, "frac": 0.31, "exec": "taker"}]))
        self.assertIn("E_EXIT_SCHEMA:partial[1].at_r",
                      errs(partial=[{"at_r": 1.0, "frac": 0.2, "exec": "maker"}, {"at_r": 1.0, "frac": 0.2, "exec": "maker"}]))

    def test_unknown_and_types(self):
        def errs(mut):
            p = copy.deepcopy(FULL)
            mut(p)
            return X.validate(p)
        self.assertIn("E_EXIT_SCHEMA:decay_flow", errs(lambda p: p.update(decay_flow={})))
        self.assertIn("E_EXIT_SCHEMA:be.x", errs(lambda p: p["be"].update(x=1)))
        self.assertIn("E_EXIT_SCHEMA:lock[0].x", errs(lambda p: p["lock"][0].update(x=1)))
        self.assertIn("E_EXIT_SCHEMA:partial[0].x", errs(lambda p: p["partial"][0].update(x=1)))
        self.assertIn("E_EXIT_SCHEMA:timeout.exec", errs(lambda p: p["timeout"].update(exec="limit")))
        self.assertIn("E_EXIT_SCHEMA:timeout.exec", errs(lambda p: p["timeout"].pop("exec")))
        self.assertIn("E_EXIT_SCHEMA:partial[0].exec", errs(lambda p: p["partial"][0].pop("exec")))
        self.assertIn("E_EXIT_SCHEMA:decay.exec", errs(lambda p: p["decay"].update(exec=None)))
        self.assertIn("E_EXIT_SCHEMA:near_tp.exec", errs(lambda p: p["near_tp"].update(exec="x")))
        self.assertIn("E_EXIT_SCHEMA:stall.keep", errs(lambda p: p["stall"].pop("keep")))
        self.assertIn("E_EXIT_SCHEMA:be.r", errs(lambda p: p["be"].update(r=True)))
        self.assertIn("E_EXIT_SCHEMA:be.r", errs(lambda p: p["be"].update(r="0.7")))
        self.assertIn("E_EXIT_SCHEMA:be.r", errs(lambda p: p["be"].update(r=float("nan"))))
        self.assertIn("E_EXIT_SCHEMA:be", errs(lambda p: p.update(be=0.7)))
        self.assertIn("E_EXIT_SCHEMA:scope", errs(lambda p: p.update(scope="scalp")))
        self.assertIn("E_EXIT_SCHEMA:schema", errs(lambda p: p.update(schema="tb.exit_policy/2")))
        self.assertEqual(X.validate("x"), ["E_EXIT_SCHEMA:policy"])
        self.assertEqual(X.validate(FULL), X.validate(copy.deepcopy(FULL)))

    def test_menu_valid_and_not_weaker(self):
        self.assertEqual(len(MENU), 23)
        for vid, pol in MENU.items():
            n = X.normalize(pol, F)
            self.assertEqual(X.validate(n), [], vid)
            self.assertEqual(X.weaker_than(n, F), [], vid)


class TestFloor(unittest.TestCase):
    def test_floor_from_config(self):
        with open(os.path.join(ROOT, "config", "exec_limits.json"), encoding="utf-8") as f:
            ap = json.load(f)["auto_protect"]
        fl = X.floor(ap)
        self.assertEqual(X.validate(fl), [])
        self.assertEqual(X.compile(fl, fl, CTX), rules._auto_rules({"auto_protect": ap}))
        self.assertEqual(X.compile(fl, fl), rules._auto_rules({"auto_protect": ap}))

    def test_floor_random_auto_protect_matches_auto_rules(self):
        rnd = random.Random(5)
        vals = [None, 0, -0.5, 0.3, 0.7, 1.0, 1.4, "0.8", "x", True]
        fracs = [None, 0, 0.5, 0.65, 1, 1.2, -0.1, "0.5"]
        for _ in range(500):
            ap = {"enabled": rnd.choice([True, False, 1, 0, None])}
            for k, pool in (("be_r", vals), ("trail_activate_r", vals), ("trail_lock_frac", fracs), ("min_profit_r", [None, 0, 0.2, 0.5])):
                if rnd.random() < 0.85:
                    ap[k] = rnd.choice(pool)
            fl = X.floor(ap)
            self.assertEqual(X.compile(fl, fl), rules._auto_rules({"auto_protect": ap}), ap)
            self.assertEqual(X.normalize(fl, fl), fl if "schema" in fl else dict(fl, schema=X.SCHEMA))
        self.assertEqual(X.floor(None), EMPTY)
        self.assertEqual(X.compile(EMPTY, EMPTY), [])

    def test_property_5_same_actions_on_random_paths(self):
        """Пол через compile в rules.evaluate_manage даёт те же действия и rt_patch, что прежний _auto_rules (500 путей)."""
        with open(os.path.join(ROOT, "config", "exec_limits.json"), encoding="utf-8") as f:
            limits = json.load(f)
        fl = X.floor(limits["auto_protect"])
        orig = rules._auto_rules
        rnd = random.Random(55)
        for n in range(500):
            side = rnd.choice(["long", "short"])
            s = 1 if side == "long" else -1
            seed = rnd.random()
            runs = []
            for patched in (False, True):
                r2 = random.Random(seed)
                plan = {"side": side, "manage": []}
                rt = {"status": "managing", "pos": {"qty": 1.0, "avg": 100.0, "initial_stop": 100.0 - s * 1.0, "cur_stop": 100.0 - s * 1.0,
                                                     "r_unit": 1.0, "best_px": 100.0}, "rules": {}}
                px, now, log = 100.0, 1000.0, []
                with mock.patch.object(rules, "_auto_rules", (lambda lim: X.compile(fl, fl)) if patched else orig):
                    for _ in range(120):
                        px = max(90.0, min(110.0, px + r2.gauss(0, 0.25)))
                        now += 10.0
                        p = rt["pos"]
                        p["best_px"] = max(p["best_px"], px) if s == 1 else min(p["best_px"], px)
                        v = MarketView("X", px, px - 0.01, px + 0.01, px, now, None, now)
                        res = rules.evaluate_manage(plan, rt, v, now, limits)
                        log.append((res["actions"], res["rt_patch"].get("rules")))
                        for a in res["actions"]:
                            if a["type"] == "move_stop":
                                p["cur_stop"] = a["to"]
                        for rid, st in (res["rt_patch"].get("rules") or {}).items():
                            rt["rules"][rid] = st
                runs.append(log)
            self.assertEqual(runs[0], runs[1], n)


class TestNormalizeWeaker(unittest.TestCase):
    def test_normalize(self):
        p = {"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]}
        before = copy.deepcopy(p)
        n = X.normalize(p, F)
        self.assertEqual(p, before)
        self.assertEqual(n["be"], F["be"])
        self.assertEqual(n["lock"], F["lock"])
        self.assertEqual(n["min_profit_r"], F["min_profit_r"])
        self.assertEqual(n["schema"], X.SCHEMA)
        self.assertEqual(X.normalize(n, F), n)
        n["lock"][0]["keep"] = 0.9
        self.assertEqual(F["lock"][0]["keep"], 0.5)            # копия, а не ссылка на пол
        own = X.normalize({"be": {"r": 0.5}}, F)
        self.assertEqual(own["be"], {"r": 0.5})
        self.assertEqual(X.normalize({}, EMPTY), EMPTY)

    def test_weaker_each_component(self):
        cases = {
            "be": {"be": {"r": 0.8}},
            "lock": {"lock": [{"at_r": 1.0, "keep": 0.45}, {"at_r": 2.0, "keep": 0.8}]},
            "min_profit_r": {"min_profit_r": 0.1},
        }
        for comp, pol in cases.items():
            self.assertEqual(X.weaker_than(X.normalize(pol, F), F), ["E_EXIT_WEAKER:" + comp], comp)
        # первая ступень политики позже активации пола: на MFE [1.0, 1.2) удержание 0 < 0.5
        self.assertEqual(X.weaker_than(X.normalize({"lock": [{"at_r": 1.2, "keep": 0.9}]}, F), F), ["E_EXIT_WEAKER:lock"])
        # раньше и строже — принимается (Edge Case Requirement 2)
        self.assertEqual(X.weaker_than(X.normalize({"be": {"r": 0.4}, "lock": [{"at_r": 0.8, "keep": 0.5}]}, F), F), [])
        # равная полу граница не слабее
        self.assertEqual(X.weaker_than(X.normalize({"be": {"r": 0.7}, "min_profit_r": 0.2}, F), F), [])
        # без нормализации отсутствие замещающего компонента — слабее
        self.assertEqual(X.weaker_than({"schema": X.SCHEMA}, F),
                         ["E_EXIT_WEAKER:be", "E_EXIT_WEAKER:lock", "E_EXIT_WEAKER:min_profit_r"])
        # пустой пол (auto_protect выключен): ничего не проверяется
        self.assertEqual(X.weaker_than(X.normalize({"be": {"r": 1.5}}, EMPTY), EMPTY), [])

    def test_additive_components_do_not_affect_weaker(self):
        rnd = random.Random(11)
        add = {k: FULL[k] for k in X.ADDITIVE}
        for _ in range(300):
            p = X.normalize(rand_policy(rnd, F), F)
            q = dict(p)
            for k in rnd.sample(sorted(add), rnd.randint(1, len(add))):
                q[k] = copy.deepcopy(add[k])
            self.assertEqual(X.weaker_than(p, F), X.weaker_than(q, F))

    def test_property_4_static_check_matches_composition(self):
        """weaker_than(P') пусто => пол ничего не перекрывает: стоп P'∘F == стоп P' без правил пола на любом MFE пути; не пусто =>
        найдётся MFE, где они различаются. Обратное направление проверяется, когда ослабленный компонент не замаскирован другим, более
        строгим компонентом той же политики (ранние ступени lock выше пола прибыли маскируют слабые be и min_profit_r): проверка
        weaker_than по компонентам (Requirement 2.4) в таком случае консервативна — отклоняет вариант, который полом не перекрылся бы."""
        rnd = random.Random(4)
        fl_only = [stop_r({}, F, m) for m in GRID]
        for i in range(800):
            weak = rnd.choice(["", "", "be", "lock", "min_profit_r"])
            p = X.normalize(rand_policy(rnd, F, weaker=weak), F)
            self.assertEqual(X.validate(p), [], p)
            w = X.weaker_than(p, F)
            comp = [stop_r(p, F, m) for m in GRID]
            alone = [stop_r(p, EMPTY, m) for m in GRID]
            if not w:
                self.assertEqual(weak, "", p)
                self.assertEqual(comp, alone, p)
            else:
                self.assertIn("E_EXIT_WEAKER:" + weak, w, p)
                self.assertNotEqual(comp, alone, p)
            # композиция никогда не слабее пола и не слабее самой политики
            for c, a, f in zip(comp, alone, fl_only):
                for x in (a, f):
                    if x is not None:
                        self.assertIsNotNone(c)
                        self.assertGreaterEqual(c, x - 1e-12)

    def test_keep_at(self):
        p = MENU["L1"]
        self.assertEqual([X.keep_at(p, F, m) for m in (0.5, 1.0, 1.49, 1.5, 2.2)], [0.0, 0.5, 0.5, 0.65, 0.75])
        self.assertEqual(X.keep_at({"lock": [{"at_r": 0.8, "keep": 0.4}]}, F, 1.0), 0.5)     # пол строже ступени политики
        self.assertEqual(X.keep_at({}, EMPTY, 3.0), 0.0)


class TestHash(unittest.TestCase):
    def test_hash(self):
        h = X.policy_hash(FULL)
        self.assertEqual(len(h), 16)
        int(h, 16)
        shuffled = json.loads(json.dumps(FULL, sort_keys=False))
        shuffled = dict(reversed(list(shuffled.items())))
        self.assertEqual(X.policy_hash(shuffled), h)
        q = copy.deepcopy(FULL)
        q["be"]["r"] = 0.69
        self.assertNotEqual(X.policy_hash(q), h)
        self.assertNotEqual(X.policy_hash(X.normalize(MENU["F2"], F)), X.policy_hash(X.normalize(MENU["F5"], F)))
        self.assertEqual(X.policy_hash(X.normalize(MENU["L1"], F)), X.policy_hash(X.normalize(copy.deepcopy(MENU["L1"]), F)))


class TestCompile(unittest.TestCase):
    def test_full_ids_order_and_params(self):
        rs = X.compile(FULL, F, CTX)
        self.assertEqual([r["id"] for r in rs], ["auto_be", "auto_lock", "xp_lock", "xp_partial_0", "xp_near", "xp_trail", "xp_stall",
                                                 "xp_decay", "xp_timeout"])
        by = {r["id"]: r for r in rs}
        self.assertEqual(by["auto_be"], rules._auto_rules({"auto_protect": AP})[0])
        self.assertEqual(by["auto_lock"], rules._auto_rules({"auto_protect": AP})[1])
        self.assertEqual(by["xp_lock"]["do"], {"action": "trail", "mode": "mfe_tiers", "tiers": [[1.0, 0.5], [1.5, 0.65], [2.0, 0.75]],
                                                "min_profit_r": 0.2})
        self.assertEqual(by["xp_partial_0"]["do"], {"action": "partial_close", "frac_of_initial": 0.3, "exec": "maker", "at_r": 1.0})
        self.assertTrue(by["xp_partial_0"]["once"])
        self.assertEqual(by["xp_near"]["when"], {"kind": "pnl_r", "gte": 0.92})
        self.assertEqual(by["xp_trail"]["do"], {"action": "trail", "mode": "atr_entry", "mult": 2.0, "atr_pct": 0.4, "min_profit_r": 0.2})
        self.assertEqual(by["xp_stall"]["when"]["all"][0], {"kind": "since_peak_min", "gte": round(0.33 * 240, 6)})
        self.assertEqual(by["xp_decay"]["when"]["all"][0], {"kind": "elapsed", "min": 120.0})
        self.assertEqual(by["xp_decay"]["do"], {"action": "close", "frac": 1.0, "reason": "rule", "exec": "maker"})
        self.assertEqual(by["xp_timeout"]["when"], {"kind": "elapsed", "min": 240.0})
        for r in rs:
            self.assertTrue(r["id"].startswith(("auto_", "xp_")))
            self.assertNotIn(r["do"]["action"], ("add", "move_target"))

    def test_time_from_entry_hold(self):
        a = {r["id"]: r for r in X.compile(MENU["O2"], F, dict(CTX, max_hold_min=60))}
        self.assertEqual(a["xp_timeout"]["when"]["min"], 90.0)
        self.assertNotIn("xp_timeout", [r["id"] for r in X.compile(MENU["O2"], F, {})])
        self.assertNotIn("xp_trail", [r["id"] for r in X.compile(MENU["T1"], F, {"max_hold_min": 60})])
        self.assertNotIn("xp_near", [r["id"] for r in X.compile(FULL, F, dict(CTX, horizon="scalp"))])
        lv = {r["id"]: r for r in X.compile(dict(FULL, near_tp=dict(FULL["near_tp"], table={"oi_spike": {"intraday": 0.3}})), F, CTX)}
        self.assertEqual(lv["xp_near"]["when"]["gte"], 0.5)               # не ниже min_r

    def test_floor_stricter_than_version(self):
        # версия с be слабее (например, пол поднят владельцем после продвижения): действует более строгий пол
        rs = {r["id"]: r for r in X.compile({"be": {"r": 1.2}, "lock": [{"at_r": 1.5, "keep": 0.4}], "min_profit_r": 0.1}, F, CTX)}
        self.assertEqual(rs["auto_be"]["when"]["gte"], 0.7)
        self.assertIn("auto_lock", rs)
        self.assertNotIn("xp_lock", rs)                                    # ступени политики везде под полом
        self.assertNotIn("min_profit_r", rs["auto_be"]["do"])             # пол прибыли композиции = пол
        st = {r["id"]: r for r in X.compile({"min_profit_r": 0.5}, F, CTX)}
        self.assertEqual(st["auto_be"]["do"]["min_profit_r"], 0.5)
        self.assertEqual(st["auto_lock"]["do"]["min_profit_r"], 0.5)

    def test_compile_does_not_mutate_and_is_deterministic(self):
        p, f = copy.deepcopy(FULL), copy.deepcopy(F)
        a = X.compile(p, f, CTX)
        self.assertEqual(p, FULL)
        self.assertEqual(f, F)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(X.compile(FULL, F, dict(CTX)), sort_keys=True))


class TestApplyState(unittest.TestCase):
    def test_stops_and_closes(self):
        st = X.apply_state(MENU["L1"], F, CTX, {"mfe_r": 1.6, "pnl_r": 1.0})
        self.assertAlmostEqual(st["stop_r"], 0.65 * 1.6)
        self.assertEqual(X.apply_state({}, F, CTX, {"mfe_r": 0.69})["stop_r"], None)
        self.assertEqual(X.apply_state({}, F, CTX, {"mfe_r": 0.7})["stop_r"], 0.2)
        self.assertEqual(X.apply_state({}, F, CTX, {"mfe_r": 0.7, "done": ["auto_be"]})["stop_r"], None)
        pc = X.apply_state(FULL, F, CTX, {"mfe_r": 1.0, "pnl_r": 1.0})
        self.assertAlmostEqual(pc["close_frac_initial"], 0.6)              # partial 0.3 + near_tp 0.3 (уровень 0.92)
        self.assertFalse(pc["close_all"])
        to = X.apply_state(MENU["O1"], F, CTX, {"mfe_r": 0.3, "pnl_r": 0.1, "elapsed_min": 240})
        self.assertTrue(to["close_all"])
        self.assertFalse(X.apply_state(MENU["O1"], F, CTX, {"mfe_r": 0.3, "elapsed_min": 239.9})["close_all"])
        dc = X.apply_state(MENU["D1"], F, CTX, {"mfe_r": 0.4, "pnl_r": 0.1, "elapsed_min": 121})
        self.assertTrue(dc["close_all"])
        self.assertFalse(X.apply_state(MENU["D1"], F, CTX, {"mfe_r": 0.6, "pnl_r": 0.1, "elapsed_min": 121})["close_all"])
        sl = X.apply_state(MENU["S1"], F, CTX, {"mfe_r": 0.9, "pnl_r": 0.6, "since_peak_min": 80})
        self.assertAlmostEqual(sl["stop_r"], 0.48)
        self.assertIn("xp_stall", sl["fired"])

    def test_close_never_above_one_and_no_add(self):
        rnd = random.Random(9)
        for _ in range(500):
            p = X.normalize(rand_policy(rnd, F), F)
            st = X.apply_state(p, F, CTX, {"mfe_r": rnd.uniform(0, 5), "elapsed_min": rnd.uniform(0, 400)})
            self.assertLessEqual(st["close_frac_initial"], 1.0)
            for r in X.compile(p, F, CTX):
                self.assertIn(r["do"]["action"], ("move_stop", "trail", "partial_close", "close"))

    def test_monotone_in_mfe(self):
        rnd = random.Random(2)
        for _ in range(150):
            p = X.normalize(rand_policy(rnd, F, weaker=""), F)
            prev = None
            for m in GRID:
                s = stop_r(p, F, m)
                if prev is not None:
                    self.assertIsNotNone(s)
                    self.assertGreaterEqual(s, prev - 1e-12)
                prev = s if s is not None else prev


class TestSummary(unittest.TestCase):
    def test_summary(self):
        s = X.summary(MENU["L1"], F, {"mfe_r": 1.2})
        self.assertEqual(s["next"], {"kind": "lock", "at_r": 1.5, "keep": 0.65})
        self.assertEqual(s["keep"], 0.5)
        s = X.summary(MENU["C5"], F, {"mfe_r": 0.1}, CTX)
        self.assertEqual(s["next"]["kind"], "be")
        self.assertEqual(s["timeout_min"], 240.0)
        self.assertIn("закрытие через 240 мин", s["text"])
        s = X.summary(MENU["F2"], F, {"mfe_r": 0.8, "done": ["xp_partial_0"]})
        self.assertEqual(s["next"]["kind"], "lock")
        self.assertEqual(X.summary({}, EMPTY, {})["text"], "код: только стоп")
        self.assertLessEqual(len(X.summary(FULL, F, {"mfe_r": 0.0}, CTX)["text"]), 160)


class TestPurity(unittest.TestCase):
    def test_imports_only_stdlib_and_tb_logic(self):
        path = os.path.join(ROOT, "src", "tb", "logic", "exitpol.py")
        tree = ast.parse(open(path, encoding="utf-8").read())
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                mods.add(n.module or "")
        self.assertTrue(mods <= {"copy", "hashlib", "json", "math"} | {m for m in mods if m.startswith("tb.logic")}, mods)


if __name__ == "__main__":
    unittest.main()
