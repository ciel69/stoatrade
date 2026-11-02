"""tb.labels.query (R1.6, задача 12): policy_stats / outcome_stats / coverage / throughput на синтетической базе; только чтение."""
import json
import math
import os
import sqlite3
import unittest

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.core import expstats
from tb.labels import query, store

PR_COLS = "key, policy_id, policy_ver, geometry_ver, ctx_ver, t0, sym, type, side, plan, code, filled, exit_reason, r_gross, r_net, fees_r, mfe_r, mae_r, hold_min, flags"


def pr(key, pid, ver, t0, typ, filled, exit_reason, r_net, mfe=None, mae=None, code=None, sym="AAA", side="long"):
    return (key, pid, ver, "g1", "c0ffee00", t0, sym, typ, side, None, code, filled, exit_reason, r_net, r_net, 0.1 if filled else None, mfe, mae, 10.0 if filled else None, None)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        self.conn = store.open_rw(self.root)
        self.addCleanup(self.conn.close)

    def add_runs(self, rows):
        self.conn.executemany("INSERT INTO policy_runs(%s) VALUES(%s)" % (PR_COLS, ",".join("?" * 20)), rows)

    def add_sig(self, key, t0, typ="range_5m", sym="AAA", status="done", feat=None, side="long", labeled=None):
        self.conn.execute("INSERT INTO signals(key, t0, sym, type, side, ev, feat, status, mature_ts, next_try_ts, captured_ts, labeled_ts, schema_ver) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)",
                          (key, t0, sym, typ, side, "{}", json.dumps(feat) if feat is not None else None, status, t0 + 100, t0 + 100, t0, labeled))


class TestPolicyStats(Base):
    def test_hand_computed_group(self):
        rs = [1.0, -1.0, 2.0, -0.5]
        rows = [pr("k%d" % i, "P2", 1, H.T0 + i, "range_5m", 1, "tp" if r > 0 else "sl", r, mfe=1.0 + i, mae=0.5) for i, r in enumerate(rs)]
        rows += [pr("u1", "P2", 1, H.T0 + 10, "range_5m", 0, "ttl", None), pr("u2", "P2", 1, H.T0 + 11, "range_5m", 0, None, None, code="GEO_NO_CAND")]
        self.add_runs(rows)
        (g,) = query.policy_stats(self.root, min_n=3)
        self.assertEqual((g["policy_id"], g["policy_ver"], g["n"], g["filled"]), ("P2", 1, 6, 4))
        self.assertAlmostEqual(g["fill_rate"], 4 / 6)
        self.assertAlmostEqual(g["win"], 0.5)
        self.assertAlmostEqual(g["avg_r_net"], 0.375)
        self.assertAlmostEqual(g["se_r"], math.sqrt(5.6875 / 3) / 2)          # sd по выборке (n-1), se = sd / sqrt(n)
        self.assertAlmostEqual(g["pf"], 2.0)                                  # (1 + 2) / (1 + 0.5)
        self.assertAlmostEqual(g["mfe_r"], 2.5)                               # (1 + 2 + 3 + 4) / 4
        self.assertAlmostEqual(g["mae_r"], 0.5)
        self.assertAlmostEqual(g["exit_reasons"]["tp"], 0.5)                  # доли от заполненных
        self.assertAlmostEqual(g["exit_reasons"]["sl"], 0.5)
        self.assertAlmostEqual(g["exit_reasons"]["ttl"], 1 / 6)               # ttl и отказы без плана - от всех прогонов
        self.assertAlmostEqual(g["exit_reasons"]["none"], 1 / 6)
        t = g["avg_r_net"] / g["se_r"]
        self.assertAlmostEqual(g["p"], expstats.t_two_sided_p(t, 3))
        self.assertTrue(0 < g["p"] < 1)

    def test_bonferroni_by_enabled_policies_and_min_n(self):
        self.add_runs([pr("k%d" % i, "P2", 1, H.T0 + i, "range_5m", 1, "tp", 0.5 + 0.01 * (i % 5)) for i in range(40)])
        (g,) = query.policy_stats(self.root)                                  # реестр: 4 включённые политики
        self.assertEqual(g["k"], 4)
        self.assertAlmostEqual(g["alpha_adj"], expstats.bonferroni(4))
        self.assertAlmostEqual(g["alpha_adj"], 0.05 / 4)
        self.assertIsNotNone(g["p"])
        self.assertTrue(g["below_alpha"])                                     # средний R заметно > 0
        self.assertFalse(g["small"])
        (g,) = query.policy_stats(self.root, min_n=100)
        self.assertIsNone(g["p"])
        self.assertTrue(g["small"])

    def test_k_follows_registry(self):
        p = os.path.join(self.root, "config", "shadow_policies.json")
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        d["policies"][0]["enabled"] = False
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f)
        self.add_runs([pr("k1", "P2", 1, H.T0, "range_5m", 1, "tp", 1.0)])
        self.assertEqual(query.policy_stats(self.root)[0]["k"], 3)

    def test_versions_are_not_mixed_and_filters(self):
        self.add_runs([pr("a1", "P2", 1, H.T0, "range_5m", 1, "tp", 1.0), pr("a2", "P2", 2, H.T0 + 5, "range_5m", 1, "sl", -1.0),
                       pr("a3", "P2", 2, H.T0 + 90000, "oi_spike", 1, "sl", -2.0, sym="BBB")])
        rows = query.policy_stats(self.root)
        self.assertEqual([(r["policy_id"], r["policy_ver"], r["n"]) for r in rows], [("P2", 1, 1), ("P2", 2, 2)])
        rows = query.policy_stats(self.root, since=H.T0 + 60)
        self.assertEqual([(r["policy_ver"], r["n"]) for r in rows], [(2, 1)])
        rows = query.policy_stats(self.root, since="2026-11-02")              # дата UTC: все три прогона (T0 = 2026-11-02 12:00 UTC и позже)
        self.assertEqual(sum(r["n"] for r in rows), 3)
        self.assertEqual(query.policy_stats(self.root, since="2026-11-04"), [])
        rows = query.policy_stats(self.root, kinds=["oi_spike"])
        self.assertEqual([(r["policy_ver"], r["n"]) for r in rows], [(2, 1)])
        rows = query.policy_stats(self.root, by=("policy_id", "policy_ver", "sym"))
        self.assertEqual(sorted((r["policy_ver"], r["sym"]) for r in rows), [(1, "AAA"), (2, "AAA"), (2, "BBB")])

    def test_pf_inf_and_no_filled(self):
        self.add_runs([pr("a", "P5", 1, H.T0, "range_5m", 1, "tp", 1.0), pr("b", "P6", 1, H.T0, "range_5m", 0, "ttl", None)])
        rows = dict((r["policy_id"], r) for r in query.policy_stats(self.root))
        self.assertTrue(rows["P5"]["pf_inf"])
        self.assertIsNone(rows["P5"]["pf"])
        self.assertIsNone(rows["P5"]["se_r"])                                 # одно наблюдение
        self.assertEqual(rows["P6"]["filled"], 0)
        self.assertIsNone(rows["P6"]["avg_r_net"])
        self.assertIsNone(rows["P6"]["win"])

    def test_bad_by_and_no_db(self):
        with self.assertRaises(ValueError):
            query.policy_stats(self.root, by=("policy_id; DROP TABLE signals",))
        empty = H.mk_root(self)
        self.assertEqual(query.policy_stats(empty), [])
        self.assertEqual(query.outcome_stats(empty), [])
        self.assertEqual(query.coverage(empty), {})
        self.assertEqual(query.throughput(empty), {})
        self.assertFalse(os.path.exists(store.db_path(empty)))                # читатель базу не создаёт

    def test_read_only_connection(self):
        ro = store.open_ro(self.root)
        self.addCleanup(ro.close)
        with self.assertRaises(sqlite3.OperationalError):
            ro.execute("DELETE FROM signals")


class TestOutcomeStats(Base):
    def test_by_type_horizon_and_flags(self):
        self.add_sig("s1", H.T0, "range_5m")
        self.add_sig("s2", H.T0 + 1, "range_5m")
        self.add_sig("s3", H.T0 + 2, "oi_spike", side=None)
        ins = "INSERT INTO outcomes(key, h, side, ret_pct, mfe_pct, mae_pct, mfe_atr, mae_atr, t_mfe_min, t_mae_min, cost_rt_pct, net_dir_pct, flags) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"
        self.conn.executemany(ins, [
            ("s1", 60, "long", 1.0, 1.5, -0.5, 5.0, -2.0, 30, 10, 0.3, 0.7, None),
            ("s2", 60, "long", -0.4, 0.2, -1.0, 1.0, -4.0, 5, 40, 0.3, -0.7, "gap"),
            ("s1", 15, "long", 9.0, 9.0, 0.0, 1, 0, 1, 0, 0.3, 8.7, None),
            ("s3", 60, "long", 0.5, 0.6, -0.1, 2, -1, 1, 1, 0.3, 0.2, "halt"),
            ("s3", 60, "short", -0.5, 0.1, -0.6, 1, -2, 1, 1, 0.3, -0.8, "halt")])
        rows = dict((r["type"], r) for r in query.outcome_stats(self.root, horizon=60))
        r = rows["range_5m"]
        self.assertEqual((r["n"], r["h"]), (2, 60))
        self.assertAlmostEqual(r["avg_ret_pct"], 0.3)
        self.assertAlmostEqual(r["avg_mfe_pct"], 0.85)
        self.assertAlmostEqual(r["avg_mae_pct"], -0.75)
        self.assertAlmostEqual(r["avg_net_dir_pct"], 0.0)
        self.assertAlmostEqual(r["net_pos_share"], 0.5)
        self.assertAlmostEqual(r["gap_share"], 0.5)
        self.assertEqual(rows["oi_spike"]["n"], 2)                            # сигнал без направления: строки обеих сторон
        self.assertAlmostEqual(rows["oi_spike"]["halt_share"], 1.0)
        self.assertEqual(query.outcome_stats(self.root, horizon=15)[0]["n"], 1)
        by_side = query.outcome_stats(self.root, by=("type", "side"), horizon=60)
        self.assertEqual(sorted((r["type"], r["side"], r["n"]) for r in by_side), [("oi_spike", "long", 1), ("oi_spike", "short", 1), ("range_5m", "long", 2)])
        self.assertEqual(sum(r["n"] for r in query.outcome_stats(self.root, since=H.T0 + 1, horizon=60)), 3)
        with self.assertRaises(ValueError):
            query.outcome_stats(self.root, by=("nope",))


class TestCoverageThroughput(Base):
    FULL = {"atr5_pct": 0.2, "range_15m_pct": 1.0, "ret_1h_pct": 0.1, "btc_ret_1h_pct": 0.0, "dist_sup_pct": 0.5, "dist_res_pct": 0.5}

    def test_coverage_shares(self):
        self.add_sig("a", H.T0, feat=dict(self.FULL))
        self.add_sig("b", H.T0 + 1, feat=dict(self.FULL, dist_sup_pct=None))
        self.add_sig("c", H.T0 + 2, feat=dict(self.FULL, dist_sup_pct=None, atr5_pct=None))
        self.add_sig("d", H.T0 + 3, feat=dict(self.FULL))
        self.add_sig("p", H.T0 + 4, status="pending")
        self.add_sig("f", H.T0 + 5, status="failed")
        c = query.coverage(self.root)
        self.assertEqual(c["signals"], {"done": 4, "pending": 1, "failed": 1})
        self.assertEqual(c["done"], 4)
        self.assertAlmostEqual(c["fields"]["dist_sup_pct"], 0.5)
        self.assertAlmostEqual(c["fields"]["atr5_pct"], 0.25)
        self.assertEqual(c["fields"]["ret_1h_pct"], 0.0)
        self.assertAlmostEqual(c["any_missing_share"], 0.5)
        self.assertEqual(c["limit"], 0.10)
        self.assertEqual(query.coverage(self.root, since=H.T0 + 2)["done"], 2)

    def test_throughput_counters(self):
        now = H.T0 + 5 * 3600
        self.add_sig("done1", H.T0, status="done", labeled=now - 100, feat=dict(self.FULL))
        self.add_sig("old", H.T0 - 3 * 86400, status="done", labeled=now - 2 * 86400)
        self.add_sig("fail", H.T0 + 1, status="failed", labeled=now - 50)
        self.add_sig("nod", H.T0 + 2, status="no_data", labeled=now - 40)
        self.add_sig("due", H.T0 + 3, status="pending")
        self.conn.execute("INSERT INTO signals(key, t0, sym, type, ev, status, mature_ts, next_try_ts, captured_ts, schema_ver) VALUES('fut', ?, 'X', 'range_5m', '{}', 'pending', ?, ?, ?, 1)",
                          (H.T0 + 4, now + 1000, now + 1000, H.T0 + 4))
        t = query.throughput(self.root, now)
        self.assertEqual((t["labeled_24h"], t["failed_24h"], t["no_data_24h"], t["pending"], t["due"]), (1, 1, 1, 2, 1))
        self.assertEqual(t["captured_24h"], 5)                                # captured_ts = t0: old (3 суток назад) не входит
        self.assertAlmostEqual(t["missing_share_24h"], 0.0)
        self.assertEqual(t["oldest_pending_age_s"], now - (H.T0 + 3))


if __name__ == "__main__":
    unittest.main()
