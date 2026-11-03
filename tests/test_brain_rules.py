"""R2, задача 13: применение правил rulebook в общих точках входа (Requirement 7.7, 7.11, 8.3).

Точка (а) brain/cands.candidates (через brief.build, для раннера entry_scout и chat через h_brief) и точка (б) gateway h_submit (submit_plan
обоих путей) идут через одну функцию cands.rule_check: resolve_event -> rule_features -> learn_rules.apply. Проверяется: разметка RULE_<id>
и отказ E_RULE_<id>; одно решение (монета, сторона, событие) одинаково в двух точках; event_seq доверяется только заданию раннера (meta),
подмена (чужая монета, старое, idea_stale) отвергается, для chat присланный event_seq игнорируется; предыдущий ротированный файл шины
читается только при недавней ротации; запись rule_eval (kind="note") для принятого и отклонённого плана, её нет при раннем отказе, она
не попадает в expstats.rules_by_plan; RULES_NO_EVENT (запись без события); режим без применения при пустом rulebook, просроченной
аренде, выключателе, восстановлении и повреждении базы — вывод прежний поле в поле. Всё во временном корне (боевой каталог не читается).
"""
import copy
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import _gw
import test_brain_entry as TBE
from tb import paths
from tb.brain import brief as brief_mod, cands, entryplan, validate
from tb.core import decisions, expstats
from tb.knowledge import markers as KM
from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import cycle
from tb.learnloop import versions as V

REPO = _bootstrap.ROOT
P2_KINDS = ["range_5m", "turnover_surge", "hot_enter", "oi_spike", "liq_cascade"]
CFG_FILES = ("learnloop.json", "memory.json", "shadow_policies.json", "geometry.json")


def copy_cfg(root):
    os.makedirs(os.path.join(root, "config"), exist_ok=True)
    for f in CFG_FILES:
        src = os.path.join(REPO, "config", f)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(root, "config", f))


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def ev(seq, sym, kind, ts, **d):
    dd = {"kind": kind, "dir": "up", "spread_bps": 3.0}
    dd.update(d)
    return {"seq": seq, "ts": iso(ts), "type": "scanner_signal", "pri": "wake", "sym": sym, "d": dd}


def write_events(root, evs, path=None):
    p = path or paths.bus_events(root=root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for e in evs:
            f.write(json.dumps(e) + "\n")
    return p


def make_db(root, now, with_rule=True):
    """База v2: R-0001 и (по умолчанию) активное правило RL0001 skip_when spread_bps >= 5 в области P2 (with_dir) — версия R-0002."""
    with open(os.path.join(root, "config", "learnloop.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    c = store.open_rw(root)
    try:
        store2.migrate_v2a(c, root, explicit=True, backup=False, now=now)
        V.init_rulebook(c, now)
        if with_rule:
            draft = {"kind": "skip_when", "cond": [{"feature": "spread_bps", "op": ">=", "value": 5.0}], "param": {},
                     "scope": {"policy": "P2", "kinds": P2_KINDS, "side": "with_dir", "entry": "candle_confirm_limit"}, "env_norm": "mainnet",
                     "from_lesson": "L-0001", "policy_ver": 1}
            trial = {"criterion": {"n_target": 150}, "criterion_h": "0" * 16, "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1", "start_ts": int(now),
                     "deadline_ts": int(now) + 30 * 86400, "env_norm": "mainnet"}
            ids = V.create_with_trial(c, draft, trial, now)
            V.promote(c, ids["rule_id"], now, cfg=cfg, root=root, backup=False)
    finally:
        c.close()


def lease(root, now, age_s=60.0):
    KM.write_json(RV.lease_path(root), {"ts": now - age_s, "rulebook_ver": "R-0002"})


def fixture_brief():
    """Досье события SOL, на котором геометрия даёт ok-кандидатов обеих сторон (как test_brain_entry.Candidates)."""
    b = copy.deepcopy(TBE.SCAN_BRIEF)
    b.update(copy.deepcopy(TBE.Candidates.B))
    b["struct"]["s"]["SOL"].update(res=[[146.5, 0.9, 0.5, "dh"], [150.0, 3.3, 1.8, "wh"]], sup=[[144.0, -0.8, 0.4, "s1h"], [141.0, -2.9, 1.5, "pdl"]])
    b["levels"]["SOL"]["swing_5m"] = [144.6, 145.0]
    return b


class RulesBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbrules_")
        self.addCleanup(shutil.rmtree, self.root, True)
        copy_cfg(self.root)
        RV.clear_cache()
        store2._STATUS_CACHE.clear()
        self.now = time.time()

    def active_rule(self):
        make_db(self.root, self.now)
        lease(self.root, self.now)


# ---------------------------------------------------------------- точка (а): разметка кандидатов

class TestCands(RulesBase):
    def base(self):
        return cands.candidates(fixture_brief(), "SOL")            # без корня: правила не читаются (прежний вывод)

    def test_rule_marks_with_dir_side(self):
        self.active_rule()
        write_events(self.root, [ev(1, "SOL", "hot_enter", self.now - 30, dir="up", spread_bps=6.0)])
        base = self.base()
        self.assertTrue(any(c.get("ok") and c["side"] == "long" for c in base) and any(c.get("ok") and c["side"] == "short" for c in base))
        out = cands.candidates(fixture_brief(), "SOL", root=self.root, now=self.now)
        self.assertEqual([c["n"] for c in out], [c["n"] for c in base])                                 # места и номера прежние
        for b, c in zip(base, out):
            if b.get("ok") and b["side"] == "long":
                self.assertEqual((c["ok"], c["fail"]), (False, "RULE_RL0001"))
                self.assertEqual({k: v for k, v in c.items() if k not in ("ok", "fail")}, {k: v for k, v in b.items() if k != "ok"})
            else:
                self.assertEqual(c, b)                                                                   # short (против сигнала) вне области
        self.assertEqual(cands.ok_before_rules(out), sum(1 for c in base if c.get("ok")))

    def test_rule_pass_and_no_event_keep_output(self):
        self.active_rule()
        base = self.base()
        self.assertEqual(cands.candidates(fixture_brief(), "SOL", root=self.root, now=self.now), base)        # события нет: правило не применяется
        write_events(self.root, [ev(1, "SOL", "hot_enter", self.now - 30, spread_bps=3.0)])
        self.assertEqual(cands.candidates(fixture_brief(), "SOL", root=self.root, now=self.now), base)        # условие ложно

    def test_no_apply_modes_output_unchanged(self):
        """Пустой rulebook, нет базы, просроченная аренда, выключатель, маркер восстановления, сигнал повреждения, ошибка базы — вывод прежний."""
        write_events(self.root, [ev(1, "SOL", "hot_enter", self.now - 30, spread_bps=9.0)])
        base = self.base()
        same = lambda: self.assertEqual(cands.candidates(fixture_brief(), "SOL", root=self.root, now=self.now), base)      # noqa: E731
        same()                                                                                            # нет базы
        make_db(self.root, self.now, with_rule=False)
        lease(self.root, self.now)
        same()                                                                                            # пустой rulebook R-0001
        shutil.rmtree(self.root)
        os.makedirs(self.root)
        copy_cfg(self.root)
        RV.clear_cache()
        write_events(self.root, [ev(1, "SOL", "hot_enter", self.now - 30, spread_bps=9.0)])
        make_db(self.root, self.now)
        same()                                                                                            # аренды нет
        lease(self.root, self.now, age_s=7 * 3600)
        same()                                                                                            # аренда просрочена
        lease(self.root, self.now)
        self.assertNotEqual(cands.candidates(fixture_brief(), "SOL", root=self.root, now=self.now), base)    # контроль: правило действует
        KM.write_json(RV.switch_path(self.root), {"apply": False})
        same()
        KM.remove(RV.switch_path(self.root))
        KM.write_json(KM.restoring_path(self.root), {"pid": 1, "ts": int(self.now)})
        same()
        KM.remove(KM.restoring_path(self.root))
        KM.write_corrupt(self.root, where="label_step")
        same()
        KM.remove(KM.corrupt_path(self.root))
        with mock.patch.object(store, "open_ro", side_effect=store.DbCorruptError("DB_CORRUPT")):
            RV.clear_cache()
            same()
        with mock.patch.object(store, "open_ro", side_effect=store.KnowledgeRestoring("restoring")):
            same()
        with mock.patch.object(RV, "active", side_effect=OSError("boom")):
            same()                                                                                        # любая ошибка — без применения

    def test_no_db_is_fast(self):
        """Недоступная база не замедляет разметку: режим без применения без чтения шины."""
        with mock.patch.object(RV, "resolve_event", side_effect=AssertionError("шина не читается без правил")):
            t0 = time.perf_counter()
            for _ in range(20):
                cands.candidates(fixture_brief(), "SOL", root=self.root, now=self.now)
            self.assertLess((time.perf_counter() - t0) / 20, 0.05)


class TestValidateAndEntryplan(RulesBase):
    def marked(self):
        self.active_rule()
        write_events(self.root, [ev(1, "SOL", "hot_enter", self.now - 30, dir="up", spread_bps=6.0)])
        b = fixture_brief()
        b["cands"] = cands.candidates(b, "SOL", root=self.root, now=self.now)
        return b

    def test_validator_rechecks_rule_code(self):
        b = self.marked()
        ctx = validate.entry_context(b, TBE.EV)
        ctx["cands"] = b["cands"]
        rc = next(c for c in b["cands"] if c.get("fail") == "RULE_RL0001")
        o = TBE.propose(side=rc["side"], horizon=rc["horizon"], level=rc["level"], stop=rc["stop"], tp=rc["tp"])
        self.assertIn("RULE_RL0001", validate.semantic("entry/1", o, ctx))
        sc = next((c for c in b["cands"] if c.get("ok") and c["side"] == "short"), None)
        if sc is not None:
            o = TBE.propose(side="short", horizon=sc["horizon"], level=sc["level"], stop=sc["stop"], tp=sc["tp"])
            self.assertFalse([e for e in validate.semantic("entry/1", o, ctx) if e.startswith("RULE_")])

    def test_model_cannot_lift_rule_code(self):
        """Модель называет кандидата под правилом (es_take) или подаёт свои числа той же стороны — отказ; подмена кода в ответе ничего не меняет:
        код берётся из разметки кода (ctx.cands), а не из ответа."""
        b = self.marked()
        rc = next(c for c in b["cands"] if c.get("fail") == "RULE_RL0001")
        obj, errs, _c = entryplan.expand("es_take", {"candidate": rc["n"], "setup": "level_reclaim", "why": "x", "fail": None, "ok": True}, b["cands"], "SOL", 1)
        self.assertIsNone(obj)
        self.assertEqual(errs, ["CANDIDATE_FAILED:RULE_RL0001"])
        ctx = validate.entry_context(b, TBE.EV)
        ctx["cands"] = b["cands"]
        o = TBE.propose(side="long", fail=None)
        self.assertIn("RULE_RL0001", validate.semantic("entry/1", {k: v for k, v in o.items() if k != "fail"}, ctx))
        only = [c for c in b["cands"] if c["side"] == "long"]                                    # все ok-кандидаты отсечены правилом -> NO_CANDIDATES до модели
        self.assertEqual(validate.entry_precheck({"has_brief": True, "session_type": "intraday", "cands": only}), "NO_CANDIDATES")


# ---------------------------------------------------------------- точка (б): submit_plan

class GwRules(_gw.GwBase):
    def setUp(self):
        _gw.GwBase.setUp(self)
        copy_cfg(self.root)
        RV.clear_cache()
        store2._STATUS_CACHE.clear()
        self.now = time.time()

    def active_rule(self):
        make_db(self.root, self.now)
        lease(self.root, self.now)

    def submit(self, agent="chat", meta=None, **args):
        a = dict({"plan": _gw.plan(), "wait_s": 0, "reason": "тест правил"}, **args)
        return self.gw.call(agent, "submit_plan", a, meta=meta)

    def evals(self):
        return [r for r in decisions.read(0, None, 0, self.root) if r.get("what") == "rule_eval"]


class TestSubmit(GwRules):
    def test_chat_rejected_by_rule_and_rule_eval(self):
        self.active_rule()
        write_events(self.root, [ev(5, "SOL", "hot_enter", self.now - 20, dir="down", spread_bps=7.0)])     # шаблон плана — short: по сигналу
        r = self.submit()
        self.assertFalse(r["ok"])
        self.assertEqual((self.code(r), r["status"]), ("E_RULE_RL0001", "denied"))
        self.assertEqual(self.inbox(), [])                                                    # до исполнителя план не дошёл
        e = self.evals()
        self.assertEqual(len(e), 1)
        self.assertEqual((e[0]["kind"], e[0]["res"]), ("note", {"ok": False, "code": "E_RULE_RL0001"}))
        bs = e[0]["basis"]
        self.assertEqual(sorted(bs), sorted(RV.RULE_EVAL_KEYS))
        self.assertEqual((bs["role"], bs["event_seq"], bs["rule_hits"], bs["rule_eval_ids"], bs["feat_missing_ids"], bs["rulebook_ver"], bs["plan"]),
                         ("chat", 5, ["RL0001"], ["RL0001"], [], "R-0002", None))
        self.assertEqual(expstats.rules_by_plan(self.root), {})                                  # note не засчитывается как сработавшее правило плана

    def test_accepted_plan_rule_eval(self):
        self.active_rule()
        write_events(self.root, [ev(5, "SOL", "hot_enter", self.now - 20, dir="down", spread_bps=2.0)])
        r = self.submit()
        self.assertIsNone(r["ok"], r)                                                           # в очереди исполнителя
        e = self.evals()
        self.assertEqual(len(e), 1)
        self.assertEqual(e[0]["res"], {"ok": True, "code": "OK"})
        self.assertEqual((e[0]["basis"]["rule_hits"], e[0]["basis"]["rule_eval_ids"], e[0]["basis"]["event_seq"]), ([], ["RL0001"], 5))
        self.assertEqual(expstats.rules_by_plan(self.root), {})

    def test_against_dir_out_of_scope(self):
        self.active_rule()
        write_events(self.root, [ev(5, "SOL", "hot_enter", self.now - 20, dir="up", spread_bps=9.0)])       # short против сигнала: вне области
        self.assertIsNone(self.submit()["ok"])
        self.assertEqual(self.evals()[0]["basis"]["rule_eval_ids"], [])

    def test_early_rejections_write_no_rule_eval(self):
        self.active_rule()
        write_events(self.root, [ev(5, "SOL", "hot_enter", self.now - 20, dir="down", spread_bps=1.0)])      # правило не срабатывает: лимит в час считает принятые
        self.assertEqual(self.code(self.gw.call("bot", "submit_plan", {"plan": _gw.plan(sym="BTC")})), "E_SYMBOL_FORBIDDEN")        # check_scope
        self.assertEqual(self.code(self.submit(plan=_gw.plan(mode="live"))), "E_TRADING_FLAG")                                     # _need_trading
        r = self.submit(plan={"schema": "tb.plan/1", "symbol": "SOL"})
        self.assertEqual(r["status"], "rejected_local")                                                                             # precheck
        self.risk = 99.0
        self.assertEqual(self.code(self.submit()), "E_RISK_LIMIT")                                                                  # check_plan
        self.risk = 0.5
        for _ in range(2):
            self.gw.call("bot", "submit_plan", {"plan": _gw.plan(), "wait_s": 0, "reason": "r"})
        n = len(self.evals())
        self.assertEqual(self.code(self.gw.call("bot", "submit_plan", {"plan": _gw.plan(), "wait_s": 0, "reason": "r"})), "E_RATE_PLANS")  # лимит в час
        self.assertEqual(len(self.evals()), n)
        self.assertEqual(n, 2)                                                                  # только два вызова дошли до точки правил

    def test_chat_event_seq_ignored(self):
        """chat: присланный event_seq (событие с узким спредом) не используется — берётся последнее событие монеты."""
        self.active_rule()
        write_events(self.root, [ev(3, "SOL", "hot_enter", self.now - 200, dir="down", spread_bps=1.0),
                                 ev(6, "SOL", "range_5m", self.now - 10, dir="down", spread_bps=8.0)])
        r = self.submit(event_seq=3)
        self.assertEqual(self.code(r), "E_RULE_RL0001")
        self.assertEqual(self.evals()[-1]["basis"]["event_seq"], 6)
        r = self.submit(meta={"role": "chat", "event_seq": 3})                                   # и meta-event_seq не у раннера entry_scout не доверяется
        self.assertEqual(self.code(r), "E_RULE_RL0001")

    def test_entry_scout_event_seq_trusted_and_substitution_rejected(self):
        self.active_rule()
        n = self.now
        write_events(self.root, [ev(3, "SOL", "hot_enter", n - 200, dir="down", spread_bps=1.0), ev(4, "ETH", "hot_enter", n - 15, dir="down", spread_bps=1.0),
                                 ev(5, "SOL", "hot_enter", n - 2000, dir="down", spread_bps=1.0), ev(7, "SOL", "idea_stale", n - 5),
                                 ev(8, "SOL", "range_5m", n - 10, dir="down", spread_bps=8.0)])
        meta = {"role": "entry_scout", "event_seq": 3}
        self.assertIsNone(self.submit(meta=meta)["ok"])                                          # событие задания раннера: спред 1 — правило не срабатывает
        self.assertEqual(self.evals()[-1]["basis"]["event_seq"], 3)
        self.assertEqual(self.evals()[-1]["basis"]["role"], "entry_scout")
        for bad in (4, 5, 7, 99):                                                                # чужая монета, старое, idea_stale, нет такого
            r = self.submit(meta={"role": "entry_scout", "event_seq": bad}, event_seq=3)
            self.assertEqual(self.code(r), "E_RULE_RL0001", bad)
            self.assertEqual(self.evals()[-1]["basis"]["event_seq"], 8, bad)

    def test_no_event_feat_missing_and_rules_no_event(self):
        self.active_rule()
        write_events(self.root, [ev(5, "SOL", "hot_enter", self.now - 5000, dir="down", spread_bps=9.0)])      # старше 900 с
        self.assertIsNone(self.submit()["ok"])                                                   # правило не срабатывает без события
        e = self.evals()[-1]
        self.assertEqual((e["basis"]["event_seq"], e["basis"]["feat_missing_ids"], e["basis"]["rule_hits"]), (None, ["RL0001"], []))
        rows = cycle.rule_eval_rows(e)
        self.assertEqual([(r["rule_id"], r["no_event"], r["missing"]) for r in rows], [("", 1, 1), ("RL0001", 1, 1)])

    def test_empty_rulebook_answers_unchanged_and_event_resolved(self):
        """Пустой rulebook: ответ шлюза прежний; запись rule_eval есть и событие разрешено по видам политики P2 (нет ложного RULES_NO_EVENT)."""
        make_db(self.root, self.now, with_rule=False)
        lease(self.root, self.now)
        write_events(self.root, [ev(5, "SOL", "hot_enter", self.now - 20, dir="down", spread_bps=9.0)])
        r = self.submit()
        self.assertIsNone(r["ok"])
        e = self.evals()[-1]
        self.assertEqual((e["basis"]["event_seq"], e["basis"]["rulebook_ver"], e["basis"]["rule_eval_ids"], e["basis"]["feat_missing_ids"]), (5, "R-0001", [], []))
        self.assertEqual(cands.policy_kinds(self.root), P2_KINDS)

    def test_previous_rotated_file_only_if_recent(self):
        self.active_rule()
        n = self.now
        write_events(self.root, [ev(9, "BTC", "range_5m", n - 5)])
        p = write_events(self.root, [ev(5, "SOL", "range_5m", n - 200, dir="down", spread_bps=9.0)],
                         path=paths.bus("events-%s.jsonl" % time.strftime("%Y%m%d", time.gmtime(n - 86400)), root=self.root))
        os.utime(p, (n - 150, n - 150))
        self.assertEqual(self.code(self.submit()), "E_RULE_RL0001")
        os.utime(p, (n - 1000, n - 1000))                                                        # ротация давно: файл не читается
        self.assertIsNone(self.submit()["ok"])
        self.assertIsNone(self.evals()[-1]["basis"]["event_seq"])

    def test_rule_eval_write_failure_does_not_break_gateway(self):
        self.active_rule()
        write_events(self.root, [ev(5, "SOL", "hot_enter", self.now - 20, dir="down", spread_bps=9.0)])
        with mock.patch.object(decisions, "append", side_effect=OSError("disk full")):
            self.assertEqual(self.code(self.submit()), "E_RULE_RL0001")
        write_events(self.root, [ev(6, "SOL", "hot_enter", self.now - 10, dir="down", spread_bps=1.0)])
        with mock.patch.object(decisions, "append", side_effect=TimeoutError("lock")):
            self.assertIsNone(self.submit()["ok"])
        with mock.patch.object(cands, "rule_check", side_effect=RuntimeError("boom")):
            self.assertIsNone(self.submit()["ok"])                                               # сбой применения — базовое поведение

    def test_cands_ok_n_from_brief(self):
        self.active_rule()
        write_events(self.root, [ev(5, "SOL", "hot_enter", self.now - 20, dir="up", spread_bps=9.0)])
        b = fixture_brief()
        b["brief_id"] = "B-20261103-1200-01"
        b["cands"] = cands.candidates(b, "SOL", root=self.root, now=self.now)
        brief_mod.save(b, root=self.root, now=self.now)
        self.submit(brief_id=b["brief_id"])
        self.assertEqual(self.evals()[-1]["basis"]["cands_ok_n"], cands.ok_before_rules(b["cands"]))
        self.assertGreater(self.evals()[-1]["basis"]["cands_ok_n"], len([c for c in b["cands"] if c.get("ok")]))
        self.submit()
        self.assertIsNone(self.evals()[-1]["basis"]["cands_ok_n"])


class TestBothPathsSameDecision(GwRules):
    """Одно решение (монета, сторона, событие) одинаково в cands.candidates и h_submit; оба пути (раннер entry_scout и chat через h_brief)."""

    def test_same_result_in_two_points(self):
        self.active_rule()
        for dir_, sp in (("down", 9.0), ("down", 1.0), ("up", 9.0)):
            RV.clear_cache()
            write_events(self.root, [ev(int(time.time() * 1000) % 10 ** 9, "SOL", "hot_enter", self.now - 1, dir=dir_, spread_bps=sp)])
            out = cands.candidates(fixture_brief(), "SOL", root=self.root, now=self.now)
            short_rule = any(c.get("fail") == "RULE_RL0001" for c in out if c["side"] == "short")
            r = self.submit()
            self.assertEqual(self.code(r) == "E_RULE_RL0001", short_rule, (dir_, sp))
            self.assertEqual(short_rule, dir_ == "down" and sp >= 5)

    def test_h_brief_passes_event_seq_only_for_entry_scout(self):
        seen = []
        real = brief_mod.build

        def spy(*a, **kw):
            seen.append(kw.get("event_seq"))
            return real(*a, **kw)
        with mock.patch.object(brief_mod, "build", side_effect=spy):
            self.gw.call("chat", "get_brief", {"scope": "event", "sym": "SOL", "seqs": [5]}, meta=None)
            self.gw.call("chat", "get_brief", {"scope": "event", "sym": "SOL", "seqs": [5]}, meta={"role": "chat", "event_seq": 5})
            self.gw.call("chat", "get_brief", {"scope": "event", "sym": "SOL", "seqs": [5]}, meta={"role": "entry_scout", "event_seq": 5})
        self.assertEqual(seen, [None, None, 5])

    def test_brief_build_passes_root_and_event_to_cands(self):
        seen = []

        def spy(b, sym, ctx=None, root=None, event_seq=None, now=None, view=None):
            seen.append((sym, root, event_seq, view is not None or root is not None))
            return None
        with mock.patch.object(brief_mod.cands_mod, "candidates", side_effect=spy):
            brief_mod.build("event", root=self.root, now=self.now, sym="SOL", seqs=[5], event_seq=5)
        self.assertEqual(seen, [("SOL", self.root, 5, True)])

    def test_rule_cuts_entry_for_both_roles(self):
        self.active_rule()
        write_events(self.root, [ev(5, "SOL", "hot_enter", self.now - 20, dir="down", spread_bps=9.0)])
        self.assertEqual(self.code(self.submit(meta={"role": "entry_scout", "event_seq": 5, "profile": "ds-flash"})), "E_RULE_RL0001")
        self.assertEqual(self.code(self.submit()), "E_RULE_RL0001")
        self.assertEqual([e["basis"]["role"] for e in self.evals()], ["entry_scout", "chat"])


if __name__ == "__main__":
    unittest.main()
