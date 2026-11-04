"""tb.knowledge.rulebook_view (R2, задача 11): свойство 22 (нет безнадзорных правил: просроченная или отсутствующая аренда, маркер восстановления,
сигнал повреждения, повреждённая или отсутствующая база, apply=false и при повреждённой базе), соединение на один вызов (файл базы можно
переименовать сразу после вызова), кеш по версии, resolve_event по веткам (event_seq, чужая монета, старое событие, idea_stale/idea_level,
вид вне scope, нет события, предыдущий ротированный файл только при недавней ротации), rule_eval_fields (shadow_hits пусто без правил trial), слои. Сеть не используется."""
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import test_layering as TL
from tb import paths
from tb.knowledge import markers as KM
from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import versions as V
from tb.logic import learn_rules as LR

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P2 = {"id": "P2", "ver": 1, "kinds": ["range_5m", "turnover_surge", "hot_enter", "oi_spike", "liq_cascade"], "side": "with_dir", "entry": "candle_confirm_limit"}
KINDS = P2["kinds"]


def copy_cfg(root):
    os.makedirs(os.path.join(root, "config"), exist_ok=True)
    for f in ("learnloop.json", "memory.json"):
        shutil.copy(os.path.join(REPO, "config", f), os.path.join(root, "config", f))


def cfg_of(root):
    with open(os.path.join(root, "config", "learnloop.json"), encoding="utf-8") as f:
        return json.load(f)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbrbv_")
        self.addCleanup(shutil.rmtree, self.root, True)
        copy_cfg(self.root)
        RV.clear_cache()
        store2._STATUS_CACHE.clear()
        self.now = time.time()

    def db(self, with_rule=True):
        """База v2 с R-0001 и (по умолчанию) одним активным правилом spread_bps >= 5 (R-0002)."""
        c = store.open_rw(self.root)
        try:
            store2.migrate_v2a(c, self.root, explicit=True, backup=False, now=self.now)
            V.init_rulebook(c, self.now)
            if with_rule:
                draft = {"kind": "skip_when", "cond": [{"feature": "spread_bps", "op": ">=", "value": 5.0}], "param": {},
                         "scope": {"policy": "P2", "kinds": KINDS, "side": "with_dir", "entry": "candle_confirm_limit"}, "env_norm": "mainnet",
                         "from_lesson": "L-0001", "policy_ver": 1}
                ids = V.create_with_trial(c, draft, trial_row(self.now), self.now)
                V.promote(c, ids["rule_id"], self.now, cfg=cfg_of(self.root), root=self.root, backup=False)
        finally:
            c.close()

    def lease(self, age_s=60.0):
        KM.write_json(RV.lease_path(self.root), {"ts": self.now - age_s, "rulebook_ver": "R-0002"})

    def switch(self, on):
        KM.write_json(RV.switch_path(self.root), {"apply": on})


def trial_row(now):
    return {"criterion": {"n_target": 150}, "criterion_h": "0" * 16, "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1", "start_ts": int(now),
            "deadline_ts": int(now) + 30 * 86400, "env_norm": "mainnet"}


class TestSafetyRule(Base):
    def test_ok_applies(self):
        self.db()
        self.lease()
        v = RV.active(self.root, self.now)
        self.assertEqual((v["apply"], v["why"], v["ver"], [r["rule_id"] for r in v["rules"]]), (True, "OK", "R-0002", ["RL0001"]))
        self.assertEqual(v["kinds"], sorted(KINDS))

    def test_no_db(self):
        v = RV.active(self.root, self.now)
        self.assertEqual((v["apply"], v["why"], v["ver"], v["rules"]), (False, "DB", None, []))

    def test_lease_stale_or_missing(self):
        self.db()
        self.assertEqual(RV.active(self.root, self.now)["why"], "LEASE")                      # аренды ещё нет (старт платформы)
        self.lease(age_s=6 * 3600 + 5)
        v = RV.active(self.root, self.now)
        self.assertEqual((v["apply"], v["why"]), (False, "LEASE"))
        self.assertEqual(len(v["rules"]), 1)                                                   # правила видны, но не применяются
        self.lease(age_s=6 * 3600 - 5)
        self.assertTrue(RV.active(self.root, self.now)["apply"])
        KM.write_json(RV.lease_path(self.root), {"ts": self.now + 3600})                        # аренда из будущего
        self.assertEqual(RV.active(self.root, self.now)["why"], "LEASE")

    def test_lease_without_config(self):
        self.db()
        self.lease()
        os.remove(os.path.join(self.root, "config", "learnloop.json"))
        self.assertEqual(RV.active(self.root, self.now)["why"], "LEASE")

    def test_switch(self):
        self.db()
        self.lease()
        self.switch(False)
        self.assertEqual(RV.active(self.root, self.now)["why"], "SWITCH")
        with open(RV.switch_path(self.root), "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertEqual(RV.active(self.root, self.now)["why"], "SWITCH")                     # битый выключатель — без применения
        self.switch(True)
        self.assertEqual(RV.active(self.root, self.now)["why"], "OK")

    def test_switch_off_with_corrupt_db(self):
        self.db()
        self.lease()
        self.switch(False)
        with open(store.db_path(self.root), "wb") as f:
            f.write(b"not a database " * 300)
        for suf in ("-wal", "-shm"):
            KM.remove(store.db_path(self.root) + suf)
        RV.clear_cache()
        v = RV.active(self.root, self.now)
        self.assertEqual((v["apply"], v["why"], v["rules"]), (False, "SWITCH", []))
        self.switch(True)
        self.assertEqual(RV.active(self.root, self.now)["why"], "DB")

    def test_restoring_marker(self):
        self.db()
        self.lease()
        KM.write_json(KM.restoring_path(self.root), {"pid": 1, "ts": int(self.now)})
        with mock.patch.object(store, "open_ro", side_effect=AssertionError("open_ro при маркере")) as m:
            v = RV.active(self.root, self.now)
        self.assertFalse(m.called)
        self.assertEqual((v["apply"], v["why"], v["rules"]), (False, "RESTORING", []))

    def test_corrupt_signal_and_restore_failed(self):
        self.db()
        self.lease()
        KM.write_corrupt(self.root, where="label_step")
        self.assertEqual(RV.active(self.root, self.now)["why"], "DB")
        KM.remove(KM.corrupt_path(self.root))
        KM.write_json(KM.restore_failed_path(self.root), {"code": "ACK_TIMEOUT"})
        self.assertEqual(RV.active(self.root, self.now)["why"], "DB")
        KM.remove(KM.restore_failed_path(self.root))
        self.assertEqual(RV.active(self.root, self.now)["why"], "OK")

    def test_db_error_is_no_apply(self):
        self.db()
        self.lease()
        with mock.patch.object(RV, "_load", side_effect=sqlite3.DatabaseError("database disk image is malformed")):
            self.assertEqual(RV.active(self.root, self.now)["why"], "DB")

    def test_connection_per_call(self):
        """После вызова соединение закрыто: файл базы переименовывается (на Windows открытый файл не переименовать)."""
        self.db()
        self.lease()
        RV.active(self.root, self.now)
        p = store.db_path(self.root)
        os.rename(p, p + ".moved")
        os.rename(p + ".moved", p)

    def test_cache_by_version(self):
        self.db()
        self.lease()
        real = RV.active_from_conn
        with mock.patch.object(RV, "active_from_conn", side_effect=real) as m:
            RV.active(self.root, self.now)
            RV.active(self.root, self.now)
            self.assertEqual(m.call_count, 1)
            c = store.open_rw(self.root)
            try:
                V.disable(c, "RL0001", "OWNER_DISABLE", self.now, cfg_of(self.root), root=None)
            finally:
                c.close()
            k = m.call_count
            v = RV.active(self.root, self.now)
            RV.active(self.root, self.now)
            self.assertEqual(m.call_count, k + 1)
        self.assertEqual((v["ver"], v["rules"]), ("R-0003", []))

    def test_invalid_rule_not_applied_on_read(self):
        """Неизвестный вид в действующей версии отклоняется при чтении (в rules не попадает, есть в invalid)."""
        self.db(with_rule=False)
        self.lease()
        c = store.open_rw(self.root)
        try:
            with store.tx(c):
                store2.append_row(c, "rule_rows", {"rule_id": "RL0009", "rev": 1, "kind": "size_cap", "status": "active", "from_lesson": "L-0009",
                                                   "env_norm": "mainnet", "body": {"cond": [{"feature": "spread_bps", "op": ">=", "value": 1}],
                                                                                   "param": {}, "scope": {"kinds": KINDS, "side": "with_dir"}},
                                                   "ts": store2.iso(self.now)})
                store2.append_row(c, "rulebook_versions", {"ver": "R-0002", "parent": "R-0001", "reason_code": "PROMOTE", "rule_ids": ["RL0009"],
                                                           "ts": store2.iso(self.now)})
        finally:
            c.close()
        v = RV.active(self.root, self.now)
        self.assertEqual((v["rules"], v["invalid"]), ([], ["RL0009"]))


def write_events(root, evs):
    p = paths.bus_events(root=root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for e in evs:
            f.write(json.dumps(e) + "\n")


def ev(seq, sym, kind, ts, **d):
    dd = {"kind": kind, "dir": "up", "spread_bps": 3.0}
    dd.update(d)
    return {"seq": seq, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)), "type": "scanner_signal", "pri": "wake", "sym": sym, "d": dd}


class TestResolveEvent(Base):
    def test_event_seq_valid_is_used(self):
        n = self.now
        write_events(self.root, [ev(1, "ETHUSDT", "range_5m", n - 100), ev(2, "ETHUSDT", "oi_spike", n - 10)])
        self.assertEqual(RV.resolve_event(self.root, "ETHUSDT", 1, n, KINDS)["seq"], 1)
        self.assertEqual(RV.resolve_event(self.root, "ETHUSDT", None, n, KINDS)["seq"], 2)

    def test_event_seq_rejected_falls_back(self):
        n = self.now
        write_events(self.root, [ev(1, "BTCUSDT", "range_5m", n - 50), ev(2, "ETHUSDT", "range_5m", n - 2000), ev(3, "ETHUSDT", "idea_stale", n - 5),
                                 ev(4, "ETHUSDT", "hot_enter", n - 300), ev(5, "ETHUSDT", "patrol", n - 3)])
        for bad in (1, 2, 3, 5, 99, True, "4"):                     # чужая монета, старше 900 с, idea_stale, вид вне scope, нет такого, не int
            self.assertEqual(RV.resolve_event(self.root, "ETHUSDT", bad, n, KINDS)["seq"], 4, bad)

    def test_never_idea_stale_level_even_if_listed(self):
        n = self.now
        write_events(self.root, [ev(1, "ETHUSDT", "idea_stale", n - 5), ev(2, "ETHUSDT", "idea_level", n - 4)])
        self.assertIsNone(RV.resolve_event(self.root, "ETHUSDT", 1, n, KINDS + ["idea_stale", "idea_level"]))
        self.assertIsNone(RV.resolve_event(self.root, "ETHUSDT", None, n, ["*"]))

    def test_old_missing_and_no_kinds(self):
        n = self.now
        self.assertIsNone(RV.resolve_event(self.root, "ETHUSDT", None, n, KINDS))           # шины нет
        write_events(self.root, [ev(1, "ETHUSDT", "range_5m", n - 901)])
        self.assertIsNone(RV.resolve_event(self.root, "ETHUSDT", 1, n, KINDS))
        self.assertIsNotNone(RV.resolve_event(self.root, "ETHUSDT", 1, n, KINDS, max_age_s=1000))
        self.assertIsNone(RV.resolve_event(self.root, "ETHUSDT", None, n, []))
        write_events(self.root, [{"seq": 2, "ts": "x", "type": "scanner_signal", "sym": "ETHUSDT", "d": {"kind": "range_5m"}}, "garbage"])
        self.assertIsNone(RV.resolve_event(self.root, "ETHUSDT", 2, n, KINDS))

    def test_max_age_from_config(self):
        n = self.now
        write_events(self.root, [ev(1, "ETHUSDT", "range_5m", n - 899)])
        self.assertEqual(RV.resolve_event(self.root, "ETHUSDT", None, n, KINDS)["seq"], 1)
        os.remove(os.path.join(self.root, "config", "learnloop.json"))
        self.assertIsNone(RV.resolve_event(self.root, "ETHUSDT", None, n, KINDS))           # порог не прочитан — события нет

    def test_long_tail(self):
        """Свежее событие монеты в начале длинного хвоста (больше одного куска чтения) находится."""
        n = self.now
        evs = [ev(1, "ETHUSDT", "range_5m", n - 800)] + [ev(i, "SYM%dUSDT" % i, "range_5m", n - 700 + i * 0.1) for i in range(2, 2500)]
        write_events(self.root, evs)
        self.assertGreater(os.path.getsize(paths.bus_events(root=self.root)), RV.TAIL_CHUNK)
        self.assertEqual(RV.resolve_event(self.root, "ETHUSDT", None, n, KINDS)["seq"], 1)

    def rotated(self, evs, mtime):
        name = "events-%s.jsonl" % time.strftime("%Y%m%d", time.gmtime(mtime))
        p = paths.bus(name, root=self.root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            for e in evs:
                f.write(json.dumps(e) + "\n")
        os.utime(p, (mtime, mtime))
        return p

    def test_previous_rotated_only_if_recent(self):
        n = self.now
        write_events(self.root, [ev(9, "BTCUSDT", "range_5m", n - 5)])
        p = self.rotated([ev(5, "ETHUSDT", "range_5m", n - 200)], n - 150)
        self.assertEqual(RV.resolve_event(self.root, "ETHUSDT", None, n, KINDS)["seq"], 5)
        os.utime(p, (n - 1000, n - 1000))                       # последняя запись старше порога: файл не читается (старые файлы не сканируются)
        self.assertIsNone(RV.resolve_event(self.root, "ETHUSDT", None, n, KINDS))
        older = self.rotated([ev(3, "ETHUSDT", "range_5m", n - 100)], n - 3 * 86400)    # не последний ротированный: не читается никогда
        os.utime(older, (n, n))
        os.utime(p, (n - 100, n - 100))
        self.assertEqual(RV.resolve_event(self.root, "ETHUSDT", None, n, KINDS)["seq"], 5)

    def test_bus_emit_roundtrip(self):
        """Событие, записанное настоящим bus.emit, разрешается (формат журнала шины)."""
        from tb.core import bus
        seq = bus.emit("scanner_signal", {"kind": "range_5m", "dir": "up", "spread_bps": 6.0, "range_5m_pct": 1.2}, sym="ETHUSDT", root=self.root)
        e = RV.resolve_event(self.root, "ETHUSDT", seq, time.time(), KINDS)
        self.assertEqual((e["seq"], e["d"]["spread_bps"]), (seq, 6.0))


class TestEventWhy(Base):
    """Причина события записи rule_eval (resolve_event_why): событие то же, что у resolve_event; причина только для учёта."""

    def why(self, seq, kinds=KINDS):
        r = RV.resolve_event_why(self.root, "ETHUSDT", seq, self.now, kinds)
        self.assertEqual(r["event"], RV.resolve_event(self.root, "ETHUSDT", seq, self.now, kinds))
        return r["why"], r["kind"], (r["event"] or {}).get("seq")

    def test_in_scope_ok(self):
        write_events(self.root, [ev(1, "ETHUSDT", "hot_enter", self.now - 100)])
        self.assertEqual(self.why(1), ("ok", "hot_enter", 1))
        self.assertEqual(self.why(None), ("ok", "hot_enter", 1))

    def test_patrol_out_of_scope(self):
        write_events(self.root, [ev(88080, "ETHUSDT", "patrol", self.now - 30), ev(88081, "ETHUSDT", "book_wall", self.now - 20)])
        self.assertEqual(self.why(88080), ("out_of_scope", "patrol", None))
        self.assertEqual(self.why(88081), ("out_of_scope", "book_wall", None))
        self.assertEqual(self.why(None), ("out_of_scope", "book_wall", None))       # chat: последнее событие монеты вне области

    def test_hot_enter_older_than_max_age_stale(self):
        write_events(self.root, [ev(5, "ETHUSDT", "hot_enter", self.now - 1500), ev(6, "BTCUSDT", "range_5m", self.now - 10)])
        self.assertEqual(self.why(5), ("stale", "hot_enter", None))
        self.assertEqual(self.why(None), ("no_event", None, None))                  # без event_seq старое событие не ищется

    def test_no_event(self):
        self.assertEqual(self.why(None), ("no_event", None, None))                  # шины нет
        write_events(self.root, [ev(1, "BTCUSDT", "range_5m", self.now - 10), ev(2, "ETHUSDT", "idea_stale", self.now - 5)])
        for seq in (1, 2, 99, None):                                                # чужая монета, idea_stale, нет такого, нет события монеты
            self.assertEqual(self.why(seq), ("no_event", None, None), seq)
        self.assertEqual(self.why(1, kinds=[]), ("no_event", None, None))           # видов нет (конфиг)

    def test_fallback_to_fresh_in_scope_is_ok(self):
        """Событие задания вне области, но у монеты есть свежее событие вида из области: правила применяются к нему (как прежде) -> ok."""
        write_events(self.root, [ev(1, "ETHUSDT", "range_5m", self.now - 200), ev(2, "ETHUSDT", "patrol", self.now - 5)])
        self.assertEqual(self.why(2), ("ok", "range_5m", 1))


class TestRuleEvalWhy(Base):
    """Учёт причин по rule_evals: RULES_NO_EVENT только по no_event и stale (+ прежние no_event=1), покрытие — доля вне области."""

    def rows(self, c, spec):
        rows = []
        for i, (why, kind, role) in enumerate(spec):
            rows.append({"rec_seq": i + 1, "rule_id": "", "ts": int(self.now) - 60, "role": role, "event_seq": 7 if why == "ok" else None,
                         "no_event": 0 if why == "ok" else 1, "hit": 0, "evaluated": 0, "missing": 0, "event_why": why, "event_kind": kind})
        store2.insert_rows(c, "rule_evals", rows)

    def test_counts_and_coverage(self):
        self.db(with_rule=False)
        c = store.open_rw(self.root)
        try:
            store2.migrate_re_why(c)
            self.rows(c, [("ok", "hot_enter", "entry_scout")] * 4 + [("out_of_scope", "patrol", "entry_scout")] * 3
                      + [("out_of_scope", "book_wall", "chat")] + [("stale", "hot_enter", "entry_scout"), ("no_event", None, "chat"),
                                                                  (None, None, "entry_scout")])
            w = RV.rule_eval_why(c, self.now, 7 * 86400)
        finally:
            c.close()
        self.assertEqual(w["n"], 11)
        self.assertEqual(w["fail"], 3)                                  # stale + no_event + прежняя строка no_event=1; out_of_scope не считается
        self.assertEqual(w["why"], {"ok": 4, "no_event": 1, "out_of_scope": 4, "stale": 1, "legacy": 1})
        cov = w["coverage"]
        self.assertEqual((cov["n"], cov["in_scope"], cov["out_of_scope"], cov["by_kind"]), (10, 6, 4, {"patrol": 3, "book_wall": 1}))
        self.assertEqual(cov["share"], 0.4)
        self.assertEqual(RV.coverage_txt(cov), "вне области правил: 40% (patrol 3, book_wall 1) из 10 решений")

    def test_without_columns_all_legacy(self):
        self.db(with_rule=False)
        c = store.open_rw(self.root)
        try:
            store2.insert_rows(c, "rule_evals", [{"rec_seq": 1, "rule_id": "", "ts": int(self.now), "no_event": 1, "hit": 0, "evaluated": 0, "missing": 0},
                                                 {"rec_seq": 2, "rule_id": "", "ts": int(self.now), "no_event": 0, "hit": 0, "evaluated": 0, "missing": 0}])
            w = RV.rule_eval_why(c, self.now, 86400)
        finally:
            c.close()
        self.assertEqual((w["n"], w["fail"], w["why"]["legacy"], w["coverage"]["n"], w["coverage"]["share"]), (2, 1, 2, 0, None))
        self.assertIsNone(RV.coverage_txt(w["coverage"]))

    def test_migration_idempotent(self):
        self.db(with_rule=False)
        c = store.open_rw(self.root)
        try:
            self.assertFalse(store2.has_re_why(c))
            store2.insert_rows(c, "rule_evals", [{"rec_seq": 1, "rule_id": "", "ts": 1, "no_event": 1, "hit": 0, "evaluated": 0, "missing": 0}])
            self.assertEqual(store2.migrate_re_why(c), 1)
            self.assertEqual(store2.migrate_re_why(c), 1)
            self.assertTrue(store2.has_re_why(c))
            cols = [r[1] for r in c.execute("PRAGMA table_info(rule_evals)").fetchall()]
            self.assertEqual((cols.count("event_why"), cols.count("event_kind")), (1, 1))
            self.assertEqual(c.execute("SELECT event_why, event_kind FROM rule_evals").fetchall(), [(None, None)])   # старые строки: NULL
            self.assertEqual(c.execute("SELECT v FROM meta WHERE k=?", ("knowledge_re_why_ver",)).fetchone()[0], "1")
            c.execute("DELETE FROM meta WHERE k='knowledge_re_why_ver'")                     # ключ потерян, колонки есть: без повторного ALTER
            self.assertEqual(store2.migrate_re_why(c), 1)
            self.assertTrue(store2.ready(c))
        finally:
            c.close()


class TestRuleEvalFields(Base):
    def test_fields(self):
        self.db()
        self.lease()
        view = RV.active(self.root, self.now)
        e = ev(7, "ETHUSDT", "range_5m", self.now)
        from tb.memory import features as MF
        res = LR.apply(view, {"ok": True, "side": "long"}, MF.rule_features(dict(e, d=dict(e["d"], spread_bps=9.0)), {"side": "long"}, {}))
        f = RV.rule_eval_fields(view, res, {"role": "chat", "event": e, "plan": "PL-0001-ETHUSDT-01", "cands_ok_n": 2})
        self.assertEqual(tuple(sorted(f)), tuple(sorted(RV.RULE_EVAL_KEYS)))
        self.assertEqual(f["shadow_hits"], [])                    # вторая итерация (задача 31): поле есть, без правил trial — пусто
        self.assertEqual((f["event_seq"], f["rule_hits"], f["rule_eval_ids"], f["feat_missing_ids"], f["rulebook_ver"]), (7, ["RL0001"], ["RL0001"], [], "R-0002"))
        res = LR.apply(view, {"ok": True}, MF.rule_features(None, {"side": "long"}, {}))
        f = RV.rule_eval_fields(view, res, {"role": "entry_scout", "event_seq": None, "cands_ok_n": True})
        self.assertEqual((f["event_seq"], f["feat_missing_ids"], f["cands_ok_n"]), (None, ["RL0001"], None))
        self.assertEqual((f["event_why"], f["event_kind"]), (None, None))
        f = RV.rule_eval_fields(view, res, {"role": "entry_scout", "event": None, "event_why": "out_of_scope", "event_kind": "patrol"})
        self.assertEqual((f["event_seq"], f["event_why"], f["event_kind"]), (None, "out_of_scope", "patrol"))
        f = RV.rule_eval_fields(view, res, {"role": "chat", "event": e})
        self.assertEqual((f["event_why"], f["event_kind"]), ("ok", "range_5m"))


class TestLayers(unittest.TestCase):
    BANNED = ("tb.learnloop", "tb.venues", "tb.executor", "tb.control", "tb.llm", "tb.brain", "tb.gateway", "tb.dash", "tb.dash2")

    def test_rulebook_view_closure(self):
        hit = lambda src, m: sorted(n for n in TL.imports_of(src, m) if TL.under(n, self.BANNED))      # noqa: E731
        self.assertEqual(TL.closure_hits(["tb.knowledge.rulebook_view", "tb.logic.learn_rules"], hit, TL.disk_source), {})

    def test_brain_import_stays_clean(self):
        """Импорт rulebook_view из мозга не тянет биржу и исполнитель транзитивно (как test_brain_transitively_clean)."""
        hit = lambda src, m: sorted(n for n in TL.imports_of(src, m) if TL.under(n, TL.FORBIDDEN_FROM_LAYERS))      # noqa: E731
        self.assertEqual(TL.closure_hits(["tb.knowledge.rulebook_view"], hit, TL.disk_source), {})
        src = TL.read(os.path.join(TL.SRC, "knowledge", "rulebook_view.py"))
        self.assertNotIn("open_rw", src)
        self.assertFalse([x for x in TL.string_literals(src) if ".config" in x])


if __name__ == "__main__":
    unittest.main()
