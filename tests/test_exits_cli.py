"""tb.knowledge.exits_cli (R4, задача 21; Requirements 16.3–16.6, 7.7; design.md «tb.learnloop.exits_*», «Снимок и аренда»).

Проверяется: команды чтения (`status`, `versions`, `search`, `trials`, `brain`, `after`, `gate`), `apply on|off` без базы,
`rollback` (`OWNER_ROLLBACK` к полу: версия, снимок и аренда), `promotion auto|off` (`set_mode` меняет только `promotion.mode`,
остальной файл сохраняется), запись `learn_events` с `actor=OWNER`, отдельный обработчик `owner_rollback` для кнопки дашборда,
работа при недоступной базе. Временные корни, сети нет."""
import contextlib
import io
import json
import os
import shutil
import time
import unittest

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.core import exitdoc as XD
from tb.knowledge import exits_cli as CLI
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import exits_conf as CONF
from tb.learnloop import exits_trial as ET
from tb.learnloop import exits_versions as XV
from tb.logic import exitpol as XP

NOW = 1793750400                     # 2026-11-04T00:00:00Z
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
        self.cfg = CONF.load_strict(self.root)
        self.floor = CONF.current_floor(self.root)
        self.assertEqual(XV.init(self.conn, self.floor, NOW), "X-0001")
        XD.clear_cache()

    # ---- помощники
    def cfg_text(self):
        with open(os.path.join(self.root, "config", "exits.json"), encoding="utf-8") as f:
            return f.read()

    def counts(self):
        return tuple(self.conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                     for t in ("exit_versions", "exit_trials", "learn_events", "meta"))

    def run_cli(self, argv):
        """-> (код, JSON-объект | None, сырой вывод)."""
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            code = CLI.main(list(argv), root=self.root)
        raw = buf.getvalue().strip()
        obj = json.loads(raw) if raw[:1] == "{" else None
        return code, obj, raw

    def last_owner_event(self):
        return self.conn.execute("SELECT step, code, actor, ref FROM learn_events WHERE actor='OWNER' "
                                 "ORDER BY seq DESC LIMIT 1").fetchone()

    def cand(self, vid="L1"):
        return dict([v for v in CONF.variants(self.cfg, self.floor) if v["id"] == vid][0])


class TestReadCommands(Base):
    def test_status(self):
        code, rep, _raw = self.run_cli(["status"])
        self.assertEqual(code, 0)
        self.assertEqual((rep["db"], rep["ok"], rep["action"]), ("OK", True, "status"))
        self.assertEqual(rep["version"]["ver"], "X-0001")
        self.assertEqual(rep["version"]["reason_code"], "INIT")
        self.assertEqual(rep["promotion"]["mode"], "auto")
        self.assertEqual((rep["switch"]["apply"], rep["switch"]["source"]), (True, "default"))
        self.assertIsNone(rep["snapshot"])
        self.assertIsNone(rep["lease"])
        self.assertEqual((rep["effective"]["ver"], rep["effective"]["why"]), ("X-0001", "SNAPSHOT"))
        self.assertEqual((rep["trials"]["open"], rep["trials"]["closed"]), (0, 0))
        self.assertEqual(rep["config"]["errors"], [])

    def test_status_text(self):
        code, _rep, raw = self.run_cli(["status", "--text"])
        self.assertEqual(code, 0)
        self.assertIn("версия: X-0001", raw)
        self.assertIn("продвижение: auto", raw)
        self.assertIn("выключатель: включено", raw)

    def test_status_reads_only(self):
        before = self.counts()
        self.assertEqual(self.run_cli(["status"])[0], 0)
        self.assertEqual(self.counts(), before)

    def test_versions(self):
        code, rep, _raw = self.run_cli(["versions"])
        self.assertEqual(code, 0)
        self.assertEqual((rep["n"], rep["versions"][0]["ver"], rep["versions"][0]["reason_code"]), (1, "X-0001", "INIT"))
        code, _rep, raw = self.run_cli(["versions", "--text"])
        self.assertEqual(code, 0)
        self.assertIn("X-0001", raw)

    def test_search(self):
        with store.tx(self.conn):
            self.conn.execute("INSERT INTO exit_search(ts, a, b, cut_ts, sample_h, cfg_h, menu_ver, base_ver, block_h, code, results) "
                              "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                              (NOW, NOW - 86400, NOW, NOW - 60, "abc123", "cfg1", 1, "X-0001", 24, "NO_CANDIDATE",
                               json.dumps({"n_paths": 0, "n_blocks": 0, "candidates": []})))
        code, rep, _raw = self.run_cli(["search", "--limit", "5"])
        self.assertEqual(code, 0)
        self.assertEqual((rep["n"], rep["rows"][0]["code"], rep["rows"][0]["sample_h"]), (1, "NO_CANDIDATE", "abc123"))
        self.assertEqual(rep["rows"][0]["results"]["n_paths"], 0)
        code, _rep, raw = self.run_cli(["search", "--text"])
        self.assertEqual(code, 0)
        self.assertIn("поиски: 1", raw)

    def test_trials(self):
        tid = ET.register(self.conn, self.cand(), None, self.cfg, NOW, root=self.root)
        code, rep, _raw = self.run_cli(["trials"])
        self.assertEqual(code, 0)
        self.assertEqual((rep["n"], rep["trials"][0]["trial_id"], rep["trials"][0]["state"]), (1, tid, "open"))
        code, rep, _raw = self.run_cli(["trials", "closed"])
        self.assertEqual((code, rep["n"]), (0, 0))
        code, _rep, raw = self.run_cli(["trials", "--text"])
        self.assertEqual(code, 0)
        self.assertIn(tid, raw)

    def test_gate_report(self):
        code, rep, _raw = self.run_cli(["gate"])
        self.assertEqual(code, 0)
        self.assertEqual((rep["ok"], rep["db"], rep["passed"]), (True, "OK", False))
        self.assertEqual((rep["version"]["ver"], rep["conds"]["VERSION"]), ("X-0001", False))
        self.assertEqual(rep["why"], ["VERSION"])
        code, _rep, raw = self.run_cli(["gate", "--text"])
        self.assertEqual(code, 0)
        self.assertIn("гейт этапа 4", raw)
        self.assertIn("итог: гейт не пройден", raw)

    def test_brain_and_after_on_empty_db(self):
        code, rep, _raw = self.run_cli(["after"])
        self.assertEqual((code, rep["db"]), (0, "OK"))
        code, rep, _raw = self.run_cli(["brain"])
        self.assertEqual((code, rep["db"]), (0, "OK"))

    def test_usage(self):
        self.assertEqual(CLI.main([], root=self.root), 2)


class TestNoDatabase(Base):
    def setUp(self):
        # базы нет вовсе: только конфиги и runtime/
        self.root = H.mk_root(self)
        for name in CFG_FILES:
            shutil.copy(os.path.join(H.PROJECT, "config", name), os.path.join(self.root, "config", name))

    def test_read_commands_unavailable(self):
        for argv in (["status"], ["versions"], ["search"], ["trials"]):
            code, rep, _raw = self.run_cli(argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual((rep["db"], rep["ok"]), ("NO_DB", False), argv)
        for argv in (["after"], ["brain"]):                     # отчёты при недоступной базе только код 1 (текст в stderr)
            code, _rep, _raw = self.run_cli(argv)
            self.assertEqual(code, 1, argv)

    def test_status_still_reports_switch(self):
        code, rep, _raw = self.run_cli(["status"])
        self.assertEqual(code, 1)
        self.assertTrue(rep["switch"]["apply"])

    def test_apply_off_works_without_db(self):
        code, rep, _raw = self.run_cli(["apply", "off"])
        self.assertEqual((code, rep["ok"], rep["apply"]), (0, True, False))
        self.assertFalse(os.path.exists(store.db_path(self.root)))          # база не создана
        with open(XD.switch_path(self.root), encoding="utf-8") as f:
            sw = json.load(f)
        self.assertEqual((sw["apply"], sw["actor"]), (False, "OWNER"))
        floor = CONF.current_floor(self.root)
        _policy, info = XD.current(self.root, NOW, floor)
        self.assertEqual(info["why"], "SWITCH")                             # Requirement 16.4
        self.assertIn("log_error", rep)

    def test_promotion_works_without_db(self):
        code, rep, _raw = self.run_cli(["promotion", "off"])
        self.assertEqual((code, rep["mode"], rep["changed"]), (0, "off", True))
        self.assertFalse(os.path.exists(store.db_path(self.root)))
        self.assertIn("log_error", rep)
        self.assertEqual(CONF.load_strict(self.root)["promotion"]["mode"], "off")

    def test_rollback_unavailable(self):
        code, rep, _raw = self.run_cli(["rollback"])
        self.assertEqual((code, rep["ok"], rep["db"]), (1, False, "NO_DB"))


class TestApply(Base):
    def test_apply_off_and_on(self):
        code, rep, _raw = self.run_cli(["apply", "off"])
        self.assertEqual((code, rep["ok"]), (0, True))
        with open(XD.switch_path(self.root), encoding="utf-8") as f:
            self.assertIs(json.load(f)["apply"], False)
        _policy, info = XD.current(self.root, NOW, self.floor)
        self.assertEqual(info["why"], "SWITCH")
        self.assertEqual(self.last_owner_event()[:3], ("exits", "APPLY_OFF", "OWNER"))

        code, rep, _raw = self.run_cli(["apply", "on"])
        self.assertEqual((code, rep["ok"]), (0, True))
        with open(XD.switch_path(self.root), encoding="utf-8") as f:
            self.assertIs(json.load(f)["apply"], True)
        _policy, info = XD.current(self.root, NOW, self.floor)
        self.assertEqual(info["why"], "SNAPSHOT")                           # снимка ещё нет, но выключатель включён
        self.assertEqual(self.last_owner_event()[:3], ("exits", "APPLY_ON", "OWNER"))

    def test_apply_text(self):
        code, _rep, raw = self.run_cli(["apply", "off", "--text"])
        self.assertEqual(code, 0)
        self.assertIn("ВЫКЛЮЧЕНО", raw)


class TestPromotion(Base):
    def test_set_mode_changes_only_promotion_mode(self):
        before = self.cfg_text()
        raw_before = json.loads(before)
        code, rep, _raw = self.run_cli(["promotion", "off"])
        self.assertEqual((code, rep["mode"], rep["was"], rep["changed"]), (0, "off", "auto", True))
        after = self.cfg_text()
        # меняется ровно одно значение в тексте, форматирование и остальные поля сохранены
        self.assertEqual(after, before.replace('"promotion": {"mode": "auto"', '"promotion": {"mode": "off"', 1))
        raw_after = json.loads(after)
        raw_before["promotion"]["mode"] = "off"
        self.assertEqual(raw_after, raw_before)
        self.assertEqual(CONF.load_strict(self.root)["promotion"]["mode"], "off")
        self.assertEqual(self.last_owner_event()[:3], ("exits", "PROMOTION_OFF", "OWNER"))

    def test_set_mode_idempotent_no_event(self):
        code, rep, _raw = self.run_cli(["promotion", "auto"])
        self.assertEqual((code, rep["changed"]), (0, False))
        self.assertIsNone(self.last_owner_event())

    def test_show_mode(self):
        code, rep, _raw = self.run_cli(["promotion"])
        self.assertEqual((code, rep["mode"], rep["errors"]), (0, "auto", []))

    def test_broken_config_not_fixed(self):
        path = os.path.join(self.root, "config", "exits.json")
        broken = '{"schema": "tb.exits_cfg/1"}\n'
        with open(path, "w", encoding="utf-8") as f:
            f.write(broken)
        code, _rep, _raw = self.run_cli(["promotion", "off"])
        self.assertEqual(code, 1)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), broken)
        code, rep, _raw = self.run_cli(["promotion"])
        self.assertEqual(code, 1)
        self.assertTrue(rep["errors"])

    def test_unknown_key_refused(self):
        path = os.path.join(self.root, "config", "exits.json")
        raw = json.loads(self.cfg_text())
        raw["extra"] = True
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)
        code, _rep, _raw = self.run_cli(["promotion", "off"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(self.cfg_text())["promotion"]["mode"], "auto")


class TestRollback(Base):
    def test_rollback_creates_floor_version_snapshot_and_lease(self):
        before_cfg = self.cfg_text()
        code, rep, _raw = self.run_cli(["rollback"])
        self.assertEqual((code, rep["ok"], rep["ver"], rep["from"]), (0, True, "X-0002", "X-0001"))
        row = XV.last_version(self.conn)
        self.assertEqual((row["ver"], row["reason_code"], row["parent"]), ("X-0002", "OWNER_ROLLBACK", "X-0001"))
        self.assertEqual(row["policy"], XP.normalize(self.floor, self.floor))
        self.assertTrue(store2.verify_chain(self.conn, "exit_versions")["ok"])
        # снимок и аренда
        with open(XD.snapshot_path(self.root), encoding="utf-8") as f:
            snap = json.load(f)
        self.assertEqual((snap["ver"], snap["policy_h"]), ("X-0002", row["policy_h"]))
        with open(XD.lease_path(self.root), encoding="utf-8") as f:
            lease = json.load(f)
        self.assertEqual((lease["ver"], lease["policy_h"], lease["by"]), ("X-0002", row["policy_h"], "rollback"))
        _policy, info = XD.current(self.root, int(time.time()) + 1, self.floor)
        self.assertEqual((info["ver"], info["why"]), ("X-0002", "OK"))
        self.assertEqual(self.last_owner_event()[:3], ("exits", "OWNER_ROLLBACK", "OWNER"))
        self.assertEqual(self.cfg_text(), before_cfg)                       # config/ не тронут

    def test_owner_rollback_function_for_dashboard(self):
        rep = CLI.owner_rollback(self.root, now=NOW + 60)
        self.assertEqual((rep["ok"], rep["ver"], rep["from"]), (True, "X-0002", "X-0001"))
        self.assertEqual(rep["snapshot"]["ver"], "X-0002")
        self.assertEqual(rep["lease"]["by"], "rollback")
        self.assertNotIn("log_error", rep)

    def test_owner_rollback_unavailable_does_not_raise(self):
        root = H.mk_root(self)
        rep = CLI.owner_rollback(root, now=NOW)
        self.assertEqual((rep["ok"], rep["db"]), (False, "NO_DB"))

    def test_rollback_text(self):
        code, _rep, raw = self.run_cli(["rollback", "--text"])
        self.assertEqual(code, 0)
        self.assertIn("откат владельца: версия X-0002", raw)


class TestRegistration(unittest.TestCase):
    def test_group_and_module(self):
        from tb import __main__ as M
        self.assertEqual(M.V2.get("exits"), "tb.knowledge.exits_cli")
        self.assertEqual(CLI.__name__, "tb.knowledge.exits_cli")
        self.assertNotIn("learnloop", CLI.__name__)

    def test_commands_registered(self):
        self.assertEqual(sorted(CLI.HANDLERS),
                         ["after", "apply", "brain", "gate", "promotion", "rollback", "search", "status", "trials", "versions"])


if __name__ == "__main__":
    unittest.main()
