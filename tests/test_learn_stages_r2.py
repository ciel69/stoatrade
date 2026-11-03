"""R2.6 (задача 18): блоки «Уроки» и «Версии мозга до/после» (tb.learn.stages). Свойство 17 «страница не врёт» (числа = пересчёт прямыми
запросами), n < 30, границы вердикта до/после, нет таблиц цикла, восстановление базы, режимы не смешиваются, только чтение, общий вердикт."""
import builtins
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
from tb.knowledge import markers as KM, store2
from tb.labels import store as LST
from tb.learn import conf, report, stages, stats
from tb.memory import lessons as LS

NOW = 1793750400                      # 2026-11-03T00:00:00Z (вторник)
DAY = 86400


def iso(t):
    return stats.datetime.datetime.fromtimestamp(t, stats.datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbst2_")
        self.addCleanup(shutil.rmtree, self.root, True)
        for d in ("state", "config", "runtime", os.path.join("state", "brain")):
            os.makedirs(os.path.join(self.root, d), exist_ok=True)
        self.conn = None

    def tearDown(self):
        if self.conn is not None:
            self.conn.close()

    # ---------------------------------------------------------------- база знаний
    def db(self):
        self.conn = LST.open_rw(self.root)
        store2.migrate_v2a(self.conn, self.root, explicit=True, backup=False, now=NOW)
        return self.conn

    def version(self, ver, rule_ids=(), ts=NOW, reason="PROMOTE"):
        store2.append_row(self.conn, "rulebook_versions", {"ver": ver, "parent": None, "reason_code": reason, "trial_id": None,
                                                           "rule_ids": list(rule_ids), "numbers": None, "ts": iso(ts)})

    def cand(self, i, env="mainnet", lesson_id=None):
        store2.insert_rows(self.conn, "lesson_candidates", [{"id": i, "menu_id": "M-%d" % i, "src": "labels", "cond": [{"feature": "coin_class", "op": "=", "value": "meme"}],
                                                             "claim": {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.2}, "env_norm": env,
                                                             "ctx_ver": "c1", "k_space": 4, "status": "hyp" if lesson_id else "new", "ts": NOW,
                                                             "lesson_id": lesson_id, "evidence": {"x": i}}])

    def trial(self, tid, rule_id, env="mainnet", state="open", verdict=None):
        store2.insert_rows(self.conn, "trials", [{"trial_id": tid, "rule_id": rule_id, "lesson_id": "L-0001", "env_norm": env, "criterion": {"k": 1},
                                                  "criterion_h": "0" * 16, "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1", "start_ts": NOW - DAY,
                                                  "deadline_ts": NOW + DAY, "state": state, "verdict": verdict}])

    def rule(self, rid, rev, status, env="mainnet"):
        store2.append_row(self.conn, "rule_rows", {"rule_id": rid, "rev": rev, "kind": "skip_when", "status": status, "from_lesson": "L-0001", "env_norm": env,
                                                   "body": {"cond": []}, "ts": iso(NOW)})

    # ---------------------------------------------------------------- уроки
    def lesson(self, lid, status="hyp", src="labels", n=40, effect=0.3, diff=-0.3, se=0.1, env="mainnet", created=NOW - DAY, hist=()):
        ev = {"n_closed": n, "diff": diff, "se": se}
        if src == "labels":
            ev.update(src="labels", sample={"env_norm": env})
        rec = {"schema": LS.SCHEMA, "lesson_id": lid, "rev": 1, "ts": iso(created), "created_ts": created, "kind": "new", "status": status,
               "cond": [{"feature": "coin_class", "op": "=", "value": "meme"}], "claim": {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": effect},
               "created_by": "code", "contradicts": [], "merged_into": None, "evidence": ev, "conf": 0.2, "expires_ts": None,
               "history": [{"ts": iso(created), "status": "hyp", "reason_code": "NEW"}] + [{"ts": iso(t), "status": s, "reason_code": "X"} for t, s in hist]}
        LS._write(self.root, rec)

    # ---------------------------------------------------------------- отчёт
    def ctx(self, mode="paper", trades=(), cases=()):
        return report.Ctx(root=self.root, mode=mode, now=NOW, cfg=conf.defaults(), trades=list(trades), cases=list(cases))


def trades_of(rs, t0, prefix, step=60):
    return [{"id": "%s-%d" % (prefix, i), "r": r, "ts_open": iso(t0 + i * step), "mode": "paper"} for i, r in enumerate(rs)]


def cases_of(trades, ver, rf=None):
    out = []
    for i, t in enumerate(trades):
        src = {"mode": "paper"}
        if ver:
            src["rulebook_ver"] = ver
        out.append({"case_id": "C-" + t["id"], "refs": {"journal_id": t["id"]}, "src": src,
                    "tags": {"rule_followed": (rf(i) if rf else "yes"), "err": []}})
    return out


class TestNoCycleTables(Base):
    def test_no_db_file_gives_stub(self):
        for fn, bid in ((stages.lessons, "lessons"), (stages.versions, "versions")):
            b = fn(self.ctx())
            self.assertEqual((b["status"], b["note"], b["stage"], b["roadmap"]), ("stage", "появится на этапе 2", 2, "R2.6"), bid)

    def test_r1_db_without_cycle_tables_gives_stub(self):
        LST.open_rw(self.root).close()              # база меток R1 без v2a
        self.lesson("L-0001")                       # уроки есть, но таблиц цикла нет: прежняя заглушка (Requirement 14.5)
        self.assertEqual(stages.lessons(self.ctx())["status"], "stage")
        self.assertEqual(stages.versions(self.ctx())["status"], "stage")

    def test_no_ctx_stub_without_files(self):
        self.assertEqual(stages.lessons(None)["status"], "stage")
        self.assertEqual(stages.versions({"root": self.root})["status"], "stage")

    def test_restoring_reason(self):
        self.db()
        self.conn.close()
        self.conn = None
        with open(KM.restoring_path(self.root), "w", encoding="utf-8") as f:
            f.write("{}")
        for fn in (stages.lessons, stages.versions):
            b = fn(self.ctx())
            self.assertEqual((b["status"], b["reason"]), ("no_data", "KNOWLEDGE_RESTORING"))
            self.assertIn("восстанавливается", b["note"])

    def test_single_version_no_changes(self):
        self.db()
        self.version("R-0001", reason="INIT")
        b = stages.versions(self.ctx())
        self.assertEqual((b["status"], b["reason"], b["rulebook"], b["swaps"]), ("no_data", "no_changes", [], []))

    def test_db_error_reason(self):
        with mock.patch.object(LST, "open_ro", side_effect=sqlite3.DatabaseError("x")):
            b = stages.lessons(self.ctx())
        self.assertEqual((b["status"], b["reason"]), ("no_data", "E_DB"))


class TestLessons(Base):
    def setUp(self):
        Base.setUp(self)
        self.db()
        self.version("R-0001", reason="INIT")

    def test_empty_tables_no_data(self):
        b = stages.lessons(self.ctx())
        self.assertEqual((b["status"], b["reason"]), ("no_data", "no_lessons"))

    def test_property17_funnel_equals_direct_queries(self):
        for i in range(1, 6):
            self.cand(i, lesson_id="L-%04d" % i if i <= 3 else None)
        self.cand(9, env="testnet", lesson_id="L-0009")
        self.trial("T-1", "RU-1", state="closed", verdict="promote")
        self.trial("T-2", "RU-2", state="closed", verdict="reject")
        self.trial("T-3", "RU-3")
        self.trial("T-9", "RU-9", env="testnet", state="closed", verdict="promote")
        self.rule("RU-1", 1, "trial")
        self.rule("RU-1", 2, "active")
        self.rule("RU-1", 3, "disabled")
        self.rule("RU-2", 1, "trial")
        b = stages.lessons(self.ctx())
        c = sqlite3.connect(LST.db_path(self.root))
        try:
            q = lambda s, a=("mainnet",): c.execute(s, a).fetchone()[0]
            want = {"candidates": q("SELECT COUNT(*) FROM lesson_candidates WHERE env_norm=?"),
                    "hyp": q("SELECT COUNT(*) FROM lesson_candidates WHERE env_norm=? AND lesson_id IS NOT NULL"),
                    "trials": q("SELECT COUNT(*) FROM trials WHERE env_norm=?"),
                    "trials_closed": q("SELECT COUNT(*) FROM trials WHERE env_norm=? AND state='closed'"),
                    "confirmed": q("SELECT COUNT(*) FROM trials WHERE env_norm=? AND verdict='promote'"),
                    "rules": q("SELECT COUNT(DISTINCT rule_id) FROM rule_rows WHERE env_norm=? AND status='active'")}
        finally:
            c.close()
        self.assertEqual(b["funnel"], want)
        self.assertEqual(want, {"candidates": 5, "hyp": 3, "trials": 3, "trials_closed": 2, "confirmed": 1, "rules": 1})
        tb_ = stages.lessons(self.ctx("testnet"))["funnel"]
        self.assertEqual((tb_["candidates"], tb_["hyp"], tb_["trials"], tb_["confirmed"]), (1, 1, 1, 1))     # режимы не смешиваются

    def test_status_and_source_counts(self):
        self.lesson("L-0001", "hyp", "labels")
        self.lesson("L-0002", "confirmed", "labels")
        self.lesson("L-0003", "rejected", "brain")
        self.lesson("L-0004", "hyp", "brain")
        self.lesson("L-0005", "hyp", "labels", env="testnet")
        b = stages.lessons(self.ctx())
        self.assertEqual(b["status"], "ok")
        rows = {r["status"]: r for r in b["by_status"]}
        self.assertEqual((rows["hyp"]["brain"], rows["hyp"]["labels"], rows["hyp"]["total"]), (1, 1, 2))
        self.assertEqual((rows["confirmed"]["labels"], rows["rejected"]["brain"], rows["expired"]["total"]), (1, 1, 0))
        self.assertEqual(b["by_source"], {"brain": 2, "labels": 2})
        self.assertEqual(b["n_lessons"], 4)
        self.assertEqual(b["env_norm"], "mainnet")
        t = stages.lessons(self.ctx("testnet"))
        self.assertEqual(t["by_source"], {"brain": 2, "labels": 1})            # урок меток testnet только в testnet

    def test_best_lesson_and_interval(self):
        self.lesson("L-0001", n=29, effect=5.0)                                 # n < 30: не кандидат в лучшие
        self.lesson("L-0002", n=30, effect=0.4, diff=-0.4, se=0.1)
        self.lesson("L-0003", n=100, effect=0.3, diff=0.3, se=0.05)
        self.lesson("L-0004", "rejected", n=200, effect=3.0)                    # не активный
        b = stages.lessons(self.ctx())
        best = b["best"]
        self.assertEqual((best["lesson_id"], best["n"], best["effect"], best["small"]), ("L-0002", 30, 0.4, False))
        h = stats.X.t_quantile(0.975, 29) * 0.1
        self.assertAlmostEqual(best["diff"]["lo"], -0.4 - h, places=9)
        self.assertAlmostEqual(best["diff"]["hi"], -0.4 + h, places=9)
        self.assertEqual((best["diff"]["n"], best["diff"]["lvl"]), (30, 0.95))

    def test_best_none_when_all_small(self):
        self.lesson("L-0001", n=29, effect=1.0)
        self.assertIsNone(stages.lessons(self.ctx())["best"])

    def test_effect_tie_smaller_id(self):
        self.lesson("L-0002", n=50, effect=0.5)
        self.lesson("L-0001", n=50, effect=0.5)
        self.assertEqual(stages.best_lesson(stages.lessons_for(list(LS.read_all(self.root).values()), "paper"))["lesson_id"], "L-0001")

    def test_weekly_series(self):
        wk = stats.week_range(NOW, 8)
        self.lesson("L-0001", "confirmed", "labels", created=wk[-2]["start"] + 10, hist=((wk[-1]["start"] + 5, "confirmed"),))
        self.lesson("L-0002", "rejected", "brain", created=wk[-1]["start"] + 10, hist=((wk[-1]["start"] + 20, "rejected"),))
        self.lesson("L-0003", "hyp", "brain", created=wk[0]["start"] - 10)      # раньше окна
        rows = stages.lessons(self.ctx())["weeks"]
        self.assertEqual(len(rows), 8)
        self.assertEqual([r["week"] for r in rows], [w["week"] for w in wk])
        self.assertEqual((rows[-2]["created_labels"], rows[-1]["created_brain"], rows[-1]["confirmed"], rows[-1]["rejected"]), (1, 1, 1, 1))
        self.assertEqual(sum(r["created_brain"] + r["created_labels"] for r in rows), 2)
        self.assertTrue(rows[-1]["partial"])


class TestVersionVerdict(unittest.TestCase):
    def side(self, n):
        return {"n": n}

    def test_few_data_boundary(self):
        d = stats.est(-1.0, -2.0, -0.5, 200)
        self.assertEqual(stages.verdict_of(self.side(99), self.side(100), d), "few_data")
        self.assertEqual(stages.verdict_of(self.side(100), self.side(99), d), "few_data")
        self.assertEqual(stages.verdict_of(self.side(100), self.side(100), d), "worse")

    def test_upper_bound_boundary(self):
        n = self.side(100)
        self.assertEqual(stages.verdict_of(n, n, stats.est(-0.1, -0.2, -1e-9, 200)), "worse")
        self.assertEqual(stages.verdict_of(n, n, stats.est(-0.1, -0.2, 0.0, 200)), "not_worse")      # граница 0 — не хуже
        self.assertEqual(stages.verdict_of(n, n, stats.est(0.1, -0.2, 0.3, 200)), "not_worse")


class TestVersions(Base):
    def setUp(self):
        Base.setUp(self)
        self.db()
        self.version("R-0001", reason="INIT", ts=NOW - 20 * DAY)
        self.version("R-0002", ["RU-1"], ts=NOW - 10 * DAY)

    def test_property17_rulebook_pair_numbers(self):
        before = trades_of([0.5 if i % 2 else -0.3 for i in range(120)], NOW - 19 * DAY, "A")
        after = trades_of([0.2 if i % 3 else -0.6 for i in range(110)], NOW - 9 * DAY, "B")
        pre = trades_of([1.0] * 7, NOW - 30 * DAY, "P")
        cs = cases_of(before, "R-0001", rf=lambda i: "no" if i < 12 else "yes") + cases_of(after, "R-0002", rf=lambda i: "na" if i < 10 else "partial") \
            + cases_of(pre, None)
        b = stages.versions(self.ctx(trades=before + after + pre, cases=cs))
        self.assertEqual(b["n_pre_r2"], 7)
        row = b["rulebook"][0]
        self.assertEqual((row["from"], row["to"], row["rules_n"]), ("R-0001", "R-0002", 1))
        rb, ra = [t["r"] for t in before], [t["r"] for t in after]
        mb, ma = stats.mean_est(rb), stats.mean_est(ra)
        self.assertEqual((row["before"]["n"], row["after"]["n"]), (120, 110))
        self.assertEqual((row["before"]["r"], row["after"]["r"]), (mb, ma))
        self.assertEqual(row["before"]["viol"], stats.wilson_est(12, 120))
        self.assertEqual((row["after"]["n_rf"], row["after"]["viol"]["v"]), (100, 0.0))
        sd = lambda xs: stats.X._mean_sd(xs)[1] / len(xs) ** 0.5
        w = stats.welch_summary(ma["v"], sd(ra), 110, mb["v"], sd(rb), 120)
        self.assertEqual((row["diff"]["v"], row["diff"]["lo"], row["diff"]["hi"]), (w["diff"], w["lo"], w["hi"]))
        self.assertEqual(row["verdict"], "worse" if w["hi"] < 0 else "not_worse")
        self.assertEqual(b["status"], "ok")

    def test_few_data_rulebook(self):
        before = trades_of([0.1] * 40, NOW - 19 * DAY, "A")
        after = trades_of([0.2] * 20, NOW - 9 * DAY, "B")
        b = stages.versions(self.ctx(trades=before + after, cases=cases_of(before, "R-0001") + cases_of(after, "R-0002")))
        row = b["rulebook"][0]
        self.assertEqual(row["verdict"], "few_data")
        self.assertTrue(row["after"]["small"])                  # n < 30 помечено
        self.assertEqual((b["status"], b["reason"]), ("few_data", "n_lt_min"))

    def test_worse_after(self):
        before = trades_of([0.5, 0.7] * 60, NOW - 19 * DAY, "A")
        after = trades_of([-0.5, -0.3] * 60, NOW - 9 * DAY, "B")
        b = stages.versions(self.ctx(trades=before + after, cases=cases_of(before, "R-0001") + cases_of(after, "R-0002")))
        self.assertEqual(b["rulebook"][0]["verdict"], "worse")

    def write_swaps(self, recs):
        with open(os.path.join(self.root, "state", "brain", "swaps.jsonl"), "w", encoding="utf-8") as f:
            for i, r in enumerate(recs, 1):
                f.write(json.dumps(dict({"seq": i, "result": "ok", "tier": 0, "by": "owner-cli", "from": "a", "to": "b", "target": ["entry_scout"]}, **r)) + "\n")

    def test_swaps_segments_and_merge(self):
        t1, t2 = NOW - 6 * DAY, NOW - 3 * DAY
        self.write_swaps([{"kind": "use", "ts": t1}, {"kind": "use", "ts": t1 + 1, "target": ["position_manager"]},       # одна смена (пакет)
                          {"kind": "reload", "ts": t2, "target": ["files"], "to": "-"},
                          {"kind": "use", "ts": t2 + 5000, "result": "failed"}])                                            # неуспешная — не смена
        tr = trades_of([0.1] * 5, t1 - 3600, "X") + trades_of([0.2] * 4, t1 + 3600, "Y") + trades_of([0.3] * 3, t2 + 3600, "Z")
        b = stages.versions(self.ctx(trades=tr))
        sw = b["swaps"]
        self.assertEqual(len(sw), 2)
        self.assertEqual((sw[0]["kind"], sw[0]["seqs"], sw[0]["targets"]), ("model", [1, 2], ["entry_scout", "position_manager"]))
        self.assertEqual((sw[0]["before"]["n"], sw[0]["after"]["n"]), (5, 4))
        self.assertEqual((sw[1]["kind"], sw[1]["before"]["n"], sw[1]["after"]["n"]), ("prompt", 4, 3))
        self.assertEqual(sw[1]["to"], [])
        self.assertEqual(sw[1]["after"]["r"], stats.mean_est([0.3] * 3))

    def test_modes_not_mixed_in_report(self):
        """Сделки режима берёт report (load.brain_trades): live-сделки в paper-отчёт не попадают."""
        rows = []
        for i in range(6):
            for mode in ("paper", "live"):
                tid = "%s-%d" % (mode, i)
                rows.append({"ev": "open", "id": tid, "ts": iso(NOW - 5 * DAY + i * 60), "sym": "ETH", "side": "long", "mode": mode, "plan_id": "PL-" + tid,
                             "risk_usdt": 10})
                rows.append({"ev": "close", "id": tid, "ts_close": iso(NOW - 5 * DAY + i * 60 + 30), "pnl_usdt": 5.0, "r": 0.5})
        with open(os.path.join(self.root, "state", "trades.jsonl"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        self.write_swaps([{"kind": "use", "ts": NOW - 5 * DAY + 3 * 60 - 1}])
        self.conn.close()
        self.conn = None
        out = report.build(self.root, "paper", NOW)
        sw = out["blocks"]["versions"]["swaps"][0]
        self.assertEqual((sw["before"]["n"], sw["after"]["n"]), (3, 3))
        self.assertEqual(out["blocks"]["versions"]["mode"], "paper")


class TestPageVerdictAndReadOnly(Base):
    def test_page_verdict_independent_of_lessons(self):
        self.db()
        self.version("R-0001", reason="INIT")
        self.conn.close()
        self.conn = None
        v0 = report.build(self.root, "paper", NOW)["verdict"]
        for i in range(1, 4):
            self.lesson("L-%04d" % i, n=50, effect=0.5)
        out = report.build(self.root, "paper", NOW)
        self.assertEqual(out["blocks"]["lessons"]["status"], "ok")
        self.assertEqual(out["verdict"], v0)

    def test_no_writes_no_rw(self):
        self.db()
        self.version("R-0001", reason="INIT")
        self.lesson("L-0001")
        self.conn.close()
        self.conn = None
        real_open = builtins.open

        def guarded(f, mode="r", *a, **kw):
            if any(ch in mode for ch in "wax+"):
                raise AssertionError("запись файла: %s" % f)
            return real_open(f, mode, *a, **kw)

        before = sorted(os.listdir(os.path.join(self.root, "state")))
        with mock.patch.object(builtins, "open", guarded), mock.patch.object(LST, "open_rw", side_effect=AssertionError("open_rw")):
            stages.build(self.ctx())
        self.assertEqual([x for x in sorted(os.listdir(os.path.join(self.root, "state"))) if not x.endswith(("-wal", "-shm"))],
                         [x for x in before if not x.endswith(("-wal", "-shm"))])

    def test_one_connection_per_call_closed(self):
        self.db()
        self.version("R-0001", reason="INIT")
        self.conn.close()
        self.conn = None
        opened = []
        real = LST.open_ro

        def spy(root=None):
            c = real(root)
            opened.append(c)
            return c

        with mock.patch.object(LST, "open_ro", spy):
            stages.build(self.ctx())
        self.assertEqual(len(opened), 2)
        for c in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                c.execute("SELECT 1")

    def test_cache_signature_sees_new_sources(self):
        from tb.learn import load
        s0 = load.source_signature(self.root)
        self.lesson("L-0001")
        s1 = load.source_signature(self.root)
        self.assertNotEqual(s0, s1)
        with open(os.path.join(self.root, "state", "brain", "swaps.jsonl"), "w", encoding="utf-8") as f:
            f.write("{}\n")
        self.assertNotEqual(s1, load.source_signature(self.root))


if __name__ == "__main__":
    unittest.main()
