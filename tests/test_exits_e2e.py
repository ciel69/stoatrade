"""Сквозной тест цикла выходов R4 (задача 24; Requirements 6.2, 7.1, 8.3, 8.6, 9.3, 14.1, 14.3; design.md «Сквозной тест»).

Фикстура — 30 суток синтетических путей прогонов (детерминированный генератор, зёрна фиксированы, время виртуальное): у одного
варианта меню (`L1`) разность `d` с действующей версией `X-0001` равна +0.1R на каждом пути, у остальных вариантов — ровно 0.
Ожидаемые числа считаются независимо от модуля поиска: по границе окна (`cut_ts(b) = b + cluster_close_max_h + observe_s`)
отбираются сгенерированные пути, среднее `d` считается по ним вручную.

Сценарий целиком:
  * поиск (`learnloop.cycle.step_exits_search`) находит ровно `L1` (остальные варианты — `NOOP`);
  * регистрация испытания шагом цикла `step_exits_register` (кандидат `L1` и его политика — из строки `exit_search`;
    повторный проход на той же строке ничего не создаёт), новые пути после `start_ts` (до начала — путь-приманка с
    `d = −0.9R`) и 60 сделок мозга с `d_b = 0`; вердикт `promote` (`step_exits_verdicts`);
  * продвижение (`step_exits_promote`): версия `X-0002`, снимок `runtime/exit_policy.json`, аренда `runtime/exit_lease.json`;
  * исполнитель на paper-порту: позиция, открытая до продвижения, ведётся версией `X-0001`; новая позиция получает `X-0002`;
  * ухудшение после продвижения (два блока с `d_w = −0.25R`) → сторож (`step_exits_watch`) откатывает: версия `X-0003` с политикой
    предыдущей версии; новая позиция исполнителя идёт на `X-0003`, старая остаётся на `X-0002`;
  * отдельно: просроченная аренда → исполнитель на полу (`EXIT_POLICY_FALLBACK:LEASE`, версия `X-0001`);
  * R2: `ctx_ver` теневых политик, `rules_ver` (`config/memory.json`) и хеши `risk_profile.json`, `risk_levels.json`,
    `exec_limits.json` за весь сценарий не меняются.

Временные корни, сети нет, реальный `state/` не используется. Критерий задачи — не дольше 60 с (порог × TB_PERF_FACTOR).
"""
import copy
import hashlib
import json
import os
import shutil
import time
import unittest

import _bootstrap  # noqa: F401
import _labels_helpers as H
from fakes.harness import Env, plan_long
from tb.core import exitdoc as XD
from tb.executor.paper_port import PaperPort
from tb.knowledge import store2
from tb.labels import policies as LP
from tb.labels import store
from tb.learnloop import conf as LC
from tb.learnloop import cycle as CY
from tb.learnloop import exits_conf as EC
from tb.learnloop import exits_eval as EE
from tb.learnloop import exits_search as XS
from tb.learnloop import exits_trial as XT
from tb.learnloop import exits_versions as XV
from tb.learnloop import exits_watch as XW
from tb.logic import exitpol as XP
from tb.logic import exitsim as XSIM

NOW = 1793750400                     # 2026-11-04T00:00:00Z
DAY = 86400
HOUR = 3600
PERF_FACTOR = float(os.environ.get("TB_PERF_FACTOR") or 1.0)
CFG_FILES = ("exits.json", "learnloop.json", "risk_levels.json")
VAR = "L1"                           # единственный вариант меню с эффектом
D_EFFECT = 0.1                       # +0.1R на каждом пути
SEED = 20261104


def _round(x, nd=6):
    return round(float(x), nd)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        for name in CFG_FILES:
            shutil.copy(os.path.join(H.PROJECT, "config", name), os.path.join(self.root, "config", name))
        self.conn = store.open_rw(self.root)
        self.addCleanup(self.conn.close)
        self.assertEqual(store2.migrate_x1(self.conn, self.root, backup=False, now=NOW), 1)
        self.assertEqual(store2.migrate_v2a(self.conn, self.root, enabled=True, backup=False, now=NOW), 1)
        self.cfg = EC.load_strict(self.root)
        self.floor = EC.current_floor(self.root)
        self.assertEqual(XV.init(self.conn, self.floor, NOW), "X-0001")
        self.lcfg, errs = LC.load(self.root)
        self.assertEqual(errs, [])
        self.inp = EE.market_inputs(self.root)
        self.cost_h = self.inp["cost_h"]
        self.base_h = XP.policy_hash(self.floor)
        self.menu = XS.menu_variants(self.cfg, self.floor)
        self.menu_h = {(v["var_id"], v["var_ver"]): v["policy_h"] for v in self.menu}
        self.assertNotEqual(self.menu_h[(VAR, 1)], self.base_h)
        XD.clear_cache()
        self.cfg0 = self.config_bytes()
        self.ctx0 = self.ctx()
        self.rules_ver0 = self.rules_ver()

    # ---------------------------------------------------------------- мелочи стенда
    def ctx(self):
        """`ctx_ver` теневых политик из конфигов исполнителя/профиля риска (Requirement 14.1)."""
        return LP.ctx_ver(LP._inputs(self.root, {})[0])

    def rules_ver(self):
        """`rules_ver` R2 из `config/memory.json` (за весь сценарий выходов не меняется)."""
        with open(os.path.join(self.root, "config", "memory.json"), encoding="utf-8") as f:
            return json.load(f).get("rules_ver")

    def config_bytes(self):
        out = {}
        for name in ("risk_profile.json", "risk_levels.json", "exec_limits.json"):
            with open(os.path.join(self.root, "config", name), "rb") as f:
                out[name] = f.read()
        return out

    def cycle(self):
        return CY.Cycle(self.root, copy.deepcopy(self.lcfg), steps=CY.default_steps())

    def ctx_at(self, cyc, now):
        return CY.Ctx(self.root, self.conn, copy.deepcopy(self.lcfg), now, cycle=cyc)

    def window(self, now):
        """Окно поиска, посчитанное независимо от `exits_search.window` (Requirement 4.3, 5.2)."""
        b = int(now - int(self.lcfg["clusters"]["cluster_close_max_h"]) * HOUR - int(self.lcfg["intervals"]["observe_s"])) // HOUR * HOUR
        a = b - int(self.cfg["search"]["window_days"]) * DAY
        return {"a": int(a), "b": int(b), "cut_ts": int(b) + int(self.lcfg["clusters"]["cluster_close_max_h"]) * HOUR
                + int(self.lcfg["intervals"]["observe_s"])}

    # ---------------------------------------------------------------- строки оценок
    def ev(self, var_id, var_ver, policy_h, key, t0, side, r_net, mfe, src="run", created_ts=None, labeled_ts=None):
        return {"src": src, "key": key, "var_id": str(var_id), "var_ver": int(var_ver), "ord": "S", "px_mode": "fee",
                "policy_h": str(policy_h), "cost_h": self.cost_h, "sim_ver": XSIM.SIM_VER, "t0": int(t0),
                "block": EE.block_id(t0, side, int(self.cfg["unit"]["block_h"])),
                "labeled_ts": int(t0 + 600 if labeled_ts is None else labeled_ts),
                "r_net": float(r_net), "r_gross": float(r_net), "fees_r": 0.0, "mfe_r": float(mfe), "mae_r": 0.0,
                "path_mfe_r": float(mfe), "exit_reason": "tp", "hold_min": 60.0, "end_ts": int(t0) + HOUR, "amb": 0,
                "created_ts": int(NOW if created_ts is None else created_ts)}

    def insert(self, rows):
        with store.tx(self.conn):
            store2.insert_rows(self.conn, "exit_evals", rows, ignore_dup=True)

    def add_search_path(self, key, t0, side, base_r, mfe, created_ts):
        """Путь прогона: база и все варианты меню; у `VAR` разность +0.1R, у остальных 0."""
        rows = [self.ev("X-0001", 1, self.base_h, key, t0, side, base_r, mfe, created_ts=created_ts)]
        for v in self.menu:
            k = (v["var_id"], v["var_ver"])
            d = D_EFFECT if k == (VAR, 1) else 0.0
            rows.append(self.ev(v["var_id"], v["var_ver"], v["policy_h"], key, t0, side, base_r + d, mfe, created_ts=created_ts))
        self.insert(rows)

    def add_trial_path(self, key, t0, side, base_r, mfe, created_ts):
        """Путь испытания: только вариант и база (остальные варианты испытанию не нужны)."""
        self.insert([self.ev("X-0001", 1, self.base_h, key, t0, side, base_r, mfe, created_ts=created_ts),
                     self.ev(VAR, 1, self.menu_h[(VAR, 1)], key, t0, side, base_r + D_EFFECT, mfe, created_ts=created_ts)])

    def add_trade(self, key, ts, created_ts):
        """Сделка мозга: обе стороны пары с одинаковым `r`, поэтому `d_b = 0` (условие `BRAIN`)."""
        self.insert([self.ev("X-0001", 1, self.base_h, key, ts, "long", 0.0, 1.0, src="trade", created_ts=created_ts),
                     self.ev(VAR, 1, self.menu_h[(VAR, 1)], key, ts, "long", 0.0, 1.0, src="trade", created_ts=created_ts)])

    def add_pair_evals(self, cur, prev, key, t0, side, r_cur, r_prev, mfe, created_ts):
        """Пара «действующая — предыдущая» на одном пути (для сторожа)."""
        self.insert([self.ev(cur["ver"], XV.ver_num(cur["ver"]), cur["policy_h"], key, t0, side, r_cur, mfe, created_ts=created_ts),
                     self.ev(prev["ver"], XV.ver_num(prev["ver"]), prev["policy_h"], key, t0, side, r_prev, mfe, created_ts=created_ts)])

    def degrade(self, promoted, t_promo):
        """Два блока после продвижения, где действующая версия хуже предыдущей на 0.25R (одинаково — p = 0)."""
        day0 = (int(t_promo) // DAY + 1) * DAY
        t_long, t_short = day0 + 6 * HOUR, day0 + 18 * HOUR
        prev = XV.version_row(self.conn, "X-0001")
        self.add_pair_evals(promoted, prev, "w-long", t_long, "long", 0.10, 0.35, 1.8, int(t_promo) + 60)
        self.add_pair_evals(promoted, prev, "w-short", t_short, "short", 0.10, 0.35, 1.8, int(t_promo) + 60)
        return t_short + 3 * DAY

    # ---------------------------------------------------------------- данные фикстуры
    def generate_search_days(self, start_ts, days=30, per_block=2):
        """30 суток синтетических путей: 2 стороны × `per_block` путей, детерминированные числа от `SEED`."""
        expected = []
        for day in range(days):
            for j, (off, side) in enumerate(((6 * HOUR, "long"), (18 * HOUR, "short"))):
                for k in range(per_block):
                    idx = day * 4 + j * per_block + k
                    h = hashlib.sha256(("%d:%d" % (SEED, idx)).encode("ascii")).digest()
                    base_r = _round(0.30 + 0.10 * (h[0] % 5) / 4.0)
                    mfe = _round(1.70 + 0.20 * (h[1] % 6) / 5.0)
                    t0 = int(start_ts) + day * DAY + off + k * 60
                    key = "s%05d" % idx
                    self.add_search_path(key, t0, side, base_r, mfe, NOW)
                    expected.append({"key": key, "t0": t0, "side": side, "block": EE.block_id(t0, side, 24),
                                     "base_r": base_r, "d": D_EFFECT, "mfe": mfe})
        return expected

    def generate_trial_blocks(self, start_ts, days=15, per_block=2):
        """30 блоков (15 суток × 2 стороны) после `start_ts`; плюс путь-приманка до старта."""
        expected = []
        self.add_trial_path("prey", int(start_ts) - 2 * HOUR, "long", 0.4, 1.8, NOW)
        for day in range(1, days + 1):
            for j, (off, side) in enumerate(((6 * HOUR, "long"), (18 * HOUR, "short"))):
                for k in range(per_block):
                    idx = day * 4 + j * per_block + k
                    h = hashlib.sha256(("t:%d:%d" % (SEED, idx)).encode("ascii")).digest()
                    base_r = _round(0.30 + 0.10 * (h[0] % 5) / 4.0)
                    mfe = _round(1.70 + 0.20 * (h[1] % 6) / 5.0)
                    t0 = int(start_ts) + day * DAY + off + k * 60
                    key = "t%05d" % idx
                    self.add_trial_path(key, t0, side, base_r, mfe, NOW)
                    expected.append({"key": key, "t0": t0, "block": EE.block_id(t0, side, 24), "d": D_EFFECT})
        for i in range(60):
            self.add_trade("b%03d" % i, int(start_ts) + i * HOUR + 60, NOW)
        return expected

    # ---------------------------------------------------------------- исполнитель на paper-порту
    def paper_env(self):
        e = Env(env="paper", port=lambda clk, root: PaperPort.from_config(equity=40.0, root=root, clock=clk))
        self.addCleanup(e.close)
        e.port.on_private = e.q.append
        return e

    def apply_runtime(self, e):
        """Перенос действующих снимка и аренды цикла в корень исполнителя (production: общий runtime/)."""
        for name in ("exits.json",):
            shutil.copy(os.path.join(self.root, "config", name), os.path.join(e.root, "config", name))
        shutil.copy(XD.snapshot_path(self.root), XD.snapshot_path(e.root))
        shutil.copy(XD.lease_path(self.root), XD.lease_path(e.root))
        XD.clear_cache()

    def price(self, e, syms=("SOL", "ETH", "XRP", "ADA")):
        for sym in syms:
            e.port.set_price(sym, 100.0, spread=0.04)

    def enter_sym(self, e, sym):
        """Вход paper-порта по плану `plan_long` на отдельной монете (позиции друг друга не блокируют)."""
        r = e.submit(plan_long(mode="paper", symbol=sym, manage=[],
                               targets=[{"id": "t1", "price": 120.0, "frac": 1.0, "exec": "touch"}],
                               lifecycle={"ttl_min": 60, "max_hold_min": 600}))
        self.assertTrue(r["ok"], r)
        pid = r["plan_id"]
        e.tick(100.1, sym=sym)
        e.candle(100.3, sym=sym)
        for _ in range(6):
            e.tick(100.25, sym=sym, dt=0.5)
            e.timer(1, dt=0.1)
        self.assertIn(e.rt(pid)["status"], ("managing",), e.rt(pid))
        return pid


class FullCycle(Base):
    def test_search_trial_promote_executor_rollback_executor(self):     # Requirement 6.2, 7.1, 8.3, 8.6, 9.3, 14.1, 14.3
        t_start = time.time()
        # ---- 30 суток синтетических путей до поиска ------------------------------------------------
        T0 = NOW + 30 * DAY
        expected = self.generate_search_days(T0 - 30 * DAY, days=30, per_block=2)
        win = self.window(T0)
        self.assertEqual(win, EE.window(self.cfg, self.root, T0))          # граница окна совпадает с модулем
        sel = [p for p in expected if win["a"] <= p["t0"] < win["b"] and p["t0"] + 600 <= win["cut_ts"]]
        exp_blocks = len(set(p["block"] for p in sel))
        exp_mean = sum(p["d"] for p in sel) / float(len(sel))
        self.assertGreaterEqual(exp_blocks, int(self.cfg["search"]["min_clusters"]))
        self.assertGreaterEqual(len(sel), int(self.cfg["tail"]["min_n"]))

        # ---- поиск ---------------------------------------------------------------------------------
        cyc = self.cycle()
        rep = CY.step_exits_search(self.ctx_at(cyc, T0))
        self.assertEqual(rep["code"], "OK")
        self.assertEqual(rep["candidates"], 1)
        row = self.conn.execute("SELECT code, results FROM exit_search ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row[0], "OK")
        res = json.loads(row[1])
        self.assertEqual(res["candidates"], [[VAR, 1]])
        self.assertEqual(res["n_blocks"], exp_blocks)
        self.assertEqual(res["n_paths"], len(sel))
        l1 = next(v for v in res["variants"] if v["id"] == VAR)
        self.assertAlmostEqual(l1["mean_d"], exp_mean, places=9)
        self.assertAlmostEqual(l1["mean_d"], D_EFFECT, places=12)
        self.assertLess(l1["p"], float(self.cfg["search"]["alpha"]))
        for v in res["variants"]:
            if v["id"] != VAR:
                self.assertTrue(v["noop"], v)                       # эффект ровно 0 -> NOOP, не кандидат
        self.assertEqual(len(res["noop"]), len(res["variants"]) - 1)

        # ---- испытание: регистрация шагом цикла и новые пути после start_ts -----------------------
        reg = CY.step_exits_register(self.ctx_at(cyc, T0))
        self.assertEqual((reg["code"], reg["registered"]), ("OK", 1))
        open_trials = XT.trials(self.conn, XT.OPEN)
        self.assertEqual([(t["var_id"], t["var_ver"]) for t in open_trials], [(VAR, 1)])
        trial = open_trials[0]
        tid = trial["trial_id"]
        self.assertEqual(trial["criterion"]["var"]["policy_h"], l1["policy_h"])   # политика из строки поиска
        again = CY.step_exits_register(self.ctx_at(cyc, T0))
        self.assertEqual(again["registered"], 0)                                  # повторный проход той же строки
        self.assertEqual(len(XT.trials(self.conn)), 1)
        self.assertEqual(trial["state"], XT.OPEN)
        start = trial["start_ts"]
        self.assertEqual(start, ((int(T0) + HOUR - 1) // HOUR) * HOUR)
        trial_expected = self.generate_trial_blocks(start, days=15, per_block=2)
        n_blocks_expected = len(set(p["block"] for p in trial_expected))
        self.assertEqual(n_blocks_expected, 30)

        # сторож отработал -> свежая аренда и состояние (готовность продвижения, Requirement 7.1)
        T1 = start + 18 * DAY
        self.assertEqual(CY.step_exits_watch(self.ctx_at(cyc, T1))["code"], XW.OK)
        store2.make_backup(self.root, "daily", now=T1)
        verdict = CY.step_exits_verdicts(self.ctx_at(cyc, T1))
        self.assertEqual(verdict["closed"], 1)
        trial = XT.get(self.conn, tid)
        self.assertEqual((trial["state"], trial["verdict"]), (XT.CLOSED, XT.PROMOTE))
        nums = trial["numbers"]
        self.assertEqual(nums["n_blocks"], n_blocks_expected)
        self.assertAlmostEqual(nums["mean_d"], D_EFFECT, places=9)
        self.assertAlmostEqual(nums["mean_d"], sum(p["d"] for p in trial_expected) / float(len(trial_expected)), places=9)

        # ---- продвижение: версия, снимок и аренда -------------------------------------------------
        T_PROMO = T1 + 60
        pr = CY.step_exits_promote(self.ctx_at(cyc, T_PROMO))
        self.assertEqual((pr["code"], pr["promoted"]), ("OK", 1))
        versions = XV.versions(self.conn)
        self.assertEqual([(v["ver"], v["reason_code"], v["parent"]) for v in versions],
                         [("X-0001", "INIT", None), ("X-0002", "PROMOTE", "X-0001")])
        promoted = XV.last_version(self.conn)
        self.assertEqual(XD.current(self.root, T_PROMO, self.floor)[1]["ver"], "X-0002")
        with open(XD.snapshot_path(self.root), encoding="utf-8") as f:
            snap = json.load(f)
        with open(XD.lease_path(self.root), encoding="utf-8") as f:
            lease = json.load(f)
        self.assertEqual((snap["ver"], snap["policy_h"]), ("X-0002", promoted["policy_h"]))
        self.assertEqual((lease["ver"], lease["policy_h"]), ("X-0002", promoted["policy_h"]))

        # ---- исполнитель: версия на новую позицию, старая позиция своей версией --------------------
        e = self.paper_env()
        e.clock.t = float(T_PROMO - 60)
        self.price(e)
        p_old = self.enter_sym(e, "SOL")
        old = e.rt(p_old)["pos"]["exit"]
        self.assertEqual((old["ver"], old["why"]), ("X-0001", "SNAPSHOT"))       # снимка ещё нет
        self.assertEqual(old["policy"], self.floor)

        self.apply_runtime(e)
        e.clock.t = float(T_PROMO + 60)
        self.price(e)
        p_new = self.enter_sym(e, "ETH")
        new = e.rt(p_new)["pos"]["exit"]
        self.assertEqual((new["ver"], new["why"]), ("X-0002", "OK"))
        self.assertEqual(new["policy"], promoted["policy"])
        self.assertEqual(new["policy_h"], promoted["policy_h"])
        self.assertEqual(e.ex.limits_for(e.ex.plans[p_new])["exit_policy"], promoted["policy"])
        # позиция до продвижения продолжает свою версию (Requirement 8.9, 9.3)
        self.assertEqual(e.rt(p_old)["pos"]["exit"]["ver"], "X-0001")
        self.assertEqual(e.rt(p_old)["pos"]["exit"]["policy"], self.floor)
        self.assertEqual(e.ex.limits_for(e.ex.plans[p_old])["exit_policy"], self.floor)

        # ---- ухудшение после продвижения -> сторож откатывает -------------------------------------
        T_RB = self.degrade(promoted, T_PROMO)
        wr = CY.step_exits_watch(self.ctx_at(cyc, T_RB))
        self.assertEqual(wr["code"], XW.ROLLBACK)
        self.assertIsNotNone(wr["rollback"])
        last = XV.last_version(self.conn)
        self.assertEqual((last["ver"], last["reason_code"], last["parent"]), (wr["rollback"], "ROLLBACK", "X-0002"))
        self.assertEqual(last["policy"], self.floor)                         # политика предыдущей версии
        self.assertLess(last["numbers"]["mean_r"], -float(self.cfg["watch"]["rollback_delta_r"]))
        self.assertEqual(wr["points"], 1)
        self.assertEqual([p["look"] for p in XW.state_get(self.conn)["state"]["points"]], ["L2"])
        with open(XD.snapshot_path(self.root), encoding="utf-8") as f:
            snap = json.load(f)
        with open(XD.lease_path(self.root), encoding="utf-8") as f:
            lease = json.load(f)
        self.assertEqual((snap["ver"], snap["policy_h"]), (last["ver"], last["policy_h"]))
        self.assertEqual((lease["ver"], lease["policy_h"], lease["by"]), (last["ver"], last["policy_h"], "rollback"))

        # ---- исполнитель: предыдущая версия на новой позиции; позиция X-0002 не меняется -----------
        self.apply_runtime(e)
        e.clock.t = float(T_RB + 60)
        self.price(e)
        p_after = self.enter_sym(e, "XRP")
        after = e.rt(p_after)["pos"]["exit"]
        self.assertEqual((after["ver"], after["why"]), (last["ver"], "OK"))
        self.assertEqual(after["policy"], self.floor)
        self.assertEqual(e.rt(p_new)["pos"]["exit"]["ver"], "X-0002")     # версия открытой позиции не откатилась

        # ---- отдельно: просроченная аренда -> исполнитель на полу (Requirement 8.6) ----------------
        lease_h = int(self.cfg["lease"]["lease_h"])
        with open(XD.lease_path(e.root), "w", encoding="utf-8") as f:
            json.dump({"ts": int(T_RB) - (lease_h + 1) * HOUR, "ver": last["ver"], "policy_h": last["policy_h"], "by": "watch"}, f)
        XD.clear_cache()
        e.clock.t = float(T_RB + 120)
        self.price(e)
        p_floor = self.enter_sym(e, "ADA")
        fallback = e.rt(p_floor)["pos"]["exit"]
        self.assertEqual((fallback["ver"], fallback["why"]), ("X-0001", "LEASE"))
        self.assertEqual(fallback["policy"], self.floor)
        self.assertEqual(e.ex.exit_status["code"], "EXIT_POLICY_FALLBACK:LEASE")

        # ---- R2 и конфиги не меняются --------------------------------------------------------------
        self.assertEqual(self.ctx(), self.ctx0)
        self.assertEqual(self.rules_ver(), self.rules_ver0)
        self.assertEqual(self.config_bytes(), self.cfg0)
        self.assertLessEqual(time.time() - t_start, 60.0 * PERF_FACTOR,
                             "сквозной цикл: %.1f с" % (time.time() - t_start))


class Perf(Base):
    def test_scenario_under_60s(self):                                  # критерий задачи 24
        """Тот же прогон без исполнителя: поиск + испытание + продвижение + откат укладываются в порог."""
        t0 = time.time()
        T0 = NOW + 30 * DAY
        self.generate_search_days(T0 - 30 * DAY)
        cyc = self.cycle()
        self.assertEqual(CY.step_exits_search(self.ctx_at(cyc, T0))["code"], "OK")
        self.assertEqual(CY.step_exits_register(self.ctx_at(cyc, T0))["registered"], 1)
        trial = XT.trials(self.conn, XT.OPEN)[0]
        start = trial["start_ts"]
        self.generate_trial_blocks(start)
        T1 = start + 18 * DAY
        CY.step_exits_watch(self.ctx_at(cyc, T1))
        store2.make_backup(self.root, "daily", now=T1)
        self.assertEqual(CY.step_exits_verdicts(self.ctx_at(cyc, T1))["closed"], 1)
        T_PROMO = T1 + 60
        self.assertEqual(CY.step_exits_promote(self.ctx_at(cyc, T_PROMO))["promoted"], 1)
        promoted = XV.last_version(self.conn)
        T_RB = self.degrade(promoted, T_PROMO)
        self.assertEqual(CY.step_exits_watch(self.ctx_at(cyc, T_RB))["code"], XW.ROLLBACK)
        took = time.time() - t0
        self.assertLessEqual(took, 60.0 * PERF_FACTOR, "цикл выходов: %.1f с" % took)


if __name__ == "__main__":
    unittest.main()

