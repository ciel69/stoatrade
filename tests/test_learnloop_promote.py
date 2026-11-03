"""tb.learnloop.promote (R2, задача 15): продвижение правил. Свойства design.md: 23 (LOSS_CAP и NO_VERDICT по OVERLAP в чёрный список не пишут),
24 (пересечение по строкам: два ожидающих правила выше 0.5 — продвигается одно; ниже — оба; открытое испытание после продвижения), 25 (LOSS_CAP:
прогноз по выборке окна 7 суток, порог 0.4, сразу после продвижения та же мера ниже порога, дольше 14 суток — отклонение), 26 (ожидание READY и
MAX_ACTIVE без срока). Выход из прогрева на фейковых часах, нарушение условия (CHAIN_BROKEN, старая копия) приостанавливает продвижение, режим off,
mode=auto без сторожа — ни одного продвижения, правило ждёт READY без срока; версия, копия promote, событие rulebook_promoted (info).
Временные корни, фейковые часы (now передаётся явно), сети нет."""
import copy
import json
import os
import random
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb.core import bus
from tb.knowledge import markers as KM
from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import clusters as CL
from tb.learnloop import cycle as CY
from tb.learnloop import promote as PR
from tb.learnloop import rules as RL
from tb.learnloop import trial as TR
from tb.learnloop import versions as V
from tb.logic import learn_rules as LR
from tb.memory import cfg as memcfg

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = 3600
D = 86400
T0 = 1790812800                   # 2026-10-01T00:00:00Z
NOW = T0 + 12 * D
P2 = {"id": "P2", "ver": 1, "name": "confirm_follow", "kinds": ["range_5m", "turnover_surge", "hot_enter", "oi_spike", "liq_cascade"], "side": "with_dir",
      "entry": "candle_confirm_limit", "enabled": True}
SPREAD = [{"feature": "spread_bps", "op": ">=", "value": 5.0}]
SPREAD8 = [{"feature": "spread_bps", "op": ">=", "value": 8.0}]
RANGE = [{"feature": "range_5m_pct", "op": ">=", "value": 5.0}]
CLAIM = {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.5}
READY = {"ok": True, "failed": []}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbprom_")
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
        self.addCleanup(self.c.close)
        store2.migrate_v2a(self.c, self.root, explicit=True, backup=False, now=T0)
        V.init_rulebook(self.c, T0)
        self.n = 0
        self.rng = random.Random(3)

    def ccfg(self, **kw):
        c = copy.deepcopy(self.cfg)
        for path, v in kw.items():
            a, b = path.split("__")
            c[a][b] = v
        return c

    # ---- данные: в каждом часе прогоны spread (хит SPREAD/SPREAD8), range (хит RANGE) и обычные
    def row(self, t0, spread=1.0, rng=1.0, r=0.0, ctx="c1"):
        self.n += 1
        hour = t0 // H
        return {"cluster": "%d:long" % hour, "hour": hour, "side": "long", "key": "K%07d" % self.n, "t0": int(t0), "type": "range_5m", "policy_id": "P2",
                "policy_ver": 1, "ctx_ver": ctx, "env_norm": "mainnet", "filled": 1, "r_net": r, "feats": {"spread_bps": spread, "range_5m_pct": rng},
                "flags": None, "closed_ts": int(hour * H + H + 60)}

    def fill(self, t_from, hours, n_spread=1, n_range=0, n_plain=3, both=0):
        rows = []
        for i in range(hours):
            h = t_from + i * H
            rows += [self.row(h + 60, spread=10.0, r=-0.5 + self.rng.gauss(0, 0.2)) for _ in range(n_spread)]
            rows += [self.row(h + 90, rng=10.0, r=-0.5) for _ in range(n_range)]
            rows += [self.row(h + 100, spread=10.0, rng=10.0, r=-0.5) for _ in range(both)]
            rows += [self.row(h + 120, r=self.rng.gauss(0, 0.2)) for _ in range(n_plain)]
        store2.insert_rows(self.c, "cluster_runs", rows)

    # ---- правила
    def _create(self, cond, lid, now, active_ids=(), start=None):
        draft = RL.derive({"lesson_id": lid, "status": "hyp", "cond": cond, "claim": dict(CLAIM), "evidence": {"src": "labels"}}, P2, self.c, now)
        start = now - 5 * D if start is None else start
        return V.create_with_trial(self.c, draft, {"criterion": {"active": [{"rule_id": x} for x in active_ids]}, "criterion_h": "0" * 16,
                                                   "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1", "start_ts": start, "deadline_ts": start + 30 * D,
                                                   "env_norm": "mainnet"}, now, self.cfg)

    def pending(self, cond, lid, in_mean=-0.5, now=NOW, active_ids=()):
        """Правило trial, испытание закрыто вердиктом promote, wait=READY (как trial.close)."""
        res = self._create(cond, lid, now, active_ids)
        store2.update_row(self.c, "trials", res["trial_id"], {"state": "closed", "verdict": "promote", "cut_ts": now - H, "closed_ts": now - H,
                                                              "numbers": {"in_mean": in_mean, "n": 150, "diff": -0.4, "se": 0.05, "p": 0.001}})
        V.set_wait(self.c, res["rule_id"], "READY", now - H)
        return res["rule_id"]

    def active(self, cond, lid, now=NOW):
        rid = self.pending(cond, lid, now=now)
        V.promote(self.c, rid, now, self.cfg)
        return rid

    def open_trial(self, cond, lid, now=NOW, start=None):
        draft = RL.derive({"lesson_id": lid, "status": "hyp", "cond": cond, "claim": dict(CLAIM), "evidence": {"src": "labels"}}, P2, self.c, now)
        smp = {"policy": "P2", "policy_ver": 1, "ctx_ver": "c1", "env_norm": "mainnet", "a": T0, "b": T0 + D, "cut_ts": T0 + 3 * D, "h": "x" * 16}
        start = now - 5 * D if start is None else start
        crit = TR.criterion(draft, smp, V.active_rules(self.c), self.cfg, start, lid)
        res = V.create_with_trial(self.c, draft, {"criterion": crit, "criterion_h": TR.crit_hash(crit), "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1",
                                                  "start_ts": crit["start_ts"], "deadline_ts": crit["deadline_ts"], "env_norm": "mainnet"}, now, self.cfg)
        return res["trial_id"]

    def prun(self, now=NOW, cfg=None, ready=READY, **kw):
        return PR.run(self.c, self.root, cfg or self.cfg, now, ready=ready, **kw)

    def status(self, rid):
        r = V.rule(self.c, rid)
        return r["status"], r["wait"], r["reason_code"]

    def vers(self):
        return [(v, rc) for v, rc in self.c.execute("SELECT ver, reason_code FROM rulebook_versions ORDER BY seq")]


# ---------------------------------------------------------------- продвижение и событие

class TestPromote(Base):
    def test_promotes_with_version_backup_event(self):
        self.fill(T0, 12 * 24)
        rid = self.pending(SPREAD, "L-0001")
        rep = self.prun()
        self.assertEqual([p["rule_id"] for p in rep["promoted"]], [rid])
        self.assertEqual(self.vers(), [("R-0001", "INIT"), ("R-0002", "PROMOTE")])
        st = self.status(rid)
        self.assertEqual((st[0], st[1], st[2]), ("active", None, "PROMOTE"))
        self.assertEqual(V.rule(self.c, rid)["rulebook_ver_in"], "R-0002")
        self.assertIn("promote", [b["reason"] for b in store2.list_backups(self.root)])
        evs = [e for e in bus.read(root=self.root) if e["type"] == "rulebook_promoted"]
        self.assertEqual(len(evs), 1)
        self.assertEqual((evs[0]["d"]["ver"], evs[0]["d"]["rule"], evs[0]["pri"]), ("R-0002", rid, "info"))
        v = RV.last_version(self.c)
        self.assertEqual(v["trial_id"], TR.by_rule(self.c, rid)["trial_id"])
        self.assertEqual(self.c.execute("SELECT code FROM learn_events WHERE step='promote'").fetchall(), [("PROMOTE",)])
        # идемпотентность: повтор ничего не создаёт
        n = [self.c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in ("rulebook_versions", "rule_rows", "learn_events")]
        rep2 = self.prun(NOW + H)
        self.assertEqual(rep2["promoted"], [])
        self.assertEqual([self.c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in ("rulebook_versions", "rule_rows", "learn_events")], n)

    def test_no_pending_no_checks(self):
        calls = []
        orig = V.promotion_ready
        V.promotion_ready = lambda *a, **k: calls.append(1) or orig(*a, **k)
        try:
            rep = PR.run(self.c, self.root, self.cfg, NOW)
        finally:
            V.promotion_ready = orig
        self.assertEqual((rep["code"], rep["ready"], calls), ("OK", None, []))

    def test_mode_off_never_changes(self):
        self.fill(T0, 12 * 24)
        rid = self.pending(SPREAD, "L-0001")
        cfg = self.ccfg(promotion__mode="off")
        for i in range(48):
            rep = self.prun(NOW + i * H, cfg=cfg)
            self.assertEqual(rep["code"], PR.OFF)
        self.assertEqual(self.vers(), [("R-0001", "INIT")])
        self.assertEqual(self.status(rid)[:2], ("trial", "READY"))
        self.assertEqual(self.prun(NOW + 49 * H)["promoted"][0]["rule_id"], rid)        # снова auto: продвигается само

    def test_not_ready_waits_ready(self):
        self.fill(T0, 12 * 24)
        rid = self.pending(SPREAD, "L-0001")
        V.set_wait(self.c, rid, None, NOW - H)
        rep = self.prun(ready={"ok": False, "failed": ["DAYS"]})
        self.assertEqual((rep["promoted"], rep["waiting"]), ([], [(rid, "READY")]))
        self.assertEqual(self.status(rid)[:2], ("trial", "READY"))
        self.assertEqual(self.vers(), [("R-0001", "INIT")])


# ---------------------------------------------------------------- готовность на фейковых часах

class TestReadiness(Base):
    """Выход из прогрева и приостановка по условиям versions.promotion_ready (настоящая функция)."""
    START = T0 + 20 * D + 12 * H

    def day(self, i, full=True):
        store2.log_event(self.c, V.DAY_STEP, "DAY", "code", ref=CY.day_of(T0 + i * D), body={"counted": True, "full": full, "work_s": 24 * H},
                         now=T0 + (i + 1) * D)

    def env(self, now, lease=True, backup=True):
        if lease:
            KM.write_json(RV.lease_path(self.root), {"ts": now - H})
        if backup:
            store2.make_backup(self.root, "daily", now=now - 2 * H)
        V.meta_put(self.c, V.META_DAY, dict(CY.new_acc(CY.day_of(now)), work_s=12 * 3600.0))

    def setUp(self):
        Base.setUp(self)
        self.fill(T0 + 8 * D, 14 * 24)
        self.rid = self.pending(SPREAD, "L-0001", now=self.START)

    def real(self, now):
        return PR.run(self.c, self.root, self.cfg, now, state={})

    def test_warmup_exit(self):
        """6 полных суток — не готово (DAYS), правило ждёт READY; 7-е полное — продвижение без вмешательства."""
        for i in range(13, 19):
            self.day(i)
        now = self.START
        self.env(now)
        rep = self.real(now)
        self.assertEqual((rep["ready"]["failed"], rep["promoted"]), (["DAYS"], []))
        self.assertEqual(self.status(self.rid)[:2], ("trial", "READY"))
        self.day(19)
        now += H
        self.env(now)
        rep = self.real(now)
        self.assertTrue(rep["ready"]["ok"], rep)
        self.assertEqual([p["rule_id"] for p in rep["promoted"]], [self.rid])

    def test_chain_broken_and_old_backup_pause(self):
        for i in range(12, 20):
            self.day(i)
        now = self.START
        self.env(now)
        store2.log_event(self.c, "x", "CHAIN_BROKEN", "code", error=True, now=now - 2 * D)
        rep = self.real(now)
        self.assertIn("BAD_EVENTS", rep["ready"]["failed"])
        self.assertEqual(self.vers(), [("R-0001", "INIT")])
        # копия старше 24 ч (события CHAIN_BROKEN уже за окном 7 x 24 ч работы)
        now2 = now + 8 * D
        for i in range(20, 28):
            self.day(i)
        self.env(now2, backup=False)
        rep = self.real(now2)
        self.assertEqual(rep["ready"]["failed"], ["BACKUP"])
        self.assertEqual(self.vers(), [("R-0001", "INIT")])
        self.assertEqual(self.status(self.rid)[:2], ("trial", "READY"))
        self.env(now2 + H)
        self.assertEqual(len(self.real(now2 + H)["promoted"]), 1)                      # условие снова выполнено — возобновляется само

    def test_auto_without_watch_waits_forever(self):
        """Решение владельца 6: mode=auto, сторожа нет (заглушка задачи 16) -> ни одного продвижения, правило ждёт READY без срока."""
        for i in range(12, 20):
            self.day(i)
        now = self.START
        self.assertEqual(self.cfg["promotion"]["mode"], "auto")
        for k in range(0, 40 * 24, 6):
            t = now + k * H
            self.env(t, lease=False)
            rep = self.real(t)
            self.assertIn("WATCH", rep["ready"]["failed"])
            self.assertEqual(rep["promoted"], [])
        self.assertEqual(self.vers(), [("R-0001", "INIT")])
        self.assertEqual(self.status(self.rid), ("trial", "READY", "TRIAL"))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM rule_rows WHERE rule_id=?", (self.rid,)).fetchone()[0], 2)   # TRIAL + READY


# ---------------------------------------------------------------- лимит, LOSS_CAP

class TestLimits(Base):
    def test_max_active_waits_without_term(self):
        """Свойство 26: MAX_ACTIVE без срока; место освободилось — продвижение."""
        self.fill(T0, 60 * 24, n_spread=1, n_range=1, n_plain=6)
        cfg = self.ccfg(rulebook__max_active_rules=1)
        a = self.active(RANGE, "L-0001")
        rid = self.pending(SPREAD, "L-0002", active_ids=[a])
        for k in (0, 10 * D, 40 * D):
            rep = self.prun(NOW + k, cfg=cfg)
            self.assertEqual(rep["waiting"], [(rid, "MAX_ACTIVE")])
        self.assertEqual(self.status(rid)[:2], ("trial", "MAX_ACTIVE"))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM learn_events WHERE code='MAX_ACTIVE'").fetchone()[0], 1)
        V.disable(self.c, [a], "ROLLBACK", NOW + 40 * D, cfg)
        rep = self.prun(NOW + 40 * D + H, cfg=cfg)
        self.assertEqual([p["rule_id"] for p in rep["promoted"]], [rid])

    def test_loss_cap_wait_then_lifted(self):
        """Свойство 25: набор (RANGE + SPREAD) отсёк бы 2/4 >= 0.4 -> LOSS_CAP; после отключения RANGE доля 1/4 -> продвижение;
        сразу после продвижения та же мера (TRADE_STARVATION сторожа) ниже порога."""
        self.fill(T0, 30 * 24, n_spread=1, n_range=1, n_plain=2)
        a = self.active(RANGE, "L-0001")
        rid = self.pending(SPREAD, "L-0002", active_ids=[a])
        rep = self.prun()
        self.assertEqual(rep["waiting"], [(rid, "LOSS_CAP")])
        r = V.rule(self.c, rid)
        self.assertEqual((r["wait"], r["wait_since_ts"]), ("LOSS_CAP", NOW))
        self.assertIsNone(RL.blocked(self.c, LR.block_h(SPREAD, "lower"), NOW))
        self.prun(NOW + 5 * D)
        self.assertEqual(V.rule(self.c, rid)["wait_since_ts"], NOW)                       # непрерывное ожидание: начало не сдвигается
        V.disable(self.c, [a], "OWNER_DISABLE", NOW + 5 * D + 1, self.cfg)
        now = NOW + 5 * D + H
        rep = self.prun(now)
        self.assertEqual([p["rule_id"] for p in rep["promoted"]], [rid])
        src = PR.src_of(TR.by_rule(self.c, rid))
        ls = PR.loss_share(PR.loss_window(self.c, src, self.cfg, now), V.active_rules(self.c), V.active_rules(self.c)[0]["scope"])
        self.assertLess(ls["share"], self.cfg["watch"]["disable_trade_loss_share"])
        self.assertAlmostEqual(ls["share"], 0.25)

    def test_loss_cap_expires_after_14_days(self):
        """Свойства 23, 25: LOSS_CAP дольше loss_cap_wait_days -> rejected (LOSS_CAP) без чёрного списка."""
        self.fill(T0, 30 * 24, n_spread=2, n_plain=2)
        rid = self.pending(SPREAD, "L-0001")
        self.prun()
        self.assertEqual(self.status(rid)[:2], ("trial", "LOSS_CAP"))
        self.prun(NOW + 14 * D)
        self.assertEqual(self.status(rid)[:2], ("trial", "LOSS_CAP"))                    # ровно 14 суток — ещё ждёт
        rep = self.prun(NOW + 14 * D + H)
        self.assertEqual(rep["rejected"], [(rid, "LOSS_CAP", None)])
        self.assertEqual(self.status(rid), ("rejected", None, "LOSS_CAP"))
        self.assertIsNone(RL.blocked(self.c, LR.block_h(SPREAD, "lower"), NOW + 14 * D + 2 * H))
        self.assertEqual(self.vers(), [("R-0001", "INIT")])

    def test_ready_interrupt_restarts_loss_cap_clock(self):
        """Решение задачи: ожидание READY прерывает непрерывное ожидание LOSS_CAP (wait_since_ts — начало текущего ожидания с этой причиной)."""
        self.fill(T0, 30 * 24, n_spread=2, n_plain=2)
        rid = self.pending(SPREAD, "L-0001")
        self.prun()
        self.prun(NOW + 10 * D, ready={"ok": False, "failed": ["WATCH"]})
        self.assertEqual(self.status(rid)[:2], ("trial", "READY"))
        self.prun(NOW + 10 * D + H)
        self.assertEqual(V.rule(self.c, rid)["wait_since_ts"], NOW + 10 * D + H)
        self.prun(NOW + 15 * D)
        self.assertEqual(self.status(rid)[:2], ("trial", "LOSS_CAP"))


# ---------------------------------------------------------------- пересечения по строкам

class TestOverlap(Base):
    def test_two_overlapping_pending_one_promoted(self):
        """Свойства 23, 24: SPREAD8 ⊂ SPREAD по строкам (доля 1.0 > 0.5): продвигается правило с большим |in_mean|, второе NO_VERDICT (OVERLAP)
        без чёрного списка."""
        self.fill(T0, 12 * 24, n_spread=1, n_plain=5)
        a = self.pending(SPREAD, "L-0001", in_mean=-0.3)
        b = self.pending(SPREAD8, "L-0002", in_mean=-0.6)
        rep = self.prun()
        self.assertEqual([p["rule_id"] for p in rep["promoted"]], [b])
        self.assertEqual(rep["rejected"], [(a, "NO_VERDICT", "OVERLAP")])
        self.assertEqual(self.status(a), ("rejected", None, "NO_VERDICT"))
        self.assertIsNone(RL.blocked(self.c, LR.block_h(SPREAD, "lower"), NOW))
        self.assertEqual(self.vers(), [("R-0001", "INIT"), ("R-0002", "PROMOTE")])

    def test_disjoint_pending_both_promoted(self):
        """Непересекающиеся правила продвигаются по очереди в одном проходе (2/6 < 0.4 с каждым)."""
        self.fill(T0, 12 * 24, n_spread=1, n_range=1, n_plain=4)
        a = self.pending(SPREAD, "L-0001", in_mean=-0.3)
        b = self.pending(RANGE, "L-0002", in_mean=-0.6)
        rep = self.prun()
        self.assertEqual([p["rule_id"] for p in rep["promoted"]], [b, a])
        self.assertEqual(self.vers(), [("R-0001", "INIT"), ("R-0002", "PROMOTE"), ("R-0003", "PROMOTE")])
        self.assertEqual(sorted(RV.last_version(self.c)["rule_ids"]), sorted([a, b]))

    def test_second_rule_loss_checked_after_first(self):
        """Пункт 7 для каждого: после первого продвижения второй правило добавило бы 2/4 -> LOSS_CAP."""
        self.fill(T0, 12 * 24, n_spread=1, n_range=1, n_plain=2)
        a = self.pending(SPREAD, "L-0001", in_mean=-0.3)
        b = self.pending(RANGE, "L-0002", in_mean=-0.6)
        rep = self.prun()
        self.assertEqual([p["rule_id"] for p in rep["promoted"]], [b])
        self.assertEqual(rep["waiting"], [(a, "LOSS_CAP")])

    def test_waiting_vs_later_active(self):
        """Ожидающее правило, пересекающееся с правилом, продвинутым после старта его испытания: NO_VERDICT (OVERLAP)."""
        self.fill(T0, 12 * 24, n_spread=1, n_plain=5)
        b = self.active(SPREAD8, "L-0002")
        a = self.pending(SPREAD, "L-0001")
        rep = self.prun()
        self.assertEqual(rep["rejected"], [(a, "NO_VERDICT", "OVERLAP")])

    def test_open_trials_after_promotion(self):
        """Свойство 24: открытое испытание с долей хитов, отсекаемых продвинутым правилом, > 0.5 закрывается NO_VERDICT (OVERLAP); на другом признаке
        с малым пересечением — продолжается."""
        self.fill(T0, 12 * 24, n_spread=1, n_range=1, n_plain=6, both=0)
        t_same = self.open_trial(SPREAD8, "L-0003")
        t_other = self.open_trial(RANGE, "L-0004")
        rid = self.pending(SPREAD, "L-0001")
        rep = self.prun()
        self.assertEqual([p["rule_id"] for p in rep["promoted"]], [rid])
        self.assertEqual([x["trial_id"] for x in rep["overlap_closed"]], [t_same])
        self.assertEqual((TR.get(self.c, t_same)["state"], TR.get(self.c, t_same)["numbers"]["reason"]), ("closed", "OVERLAP"))
        self.assertEqual(TR.get(self.c, t_other)["state"], "open")


# ---------------------------------------------------------------- шаг цикла

class TestStep(Base):
    def test_step_in_registry(self):
        names = dict((s.name, s) for s in CY.default_steps())
        self.assertIs(names["promote"].fn, PR.step_promote)
        self.assertFalse(names["promote"].stub)
        self.assertEqual((names["promote"].group, names["promote"].interval), ("verdict", "verdict_s"))

    def test_step_on_ctx(self):
        self.fill(T0, 12 * 24)
        rid = self.pending(SPREAD, "L-0001")
        ctx = CY.Ctx(self.root, self.c, self.cfg, NOW)
        res = PR.step_promote(ctx)                                       # без сторожа: не готово
        self.assertEqual((res["code"], res["promoted"], res["ready"]), ("OK", 0, False))
        self.assertEqual(self.status(rid)[:2], ("trial", "READY"))


if __name__ == "__main__":
    unittest.main()
