"""Недельная ретроспектива (R2 вторая итерация, задача 28; Requirements 12.1–12.5, 17.4, 17.6): сводка недели кодом (только числа,
перечисления, идентификаторы), шаг retro раз в неделю (одна неделя — одно задание или одна отметка llm=false), приём ответа tb.retro/1 через
порт: правки промптов, числа правил и свободный текст отклоняются, сверх 3 — TOO_MANY; принятое — кандидат src=llm без правила до испытания;
недоступная модель не останавливает цикл. На SQLite из test_learnloop_verify.build_db."""
import datetime
import json
import os
import unittest

import _bootstrap  # noqa: F401
from test_learnloop_candidates_llm import Base
from test_learnloop_clusters import CFG, D, MCFG, POLICY
from test_learnloop_verify import NOW_H, NOW_M
from tb.learnloop import candidates as CA
from tb.learnloop import cycle as CY
from tb.learnloop import port as PT
from tb.learnloop import retro as RT
from tb.platform import services as S

WEEK = CA.week_id(NOW_M)
CLAIM = {"metric": "mean_r", "dir": "lower", "vs": "base"}


def _ts(y, m, d, h=0, mi=0):
    return int(datetime.datetime(y, m, d, h, mi, tzinfo=datetime.timezone.utc).timestamp())


class RBase(Base):
    def hb(self, now=NOW_M, roles=None):
        S.write_hb("brain", {"protect_only": False, "budget": "ok", "learn_roles": roles or {"day_reviewer": True, "retro": True}}, root=self.root, now=now)

    def answer(self, jid, out, ok=True):
        S.write_json(os.path.join(PT.jobs_dir(self.root), jid + ".result.json"),
                     {"id": jid, "kind": "learnloop", "ok": ok, "output": out, "err": None if ok else "NO_PROFILE", "usd": 0.002})

    def submit(self, now=NOW_M):
        self.hb(now=now)
        rep = RT.run_retro(self.c, self.root, now, CFG, policy=POLICY)
        self.assertEqual(rep["code"], "LLM_SUBMIT", rep)
        return rep["job"]

    def rule_rows(self):
        return self.c.execute("SELECT COUNT(*) FROM rule_rows").fetchone()[0], self.c.execute("SELECT COUNT(*) FROM trials").fetchone()[0]


class TestWeek(unittest.TestCase):
    def test_bounds_match_week_id(self):
        for now in (_ts(2026, 10, 12, 0, 5), _ts(2026, 10, 14, 13), _ts(2026, 10, 18, 23, 59), _ts(2027, 1, 4, 1), _ts(2026, 12, 31, 12)):
            a, b = RT.week_bounds(now)
            self.assertEqual(b - a, 7 * D)
            self.assertEqual(datetime.datetime.fromtimestamp(a, datetime.timezone.utc).weekday(), 0)          # понедельник 00:00 UTC
            self.assertTrue(a < b <= now < b + 7 * D)
            iso = datetime.datetime.fromtimestamp(a, datetime.timezone.utc).isocalendar()
            self.assertEqual(CA.week_id(now), "W-%04d-%02d" % (iso[0], iso[1]))                             # сводка и задание об одной неделе
        self.assertEqual(CA.week_id(_ts(2026, 10, 12, 0, 5)), "W-2026-41")
        self.assertEqual(CA.week_id(_ts(2026, 10, 11, 23, 59)), "W-2026-40")


class TestDigest(RBase):
    def test_numbers_enums_ids_only(self):
        a, b = RT.week_bounds(NOW_M)
        self.c.execute("INSERT INTO trials(trial_id, rule_id, lesson_id, env_norm, criterion, criterion_h, policy_id, policy_ver, ctx_ver, start_ts, deadline_ts, "
                       "state, verdict, numbers, cut_ts, closed_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("T-0001", "R-0001", "L-0001", "mainnet", "{}", "h1", "p", 1, "c1", a - 3 * D, b + 9 * D, "closed", "reject",
                        json.dumps({"code": "TRIAL_REJECT", "n": 150, "diff": -0.02, "p": 0.4, "note": "free text here"}), a + D, a + D))
        self.c.commit()
        d = RT.retro_digest(self.c, NOW_M)
        self.assertEqual((d["week_id"], d["a"], d["b"], d["env"]), (WEEK, a, b, "mainnet"))
        self.assertEqual(set(d), {"week_id", "a", "b", "env", "runs", "candidates", "trials", "rules", "versions", "events"})
        self.assertEqual(sorted(d["runs"]), ["clusters", "filled", "mean_r", "runs"])
        self.assertEqual(d["trials"]["closed"], 1)
        self.assertEqual(d["trials"]["verdicts"], {"reject": 1})
        self.assertEqual(d["trials"]["closed_items"], [{"trial": "T-0001", "rule": "R-0001", "verdict": "reject", "code": "TRIAL_REJECT", "n": 150, "diff": -0.02, "p": 0.4}])
        self.assertEqual(sorted(d["rules"]), ["active_now", "promoted", "reasons", "rejected", "rolled_back", "status"])
        self.assertEqual(sorted(d["events"]), ["day_review", "days", "step_errors"])

        def walk(v):
            if isinstance(v, dict):
                for k, x in v.items():
                    self.assertRegex(k, PT.RE_TOKEN)
                    walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)
            elif isinstance(v, str):
                self.assertRegex(v, PT.RE_TOKEN)                                                    # нет свободного текста
            else:
                self.assertTrue(v is None or isinstance(v, (int, float, bool)), v)
        walk(d)
        self.assertNotIn("free text here", json.dumps(d))

    def test_week_counts_candidates(self):
        CA.run_menu(self.c, self.root, NOW_M, CFG, policy=POLICY)
        n = len(CA.candidates(self.c))
        self.assertGreater(n, 0)
        d = RT.retro_digest(self.c, NOW_M + 7 * D)                                                 # кандидаты меню попали в прошедшую неделю
        a, b = RT.week_bounds(NOW_M + 7 * D)
        self.assertTrue(a <= NOW_M < b)
        self.assertEqual(d["candidates"]["by_src"]["code"]["n"], n)


class TestStep(RBase):
    def events(self, step="retro"):
        return self.c.execute("SELECT code, ref, body FROM learn_events WHERE step=? ORDER BY seq", (step,)).fetchall()

    def test_no_model_marks_week_llm_false_once(self):
        rep = RT.run_retro(self.c, self.root, NOW_M, CFG, policy=POLICY)
        self.assertEqual((rep["code"], rep["why"], rep["id"]), ("LLM_FALSE", "NO_DAEMON", WEEK))
        self.assertEqual(PT.own(self.root), [])                                                   # задания не копятся
        self.assertEqual([(e[0], e[1], json.loads(e[2])) for e in self.events()], [("LLM_FALSE", WEEK, {"llm": False, "why": "NO_DAEMON"})])
        self.hb(now=NOW_M + D)
        self.assertEqual(RT.run_retro(self.c, self.root, NOW_M + D, CFG, policy=POLICY)["code"], "NOT_DUE")   # неделя уже помечена
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(PT.own(self.root), [])
        self.hb(now=NOW_M + 7 * D, roles={"retro": False, "day_reviewer": True})
        rep = RT.run_retro(self.c, self.root, NOW_M + 7 * D, CFG, policy=POLICY)                  # следующая неделя, роль выключена
        self.assertEqual((rep["code"], rep["why"], rep["id"]), ("LLM_FALSE", "NO_ROLE", CA.week_id(NOW_M + 7 * D)))

    def test_one_week_one_job(self):
        jid = self.submit()
        job = PT.read_job(self.root, jid)
        self.assertEqual((job["role"], job["meta"]), ("retro", {"kind": "week", "id": WEEK, "env": "mainnet"}))
        p = job["payload"]
        self.assertEqual((p["kind"], p["week_id"], p["limit"]), ("week", WEEK, 3))
        self.assertEqual(p["week"]["week_id"], WEEK)
        self.assertIn("trials", p["week"])
        self.assertNotIn("day", p)
        self.assertEqual(RT.run_retro(self.c, self.root, NOW_M + 60, CFG, policy=POLICY)["code"], "NOT_DUE")
        self.answer(jid, {"schema": "tb.retro/1", "week_id": WEEK, "items": []})
        self.assertEqual(CA.collect(self.c, self.root, NOW_M + 120, CFG, policy=POLICY)["done"], [(jid, "LLM")])
        self.assertEqual(PT.own(self.root), [])
        self.hb(now=NOW_M + D)
        self.assertEqual(RT.run_retro(self.c, self.root, NOW_M + D, CFG, policy=POLICY)["code"], "NOT_DUE")   # задание уже принято: не повторяется
        self.assertEqual(PT.own(self.root), [])
        self.assertEqual([e[0] for e in self.events()], ["LLM_SUBMIT", "LLM"])

    def test_idempotent_without_events(self):
        """Отметка недели в meta: даже если событие LLM_SUBMIT не записано (ограничение per_day), второе задание не ставится."""
        self.c.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (RT.META_RETRO, json.dumps({"week": WEEK, "code": "LLM_SUBMIT"})))
        self.c.commit()
        self.hb()
        self.assertEqual(self.events(), [])
        self.assertEqual(RT.run_retro(self.c, self.root, NOW_M, CFG, policy=POLICY)["code"], "NOT_DUE")
        self.assertEqual(PT.own(self.root), [])
        self.assertEqual(RT.run_retro(self.c, self.root, NOW_M + 7 * D, CFG, policy=POLICY)["code"], "LLM_FALSE")   # следующая неделя (hb устарел)

    def test_answer_validation_and_no_rule_without_trial(self):
        jid = self.submit()
        p = PT.read_job(self.root, jid)["payload"]
        m = self.menu_id(p, "range_5m_pct", ">=")
        combo = {"cond": [{"feature": "range_5m_pct", "op": ">=", "value": "q80"}, {"feature": "spread_bps", "op": "<=", "value": "q90"}], "claim": CLAIM}
        items = [
            {"menu": m, "dir": "lower"},                                                                       # 0 принят
            {"menu": m, "prompt": "be more aggressive"},                                                        # 1 правка промпта
            {"cond": [{"feature": "side", "op": "=", "value": "long"}], "rule": {"kind": "size_cap", "size_mult": 0.5}},   # 2 числа правила
            "lower the threshold on spread",                                                                    # 3 свободный текст
            {"cond": [{"feature": "range_5m_pct", "op": ">=", "value": 1.234}]},                                # 4 своё число
            {"cond": [{"feature": "side", "op": "=", "value": "long"}], "claim": dict(CLAIM, effect=0.9)},      # 5 effect модели
            {"menu": m, "reject_lesson": "L-1"},                                                                # 6 отклонить урок
            combo,                                                                                              # 7 сверх 3
        ]
        # лимит 3 считается по позициям: элементы с индексом >= 3 — TOO_MANY независимо от содержания
        self.answer(jid, {"schema": "tb.retro/1", "week_id": WEEK, "items": items})
        done = CA.collect(self.c, self.root, NOW_M + 60, CFG, policy=POLICY)["done"]
        self.assertEqual(done, [(jid, "LLM")])
        ev = json.loads(self.events()[-1][2])
        self.assertEqual(ev["accepted"], [0])
        self.assertEqual(ev["rejected"], [[1, "BAD_SCHEMA"], [2, "BAD_SCHEMA"], [3, "TOO_MANY"], [4, "TOO_MANY"], [5, "TOO_MANY"], [6, "TOO_MANY"], [7, "TOO_MANY"]])
        for it, want in ((items[3], "BAD_SCHEMA"), (items[4], "NOT_IN_DICT"), (items[5], "BAD_SCHEMA"), (items[6], "BAD_SCHEMA")):
            with self.assertRaises(CA.ItemRejected) as cm:
                CA.check_item(it, dict((x["id"], x["cond"]) for x in p["menu"]), p["dict"])
            self.assertEqual(cm.exception.code, want, it)
        rows = CA.candidates(self.c)
        self.assertEqual([(r["src"], r["status"], r["menu_id"]) for r in rows], [("llm", "new", "LW-mainnet-" + WEEK)])
        self.assertEqual(rows[0]["evidence"]["llm"]["role"], "retro")
        self.assertGreater(rows[0]["claim"]["effect"], 0.3)                                                # effect считает код
        self.assertEqual(self.rule_rows(), (0, 0))                                                         # правила нет без испытания
        cid = rows[0]["id"]
        self.assertIn((cid, "holdout", None), CA.accept(self.c, self.root, NOW_M, CFG, policy=POLICY))
        out = [o for o in CA.check_holdouts(self.c, self.root, NOW_H, CFG, policy=POLICY, mcfg=MCFG) if o[0] == cid]
        self.assertEqual(out[0][1], "hyp")                                                                 # тот же путь, что у кандидатов кода
        self.assertEqual(self.rule_rows(), (0, 0))

    def test_response_level_rejects_whole(self):
        jid = self.submit()
        m = self.menu_id(PT.read_job(self.root, jid)["payload"], "range_5m_pct", ">=")
        self.answer(jid, {"schema": "tb.retro/1", "week_id": WEEK, "items": [{"menu": m}], "prompt_patch": {"retro": "x"}})
        self.assertEqual(CA.collect(self.c, self.root, NOW_M + 60, CFG, policy=POLICY)["done"], [(jid, "LLM_REJECT")])
        self.assertEqual(json.loads(self.events()[-1][2])["why"], "BAD_SCHEMA")
        self.assertEqual(CA.candidates(self.c), [])
        jid = self.submit(now=NOW_M + 7 * D)
        wk = CA.week_id(NOW_M + 7 * D)
        self.answer(jid, {"schema": "tb.retro/1", "week_id": WEEK, "items": [{"menu": m}]})                # чужая неделя
        CA.collect(self.c, self.root, NOW_M + 7 * D + 60, CFG, policy=POLICY)
        self.assertEqual((self.events()[-1][0], self.events()[-1][1], json.loads(self.events()[-1][2])["why"]), ("LLM_REJECT", wk, "BAD_ID"))
        self.assertEqual(CA.candidates(self.c), [])

    def test_timeout_llm_false(self):
        jid = self.submit()
        rep = CA.collect(self.c, self.root, NOW_M + CFG["second_iteration"]["port"]["llm_wait_s"] + 1, CFG, policy=POLICY)
        self.assertEqual(rep["done"], [(jid, "LLM_FALSE")])
        self.assertEqual((self.events()[-1][0], json.loads(self.events()[-1][2])["why"]), ("LLM_FALSE", "TIMEOUT"))
        self.assertEqual(PT.own(self.root), [])

    def test_cycle_step_registered_and_continues_without_model(self):
        names = dict((s.name, s) for s in CY.default_steps())
        self.assertEqual(names["retro"].period, "daily")
        cyc = CY.Cycle(self.root, CFG, policy=POLICY, mcfg=MCFG)
        rep = cyc.run_pass(self.c, NOW_M)
        self.assertEqual(rep["errors"], {}, rep)
        self.assertEqual(rep["ran"]["retro"], "LLM_FALSE")
        self.assertEqual(rep["ran"]["menu"], "MENU")                                                       # кодовая ветка идёт
        rep = cyc.run_pass(self.c, NOW_M + D)
        self.assertEqual(rep["errors"], {}, rep)
        self.assertEqual(rep["ran"]["retro"], "NOT_DUE")
        self.assertEqual(cyc.acc["daily"]["retro"], "NOT_DUE")                                             # пропуск с кодом: сутки остаются полными


if __name__ == "__main__":
    unittest.main()
