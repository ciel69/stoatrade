"""R1 задача 4 (Requirement 10): кеш cases.load с дочитыванием хвоста, свойство 10, скорость на 20 000 случаев, пропуск migrate в tick."""
import copy
import json
import os
import random
import time
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
from _memory_helpers import TmpRoot, mk
from tb.memory import cases, chain, migrate, tick


def full_load(root):
    """Эталон: полное перечитывание без кеша."""
    rows, _ = chain.read(cases.cases_path(root))
    return sorted(cases.fold(rows).values(), key=lambda c: (c.get("ts") or "", c["case_id"]))


class TestCacheProperty(TmpRoot):
    def test_property_10_random_new_update_sequences(self):
        for seed in range(8):
            rnd = random.Random(seed)
            root = self.root + "_s%d" % seed
            os.makedirs(os.path.join(root, "state"))
            ids = []
            try:
                for step in range(60):
                    if not ids or rnd.random() < 0.5:
                        c = mk(len(ids), sym="S%d" % rnd.randint(0, 5), r=round(rnd.uniform(-1, 2), 2))
                        cases.append_new(c, root=root)
                        ids.append(c["case_id"])
                    else:
                        cid = rnd.choice(ids)
                        patch = rnd.choice([{"result": {"status": "closed", "r": round(rnd.uniform(-1, 2), 2)}},
                                            {"whatif": {"h_min": 15, "dir_pct": 0.5}}, {"refs": {"idea": "I%d" % rnd.randint(1, 9)}},
                                            {"flags": ["backfill"]}])
                        cases.append_update(cid, patch, root=root)
                    if rnd.random() < 0.4:
                        got = cases.load(root)
                        if rnd.random() < 0.5:                 # изменение результата не должно портить кеш
                            for c in cases.load(root, copy=True):
                                c["result"] = {"status": "na"}
                                c["tags"]["err"].append("x")
                            got = copy.deepcopy(got)
                    self.assertEqual(cases.load(root), full_load(root), "seed %d step %d" % (seed, step))
                for cid in ids:
                    self.assertEqual(cases.get(cid, root), next(c for c in full_load(root) if c["case_id"] == cid))
            finally:
                import shutil
                shutil.rmtree(root, ignore_errors=True)

    def test_mutating_copy_does_not_corrupt_cache(self):
        self.put([mk(i) for i in range(3)])
        before = copy.deepcopy(cases.load(self.root))
        for c in cases.load(self.root, copy=True):
            c["situation"]["sym"] = "ZZZ"
            c["result"]["r"] = 99
        g = cases.get(before[0]["case_id"], self.root)
        g["situation"]["sym"] = "QQQ"
        g["tags"]["err"].append("no-confirm")
        self.assertEqual(cases.load(self.root), before)

    def test_external_rewrite_resets_cache(self):
        self.put([mk(i) for i in range(5)])
        self.assertEqual(len(cases.load(self.root)), 5)
        with open(cases.cases_path(self.root), "rb") as f:
            lines = f.read().split(b"\n")
        with open(cases.cases_path(self.root), "wb") as f:        # файл стал короче
            f.write(b"\n".join(lines[:2]) + b"\n")
        self.assertEqual(len(cases.load(self.root)), 2)
        other = self.root + "_o"
        os.makedirs(os.path.join(other, "state"))
        try:
            for i in range(10, 13):
                cases.append_new(mk(i), root=other)
            with open(cases.cases_path(other), "rb") as f:
                data = f.read()
            with open(cases.cases_path(self.root), "wb") as f:    # переписан другим содержимым, размер больше
                f.write(data)
            self.assertEqual(cases.load(self.root), full_load(self.root))
            self.assertEqual(len(cases.load(self.root)), 3)
        finally:
            import shutil
            shutil.rmtree(other, ignore_errors=True)

    def test_partial_last_line_equals_full_read(self):
        self.put([mk(i) for i in range(3)])
        cases.load(self.root)
        with open(cases.cases_path(self.root), "ab") as f:
            f.write(b'{"seq": 9, "h": "abc", "kind": "new", "case_id"')        # оборванный хвост
        self.assertEqual(cases.load(self.root), full_load(self.root))
        self.assertEqual(cases.read_raw(self.root)[1], chain.read(cases.cases_path(self.root))[1])
        cases.append_new(mk(7), root=self.root)
        self.assertEqual(cases.load(self.root), full_load(self.root))
        self.assertEqual(len(cases.load(self.root)), 4)

    def test_exists_uses_cache_and_rejects_duplicate(self):
        c = mk(1)
        cases.append_new(c, root=self.root)
        with mock.patch.object(chain, "read", side_effect=AssertionError("полное чтение")):
            with self.assertRaises(cases.CaseError):
                cases.append_new(c, root=self.root)
            cases.append_update(c["case_id"], {"flags": ["backfill"]}, root=self.root)
        self.assertEqual(cases.get(c["case_id"], self.root)["rev"], 2)


class TestCacheSpeed(TmpRoot):
    def test_20000_cases_speed(self):
        path = cases.cases_path(self.root)
        prev, seq = chain.GENESIS, 0
        with open(path, "w", encoding="utf-8") as f:
            for i in range(20000):
                c = mk(i % 500, sym="SYM%d" % (i % 37), ts="2026-10-%02dT%02d:%02d:00Z" % (1 + i % 27, i // 1000 % 24, i % 60))
                c["case_id"] = "C-20261001-SYM%d-%06d" % (i % 37, i)
                c.update({"schema": cases.SCHEMA, "kind": "new", "rev": 1, "seq": seq + 1, "prev": prev})
                c["h"] = chain.make_hash(prev, c)
                prev, seq = c["h"], seq + 1
                f.write(chain.canon(c) + "\n")
        t = time.perf_counter()
        n = len(cases.load(self.root))
        first = time.perf_counter() - t
        self.assertEqual(n, 20000)
        t = time.perf_counter()
        for _ in range(5):
            cases.load(self.root)
        again = (time.perf_counter() - t) / 5
        self.assertLessEqual(first, 3.0)
        self.assertLessEqual(again, 0.05)
        t = time.perf_counter()
        cases.append_new(mk(900, sym="NEW"), root=self.root)          # одна запись: дочитывается только хвост
        cases.load(self.root)
        self.assertLessEqual(time.perf_counter() - t, 0.15 * float(os.environ.get("TB_PERF_FACTOR") or 1.0))
        self.assertEqual(len(cases.load(self.root)), 20001)


class TestTickSkipMigrate(TmpRoot):
    def setUp(self):
        TmpRoot.setUp(self)
        self.copy_fixtures()

    def test_unchanged_sources_skip_migrate(self):
        r1 = tick.sync_now(self.root, 1793200000.0)
        self.assertGreater(r1["written"], 0)
        with mock.patch.object(migrate, "migrate", side_effect=AssertionError("migrate вызван")):
            r2 = tick.sync_now(self.root, 1793200100.0)
        self.assertEqual((r2["written"], r2.get("skipped")), (0, True))
        self.assertTrue(os.path.exists(tick.state_file(self.root)))

    def test_changed_source_runs_migrate(self):
        tick.sync_now(self.root, 1793200000.0)
        tid = "BB-P-ETH-1028-2000"
        rows = [{"ev": "open", "id": tid, "ts_open": "2026-10-28T10:00:00Z", "sym": "ETH", "side": "long", "entry": 100.0, "sl": 99.0, "tp": 102.0, "mode": "paper",
                 "tags": ["setup:level_reclaim"]},
                {"ev": "close", "id": tid, "ts_close": "2026-10-28T10:30:00Z", "exit": 102.0, "reason": "tp", "pnl_usdt": 0.1, "r": 0.8}]
        with open(os.path.join(self.root, "state", "trades.jsonl"), "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        r = tick.sync_now(self.root, 1793200200.0)
        self.assertFalse(r.get("skipped"))
        self.assertEqual(r["new"], {"trade": 1})

    def test_failed_run_is_not_remembered(self):
        with mock.patch.object(migrate, "migrate", side_effect=OSError("сбой")):
            with self.assertRaises(OSError):
                tick.sync_now(self.root, 1793200000.0)
        self.assertFalse(os.path.exists(tick.state_file(self.root)))
        self.assertGreater(tick.sync_now(self.root, 1793200100.0)["written"], 0)


if __name__ == "__main__":
    unittest.main()
