"""Тема `cmkt` и монета по запросу (tb.dash2.topics.cmkt / cmkt_symbol; R1.7, задача 29): сводный рынок на экране «Рынок»
читается только из снимка через tb.core.cmktdoc, устаревший снимок не даёт чисел, монета вне фокуса — NOT_FOCUS.
Временные корни, сети нет."""
import json
import os
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
import test_cmktdoc as C
from tb.core import cmktdoc
from tb.dash2 import topics

NOW = 1793750400                     # 2026-11-04T00:00:00Z


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbcmkt_dash_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def snap(self, ts=NOW, syms=None):
        return C.write_snap(self.root, ts, syms if syms is not None else {
            "BTC": C.snap_row(), "SOL": C.snap_row(tier="light", flags=("warmup",))})


class TestTopic(Base):
    def test_fresh_snapshot(self):
        self.snap()
        rep = topics.cmkt(self.root, NOW)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["state"], "ok")
        self.assertEqual((rep["ts"], rep["age_s"]), (NOW, 0.0))
        self.assertEqual(rep["n_ex"], {"perp": 5, "spot": 5})
        self.assertEqual(rep["n_syms"], 2)
        self.assertEqual([c["sym"] for c in rep["coins"]], ["BTC", "SOL"])          # ярус deep первым
        self.assertEqual(rep["coins"][0]["tier"], "deep")
        self.assertEqual(rep["coins"][1]["flags"], ["warmup"])
        self.assertEqual(len(rep["venues"]), len(C.MKTS))
        v = [x for x in rep["venues"] if x["mkt"] == C.MKTS[0]][0]
        self.assertEqual((v["code"], v["lag_ms"], v["age_s"]), ("OK", 100, 0.7))

    def test_stale_snapshot_has_no_numbers(self):
        self.snap()
        rep = topics.cmkt(self.root, NOW + 10)
        self.assertEqual((rep["ok"], rep["state"]), (False, "stale"))
        self.assertEqual((rep["coins"], rep["venues"]), ([], []))
        self.assertEqual(rep["warn"], ["STALE"])
        self.assertIsNone(rep["ts"])

    def test_no_snapshot(self):
        rep = topics.cmkt(self.root, NOW)
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["coins"], [])


class TestSymbol(Base):
    def test_full_answer(self):
        self.snap()
        rep = topics.cmkt_symbol(self.root, "BTC", NOW)
        self.assertTrue(rep["ok"], rep.get("warn"))
        self.assertEqual((rep["sym"], rep["ts"]), ("BTC", NOW))
        self.assertEqual(rep["n_ex"], 10)
        self.assertIn("all", rep["book"]["bands"])
        self.assertTrue(rep["book"]["walls"]["bid"])

    def test_not_focus(self):
        self.snap()
        rep = topics.cmkt_symbol(self.root, "NOPE", NOW)
        self.assertEqual((rep["ok"], rep["warn"]), (False, ["NOT_FOCUS"]))

    def test_stale(self):
        self.snap()
        rep = topics.cmkt_symbol(self.root, "BTC", NOW + 10)
        self.assertEqual((rep["ok"], rep["warn"]), (False, ["STALE"]))
        self.assertIsNone(rep.get("book"))

    def test_bad_sym(self):
        self.snap()
        rep = topics.cmkt_symbol(self.root, None, NOW)
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["warn"], ["NOT_FOCUS"])


class TestSymbolsReader(Base):
    def test_sorted_by_tier(self):
        self.snap()
        rows = cmktdoc.symbols(self.root, NOW)
        self.assertEqual([r["sym"] for r in rows], ["BTC", "SOL"])
        self.assertEqual([r["tier"] for r in rows], ["deep", "light"])
        self.assertEqual(rows[0]["n_ex"], 10)

    def test_deep_focus_first_even_when_alphabetically_later(self):
        self.snap(syms={"AAA": C.snap_row(tier="light"), "ZZZ": C.snap_row()})
        self.assertEqual([r["sym"] for r in cmktdoc.symbols(self.root, NOW)], ["ZZZ", "AAA"])
        self.assertEqual([c["sym"] for c in topics.cmkt(self.root, NOW)["coins"]], ["ZZZ", "AAA"])

    def test_stale_and_missing_root(self):
        self.snap()
        self.assertEqual(cmktdoc.symbols(self.root, NOW + 10), [])
        self.assertEqual(cmktdoc.symbols(os.path.join(self.root, "nope"), NOW), [])


if __name__ == "__main__":
    unittest.main()

EX_COLS = ("tier", "st", "a", "lag", "tx", "mid", "mx", "dp", "bands", "bk", "walls", "pulled", "tape", "tk",
           "oi", "liq", "w", "fs", "q")


def ex_row(mid, walls, bands=None, bk=None, st="ok"):
    return ["d", st, 0, 100, NOW, mid, [], 0.2, bands, bk, walls, None, None,
            [mid, mid, mid - 1, mid + 1, 0.0, 0, 0], None, None, 0, 0.0, "USDT"]


def write_ex(root, mkt, kind, sym, row):
    p = os.path.join(root, "runtime", "cmkt", "ex", mkt + ".json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"schema": "tb.cmkt_ex/1", "mkt": mkt, "venue": mkt.split("_")[0], "kind": kind,
                   "cols": list(EX_COLS), "syms": {sym: row}}, f)


class TestBook(Base):
    def test_ladder_from_market_files(self):
        self.snap()
        write_ex(self.root, "binance_perp", "perp", "BTC", ex_row(100.0, [
            ["b", 99.5, 1_000_000, 10], ["b", 99.0, 3_000_000, 20], ["a", 100.5, 2_000_000, 30]]))
        write_ex(self.root, "bybit_perp", "perp", "BTC", ex_row(100.0, [
            ["b", 99.2, 2_000_000, 5], ["a", 100.2, 1_000_000, 6]]))
        write_ex(self.root, "binance_spot", "spot", "BTC", ex_row(100.5, [["a", 101.0, 500_000, 1]]))
        b = cmktdoc.book(self.root, "BTC")
        self.assertEqual(set(b["kinds"]), {"perp", "spot"})
        perp = b["kinds"]["perp"]
        self.assertEqual(perp["source"], "walls")
        self.assertEqual([lv["px"] for lv in perp["walls"]["ask"]], [100.2, 100.5])
        self.assertEqual([lv["px"] for lv in perp["walls"]["bid"]], [99.5, 99.2, 99.0])
        self.assertEqual([lv["cum"] for lv in perp["walls"]["bid"]], [1.0, 3.0, 6.0])
        self.assertEqual(perp["n_mkts"], 2)
        self.assertEqual(perp["best_bid"], 99.0)
        self.assertEqual(perp["best_ask"], 101.0)
        self.assertAlmostEqual(perp["spread_pct"], 2.0, places=6)

    def test_ladder_falls_back_to_bands_and_skips_stale_market(self):
        self.snap()
        row = ex_row(100.0, [], bands=[[1_000_000, 2_000_000], [3_000_000, 4_000_000], None, None])
        write_ex(self.root, "binance_perp", "perp", "BTC", row)
        write_ex(self.root, "bybit_perp", "perp", "BTC", ex_row(100.0, [["a", 100.1, 9_000_000, 1]], st="err"))
        b = cmktdoc.book(self.root, "BTC")
        perp = b["kinds"].get("perp")
        self.assertEqual(perp["source"], "bands")
        self.assertEqual(perp["n_mkts"], 1)
        self.assertEqual([lv["px"] for lv in perp["levels"]["ask"]], [100.25, 100.5])

    def test_symbol_response_carries_ladder(self):
        self.snap()
        write_ex(self.root, "binance_perp", "perp", "BTC", ex_row(100.0, [["a", 100.2, 1_000_000, 1]]))
        rep = topics.cmkt_symbol(self.root, "BTC", NOW)
        self.assertIn("perp", rep["ladder"]["kinds"])
        self.assertEqual(rep["ladder"]["kinds"]["perp"]["walls"]["ask"][0]["px"], 100.2)

    def test_no_snapshot_no_ladder(self):
        self.assertEqual(cmktdoc.book(self.root, "BTC"), None)
