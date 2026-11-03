"""tb.learnloop.cycle / service / versions.promotion_ready (R2, задача 10): каркас цикла и сервис learnloop.

Свойство 14 (идемпотентность шагов), свойство 27 (учёт суток), расписание на фейковых часах (первое меню на 9-е сутки, holdout только после cut_ts),
STEP_ERROR без остановки, IntegrityError — не повреждение, простой платформы (прогрев не сбрасывается, WATCH_STALE нет), WATCH_STALE при
непрерывной работе без сторожа, две копии сервиса, promotion_ready по каждому условию (в том числе mode=auto без сторожа), протокол
восстановления: сигнал сервиса меток -> восстановление за один проход observe, повреждённый файл, таймаут подтверждений, оставшийся маркер;
enabled=false — ничего не мигрирует и не пишет. Временные корни со своим конфигом, фейковые часы, без сна и сети.
Часть 2 (задача 22, свойства 15 и 28): проход с настоящими шагами пишет только в state/ и runtime/, config/ (в том числе копии risk_profile,
risk_levels, exec_limits) не меняется, config/local не создаётся, торговые слои в процесс сервиса не загружаются, записей OWNER нет."""
import copy
import json
import os
import random
import shutil
import sqlite3
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb.core.filelock import pid_alive
from tb.knowledge import markers as KM
from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import clusters as CL
from tb.learnloop import conf as LC
from tb.learnloop import cycle as CY
from tb.learnloop import service as SV
from tb.learnloop import versions as V
from tb.logic.learn_cond import RULE_FEATURES
from tb.platform import services as S
from tb.platform import status as ST

H = 3600
D = 86400
T0 = 1790812800            # 2026-10-01T00:00:00Z
POLICY = {"id": "P2", "ver": 1, "name": "confirm_follow", "kinds": ["range_5m", "turnover_surge", "hot_enter", "oi_spike", "liq_cascade"],
          "side": "with_dir", "entry": "candle_confirm_limit", "enabled": True}

# свой конфиг тестов (не боевой config/learnloop.json)
CFG = {
    "schema": "tb.learnloop_cfg/1", "enabled": True, "allow_size_rules": False,
    "promotion": {"mode": "auto", "warmup_days": 7, "warmup_window_days": 10, "warmup_stuck_days": 21, "backup_max_age_h": 24,
                  "watch_fresh_h": 3, "step_error_max": 3, "loss_cap_wait_days": 14},
    "lease": {"lease_h": 6}, "restore": {"restore_ack_timeout_s": 60}, "days": {"day_min_work_h": 12, "observe_ok_share": 0.9},
    "blocklist": {"cooldown_days": 14}, "apply": {"event_max_age_s": 900, "feat_missing_max": 0.2},
    "stale_min": 120, "lock_wait_s": 20, "events_per_day": 500,
    "verify": {"policy": "P2", "menu_every_days": 7, "discovery_days": 14, "discovery_days_min": 7, "min_n_menu": 88, "holdout_days": 7,
               "min_n_hyp": 30, "alpha": 0.05, "feat_cover_min": 0.8, "labels_max_new_per_day": 3},
    "clusters": {"grace_min": 30, "cluster_close_max_h": 48},
    "trial": {"n_target": 150, "max_days": 30, "min_effect_r": 0.1, "max_trade_loss_share": 0.3, "max_parallel": 5, "wait_max_days": 7,
              "reach_factor": 0.8, "reach_lookback_days": 7, "overlap_share_max": 0.5},
    "rulebook": {"max_active_rules": 10},
    "watch": {"looks": [50, 100, 200], "weekly_window": 200, "alpha_watch": 0.05, "alpha_watch_weekly": 0.001, "rollback_delta_r": 0.1,
              "disable_trade_loss_share": 0.4, "disable_min_considered": 30, "starvation_window_days": 7},
    "intervals": {"observe_s": 900, "verdict_s": 3600, "watch_s": 3600, "reconcile_s": 3600, "hypref_s": 3600, "daily_after": "00:20Z"},
    "hypref": {"window_days": 14},
    "second_iteration": {"prefilter_signals": 5000, "prefilter_cases": 2000, "port": {"llm_job_ttl_s": 900, "claim_ttl_s": 120, "llm_wait_s": 600},
                         "brain_ref": {"min_n": 15, "min_diff_r": 0.5, "brain_window_days": 30},
                         "freeze": {"freeze_trade_share": 0.5, "freeze_signal_share": 0.8, "freeze_window_days": 7},
                         "violations": {"brain_hits_min": 30}},
}
assert LC.check(CFG) == [], LC.check(CFG)


def cfg(**over):
    c = copy.deepcopy(CFG)
    for k, v in over.items():
        c[k] = v
    return c


class Clock(object):
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += float(s)


def write_cfg(root, c):
    p = os.path.join(root, "config", "learnloop.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(c, f)


def dead_pid():
    for p in range(999990, 900000, -7):
        if not pid_alive(p):
            return p
    raise RuntimeError("нет свободного pid")


def ok_step(name, period, calls=None, code="OK", **kw):
    def fn(ctx):
        if calls is not None:
            calls.append((name, ctx.now))
        return {"code": code}
    return CY.Step(name, period, fn, **kw)


def light_steps(calls=None, watch=None):
    """Лёгкий реестр для долгих прогонов на фейковых часах: те же периоды, что у default_steps."""
    st = [ok_step("obs", "observe", calls, group="observe"), ok_step("d1", "daily", calls), ok_step("menu", "daily", calls, code="NO_MENU"),
          ok_step("verd", "hourly", calls, interval="verdict_s", group="verdict")]
    if watch is not None:
        st.append(CY.Step("watch", "hourly", watch, interval="watch_s", group="watch"))
    return st


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbcy_")
        self.addCleanup(shutil.rmtree, self.root, True)
        store2._STATUS_CACHE.clear()
        RV.clear_cache()

    def svc(self, c=None, steps=None, t=T0 + D + H, clock=None):
        clock = clock or Clock(t)
        s = SV.Service(self.root, copy.deepcopy(c or CFG), steps, policy=POLICY, clock=clock, sleep=clock.sleep, log=lambda m: None)
        self.addCleanup(s.close)
        return s, clock

    def ro(self):
        c = store.open_ro(self.root)
        self.addCleanup(c.close)
        return c

    def q(self, sql, *a):
        c = sqlite3.connect(store.db_path(self.root))
        try:
            return c.execute(sql, a).fetchall()
        finally:
            c.close()

    def run_for(self, s, clock, seconds, step=60):
        end = clock.t + seconds
        while clock.t < end:
            s.tick()
            clock.t += step


# ---------------------------------------------------------------- выключенный цикл

class TestDisabled(Base):
    def test_enabled_false_does_nothing(self):
        write_cfg(self.root, cfg(enabled=False))
        clock = Clock(T0 + D + H)
        s = SV.Service(self.root, clock=clock, sleep=clock.sleep, log=lambda m: None)
        self.addCleanup(s.close)
        for _ in range(5):
            s.tick()
            clock.t += 900
        self.assertFalse(os.path.exists(store.db_path(self.root)))           # база не создана и не открыта
        self.assertFalse(KM.has_v2(self.root))                               # миграции нет
        self.assertFalse(os.path.isdir(store2.backups_dir(self.root)))       # копий нет
        self.assertEqual(KM.writers(self.root), [])
        _a, hb = S.read_hb("learnloop", self.root, clock.t)
        self.assertEqual((hb["enabled"], hb["state"], hb["codes"]), (False, "disabled", []))
        lb = ST.learnloop_block(hb, clock.t)
        self.assertEqual((lb["state"], lb["color"], lb["reasons"]), ("off", "off", []))
        self.assertIn("выключен", ST.learnloop_line(lb))

    def test_broken_config_is_disabled_and_yellow(self):
        bad = cfg()
        bad["verify"]["min_n_menu"] = 10
        write_cfg(self.root, bad)
        s, clock = self.svc()
        s.fixed_cfg = False
        s.cfg, s.cfg_errors = LC.load(self.root)
        s.tick()
        self.assertFalse(os.path.exists(store.db_path(self.root)))
        _a, hb = S.read_hb("learnloop", self.root, clock.t)
        lb = ST.learnloop_block(hb, clock.t)
        self.assertEqual((lb["state"], lb["color"]), ("off", "yellow"))

    def test_status_block_in_platform_status(self):
        write_cfg(self.root, cfg(enabled=False))
        clock = Clock(T0 + D + H)
        s = SV.Service(self.root, clock=clock, sleep=clock.sleep, log=lambda m: None)
        self.addCleanup(s.close)
        s.tick()
        svc = {"state": "running", "pid": 1, "started_ts": clock.t - 50, "restarts": 0, "tries": 0, "hb_ok": True, "hb_age_s": 1.0}
        S.write_json(S.lock_file(self.root), {"pid": os.getpid(), "started": clock.t - 100, "profile": "paper", "observe": False,
                                              "services": ["exec", "learnloop"]})
        S.write_json(S.status_file(self.root), {"ts_epoch": clock.t, "running": True, "services": {"exec": dict(svc), "learnloop": dict(svc)}})
        st = ST.build_status(self.root, clock.t)
        self.assertEqual(st["learnloop"]["state"], "off")
        self.assertFalse([r for r in st["reasons"] if "цикл обучения" in r])
        self.assertIn("цикл обучения: выключен", ST.format_text(st))


# ---------------------------------------------------------------- старт, миграция, идемпотентность

class TestStartup(Base):
    def test_migrate_and_init_idempotent(self):
        c = store.open_rw(self.root)                   # база меток R1 уже есть
        c.close()
        s, clock = self.svc(steps=[])
        s.tick()
        self.assertEqual(s.state, "ok")
        self.assertTrue(KM.has_v2(self.root))
        self.assertIn(os.getpid(), KM.writers(self.root))
        self.assertEqual([b["reason"] for b in store2.list_backups(self.root)], ["premigrate"])
        s.close()
        s2, _ = self.svc(steps=[], clock=clock)
        clock.t += 60
        s2.tick()
        s2.tick()
        self.assertEqual(s2.state, "ok")
        self.assertEqual(self.q("SELECT ver, reason_code FROM rulebook_versions"), [("R-0001", "INIT")])
        self.assertEqual([b["reason"] for b in store2.list_backups(self.root)], ["premigrate"])       # повторной копии и миграции нет
        self.assertEqual(int(self.q("SELECT v FROM meta WHERE k='knowledge_v2_ver'")[0][0]), 1)

    def test_default_steps_on_empty_db(self):
        s, clock = self.svc()
        s.tick()
        rep = s.cycle.last_report
        self.assertEqual(rep["errors"], {}, rep)
        self.assertEqual(rep["ran"]["menu"], "NO_DATA")
        for n in ("register", "verdicts", "promote", "reconcile", "watch"):  # задачи 14–16: настоящие шаги
            self.assertEqual(rep["ran"][n], "OK")
        self.assertEqual(rep["ran"]["hypref"], "NO_DATA")                   # задача 36: настоящий шаг; во временном корне нет shadow_policies.json
        self.assertEqual(s.cycle.stubs(), [])
        self.assertTrue(s.cycle.watch_real())
        self.assertIn("daily", [b["reason"] for b in store2.list_backups(self.root)])
        self.assertEqual(self.q("SELECT step, code FROM learn_events WHERE step='menu'"), [("menu", "NO_DATA")])
        lease = RV.read_lease(self.root)                                     # пустой rulebook: проверка успешна, аренда записана
        self.assertEqual((lease["ts"], lease["rulebook_ver"]), (int(clock.t), "R-0001"))
        _a, hb = S.read_hb("learnloop", self.root, clock.t)
        self.assertEqual((hb["state"], hb["mode"]), ("ok", "auto"))
        self.assertFalse(hb["ready"]["ok"])
        self.assertNotIn("WATCH", hb["ready"]["failed"])                     # аренда свежа; не готово по прогреву
        self.assertIn("DAYS", hb["ready"]["failed"])
        lb = ST.learnloop_block(hb, clock.t)
        self.assertEqual(lb["color"], "green")
        self.assertNotIn("заглушки", ST.learnloop_line(lb))

    def test_second_pass_same_time_adds_nothing(self):
        """Свойство 14: повтор прохода на тех же данных не создаёт строк, версий и переходов."""
        s, clock = self.svc()
        s.tick()
        tabs = ("cluster_runs", "lesson_candidates", "rule_rows", "rulebook_versions", "trials", "blocklist_rows", "rule_evals", "learn_events")
        before = dict((t, self.q("SELECT COUNT(*) FROM %s" % t)[0][0]) for t in tabs)
        for _ in range(3):
            s.cycle.last.clear()                        # снова срок у шагов observe и hourly, суточные уже выполнены сегодня
            s.cycle.run_pass(s.conn, clock.t)
        after = dict((t, self.q("SELECT COUNT(*) FROM %s" % t)[0][0]) for t in tabs)
        self.assertEqual(before, after)

    def test_two_copies_lock(self):
        self.assertTrue(SV.acquire_lock(self.root))
        self.addCleanup(SV.release_lock, self.root)
        self.assertFalse(SV.acquire_lock(self.root))                  # вторая копия при живом владельце
        with open(SV.lock_path(self.root), "w") as f:
            f.write(str(dead_pid()))
        self.assertTrue(SV.acquire_lock(self.root))                   # замок мёртвого процесса снимается

    def test_scrub_env(self):
        env = {"BYBIT_API_KEY": "x", "DEEPSEEK_API_KEY": "y", "OPENAI_X": "z", "MY_TOKEN": "t", "PATH": "p", "TB_ROOT": "r"}
        self.assertEqual(SV.scrub_env(env), 4)
        self.assertEqual(sorted(env), ["PATH", "TB_ROOT"])


# ---------------------------------------------------------------- ошибки шагов

class TestStepErrors(Base):
    def test_step_error_does_not_stop_pass(self):
        calls = []

        def boom(ctx):
            raise ValueError("сломалось")

        def integ(ctx):
            ctx.conn.execute("INSERT INTO rule_evals(rec_seq, rule_id, ts, no_event, hit, evaluated, missing) VALUES(1, '', 1, 0, 0, 0, 0)")
            ctx.conn.execute("INSERT INTO rule_evals(rec_seq, rule_id, ts, no_event, hit, evaluated, missing) VALUES(1, '', 1, 0, 0, 0, 0)")

        steps = [CY.Step("bad", "observe", boom, group="observe"), CY.Step("uniq", "observe", integ, group="observe"),
                 ok_step("good", "observe", calls, group="observe")]
        s, clock = self.svc(steps=steps)
        s.tick()
        rep = s.cycle.last_report
        self.assertEqual(rep["errors"], {"bad": "STEP_ERROR", "uniq": "STEP_ERROR"})        # IntegrityError (UNIQUE) — обычная ошибка шага
        self.assertEqual(rep["ran"], {"good": "OK"})
        self.assertEqual(len(calls), 1)
        self.assertFalse(KM.restoring(self.root))
        self.assertFalse(os.path.exists(KM.restore_failed_path(self.root)))
        self.assertEqual(s.restores, 0)
        rows = self.q("SELECT step, code FROM learn_events WHERE code='STEP_ERROR' ORDER BY seq")
        self.assertEqual(rows, [("bad", "STEP_ERROR"), ("uniq", "STEP_ERROR")])
        self.assertIsNone(s.cycle.last_ok_ts)

    def test_series_pauses_promotion(self):
        def boom(ctx):
            raise RuntimeError("x")
        s, clock = self.svc(steps=[CY.Step("obs", "observe", boom, group="observe")])
        for _ in range(3):
            s.tick()
            clock.t += 900
        st = s.cycle.state["steps"]["obs"]
        self.assertEqual(st["err_run"], 3)
        self.assertIn("STEP_ERRORS", V.promotion_ready(s.conn, CFG, clock.t, self.root, s.cycle.state, check_db=False)["failed"])

    def test_daily_error_retried_after_hour(self):
        n = {"k": 0}

        def flaky(ctx):
            n["k"] += 1
            if n["k"] == 1:
                raise RuntimeError("x")
            return {"code": "OK"}
        s, clock = self.svc(steps=[CY.Step("dd", "daily", flaky), ok_step("obs", "observe", group="observe")])
        s.tick()
        self.assertEqual(s.cycle.acc["daily"]["dd"], CY.ERROR)
        clock.t += 900
        s.tick()
        self.assertEqual(n["k"], 1)                     # не чаще раза в час
        clock.t += H
        s.tick()
        self.assertEqual((n["k"], s.cycle.acc["daily"]["dd"]), (2, "OK"))


# ---------------------------------------------------------------- учёт суток (свойство 27)

def acc_of(work_h, ok_h, daily=None):
    acc = CY.new_acc("2026-10-02")
    acc["work_s"] = work_h * 3600.0
    acc["hw"] = dict((str(h), 3600.0) for h in range(work_h))
    acc["oh"] = [str(h) for h in range(ok_h)]
    acc["daily"] = daily if daily is not None else {"d1": "OK", "menu": "NO_MENU"}
    return acc


class TestDays(unittest.TestCase):
    def test_short_day_not_counted(self):
        d = CY.summarize_day(acc_of(11, 11), CFG, ["d1", "menu"])
        self.assertEqual((d["counted"], d["full"]), (False, False))

    def test_observe_share_below_90(self):
        d = CY.summarize_day(acc_of(13, 11), CFG, ["d1", "menu"])          # 11/13 = 0.846
        self.assertEqual((d["counted"], d["full"]), (True, False))
        d = CY.summarize_day(acc_of(20, 18), CFG, ["d1", "menu"])          # 0.9
        self.assertEqual((d["counted"], d["full"]), (True, True))

    def test_daily_steps(self):
        self.assertTrue(CY.summarize_day(acc_of(13, 13, {"d1": "STUB", "menu": "NOT_DUE"}), CFG, ["d1", "menu"])["full"])
        self.assertFalse(CY.summarize_day(acc_of(13, 13, {"d1": "ERROR", "menu": "MENU"}), CFG, ["d1", "menu"])["full"])
        self.assertEqual(CY.summarize_day(acc_of(13, 13, {"d1": "OK"}), CFG, ["d1", "menu"])["missing"], ["menu"])

    def test_short_hours_not_worked(self):
        acc = acc_of(13, 13)
        acc["hw"]["20"] = 600.0                          # 10 минут работы в часе — не час работы
        self.assertTrue(CY.summarize_day(acc, CFG, ["d1", "menu"])["full"])


class TestDaysService(Base):
    def days(self):
        return dict((d["day"], d) for d in V.day_rows(self.ro()))

    def lease_writer(self):
        def watch(ctx):
            KM.write_json(RV.lease_path(ctx.root), {"ts": ctx.now})
            return {"code": "OK"}
        return watch

    def test_downtime_keeps_counting_no_watch_stale(self):
        """Платформа выключена на ночь: сутки по 14 ч работы засчитываются, перерыв не засчитывается и не сбрасывает прогрев, WATCH_STALE нет."""
        clock = Clock(T0 + D + 6 * H)                      # 02.10 06:00
        for day in range(3):
            s, _ = self.svc(steps=light_steps(watch=self.lease_writer()), clock=clock)
            self.run_for(s, clock, 14 * H)
            s.close()
            clock.t = T0 + (day + 2) * D + 6 * H           # следующий день 06:00 (10 часов простоя)
        s, _ = self.svc(steps=light_steps(watch=self.lease_writer()), clock=clock)
        s.tick()                                          # закрывает 04.10
        days = self.days()
        self.assertEqual(sorted(days), ["2026-10-02", "2026-10-03", "2026-10-04"])
        for d in days.values():
            self.assertTrue(d["counted"] and d["full"], d)
            self.assertAlmostEqual(d["work_h"], 14.0, delta=0.1)
        self.assertEqual(self.q("SELECT COUNT(*) FROM learn_events WHERE code='WATCH_STALE'")[0][0], 0)
        self.assertNotIn("WATCH_STALE", s.cycle.codes)

    def test_short_day_neutral(self):
        clock = Clock(T0 + D + 6 * H)
        s, _ = self.svc(steps=light_steps(watch=self.lease_writer()), clock=clock)
        self.run_for(s, clock, 10 * H)
        s.close()
        clock.t = T0 + 2 * D + 6 * H
        s, _ = self.svc(steps=light_steps(watch=self.lease_writer()), clock=clock)
        s.tick()
        d = self.days()["2026-10-02"]
        self.assertEqual((d["counted"], d["full"]), (False, False))
        rd = V.promotion_ready(s.conn, CFG, clock.t, self.root, check_db=False)
        self.assertEqual(rd["details"]["days"]["counted"], 0)

    def test_watch_stale_after_lease_h_without_watch(self):
        def no_lease(ctx):
            return {"code": "OK"}
        clock = Clock(T0 + D + 6 * H)
        s, _ = self.svc(steps=light_steps(watch=no_lease), clock=clock)
        self.run_for(s, clock, 5 * H, step=300)
        self.assertNotIn("WATCH_STALE", s.cycle.codes)       # меньше lease_h после старта — не ошибка
        self.run_for(s, clock, 2 * H, step=300)
        self.assertIn("WATCH_STALE", s.cycle.codes)
        self.run_for(s, clock, 2 * H, step=300)
        self.assertEqual(self.q("SELECT COUNT(*) FROM learn_events WHERE code='WATCH_STALE'")[0][0], 1)    # одна запись на эпизод
        _a, hb = S.read_hb("learnloop", self.root, clock.t)
        self.assertEqual(ST.learnloop_block(hb, clock.t)["color"], "red")
        s.close()
        s2, _ = self.svc(steps=light_steps(watch=no_lease), clock=clock)
        s2.tick()
        self.assertNotIn("WATCH_STALE", s2.cycle.codes)       # перезапуск: отсчёт непрерывной работы заново

    def test_stub_watch_never_stale(self):
        clock = Clock(T0 + D + 6 * H)
        s, _ = self.svc(steps=light_steps() + [CY.Step("watch", "hourly", CY.stub(16), interval="watch_s", group="watch", stub=True)], clock=clock)
        self.run_for(s, clock, 8 * H, step=600)
        self.assertNotIn("WATCH_STALE", s.cycle.codes)


# ---------------------------------------------------------------- расписание: меню и holdout

def cr_rows(t_from, t_to, seed=1):
    rnd = random.Random(seed)
    out = []
    for t in range(t_from, t_to, H):
        for side in ("long", "short"):
            key = "K-%d-%s" % (t, side)
            feats = dict((f, None) for f in RULE_FEATURES)
            feats.update(range_5m_pct=round(rnd.uniform(0.5, 3), 3), ret_5m_pct=round(rnd.uniform(-1, 1), 3), turn_surge_5m=round(rnd.uniform(1, 6), 3),
                         chg_24h_pct=round(rnd.uniform(-5, 5), 3), spread_bps=round(rnd.uniform(1, 8), 3), funding_pct=round(rnd.uniform(-0.01, 0.01), 5),
                         coin_class="alt_mid", side=side, utc_session="eu")
            out.append({"cluster": CL.cluster_id(t // H, side), "hour": t // H, "side": side, "key": key, "t0": t + 60, "type": "range_5m",
                        "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1", "env_norm": "mainnet", "filled": 1, "r_net": round(rnd.gauss(0, 1), 3),
                        "feats": feats, "flags": None, "closed_ts": t + 2 * H})
    return out


class TestSchedule(Base):
    def setUp(self):
        Base.setUp(self)
        self.c = store.open_rw(self.root)
        self.addCleanup(self.c.close)
        store2.migrate_v2a(self.c, self.root, explicit=True, backup=False, now=T0)
        store2.insert_rows(self.c, "cluster_runs", cr_rows(T0, T0 + 12 * D))

    def test_first_menu_on_day_9(self):
        seen = {}

        def menu(ctx):
            r = CY.step_menu(ctx)
            seen[(ctx.now - T0) // D] = r
            return r
        cy = CY.Cycle(self.root, copy.deepcopy(CFG), steps=[CY.Step("menu", "daily", menu)], policy=POLICY)
        for d in range(11):
            cy.run_pass(self.c, T0 + d * D + 10 * 60)          # 00:10 — раньше daily_after: суточные шаги не идут
            cy.run_pass(self.c, T0 + d * D + 30 * 60)
        for d in range(9):
            self.assertIn(seen[d]["code"], ("NO_DATA", "NO_MENU"), d)          # до b > T0 данных окна нет, затем окно короче discovery_days_min
            self.assertFalse(seen[d]["window"], d)
        self.assertEqual(seen[8]["code"], "NO_MENU")
        self.assertTrue(seen[9]["window"], seen[9])
        self.assertEqual(sorted(seen), list(range(11)))          # раз в сутки, после 00:20Z

    def test_holdout_only_after_cut_ts(self):
        win = CL.window(self.c, "P2", 1, "c1", "mainnet", T0, T0 + D, CFG, T0 + 10 * D)
        hs, he = T0 + 2 * D, T0 + 3 * D
        cond = [{"feature": "spread_bps", "op": ">=", "value": 6.0}]
        store2.insert_rows(self.c, "lesson_candidates", [{
            "menu_id": "M-test", "src": "code", "cond": cond, "claim": {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.2},
            "env_norm": "mainnet", "ctx_ver": "c1", "k_space": 1, "p_screen": 0.001, "status": "holdout", "code": None, "holdout_start": hs,
            "holdout_end": he, "evidence": {"disc": CL.sample(win)}, "lesson_id": None, "ts": hs}])
        cut = CL.cut_ts(he, CFG)
        cy = CY.Cycle(self.root, copy.deepcopy(CFG), steps=[CY.Step("holdouts", "daily", CY.step_holdouts)], policy=POLICY)
        status = {}
        for d in range(3, 7):
            now = T0 + d * D + 30 * 60
            cy.run_pass(self.c, now)
            status[d] = (now >= cut, self.c.execute("SELECT status FROM lesson_candidates").fetchone()[0])
        for d, (after, st) in status.items():
            self.assertEqual(st != "holdout", after, (d, st, cut))
        self.assertTrue(any(a for a, _ in status.values()))


# ---------------------------------------------------------------- promotion_ready

class TestPromotionReady(Base):
    NOW = T0 + 20 * D + 12 * H

    def setUp(self):
        Base.setUp(self)
        self.c = store.open_rw(self.root)
        self.addCleanup(self.c.close)
        store2.migrate_v2a(self.c, self.root, explicit=True, backup=False, now=T0)
        V.init_rulebook(self.c, T0)

    def day(self, i, counted=True, full=True, work_h=24):
        d = CY.day_of(T0 + i * D)
        store2.log_event(self.c, V.DAY_STEP, "DAY", "code", ref=d, body={"counted": counted, "full": full, "work_s": work_h * 3600}, now=T0 + (i + 1) * D)

    def good(self):
        for i in range(10, 20):
            self.day(i)
        KM.write_json(RV.lease_path(self.root), {"ts": self.NOW - H})
        store2.make_backup(self.root, "daily", now=self.NOW - 2 * H)
        V.meta_put(self.c, V.META_DAY, dict(CY.new_acc(CY.day_of(self.NOW)), work_s=12 * 3600.0))

    def ready(self, **kw):
        return V.promotion_ready(self.c, CFG, self.NOW, root=self.root, **kw)

    def test_all_ok(self):
        self.good()
        r = self.ready()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["details"]["integrity"], "ok")

    def test_days(self):
        for i in range(10, 16):
            self.day(i)                                   # 6 полных
        for i in range(16, 20):
            self.day(i, full=False)
        KM.write_json(RV.lease_path(self.root), {"ts": self.NOW - H})
        store2.make_backup(self.root, "daily", now=self.NOW - 2 * H)
        self.assertEqual(self.ready()["failed"], ["DAYS"])

    def test_short_days_are_neutral(self):
        self.good()
        for i in range(0, 10):
            self.day(i, counted=False, full=False, work_h=5)
        self.day(5, counted=False, full=False, work_h=5)
        self.assertTrue(self.ready()["ok"])

    def test_bad_events_window(self):
        self.good()
        store2.log_event(self.c, "x", "CHAIN_BROKEN", "code", error=True, now=T0 + 11 * D)       # старше 7 x 24 ч работы — не мешает
        self.assertTrue(self.ready()["ok"])
        for code in ("CHAIN_BROKEN", "DB_CORRUPT", "WATCH_STALE"):
            seq = store2.log_event(self.c, "x", code, "code", error=True, now=self.NOW - 3 * D)
            self.assertIn("BAD_EVENTS", self.ready()["failed"], code)
            self.assertIsNotNone(seq)
            break

    def test_watch(self):
        self.good()
        os.remove(RV.lease_path(self.root))
        r = self.ready()
        self.assertEqual((r["failed"], r["details"]["watch"]["why"]), (["WATCH"], "NO_LEASE"))
        KM.write_json(RV.lease_path(self.root), {"ts": self.NOW - 4 * H})
        r = self.ready()
        self.assertEqual((r["failed"], r["details"]["watch"]["why"]), (["WATCH"], "WATCH_STALE"))

    def test_mode_auto_without_watch_never_ready(self):
        """Решение владельца 6: mode=auto, сторожа никогда не было (заглушка задачи 16) -> не готово."""
        self.good()
        os.remove(RV.lease_path(self.root))
        self.assertEqual(LC.promotion_mode(CFG), "auto")
        r = self.ready()
        self.assertFalse(r["ok"])
        self.assertIn("WATCH", r["failed"])
        self.assertEqual(r["details"]["watch"]["why"], "NO_LEASE")

    def test_step_errors(self):
        self.good()
        for st, bad in (({"steps": {"v": {"group": "verdict", "err_run": 3}}}, True),
                        ({"steps": {"v": {"group": "watch", "err_run": 0, "series_ts": self.NOW - 23 * H}}}, True),
                        ({"steps": {"v": {"group": "observe", "err_run": 0, "series_ts": self.NOW - 25 * H}}}, False),
                        ({"steps": {"v": {"group": None, "err_run": 9}}}, False)):
            self.assertEqual("STEP_ERRORS" in self.ready(state=st)["failed"], bad, st)

    def test_backup(self):
        self.good()
        for b in store2.list_backups(self.root):
            os.remove(b["path"])
        self.assertEqual(self.ready()["failed"], ["BACKUP"])
        store2.make_backup(self.root, "daily", now=self.NOW - 25 * H)
        self.assertEqual(self.ready()["failed"], ["BACKUP"])

    def test_integrity(self):
        self.good()
        real = self.c

        class Bad(object):
            def execute(self, sql, *a):
                if "integrity_check" in sql:
                    return real.execute("SELECT 'row 1 missing from index'")
                return real.execute(sql, *a)
        r = V.promotion_ready(Bad(), CFG, self.NOW, root=self.root, state={})
        self.assertEqual(r["failed"], ["INTEGRITY"])

    def test_warmup_stuck(self):
        cy = CY.Cycle(self.root, copy.deepcopy(CFG), steps=[], policy=POLICY)
        cy.load(self.c, self.NOW)
        cy.state["started_ts"] = self.NOW - 22 * D
        cy.update_status(self.c, self.NOW)
        self.assertIn("WARMUP_STUCK", cy.codes)
        cy.state["started_ts"] = self.NOW - 20 * D
        cy.update_status(self.c, self.NOW)
        self.assertNotIn("WARMUP_STUCK", cy.codes)


# ---------------------------------------------------------------- восстановление

class TestRecovery(Base):
    def started(self):
        s, clock = self.svc(steps=[ok_step("obs", "observe", group="observe"), CY.Step("backup", "daily", CY.step_backup)], t=T0 + D + 600)
        s.tick()                                          # 00:10: миграция (копия premigrate), суточные шаги ещё не идут
        clock.t += 3000
        s.tick()                                          # 01:00: суточная копия
        self.assertEqual(s.state, "ok")
        self.assertIn("daily", [b["reason"] for b in store2.list_backups(self.root)])
        return s, clock

    def test_labels_signal_restored_in_one_observe_pass(self):
        s, clock = self.started()
        KM.write_corrupt(self.root, "DB_CORRUPT", where="label_step", now=clock.t)
        clock.t += 900
        s.tick()
        self.assertEqual((s.restores, s.state), (1, "ok"))
        self.assertIsNone(KM.read_corrupt(self.root))
        self.assertFalse(KM.restoring(self.root))
        codes = [r[0] for r in self.q("SELECT code FROM learn_events WHERE code IN ('AUTO_RESTORED', 'DB_CORRUPT') ORDER BY seq")]
        self.assertEqual(sorted(codes), ["AUTO_RESTORED", "DB_CORRUPT"])
        clock.t += 900
        s.tick()
        self.assertIn("AUTO_RESTORED", s.cycle.codes)
        _a, hb = S.read_hb("learnloop", self.root, clock.t)
        self.assertEqual(ST.learnloop_block(hb, clock.t)["color"], "yellow")

    def test_corrupt_file_restored_by_itself(self):
        s, clock = self.started()
        s._close()
        p = store.db_path(self.root)
        for suf in ("-wal", "-shm"):
            KM.remove(p + suf)
        with open(p, "r+b") as f:
            f.write(b"\x00" * 512)
        clock.t += 900
        s.tick()
        self.assertEqual((s.restores, s.state), (1, "ok"))
        self.assertTrue([n for n in os.listdir(os.path.dirname(p)) if n.startswith("knowledge.corrupt-")])
        self.assertEqual(self.q("SELECT ver FROM rulebook_versions"), [("R-0001",)])

    def test_corruption_in_step(self):
        n = {"k": 0}

        def bad(ctx):
            n["k"] += 1
            if n["k"] == 1:
                raise sqlite3.DatabaseError("database disk image is malformed")
            return {"code": "OK"}
        s, clock = self.svc(steps=[CY.Step("backup", "daily", CY.step_backup), CY.Step("obs", "observe", bad, group="observe")])
        s.tick()
        self.assertEqual((s.restores, s.state), (1, "ok"))

    def test_ack_timeout_red(self):
        s, clock = self.started()
        KM.register_writer(self.root, "labels", pid=os.getppid())          # живой писатель, который не подтверждает
        KM.write_corrupt(self.root, "DB_CORRUPT", where="capture_step", now=clock.t)
        clock.t += 900
        s.tick()
        self.assertEqual(s.state, "restore_failed")
        self.assertFalse(KM.restoring(self.root))
        self.assertTrue(os.path.exists(KM.restore_failed_path(self.root)))
        _a, hb = S.read_hb("learnloop", self.root, clock.t)
        self.assertIn("DB_CORRUPT", hb["codes"])
        self.assertEqual(ST.learnloop_block(hb, clock.t)["color"], "red")
        clock.t += 900
        s.tick()
        self.assertEqual((s.state, s.restores), ("restore_failed", 0))     # без повторов до ручного restore
        KM.unregister_writer(self.root, pid=os.getppid())

    def test_stale_marker_completed_on_start(self):
        s, clock = self.started()
        s.close()
        KM.write_json(KM.restoring_path(self.root), {"pid": dead_pid(), "ts": int(clock.t), "mode": "manual"})
        s2, _ = self.svc(steps=[ok_step("obs", "observe", group="observe")], clock=clock)
        s2.tick()
        self.assertFalse(KM.restoring(self.root))
        self.assertEqual(s2.state, "ok")

    def test_foreign_restore_pauses_and_acks(self):
        s, clock = self.started()
        KM.write_json(KM.restoring_path(self.root), {"pid": os.getppid(), "ts": int(clock.t), "mode": "manual"})
        clock.t += 60
        s.tick()
        self.assertEqual((s.state, s.conn), ("restoring", None))
        self.assertTrue(KM.has_ack(self.root))
        KM.remove(KM.restoring_path(self.root))
        clock.t += 60
        s.tick()
        self.assertEqual(s.state, "ok")
        self.assertFalse(KM.has_ack(self.root))


# ---------------------------------------------------------------- часть 2 (задача 22): границы полномочий цикла (свойства 15 и 28)

GUARDED = ("risk_profile.json", "risk_levels.json", "exec_limits.json")
TRADING = ("tb.brain", "tb.gateway", "tb.mcp", "tb.venues", "tb.executor", "tb.control", "tb.launcher", "tb.core.secretfile", "tb.llm.keys",
           "tb.llm.providers", "tb.knowledge.learn_cli", "tb.knowledge.rulebook_cli")


def _tree(top):
    import hashlib
    out = {}
    for dp, _dn, fn in os.walk(top):
        for n in fn:
            p = os.path.join(dp, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, top)] = hashlib.sha256(f.read()).hexdigest()
    return out


class TestBoundaries(Base):
    """Свойство 15: проход цикла с настоящими шагами не меняет config/ (risk_profile, risk_levels, exec_limits, learnloop.json), не создаёт
    config/local и пишет только в state/ и runtime/ корня; торговые слои не загружаются. Свойство 28 (частично; полный путь — test_learnloop_e2e):
    действия цикла записаны actor=code, OWNER нет."""

    def setUp(self):
        Base.setUp(self)
        cdir = os.path.join(self.root, "config")
        os.makedirs(cdir)
        for n in GUARDED + ("shadow_policies.json", "memory.json"):
            shutil.copyfile(os.path.join(_bootstrap.ROOT, "config", n), os.path.join(cdir, n))
        write_cfg(self.root, CFG)
        c = store.open_rw(self.root)
        try:
            store2.migrate_v2a(c, self.root, explicit=True, backup=False, now=T0)
            store2.insert_rows(c, "cluster_runs", cr_rows(T0, T0 + 12 * D))
        finally:
            c.close()

    def test_cycle_writes_only_state_and_runtime(self):
        before = _tree(self.root)
        cfg_before = _tree(os.path.join(self.root, "config"))
        s, clock = self.svc(t=T0 + 10 * D + 600)                # настоящие шаги default_steps: меню, holdout, испытания, сторож, справка
        self.run_for(s, clock, 2 * D, step=H)
        self.assertEqual(s.cycle.stubs(), [])
        self.assertEqual(s.state, "ok")
        self.assertTrue(self.q("SELECT 1 FROM learn_events WHERE step='menu' AND code IN ('MENU', 'NO_MENU')"))    # настоящий шаг меню по окну
        after = _tree(self.root)
        changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
        self.assertTrue(changed)
        self.assertEqual([p for p in changed if p.split(os.sep)[0] not in ("state", "runtime")], [])
        self.assertEqual(_tree(os.path.join(self.root, "config")), cfg_before)
        self.assertFalse(os.path.exists(os.path.join(self.root, "config", "local")))
        self.assertEqual(set(r[0] for r in self.q("SELECT DISTINCT actor FROM learn_events")), {"code"})
        self.assertEqual(set(r[0] for r in self.q("SELECT DISTINCT actor FROM blocklist_rows")) - {"code"}, set())

    def test_trading_layers_not_loaded(self):
        """Requirement 8.7: процесс сервиса с настоящими шагами не загружает торговые слои, ключи и CLI владельца (отдельный процесс)."""
        import subprocess
        import sys
        code = ("import json, sys\n"
                "sys.path.insert(0, %r)\n"
                "from tb.learnloop import service as SV\n"
                "class C(object):\n"
                "    t = %d\n"
                "    def __call__(self):\n        return self.t\n"
                "    def sleep(self, s):\n        self.t += s\n"
                "c = C()\n"
                "s = SV.Service(%r, clock=c, sleep=c.sleep, log=lambda m: None)\n"
                "for _ in range(30):\n    s.tick()\n    c.t += 900\n"
                "s.close()\n"
                "print(json.dumps({'state': s.state, 'ran': sorted((s.cycle.last_report or {}).get('ran') or {}), "
                "'mods': sorted(m for m in sys.modules if m.startswith('tb.'))}))\n") % (_bootstrap.SRC, T0 + 8 * D + 600, self.root)
        env = dict(os.environ, TB_ROOT=self.root)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        rep = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(rep["state"], "ok")
        self.assertIn("tb.learnloop.watch", rep["mods"])
        self.assertIn("tb.learnloop.trial", rep["mods"])
        self.assertEqual([m for m in rep["mods"] if any(m == p or m.startswith(p + ".") for p in TRADING)], [])
        self.assertEqual(set(r[0] for r in self.q("SELECT DISTINCT actor FROM learn_events")), {"code"})


if __name__ == "__main__":
    unittest.main()
