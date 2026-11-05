"""tb.learnloop.exits_versions (R4, задача 13; Requirements 7.1–7.7, 2.7, 8.3, 8.6; свойства 10 и 19).

Проверяется: `X-0001` из пола и идемпотентность `init`; продвижение закрытого испытания `promote` — версия `PROMOTE`, снимок и аренда
с новыми `ver`/`policy_h` (сначала снимок, затем аренда), `exitdoc.current` сразу видит `OK`, событие `exit_policy_promoted`, цепочка
`exit_versions` цела; ожидание `READY` без сторожа и без свежей проверенной копии, `STALE` через `promotion.wait_days`; не больше одной
версии за проход и `BASE_CHANGED` для второго закрытого `promote`; версия `FLOOR_CHANGED` при смене пола и закрытие открытых испытаний;
откат (`ROLLBACK` — политика предыдущей версии, снимок и аренда `by="rollback"`, чёрный список, событие); ошибка записи снимка/аренды
не оставляет аренду с новым хешем при старом снимке и исправляется повтором; продвижение и откат не меняют `ctx_ver`/`rules_ver`.
Временные корни, фейковые часы, сети нет."""
import json
import os
import shutil
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.core import bus
from tb.core import exitdoc as XD
from tb.knowledge import store2
from tb.labels import policies as LP
from tb.labels import store
from tb.learnloop import exits_conf as EC
from tb.learnloop import exits_trial as ET
from tb.learnloop import exits_versions as XV
from tb.logic import exitpol as XP

NOW = 1793750400                     # 2026-11-04T00:00:00Z
DAY = 86400
CFG_FILES = ("exits.json", "learnloop.json")


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        for name in CFG_FILES:
            shutil.copy(os.path.join(H.PROJECT, "config", name), os.path.join(self.root, "config", name))
        self.conn = store.open_rw(self.root)
        self.addCleanup(self.conn.close)
        self.assertEqual(store2.migrate_x1(self.conn, self.root, backup=False, now=NOW), 1)
        store2.migrate_v2a(self.conn, self.root, enabled=True, backup=False, now=NOW)
        self.cfg = EC.load_strict(self.root)
        self.floor = EC.current_floor(self.root)
        self.assertEqual(XV.init(self.conn, self.floor, NOW), "X-0001")
        XD.clear_cache()
        # свежая проверенная копия базы (готовность продвижения, Requirement 7.1)
        store2.make_backup(self.root, "daily", now=NOW)
        self.watch(NOW)

    # ---- данные
    def watch(self, ts=NOW, obj=None):
        doc = obj if obj is not None else {"ts": int(ts), "points": {}}
        self.conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('exits_watch', ?)", (json.dumps(doc),))
        self.conn.commit()

    def no_watch(self):
        self.conn.execute("DELETE FROM meta WHERE k='exits_watch'")
        self.conn.commit()

    def cand(self, vid="L1"):
        return dict([v for v in EC.variants(self.cfg, self.floor) if v["id"] == vid][0])

    def open_trial(self, vid="L1", now=NOW):
        return ET.register(self.conn, self.cand(vid), None, self.cfg, now, root=self.root)

    def close_promote(self, tid, numbers=None, now=NOW):
        nums = {"n_blocks": 40, "mean_d": 0.12, "p": 0.001, "brain_lb": 0.02, "tail_ratio": 0.9,
                "cut_share": 0.05, "amb_share": 0.1, "b": now - 3600, "cut_ts": now - 3600}
        nums.update(numbers or {})
        with store.tx(self.conn):
            store2.update_row(self.conn, "exit_trials", tid,
                              {"state": ET.CLOSED, "verdict": ET.PROMOTE, "numbers": nums, "cut_ts": now - 3600, "closed_ts": now - 3600})
        return ET.get(self.conn, tid)

    def pending(self, vid="L1", numbers=None):
        tid = self.open_trial(vid)
        self.close_promote(tid, numbers)
        return tid

    def vers(self):
        return [(v["ver"], v["reason_code"], v["parent"]) for v in XV.versions(self.conn)]

    def ctx(self):
        """`ctx_ver` теневых политик: входы только из конфигов исполнителя/профиля риска (Requirement 14.1)."""
        return LP.ctx_ver(LP._inputs(self.root, {})[0])

    def config_bytes(self):
        out = {}
        for name in os.listdir(os.path.join(self.root, "config")):
            with open(os.path.join(self.root, "config", name), "rb") as f:
                out[name] = f.read()
        return out


# ---------------------------------------------------------------- init и версия пола

class TestInit(Base):
    def test_init_floor_version(self):
        self.assertEqual(self.vers(), [("X-0001", "INIT", None)])
        row = XV.last_version(self.conn)
        self.assertEqual(row["policy"], XP.normalize(self.floor, self.floor))
        self.assertEqual(row["policy_h"], XP.policy_hash(self.floor))
        self.assertEqual(row["floor_h"], XP.policy_hash(self.floor))
        self.assertTrue(store2.verify_chain(self.conn, "exit_versions")["ok"])

    def test_init_idempotent(self):
        self.assertEqual(XV.init(self.conn, self.floor, NOW + 60), "X-0001")
        self.assertEqual(len(XV.versions(self.conn)), 1)


# ---------------------------------------------------------------- продвижение

class TestPromote(Base):
    def test_promote_writes_version_snapshot_lease_and_event(self):        # Requirement 7.1, 7.4; свойство 19
        tid = self.pending("L1")
        before_cfg, before_ctx = self.config_bytes(), self.ctx()
        ver = XV.promote(self.conn, self.root, tid, NOW)
        self.assertEqual(ver, "X-0002")
        self.assertEqual(self.vers(), [("X-0001", "INIT", None), ("X-0002", "PROMOTE", "X-0001")])
        row = XV.last_version(self.conn)
        self.assertEqual(row["numbers"]["trial"], tid)
        self.assertEqual(row["numbers"]["var"], "L1")
        self.assertEqual(row["policy_h"], XP.policy_hash(row["policy"]))
        self.assertEqual(XP.validate(row["policy"]), [])
        self.assertTrue(store2.verify_chain(self.conn, "exit_versions")["ok"])
        full = store2.verify(self.root)
        self.assertTrue(full["chains"]["exit_versions"]["ok"], full)
        # снимок
        with open(XD.snapshot_path(self.root), encoding="utf-8") as f:
            snap = json.load(f)
        self.assertEqual(snap["schema"], XD.SNAPSHOT_SCHEMA)
        self.assertEqual((snap["ver"], snap["policy_h"], snap["floor_h"]), ("X-0002", row["policy_h"], XP.policy_hash(self.floor)))
        self.assertEqual(XP.validate(snap["policy"]), [])
        # аренда в той же операции
        with open(XD.lease_path(self.root), encoding="utf-8") as f:
            lease = json.load(f)
        self.assertEqual((lease["ver"], lease["policy_h"], lease["by"]), ("X-0002", row["policy_h"], "promote"))
        # исполнитель сразу видит OK (свойство 19)
        policy, info = XD.current(self.root, NOW, self.floor)
        self.assertEqual((info["ver"], info["why"]), ("X-0002", "OK"))
        self.assertEqual(policy, row["policy"])
        # событие шины info
        evs = [e for e in bus.read(root=self.root) if e["type"] == "exit_policy_promoted"]
        self.assertEqual(len(evs), 1)
        self.assertEqual((evs[0]["d"]["ver"], evs[0]["d"]["trial"], evs[0]["d"]["var"], evs[0]["pri"]),
                         ("X-0002", tid, "L1", "info"))
        # строки без ошибок
        self.assertEqual([r[0] for r in self.conn.execute("SELECT code FROM learn_events WHERE step=? ORDER BY seq", (XV.STEP,))],
                         ["INIT", "PROMOTE"])
        # свойство 10: ctx_ver и rules_ver (конфиги) не меняются
        self.assertEqual(self.ctx(), before_ctx)
        self.assertEqual(self.config_bytes(), before_cfg)

    def test_promote_idempotent(self):
        tid = self.pending("L1")
        self.assertEqual(XV.promote(self.conn, self.root, tid, NOW), "X-0002")
        n = len(XV.versions(self.conn))
        ev = len([e for e in bus.read(root=self.root) if e["type"] == "exit_policy_promoted"])
        self.assertEqual(XV.promote(self.conn, self.root, tid, NOW + 60), "X-0002")
        self.assertEqual(len(XV.versions(self.conn)), n)
        self.assertEqual(len([e for e in bus.read(root=self.root) if e["type"] == "exit_policy_promoted"]), ev)
        self.assertEqual(XD.current(self.root, NOW + 60, self.floor)[1]["ver"], "X-0002")

    def test_promote_requires_auto_mode(self):
        tid = self.pending("L1")
        cfg = dict(self.cfg, promotion=dict(self.cfg["promotion"], mode="off"))
        self.assertIsNone(XV.promote(self.conn, self.root, tid, NOW, cfg=cfg))
        self.assertEqual(len(XV.versions(self.conn)), 1)
        rep = XV.run(self.conn, self.root, cfg, NOW)
        self.assertEqual(rep["code"], XV.OFF)
        self.assertFalse(os.path.exists(XD.snapshot_path(self.root)))

    def test_promote_wrong_state_refused(self):
        tid = self.open_trial("L1")
        with self.assertRaises(XV.PromoteRefused):
            XV.promote(self.conn, self.root, tid, NOW)


class TestReadiness(Base):
    def test_no_watch_waits_ready(self):                                   # Requirement 7.1, 7.6
        self.no_watch()
        tid = self.pending("L1")
        self.assertIsNone(XV.promote(self.conn, self.root, tid, NOW))
        t = ET.get(self.conn, tid)
        self.assertEqual(t["verdict"], ET.PROMOTE)
        self.assertEqual(t["numbers"]["wait"], XV.READY)
        self.assertEqual(t["numbers"]["wait_since_ts"], NOW)
        self.assertEqual(len(XV.versions(self.conn)), 1)
        self.assertFalse(os.path.exists(XD.snapshot_path(self.root)))
        self.assertFalse(os.path.exists(XD.lease_path(self.root)))
        # повтор с той же причиной не сдвигает начало ожидания
        self.assertIsNone(XV.promote(self.conn, self.root, tid, NOW + 60))
        self.assertEqual(ET.get(self.conn, tid)["numbers"]["wait_since_ts"], NOW)

    def test_stale_watch_is_not_ready(self):
        self.watch(NOW - self.cfg["promotion"]["watch_fresh_h"] * 3600 - 1)
        tid = self.pending("L1")
        rd = XV.promotion_ready(self.conn, self.cfg, NOW, root=self.root)
        self.assertFalse(rd["ok"])
        self.assertEqual(rd["failed"], ["WATCH"])
        self.assertIsNone(XV.promote(self.conn, self.root, tid, NOW))
        self.assertEqual(ET.get(self.conn, tid)["numbers"]["wait"], XV.READY)

    def test_no_verified_backup_waits(self):
        shutil.rmtree(store2.backups_dir(self.root), True)
        tid = self.pending("L1")
        rd = XV.promotion_ready(self.conn, self.cfg, NOW, root=self.root)
        self.assertFalse(rd["ok"])
        self.assertEqual(rd["failed"], ["BACKUP"])
        self.assertIsNone(XV.promote(self.conn, self.root, tid, NOW))

    def test_watch_state_formats(self):
        for doc in (NOW, str(NOW), {"last_ts": NOW}, {"last": {"ts": NOW}}, {"checked_ts": NOW, "points": {}}):
            self.watch(obj=doc)
            self.assertTrue(XV.promotion_ready(self.conn, self.cfg, NOW, root=self.root)["ok"], doc)

    def test_stale_after_wait_days(self):                                  # Requirement 7.6
        self.no_watch()
        tid = self.pending("L1")
        self.assertIsNone(XV.promote(self.conn, self.root, tid, NOW))
        later = NOW + (self.cfg["promotion"]["wait_days"] + 1) * DAY
        self.assertIsNone(XV.promote(self.conn, self.root, tid, later))
        t = ET.get(self.conn, tid)
        self.assertEqual(t["verdict"], ET.INCONCLUSIVE)
        self.assertEqual(t["numbers"]["code"], "STALE")
        self.assertNotIn("wait", t["numbers"])
        self.assertEqual(len(XV.versions(self.conn)), 1)
        self.assertFalse(os.path.exists(XD.snapshot_path(self.root)))


# ---------------------------------------------------------------- проход

class TestRun(Base):
    def test_one_version_per_pass_second_base_changed(self):               # Requirement 7.3, 6.8
        t1 = self.pending("L1")
        t2 = self.pending("L2")
        rep = XV.run(self.conn, self.root, self.cfg, NOW)
        self.assertEqual(rep["code"], "OK")
        self.assertEqual([p["trial"] for p in rep["promoted"]], [t1])
        self.assertEqual(self.vers(), [("X-0001", "INIT", None), ("X-0002", "PROMOTE", "X-0001")])
        other = ET.get(self.conn, t2)
        self.assertEqual(other["verdict"], ET.INCONCLUSIVE)
        self.assertEqual(other["numbers"]["code"], ET.BASE_CHANGED)
        self.assertEqual(rep["rejected"], [{"trial": t2, "code": ET.BASE_CHANGED}])
        # повтор прохода ничего не создаёт (идемпотентность)
        n = len(XV.versions(self.conn))
        self.assertEqual(XV.run(self.conn, self.root, self.cfg, NOW + 60)["promoted"], [])
        self.assertEqual(len(XV.versions(self.conn)), n)

    def test_run_not_ready_marks_all_waiting(self):
        self.no_watch()
        t1 = self.pending("L1")
        t2 = self.pending("L2")
        rep = XV.run(self.conn, self.root, self.cfg, NOW)
        self.assertEqual(rep["promoted"], [])
        self.assertEqual(sorted(x["trial"] for x in rep["waiting"]), sorted([t1, t2]))
        for tid in (t1, t2):
            self.assertEqual(ET.get(self.conn, tid)["numbers"]["wait"], XV.READY)


# ---------------------------------------------------------------- смена пола

class TestFloorChanged(Base):
    F2AP = {"enabled": True, "be_r": 0.9, "trail_activate_r": 1.0, "trail_lock_frac": 0.6, "min_profit_r": 0.35}

    def test_floor_changed_creates_floor_version(self):                   # Requirement 2.7
        tid = self.pending("L1")
        self.assertEqual(XV.promote(self.conn, self.root, tid, NOW), "X-0002")
        f2 = XP.floor(self.F2AP)
        open_tid = self.open_trial("L2", now=NOW + 60)
        ver = XV.floor_changed(self.conn, self.root, NOW + 120, floor_policy=f2)
        self.assertEqual(ver, "X-0003")
        row = XV.last_version(self.conn)
        self.assertEqual((row["reason_code"], row["parent"]), ("FLOOR_CHANGED", "X-0002"))
        self.assertEqual(row["policy"], XP.normalize(f2, f2))
        self.assertEqual(row["numbers"]["from"], "X-0002")
        self.assertTrue(store2.verify_chain(self.conn, "exit_versions")["ok"])
        self.assertEqual(XD.current(self.root, NOW + 120, f2)[1]["ver"], "X-0003")
        self.assertEqual(XD.current(self.root, NOW + 120, f2)[1]["why"], "OK")
        self.assertEqual(ET.get(self.conn, open_tid)["verdict"], ET.INCONCLUSIVE)
        self.assertEqual(ET.get(self.conn, open_tid)["numbers"]["code"], ET.FLOOR_CHANGED)
        # повтор при том же поле ничего не создаёт
        self.assertIsNone(XV.floor_changed(self.conn, self.root, NOW + 180, floor_policy=f2))
        self.assertEqual(len(XV.versions(self.conn)), 3)

    def test_looser_floor_keeps_version(self):
        tid = self.pending("L3")                                          # L3 строже пола по keep
        self.assertEqual(XV.promote(self.conn, self.root, tid, NOW), "X-0002")
        f2 = XP.floor({"enabled": True, "be_r": 0.9, "trail_activate_r": 1.0, "trail_lock_frac": 0.5, "min_profit_r": 0.15})
        self.assertIsNone(XV.floor_changed(self.conn, self.root, NOW + 60, floor_policy=f2))
        self.assertEqual(len(XV.versions(self.conn)), 2)


# ---------------------------------------------------------------- откат

class TestRollback(Base):
    def test_rollback_previous_policy_snapshot_lease_event(self):         # Requirement 8.3; свойство 19
        tid = self.pending("L1")
        self.assertEqual(XV.promote(self.conn, self.root, tid, NOW), "X-0002")
        active_policy = XV.last_version(self.conn)["policy"]
        before_cfg, before_ctx = self.config_bytes(), self.ctx()
        ver = XV.rollback(self.conn, self.root, "ROLLBACK", {"n": 120, "mean_r": -0.09, "p": 0.002}, NOW + 60)
        self.assertEqual(ver, "X-0003")
        row = XV.last_version(self.conn)
        self.assertEqual((row["reason_code"], row["parent"]), ("ROLLBACK", "X-0002"))
        self.assertEqual(row["policy"], XP.normalize(XV.version_row(self.conn, "X-0001")["policy"], self.floor))
        self.assertNotEqual(row["policy"], active_policy)
        self.assertTrue(store2.verify_chain(self.conn, "exit_versions")["ok"])
        with open(XD.lease_path(self.root), encoding="utf-8") as f:
            lease = json.load(f)
        self.assertEqual((lease["ver"], lease["policy_h"], lease["by"]), ("X-0003", row["policy_h"], "rollback"))
        policy, info = XD.current(self.root, NOW + 60, self.floor)
        self.assertEqual((info["ver"], info["why"]), ("X-0003", "OK"))
        self.assertEqual(policy, row["policy"])
        evs = [e for e in bus.read(root=self.root) if e["type"] == "exit_policy_rollback"]
        self.assertEqual(len(evs), 1)
        self.assertEqual((evs[0]["d"]["ver"], evs[0]["d"]["from"], evs[0]["d"]["code"], evs[0]["pri"]),
                         ("X-0003", "X-0002", "ROLLBACK", "info"))
        # откатанный вариант в чёрном списке на cooldown_days
        bl = self.conn.execute("SELECT var_id, var_ver, reason FROM exit_blacklist").fetchall()
        self.assertEqual(bl, [("L1", 1, "ROLLBACK")])
        # свойство 10
        self.assertEqual(self.ctx(), before_ctx)
        self.assertEqual(self.config_bytes(), before_cfg)

    def test_owner_rollback_uses_floor(self):
        tid = self.pending("L1")
        self.assertEqual(XV.promote(self.conn, self.root, tid, NOW), "X-0002")
        ver = XV.rollback(self.conn, self.root, "OWNER_ROLLBACK", None, NOW + 60)
        self.assertEqual(ver, "X-0003")
        self.assertEqual(XV.last_version(self.conn)["policy"], XP.normalize(self.floor, self.floor))
        self.assertEqual(XD.current(self.root, NOW + 60, self.floor)[1]["ver"], "X-0003")


# ---------------------------------------------------------------- ошибки записи

class TestPublishErrors(Base):
    def test_snapshot_error_leaves_lease_untouched(self):                 # «сначала снимок, затем аренда»
        tid = self.pending("L1")
        orig = XV._write_json

        def fail_snapshot(path, obj):
            if path == XD.snapshot_path(self.root):
                raise OSError("disk full")
            return orig(path, obj)

        with mock.patch.object(XV, "_write_json", side_effect=fail_snapshot):
            with self.assertRaises(OSError):
                XV.promote(self.conn, self.root, tid, NOW)
        self.assertFalse(os.path.exists(XD.snapshot_path(self.root)))
        self.assertFalse(os.path.exists(XD.lease_path(self.root)))          # аренда не получила новый хеш
        self.assertEqual(XV.last_version(self.conn)["ver"], "X-0002")       # версия записана, публикация — следующим проходом
        # повтор дозаписывает снимок и аренду (исправление сбоя публикации)
        self.assertEqual(XV.promote(self.conn, self.root, tid, NOW + 60), "X-0002")
        self.assertEqual(XD.current(self.root, NOW + 60, self.floor)[1]["why"], "OK")
        self.assertEqual(len(XV.versions(self.conn)), 2)

    def test_lease_error_never_leaves_new_hash_with_old_snapshot(self):
        tid = self.pending("L1")
        orig = XV._write_json

        def fail_lease(path, obj):
            if path == XD.lease_path(self.root):
                raise OSError("disk full")
            return orig(path, obj)

        with mock.patch.object(XV, "_write_json", side_effect=fail_lease):
            with self.assertRaises(OSError):
                XV.promote(self.conn, self.root, tid, NOW)
        self.assertTrue(os.path.exists(XD.snapshot_path(self.root)))
        self.assertFalse(os.path.exists(XD.lease_path(self.root)))
        _policy, info = XD.current(self.root, NOW, self.floor)
        self.assertIn(info["why"], ("LEASE", "HASH"))                      # пол, а не версия с чужим хешем
        # повтор исправляет аренду
        self.assertEqual(XV.promote(self.conn, self.root, tid, NOW + 60), "X-0002")
        self.assertEqual(XD.current(self.root, NOW + 60, self.floor)[1]["why"], "OK")

    def test_run_repairs_failed_publication(self):
        """Сбой публикации в проходе исправляется следующим проходом (испытание уже «применено», в очереди его нет)."""
        self.pending("L1")
        orig = XV._write_json

        def fail_snapshot(path, obj):
            if path == XD.snapshot_path(self.root):
                raise OSError("disk full")
            return orig(path, obj)

        with mock.patch.object(XV, "_write_json", side_effect=fail_snapshot):
            with self.assertRaises(OSError):
                XV.run(self.conn, self.root, self.cfg, NOW)
        self.assertFalse(os.path.exists(XD.snapshot_path(self.root)))
        rep = XV.run(self.conn, self.root, self.cfg, NOW + 60)
        self.assertEqual(rep["promoted"], [])                              # версия уже записана, новой нет
        self.assertEqual(XD.current(self.root, NOW + 60, self.floor)[1], {"ver": "X-0002", "policy_h": XV.last_version(self.conn)["policy_h"],
                                                                          "why": "OK"})
        self.assertEqual(len(XV.versions(self.conn)), 2)


# ---------------------------------------------------------------- готовность: режим и конфиг

class TestMode(Base):
    def test_run_disabled_config_does_nothing(self):
        tid = self.pending("L1")
        rep = XV.run(self.conn, self.root, EC.disabled(), NOW)
        self.assertEqual(rep["code"], "DISABLED")
        self.assertEqual(ET.get(self.conn, tid)["verdict"], ET.PROMOTE)
        self.assertEqual(len(XV.versions(self.conn)), 1)


if __name__ == "__main__":
    unittest.main()
