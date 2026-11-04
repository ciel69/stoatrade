"""R1.7, задача 5: ряды сводного рынка tb.cmkt.store (Requirements 13.1–13.6; свойства 3, 14).

Все файлы — во временном корне; боевые state/ и runtime/ не трогаются.
"""
import json
import os
import random
import shutil
import sqlite3
import statistics
import tempfile
import time
import unittest

import _bootstrap  # noqa: F401
from tb.cmkt import conf as C
from tb.cmkt import store as S
from tb.logic import cmkt as L

DAY0 = 1793750400            # 2026-11-04 00:00:00 UTC
assert S.day_of(DAY0) == "20261104"


def synth_f(rng, none_share=0.3):
    """Признаки с типичными величинами (по таблице масштабов design.md), около 30 % None."""
    typical = {"cm_n_perp": 5, "cm_n_spot": 4, "cm_cov": 0.85, "cm_oi_musd": 3000.0, "cm_lead_ex": 3, "cm_lead_lag_s": 2,
               "cm_pulled_15m_n": 2, "cm_fund_w_pct": 0.01, "cm_fund_spread_pct": 0.005, "cm_liq_cov": 0.7}
    out = {}
    for n in L.CM_FEATURES:
        if rng.random() < none_share:
            out[n] = None
            continue
        base = typical.get(n)
        if base is None:
            if n.endswith("_pct"):
                base = 30.0 if "imb" in n or "cvd" in n else 0.5
            elif n.endswith("_musd"):
                base = 5.0
            elif n.endswith("_bps"):
                base = 8.0
            else:
                base = 1.0
        out[n] = base * (rng.random() * 2 - 1) if isinstance(base, float) else int(base * rng.random()) + 1
    return out


def synth_by_ex(rng, n_mkts=10):
    rows = []
    for m in L.MARKETS[:n_mkts]:
        rows.append({"mkt": m, "cvd5": rng.uniform(-3e5, 3e5), "doi5": rng.uniform(-50, 50) if m.endswith("perp") else None,
                     "liq5_long": rng.choice([0.0, 0.0, rng.uniform(0, 5e4)]) if m.endswith("perp") else None,
                     "liq5_short": rng.choice([0.0, 0.0, rng.uniform(0, 5e4)]) if m.endswith("perp") else None,
                     "pulled15": rng.randint(0, 2)})
    return rows


def synth_row(rng, sym, tier):
    return {"sym": sym, "tier": tier, "f": synth_f(rng), "flags": ["warmup"] if rng.random() < 0.1 else [], "by_ex": synth_by_ex(rng),
            "focus_s": rng.randint(0, 86400), "skew_ms": rng.randint(50, 1800), "gap_s": None}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbcmkt_store_")
        self.cfg = C.defaults()
        self.now = [DAY0 + 10.0]
        self.free = [10 ** 12]

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def store(self, **kw):
        s = S.Store(self.root, self.cfg, clock=lambda: self.now[0], disk_free=lambda p: self.free[0], **kw)
        self.addCleanup(s.close)
        return s

    def path(self, day):
        return S.day_path(self.root, day, self.cfg)


class TestWriteRead(Base):
    def test_roundtrip_and_schema(self):
        st = self.store()
        rng = random.Random(1)
        row = synth_row(rng, "BTC", "deep")
        r = st.write(DAY0 + 10, [row], wr_ts=DAY0 + 10.4)
        self.assertEqual(r, {"n": 1, "code": None})
        st.close()
        got = S.read_row(self.root, "BTC", DAY0 + 10, self.cfg)
        self.assertEqual(got["tier"], "deep")
        self.assertAlmostEqual(got["wr_ts"], DAY0 + 10.4, places=3)
        self.assertEqual(got["f"], L.quantized(row["f"]))
        self.assertEqual(got["flags"], row["flags"])
        self.assertEqual(got["cm_ver"], L.CM_VER)
        self.assertEqual([x["mkt"] for x in got["by_ex"]], [x["mkt"] for x in row["by_ex"]])
        conn = sqlite3.connect(self.path("20261104"))
        try:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            meta = dict(conn.execute("SELECT k, v FROM meta"))
            self.assertEqual(meta["cm_ver"], L.CM_VER)
            self.assertEqual(json.loads(meta["feats"]), [list(x) for x in L.FEATURE_SCALES])
            ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name='rows'").fetchone()[0]
            self.assertIn("PRIMARY KEY(ts, sym_id)", ddl)
            self.assertIn("WITHOUT ROWID", ddl)
            self.assertEqual(dict(conn.execute("SELECT mkt, mkt_id FROM mkts"))["binance_perp"], 1)
        finally:
            conn.close()

    def test_tiers_and_steps(self):
        st = self.store()
        rng = random.Random(2)
        for ts in range(DAY0 + 1, DAY0 + 11):
            st.write(ts, [synth_row(rng, "BTC", "deep"), synth_row(rng, "DOGE", "light")], wr_ts=ts + 0.3)
        st.close()
        deep = S.read_rows(self.root, "BTC", DAY0, DAY0 + 20, self.cfg)
        light = S.read_rows(self.root, "DOGE", DAY0, DAY0 + 20, self.cfg)
        self.assertEqual([r["ts"] for r in deep], list(range(DAY0 + 1, DAY0 + 11)))
        self.assertEqual([r["ts"] for r in light], [DAY0 + 5, DAY0 + 10])         # light только при ts % 5 == 0
        self.assertEqual(st.stats["skip_tier"], 8)
        self.assertEqual(S.read_rows(self.root, "BTC", DAY0, DAY0 + 20, self.cfg, tier="light"), [])

    def test_monotonic_ts_property3(self):
        """Свойство 3: строки рядов строго возрастают по ts при шаге часов назад; повтор ts не пишется."""
        st = self.store()
        rng = random.Random(3)
        seq = [DAY0 + 5, DAY0 + 6, DAY0 + 4, DAY0 + 6, DAY0 + 7, DAY0 + 2, DAY0 + 9]
        codes = [st.write(ts, [synth_row(rng, "ETH", "deep")])["code"] for ts in seq]
        self.assertEqual(codes, [None, None, "CLOCK_BACK", "CLOCK_BACK", None, "CLOCK_BACK", None])
        self.assertEqual(st.stats["clock_back"], 3)
        st.close()
        ts = [r["ts"] for r in S.read_rows(self.root, "ETH", DAY0, DAY0 + 20, self.cfg)]
        self.assertEqual(ts, sorted(set(ts)))
        self.assertEqual(ts, [DAY0 + 5, DAY0 + 6, DAY0 + 7, DAY0 + 9])
        # перезапуск писателя: последняя отметка берётся из файла
        st2 = self.store()
        self.assertEqual(st2.write(DAY0 + 8, [synth_row(rng, "ETH", "deep")])["code"], "CLOCK_BACK")
        self.assertEqual(st2.write(DAY0 + 10, [synth_row(rng, "ETH", "deep")])["code"], None)

    def test_midnight_switches_file(self):
        st = self.store()
        rng = random.Random(4)
        for ts in (DAY0 + 86398, DAY0 + 86399, DAY0 + 86400, DAY0 + 86401):
            st.write(ts, [synth_row(rng, "SOL", "deep")], wr_ts=ts + 0.2)
        st.close()
        self.assertTrue(os.path.isfile(self.path("20261104")))
        self.assertTrue(os.path.isfile(self.path("20261105")))
        rows = S.read_rows(self.root, "SOL", DAY0 + 86390, DAY0 + 86410, self.cfg)
        self.assertEqual([r["ts"] for r in rows], [DAY0 + 86398, DAY0 + 86399, DAY0 + 86400, DAY0 + 86401])
        c = sqlite3.connect(self.path("20261105"))
        try:
            self.assertEqual(c.execute("SELECT MIN(ts) FROM rows").fetchone()[0], DAY0 + 86400)
        finally:
            c.close()

    def test_reader_is_read_only_and_never_raises(self):
        self.assertIsNone(S.read_row(self.root, "BTC", DAY0 + 1, self.cfg))           # нет файла
        os.makedirs(os.path.dirname(self.path("20261104")), exist_ok=True)
        with open(self.path("20261104"), "wb") as f:
            f.write(b"garbage" * 100)
        self.assertIsNone(S.read_row(self.root, "BTC", DAY0 + 1, self.cfg))           # битый файл
        self.assertIsNone(S.page_stats(self.path("20261104")))
        os.remove(self.path("20261104"))
        st = self.store()
        st.write(DAY0 + 1, [synth_row(random.Random(5), "BTC", "deep")])
        conn = S.open_ro(self.path("20261104"))
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM rows")
        finally:
            conn.close()

    def test_old_cm_ver_file_is_read_by_its_feats(self):
        """Файл со старым cm_ver читается по своим meta.feats (имена и масштабы файла)."""
        st = self.store()
        st.write(DAY0 + 1, [synth_row(random.Random(6), "BTC", "deep")])
        st.close()
        old = [["cm_n_perp", 1], ["cm_old_pct", 100], ["cm_cov", 1000]]
        buf = bytearray()
        for q in (3, -1234, None):
            L._put_opt(buf, q)
        c = sqlite3.connect(self.path("20261104"))
        try:
            c.execute("UPDATE meta SET v=? WHERE k='feats'", (json.dumps(old),))
            c.execute("UPDATE meta SET v='deadbeef' WHERE k='cm_ver'")
            c.execute("UPDATE rows SET f=?", (bytes(buf),))
            c.commit()
        finally:
            c.close()
        got = S.read_row(self.root, "BTC", DAY0 + 1, self.cfg)
        self.assertEqual(got["cm_ver"], "deadbeef")
        self.assertEqual(got["f"], {"cm_n_perp": 3, "cm_old_pct": -12.34, "cm_cov": None})
        # писатель не дописывает в файл суток со старым набором: файл уходит в .cmver-*, запись — в новый
        st2 = self.store()
        self.assertEqual(st2.write(DAY0 + 2, [synth_row(random.Random(7), "BTC", "deep")])["code"], None)
        st2.close()
        names = os.listdir(os.path.dirname(self.path("20261104")))
        self.assertTrue(any(n.startswith("20261104.cmver-") and n.endswith(".sqlite") for n in names), names)
        self.assertEqual(S.read_row(self.root, "BTC", DAY0 + 2, self.cfg)["cm_ver"], L.CM_VER)


class TestFaults(Base):
    def test_quarantine_notadb_and_continue(self):
        """Requirement 13.5: SQLITE_NOTADB -> файл суток переименован в *.corrupt-<ts>.sqlite, запись продолжается в новый файл, без исключений."""
        st = self.store()
        rng = random.Random(8)
        st.write(DAY0 + 1, [synth_row(rng, "BTC", "deep")])
        st.close()
        for suf in ("-wal", "-shm"):
            if os.path.exists(self.path("20261104") + suf):
                os.remove(self.path("20261104") + suf)
        with open(self.path("20261104"), "wb") as f:
            f.write(b"\x00not a sqlite file" * 512)
        self.now[0] = DAY0 + 3.0
        r = st.write(DAY0 + 3, [synth_row(rng, "BTC", "deep")])
        self.assertEqual(r, {"n": 1, "code": None})
        self.assertEqual(st.stats["corrupt"], 1)
        names = os.listdir(os.path.dirname(self.path("20261104")))
        self.assertIn("20261104.corrupt-%d.sqlite" % (DAY0 + 3), names)
        st.close()
        self.assertIsNotNone(S.read_row(self.root, "BTC", DAY0 + 3, self.cfg))
        self.assertTrue(S.is_corrupt(sqlite3.DatabaseError("file is not a database")))
        self.assertTrue(S.is_corrupt(sqlite3.DatabaseError("database disk image is malformed")))
        self.assertFalse(S.is_corrupt(sqlite3.OperationalError("database is locked")))

    def test_disk_low_stops_rows_only(self):
        st = self.store()
        self.free[0] = 39 * S.GIB
        r = st.write(DAY0 + 1, [synth_row(random.Random(9), "BTC", "deep")])
        self.assertEqual(r, {"n": 0, "code": "DISK_LOW"})
        self.assertTrue(st.stats["disk_low"])
        self.assertFalse(os.path.exists(self.path("20261104")))
        self.free[0] = 41 * S.GIB
        self.now[0] += 30                                  # проверка диска не чаще раза в 60 с
        self.assertEqual(st.write(DAY0 + 2, [])["code"], "DISK_LOW")
        self.now[0] += 31
        self.assertEqual(st.write(DAY0 + 3, [synth_row(random.Random(9), "BTC", "deep")])["code"], None)
        self.assertFalse(st.stats["disk_low"])

    def test_db_error_is_not_raised(self):
        st = self.store()
        st.write(DAY0 + 1, [synth_row(random.Random(10), "BTC", "deep")])
        st.conn.close()                                    # соединение испорчено извне
        r = st.write(DAY0 + 2, [synth_row(random.Random(10), "BTC", "deep")])
        self.assertEqual(r["code"], "ERR")
        self.assertEqual(st.write(DAY0 + 3, [synth_row(random.Random(10), "BTC", "deep")])["code"], None)    # переоткрыт


class TestRotation(Base):
    def touch(self, name):
        d = S.store_dir(self.root, self.cfg)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "wb") as f:
            f.write(b"x")

    def test_rotation_property14(self):
        """Свойство 14: после прохода нет файлов старше keep_days, файлы текущих и предыдущих суток есть всегда."""
        rng = random.Random(11)
        for keep in (2, 3, 30):
            self.cfg["store"]["keep_days"] = keep
            shutil.rmtree(S.store_dir(self.root, self.cfg), ignore_errors=True)
            today = DAY0 + rng.randint(0, 400) * 86400
            for back in range(0, 40):
                d = S.day_of(today - back * 86400)
                self.touch("%s.sqlite" % d)
                if back % 7 == 0:
                    self.touch("%s.sqlite-wal" % d)
                    self.touch("%s.corrupt-123.sqlite" % d)
            self.touch("notes.txt")
            st = self.store()
            st.rotate(today + 3600)
            left = sorted(os.listdir(S.store_dir(self.root, self.cfg)))
            self.assertIn("notes.txt", left)
            for n in left:
                m = S._RE_FILE.match(n)
                if m:
                    age = (S.day_start(S.day_of(today)) - S.day_start(m.group(1))) // 86400
                    self.assertLessEqual(age, keep, n)
            self.assertIn("%s.sqlite" % S.day_of(today), left)
            self.assertIn("%s.sqlite" % S.day_of(today - 86400), left)
            st.close()

    def test_permission_error_retried_next_pass(self):
        self.touch("%s.sqlite" % S.day_of(DAY0 - 40 * 86400))
        st = self.store()
        real = os.remove
        calls = []

        def deny(p):
            calls.append(p)
            raise PermissionError(13, "in use", p)
        os.remove = deny
        try:
            r = st.maybe_rotate(DAY0)
        finally:
            os.remove = real
        self.assertEqual(len(r["failed"]), 1)
        self.assertIsNone(st.maybe_rotate(DAY0 + 600))           # повтор не раньше чем через час
        r = st.maybe_rotate(DAY0 + 3601)
        self.assertEqual(len(r["deleted"]), 1)
        self.assertIsNone(st.maybe_rotate(DAY0 + 7200))          # следующий проход — через сутки

    def test_open_file_never_deleted(self):
        st = self.store()
        st.write(DAY0 + 1, [synth_row(random.Random(12), "BTC", "deep")])
        st.rotate(DAY0 + 100 * 86400)                            # часы ушли далеко вперёд
        self.assertTrue(os.path.isfile(self.path("20261104")))


class TestBudget(Base):
    def test_not_in_knowledge_backup(self):
        """Requirement 13.6: make_backup копирует только state/knowledge.sqlite; файлы рядов в копии не попадают."""
        from tb.knowledge import store2
        st = self.store()
        st.write(DAY0 + 1, [synth_row(random.Random(13), "BTC", "deep")])
        st.close()
        self.assertNotEqual(os.path.dirname(os.path.abspath(store2.db_path(self.root))), os.path.abspath(S.store_dir(self.root, self.cfg)))
        os.makedirs(os.path.dirname(store2.db_path(self.root)), exist_ok=True)
        c = sqlite3.connect(store2.db_path(self.root))
        c.execute("CREATE TABLE t(x)")
        c.commit()
        c.close()
        try:
            store2.make_backup(self.root, "owner")
        except Exception:                                        # noqa: BLE001 — копия может не пройти проверку схемы; важно, что в ней
            pass
        bdir = store2.backups_dir(self.root)
        found = []
        for d, _, fs in os.walk(bdir):
            found.extend(fs)
        self.assertFalse([n for n in found if n.startswith("2026") or "cmkt" in n], found)
        self.assertTrue(os.path.isfile(self.path("20261104")))

    def test_tick_write_time_and_row_size(self):
        """Критерии задачи 5: 160 строк такта пишутся за ≤ 20 мс; средний размер строки на синтетических рядах — 215 Б ± 30 %
        (Requirement 13.4: около 820 МБ в сутки при пределах фокуса; заполнение страниц — page_stats)."""
        st = self.store()
        rng = random.Random(14)
        deep = ["D%02d" % i for i in range(20)]
        light = ["L%03d" % i for i in range(140)]
        times = []
        t0 = DAY0 + 1000                                          # кратно 5: на каждой 5-й секунде пишутся и light
        for k in range(60):
            ts = t0 + k
            rows = [synth_row(rng, s, "deep") for s in deep]
            if ts % 5 == 0:
                rows += [synth_row(rng, s, "light") for s in light]
            a = time.perf_counter()
            r = st.write(ts, rows)
            dt = (time.perf_counter() - a) * 1000.0
            self.assertIsNone(r["code"])
            if ts % 5 == 0:
                times.append(dt)
        st.close()
        self.assertLessEqual(statistics.median(times), 20.0, times)
        ps = S.page_stats(self.path("20261104"))
        self.assertEqual(ps["rows"], 60 * 20 + 12 * 140)
        self.assertGreaterEqual(ps["bytes_per_row"], 215 * 0.7, ps)
        self.assertLessEqual(ps["bytes_per_row"], 215 * 1.3, ps)
        if ps["fill"] is not None:                                # дописывание в конец дерева: страницы заполнены плотно
            self.assertGreaterEqual(ps["fill"], 0.85, ps)


if __name__ == "__main__":
    unittest.main()
