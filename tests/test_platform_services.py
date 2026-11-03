"""Регистрация сервиса learnloop в платформе (R2, задача 10; Requirement 15.1): профили paper/testnet/trade (умолчания и config/platform.json),
порядок запуска и остановки, команда, чистка окружения как у labels, поведение остальных сервисов не меняется; блок статуса learnloop."""
import json
import os
import unittest

import _bootstrap  # noqa: F401
from tb.platform import services as S
from tb.platform import status as ST

SECRETS = {"BYBIT_API_KEY": "k", "ANTHROPIC_API_KEY": "a", "DEEPSEEK_API_KEY": "d", "OPENAI_API_KEY": "o", "MY_SECRET": "s", "GH_TOKEN": "t"}
SAFE = {"PATH": "/bin", "LANG": "C"}


class TestRegistration(unittest.TestCase):
    def test_order_and_defaults(self):
        self.assertIn("learnloop", S.ORDER)
        self.assertEqual(S.ORDER.index("learnloop"), S.ORDER.index("labels") + 1)
        for prof in S.PROFILES:
            sv = S.DEFAULTS["services"][prof]
            self.assertIn("learnloop", sv, prof)
            self.assertEqual(sv, S.parse_services(",".join(sv)), prof)
        self.assertEqual(S.DEFAULTS["svc"]["learnloop"]["hb"], "file")
        self.assertEqual(S.parse_services("learnloop,exec,labels"), ["exec", "labels", "learnloop"])
        self.assertEqual(S.stop_order(["exec", "labels", "learnloop", "brain"]), ["brain", "learnloop", "labels", "exec"])

    def test_repo_platform_config_profiles(self):
        with open(os.path.join(_bootstrap.ROOT, "config", "platform.json"), encoding="utf-8") as f:
            d = json.load(f)
        for prof in S.PROFILES:
            self.assertIn("learnloop", d["services"][prof], prof)
            self.assertIn("labels", d["services"][prof], prof)
        self.assertEqual(d["svc"]["learnloop"]["hb"], "file")
        cfg = S.deep_merge(S.DEFAULTS, d)                                     # без config/local
        for prof in S.PROFILES:
            self.assertIn("learnloop", S.default_services(prof, cfg), prof)
        self.assertNotIn("brain", S.default_services("trade", cfg))         # прочий состав профилей прежний

    def test_command(self):
        self.assertEqual(S.service_cmd("learnloop", "trade", False, S.DEFAULTS, py="py"), ["py", "-m", "tb.learnloop.service", "run"])
        self.assertTrue(S.module_available("tb.learnloop.service"))

    def test_env_scrubbed_like_labels(self):
        env = S.child_env("learnloop", dict(SECRETS, **SAFE), "/tmp/r", profile="trade")
        self.assertFalse([k for k in env if k in SECRETS], env)
        self.assertEqual(env["TB_PLATFORM_SERVICE"], "learnloop")
        lab = S.child_env("labels", dict(SECRETS, **SAFE), "/tmp/r", profile="trade")
        self.assertEqual(sorted(k for k in env if k != "TB_PLATFORM_SERVICE"), sorted(k for k in lab if k != "TB_PLATFORM_SERVICE"))

    def test_other_services_env_unchanged(self):
        """Регистрация learnloop не меняет окружение остальных сервисов: общая чистка — только BYBIT_*/ANTHROPIC_* и прокси."""
        for name in ("scan", "dash", "brain", "dispatch"):
            env = S.child_env(name, dict(SECRETS, **SAFE), "/tmp/r", profile="paper")
            self.assertNotIn("BYBIT_API_KEY", env)
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertIn("DEEPSEEK_API_KEY", env, name)
            self.assertIn("GH_TOKEN", env, name)


class TestStatusBlock(unittest.TestCase):
    NOW = 1790900000.0

    def hb(self, **kw):
        d = {"enabled": True, "state": "ok", "mode": "auto", "items": [], "last_ok_ts": self.NOW - 60, "run_start": self.NOW - 3600, "stale_min": 120,
             "ready": {"ok": False, "failed": ["WATCH"]}, "stubs": ["watch"], "day": {"day": "2026-10-02", "work_h": 3.0}}
        d.update(kw)
        return d

    def test_none_and_green(self):
        self.assertIsNone(ST.learnloop_block({}, self.NOW))
        lb = ST.learnloop_block(self.hb(), self.NOW)
        self.assertEqual((lb["color"], lb["codes"]), ("green", []))
        self.assertIn("прогрев: WATCH", ST.learnloop_line(lb))

    def test_yellow_and_red(self):
        for code in ("RULES_NO_EVENT", "WARMUP_STUCK", "AUTO_RESTORED"):
            lb = ST.learnloop_block(self.hb(items=[{"code": code, "text": code}]), self.NOW)
            self.assertEqual(lb["color"], "yellow", code)
        for code in ("WATCH_STALE", "CHAIN_BROKEN", "DB_CORRUPT"):
            lb = ST.learnloop_block(self.hb(items=[{"code": "RULES_NO_EVENT", "text": "y"}, {"code": code, "text": code}]), self.NOW)
            self.assertEqual(lb["color"], "red", code)

    def test_stale_pass(self):
        lb = ST.learnloop_block(self.hb(last_ok_ts=self.NOW - 121 * 60), self.NOW)
        self.assertEqual((lb["color"], lb["codes"]), ("yellow", ["STALE"]))
        lb = ST.learnloop_block(self.hb(last_ok_ts=self.NOW - 121 * 60), self.NOW, running=False)
        self.assertEqual(lb["color"], "green")
        lb = ST.learnloop_block(self.hb(last_ok_ts=None, run_start=self.NOW - 3 * 3600), self.NOW)
        self.assertIn("STALE", lb["codes"])

    def test_off(self):
        lb = ST.learnloop_block({"enabled": False, "state": "disabled", "conf_errors": ["E_CONF:nofile"]}, self.NOW)
        self.assertEqual((lb["state"], lb["color"], lb["reasons"]), ("off", "off", []))


if __name__ == "__main__":
    unittest.main()
