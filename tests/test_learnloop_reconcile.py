"""tb.learnloop.reconcile (R2, задача 15): сверка статусов уроков меток с базой. Свойства design.md: 18 (статус урока меток меняет только
lessons.transition из сверки; любое терминальное состояние правила выводит урок из hyp; падение между записью версии и переходом урока исправляет
следующая сверка; после восстановления базы из копии урок не идёт назад, база приводится к нему — RECONCILE_MISMATCH), 19 (истечение урока не
меняет активное правило). Урок hyp без испытания и правила после восстановления регистрируется заново. Идемпотентность (свойство 14).
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
from tb.learnloop import promote as PR
from tb.learnloop import reconcile as RC
from tb.learnloop import rules as RL
from tb.learnloop import trial as TR
from tb.learnloop import versions as V
from tb.memory import cfg as memcfg
from tb.memory import lessons as LS

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = 3600
D = 86400
T0 = 1790812800                   # 2026-10-01T00:00:00Z
HOLD_B = T0 + 7 * D
NOW = T0 + 10 * D
P2 = {"id": "P2", "ver": 1, "name": "confirm_follow", "kinds": ["range_5m", "turnover_surge", "hot_enter", "oi_spike", "liq_cascade"], "side": "with_dir",
      "entry": "candle_confirm_limit", "enabled": True}
SPREAD = [{"feature": "spread_bps", "op": ">=", "value": 5.0}]
CLAIM = {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.5}
READY = {"ok": True, "failed": []}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbrec_")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "config"))
        for f in ("learnloop.json", "memory.json", "shadow_policies.json"):
            shutil.copy(os.path.join(REPO, "config", f), os.path.join(self.root, "config", f))
        with open(os.path.join(self.root, "config", "learnloop.json"), encoding="utf-8") as f:
            self.cfg = json.load(f)
        self.cfg["promotion"]["mode"] = "auto"
        self.mcfg = memcfg.load(self.root)
        RV.clear_cache()
        store2._STATUS_CACHE.clear()
        self.c = store.open_rw(self.root)
        self.addCleanup(lambda: self.c.close())
        store2.migrate_v2a(self.c, self.root, explicit=True, backup=False, now=T0)
        V.init_rulebook(self.c, T0)
        self.n = 0
        self.rng = random.Random(5)

    def row(self, t0, spread=1.0, r=0.0):
        self.n += 1
        hour = t0 // H
        return {"cluster": "%d:long" % hour, "hour": hour, "side": "long", "key": "K%07d" % self.n, "t0": int(t0), "type": "range_5m", "policy_id": "P2",
                "policy_ver": 1, "ctx_ver": "c1", "env_norm": "mainnet", "filled": 1, "r_net": r, "feats": {"spread_bps": spread, "range_5m_pct": 1.0},
                "flags": None, "closed_ts": int(hour * H + H + 60)}

    def fill(self, t_from, hours):
        rows = []
        for i in range(hours):
            h = t_from + i * H
            rows.append(self.row(h + 60, spread=10.0, r=-0.5 + self.rng.gauss(0, 0.3)))
            rows += [self.row(h + 120, r=self.rng.gauss(0, 0.3)) for _ in range(3)]
        store2.insert_rows(self.c, "cluster_runs", rows)

    def lesson(self, cond=None, now=NOW - 12 * H, sample=True):
        ev = {"cand": 1, "n_closed": 60, "diff": -0.5, "se": 0.05}
        if sample:
            w = CL.window(self.c, "P2", 1, "c1", "mainnet", T0, HOLD_B, self.cfg, now)
            ev["sample"] = {"policy": "P2", "policy_ver": 1, "ctx_ver": "c1", "env_norm": "mainnet",
                            "hold": {"a": w["a"], "b": w["b"], "cut_ts": w["cut_ts"], "h": w["hash"]}}
        return LS.create(self.root, cond or SPREAD, dict(CLAIM), "code", [], self.mcfg, now, evidence_src="labels", evidence_extra=ev)

    def rule_for(self, l, now=NOW, state="closed", verdict="promote"):
        """Правило trial и испытание урока (испытание закрыто вердиктом promote и правило ждёт READY, или открыто)."""
        draft = RL.derive(l, P2, self.c, now)
        res = V.create_with_trial(self.c, draft, {"criterion": {"active": []}, "criterion_h": "0" * 16, "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1",
                                                  "start_ts": now - 5 * D, "deadline_ts": now + 25 * D, "env_norm": "mainnet"}, now, self.cfg)
        if state == "closed":
            store2.update_row(self.c, "trials", res["trial_id"], {"state": "closed", "verdict": verdict, "cut_ts": now, "closed_ts": now,
                                                                  "numbers": {"in_mean": -0.5, "n": 150, "diff": -0.4, "se": 0.05}})
            if verdict == "promote":
                V.set_wait(self.c, res["rule_id"], "READY", now)
        return res["rule_id"]

    def rec(self, now=NOW):
        return RC.reconcile(self.c, self.root, self.cfg, now, mcfg=self.mcfg)

    def st(self, lid):
        l = LS.read_all(self.root)[lid]
        return l["status"], l["history"][-1]["reason_code"]

    def counts(self):
        with open(LS.lessons_path(self.root), encoding="utf-8") as f:
            n = len(f.read().splitlines())
        return [self.c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in ("rule_rows", "trials", "learn_events", "blocklist_rows")] + [n]

    def backup_db(self, now):
        return store2.make_backup(self.root, "daily", now=now)

    def restore(self, path):
        """Подмена содержимого базы копией (как после восстановления: соединение цикла видит базу копии)."""
        src = sqlite3.connect(path)
        try:
            src.backup(self.c)
        finally:
            src.close()
        RV.clear_cache()


# ---------------------------------------------------------------- переходы вперёд

class TestForward(Base):
    def test_terminal_rule_states_move_lesson_out_of_hyp(self):
        """Свойство 18: active / disabled -> confirmed; rejected с любым кодом -> rejected с тем же кодом; правило ждёт -> hyp."""
        ls = [self.lesson(sample=False) for _ in range(5)]
        r_act, r_dis = self.rule_for(ls[0]), self.rule_for(ls[1])
        r_rej, r_loss = self.rule_for(ls[2], state="open"), self.rule_for(ls[3])
        self.rule_for(ls[4])                                           # ждёт READY
        V.promote(self.c, r_act, NOW, self.cfg)
        V.promote(self.c, r_dis, NOW, self.cfg)
        V.disable(self.c, [r_dis], "ROLLBACK", NOW + 1, self.cfg)
        V.reject_rule(self.c, r_rej, "TRIAL_REJECT", NOW, self.cfg)
        V.reject_rule(self.c, r_loss, "LOSS_CAP", NOW, self.cfg)
        acts = self.rec(NOW + H)
        self.assertEqual(sorted((a["lesson_id"], a["to"], a["code"]) for a in acts if a["action"] == "forward"),
                         sorted([(ls[0]["lesson_id"], "confirmed", "TRIAL_PROMOTE"), (ls[1]["lesson_id"], "confirmed", "TRIAL_PROMOTE"),
                                 (ls[2]["lesson_id"], "rejected", "TRIAL_REJECT"), (ls[3]["lesson_id"], "rejected", "LOSS_CAP")]))
        self.assertEqual(self.st(ls[4]["lesson_id"]), ("hyp", "NEW"))
        l0 = LS.read_all(self.root)[ls[0]["lesson_id"]]
        self.assertEqual(l0["expires_ts"], NOW + H + self.mcfg["lessons"]["ttl_confirmed_days"] * D)
        n = self.counts()
        self.assertEqual([a for a in self.rec(NOW + 2 * H) if a["action"] != "unregistered"], [])    # идемпотентно
        self.assertEqual(self.counts(), n)

    def test_crash_between_version_and_transition(self):
        """Падение между записью версии и переходом урока: урок hyp при активном правиле — следующая сверка переводит в confirmed."""
        l = self.lesson(sample=False)
        rid = self.rule_for(l)
        PR.run(self.c, self.root, self.cfg, NOW, ready=READY)          # данных нет: доля потери 0 -> продвижение
        self.assertEqual(V.rule(self.c, rid)["status"], "active")
        self.assertEqual(self.st(l["lesson_id"]), ("hyp", "NEW"))      # шаг продвижения урок не трогает
        orig = LS.transition
        LS.transition = lambda *a, **k: (_ for _ in ()).throw(OSError("диск"))
        try:
            with self.assertRaises(OSError):
                self.rec(NOW + H)
        finally:
            LS.transition = orig
        self.assertEqual(self.st(l["lesson_id"]), ("hyp", "NEW"))
        self.rec(NOW + 2 * H)
        self.assertEqual(self.st(l["lesson_id"]), ("confirmed", "TRIAL_PROMOTE"))

    def test_expiry_keeps_active_rule(self):
        """Свойство 19: истечение confirmed -> expired, активное правило не меняется."""
        l = self.lesson(sample=False)
        rid = self.rule_for(l)
        V.promote(self.c, rid, NOW, self.cfg)
        self.rec(NOW)
        exp = LS.read_all(self.root)[l["lesson_id"]]["expires_ts"]
        self.assertEqual(self.rec(exp - 1), [])
        acts = self.rec(exp)
        self.assertEqual([(a["action"], a["to"]) for a in acts], [("expire", "expired")])
        self.assertEqual(self.st(l["lesson_id"]), ("expired", "EXPIRED"))
        self.assertEqual(V.rule(self.c, rid)["status"], "active")
        self.assertEqual(RV.last_version(self.c)["rule_ids"], [rid])
        self.assertEqual(self.rec(exp + D), [])

    def test_owner_rejected_lesson_keeps_active_rule(self):
        """Урок отклонён владельцем при активном правиле: сверка не возвращает урок и не трогает правило."""
        l = self.lesson(sample=False)
        rid = self.rule_for(l)
        V.promote(self.c, rid, NOW, self.cfg)
        self.rec(NOW)
        LS.transition(self.root, l["lesson_id"], "rejected", "OWNER", NOW + H, cfg=self.mcfg)
        n = self.counts()
        self.assertEqual(self.rec(NOW + 2 * H), [])
        self.assertEqual(self.counts(), n)
        self.assertEqual(V.rule(self.c, rid)["status"], "active")


# ---------------------------------------------------------------- восстановление базы из копии

class TestRestore(Base):
    def test_confirmed_lesson_waiting_rule_after_restore(self):
        """Копия сделана, пока правило ждало READY; после продвижения и сверки (урок confirmed) база восстановлена — урок не идёт назад,
        ожидающее правило отклоняется NO_VERDICT, RECONCILE_MISMATCH; повтор ничего не пишет."""
        l = self.lesson(sample=False)
        rid = self.rule_for(l)
        bk = self.backup_db(NOW)
        PR.run(self.c, self.root, self.cfg, NOW + H, ready=READY)
        self.rec(NOW + H)
        self.assertEqual(self.st(l["lesson_id"]), ("confirmed", "TRIAL_PROMOTE"))
        self.restore(bk)
        self.assertEqual((V.rule(self.c, rid)["status"], V.rule(self.c, rid)["wait"]), ("trial", "READY"))
        acts = self.rec(NOW + 2 * H)
        self.assertEqual([(a["action"], a["rule_id"], a["code"]) for a in acts], [("mismatch", rid, "NO_VERDICT")])
        self.assertEqual(self.st(l["lesson_id"]), ("confirmed", "TRIAL_PROMOTE"))
        self.assertEqual(V.rule(self.c, rid)["status"], "rejected")
        self.assertEqual(self.c.execute("SELECT step, ref FROM learn_events WHERE code='RECONCILE_MISMATCH'").fetchall(), [("reconcile", l["lesson_id"])])
        self.assertEqual(TR.promoted(self.c), [])                       # продвигать нечего
        n = self.counts()
        self.assertEqual(self.rec(NOW + 3 * H), [])
        self.assertEqual(self.counts(), n)

    def test_rejected_lesson_open_trial_after_restore(self):
        """Урок rejected (TRIAL_REJECT), а восстановленная база показывает открытое испытание: испытание закрывается кодом урока."""
        l = self.lesson(sample=False)
        rid = self.rule_for(l, state="open")
        bk = self.backup_db(NOW)
        tid = TR.by_rule(self.c, rid)["trial_id"]
        TR.close(self.c, TR.get(self.c, tid), TR.REJECT, "TRIAL_REJECT", {}, NOW + H, self.cfg)
        self.rec(NOW + H)
        self.assertEqual(self.st(l["lesson_id"]), ("rejected", "TRIAL_REJECT"))
        self.restore(bk)
        self.assertEqual(TR.get(self.c, tid)["state"], "open")
        acts = self.rec(NOW + 2 * H)
        self.assertEqual([(a["action"], a["code"]) for a in acts], [("mismatch", "TRIAL_REJECT")])
        t = TR.get(self.c, tid)
        self.assertEqual((t["state"], t["verdict"], t["numbers"]["reason"]), ("closed", "inconclusive", "RECONCILE"))
        self.assertEqual((V.rule(self.c, rid)["status"], V.rule(self.c, rid)["reason_code"]), ("rejected", "TRIAL_REJECT"))
        self.assertEqual(self.st(l["lesson_id"]), ("rejected", "TRIAL_REJECT"))
        self.assertEqual(self.rec(NOW + 3 * H), [])

    def test_hyp_lesson_lost_trial_reregistered(self):
        """Урок hyp, у которого после восстановления нет ни испытания, ни правила: сверка не трогает урок, шаг регистрации регистрирует заново."""
        self.fill(T0, 7 * 24)
        l = self.lesson()
        bk = self.backup_db(NOW - H)
        rep = TR.register_pending(self.c, self.root, self.cfg, NOW, policy=P2, mcfg=self.mcfg, live=lambda p, v: True)
        self.assertEqual(len(rep["registered"]), 1)
        self.restore(bk)
        self.assertEqual(TR.trials(self.c), [])
        acts = self.rec(NOW + H)
        self.assertEqual(acts, [{"lesson_id": l["lesson_id"], "action": "unregistered"}])
        self.assertEqual(self.st(l["lesson_id"]), ("hyp", "NEW"))
        rep = TR.register_pending(self.c, self.root, self.cfg, NOW + H, policy=P2, mcfg=self.mcfg, live=lambda p, v: True)
        self.assertEqual(len(rep["registered"]), 1)
        self.assertEqual(TR.trials(self.c)[0]["lesson_id"], l["lesson_id"])
        self.assertEqual([a for a in self.rec(NOW + 2 * H) if a["action"] != "unregistered"], [])


# ---------------------------------------------------------------- шаг цикла

class TestStep(Base):
    def test_step_in_registry_and_ctx(self):
        names = dict((s.name, s) for s in CY.default_steps())
        self.assertIs(names["reconcile"].fn, RC.step_reconcile)
        self.assertFalse(names["reconcile"].stub)
        self.assertEqual(names["reconcile"].interval, "reconcile_s")
        l = self.lesson(sample=False)
        rid = self.rule_for(l)
        V.reject_rule(self.c, rid, "NO_VERDICT", NOW, self.cfg)
        res = RC.step_reconcile(CY.Ctx(self.root, self.c, self.cfg, NOW, mcfg=self.mcfg))
        self.assertEqual((res["code"], res["changed"], res["mismatch"]), ("OK", 1, 0))
        self.assertEqual(self.st(l["lesson_id"]), ("rejected", "NO_VERDICT"))


if __name__ == "__main__":
    unittest.main()
