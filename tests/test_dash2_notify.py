"""Тема `notify` нового дашборда (tb.dash2.topics.notify) и доступность notify.set / notify.test через мост; расширение темы `settings` (шаблоны провайдеров, профили).
Временный корень, без платформы, сети и живых команд."""
import json
import os
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb.control import protocol
from tb.dash2 import ctlbridge, topics


def w(root, rel, obj):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f)


class NotifyTopic(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbdash2n_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_registered_with_interval(self):
        self.assertIn("notify", topics.TOPICS)
        self.assertTrue(0 < topics.INTERVAL["notify"] <= 15)

    def test_no_own_settings_uses_config_source_and_everything_off(self):
        t = topics.build("notify", self.root, 1000.0)
        self.assertEqual((t["source"], t["engine"], t["settings"]["master"]), ("config", False, False))
        self.assertEqual(sorted(t["settings"]["categories"]), sorted(protocol.NOTIFY_KEYS))
        self.assertFalse(any(t["settings"]["categories"].values()))
        self.assertEqual(len(t["categories"]), len(protocol.NOTIFY_KEYS))
        self.assertEqual((t["quiet_now"], t["held_n"], t["stats"], t["sent_last"]), (False, 0, {"sent": 0, "merged": 0, "limited": 0, "failed": 0}, None))
        self.assertIsInstance(t["hint"], str)

    def test_own_settings_critical_marks_and_whitelist_without_secrets(self):
        w(self.root, "state/control/notify.json", {"version": 1, "master": True, "categories": {"day_start": True, "emergency": True},
                                                    "quiet": {"enabled": True, "from": "22:00", "to": "06:30", "tz": "utc", "bypass": ["emergency", "bogus"]},
                                                    "limits": {"per_hour": 3, "same_kind_min": 20}, "by": "dash", "api_key": "SECRET"})
        w(self.root, "state/control/notify_state.json", {"held": [1, 2], "last_sent": {"cat": "day_start", "token": "SECRET2"}, "stats": {"sent": 4, "merged": 1, "limited": 2, "failed": 0, "x": 9}})
        t = topics.build("notify", self.root, 1000.0)
        self.assertEqual((t["source"], t["engine"], t["settings"]["master"]), ("file", True, True))
        self.assertEqual(t["settings"]["quiet"], {"enabled": True, "from": "22:00", "to": "06:30", "tz": "utc", "bypass": ["emergency"]})
        self.assertEqual(t["settings"]["limits"], {"per_hour": 3, "same_kind_min": 20})
        crit = {c["id"] for c in t["categories"] if c["critical"]}
        self.assertEqual(crit, set(protocol.NOTIFY_CRITICAL))
        self.assertEqual((t["held_n"], t["sent_last"], t["stats"]["sent"], t["stats"]["limited"]), (2, {"cat": "day_start"}, 4, 2))
        self.assertNotIn("x", t["stats"])
        self.assertNotIn("SECRET", repr(t))
        self.assertNotIn("api_key", repr(t))

    def test_quiet_now_across_midnight_and_broken_file_is_all_off(self):
        w(self.root, "state/control/notify.json", {"master": True, "quiet": {"enabled": True, "from": "00:00", "to": "23:59", "tz": "utc"}})
        self.assertTrue(topics.build("notify", self.root, 3600.0)["quiet_now"])
        with open(os.path.join(self.root, "state/control/notify.json"), "w", encoding="utf-8") as f:
            f.write("{не json")
        t = topics.build("notify", self.root, 3600.0)
        self.assertEqual((t["settings"]["master"], t["quiet_now"]), (False, False))
        self.assertTrue(t["warn"])

    def test_topic_is_stable_between_calls(self):
        self.assertEqual(topics.build("notify", self.root, 1000.0), topics.build("notify", self.root, 1001.0))


class NotifyActions(unittest.TestCase):
    def test_set_and_test_reach_through_dash2_whitelist_with_strict_args(self):
        args, code = ctlbridge.CtlBridge.allowed_full("notify.set", {"master": True, "quiet": {"enabled": True, "from": "23:00", "to": "07:00", "tz": "local"}})
        self.assertIsNone(code)
        self.assertEqual(args["quiet"]["from"], "23:00")
        self.assertEqual(ctlbridge.CtlBridge.allowed_full("notify.test", {})[1], None)
        self.assertEqual(ctlbridge.CtlBridge.allowed_full("notify.set", {"master": "yes"})[1], "E_ARGS")
        self.assertEqual(ctlbridge.CtlBridge.allowed_full("notify.set", {"quiet": {"from": "25:00"}})[1], "E_ARGS")
        self.assertEqual(ctlbridge.CtlBridge.allowed_full("notify.set", {"secret": 1})[1], "E_ARGS")


class SettingsExtras(unittest.TestCase):
    def test_presets_profile_params_and_remarks_without_secrets(self):
        from tb.llm import dashapi
        data = {"overview": {"mode": "paper", "errors": ["конфиг мозга: ошибка"]}, "roles": {"roles": [], "errors": []}, "usage": {"groups": []},
                "providers": {"providers": [], "errors": ["провайдер x: нет адреса", "конфиг мозга: ошибка"]}, "exchanges": {"exchanges": []}, "revisions": {"revisions": [], "swaps": []},
                "profiles": {"profiles": [{"id": "ds", "provider": "deepseek", "model": "flash", "model_name": "deepseek-flash", "provider_enabled": True, "live_ok": False, "builtin": True,
                                           "used_by": [], "params": {"temperature": 0.2, "max_out_tok": 1200, "thinking": "low", "api_key": "SECRET"}}], "errors": ["профиль q: нет модели"]},
                "presets": {"presets": [{"id": "openai", "name": "OpenAI", "adapter": "openai", "base_url": "https://api.openai.com/v1?k=1", "verified": False, "note": "шаблон",
                                         "models": ["gpt"], "key_file": "openai.env", "key_env": "OPENAI_API_KEY", "added": False}]}}
        orig = dashapi.api
        dashapi.api = lambda path, query, root, now: (200, data[path.rsplit("/", 1)[1]])
        try:
            t = topics.build("settings", None, 1000.0)
        finally:
            dashapi.api = orig
        pr = t["preset_list"][0]
        self.assertEqual((pr["id"], pr["base_host"], pr["verified"], pr["added"], pr["models"]), ("openai", "api.openai.com", False, False, ["gpt"]))
        self.assertNotIn("openai.env", repr(t))
        self.assertNotIn("OPENAI_API_KEY", repr(t))
        self.assertNotIn("SECRET", repr(t))
        p = t["profile_list"][0]
        self.assertEqual((p["model"], p["builtin"], p["params"]), ("flash", True, {"temperature": 0.2, "max_out_tok": 1200, "thinking": "low"}))
        self.assertEqual(t["errors"], ["конфиг мозга: ошибка", "провайдер x: нет адреса", "профиль q: нет модели"])

    def test_missing_presets_do_not_break_topic(self):
        from tb.llm import dashapi
        orig = dashapi.api

        def api(path, query, root, now):
            if path.endswith("presets"):
                raise RuntimeError("нет файла")
            return 200, {}
        dashapi.api = api
        try:
            t = topics.build("settings", None, 1000.0)
        finally:
            dashapi.api = orig
        self.assertEqual((t["preset_list"], t["profile_list"], t["errors"]), ([], [], []))


if __name__ == "__main__":
    unittest.main()
