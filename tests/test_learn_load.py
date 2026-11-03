"""tb.learn.load (R0.2, задача 2): фильтр сделок мозга (свойство 2), режимы, exclude_trades.json, перестановка строк, случаи пропусков (mode_unknown),
подпись источников, чтение без изменения объектов кеша памяти, отсутствие файлов."""
import copy
import json
import os
import random
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from _memory_helpers import mk
from tb.learn import load as L
from tb.memory import cases


def tr(i, r=1.0, mode="paper", plan="PL-%d", tags=None, ts_close="2026-10-28T12:00:%02dZ", pnl=True):
    tid = "T%d-%s" % (i, mode)
    o = {"ev": "open", "id": tid, "ts": "2026-10-28T10:00:00Z", "sym": "SOL", "side": "long", "mode": mode, "risk_usdt": 1.0, "tags": tags or []}
    if plan:
        o["plan_id"] = plan % i if "%" in plan else plan
    c = {"ev": "close", "id": tid, "ts_close": ts_close % (i % 60) if "%" in ts_close else ts_close, "reason": "sl", "fees_usdt": 0.01}
    if pnl:
        c["pnl_usdt"] = r
        c["r"] = r
    return [o, c]


class Root(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tblearn_")
        os.makedirs(os.path.join(self.root, "state"))
        os.makedirs(os.path.join(self.root, "config"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write_trades(self, recs):
        with open(os.path.join(self.root, "state", "trades.jsonl"), "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def put(self, lst):
        for c in lst:
            cases.append_new(c, root=self.root)


class TestBrainTrades(Root):
    def test_filters_and_counters(self):
        recs = []
        recs += tr(1)                                               # мозг
        recs += tr(2, tags=["shadow"])                              # shadow
        recs += tr(3, tags=["manual-pick"])                         # ручная с планом
        recs += tr(4, plan=None)                                    # без plan_id
        recs += tr(5, tags=["plan:PL-5"], plan=None)                # plan_id только в теге: это сделка мозга
        recs += tr(6, r=1.0, pnl=False)                             # без pnl: load_trades отбросит сама (не в счётчике)
        recs += tr(7, tags=["manual", "shadow"])                    # первая причина: shadow
        recs += tr(8, mode="live")                                  # другой режим
        self.write_trades(recs)
        out, ex = L.brain_trades(self.root, "paper")
        self.assertEqual([t["id"] for t in out], ["T1-paper", "T5-paper"])
        self.assertEqual(ex, {"shadow": 2, "manual": 1, "no_plan": 1, "no_r": 0})
        self.assertEqual(list(ex), ["shadow", "manual", "no_plan", "no_r"])
        for t in out:
            self.assertIsInstance(t["t_close"], int)
            self.assertGreater(t["t_close"], 0)

    def test_no_r_counted(self):
        o, c = tr(1)
        c.pop("r")
        c["pnl_usdt"] = 0.5
        o["risk_usdt"] = 0                                          # R не вычислить
        self.write_trades([o, c])
        out, ex = L.brain_trades(self.root, "paper")
        self.assertEqual(out, [])
        self.assertEqual(ex["no_r"], 1)

    def test_exclude_trades_json(self):
        self.write_trades(tr(1) + tr(2) + tr(3))
        with open(os.path.join(self.root, "state", "exclude_trades.json"), "w", encoding="utf-8") as f:
            json.dump({"ids": ["T2-paper"]}, f)
        out, ex = L.brain_trades(self.root, "paper")
        self.assertEqual([t["id"] for t in out], ["T1-paper", "T3-paper"])
        self.assertEqual(sum(ex.values()), 0)

    def test_modes_do_not_mix(self):
        self.write_trades(tr(1) + tr(2, mode="live") + tr(3, mode="testnet"))
        for m in ("paper", "live", "testnet"):
            out, _ = L.brain_trades(self.root, m)
            self.assertEqual([t["mode"] for t in out], [m])
        with self.assertRaises(ValueError):
            L.brain_trades(self.root, "all")

    def test_order_independent_of_row_order(self):
        """Свойство 2: порядок результата не зависит от перестановки строк; сортировка по (эпоха закрытия, id)."""
        rng = random.Random(7)
        for seed in range(20):
            rng = random.Random(seed)
            trades = []
            for i in range(12):
                tc = "2026-10-28T12:00:%02dZ" % rng.choice((0, 5, 5, 9))     # есть равные ts_close
                trades.append(tr(i, r=round(rng.uniform(-1, 2), 2), ts_close=tc))
            flat = [r for p in trades for r in p]
            self.write_trades(flat)
            base, bex = L.brain_trades(self.root, "paper")
            keys = [(t["t_close"], t["id"]) for t in base]
            self.assertEqual(keys, sorted(keys))
            # перестановка пар: открытие всегда раньше закрытия своей сделки
            rng.shuffle(trades)
            self.write_trades([r for p in trades for r in p])
            got, gex = L.brain_trades(self.root, "paper")
            self.assertEqual([t["id"] for t in got], [t["id"] for t in base])
            self.assertEqual(gex, bex)

    def test_tie_by_id(self):
        self.write_trades(tr(2, ts_close="2026-10-28T12:00:00Z") + tr(1, ts_close="2026-10-28T12:00:00Z"))
        out, _ = L.brain_trades(self.root, "paper")
        self.assertEqual([t["id"] for t in out], ["T1-paper", "T2-paper"])

    def test_missing_file_and_junk(self):
        out, ex = L.brain_trades(self.root, "paper")
        self.assertEqual(out, [])
        self.assertEqual(ex, {"shadow": 0, "manual": 0, "no_plan": 0, "no_r": 0})
        with open(os.path.join(self.root, "state", "trades.jsonl"), "w", encoding="utf-8") as f:
            f.write("мусор\n\n[1]\n")
        self.assertEqual(L.brain_trades(self.root, "paper")[0], [])


class TestCases(Root):
    def test_cases_mode_and_filters(self):
        self.put([mk(1, r=1.0, mode="paper"), mk(2, r=-1.0, mode="live"), mk(3, r=0.5, mode="paper", exclude=True),
                  mk(4, r=0.5, mode="paper", flags=["inj_suspect"]), mk(5, r=0.5, mode="paper", role="mechanics"), mk(6, r=2.0, mode="paper")])
        got = L.cases(self.root, "paper")
        self.assertEqual([c["refs"]["journal_id"] for c in got], ["J1", "J6"])
        self.assertEqual([c["refs"]["journal_id"] for c in L.cases(self.root, "live")], ["J2"])

    def test_missing_cases_file(self):
        self.assertEqual(L.cases(self.root, "paper"), [])
        self.assertEqual(L.skip_cases(self.root, "paper"), ([], "no_whatif"))

    def test_read_does_not_mutate_cache(self):
        self.put([mk(1, r=1.0, mode="paper"), mk(2, type="idea", mode="paper", whatif={"net_dir_pct": -0.3})])
        before = copy.deepcopy(cases.load(self.root))
        L.cases(self.root, "paper")
        L.skip_cases(self.root, "paper")
        self.assertEqual(cases.load(self.root), before)


class TestSkipCases(Root):
    def wi(self, v):
        return {"net_dir_pct": v}

    def test_idea_mode_known(self):
        self.put([mk(1, type="idea", mode="paper", whatif=self.wi(-0.5)), mk(2, type="idea", mode="paper", whatif=self.wi(0.4)),
                  mk(3, type="idea", mode="testnet", whatif=self.wi(0.4))])
        got, why = L.skip_cases(self.root, "paper")
        self.assertIsNone(why)
        self.assertEqual(len(got), 2)
        self.assertTrue(all(c["src"]["mode"] == "paper" for c in got))

    def test_migration_skip_live_default_is_mode_unknown(self):
        """Случаи skip, записанные миграцией с mode=live по умолчанию, не подмешиваются в paper: no_data/mode_unknown, а не чужие цифры."""
        self.put([mk(1, type="skip", mode="live", whatif=self.wi(0.7)), mk(2, type="idea", mode="live", whatif=self.wi(0.2))])
        self.assertEqual(L.skip_cases(self.root, "paper"), ([], "mode_unknown"))
        self.assertEqual(L.skip_cases(self.root, "testnet"), ([], "mode_unknown"))

    def test_live_takes_only_idea_not_skip_type(self):
        self.put([mk(1, type="skip", mode="live", whatif=self.wi(0.7)), mk(2, type="idea", mode="live", whatif=self.wi(0.2))])
        got, why = L.skip_cases(self.root, "live")
        self.assertIsNone(why)
        self.assertEqual([c["type"] for c in got], ["idea"])
        self.put([mk(3, type="skip", mode="live", whatif=self.wi(0.7))])
        only = tempfile.mkdtemp(prefix="tblearn_")
        try:
            os.makedirs(os.path.join(only, "state"))
            cases.append_new(mk(1, type="skip", mode="live", whatif=self.wi(0.7)), root=only)
            self.assertEqual(L.skip_cases(only, "live"), ([], "mode_unknown"))
        finally:
            shutil.rmtree(only, ignore_errors=True)

    def test_paper_known_ignores_ambiguous(self):
        self.put([mk(1, type="idea", mode="paper", whatif=self.wi(0.1)), mk(2, type="skip", mode="live", whatif=self.wi(0.9))])
        got, why = L.skip_cases(self.root, "paper")
        self.assertIsNone(why)
        self.assertEqual([c["refs"]["journal_id"] for c in got], ["J1"])

    def test_no_whatif(self):
        self.put([mk(1, type="idea", mode="paper", whatif=None), mk(2, type="idea", mode="paper", whatif={"h_min": 15}), mk(3, type="skip", mode="live")])
        self.assertEqual(L.skip_cases(self.root, "paper"), ([], "no_whatif"))

    def test_taken_idea_excluded(self):
        c = mk(1, type="idea", mode="paper", whatif=self.wi(0.5), flags=["dup_of_trade"])
        d = mk(2, type="idea", mode="paper", whatif=self.wi(0.5))
        d["decision"]["action"] = "enter"
        self.put([c, d, mk(3, type="idea", mode="paper", whatif=self.wi(0.5), exclude=True),
                  mk(4, type="idea", mode="paper", whatif=self.wi(0.5), flags=["inj_suspect"])])
        self.assertEqual(L.skip_cases(self.root, "paper"), ([], "no_whatif"))

    def test_zero_net_dir_pct_kept(self):
        self.put([mk(1, type="idea", mode="paper", whatif=self.wi(0.0))])
        got, why = L.skip_cases(self.root, "paper")
        self.assertEqual((len(got), why), (1, None))

    def test_explicit_non_live_skip_type_accepted(self):
        self.put([mk(1, type="skip", mode="paper", whatif=self.wi(-0.1))])
        got, why = L.skip_cases(self.root, "paper")
        self.assertEqual((len(got), why), (1, None))
        self.assertEqual(L.skip_cases(self.root, "testnet")[1], "no_whatif")


class TestUsageAndSignature(Root):
    def write_calls(self, rows):
        with open(os.path.join(self.root, "state", "llm_calls.jsonl"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def test_usage_rows(self):
        self.write_calls([{"t": 100.0, "ts": "x", "in": 10, "out": 2, "usd": 0.01}, {"kind": "fmt", "t": 150.0, "ts": "x"},
                          {"t": 200.0, "in": 5, "out": 1, "usd": 0.0}, {"no": "t"}])
        rows, info = L.usage_rows(self.root, 150.0)
        self.assertEqual([r["t"] for r in rows], [200.0])
        self.assertEqual(info, {"truncated": False, "fmt_skipped": 1})
        rows, info = L.usage_rows(self.root, None)
        self.assertEqual(len(rows), 2)

    def test_usage_missing(self):
        self.assertEqual(L.usage_rows(self.root, 0), ([], {"truncated": False, "fmt_skipped": 0}))

    def test_usage_truncated_flag(self):
        self.write_calls([{"t": 1.0, "in": 1, "out": 1, "usd": 0.0}])
        from tb.llm import usage
        old = usage.MAX_READ
        usage.MAX_READ = 5
        try:
            self.assertTrue(L.usage_rows(self.root, None)[1]["truncated"])
        finally:
            usage.MAX_READ = old

    def test_signature_changes(self):
        s0 = L.source_signature(self.root)
        self.assertEqual(s0, (None, None, None, None, None))
        self.write_trades(tr(1))
        s1 = L.source_signature(self.root)
        self.assertNotEqual(s0, s1)
        self.assertEqual(s1, L.source_signature(self.root))
        self.write_trades(tr(1) + tr(2))
        self.assertNotEqual(s1, L.source_signature(self.root))
        with open(os.path.join(self.root, "config", "learning.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        self.assertIsNotNone(L.source_signature(self.root)[4])


if __name__ == "__main__":
    unittest.main()
