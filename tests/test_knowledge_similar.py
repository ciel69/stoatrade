"""R2 задача 25: tb.knowledge.similar - равенство с memory.similar.select, пропуски признаков, размер ответа, индекс (type, side, t0), скорость."""
import copy
import json
import os
import shutil
import tempfile
import time
import unittest

import _bootstrap  # noqa: F401
from _memory_helpers import NOW, mk, synthetic
from tb.knowledge import similar as KS
from tb.labels import schema as LSC
from tb.labels import store
from tb.memory import cfg as memcfg
from tb.memory import similar as MS

CFG = memcfg.load()
SLOW = os.environ.get("TB_SLOW_TESTS") == "1"


def cfg_labels(on=True, **sim):
    c = copy.deepcopy(CFG)
    c.setdefault("similar", {})["labels"] = on
    c["similar"].update(sim)
    return c


def fold(lst):
    out = []
    for i, c in enumerate(lst):
        c = copy.deepcopy(c)
        c["rev"], c["seq"] = 1, i + 1
        out.append(c)
    return out


def q(feats, side=None, mode="live", **kw):
    d = {"features": feats, "side": side, "mode": mode}
    d.update(kw)
    return d


FEATS = {"setup": "level_reclaim", "confirmed": True, "regime": "range", "range_5m_pct": 1.6, "stop_pct": 2.0, "coin_class": "meme", "utc_session": "us"}


def sig_row(i, t0, typ="range_5m", side="long", status="done", env=None, ev=None, feat=None, missing=None, sym="DOOD"):
    key = "%d|%s|%s|%d" % (t0, sym, typ, i)
    return (key, int(t0), sym, typ, side, json.dumps(ev if ev is not None else {}), json.dumps(feat) if feat is not None else None,
            json.dumps(missing) if missing is not None else None, status, int(t0), int(t0), int(t0), LSC.SCHEMA_VER, env)


def insert_signals(conn, rows):
    with store.tx(conn):
        conn.executemany("INSERT INTO signals(key, t0, sym, type, side, ev, feat, missing, status, mature_ts, next_try_ts, captured_ts, schema_ver, env)"
                         " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbsim_")
        os.makedirs(os.path.join(self.root, "state"))
        os.makedirs(os.path.join(self.root, "runtime"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def db(self):
        return store.open_rw(self.root)


class TestBrainEquality(Base):
    def cases(self):
        lst = synthetic(40)
        lst += [mk(100 + i, mode="paper", sym="PAP%d" % i) for i in range(5)]
        lst += [mk(200 + i, type="skip", sym="SKP%d" % i) for i in range(5)]
        lst += [mk(300, flags=["inj_suspect"], sym="INJ"), mk(301, exclude=True, sym="EXC"), mk(302, mode="paper", flags=["dup_of_trade"], sym="DUP"),
                mk(303, rules_ver="2020-01-01", sym="OLD")]
        return fold(lst)

    def test_brain_select_equals_select(self):
        cs = self.cases()
        variants = [dict(), dict(scope="all"), dict(types=("trade",)), dict(rules="any"), dict(k=3), dict(as_of=NOW - 3 * 86400),
                    dict(min_sim=0.3, min_coverage=0.3), dict(include_excluded=True), dict(mode="paper"), dict(max_age_days=0)]
        for side in ("long", "short", None, "watch"):
            for kw in variants:
                query = q(FEATS, side=side)
                a = MS.select(cs, query, CFG, NOW, **kw)
                b = KS.brain_select(cs, query, CFG, NOW, **kw)
                b.pop("pool")
                self.assertEqual(a, b, (side, kw))

    def test_nearest_brain_hits_equal_select(self):
        cs = self.cases()
        for side in ("long", "short", None):
            query = q(FEATS, side=side)
            a = MS.select(cs, query, CFG, NOW, k=10, min_sim=0.3, min_coverage=0.3)
            r = KS.nearest(query, "brain", k=10, cfg=CFG, now=NOW, cases=cs, min_sim=0.3, min_coverage=0.3)
            self.assertTrue(a["hits"])
            self.assertEqual([(h["case"]["case_id"], h["sim"], h["coverage"], h["n_used"]) for h in a["hits"]],
                             [(h["id"], h["sim"], h["cov"], h["n_used"]) for h in r["hits"]])
            self.assertEqual([h["case"]["result"].get("r") for h in a["hits"]], [h["r"] for h in r["hits"]])
            self.assertTrue(all(h["src"] == "brain" for h in r["hits"]))
            self.assertNotIn("off", r)

    def test_prefilter_cap_keeps_newest(self):
        cs = fold([mk(i, sym="S%d" % i) for i in range(30)])
        pool, dropped = KS.prefilter_cases(cs, q(FEATS), CFG, cap=10)
        self.assertEqual(len(pool), 10)
        self.assertEqual(dropped, {KS.PREFILTER_CAP: 20})
        self.assertEqual(sorted(c["ts"] for c in pool), sorted(c["ts"] for c in cs)[-10:])
        sel = KS.brain_select(cs, q(FEATS), CFG, NOW, cap=10, min_sim=0, min_coverage=0, max_age_days=0)
        self.assertEqual(sel["considered"], 30)
        self.assertEqual(sel["filtered"][KS.PREFILTER_CAP], 20)
        self.assertLessEqual(len(sel["hits"]), 8)

    def test_bad_source(self):
        with self.assertRaises(ValueError):
            KS.nearest(q(FEATS), "news", cfg=CFG, now=NOW, cases=[])


class TestLabels(Base):
    def fill(self, conn):
        base = int(NOW) - 86400
        feat = {"range_15m_pct": 2.0, "utc_hour": 17, "utc_session": "us", "coin_class": "meme", "fng": 40, "levels": [1.0, 2.0]}
        rows = [
            sig_row(1, base + 100, ev={"range_5m_pct": 1.6, "spread_bps": 5.0}, feat=feat),                     # точное совпадение
            sig_row(2, base + 200, ev={"range_5m_pct": 3.0, "spread_bps": 5.0}, feat=feat),                     # дальше по range_5m
            sig_row(3, base + 300, side="short", ev={"range_5m_pct": 1.6}, feat=feat),                          # другая сторона
            sig_row(4, base + 400, typ="oi_spike", ev={"range_5m_pct": 1.6}, feat=feat),                        # другой тип
            sig_row(5, base + 500, status="pending", ev={"range_5m_pct": 1.6}, feat=feat),                      # не размечена
            sig_row(6, base + 600, env="testnet", ev={"range_5m_pct": 1.6}, feat=feat),                         # другой режим данных
            sig_row(7, base + 700, ev={"range_5m_pct": 1.6}, feat=dict(feat, utc_session="asia", utc_hour=3)),  # другая сессия
        ]
        insert_signals(conn, rows)
        k1 = rows[0][0]
        with store.tx(conn):
            conn.execute("INSERT INTO outcomes(key, h, side, net_dir_pct) VALUES(?,?,?,?)", (k1, 60, "long", 0.42))
            conn.execute("INSERT INTO outcomes(key, h, side, net_dir_pct) VALUES(?,?,?,?)", (k1, 15, "long", -0.1))
            for ver, r in ((1, -0.5), (2, 0.75)):
                conn.execute("INSERT INTO policy_runs(key, policy_id, policy_ver, geometry_ver, ctx_ver, t0, sym, type, side, filled, r_net)"
                             " VALUES(?,?,?,?,?,?,?,?,?,?,?)", (k1, "P2", ver, "g", "c", rows[0][1], "DOOD", "range_5m", "long", 1, r))
        return rows

    def query(self, **kw):
        f = {"range_5m_pct": 1.6, "spread_bps": 5.0, "range_15m_pct": 2.0, "utc_hour": 17, "coin_class": "meme", "fng": 40}
        return q(f, side="long", sig_type="range_5m", **kw)

    def test_off_by_default(self):
        self.assertIs(CFG["similar"].get("labels"), False)
        r = KS.nearest(self.query(), "labels", cfg=CFG, now=NOW, root=self.root)
        self.assertEqual(r["off"], {"labels": KS.LABELS_OFF})
        self.assertEqual(r["hits"], [])
        self.assertFalse(os.path.exists(store.db_path(self.root)))          # база не открывалась и не создавалась
        r = KS.nearest(self.query(), "both", cfg=CFG, now=NOW, root=self.root, cases=[])
        self.assertEqual(r["off"], {"labels": KS.LABELS_OFF})

    def test_no_db(self):
        r = KS.nearest(self.query(), "labels", cfg=cfg_labels(), now=NOW, root=self.root)
        self.assertEqual(r["off"], {"labels": KS.NO_DB})

    def test_prefilter_and_outcome(self):
        conn = self.db()
        try:
            rows = self.fill(conn)
            KS.migrate_v2s(conn)
            cfg = cfg_labels(min_sim=0.0, min_coverage=0.0)
            r = KS.nearest(self.query(), "labels", cfg=cfg, now=NOW, conn=conn, k=10)
            ids = [h["id"] for h in r["hits"]]
            self.assertEqual(ids[0], rows[0][0])
            self.assertEqual(set(ids), {rows[0][0], rows[1][0], rows[6][0]})  # без utc_session в запросе сессия не фильтрует
            h = r["hits"][0]
            self.assertEqual((h["sim"], h["cov"]), (1.0, 1.0))
            self.assertEqual((h["net_dir_pct"], h["r"], h["filled"]), (0.42, 0.75, True))   # горизонт 60, последняя версия политики
            self.assertEqual(r["hits"][1]["net_dir_pct"], None)
            self.assertEqual(r["n"], {"labels": 3})
            self.assertNotIn("off", r)
            r = KS.nearest(self.query(), "labels", cfg=cfg, now=NOW, conn=conn, utc_session="us", h=15)
            self.assertEqual([x["id"] for x in r["hits"]], [rows[0][0], rows[1][0]])
            self.assertEqual(r["hits"][0]["net_dir_pct"], -0.1)
            r = KS.nearest(self.query(mode="testnet"), "labels", cfg=cfg, now=NOW, conn=conn)
            self.assertEqual([x["id"] for x in r["hits"]], [rows[5][0]])
            r = KS.nearest(self.query(), "labels", cfg=cfg, now=NOW, conn=conn, as_of=rows[1][1])
            self.assertEqual([x["id"] for x in r["hits"]], [rows[0][0]])
            r = KS.nearest(q({"range_5m_pct": 1.6}, side="long"), "labels", cfg=cfg, now=NOW, conn=conn)
            self.assertEqual(r["off"], {"labels": KS.NO_SIG_TYPE})
            r = KS.nearest(q({"range_5m_pct": 1.6, "ev_kind": "oi_spike"}, side="long"), "labels", cfg=cfg, now=NOW, conn=conn)
            self.assertEqual([x["id"] for x in r["hits"]], [rows[3][0]])
        finally:
            conn.close()

    def test_sim_equals_memory_similarity(self):
        conn = self.db()
        try:
            rows = self.fill(conn)
            cfg = cfg_labels(min_sim=0.0, min_coverage=0.0)
            query = self.query()
            query["features"]["setup"] = "level_reclaim"                   # у меток признака нет
            r = KS.nearest(query, "labels", cfg=cfg, now=NOW, conn=conn, k=10)
            feats = dict(query["features"], side="long")
            for h in r["hits"]:
                row = [x for x in rows if x[0] == h["id"]][0]
                v = KS.label_vector(row[6], row[5], row[4], row[7])
                s, cov, n = MS.similarity(feats, v, cfg)
                self.assertEqual((h["sim"], h["cov"], h["n_used"]), (s, cov, n))
            self.assertLess(r["hits"][0]["cov"], 1.0)                          # setup отсутствует -> coverage ниже, а не ноль сходства
            self.assertEqual(r["hits"][0]["sim"], 1.0)
        finally:
            conn.close()

    def test_missing_feature_is_absent(self):
        conn = self.db()
        try:
            base = int(NOW) - 3600
            full = {"range_15m_pct": 2.0, "utc_hour": 17, "coin_class": "meme"}
            part = {"range_15m_pct": None, "utc_hour": 17, "coin_class": "unknown"}
            rows = [sig_row(1, base, ev={"range_5m_pct": 1.6}, feat=full), sig_row(2, base + 1, ev={"range_5m_pct": 1.6}, feat=part,
                                                                                      missing=["range_15m_pct"])]
            insert_signals(conn, rows)
            cfg = cfg_labels(min_sim=0.0, min_coverage=0.0)
            query = q({"range_5m_pct": 1.6, "range_15m_pct": 9.0, "utc_hour": 17, "coin_class": "meme"}, side="long", sig_type="range_5m")
            r = KS.nearest(query, "labels", cfg=cfg, now=NOW, conn=conn)
            by = dict((h["id"], h) for h in r["hits"])
            hf, hp = by[rows[0][0]], by[rows[1][0]]
            self.assertLess(hp["cov"], hf["cov"])
            self.assertEqual(hp["n_used"], hf["n_used"] - 2)                   # range_15m_pct=None и coin_class=unknown не сравниваются
            self.assertEqual(hp["sim"], 1.0)                                    # пропуск не превращается в ноль сходства
            self.assertLess(hf["sim"], 1.0)
            v = KS.label_vector(rows[1][6], rows[1][5], "long", rows[1][7])
            self.assertNotIn("range_15m_pct", v)
            self.assertNotIn("coin_class", v)
            self.assertNotIn("levels", KS.label_vector({"levels": [1, 2]}, {}, "long"))
            # порог coverage как у select
            r = KS.nearest(query, "labels", cfg=cfg_labels(min_sim=0.0, min_coverage=0.99), now=NOW, conn=conn)
            self.assertEqual([h["id"] for h in r["hits"]], [rows[0][0]])
        finally:
            conn.close()

    def test_both_merge_and_size(self):
        conn = self.db()
        try:
            base = int(NOW) - 86400
            feat = {"range_15m_pct": 2.0, "utc_hour": 17, "utc_session": "us", "coin_class": "meme"}
            rows = [sig_row(i, base + i, ev={"range_5m_pct": 1.0 + i * 0.05, "spread_bps": 5.0}, feat=feat, sym="SYMBOLLONGNAME%03d" % i) for i in range(60)]
            insert_signals(conn, rows)
            cs = fold(synthetic(40))
            cfg = cfg_labels(min_sim=0.0, min_coverage=0.0, k_max=20)
            query = q(dict(FEATS, spread_bps=5.0, range_15m_pct=2.0, utc_hour=17), side="long", sig_type="range_5m")
            r = KS.nearest(query, "both", cfg=cfg, now=NOW, conn=conn, cases=cs, k=20)
            body = json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.assertLessEqual(len(body), 2048)
            self.assertEqual(set(r["n"]), {"brain", "labels"})
            keys = [(-h["sim"], -KS._hit_t(h), h["src"], h["id"]) for h in r["hits"]]
            self.assertEqual(keys, sorted(keys))
            self.assertLessEqual(len(r["hits"]) + r.get("trimmed", 0), 20)
            r = KS.nearest(query, "both", cfg=cfg, now=NOW, conn=conn, cases=cs, k=10)
            self.assertLessEqual(len(json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode("utf-8")), 2048)
            srcs = set(h["src"] for h in r["hits"])
            self.assertTrue(srcs <= {"brain", "labels"})
            r = KS.nearest(query, "both", cfg=cfg, now=NOW, conn=conn, cases=cs, k=10, max_bytes=600)
            self.assertLessEqual(len(json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode("utf-8")), 600)
            self.assertGreater(r["trimmed"], 0)
        finally:
            conn.close()


class TestIndexMigration(Base):
    def test_migrate_v2s_idempotent_own_key(self):
        conn = self.db()
        try:
            self.assertFalse(KS.has_index(conn))
            self.assertEqual(KS.migrate_v2s(conn), KS.SIM_VER)
            self.assertTrue(KS.has_index(conn))
            self.assertEqual(KS.migrate_v2s(conn), KS.SIM_VER)
            meta = dict(conn.execute("SELECT k, v FROM meta").fetchall())
            self.assertEqual(meta[KS.META_VER_KEY], str(KS.SIM_VER))
            self.assertEqual(meta[LSC.META_VER_KEY], str(LSC.SCHEMA_VER))
            self.assertNotIn("knowledge_v2_ver", meta)
            cols = [r[2] for r in conn.execute("PRAGMA index_info(%s)" % KS.INDEX_NAME)]
            self.assertEqual(cols, ["type", "side", "t0"])
            plan = " ".join(str(r) for r in conn.execute(
                "EXPLAIN QUERY PLAN SELECT key FROM signals WHERE type=? AND side=? AND status='done' ORDER BY t0 DESC LIMIT 5000", ("a", "long")))
            self.assertIn(KS.INDEX_NAME, plan)
            self.assertNotIn("TEMP B-TREE", plan.upper())
        finally:
            conn.close()
        conn = self.db()                         # повторное открытие (миграции меток) индекс не трогает
        try:
            self.assertTrue(KS.has_index(conn))
        finally:
            conn.close()

    def test_migrate_without_signals_fails(self):
        import sqlite3
        from tb.knowledge import store2
        conn = sqlite3.connect(":memory:", isolation_level=None)
        conn.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
        with self.assertRaises(store2.KnowledgeMigrationError):
            KS.migrate_v2s(conn)
        self.assertIsNone(conn.execute("SELECT v FROM meta WHERE k=?", (KS.META_VER_KEY,)).fetchone())
        conn.close()


@unittest.skipUnless(SLOW, "TB_SLOW_TESTS=1")
class TestSpeed(Base):
    def test_both_100k_signals_20k_cases(self):
        types = ("range_5m", "turnover_surge", "hot_enter", "oi_spike", "funding_extreme")
        sess = ("asia", "europe", "us")
        base = int(NOW) - 100000 * 30
        rows = []
        for i in range(100000):
            t0 = base + i * 30
            feat = {"atr5_pct": 0.5 + (i % 7) * 0.1, "range_15m_pct": 1.0 + (i % 11) * 0.2, "btc_ret_1h_pct": (i % 9 - 4) * 0.1, "utc_hour": (i // 120) % 24,
                    "dow": i % 7, "utc_session": sess[i % 3], "coin_class": ("meme", "alt_mid", "major")[i % 3], "fng": 20 + i % 60, "levels": [1.0, 2.0, 3.0]}
            ev = {"range_5m_pct": 0.5 + (i % 13) * 0.25, "spread_bps": 1 + i % 8, "turn_surge_5m": 1 + i % 6, "funding_pct": (i % 5) * 0.01, "chg_24h_pct": i % 20}
            rows.append(sig_row(i, t0, typ=types[i % 5], side=("long", "short")[(i // 5) % 2], ev=ev, feat=feat, sym="S%d" % (i % 300)))
        conn = self.db()
        try:
            insert_signals(conn, rows)
            KS.migrate_v2s(conn)
            with store.tx(conn):
                conn.executemany("INSERT INTO outcomes(key, h, side, net_dir_pct) VALUES(?,?,?,?)", [(r[0], 60, r[4], 0.1) for r in rows[::3]])
            cs = []
            for i in range(20000):
                c = mk(i, sym="C%d" % (i % 500), side=("long", "short")[i % 2], ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 600 - i * 120)),
                       range_5m_pct=0.5 + (i % 13) * 0.25, stop_pct=1.0 + (i % 5) * 0.4, setup=("level_reclaim", "sweep_reject", "breakout_retest")[i % 3],
                       regime=("range", "trend")[i % 2], confirmed=bool(i % 2), mode=("live", "live", "paper")[i % 3])
                c["rev"], c["seq"] = 1, i + 1
                cs.append(c)
            cfg = cfg_labels()
            query = q({"setup": "level_reclaim", "confirmed": True, "regime": "range", "range_5m_pct": 1.6, "stop_pct": 2.0, "coin_class": "meme",
                       "utc_session": "us", "utc_hour": 17, "spread_bps": 3.0, "range_15m_pct": 1.8, "turn_surge_5m": 3.0, "fng": 40},
                      side="long", sig_type="range_5m")
            best = None
            for _ in range(5):
                t = time.perf_counter()
                r = KS.nearest(query, "both", cfg=cfg, now=NOW, conn=conn, cases=cs, k=10)
                dt = time.perf_counter() - t
                best = dt if best is None else min(best, dt)
            self.assertEqual(r["n"]["labels"], KS.PREFILTER_SIGNALS)
            self.assertEqual(r["n"]["brain"], KS.PREFILTER_CASES)
            self.assertTrue(r["hits"])
            print("\n[similar speed] both, 100k signals / 20k cases, k=10: best %.1f ms, last %.1f ms" % (best * 1000, dt * 1000))
            self.assertLessEqual(best, 0.200)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
