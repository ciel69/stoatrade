"""Сквозной тест полной автономии цикла обучения R2 (задача 22; Requirement 15.7, 8.4, 8.7, 8.8, 16.5; свойства 15 и 28 design.md).

Временный корень: свой config/learnloop.json (enabled=true, promotion.mode="auto", окна уменьшены параметрами конфига при тех же формулах:
min_n_menu сверяет загрузчик), копии поставляемых config/shadow_policies.json, memory.json, hypotheses.json и охраняемых risk_profile.json,
risk_levels.json, exec_limits.json. Фейковые часы, тик раз в час (observe_s = 3600), сервис learnloop с настоящими шагами default_steps, без сна
и сети. Синтетический рынок: каждый час по 5 сигналов mainnet и 2 сигнала testnet на сторону размечаются и попадают в базу меток
(signals + policy_runs P2), дальше всё делает цикл: закрытие кластеров -> меню -> holdout -> урок hyp -> регистрация испытания -> вердикт promote
-> прогрев (сутки, аренда сторожа) -> продвижение (версия R-0002, правило active, rulebook_view.active применяет) -> подмена будущего эффекта
на положительный -> откат сторожем (ROLLBACK, R-0003). Ни одной записи OWNER. Закономерность: у прогонов с spread_bps >= 8 среднее R на 0.5
ниже (mainnet); у testnet закономерность обратная — режимы данных не смешиваются (все выборки по env_norm=mainnet). Без закономерности
правил нет. Хеши охраняемых конфигов до и после; запись в config/ (в том числе config/local) проверяется по путям всех записей процесса.
"""
import builtins
import copy
import hashlib
import json
import os
import random
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import clusters as CL
from tb.learnloop import conf as LC
from tb.learnloop import service as SV
from tb.learnloop import verify as VF
from tb.logic import learn_rules as LR
from tb.memory import lessons as LS
from test_learnloop_clusters import _RUN, _SIG
from test_learnloop_cycle import CFG as CYCLE_CFG
from test_learnloop_cycle import Clock

H = 3600
D = 86400
T0 = 1790812800                                   # 2026-10-01T00:00:00Z
REPO = _bootstrap.ROOT
COPIED = ("shadow_policies.json", "memory.json", "hypotheses.json")
GUARDED = ("risk_profile.json", "risk_levels.json", "exec_limits.json")
SYMS = (("QAUSDT", 300.0), ("QBUSDT", 250.0), ("QCUSDT", 50.0), ("QDUSDT", 60.0), ("QEUSDT", 80.0), ("QFUSDT", 40.0), ("QGUSDT", 120.0),
        ("QHUSDT", 30.0), ("QIUSDT", 5.0), ("QJUSDT", 8.0))
HIT_SPREAD = 8.0                                  # закономерность: spread_bps >= 8 (≈ q80 равномерного 1..10)
BASE_MU, SD = 0.05, 0.5                          # sd прогона: при n_target=40 мощность испытания (alpha_adj=0.01) ≈ 1
NEG_MU, POS_MU = -0.45, 0.6                      # эффект ≈ -0.5R; после подмены — +0.55R к базе (откат)


def e2e_cfg():
    """Конфиг цикла с уменьшенными окнами. Формулы те же: min_n_menu = ceil(n_target / (reach_factor × max_days) × discovery_days)."""
    c = copy.deepcopy(CYCLE_CFG)
    c["enabled"] = True
    c["promotion"]["mode"] = "auto"
    c["clusters"].update(grace_min=0, cluster_close_max_h=1)          # cut_ts(b) = b + 1 ч + observe_s
    c["intervals"]["observe_s"] = 3600                              # тик раз в час засчитывается работой (промежуток <= 2 x observe_s)
    c["verify"].update(discovery_days=3, discovery_days_min=3, holdout_days=2)
    c["trial"].update(n_target=40, max_days=5, reach_lookback_days=2)
    c["hypref"]["window_days"] = 3
    t, v = c["trial"], c["verify"]
    v["min_n_menu"] = LC.min_n_menu_formula(t["n_target"], t["reach_factor"], t["max_days"], v["discovery_days"])
    return c


CFG = e2e_cfg()
assert LC.check(CFG) == [], LC.check(CFG)
assert CFG["verify"]["min_n_menu"] == 30
assert CFG["promotion"]["warmup_days"] == 7 and CFG["watch"]["looks"] == [50, 100, 200]       # прогрев и точки сторожа — как в поставке


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def tree(top):
    """{относительный путь: sha256} всех файлов каталога."""
    out = {}
    for dp, _dn, fn in os.walk(top):
        for n in fn:
            p = os.path.join(dp, n)
            out[os.path.relpath(p, top)] = sha(p)
    return out


class Market(object):
    """Синтетическая разметка: часы с концом <= now попадают в signals/policy_runs (status=done). hit_mu — среднее R прогонов mainnet с
    spread_bps >= HIT_SPREAD (None — без закономерности); у testnet закономерность обратная (POS_MU) всегда."""

    def __init__(self, root, hit_mu, seed, n_main=5, n_test=2):
        self.root, self.hit_mu, self.seed = root, hit_mu, seed
        self.n_main, self.n_test = n_main, n_test
        self.next_h = T0 // H

    def rows(self, hour):
        rnd = random.Random(self.seed * 1000003 + hour)
        sig, run = [], []
        for env, n in (("mainnet", self.n_main), ("testnet", self.n_test)):
            for side in ("long", "short"):
                for i in range(n):
                    sym, turn = SYMS[rnd.randrange(len(SYMS))]
                    t0 = hour * H + 60 + i * 500 + (0 if side == "long" else 200) + (30 if env == "testnet" else 0)
                    ev = {"range_5m_pct": round(rnd.uniform(0.3, 3.0), 4), "ret_5m_pct": round(rnd.uniform(-1, 1), 4),
                          "turn_surge_5m": round(rnd.uniform(1, 6), 4), "chg_24h_pct": round(rnd.uniform(-5, 5), 4),
                          "spread_bps": round(rnd.uniform(1, 10), 4), "funding_pct": round(rnd.uniform(-0.01, 0.01), 6), "turn_24h_musd": turn}
                    hit = ev["spread_bps"] >= HIT_SPREAD
                    if env == "testnet":
                        mu = POS_MU if hit else BASE_MU
                    else:
                        mu = self.hit_mu if (hit and self.hit_mu is not None) else BASE_MU
                    filled = rnd.random() >= 0.05
                    r = round(rnd.gauss(mu, SD), 4) if filled else None
                    key = "S-%d-%s-%s-%d-%s" % (t0, env[0], side[0], i, sym)
                    lab = (hour + 1) * H + 300
                    sig.append((key, t0, sym, "range_5m", side, 1, env, json.dumps(ev), "done", lab, lab, t0 + 30, lab))
                    run.append((key, "P2", 1, "g1", "c1", t0, sym, "range_5m", side, 1 if filled else 0, r, None))
        return sig, run

    def feed(self, now):
        sig, run = [], []
        while (self.next_h + 1) * H + 300 <= now:
            s, r = self.rows(self.next_h)
            sig.extend(s)
            run.extend(r)
            self.next_h += 1
        if not sig:
            return 0
        c = sqlite3.connect(store.db_path(self.root), timeout=30)
        try:
            with c:
                c.executemany(_SIG, sig)
                c.executemany(_RUN, run)
        finally:
            c.close()
        return len(sig)


class WriteLog(object):
    """Пути всех записей процесса через open (w/a/x/+), os.open с флагами записи, os.replace/os.rename (назначение) — за время прогона цикла."""

    def __init__(self):
        self.paths = []

    def _rec(self, p):
        try:
            self.paths.append(os.path.abspath(os.fspath(p)))
        except TypeError:
            pass

    def patches(self):
        real_open, real_os_open, real_replace, real_rename = builtins.open, os.open, os.replace, os.rename
        log = self

        def open_(file, mode="r", *a, **kw):
            if isinstance(file, (str, bytes, os.PathLike)) and any(ch in str(mode) for ch in "wax+"):
                log._rec(file)
            return real_open(file, mode, *a, **kw)

        def os_open(path, flags, *a, **kw):
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):
                log._rec(path)
            return real_os_open(path, flags, *a, **kw)

        def replace(src, dst, *a, **kw):
            log._rec(dst)
            return real_replace(src, dst, *a, **kw)

        def rename(src, dst, *a, **kw):
            log._rec(dst)
            return real_rename(src, dst, *a, **kw)
        return [mock.patch("builtins.open", open_), mock.patch("os.open", os_open), mock.patch("os.replace", replace), mock.patch("os.rename", rename)]

    def under(self, top):
        top = os.path.abspath(top)
        return sorted(set(p for p in self.paths if p == top or p.startswith(top + os.sep)))


class Base(unittest.TestCase):
    SEED = 7

    def setUp(self):
        self.t_start = time.time()
        self.root = tempfile.mkdtemp(prefix="tbe2e_")
        self.addCleanup(shutil.rmtree, self.root, True)
        env = mock.patch.dict(os.environ, {"TB_ROOT": self.root})          # код без явного root тоже пишет во временный корень
        env.start()
        self.addCleanup(env.stop)
        store2._STATUS_CACHE.clear()
        RV.clear_cache()
        self.addCleanup(RV.clear_cache)
        cdir = os.path.join(self.root, "config")
        os.makedirs(cdir)
        for n in COPIED + GUARDED:
            shutil.copyfile(os.path.join(REPO, "config", n), os.path.join(cdir, n))
        with open(os.path.join(cdir, "learnloop.json"), "w", encoding="utf-8") as f:
            json.dump(CFG, f, indent=1)
        self.assertEqual(LC.load(self.root), (CFG, []))
        self.real_guarded = dict((n, sha(os.path.join(REPO, "config", n))) for n in GUARDED)
        self.cfg_before = tree(cdir)
        c = store.open_rw(self.root)                   # база меток R1 до старта цикла (её пишет сервис меток)
        c.close()
        self.clock = Clock(T0 + 30 * 60)
        self.svc = SV.Service(self.root, clock=self.clock, sleep=self.clock.sleep, log=lambda m: None)    # конфиг, политика и memory.json — из корня
        self.addCleanup(self.svc.close)
        self.writes = WriteLog()
        for p in self.writes.patches():
            p.start()
            self.addCleanup(p.stop)

    def q(self, sql, *a):
        c = sqlite3.connect(store.db_path(self.root))
        try:
            return c.execute(sql, a).fetchall()
        finally:
            c.close()

    def run_until(self, market, end, stop=None):
        """Тик раз в час: сначала разметка закончившихся часов, затем проход сервиса. -> True, если stop() стал истинным."""
        while self.clock.t < end:
            market.feed(self.clock.t)
            self.svc.tick()
            self.assertEqual(self.svc.state, "ok", self.svc.last_error)
            if stop is not None and stop():
                return True
            self.clock.t += H
        return False

    def versions(self):
        return [(v, r) for v, r in self.q("SELECT ver, reason_code FROM rulebook_versions ORDER BY seq")]

    def check_common(self):
        """Свойство 15 и 28: ни одной записи OWNER, ошибок шагов нет, конфиги не тронуты, в config/ (и config/local) записей нет; режимы не смешаны."""
        actors = set(r[0] for r in self.q("SELECT DISTINCT actor FROM learn_events"))
        self.assertEqual(actors, {"code"})
        self.assertEqual(self.q("SELECT COUNT(*) FROM learn_events WHERE actor='OWNER'")[0][0], 0)
        self.assertEqual(set(r[0] for r in self.q("SELECT DISTINCT actor FROM blocklist_rows")) - {"code"}, set())
        self.assertEqual(self.q("SELECT step, code FROM learn_events WHERE code IN ('STEP_ERROR', 'CHAIN_BROKEN', 'DB_CORRUPT', 'WATCH_STALE')"), [])
        self.assertTrue(all(l.get("created_by") == "code" for l in LS.read_all(self.root).values()))
        cdir = os.path.join(self.root, "config")
        self.assertEqual(tree(cdir), self.cfg_before)                           # хеши risk_profile/risk_levels/exec_limits и всего config/
        for n in GUARDED:
            self.assertEqual(sha(os.path.join(REPO, "config", n)), self.real_guarded[n], n)
        self.assertFalse(os.path.exists(os.path.join(cdir, "local")))
        self.assertEqual(self.writes.under(cdir), [])                           # ни одной записи в config/ (в том числе config/local/*)
        self.assertEqual(self.writes.under(os.path.join(REPO, "config")), [])
        self.assertTrue(self.writes.under(os.path.join(self.root, "runtime")))  # запись шла (аренда, heartbeat) — журнал записей работает
        self.assertEqual([p for p in self.writes.paths if not p.startswith(os.path.abspath(self.root) + os.sep)], [])
        # режимы данных: testnet размечен, но меню, испытания и правила — только mainnet
        self.assertGreater(self.q("SELECT COUNT(*) FROM cluster_runs WHERE env_norm='testnet'")[0][0], 0)
        self.assertEqual(set(r[0] for r in self.q("SELECT DISTINCT env_norm FROM lesson_candidates")) - {"mainnet"}, set())
        self.assertEqual(set(r[0] for r in self.q("SELECT DISTINCT env_norm FROM trials")) - {"mainnet"}, set())
        self.assertEqual(set(r[0] for r in self.q("SELECT DISTINCT env_norm FROM rule_rows")) - {"mainnet"}, set())

    def elapsed(self):
        return time.time() - self.t_start


class TestAutonomyE2E(Base):
    def test_menu_to_promotion_and_rollback_without_owner(self):
        mkt = Market(self.root, NEG_MU, self.SEED)
        promoted = self.run_until(mkt, T0 + 14 * D, stop=lambda: len(self.versions()) >= 2)
        self.assertTrue(promoted, "за 14 суток правило не продвинуто: %s" % (self.svc.cycle.ready,))
        tp = self.clock.t
        # ---- путь до продвижения
        self.assertEqual(self.versions(), [("R-0001", "INIT"), ("R-0002", "PROMOTE")])
        menus = self.q("SELECT ts, ref FROM learn_events WHERE step='menu' AND code='MENU' ORDER BY seq")
        self.assertTrue(menus)
        cands = self.q("SELECT id, cond, status, code, env_norm, holdout_start, holdout_end, lesson_id FROM lesson_candidates ORDER BY id")
        hyp = [c for c in cands if c[2] == "hyp"]
        self.assertEqual(len(hyp), 1, cands)
        cond = json.loads(hyp[0][1])
        self.assertEqual([(x["feature"], x["op"]) for x in cond], [("spread_bps", ">=")])
        self.assertTrue(7.0 <= cond[0]["value"] <= 9.0, cond)
        self.assertEqual(hyp[0][4], "mainnet")
        lesson = LS.read_all(self.root)[hyp[0][7]]
        self.assertEqual((lesson["status"], lesson["created_by"], lesson["evidence"]["src"]), ("confirmed", "code", "labels"))   # сверка в том же проходе
        rules = self.q("SELECT rule_id, rev, status, kind, reason_code, env_norm, from_lesson FROM rule_rows ORDER BY seq")
        self.assertEqual(rules[0][2:4], ("trial", "skip_when"))
        self.assertEqual(rules[-1][:1] + rules[-1][2:], ("RL0001", "active", "skip_when", "PROMOTE", "mainnet", hyp[0][7]))
        tr = self.q("SELECT trial_id, state, verdict, numbers, criterion, start_ts, closed_ts FROM trials")
        self.assertEqual(len(tr), 1)
        self.assertEqual(tr[0][1:3], ("closed", "promote"))
        nums, crit = json.loads(tr[0][3]), json.loads(tr[0][4])
        self.assertEqual(crit["src"]["env_norm"], "mainnet")
        self.assertEqual(nums["n"], CFG["trial"]["n_target"])
        self.assertLess(nums["in_mean"], -CFG["trial"]["min_effect_r"])
        self.assertLess(nums["p"], crit["alpha_adj"])
        self.assertGreater(tr[0][5], hyp[0][6])                       # испытание на будущих кластерах после holdout
        # прогрев: 7 полных суток, сторож пишет аренду, копия; продвижение — шагом цикла, не владельцем
        days = self.q("SELECT body FROM learn_events WHERE step='day' ORDER BY seq")
        self.assertGreaterEqual(sum(1 for (b,) in days if json.loads(b)["full"]), CFG["promotion"]["warmup_days"])
        lease = RV.read_lease(self.root)
        self.assertEqual((lease["ts"], lease["rulebook_ver"]), (int(tp), "R-0002"))
        self.assertTrue(self.q("SELECT 1 FROM learn_events WHERE step='promote' AND code='PROMOTE' AND actor='code' AND ref='RL0001'"))
        self.assertIn("promote", [b["reason"] for b in store2.list_backups(self.root)])
        # rulebook_view.active применяет правило: вход с высоким спредом в области P2 снимается, с низким — нет
        RV.clear_cache()
        view = RV.active(self.root, now=tp + 60)
        self.assertEqual((view["ver"], view["apply"], view["why"]), ("R-0002", True, "OK"))
        self.assertEqual([r["rule_id"] for r in view["rules"]], ["RL0001"])
        feats = {"ev_kind": "range_5m", "side_rel": "with_dir", "spread_bps": 9.5, "side": "long"}
        res = LR.apply(view, {"ok": True}, feats, {"env_norm": "mainnet"})
        self.assertEqual((res["ok"], res["fail"]), (False, "RULE_RL0001"))
        self.assertTrue(LR.apply(view, {"ok": True}, dict(feats, spread_bps=2.0), {"env_norm": "mainnet"})["ok"])
        # testnet размечен с обратной закономерностью и в выборки mainnet не попал
        b = CL.last_b(tp, CFG)
        w_test = CL.window(self.svc.conn, "P2", 1, "c1", "testnet", T0, b, CFG, tp)
        tc = VF.test_clusters(w_test, cond)
        self.assertGreater(len(tc), 50)
        self.assertGreater(sum(x["diff"] for x in tc) / len(tc), 0.2)
        self.assertEqual(set(r["env_norm"] for r in CL.window(self.svc.conn, "P2", 1, "c1", "mainnet", T0, b, CFG, tp)["rows"]), {"mainnet"})
        # ---- подмена будущего эффекта: отсечённые входы стали прибыльными -> откат сторожем
        mkt.hit_mu = POS_MU
        rolled = self.run_until(mkt, tp + 6 * D, stop=lambda: len(self.versions()) >= 3)
        self.assertTrue(rolled, "сторож не откатил правило за 6 суток")
        tr_ = self.clock.t
        self.assertEqual(self.versions()[-1], ("R-0003", "ROLLBACK"))
        ver = self.q("SELECT rule_ids, numbers, parent FROM rulebook_versions WHERE ver='R-0003'")[0]
        self.assertEqual((json.loads(ver[0]), ver[2]), ([], "R-0002"))
        vn = json.loads(ver[1])
        self.assertEqual(vn["look"], "L50")
        self.assertGreater(vn["mean"], CFG["watch"]["rollback_delta_r"])
        self.assertLess(vn["p"], CFG["watch"]["alpha_watch"] / len(CFG["watch"]["looks"]))
        last = self.q("SELECT status, reason_code, rulebook_ver_out FROM rule_rows WHERE rule_id='RL0001' ORDER BY seq DESC LIMIT 1")[0]
        self.assertEqual(last, ("disabled", "ROLLBACK", "R-0003"))
        ch = LR.block_h(cond, "lower")
        self.assertEqual(self.q("SELECT code, actor FROM blocklist_rows WHERE cond_h=?", ch)[-1], ("ROLLBACK", "code"))
        self.assertTrue(self.q("SELECT 1 FROM learn_events WHERE step='watch' AND code='ROLLBACK' AND actor='code'"))
        RV.clear_cache()
        view = RV.active(self.root, now=tr_ + 60)
        self.assertEqual((view["ver"], view["rules"]), ("R-0003", []))
        self.assertTrue(LR.apply(view, {"ok": True}, feats, {"env_norm": "mainnet"})["ok"])       # множество входов — как до продвижения
        # ещё сутки: откат окончателен, новых версий и правил нет (условие в чёрном списке)
        self.run_until(mkt, tr_ + D)
        self.assertEqual(len(self.versions()), 3)
        self.assertEqual(self.q("SELECT COUNT(DISTINCT rule_id) FROM rule_rows")[0][0], 1)
        # чёрный список по семейству (Requirement 7.14, ревизия 2026-11-03): порог меню сдвигается с квантилем окна, но отказанное семейство
        # spread_bps >= * не возвращается ни в меню, ни в приём
        fam = LR.block_h(cond, "lower")
        shifted = [dict(cond[0], value=round(cond[0]["value"] - 0.07259, 6))]
        self.assertEqual(LR.block_h(shifted, "lower"), fam)
        self.assertEqual(store2.blocklist_get(self.svc.conn, fam, now=self.clock.t)["code"], "ROLLBACK")
        late = [r for r in self.q("SELECT cond, status FROM lesson_candidates WHERE ts>?", tr_) if LR.block_h(json.loads(r[0]), "lower") == fam]
        self.assertEqual([r for r in late if r[1] in ("new", "holdout", "hyp")], [])
        self.check_common()
        self.assertLess(tp - T0, 10 * D)                      # прогрев 7 суток не задерживает: меню ~5-е, hyp ~8-е, продвижение ~8.5-е сутки
        self.assertLess(self.elapsed(), 60)


class TestNoPattern(Base):
    def test_no_pattern_no_rules(self):
        mkt = Market(self.root, None, self.SEED)
        self.run_until(mkt, T0 + 11 * D)
        self.assertTrue(self.q("SELECT 1 FROM learn_events WHERE step='menu' AND code='MENU'"))          # меню строилось
        self.assertEqual(self.versions(), [("R-0001", "INIT")])
        self.assertEqual(self.q("SELECT COUNT(*) FROM rule_rows")[0][0], 0)
        self.assertEqual(self.q("SELECT COUNT(*) FROM trials")[0][0], 0)
        self.assertEqual(self.q("SELECT COUNT(*) FROM lesson_candidates WHERE status='hyp'")[0][0], 0)
        self.assertEqual([l for l in LS.read_all(self.root).values() if LS.is_labels(l)], [])
        RV.clear_cache()
        self.assertEqual(RV.active(self.root, now=self.clock.t)["rules"], [])
        self.check_common()
        self.assertLess(self.elapsed(), 60)


if __name__ == "__main__":
    unittest.main()
