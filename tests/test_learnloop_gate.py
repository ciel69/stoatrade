"""tb.learnloop.gate и `./tb learn gate` (R2, задача 21; Requirements 15.8, 15.9, 15.6; свойство 27). Ступень A: 7 полных проходов без
вмешательства владельца среди последних 10 засчитываемых суток и урок меток hyp с n >= 30; ступень B: испытание с вердиктом цикла.
Границы суток UTC, сутки < 12 часов, один и четыре неполных прохода из 10, пропущенные сутки, запись OWNER по правилу и по уроку мозга,
команды `./tb learn promotion off` и `./tb rulebook apply off`, база отсутствует / без таблиц / восстанавливается.
Временные корни, фейковые часы (now передаётся явно), сети нет."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb.knowledge import learn_cli as LCLI
from tb.knowledge import markers as KM
from tb.knowledge import rulebook_cli as RC
from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import cycle as CY
from tb.learnloop import gate as G
from tb.learnloop import rules as RL
from tb.learnloop import trial as TR
from tb.learnloop import versions as V
from tb.memory import cfg as memcfg
from tb.memory import lessons as LS

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = 3600
D = 86400
T0 = 1790812800                   # 2026-10-01T00:00:00Z
P2 = {"id": "P2", "ver": 1, "name": "confirm_follow", "kinds": ["range_5m", "turnover_surge", "hot_enter", "oi_spike", "liq_cascade"], "side": "with_dir",
      "entry": "candle_confirm_limit", "enabled": True}
SPREAD = [{"feature": "spread_bps", "op": ">=", "value": 5.0}]
NC = [{"feature": "confirmed", "op": "=", "value": False}]
CLAIM = {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.5}


def dname(i):
    return CY.day_of(T0 + i * D)


class Root(unittest.TestCase):
    def mkroot(self):
        root = tempfile.mkdtemp(prefix="tbgate_")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "config"))
        for f in ("learnloop.json", "memory.json", "shadow_policies.json"):
            shutil.copy(os.path.join(REPO, "config", f), os.path.join(root, "config", f))
        return root


class Base(Root):
    def setUp(self):
        self.root = self.mkroot()
        with open(os.path.join(self.root, "config", "learnloop.json"), encoding="utf-8") as f:
            self.cfg = json.load(f)
        self.mcfg = memcfg.load(self.root)
        RV.clear_cache()
        store2._STATUS_CACHE.clear()
        self.c = store.open_rw(self.root)
        self.addCleanup(self.c.close)
        store2.migrate_v2a(self.c, self.root, explicit=True, backup=False, now=T0)
        V.init_rulebook(self.c, T0)

    # ---- данные
    def day(self, i, work_h=24.0, full=True, obs_share=None):
        """Сводка суток i (от T0) через cycle.summarize_day: работа work_h, все суточные шаги; full=False — суточный шаг menu с ERROR."""
        names = ["integrity", "backup", "menu", "accept", "holdouts", "register"]
        work_s = work_h * H
        hw = dict((str(h), min(float(H), max(0.0, work_s - h * H))) for h in range(24))
        worked = [h for h, s in hw.items() if s >= int(self.cfg["intervals"]["observe_s"])]
        k = len(worked) if obs_share is None else int(len(worked) * obs_share)
        acc = {"day": dname(i), "work_s": work_s, "hw": hw, "oh": worked[:k], "daily": dict((n, "OK") for n in names)}
        if not full:
            acc["daily"]["menu"] = CY.ERROR
        s = CY.summarize_day(acc, self.cfg, names)
        store2.log_event(self.c, V.DAY_STEP, "DAY", "code", ref=s["day"], body=s, now=T0 + (i + 1) * D + 60)
        return s

    def days(self, n, start=0, incomplete=()):
        for i in range(start, start + n):
            self.day(i, full=(i - start) not in incomplete)

    def owner(self, step, code, ref, ts, body=None):
        RC.log_owner(self.c, step, code, ref, body or {"action": code.lower()}, ts)

    def label_lesson(self, n, now=T0):
        ev = {"n_closed": n, "diff": -0.4, "se": 0.1, "p": 0.001}
        return LS.create(self.root, SPREAD, dict(CLAIM), "code", [], self.mcfg, now, evidence_src="labels", evidence_extra=ev)["lesson_id"]

    def brain_lesson(self, now=T0):
        return LS.create(self.root, NC, dict(CLAIM, effect=1.0), "code", [], self.mcfg, now)["lesson_id"]

    def trial(self, lid, now):
        draft = RL.derive({"lesson_id": lid, "status": "hyp", "cond": SPREAD, "claim": dict(CLAIM), "evidence": {"src": "labels"}}, P2, self.c, now)
        res = V.create_with_trial(self.c, draft, {"criterion": {"active": []}, "criterion_h": "0" * 16, "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1",
                                                  "start_ts": now, "deadline_ts": now + 30 * D, "env_norm": "mainnet"}, now, self.cfg)
        return TR.get(self.c, res["trial_id"])

    def gate(self, now=None):
        return G.evaluate(self.c, now=now or T0 + 40 * D, lessons=LS.read_all(self.root))

    def a(self, now=None):
        return self.gate(now)["conds"]["A_DAYS"]


# ---------------------------------------------------------------- ступень A: сутки

class TestDays(Base):
    def test_ten_full(self):
        self.days(10)
        a = self.a()
        self.assertTrue(a["ok"])
        self.assertEqual((a["clean"], a["counted"]), (10, 10))

    def test_one_incomplete_of_ten(self):
        self.days(10, incomplete=(4,))
        a = self.a()
        self.assertTrue(a["ok"])
        self.assertEqual((a["clean"], a["full"]), (9, 9))

    def test_three_incomplete_boundary(self):
        self.days(10, incomplete=(0, 3, 9))
        self.assertEqual(self.a()["clean"], 7)
        self.assertTrue(self.a()["ok"])

    def test_four_incomplete_of_ten(self):
        self.days(10, incomplete=(0, 2, 5, 9))
        a = self.a()
        self.assertFalse(a["ok"])
        self.assertEqual(a["clean"], 6)
        self.assertIn("неполных 4", a["why"])

    def test_window_is_last_ten_counted(self):
        self.days(10, start=0)                                 # старые полные сутки за окном
        self.days(10, start=10, incomplete=(0, 1, 2, 3))       # последние 10: 6 полных
        a = self.a()
        self.assertEqual((a["counted"], a["clean"], a["recorded"]), (10, 6, 20))
        self.assertFalse(a["ok"])

    def test_obs_share_below(self):
        """Сутки >= 12 часов, но observe успешен меньше чем в 90% часов работы — не полный проход (свойство 27)."""
        for i in range(10):
            self.day(i, obs_share=0.8 if i < 4 else None)
        self.assertEqual(self.a()["clean"], 6)

    def test_short_days_not_counted_not_reset(self):
        """Сутки < 12 часов не засчитываются ни в плюс, ни в минус: 7 полных среди коротких суток — ступень проходит."""
        s = self.day(0, work_h=11.99)
        self.assertFalse(s["counted"])
        self.assertTrue(self.day(1, work_h=12.0)["counted"])
        for i in range(2, 8):
            self.day(i)
        for i in range(8, 14):
            self.day(i, work_h=5.0, full=False)                # платформа почти выключена: короткие сутки
        a = self.a()
        self.assertTrue(a["ok"])
        self.assertEqual((a["counted"], a["clean"], a["not_counted"]), (7, 7, 7))
        self.assertIn("не учитываются", a["why"])

    def test_missing_days_not_reset(self):
        """Пропущенные сутки (сводок нет: платформа выключена) не сбрасывают счётчик."""
        for i in (0, 1, 2, 10, 11, 25, 30):
            self.day(i)
        a = self.a()
        self.assertTrue(a["ok"])
        self.assertEqual(a["clean"], 7)

    def test_too_few_days(self):
        self.days(6)
        a = self.a()
        self.assertFalse(a["ok"])
        self.assertIn("засчитываемых суток пока 6", a["why"])

    def test_no_days(self):
        a = self.a()
        self.assertFalse(a["ok"])
        self.assertIn("сводок суток нет", a["why"])

    def test_day_summary_last_wins(self):
        """Повторная сводка тех же суток (после восстановления базы) заменяет прежнюю."""
        self.days(7)
        self.day(3, full=False)
        self.assertEqual(self.a()["clean"], 6)


# ---------------------------------------------------------------- ступень A: вмешательства владельца

class TestOwner(Base):
    def test_owner_rule_marks_day(self):
        self.days(10, incomplete=(0, 1, 2))
        self.assertTrue(self.a()["ok"])
        self.owner("rulebook", "OWNER_DISABLE", "RL0001", T0 + 5 * D + 3600)
        a = self.a()
        self.assertFalse(a["ok"])
        self.assertEqual((a["clean"], a["owner_days"]), (6, 1))
        self.assertEqual([d["owner"] for d in a["days"] if d["day"] == dname(5)], [["OWNER_DISABLE"]])
        self.assertIn("вмешательством владельца 1", a["why"])

    def test_day_boundaries(self):
        """Запись в 23:59:59 относится к своим суткам UTC, в 00:00:00 — к следующим."""
        self.days(10)
        self.owner("rulebook", "UNBLOCK", "ab" * 8, T0 + 4 * D - 1)       # конец суток 3
        self.owner("rulebook", "UNBLOCK", "cd" * 8, T0 + 6 * D)           # начало суток 6
        a = self.a()
        own = dict((d["day"], bool(d["owner"])) for d in a["days"])
        self.assertTrue(own[dname(3)])
        self.assertTrue(own[dname(6)])
        self.assertFalse(own[dname(4)])
        self.assertFalse(own[dname(5)])
        self.assertEqual(a["clean"], 8)

    def test_owner_on_brain_lesson_ignored(self):
        self.days(10, incomplete=(0, 1, 2))
        lid = self.brain_lesson()
        self.owner("lesson", "OWNER_REJECT", lid, T0 + 5 * D + 60)
        rep = self.gate()
        self.assertTrue(rep["conds"]["A_DAYS"]["ok"])
        self.assertEqual(rep["owner"], {"cycle": 0, "brain_lesson": 1, "other": 0})

    def test_owner_on_label_lesson_counts(self):
        self.days(10, incomplete=(0, 1, 2))
        lid = self.label_lesson(40)
        self.owner("lesson", "OWNER_REJECT", lid, T0 + 5 * D + 60)
        self.assertFalse(self.a()["ok"])

    def test_owner_on_unknown_lesson_counts(self):
        self.days(10, incomplete=(0, 1, 2))
        self.owner("lesson", "OWNER_REJECT", "L-0999", T0 + 5 * D + 60)
        self.assertFalse(self.a()["ok"])

    def test_owner_restore_not_cycle_object(self):
        self.days(10, incomplete=(0, 1, 2))
        self.owner("knowledge_restore", "OWNER", "knowledge-x.sqlite", T0 + 5 * D + 60)
        rep = self.gate()
        self.assertTrue(rep["conds"]["A_DAYS"]["ok"])
        self.assertEqual(rep["owner"]["other"], 1)

    def test_code_actions_ignored(self):
        """Автоматические действия цикла (actor=code), в том числе откат и отклонение, — не вмешательство."""
        self.days(10, incomplete=(0, 1, 2))
        store2.log_event(self.c, "watch", "ROLLBACK", "code", ref="RL0001", body={"ver": "R-0002"}, now=T0 + 5 * D + 60)
        store2.log_event(self.c, "trial", "TRIAL_REJECT", "code", ref="T-RL0001", body={}, now=T0 + 6 * D + 60)
        self.assertTrue(self.a()["ok"])

    def test_owner_on_uncounted_day_ignored(self):
        self.days(7)
        self.day(7, work_h=6.0)
        self.owner("rulebook", "APPLY_OFF", None, T0 + 7 * D + 60)
        a = self.a()
        self.assertTrue(a["ok"])
        self.assertEqual(a["owner_outside"], 1)

    def test_command_promotion_off(self):
        """`./tb learn promotion off` пишет OWNER (promotion) — сутки не засчитываются в ступень A."""
        self.days(10, incomplete=(0, 1, 2))
        rep = LCLI.set_mode(self.root, "off", now=T0 + 8 * D + 120)
        self.assertTrue(rep["changed"])
        self.assertNotIn("log_error", rep)
        a = self.a()
        self.assertFalse(a["ok"])
        self.assertEqual([d["owner"] for d in a["days"] if d["day"] == dname(8)], [["PROMOTION_OFF"]])

    def test_command_rulebook_apply_off(self):
        self.days(10, incomplete=(0, 1, 2))
        rep = RC.set_apply(self.root, False, now=T0 + 9 * D + 120)
        self.assertNotIn("log_error", rep)
        self.assertFalse(self.a()["ok"])


# ---------------------------------------------------------------- ступень A: урок меток; ступень B: вердикт

class TestHypAndVerdict(Base):
    def test_hyp_n(self):
        h = self.gate()["conds"]["A_HYP"]
        self.assertFalse(h["ok"])
        self.assertIn("ещё нет", h["why"])
        self.label_lesson(29)
        self.brain_lesson()
        h = self.gate()["conds"]["A_HYP"]
        self.assertFalse(h["ok"])
        self.assertEqual((h["n_labels"], h["best_n"]), (1, 29))
        lid = self.label_lesson(30)
        h = self.gate()["conds"]["A_HYP"]
        self.assertTrue(h["ok"])
        self.assertEqual(h["lesson_id"], lid)

    def test_hyp_history_counts_after_reject(self):
        lid = self.label_lesson(45)
        LS.transition(self.root, lid, "rejected", "TRIAL_REJECT", T0 + D)
        self.assertTrue(self.gate()["conds"]["A_HYP"]["ok"])

    def test_stage_a_needs_both(self):
        self.days(10)
        rep = self.gate()
        self.assertFalse(rep["stage_a"])
        self.assertEqual(rep["failed"], ["A_HYP", "B_VERDICT"])
        self.label_lesson(31)
        rep = self.gate()
        self.assertTrue(rep["stage_a"])
        self.assertFalse(rep["ok"])

    def test_verdict(self):
        lid = self.label_lesson(40)
        t = self.trial(lid, T0 + 2 * D)
        b = self.gate()["conds"]["B_VERDICT"]
        self.assertFalse(b["ok"])
        self.assertEqual(b["open"], 1)
        TR.close(self.c, t, TR.REJECT, "TRIAL_REJECT", {"n_tc": 150, "p": 0.4}, T0 + 12 * D, self.cfg)
        b = self.gate()["conds"]["B_VERDICT"]
        self.assertTrue(b["ok"])
        self.assertEqual(b["by_verdict"], {"reject": 1})

    def test_inconclusive_counts_owner_close_does_not(self):
        lid = self.label_lesson(40)
        t = self.trial(lid, T0 + 2 * D)
        TR.close(self.c, t, TR.INCONCLUSIVE, "OWNER", {"owner": "OWNER_DISABLE"}, T0 + 3 * D, self.cfg, reason="OWNER_DISABLE")
        b = self.gate()["conds"]["B_VERDICT"]
        self.assertFalse(b["ok"])
        self.assertEqual(b["owner_closed"], 1)
        self.assertIn("закрытых владельцем 1", b["why"])
        self.assertTrue(G.stage_verdict([{"trial_id": "T-1", "state": "closed", "verdict": "inconclusive", "numbers": {"code": "NO_VERDICT"},
                                          "closed_ts": 1}])["ok"])

    def test_full_pass(self):
        self.days(10, incomplete=(1,))
        lid = self.label_lesson(35)
        t = self.trial(lid, T0 + 2 * D)
        TR.close(self.c, t, TR.PROMOTE, "PROMOTE", {"n_tc": 150, "p": 0.001}, T0 + 12 * D, self.cfg)
        rep = self.gate()
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["failed"], [])
        text = "\n".join(G.render(rep))
        self.assertIn("гейт этапа R2: пройден", text)
        self.assertEqual(text.count(": да — "), 3)


# ---------------------------------------------------------------- run и CLI: недоступная база

class TestRun(Root):
    def setUp(self):
        RV.clear_cache()
        store2._STATUS_CACHE.clear()

    def cli(self, root, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = LCLI.main(["gate"] + list(args), root=root)
        return code, buf.getvalue()

    def test_no_db(self):
        root = self.mkroot()
        rep = G.run(root, now=T0)
        self.assertEqual(rep["db"], G.NO_DB)
        self.assertFalse(rep["ok"])
        self.assertIn("базы знаний нет", rep["conds"]["A_DAYS"]["why"])
        code, out = self.cli(root)
        self.assertEqual(code, 1)
        self.assertIn("базы знаний нет", out)
        self.assertFalse(os.path.exists(store.db_path(root)))          # только чтение: файл не создан

    def test_not_ready(self):
        root = self.mkroot()
        store.open_rw(root).close()                                     # база меток R1 без таблиц цикла
        rep = G.run(root, now=T0)
        self.assertEqual(rep["db"], G.NOT_READY)
        self.assertIn("NOT_READY", rep["error"])

    def test_restoring(self):
        root = self.mkroot()
        c = store.open_rw(root)
        store2.migrate_v2a(c, root, explicit=True, backup=False, now=T0)
        c.close()
        os.makedirs(os.path.dirname(KM.restoring_path(root)), exist_ok=True)
        with open(KM.restoring_path(root), "w", encoding="utf-8") as f:
            f.write("{}")
        rep = G.run(root, now=T0)
        self.assertEqual(rep["db"], G.RESTORING)
        self.assertIn("восстанавливается", rep["conds"]["B_VERDICT"]["why"])
        code, out = self.cli(root, "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["db"], G.RESTORING)

    def test_cli_ok_readonly(self):
        root = self.mkroot()
        c = store.open_rw(root)
        store2.migrate_v2a(c, root, explicit=True, backup=False, now=T0)
        V.init_rulebook(c, T0)
        n0 = c.execute("SELECT COUNT(*) FROM learn_events").fetchone()[0]
        c.close()
        code, out = self.cli(root, "--json")
        self.assertEqual(code, 0)
        rep = json.loads(out)
        self.assertEqual((rep["db"], rep["ok"], rep["failed"]), ("OK", False, list(G.CONDS)))
        for k in G.CONDS:
            self.assertTrue(rep["conds"][k]["why"])
        code, out = self.cli(root)
        self.assertEqual(code, 0)
        self.assertIn("ступень A — нет", out)
        self.assertEqual(len([ln for ln in out.splitlines() if " — " in ln and ": нет" in ln]), 3)
        c = store.open_ro(root)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM learn_events").fetchone()[0], n0)
        c.close()


if __name__ == "__main__":
    unittest.main()
