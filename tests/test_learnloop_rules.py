"""tb.learnloop.rules и tb.learnloop.versions, ./tb rulebook (R2, задача 11): свойства 20 (правило и испытание одной транзакцией, гонка двух создателей,
одно испытание на правило), 23 (чёрный список — единственный запрет повтора: срок 14 суток, бессрочные записи владельца с копией, коды без записи),
24 (часть overlap_rows: пересечение по строкам выборки окна), derive, достижимость, версии R-NNNN (INIT, PROMOTE только ужесточение, выключение,
BOUNDS_BLOCKED), ожидание wait, rules_ver не меняется, копии после смены версии, CLI list|show|verify. Сеть не используется."""
import hashlib
import io
import json
import math
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _bootstrap  # noqa: F401
from tb.knowledge import rulebook_cli as CLI
from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import rules as RL
from tb.learnloop import versions as V
from tb.logic import learn_rules as LR

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = 1793664000          # 2026-11-03T00:00:00Z
DAY = 86400
P2 = {"id": "P2", "ver": 1, "name": "confirm_follow", "kinds": ["range_5m", "turnover_surge", "hot_enter", "oi_spike", "liq_cascade"], "side": "with_dir",
      "entry": "candle_confirm_limit"}
SPREAD = [{"feature": "spread_bps", "op": ">=", "value": 5.0}]
RANGE = [{"feature": "range_5m_pct", "op": "<=", "value": 0.4}]


def lesson(lid="L-0001", cond=None, dir="lower", status="hyp", src="labels", vs="base"):
    return {"lesson_id": lid, "status": status, "cond": cond or SPREAD, "claim": {"metric": "mean_r", "dir": dir, "vs": vs, "effect": 0.4},
            "evidence": {"src": src, "n_closed": 40, "diff": -0.4, "se": 0.1}}


def trial_row(now=NOW):
    return {"criterion": {"n_target": 150}, "criterion_h": "a" * 16, "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1", "start_ts": int(now),
            "deadline_ts": int(now) + 30 * DAY, "env_norm": "mainnet"}


def fhash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def run_row(key, cluster, kind="range_5m", filled=1, r=0.0, policy="P2", **f):
    feats = {"spread_bps": None, "range_5m_pct": None, "coin_class": None}
    feats.update(f)
    return {"key": key, "cluster": cluster, "type": kind, "filled": filled, "r_net": r, "policy_id": policy, "feats": json.dumps(feats)}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbrules_")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "config"))
        for f in ("learnloop.json", "memory.json"):
            shutil.copy(os.path.join(REPO, "config", f), os.path.join(self.root, "config", f))
        with open(os.path.join(self.root, "config", "learnloop.json"), encoding="utf-8") as f:
            self.cfg = json.load(f)
        RV.clear_cache()
        store2._STATUS_CACHE.clear()
        self.conns = []
        self.c = self.rw()
        store2.migrate_v2a(self.c, self.root, explicit=True, backup=False, now=NOW)

    def tearDown(self):
        for c in self.conns:
            try:
                c.close()
            except sqlite3.Error:
                pass

    def rw(self):
        c = store.open_rw(self.root)
        self.conns.append(c)
        return c

    def make(self, lid="L-0001", cond=None, now=NOW):
        draft = RL.derive(lesson(lid, cond), P2, self.c, now)
        self.assertIsNotNone(draft)
        return V.create_with_trial(self.c, draft, trial_row(now), now, self.cfg)

    def count(self, table):
        return self.c.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]

    def backups(self):
        d = os.path.join(self.root, "state", "backups")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []


# ---------------------------------------------------------------- derive и чёрный список

class TestDerive(Base):
    def test_skip_when_from_labels_lesson(self):
        r = RL.derive(lesson(cond=[{"feature": "spread_bps", "op": ">=", "value": 5.0}, {"feature": "coin_class", "op": "=", "value": "meme"}]), P2)
        self.assertEqual((r["kind"], r["param"], r["from_lesson"], r["env_norm"]), ("skip_when", {}, "L-0001", "mainnet"))
        self.assertEqual([c["feature"] for c in r["cond"]], ["coin_class", "spread_bps"])
        self.assertEqual(r["scope"], {"policy": "P2", "kinds": P2["kinds"], "side": "with_dir", "entry": "candle_confirm_limit"})
        self.assertEqual(r["cond_h"], LR.cond_h(SPREAD + [{"feature": "coin_class", "op": "=", "value": "meme"}], "lower"))

    def test_only_mean_r_lower_base(self):
        self.assertEqual(RL.derive_reason(lesson(dir="higher")), "NOT_SKIP")
        self.assertEqual(RL.derive_reason(lesson(vs="zero")), "NOT_SKIP")
        self.assertEqual(RL.derive_reason(lesson(src="brain")), "NOT_LABELS")
        self.assertEqual(RL.derive_reason(lesson(status="confirmed")), "NOT_HYP")
        self.assertEqual(RL.derive_reason(lesson(cond=[{"feature": "stop_pct", "op": ">=", "value": 2}])), "BAD_COND")
        self.assertIsNone(RL.derive(lesson(dir="higher"), P2))
        self.assertIsNone(RL.derive(lesson(), dict(P2, side="random")))                      # область без стороны сигнала — правила нет

    def test_blocked_condition(self):
        ch = LR.cond_h(SPREAD, "lower")
        RL.block(self.c, ch, "TRIAL_REJECT", NOW, self.cfg)
        self.assertEqual(RL.derive_reason(lesson(), self.c, NOW + 13 * DAY), "BLOCKED")
        self.assertIsNone(RL.derive(lesson(), P2, self.c, NOW + 13 * DAY))
        self.assertIsNone(RL.derive_reason(lesson(), self.c, NOW + 14 * DAY))                 # срок 14 суток истёк
        self.assertIsNotNone(RL.derive(lesson(), P2, self.c, NOW + 14 * DAY + 1))


class TestBlocklist(Base):
    def test_policy_table(self):
        for code in ("NO_OOS_SIGN", "NOT_SIGNIFICANT", "N_TOO_SMALL", "SAMPLE_CHANGED", "TRIAL_REJECT", "NO_VERDICT", "UNREACHABLE_N", "ROLLBACK",
                     "TRADE_STARVATION", "BOUNDS_BLOCKED"):
            self.assertEqual(RL.block_policy(code), "cooldown", code)
        for code in ("OWNER_DISABLE", "OWNER_REJECT"):
            self.assertEqual(RL.block_policy(code), "forever")
        for code in ("LOSS_CAP", "CTX_CHANGED", "RESTORED", "DUP_ACTIVE", "BUDGET", "EVIDENCE_TOO_BIG", "OWNER"):
            self.assertIsNone(RL.block_policy(code), code)
        self.assertIsNone(RL.block_policy("NO_VERDICT", RL.OVERLAP))
        with self.assertRaises(ValueError):
            RL.block_policy("WHATEVER")

    def test_cooldown_fixed_14_days(self):
        ch = "b" * 16
        row = RL.block(self.c, ch, "NOT_SIGNIFICANT", NOW, self.cfg)
        self.assertEqual((row["until_ts"], row["actor"]), (NOW + 14 * DAY, "code"))
        self.assertIsNotNone(RL.blocked(self.c, ch, NOW + 14 * DAY - 1))
        self.assertIsNone(RL.blocked(self.c, ch, NOW + 14 * DAY))
        RL.block(self.c, ch, "NOT_SIGNIFICANT", NOW + 20 * DAY, self.cfg)                     # повтор — снова ровно 14 суток, без роста срока
        self.assertEqual(RL.blocked(self.c, ch, NOW + 20 * DAY)["until_ts"], NOW + 34 * DAY)

    def test_no_write_codes(self):
        n0 = self.count("blocklist_rows")
        for code, reason in (("LOSS_CAP", None), ("CTX_CHANGED", None), ("RESTORED", None), ("NO_VERDICT", "OVERLAP"), ("BUDGET", None)):
            self.assertIsNone(RL.block(self.c, "c" * 16, code, NOW, self.cfg, reason=reason))
        self.assertEqual(self.count("blocklist_rows"), n0)

    def test_owner_forever_with_backup(self):
        row = RL.block(self.c, "d" * 16, "OWNER_REJECT", NOW, self.cfg, root=self.root)
        self.assertEqual((row["until_ts"], row["actor"]), (None, "OWNER"))
        self.assertTrue(os.path.basename(row["backup"]).endswith("-owner.sqlite"))
        self.assertIsNotNone(RL.blocked(self.c, "d" * 16, NOW + 3650 * DAY))


# ---------------------------------------------------------------- строки выборки окна

class TestOverlapAndReach(unittest.TestCase):
    A = {"rule_id": "RL0001", "kind": "skip_when", "cond": SPREAD, "param": {}, "scope": {"policy": "P2", "kinds": P2["kinds"], "side": "with_dir"}}
    B = {"rule_id": "RL0002", "kind": "skip_when", "cond": RANGE, "param": {}, "scope": {"policy": "P2", "kinds": P2["kinds"], "side": "with_dir"}}

    def test_different_features_small_common_rows(self):
        rows = [run_row("k%d" % i, "h%d:long" % (i // 4), spread_bps=9.0, range_5m_pct=2.0) for i in range(40)]       # хиты A, не B
        rows += [run_row("m%d" % i, "h%d:long" % (i // 4), spread_bps=1.0, range_5m_pct=0.1) for i in range(40)]      # хиты B, не A
        rows += [run_row("x%d" % i, "h%d:long" % i, spread_bps=9.0, range_5m_pct=0.1) for i in range(4)]              # общие
        share = RL.overlap_rows(rows, self.A, self.B)
        self.assertAlmostEqual(share, 4 / 44.0)
        self.assertLess(share, 0.5)
        self.assertAlmostEqual(RL.overlap_rows({"rows": rows}, self.B, self.A), 4 / 44.0)
        self.assertAlmostEqual(RL.overlap_rows(rows, self.A, self.A), 1.0)

    def test_filled_scope_and_empty(self):
        rows = [run_row("a", "h1:long", spread_bps=9.0, range_5m_pct=0.1, filled=0), run_row("b", "h1:long", spread_bps=9.0, range_5m_pct=2.0),
                run_row("c", "h1:long", kind="patrol", spread_bps=9.0, range_5m_pct=0.1), run_row("d", "h1:long", spread_bps=None, range_5m_pct=0.1)]
        self.assertEqual(RL.hit_keys(rows, self.A), {"b"})                   # незаполненный, вне scope и без признака — не хиты
        self.assertEqual(RL.overlap_rows(rows, self.A, self.B), 0.0)
        self.assertEqual(RL.overlap_rows([], self.A, self.B), 0.0)
        other = [dict(run_row("e", "h1:long", spread_bps=9.0), policy_id="P4")]                # чужая политика без side_rel — сторона не определена
        self.assertEqual(RL.hit_keys(other, self.A), set())
        self.assertEqual(RL.overlap_keys({"a", "b"}, {"b", "c"}), 0.5)

    def test_reach(self):
        rows = []
        for h in range(14):                                                                   # 14 кластеров теста за 7 суток -> 2 в сутки
            rows += [run_row("i%d" % h, "%d:long" % h, spread_bps=9.0), run_row("o%d" % h, "%d:long" % h, spread_bps=1.0)]
        rows += [run_row("z", "99:long", spread_bps=9.0)]                                     # кластер без базы — не кластер теста
        self.assertEqual(RL.test_cluster_count(rows, self.A), 14)
        cfg = {"trial": {"n_target": 150, "reach_lookback_days": 7, "reach_factor": 0.8, "max_days": 30}}
        days = RL.estimate_reach(rows, self.A, cfg)
        self.assertAlmostEqual(days, 75.0)
        self.assertFalse(RL.reachable(days, cfg))
        self.assertTrue(RL.reachable(RL.estimate_reach(rows * 1, self.A, cfg, lookback_days=1), cfg))
        # активный набор отсекает базу (pass_active=0): кластеров теста нет -> недостижимо
        cut_base = dict(self.B, cond=[{"feature": "spread_bps", "op": "<=", "value": 2.0}])
        self.assertEqual(RL.test_cluster_count(rows, self.A, [cut_base]), 0)
        self.assertEqual(RL.estimate_reach(rows, self.A, cfg, [cut_base]), math.inf)


# ---------------------------------------------------------------- версии и правила

class TestVersions(Base):
    def test_init_idempotent(self):
        v = V.init_rulebook(self.c, NOW)
        self.assertEqual((v["ver"], v["rule_ids"], v["reason_code"], v["parent"]), ("R-0001", [], "INIT", None))
        V.init_rulebook(self.c, NOW + 1)
        self.assertEqual(self.count("rulebook_versions"), 1)

    def test_create_with_trial(self):
        ids = self.make()
        self.assertEqual(ids, {"rule_id": "RL0001", "trial_id": "TR0001"})
        r = V.rule(self.c, "RL0001")
        self.assertEqual((r["status"], r["rev"], r["reason_code"], r["from_lesson"], r["body"]["trial_id"]), ("trial", 1, "TRIAL", "L-0001", "TR0001"))
        t = self.c.execute("SELECT rule_id, lesson_id, state FROM trials").fetchall()
        self.assertEqual(t, [("RL0001", "L-0001", "open")])
        with self.assertRaises(V.RuleRefused) as e:                                           # второе не-терминальное правило урока
            self.make()
        self.assertEqual(e.exception.code, "DUP_RULE")
        self.assertEqual(self.make("L-0002", RANGE)["rule_id"], "RL0002")

    def test_atomic_rule_and_trial(self):
        """Свойство 20: сбой вставки испытания откатывает и правило."""
        draft = RL.derive(lesson(), P2)
        bad = trial_row()
        del bad["criterion"]
        with self.assertRaises(sqlite3.IntegrityError):
            V.create_with_trial(self.c, draft, bad, NOW, self.cfg)
        self.assertEqual((self.count("rule_rows"), self.count("trials")), (0, 0))
        self.assertFalse(self.c.in_transaction)
        with self.assertRaises(sqlite3.IntegrityError):                                       # UNIQUE(rule_id) в trials
            store2.insert_rows(self.c, "trials", [dict(trial_row(), trial_id="TRX", rule_id="RL0001", lesson_id="L-1")] * 2)

    def test_race_two_creators(self):
        """Два создателя (разные соединения, потоки) на один урок: ровно одно правило и одно испытание."""
        res, barrier = [], threading.Barrier(2)

        def go():
            c = store.open_rw(self.root)
            try:
                barrier.wait()
                res.append(V.create_with_trial(c, RL.derive(lesson(), P2), trial_row(), NOW, self.cfg)["rule_id"])
            except V.RuleRefused as e:
                res.append(e.code)
            finally:
                c.close()

        ts = [threading.Thread(target=go) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        self.assertEqual(sorted(res), ["DUP_RULE", "RL0001"])
        self.assertEqual((self.count("rule_rows"), self.count("trials")), (1, 1))

    def test_blocked_refused_in_tx(self):
        draft = RL.derive(lesson(), P2)
        RL.block(self.c, draft["cond_h"], "ROLLBACK", NOW, self.cfg)
        with self.assertRaises(V.RuleRefused) as e:
            V.create_with_trial(self.c, draft, trial_row(), NOW + DAY, self.cfg)
        self.assertEqual(e.exception.code, "BLOCKED")

    def test_promote_disable_cycle(self):
        mem = os.path.join(self.root, "config", "memory.json")
        h0 = fhash(mem)
        self.make()
        rep = V.promote(self.c, "RL0001", NOW + DAY, cfg=self.cfg, root=self.root, numbers={"n": 150, "p": 0.001})
        self.assertEqual((rep["ver"], rep["parent"], rep["rule_ids"]), ("R-0002", "R-0001", ["RL0001"]))
        self.assertTrue(rep["backup"].endswith("-promote.sqlite"))
        r = V.rule(self.c, "RL0001")
        self.assertEqual((r["status"], r["rulebook_ver_in"], r["rev"]), ("active", "R-0002", 2))
        self.assertEqual(self.c.execute("SELECT trial_id FROM rulebook_versions WHERE ver='R-0002'").fetchone()[0], "TR0001")
        self.assertEqual([x["rule_id"] for x in V.active_rules(self.c)], ["RL0001"])
        rep = V.disable(self.c, "RL0001", "ROLLBACK", NOW + 2 * DAY, self.cfg, root=self.root, numbers={"n": 50, "mean_r": 0.4, "p": 0.001})
        self.assertEqual((rep["ver"], rep["rule_ids"]), ("R-0003", []))
        self.assertTrue(rep["backup"].endswith("-rollback.sqlite"))
        r = V.rule(self.c, "RL0001")
        self.assertEqual((r["status"], r["reason_code"], r["rulebook_ver_out"]), ("disabled", "ROLLBACK", "R-0003"))
        bl = RL.blocked(self.c, r["body"]["cond"], NOW + 2 * DAY)
        self.assertEqual((bl["code"], bl["until_ts"]), ("ROLLBACK", NOW + 16 * DAY))
        self.assertEqual(store2.verify_chain(self.c)["ok"], True)
        self.assertEqual(fhash(mem), h0)               # rules_ver (config/memory.json) не меняется
        with self.assertRaises(V.RuleRefused):                                                # терминальное правило не продвигается
            V.promote(self.c, "RL0001", NOW + 3 * DAY, cfg=self.cfg, root=None)

    def test_owner_disable_forever(self):
        self.make()
        V.promote(self.c, "RL0001", NOW, cfg=self.cfg, root=None)
        rep = V.disable(self.c, ["RL0001"], "OWNER_DISABLE", NOW, self.cfg, root=self.root)
        self.assertTrue(rep["backup"].endswith("-owner.sqlite"))
        bl = RL.blocked(self.c, LR.block_h(SPREAD), NOW + 400 * DAY)
        self.assertEqual((bl["code"], bl["until_ts"]), ("OWNER_DISABLE", None))
        self.assertEqual(self.c.execute("SELECT actor FROM blocklist_rows ORDER BY seq DESC LIMIT 1").fetchone()[0], "OWNER")

    def test_promote_only_tightening(self):
        """size_cap при allow_size_rules=false не продвигается (и не применяется); продвижение не может убрать правило."""
        V.init_rulebook(self.c, NOW)
        self.make()
        with store.tx(self.c):
            store2.append_row(self.c, "rule_rows", {"rule_id": "RL0002", "rev": 1, "kind": "size_cap", "status": "trial", "from_lesson": "L-0002",
                                                    "env_norm": "mainnet", "body": {"cond": RANGE, "param": {"mult": 0.5},
                                                                                    "scope": {"policy": "P2", "kinds": P2["kinds"], "side": "with_dir"}},
                                                    "ts": store2.iso(NOW)})
        with self.assertRaises(V.RuleRefused) as e:
            V.promote(self.c, "RL0002", NOW, cfg=self.cfg)
        self.assertEqual(e.exception.code, "NOT_TIGHTENING")
        self.assertEqual(V.current(self.c)["ver"], "R-0001")
        V.promote(self.c, "RL0001", NOW, cfg=self.cfg)
        with self.assertRaises(V.RuleRefused):
            V.write_version(self.c, "PROMOTE", [], NOW, changes=[], cfg=self.cfg)
        with self.assertRaises(V.RuleRefused):
            V.write_version(self.c, "ROLLBACK", ["RL0001", "RL0002"], NOW, cfg=self.cfg)
        with self.assertRaises(V.RuleRefused):
            V.write_version(self.c, "INIT", [], NOW)

    def test_bounds_check_disables_invalid_active(self):
        self.make()
        V.promote(self.c, "RL0001", NOW, cfg=self.cfg)
        self.assertIsNone(V.bounds_check(self.c, NOW, self.cfg))
        prev = V.rule(self.c, "RL0001")
        with store.tx(self.c):                                                                 # ревизия с неизвестным видом (как при порче данных)
            V._append_rev(self.c, prev, NOW, kind="boost_when")
        self.assertEqual(RV.active_from_conn(self.c)["invalid"], ["RL0001"])
        rep = V.bounds_check(self.c, NOW + 60, self.cfg, root=self.root)
        self.assertEqual((rep["ver"], rep["rule_ids"]), ("R-0003", []))
        r = V.rule(self.c, "RL0001")
        self.assertEqual((r["status"], r["reason_code"]), ("disabled", "BOUNDS_BLOCKED"))
        self.assertEqual(self.c.execute("SELECT reason_code FROM rulebook_versions WHERE ver='R-0003'").fetchone()[0], "BOUNDS_BLOCKED")
        self.assertEqual(RL.blocked(self.c, r["body"]["cond"], NOW + 60)["code"], "BOUNDS_BLOCKED")

    def test_reject_and_wait(self):
        self.make()
        self.make("L-0002", RANGE)
        self.make("L-0003", [{"feature": "coin_class", "op": "=", "value": "meme"}])
        self.assertIsNotNone(V.set_wait(self.c, "RL0001", "LOSS_CAP", NOW))
        self.assertIsNone(V.set_wait(self.c, "RL0001", "LOSS_CAP", NOW + 3600))                # та же причина — без строки
        r = V.rule(self.c, "RL0001")
        self.assertEqual((r["wait"], r["wait_since_ts"]), ("LOSS_CAP", NOW))
        V.set_wait(self.c, "RL0001", "READY", NOW + 7200)
        self.assertEqual(V.rule(self.c, "RL0001")["wait_since_ts"], NOW + 7200)
        with self.assertRaises(V.RuleRefused):
            V.set_wait(self.c, "RL0001", "SOON", NOW)
        V.reject_rule(self.c, "RL0001", "LOSS_CAP", NOW, self.cfg)                            # без чёрного списка
        V.reject_rule(self.c, "RL0002", "NO_VERDICT", NOW, self.cfg, reason="OVERLAP")        # без чёрного списка
        V.reject_rule(self.c, "RL0003", "TRIAL_REJECT", NOW, self.cfg)                        # 14 суток
        self.assertIsNone(V.reject_rule(self.c, "RL0003", "TRIAL_REJECT", NOW, self.cfg))     # идемпотентно
        self.assertEqual([x[0] for x in self.c.execute("SELECT code FROM blocklist_rows")], ["TRIAL_REJECT"])
        self.assertEqual(V.rule(self.c, "RL0001")["wait"], None)
        self.assertEqual(V.non_terminal_for_lesson(self.c, "L-0001"), [])
        self.assertEqual(self.make("L-0001", [{"feature": "funding_pct", "op": ">=", "value": 0.01}])["rule_id"], "RL0004")   # новое правило после отклонения
        with self.assertRaises(V.RuleRefused):
            V.reject_rule(self.c, "RL0004", "ROLLBACK", NOW, self.cfg)


# ---------------------------------------------------------------- CLI

class TestCli(Base):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = CLI.main(list(argv), root=self.root)
        return code, out.getvalue(), err.getvalue()

    def test_list_show_verify(self):
        self.make()
        V.promote(self.c, "RL0001", NOW, cfg=self.cfg)
        code, out, _ = self.run_cli("list")
        d = json.loads(out)
        self.assertEqual((code, d["ver"], [v["ver"] for v in d["versions"]], d["rules"][0]["status"], d["why"]), (0, "R-0002", ["R-0001", "R-0002"], "active", "LEASE"))
        code, out, _ = self.run_cli("show")
        self.assertEqual((code, json.loads(out)["version"]["ver"], [r["rule_id"] for r in json.loads(out)["rules"]]), (0, "R-0002", ["RL0001"]))
        code, out, _ = self.run_cli("show", "RL0001", "--text")
        self.assertEqual(code, 0)
        self.assertIn("rev 2", out)
        self.assertEqual(self.run_cli("show", "R-0009")[0], 1)
        code, out, _ = self.run_cli("verify")
        self.assertEqual((code, json.loads(out)["ok"]), (0, True))
        self.assertEqual(self.run_cli()[0], 2)

    def test_verify_detects_chain_break(self):
        V.init_rulebook(self.c, NOW)
        self.make()
        V.promote(self.c, "RL0001", NOW, cfg=self.cfg)
        self.c.execute("DROP TRIGGER tr_rulebook_versions_nu")
        self.c.execute("UPDATE rulebook_versions SET reason_code='ROLLBACK' WHERE seq=1")
        code, out, _ = self.run_cli("verify")
        d = json.loads(out)
        self.assertEqual((code, d["ok"], d["chain"]["ok"]), (1, False, False))

    def test_no_db_and_restoring(self):
        empty = tempfile.mkdtemp(prefix="tbrules_empty_")
        self.addCleanup(shutil.rmtree, empty, True)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(CLI.main(["list"], root=empty), 1)
        from tb.knowledge import markers as KM
        KM.write_json(KM.restoring_path(self.root), {"pid": 1})
        code, _, err = self.run_cli("verify")
        self.assertEqual(code, 1)
        self.assertIn("восстанавливается", err)

    def test_registered_in_main(self):
        from tb import __main__ as M
        self.assertEqual(M.V2["rulebook"], "tb.knowledge.rulebook_cli")


if __name__ == "__main__":
    unittest.main()
