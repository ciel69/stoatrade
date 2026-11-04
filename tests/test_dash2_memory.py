"""Вкладка «Память» нового дашборда (R2, задача 17; Requirements 13.2–13.6, 11.11): контракт операций memory_cases, memory_lessons, memory_rules,
memory_path (схемы ответов), «нет данных» без таблиц цикла, «база восстанавливается» при маркере, соединение на один вызов через open_ro,
аварийные действия lesson_reject (без диалога), rule_disable и rules_disable_all (только с подтверждением рисков), записи OWNER в learn_events,
CSRF и лимит частоты соединения, маршруты сокета. Временные корни, сети и платформы нет."""
import asyncio
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
from tb.dash2 import memory_ops as MO
from tb.dash2 import server as S
from tb.knowledge import markers as KM
from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import rules as RL
from tb.learnloop import trial as TR
from tb.learnloop import versions as V
from tb.memory import cfg as memcfg
from tb.memory import lessons as LS

try:
    import websockets  # noqa: F401
    from websockets.asyncio.client import connect as _ws_connect  # noqa: F401
    from _bootstrap import ws_connect as connect
    HAVE_WS = True
except ImportError:
    HAVE_WS = False

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = 86400
T0 = 1790812800
NOW = T0 + 12 * D
P2 = {"id": "P2", "ver": 1, "name": "confirm_follow", "kinds": ["range_5m", "turnover_surge", "hot_enter", "oi_spike", "liq_cascade"], "side": "with_dir",
      "entry": "candle_confirm_limit", "enabled": True}
SPREAD = [{"feature": "spread_bps", "op": ">=", "value": 5.0}]
RANGE = [{"feature": "range_5m_pct", "op": ">=", "value": 5.0}]
CLAIM = {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.5}

NUM_KEYS = {"v", "n", "small"}
ENV_KEYS = {"ok", "state", "reason"}
CASES_KEYS = ENV_KEYS | {"total", "by_type", "by_status", "modes", "recent", "similar"}
CASE_ROW_KEYS = {"case_id", "ts", "type", "mode", "sym", "side", "setup", "action", "status", "r", "rulebook_ver", "eligible"}
LESSONS_KEYS = ENV_KEYS | {"db", "db_reason", "total", "by_status", "by_src", "small_n", "rows"}
LESSON_ROW_KEYS = {"lesson_id", "src", "status", "reason_code", "txt", "cond_txt", "claim", "created_by", "conf", "n", "effect", "p", "ts", "expires_ts",
                   "trial", "rule", "can_reject"}
TRIAL_KEYS = {"trial_id", "rule_id", "state", "verdict", "start_ts", "deadline_ts", "closed_ts"}
RULE_KEYS = {"rule_id", "rev", "kind", "status", "wait", "reason_code", "from_lesson", "ver_in", "ver_out", "cond_txt", "cond_h", "env_norm", "ts", "in_version"}
RULES_KEYS = ENV_KEYS | {"mode", "current", "versions", "versions_total", "counts", "active", "waiting", "rules", "rules_total", "feat_missing", "no_event"}
VERSION_KEYS = {"ver", "parent", "reason_code", "rule_ids", "trial_id", "ts"}
WAIT_KEYS = {"rule_id", "wait", "since_ts", "waited_s", "deadline_ts", "left_s", "from_lesson", "cond_txt"}
PATH_KEYS = ENV_KEYS | {"funnel", "paths", "lesson_id"}
FUNNEL_KEYS = {"candidates", "hyp", "trial", "confirmed", "rule_active"}
STEP_KEYS = {"candidate", "lesson", "trial", "rule", "version"}
CAND_KEYS = {"id", "menu_id", "src", "status", "code", "p_screen", "holdout_start", "holdout_end", "ts"}
ACTION_KEYS = {"id", "op", "ok", "code", "msg"}


def is_num(test, x, n=None):
    test.assertEqual(set(x), NUM_KEYS, x)
    test.assertIsInstance(x["n"], int)
    test.assertEqual(x["small"], x["n"] < 30)
    if n is not None:
        test.assertEqual(x["n"], n)


class WS(object):
    """Заглушка соединения (ключ сессии)."""


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbdash2m_")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "config"))
        for f in ("learnloop.json", "memory.json", "shadow_policies.json"):
            shutil.copy(os.path.join(REPO, "config", f), os.path.join(self.root, "config", f))
        with open(os.path.join(self.root, "config", "learnloop.json"), encoding="utf-8") as f:
            self.cfg = json.load(f)
        RV.clear_cache()
        store2._STATUS_CACHE.clear()
        self.c = None

    def v2(self):
        self.c = store.open_rw(self.root)
        self.addCleanup(self.close)
        store2.migrate_v2a(self.c, self.root, explicit=True, backup=False, now=T0)
        V.init_rulebook(self.c, T0)

    def close(self):
        if self.c is not None:
            self.c.close()
            self.c = None

    def lesson(self, cond, n=20, diff=-0.4, se=0.1, cand=None):
        ev = {"n_closed": n, "diff": diff, "se": se, "p": 0.001, "cand": cand, "last_ts": "2026-10-01T00:00:00Z"}
        return LS.create(self.root, cond, dict(CLAIM), "code", [], memcfg.load(self.root), NOW - 10 * D, evidence_src="labels", evidence_extra=ev)["lesson_id"]

    def trial_rule(self, cond, lid, state="open", wait="READY"):
        draft = RL.derive({"lesson_id": lid, "status": "hyp", "cond": cond, "claim": dict(CLAIM), "evidence": {"src": "labels"}}, P2, self.c, NOW)
        res = V.create_with_trial(self.c, draft, {"criterion": {"active": []}, "criterion_h": "0" * 16, "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1",
                                                  "start_ts": NOW - 5 * D, "deadline_ts": NOW + 25 * D, "env_norm": "mainnet"}, NOW, self.cfg)
        if state == "closed":
            store2.update_row(self.c, "trials", res["trial_id"], {"state": "closed", "verdict": "promote", "cut_ts": NOW, "closed_ts": NOW,
                                                                  "numbers": {"in_mean": -0.5, "n": 150}})
            V.set_wait(self.c, res["rule_id"], wait, NOW - 3 * D)
        return res["rule_id"]

    def active_rule(self, cond, lid):
        rid = self.trial_rule(cond, lid, state="closed")
        V.promote(self.c, rid, NOW, self.cfg)
        return rid

    def owner(self):
        return self.c.execute("SELECT step, code, ref FROM learn_events WHERE actor='OWNER' ORDER BY seq").fetchall()


# ---------------------------------------------------------------- нет данных, восстановление, соединение на вызов

class NoData(Base):
    def test_empty_root(self):
        for op in MO.READ_OPS:
            d = MO.read(op, self.root, {}, NOW)
            self.assertTrue(d["ok"], op)
            self.assertEqual(d["state"], "no_data", op)
            self.assertTrue(d["reason"].startswith("нет данных"), op)
        self.assertEqual(MO.memory_rules(self.root, NOW)["reason"], "нет данных: таблиц цикла нет")
        self.assertEqual(MO.memory_lessons(self.root, NOW)["db"], "no_data")
        self.assertEqual(MO.memory_cases(self.root, NOW)["similar"], {"state": "stage3", "reason": "появится на этапе 3"})

    def test_labels_db_without_cycle_tables(self):
        c = store.open_rw(self.root)
        c.close()
        self.assertTrue(os.path.exists(store.db_path(self.root)))
        self.assertEqual(MO.memory_rules(self.root, NOW)["state"], "no_data")
        self.assertEqual(MO.memory_path(self.root, NOW)["state"], "no_data")

    def test_restoring(self):
        self.v2()
        self.close()
        KM.write_json(KM.restoring_path(self.root), {"pid": 1})
        for op in ("memory_rules", "memory_path"):
            d = MO.read(op, self.root, {}, NOW)
            self.assertEqual((d["ok"], d["state"], d["reason"]), (True, "restoring", "база восстанавливается"))
        self.lesson(SPREAD)
        d = MO.memory_lessons(self.root, NOW)
        self.assertEqual((d["state"], d["db"], d["db_reason"]), ("ok", "restoring", "база восстанавливается"))

    def test_corrupt_db_is_error_not_crash(self):
        os.makedirs(os.path.dirname(store.db_path(self.root)), exist_ok=True)
        with open(store.db_path(self.root), "wb") as f:
            f.write(b"not a database" * 512)
        self.assertEqual(MO.memory_rules(self.root, NOW)["state"], "error")
        self.assertEqual(MO.memory_path(self.root, NOW)["state"], "error")

    def test_connection_per_call_via_open_ro(self):
        self.v2()
        opened = []
        real = store.open_ro

        def spy(root=None):
            c = real(root)
            opened.append(c)
            return c
        with mock.patch.object(store, "open_ro", side_effect=spy), mock.patch.object(store, "open_rw", side_effect=AssertionError("запись из чтения")):
            for op in MO.READ_OPS:
                MO.read(op, self.root, {}, NOW)
        self.assertTrue(opened)
        for c in opened:
            with self.assertRaises(sqlite3.ProgrammingError):          # закрыто после вызова
                c.execute("SELECT 1")


# ---------------------------------------------------------------- схемы ответов

class Contract(Base):
    def setUp(self):
        Base.setUp(self)
        self.v2()
        self.l1 = self.lesson(SPREAD, n=20, cand=1)
        self.l2 = self.lesson(RANGE, n=120, cand=2)
        self.r_act = self.active_rule(SPREAD, self.l1)
        self.r_wait = self.trial_rule(RANGE, self.l2, state="closed", wait="LOSS_CAP")
        store2.insert_rows(self.c, "lesson_candidates", [
            {"id": 1, "menu_id": "M1", "src": "code", "cond": SPREAD, "claim": CLAIM, "env_norm": "mainnet", "ctx_ver": "c1", "k_space": 4, "p_screen": 0.01,
             "status": "hyp", "code": None, "holdout_start": NOW - 9 * D, "holdout_end": NOW - 2 * D, "evidence": {}, "lesson_id": self.l1, "ts": NOW - 10 * D},
            {"id": 2, "menu_id": "M2", "src": "code", "cond": RANGE, "claim": CLAIM, "env_norm": "mainnet", "ctx_ver": "c1", "k_space": 4, "p_screen": 0.02,
             "status": "hyp", "code": None, "holdout_start": NOW - 9 * D, "holdout_end": NOW - 2 * D, "evidence": {}, "lesson_id": self.l2, "ts": NOW - 10 * D},
            {"id": 3, "menu_id": "M3", "src": "code", "cond": [{"feature": "spread_bps", "op": ">=", "value": 9.0}], "claim": CLAIM, "env_norm": "mainnet",
             "ctx_ver": "c1", "k_space": 4, "p_screen": 0.2, "status": "dropped", "code": "NOT_SIGNIFICANT", "holdout_start": None, "holdout_end": None,
             "evidence": {}, "lesson_id": None, "ts": NOW - 3 * D}])
        evs = []
        for i in range(10):                                    # 10 решений: 4 без события; правило r_act оценено в 6, из них без признаков 3
            common = {"rec_seq": 100 + i, "ts": NOW - D, "role": "entry_scout", "event_seq": None if i < 4 else 7, "no_event": 1 if i < 4 else 0,
                      "rulebook_ver": "R-0002", "plan": None}
            evs.append(dict(common, rule_id="", hit=0, evaluated=1, missing=1 if i < 3 else 0))
            if i < 6:
                evs.append(dict(common, rule_id=self.r_act, hit=0, evaluated=1 if i >= 3 else 0, missing=1 if i < 3 else 0))
        evs.append({"rec_seq": 1, "rule_id": "", "ts": NOW - 30 * D, "role": "chat", "event_seq": None, "no_event": 1, "hit": 0, "evaluated": 0, "missing": 0,
                    "rulebook_ver": None, "plan": None})          # вне окна 7 суток
        store2.insert_rows(self.c, "rule_evals", evs)

    def test_rules_schema(self):
        d = MO.memory_rules(self.root, NOW)
        self.assertEqual(set(d), RULES_KEYS)
        self.assertEqual(d["state"], "ok")
        self.assertEqual(set(d["mode"]), {"apply", "why", "ver"})
        self.assertEqual(d["current"], "R-0002")
        self.assertEqual(d["versions_total"], 2)
        for v in d["versions"]:
            self.assertEqual(set(v), VERSION_KEYS)
        self.assertEqual(d["versions"][0]["ver"], "R-0002")              # новые сверху
        for r in d["rules"]:
            self.assertEqual(set(r), RULE_KEYS)
        self.assertEqual([r["rule_id"] for r in d["active"]], [self.r_act])
        self.assertEqual(d["counts"], {"active": 1, "trial": 1})
        (w,) = d["waiting"]
        self.assertEqual(set(w), WAIT_KEYS)
        self.assertEqual((w["rule_id"], w["wait"], w["since_ts"], w["waited_s"]), (self.r_wait, "LOSS_CAP", NOW - 3 * D, 3 * D))
        cap = self.cfg["promotion"]["loss_cap_wait_days"] * D
        self.assertEqual((w["deadline_ts"], w["left_s"]), (NOW - 3 * D + cap, cap - 3 * D))
        fm = d["feat_missing"]
        self.assertEqual(set(fm), {"window_days", "all", "by_rule"})
        self.assertEqual(fm["window_days"], 7)
        is_num(self, fm["all"], 6)
        self.assertEqual(fm["all"]["v"], 0.5)
        (br,) = fm["by_rule"]
        self.assertEqual(set(br), NUM_KEYS | {"rule_id", "missing"})
        self.assertEqual((br["rule_id"], br["v"], br["n"], br["small"], br["missing"]), (self.r_act, 0.5, 6, True, 3))
        ne = d["no_event"]
        self.assertEqual(set(ne), NUM_KEYS | {"window_days", "threshold", "yellow"})
        self.assertEqual((ne["v"], ne["n"], ne["small"], ne["threshold"], ne["yellow"]), (0.4, 10, True, 0.2, True))

    def test_waiting_ready_has_no_deadline(self):
        V.set_wait(self.c, self.r_wait, "READY", NOW - D)
        (w,) = MO.memory_rules(self.root, NOW)["waiting"]
        self.assertEqual((w["wait"], w["since_ts"], w["deadline_ts"], w["left_s"]), ("READY", NOW - D, None, None))

    def test_lessons_schema(self):
        d = MO.memory_lessons(self.root, NOW)
        self.assertEqual(set(d), LESSONS_KEYS)
        self.assertEqual((d["state"], d["db"], d["total"], d["small_n"]), ("ok", "ok", 2, 30))
        self.assertEqual(d["by_src"], {"labels": 2})
        rows = {r["lesson_id"]: r for r in d["rows"]}
        for r in rows.values():
            self.assertEqual(set(r), LESSON_ROW_KEYS)
            self.assertEqual(set(r["effect"]), NUM_KEYS | {"lo", "hi"})
            self.assertEqual(set(r["claim"]), {"metric", "dir", "vs"})
        a, b = rows[self.l1], rows[self.l2]
        self.assertEqual((a["n"], a["effect"]["n"], a["effect"]["small"], b["effect"]["small"]), (20, 20, True, False))
        self.assertEqual(a["effect"]["v"], -0.4)
        self.assertLess(a["effect"]["lo"], -0.4)
        self.assertGreater(a["effect"]["hi"], -0.4)
        self.assertAlmostEqual(a["effect"]["hi"] - a["effect"]["v"], 2.093 * 0.1, places=2)     # t(0.975, 19) ≈ 2.093
        self.assertEqual(set(a["trial"]), TRIAL_KEYS)
        self.assertEqual(set(a["rule"]), RULE_KEYS)
        self.assertEqual((a["rule"]["rule_id"], a["rule"]["status"]), (self.r_act, "active"))
        self.assertEqual(b["trial"]["deadline_ts"], NOW + 25 * D)
        self.assertTrue(a["can_reject"])

    def test_path_schema_and_funnel(self):
        d = MO.memory_path(self.root, NOW)
        self.assertEqual(set(d), PATH_KEYS)
        self.assertEqual(d["state"], "ok")
        self.assertEqual(set(d["funnel"]), FUNNEL_KEYS)
        self.assertEqual(d["funnel"], {"candidates": 3, "hyp": 2, "trial": 2, "confirmed": 0, "rule_active": 1})
        self.assertEqual([p["candidate"]["id"] for p in d["paths"]], [3, 2, 1])
        for p in d["paths"]:
            self.assertEqual(set(p), STEP_KEYS)
            self.assertEqual(set(p["candidate"]), CAND_KEYS)
        dropped, _w, act = d["paths"]
        self.assertEqual((dropped["candidate"]["code"], dropped["lesson"], dropped["trial"], dropped["rule"], dropped["version"]),
                         ("NOT_SIGNIFICANT", None, None, None, None))
        self.assertEqual(act["lesson"]["lesson_id"], self.l1)
        self.assertEqual(act["rule"]["rule_id"], self.r_act)
        self.assertEqual(set(act["version"]), {"in", "in_ts", "out", "out_ts", "out_reason"})
        self.assertEqual(act["version"]["in"], "R-0002")
        one = MO.memory_path(self.root, NOW, self.l2)
        self.assertEqual([p["lesson"]["lesson_id"] for p in one["paths"]], [self.l2])
        bad = MO.memory_path(self.root, NOW, "../x")
        self.assertEqual((bad["ok"], bad["error"]), (False, "bad_args"))

    def test_cases_schema(self):
        from tb.memory import cases as CS
        rows = [{"case_id": "C-20261001-BTC-a1", "sym": "BTC", "mode": "paper", "r": 1.0}, {"case_id": "C-20261002-ETH-a2", "sym": "ETH", "mode": "paper", "r": -0.5},
                {"case_id": "C-20261003-SOL-a3", "sym": "SOL", "mode": "testnet", "r": 0.2}]
        fake = [{"case_id": r["case_id"], "ts": "2026-10-0%dT00:00:00Z" % (i + 1), "type": "trade", "situation": {"sym": r["sym"], "side": "long", "setup": "range_fade"},
                 "src": {"mode": r["mode"]}, "decision": {"action": "enter"}, "result": {"status": "closed", "r": r["r"]}} for i, r in enumerate(rows)]
        with mock.patch.object(CS, "load", return_value=fake):
            d = MO.memory_cases(self.root, NOW)
        self.assertEqual(set(d), CASES_KEYS)
        self.assertEqual((d["state"], d["total"], d["by_type"], d["by_status"]), ("ok", 3, {"trade": 3}, {"closed": 3}))
        modes = {m["mode"]: m for m in d["modes"]}
        self.assertEqual(sorted(modes), ["paper", "testnet"])                  # режимы не смешиваются
        for m in modes.values():
            self.assertEqual(set(m), {"mode", "n", "mean_r", "win_share"})
            is_num(self, m["mean_r"], m["n"])
            is_num(self, m["win_share"], m["n"])
        self.assertEqual((modes["paper"]["mean_r"]["v"], modes["paper"]["win_share"]["v"]), (0.25, 0.5))
        self.assertEqual([r["case_id"] for r in d["recent"]], [r["case_id"] for r in reversed(rows)])
        for r in d["recent"]:
            self.assertEqual(set(r), CASE_ROW_KEYS)

    def test_num_helper(self):
        is_num(self, MO.num(1.0, 29), 29)
        self.assertTrue(MO.num(1.0, 29)["small"])
        self.assertFalse(MO.num(1.0, 30)["small"])
        self.assertEqual(MO.share(0, 0), {"v": None, "n": 0, "small": True})
        e = MO.effect(0.1, None, 50)
        self.assertEqual((e["lo"], e["hi"]), (None, None))


# ---------------------------------------------------------------- действия владельца

class Actions(Base):
    def setUp(self):
        Base.setUp(self)
        self.v2()
        self.acts = MO.MemoryActions(self.root, clock=lambda: NOW)
        self.ws = WS()
        self.hello = self.acts.open(self.ws)

    def act(self, op, **kw):
        m = dict({"op": op, "id": "a1", "csrf": self.hello["csrf"]}, **kw)
        r = self.acts.handle(self.ws, m, now=NOW)
        self.assertTrue(ACTION_KEYS <= set(r), r)
        return r

    def test_hello(self):
        self.assertEqual(set(self.hello), {"csrf", "actions", "confirm"})
        self.assertEqual(self.hello["actions"], ["lesson_reject", "rule_disable", "rules_disable_all"])
        self.assertEqual(self.hello["confirm"]["lesson_reject"], [])
        self.assertEqual(sorted(self.hello["confirm"]["rule_disable"]), ["block_forever", "rule_off"])
        self.assertNotIn("confirm", MO.ACTIONS)
        self.assertNotIn("promote", " ".join(MO.ACTIONS))                 # операций «подтвердить» и «продвинуть» нет
        self.assertEqual(sorted(MO.READ_OPS), ["memory_cases", "memory_lessons", "memory_path", "memory_rules"])

    def test_rule_disable_requires_confirm(self):
        rid = self.active_rule(SPREAD, self.lesson(SPREAD))
        r = self.act("rule_disable", rule_id=rid)
        self.assertEqual((r["ok"], r["code"], r["needs_confirm"]), (False, "E_CONFIRM", True))
        self.assertEqual([w["id"] for w in r["warnings"]], ["rule_off", "block_forever"])
        r = self.act("rule_disable", rule_id=rid, accepted=["rule_off"])          # не все риски приняты
        self.assertEqual(r["code"], "E_CONFIRM")
        r = self.act("rule_disable", rule_id=rid, kind="challenge")
        self.assertEqual((r["ok"], r["code"]), (True, "CONFIRM"))
        self.assertEqual(V.rule(self.c, rid)["status"], "active")
        self.assertEqual(self.owner(), [])
        r = self.act("rule_disable", rule_id=rid, accepted=self.hello["confirm"]["rule_disable"])
        self.assertEqual((r["ok"], r["code"]), (True, "OK"))
        self.assertEqual((r["report"]["done"], r["report"]["ver"]), ("disabled", "R-0003"))
        self.assertEqual(V.rule(self.c, rid)["status"], "disabled")
        self.assertEqual(self.owner(), [("rulebook", "OWNER_DISABLE", rid)])

    def test_rule_disable_unknown_and_bad_id(self):
        acc = self.hello["confirm"]["rule_disable"]
        self.assertEqual(self.act("rule_disable", rule_id="RL9999", accepted=acc)["code"], "E_ARGS")
        self.assertEqual(self.act("rule_disable", rule_id="x; rm", accepted=acc)["code"], "E_ARGS")

    def test_disable_all(self):
        rid = self.active_rule(SPREAD, self.lesson(SPREAD))
        r = self.act("rules_disable_all")
        self.assertEqual(r["code"], "E_CONFIRM")
        self.assertTrue(RV.read_switch(self.root))                              # без подтверждения выключатель не тронут
        r = self.act("rules_disable_all", accepted=self.hello["confirm"]["rules_disable_all"])
        self.assertEqual((r["ok"], r["code"], r["report"]["disabled"]), (True, "OK", [rid]))
        self.assertFalse(RV.read_switch(self.root))
        self.assertEqual(self.owner(), [("rulebook", "DISABLE_ALL", None)])

    def test_disable_all_db_unavailable_retry(self):
        KM.write_json(KM.restoring_path(self.root), {"pid": 1})
        r = self.act("rules_disable_all", accepted=self.hello["confirm"]["rules_disable_all"])
        self.assertEqual((r["ok"], r["code"], r["report"]["retry"]), (False, "E_RETRY", True))
        self.assertIn("повторите", r["msg"])
        self.assertFalse(RV.read_switch(self.root))

    def test_lesson_reject_without_dialog(self):
        lid = self.lesson(SPREAD)
        rid = self.trial_rule(SPREAD, lid)
        r = self.act("lesson_reject", lesson_id=lid)
        self.assertEqual((r["ok"], r["code"]), (True, "OK"))
        self.assertEqual(LS.read_all(self.root)[lid]["status"], "rejected")
        self.assertEqual(TR.by_rule(self.c, rid)["state"], "closed")
        self.assertEqual(self.owner(), [("lesson", "OWNER_REJECT", lid)])
        ch = self.act("lesson_reject", lesson_id=lid, kind="challenge")
        self.assertEqual((ch["code"], ch["needs_confirm"], ch["warnings"]), ("OK", False, []))

    def test_lesson_reject_brain_lesson_refused(self):
        lid = LS.create(self.root, SPREAD, dict(CLAIM), "owner", [], memcfg.load(self.root), NOW)["lesson_id"]
        r = self.act("lesson_reject", lesson_id=lid)
        self.assertEqual(r["code"], "E_ARGS")
        self.assertEqual(LS.read_all(self.root)[lid]["status"], "hyp")
        self.assertEqual(self.act("lesson_reject", lesson_id="L-9999")["code"], "E_ARGS")

    def test_csrf_rate_unknown(self):
        r = self.acts.handle(self.ws, {"op": "rules_disable_all", "csrf": "nope", "accepted": self.hello["confirm"]["rules_disable_all"]}, now=NOW)
        self.assertEqual(r["code"], "E_CSRF")
        self.assertTrue(RV.read_switch(self.root))
        self.assertEqual(self.acts.handle(WS(), {"op": "lesson_reject", "csrf": self.hello["csrf"]}, now=NOW)["code"], "E_AUTH")
        self.assertEqual(self.acts.handle(self.ws, {"op": "rule_promote", "csrf": self.hello["csrf"]}, now=NOW)["code"], "E_ARGS")
        codes = [self.act("lesson_reject", lesson_id="L-9999")["code"] for _ in range(MO.RATE_PER_MIN + 1)]
        self.assertEqual(codes[-1], "E_RATE")
        self.acts.close(self.ws)
        self.assertEqual(self.acts.handle(self.ws, {"op": "lesson_reject", "csrf": self.hello["csrf"]}, now=NOW)["code"], "E_AUTH")


class Layering(unittest.TestCase):
    def test_memory_ops_imports(self):
        import ast
        with open(MO.__file__, encoding="utf-8") as f:
            src = f.read()
        names = set()
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Import):
                names.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                names.add(n.module)
        bad = [x for x in names if x.split(".")[0] in ("subprocess", "socket", "signal", "multiprocessing")
               or x.startswith(("tb.venues", "tb.executor", "tb.learnloop", "tb.llm", "tb.brain", "tb.gateway", "tb.launcher", "tb.lifecycle"))]
        self.assertEqual(bad, [])

    def test_closure_has_no_exchange_or_process_code(self):
        import test_layering as TL
        roots = ["tb.dash2.memory_ops", "tb.knowledge.rulebook_cli", "tb.learnloop.trial", "tb.learnloop.versions", "tb.learnloop.rules", "tb.learnloop.conf"]
        seen, stack, bad = set(), list(roots), {}
        while stack:
            mod = stack.pop()
            if mod in seen:
                continue
            seen.add(mod)
            p = os.path.join(os.path.dirname(TL.SRC), *mod.split("."))
            p = os.path.join(p, "__init__.py") if os.path.isdir(p) else p + ".py"
            if not os.path.isfile(p):
                continue
            imps = TL.imports_of(TL.read(p), mod)
            hit = [n for n in imps if TL.under(n, ("tb.venues", "tb.executor", "tb.launcher", "tb.lifecycle", "tb.llm", "subprocess", "signal"))]
            if hit:
                bad[mod] = hit
            stack.extend(n for n in imps if n.startswith("tb.") and n not in seen)
        self.assertGreater(len(seen), len(roots))
        self.assertEqual(bad, {})


# ---------------------------------------------------------------- маршруты сокета

@unittest.skipUnless(HAVE_WS, "нет websockets")
class MemoryServer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _bootstrap.require_localhost()
        self.root = tempfile.mkdtemp(prefix="tbdash2ms_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.ready = asyncio.get_event_loop().create_future()
        self.task = asyncio.ensure_future(S.serve(port=0, root=self.root, ready=self._ready))
        await asyncio.wait_for(self.ready, 5)

    def _ready(self, hub, srv):
        self.port = list(srv.sockets)[0].getsockname()[1]
        hub.port = self.port
        self.ready.set_result(True)

    async def asyncTearDown(self):
        self.task.cancel()
        try:
            await self.task
        except BaseException:
            pass

    async def wait_topic(self, ws, topic, timeout=3):
        for _ in range(80):
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if m["topic"] == topic:
                return m
        self.fail("нет %s" % topic)

    async def test_read_ops_and_actions(self):
        async with connect("ws://127.0.0.1:%d/ws" % self.port) as a, connect("ws://127.0.0.1:%d/ws" % self.port) as b:
            hello = await self.wait_topic(a, "hello")
            mem = hello["data"]["memory"]
            self.assertTrue(mem["csrf"])
            for op, topic in MO.READ_OPS.items():
                await a.send(json.dumps({"op": op}))
                m = await self.wait_topic(a, topic)
                self.assertEqual((m["data"]["ok"], m["data"]["state"]), (True, "no_data"), op)
            await a.send(json.dumps({"op": "rule_disable", "id": "x1", "csrf": mem["csrf"], "rule_id": "RL0001"}))
            m = await self.wait_topic(a, MO.RESULT_TOPIC)
            self.assertEqual((m["data"]["id"], m["data"]["code"]), ("x1", "E_CONFIRM"))
            await a.send(json.dumps({"op": "rules_disable_all", "id": "x2", "csrf": "bad", "accepted": mem["confirm"]["rules_disable_all"]}))
            self.assertEqual((await self.wait_topic(a, MO.RESULT_TOPIC))["data"]["code"], "E_CSRF")
            seen = []
            try:
                while True:
                    seen.append(json.loads(await asyncio.wait_for(b.recv(), 0.5))["topic"])
            except asyncio.TimeoutError:
                pass
            self.assertFalse([t for t in seen if t.startswith("memory_")])

    async def test_read_failure_keeps_connection(self):
        async with connect("ws://127.0.0.1:%d/ws" % self.port) as a:
            with mock.patch.object(MO, "memory_rules", side_effect=RuntimeError("boom")):
                await a.send(json.dumps({"op": "memory_rules"}))
                m = await self.wait_topic(a, "memory_rules_detail")
            self.assertEqual((m["data"]["ok"], m["data"]["error"]), (False, "failed"))
            await a.send(json.dumps({"op": "ping"}))
            await self.wait_topic(a, "pong")


if __name__ == "__main__":
    unittest.main()
