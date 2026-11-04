"""R4 задача 14: читатель политики выхода tb.core.exitdoc (Requirements 9.1, 8.6, 18.3; design.md «tb.core.exitdoc», свойство 19).

Проверяется: снимок и аренда после продвижения и отката дают OK; каждая причина отката к полу (LEASE, SNAPSHOT, HASH, SWITCH); кеш
снимка и аренды по mtime; битый и отсутствующий файл; смена пола не отменяет действующую версию, а запасной пол берётся новый;
исключений наружу нет.
"""
import json
import os
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb.core import exitdoc as XD
from tb.logic import exitpol as XP

AP = {"enabled": True, "be_r": 0.7, "trail_activate_r": 1.0, "trail_lock_frac": 0.5, "min_profit_r": 0.2, "stop_slip_bps": 5}
F = XP.floor(AP)
NOW = 1762300000.0
LEASE_H = 6
# нормализованная политика версии (как её пишет продвижение, Requirement 2.4)
POL = XP.normalize({"scope": "all", "partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]}, F)
H = XP.policy_hash(POL)
# та же политика с другим значением доли: длина JSON та же (проверка кеша по mtime, а не по размеру)
POL_B = XP.normalize({"scope": "all", "partial": [{"at_r": 1.0, "frac": 0.4, "exec": "maker"}]}, F)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tb_exitdoc_")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "runtime"))
        os.makedirs(os.path.join(self.root, "config"))
        self.write_cfg()
        XD.clear_cache()

    def tearDown(self):
        XD.clear_cache()

    # --- файлы
    def write_json(self, path, obj):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def write_cfg(self, hours=LEASE_H):
        self.write_json(os.path.join(self.root, "config", "exits.json"), {"schema": "tb.exits_cfg/1", "lease": {"lease_h": hours}})

    def snap(self, ver="X-0003", policy=None, policy_h=None, schema=XD.SNAPSHOT_SCHEMA, drop=()):
        pol = POL if policy is None else policy
        doc = {"schema": schema, "ver": ver, "policy": pol,
               "policy_h": H if policy_h is None else policy_h, "floor_h": XP.policy_hash(F), "ts": int(NOW)}
        for k in drop:
            doc.pop(k, None)
        self.write_json(XD.snapshot_path(self.root), doc)
        return doc

    def lease(self, ts=NOW, ver="X-0003", policy_h=H, by="promote"):
        doc = {"ts": ts, "ver": ver, "policy_h": policy_h, "by": by}
        self.write_json(XD.lease_path(self.root), doc)
        return doc

    def switch(self, apply):
        self.write_json(XD.switch_path(self.root), {"apply": apply, "ts": int(NOW), "actor": "OWNER"})

    def cur(self, now=NOW, floor_policy=F, **kw):
        return XD.current(self.root, now, floor_policy, **kw)

    def assert_floor(self, policy, info, floor_policy=F, why=None):
        self.assertEqual(policy, floor_policy)
        self.assertEqual(info["ver"], XD.FLOOR_VER)
        self.assertEqual(info["policy_h"], XP.policy_hash(floor_policy))
        if why is not None:
            self.assertEqual(info["why"], why)


class TestOk(Base):
    """Свойство 19: сразу после продвижения и отката (до прохода сторожа) действует новая версия."""

    def test_ok_after_promotion(self):
        self.snap()
        self.lease(by="promote")
        policy, info = self.cur()
        self.assertEqual(policy, POL)
        self.assertEqual(info, {"ver": "X-0003", "policy_h": H, "why": "OK"})

    def test_ok_after_rollback(self):
        pol = XP.normalize({}, F)
        h = XP.policy_hash(pol)
        self.snap(ver="X-0004", policy=pol, policy_h=h)
        self.lease(ver="X-0004", policy_h=h, by="rollback")
        policy, info = self.cur()
        self.assertEqual(policy, pol)
        self.assertEqual(info["ver"], "X-0004")
        self.assertEqual(info["policy_h"], h)
        self.assertEqual(info["why"], "OK")

    def test_switch_absent_and_true_apply(self):
        self.snap()
        self.lease()
        self.assertEqual(self.cur()[1]["why"], "OK")            # файла выключателя нет — применяем
        self.switch(True)
        self.assertEqual(self.cur()[1]["why"], "OK")

    def test_lease_boundary_and_small_future_skew(self):
        self.snap()
        self.lease(ts=NOW - LEASE_H * 3600)                     # ровно срок: ещё свежая
        self.assertEqual(self.cur()[1]["why"], "OK")
        self.lease(ts=NOW + 60)                                 # часы немного впереди (допуск)
        self.assertEqual(self.cur()[1]["why"], "OK")

    def test_info_shape(self):
        self.snap()
        self.lease()
        _policy, info = self.cur()
        self.assertEqual(sorted(info), ["policy_h", "ver", "why"])


class TestSwitch(Base):
    def test_switch_off(self):
        self.snap()
        self.lease()
        self.switch(False)
        self.assert_floor(*self.cur(), why="SWITCH")

    def test_switch_broken_or_foreign(self):
        self.snap()
        self.lease()
        for body in ('{oops', '[]', '{"apply": "yes"}', '42'):
            with open(XD.switch_path(self.root), "w", encoding="utf-8") as f:
                f.write(body)
            self.assert_floor(*self.cur(), why="SWITCH")


class TestSnapshot(Base):
    def test_missing_snapshot(self):
        self.lease()
        self.assert_floor(*self.cur(), why="SNAPSHOT")

    def test_unreadable_files(self):
        self.lease()
        for body in (b"\xff\xfe", b"not json", b"[]", b"42"):
            with open(XD.snapshot_path(self.root), "wb") as f:
                f.write(body)
            self.assert_floor(*self.cur(), why="SNAPSHOT")

    def test_foreign_file_schema(self):
        self.snap(schema="tb.exit_snapshot/2")
        self.lease()
        self.assert_floor(*self.cur(), why="SNAPSHOT")

    def test_policy_does_not_pass_schema(self):
        bad = {"schema": XP.SCHEMA, "be": {"r": 9.0}}           # be.r вне диапазона 0.3–1.5
        self.snap(policy=bad, policy_h=XP.policy_hash(bad))
        self.lease(policy_h=XP.policy_hash(bad))
        self.assert_floor(*self.cur(), why="SNAPSHOT")

    def test_snapshot_without_ver_or_hash(self):
        self.snap(drop=("ver",))
        self.lease()
        self.assert_floor(*self.cur(), why="SNAPSHOT")
        self.snap(drop=("policy_h",))
        self.lease()
        self.assert_floor(*self.cur(), why="SNAPSHOT")

    def test_policy_not_object(self):
        doc = {"schema": XD.SNAPSHOT_SCHEMA, "ver": "X-0003", "policy": [1, 2], "policy_h": H, "floor_h": H, "ts": int(NOW)}
        self.write_json(XD.snapshot_path(self.root), doc)
        self.lease()
        self.assert_floor(*self.cur(), why="SNAPSHOT")


class TestLease(Base):
    def test_missing_lease(self):
        self.snap()
        self.assert_floor(*self.cur(), why="LEASE")

    def test_unreadable_lease(self):
        self.snap()
        for body in (b"\xff", b"nope", b"[]", b"7"):
            with open(XD.lease_path(self.root), "wb") as f:
                f.write(body)
            self.assert_floor(*self.cur(), why="LEASE")

    def test_lease_without_numeric_ts(self):
        self.snap()
        self.write_json(XD.lease_path(self.root), {"ver": "X-0003", "policy_h": H, "by": "watch"})
        self.assert_floor(*self.cur(), why="LEASE")
        self.write_json(XD.lease_path(self.root), {"ts": "soon", "ver": "X-0003", "policy_h": H})
        self.assert_floor(*self.cur(), why="LEASE")

    def test_lease_stale(self):
        self.snap()
        self.lease(ts=NOW - LEASE_H * 3600 - 1)
        self.assert_floor(*self.cur(), why="LEASE")

    def test_lease_too_far_in_future(self):
        self.snap()
        self.lease(ts=NOW + XD.FUTURE_SLACK_S + 1)
        self.assert_floor(*self.cur(), why="LEASE")

    def test_config_unreadable_means_lease_unknown(self):
        """Без config/exits.json срок аренды неизвестен: числовых умолчаний в коде нет (Requirement 16.1) -> пол."""
        self.snap()
        self.lease()
        os.remove(os.path.join(self.root, "config", "exits.json"))
        self.assert_floor(*self.cur(), why="LEASE")
        with open(os.path.join(self.root, "config", "exits.json"), "w", encoding="utf-8") as f:
            f.write("{bad")
        self.assert_floor(*self.cur(), why="LEASE")
        self.write_cfg(hours=24)
        self.assertEqual(self.cur()[1]["why"], "OK")            # конфиг снова читается — новый срок действует

    def test_lease_h_argument_overrides_config(self):
        self.snap()
        self.lease(ts=NOW - 2 * 3600)
        self.assertEqual(self.cur(lease_h=3)[1]["why"], "OK")
        self.assert_floor(*self.cur(lease_h=1), why="LEASE")


class TestHash(Base):
    def test_snapshot_hash_disagrees_with_lease(self):
        other = XP.policy_hash(XP.normalize({}, F))
        self.snap()
        self.lease(policy_h=other, ver="X-0003")
        self.assert_floor(*self.cur(), why="HASH")

    def test_snapshot_hash_is_not_policy_hash(self):
        self.snap(policy_h="deadbeefdeadbeef")
        self.lease(policy_h="deadbeefdeadbeef")
        self.assert_floor(*self.cur(), why="HASH")

    def test_tampered_policy_breaks_hash(self):
        self.snap(policy=POL_B, policy_h=H)                      # хеш остался от POL
        self.lease(policy_h=H)
        self.assert_floor(*self.cur(), why="HASH")


class TestCache(Base):
    """Кеш по mtime: подмена содержимого без изменения подписи не видна, изменение подписи — видно."""

    def test_snapshot_cache_by_mtime(self):
        self.snap()
        self.lease()
        self.assertEqual(self.cur()[0], POL)
        path = XD.snapshot_path(self.root)
        size = os.stat(path).st_size
        old = os.stat(path)
        self.snap(ver="X-0009", policy=POL_B, policy_h=XP.policy_hash(POL_B))
        self.assertEqual(os.stat(path).st_size, size)            # размер тот же: подпись отличается только mtime
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))    # вернуть прежний mtime
        policy, info = self.cur()
        self.assertEqual(policy, POL)                            # из кеша: старый снимок, аренда сходится
        self.assertEqual(info["why"], "OK")
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns + 1000000000))   # новый mtime
        self.assert_floor(*self.cur(), why="HASH")               # прочитали POL_B, аренда осталась от POL

    def test_lease_cache_by_mtime(self):
        self.snap()
        self.lease()
        self.assertEqual(self.cur()[1]["why"], "OK")
        path = XD.lease_path(self.root)
        old = os.stat(path)
        size = old.st_size
        self.lease(ts=NOW - (LEASE_H + 1) * 3600)                # те же 10 цифр: размер не меняется
        self.assertEqual(os.stat(path).st_size, size)
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
        self.assertEqual(self.cur()[1]["why"], "OK")             # из кеша: свежая аренда
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns + 1000000000))
        self.assert_floor(*self.cur(), why="LEASE")              # прочитали просроченную аренду

    def test_clear_cache_sees_same_mtime_content(self):
        self.snap()
        self.lease()
        self.assertEqual(self.cur()[1]["why"], "OK")
        path = XD.snapshot_path(self.root)
        old = os.stat(path)
        self.snap(policy=POL_B, policy_h=XP.policy_hash(POL_B))
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns))
        self.assertEqual(self.cur()[0], POL)                     # без сброса кеша — старый снимок
        XD.clear_cache()
        self.assert_floor(*self.cur(), why="HASH")

    def test_returned_policy_is_copy(self):
        self.snap()
        self.lease()
        policy, _info = self.cur()
        policy["be"]["r"] = 1.4
        self.assertEqual(self.cur()[0], POL)                     # кеш и файл не испорчены


class TestFloorChange(Base):
    """Смена пола: действующая версия остаётся (Requirement 2.7), запасной пол — новый."""

    def test_snapshot_survives_floor_change(self):
        self.snap()
        self.lease()
        f2 = XP.floor(dict(AP, be_r=0.9, min_profit_r=0.35))
        policy, info = self.cur(floor_policy=f2)
        self.assertEqual(policy, POL)                            # снимок не нормализуется заново с новым полом
        self.assertEqual(info["policy_h"], H)
        self.assertEqual(info["why"], "OK")

    def test_fallback_returns_current_floor(self):
        bad = {"schema": XP.SCHEMA, "be": {"r": 9.0}}
        self.snap(policy=bad, policy_h=XP.policy_hash(bad))
        self.lease(policy_h=XP.policy_hash(bad))
        self.assert_floor(*self.cur(), why="SNAPSHOT")
        f2 = XP.floor(dict(AP, be_r=0.9, min_profit_r=0.35))
        self.assertNotEqual(f2, F)
        self.assert_floor(*self.cur(floor_policy=f2), floor_policy=f2, why="SNAPSHOT")


class TestRobust(Base):
    """Читатель не бросает: мусор в файлах, плохой now и плохой пол дают пол и причину."""

    def test_garbage_never_raises(self):
        for body in (b"\xff\xfe", b"not json", b"[]", b"42", b'{"ts": null}'):
            with open(XD.snapshot_path(self.root), "wb") as f:
                f.write(body)
            with open(XD.lease_path(self.root), "wb") as f:
                f.write(body)
            with open(XD.switch_path(self.root), "wb") as f:
                f.write(body)
            policy, info = self.cur(now="not-a-number", floor_policy=["bad floor"])
            self.assertIsInstance(policy, dict)
            self.assertIn(info["why"], XD.WHY)
            self.assertEqual(policy, {"schema": XP.SCHEMA})

    def test_missing_root_files(self):
        policy, info = self.cur()
        self.assertIsInstance(policy, dict)
        self.assertIn(info["why"], XD.WHY)

    def test_floor_none_gives_empty_floor(self):
        self.snap()
        self.lease()
        policy, info = self.cur(floor_policy=None)
        self.assertEqual(policy, POL)                            # снимок действует, пол не нужен
        self.assertEqual(info["why"], "OK")
        os.remove(XD.snapshot_path(self.root))
        policy, info = self.cur(floor_policy=None)
        self.assertEqual(policy, {"schema": XP.SCHEMA})          # пол не передан -> пустая политика (только начальный стоп)
        self.assertEqual(info["why"], "SNAPSHOT")
        self.assertEqual(info["policy_h"], XP.policy_hash({"schema": XP.SCHEMA}))


if __name__ == "__main__":
    unittest.main()
