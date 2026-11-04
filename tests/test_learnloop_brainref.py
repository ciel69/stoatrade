"""tb.learnloop.brainref и смежные части второй итерации (R2, задача 31; Requirements 18.1–18.8): справочные уроки мозга (пороги brain_ref,
evidence.ref=true, не показываются мозгу, правил не порождают, конфиг не меняется), фильтр lessons.pool_mode, критерий режимов справочного урока,
справочная проверка кандидата модели с непроверяемым признаком (урок ref или N_TOO_SMALL), заморозка продвижения (история < 21 суток — не
вычисляется), условие по нарушениям (ok, fail, not_evaluable, not_evaluable_link), shadow_hits в rulebook_view и записи rule_eval, меню по
utc_session (выключено по умолчанию; LOW_POWER при недостижимой мощности; тест Уэлча). Временные корни, сети нет."""
import copy
import json
import os
import random
import shutil
import sqlite3
import tempfile
import unittest

import _bootstrap  # noqa: F401
from _memory_helpers import mk
from tb.core import decisions as DJ
from tb.knowledge import rulebook_view as RV
from tb.learnloop import brainref as BR
from tb.learnloop import candidates as CA
from tb.learnloop import conf as LC
from tb.learnloop import menu as MN
from tb.learnloop import verify as VF
from tb.memory import blocks, cases, similar
from tb.memory import cfg as memcfg
from tb.memory import lessons as LS

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = 86400
H = 3600
NOW = int(similar.ts_epoch("2026-11-03T00:00:00Z"))
SPREAD_COND = [{"feature": "regime", "op": "=", "value": "volatile"}]


def lcfg():
    with open(os.path.join(REPO, "config", "learnloop.json"), encoding="utf-8") as f:
        return json.load(f)


def iso(t):
    return cases.iso(t)


def brain_pool(n_bad=20, n_good=40, mode="paper", start=NOW - 20 * D, regime_bad="volatile", plan_prefix=None, err_bad=None):
    """Сделки мозга: n_bad убыточных (regime=regime_bad, R -1.2) и n_good прибыльных (trend/range, R +0.6), раз в 4 часа от start."""
    out = []
    for i in range(n_bad + n_good):
        bad = i < n_bad
        t = start + i * 4 * H
        c = mk(i, sym="S%d" % i, r=-1.2 if bad else 0.6, regime=regime_bad if bad else ("trend", "range")[i % 2], mode=mode, ts=iso(t),
               err=(err_bad or []) if bad else [], confirmed=not bad)
        if plan_prefix:
            c["refs"]["plan"] = "%s-%d" % (plan_prefix, i)
        out.append(c)
    return out


class Root(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbbref_")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "state"))
        os.makedirs(os.path.join(self.root, "config"))
        shutil.copy(os.path.join(REPO, "config", "memory.json"), os.path.join(self.root, "config", "memory.json"))
        self.mcfg = memcfg.load(self.root)
        self.cfg = lcfg()


# ---------------------------------------------------------------- pool_mode

class TestPoolMode(Root):
    def test_null_default_and_paper_filter(self):
        cs = brain_pool(4, 4, mode="paper") + brain_pool(4, 4, mode="live", start=NOW - 10 * D)
        self.assertIsNone(LS.pool_mode(self.mcfg))                                            # поставка: ключа нет -> null
        self.assertEqual(len(LS.pool(cs, self.mcfg)), 16)                                     # прежнее поведение
        pc = copy.deepcopy(self.mcfg)
        pc["lessons"]["pool_mode"] = "paper"
        self.assertEqual(set(c["src"]["mode"] for c in LS.pool(cs, pc)), {"paper"})
        pc["lessons"]["pool_mode"] = "bogus"
        self.assertEqual(len(LS.pool(cs, pc)), 16)                                            # неизвестное значение = null


# ---------------------------------------------------------------- справочные уроки

class TestBrainRef(Root):
    def test_ref_lessons_thresholds_flag_and_config_untouched(self):
        cs = brain_pool()
        p = os.path.join(self.root, "config", "memory.json")
        before = open(p, "rb").read()
        rep = BR.run_brain_ref(self.root, NOW, self.cfg, mcfg=self.mcfg, all_cases=cs)
        self.assertEqual(rep["code"], "OK")
        self.assertTrue(rep["made"])
        self.assertLessEqual(len(rep["made"]), self.mcfg["lessons"]["max_new_per_day"])           # бюджет lessons.max_new_per_day
        ls = LS.read_all(self.root)
        for lid in rep["made"]:
            l = ls[lid]
            self.assertEqual((l["evidence"]["src"], l["evidence"]["ref"], l["created_by"]), ("brain", True, "code"))
            self.assertGreaterEqual(l["evidence"]["n_closed"], self.cfg["second_iteration"]["brain_ref"]["min_n"])
            self.assertTrue(LS.is_ref(l))
        self.assertEqual(open(p, "rb").read(), before)                                        # файл конфигурации не меняется
        self.assertEqual(self.mcfg["lessons"]["min_n_propose"], 4)                             # копия cfg, исходный словарь цел

    def test_below_min_n_no_ref(self):
        cs = brain_pool(n_bad=10, n_good=40)                                                  # 10 < brain_ref.min_n (15), но >= min_n_propose (4)
        self.assertTrue(LS.propose(self.root, cs, self.mcfg, NOW))                            # обычный порог дал бы урок
        rep = BR.run_brain_ref(self.root, NOW, self.cfg, mcfg=self.mcfg, all_cases=cs)
        self.assertEqual([l for l in LS.read_all(self.root).values() if any(c.get("feature") == "regime" for c in l["cond"])], [])
        self.assertTrue(all(LS.read_all(self.root)[i]["evidence"]["n_closed"] >= 15 for i in rep["made"]))

    def test_not_shown_to_brain_and_no_rules(self):
        cs = brain_pool()
        made = BR.run_brain_ref(self.root, NOW, self.cfg, mcfg=self.mcfg, all_cases=cs)["made"]
        self.assertTrue(made)
        ls = list(LS.read_all(self.root).values())
        feats = {"regime": "volatile", "confirmed": False}
        self.assertEqual(LS.applicable(ls, feats, NOW, self.mcfg, limit=50), [])
        self.assertEqual(blocks.top_lessons(self.root, NOW, self.mcfg, limit=50, all_lessons=ls), [])
        self.assertEqual(blocks.lessons_payload(self.root, "all", 10, NOW, self.mcfg)["n"], 0)
        # обычный урок мозга с тем же условием мозгу виден (контроль)
        l = LS.create(self.root, [{"feature": "regime", "op": "in", "value": ["volatile", "range"]}],
                      {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.5}, "owner", cs, self.mcfg, NOW)
        self.assertEqual([x["id"] for x in LS.applicable(list(LS.read_all(self.root).values()), feats, NOW, self.mcfg, limit=50)], [l["lesson_id"]])
        # правил не порождают: регистрация испытаний берёт только уроки меток
        from tb.learnloop import rules as RL
        for lid in made:
            self.assertEqual(RL.derive_reason(LS.read_all(self.root)[lid]), "NOT_LABELS")

    def test_refresh_keeps_ref(self):
        cs = brain_pool()
        made = BR.run_brain_ref(self.root, NOW, self.cfg, mcfg=self.mcfg, all_cases=cs)["made"]
        LS.refresh(self.root, cs + brain_pool(3, 3, start=NOW - 2 * D), self.mcfg, NOW + D)
        for lid in made:
            self.assertTrue(LS.is_ref(LS.read_all(self.root)[lid]))

    def test_ref_regime_criterion(self):
        lesson = {"cond": SPREAD_COND, "claim": {"metric": "mean_r", "dir": "lower", "vs": "base"}}
        cs = brain_pool(regime_bad="volatile")
        self.assertFalse(LS.ref_regime_ok(lesson, cs, self.mcfg))                           # известен один режим: < confirm_min_regimes
        # режим известен меньше чем у половины случаев условия -> знак в обеих половинах и >= 2 недели
        cond = [{"feature": "confirmed", "op": "=", "value": False}]
        les2 = {"cond": cond, "claim": {"metric": "mean_r", "dir": "lower", "vs": "base"}}
        two_weeks = brain_pool(regime_bad="unknown", start=NOW - 17 * D)                       # хиты 2026-10-17..20: суббота -> вторник, 2 недели
        self.assertTrue(LS.ref_regime_ok(les2, two_weeks, self.mcfg))
        one_week = brain_pool(regime_bad="unknown", start=NOW - 20 * D)                        # хиты 2026-10-14..17: одна неделя ISO
        self.assertFalse(LS.ref_regime_ok(les2, one_week, self.mcfg))


# ---------------------------------------------------------------- справочная проверка кандидата модели

class TestCheckCandidate(Root):
    def test_ref_lesson_or_n_too_small(self):
        cs = brain_pool(n_bad=8, n_good=20)
        res = BR.check_candidate(self.root, SPREAD_COND, {"dir": "lower"}, NOW, self.cfg, mcfg=self.mcfg, all_cases=cs)
        self.assertEqual(res["code"], BR.REF_OK)
        l = LS.read_all(self.root)[res["lesson_id"]]
        self.assertEqual((l["evidence"]["src"], l["evidence"]["ref"], l["evidence"]["via"]), ("brain", True, "llm"))
        self.assertLessEqual(l["conf"], self.mcfg["lessons"]["conf_cap_reviewer"])
        # повтор — дубль активного урока
        self.assertEqual(BR.check_candidate(self.root, SPREAD_COND, {"dir": "lower"}, NOW, self.cfg, mcfg=self.mcfg, all_cases=cs)["code"], BR.DUPLICATE)
        # только live-сделки (выборка мозга — paper) и старые (окно 30 суток) не считаются
        for cs2 in (brain_pool(8, 20, mode="live"), brain_pool(8, 20, start=NOW - 60 * D)):
            r2 = BR.check_candidate(self.root, [{"feature": "regime", "op": "=", "value": "range"}], {"dir": "lower"}, NOW, self.cfg, mcfg=self.mcfg,
                                    all_cases=cs2)
            self.assertEqual(r2["code"], BR.N_TOO_SMALL)
        # мало хитов
        r3 = BR.check_candidate(self.root, [{"feature": "regime", "op": "=", "value": "volatile"}], {"dir": "lower"}, NOW, self.cfg, mcfg=self.mcfg,
                                all_cases=brain_pool(n_bad=3, n_good=20, regime_bad="volatile"))
        self.assertIn(r3["code"], (BR.N_TOO_SMALL, BR.DUPLICATE))

    def test_check_item_routes_ref(self):
        dic = {"features": {"side": {"ops": ["="], "values": ["long", "short"]}}, "max_cond": 3,
               "ref": {"regime": {"ops": ["=", "in"], "values": ["trend", "range", "volatile"]}, "confirmed": {"ops": ["="], "values": [True, False]}}}
        cond, pick = CA.check_item({"cond": [{"feature": "regime", "op": "=", "value": "volatile"}, {"feature": "side", "op": "=", "value": "long"}]}, {}, dic)
        self.assertEqual(pick, CA.REF)
        self.assertEqual([c["feature"] for c in cond], ["regime", "side"])
        self.assertEqual(CA.check_item({"cond": [{"feature": "confirmed", "op": "=", "value": False}]}, {}, dic)[1], CA.REF)
        for bad in ({"cond": [{"feature": "regime", "op": "=", "value": "unknown"}]}, {"cond": [{"feature": "setup", "op": "=", "value": "x"}]}):
            with self.assertRaises(CA.ItemRejected) as e:
                CA.check_item(bad, {}, dic)
            self.assertEqual(e.exception.code, "NOT_IN_DICT")
        self.assertEqual(CA.check_item({"cond": [{"feature": "side", "op": "=", "value": "long"}]}, {}, dic)[1], None)   # проверяемый — как прежде

    def test_ref_dictionary(self):
        d = BR.ref_dictionary(self.root, NOW, self.cfg, mcfg=self.mcfg, all_cases=brain_pool())
        self.assertNotIn("utc_session", d)
        self.assertNotIn("coin_class", d)
        self.assertIn("unknown", LS.CAT_FEATURES["regime"])
        self.assertNotIn("unknown", d["regime"]["values"])
        self.assertEqual(d["confirmed"], {"ops": ["="], "values": [True, False]})
        self.assertEqual(sorted(d["stop_pct"]["values"]), ["q20", "q80"])                      # пороги считает код по выборке мозга


# ---------------------------------------------------------------- заморозка

def sig_conn(per_day_prev, per_day_cur, now=NOW, w=7):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE signals(t0 INTEGER, tradable INTEGER)")
    rows = []
    for d in range(3 * w):
        n = per_day_cur if d < w else per_day_prev
        for k in range(n):
            rows.append((now - d * D - 1 - k, 1))
            rows.append((now - d * D - 1 - k, 0))                                             # tradable=0 не считается
    c.executemany("INSERT INTO signals VALUES(?, ?)", rows)
    return c


def trades(per_day_prev, per_day_cur, days=22, now=NOW, mode="paper"):
    out, i = [], 0
    for d in range(days):
        n = per_day_cur if d < 7 else per_day_prev
        for k in range(n):
            i += 1
            out.append(mk(i, sym="F%d" % i, r=0.1, mode=mode, ts=iso(now - d * D - 3600 - k * 60)))
    return out


class TestFreeze(Root):
    def test_short_history_unknown(self):
        r = BR.freeze(sig_conn(10, 10), self.root, self.cfg, NOW, all_cases=trades(4, 0, days=20), mcfg=self.mcfg)
        self.assertEqual((r["known"], r["frozen"], r["why"]), (False, False, "SHORT_HISTORY"))
        self.assertEqual(BR.freeze(sig_conn(10, 10), None, self.cfg, NOW)["known"], False)

    def test_frozen_and_not(self):
        r = BR.freeze(sig_conn(10, 10), self.root, self.cfg, NOW, all_cases=trades(4, 1), mcfg=self.mcfg)
        self.assertEqual((r["known"], r["frozen"], r["trades"], r["trades_avg"]), (True, True, 7, 28.0))
        r = BR.freeze(sig_conn(10, 5), self.root, self.cfg, NOW, all_cases=trades(4, 1), mcfg=self.mcfg)       # сигналов тоже меньше
        self.assertEqual((r["known"], r["frozen"]), (True, False))
        r = BR.freeze(sig_conn(10, 10), self.root, self.cfg, NOW, all_cases=trades(4, 3), mcfg=self.mcfg)       # 21 >= 0.5 x 28
        self.assertEqual((r["known"], r["frozen"]), (True, False))


# ---------------------------------------------------------------- нарушения

class TestViolations(Root):
    A, B = NOW - 20 * D, NOW

    def run_v(self, recs, cs):
        return BR.violations(self.root, "RL0001", self.A, self.B, self.cfg, mcfg=self.mcfg, all_cases=cs, records=recs)

    def test_codes(self):
        cs = brain_pool(n_bad=40, n_good=40, plan_prefix="PL", err_bad=["chase"])
        self.assertEqual(self.run_v([], cs)["code"], BR.V_NOT_EVALUABLE)                     # записей нет
        self.assertEqual(self.run_v([{"plan": "PL-X", "event_seq": None}], cs)["code"], BR.V_NOT_EVALUABLE_LINK)
        few = [{"plan": "PL-%d" % i, "event_seq": None} for i in range(10)]
        r = self.run_v(few, cs)
        self.assertEqual((r["code"], r["n"], r["min"]), (BR.V_NOT_EVALUABLE, 10, 30))
        bad = [{"plan": "PL-%d" % i, "event_seq": None} for i in range(35)]                    # хиты — сделки с нарушениями
        self.assertEqual(self.run_v(bad, cs)["code"], BR.V_FAIL)
        good = [{"plan": "PL-%d" % i, "event_seq": None} for i in range(40, 75)]               # хиты без нарушений
        self.assertEqual(self.run_v(good, cs)["code"], BR.V_OK)

    def test_link_by_event_seq_and_rule_followed(self):
        cs = brain_pool(n_bad=0, n_good=60)
        for i, c in enumerate(cs):
            c["refs"]["event_seq"] = 1000 + i
            if i < 31:
                c["tags"]["rule_followed"] = "no"
        recs = [{"plan": "PL-n%d" % i, "event_seq": 1000 + i} for i in range(31)]
        r = self.run_v(recs, cs)
        self.assertEqual((r["code"], r["n"], r["share"]), (BR.V_FAIL, 31, 1.0))

    def test_shadow_records_from_journal(self):
        base = {"actor": "brain:chat", "kind": "note", "what": "rule_eval", "why": "t"}
        DJ.append(dict(base, basis={"shadow_hits": ["RL0001"], "plan": "PL-1", "event_seq": 5}, t=NOW - D), root=self.root)
        DJ.append(dict(base, basis={"shadow_hits": ["RL0002"], "plan": "PL-2"}, t=NOW - D), root=self.root)
        DJ.append(dict(base, basis={"shadow_hits": [], "plan": "PL-3"}, t=NOW - D), root=self.root)
        recs = BR.shadow_records(self.root, "RL0001", self.A, self.B)
        self.assertEqual([(r["plan"], r["event_seq"]) for r in recs], [("PL-1", 5)])


# ---------------------------------------------------------------- shadow_hits

class TestShadowHits(unittest.TestCase):
    RULE = {"rule_id": "RL0007", "kind": "skip_when", "cond": [{"feature": "spread_bps", "op": ">=", "value": 5.0}], "param": {},
            "scope": {"policy": "P2", "kinds": ["hot_enter"], "side": "with_dir"}, "env_norm": "mainnet"}

    def feats(self, spread):
        return {"ev_kind": "hot_enter", "side_rel": "with_dir", "spread_bps": spread, "side": "long"}

    def test_trial_and_no_apply(self):
        view = {"ver": "R-0001", "rules": [], "apply": True, "trial": [dict(self.RULE, status="trial")]}
        self.assertEqual(RV.shadow_hits(view, {"ok": True}, self.feats(9.0)), ["RL0007"])
        self.assertEqual(RV.shadow_hits(view, {"ok": True}, self.feats(1.0)), [])
        act = dict(self.RULE, rule_id="RL0008", status="active")
        self.assertEqual(RV.shadow_hits({"rules": [act], "apply": False, "trial": []}, {"ok": True}, self.feats(9.0)), ["RL0008"])
        self.assertEqual(RV.shadow_rules({"rules": [act], "apply": True, "trial": []}), [])          # применяемые правила — не shadow
        self.assertEqual(RV.shadow_rules({"rules": [], "apply": False, "trial": []}), [])          # пустой rulebook — пусто
        f = RV.rule_eval_fields(view, {"hits": [], "shadow_hits": ["RL0007"]}, {"role": "chat"})
        self.assertEqual(f["shadow_hits"], ["RL0007"])
        self.assertEqual(RV.rule_eval_fields(None, None, None)["shadow_hits"], [])


# ---------------------------------------------------------------- меню по сессиям

def session_window(days=14, effect=-0.5, sd=0.3, seed=5, a=NOW - 14 * D):
    rng = random.Random(seed)
    from tb.memory.features import utc_session
    rows, n = [], 0
    for h in range(days * 24):
        t = a + h * H
        hour = t // H
        for _ in range(3):
            n += 1
            r = (effect if utc_session(hour % 24) == "us" else 0.0) + rng.gauss(0, sd)
            rows.append({"cluster": "%d:long" % hour, "hour": hour, "side": "long", "key": "K%07d" % n, "t0": t + 60, "filled": 1, "r_net": r,
                         "feats": {}, "flags": None})
    return {"rows": rows, "a": a, "b": a + days * D}


class TestSessionMenu(unittest.TestCase):
    def cfg(self, **sm):
        c = lcfg()
        if sm:
            c["second_iteration"]["session_menu"] = dict({"enabled": True, "power_min": 0.5, "effect_r": 0.3}, **sm)
        return c

    def test_conf_optional_block(self):
        c = lcfg()
        self.assertNotIn("session_menu", c["second_iteration"])                               # поставка: выключено
        self.assertEqual(LC.check(c), [])
        c["second_iteration"]["session_menu"] = {"enabled": True, "power_min": 0.5, "effect_r": 0.3}
        self.assertEqual(LC.check(c), [])
        c["second_iteration"]["session_menu"]["power_min"] = 2
        self.assertIn("E_CONF:value:second_iteration.session_menu.power_min", LC.check(c))

    def test_disabled_by_default(self):
        win = session_window()
        m = MN.build_menu(win, self.cfg())
        self.assertFalse([it for it in m["items"] if it["cond"][0]["feature"] == "utc_session"])
        self.assertFalse([d for d in m["dropped"] if d.get("cond") and d["cond"][0]["feature"] == "utc_session"])
        self.assertEqual(MN.build_menu(win, self.cfg(enabled=False))["k"], m["k"])

    def test_enabled_power_gate_and_welch(self):
        win = session_window()
        cfg = self.cfg(enabled=True)
        m = MN.build_menu(win, cfg)
        sess = dict((it["cond"][0]["value"], it) for it in m["items"] if it.get("test") == "welch")
        self.assertIn("us", sess)
        self.assertGreaterEqual(sess["us"]["power"]["power"], 0.5)
        codes = dict((d["cond"][0]["value"], d["code"]) for d in m["dropped"] if d.get("cond") and d["cond"][0]["feature"] == "utc_session")
        self.assertEqual(codes.get("asia"), "TOO_BROAD")                                      # 9 часов из 24 > 0.3 прогонов
        res = dict((it["cond"][0]["value"], sc) for it, sc in MN.screen_menu(win, m, cfg) if it.get("test") == "welch")
        self.assertTrue(res["us"]["ok"], res["us"])
        self.assertEqual(res["us"]["test"], "welch")
        self.assertLess(res["us"]["diff"], -0.3)
        # недостижимая мощность: короткое окно и большой разброс
        weak = session_window(days=2, effect=-0.1, sd=2.0)
        mw = MN.build_menu(weak, cfg)
        self.assertFalse([it for it in mw["items"] if it.get("test") == "welch"])
        low = [d for d in mw["dropped"] if d.get("code") == MN.SESSION_LOW_POWER]
        self.assertTrue(low)
        self.assertLess(low[0]["power"]["power"], 0.5)

    def test_holdout_dispatch(self):
        win = session_window(days=7)
        cond = [{"feature": "utc_session", "op": "=", "value": "us"}]
        st, code, nums = VF.holdout(win, cond, 3, lcfg())
        self.assertEqual((st, code, nums["test"]), ("hyp", None, "welch"))
        self.assertEqual(VF.test_clusters(win, cond), [])                                    # парный тест неприменим: пар нет
        st2, code2, _ = VF.holdout(session_window(days=7, effect=0.0, seed=9), cond, 3, lcfg())
        self.assertEqual(st2, "dropped")

    def test_power_formula(self):
        self.assertAlmostEqual(MN.welch_power(100, 100, 1.0, 1.0, 0.3, 0.05), MN._phi(0.3 / (2 ** 0.5 / 10) - 1.6448536), places=4)
        self.assertEqual(MN.welch_power(1, 100, 1.0, 1.0, 0.3, 0.05), 0.0)


if __name__ == "__main__":
    unittest.main()
