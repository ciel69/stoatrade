"""Аварийные команды владельца (R2, задача 16; Requirements 11.8–11.10, 10.6, 8.4, 7.14): ./tb rulebook disable|disable-all|apply|unblock и
./tb learn promotion auto|off (вне пакета tb.learnloop). Записи OWNER в learn_events, бессрочный чёрный список, копия owner; disable-all сначала
пишет выключатель apply=false и при повреждённой или восстанавливаемой базе сообщает о повторе (свойство 22); отключённое владельцем условие не
возвращается через меню. Запись config/learnloop.json:promotion.mode атомарна, меняет только это поле и проверяется загрузчиком.
Временные корни, сети нет."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb import __main__ as M
from tb.knowledge import learn_cli as LCLI
from tb.knowledge import markers as KM
from tb.knowledge import rulebook_cli as RBC
from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import conf as LC
from tb.learnloop import rules as RL
from tb.learnloop import trial as TR
from tb.learnloop import versions as V
from tb.logic import learn_rules as LR

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = 3600
D = 86400
T0 = 1790812800
NOW = T0 + 12 * D
P2 = {"id": "P2", "ver": 1, "name": "confirm_follow", "kinds": ["range_5m", "turnover_surge", "hot_enter", "oi_spike", "liq_cascade"], "side": "with_dir",
      "entry": "candle_confirm_limit", "enabled": True}
SPREAD = [{"feature": "spread_bps", "op": ">=", "value": 5.0}]
RANGE = [{"feature": "range_5m_pct", "op": ">=", "value": 5.0}]
CLAIM = {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.5}


def call(main, argv, root):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv, root=root)
    s = out.getvalue().strip()
    try:
        obj = json.loads(s) if s else None
    except ValueError:
        obj = s
    return code, obj, err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbowner_")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "config"))
        for f in ("learnloop.json", "memory.json", "shadow_policies.json"):
            shutil.copy(os.path.join(REPO, "config", f), os.path.join(self.root, "config", f))
        with open(os.path.join(self.root, "config", "learnloop.json"), encoding="utf-8") as f:
            self.cfg = json.load(f)
        RV.clear_cache()
        store2._STATUS_CACHE.clear()
        self.c = store.open_rw(self.root)
        self.addCleanup(self.close)
        store2.migrate_v2a(self.c, self.root, explicit=True, backup=False, now=T0)
        V.init_rulebook(self.c, T0)

    def close(self):
        if self.c is not None:
            self.c.close()
            self.c = None

    def trial_rule(self, cond, lid, state="open"):
        draft = RL.derive({"lesson_id": lid, "status": "hyp", "cond": cond, "claim": dict(CLAIM), "evidence": {"src": "labels"}}, P2, self.c, NOW)
        res = V.create_with_trial(self.c, draft, {"criterion": {"active": []}, "criterion_h": "0" * 16, "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1",
                                                  "start_ts": NOW - 5 * D, "deadline_ts": NOW + 25 * D, "env_norm": "mainnet"}, NOW, self.cfg)
        if state == "closed":
            store2.update_row(self.c, "trials", res["trial_id"], {"state": "closed", "verdict": "promote", "cut_ts": NOW, "closed_ts": NOW,
                                                                  "numbers": {"in_mean": -0.5, "n": 150}})
            V.set_wait(self.c, res["rule_id"], "READY", NOW)
        return res["rule_id"]

    def active_rule(self, cond, lid):
        rid = self.trial_rule(cond, lid, state="closed")
        V.promote(self.c, rid, NOW, self.cfg)
        return rid

    def rb(self, *argv):
        return call(RBC.main, list(argv), self.root)

    def owner(self):
        return self.c.execute("SELECT step, code, ref FROM learn_events WHERE actor='OWNER' ORDER BY seq").fetchall()

    def vers(self):
        return [(v, rc) for v, rc in self.c.execute("SELECT ver, reason_code FROM rulebook_versions ORDER BY seq")]


class TestDisable(Base):
    def test_disable_active(self):
        rid = self.active_rule(SPREAD, "L-0001")
        code, rep, _ = self.rb("disable", rid)
        self.assertEqual(code, 0)
        self.assertEqual((rep["ver"], rep["done"], rep["blocked"]), ("R-0003", "disabled", False))   # бессрочную запись сделала версия
        self.assertEqual(self.vers()[-1], ("R-0003", "OWNER_DISABLE"))
        r = V.rule(self.c, rid)
        self.assertEqual((r["status"], r["reason_code"]), ("disabled", "OWNER_DISABLE"))
        ch = LR.block_h(SPREAD, "lower")
        self.assertEqual(self.c.execute("SELECT code, until_ts, actor FROM blocklist_rows WHERE cond_h=?", (ch,)).fetchall(), [("OWNER_DISABLE", None, "OWNER")])
        self.assertIsNone(RL.derive({"lesson_id": "L-0009", "status": "hyp", "cond": SPREAD, "claim": dict(CLAIM), "evidence": {"src": "labels"}},
                                    P2, self.c, NOW + 365 * D))                       # отключённое условие не возвращается через меню
        self.assertEqual(self.owner(), [("rulebook", "OWNER_DISABLE", rid)])
        self.assertIn("owner", [b["reason"] for b in store2.list_backups(self.root)])
        code, rep, _ = self.rb("disable", rid)                                          # повтор: без новой версии
        self.assertEqual((code, rep["ver"]), (0, None))
        self.assertEqual(len(self.vers()), 3)

    def test_disable_trial(self):
        rid = self.trial_rule(SPREAD, "L-0001")
        tid = TR.by_rule(self.c, rid)["trial_id"]
        code, rep, _ = self.rb("disable", rid)
        self.assertEqual((code, rep["done"], rep["blocked"], rep["ver"]), (0, "closed", True, None))
        self.assertEqual((TR.get(self.c, tid)["state"], TR.get(self.c, tid)["numbers"]["reason"]), ("closed", "OWNER_DISABLE"))
        self.assertEqual((V.rule(self.c, rid)["status"], V.rule(self.c, rid)["reason_code"]), ("rejected", "OWNER"))
        self.assertEqual(RL.blocked(self.c, LR.block_h(SPREAD, "lower"), NOW + 400 * D)["code"], "OWNER_DISABLE")
        self.assertEqual(self.vers(), [("R-0001", "INIT")])

    def test_unknown_rule(self):
        self.assertEqual(self.rb("disable", "RL9999")[0], 2)


class TestDisableAll(Base):
    def test_all(self):
        a = self.active_rule(SPREAD, "L-0001")
        t = self.trial_rule(RANGE, "L-0002")
        KM.write_json(RV.lease_path(self.root), {"ts": NOW})
        code, rep, _ = self.rb("disable-all")
        self.assertEqual(code, 0)
        self.assertFalse(RV.read_switch(self.root))
        self.assertEqual((rep["disabled"], rep["rejected"], rep["blocked"]), ([a], [t], 1))
        self.assertEqual(self.vers()[-1], ("R-0003", "OWNER_DISABLE"))
        self.assertEqual(RV.last_version(self.c)["rule_ids"], [])
        self.assertEqual(V.rule(self.c, a)["status"], "disabled")
        self.assertEqual(V.rule(self.c, t)["status"], "rejected")
        for cond in (SPREAD, RANGE):
            b = RL.blocked(self.c, LR.block_h(cond, "lower"), NOW + 400 * D)
            self.assertEqual((b["code"], b["until_ts"]), ("OWNER_DISABLE", None))
        self.assertEqual(self.owner(), [("rulebook", "DISABLE_ALL", None)])
        view = RV.active(self.root, NOW)
        self.assertEqual((view["apply"], view["why"]), (False, "SWITCH"))
        code, rep, _ = self.rb("disable-all")                                         # повтор: версия не нужна
        self.assertEqual((code, rep["ver"], rep["blocked"]), (0, None, 0))
        self.assertEqual(len(self.vers()), 3)

    def test_corrupt_db_switch_first(self):
        """Свойство 22: при повреждённой базе выключатель записан сразу, команда сообщает о повторе записи версии, отложенных файлов нет."""
        self.active_rule(SPREAD, "L-0001")
        KM.write_json(RV.lease_path(self.root), {"ts": NOW})
        self.close()
        path = store.db_path(self.root)
        for suf in ("-wal", "-shm"):
            if os.path.exists(path + suf):
                os.remove(path + suf)
        with open(path, "wb") as f:
            f.write(b"not a database" * 512)
        before = sorted(os.listdir(os.path.join(self.root, "runtime")))
        code, rep, err = self.rb("disable-all")
        self.assertEqual(code, 1)
        self.assertEqual((rep["ok"], rep["retry"], rep["switch"]), (False, True, "apply=false"))
        self.assertIn("повторите", err)
        self.assertFalse(RV.read_switch(self.root))
        self.assertEqual(RV.active(self.root, NOW)["why"], "SWITCH")
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "runtime"))), sorted(set(before) | {"rules_switch.json"}))

    def test_restoring_db(self):
        KM.write_json(KM.restoring_path(self.root), {"pid": 1})
        code, rep, _ = self.rb("disable-all", "--text")
        self.assertEqual(code, 1)
        self.assertIn("НЕ сделана", rep)
        self.assertFalse(RV.read_switch(self.root))


class TestApplyUnblock(Base):
    def test_apply(self):
        code, rep, _ = self.rb("apply", "off")
        self.assertEqual((code, rep["apply"]), (0, False))
        self.assertFalse(RV.read_switch(self.root))
        code, rep, _ = self.rb("apply", "on")
        self.assertTrue(RV.read_switch(self.root))
        self.assertEqual([x[1] for x in self.owner()], ["APPLY_OFF", "APPLY_ON"])
        self.assertEqual(self.vers(), [("R-0001", "INIT")])

    def test_apply_without_db(self):
        KM.write_json(KM.restoring_path(self.root), {"pid": 1})
        code, rep, _ = self.rb("apply", "off")
        self.assertEqual(code, 0)
        self.assertIn("log_error", rep)
        self.assertFalse(RV.read_switch(self.root))

    def test_unblock(self):
        rid = self.active_rule(SPREAD, "L-0001")
        self.rb("disable", rid)
        ch = LR.block_h(SPREAD, "lower")
        code, rep, _ = self.rb("unblock", ch)
        self.assertEqual((code, rep["changed"], rep["was"]["code"]), (0, True, "OWNER_DISABLE"))
        self.assertIsNone(RL.blocked(self.c, ch, NOW))
        self.assertEqual(self.owner()[-1], ("rulebook", "UNBLOCK", ch))
        n = self.c.execute("SELECT COUNT(*) FROM blocklist_rows").fetchone()[0]
        code, rep, _ = self.rb("unblock", ch)
        self.assertEqual((code, rep["changed"]), (0, False))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM blocklist_rows").fetchone()[0], n)


class TestBlockFamily(Base):
    """Requirement 7.14 (ревизия 2026-11-03, сквозной тест задачи 22): ключ чёрного списка — семейство условия block_h; сдвиг порога числового
    признака (квантиль меню) не возвращает запрещённое условие, другое значение категориального — другое семейство; прежние записи по точному
    cond_h продолжают действовать; unblock снимает запрет по block_h, его показывает list."""

    def lesson(self, cond, lid="L-0009"):
        return {"lesson_id": lid, "status": "hyp", "cond": cond, "claim": dict(CLAIM), "evidence": {"src": "labels"}}

    def test_owner_disable_blocks_family_forever(self):
        rid = self.active_rule(SPREAD, "L-0001")
        self.assertEqual(self.rb("disable", rid)[0], 0)
        shifted = [{"feature": "spread_bps", "op": ">=", "value": 4.79134}]
        self.assertNotEqual(LR.cond_h(shifted), LR.cond_h(SPREAD))
        self.assertEqual(LR.block_h(shifted), LR.block_h(SPREAD))
        b = RL.blocked(self.c, shifted, NOW + 400 * D)
        self.assertEqual((b["code"], b["until_ts"], b["key"]), ("OWNER_DISABLE", None, LR.block_h(SPREAD)))
        self.assertEqual(RL.derive_reason(self.lesson(shifted), self.c, NOW + 400 * D), "BLOCKED")
        with self.assertRaises(V.RuleRefused) as e:                                     # и регистрация испытания отказывает
            V.create_with_trial(self.c, dict(RL.derive(self.lesson(SPREAD), P2), cond=shifted, cond_h=LR.cond_h(shifted), from_lesson="L-0009"),
                                {"criterion": {}, "criterion_h": "0" * 16, "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1", "start_ts": NOW,
                                 "deadline_ts": NOW + 30 * D, "env_norm": "mainnet"}, NOW + 400 * D, self.cfg)
        self.assertEqual(e.exception.code, "BLOCKED")
        self.assertIsNone(RL.blocked(self.c, [{"feature": "spread_bps", "op": "<=", "value": 5.0}], NOW))   # другой оператор — другое семейство
        code, rep, _ = self.rb("list")
        r = [x for x in rep["rules"] if x["rule_id"] == rid][0]
        self.assertEqual((r["block_h"], r["blocked"]["code"], r["blocked"]["key"]), (LR.block_h(SPREAD), "OWNER_DISABLE", LR.block_h(SPREAD)))
        code, rep, _ = self.rb("show", rid)
        self.assertEqual((rep["block_h"], rep["blocked"]["code"]), (LR.block_h(SPREAD), "OWNER_DISABLE"))
        code, rep, _ = self.rb("unblock", LR.block_h(SPREAD))
        self.assertEqual((code, rep["changed"]), (0, True))
        self.assertIsNone(RL.blocked(self.c, shifted, NOW))
        self.assertIsNone(RL.derive_reason(self.lesson(shifted), self.c, NOW))

    def test_categorical_value_is_own_family(self):
        meme = [{"feature": "coin_class", "op": "=", "value": "meme"}]
        major = [{"feature": "coin_class", "op": "=", "value": "major"}]
        rid = self.active_rule(meme, "L-0001")
        self.rb("disable", rid)
        self.assertEqual(RL.blocked(self.c, meme, NOW + 400 * D)["code"], "OWNER_DISABLE")
        self.assertIsNone(RL.blocked(self.c, major, NOW + 400 * D))
        self.assertIsNone(RL.derive_reason(self.lesson(major), self.c, NOW + 400 * D))

    def test_legacy_exact_cond_h_row_still_blocks(self):
        store2.blocklist_put(self.c, LR.cond_h(SPREAD), "OWNER_DISABLE", None, "OWNER", now=NOW)   # запись до ключа семейства
        b = RL.blocked(self.c, SPREAD, NOW + 400 * D)
        self.assertEqual((b["code"], b["key"]), ("OWNER_DISABLE", LR.cond_h(SPREAD)))
        self.assertEqual(RL.derive_reason(self.lesson(SPREAD), self.c, NOW + 400 * D), "BLOCKED")
        code, rep, _ = self.rb("unblock", LR.cond_h(SPREAD))                            # прежняя запись снимается по своему ключу
        self.assertEqual((code, rep["changed"]), (0, True))
        self.assertIsNone(RL.blocked(self.c, SPREAD, NOW))


class TestPromotion(Base):
    def text(self):
        with open(os.path.join(self.root, "config", "learnloop.json"), encoding="utf-8") as f:
            return f.read()

    def test_off_and_auto(self):
        orig = self.text()
        code, rep, _ = call(LCLI.main, ["promotion", "off"], self.root)
        self.assertEqual((code, rep["mode"], rep["was"], rep["changed"]), (0, "off", "auto", True))
        new = self.text()
        self.assertEqual(new, orig.replace('"mode": "auto"', '"mode": "off"', 1))         # только поле promotion.mode, форматирование сохранено
        cfg, errs = LC.load(self.root)
        self.assertEqual((errs, LC.promotion_mode(cfg)), ([], "off"))
        self.assertEqual(self.owner(), [("promotion", "PROMOTION_OFF", None)])
        code, rep, _ = call(LCLI.main, ["promotion", "off"], self.root)
        self.assertEqual((code, rep["changed"]), (0, False))
        self.assertEqual(len(self.owner()), 1)
        code, rep, _ = call(LCLI.main, ["promotion", "auto"], self.root)
        self.assertEqual((code, rep["mode"]), (0, "auto"))
        self.assertEqual(self.text(), orig)
        code, rep, _ = call(LCLI.main, ["promotion"], self.root)
        self.assertEqual((code, rep["mode"]), (0, "auto"))
        self.assertEqual([x for x in os.listdir(os.path.join(self.root, "config")) if x.endswith(".tmp")], [])

    def test_broken_config_untouched(self):
        p = os.path.join(self.root, "config", "learnloop.json")
        bad = self.text().replace('"lease_h": 6', '"lease_h": "x"')
        with open(p, "w", encoding="utf-8") as f:
            f.write(bad)
        before = self.text()
        code, _rep, err = call(LCLI.main, ["promotion", "off"], self.root)
        self.assertEqual(code, 1)
        self.assertIn("проверку", err)
        self.assertEqual(self.text(), before)

    def test_render_fallback(self):
        raw = LC.parse(self.text())
        compact = json.dumps(raw)
        out = LCLI.render(compact.replace('"mode": "auto"', '"mode" :\n "auto"'), raw, "off")
        self.assertEqual(LC.parse(out)["promotion"]["mode"], "off")
        self.assertEqual(LC.check(LC.parse(out)), [])

    def test_registered(self):
        self.assertEqual(M.V2["learn"], "tb.knowledge.learn_cli")
        self.assertEqual(M.V2["rulebook"], "tb.knowledge.rulebook_cli")
        self.assertIn("learn promotion", M.USAGE)
        self.assertIn("disable-all", M.USAGE)


if __name__ == "__main__":
    unittest.main()
