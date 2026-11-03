"""tb.knowledge.rulebook_view (R2, задача 11): свойство 22 (нет безнадзорных правил: просроченная или отсутствующая аренда, маркер восстановления,
сигнал повреждения, повреждённая или отсутствующая база, apply=false и при повреждённой базе), соединение на один вызов (файл базы можно
переименовать сразу после вызова), кеш по версии, resolve_event по веткам (event_seq, чужая монета, старое событие, idea_stale/idea_level,
вид вне scope, нет события, предыдущий ротированный файл только при недавней ротации), rule_eval_fields без shadow_hits, слои. Сеть не используется."""
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
        self.assertNotIn("shadow_hits", f)
        self.assertEqual((f["event_seq"], f["rule_hits"], f["rule_eval_ids"], f["feat_missing_ids"], f["rulebook_ver"]), (7, ["RL0001"], ["RL0001"], [], "R-0002"))
        res = LR.apply(view, {"ok": True}, MF.rule_features(None, {"side": "long"}, {}))
        f = RV.rule_eval_fields(view, res, {"role": "entry_scout", "event_seq": None, "cands_ok_n": True})
        self.assertEqual((f["event_seq"], f["feat_missing_ids"], f["cands_ok_n"]), (None, ["RL0001"], None))


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
