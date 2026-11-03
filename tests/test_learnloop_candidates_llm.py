"""Кандидаты модели (R2 вторая итерация, задача 27; Requirements 12.2, 12.3, 16.3, 17.2, 17.4–17.6): вход роли кодом (меню, словарь с порогами-
токенами, только числа и идентификаторы), двухуровневая проверка ответа (уровень ответа отклоняет целиком; элемент — BAD_SCHEMA, NOT_IN_DICT,
DUPLICATE, TOO_MANY по порядку), скрининг принятых элементов и effect кодом, путь кандидата src=llm до урока hyp, шаги цикла day_review и
llm_collect через файловый порт, llm=false без демона и по таймауту — кодовая ветка не останавливается. На SQLite из test_learnloop_verify.build_db."""
import json
import os
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from test_learnloop_clusters import CFG, D, MCFG, POLICY, T0
from test_learnloop_verify import NOW_H, NOW_M, build_db
from tb.knowledge import store2
from tb.labels import store
from tb.learnloop import candidates as CA
from tb.learnloop import cycle as CY
from tb.learnloop import port as PT
from tb.memory import lessons as LS
from tb.platform import services as S

DAY = CA.day_id(NOW_M)
CLAIM = {"metric": "mean_r", "dir": "lower", "vs": "base"}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpl = tempfile.mkdtemp(prefix="tbll_llm_tmpl_")
        build_db(cls.tmpl)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpl, True)

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbll_llm_")
        self.addCleanup(shutil.rmtree, self.root, True)
        shutil.copytree(os.path.join(self.tmpl, "state"), os.path.join(self.root, "state"))
        store2._STATUS_CACHE.clear()
        self.c = store.open_rw(self.root)
        self.addCleanup(self.c.close)

    def request(self, now=NOW_M):
        payload, code = CA.llm_request(self.c, self.root, now, CFG, "day", policy=POLICY)
        self.assertIsNone(code)
        return payload

    def job(self, payload=None):
        payload = payload or self.request()
        return {"id": "L-test-1", "kind": "learnloop", "role": "day_reviewer", "meta": {"kind": "day", "id": payload["day_id"]}, "payload": payload}

    def menu_id(self, payload, feature, op):
        return [m["id"] for m in payload["menu"] if m["cond"][0]["feature"] == feature and m["cond"][0]["op"] == op][0]

    def accept(self, items, job=None, now=NOW_M, **kw):
        job = job or self.job()
        ans = dict({"schema": "tb.lesson_cand/1", "day_id": job["payload"]["day_id"], "items": items}, **kw)
        return CA.accept_llm(self.c, self.root, now, CFG, "day", job, ans, policy=POLICY)

    def hb(self, now=NOW_M, roles=None):
        S.write_hb("brain", {"protect_only": False, "budget": "ok", "learn_roles": roles or {"day_reviewer": True, "retro": True}}, root=self.root, now=now)

    def events(self, step="day_review"):
        return self.c.execute("SELECT code, ref, body FROM learn_events WHERE step=? ORDER BY seq", (step,)).fetchall()


class TestRequest(Base):
    def test_payload_numbers_enums_ids_only(self):
        p = self.request()
        self.assertEqual(p["day_id"], DAY)
        self.assertEqual(DAY, "D-20261016")                                             # прошедшие сутки UTC
        self.assertEqual([m["id"] for m in p["menu"]], ["M%d" % (i + 1) for i in range(len(p["menu"]))])
        self.assertEqual(p["k"], len(p["menu"]))
        self.assertEqual(p["limit"], 5)
        f = p["dict"]["features"]
        self.assertEqual(sorted(f["range_5m_pct"]["values"]), ["q10", "q20", "q80", "q90"])
        self.assertTrue(1.5 < f["range_5m_pct"]["values"]["q80"] < 1.7)                  # квантиль окна считает код
        self.assertEqual(f["side"], {"ops": ["="], "values": ["long", "short"]})
        self.assertNotIn("utc_session", f)
        self.assertEqual(p["dict"]["claim"], {"metric": ["mean_r"], "dir": ["lower"], "vs": ["base"]})
        self.assertGreater(p["day"]["runs"], 0)

        def walk(v):
            if isinstance(v, dict):
                for k, x in v.items():
                    self.assertRegex(k, PT.RE_TOKEN)
                    walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)
            elif isinstance(v, str):
                self.assertRegex(v, PT.RE_TOKEN)                                         # нет свободного текста
            else:
                self.assertTrue(v is None or isinstance(v, (int, float, bool)), v)
        walk(p)

    def test_no_data(self):
        root = tempfile.mkdtemp(prefix="tbll_llm_empty_")
        self.addCleanup(shutil.rmtree, root, True)
        c = store.open_rw(root)
        self.addCleanup(c.close)
        store2.migrate_v2a(c, root, explicit=True, backup=False, now=T0)
        self.assertEqual(CA.llm_request(c, root, NOW_M, CFG, "day", policy=POLICY), (None, "NO_DATA"))


class TestChecks(unittest.TestCase):
    DIC = {"features": {"spread_bps": {"ops": [">=", "<="], "values": {"q20": 1.9, "q80": 6.1}},
                        "coin_class": {"ops": ["=", "in"], "values": ["meme", "major"]}, "side": {"ops": ["="], "values": ["long", "short"]}}, "max_cond": 3}
    MENU = {"M1": [{"feature": "spread_bps", "op": ">=", "value": 6.1}]}

    def code(self, it):
        try:
            CA.check_item(it, self.MENU, self.DIC)
            return None
        except CA.ItemRejected as e:
            return e.code

    def test_item_codes(self):
        self.assertEqual(CA.check_item({"menu": "M1", "dir": "lower"}, self.MENU, self.DIC), (self.MENU["M1"], "M1"))
        self.assertEqual(CA.check_item({"menu": "M1"}, self.MENU, self.DIC)[1], "M1")
        cond, pick = CA.check_item({"cond": [{"feature": "spread_bps", "op": ">=", "value": "q80"}, {"feature": "coin_class", "op": "in", "value": ["meme", "major"]}]},
                                   self.MENU, self.DIC)
        self.assertIsNone(pick)
        self.assertEqual(cond, [{"feature": "coin_class", "op": "in", "value": ["major", "meme"]}, {"feature": "spread_bps", "op": ">=", "value": 6.1}])
        self.assertEqual(CA.check_item({"cond": [{"feature": "spread_bps", "op": "<=", "value": 1.9}]}, self.MENU, self.DIC)[0][0]["value"], 1.9)
        cases = [
            ("x", "BAD_SCHEMA"), ({"menu": "M1", "why": "a"}, "BAD_SCHEMA"), ({"menu": "M9"}, "NOT_IN_DICT"), ({"menu": "M1", "dir": "higher"}, "NOT_IN_DICT"),
            ({"cond": []}, "BAD_SCHEMA"), ({"cond": [{"feature": "spread_bps", "op": ">="}]}, "BAD_SCHEMA"),
            ({"cond": [{"feature": "regime", "op": "=", "value": "trend"}]}, "NOT_IN_DICT"),               # признак вне словаря (не проверяем на метках)
            ({"cond": [{"feature": "spread_bps", "op": "=", "value": "q80"}]}, "NOT_IN_DICT"),
            ({"cond": [{"feature": "spread_bps", "op": ">=", "value": 5.0}]}, "NOT_IN_DICT"),             # своё число модели
            ({"cond": [{"feature": "spread_bps", "op": ">=", "value": "q50"}]}, "NOT_IN_DICT"),
            ({"cond": [{"feature": "coin_class", "op": "=", "value": "unknown"}]}, "NOT_IN_DICT"),
            ({"cond": [{"feature": "coin_class", "op": "in", "value": ["meme", "meme"]}]}, "NOT_IN_DICT"),
            ({"cond": [{"feature": "spread_bps", "op": ">=", "value": "q80"}, {"feature": "spread_bps", "op": "<=", "value": "q20"}]}, "BAD_SCHEMA"),
            ({"cond": [{"feature": "side", "op": "=", "value": "long"}] * 4}, "BAD_SCHEMA"),
            ({"cond": [{"feature": "side", "op": "=", "value": "long"}], "claim": dict(CLAIM, effect=3)}, "BAD_SCHEMA"),   # effect считает код
            ({"cond": [{"feature": "side", "op": "=", "value": "long"}], "claim": dict(CLAIM, dir="higher")}, "NOT_IN_DICT"),
            ({"cond": [{"feature": "side", "op": "=", "value": "long"}], "rule": {"kind": "size_cap"}}, "BAD_SCHEMA"),
        ]
        for it, want in cases:
            self.assertEqual(self.code(it), want, it)

    def test_answer_level(self):
        ok = {"schema": "tb.lesson_cand/1", "day_id": DAY, "items": []}
        self.assertEqual(CA.check_answer(ok, "day", DAY), ([], None))
        self.assertEqual(CA.check_answer(dict(ok, day_id="D-20261001"), "day", DAY), (None, "BAD_ID"))
        self.assertEqual(CA.check_answer(dict(ok, prompt="x"), "day", DAY), (None, "BAD_SCHEMA"))
        self.assertEqual(CA.check_answer(dict(ok, schema="tb.retro/1"), "day", DAY), (None, "BAD_SCHEMA"))
        self.assertEqual(CA.check_answer("{}", "day", DAY), (None, "BAD_SCHEMA"))
        self.assertEqual(CA.check_answer({"schema": "tb.retro/1", "week_id": "W-2026-41", "items": []}, "week", "W-2026-41"), ([], None))


class TestAccept(Base):
    def test_partial_answer_accepted_items_and_reject_codes(self):
        job = self.job()
        p = job["payload"]
        m_range = self.menu_id(p, "range_5m_pct", ">=")
        combo = {"cond": [{"feature": "range_5m_pct", "op": ">=", "value": "q80"}, {"feature": "spread_bps", "op": "<=", "value": "q90"}], "claim": CLAIM}
        items = [combo, {"menu": m_range, "dir": "lower"}, {"menu": "M99"}, {"cond": [{"feature": "regime", "op": "=", "value": "trend"}]},
                 {"cond": [{"feature": "side", "op": "=", "value": "long"}, {"feature": "coin_class", "op": "=", "value": "meme"}], "claim": dict(CLAIM, effect=9)},
                 {"menu": m_range}]
        rep = self.accept(items, job)
        self.assertEqual(rep["code"], "LLM")
        self.assertEqual([i for i, _ in rep["accepted"]], [0, 1])
        self.assertEqual(rep["rejected"], [(2, "NOT_IN_DICT"), (3, "NOT_IN_DICT"), (4, "BAD_SCHEMA"), (5, "TOO_MANY")])
        rows = CA.candidates(self.c, status=("new",))
        self.assertEqual(sorted(r["id"] for r in rows), sorted(cid for _, cid in rep["accepted"]))
        for r in rows:
            self.assertEqual((r["src"], r["menu_id"], r["status"]), ("llm", "LD-mainnet-" + DAY, "new"))
            self.assertEqual(LS.validate_claim(r["claim"]), [])
            self.assertGreater(r["claim"]["effect"], 0.3)                               # effect считает код по кластерам теста окна
            self.assertEqual(r["k_space"], p["k"] + 1)                                    # одно своё условие модели расширяет K
            self.assertTrue(r["p_screen"] < CFG["verify"]["alpha"] / r["k_space"])
            self.assertEqual(r["evidence"]["llm"]["role"], "day_reviewer")
        ev = self.events()
        self.assertEqual(ev[-1][0], "LLM")
        self.assertEqual(json.loads(ev[-1][2])["rejected"], [[2, "NOT_IN_DICT"], [3, "NOT_IN_DICT"], [4, "BAD_SCHEMA"], [5, "TOO_MANY"]])

    def test_response_level_rejects_whole(self):
        job = self.job()
        m = self.menu_id(job["payload"], "range_5m_pct", ">=")
        rep = self.accept([{"menu": m}], job, day_id="D-20260101")
        self.assertEqual((rep["code"], rep["why"], rep["accepted"]), ("LLM_REJECT", "BAD_ID", []))
        rep = self.accept([{"menu": m}], job, note="x")
        self.assertEqual((rep["code"], rep["why"]), ("LLM_REJECT", "BAD_SCHEMA"))
        self.assertEqual(CA.candidates(self.c), [])

    def test_duplicates(self):
        job = self.job()
        m = self.menu_id(job["payload"], "range_5m_pct", ">=")
        cond = [dict(x) for x in [c for c in job["payload"]["menu"] if c["id"] == m][0]["cond"]]
        same = {"cond": [{"feature": "range_5m_pct", "op": ">=", "value": "q80"}], "claim": CLAIM}
        self.assertEqual(cond[0]["value"], job["payload"]["dict"]["features"]["range_5m_pct"]["values"]["q80"])
        rep = self.accept([{"menu": m}, same], job)                                     # одно и то же условие дважды в ответе
        self.assertEqual(rep["rejected"], [(1, "DUPLICATE")])
        rep = self.accept([{"menu": m}], job)                                           # уже кандидат new
        self.assertEqual(rep["rejected"], [(0, "DUPLICATE")])

    def test_code_candidate_is_duplicate(self):
        CA.run_menu(self.c, self.root, NOW_M, CFG, policy=POLICY)
        job = self.job()
        ok_items = [m for m in job["payload"]["menu"] if m["ok"]]
        self.assertTrue(ok_items)
        rep = self.accept([{"menu": ok_items[0]["id"]}], job)
        self.assertEqual(rep["rejected"], [(0, "DUPLICATE")])

    def test_screen_and_broad_rejected(self):
        job = self.job()
        items = [{"cond": [{"feature": "ret_5m_pct", "op": ">=", "value": "q80"}, {"feature": "chg_24h_pct", "op": "<=", "value": "q90"}]},   # без эффекта
                 {"cond": [{"feature": "side", "op": "=", "value": "long"}]}]                                                             # половина прогонов
        rep = self.accept(items, job)
        self.assertEqual(rep["accepted"], [])
        codes = dict(rep["rejected"])
        self.assertIn(codes[0], ("NOT_SIGNIFICANT", "NO_OOS_SIGN", "BELOW_N"))
        self.assertEqual(codes[1], "TOO_BROAD")

    def test_llm_candidate_goes_code_path_to_hyp(self):
        job = self.job()
        m = self.menu_id(job["payload"], "range_5m_pct", ">=")
        rep = self.accept([{"menu": m, "dir": "lower"}], job)
        cid = rep["accepted"][0][1]
        acc = CA.accept(self.c, self.root, NOW_M, CFG, policy=POLICY)
        self.assertIn((cid, "holdout", None), acc)
        out = [o for o in CA.check_holdouts(self.c, self.root, NOW_H, CFG, policy=POLICY, mcfg=MCFG) if o[0] == cid]
        self.assertEqual(out[0][1], "hyp")
        l = LS.read_all(self.root)[out[0][2]]
        self.assertEqual((l["status"], l["evidence"]["src"], l["evidence"]["via"]), ("hyp", "labels", "llm"))
        self.assertGreater(l["claim"]["effect"], 0.3)


class TestPortSteps(Base):
    def test_no_daemon_llm_false_code_continues(self):
        rep = CA.submit_review(self.c, self.root, NOW_M, CFG, "day", policy=POLICY)
        self.assertEqual((rep["code"], rep["why"]), ("LLM_FALSE", "NO_DAEMON"))
        self.assertEqual(PT.own(self.root), [])                                          # задания не копятся
        ev = self.events()
        self.assertEqual((ev[0][0], ev[0][1], json.loads(ev[0][2])), ("LLM_FALSE", DAY, {"llm": False, "why": "NO_DAEMON"}))
        self.assertEqual(CA.run_menu(self.c, self.root, NOW_M, CFG, policy=POLICY)["code"], "MENU")    # кодовая ветка работает
        self.hb(roles={"day_reviewer": False})
        self.assertEqual(CA.submit_review(self.c, self.root, NOW_M + 60, CFG, "day", policy=POLICY)["why"], "NO_ROLE")

    def test_submit_collect_roundtrip(self):
        self.hb()
        rep = CA.submit_review(self.c, self.root, NOW_M, CFG, "day", policy=POLICY)
        self.assertEqual(rep["code"], "LLM_SUBMIT")
        jid = rep["job"]
        self.assertEqual(CA.submit_review(self.c, self.root, NOW_M + 5, CFG, "day", policy=POLICY)["code"], "LLM_PENDING")
        self.assertEqual(CA.collect(self.c, self.root, NOW_M + 10, CFG, policy=POLICY)["done"], [])          # ответа ещё нет
        job = PT.read_job(self.root, jid)
        self.assertEqual(job["meta"], {"kind": "day", "id": DAY, "env": "mainnet"})
        m = self.menu_id(job["payload"], "range_5m_pct", ">=")
        out = {"schema": "tb.lesson_cand/1", "day_id": DAY, "items": [{"menu": m}, {"menu": "M77"}]}
        S.write_json(os.path.join(PT.jobs_dir(self.root), jid + ".result.json"), {"id": jid, "kind": "learnloop", "ok": True, "output": out, "err": None, "usd": 0.001})
        rep = CA.collect(self.c, self.root, NOW_M + 60, CFG, policy=POLICY)
        self.assertEqual(rep["done"], [(jid, "LLM")])
        self.assertEqual(os.listdir(PT.jobs_dir(self.root)), [])
        self.assertEqual([r["src"] for r in CA.candidates(self.c)], ["llm"])
        self.assertEqual(CA.collect(self.c, self.root, NOW_M + 70, CFG, policy=POLICY)["done"], [])          # повтор ничего не добавляет
        self.assertEqual([e[0] for e in self.events()], ["LLM_SUBMIT", "LLM"])

    def test_timeout_and_failed_answer_llm_false(self):
        self.hb()
        jid = CA.submit_review(self.c, self.root, NOW_M, CFG, "day", policy=POLICY)["job"]
        rep = CA.collect(self.c, self.root, NOW_M + CFG["second_iteration"]["port"]["llm_wait_s"] + 1, CFG, policy=POLICY)
        self.assertEqual(rep["done"], [(jid, "LLM_FALSE")])
        self.assertEqual(json.loads(self.events()[-1][2])["why"], "TIMEOUT")
        self.assertEqual(PT.own(self.root), [])
        self.hb(now=NOW_M + D)
        jid = CA.submit_review(self.c, self.root, NOW_M + D, CFG, "day", policy=POLICY)["job"]
        S.write_json(os.path.join(PT.jobs_dir(self.root), jid + ".result.json"), {"id": jid, "kind": "learnloop", "ok": False, "output": None, "err": "NO_PROFILE"})
        CA.collect(self.c, self.root, NOW_M + D + 30, CFG, policy=POLICY)
        self.assertEqual(json.loads(self.events()[-1][2]), {"llm": False, "why": "NO_PROFILE", "job": jid})
        self.assertEqual(CA.candidates(self.c), [])

    def test_cycle_steps_registered_and_pass_without_model(self):
        names = dict((s.name, s) for s in CY.default_steps())
        self.assertEqual(names["day_review"].period, "daily")
        self.assertEqual(names["llm_collect"].period, "pass")
        cyc = CY.Cycle(self.root, CFG, policy=POLICY, mcfg=MCFG)
        rep = cyc.run_pass(self.c, NOW_M)
        self.assertEqual(rep["errors"], {}, rep)
        self.assertEqual(rep["ran"]["day_review"], "LLM_FALSE")
        self.assertEqual(rep["ran"]["menu"], "MENU")
        self.assertEqual(cyc.acc["daily"]["day_review"], "LLM_FALSE")                   # сутки помечены llm=false в сводке суток


if __name__ == "__main__":
    unittest.main()
