"""tb.learnloop.trial (R2, задача 14): испытание правила. Свойства design.md: 7 (в испытание попадают только кластеры t0 >= start_ts, start_ts после
holdout), 11 (критерий до испытания: criterion_h, неизменяемость, вердикт только из выборки окна и критерия), 20 (правило и испытание одной
транзакцией, одно испытание на правило), 24 (пересечение по строкам с продвинутым правилом: ниже и выше 0.5), 25 (предельные хиты: прогоны,
отсечённые активным набором критерия, не засчитываются), 26 (место в max_parallel освобождается вердиктом), 29 (окно [start_ts, deadline_ts),
inconclusive не раньше cut_ts(deadline_ts) с поздно закрытым кластером, смена ctx_ver). Граница n_target, вердикт не раньше cut_ts, поздно
закрытый кластер, достижимость (UNREACHABLE_N), ожидание лимита (BUDGET), RESTORED, SAMPLE_CHANGED, смена версии политики.
Временные корни, фейковые часы (now передаётся явно), сети нет."""
import json
import os
import random
import shutil
import sqlite3
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import clusters as CL
from tb.learnloop import cycle as CY
from tb.learnloop import rules as RL
from tb.learnloop import trial as TR
from tb.learnloop import versions as V
from tb.logic import learn_rules as LR
from tb.memory import cfg as memcfg
from tb.memory import lessons as LS

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = 3600
D = 86400
T0 = 1790812800                   # 2026-10-01T00:00:00Z: начало holdout
HOLD_B = T0 + 7 * D
NOW = T0 + 10 * D                 # регистрация (после cut_ts holdout); start_ts = NOW
P2 = {"id": "P2", "ver": 1, "name": "confirm_follow", "kinds": ["range_5m", "turnover_surge", "hot_enter", "oi_spike", "liq_cascade"], "side": "with_dir",
      "entry": "candle_confirm_limit", "enabled": True}
SPREAD = [{"feature": "spread_bps", "op": ">=", "value": 5.0}]
RANGE = [{"feature": "range_5m_pct", "op": ">=", "value": 5.0}]
CLAIM = {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.5}


def cut(b, cfg):
    return CL.cut_ts(b, cfg)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbtrial_")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "config"))
        for f in ("learnloop.json", "memory.json", "shadow_policies.json"):
            shutil.copy(os.path.join(REPO, "config", f), os.path.join(self.root, "config", f))
        with open(os.path.join(self.root, "config", "learnloop.json"), encoding="utf-8") as f:
            self.cfg = json.load(f)
        self.mcfg = memcfg.load(self.root)
        RV.clear_cache()
        store2._STATUS_CACHE.clear()
        self.c = store.open_rw(self.root)
        self.addCleanup(self.c.close)
        store2.migrate_v2a(self.c, self.root, explicit=True, backup=False, now=T0)
        V.init_rulebook(self.c, T0)
        self.n = 0
        self.rng = random.Random(7)

    # ---- данные
    def row(self, t0, spread=1.0, r=0.0, filled=1, ctx="c1", closed=None, side="long", rng=1.0, ver=1, env="mainnet", typ="range_5m"):
        self.n += 1
        hour = t0 // H
        return {"cluster": "%d:%s" % (hour, side), "hour": hour, "side": side, "key": "K%07d" % self.n, "t0": int(t0), "type": typ, "policy_id": "P2",
                "policy_ver": ver, "ctx_ver": ctx, "env_norm": env, "filled": filled, "r_net": r if filled else None,
                "feats": {"spread_bps": spread, "range_5m_pct": rng, "coin_class": None}, "flags": None,
                "closed_ts": int(closed if closed is not None else hour * H + H + 60)}

    def fill(self, t_from, hours, r_in=-0.5, r_out=0.0, sd=0.3, n_in=1, n_out=3, every=1, in_rng=1.0, extra=None, **kw):
        rows = []
        for i in range(0, hours, every):
            h = t_from + i * H
            for _ in range(n_in):
                rows.append(self.row(h + 60, spread=10.0, r=r_in + self.rng.gauss(0, sd), rng=in_rng, **kw))
            for _ in range(n_out):
                rows.append(self.row(h + 120, spread=1.0, r=r_out + self.rng.gauss(0, sd), **kw))
            if extra:
                rows.extend(extra(h))
        self.put(rows)
        return rows

    def put(self, rows):
        store2.insert_rows(self.c, "cluster_runs", rows)

    def lesson(self, cond=None, now=NOW - 12 * H, ctx="c1"):
        w = CL.window(self.c, "P2", 1, ctx, "mainnet", T0, HOLD_B, self.cfg, now)
        ev = {"cand": 1, "sample": {"policy": "P2", "policy_ver": 1, "ctx_ver": ctx, "env_norm": "mainnet",
                                    "hold": {"a": w["a"], "b": w["b"], "cut_ts": w["cut_ts"], "h": w["hash"]}},
              "n_closed": 60, "diff": -0.5, "se": 0.05}
        return LS.create(self.root, cond or SPREAD, dict(CLAIM), "code", [], self.mcfg, now, evidence_src="labels", evidence_extra=ev)

    def register(self, now=NOW, live=None, cfg=None):
        return TR.register_pending(self.c, self.root, cfg or self.cfg, now, policy=P2, mcfg=self.mcfg, live=live or (lambda p, v: True))

    def verdicts(self, now, live=None, cfg=None):
        return TR.run_verdicts(self.c, cfg or self.cfg, now, live=live)

    def q(self, sql, *a):
        return self.c.execute(sql, a).fetchall()

    def only_trial(self):
        ts = TR.trials(self.c)
        self.assertEqual(len(ts), 1)
        return ts[0]

    def active_rule(self, cond, lid="L-9000", now=NOW):
        """Активное правило на условии cond (продвинуто через versions; испытание-заглушка закрыто)."""
        draft = RL.derive({"lesson_id": lid, "status": "hyp", "cond": cond, "claim": dict(CLAIM), "evidence": {"src": "labels"}}, P2, self.c, now)
        res = V.create_with_trial(self.c, draft, {"criterion": {"stub": 1}, "criterion_h": "0" * 16, "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1",
                                                  "start_ts": now, "deadline_ts": now + D, "env_norm": "mainnet"}, now, self.cfg)
        store2.update_row(self.c, "trials", res["trial_id"], {"state": "closed", "verdict": "promote"})
        V.promote(self.c, res["rule_id"], now, self.cfg)
        return V.rule(self.c, res["rule_id"])


# ---------------------------------------------------------------- регистрация и критерий

class TestRegister(Base):
    def setUp(self):
        Base.setUp(self)
        self.fill(T0, 7 * 24)

    def test_registers_with_fixed_criterion(self):
        """Свойства 7, 11, 20: правило trial и испытание; критерий с хешем; start_ts после holdout; критерий не меняется."""
        l = self.lesson()
        rep = self.register()
        self.assertEqual(len(rep["registered"]), 1, rep)
        t = self.only_trial()
        crit = t["criterion"]
        self.assertTrue(TR.criterion_ok(t))
        self.assertEqual(t["criterion_h"], TR.crit_hash(crit))
        self.assertEqual((t["lesson_id"], t["state"], t["verdict"]), (l["lesson_id"], "open", None))
        self.assertEqual((crit["n_target"], crit["alpha_adj"], crit["max_days"], crit["overlap_share_max"]), (150, 0.01, 30, 0.5))
        self.assertEqual(crit["src"], {"policy": "P2", "policy_ver": 1, "ctx_ver": "c1", "env_norm": "mainnet"})
        self.assertEqual((crit["test"], crit["metric"], crit["active"]), ("paired", "mean_r", []))
        self.assertEqual(crit["scope"]["policy"], "P2")
        self.assertEqual(len(crit["cfg_h"]), 16)
        self.assertGreaterEqual(t["start_ts"], HOLD_B)                       # окна не пересекаются
        self.assertEqual(t["start_ts"] % H, 0)
        self.assertEqual(t["deadline_ts"], t["start_ts"] + 30 * D)
        r = V.rule(self.c, t["rule_id"])
        self.assertEqual((r["status"], r["reason_code"], r["from_lesson"]), ("trial", "TRIAL", l["lesson_id"]))
        with self.assertRaises(sqlite3.DatabaseError):
            self.c.execute("UPDATE trials SET criterion='{}'")
        with self.assertRaises(sqlite3.DatabaseError):
            self.c.execute("UPDATE trials SET start_ts=0")
        self.assertEqual(self.q("SELECT code FROM learn_events WHERE step='trial'"), [("TRIAL",)])

    def test_idempotent_and_one_trial_per_rule(self):
        """Свойства 14, 20: повтор регистрации ничего не создаёт; второй create для урока — DUP_RULE."""
        l = self.lesson()
        self.register()
        n = [self.q("SELECT COUNT(*) FROM %s" % t)[0][0] for t in ("rule_rows", "trials", "learn_events")]
        self.register(NOW + H)
        self.assertEqual([self.q("SELECT COUNT(*) FROM %s" % t)[0][0] for t in ("rule_rows", "trials", "learn_events")], n)
        rule = RL.derive(l, P2, self.c, NOW)
        with self.assertRaises(V.RuleRefused) as e:
            TR.register(self.c, rule, l, self.cfg, NOW)
        self.assertEqual(e.exception.code, "DUP_RULE")
        self.assertEqual(len(TR.trials(self.c)), 1)

    def test_atomic_failure_leaves_nothing(self):
        """Свойство 20: сбой записи испытания откатывает и правило."""
        self.lesson()
        orig = TR.criterion

        def bad(*a, **k):
            return dict(orig(*a, **k), junk="A" * 40)                     # privacy_scan: длинная строка -> отказ записи trials
        TR.criterion = bad
        try:
            with self.assertRaises(store2.RowRejected):
                self.register()
        finally:
            TR.criterion = orig
        self.assertEqual(self.q("SELECT COUNT(*) FROM rule_rows")[0][0], 0)
        self.assertEqual(self.q("SELECT COUNT(*) FROM trials")[0][0], 0)
        self.register()
        self.assertEqual(len(TR.trials(self.c)), 1)

    def test_active_set_recorded_at_start(self):
        a = self.active_rule(RANGE)
        self.lesson()
        self.register()
        t = [x for x in TR.trials(self.c, "open")][0]
        self.assertEqual([x["rule_id"] for x in t["criterion"]["active"]], [a["rule_id"]])
        self.assertEqual(t["criterion"]["active"][0]["cond"], LR.canon_cond(RANGE))

    def test_higher_claim_no_rule(self):
        ev = {"sample": {"policy": "P2", "policy_ver": 1, "ctx_ver": "c1", "env_norm": "mainnet", "hold": {"a": T0, "b": HOLD_B, "cut_ts": 1, "h": "x"}},
              "n_closed": 60, "diff": 0.5, "se": 0.05}
        LS.create(self.root, SPREAD, dict(CLAIM, dir="higher"), "code", [], self.mcfg, NOW - 12 * H, evidence_src="labels", evidence_extra=ev)
        rep = self.register()
        self.assertEqual((rep["registered"], rep["skipped"]), ([], 1))
        self.assertEqual(self.q("SELECT COUNT(*) FROM rule_rows")[0][0], 0)

    def test_sample_changed_and_restored(self):
        l = self.lesson()
        self.c.execute("DROP TRIGGER tr_cluster_runs_nu")
        self.c.execute("UPDATE cluster_runs SET r_net=r_net+1 WHERE t0<?", (T0 + D,))
        rep = self.register()
        self.assertEqual(rep["rejected"], [(l["lesson_id"], "SAMPLE_CHANGED")])
        self.assertEqual(LS.read_all(self.root)[l["lesson_id"]]["status"], "rejected")
        self.assertIsNotNone(RL.blocked(self.c, LR.block_h(SPREAD, "lower"), NOW))
        self.assertEqual(self.q("SELECT COUNT(*) FROM trials")[0][0], 0)
        self.assertTrue(self.q("SELECT 1 FROM learn_events WHERE step='trial' AND code='SAMPLE_CHANGED'"))

    def test_restored_without_blocklist(self):
        l = self.lesson(cond=RANGE)
        hc = CL.cut_ts(HOLD_B, self.cfg)
        self.c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('copy_ts', ?)", (str(hc - 10),))
        self.c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('restore_ts', ?)", (str(hc + 10),))
        rep = self.register()
        self.assertEqual(rep["rejected"], [(l["lesson_id"], "RESTORED")])
        self.assertIsNone(RL.blocked(self.c, LR.block_h(RANGE, "lower"), NOW))

    def test_ctx_changed_before_register(self):
        l = self.lesson()
        self.put([self.row(HOLD_B + 3 * H, ctx="c2")])
        rep = self.register()
        self.assertEqual(rep["rejected"], [(l["lesson_id"], "CTX_CHANGED")])
        self.assertIsNone(RL.blocked(self.c, LR.block_h(SPREAD, "lower"), NOW))

    def test_policy_version_off_at_register(self):
        l = self.lesson()
        rep = self.register(live=lambda p, v: False)
        self.assertEqual(rep["rejected"], [(l["lesson_id"], "NO_VERDICT")])


class TestReachAndBudget(Base):
    def test_unreachable(self):
        """Requirement 9.4: кластеров теста за 7 суток мало -> правило rejected UNREACHABLE_N без испытания, чёрный список, урок rejected."""
        self.fill(T0, 7 * 24, every=24)                                       # 1 кластер теста в сутки: 150 дней > 0.8 x 30
        l = self.lesson()
        rep = self.register()
        self.assertEqual(rep["rejected"], [(l["lesson_id"], "UNREACHABLE_N")])
        self.assertEqual(self.q("SELECT COUNT(*) FROM trials")[0][0], 0)
        rows = self.q("SELECT status, reason_code, from_lesson FROM rule_rows")
        self.assertEqual(rows, [("rejected", "UNREACHABLE_N", l["lesson_id"])])
        b = RL.blocked(self.c, LR.block_h(SPREAD, "lower"), NOW)
        self.assertEqual((b["code"], b["until_ts"]), ("UNREACHABLE_N", NOW + 14 * D))
        self.assertEqual(LS.read_all(self.root)[l["lesson_id"]]["status"], "rejected")
        self.register(NOW + H)
        self.assertEqual(len(self.q("SELECT 1 FROM rule_rows")), 1)

    def test_reach_boundary(self):
        """8 кластеров теста в сутки: 150 / 8 = 18.75 <= 24 суток — достижимо; 6 в сутки: 25 > 24 — нет."""
        self.fill(T0, 7 * 24, every=3)
        self.lesson()
        self.assertEqual(len(self.register()["registered"]), 1)

    def test_reach_boundary_fail(self):
        self.fill(T0, 7 * 24, every=4)
        self.lesson()
        rep = self.register()
        self.assertEqual([c for _l, c in rep["rejected"]], ["UNREACHABLE_N"])

    def test_wait_for_slot_then_budget(self):
        """Requirement 9.5: лимит max_parallel занят -> урок ждёт; дольше wait_max_days -> BUDGET (без чёрного списка)."""
        self.fill(T0, 7 * 24)
        cfg = json.loads(json.dumps(self.cfg))
        cfg["trial"]["max_parallel"] = 1
        l1 = self.lesson()
        l2 = self.lesson(cond=RANGE)
        rep = self.register(cfg=cfg)
        self.assertEqual((len(rep["registered"]), rep["waiting"]), (1, [l2["lesson_id"]]))
        self.assertEqual(TR.trials(self.c)[0]["lesson_id"], l1["lesson_id"])
        rep = self.register(NOW - 12 * H + 7 * D, cfg=cfg)                   # ещё ждёт: ровно 7 суток с создания
        self.assertEqual(rep["waiting"], [l2["lesson_id"]])
        rep = self.register(NOW - 12 * H + 7 * D + 1, cfg=cfg)               # 7 суток + 1 с с создания урока
        self.assertEqual(rep["rejected"], [(l2["lesson_id"], "BUDGET")])
        self.assertEqual(LS.read_all(self.root)[l2["lesson_id"]]["history"][-1]["reason_code"], "BUDGET")
        self.assertIsNone(RL.blocked(self.c, LR.block_h(RANGE, "lower"), NOW + 7 * D))

    def test_slot_freed_by_verdict(self):
        """Свойство 26: испытание после вердикта не занимает место; ждущий урок регистрируется."""
        self.fill(T0, 7 * 24)
        cfg = json.loads(json.dumps(self.cfg))
        cfg["trial"]["max_parallel"] = 1
        cfg["trial"]["wait_max_days"] = 30                                   # вердикт через ~8.3 суток: ожидание не истекает
        self.lesson()
        l2 = self.lesson(cond=[{"feature": "spread_bps", "op": ">=", "value": 8.0}])
        self.register(cfg=cfg)
        t = TR.trials(self.c)[0]
        self.fill(t["start_ts"], 150)
        c150 = cut(t["start_ts"] + 150 * H, cfg)
        self.assertEqual(TR.open_count(self.c), 1)
        self.verdicts(c150, cfg=cfg)
        self.assertEqual(TR.open_count(self.c), 0)
        self.assertEqual(TR.get(self.c, t["trial_id"])["verdict"], "promote")
        rep = self.register(c150, cfg=cfg)
        self.assertEqual(len(rep["registered"]), 1)
        self.assertEqual(TR.trials(self.c, "open")[0]["lesson_id"], l2["lesson_id"])


# ---------------------------------------------------------------- вердикт

class TestVerdict(Base):
    def start(self, cond=None):
        self.fill(T0, 7 * 24)
        self.lesson(cond)
        self.assertEqual(len(self.register()["registered"]), 1)
        return self.only_trial()

    def test_promote_at_cut_not_before(self):
        """Граница n_target и вердикт не раньше cut_ts: 150-й кластер теста в часе start+149 -> b = start+150 ч."""
        t = self.start()
        self.fill(t["start_ts"], 160)
        b150 = t["start_ts"] + 150 * H
        self.assertEqual(self.verdicts(cut(b150, self.cfg) - 1), [])
        self.assertEqual(TR.get(self.c, t["trial_id"])["state"], "open")
        res = self.verdicts(cut(b150, self.cfg))
        self.assertEqual([(r["verdict"], r["code"]) for r in res], [("promote", "READY")])
        t2 = TR.get(self.c, t["trial_id"])
        self.assertEqual((t2["state"], t2["verdict"], t2["cut_ts"]), ("closed", "promote", cut(b150, self.cfg)))
        n = t2["numbers"]
        self.assertEqual((n["n"], n["n_target"], n["b"], n["last"]), (150, 150, b150, "%d:long" % (b150 // H - 1)))
        self.assertTrue(n["a_ok"] and n["b_ok"])
        self.assertLess(n["p"], 0.01)
        self.assertEqual(n["loss_share"], 0.25)
        r = V.rule(self.c, t["rule_id"])
        self.assertEqual((r["status"], r["wait"]), ("trial", "READY"))           # продвижение — задача 15
        self.assertEqual([x["trial"]["trial_id"] for x in TR.promoted(self.c)], [t["trial_id"]])
        self.assertEqual(self.verdicts(cut(b150, self.cfg) + 5 * H), [])         # закрытое испытание не трогается

    def test_149_clusters_no_verdict(self):
        t = self.start()
        self.fill(t["start_ts"], 149)
        self.assertEqual(self.verdicts(cut(t["start_ts"] + 200 * H, self.cfg)), [])
        self.assertEqual(TR.get(self.c, t["trial_id"])["state"], "open")

    def test_reproducible_and_pure(self):
        """Свойство 11: вердикт из выборки окна и критерия; порядок строк и строки после cut_ts не влияют; переразметка невозможна (insert-only)."""
        t = self.start()
        self.fill(t["start_ts"], 160)
        now = cut(t["start_ts"] + 150 * H, self.cfg)
        st, win = TR.find_window(self.c, t, now)
        self.assertEqual(st, "ready")
        v1 = TR.verdict(t, win)
        rows = list(win["rows"])
        random.Random(3).shuffle(rows)
        v2 = TR.verdict(t, dict(win, rows=rows))
        self.assertEqual(v1, v2)
        self.put([self.row(t["start_ts"] + 10 * H + 5, spread=10.0, r=9.0, closed=now + D)])     # закрыт после cut_ts: не входит
        st, win3 = TR.find_window(self.c, t, now + 2 * D)
        self.assertEqual((win3["b"], win3["hash"]), (win["b"], win["hash"]))
        self.assertEqual(TR.verdict(t, win3), v1)
        with self.assertRaises(sqlite3.DatabaseError):
            self.c.execute("UPDATE cluster_runs SET r_net=1")

    def test_late_closed_cluster(self):
        """Кластер 150 закрыт позже cut_ts(b150): вердикт сдвигается на b151, первые 150 кластеров — те же."""
        t = self.start()
        s = t["start_ts"]
        self.fill(s, 149)
        late = s + 149 * H
        self.put([self.row(late + 60, spread=10.0, r=-0.5, closed=late + H + 48 * H + 1800),
                  self.row(late + 120, spread=1.0, r=0.0, closed=late + H + 48 * H + 1800)])
        self.fill(s + 150 * H, 5)
        b150 = s + 150 * H
        self.assertEqual(self.verdicts(cut(b150, self.cfg)), [])
        res = self.verdicts(cut(b150 + H, self.cfg))
        self.assertEqual(len(res), 1)
        n = TR.get(self.c, t["trial_id"])["numbers"]
        self.assertEqual((n["b"], n["n"], n["last"]), (b150 + H, 150, "%d:long" % (late // H)))

    def test_cluster_closed_47h_counted(self):
        """Поздно закрытый (47 ч) кластер в начале окна учитывается в первой же выборке с n_target."""
        t = self.start()
        s = t["start_ts"]
        self.put([self.row(s + 60, spread=10.0, r=-0.5, closed=s + H + 47 * H), self.row(s + 120, spread=1.0, r=0.0, closed=s + H + 47 * H)])
        self.fill(s + H, 149)
        b150 = s + 150 * H
        self.verdicts(cut(b150, self.cfg))
        n = TR.get(self.c, t["trial_id"])["numbers"]
        self.assertEqual((n["b"], n["n"], n["clusters"][0]), (b150, 150, "%d:long" % (s // H)))

    def test_reject_no_effect(self):
        t = self.start()
        self.fill(t["start_ts"], 150, r_in=0.0)
        res = self.verdicts(cut(t["start_ts"] + 150 * H, self.cfg))
        self.assertEqual([(r["verdict"], r["code"]) for r in res], [("reject", "TRIAL_REJECT")])
        r = V.rule(self.c, t["rule_id"])
        self.assertEqual((r["status"], r["reason_code"]), ("rejected", "TRIAL_REJECT"))
        self.assertEqual(RL.blocked(self.c, LR.block_h(SPREAD, "lower"), NOW)["code"], "TRIAL_REJECT")

    def test_reject_by_absolute_threshold(self):
        """(а): значимая разность, но среднее предельных хитов выше -min_effect_r (база положительна) -> reject."""
        t = self.start()
        self.fill(t["start_ts"], 150, r_in=0.0, r_out=0.5)
        n = TR.verdict(t, TR.find_window(self.c, t, cut(t["start_ts"] + 150 * H, self.cfg))[1])
        self.assertEqual(n["verdict"], "reject")
        self.assertLess(n["numbers"]["p"], 0.01)
        self.assertFalse(n["numbers"]["a_ok"])

    def test_reject_by_loss_share(self):
        """(б): доля предельных хитов среди пропущенных активным набором 0.5 > 0.3 -> reject."""
        t = self.start()
        self.fill(t["start_ts"], 150, n_in=2, n_out=2)
        v = TR.verdict(t, TR.find_window(self.c, t, cut(t["start_ts"] + 150 * H, self.cfg))[1])
        self.assertEqual((v["verdict"], v["numbers"]["loss_share"], v["numbers"]["a_ok"], v["numbers"]["b_ok"]), ("reject", 0.5, True, False))

    def test_nofill_and_before_start(self):
        """Свойство 7: строки до start_ts не входят; незаполненные — только счётчик nofill_n."""
        t = self.start()
        s = t["start_ts"]
        self.put([self.row(s - H + 60, spread=10.0, r=-9.0, closed=s + 60)])
        self.fill(s, 150, extra=lambda h: [self.row(h + 300, spread=10.0, filled=0)])
        v = TR.verdict(t, TR.find_window(self.c, t, cut(s + 150 * H, self.cfg))[1])
        self.assertEqual((v["numbers"]["nofill_n"], v["numbers"]["n_rows"], v["numbers"]["n_hits"]), (150, 600, 150))
        self.assertEqual(v["verdict"], "promote")

    def test_policy_version_not_mixed(self):
        t = self.start()
        self.fill(t["start_ts"], 150)
        now = cut(t["start_ts"] + 150 * H, self.cfg)
        v1 = TR.verdict(t, TR.find_window(self.c, t, now)[1])
        self.fill(t["start_ts"], 150, r_in=5.0, ver=2)
        self.fill(t["start_ts"], 150, r_in=5.0, env="testnet")
        v2 = TR.verdict(t, TR.find_window(self.c, t, now)[1])
        self.assertEqual(v1, v2)

    def test_policy_version_disabled(self):
        t = self.start()
        res = self.verdicts(NOW + D, live=lambda p, v: False)
        self.assertEqual([(r["code"], r["reason"]) for r in res], [("NO_VERDICT", "POLICY_OFF")])
        self.assertEqual(V.rule(self.c, t["rule_id"])["reason_code"], "NO_VERDICT")

    def test_ctx_changed_closes_immediately(self):
        """Свойство 29: смена ctx_ver во время испытания -> CTX_CHANGED сразу, без чёрного списка; снимки другой версии не смешиваются."""
        t = self.start()
        self.fill(t["start_ts"], 10)
        self.put([self.row(t["start_ts"] + 11 * H, ctx="c2")])
        res = self.verdicts(t["start_ts"] + 12 * H)
        self.assertEqual([(r["verdict"], r["code"]) for r in res], [("inconclusive", "CTX_CHANGED")])
        r = V.rule(self.c, t["rule_id"])
        self.assertEqual((r["status"], r["reason_code"]), ("rejected", "CTX_CHANGED"))
        self.assertIsNone(RL.blocked(self.c, LR.block_h(SPREAD, "lower"), NOW + D))

    def test_deadline_inconclusive_with_late_cluster(self):
        """Свойство 29: без n_target в [start_ts, deadline_ts) — inconclusive не раньше cut_ts(deadline_ts); поздно закрытый кластер у deadline_ts
        учитывается."""
        t = self.start()
        s, dl = t["start_ts"], t["deadline_ts"]
        self.fill(s, 30 * 24 - 1, every=6)                                    # 120 кластеров теста за 30 суток
        last = dl - H
        self.put([self.row(last + 60, spread=10.0, r=-0.5, closed=last + H + 47 * H), self.row(last + 120, spread=1.0, closed=last + H + 47 * H)])
        self.put([self.row(dl + 60, spread=10.0, r=-0.5), self.row(dl + 120, spread=1.0)])      # после deadline_ts: не входит
        self.assertEqual(self.verdicts(cut(dl, self.cfg) - 1), [])
        self.assertEqual(TR.get(self.c, t["trial_id"])["state"], "open")
        res = self.verdicts(cut(dl, self.cfg))
        self.assertEqual([(r["verdict"], r["code"], r["reason"]) for r in res], [("inconclusive", "NO_VERDICT", "DEADLINE")])
        t2 = TR.get(self.c, t["trial_id"])
        self.assertEqual((t2["cut_ts"], t2["numbers"]["n_tc"], t2["numbers"]["b"]), (cut(dl, self.cfg), 121, dl))
        self.assertEqual(RL.blocked(self.c, LR.block_h(SPREAD, "lower"), NOW + 31 * D)["code"], "NO_VERDICT")

    def test_restored_window(self):
        t = self.start()
        self.fill(t["start_ts"], 150)
        c150 = cut(t["start_ts"] + 150 * H, self.cfg)
        self.c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('copy_ts', ?)", (str(c150 - 100),))
        self.c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('restore_ts', ?)", (str(c150 + 100),))
        res = self.verdicts(c150 + 200)
        self.assertEqual([(r["verdict"], r["code"]) for r in res], [("inconclusive", "RESTORED")])
        self.assertIsNone(RL.blocked(self.c, LR.block_h(SPREAD, "lower"), NOW + D))


class TestViolationsCondition(Base):
    """Вторая итерация (задача 31; Requirement 18.6): условие по нарушениям при вердикте promote по записям rule_eval с правилом в shadow_hits.
    fail -> reject TRIAL_REJECT (причина VIOLATIONS); not_evaluable и not_evaluable_link продвижение не блокируют."""

    def start(self):
        self.fill(T0, 7 * 24)
        self.lesson()
        self.assertEqual(len(self.register()["registered"]), 1)
        t = self.only_trial()
        self.fill(t["start_ts"], 160)
        return t

    def journal(self, rid, t0, plans):
        from tb.core import decisions as DJ
        for i, p in enumerate(plans):
            DJ.append({"actor": "brain:chat", "kind": "note", "what": "rule_eval", "why": "t", "t": t0 + i * 60,
                       "basis": {"shadow_hits": [rid], "plan": p, "event_seq": None}}, root=self.root)

    def trades(self, t0, n, viol, prefix="PL", i0=0):
        from _memory_helpers import mk
        from tb.memory import cases
        for i in range(n):
            c = mk(i0 + i, sym="V%d" % (i0 + i), r=-0.2, mode="paper", ts=cases.iso(t0 + (i0 + i) * 600), err=["chase"] if viol else [])
            c["refs"]["plan"] = "%s-%d" % (prefix, i0 + i)
            cases.append_new(c, root=self.root)

    def run_at(self, t):
        return TR.run_verdicts(self.c, self.cfg, cut(t["start_ts"] + 150 * H, self.cfg), root=self.root, mcfg=self.mcfg)

    def test_fail_rejects(self):
        t = self.start()
        s = t["start_ts"]
        self.trades(s + H, 35, viol=True)                                  # сделки под правилом испытания — все с нарушениями
        self.trades(s + H, 35, viol=False, prefix="OK", i0=100)            # остальные сделки мозга без нарушений
        self.journal(t["rule_id"], s + H, ["PL-%d" % i for i in range(35)])
        res = self.run_at(t)
        self.assertEqual([(r["verdict"], r["code"], r["reason"]) for r in res], [("reject", "TRIAL_REJECT", "VIOLATIONS")])
        n = TR.get(self.c, t["trial_id"])["numbers"]
        self.assertEqual((n["violations"]["code"], n["violations"]["n"], n["a_ok"], n["b_ok"]), ("fail", 35, True, True))
        self.assertEqual(V.rule(self.c, t["rule_id"])["status"], "rejected")

    def test_ok_promotes(self):
        t = self.start()
        s = t["start_ts"]
        self.trades(s + H, 35, viol=False)
        self.trades(s + H, 35, viol=True, prefix="BAD", i0=100)
        self.journal(t["rule_id"], s + H, ["PL-%d" % i for i in range(35)])
        res = self.run_at(t)
        self.assertEqual([(r["verdict"], r["code"]) for r in res], [("promote", "READY")])
        self.assertEqual(TR.get(self.c, t["trial_id"])["numbers"]["violations"]["code"], "ok")

    def test_not_evaluable_does_not_block(self):
        for case in ("none", "few", "link"):
            with self.subTest(case=case):
                Base.setUp(self)
                t = self.start()
                s = t["start_ts"]
                if case == "few":
                    self.trades(s + H, 10, viol=True)
                    self.journal(t["rule_id"], s + H, ["PL-%d" % i for i in range(10)])
                elif case == "link":
                    self.journal(t["rule_id"], s + H, ["PL-X%d" % i for i in range(40)])     # планы без случаев: связи нет
                res = self.run_at(t)
                self.assertEqual([(r["verdict"], r["code"]) for r in res], [("promote", "READY")])
                want = {"none": "not_evaluable", "few": "not_evaluable", "link": "not_evaluable_link"}[case]
                self.assertEqual(TR.get(self.c, t["trial_id"])["numbers"]["violations"]["code"], want)

    def test_without_root_unchanged(self):
        t = self.start()
        res = TR.run_verdicts(self.c, self.cfg, cut(t["start_ts"] + 150 * H, self.cfg))
        self.assertEqual([(r["verdict"], r["code"]) for r in res], [("promote", "READY")])
        self.assertNotIn("violations", TR.get(self.c, t["trial_id"])["numbers"])


class TestMarginal(Base):
    def test_active_set_rows_not_counted(self):
        """Свойство 25: прогоны, отсечённые активным набором критерия (range >= 5), не входят ни в предельные хиты, ни в базу, ни в долю потери."""
        a = self.active_rule(RANGE, now=T0)
        self.fill(T0, 7 * 24)
        self.lesson()
        self.register()
        t = TR.trials(self.c, "open")[0]
        s = t["start_ts"]
        # в каждом часе: хит с range 9 (отсекается активным правилом) и r=+2, хит с range 1 и r=-0.5, база r=0
        self.fill(s, 150, n_in=1, n_out=3, extra=lambda h: [self.row(h + 400, spread=10.0, r=2.0, rng=9.0), self.row(h + 500, spread=1.0, r=5.0, rng=9.0)])
        v = TR.verdict(t, TR.find_window(self.c, t, cut(s + 150 * H, self.cfg))[1])
        n = v["numbers"]
        self.assertEqual((n["cut_active"], n["n_rows"], n["n_hits"], n["loss_share"]), (300, 600, 150, 0.25))
        self.assertLess(n["in_mean"], -0.3)
        self.assertEqual(v["verdict"], "promote")
        bare = dict(t, criterion=dict(t["criterion"], active=[]))             # без активного набора хит с r=+2 вошёл бы в среднее
        vb = TR.verdict(bare, TR.find_window(self.c, bare, cut(s + 150 * H, self.cfg))[1])
        self.assertGreater(vb["numbers"]["in_mean"], 0.3)
        self.assertEqual(a["status"], "active")


class TestOverlap(Base):
    def setup_trial(self, share_cut):
        """Хиты испытания (spread) несут range 9 с долей share_cut; продвигается правило range >= 5."""
        rng = random.Random(11)
        rows = []
        for i in range(7 * 24 + 60):
            h = T0 + i * H
            rows.append(self.row(h + 60, spread=10.0, r=-0.5, rng=9.0 if rng.random() < share_cut else 1.0))
            rows.extend(self.row(h + 120 + k, spread=1.0, r=0.0) for k in range(3))
        self.put(rows)
        self.lesson()
        self.register()
        return TR.trials(self.c, "open")[0]

    def test_low_overlap_continues(self):
        """Свойство 24: продвинуто правило на другом признаке, отсекающее <= 0.5 хитов испытания, — испытание продолжается, набор критерия прежний."""
        t = self.setup_trial(0.2)
        a = self.active_rule(RANGE, now=NOW + 2 * D)
        res = TR.check_overlaps(self.c, self.cfg, NOW + 3 * D)
        self.assertEqual(res, [])
        t2 = TR.get(self.c, t["trial_id"])
        self.assertEqual(t2["state"], "open")
        ov = t2["numbers"]["overlap"][a["rule_id"]]
        self.assertLess(ov["share"], 0.5)
        self.assertGreater(ov["n"], 100)
        self.assertEqual(t2["criterion"]["active"], [])
        self.assertEqual(TR.check_overlaps(self.c, self.cfg, NOW + 4 * D), [])          # проверяется один раз
        self.assertEqual(TR.get(self.c, t["trial_id"])["numbers"]["overlap"], t2["numbers"]["overlap"])

    def test_high_overlap_closes_without_blocklist(self):
        t = self.setup_trial(0.8)
        a = self.active_rule(RANGE, now=NOW + 2 * D)
        res = self.verdicts(NOW + 3 * D)
        self.assertEqual([(r["verdict"], r["code"], r["reason"]) for r in res], [("inconclusive", "NO_VERDICT", "OVERLAP")])
        t2 = TR.get(self.c, t["trial_id"])
        self.assertGreater(t2["numbers"]["overlap"][a["rule_id"]]["share"], 0.5)
        self.assertEqual(t2["numbers"]["overlap_rule"], a["rule_id"])
        r = V.rule(self.c, t["rule_id"])
        self.assertEqual((r["status"], r["reason_code"]), ("rejected", "NO_VERDICT"))
        self.assertIsNone(RL.blocked(self.c, LR.block_h(SPREAD, "lower"), NOW + 3 * D))
        self.assertEqual(TR.open_count(self.c), 0)


class TestWiring(Base):
    def test_cycle_steps(self):
        steps = dict((s.name, s) for s in CY.default_steps())
        self.assertIs(steps["register"].fn, TR.step_register)
        self.assertIs(steps["verdicts"].fn, TR.step_verdicts)
        self.assertEqual((steps["register"].period, steps["verdicts"].period, steps["verdicts"].group, steps["verdicts"].interval),
                         ("daily", "hourly", "verdict", "verdict_s"))
        self.assertFalse(steps["register"].stub or steps["verdicts"].stub)

    def test_steps_through_ctx(self):
        self.fill(T0, 7 * 24)
        self.lesson()
        ctx = CY.Ctx(self.root, self.c, self.cfg, NOW, policy=P2, mcfg=self.mcfg)
        self.assertEqual(TR.step_register(ctx)["registered"], 1)
        self.assertEqual(TR.step_verdicts(ctx), {"code": "OK", "closed": 0, "registered": 0, "waiting": 0, "rejected": 0})

    def test_layering(self):
        with open(TR.__file__, encoding="utf-8") as f:
            src = f.read()
        for bad in ("tb.brain", "tb.gateway", "tb.llm", "tb.venues", "tb.executor", "tb.control"):
            self.assertNotIn(bad, src)


if __name__ == "__main__":
    unittest.main()
