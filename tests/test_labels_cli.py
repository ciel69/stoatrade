"""./tb labels (R1, задача 13): status | stats | coverage | show | backfill | rerun | backup | verify на временном корне и фейковом fetch.
Коды выхода, verify с нарушением и без, возобновляемая догрузка, перепрогон только одной политики. Сеть и реальные state/runtime не используются."""
import contextlib
import io
import json
import os
import sqlite3
import time
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.core import expstats
from tb.labels import capture, cli, klines, label, policies, store
from tb.platform import services as S

NOW = H.T0 + 6 * 3600
SINCE = "2026-11-02"


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        self.conn = store.open_rw(self.root)
        self.addCleanup(self.conn.close)
        self.mk = H.Market()
        self.btc = H.FakeBtc(self.mk)
        self.geom = label.load_geom(self.root)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv), root=self.root)
        return code, out.getvalue(), err.getvalue()

    def js(self, *argv, code=0):
        c, out, err = self.run_cli(*argv)
        self.assertEqual(c, code, "%s -> %s | %s" % (argv, out, err))
        return json.loads(out)

    def q(self, sql, *a):
        return self.conn.execute(sql, a).fetchall()

    def seed(self, events):
        """Захват событий и разметка всех созревших. -> ключи по t0."""
        self.cfg = H.capture_all(self.root, self.conn, events)
        self.label_all()
        return [r[0] for r in self.q("SELECT key FROM signals ORDER BY t0, key")]

    def label_all(self, now=NOW):
        pols = policies.load(self.root)
        for row in label.due(self.conn, now, 100):
            self.assertEqual(label.label_one(self.conn, self.root, row, self.cfg, pols, self.geom, self.mk.fetch, self.btc, now), "done")

    def policy_rows(self, pid):
        return dict((k, r) for k, r in self.q("SELECT key, r_net FROM policy_runs WHERE policy_id=?", pid))


def events3():
    return [H.hot_event(0, H.T0, typ="range_5m"), H.hot_event(1, H.T0 + 600, typ="turnover_surge"),
            H.hot_event(2, H.T0 + 1200, typ="xex_gap", tradable=False)]


class TestShow(Base):
    def test_show_prints_signal_features_outcomes_runs(self):
        key = self.seed(events3())[0]
        d = self.js("show", key)
        self.assertEqual(d["signal"]["key"], key)
        self.assertEqual(d["signal"]["status"], "done")
        self.assertIsNotNone(d["features"]["atr5_pct"])
        self.assertEqual(sorted((o["h"], o["side"]) for o in d["outcomes"]), [(15, "long"), (60, "long"), (240, "long")])
        self.assertEqual([r["policy_id"] for r in d["policy_runs"]], ["P2", "P4", "P5", "P6"])
        self.assertIsInstance(d["policy_runs"][2]["plan"], dict)                                  # план разобран из JSON

    def test_show_missing_key_is_1(self):
        self.seed(events3())
        c, out, err = self.run_cli("show", "S-NOPE")
        self.assertEqual(c, 1)
        self.assertIn("нет сигнала", err)

    def test_show_without_db_is_1(self):
        self.conn.close()
        for suf in ("", "-wal", "-shm"):
            try:
                os.remove(store.db_path(self.root) + suf)
            except OSError:
                pass
        self.assertEqual(self.run_cli("show", "S-X")[0], 1)
        self.assertEqual(self.run_cli("verify")[0], 1)
        self.assertEqual(self.run_cli("backup")[0], 1)

    def test_group_registered_in_dispatcher_and_help(self):
        from tb import __main__ as M
        self.assertEqual(M.V2["labels"], "tb.labels.cli")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(M.main(["help"]), 0)
        self.assertIn("labels <status", buf.getvalue())
        self.assertIn("backfill --since", buf.getvalue())

    def test_usage_errors_are_2(self):
        self.assertEqual(self.run_cli()[0], 2)
        self.assertEqual(self.run_cli("nope")[0], 2)
        self.assertEqual(self.run_cli("show")[0], 2)
        self.assertEqual(self.run_cli("backfill")[0], 2)
        self.assertEqual(self.run_cli("backfill", "--since", "вчера")[0], 2)


class TestVerify(Base):
    def test_clean_base_is_zero_violations(self):
        self.seed(events3())                                                                       # третий сигнал tradable=false: строк политик нет и это не нарушение
        d = self.js("verify")
        self.assertEqual((d["ok"], d["violations"], d["integrity"], d["done"]), (True, 0, "ok", 3))

    def test_missing_outcome_is_violation(self):
        keys = self.seed(events3())
        self.conn.execute("DELETE FROM outcomes WHERE key=? AND h=60", (keys[0],))
        d = self.js("verify", code=1)
        self.assertEqual((d["ok"], d["violations"]), (False, 1))
        self.assertIn("h=60", d["examples"][0]["what"])

    def test_missing_policy_row_is_violation(self):
        keys = self.seed(events3())
        self.conn.execute("DELETE FROM policy_runs WHERE key=? AND policy_id='P4'", (keys[0],))
        d = self.js("verify", code=1)
        self.assertEqual(d["violations"], 1)
        self.assertIn("P4", d["examples"][0]["what"])

    def test_new_policy_version_requires_rows_of_current_version(self):
        self.seed(events3())
        p = os.path.join(self.root, "config", "shadow_policies.json")
        with open(p, encoding="utf-8") as f:
            reg = json.load(f)
        for x in reg["policies"]:
            if x["id"] == "P5":
                x["ver"] = 2
        with open(p, "w", encoding="utf-8") as f:
            json.dump(reg, f)
        d = self.js("verify", code=1)
        self.assertEqual(d["violations"], 2)                                                       # P5 v2 без строк у двух торгуемых сигналов (tradable=false политик не ждёт)
        self.assertIn("P5 v2", d["examples"][0]["what"])


class TestStatsCoverageBackup(Base):
    def test_stats_by_policy_and_type(self):
        self.seed(events3())
        d = self.js("stats", "--by", "policy", "--since", SINCE)
        self.assertEqual([(r["policy_id"], r["policy_ver"]) for r in d["rows"]], [("P2", 1), ("P4", 1), ("P5", 1), ("P6", 1)])
        for r in d["rows"]:
            self.assertEqual(r["k"], 4)
            self.assertAlmostEqual(r["alpha_adj"], expstats.ALPHA / 4)                             # Бонферрони по числу включённых политик
            self.assertIn("exit_reasons", r)
        t = self.js("stats", "--by", "type", "--horizon", "15")
        self.assertEqual(sorted(r["type"] for r in t["rows"]), ["range_5m", "turnover_surge", "xex_gap"])
        c, out, _ = self.run_cli("stats", "--text")
        self.assertEqual(c, 0)
        self.assertIn("P2 v1", out)

    def test_stats_without_db_is_empty_zero(self):
        self.conn.close()
        os.remove(store.db_path(self.root))
        d = self.js("stats")
        self.assertEqual(d["rows"], [])

    def test_coverage_threshold(self):
        keys = self.seed(events3())
        full = dict((f, 1.0) for f in capture.load_cfg(self.root)["coverage_fields"])
        self.conn.execute("UPDATE signals SET feat=?", (json.dumps(full),))
        d = self.js("coverage")
        self.assertEqual((d["done"], d["any_missing_share"], d["ok"], d["limit"]), (3, 0.0, True, 0.10))
        self.conn.execute("UPDATE signals SET feat=? WHERE key=?", (json.dumps(dict(full, atr5_pct=None)), keys[0]))
        d = self.js("coverage", code=1)                                                            # 1 из 3 > 10%
        self.assertEqual(d["ok"], False)
        self.assertAlmostEqual(d["any_missing_share"], 1 / 3)
        self.assertAlmostEqual(d["fields"]["atr5_pct"], 1 / 3)
        self.assertEqual(self.run_cli("coverage", "--since", "2030-01-01")[0], 0)                  # нет done за период -> порог не нарушен

    def test_backup_copies_live_base(self):
        self.seed(events3())
        d = self.js("backup")
        self.assertTrue(os.path.exists(d["path"]))
        self.assertIn(os.path.join("state", "backups", "knowledge-"), d["path"])
        c = sqlite3.connect(d["path"])
        try:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM signals").fetchone()[0], 3)
        finally:
            c.close()


class TestStatus(Base):
    def test_status_without_service(self):
        d = self.js("status")
        self.assertFalse(d["service"]["alive"])
        self.assertIn("не запущен", d["note"])
        self.assertIsNone(d["backfill"])
        self.assertEqual(d["rerun_pending"], 0)

    def test_status_with_heartbeat_and_queue(self):
        self.seed(events3())
        S.write_hb("labels", {"captured_24h": 3, "pending": 0, "due": 0, "warnings": []}, root=self.root, now=time.time())
        d = self.js("status")
        self.assertTrue(d["service"]["alive"])
        self.assertEqual(d["service"]["captured_24h"], 3)
        self.assertNotIn("note", d)
        self.assertEqual(d["queue"]["pending"], 0)
        c, out, _ = self.run_cli("status", "--text")
        self.assertEqual(c, 0)
        self.assertIn("работает", out)


class TestBackfill(Base):
    def write(self, events, mode="wb"):
        H.write_events(self.root, events, mode=mode)

    def history(self):
        ev = [H.hot_event(0, H.T0 - 3 * 86400, typ="range_5m")]                                     # до --since
        ev += [H.hot_event(i, H.T0 + i * 600, typ=t) for i, t in ((1, "range_5m"), (2, "turnover_surge"), (3, "hot_enter"), (4, "oi_spike"))]
        ev += [H.hot_event(5, H.T0 + 3000, typ="не_захватываемый_тип")]                              # вне capture_types
        return ev

    def count(self):
        return self.q("SELECT COUNT(*) FROM signals")[0][0]

    def test_backfill_resumable_without_duplicates(self):
        self.write(self.history())
        with open(os.path.join(self.root, "runtime", "hot_events.jsonl"), "ab") as f:
            f.write(b"{bad json\n")
        with mock.patch.object(cli, "CHUNK", 700):
            r1 = self.js("backfill", "--since", SINCE, "--max-mb", "0.000001")                      # один кусок и остановка
            self.assertFalse(r1["done"])
            self.assertFalse(r1["resumed"])
            n1 = self.count()
            self.assertGreater(n1, 0)
            self.assertLess(n1, 4)
            st = self.js("status")["backfill"]                                                      # прогресс виден в status
            self.assertTrue(0 < st["progress"] < 1)
            self.assertFalse(st["done"])
            r2 = self.js("backfill", "--since", SINCE)
            self.assertTrue(r2["resumed"])
            self.assertTrue(r2["done"])
            self.assertEqual(r2["new"], 4)                                                          # накопленно: 4 захватываемых события с даты
            self.assertEqual(self.count(), 4)
            self.assertEqual(r2["bad_lines"], 1)
            self.assertEqual(self.js("status")["backfill"]["progress"], 1.0)
            r3 = self.js("backfill", "--since", SINCE)                                               # повтор без изменений
            self.assertEqual((r3["new"], r3["done"], self.count()), (4, True, 4))
            self.write([H.hot_event(9, H.T0 + 5000, typ="range_5m")], mode="ab")                     # хвост дописан
            r4 = self.js("backfill", "--since", SINCE)
            self.assertEqual((r4["new"], self.count()), (5, 5))
            r5 = self.js("backfill", "--since", SINCE, "--restart")                                  # чтение с нуля: дублей нет
            self.assertEqual((r5["resumed"], self.count()), (False, 5))
        rows = self.q("SELECT status, mature_ts, t0 FROM signals")
        self.assertTrue(all(r[0] == "pending" and r[1] > r[2] for r in rows))
        self.assertEqual(self.q("SELECT COUNT(*) FROM signals WHERE t0 < ?", H.T0)[0][0], 0)       # события до даты не попали

    def test_backfill_does_not_duplicate_service_capture(self):
        self.write(self.history())
        H.capture_all(self.root, self.conn, [], now=H.T0 + 10)                                       # сервис уже захватил всё
        n = self.count()
        r = self.js("backfill", "--since", SINCE)
        self.assertEqual((r["new"], self.count()), (0, n))

    def test_backfill_context_fields_stay_null(self):
        self.write([H.hot_event(0, H.T0)])
        with open(os.path.join(self.root, "runtime", "context.json"), "w", encoding="utf-8") as f:
            json.dump({"ts_epoch": time.time(), "risk_off": True, "sources": {"fear_greed": {"value": 55}, "dvol_ETH": {"value": 61.5}}}, f)
        self.js("backfill", "--since", SINCE)
        ev = json.loads(self.q("SELECT ev FROM signals")[0][0])
        self.assertNotIn("ctx_snap", ev)
        self.cfg = capture.load_cfg(self.root)
        self.label_all()
        feat = json.loads(self.q("SELECT feat FROM signals")[0][0])
        self.assertEqual((feat["fng"], feat["dvol"], feat["risk_off"]), (None, None, None))

    def test_backfill_without_hot_events(self):
        d = self.js("backfill", "--since", SINCE)
        self.assertTrue(d["done"])
        self.assertEqual(self.count(), 0)


class TestRerun(Base):
    def setUp(self):
        Base.setUp(self)
        self.keys = self.seed([H.hot_event(0, H.T0, typ="range_5m"), H.hot_event(1, H.T0 + 600, typ="range_5m")])
        self.feat0 = dict(self.q("SELECT key, feat FROM signals"))
        self.conn.execute("UPDATE policy_runs SET r_net=999")                                       # маркер: что перепрогон не пересчитал, останется 999
        self.conn.execute("UPDATE outcomes SET ret_pct=777")

    def sig(self):
        return dict((k, (s, c)) for k, s, c in self.q("SELECT key, status, code FROM signals"))

    def test_only_selected_policy_rows_are_rewritten(self):
        d = self.js("rerun", "--policy", "P2", "--since", SINCE)
        self.assertEqual((d["queued"], d["not_applicable"]), (2, 0))
        self.assertEqual(set(self.sig().values()), {("pending", "RERUN:P2")})
        calls = len(self.mk.calls)
        self.label_all()
        self.assertGreater(len(self.mk.calls), calls)                                              # свечи загружены заново, в базе их нет
        self.assertEqual(set(self.sig().values()), {("done", None)})
        self.assertTrue(all(v != 999 for v in self.policy_rows("P2").values()))                    # P2 пересчитана
        for pid in ("P4", "P5", "P6"):
            self.assertEqual(set(self.policy_rows(pid).values()), {999})                           # остальные политики не тронуты
        self.assertEqual(set(r[0] for r in self.q("SELECT ret_pct FROM outcomes")), {777})         # исходы не тронуты
        self.assertEqual(dict(self.q("SELECT key, feat FROM signals")), self.feat0)                # признаки не тронуты
        self.assertEqual(self.q("SELECT COUNT(*) FROM policy_runs")[0][0], 8)

    def test_two_policies_merge_marker(self):
        self.js("rerun", "--policy", "P2", "--since", SINCE)
        self.js("rerun", "--policy", "P4", "--since", SINCE)
        self.assertEqual(set(self.sig().values()), {("pending", "RERUN:P2,P4")})
        self.label_all()
        self.assertTrue(all(v != 999 for p in ("P2", "P4") for v in self.policy_rows(p).values()))
        self.assertEqual(set(self.policy_rows("P5").values()), {999})

    def test_since_filters_and_plain_pending_untouched(self):
        d = self.js("rerun", "--policy", "P2", "--since", "2026-11-03")
        self.assertEqual(d["queued"], 0)
        self.conn.execute("UPDATE signals SET status='pending', code=NULL WHERE key=?", (self.keys[0],))   # ещё не размечен: разметит сервис
        d = self.js("rerun", "--policy", "P2", "--since", SINCE)
        self.assertEqual((d["queued"], d["pending_plain"]), (1, 1))
        self.assertEqual(self.sig()[self.keys[0]], ("pending", None))

    def test_not_applicable_signals_skipped(self):
        self.conn.execute("UPDATE signals SET tradable=0 WHERE key=?", (self.keys[0],))
        d = self.js("rerun", "--policy", "P2", "--since", SINCE)
        self.assertEqual((d["queued"], d["not_applicable"]), (1, 1))

    def test_unknown_or_disabled_policy_is_2(self):
        c, out, err = self.run_cli("rerun", "--policy", "P99", "--since", SINCE)
        self.assertEqual(c, 2)
        self.assertIn("P99", err)
        p = os.path.join(self.root, "config", "shadow_policies.json")
        with open(p, encoding="utf-8") as f:
            reg = json.load(f)
        for x in reg["policies"]:
            if x["id"] == "P2":
                x["enabled"] = False
        with open(p, "w", encoding="utf-8") as f:
            json.dump(reg, f)
        self.assertEqual(self.run_cli("rerun", "--policy", "P2", "--since", SINCE)[0], 2)
        self.assertEqual(set(v[0] for v in self.sig().values()), {"done"})                         # ничего не поставлено в очередь

    def test_failed_rerun_keeps_old_rows_and_returns_to_done(self):
        self.js("rerun", "--policy", "P2", "--since", SINCE)
        self.assertEqual(self.js("status")["rerun_pending"], 2)
        self.mk.fail = klines.FetchError("нет сети")
        pols = policies.load(self.root)
        now = NOW
        for _ in range(int(self.cfg.get("max_attempts", 5))):
            for row in label.due(self.conn, now, 10):
                label.label_one(self.conn, self.root, row, self.cfg, pols, self.geom, self.mk.fetch, self.btc, now)
            now += 7200
        st = self.sig()
        self.assertEqual(set(s for s, _ in st.values()), {"done"})
        self.assertTrue(all(c == "RERUN_FAILED:FETCH" for _, c in st.values()))
        self.assertEqual(set(self.policy_rows("P2").values()), {999})                              # прежние строки целы
        self.assertEqual(self.js("status")["rerun_pending"], 0)


if __name__ == "__main__":
    unittest.main()
