"""Роли цикла обучения day_reviewer и retro (R2, Requirement 17.8, решение владельца 4): профиль берёт из настроек платформы, назначает владелец.

1. Подписанный active.json старше задачи 27 (без этих ролей): роль = «не назначена» без ошибки конфигурации и без жёлтого статуса мозга,
   learn_roles=false, порт отвечает NO_ROLE; `./tb brain role chain shared.day_reviewer --primary ...`, brain.use и ctl brain.role (форма
   ролей дашборда) назначают её новой подписанной ревизией с записью в swaps, не трогая остальные цепочки.
2. Чистая установка (нет active.json): поставляемый config/brain.json не назначает роли цикла, модель без решения владельца не вызывается.
3. Дашборд: таблица ролей (llm.dashapi /api/settings/roles, dash2 settings и brain) показывает роли как «не назначена» (primary=None).
ФЕЙКОВЫЙ LLM, временный корень, ключ подписи поддельный."""
import contextlib
import io
import json
import os
import time
import unittest

import _bootstrap  # noqa: F401
import _ctl
from _brain import BrainBase
from tb.brain import api_daemon, brain_cli, learnjobs, profiles, swap
from tb.dash2 import topics
from tb.learnloop import port as PT

CFG = {"second_iteration": {"port": {"llm_job_ttl_s": 900, "claim_ttl_s": 120, "llm_wait_s": 600}}}
LEARN = ("day_reviewer", "retro")
OLD_SHARED = {"news_reader": {"primary": "ds-pro", "fallback": ["ds-flash"]}, "reviewer": {"primary": "ds-flash", "fallback": ["ds-pro"]},
              "day_auditor": {"primary": "ds-pro", "fallback": ["ds-flash"]}}


class PreTask27Active(BrainBase):
    """active.json, созданный до задачи 27: в общих ролях нет day_reviewer и retro."""

    def setUp(self):
        super().setUp()
        teams = json.loads(json.dumps(self.brain_doc["teams"]))
        teams["A"]["roles"]["entry_scout"] = {"primary": "ds-pro", "fallback": []}            # владелец уже менял назначения: они должны сохраниться
        old = swap.Assign(7, "A", teams, json.loads(json.dumps(OLD_SHARED)), "active")
        swap._write_rev(self.root, old, 7, "owner-cli", "use", self.t - 100, swap._key(), 6)
        self.before = swap.read_active(self.root)[0]
        self.assertNotIn("day_reviewer", self.before["shared_roles"])

    def other_chains(self, o):
        return {"teams": o["teams"], "live_team": o["live_team"], "shared": {k: v for k, v in o["shared_roles"].items() if k not in LEARN}}

    def test_unassigned_is_not_error_and_not_ready(self):
        a = swap.load_effective(self.root)
        self.assertEqual(a.source, "active")
        self.assertEqual(a.chain("day_reviewer")[0], [])
        st = swap.status(self.root)
        self.assertIsNone(st["warn"])                                          # подпись в порядке, active.json действует
        self.assertEqual(st["config_errors"], [])                              # отсутствие роли не ошибка конфигурации
        r = self.runner({"ds-flash": [{"x": 1}]})
        self.assertEqual(learnjobs.role_ready(r, "day_reviewer"), (False, "NO_ASSIGNMENT"))
        self.assertEqual(learnjobs.roles_status(r), {"day_reviewer": False, "retro": False})
        api_daemon.heartbeat(r)
        self.assertEqual(PT.available(self.root, time.time(), "day_reviewer"), (False, "NO_ROLE"))
        self.assertEqual(PT.available(self.root, time.time(), "retro"), (False, "NO_ROLE"))
        b = topics.brain(self.root, time.time())
        self.assertEqual(b["status"], "idle")                                  # мозг не в «только защите» и не помечен из-за ролей цикла
        self.assertFalse(r.health.protect_on)
        jid = PT.submit(self.root, "day_reviewer", {"day_id": "D-20261102"}, self.t, CFG)
        learnjobs.step(r)
        self.assertEqual(PT.result(self.root, jid)["err"], "NO_ASSIGNMENT")
        self.assertEqual(self.factory.calls(), 0)

    def test_cli_role_chain_assigns_only_that_role(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = brain_cli.cmd_profile_role(self.root, ["role", "chain", "shared.day_reviewer", "--primary", "ds-flash", "--fallback", "ds-pro"])
        self.assertEqual(rc, 0, buf.getvalue())
        o, state = swap.read_active(self.root)
        self.assertEqual(state, "ok")                                          # новая ревизия подписана
        self.assertEqual(o["rev"], 8)
        self.assertEqual(o["prev_rev"], 7)
        self.assertEqual(o["shared_roles"]["day_reviewer"], {"primary": "ds-flash", "fallback": ["ds-pro"]})
        self.assertNotIn("retro", o["shared_roles"])                           # вторая роль осталась не назначенной
        self.assertEqual(self.other_chains(o), self.other_chains(self.before))  # остальные цепочки не изменились
        self.assertTrue(os.path.exists(os.path.join(swap.history_dir(self.root), "R-8.json")))
        last = swap.read_swaps(self.root)[-1]
        self.assertEqual((last["rev"], last["prev_rev"], last["target"], last["from"], last["to"], last["by"]),
                         (8, 7, ["day_reviewer"], "-", "ds-flash,ds-pro", "owner-cli"))
        self.assertTrue(swap.verify_swaps(self.root)["ok"])
        r = self.runner({})
        self.assertEqual(learnjobs.role_ready(r, "day_reviewer"), (True, "OK"))
        self.assertEqual(learnjobs.role_ready(r, "retro"), (False, "NO_ASSIGNMENT"))

    def test_chain_without_primary_rejected(self):
        r = swap.set_chain(self.root, {"role": "shared.retro", "fallback": ["ds-pro"]}, self.t, by="test")
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "E_SWAP_PROFILE")
        self.assertEqual(swap.read_active(self.root)[0]["rev"], 7)

    def test_bare_role_name_and_use(self):
        r = swap.set_chain(self.root, {"role": "retro", "primary": "ds-pro"}, self.t, by="test")
        self.assertTrue(r["ok"], r)
        r = swap.use(self.root, {"role": "shared.day_reviewer", "profile": "ds-flash"}, self.t + 1, by="test")
        self.assertTrue(r["ok"], r)
        o = swap.read_active(self.root)[0]
        self.assertEqual(o["shared_roles"]["retro"], {"primary": "ds-pro", "fallback": []})
        self.assertEqual(o["shared_roles"]["day_reviewer"], {"primary": "ds-flash", "fallback": []})
        self.assertEqual(self.other_chains(o), self.other_chains(self.before))
        self.assertEqual(swap.set_chain(self.root, {"role": "shared.hacker", "primary": "ds-pro"}, self.t, by="test")["code"], "E_SWAP_TARGET")

    def test_dashboard_rows_show_unassigned(self):
        rows = {r["role"]: r for r in profiles.role_rows(self.root, self.t)["roles"]}
        for role in LEARN:
            self.assertEqual((rows[role]["scope"], rows[role]["primary"], rows[role]["fallback"], rows[role]["assigned"]), ("shared", None, [], False))
        self.assertTrue(rows["news_reader"]["assigned"])
        s = {r["role"]: r for r in topics.settings(self.root, self.t)["roles"]}
        self.assertEqual((s["day_reviewer"]["target"], s["day_reviewer"]["primary"]), ("shared.day_reviewer", None))
        b = {r["role"]: r for r in topics.brain(self.root, self.t + 100)["roles"]}
        self.assertIsNone(b["retro"]["primary"])


class CleanInstall(BrainBase):
    def test_shipped_config_has_no_learn_assignment(self):
        with open(os.path.join(_ctl.PROJECT, "config", "brain.json"), encoding="utf-8") as f:
            doc = json.load(f)
        for role in LEARN:
            self.assertNotIn(role, doc["shared_roles"])                        # поставка: роли цикла без профиля
        self.assertEqual(sorted(doc["shared_roles"]), ["day_auditor", "news_reader", "reviewer"])

    def test_no_active_no_call(self):
        self.assertFalse(os.path.exists(swap.active_file(self.root)))
        a = swap.load_effective(self.root)
        self.assertEqual(a.source, "config")
        self.assertEqual(a.chain("day_reviewer")[0], [])
        self.assertEqual(swap.status(self.root)["config_errors"], [])
        r = self.runner({"ds-flash": [{"x": 1}], "ds-pro": [{"x": 1}]})
        self.assertEqual(learnjobs.roles_status(r), {"day_reviewer": False, "retro": False})
        for role in LEARN:
            jid = PT.submit(self.root, role, {"week_id": "W-2026-44"}, self.t, CFG)
            learnjobs.step(r)
            self.assertEqual(PT.result(self.root, jid)["err"], "NO_ASSIGNMENT")
        self.assertEqual(self.factory.calls(), 0)                              # без назначения владельцем модель (в т. ч. платная) не вызывается
        r2 = swap.set_chain(self.root, {"role": "shared.day_reviewer", "primary": "ds-flash"}, self.t, by="test")
        self.assertTrue(r2["ok"], r2)
        o = swap.read_active(self.root)[0]
        self.assertEqual(o["shared_roles"]["day_reviewer"]["primary"], "ds-flash")
        self.assertNotIn("retro", o["shared_roles"])


class DashCtlAssign(unittest.TestCase):
    """Форма ролей дашборда шлёт ctl brain.role op=chain с target «shared.<роль>»: назначает роль, которой нет в active.json."""

    def setUp(self):
        self.e = _ctl.Env(self)
        self.e.good_live()
        self.ctl = self.e.ctl()

    def test_ctl_brain_role_chain_assigns_learn_role(self):
        rows = {r["role"]: r for r in profiles.role_rows(self.e.root)["roles"]}
        self.assertIsNone(rows["day_reviewer"]["primary"])
        res, _c = self.e.send(self.ctl, "brain.role", {"op": "chain", "role": "shared.day_reviewer", "primary": "ds-flash"})
        self.assertEqual(res["status"], "done", res)
        o, state = swap.read_active(self.e.root)
        self.assertEqual(state, "ok")
        self.assertEqual(o["shared_roles"]["day_reviewer"], {"primary": "ds-flash", "fallback": []})
        self.assertNotIn("retro", o["shared_roles"])
        self.assertEqual(swap.read_swaps(self.e.root)[-1]["target"], ["day_reviewer"])
        rows = {r["role"]: r for r in profiles.role_rows(self.e.root)["roles"]}
        self.assertEqual(rows["day_reviewer"]["primary"]["id"], "ds-flash")


if __name__ == "__main__":
    unittest.main()
