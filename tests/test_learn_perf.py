"""Скорость и размер страницы «Учится ли мозг» (R0.2, задача 14; требования 13.4, 13.7, 14.2-14.4, 17.1). Синтетические данные во временном корне,
боевые state/config/runtime не читаются и не пишутся.

Пороги из спеки: холодная сборка без блока памяти <= 1,0 с на 500 сделках / 5 000 случаях / 20 000 строках policy_runs; кеш <= 20 мс; ответ <= 64 КБ;
расширенный набор (2 000 / 20 000 / 100 000, <= 2,0 с) и агрегат на 1 000 000 строк - при TB_SLOW_TESTS=1.
Запас на медленные машины: TB_PERF_FACTOR (по умолчанию 1.0) умножает пороги времени; холодная сборка берётся лучшей из двух попыток.
Пороги времени проверяются только при TB_SLOW_TESTS=1 (`./tb test --slow`): обычный `./tb test` идёт параллельно, и замер на занятом процессоре
не говорит о скорости кода. Данные, ответ, кеш, размер и «только чтение» проверяются всегда."""
import hashlib
import json
import os
import random
import shutil
import time
import unittest

import _bootstrap  # noqa: F401
import _learn_report_helpers as H
from tb.labels import query, store
from tb.learn import cache, memory_vs_base, report
from tb.memory import cases as memcases

FACTOR = float(os.environ.get("TB_PERF_FACTOR") or 1.0)
SLOW = os.environ.get("TB_SLOW_TESTS") == "1"
COLD_S, COLD_BIG_S, CACHE_S = 1.0 * FACTOR, 2.0 * FACTOR, 0.020 * FACTOR
AGG_100K_S, AGG_1M_S = 0.5 * FACTOR, 3.0 * FACTOR
CFG_NO_MEMORY = {"schema": "tb.learning_cfg/1", "memory": {"max_trades": 100}}       # больше 100 сделок: блок памяти too_many_for_request, без расчёта


def snapshot(root):
    """Состав и содержимое файлов state/, config/, runtime/ (имя -> размер, sha1)."""
    out = {}
    for sub in ("state", "config", "runtime"):
        for d, _, fs in os.walk(os.path.join(root, sub)):
            for f in fs:
                p = os.path.join(d, f)
                with open(p, "rb") as fh:
                    out[os.path.relpath(p, root)] = (os.path.getsize(p), hashlib.sha1(fh.read()).hexdigest())
    return out


def fill_policy_runs(root, n, seed=5):
    """n строк policy_runs (две политики, один ctx_ver) за 30 суток до H.NOW; пишет одной транзакцией."""
    rng = random.Random(seed)
    conn = store.open_rw(root)
    try:
        def gen():
            for i in range(n):
                pid = ("P5", "P6")[i % 2]
                r = round(rng.gauss(0.0, 0.9), 3)
                yield ("k%d" % i, pid, 1, "g1", "aaaa0001", H.NOW - 3600 - (i * 2592000 // max(n, 1)), "AAA", "range_5m", "long", None, None, 1,
                       "tp" if r > 0 else "sl", r, r, 0.1, None, None, 10.0, None)
        with store.tx(conn):
            conn.executemany("INSERT INTO policy_runs(%s) VALUES(%s)" % (H.COLS, ",".join("?" * 20)), gen())
    finally:
        conn.close()


def put_install(root, trades, cases, runs, seed=7):
    """Журнал сделок (пары open/close), случаи (цепочка cases.jsonl) и policy_runs. -> секунды подготовки (в замер не входят)."""
    t0 = time.perf_counter()
    rng = random.Random(seed)
    rs = H.gauss_rs(rng, [(trades, 0.1, 1.0)])
    ts = H.times(trades, 3 * 3600)
    H._jsonl(root, "trades.jsonl", H.trade_recs(rs, ts))
    H.write_cfg(root, CFG_NO_MEMORY)
    if cases:
        crs = H.gauss_rs(rng, [(cases, 0.1, 1.0)])
        cts = H.times(cases, 600)
        H.put_cases(root, H.case_recs(rng, crs, cts))
    if runs:
        fill_policy_runs(root, runs)
    return time.perf_counter() - t0


def cold(root, mode="paper", tries=2):
    """Лучшее время холодной сборки из tries попыток (кеши памяти и отчёта сброшены) и последний ответ."""
    best, out = None, None
    for _ in range(tries):
        cache.REPORT.clear()
        memory_vs_base.clear()
        t = time.perf_counter()
        out = report.build(root, mode, H.NOW)
        dt = time.perf_counter() - t
        best = dt if best is None else min(best, dt)
    return best, out


def dumps_size(p):
    return len(json.dumps(p, ensure_ascii=False).encode("utf-8"))


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root()
        cache.REPORT.clear()
        memory_vs_base.clear()

    def tearDown(self):
        memory_vs_base.clear()
        cache.REPORT.clear()
        shutil.rmtree(self.root, ignore_errors=True)


class TestBuildSpeed(Base):
    def test_cold_build_500_5000_20000(self):
        put_install(self.root, 500, 5000, 20000)
        best, p = cold(self.root)
        self.assertTrue(p["ok"])
        self.assertEqual(p["meta"]["sources"]["trades"], 500)
        self.assertEqual(p["meta"]["sources"]["cases"], 5000)
        self.assertEqual(p["blocks"]["memory"]["reason"], "too_many_for_request")      # блок памяти в замер не входит
        self.assertNotEqual(p["blocks"]["vs_policy"]["status"], "error")               # политики действительно прочитаны из базы
        if SLOW:
            self.assertLessEqual(best, COLD_S, "холодная сборка: %.3f с (порог %.1f с)" % (best, COLD_S))
            self.assertLessEqual(p["meta"]["built_ms"], COLD_S * 1000 * 1.5)

    def test_empty_install_is_fast_and_creates_nothing(self):
        before = snapshot(self.root)
        best, p = cold(self.root)
        self.assertEqual(p["verdict"]["state"], "few_data")
        if SLOW:
            self.assertLessEqual(best, 0.3 * max(FACTOR, 1.0))
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(os.path.exists(store.db_path(self.root)), "tb.learn не должен создавать базу меток")

    @unittest.skipUnless(SLOW, "TB_SLOW_TESTS=1")
    def test_cold_build_2000_20000_100000(self):
        put_install(self.root, 2000, 20000, 100000)
        best, p = cold(self.root, tries=1)
        self.assertEqual(p["meta"]["sources"]["trades"], 2000)
        self.assertEqual(p["meta"]["sources"]["cases"], 20000)
        self.assertLessEqual(best, COLD_BIG_S, "холодная сборка (расширенный набор): %.3f с (порог %.1f с)" % (best, COLD_BIG_S))
        self.assertLessEqual(dumps_size(p), report.MAX_BYTES)


class TestCacheAndSize(Base):
    def test_cache_hit_and_size_and_readonly(self):
        put_install(self.root, 500, 300, 20000)
        before = snapshot(self.root)
        first = report.get(self.root, "paper", H.NOW)
        self.assertFalse(first["meta"]["cached"])
        times, last = [], None
        for _ in range(7):
            t = time.perf_counter()
            last = report.get(self.root, "paper", H.NOW)
            times.append(time.perf_counter() - t)
        times.sort()
        med = times[len(times) // 2]
        self.assertTrue(last["meta"]["cached"])
        self.assertEqual(last["asof"], first["asof"])
        if SLOW:
            self.assertLessEqual(med, CACHE_S, "попадание в кеш: %.1f мс (порог %.0f мс)" % (med * 1000, CACHE_S * 1000))
        self.assertLessEqual(dumps_size(first), report.MAX_BYTES)
        self.assertLessEqual(dumps_size(last), report.MAX_BYTES)
        self.assertEqual(snapshot(self.root), before, "запрос не должен менять state/, config/, runtime/")

    def test_cached_copy_is_independent(self):
        put_install(self.root, 40, 0, 0)
        a = report.get(self.root, "paper", H.NOW)
        a["verdict"]["state"] = "mutated"
        a["blocks"].clear()
        b = report.get(self.root, "paper", H.NOW)
        self.assertNotEqual(b["verdict"]["state"], "mutated")
        self.assertTrue(b["blocks"])

    def test_size_limit_on_max_install(self):
        """Много сделок и недель: ряды прореживаются, ответ не больше 64 КБ, последняя точка ряда сохраняется."""
        put_install(self.root, 1500, 0, 0)
        H._jsonl(self.root, "llm_calls.jsonl", H.usage_recs(random.Random(3), days=60))
        _, p = cold(self.root, tries=1)
        self.assertLessEqual(dumps_size(p), report.MAX_BYTES)
        self.assertTrue(p["blocks"]["result"]["series"])

    def test_every_mode_stays_under_limit(self):
        put_install(self.root, 300, 0, 0)
        for mode in ("paper", "testnet", "live", "bogus"):
            p = report.build(self.root, mode, H.NOW)
            self.assertLessEqual(dumps_size(p), report.MAX_BYTES, mode)


class TestSqliteAggregate(Base):
    """Агрегат из базы меток через tb.labels (open_ro, query_only) на большой базе; пишет только тест при подготовке."""

    def _stats(self):
        t = time.perf_counter()
        rows = query.policy_stats(self.root, since=H.NOW - 14 * 86400, by=("policy_id", "policy_ver", "ctx_ver"))
        return rows, time.perf_counter() - t

    def test_policy_stats_100k_through_open_ro(self):
        fill_policy_runs(self.root, 100000)
        self._stats()                                           # прогрев страниц
        rows, dt = self._stats()
        self.assertEqual(sorted(r["policy_id"] for r in rows), ["P5", "P6"])
        self.assertGreater(sum(r["n"] for r in rows), 10000)
        if SLOW:
            self.assertLessEqual(dt, AGG_100K_S, "policy_stats 100 000 строк: %.0f мс (порог %.0f мс)" % (dt * 1000, AGG_100K_S * 1000))

    def test_reader_cannot_write(self):
        fill_policy_runs(self.root, 100)
        conn = store.open_ro(self.root)
        try:
            self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(Exception):
                conn.execute("DELETE FROM policy_runs")
            self.assertEqual(conn.execute("SELECT count(*) FROM policy_runs").fetchone()[0], 100)
        finally:
            conn.close()

    def test_report_leaves_database_untouched(self):
        put_install(self.root, 60, 0, 5000)
        before = snapshot(self.root)
        p = report.build(self.root, "paper", H.NOW)
        self.assertTrue(p["meta"]["sources"]["labels_db"])
        self.assertEqual(snapshot(self.root), before)

    @unittest.skipUnless(SLOW, "TB_SLOW_TESTS=1")
    def test_policy_stats_1m_through_open_ro(self):
        fill_policy_runs(self.root, 1000000)
        self._stats()
        rows, dt = self._stats()
        self.assertGreater(sum(r["n"] for r in rows), 100000)
        self.assertLessEqual(dt, AGG_1M_S, "policy_stats 1 000 000 строк: %.0f мс (порог %.0f мс)" % (dt * 1000, AGG_1M_S * 1000))


if __name__ == "__main__":
    unittest.main()
