"""R1.7 задача 19 (Requirements 15.1–15.7, 23.3): первая волна меню и паритет признаков сводного рынка.

Свойство 12 — паритет: для одного сигнала путь меток (строка hot_events.jsonl -> labels.capture.poll -> signals.ev.cm -> rule_features) и путь
мозга (та же строка -> Publisher.convert -> bus.emit -> журнал шины -> brain/cands.rule_check -> cmktdoc.features_at(wait=True) ->
rule_features) дают равные значения всех RULE_FEATURES: событие шины позже сигнала на 0–60 с, решение мозга до записи строки (ожидание срока),
захват меток раньше срока ожидания строки (отложенная вставка задачи 18), чтение меток через 15–120 с, строка записана позже accept_s (обе
стороны — late), монета вошла в фокус после t0 (обе — без признаков); событие без d.t0 — cm_missing=no_t0 и признаки cm_ None.
Свойство 16 — граница меню: K_MAX = 25, признак с заполнением 0.5 отсекается COVERAGE_LOW; прежние cluster_runs без cm_ читаются с None.
`./tb learn menu-wave --check` на фикстуре базы: числа (а)–(в) и критерий (г) по установившемуся окну 14 суток; на неполном окне (г) = None.
Ряды пишет tb.cmkt.store на временном корне; часы cmktdoc подменены; сеть и реальные state/runtime не используются.
"""
import contextlib
import io
import json
import os
import random
import shutil
import tempfile
import time
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import _labels_helpers as H
import test_rule_features_parity as TRP
from tb.brain import cands
from tb.cmkt import conf as C
from tb.cmkt import store as CS
from tb.core import bus
from tb.core import cmktdoc as D
from tb.knowledge import learn_cli, store2
from tb.labels import capture, store
from tb.learnloop import candidates as CA
from tb.learnloop import conf as LC
from tb.learnloop import menu as MN
from tb.learnloop import rules as RL
from tb.logic import cmkt as L
from tb.logic import learn_cond
from tb.memory import features as MF
from tb.memory import lessons as LS
from tb.platform.scansvc import Publisher

PERF = float(os.environ.get("TB_PERF_FACTOR") or 1.0)
NOW = 1793181300.0                     # 2026-10-28 ~10:35 UTC
SYM = "SOLUSDT"
P2_KINDS = ["range_5m", "turnover_surge", "hot_enter", "oi_spike", "liq_cascade"]
WAVE = learn_cond.CM_WAVE
CFG = LC.load_strict()


def cm_values(k=0.0):
    """Значения всех CM_FEATURES (масштабы ряда: число через запись и чтение остаётся тем же); у первой волны — различные."""
    out = dict((n, (3 if L.SCALES[n] == 1 else 1.5)) for n in L.CM_FEATURES)
    out.update({"cm_imb_025_pct": 12.3 + k, "cm_oi_liq_5m_pct": -0.457, "cm_cvd_5m_perp_pct": 7.4, "cm_wall_pull_15m_pct": 33.1})
    return out


def rule(feature="cm_imb_025_pct", op=">=", value=10.0, rid="RL0001"):
    return {"rule_id": rid, "kind": "skip_when", "status": "active", "cond": [{"feature": feature, "op": op, "value": value}], "param": {},
            "scope": {"policy": "P2", "kinds": P2_KINDS, "side": "with_dir", "entry": "candle_confirm_limit"}, "env_norm": "mainnet"}


def view_of(*rules):
    return {"ver": "R-0002", "apply": True, "why": "OK", "rules": list(rules), "trial": [], "kinds": P2_KINDS, "invalid": []}


class FakeClock(object):
    """Часы cmktdoc (_clock, _sleep): sleep сдвигает время; writes — [(момент, функция)] выполняются, когда часы его достигли."""

    def __init__(self, t):
        self.t = float(t)
        self.writes = []
        self.slept = 0.0

    def clock(self):
        return self.t

    def sleep(self, dt):
        self.t += dt
        self.slept += dt
        self._run()

    def _run(self):
        for item in list(self.writes):
            if self.t >= item[0]:
                self.writes.remove(item)
                item[1]()

    def at(self, t, fn):
        self.writes.append((float(t), fn))


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        shutil.copy(os.path.join(H.PROJECT, "config", "learnloop.json"), os.path.join(self.root, "config", "learnloop.json"))
        self.conn = store.open_rw(self.root)
        self.addCleanup(self.conn.close)
        self.cfg = capture.load_cfg(self.root)
        self.cstore = CS.Store(self.root, C.defaults(), clock=lambda: 0.0, disk_free=lambda p: 10 ** 13)
        self.addCleanup(self.cstore.close)
        D._cfg_cache.clear()
        self.t0 = int(NOW)
        self.clk = FakeClock(self.t0)
        for name, fn in (("_clock", self.clk.clock), ("_sleep", self.clk.sleep)):
            p = mock.patch.object(D, name, fn)
            p.start()
            self.addCleanup(p.stop)

    # ---- ряды
    def row(self, ts, lag=0.4, sym="SOL", k=0.0, focus_s=600):
        self.cstore.write(ts, [{"sym": sym, "tier": "deep", "f": cm_values(k), "focus_s": focus_s, "flags": []}], wr_ts=ts + lag)

    def series(self, t_from, t_to, **kw):
        for ts in range(t_from, t_to + 1):
            self.row(ts, **kw)

    # ---- путь мозга
    def line(self, kind="range_5m"):
        return TRP.scan_line(kind, now=self.t0, sym=SYM)

    def bus_event(self, line, delay=0.0, drop_t0=False):
        """Событие шины: Publisher.convert -> bus.emit (позже сигнала на delay) -> чтение журнала шины."""
        c = Publisher(self.root, {"publish_info": True}, events_path=os.path.join(self.root, "none.jsonl")).convert(line)
        self.assertIsNotNone(c)
        d = dict(c[0])
        if drop_t0:
            d.pop("t0", None)                     # старый публикатор: события без d.t0
        bus.emit("scanner_signal", d, sym=c[1], root=self.root, now=self.t0 + delay)
        evs = [e for e in bus.read(root=self.root, limit=0) if e.get("type") == "scanner_signal" and e.get("sym") == c[1]]
        return evs[-1]

    def brain(self, ev, now, view=None, side="long"):
        """rule_check с правилом по cm_imb_025_pct: признаки, которые увидело правило (перехват rule_features), и ответ."""
        seen = []
        real = MF.rule_features

        def spy(*a, **kw):
            out = real(*a, **kw)
            seen.append(out)
            return out
        with mock.patch.object(MF, "rule_features", side_effect=spy):
            chk = cands.rule_check(self.root, SYM, side, now=now, view=view or view_of(rule()), event=ev, cfg=TRP.MEM_CFG)
        self.assertEqual(len(seen), 1)
        return seen[0], chk

    # ---- путь меток
    def label(self, line, now):
        H.write_events(self.root, [line])
        return capture.poll(self.conn, self.root, self.cfg, now)

    def label_feats(self, side="long"):
        row = self.conn.execute("SELECT t0, sym, type, side, ev FROM signals").fetchone()
        self.assertIsNotNone(row, "сигнал не вставлен")
        sig = {"t0": row[0], "sym": row[1], "type": row[2], "side": row[3], "ev": row[4]}
        return MF.rule_features(sig, {"side": side}, TRP.MEM_CFG), json.loads(row[4])

    def assertParity(self, a, b):
        for k in learn_cond.RULE_FEATURES + MF.RULE_SCOPE_KEYS:
            self.assertEqual(a[k], b[k], k)


# ---------------------------------------------------------------- свойство 12
class Parity(Base):
    def test_bus_event_later_than_signal_and_label_read_later(self):
        """Событие шины позже сигнала на 0–60 с, чтение меток через 15–120 с: одна и та же строка t0 − 1 на обоих путях."""
        self.series(self.t0 - 30, self.t0 + 200)
        line = self.line()
        res = self.label(line, self.t0 + 15)
        self.assertEqual(res["new"], 1)
        lab, ev = self.label_feats()
        self.assertEqual(ev["cm"]["row_ts"], self.t0 - 1)
        for delay in (0, 1, 15, 60):
            with self.subTest(delay=delay):
                bev = self.bus_event(line, delay)
                self.assertEqual(bev["d"]["t0"], self.t0)
                self.clk.t = self.t0 + delay + 0.5
                feats, chk = self.brain(bev, self.t0 + delay + 0.5)
                self.assertParity(feats, lab)
                self.assertEqual(feats["cm_imb_025_pct"], 12.3)
                self.assertIsNone(chk["cm_missing"])
                self.assertEqual(chk["res"]["fail"], "RULE_RL0001")              # правило cm_ сработало на значении строки
        for k in WAVE:
            self.assertIsNotNone(lab[k], k)
        # чтение меток ещё через 120 с даёт то же (значения заморожены при вставке)
        lab2, _ = self.label_feats()
        self.assertParity(lab, lab2)

    def test_decision_before_row_written_waits_for_deadline(self):
        """Решение мозга сразу после сигнала: строки t0 − 1 ещё нет, rule_check ждёт до срока и берёт записанную вовремя строку."""
        self.series(self.t0 - 30, self.t0 - 2)
        line = self.line()
        bev = self.bus_event(line, 0.2)
        self.clk.t = self.t0 + 0.2
        self.clk.at(self.t0 + 1.5, lambda: self.row(self.t0 - 1, lag=2.5, k=1.0))      # wr_ts = row_ts + 2.5 ≤ accept_s
        feats, chk = self.brain(bev, self.t0 + 0.2)
        self.assertGreater(self.clk.slept, 0.0)
        self.assertLessEqual(self.clk.slept, 3 + 2.0)
        self.assertEqual(feats["cm_imb_025_pct"], 13.3)
        self.assertIsNone(chk["cm_missing"])
        self.series(self.t0, self.t0 + 60)
        self.label(line, self.t0 + 30)
        lab, ev = self.label_feats()
        self.assertEqual(ev["cm"]["row_ts"], self.t0 - 1)
        self.assertParity(feats, lab)

    def test_label_capture_before_deadline_deferred(self):
        """Захват меток раньше срока ожидания строки: вставка отложена (курсор не сдвинут), следующий проход вставляет с той же строкой."""
        self.series(self.t0 - 30, self.t0 - 2)
        line = self.line()
        r = self.label(line, self.t0 + 0.5)
        self.assertEqual(r["new"], 0)
        self.assertIsNotNone(r.get("cm_pending"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0], 0)
        self.row(self.t0 - 1, lag=1.2, k=2.0)
        r = capture.poll(self.conn, self.root, self.cfg, self.t0 + 2)
        self.assertEqual(r["new"], 1)
        lab, ev = self.label_feats()
        self.assertEqual(ev["cm"]["row_ts"], self.t0 - 1)
        bev = self.bus_event(line, 0.5)
        self.clk.t = self.t0 + 0.5
        feats, _chk = self.brain(bev, self.t0 + 0.5)
        self.assertParity(feats, lab)
        self.assertEqual(feats["cm_imb_025_pct"], 14.3)

    def test_row_written_after_accept_s(self):
        """Строка записана позже accept_s: обе стороны — late, признаки cm_ None (в том числе когда мозг ждал её записи)."""
        self.series(self.t0 - 30, self.t0 - 2)
        line = self.line()
        bev = self.bus_event(line, 0.3)
        self.clk.t = self.t0 + 0.3
        self.clk.at(self.t0 + 2.7, lambda: self.row(self.t0 - 1, lag=3.6))                # wr_ts = row_ts + 3.6 > accept_s
        feats, chk = self.brain(bev, self.t0 + 0.3)
        self.assertEqual(chk["cm_missing"], "late")
        self.assertIsNone(chk["res"]["fail"])                                              # признак None: условие ложно
        self.assertEqual(chk["res"]["missing"], ["RL0001"])
        self.label(line, self.t0 + 30)
        lab, ev = self.label_feats()
        self.assertEqual(ev["cm_missing"], "late")
        self.assertParity(feats, lab)
        for k in WAVE:
            self.assertIsNone(lab[k], k)

    def test_coin_entered_focus_after_t0(self):
        """Монета вошла в фокус после t0: строки t0 − 1 и t0 − 2 нет — обе стороны not_focus без ожидания."""
        self.series(self.t0 + 5, self.t0 + 60)
        line = self.line()
        bev = self.bus_event(line, 0.1)
        self.clk.t = self.t0 + 0.1
        feats, chk = self.brain(bev, self.t0 + 0.1)
        self.assertEqual(self.clk.slept, 0.0)
        self.assertEqual(chk["cm_missing"], "not_focus")
        self.label(line, self.t0 + 30)
        lab, ev = self.label_feats()
        self.assertEqual(ev["cm_missing"], "not_focus")
        self.assertParity(feats, lab)

    def test_event_without_t0(self):
        """Событие без d.t0 (старый публикатор): время конверта для utc_session, cm_missing=no_t0, признаки cm_ None; остальные — как у меток."""
        self.series(self.t0 - 30, self.t0 + 60)
        line = self.line()
        bev = self.bus_event(line, 5, drop_t0=True)
        self.assertNotIn("t0", bev["d"])
        with mock.patch.object(D, "features_at", side_effect=AssertionError("без t0 ряды не читаются")):
            feats, chk = self.brain(bev, self.t0 + 5)
        self.assertEqual(chk["cm_missing"], "no_t0")
        for k in WAVE:
            self.assertIsNone(feats[k], k)
        self.label(line, self.t0 + 30)
        lab, _ev = self.label_feats()
        for k in learn_cond.BASE_RULE_FEATURES + MF.RULE_SCOPE_KEYS:
            self.assertEqual(feats[k], lab[k], k)

    def test_rule_check_does_not_throw(self):
        bev = self.bus_event(self.line(), 0.1)
        with mock.patch.object(D, "_once", side_effect=RuntimeError("boom")):
            feats, chk = self.brain(bev, self.t0 + 0.1)
        self.assertEqual(chk["cm_missing"], "db")
        self.assertTrue(chk["res"]["ok"])
        with mock.patch.object(D, "features_at", side_effect=RuntimeError("boom")):
            self.assertEqual(cands.rule_cm(self.root, SYM, bev, self.t0), {"cm": None, "cm_missing": "db"})


class NoBlocking(Base):
    def test_no_cm_rule_no_read_no_wait(self):
        """Правил по cm_ нет: строка признаков не читается и решение не ждёт (горячий путь мозга и шлюза не задерживается)."""
        self.series(self.t0 - 30, self.t0 - 2)
        bev = self.bus_event(self.line(), 0.1)
        with mock.patch.object(D, "features_at", side_effect=AssertionError("не должно читаться")):
            _f, chk = self.brain(bev, self.t0 + 0.1, view=view_of(rule("spread_bps", ">=", 1.0)))
        self.assertEqual(chk["res"]["fail"], "RULE_RL0001")
        self.assertIsNone(chk["cm_missing"])
        self.assertFalse(cands.needs_cm(view_of(rule("spread_bps", ">=", 1.0))))
        self.assertTrue(cands.needs_cm(dict(view_of(), trial=[rule()])))
        self.assertFalse(cands.needs_cm(None))

    def test_wait_bounded_by_deadline(self):
        """Строка так и не появилась: ожидание не дольше срока row_ts + accept_s + 1 (не больше accept_s + 2 с), затем no_row."""
        self.series(self.t0 - 30, self.t0 - 2)
        bev = self.bus_event(self.line(), 0.0)
        self.clk.t = self.t0
        _f, chk = self.brain(bev, self.t0)
        self.assertEqual(chk["cm_missing"], "no_row")
        self.assertLessEqual(self.clk.slept, 3 + 1 + 1e-6)

    def test_real_clock_no_wait_when_not_in_focus(self):
        """Без подмены часов: монета вне рядов — ответ сразу."""
        mock.patch.stopall()
        bev = self.bus_event(self.line(), 0.0)
        t = time.time()
        r = cands.rule_cm(self.root, SYM, bev, time.time())
        self.assertLess(time.time() - t, 1.0 * PERF)
        self.assertEqual(r["cm_missing"], "db")

    def test_apply_rules_reads_row_once(self):
        """Разметка кандидатов: одна строка признаков на обе стороны."""
        self.series(self.t0 - 30, self.t0 + 5)
        bev = self.bus_event(self.line(), 1)
        out = [{"side": "long", "ok": True}, {"side": "short", "ok": True}]
        with mock.patch.object(cands, "resolve", return_value=bev), mock.patch.object(cands, "_mem_cfg", return_value=TRP.MEM_CFG), \
                mock.patch.object(D, "features_at", wraps=D.features_at) as fa:
            cands.apply_rules(out, SYM, root=self.root, now=self.t0 + 2, view=view_of(rule()))
        self.assertEqual(fa.call_count, 1)
        self.assertEqual((out[0]["ok"], out[0].get("fail")), (False, "RULE_RL0001"))          # long: with_dir
        self.assertTrue(out[1]["ok"])                                                            # short: против направления — вне области


# ---------------------------------------------------------------- словарь, свойство 16
class Dictionary(unittest.TestCase):
    def test_wave_constants(self):
        self.assertEqual(learn_cond.CM_WAVE, L.FIRST_WAVE)
        self.assertEqual(MN.K_MAX, 25)
        self.assertEqual(MN.K_MAX, L.wave_k_max())
        self.assertEqual(learn_cond.RULE_FEATURES[-4:], L.FIRST_WAVE)
        self.assertEqual(learn_cond.MENU_NUM[-4:], L.FIRST_WAVE)
        others = set(L.CM_FEATURES) - set(L.FIRST_WAVE)
        self.assertFalse(others & set(learn_cond.RULE_FEATURES))
        self.assertFalse(others & set(LS.FEATURES))                       # словарь уроков (роли) — только волна
        self.assertEqual(learn_cond.validate_cond([{"feature": "cm_imb_025_pct", "op": ">=", "value": 1.5}]), [])
        self.assertTrue(learn_cond.validate_cond([{"feature": "cm_imb_05_pct", "op": ">=", "value": 1.5}]))
        self.assertEqual(LS.validate_cond([{"feature": "cm_oi_liq_5m_pct", "op": "<=", "value": -0.2}]), [])
        self.assertTrue(LS.validate_cond([{"feature": "cm_cvd_5m_pct", "op": "<=", "value": -0.2}]))
        for f in L.FIRST_WAVE:
            self.assertIn(f, LS.FEATURE_RU)

    def test_rule_features_sources_and_rounding(self):
        sig = {"t0": int(NOW), "sym": SYM, "type": "range_5m", "side": "long", "ev": json.dumps({"range_5m_pct": 1.0, "cm": {"cm_imb_025_pct": 1.234567891,
                                                                                                                         "cm_prem_ps_bps": 5.0}})}
        f = MF.rule_features(sig, "long", TRP.MEM_CFG)
        self.assertEqual(f["cm_imb_025_pct"], 1.2345679)                  # _num8, как у признаков события
        self.assertNotIn("cm_prem_ps_bps", f)                             # не волна — не признак правил
        self.assertIsNone(f["cm_oi_liq_5m_pct"])
        bev = {"type": "scanner_signal", "ts": bus.iso(NOW), "sym": SYM, "d": {"kind": "range_5m", "dir": "up", "t0": int(NOW)}}
        self.assertEqual(MF.rule_features(bev, "long", TRP.MEM_CFG, cm={"cm_imb_025_pct": 1.234567891})["cm_imb_025_pct"], 1.2345679)
        self.assertIsNone(MF.rule_features(bev, "long", TRP.MEM_CFG, cm={})["cm_imb_025_pct"])
        self.assertIsNone(MF.rule_features(None, "long", TRP.MEM_CFG, cm={"cm_imb_025_pct": 1.0})["cm_imb_025_pct"])

    def test_old_cluster_runs_read_with_none(self):
        """Прежние строки cluster_runs без признаков cm_: признаки None, условие по cm_ ложно, правила по прежним признакам не меняются."""
        row = {"feats": json.dumps({"range_5m_pct": 1.5, "side_rel": "with_dir"}), "type": "range_5m", "policy_id": "P2"}
        feats = RL.row_feats(row)
        for k in WAVE:
            self.assertIsNone(feats.get(k))
        self.assertFalse(learn_cond.matches_features([{"feature": "cm_imb_025_pct", "op": "<=", "value": 100.0}], feats))
        self.assertTrue(learn_cond.matches_features([{"feature": "range_5m_pct", "op": ">=", "value": 1.0}], feats))

    def test_coverage_low_at_half(self):
        """Заполнение признака 0.5 < feat_cover_min: условия по нему не попадают в K (COVERAGE_LOW); 0.9 — попадают."""
        rnd = random.Random(5)
        rows = []
        for h in range(14 * 24):
            for i in range(4):
                f = {"range_5m_pct": rnd.random(), "coin_class": "meme", "side": "long"}
                if (h * 4 + i) % 2 == 0:
                    f["cm_imb_025_pct"] = rnd.random()                     # 0.5
                if (h * 4 + i) % 10:
                    f["cm_cvd_5m_perp_pct"] = rnd.random()                 # 0.9
                rows.append({"cluster": "%d:long" % h, "hour": h, "side": "long", "key": "k%d-%d" % (h, i), "t0": h * 3600 + i * 60, "filled": 1,
                             "r_net": rnd.gauss(0, 1), "feats": f, "flags": None})
        m = MN.build_menu({"rows": rows, "a": 0, "b": 14 * 86400}, CFG)
        low = dict((d["feature"], d["cover"]) for d in m["dropped"] if d.get("code") == "COVERAGE_LOW")
        self.assertEqual(low["cm_imb_025_pct"], 0.5)
        feats_in = set(it["cond"][0]["feature"] for it in m["items"])
        self.assertNotIn("cm_imb_025_pct", feats_in)
        self.assertIn("cm_cvd_5m_perp_pct", feats_in)
        self.assertLessEqual(m["k"], MN.K_MAX)

    def test_roles_dictionary_only_wave(self):
        """Словарь ролей day_reviewer / retro (candidates.llm_dictionary): признаки cm_ — только волна и только при заполнении ≥ 0.8."""
        rnd = random.Random(9)
        rows = []
        for h in range(14 * 24):
            for i in range(3):
                f = dict((n, rnd.random()) for n in learn_cond.BASE_MENU_NUM)
                f.update((n, rnd.random()) for n in L.CM_FEATURES if not L.SCALES[n] == 1)
                f["cm_wall_pull_15m_pct"] = None if i else rnd.random()     # заполнение 1/3
                rows.append({"cluster": "%d:long" % h, "hour": h, "side": "long", "key": "k%d-%d" % (h, i), "t0": h * 3600 + i * 60, "filled": 1,
                             "r_net": rnd.gauss(0, 1), "feats": f, "flags": None})
        dic = CA.llm_dictionary({"rows": rows, "a": 0, "b": 14 * 86400}, CFG)
        cm = set(k for k in dic["features"] if k.startswith("cm_"))
        self.assertEqual(cm, set(WAVE) - {"cm_wall_pull_15m_pct"})


# ---------------------------------------------------------------- критерий волны: метод задачи 9 и команда
class PowerMethod(unittest.TestCase):
    def test_reproduces_r2_task9(self):
        """Замер задачи 9 R2: поток 24.6, σ_d 0.93R, среднее базы −0.13R, K = 13 -> скрининг 1.00, holdout 0.98, испытание 0.95, конвейер 0.93."""
        p = MN.pipeline_power(24.6, 0.93, 13, CFG, base_mean=-0.13, sd_run=1.0)
        self.assertAlmostEqual(p["screen"], 1.0, delta=0.01)
        self.assertAlmostEqual(p["holdout"], 0.98, delta=0.01)
        self.assertAlmostEqual(p["trial"], 0.95, delta=0.01)
        self.assertAlmostEqual(p["pipeline"], 0.93, delta=0.01)
        q = MN.pipeline_power(22.1, 0.93, 25, CFG, base_mean=-0.13, sd_run=1.0)      # design.md R1.7, «Мощность»: с волной не ниже 0.92
        self.assertGreaterEqual(q["pipeline"], 0.9)
        self.assertLessEqual(p["pipeline"] - q["pipeline"], 0.05)
        self.assertEqual(MN.pipeline_power(5.0, 0.93, 13, CFG)["pipeline"], 0.0)       # n < min_n_menu на окне 14 суток
        self.assertLess(MN.pipeline_power(24.6, 0.93, 13, CFG, effect=0.2)["pipeline"], 0.5)

    def test_spearman(self):
        self.assertAlmostEqual(MN.spearman([1, 2, 3, 4], [10, 20, 30, 40])[0], 1.0)
        self.assertAlmostEqual(MN.spearman([1, 2, 3, 4], [4, 3, 2, 1])[0], -1.0)
        self.assertAlmostEqual(MN.spearman([1, 2, 2, 3], [1, 2, 3, 4])[0], 0.9486833, places=6)     # связки — средние ранги
        self.assertEqual(MN.spearman([1, None, 3], [1, 2, 3]), (None, 2))
        self.assertEqual(MN.spearman([1, 1, 1], [1, 2, 3])[0], None)


T0W = 1790812800            # 2026-10-01T00:00:00Z
DAY = 86400


def build_wave_db(root, days=16, cm_from_day=0, corr=False, seed=3):
    """База знаний с cluster_runs P2 (ctx c1) за days суток: 2 стороны × 6 прогонов в час; признаки меню и волны (с дня cm_from_day)."""
    for n in ("learnloop.json", "shadow_policies.json", "memory.json"):
        shutil.copy(os.path.join(H.PROJECT, "config", n), os.path.join(root, "config", n))
    c = store.open_rw(root)
    store2.migrate_v2a(c, root, explicit=True, backup=False, now=T0W)
    rnd = random.Random(seed)
    rows = []
    for h in range(days * 24):
        for side in ("long", "short"):
            shift = rnd.gauss(0, 0.5)
            for i in range(6):
                t0 = T0W + h * 3600 + i * 500 + (250 if side == "short" else 0)
                f = {"range_5m_pct": round(rnd.uniform(0, 2), 4), "ret_5m_pct": round(rnd.gauss(0, 1), 4), "turn_surge_5m": round(rnd.uniform(0.5, 3), 4),
                     "chg_24h_pct": round(rnd.gauss(0, 8), 4), "spread_bps": round(rnd.uniform(0.5, 8), 4), "funding_pct": round(rnd.gauss(0, 0.01), 6),
                     "coin_class": ("meme", "alt_small", "alt_mid")[rnd.randrange(3)], "utc_session": "asia", "side": side, "side_rel": "with_dir"}
                if h >= cm_from_day * 24 and rnd.random() < 0.9:
                    f.update({"cm_imb_025_pct": round(f["range_5m_pct"] * 10 + rnd.gauss(0, 0.05), 1) if corr else round(rnd.gauss(0, 20), 1),
                              "cm_oi_liq_5m_pct": round(rnd.gauss(0, 0.3), 3), "cm_cvd_5m_perp_pct": round(rnd.gauss(0, 15), 1),
                              "cm_wall_pull_15m_pct": round(rnd.uniform(0, 60), 1)})
                rows.append({"cluster": "%d:%s" % ((t0 // 3600), side), "hour": t0 // 3600, "side": side, "key": "K-%d-%s-%d" % (h, side, i), "t0": t0,
                             "type": "range_5m", "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1", "env_norm": "mainnet", "filled": 1,
                             "r_net": round(shift + rnd.gauss(0, 1), 4), "feats": f, "flags": None, "closed_ts": t0 + 6 * 3600})
    store2.insert_rows(c, "cluster_runs", rows)
    c.commit()
    c.close()


class MenuWaveCommand(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbwave_")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "config"))
        store2._STATUS_CACHE.clear()

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = learn_cli.main(["menu-wave"] + list(args), root=self.root)
        return code, out.getvalue()

    def test_steady_window_14_days(self):
        """Окно поиска 14 суток (установившееся): (а)–(в) по каждому признаку, (г) мощность с K + 2 × 4 и снижение ≤ 0.05."""
        build_wave_db(self.root, days=17)
        now = T0W + 16 * DAY + 20 * 60                      # b = T0W + 14 сут: окно [T0W, T0W + 14 сут)
        dbp = store.db_path(self.root)
        self.assertTrue(os.path.exists(dbp))
        before = (os.path.getmtime(dbp), os.path.getsize(dbp))
        code, out = self.run_cli("--check", "--json", "--now", str(now))
        self.assertEqual(code, 0, out)
        rep = json.loads(out)
        self.assertEqual(rep["code"], "OK")
        self.assertEqual(rep["window"]["days"], 14.0)
        self.assertEqual(set(rep["feats"]), set(WAVE))
        for f, d in rep["feats"].items():
            self.assertAlmostEqual(d["cover"], 0.9, delta=0.03)
            self.assertTrue(d["a"] and d["b"] and d["c"], (f, d))
            self.assertLess(d["q20"], d["q80"])
            self.assertEqual(set(d["rho"]), set(learn_cond.BASE_MENU_NUM) | (set(WAVE) - {f}))
        pw = rep["power"]
        self.assertTrue(pw["steady"])
        self.assertEqual(pw["wave"]["k"], pw["base"]["k"] + 8)
        self.assertLessEqual(pw["wave"]["k"], MN.K_MAX)
        self.assertGreaterEqual(pw["wave"]["pipeline"], 0.5)
        self.assertLessEqual(pw["drop"], 0.05)
        self.assertGreater(pw["wave"]["flow"], 0)
        self.assertTrue(rep["ok_d"])
        self.assertTrue(rep["ok"])
        # только чтение: файл базы не изменён
        self.assertEqual((os.path.getmtime(dbp), os.path.getsize(dbp)), before)
        code, txt = self.run_cli("--check", "--now", str(now))
        self.assertEqual(code, 0)
        self.assertIn("конвейер", txt)
        self.assertIn("итог: а да, б да, в да, г да", txt)

    def test_first_wave_7_days_and_partial_window(self):
        """Сбор с 8-х суток: заполнение за 7 суток ≥ 0.8, за всё окно — нет; окно поиска 9 суток — (г) не оценивается (None)."""
        build_wave_db(self.root, days=13, cm_from_day=4)
        now = T0W + 11 * DAY + 20 * 60                      # b = T0W + 9 сут
        code, out = self.run_cli("--check", "--json", "--days", "5", "--now", str(now))
        self.assertEqual(code, 0, out)
        rep = json.loads(out)
        self.assertEqual(rep["window"]["fill_days"], 5.0)
        self.assertTrue(rep["ok_a"])
        self.assertFalse(rep["power"]["steady"])
        self.assertIsNone(rep["ok_d"])
        code, out = self.run_cli("--check", "--json", "--now", str(now))
        rep = json.loads(out)
        self.assertFalse(rep["ok_a"])                                   # всё окно 9 суток: ≈ 5/9 × 0.9 < 0.8
        for d in rep["feats"].values():
            self.assertLess(d["cover"], 0.8)

    def test_correlated_feature_fails_c(self):
        build_wave_db(self.root, days=17, corr=True)
        code, out = self.run_cli("--check", "--json", "cm_imb_025_pct", "--now", str(T0W + 16 * DAY + 20 * 60))
        rep = json.loads(out)
        d = rep["feats"]["cm_imb_025_pct"]
        self.assertGreater(abs(d["rho"]["range_5m_pct"]["rho"]), 0.7)
        self.assertFalse(d["c"])
        self.assertFalse(rep["ok"])

    def test_usage_and_no_data(self):
        self.assertEqual(self.run_cli()[0], 2)                                     # без --check
        self.assertEqual(self.run_cli("--check", "cm_bogus")[0], 2)
        self.assertEqual(self.run_cli("--check", "cm_lead_ex")[0], 2)
        for n in ("learnloop.json", "shadow_policies.json"):
            shutil.copy(os.path.join(H.PROJECT, "config", n), os.path.join(self.root, "config", n))
        code, out = self.run_cli("--check", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["db"], "NO_DB")


if __name__ == "__main__":
    unittest.main()
