"""tb.learnloop.watch (R2, задача 16): сторож отката. Свойства design.md: 14 (идемпотентность: повтор не создаёт версий и строк), 16 (откат
восстанавливает поведение: разрешённые входы после отката равны версии до продвижения), 22 (нет безнадзорных правил: аренда только после
успешной проверки, ошибка шага — аренды нет, просрочка — режим без применения). Откат при подмене будущего эффекта, точки 50/100/200
(alpha_watch / 3), недельная проверка по скользящим 200 кластерам (alpha_watch_weekly), решение не раньше cut_ts окна, мёртвая зона,
принятая чувствительность (около +0.18R в точке 200, +0.36R в точке 50, +0.26R в недельной при разбросе 1.2R) как границы, смена версии
(ctx_ver) — отсчёт заново, cooldown 14 суток, TRADE_STARVATION по выборке окна 7 суток. Временные корни, фейковые часы, сети нет."""
import copy
import json
import os
import random
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb.core import bus
from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import cycle as CY
from tb.learnloop import rules as RL
from tb.learnloop import versions as V
from tb.learnloop import watch as W
from tb.logic import learn_rules as LR

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = 3600
D = 86400
T0 = 1790812800                   # 2026-10-01T00:00:00Z
NOW = T0 + 12 * D                 # момент продвижения
P2 = {"id": "P2", "ver": 1, "name": "confirm_follow", "kinds": ["range_5m", "turnover_surge", "hot_enter", "oi_spike", "liq_cascade"], "side": "with_dir",
      "entry": "candle_confirm_limit", "enabled": True}
SPREAD = [{"feature": "spread_bps", "op": ">=", "value": 5.0}]
RANGE = [{"feature": "range_5m_pct", "op": ">=", "value": 5.0}]
CLAIM = {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.5}


def at(k):
    """Момент, когда в выборку окна вошли k часов после продвижения: last_b(now) = NOW + k часов."""
    return NOW + k * H + 48 * H + 900


def series(n, mean, sd):
    """n значений со средним mean и выборочным разбросом около sd (знакопеременное отклонение)."""
    return [mean + sd * (1 if i % 2 else -1) for i in range(n)]


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbwatch_")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "config"))
        for f in ("learnloop.json", "memory.json", "shadow_policies.json"):
            shutil.copy(os.path.join(REPO, "config", f), os.path.join(self.root, "config", f))
        with open(os.path.join(self.root, "config", "learnloop.json"), encoding="utf-8") as f:
            self.cfg = json.load(f)
        RV.clear_cache()
        store2._STATUS_CACHE.clear()
        self.c = store.open_rw(self.root)
        self.addCleanup(self.c.close)
        store2.migrate_v2a(self.c, self.root, explicit=True, backup=False, now=T0)
        V.init_rulebook(self.c, T0)
        self.n = 0
        self.rng = random.Random(5)
        self.events = []

    def ccfg(self, **kw):
        c = copy.deepcopy(self.cfg)
        for path, v in kw.items():
            a, b = path.split("__")
            c[a][b] = v
        return c

    def row(self, t0, spread=1.0, rng=1.0, r=0.0, ctx="c1", filled=1):
        self.n += 1
        hour = t0 // H
        return {"cluster": "%d:long" % hour, "hour": hour, "side": "long", "key": "K%07d" % self.n, "t0": int(t0), "type": "range_5m", "policy_id": "P2",
                "policy_ver": 1, "ctx_ver": ctx, "env_norm": "mainnet", "filled": filled, "r_net": r if filled else None,
                "feats": {"spread_bps": spread, "range_5m_pct": rng}, "flags": None, "closed_ts": int(hour * H + H + 60)}

    def fill(self, t_from, hours, hit_r=None, n_plain=3, ctx="c1", n_range=0, n_hit=1):
        """По часу: n_hit прогонов-хитов SPREAD (r — hit_r(i) или −0.5 + шум), n_range хитов RANGE, n_plain обычных."""
        rows = []
        for i in range(hours):
            h = t_from + i * H
            r = hit_r(i) if hit_r else -0.5 + self.rng.gauss(0, 0.2)
            rows += [self.row(h + 60, spread=10.0, r=r, ctx=ctx) for _ in range(n_hit)]
            rows += [self.row(h + 90, rng=10.0, r=-0.2, ctx=ctx) for _ in range(n_range)]
            rows += [self.row(h + 120, r=self.rng.gauss(0, 0.2), ctx=ctx) for _ in range(n_plain)]
        store2.insert_rows(self.c, "cluster_runs", rows)

    def active(self, cond, lid, now=NOW):
        draft = RL.derive({"lesson_id": lid, "status": "hyp", "cond": cond, "claim": dict(CLAIM), "evidence": {"src": "labels"}}, P2, self.c, now)
        start = now - 5 * D
        res = V.create_with_trial(self.c, draft, {"criterion": {"active": []}, "criterion_h": "0" * 16, "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1",
                                                  "start_ts": start, "deadline_ts": start + 30 * D, "env_norm": "mainnet"}, now, self.cfg)
        store2.update_row(self.c, "trials", res["trial_id"], {"state": "closed", "verdict": "promote", "cut_ts": now - H, "closed_ts": now - H,
                                                              "numbers": {"in_mean": -0.5, "n": 150, "diff": -0.4, "se": 0.05, "p": 0.001}})
        V.set_wait(self.c, res["rule_id"], "READY", now - H)
        V.promote(self.c, res["rule_id"], now, self.cfg)
        return res["rule_id"]

    def emit(self, type_, d, root, now):
        self.events.append((type_, d))
        return bus.emit(type_, d, root=root, now=now)

    def wrun(self, now, cfg=None, **kw):
        return W.check(self.c, self.root, cfg or self.cfg, now, emit=self.emit, **kw)

    def vers(self):
        return [(v, rc) for v, rc in self.c.execute("SELECT ver, reason_code FROM rulebook_versions ORDER BY seq")]

    def counts(self):
        return [self.c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in ("rulebook_versions", "rule_rows", "blocklist_rows", "learn_events")]


# ---------------------------------------------------------------- чистые функции: чувствительность и мёртвая зона

class TestDecide(unittest.TestCase):
    THR = 0.05 / 3

    def test_sensitivity_point_200(self):
        """Принятая чувствительность: при разбросе 1.2R точка 200 откатывает от среднего около +0.18R."""
        self.assertTrue(W.decide(series(200, 0.20, 1.2), self.THR, 0.1)["rollback"])
        self.assertFalse(W.decide(series(200, 0.16, 1.2), self.THR, 0.1)["rollback"])

    def test_sensitivity_point_50(self):
        self.assertTrue(W.decide(series(50, 0.40, 1.2), self.THR, 0.1)["rollback"])
        self.assertFalse(W.decide(series(50, 0.32, 1.2), self.THR, 0.1)["rollback"])

    def test_sensitivity_weekly(self):
        self.assertTrue(W.decide(series(200, 0.28, 1.2), 0.001, 0.1)["rollback"])
        self.assertFalse(W.decide(series(200, 0.24, 1.2), 0.001, 0.1)["rollback"])
        self.assertTrue(W.decide(series(200, 0.24, 1.2), self.THR, 0.1)["rollback"])      # тот же ряд в точке 200 — откат

    def test_dead_zone(self):
        """Мёртвая зона: среднее до +rollback_delta_r не откатывается даже при значимости; выше — без значимости тоже нет."""
        for m in (-0.3, -0.1, 0.0, 0.05, 0.09):
            self.assertFalse(W.decide(series(200, m, 0.05), self.THR, 0.1)["rollback"], m)
        d = W.decide(series(200, 0.09, 0.05), self.THR, 0.1)
        self.assertLess(d["p"], 1e-6)                                    # значимо больше нуля, но в мёртвой зоне
        self.assertFalse(W.decide(series(50, 0.3, 3.0), self.THR, 0.1)["rollback"])        # выше delta, но без значимости


class TestLooks(unittest.TestCase):
    CFG = {"watch": {"looks": [50, 100, 200], "weekly_window": 200, "alpha_watch": 0.05, "alpha_watch_weekly": 0.001}}

    def cl(self, n, h0=1000):
        return [(h0 + i, "%d:long" % (h0 + i), 0.0) for i in range(n)]

    def test_points_only(self):
        st = {"done": [], "week": 0}
        self.assertEqual(W.looks_due(self.cl(49), 0, st, self.CFG), [])
        due = W.looks_due(self.cl(50), 0, st, self.CFG)
        self.assertEqual([(x[0], len(x[1]), round(x[2], 6)) for x in due], [("L50", 50, round(0.05 / 3, 6))])
        self.assertEqual(W.looks_due(self.cl(99), 0, {"done": [50]}, self.CFG), [])
        self.assertEqual([x[0] for x in W.looks_due(self.cl(120), 0, st, self.CFG)], ["L50", "L100"])   # пропуск после простоя: обе точки

    def test_weekly_after_200(self):
        cl = self.cl(260)
        b200 = (cl[199][0] + 1) * H
        st = {"done": [50, 100, 200], "week": 0}
        self.assertEqual(W.looks_due(cl, b200 + 7 * D - H, st, self.CFG), [])
        due = W.looks_due(cl, b200 + 7 * D, st, self.CFG)
        self.assertEqual([(x[0], len(x[1]), x[2]) for x in due], [("W1", 200, 0.001)])
        self.assertEqual(W.looks_due(cl, b200 + 8 * D, dict(st, week=1), self.CFG), [])
        self.assertEqual([x[0] for x in W.looks_due(cl, b200 + 30 * D, dict(st, week=1), self.CFG)], ["W4"])  # пропущенные недели не досчитываются
        self.assertEqual(W.looks_due(cl, b200 + 30 * D, {"done": [50, 100]}, self.CFG)[0][0], "L200")


# ---------------------------------------------------------------- откат на снимках

class TestRollback(Base):
    def setUp(self):
        Base.setUp(self)
        self.fill(T0, 12 * 24)                                           # до продвижения: хиты в среднем −0.5R
        self.before = [r["rule_id"] for r in V.active_rules(self.c)]
        self.rid = self.active(SPREAD, "L-0001")

    def test_rollback_on_swapped_future_effect(self):
        """Подмена будущего эффекта: после продвижения отсечённые входы в среднем +0.6R -> откат в точке 50 и не раньше cut_ts её окна."""
        self.fill(NOW, 80, hit_r=lambda i: 0.6 + self.rng.gauss(0, 0.5))
        rep = self.wrun(at(49))
        self.assertEqual((rep["code"], rep["rules"][0]["looks"]), ("OK", []))           # 49 кластеров в выборке: решения нет
        self.assertEqual(self.vers()[-1], ("R-0002", "PROMOTE"))
        rep = self.wrun(at(50) - 1)                                                    # 50-й кластер ещё не прошёл cut_ts
        self.assertEqual(rep["rules"][0]["looks"], [])
        rep = self.wrun(at(50))
        self.assertEqual((rep["code"], rep["rollbacks"]), ("ROLLBACK", [self.rid]))
        look = rep["rules"][0]["looks"][0]
        self.assertEqual((look["look"], look["n"], look["rollback"]), ("L50", 50, True))
        self.assertEqual(self.vers()[-1], ("R-0003", "ROLLBACK"))
        r = V.rule(self.c, self.rid)
        self.assertEqual((r["status"], r["reason_code"], r["rulebook_ver_out"]), ("disabled", "ROLLBACK", "R-0003"))
        nums = RV.last_version(self.c)["numbers"]
        self.assertEqual((nums["n"], nums["look"]), (50, "L50"))
        self.assertGreater(nums["mean"], 0.1)
        self.assertLess(nums["p"], 0.05 / 3)
        # свойство 16: разрешённые входы после отката равны версии до продвижения
        self.assertEqual([x["rule_id"] for x in V.active_rules(self.c)], self.before)
        self.assertEqual(RV.last_version(self.c)["rule_ids"], RV.get_version(self.c, "R-0001")["rule_ids"])
        cand = {"sym": "X", "side": "long", "ok": True}
        feats = {"spread_bps": 10.0, "range_5m_pct": 1.0, "ev_kind": "range_5m", "side_rel": "with_dir", "side": "long"}
        view = {"ver": "R-0003", "rules": V.active_rules(self.c), "apply": True, "why": "OK"}
        self.assertTrue(LR.apply(view, cand, feats, {})["ok"])
        # копия rollback, событие info, learn_events, аренда, чёрный список на cooldown_days
        self.assertIn("rollback", [b["reason"] for b in store2.list_backups(self.root)])
        evs = [e for e in bus.read(root=self.root) if e["type"] == "rulebook_rollback"]
        self.assertEqual(len(evs), 1)
        self.assertEqual((evs[0]["pri"], evs[0]["d"]["ver"], evs[0]["d"]["rule"], evs[0]["d"]["code"]), ("info", "R-0003", self.rid, "ROLLBACK"))
        self.assertEqual(self.c.execute("SELECT code FROM learn_events WHERE step='watch'").fetchall(), [("ROLLBACK",)])
        self.assertEqual(RV.read_lease(self.root), {"ts": at(50), "rulebook_ver": "R-0003"})
        ch = LR.block_h(SPREAD, "lower")
        b = RL.blocked(self.c, ch, at(50))
        self.assertEqual((b["code"], b["until_ts"]), ("ROLLBACK", at(50) + 14 * D))
        self.assertIsNone(RL.derive({"lesson_id": "L-0009", "status": "hyp", "cond": SPREAD, "claim": dict(CLAIM), "evidence": {"src": "labels"}},
                                    P2, self.c, at(50) + 13 * D))
        self.assertIsNone(RL.blocked(self.c, ch, at(50) + 14 * D))                     # фиксированный cooldown: потом условие может вернуться
        # свойство 14: повтор не создаёт версий и строк
        n = self.counts()
        for k in (0, H, 5 * D):
            self.assertEqual(self.wrun(at(50) + k)["rollbacks"], [])
        self.assertEqual(self.counts(), n)

    def test_dead_zone_stays_active(self):
        """Отсечённая часть около +0.05R: точки 50, 100, 200 пройдены без отката, повтор ничего не пишет."""
        self.fill(NOW, 210, hit_r=lambda i: 0.05 + (0.3 if i % 2 else -0.3))
        rep = self.wrun(at(205))
        self.assertEqual([x["look"] for x in rep["rules"][0]["looks"]], ["L50", "L100", "L200"])
        self.assertEqual(rep["rollbacks"], [])
        self.assertEqual(V.rule(self.c, self.rid)["status"], "active")
        self.assertEqual(W.state_get(self.c)[self.rid]["done"], [50, 100, 200])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM learn_events WHERE step='watch' AND code='LOOK'").fetchone()[0], 3)
        n = self.counts()
        self.assertEqual(self.wrun(at(206))["rules"][0]["looks"], [])
        self.assertEqual(self.counts(), n)

    def test_points_one_by_one(self):
        """Решение только в точках: между точками проверок нет; точка 100 по первым 100 кластерам."""
        self.fill(NOW, 120, hit_r=lambda i: 0.0 + (0.3 if i % 2 else -0.3))
        seen = []
        for k in range(45, 115, 5):
            seen += [(k, x["look"], x["n"]) for x in self.wrun(at(k))["rules"][0]["looks"]]
        self.assertEqual(seen, [(50, "L50", 50), (100, "L100", 100)])

    def test_weekly_rollback(self):
        """После точки 200 — раз в неделю по скользящим 200 кластерам с alpha_watch_weekly: эффект, появившийся позже, откатывается на
        недельной проверке, а не раньше недельной границы."""
        self.fill(NOW, 200, hit_r=lambda i: 0.0 + (0.3 if i % 2 else -0.3))
        self.fill(NOW + 200 * H, 200, hit_r=lambda i: 0.8 + (0.3 if i % 2 else -0.3))
        rep = self.wrun(at(200))
        self.assertEqual([x["look"] for x in rep["rules"][0]["looks"]], ["L50", "L100", "L200"])
        b200 = NOW + 200 * H
        self.assertEqual(self.wrun(b200 + 7 * D - 1 + 48 * H + 900)["rules"][0]["looks"], [])
        rep = self.wrun(b200 + 7 * D + 48 * H + 900)
        look = rep["rules"][0]["looks"][0]
        self.assertEqual((look["look"], look["n"], look["thr"], look["rollback"]), ("W1", 200, 0.001, True))
        self.assertEqual(self.vers()[-1], ("R-0003", "ROLLBACK"))

    def test_ctx_change_restarts_count(self):
        """Смена ctx_ver: отсчёт точек заново на новой версии, правило активно; точка 50 — по 50 кластерам новой версии."""
        self.fill(NOW, 45, hit_r=lambda i: 0.7 + (0.3 if i % 2 else -0.3))
        self.fill(NOW + 45 * H, 60, hit_r=lambda i: 0.7 + (0.3 if i % 2 else -0.3), ctx="c2")
        rep = self.wrun(at(45 + 40))
        self.assertEqual(rep["rules"][0]["looks"], [])                                   # 45 старых + 40 новых: ни один участок не дошёл до 50
        self.assertIn(":c2:", rep["rules"][0]["seg"])
        self.assertEqual(V.rule(self.c, self.rid)["status"], "active")
        rep = self.wrun(at(45 + 50))
        self.assertEqual(rep["rollbacks"], [self.rid])
        self.assertEqual(RV.last_version(self.c)["numbers"]["n"], 50)

    def test_ctx_change_resets_done_points(self):
        self.fill(NOW, 60, hit_r=lambda i: 0.0 + (0.3 if i % 2 else -0.3))
        self.wrun(at(55))
        self.assertEqual(W.state_get(self.c)[self.rid]["done"], [50])
        self.fill(NOW + 60 * H, 55, hit_r=lambda i: 0.0 + (0.3 if i % 2 else -0.3), ctx="c2")
        rep = self.wrun(at(60 + 52))
        self.assertEqual([x["look"] for x in rep["rules"][0]["looks"]], ["L50"])
        self.assertIn(":c2:", W.state_get(self.c)[self.rid]["seg"])


# ---------------------------------------------------------------- потеря входов

class TestStarvation(Base):
    def test_disables_rule_with_most_cut(self):
        """Доля отсечённых прогонов набора 3/5 > 0.4 при >= 30 кластерах: отключается правило с наибольшим числом отсечённых (SPREAD: 2 на час),
        остальные — на следующем проходе."""
        self.fill(T0, 12 * 24, n_hit=2, n_range=1, n_plain=2)
        a = self.active(RANGE, "L-0001", now=NOW - D)
        b = self.active(SPREAD, "L-0002", now=NOW - D)
        rep = self.wrun(NOW)
        self.assertEqual((rep["code"], rep["starvation"]["disabled"]), ("TRADE_STARVATION", b))
        self.assertAlmostEqual(rep["starvation"]["groups"][0]["share"], 0.6)
        r = V.rule(self.c, b)
        self.assertEqual((r["status"], r["reason_code"]), ("disabled", "TRADE_STARVATION"))
        self.assertEqual(self.vers()[-1][1], "TRADE_STARVATION")
        self.assertEqual(RL.blocked(self.c, LR.block_h(SPREAD, "lower"), NOW)["until_ts"], NOW + 14 * D)
        self.assertEqual([e[1]["code"] for e in self.events], ["TRADE_STARVATION"])
        evs = [e for e in bus.read(root=self.root) if e["type"] == "rulebook_rollback"]
        self.assertEqual([(e["pri"], e["d"]["code"], e["d"]["rule"]) for e in evs], [("info", "TRADE_STARVATION", b)])
        self.assertIn("rollback", [x["reason"] for x in store2.list_backups(self.root)])
        rep = self.wrun(NOW + H)                                                          # RANGE один: 1/5 = 0.2 — остаётся
        self.assertIsNone(rep["starvation"]["disabled"])
        self.assertEqual([x["rule_id"] for x in V.active_rules(self.c)], [a])

    def test_tie_later_promoted(self):
        self.fill(T0, 12 * 24, n_hit=1, n_range=1, n_plain=1)
        self.active(SPREAD, "L-0001", now=NOW - 2 * D)
        b = self.active(RANGE, "L-0002", now=NOW - D)
        self.assertEqual(self.wrun(NOW)["starvation"]["disabled"], b)

    def test_threshold_and_min_clusters(self):
        """Ровно 0.4 — не выше порога; меньше 30 кластеров в окне — не считается."""
        self.fill(T0, 12 * 24, n_hit=2, n_plain=3)
        self.active(SPREAD, "L-0001", now=NOW - D)
        rep = self.wrun(NOW)
        self.assertEqual((rep["starvation"]["groups"][0]["share"], rep["starvation"]["disabled"]), (0.4, None))
        rep = self.wrun(NOW, cfg=self.ccfg(watch__disable_trade_loss_share=0.39))
        self.assertIsNotNone(rep["starvation"]["disabled"])

    def test_few_clusters(self):
        self.fill(T0 + 12 * D - 49 * H - 29 * H, 29, n_hit=3, n_plain=1)
        self.active(SPREAD, "L-0001", now=NOW - D)
        rep = self.wrun(NOW)
        self.assertEqual(rep["starvation"]["groups"][0]["clusters"], 29)
        self.assertIsNone(rep["starvation"]["disabled"])


# ---------------------------------------------------------------- аренда и шаг цикла

class TestLease(Base):
    def test_empty_rulebook_writes_lease(self):
        rep = self.wrun(NOW)
        self.assertEqual((rep["code"], rep["ver"]), ("OK", "R-0001"))
        self.assertEqual(RV.read_lease(self.root), {"ts": NOW, "rulebook_ver": "R-0001"})
        self.assertTrue(RV.lease_fresh(self.root, NOW + 6 * H))
        self.assertFalse(RV.lease_fresh(self.root, NOW + 6 * H + 1))                   # просрочка: режим без применения

    def test_error_no_lease(self):
        """Свойство 22: ошибка проверки — аренда не обновляется (через цикл: STEP_ERROR)."""
        self.fill(T0, 12 * 24)
        self.active(SPREAD, "L-0001")
        self.fill(NOW, 20, hit_r=lambda i: 0.0)
        self.wrun(at(9))
        orig = W.cut_clusters
        W.cut_clusters = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("сбой"))
        try:
            cy = CY.Cycle(self.root, self.cfg, steps=[CY.Step("watch", "hourly", W.step_watch, interval="watch_s", group="watch")], policy=P2, mcfg={})
            rep = cy.run_pass(self.c, at(10))
        finally:
            W.cut_clusters = orig
        self.assertEqual(rep["errors"], {"watch": "STEP_ERROR"})
        self.assertEqual(RV.read_lease(self.root)["ts"], at(9))
        self.assertFalse(RV.lease_fresh(self.root, at(9) + 6 * H + 1))
        cy.last.clear()
        rep = cy.run_pass(self.c, at(11))                                               # следующая успешная проверка — аренда снова свежа
        self.assertEqual(rep["ran"], {"watch": "OK"})
        self.assertEqual(RV.read_lease(self.root)["ts"], at(11))

    def test_registry(self):
        st = dict((s.name, s) for s in CY.default_steps())["watch"]
        self.assertIs(st.fn, W.step_watch)
        self.assertFalse(st.stub)
        self.assertEqual((st.period, st.interval, st.group), ("hourly", "watch_s", "watch"))

    def test_step_on_ctx(self):
        res = W.step_watch(CY.Ctx(self.root, self.c, self.cfg, NOW, policy=P2, mcfg={}))
        self.assertEqual((res["code"], res["active"], res["lease"]), ("OK", 0, NOW))


if __name__ == "__main__":
    unittest.main()
