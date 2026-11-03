"""tb.learn.exits (R0.2, задача 6): сверка с expstats.mfe_capture, неполные пути, mfe_r <= 0, ряды, пустые данные."""
import random
import unittest

import _bootstrap  # noqa: F401
from tb.core import expstats as X
from tb.learn import conf, exits as EX

CFG, _ = conf.load(None)


def trade(i, r, mfe, pnl=None):
    pnl = r if pnl is None else pnl
    return {"id": "T%d" % i, "plan_id": "P%d" % i, "parent": "P%d" % i, "plan_rev": None, "rules_key": None, "exp": None, "var": None,
            "setup": "s", "hyp": None, "agent": None, "model": None, "mem": None, "blackout": "обычные", "mode": "paper", "sym": "SOL",
            "stype": "intraday", "side": "long", "r": r, "pnl": pnl, "fees": 0.01, "risk": 1.0, "reason": "sl", "mae_r": None,
            "mfe_r": mfe, "ts_open": None, "ts_close": None, "t_close": 1_790_000_000 + i * 600, "shadow": False, "rules": []}


def rand_trades(seed, n, path_share=1.0):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        r = rng.gauss(0.1, 1.0)
        mfe = abs(rng.gauss(1.0, 0.5)) + 0.1 if rng.random() < path_share else None
        out.append(trade(i, r, mfe))
    return out


def ref(seg):
    return X.group_stats([dict(t, setup="_w", mode="_w") for t in seg if t["mfe_r"] is not None], by="setup")[0]


class TestExits(unittest.TestCase):
    def test_empty_and_no_path(self):
        b = EX.build({"cfg": CFG, "trades": []})
        self.assertEqual((b["status"], b["reason"]), ("no_data", "no_path"))
        self.assertEqual(b["all"]["n_path"], 0)
        self.assertIsNone(b["all"]["capture"]["v"])
        self.assertIsNone(b["all"]["path_share"])
        self.assertEqual(b["series"], {"50": [], "200": []})
        b = EX.build({"cfg": CFG, "trades": [trade(i, 0.3, None) for i in range(40)]})
        self.assertEqual((b["status"], b["reason"]), ("no_data", "no_path"))
        self.assertEqual(b["all"]["path_share"], 0.0)
        self.assertEqual(EX.build({})["status"], "no_data")

    def test_matches_expstats_capture(self):
        for seed in range(20):
            n = random.Random(seed).randint(30, 250)
            ts = rand_trades(seed, n, path_share=0.8)
            b = EX.build({"cfg": CFG, "trades": ts})
            for key, seg in (("50", ts[-50:]), ("200", ts[-200:])):
                w = b["windows"][key]
                path = [t for t in seg if t["mfe_r"] is not None]
                self.assertEqual(w["n"], len(seg))
                self.assertEqual(w["n_path"], len(path))
                self.assertAlmostEqual(w["path_share"], len(path) / len(seg))
                if path:
                    self.assertEqual(w["capture"]["v"], ref(seg)["mfe_capture"])
                    self.assertAlmostEqual(w["mfe"]["v"], sum(t["mfe_r"] for t in path) / len(path), places=9)
            self.assertEqual(b["all"]["capture"]["v"], ref(ts)["mfe_capture"])

    def test_capture_list_wins_only_positive_mfe(self):
        ts = [trade(0, 0.5, 1.0), trade(1, 1.0, 2.0), trade(2, 1.0, 0.0),       # mfe_r <= 0 не входит
              trade(3, 1.0, -0.5), trade(4, -1.0, 2.0), trade(5, 0.8, None)]    # убыток и без пути не входят
        self.assertEqual(sorted(EX.ratios(ts)), [0.5, 0.5])
        c = EX.cell(ts)
        self.assertEqual(c["capture"]["v"], 0.5)
        self.assertEqual(c["n_path"], 5)
        self.assertAlmostEqual(c["path_share"], 5 / 6)
        # только неполные пути: захвата нет, без исключения
        c = EX.cell([trade(0, 1.0, 0.0), trade(1, 2.0, -1.0)])
        self.assertIsNone(c["capture"]["v"])
        self.assertEqual(c["capture"]["n"], 0)

    def test_partial_paths_status_and_series(self):
        ts = rand_trades(1, 100, path_share=0.2)
        n_path = sum(1 for t in ts if t["mfe_r"] is not None)
        b = EX.build({"cfg": CFG, "trades": ts})
        self.assertEqual(b["status"], "few_data" if n_path < 30 else "ok")
        self.assertEqual(b["series"]["50"], [])                                 # ряд только от 30-й сделки с путём
        self.assertTrue(b["all"]["small"])
        ts = rand_trades(2, 200, path_share=0.5)
        path = [t for t in ts if t["mfe_r"] is not None]
        b = EX.build({"cfg": CFG, "trades": ts})
        ser = b["series"]["50"]
        self.assertEqual(ser[0]["i"], 30)
        self.assertEqual(ser[-1]["i"], len(path))
        self.assertLessEqual(len(ser), CFG["series_points"])
        for p in ser:
            seg = path[max(0, p["i"] - 50):p["i"]]
            self.assertEqual(p["n"], len(seg))
            self.assertEqual(p["ts"], seg[-1]["t_close"])
            self.assertAlmostEqual(p["mfe"]["v"], sum(t["mfe_r"] for t in seg) / len(seg), places=9)

    def test_few_data_threshold(self):
        b = EX.build({"cfg": CFG, "trades": rand_trades(3, 29)})
        self.assertEqual(b["status"], "few_data")
        self.assertTrue(b["windows"]["50"]["small"])
        self.assertIsNotNone(b["all"]["mfe"]["v"])
        b = EX.build({"cfg": CFG, "trades": rand_trades(3, 30)})
        self.assertEqual(b["status"], "ok")

    def test_capture_gt_1_warning(self):
        ts = [trade(i, 0.5, 1.0) for i in range(35)]
        self.assertEqual(EX.build({"cfg": CFG, "trades": ts})["warnings"], [])
        ts[5] = trade(5, 1.2, 1.0)                                              # r больше mfe из-за округления
        b = EX.build({"cfg": CFG, "trades": ts})
        self.assertEqual(b["warnings"], ["capture_gt_1"])
        self.assertTrue(any(v > 1 for v in EX.ratios(ts)))                      # число остаётся как есть

    def test_nonfinite_mfe_is_no_path(self):
        ts = [trade(i, 0.5, float("nan")) for i in range(5)] + [trade(9, 0.5, float("inf"))]
        c = EX.cell(ts)
        self.assertEqual(c["n_path"], 0)

    def test_deterministic_input_untouched(self):
        ts = rand_trades(4, 120, 0.7)
        snap = [dict(t) for t in ts]
        a, c = EX.build({"cfg": CFG, "trades": ts}), EX.build({"cfg": CFG, "trades": ts})
        self.assertEqual(a, c)
        self.assertEqual(ts, snap)


if __name__ == "__main__":
    unittest.main()
