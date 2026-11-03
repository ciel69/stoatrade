"""tb.learnloop.clusters (R2, задача 7): свойство 10 design.md — закрытие кластеров по разметке и grace_min и по пределу 48 часов, однократная
запись в cluster_runs, признаки RULE_FEATURES (без служебных ev_kind/side_rel), ключ (час, сторона) по всему рынку, выборка окна с границей
cut_ts, отказ до cut_ts, хеш при повторе (в том числе кластер, закрытый через 47 часов, и записанный шагом observe за 15 минут до cut_ts),
переразметка policy_runs после закрытия, подмена строки в обход триггеров (SAMPLE_CHANGED), RESTORED, порядок вставки, ctx_ver, env_norm.
Временные корни, сети нет."""
import json
import random
import shutil
import sqlite3
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import clusters as CL
from tb.learnloop import conf as LC
from tb.logic.learn_cond import RULE_FEATURES
from tb.memory import cfg as memcfg

CFG = LC.load_strict()
MCFG = memcfg.load()
POLICY = {"id": "P2", "ver": 1, "name": "confirm_follow", "kinds": ["range_5m", "turnover_surge", "hot_enter", "oi_spike", "liq_cascade"],
          "side": "with_dir", "entry": "candle_confirm_limit", "enabled": True}
T0 = 1790812800            # 2026-10-01T00:00:00Z
H = 3600
D = 86400
CMAX = CFG["clusters"]["cluster_close_max_h"] * H
GRACE = CFG["clusters"]["grace_min"] * 60
OBS = CFG["intervals"]["observe_s"]

_SIG = ("INSERT INTO signals(key, t0, sym, type, side, tradable, env, ev, status, attempts, mature_ts, next_try_ts, captured_ts, labeled_ts, schema_ver)"
        " VALUES(?,?,?,?,?,?,?,?,?,0,?,?,?,?,1)")
_RUN = ("INSERT OR REPLACE INTO policy_runs(key, policy_id, policy_ver, geometry_ver, ctx_ver, t0, sym, type, side, plan, code, filled, r_net, flags)"
        " VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL,?,?,?)")


def ev_of(**kw):
    ev = {"range_5m_pct": 1.0, "ret_5m_pct": 0.1, "turn_surge_5m": 1.5, "chg_24h_pct": 2.0, "spread_bps": 3.0, "funding_pct": 0.001, "turn_24h_musd": 50.0}
    ev.update(kw)
    return dict((k, v) for k, v in ev.items() if v is not None)


def add(c, t0, sym="QAUSDT", typ="range_5m", side="long", r=0.0, status="done", labeled=None, env="mainnet", ev=None, filled=1, ctx="c1",
        flags=None, run=True, key=None):
    key = key or "S-%d-%s-%s-%s" % (t0, sym, typ, side)
    labeled = (t0 + int(4.5 * H)) if labeled is None and status != "pending" else labeled
    c.execute(_SIG, (key, t0, sym, typ, side, 1, env, json.dumps(ev if ev is not None else ev_of()), status, t0 + 4 * H, t0 + 4 * H, t0 + 30, labeled))
    if run:
        c.execute(_RUN, (key, "P2", 1, "g1", ctx, t0, sym, typ, side, 1 if filled else 0, r if filled else None, flags))
    return key


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbll_")
        self.addCleanup(shutil.rmtree, self.root, True)
        store2._STATUS_CACHE.clear()
        self.c = store.open_rw(self.root)
        self.addCleanup(self.c.close)
        store2.migrate_v2a(self.c, self.root, explicit=True, backup=False, now=T0)

    def close(self, now, **kw):
        return CL.close_clusters(self.c, now, CFG, policy=POLICY, mcfg=MCFG, root=self.root, **kw)

    def runs(self):
        cur = self.c.execute("SELECT %s FROM cluster_runs ORDER BY t0, key" % ", ".join(CL.COLS))
        return [dict(zip(CL.COLS, r)) for r in cur.fetchall()]

    def win(self, a, b, now, ctx="c1", env="mainnet"):
        return CL.window(self.c, "P2", 1, ctx, env, a, b, CFG, now)


class TestTimeAndKeys(unittest.TestCase):
    def test_env_norm(self):
        for v in (None, "", "mainnet", "demo", "TESTNET"):
            self.assertEqual(CL.env_norm(v), "mainnet")
        self.assertEqual(CL.env_norm("testnet"), "testnet")

    def test_cut_ts_and_last_b(self):
        b = T0 + 10 * H
        self.assertEqual(CL.cut_ts(b, CFG), b + 48 * H + 900)
        self.assertEqual(CL.last_b(CL.cut_ts(b, CFG), CFG), b)
        self.assertEqual(CL.last_b(CL.cut_ts(b, CFG) - 1, CFG), b - H)
        self.assertEqual(CL.hour_floor(T0 + 3599), T0)
        self.assertEqual(CL.hour_ceil(T0 + 1), T0 + H)
        self.assertEqual(CL.hour_ceil(T0), T0)

    def test_entry_side(self):
        self.assertEqual(CL.entry_side(POLICY, "long"), "long")
        self.assertEqual(CL.entry_side(dict(POLICY, side="against_dir"), "long"), "short")
        self.assertIsNone(CL.entry_side(POLICY, None))
        self.assertIsNone(CL.entry_side(dict(POLICY, side="random"), "long"))


class TestClose(Base):
    def test_close_after_labels_and_grace(self):
        add(self.c, T0 + 60, r=0.5)
        add(self.c, T0 + 1200, sym="QBUSDT", r=-0.5)
        lab = T0 + 1200 + int(4.5 * H)
        self.assertEqual(self.close(lab + GRACE - 1), 0)
        self.assertEqual(self.close(lab + GRACE), 1)
        rows = self.runs()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["cluster"] for r in rows}, {"%d:long" % (T0 // H)})
        self.assertEqual({r["closed_ts"] for r in rows}, {lab + GRACE})
        f = json.loads(rows[0]["feats"])
        self.assertEqual(set(f), set(RULE_FEATURES))
        self.assertEqual(f["side"], "long")
        self.assertEqual(f["coin_class"], "alt_small")
        self.assertEqual(rows[0]["type"], "range_5m")

    def test_not_before_hour_end(self):
        add(self.c, T0 + 60, labeled=T0 + 120)
        self.assertEqual(self.close(T0 + H - 1), 0)
        self.assertEqual(self.close(T0 + H), 1)

    def test_pending_blocks_then_forced_close_48h(self):
        add(self.c, T0 + 60, r=1.0)
        add(self.c, T0 + 900, sym="QBUSDT", status="pending", run=False)
        end = T0 + H
        self.assertEqual(self.close(end + 5 * H), 0)
        self.assertEqual(self.close(end + CMAX - 1), 0)
        self.assertEqual(self.close(end + CMAX), 1)
        rows = self.runs()
        self.assertEqual(len(rows), 1, "неразмеченный прогон в кластер не входит")

    def test_written_once_and_relabel_ignored(self):
        k = add(self.c, T0 + 60, r=1.0)
        add(self.c, T0 + 600, sym="QBUSDT", r=-1.0)
        now = T0 + 6 * H
        self.assertEqual(self.close(now), 1)
        before = self.runs()
        h1 = self.win(T0, T0 + H, CL.cut_ts(T0 + H, CFG))["hash"]
        self.assertEqual(self.close(now + H), 0)
        self.c.execute(_RUN, (k, "P2", 1, "g1", "c1", T0 + 60, "QAUSDT", "range_5m", "long", 1, -3.0, None))   # переразметка (INSERT OR REPLACE)
        self.assertEqual(self.close(now + 2 * H), 0)
        self.assertEqual(self.runs(), before)
        self.assertEqual(self.win(T0, T0 + H, CL.cut_ts(T0 + H, CFG) + 5 * D)["hash"], h1)

    def test_market_wide_cluster_and_sides(self):
        for i, s in enumerate(("QAUSDT", "QBUSDT", "QCUSDT")):
            add(self.c, T0 + 60 * (i + 1), sym=s)
        add(self.c, T0 + 300, sym="QDUSDT", side="short")
        self.assertEqual(self.close(T0 + 6 * H), 2)
        cl = {}
        for r in self.runs():
            cl.setdefault(r["cluster"], []).append(r["key"])
        self.assertEqual(sorted(len(v) for v in cl.values()), [1, 3])
        self.assertEqual(set(cl), {"%d:long" % (T0 // H), "%d:short" % (T0 // H)})

    def test_scope_kinds_side_and_tradable(self):
        add(self.c, T0 + 60, typ="idea_stale")
        add(self.c, T0 + 120, sym="QBUSDT", side=None)
        add(self.c, T0 + 180, sym="QCUSDT", typ="oi_flush")
        self.c.execute(_SIG, ("S-x", T0 + 240, "QDUSDT", "range_5m", "long", 0, "mainnet", "{}", "done", T0, T0, T0, T0 + 300))
        self.close(T0 + 6 * H)
        self.assertEqual(self.runs(), [])

    def test_missing_fields_give_none(self):
        add(self.c, T0 + 60, typ="oi_spike", ev=ev_of(turn_surge_5m=None))
        add(self.c, T0 + 120, sym="QBUSDT", typ="liq_cascade", ev=ev_of(spread_bps=None, funding_pct=None, chg_24h_pct=None, turn_24h_musd=None))
        self.close(T0 + 6 * H)
        f = {r["type"]: json.loads(r["feats"]) for r in self.runs()}
        self.assertIsNone(f["oi_spike"]["turn_surge_5m"])
        for k in ("spread_bps", "funding_pct", "chg_24h_pct", "coin_class"):
            self.assertIsNone(f["liq_cascade"][k], k)
        self.assertNotIn("ev_kind", f["oi_spike"])
        self.assertNotIn("side_rel", f["oi_spike"])

    def test_env_norm_written(self):
        add(self.c, T0 + 60, env=None)
        add(self.c, T0 + 120, sym="QBUSDT", env="testnet")
        add(self.c, T0 + 180, sym="QCUSDT", env="")
        self.close(T0 + 6 * H)
        self.assertEqual(sorted(r["env_norm"] for r in self.runs()), ["mainnet", "mainnet", "testnet"])

    def test_since_covers_downtime(self):
        for d in range(5):
            add(self.c, T0 + d * D + 60, sym="Q%dUSDT" % d)
        self.close(T0 + 6 * H)
        self.assertEqual(len(self.runs()), 1)
        self.close(T0 + 5 * D)                                      # цикл стоял 4 суток: всё закрывается одним проходом
        self.assertEqual(len(self.runs()), 5)


class TestWindow(Base):
    def test_before_cut_refused(self):
        add(self.c, T0 + 60)
        self.close(T0 + 6 * H)
        b = T0 + H
        with self.assertRaises(CL.WindowNotReady):
            self.win(T0, b, CL.cut_ts(b, CFG) - 1)
        self.assertEqual(len(self.win(T0, b, CL.cut_ts(b, CFG))["rows"]), 1)
        with self.assertRaises(ValueError):
            self.win(T0 + 1, b, CL.cut_ts(b, CFG))

    def test_late_cluster_47h_same_hash(self):
        add(self.c, T0 + 60, r=0.2)
        add(self.c, T0 + H + 60, sym="QBUSDT", r=0.3, status="pending", run=False)     # кластер второго часа размечается через 47 ч
        b = T0 + 2 * H
        self.close(T0 + 6 * H)
        self.c.execute("UPDATE signals SET status='done', labeled_ts=? WHERE sym='QBUSDT'", (b + 47 * H - GRACE,))
        self.c.execute(_RUN, ("S-%d-QBUSDT-range_5m-long" % (T0 + H + 60), "P2", 1, "g1", "c1", T0 + H + 60, "QBUSDT", "range_5m", "long", 1, 0.3, None))
        self.assertEqual(self.close(b + 47 * H), 1)
        cut = CL.cut_ts(b, CFG)
        w1 = self.win(T0, b, cut)
        w2 = self.win(T0, b, cut + 30 * D)
        self.assertEqual(len(w1["rows"]), 2)
        self.assertEqual(w1["hash"], w2["hash"])
        self.assertEqual(len(w1["hash"]), 16)

    def test_observe_last_15_min_before_cut(self):
        add(self.c, T0 + 60, status="pending", run=False)
        b = T0 + H
        cut = CL.cut_ts(b, CFG)
        self.c.execute("UPDATE signals SET status='done', labeled_ts=? WHERE 1", (cut - 700 - GRACE,))
        self.c.execute(_RUN, ("S-%d-QAUSDT-range_5m-long" % (T0 + 60), "P2", 1, "g1", "c1", T0 + 60, "QAUSDT", "range_5m", "long", 1, 0.1, None))
        self.assertEqual(self.close(cut - 600), 1)
        self.assertEqual(len(self.win(T0, b, cut)["rows"]), 1)

    def test_closed_after_cut_excluded_stably(self):
        add(self.c, T0 + 60, status="pending", run=False)
        b = T0 + H
        cut = CL.cut_ts(b, CFG)
        self.c.execute("UPDATE signals SET status='done', labeled_ts=? WHERE 1", (cut - GRACE,))
        self.c.execute(_RUN, ("S-%d-QAUSDT-range_5m-long" % (T0 + 60), "P2", 1, "g1", "c1", T0 + 60, "QAUSDT", "range_5m", "long", 1, 0.1, None))
        self.close(cut + 60)                                        # цикл стоял: кластер записан позже cut_ts
        self.assertEqual(self.win(T0, b, cut + 120)["rows"], [])
        self.assertEqual(self.win(T0, b, cut + 120)["hash"], self.win(T0, b, cut + 10 * D)["hash"])

    def test_filters_ctx_env_policy_and_metric_rows(self):
        add(self.c, T0 + 60, r=1.0)
        add(self.c, T0 + 120, sym="QBUSDT", ctx="c2")
        add(self.c, T0 + 180, sym="QCUSDT", env="testnet")
        add(self.c, T0 + 240, sym="QDUSDT", filled=0)
        add(self.c, T0 + 300, sym="QEUSDT", flags="gap,no_spread")
        add(self.c, T0 + 360, sym="QFUSDT", flags="no_spread")
        self.close(T0 + 6 * H)
        w = self.win(T0, T0 + H, CL.cut_ts(T0 + H, CFG))
        self.assertEqual(len(w["rows"]), 4)
        self.assertEqual(w["n_excl"], 1)
        self.assertEqual(w["excl_share"], round(1 / 3.0, 4))
        self.assertEqual(len(CL.metric_rows(w["rows"])), 2)
        self.assertEqual(len(CL.window(self.c, "P2", 1, "c1", "testnet", T0, T0 + H, CFG, CL.cut_ts(T0 + H, CFG))["rows"]), 1)

    def test_insert_order_does_not_change_hash(self):
        hashes = set()
        for seed in (1, 2):
            items = [(T0 + i * 300, "Q%dUSDT" % (i % 7), "long" if i % 3 else "short", round(random.Random(i).gauss(0, 1), 4)) for i in range(60)]
            random.Random(seed).shuffle(items)
            root = tempfile.mkdtemp(prefix="tbll_o_")
            self.addCleanup(shutil.rmtree, root, True)
            c = store.open_rw(root)
            store2.migrate_v2a(c, root, explicit=True, backup=False, now=T0)
            for t0, sym, side, r in items:
                add(c, t0, sym=sym, side=side, r=r)
            CL.close_clusters(c, T0 + 2 * D, CFG, policy=POLICY, mcfg=MCFG, root=root)
            b = T0 + 5 * H
            hashes.add(CL.window(c, "P2", 1, "c1", "mainnet", T0, b, CFG, CL.cut_ts(b, CFG))["hash"])
            c.close()
        self.assertEqual(len(hashes), 1)

    def test_tamper_bypassing_triggers_sample_changed(self):
        add(self.c, T0 + 60, r=1.0)
        self.close(T0 + 6 * H)
        b = T0 + H
        cut = CL.cut_ts(b, CFG)
        smp = CL.sample(self.win(T0, b, cut))
        self.assertEqual(CL.check_sample(self.c, smp, CFG, cut + D), "OK")
        with self.assertRaises(sqlite3.DatabaseError):
            self.c.execute("UPDATE cluster_runs SET r_net=5")
        self.c.execute("DROP TRIGGER tr_cluster_runs_nu")
        self.c.execute("UPDATE cluster_runs SET r_net=5")
        self.assertEqual(CL.check_sample(self.c, smp, CFG, cut + D), "SAMPLE_CHANGED")

    def test_restored(self):
        add(self.c, T0 + 60, r=1.0)
        self.close(T0 + 6 * H)
        b = T0 + H
        cut = CL.cut_ts(b, CFG)
        smp = dict(CL.sample(self.win(T0, b, cut)), h="0" * 16)          # хеш другой: строки потеряны восстановлением
        self.c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('copy_ts', ?)", (str(cut - 10),))
        self.c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('restore_ts', ?)", (str(cut + 10),))
        self.assertTrue(CL.restored(self.c, cut))
        self.assertEqual(CL.check_sample(self.c, smp, CFG, cut + D), "RESTORED")
        self.c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('copy_ts', ?)", (str(cut),))      # cut_ts <= copy_ts: окно не затронуто
        self.assertFalse(CL.restored(self.c, cut))
        self.assertEqual(CL.check_sample(self.c, smp, CFG, cut + D), "SAMPLE_CHANGED")
        self.c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('copy_ts', ?)", (str(cut - 100),))
        self.c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('restore_ts', ?)", (str(cut - 50),))   # cut_ts > restore_ts: окно продолжается
        self.assertFalse(CL.restored(self.c, cut))

    def test_current_ctx_and_change(self):
        for i in range(4):
            add(self.c, T0 + i * H + 60, sym="Q%dUSDT" % i, ctx="c1")
        for i in range(4, 6):
            add(self.c, T0 + i * H + 60, sym="Q%dUSDT" % i, ctx="c2")
        self.close(T0 + D)
        self.assertEqual(CL.current_ctx(self.c, "P2", 1, "mainnet", T0 + 4 * H), ("c1", T0 + 60))
        self.assertEqual(CL.current_ctx(self.c, "P2", 1, "mainnet", T0 + 10 * H), ("c2", T0 + 4 * H + 60))
        self.assertEqual(CL.current_ctx(self.c, "P2", 1, "testnet", T0 + 10 * H), (None, None))
        self.assertTrue(CL.ctx_changed_since(self.c, "P2", 1, "mainnet", "c1", T0 + 2 * H))
        self.assertFalse(CL.ctx_changed_since(self.c, "P2", 1, "mainnet", "c2", T0 + 4 * H))


if __name__ == "__main__":
    unittest.main()
