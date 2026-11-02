"""R1, задача 13 (Requirement 11): признаки случаев мозга. snap в сохранённом досье (в том числе урезанном), brief_id -> snap -> fng/dvol/risk_off/blackout,
confirmed из плана и из trigger_fired, рыночные поля из ctx и из события сканера, старые случаи без brief_id остаются null, `./tb memory coverage`."""
import contextlib
import io
import json
import os
import types
import unittest

import _bootstrap  # noqa: F401
from _memory_helpers import TmpRoot, mk
from tb import paths
from tb.brain import brief
from tb.core import bus, decisions
from tb.memory import cases, cli, features, migrate

BID = "B-20261102-0959-01"
PLAN = "PL-1102-SOL-01"
CTX = {"ts_epoch": 1793613000, "risk_off": True, "sources": {"fear_greed": {"value": 55}, "dvol_ETH": {"value": 61.5}}}
WHEN_CANDLE = {"all": [{"kind": "spread_bps", "lte": 5}, {"kind": "candle_close", "tf": "5m", "dir": "above", "level": 100.0}]}


def wj(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


class Base(TmpRoot):
    def setUp(self):
        TmpRoot.setUp(self)
        os.makedirs(os.path.join(self.root, "runtime"))
        self.rows = []

    def context(self, obj=CTX):
        wj(os.path.join(self.root, "runtime", "context.json"), obj)

    def big_brief(self, bid=BID, **kw):
        b = {"brief_id": bid, "ts": "2026-11-02T09:59:00Z", "scope": "event", "trunc": False, "system": {"ok": True}, "account": {"eq": 1},
             "market": {"risk_off": True, "blackout": {"event": "CPI", "in_min": 0}}, "hot": {"top": ["x" * 100] * 200}}
        b.update(kw)
        return b

    def trade(self, jid, ts="2026-11-02T10:00:00Z", **kw):
        op = {"ev": "open", "id": jid, "ts_open": ts, "sym": "SOL", "side": "long", "entry": 100.0, "lev": 3.0, "margin": 5.0, "notional": 15.0, "sl": 98.0, "tp": 104.0,
              "risk_usdt": 0.3, "thesis": "тест", "regime": "unknown", "tags": ["bybit", "paper", "plan"], "mode": "paper"}
        op.update(kw)
        self.rows += [op, {"ev": "close", "id": jid, "ts_close": "2026-11-02T10:30:00Z", "exit": 98.0, "reason": "sl", "pnl_usdt": -0.3, "r": -1.0}]

    def sync(self):
        with open(os.path.join(self.root, "state", "trades.jsonl"), "w", encoding="utf-8") as f:
            for r in self.rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        rep = migrate.migrate(self.root, cfg=self.cfg)
        self.assertEqual(rep["errors"], [])
        return dict((c["refs"]["journal_id"], c) for c in cases.load(self.root) if c["type"] == "trade")

    def plan_spec(self, pid=PLAN, when=WHEN_CANDLE, event_seq=None):
        entry = {"how": {"type": "market"}}
        if when is not None:
            entry["when"] = when
        src = {"kind": "scanner"}
        if event_seq is not None:
            src["event_seq"] = event_seq
        wj(paths.plans(pid, "spec.json", root=self.root), {"id": pid, "symbol": "SOL", "entry": entry, "source": src})

    def link(self, pid=PLAN, bid=BID, seq=None):
        basis = {"brief_id": bid}
        if seq is not None:
            basis["event_seq"] = seq
        decisions.append({"actor": "owner", "kind": "note", "what": "plan_brief", "plan": pid, "basis": basis, "res": {"ok": True, "code": "OK"}}, root=self.root)


class TestSnapInSavedBrief(Base):
    def test_snap_in_full_and_trimmed_brief(self):
        self.context()
        small = self.big_brief(hot=None)
        p = brief.save(small, root=self.root, now=1)
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual(d["snap"], {"fng": 55, "dvol": 61.5, "risk_off": True, "blackout": True})
        self.assertNotIn("snap", small)                                                            # возвращаемое моделью досье не меняется
        big = self.big_brief(bid="B-20261102-0959-02")
        p = brief.save(big, root=self.root, now=1)
        with open(p, encoding="utf-8") as f:
            raw = f.read()
        d = json.loads(raw)
        self.assertTrue(d["trunc"])
        self.assertNotIn("hot", d)
        self.assertNotIn("market", d)                                                              # урезано, а snap остался
        self.assertEqual(d["snap"], {"fng": 55, "dvol": 61.5, "risk_off": True, "blackout": True})
        self.assertLessEqual(len(raw.encode("utf-8")), brief.BRIEF_MAX_BYTES)
        self.assertLessEqual(len(json.dumps(d["snap"]).encode("utf-8")), 300)

    def test_snap_without_context_or_market(self):
        p = brief.save(self.big_brief(market=None, hot=None), root=self.root, now=1)
        with open(p, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["snap"], {})                                             # блок есть всегда, пустой, если источников нет
        p = brief.save(self.big_brief(bid="B-20261102-0959-03", market={"risk_off": False}, hot=None), root=self.root, now=1)
        with open(p, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["snap"], {"blackout": False})

    def test_bad_brief_id_not_read(self):
        self.assertIsNone(migrate._brief(self.root, "../state/trades"))
        self.assertIsNone(migrate._brief(self.root, None))
        self.assertIsNone(migrate._brief(self.root, "B-20261102-0000-09"))                         # файла нет


class TestApplyBrief(Base):
    def test_snap_fills_features_and_does_not_overwrite(self):
        sit = features.finalize({"sym": "SOL", "side": "long", "fng": 40})
        got = features.apply_brief(sit, {"snap": {"fng": 55, "dvol": 61.5, "risk_off": False, "blackout": True}})
        self.assertEqual((got["fng"], got["dvol"], got["risk_off"], got["blackout"]), (40, 61.5, False, True))
        self.assertNotIn("dvol", got["missing"])
        self.assertNotIn("risk_off", got["missing"])
        self.assertEqual(features.apply_brief(sit, {"snap": {}})["fng"], 40)                        # пустой snap: ничего не затирается
        self.assertEqual(features.apply_brief(sit, {"sentiment": {"fng": {"v": 70}}})["fng"], 40)    # прежний формат досье работает как раньше


class TestTradeCases(Base):
    def test_brief_id_to_snap_to_features(self):
        self.context()
        brief.save(self.big_brief(), root=self.root, now=1)                                        # урезанное досье: snap в нём есть
        self.plan_spec()
        self.link()
        self.trade("J1", plan_id=PLAN)
        c = self.sync()["J1"]
        self.assertEqual(c["refs"]["brief_id"], BID)
        s = c["situation"]
        self.assertEqual((s["fng"], s["dvol"], s["risk_off"], s["blackout"]), (55, 61.5, True, True))

    def test_brief_id_from_journal_open(self):
        self.context()
        brief.save(self.big_brief(), root=self.root, now=1)
        self.trade("J2", brief_id=BID)
        c = self.sync()["J2"]
        self.assertEqual((c["refs"]["brief_id"], c["situation"]["fng"]), (BID, 55))

    def test_old_case_without_brief_id_stays_null(self):
        self.context()
        brief.save(self.big_brief(), root=self.root, now=1)
        self.trade("J3", plan_id=None, tags=["bybit", "paper"])
        c = self.sync()["J3"]
        s = c["situation"]
        self.assertIsNone(c["refs"].get("brief_id"))
        self.assertIsNone(c["refs"].get("event_seq"))
        for k in ("fng", "dvol", "risk_off", "blackout", "confirmed", "range_5m_pct", "spread_bps", "funding_pct", "turn_surge_5m", "chg_24h_pct"):
            self.assertIsNone(s.get(k), k)
        self.assertEqual(s.get("confirm_kind"), "unknown")

    def test_link_without_saved_brief_keeps_null_but_ref(self):
        self.plan_spec()
        self.link()
        self.trade("J4", plan_id=PLAN)
        c = self.sync()["J4"]
        self.assertEqual(c["refs"]["brief_id"], BID)                                               # ссылка есть, файла досье нет (хранение 14 дней): признаки null
        self.assertIsNone(c["situation"].get("fng"))

    def test_confirmed_from_plan(self):
        self.plan_spec("PL-1102-SOL-01", WHEN_CANDLE)
        self.plan_spec("PL-1102-SOL-02", None)
        self.trade("C1", plan_id="PL-1102-SOL-01")
        self.trade("C2", plan_id="PL-1102-SOL-02")                                                 # без when и без тега no-confirm (старый исполнитель)
        self.trade("C3", plan_id="PL-1102-SOL-01", tags=["bybit", "paper", "plan", "no-confirm"])  # тег главнее плана
        got = self.sync()
        self.assertEqual((got["C1"]["situation"]["confirmed"], got["C1"]["situation"]["confirm_kind"]), (True, "candle_close"))
        self.assertEqual((got["C2"]["situation"]["confirmed"], got["C2"]["situation"]["confirm_kind"]), (False, "none"))
        self.assertEqual((got["C3"]["situation"]["confirmed"], got["C3"]["situation"]["confirm_kind"]), (False, "none"))

    def test_confirmed_from_trigger_fired_when_plan_file_gone(self):
        bus.emit("trigger_fired", {"cond": "candle_close", "px": 100.0}, plan="PL-1102-SOL-09", sym="SOL", root=self.root, now=1793613000)
        self.trade("T1", plan_id="PL-1102-SOL-09")
        c = self.sync()["T1"]
        self.assertEqual((c["situation"]["confirmed"], c["situation"]["confirm_kind"]), (True, "candle_close"))

    def test_market_fields_from_ctx_then_scanner_event(self):
        bus.emit("scanner_signal", {"kind": "range_5m", "range_5m_pct": 1.7, "ret_5m_pct": 0.9, "spread_bps": 4.0, "turn_surge_5m": 5.2, "funding_pct": 0.01, "wake": True},
                 sym="SOL", root=self.root, now=1793613000)                                        # seq 1
        self.plan_spec(event_seq=1)
        self.trade("M1", plan_id=PLAN, ctx={"range_5m_pct": 2.0, "spread_bps": 3.0})
        self.trade("M2", plan_id=PLAN, ctx={})                                                     # ctx входа пуст: всё из события
        got = self.sync()
        s1, s2 = got["M1"]["situation"], got["M2"]["situation"]
        self.assertEqual((s1["range_5m_pct"], s1["spread_bps"]), (2.0, 3.0))                       # ctx главнее события
        self.assertEqual((s1["turn_surge_5m"], s1["funding_pct"]), (5.2, 0.01))                    # чего нет в ctx, берётся из события
        self.assertEqual((s2["range_5m_pct"], s2["spread_bps"], s2["ret_5m_pct"]), (1.7, 4.0, 0.9))
        self.assertIsNone(s2.get("chg_24h_pct"))                                                       # в событии сканера поля нет
        self.assertEqual(got["M1"]["refs"]["event_seq"], 1)

    def test_event_seq_from_link_wins_over_plan_source(self):
        bus.emit("scanner_signal", {"kind": "range_5m", "range_5m_pct": 1.1, "wake": True}, sym="SOL", root=self.root, now=1793613000)    # seq 1
        bus.emit("scanner_signal", {"kind": "range_5m", "range_5m_pct": 2.2, "wake": True}, sym="SOL", root=self.root, now=1793613001)    # seq 2
        self.plan_spec(event_seq=1)
        self.link(seq=2)
        self.trade("E1", plan_id=PLAN, ctx={})
        c = self.sync()["E1"]
        self.assertEqual((c["refs"]["event_seq"], c["situation"]["range_5m_pct"]), (2, 2.2))

    def test_existing_case_is_not_rebuilt_with_new_sources(self):
        self.trade("X1", plan_id=PLAN)
        first = self.sync()["X1"]
        self.plan_spec()
        self.link()
        again = self.sync()["X1"]
        self.assertEqual(first["situation"], again["situation"])                                   # уже записанный случай неизменен (миграция идемпотентна)


class TestGatewayLink(Base):
    def ctx(self):
        return types.SimpleNamespace(root=self.root, agent=types.SimpleNamespace(actor="owner"))

    def test_link_written_for_accepted_plan_with_brief(self):
        from tb.gateway import catalog
        catalog._link_brief(self.ctx(), {"brief_id": BID, "event_seq": 7}, {"ok": True, "plan_id": PLAN})
        rows = [d for d in decisions.read(limit=0, root=self.root) if d.get("what") == "plan_brief"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["plan"], rows[0]["basis"]), (PLAN, {"brief_id": BID, "event_seq": 7}))
        self.assertEqual(migrate.read_sources(self.root)["plan_links"][PLAN], {"brief_id": BID, "event_seq": 7})

    def test_no_link_when_rejected_or_no_brief(self):
        from tb.gateway import catalog
        catalog._link_brief(self.ctx(), {"brief_id": BID}, {"ok": False, "plan_id": PLAN})
        catalog._link_brief(self.ctx(), {"brief_id": BID}, {"ok": True})
        catalog._link_brief(self.ctx(), {}, {"ok": True, "plan_id": PLAN})
        self.assertEqual([d for d in decisions.read(limit=0, root=self.root) if d.get("what") == "plan_brief"], [])


class TestCoverageCommand(Base):
    def run_cov(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["coverage"] + list(argv), root=self.root)
        return code, buf.getvalue()

    def test_coverage_shares_by_type_and_since(self):
        self.put([mk(0, confirmed=True, range_5m_pct=1.0, spread_bps=3.0, ts="2026-11-01T10:00:00Z"),
                  mk(1, confirmed=None, range_5m_pct=None, spread_bps=3.0, ts="2026-11-02T10:00:00Z"),
                  mk(2, confirmed=True, range_5m_pct=None, spread_bps=None, ts="2026-11-02T11:00:00Z"),
                  mk(3, type="skip", confirmed=None, ts="2026-11-02T12:00:00Z", spread_bps=None)])
        code, out = self.run_cov()
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertEqual(d["n"], 4)
        self.assertAlmostEqual(d["features"]["confirmed"], 2 / 4)
        self.assertAlmostEqual(d["features"]["range_5m_pct"], 2 / 4)
        self.assertEqual(d["by_type"]["trade"]["n"], 3)
        self.assertAlmostEqual(d["by_type"]["trade"]["features"]["spread_bps"], 1 / 3)
        self.assertAlmostEqual(d["by_type"]["skip"]["features"]["spread_bps"], 1.0)
        self.assertNotIn("sym", d["features"])
        self.assertIn("risk_off", d["features"])
        self.assertFalse(d["trade_check"]["ok"])                                                   # пустых больше 20%
        d = json.loads(self.run_cov("--since", "2026-11-02")[1])
        self.assertEqual((d["n"], d["since"]), (3, "2026-11-02"))
        code, text = self.run_cov("--text")
        self.assertIn("confirmed", text)

    def test_coverage_check_ok_when_filled(self):
        self.put([mk(i, confirmed=True, range_5m_pct=1.0, spread_bps=3.0) for i in range(5)])
        d = json.loads(self.run_cov()[1])
        self.assertTrue(d["trade_check"]["ok"])

    def test_coverage_empty_store(self):
        d = json.loads(self.run_cov()[1])
        self.assertEqual((d["n"], d["by_type"], d["trade_check"]), (0, {}, None))


if __name__ == "__main__":
    unittest.main()
