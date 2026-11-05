"""R4 задача 4: config/exits.json и строгий загрузчик tb.learnloop.exits_conf (Requirements 16.1, 16.2, 5.1, 1.1, 4.4).

Каждая граница схемы, битое меню, вариант слабее пола, hold_mult меньше тайм-аута меню, формула min_clusters, битый файл -> DISABLED.
"""
import copy
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
from tb.learnloop import exits_conf as XC
from tb.logic import exitpol as XP

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir)
with open(os.path.join(ROOT, "config", "exits.json"), encoding="utf-8") as _f:
    SHIPPED = json.load(_f)
AP = {"enabled": True, "be_r": 0.7, "trail_activate_r": 1.0, "trail_lock_frac": 0.5, "min_profit_r": 0.2, "stop_slip_bps": 5}
F = XP.floor(AP)
DESIGN_IDS = {"L1", "L2", "L3", "B1", "F1", "F2", "F3", "F4", "F5", "T1", "T2", "T3", "S1", "D1", "O1", "O2", "N1", "N2",
              "C1", "C2", "C3", "C4", "C5"}

# (путь, нижняя, верхняя, вид): int — целые включительно; num — числа включительно; open — нижняя граница не входит
BOUNDS = [
    ("paths.keep_days", 1, 3650, "int"), ("paths.hold_mult", 1.0, 3.0, "num"), ("paths.reserve_min", 0, 1440, "int"),
    ("paths.post_close_min", 0, 1440, "int"), ("paths.trade_retry_h", 1, 720, "int"),
    ("eval.every_s", 60, 86400, "int"), ("eval.max_per_pass", 1, 10000000, "int"), ("eval.keep_days_menu", 1, 3650, "int"),
    ("maker.through_ticks", 0, 100, "int"), ("maker.through_bps", 0.0, 100.0, "num"), ("maker.wait_s", 1, 3600, "int"),
    ("search.every_days", 1, 3650, "int"), ("search.window_days", 1, 3650, "int"), ("search.max_k", 1, 100, "int"),
    ("search.perm_b", 100, 1000000, "int"), ("search.alpha", 0.0, 0.5, "open"), ("search.min_effect_r", 0.0, 10.0, "num"),
    ("search.min_clusters", 2, 1000000, "int"),
    ("trial.n_target", 2, 1000000, "int"), ("trial.max_parallel", 1, 10, "int"), ("trial.alpha", 0.0, 0.5, "open"),
    ("trial.min_effect_r", 0.0, 10.0, "num"), ("trial.max_days", 1, 3650, "int"),
    ("tail.mfe_r", 0.0, 10.0, "open"), ("tail.keep", 0.0, 1.0, "open"), ("tail.min_n", 1, 1000000, "int"),
    ("robust.tol_r", 0.0, 10.0, "num"),
    ("brain.n", 1, 1000000, "int"), ("brain.min_n", 1, 1000000, "int"), ("brain.tol_r", 0.0, 10.0, "num"),
    ("promotion.wait_days", 1, 3650, "int"), ("promotion.watch_fresh_h", 1, 168, "int"),
    ("watch.weekly_window", 1, 1000000, "int"), ("watch.alpha", 0.0, 0.5, "open"), ("watch.alpha_weekly", 0.0, 0.5, "open"),
    ("watch.rollback_delta_r", 0.0, 10.0, "num"), ("watch.brain_min_n", 1, 1000000, "int"), ("watch.brain_delta_r", 0.0, 10.0, "num"),
    ("watch.brain_alpha", 0.0, 0.5, "open"), ("watch.sim_gap_r", 0.0, 10.0, "num"), ("watch.sim_gap_days", 1, 3650, "int"),
    ("watch.sim_gap_min_n", 1, 1000000, "int"),
    ("lease.lease_h", 1, 720, "int"), ("cooldown_days", 1, 3650, "int"), ("menu_ver", 1, 1000000, "int"),
]


def _set(d, path, v):
    ks = path.split(".")
    for k in ks[:-1]:
        d = d[k]
    d[ks[-1]] = v


def _del(d, path):
    ks = path.split(".")
    for k in ks[:-1]:
        d = d[k]
    del d[ks[-1]]


def _walk_errs(raw):
    errs = []
    XC._walk(XC.SPEC, raw, "", errs)
    return errs


def _variant(cfg, vid):
    return next(it for it in cfg["menu"] if it["id"] == vid)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tb_exits_conf_")
        os.makedirs(os.path.join(self.root, "config"))
        self.addCleanup(shutil.rmtree, self.root, True)

    def write(self, cfg=None, text=None):
        with open(os.path.join(self.root, "config", "exits.json"), "w", encoding="utf-8") as f:
            f.write(text if text is not None else json.dumps(SHIPPED if cfg is None else cfg))

    def errs(self, cfg, floor_policy=F):
        self.write(cfg)
        got, errs = XC.load(self.root, floor_policy=floor_policy)
        if errs:
            self.assertEqual(got, XC.DISABLED)
        return errs


class TestShipped(Base):
    def test_shipped_config_loads(self):
        cfg, errs = XC.load(ROOT)                                              # пол из действующих лимитов исполнителя
        self.assertEqual(errs, [])
        self.assertEqual(cfg, SHIPPED)
        self.assertTrue(XC.is_enabled(cfg))
        self.assertEqual(XC.promotion_mode(cfg), "auto")

    def test_measured_values(self):
        """Ревизия design.md 2026-11-04 по замеру задачи 2: block_h = 24, n_target = 30, min_clusters = 23."""
        self.assertEqual(SHIPPED["unit"]["block_h"], 24)
        self.assertEqual(SHIPPED["trial"]["n_target"], 30)
        self.assertEqual(SHIPPED["search"]["min_clusters"], 23)
        self.assertEqual(XC.min_clusters_formula(30), 23)
        self.assertEqual(XC.min_clusters_formula(200), 150)
        self.assertEqual(SHIPPED["tail"]["keep"], 0.8)                          # решение владельца 4

    def test_menu_design_table(self):
        menu = SHIPPED["menu"]
        self.assertEqual(len(menu), 23)
        self.assertEqual({it["id"] for it in menu}, DESIGN_IDS)
        self.assertLessEqual(len(menu), SHIPPED["search"]["max_k"])
        for v in XC.variants(SHIPPED, F):
            self.assertEqual(XP.validate(v["policy"]), [], v["id"])
            self.assertEqual(XP.weaker_than(v["policy"], F), [], v["id"])
            self.assertEqual(v["policy_h"], XP.policy_hash(XP.normalize(_variant(SHIPPED, v["id"])["policy"], F)))
        self.assertEqual(len({v["policy_h"] for v in XC.variants(SHIPPED, F)}), 23)    # все варианты различны после нормализации
        self.assertEqual(_variant(SHIPPED, "C1")["policy"], dict(_variant(SHIPPED, "F2")["policy"], **_variant(SHIPPED, "L1")["policy"]))
        self.assertEqual(_variant(SHIPPED, "C5")["policy"], dict(_variant(SHIPPED, "L1")["policy"], **_variant(SHIPPED, "O1")["policy"]))
        self.assertEqual(XC.max_timeout_frac(SHIPPED["menu"]), 1.5)
        self.assertGreaterEqual(SHIPPED["paths"]["hold_mult"], XC.max_timeout_frac(SHIPPED["menu"]))

    def test_loader_only_reads(self):
        self.write()
        p = os.path.join(self.root, "config", "exits.json")
        before = (os.path.getmtime(p), sorted(os.listdir(self.root)), sorted(os.listdir(os.path.join(self.root, "config"))))
        XC.load(self.root)
        XC.load(self.root, floor_policy=F)
        self.assertEqual(before, (os.path.getmtime(p), sorted(os.listdir(self.root)), sorted(os.listdir(os.path.join(self.root, "config")))))

    def test_floor_from_exec_limits_of_root(self):
        """Без явного пола загрузчик берёт auto_protect лимитов исполнителя корня; лимиты не читаются -> E_CONF:floor."""
        self.write()
        self.assertEqual(XC.load(self.root)[1], [])                              # нет exec_limits.json -> умолчания исполнителя
        with open(os.path.join(self.root, "config", "exec_limits.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "auto_protect": dict(AP, be_r=0.4)}, f)
        self.assertIn("E_CONF:menu.B1:E_EXIT_WEAKER:be", XC.load(self.root)[1])  # пол выше: ранний безубыток 0.5 уже слабее
        with open(os.path.join(self.root, "config", "exec_limits.json"), "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertEqual(XC.load(self.root), (XC.DISABLED, ["E_CONF:floor"]))


class TestFile(Base):
    def test_no_file(self):
        self.assertEqual(XC.load(self.root, floor_policy=F), (XC.DISABLED, ["E_CONF:nofile"]))
        with self.assertRaises(XC.ConfError):
            XC.load_strict(self.root, floor_policy=F)

    def test_broken_file(self):
        for text in ("{", "[]", '{"schema": "tb.exits_cfg/1", "schema": "x"}', '{"a": NaN}', "﻿{}", ""):
            self.write(text=text)
            cfg, errs = XC.load(self.root, floor_policy=F)
            self.assertEqual(cfg, XC.DISABLED, text)
            self.assertTrue(errs and all(e.startswith("E_CONF:") for e in errs), text)
        self.assertFalse(XC.is_enabled(XC.DISABLED))
        self.assertEqual(XC.promotion_mode(XC.DISABLED), "off")
        self.assertIsNot(XC.disabled(), XC.DISABLED)

    def test_unknown_missing_type(self):
        c = copy.deepcopy(SHIPPED)
        c["foo"] = 1
        self.assertEqual(self.errs(c), ["E_CONF:foo"])
        c = copy.deepcopy(SHIPPED)
        c["paths"]["foo"] = 1
        self.assertEqual(self.errs(c), ["E_CONF:paths.foo"])
        for path in ("paths.keep_days", "unit.block_h", "trial.n_target", "watch.looks", "menu", "menu_ver", "enabled"):
            c = copy.deepcopy(SHIPPED)
            _del(c, path)
            self.assertIn("E_CONF:" + path, self.errs(c), path)                 # числовых умолчаний нет: отсутствие ключа — ошибка
        c = copy.deepcopy(SHIPPED)
        c["paths"] = [1]
        self.assertEqual(self.errs(c), ["E_CONF:paths"])

    def test_each_bound(self):
        for path, lo, hi, kind in BOUNDS:
            good = [hi] if kind == "open" else [lo, hi]
            bad = [lo - 1, hi + 1, lo + 0.5, True, "1", None] if kind == "int" else [lo - 0.001, hi + 0.001, "1", True, None]
            if kind == "open":
                bad.append(lo)
            for v in good:
                c = copy.deepcopy(SHIPPED)
                _set(c, path, v)
                self.assertNotIn("E_CONF:" + path, _walk_errs(c), (path, v))
            for v in bad:
                c = copy.deepcopy(SHIPPED)
                _set(c, path, v)
                self.assertEqual(_walk_errs(c), ["E_CONF:" + path], (path, v))
                self.assertIn("E_CONF:" + path, self.errs(c), (path, v))

    def test_enums_and_lists(self):
        cases = [("schema", "tb.exits_cfg/2"), ("enabled", 1), ("unit.block_h", 2), ("unit.block_h", 12), ("unit.block_h", 0),
                 ("brain.mode", "demo"), ("promotion.mode", "manual"), ("paths.policies", []), ("paths.policies", ["P2", "P2"]),
                 ("paths.policies", ["P-2"]), ("paths.policies", "P2"), ("watch.looks", [100, 50]), ("watch.looks", []),
                 ("watch.looks", [50, 50]), ("menu", []), ("menu", {})]
        for path, v in cases:
            c = copy.deepcopy(SHIPPED)
            _set(c, path, v)
            self.assertIn("E_CONF:" + path, self.errs(c), (path, v))
        for v in (1, 4, 24):
            c = copy.deepcopy(SHIPPED)
            c["unit"]["block_h"] = v
            self.assertEqual(self.errs(c), [], v)
        for v in ("paper", "testnet", "live"):
            c = copy.deepcopy(SHIPPED)
            c["brain"]["mode"] = v
            self.assertEqual(self.errs(c), [], v)


class TestMenu(Base):
    def test_broken_variant(self):
        c = copy.deepcopy(SHIPPED)
        del c["menu"][0]["policy"]
        self.assertEqual(self.errs(c), ["E_CONF:menu[0]"])
        c = copy.deepcopy(SHIPPED)
        c["menu"][0]["extra"] = 1
        self.assertEqual(self.errs(c), ["E_CONF:menu[0]"])
        c = copy.deepcopy(SHIPPED)
        c["menu"][2]["id"] = "l3"
        self.assertEqual(self.errs(c), ["E_CONF:menu[2]"])
        c = copy.deepcopy(SHIPPED)
        c["menu"][3] = "B1"
        self.assertEqual(self.errs(c), ["E_CONF:menu[3]"])
        c = copy.deepcopy(SHIPPED)
        c["menu"][1]["id"] = "L1"                                               # повтор (id, ver)
        self.assertEqual(self.errs(c), ["E_CONF:menu.L1.ver"])
        c["menu"][1]["ver"] = 2                                                 # новая версия того же id допустима
        self.assertEqual(self.errs(c), [])
        c = copy.deepcopy(SHIPPED)
        c["menu"][0]["ver"] = 0
        self.assertEqual(self.errs(c), ["E_CONF:menu.L1.ver"])
        c = copy.deepcopy(SHIPPED)
        _variant(c, "L1")["policy"] = {}
        self.assertEqual(self.errs(c), ["E_CONF:menu.L1.policy"])

    def test_family(self):
        for vid, fam in (("L1", "partial"), ("C1", "lock"), ("T1", "combo"), ("B1", "breakeven")):
            c = copy.deepcopy(SHIPPED)
            _variant(c, vid)["family"] = fam
            self.assertEqual(self.errs(c), ["E_CONF:menu.%s.family" % vid], (vid, fam))

    def test_schema_errors_of_variant(self):
        c = copy.deepcopy(SHIPPED)
        _variant(c, "L1")["policy"]["lock"][0]["keep"] = 1.2
        self.assertIn("E_CONF:menu.L1:E_EXIT_SCHEMA:lock[0].keep", self.errs(c))
        c = copy.deepcopy(SHIPPED)
        _variant(c, "T1")["policy"]["decay_flow"] = {}
        self.assertIn("E_CONF:menu.T1:E_EXIT_SCHEMA:decay_flow", self.errs(c))
        c = copy.deepcopy(SHIPPED)
        _variant(c, "F1")["policy"]["partial"][0]["exec"] = "limit"
        self.assertEqual(self.errs(c), ["E_CONF:menu.F1:E_EXIT_SCHEMA:partial[0].exec"])
        c = copy.deepcopy(SHIPPED)
        _variant(c, "O2")["policy"]["timeout"]["hold_frac"] = 1.6
        self.assertIn("E_CONF:menu.O2:E_EXIT_SCHEMA:timeout.hold_frac", self.errs(c))

    def test_variant_weaker_than_floor(self):
        c = copy.deepcopy(SHIPPED)
        _variant(c, "B1")["policy"]["be"]["r"] = 0.9
        self.assertEqual(self.errs(c), ["E_CONF:menu.B1:E_EXIT_WEAKER:be"])
        c = copy.deepcopy(SHIPPED)
        _variant(c, "L2")["policy"]["lock"][0]["keep"] = 0.45
        self.assertEqual(self.errs(c), ["E_CONF:menu.L2:E_EXIT_WEAKER:lock"])
        c = copy.deepcopy(SHIPPED)
        _variant(c, "T2")["policy"]["min_profit_r"] = 0.1
        self.assertEqual(self.errs(c), ["E_CONF:menu.T2:E_EXIT_WEAKER:min_profit_r"])
        self.assertEqual(self.errs(SHIPPED, floor_policy=XP.floor(dict(AP, trail_lock_frac=0.55))),
                         ["E_CONF:menu.%s:E_EXIT_WEAKER:lock" % v for v in ("L1", "L2", "C1", "C3", "C5")])
        self.assertEqual(self.errs(SHIPPED, floor_policy=XP.floor(dict(AP, enabled=False))), [])   # пустой пол: слабее не бывает

    def test_menu_longer_than_max_k(self):
        c = copy.deepcopy(SHIPPED)
        c["search"]["max_k"] = 22
        self.assertEqual(self.errs(c), ["E_CONF:menu"])
        c["search"]["max_k"] = 23
        self.assertEqual(self.errs(c), [])


class TestCross(Base):
    def test_hold_mult_below_menu_timeout(self):
        c = copy.deepcopy(SHIPPED)
        c["paths"]["hold_mult"] = 1.4                                           # O2 держит до 1.5 x срока
        self.assertEqual(self.errs(c), ["E_CONF:paths.hold_mult"])
        c["paths"]["hold_mult"] = 1.5
        self.assertEqual(self.errs(c), [])
        c = copy.deepcopy(SHIPPED)
        c["menu"] = [it for it in c["menu"] if it["id"] != "O2"]
        c["paths"]["hold_mult"] = 1.0                                           # без O2 наибольший тайм-аут 1.0
        self.assertEqual(self.errs(c), [])
        c["paths"]["hold_mult"] = 0.9
        self.assertEqual(self.errs(c), ["E_CONF:paths.hold_mult"])

    def test_min_clusters_formula(self):
        c = copy.deepcopy(SHIPPED)
        c["search"]["min_clusters"] = 22
        self.assertEqual(self.errs(c), ["E_CONF:search.min_clusters"])
        c["trial"]["n_target"], c["search"]["min_clusters"] = 200, 150
        self.assertEqual(self.errs(c), [])
        c["trial"]["n_target"] = 201
        self.assertEqual(self.errs(c), ["E_CONF:search.min_clusters"])

    def test_brain_min_n(self):
        c = copy.deepcopy(SHIPPED)
        c["brain"]["min_n"] = 61
        self.assertEqual(self.errs(c), ["E_CONF:brain.min_n"])
        c["brain"]["min_n"] = 60
        self.assertEqual(self.errs(c), [])


class TestHash(unittest.TestCase):
    def test_config_hash(self):
        h = XC.config_hash(SHIPPED)
        self.assertEqual(len(h), 64)
        self.assertEqual(h, XC.config_hash(json.loads(json.dumps(SHIPPED, indent=3, sort_keys=True))))
        c = copy.deepcopy(SHIPPED)
        c["trial"]["max_days"] = 22
        self.assertNotEqual(h, XC.config_hash(c))

    def test_shipped_file_hash_after_load(self):
        with mock.patch.object(XC, "current_floor", return_value=F):
            cfg, errs = XC.load(ROOT)
        self.assertEqual(errs, [])
        self.assertEqual(XC.config_hash(cfg), XC.config_hash(SHIPPED))


if __name__ == "__main__":
    unittest.main()
