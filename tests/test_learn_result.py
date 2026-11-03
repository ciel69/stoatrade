"""tb.learn.result (R0.2, задача 5): сверка с expstats.group_stats (свойство 6), ряды (свойство 7), 30-49 сделок, пустой журнал."""
import random
import unittest

import _bootstrap  # noqa: F401
from tb.core import expstats as X
from tb.learn import conf, result as RS, stats as S


def trade(i, r, pnl=None, fees=0.01, mfe=None):
    pnl = r if pnl is None else pnl
    return {"id": "T%d" % i, "plan_id": "P%d" % i, "parent": "P%d" % i, "plan_rev": None, "rules_key": None, "exp": None, "var": None,
            "setup": "s", "hyp": None, "agent": None, "model": None, "mem": None, "blackout": "обычные", "mode": "paper", "sym": "SOL",
            "stype": "intraday", "side": "long", "r": r, "pnl": pnl, "fees": fees, "risk": 1.0, "reason": "sl", "mae_r": None,
            "mfe_r": mfe, "ts_open": None, "ts_close": None, "t_close": 1_790_000_000 + i * 600, "shadow": False, "rules": []}


def rand_trades(seed, n, mu=0.1):
    rng = random.Random(seed)
    return [trade(i, rng.gauss(mu, 1.0), fees=abs(rng.gauss(0.05, 0.02))) for i in range(n)]


CFG, _ = conf.load(None)


class TestResult(unittest.TestCase):
    def test_empty(self):
        b = RS.build({"cfg": CFG, "trades": []})
        self.assertEqual((b["status"], b["reason"]), ("no_data", "no_trades"))
        self.assertEqual(b["all"]["n"], 0)
        self.assertEqual(b["series"], {"50": [], "200": []})
        self.assertEqual(b["signal"], {"50": False, "200": False})
        self.assertIsNone(b["all"]["r"]["v"])
        self.assertTrue(b["all"]["small"])

    def test_missing_ctx_parts(self):
        self.assertEqual(RS.build({})["status"], "no_data")
        self.assertEqual(RS.build({"cfg": None, "trades": None})["status"], "no_data")

    def test_few_data_30_49(self):
        for n in (1, 29):
            b = RS.build({"cfg": CFG, "trades": rand_trades(n, n)})
            self.assertEqual(b["status"], "few_data")
            self.assertEqual(b["series"]["50"], [])
            self.assertTrue(b["windows"]["50"]["small"])
            self.assertEqual(b["windows"]["50"]["n"], n)
        for n in (30, 38, 49):
            ts = rand_trades(n, n)
            b = RS.build({"cfg": CFG, "trades": ts})
            self.assertEqual(b["status"], "ok")
            self.assertFalse(b["windows"]["50"]["small"])
            self.assertEqual(b["windows"]["50"]["n"], n)                  # окно 50 = все сделки
            self.assertEqual(b["windows"]["50"]["r"], b["all"]["r"])
            self.assertEqual(len(b["series"]["50"]), n - 29)              # по точке на сделку с 30-й
            self.assertEqual(b["series"]["50"][0]["i"], 30)

    def test_matches_group_stats(self):
        for seed in range(20):
            n = random.Random(seed).randint(30, 260)
            ts = rand_trades(seed, n)
            b = RS.build({"cfg": CFG, "trades": ts})
            for key, seg in (("50", ts[-50:]), ("200", ts[-200:])):
                row = X.group_stats([dict(t, setup="_w", mode="_w") for t in seg], by="setup")[0]
                w = b["windows"][key]
                self.assertEqual(w["n"], row["n"])
                self.assertEqual(w["r"]["v"], row["avg_r"])
                self.assertEqual(w["win"]["v"], row["win_rate"])
                self.assertEqual(w["pf"]["v"], row["pf"])
                self.assertEqual(w["pf_inf"], row["pf_inf"])
                self.assertEqual(w["fees_share"], row["fees_share"])
                self.assertEqual(w["small"], row["small"])
            row = X.group_stats([dict(t, setup="_w", mode="_w") for t in ts], by="setup")[0]
            self.assertEqual(b["all"]["r"]["v"], row["avg_r"])
            self.assertEqual(b["all"]["n"], n)

    def test_series_properties(self):
        for seed in range(20):
            n = random.Random(seed).randint(30, 400)
            ts = rand_trades(seed, n)
            b = RS.build({"cfg": CFG, "trades": ts})
            for w in (50, 200):
                ser = b["series"][str(w)]
                self.assertTrue(0 < len(ser) <= CFG["series_points"])
                self.assertEqual(ser[-1]["i"], n)
                self.assertGreaterEqual(ser[0]["i"], 30)
                self.assertEqual([p["i"] for p in ser], sorted(set(p["i"] for p in ser)))
                for p in ser:
                    seg = [t["r"] for t in ts[max(0, p["i"] - w):p["i"]]]
                    self.assertEqual(p["n"], len(seg))
                    self.assertAlmostEqual(p["r"]["v"], sum(seg) / len(seg), places=9)
                    self.assertEqual(p["ts"], ts[p["i"] - 1]["t_close"])
                # последняя точка ряда равна оценке окна
                self.assertEqual(ser[-1]["r"]["v"], b["windows"][str(w)]["r"]["v"])

    def test_signal_interval_above_zero(self):
        up = [trade(i, 0.5 + (0.01 if i % 2 else -0.01)) for i in range(80)]
        down = [trade(i, -0.5 + (0.01 if i % 2 else -0.01)) for i in range(80)]
        self.assertTrue(RS.build({"cfg": CFG, "trades": up})["signal"]["50"])
        self.assertFalse(RS.build({"cfg": CFG, "trades": down})["signal"]["50"])
        for seed in range(20):
            b = RS.build({"cfg": CFG, "trades": rand_trades(seed, 120)})
            for k in ("50", "200"):
                lo = b["windows"][k]["r"]["lo"]
                self.assertEqual(b["signal"][k], lo is not None and lo > 0)

    def test_degenerate_equal_r(self):
        ts = [trade(i, 0.5) for i in range(40)]
        b = RS.build({"cfg": CFG, "trades": ts})
        w = b["windows"]["50"]
        self.assertEqual(w["r"]["v"], 0.5)
        self.assertTrue(w["pf_inf"])
        self.assertIsNone(w["pf"]["v"])
        self.assertIsNone(w["pf"]["hi"])
        ts = [trade(i, -0.5) for i in range(40)]
        w = RS.build({"cfg": CFG, "trades": ts})["windows"]["50"]
        self.assertEqual(w["win"]["v"], 0.0)
        self.assertEqual(w["pf"]["v"], 0.0)

    def test_deterministic_and_input_untouched(self):
        ts = rand_trades(3, 90)
        snap = [dict(t) for t in ts]
        a = RS.build({"cfg": CFG, "trades": ts})
        c = RS.build({"cfg": CFG, "trades": ts})
        self.assertEqual(a, c)
        self.assertEqual(ts, snap)

    def test_object_ctx_and_custom_windows(self):
        class C:
            cfg = dict(CFG, windows=[30])
            trades = rand_trades(1, 45)
        b = RS.build(C())
        self.assertEqual(list(b["windows"]), ["30"])
        self.assertEqual(b["windows"]["30"]["n"], 30)

    def test_nonfinite_values_become_none(self):
        ts = rand_trades(5, 40)
        ts[3]["r"] = float("nan")
        b = RS.build({"cfg": CFG, "trades": ts})
        self.assertEqual(b["status"], "ok")
        self.assertEqual(b["all"]["r"]["n"], 39)


if __name__ == "__main__":
    unittest.main()
