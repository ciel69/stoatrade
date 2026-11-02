"""Разбор paper 2026-11-02 (XPL): отказ входа на устаревших данных (STALE_DATA до вызова модели) и тренд/режим в досье события и в записи open журнала.
Всё во временных корнях, без сети и LLM."""
import argparse
import contextlib
import copy
import io
import json
import os

import unittest

import _bootstrap  # noqa: F401
from _brain import BrainBase
from tb.brain import validate
from tb.core import bus, journal, tradectx
from tb.logic.candles import CandleBook
from tb.marketdata import structure as S

import test_brief as TB

SCAN_BRIEF = {"ok": True, "brief_id": "B-20261102-0539-01", "scope": "event", "system": {"exec": {"alive": True, "mode": "paper"}, "kill": False},
              "session": {"id": "S-1", "type": "intraday", "min_left": 200}, "room": {"allowed": True}, "limits": {"slots": 2, "max_open_positions": 2},
              "risk_level": {"level": 3, "no_confirm": {"allowed": True, "size_mult_max": 0.5}}, "positions": [], "plans": [],
              "struct": {"s": {"SOL": {"px": 145.2, "reg": "range", "a5_pct": 0.25, "sp": 1.8}}},
              "levels": {"SOL": {"px": 145.2, "atr_5m": 0.36}},
              "recent": [{"seq": 1, "type": "scanner_signal", "sym": "SOL", "d": {"kind": "hot_enter", "px": 145.2, "range_5m_pct": 0.5, "spread_bps": 1.8}}]}
EV = {"sym": "SOL", "kinds": ["hot_enter"], "types": ["scanner_signal"]}


def propose(**kw):
    o = {"schema": "tb.entry/1", "event_seq": 1, "decision": "propose", "sym": "SOL", "side": "long", "horizon": "intraday", "level": 145.0, "stop": 143.4, "tp": [148.2, 150.5],
         "setup": "level_reclaim", "hypothesis": "reclaim-vol-surge", "ttl_min": 30, "confirm": "candle", "thesis": "возврат выше дневного уровня 145.0 после прокола, оборот 3.1x",
         "why": "возврат уровня | RR 2.3, a5 0.25% | стоп 1.1%", "conf": 0.6}
    o.update(kw)
    return o


def with_data(**data):
    b = copy.deepcopy(SCAN_BRIEF)
    b["data"] = dict({"struct": True, "levels": 2, "ready": True, "px": True}, **data)
    return b


class StalePrecheck(unittest.TestCase):
    def pre(self, b, **ctx_over):
        ctx = validate.entry_context(b, EV)
        ctx.update(ctx_over)
        return validate.entry_precheck(ctx)

    def test_stale_price_is_refused_before_model(self):
        self.assertEqual(self.pre(with_data(px_age_s=5421)), "STALE_DATA")              # XPL 02.11: hot/prices старше 90 минут
        self.assertEqual(self.pre(with_data(px_age_s=121)), "STALE_DATA")
        self.assertIsNone(self.pre(with_data(px_age_s=30)))                             # свежие данные: как раньше
        self.assertIsNone(self.pre(with_data(px_age_s=120)))                            # ровно порог не устарел

    def test_threshold_from_config_context(self):
        self.assertIsNone(self.pre(with_data(px_age_s=200), max_data_age_s=300))
        self.assertEqual(self.pre(with_data(px_age_s=200), max_data_age_s=60), "STALE_DATA")
        self.assertEqual(self.pre(with_data(px_age_s=200), max_data_age_s=None), "STALE_DATA")   # нет порога в конфиге: 120 с по умолчанию

    def test_missing_timestamp_and_missing_data(self):
        self.assertIsNone(self.pre(with_data()))                                         # цена есть, метки времени нет: не устарели
        self.assertEqual(self.pre(with_data(px=False)), "STALE_DATA")                   # цены монеты нет ни у сканера, ни у фида
        self.assertIsNone(self.pre(SCAN_BRIEF))                                          # досье без блока data (старый формат): проверки нет
        self.assertEqual(self.pre(with_data(ready=False, levels=0, px_age_s=5421)), "NO_DATA")   # NO_DATA остаётся первым

    def test_semantic_check_rejects_stale_propose(self):
        o = propose()
        codes = validate.semantic("entry/1", o, validate.entry_context(with_data(px_age_s=600), EV))
        self.assertIn("STALE_DATA", codes)
        self.assertNotIn("STALE_DATA", validate.semantic("entry/1", o, validate.entry_context(with_data(px_age_s=10), EV)))
        self.assertIn("STALE_DATA", validate.ENTRY_PRECHECK_CODES)


class StaleRunner(BrainBase):
    def setUp(self):
        BrainBase.setUp(self)
        self.set_platform(mode="paper")
        self.gw.responses["list_plans"] = {"ok": True, "plans": []}

    def run_scan(self, brief, answer, jid="J-s1"):
        self.gw.brief = brief
        r = self.runner({"ds-flash": [answer]})
        s = self.emit("scanner_signal", {"kind": "patrol", "px": 145.2, "wake": True}, plan=None, sym="SOL")
        j = self.job(jid, seqs=[s], types=("scanner_signal",), key="sym:SOL", plan=None, kinds=["patrol"])
        r.step()
        return self.get_job(j["id"])

    def test_stale_data_skips_without_llm_and_is_journaled(self):
        j = self.run_scan(with_data(px_age_s=5421), propose(event_seq=1))
        self.assertEqual(j["result"]["roles"][0]["code"], "STALE_DATA", j["result"])
        self.assertEqual(self.factory.calls(), 0)                                        # модель не зовём
        self.assertEqual(self.gw.tool_calls("submit_plan"), [])
        d = [x for x in self.decisions() if x["what"] == "brain_decision"]
        self.assertEqual((d[-1]["kind"], d[-1]["res"]["code"]), ("skip", "STALE_DATA"))    # как прочие коды предпроверки

    def test_fresh_data_proposes_as_before(self):
        j = self.run_scan(with_data(px_age_s=5), propose(event_seq=1))
        self.assertEqual(len(self.gw.tool_calls("submit_plan")), 1, j["result"])
        self.assertEqual(self.factory.calls(), 1)

    def test_threshold_from_brain_json(self):
        self.brain_doc["runtime"]["max_data_age_s"] = 600
        self.write_conf()
        self.run_scan(with_data(px_age_s=300), propose(event_seq=1))
        self.assertEqual(len(self.gw.tool_calls("submit_plan")), 1)                      # 300 с < порога владельца 600 с


def _flat_rows(tf_min, n, now, close_of):
    """n свечей tf, последняя формирующаяся (содержит now); close_of(i) -> цена закрытия i-й свечи."""
    step = tf_min * 60
    last = int(now // step) * step
    out = []
    for i in range(n):
        st = last - (n - 1 - i) * step
        c = close_of(i)
        out.append((st * 1000, c, c * 1.001, c * 0.999, c, 10.0, 1000.0))
    return out


class TrendRule(unittest.TestCase):
    def test_rule(self):
        T = tradectx.trend_of
        self.assertEqual(T({"r4h": 0.5, "r24h": -15.0, "a1h_pct": 1.5})["dir"], "down")      # суточный тренд вниз важнее отскока 4ч (XPL)
        self.assertEqual(T({"r4h": 2.0, "r24h": 1.0, "a1h_pct": 1.5})["dir"], "up")
        self.assertEqual(T({"r4h": -0.5, "r24h": 1.0, "a1h_pct": 1.5})["dir"], "flat")
        self.assertEqual(T({"r4h": -1.2, "r24h": None})["dir"], "down")                       # нет ATR: единица 1%
        self.assertIsNone(T({"a1h_pct": 1.0, "r1h": 3.0}))                                     # нет возвратов 4ч/24ч: тренда нет, не выдумываем
        self.assertIsNone(T(None))
        self.assertEqual(T({"r1hc": 0.3, "r4h": 0.1, "r24h": 0.2})["ret_1h_pct"], 0.3)
        self.assertNotIn("r1h", T({"r1h": 9.0, "r4h": 0.1}))                                   # r1h по формирующейся цене не берётся

    def test_regime_class(self):
        self.assertEqual([tradectx.regime_class(x) for x in ("trend_up", "trend_down", "range", "storm", "unknown", "unclear", "bogus")],
                         ["trend", "trend", "range", "volatile", "unknown", "unclear", None])

    def test_only_closed_candles(self):
        """Формирующаяся свеча (резкий выброс в последней строке) не меняет возвраты тренда: r1hc, r4h, r24h по закрытым свечам."""
        now = TB.NOW

        def build(spike):
            b = CandleBook()
            b.seed(60, _flat_rows(60, 200, now, lambda i: (50.0 if spike and i == 199 else 100.0 - (199 - i) * 0.1)), now=now)
            b.seed(5, _flat_rows(5, 300, now, lambda i: (50.0 if spike and i == 299 else 100.0 - (299 - i) * 0.01)), now=now)
            return S.analyze(b, now)
        a, b = build(False), build(True)
        for k in ("r1hc", "r4h", "r24h"):
            self.assertEqual(a[k], b[k], k)
        self.assertNotEqual(a["r1h"], b["r1h"])                                                  # r1h структуры берёт цену формирующейся свечи
        self.assertEqual(tradectx.trend_of(a), tradectx.trend_of(b))
        self.assertEqual(tradectx.trend_of(a)["dir"], "up")


class TrendBrief(TB.Base):
    def structure(self, rows, age=10):
        w = TB.w
        w(self.R("runtime", "structure.json"), {"schema": "tb.structure/1", "ts_epoch": TB.NOW - age, "symbols": rows})

    def ev(self, sym="XPL"):
        s0 = bus.emit("scanner_signal", {"kind": "patrol", "wake": True}, sym=sym, root=self.root, now=TB.NOW - 20)
        return self.build("event", since=0, seqs=[s0], sym=sym)

    def test_trend_block_for_event_coin_and_btc(self):
        self.structure({"XPL": {"px": 0.071, "reg": "trend_down", "a1h_pct": 1.5, "r1h": 0.4, "r1hc": 0.3, "r4h": 0.8, "r24h": -15.6, "ts": TB.NOW - 10},
                        "BTC": {"px": 80000.0, "reg": "range", "a1h_pct": 0.4, "r1hc": 0.05, "r4h": 0.1, "r24h": -0.3, "ts": TB.NOW - 10}})
        t = self.ev()["trend"]
        self.assertEqual((t["dir"], t["ret_1h_pct"], t["ret_4h_pct"], t["ret_24h_pct"]), ("down", 0.3, 0.8, -15.6))
        self.assertEqual(t["btc"]["dir"], "flat")
        self.assertNotIn("stale_s", t)

    def test_no_structure_no_trend(self):
        b = self.ev()
        self.assertNotIn("trend", b)                                                             # нет structure.json: блока нет (null), не выдумываем
        self.structure({"BTC": {"px": 80000.0, "reg": "range", "a1h_pct": 0.4, "r4h": 0.1, "r24h": -0.3}})
        t = self.ev()["trend"]
        self.assertIsNone(t["dir"])                                                              # по монете записи нет: поля null, BTC есть
        self.assertEqual(t["btc"]["dir"], "flat")

    def test_data_price_age_from_scanner_files(self):
        self.structure({"XPL": {"px": 0.071, "reg": "range", "a1h_pct": 1.5, "r4h": 0.1, "r24h": 0.2}})
        TB.w(self.R("runtime", "prices.json"), {"ts_epoch": TB.NOW - 5421, "prices": {"XPLUSDT": 0.07119}})
        TB.w(self.R("runtime", "hot.json"), {"ts_epoch": TB.NOW - 5421, "top": [TB.hot_row("XPL", price=0.07119)], "blocked": []})
        d = self.ev()["data"]
        self.assertEqual((d["px"], d["px_age_s"]), (True, 5421))
        TB.w(self.R("runtime", "prices.json"), {"ts_epoch": TB.NOW - 4, "prices": {"XPLUSDT": 0.07119}})
        self.assertEqual(self.ev()["data"]["px_age_s"], 4)                                       # берётся самая свежая цена
        d = self.ev("ZZZ")["data"]
        self.assertFalse(d["px"])                                                                # монеты нет ни в одном источнике
        self.assertNotIn("px_age_s", d)


class JournalRegime(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile
        self.root = tempfile.mkdtemp(prefix="tbtrend_")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "runtime"))
        os.makedirs(os.path.join(self.root, "state"))
        old = (journal.PATH, os.environ.get("TB_ROOT"))
        os.environ["TB_ROOT"] = self.root
        journal.PATH = os.path.join(self.root, "state", "trades.jsonl")

        def restore():
            journal.PATH = old[0]
            if old[1] is None:
                os.environ.pop("TB_ROOT", None)
            else:
                os.environ["TB_ROOT"] = old[1]
        self.addCleanup(restore)
        self.now = journal.time.time()
        self.wj("hot.json", {"ts_epoch": self.now - 5, "top": [{"sym": "XPL", "price": 0.0712, "rank": 2, "tradable": True}], "blocked": []})
        self.wj("structure.json", {"schema": "tb.structure/1", "ts_epoch": self.now - 30, "symbols": {
            "XPL": {"px": 0.0712, "reg": "trend_down", "a1h_pct": 1.5, "r1hc": 0.3, "r4h": 0.8, "r24h": -15.6},
            "BTC": {"px": 80000.0, "reg": "range", "a1h_pct": 0.4, "r4h": 0.1, "r24h": -0.3}}})

    def wj(self, name, obj):
        with open(os.path.join(self.root, "runtime", name), "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def open(self, regime="unknown"):
        a = argparse.Namespace(sym="XPL", side="long", entry=0.0712, lev=10.0, margin=11.8, sl=0.0696, tp=0.0731, thesis="trend_pullback", news="", regime=regime,
                               tags="bybit,paper,plan", mode="paper", id="BB-P-XPL-T", session=None, stype="intraday", venue="bybit", ts_open=None)
        with contextlib.redirect_stdout(io.StringIO()):
            journal.cmd_open(a)
        return journal._load()[0]["BB-P-XPL-T"]

    def test_open_fills_regime_and_trend_from_structure(self):
        t = self.open()                                                                          # исполнитель пишет regime="unknown"
        self.assertEqual(t["regime"], "trend")                                                   # trend_down -> trend (словарь памяти случаев)
        self.assertEqual((t["ctx"]["regime"], t["ctx"]["reg_1h"]), ("trend", "trend_down"))
        self.assertEqual((t["ctx"]["trend"]["dir"], t["ctx"]["trend"]["btc"]), ("down", "flat"))
        self.assertIn("structure", t["ctx"]["src"])
        from tb.memory import cases, features
        self.assertIn(t["regime"], cases.REGIMES)
        self.assertEqual(features.situation_from_trade(t, None, {})["regime"], "trend")         # память случаев получает режим, а не unknown

    def test_explicit_regime_kept(self):
        self.assertEqual(self.open(regime="range")["regime"], "range")

    def test_no_structure_keeps_unknown(self):
        os.remove(os.path.join(self.root, "runtime", "structure.json"))
        t = self.open()
        self.assertEqual(t["regime"], "unknown")
        self.assertNotIn("trend", t["ctx"])


if __name__ == "__main__":
    unittest.main()
