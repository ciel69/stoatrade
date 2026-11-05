"""tb.labels.paths (R4, задача 8): кодек пути (свойство 11), запись run_paths в транзакции разметки, догрузка окна,
повтор после сбоя REST, paths-backfill и ротация. Сеть не используется: поддельный клиент свечей."""
import contextlib
import io
import json
import os
import sqlite3
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.labels import capture, cli, klines, label, paths, policies, store


def exits_cfg(root, **over):
    """config/exits.json репозитория с правкой блока paths (например, hold_mult/reserve_min)."""
    with open(os.path.join(H.PROJECT, "config", "exits.json"), encoding="utf-8") as f:
        d = json.load(f)
    d["paths"].update(over)
    with open(os.path.join(root, "config", "exits.json"), "w", encoding="utf-8") as f:
        json.dump(d, f)
    return d


class Boom(object):
    """Соединение-обёртка: бросает на вставке в run_paths (сбой посреди транзакции разметки)."""

    def __init__(self, conn):
        self.c = conn

    def execute(self, sql, *a):
        if "INTO run_paths" in sql:
            raise sqlite3.OperationalError("boom")
        return self.c.execute(sql, *a)

    def __getattr__(self, name):
        return getattr(self.c, name)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        exits_cfg(self.root)
        self.conn = store.open_rw(self.root)
        self.addCleanup(self.conn.close)
        self.mk = H.Market(rows=H.walk(H.T0 - 210 * 3600, H.T0 + 12 * 3600))
        self.btc = H.FakeBtc(self.mk)
        self.pols = policies.load(self.root)
        self.geom = label.load_geom(self.root)

    def seed(self, *events):
        self.cfg = H.capture_all(self.root, self.conn, list(events) or [H.hot_event(range_5m_pct=0.2)])
        return H.sig_row(self.conn)

    def run_label(self, row, now=None):
        now = row["mature_ts"] if now is None else now
        return label.label_one(self.conn, self.root, row, self.cfg, self.pols, self.geom, self.mk.fetch, self.btc, now)

    def q(self, sql, *a):
        return self.conn.execute(sql, a).fetchall()

    def paths_cfg(self):
        return paths.load_cfg(self.root)

    def runs(self):
        cols = ("key", "policy_id", "policy_ver", "entry_ts", "entry_px", "stop", "tick", "max_hold_min", "plan", "rows", "n_rows",
                "end_ts", "state", "flags", "complete_ts")
        return [dict(zip(cols, r)) for r in self.q("SELECT %s FROM run_paths ORDER BY policy_id" % ",".join(cols))]


# ---------------------------------------------------------------- кодек

class TestCodec(unittest.TestCase):
    def test_roundtrip_within_tick(self):
        rows = H.walk(H.BASE, H.BASE + 390 * 60, seed=7)
        tick, entry = 0.01, 100.0
        blob = paths.encode(rows, tick, entry)
        back = paths.decode(blob, tick, entry)
        self.assertEqual([r[0] for r in back], [r[0] for r in rows])
        for src, dst in zip(rows, back):
            for k in (1, 2, 3, 4):
                self.assertLessEqual(abs(src[k] - dst[k]), tick + 1e-9)
        self.assertEqual(len(back[0]), 5)                     # объём не хранится

    def test_average_size_under_3kb_for_390_bars(self):
        sizes = []
        for seed in range(20):
            rows = H.walk(H.BASE, H.BASE + 390 * 60, seed=seed)
            sizes.append(len(paths.encode(rows, 0.00001, 100.0)))
        self.assertLessEqual(sum(sizes) / len(sizes), 3 * 1024)

    def test_empty_and_broken(self):
        self.assertEqual(paths.decode(paths.encode([], 0.01, 100.0), 0.01, 100.0), [])
        with self.assertRaises(ValueError):
            paths.encode([(0, 1, 1, 1, 1)], 0, 100.0)
        with self.assertRaises(ValueError):
            paths.decode(b"not-zlib", 0.01, 100.0)

    def test_gap_flag_and_merge(self):
        rows = H.walk(H.BASE, H.BASE + 15 * 60)
        holes = [r for r in rows if not (H.BASE + 3 * 60 <= r[0] < H.BASE + 10 * 60)]
        self.assertTrue(paths.is_gap(holes))
        merged = paths.merge_rows(holes, rows[:4])
        self.assertEqual(len(merged), len(holes) + 1)
        self.assertEqual([r[0] for r in merged], sorted(r[0] for r in merged))
        self.assertIn(H.BASE + 3 * 60, [r[0] for r in merged])


# ---------------------------------------------------------------- запись в разметке

class TestRunPathsInLabel(Base):
    def test_paths_written_in_same_transaction_as_runs(self):
        row = self.seed()
        self.assertEqual(self.run_label(row), "done")
        runs = self.runs()
        want = self.q("SELECT COUNT(*) FROM policy_runs WHERE policy_id IN ('P2','P4') AND filled=1")[0][0]
        self.assertEqual(len(runs), want)
        self.assertGreater(len(runs), 0)
        for r in runs:
            self.assertIn(r["policy_id"], ("P2", "P4"))
            self.assertEqual(r["state"], paths.schema.PATH_PARTIAL)
            self.assertIsNone(r["complete_ts"])
            self.assertEqual(r["key"], row["key"])
            decoded = paths.decode(r["rows"], r["tick"], r["entry_px"])
            self.assertEqual(len(decoded), r["n_rows"])
            self.assertEqual(decoded[0][0], r["entry_ts"])
            self.assertEqual(r["end_ts"], decoded[-1][0] + 60)
            for bar in decoded:
                src = self.mk.row(bar[0])
                for k in (1, 2, 3, 4):
                    self.assertLessEqual(abs(src[k] - bar[k]), r["tick"] + 1e-9)
            side = self.q("SELECT side FROM policy_runs WHERE key=? AND policy_id=? AND policy_ver=?",
                          r["key"], r["policy_id"], r["policy_ver"])[0][0]
            plan = json.loads(r["plan"])
            self.assertEqual(plan["side"], side)
            self.assertEqual(plan["entry_px"], r["entry_px"])

    def test_failed_path_write_rolls_back_labeling(self):
        row = self.seed()
        boom = Boom(self.conn)
        with self.assertRaises(sqlite3.OperationalError):
            label.label_one(boom, self.root, row, self.cfg, self.pols, self.geom, self.mk.fetch, self.btc, row["mature_ts"])
        st = self.q("SELECT status, code, labeled_ts FROM signals WHERE key=?", row["key"])[0]
        self.assertEqual(st[0], "pending")
        self.assertIsNone(st[2])
        for t in ("outcomes", "policy_runs", "run_paths"):
            self.assertEqual(self.q("SELECT COUNT(*) FROM %s" % t)[0][0], 0, t)

    def test_unfilled_run_has_no_path(self):
        res = {"filled": False, "entry_ts": None, "entry_px": None}
        run = {"key": "k", "policy_id": "P2", "policy_ver": 1, "t0": H.T0, "sym": "DOGEUSDT", "type": "range_5m"}
        self.assertIsNone(paths.run_path_row(run, {"side": "long", "tick": 0.01, "max_hold_min": 60}, res, [], {}, 1.5, 30))


# ---------------------------------------------------------------- догрузка

class TestExtend(Base):
    def first(self):
        row = self.seed()
        self.assertEqual(self.run_label(row), "done")
        return self.runs()[0]

    def test_extend_loads_window_and_completes(self):
        r = self.first()
        lim = paths.window_end(r["entry_ts"], r["max_hold_min"], 1.5, 30)
        now = lim + 10 * 60 + 1
        rep = paths.extend_step(self.conn, self.root, self.paths_cfg(), self.mk.fetch, now)
        self.assertGreaterEqual(rep["extended"], 1)
        got = [x for x in self.runs() if x["policy_id"] == r["policy_id"] and x["policy_ver"] == r["policy_ver"]][0]
        self.assertEqual(got["state"], paths.schema.PATH_COMPLETE)
        self.assertEqual(got["complete_ts"], now)
        self.assertGreaterEqual(got["end_ts"], lim)
        self.assertGreaterEqual(got["n_rows"], r["n_rows"])
        self.assertEqual(self.mk.calls[-1], ("DOGEUSDT", "1", got["entry_ts"], lim))

    def test_rest_failure_keeps_partial_then_retry_completes(self):
        r = self.first()
        lim = paths.window_end(r["entry_ts"], r["max_hold_min"], 1.5, 30)
        now = lim + 10 * 60 + 1
        self.mk.fail = klines.FetchError("network")
        rep = paths.extend_step(self.conn, self.root, self.paths_cfg(), self.mk.fetch, now)
        self.assertGreaterEqual(rep["errors"], 1)
        self.assertEqual(self.runs()[0]["state"], paths.schema.PATH_PARTIAL)
        self.mk.fail = None
        rep = paths.extend_step(self.conn, self.root, self.paths_cfg(), self.mk.fetch, now + 60)
        self.assertGreaterEqual(rep["extended"], 1)
        self.assertEqual(self.runs()[0]["state"], paths.schema.PATH_COMPLETE)

    def test_rate_limited_is_raised(self):
        r = self.first()
        lim = paths.window_end(r["entry_ts"], r["max_hold_min"], 1.5, 30)
        self.mk.fail = klines.RateLimited("HTTP 429")
        with self.assertRaises(klines.RateLimited):
            paths.extend_step(self.conn, self.root, self.paths_cfg(), self.mk.fetch, lim + 601)

    def test_not_due_keeps_partial(self):
        r = self.first()
        lim = paths.window_end(r["entry_ts"], r["max_hold_min"], 1.5, 30)
        rep = paths.extend_step(self.conn, self.root, self.paths_cfg(), self.mk.fetch, lim)
        self.assertEqual(rep["due"], 0)
        self.assertEqual(self.runs()[0]["state"], paths.schema.PATH_PARTIAL)

    def test_rotation_deletes_old_paths(self):
        self.first()
        cols = paths.RUN_PATH_COLS
        old = dict(zip(cols, self.q("SELECT %s FROM run_paths LIMIT 1" % ",".join(cols))[0]))
        old["key"], old["t0"] = "old-key", H.T0 - 200 * 86400
        with store.tx(self.conn):
            self.conn.execute("INSERT INTO run_paths(%s) VALUES(%s)" % (",".join(cols), ",".join("?" * len(cols))), [old.get(c) for c in cols])
        rep = paths.rotate(self.conn, self.paths_cfg(), H.T0 + 1)
        self.assertGreaterEqual(rep["run"], 1)
        self.assertEqual(self.q("SELECT COUNT(*) FROM run_paths WHERE t0<?", H.T0 - 100 * 86400)[0][0], 0)


# ---------------------------------------------------------------- paths-backfill

class TestBackfill(Base):
    def setUp(self):
        Base.setUp(self)
        self.rows = ["P2", "P4"]
        self.conn.execute("DELETE FROM run_paths")

    def seed_done(self):
        row = self.seed()
        self.assertEqual(self.run_label(row), "done")
        self.conn.execute("DELETE FROM run_paths")

    def test_backfill_adds_paths_without_touching_runs(self):
        self.seed_done()
        before = {
            "policy_runs": self.q("SELECT * FROM policy_runs ORDER BY key, policy_id"),
            "outcomes": self.q("SELECT * FROM outcomes ORDER BY key, h, side"),
            "signals": self.q("SELECT key, feat, labeled_ts FROM signals ORDER BY key"),
        }
        now = H.T0 + 3 * 86400
        rep = paths.backfill(self.conn, self.root, 7, self.mk.fetch, now)
        self.assertGreater(rep["written"], 0)
        self.assertEqual(rep["skipped"], 0)
        self.assertEqual(self.q("SELECT COUNT(*) FROM run_paths WHERE state='complete'")[0][0], rep["written"])
        self.assertEqual(self.q("SELECT * FROM policy_runs ORDER BY 1, 2"), before["policy_runs"])
        self.assertEqual(self.q("SELECT * FROM outcomes ORDER BY 1, 2"), before["outcomes"])
        self.assertEqual(self.q("SELECT key, feat, labeled_ts FROM signals ORDER BY key"), before["signals"])
        rep2 = paths.backfill(self.conn, self.root, 7, self.mk.fetch, now)
        self.assertEqual(rep2["written"], 0)
        self.assertEqual(rep2["skipped"], rep["written"])

    def test_backfill_skips_unfilled_and_old(self):
        self.seed_done()
        n = paths.backfill(self.conn, self.root, 0, self.mk.fetch, H.T0 + 3 * 86400)
        self.assertEqual(n["written"], 0)
        self.assertEqual(n["scanned"], 0)

    def test_cli_paths_backfill(self):
        self.seed_done()
        class _L(object):
            requests = 0
            rate_limited = 0

            def __init__(self, *a, **k):
                pass

            def wait(self):
                pass

            def block(self, *a, **k):
                pass
        fake = lambda sym, interval, s, e, limiter=None, now_s=None, http=None: self.mk.fetch(sym, interval, s, e)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(klines, "Limiter", _L), mock.patch.object(klines, "fetch", fake), \
                mock.patch.object(cli.time, "time", return_value=H.T0 + 3 * 86400), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["paths-backfill", "--days", "7"], root=self.root), 0)
        rep = json.loads(out.getvalue())
        self.assertGreater(rep["written"], 0)
        self.assertEqual(self.q("SELECT COUNT(*) FROM run_paths")[0][0], rep["written"])

    def test_cli_usage_and_bad_days(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["paths-backfill", "--days", "0"], root=self.root), 2)
            self.assertEqual(cli.main(["paths-backfill"], root=self.root), 2)


if __name__ == "__main__":
    unittest.main()
