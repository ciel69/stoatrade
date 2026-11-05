"""R4, задача 18 (Requirement 11, свойство 20 «Мозг не ослабляет код»): шлюз отклоняет `amend_plan` по правилам политики выхода
(`xp_*`) и пола защиты (`auto_*`) кодом `E_EXIT_POLICY`; `hold` мозга не задерживает действий кода; разрешённые «строже» действия
(стоп, закрытие, доля) проходят; блок досье `exit` (Requirement 11.4) не больше 60 токенов, в белом списке `get_brief`, и бюджет
досье растёт не больше чем на 60 токенов. Всё во временном корне; биржа и сеть не нужны."""
import json
import os
import shutil
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import _gw
import test_brief as TB
from tb.brain import brief as brief_mod
from tb.gateway import catalog
from tb.logic import rules
from tb.logic.candles import MarketView

PID = "PL-1028-SOL-01"
AP = {"enabled": True, "be_r": 0.7, "trail_activate_r": 1.0, "trail_lock_frac": 0.5, "min_profit_r": 0.2, "stop_slip_bps": 5}
POLICY = {"schema": "tb.exit_policy/1", "be": {"r": 0.6}, "lock": [{"at_r": 1.5, "keep": 0.8}], "min_profit_r": 0.2,
          "partial": [{"at_r": 1.0, "frac": 0.3, "exec": "taker"}], "timeout": {"hold_frac": 0.5, "exec": "taker"}}


def code_when(rid="xp_timeout"):
    """Условие правила кода, которое мозг мог бы придумать (идентификатор политики выхода или пола)."""
    return {"kind": "elapsed", "min": 9990} if rid == "xp_timeout" else {"kind": "pnl_r", "gte": 9.0}


class TestAmendExitPolicy(_gw.GwBase):
    """Requirement 11.2, свойство 20: правило кода мозг не меняет, остальное (строже) — можно."""

    def amend(self, patch, agent="chat"):
        return self.call(agent, "amend_plan", {"id": PID, "patch": patch, "reason": "проверка правила кода"})

    def test_amend_touching_code_rules_rejected(self):
        self.make_plan()
        for rid in ("xp_timeout", "xp_partial_0", "auto_be", "auto_lock"):
            r = self.amend({"manage": [{"id": rid, "when": code_when(rid), "do": {"action": "close", "frac": 1.0}}]})
            self.assertEqual(self.code(r), "E_EXIT_POLICY", rid)
            self.assertEqual(r["errors"][0]["path"], "manage[0].id", rid)
        self.assertEqual(self.inbox(), [])                       # до исполнителя ничего не дошло

    def test_amend_shadow_variant_touching_code_rule_rejected(self):
        self.make_plan()
        r = self.amend({"shadow_variants": [{"name": "v1", "patch": {"manage": [{"id": "xp_lock", "when": {"kind": "pnl_r", "gte": 1.0},
                                                                                   "do": {"action": "close", "frac": 1.0}}]}}]})
        self.assertEqual(self.code(r), "E_EXIT_POLICY")

    def test_submit_with_code_rule_id_rejected(self):
        """План с правилом, чей id принадлежит коду, делил бы состояние rt.rules с правилом кода и мог бы его погасить: submit_plan тоже отклоняет."""
        for rid in ("xp_timeout", "xp_partial_0", "auto_be"):
            p = _gw.plan()
            p["manage"] = [{"id": rid, "when": code_when(rid), "do": {"action": "close", "frac": 1.0}}]
            r = self.call("chat", "submit_plan", {"plan": p, "reason": "проверка id правила кода"})
            self.assertEqual(self.code(r), "E_EXIT_POLICY", rid)
        self.assertEqual(self.inbox(), [])

    def test_submit_plain_plan_not_blocked_by_exit_policy_guard(self):
        r = self.call("chat", "submit_plan", {"plan": _gw.plan(), "reason": "обычный план"})
        self.assertNotEqual(self.code(r), "E_EXIT_POLICY")

    def test_own_plan_rule_is_not_a_code_rule(self):
        self.make_plan()
        r = self.amend({"manage": [{"id": "be_1r", "when": {"kind": "pnl_r", "gte": 1.0}, "do": {"action": "move_stop", "to": "breakeven"}, "once": True}]})
        self.assertNotEqual(self.code(r), "E_EXIT_POLICY")
        self.assertEqual(len(self.inbox()), 1)                   # правило плана прошло (мозг может быть строже)

    def test_stricter_commands_accepted(self):
        self.make_plan(cur_stop=100.0)
        r = self.call("chat", "stop_plan", {"id": PID, "to": "99.5", "reason": "стоп строже (short)"})
        self.assertNotEqual(self.code(r), "E_STOP_WIDEN")
        r = self.call("chat", "close_plan", {"id": PID, "frac": 0.5, "reason": "зафиксировать половину раньше"})
        self.assertTrue(r.get("ok") in (True, None), r)
        r = self.call("chat", "close_plan", {"id": PID, "reason": "выйти раньше кода"})
        self.assertTrue(r.get("ok") in (True, None), r)
        self.assertEqual(len(self.inbox()), 3)

    def test_hold_not_set_for_a_position(self):
        self.make_plan()
        r = self.call("chat", "hold_plan", {"id": PID, "reason": "подождать"})
        self.assertEqual(self.code(r), "E_STATUS_FORBIDS")       # у мозга нет вето: пауза только до входа
        self.assertEqual(self.inbox(), [])
        self.assertEqual([n for n in catalog.TOOLS if "hold" in n], ["hold_plan"])      # другой команды паузы у мозга нет


class TestHoldDoesNotBlockCode(unittest.TestCase):
    """Requirement 11.8: `hold` плана не задерживает действия политики — запрещает только добор (HOLD)."""

    def limits(self):
        return {"exit_policy": POLICY, "auto_protect": AP, "tick": 0.01, "connectivity": {"public_price_stale_s": 10},
                "stop_moves": {"min_gap_bps_from_price": 8, "min_step_ticks": 2, "min_interval_s": 5},
                "pyramid": {"enabled": True, "min_r": 1.0, "max_adds": 1, "max_add_risk_frac": 1.0, "min_gap_s": 60}}

    def run_manage(self, hold):
        plan = {"side": "long", "manage": []}
        rt = {"status": "managing", "hold": hold,
              "pos": {"qty": 1.0, "avg": 100.0, "cur_stop": 99.0, "initial_stop": 99.0, "r_unit": 1.0, "best_px": 101.4},
              "rules": {}}
        view = MarketView("SOL", 101.4, 101.3, 101.5, 101.4, 1000.0, None, 1001.0)
        return rules.evaluate_manage(plan, rt, view, 1001.0, self.limits())

    def test_partial_fix_fires_at_hold(self):
        fired = [a.get("rule") for a in self.run_manage(True)["actions"]]
        self.assertIn("xp_partial_0", fired)
        self.assertEqual(fired, [a.get("rule") for a in self.run_manage(False)["actions"]])   # hold ничего не меняет

    def test_hold_blocks_only_add(self):
        plan = {"side": "long", "manage": [{"id": "add1", "when": {"kind": "pnl_r", "gte": 1.0}, "do": {"action": "add", "risk_frac": 0.5}}]}
        rt = {"status": "managing", "hold": True, "pos": {"qty": 1.0, "avg": 100.0, "cur_stop": 99.0, "r_unit": 1.0}, "rules": {}}
        view = MarketView("SOL", 101.4, 101.3, 101.5, 101.4, 1000.0, None, 1001.0)
        out = rules.evaluate_manage(plan, rt, view, 1001.0, self.limits())
        self.assertIn({"rule": "add1", "code": "HOLD"}, out["skipped"])
        self.assertIn("xp_partial_0", [a.get("rule") for a in out["actions"]])


class TestBriefExitBlock(TB.Base):
    """Requirement 11.4: блок `exit` в досье position_manager — версия политики позиции, ближайший уровень и срок, <= 60 токенов."""

    def set_exit(self, ver="X-0007", policy=None, **kw):
        d = self.R("state", "plans", PID)
        with open(os.path.join(d, "runtime.json"), encoding="utf-8") as f:
            rt = json.load(f)
        rec = {"ver": ver, "policy_h": "h" * 16, "floor_h": "f" * 16, "policy": policy if policy is not None else POLICY,
               "max_hold_min": kw.pop("max_hold_min", 240), "atr5_pct": 1.2, "sig_type": "brain", "horizon": "intraday", "why": "OK"}
        rec.update(kw)
        rt["pos"]["exit"] = rec
        with open(os.path.join(d, "runtime.json"), "w", encoding="utf-8") as f:
            json.dump(rt, f)

    def test_block_content_and_size(self):
        self.set_exit()
        b = self.build()
        self.assertEqual(b["exit"]["ver"], "X-0007")
        row = b["exit"]["rows"][0]
        self.assertEqual(row["plan"], PID)
        self.assertEqual((row["next"]["kind"], row["next"]["at_r"]), ("lock", 1.5))      # ближайший уровень в R
        self.assertEqual(row["timeout_min"], 120.0)                                      # 0.5 x max_hold_min на входе
        self.assertLessEqual(brief_mod.tokens(b["exit"]), brief_mod.EXIT_MAX_TOKENS)

    def test_budget_grows_by_at_most_60_tokens(self):
        clean = self.build()
        self.set_exit()
        b = self.build()
        self.assertLessEqual(brief_mod.tokens(b) - brief_mod.tokens(clean), brief_mod.EXIT_MAX_TOKENS)

    def test_block_fits_with_several_positions(self):
        self.set_exit()
        d = self.R("state", "plans", PID)
        with open(os.path.join(d, "runtime.json"), encoding="utf-8") as f:
            rt = json.load(f)
        for i in range(5):                                                                # много позиций: блок всё равно <= 60 токенов
            pid = "PL-1028-ETH-%02d" % i
            self.plan(pid, "managing", {"symbol": "ETH", "side": "long", "mode": "live", "rev": 1},
                      {"status": "managing", "ts": {"entered": TB.NOW - 600}, "pos": dict(rt["pos"], journal_id="J%d" % i)})
        b = self.build()
        self.assertLessEqual(brief_mod.tokens(b["exit"]), brief_mod.EXIT_MAX_TOKENS)

    def test_no_position_no_block(self):
        shutil.rmtree(self.R("state", "plans", PID))
        b = self.build()
        self.assertNotIn("exit", b)

    def test_floor_when_position_has_no_policy(self):
        b = self.build()
        self.assertEqual(b["exit"]["ver"], "X-0001")                                      # позиция без rt.pos.exit: действует пол
        self.assertLessEqual(brief_mod.tokens(b["exit"]), brief_mod.EXIT_MAX_TOKENS)

    def test_block_in_gateway_whitelist(self):
        self.assertIn("exit", catalog.TOOLS["get_brief"].out)
        self.set_exit()
        ctx = catalog.Ctx(self.root, mock.Mock(consumer="chat"), now=TB.NOW)
        out = catalog.finish(catalog.TOOLS["get_brief"], catalog.h_brief(ctx, {"scope": "start"}))
        self.assertEqual(out["exit"]["ver"], "X-0007")


if __name__ == "__main__":
    unittest.main()
