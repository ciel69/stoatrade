"""tb.learnloop.conf: строгая загрузка config/learnloop.json (R2, задача 5; Requirements 4.2, 15.3)."""
import copy
import json
import os
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb import paths
from tb.learnloop import conf as C

PROJECT_CFG = paths.config("learnloop.json", root=paths.DEFAULT_ROOT)


def shipped():
    with open(PROJECT_CFG, encoding="utf-8") as f:
        return json.load(f)


class Root(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tb-llconf-")
        os.makedirs(os.path.join(self.root, "config"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, obj_or_text):
        text = obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text)
        with open(paths.config("learnloop.json", root=self.root), "w", encoding="utf-8") as f:
            f.write(text)

    def assertDisabled(self, cfg):
        self.assertFalse(C.is_enabled(cfg))
        self.assertEqual(C.promotion_mode(cfg), "off")
        self.assertNotIn("verify", cfg)          # порогов в выключенном конфиге нет


class TestShipped(unittest.TestCase):
    def test_project_config_valid(self):
        cfg, errs = C.load(paths.DEFAULT_ROOT)
        self.assertEqual(errs, [])
        self.assertEqual(cfg["schema"], C.SCHEMA)

    def test_shipped_values(self):
        cfg = C.load_strict(paths.DEFAULT_ROOT)
        self.assertEqual(cfg["promotion"]["mode"], "off")          # поставка разработки; "auto" — шаг приёмки (задача 23)
        self.assertIs(cfg["allow_size_rules"], False)
        self.assertEqual(cfg["verify"]["holdout_days"], 7)
        self.assertEqual(cfg["trial"]["n_target"], 150)
        self.assertEqual(cfg["verify"]["min_n_menu"], 88)
        self.assertEqual(cfg["verify"]["alpha"], 0.05)
        self.assertEqual(cfg["promotion"]["warmup_days"], 7)
        self.assertEqual(cfg["promotion"]["warmup_window_days"], 10)
        for path in ("trial.overlap_share_max", "promotion.loss_cap_wait_days", "restore.restore_ack_timeout_s",
                     "intervals.observe_s", "apply.event_max_age_s", "apply.feat_missing_max", "days.day_min_work_h",
                     "days.observe_ok_share", "lease.lease_h", "promotion.watch_fresh_h", "promotion.step_error_max",
                     "verify.menu_every_days", "clusters.grace_min", "clusters.cluster_close_max_h",
                     "trial.reach_factor", "trial.reach_lookback_days", "trial.wait_max_days", "blocklist.cooldown_days",
                     "watch.alpha_watch_weekly", "watch.starvation_window_days", "events_per_day",
                     "promotion.warmup_stuck_days", "verify.feat_cover_min", "intervals.hypref_s"):
            node = cfg
            for part in path.split("."):
                self.assertIn(part, node, path)
                node = node[part]
        for k in ("port", "brain_ref", "freeze", "violations", "prefilter_signals", "prefilter_cases"):
            self.assertIn(k, cfg["second_iteration"])

    def test_alpha_only_in_learnloop_json(self):
        cfg_dir = os.path.dirname(PROJECT_CFG)
        for name in os.listdir(cfg_dir):
            if name.endswith(".json") and name != "learnloop.json":
                with open(os.path.join(cfg_dir, name), encoding="utf-8") as f:
                    self.assertNotIn('"alpha"', f.read(), name)


class TestFormula(unittest.TestCase):
    def test_reference(self):
        self.assertEqual(C.min_n_menu_formula(150, 0.8, 30, 14), 88)    # ceil(150 / 24 × 14) = ceil(87.5)

    def test_exact_arithmetic(self):
        # 100 / (0.5 × 20) × 10 = 100 ровно: без точной арифметики float мог бы дать 100.00000000000001 -> 101
        self.assertEqual(C.min_n_menu_formula(100, 0.5, 20, 10), 100)
        self.assertEqual(C.min_n_menu_formula(30, 0.1, 3, 1), 100)       # 0.1 × 3 в float = 0.30000000000000004
        self.assertEqual(C.min_n_menu_formula(151, 0.8, 30, 14), 89)


class TestLoad(Root):
    def test_valid_roundtrip(self):
        self.write(shipped())
        cfg, errs = C.load(self.root)
        self.assertEqual(errs, [])
        self.assertFalse(C.is_enabled(cfg))              # в поставке разработки выключен; включает шаг приёмки (задача 23)
        self.assertEqual(cfg, shipped())
        on = shipped()
        on["enabled"] = True
        self.write(on)
        cfg, errs = C.load(self.root)
        self.assertEqual(errs, [])
        self.assertTrue(C.is_enabled(cfg))

    def test_no_file_disables(self):
        cfg, errs = C.load(self.root)
        self.assertEqual(errs, ["E_CONF:nofile"])
        self.assertDisabled(cfg)
        with self.assertRaises(C.ConfError):
            C.load_strict(self.root)

    def test_broken_json_disables(self):
        for text in ("{", "", "[]", "null", '"x"', "{\"schema\": NaN}"):
            self.write(text)
            cfg, errs = C.load(self.root)
            self.assertEqual(errs, ["E_CONF:file"], text)
            self.assertDisabled(cfg)

    def test_duplicate_key_rejected(self):
        text = json.dumps(shipped())
        self.write(text[:-1] + ', "enabled": true}')
        cfg, errs = C.load(self.root)
        self.assertEqual(errs, ["E_CONF:file"])
        self.assertDisabled(cfg)

    def test_nan_value_rejected(self):
        d = shipped()
        text = json.dumps(d).replace('"alpha": 0.05', '"alpha": NaN')
        self.write(text)
        cfg, errs = C.load(self.root)
        self.assertEqual(errs, ["E_CONF:file"])
        self.assertDisabled(cfg)

    def test_unknown_key_rejected(self):
        for mut, code in ((lambda d: d.__setitem__("extra", 1), "E_CONF:unknown:extra"),
                          (lambda d: d["verify"].__setitem__("alpah", 0.05), "E_CONF:unknown:verify.alpah"),
                          (lambda d: d["second_iteration"]["port"].__setitem__("x", 1),
                           "E_CONF:unknown:second_iteration.port.x")):
            d = shipped()
            mut(d)
            self.write(d)
            cfg, errs = C.load(self.root)
            self.assertIn(code, errs)
            self.assertDisabled(cfg)

    def test_missing_key_rejected(self):
        for path in ("verify.alpha", "promotion.mode", "second_iteration.freeze", "enabled", "trial.overlap_share_max"):
            d = shipped()
            node = d
            parts = path.split(".")
            for p in parts[:-1]:
                node = node[p]
            del node[parts[-1]]
            self.write(d)
            cfg, errs = C.load(self.root)
            self.assertIn("E_CONF:missing:" + path, errs)
            self.assertDisabled(cfg)

    def test_bad_values_rejected(self):
        cases = [("schema", "tb.learnloop_cfg/2"), ("enabled", 1), ("allow_size_rules", "false"),
                 ("promotion.mode", "on"), ("promotion.mode", "AUTO"), ("verify.alpha", 0), ("verify.alpha", 0.6),
                 ("verify.alpha", True), ("verify.holdout_days", 7.5), ("verify.holdout_days", 0),
                 ("verify.policy", "P 2"), ("trial.reach_factor", 0), ("trial.reach_factor", 1.5),
                 ("days.observe_ok_share", -0.1), ("watch.looks", [100, 50]), ("watch.looks", []),
                 ("watch.looks", [50, 50]), ("intervals.daily_after", "24:00Z"), ("intervals.daily_after", "00:05"),
                 ("stale_min", None), ("second_iteration.freeze.freeze_trade_share", 2),
                 ("clusters.grace_min", -1), ("events_per_day", "500")]
        for path, val in cases:
            d = shipped()
            node = d
            parts = path.split(".")
            for p in parts[:-1]:
                node = node[p]
            node[parts[-1]] = val
            self.write(d)
            cfg, errs = C.load(self.root)
            self.assertIn("E_CONF:value:" + path, errs, (path, val))
            self.assertDisabled(cfg)

    def test_section_type_rejected(self):
        d = shipped()
        d["verify"] = 5
        self.write(d)
        cfg, errs = C.load(self.root)
        self.assertIn("E_CONF:type:verify", errs)
        self.assertDisabled(cfg)

    def test_min_n_menu_formula_enforced(self):
        for val in (87, 89, 30):
            d = shipped()
            d["verify"]["min_n_menu"] = val
            self.write(d)
            cfg, errs = C.load(self.root)
            self.assertEqual(errs, ["E_CONF:formula:verify.min_n_menu"], val)
            self.assertDisabled(cfg)

    def test_min_n_menu_follows_inputs(self):
        d = shipped()
        d["trial"]["n_target"] = 151                     # формула -> 89: прежние 88 отклоняются, 89 принимается
        self.write(d)
        self.assertIn("E_CONF:formula:verify.min_n_menu", C.load(self.root)[1])
        d["verify"]["min_n_menu"] = 89
        self.write(d)
        cfg, errs = C.load(self.root)
        self.assertEqual(errs, [])
        self.assertEqual(cfg["verify"]["min_n_menu"], 89)

    def test_cross_checks(self):
        d = shipped()
        d["verify"]["discovery_days_min"] = 15
        self.write(d)
        self.assertIn("E_CONF:cross:verify.discovery_days_min", C.load(self.root)[1])
        d = shipped()
        d["promotion"]["warmup_days"] = 11
        self.write(d)
        self.assertIn("E_CONF:cross:promotion.warmup_days", C.load(self.root)[1])

    def test_enabled_false_and_auto(self):
        d = shipped()
        d["enabled"] = False
        d["promotion"]["mode"] = "auto"
        self.write(d)
        cfg, errs = C.load(self.root)
        self.assertEqual(errs, [])
        self.assertFalse(C.is_enabled(cfg))
        self.assertEqual(C.promotion_mode(cfg), "auto")

    def test_disabled_is_copy(self):
        a = C.disabled()
        a["enabled"] = True
        self.assertFalse(C.DISABLED["enabled"])


class TestHash(Root):
    def test_hash_stable_across_formatting(self):
        d = shipped()
        self.write(json.dumps(d, indent=4))
        h1 = C.config_hash(C.load_strict(self.root))
        reordered = dict(reversed(list(d.items())))
        self.write(json.dumps(reordered, separators=(",", ":")))
        h2 = C.config_hash(C.load_strict(self.root))
        self.assertEqual(h1, h2)
        self.assertRegex(h1, r"^[0-9a-f]{64}$")

    def test_hash_changes_with_value(self):
        d = shipped()
        e = copy.deepcopy(d)
        e["verify"]["alpha"] = 0.04
        self.assertNotEqual(C.config_hash(d), C.config_hash(e))

    def test_hash_deterministic(self):
        self.assertEqual(C.config_hash(shipped()), C.config_hash(shipped()))


if __name__ == "__main__":
    unittest.main()
