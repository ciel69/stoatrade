"""tb.labels.label (R1.6, задача 12): due, полный цикл разметки на фейковом fetch, исходы, политики, no_data/retry/failed, gap, tradable=false,
сигнал без направления, снимок контекста, атомарность (свойство 11). Сеть не используется."""
import json
import os
import sqlite3
import unittest

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.labels import capture, klines, label, policies, schema, store


class Boom(object):
    """Соединение-обёртка: бросает на N-й вставке в policy_runs (сбой посреди записи)."""

    def __init__(self, conn, n=2):
        self.c, self.n, self.k = conn, n, 0

    def execute(self, sql, *a):
        if "INTO policy_runs" in sql:
            self.k += 1
            if self.k >= self.n:
                raise sqlite3.OperationalError("boom")
        return self.c.execute(sql, *a)

    def __getattr__(self, name):
        return getattr(self.c, name)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        self.conn = store.open_rw(self.root)
        self.addCleanup(self.conn.close)
        self.mk = H.Market()
        self.btc = H.FakeBtc(self.mk)
        self.pols = policies.load(self.root)
        self.geom = label.load_geom(self.root)

    def cap(self, *events, now=None):
        self.cfg = H.capture_all(self.root, self.conn, list(events) or [H.hot_event()], now)
        return H.sig_row(self.conn)

    def run_label(self, row, now=None, fetch=None):
        now = row["mature_ts"] if now is None else now
        return label.label_one(self.conn, self.root, row, self.cfg, self.pols, self.geom, fetch or self.mk.fetch, self.btc, now)

    def q(self, sql, *a):
        return self.conn.execute(sql, a).fetchall()

    def status(self, key):
        return self.q("SELECT status, code, attempts, next_try_ts FROM signals WHERE key=?", key)[0]


class TestDue(Base):
    def test_due_filters_and_order(self):
        self.cap(H.hot_event(0, H.T0 + 100, sym="BBB"), H.hot_event(1, H.T0, sym="AAA"), H.hot_event(2, H.T0 + 200, sym="CCC"))
        mat = self.q("SELECT sym, mature_ts FROM signals ORDER BY t0")
        self.assertEqual([m[0] for m in mat], ["AAA", "BBB", "CCC"])
        self.assertEqual(label.due(self.conn, mat[0][1] - 1, 10), [])                       # не созрела
        d = label.due(self.conn, mat[1][1], 10)
        self.assertEqual([r["sym"] for r in d], ["AAA", "BBB"])                              # порядок t0, созревшие
        self.assertEqual(len(label.due(self.conn, mat[2][1], 2)), 2)                         # limit
        self.conn.execute("UPDATE signals SET next_try_ts=? WHERE sym='AAA'", (mat[2][1] + 10,))
        self.assertEqual([r["sym"] for r in label.due(self.conn, mat[2][1], 10)], ["BBB", "CCC"])   # next_try_ts в будущем
        self.conn.execute("UPDATE signals SET status='done' WHERE sym='BBB'")
        self.assertEqual([r["sym"] for r in label.due(self.conn, mat[2][1], 10)], ["CCC"])


class TestFullCycle(Base):
    def test_done_outcomes_features_policies(self):
        row = self.cap()
        key = row["key"]
        now = row["mature_ts"]
        self.assertEqual(self.run_label(row), "done")
        st = self.q("SELECT status, code, labeled_ts, levels_src, feat, missing, gap_min FROM signals WHERE key=?", key)[0]
        self.assertEqual((st[0], st[1], st[2], st[3], st[6]), ("done", None, now, "key_levels", None))
        feat = json.loads(st[4])
        self.assertIsNotNone(feat["atr5_pct"])
        self.assertIsNotNone(feat["btc_ret_1h_pct"])
        for k in json.loads(st[5]):                                                          # missing = имена признаков, равных null
            self.assertIsNone(feat[k])
        # окна: 1м [t0-30м, mature-grace], 5м x300, 1ч x200, BTC из кеша
        calls = self.mk.calls
        self.assertEqual(calls[0], ("DOGEUSDT", "1", H.T0 - 1800, row["mature_ts"] - 600))
        e5 = H.T0 // 300 * 300 - 300
        self.assertEqual(calls[1], ("DOGEUSDT", "5", e5 - 299 * 300, e5))
        e1h = H.T0 // 3600 * 3600 - 3600
        self.assertEqual(calls[2], ("DOGEUSDT", "60", e1h - 199 * 3600, e1h))
        self.assertEqual(self.btc.calls, [H.T0])
        # исходы: цена t0 = open первой 1м свечи не раньше t0; сигнал направленный -> только long
        s0 = (H.T0 + 59) // 60 * 60
        px0 = self.mk.row(s0)[1]
        outs = self.q("SELECT h, side, ret_pct, mfe_pct, mae_pct, t_mfe_min, cost_rt_pct, net_dir_pct, flags FROM outcomes WHERE key=? ORDER BY h", key)
        self.assertEqual([(o[0], o[1]) for o in outs], [(15, "long"), (60, "long"), (240, "long")])
        for o in outs:
            h = o[0]
            w = [self.mk.row(s0 + i * 60) for i in range(h)]
            self.assertAlmostEqual(o[2], (w[-1][4] / px0 - 1) * 100, places=9)
            self.assertAlmostEqual(o[3], max(0.0, max((r[2] / px0 - 1) * 100 for r in w), o[2]), places=9)
            self.assertAlmostEqual(o[4], min(0.0, min((r[3] / px0 - 1) * 100 for r in w), o[2]), places=9)
            self.assertTrue(0 <= o[5] <= h)
        with open(os.path.join(self.root, "config", "risk_profile.json"), encoding="utf-8") as f:
            cb = json.load(f)["costs"]["bybit"]
        cost = round(2 * (cb["fee"] + cb["slip"]) * 100, 2) + H.SPREAD / 100
        for o in outs:
            self.assertAlmostEqual(o[6], cost, places=9)
            self.assertAlmostEqual(o[7], o[2] - cost, places=9)
            self.assertIsNone(o[8])
        # политики: P2, P4 (range_5m), P5, P6 (*); версии и контекст
        runs = self.q("SELECT policy_id, policy_ver, geometry_ver, ctx_ver, t0, side, plan, code, filled, exit_reason FROM policy_runs WHERE key=? ORDER BY policy_id", key)
        self.assertEqual([r[0] for r in runs], ["P2", "P4", "P5", "P6"])
        self.assertTrue(all(r[1] == 1 and r[2] == self.geom["geometry_ver"] and len(r[3]) == 8 and r[4] == H.T0 for r in runs))
        self.assertEqual(len(set(r[3] for r in runs)), 1)
        p5 = runs[2]
        self.assertEqual((p5[5], p5[8]), ("long", 1))                                        # P5: с направлением, по рынку, заполнен
        plan = json.loads(p5[6])
        self.assertEqual((plan["side"], plan["entry"]["kind"], plan["tps"]), ("long", "market", []))
        for r in runs:
            if r[8] == 0:
                self.assertTrue(r[7] or r[9])                                                # отказ несёт код либо ttl/no_fill
        self.assertEqual(self.q("SELECT COUNT(*) FROM policy_runs WHERE key=? AND filled=1 AND r_net IS NULL", key)[0][0], 0)

    def test_base_profile_policy_runs_through_simulator(self):
        row = self.cap(H.hot_event(range_5m_pct=0.2))               # узкий диапазон 5м: геометрия не отказывает RANGE_GT_STOP (seed 1 даёт ok-кандидата)
        self.assertEqual(self.run_label(row), "done")
        p4 = self.q("SELECT side, plan, code, filled, exit_reason, r_net, hold_min FROM policy_runs WHERE policy_id='P4'")[0]
        plan = json.loads(p4[1])
        self.assertEqual((p4[0], plan["side"], plan["entry"]["kind"], p4[2], p4[3]), ("short", "short", "candle_confirm", None, 1))   # против направления up
        self.assertGreater(plan["stop"], plan["entry"]["level"])
        self.assertTrue(all(t < plan["entry"]["level"] for t in plan["tps"]))
        self.assertIn(plan["horizon"], ("scalp", "intraday"))
        self.assertIsNotNone(p4[4])
        self.assertIsNotNone(p4[5])

    def test_snapshot_context_and_backfill_null(self):
        with open(os.path.join(self.root, "runtime", "context.json"), "w", encoding="utf-8") as f:
            json.dump({"ts_epoch": H.T0 + 5, "risk_off": True, "sources": {"fear_greed": {"value": 55}, "dvol_ETH": {"value": 61.5}}}, f)
        row = self.cap(now=H.T0 + 10)                                                        # захват сразу: снимок берётся
        self.assertEqual(self.run_label(row), "done")
        feat = json.loads(self.q("SELECT feat FROM signals WHERE key=?", row["key"])[0][0])
        self.assertEqual((feat["fng"], feat["dvol"], feat["risk_off"]), (55.0, 61.5, True))
        # догрузка задним числом: захват позже t0 + 120 с -> null и в missing, при любом содержимом context.json сейчас
        self.conn.execute("DELETE FROM signals")
        self.conn.execute("DELETE FROM cursor")
        row = self.cap(now=H.T0 + 500)
        self.assertEqual(self.run_label(row, now=H.T0 + 20000), "done")
        feat = json.loads(self.q("SELECT feat FROM signals WHERE key=?", row["key"])[0][0])
        miss = json.loads(self.q("SELECT missing FROM signals WHERE key=?", row["key"])[0][0])
        self.assertEqual((feat["fng"], feat["dvol"], feat["risk_off"]), (None, None, None))
        self.assertTrue({"fng", "dvol", "risk_off"} <= set(miss))

    def test_rerun_is_idempotent(self):
        row = self.cap()
        self.assertEqual(self.run_label(row), "done")
        n1 = (self.q("SELECT COUNT(*) FROM outcomes")[0][0], self.q("SELECT COUNT(*) FROM policy_runs")[0][0])
        self.assertEqual(self.run_label(row), "done")
        self.assertEqual((self.q("SELECT COUNT(*) FROM outcomes")[0][0], self.q("SELECT COUNT(*) FROM policy_runs")[0][0]), n1)


class TestSignalKinds(Base):
    def test_no_direction_both_sides_and_no_dir_policies(self):
        row = self.cap(H.hot_event(direction=None))
        self.assertIsNone(row["side"])
        self.assertEqual(self.run_label(row), "done")
        outs = self.q("SELECT h, side FROM outcomes WHERE key=? ORDER BY h, side", row["key"])
        self.assertEqual(outs, [(h, s) for h in (15, 60, 240) for s in ("long", "short")])
        runs = dict((r[0], r[1:]) for r in self.q("SELECT policy_id, code, filled FROM policy_runs WHERE key=?", row["key"]))
        for pid in ("P2", "P4", "P5"):
            self.assertEqual(runs[pid], (schema.NO_DIR, 0), pid)
        self.assertNotEqual(runs["P6"][0], schema.NO_DIR)                                    # случайная сторона направления не требует

    def test_tradable_false_has_outcomes_but_no_policy_runs(self):
        row = self.cap(H.hot_event(tradable=False))
        self.assertEqual(row["tradable"], 0)
        self.assertEqual(self.run_label(row), "done")
        self.assertEqual(self.q("SELECT COUNT(*) FROM outcomes WHERE key=?", row["key"])[0][0], 3)
        self.assertEqual(self.q("SELECT COUNT(*) FROM policy_runs")[0][0], 0)

    def test_kind_filter_policies_by_type(self):
        row = self.cap(H.hot_event(typ="oi_spike"))                                          # P4 на oi_spike не подходит
        self.assertEqual(self.run_label(row), "done")
        self.assertEqual([r[0] for r in self.q("SELECT policy_id FROM policy_runs ORDER BY policy_id")], ["P2", "P5", "P6"])

    def test_disabled_policy_not_run(self):
        self.pols = [dict(p, enabled=False) if p["id"] == "P6" else p for p in self.pols]
        row = self.cap()
        self.assertEqual(self.run_label(row), "done")
        self.assertNotIn("P6", [r[0] for r in self.q("SELECT policy_id FROM policy_runs")])

    def test_no_spread_flag(self):
        row = self.cap(H.hot_event(spread_bps=None))
        self.assertEqual(self.run_label(row), "done")
        o = self.q("SELECT cost_rt_pct, flags FROM outcomes WHERE key=? AND h=15", row["key"])[0]
        with open(os.path.join(self.root, "config", "risk_profile.json"), encoding="utf-8") as f:
            cb = json.load(f)["costs"]["bybit"]
        self.assertAlmostEqual(o[0], round(2 * (cb["fee"] + cb["slip"]) * 100, 2), places=9)   # без спреда
        self.assertIn("no_spread", o[1])


class TestFailures(Base):
    def test_no_data(self):
        row = self.cap()
        self.mk.empty = True
        self.assertEqual(self.run_label(row), "no_data")
        self.assertEqual(self.status(row["key"])[:2], ("no_data", "NO_DATA"))
        self.assertEqual(self.q("SELECT COUNT(*) FROM outcomes")[0][0] + self.q("SELECT COUNT(*) FROM policy_runs")[0][0], 0)

    def test_no_candles_after_t0_is_no_data(self):
        row = self.cap()
        self.mk.drop(H.T0, H.T0 + 7 * 3600)
        self.assertEqual(self.run_label(row), "no_data")

    def test_fetch_error_retry_then_failed(self):
        row = self.cap()
        self.mk.fail = klines.FetchError("HTTP 500")
        now = row["mature_ts"]
        for i in range(1, 5):
            self.assertEqual(self.run_label(row, now=now), "pending")
            st = self.status(row["key"])
            self.assertEqual((st[0], st[1], st[2], st[3]), ("pending", "FETCH", i, now + 30 * 60))
            self.assertEqual(label.due(self.conn, now, 5), [])                                 # отложена
            now += 30 * 60
            row = label.due(self.conn, now, 5)[0]
        self.assertEqual(self.run_label(row, now=now), "failed")                                # 5-я попытка
        st = self.status(row["key"])
        self.assertEqual((st[0], st[1], st[2]), ("failed", "FETCH", 5))
        self.assertEqual(label.due(self.conn, now + 10 ** 6, 5), [])

    def test_fetch_error_recovers(self):
        row = self.cap()
        self.mk.fail = klines.FetchError("net")
        self.assertEqual(self.run_label(row), "pending")
        self.mk.fail = None
        row = label.due(self.conn, row["mature_ts"] + 1800, 5)[0]
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(self.run_label(row, now=row["mature_ts"] + 1800), "done")

    def test_btc_fetch_error_defers(self):
        row = self.cap()

        class Bad(object):
            def get(self, t0):
                raise klines.FetchError("btc")
        self.btc = Bad()
        self.assertEqual(self.run_label(row), "pending")
        self.assertEqual(self.status(row["key"])[2], 1)

    def test_rate_limited_does_not_spend_attempt(self):
        row = self.cap()
        self.mk.fail = klines.RateLimited("HTTP 429")
        now = row["mature_ts"]
        self.assertEqual(self.run_label(row, now=now), label.RATE_LIMITED)
        st = self.status(row["key"])
        self.assertEqual((st[0], st[2], st[3]), ("pending", 0, now + 60))
        for _ in range(8):                                                                       # сколько угодно раз - не failed
            row = label.due(self.conn, now + 60, 5)[0]
            now += 60
            self.assertEqual(self.run_label(row, now=now), label.RATE_LIMITED)
        self.assertEqual(self.status(row["key"])[:3], ("pending", None, 0))

    def test_config_error_defers_with_attempt(self):
        row = self.cap()
        with open(os.path.join(self.root, "config", "risk_profile.json"), "w") as f:
            f.write("{")
        self.assertEqual(self.run_label(row), "pending")
        self.assertEqual(self.status(row["key"])[1:3], ("CONFIG", 1))


class TestGap(Base):
    def test_gap_min_and_flags(self):
        row = self.cap()
        self.mk.drop(H.T0 + 20 * 60, H.T0 + 30 * 60)                                             # 10 минут без свечей внутри всех горизонтов (кроме 15)
        self.assertEqual(self.run_label(row), "done")
        st = self.q("SELECT gap_min FROM signals WHERE key=?", row["key"])[0]
        self.assertEqual(st[0], 10.0)
        flags = dict(self.q("SELECT h, flags FROM outcomes WHERE key=?", row["key"]))
        self.assertNotIn("gap", flags[15] or "")                                                 # пропуск после 15-й минуты
        self.assertIn("gap", flags[60])
        self.assertIn("gap", flags[240])
        pf = [r[0] for r in self.q("SELECT flags FROM policy_runs WHERE key=? AND plan IS NOT NULL", row["key"])]
        self.assertTrue(pf)
        self.assertTrue(all("gap" in (f or "") for f in pf))

    def test_no_gap(self):
        row = self.cap()
        self.assertEqual(self.run_label(row), "done")
        self.assertIsNone(self.q("SELECT gap_min FROM signals WHERE key=?", row["key"])[0][0])


class TestAtomic(Base):
    def test_failure_mid_write_leaves_nothing(self):
        row = self.cap()
        boom = Boom(self.conn, n=2)
        with self.assertRaises(sqlite3.OperationalError):
            label.label_one(boom, self.root, row, self.cfg, self.pols, self.geom, self.mk.fetch, self.btc, row["mature_ts"])
        self.assertGreaterEqual(boom.k, 2)                                                       # первая вставка прошла, вторая упала
        self.assertEqual(self.q("SELECT COUNT(*) FROM outcomes")[0][0], 0)
        self.assertEqual(self.q("SELECT COUNT(*) FROM policy_runs")[0][0], 0)
        st = self.q("SELECT status, feat, labeled_ts FROM signals WHERE key=?", row["key"])[0]
        self.assertEqual(st, ("pending", None, None))
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.run_label(row), "done")                                            # после сбоя метка размечается заново
        self.assertEqual(self.q("SELECT COUNT(*) FROM policy_runs")[0][0], 4)


if __name__ == "__main__":
    unittest.main()
