"""R1.11: мозг на кандидатах. Ответ модели entry_reply/1 без цен (reply_schema роли entry_scout), PRICE_KEY, разворачивание кандидата кодом (entryplan.expand через
runner._entry_obj), NO_CANDIDATES без вызова модели и порядок после SYM_BUSY, слияние тегов, basis.cand решения. Всё во временных корнях, без сети и LLM."""
import copy
import json
import unittest

import _bootstrap  # noqa: F401
import test_brain_entry as TE
from _brain import BrainBase
from tb.brain import entryplan, roles_runtime, validate
from tb.core import bus
from tb.gateway import catalog
from tb.llm import schema as schema_mod

NO_TOOLS = {"json_mode": True, "strict": False, "tools": False}


def reply(**kw):
    o = {"decision": "propose", "candidate": 1, "setup": "level_reclaim", "thesis": "возврат выше дневного уровня 145.0 после прокола, оборот 3.1x",
         "why": "возврат уровня | RR 2.3, a5 0.25% | стоп 1.1%", "conf": 0.6}
    o.update(kw)
    return o


def cand(**kw):
    c = {"n": 1, "side": "long", "level": 145.0, "stop": 143.4, "tp": [148.2, 150.5], "horizon": "intraday", "setup": "level_reclaim", "confirm": "candle",
         "ok": True, "geometry_ver": "g7"}
    c.update(kw)
    return c


class ReplySchema(unittest.TestCase):
    """Схема entry_reply/1, PRICE_KEY, обязательные поля по decision."""

    def check(self, obj):
        raw, errs = schema_mod.check_text(json.dumps(obj), validate.SCHEMAS["entry_reply/1"])
        return errs or validate.semantic("entry_reply/1", raw, {})

    def test_good_propose_and_skip(self):
        self.assertEqual(self.check(reply()), [])
        self.assertEqual(self.check(reply(size_mult=0.3, tags=["no-confirm"], hypothesis="reclaim-vol-surge")), [])
        self.assertEqual(self.check({"decision": "skip", "skip_code": "rr_low", "why": "цель близко", "conf": 0.7}), [])
        self.assertEqual(self.check({"decision": "skip", "skip_code": "no_confirm", "watch": {"candidate": 3}, "why": "жду уровень", "conf": 0.7}), [])

    def test_fields_by_decision(self):
        o = reply(); o.pop("candidate")
        self.assertIn("ARGS_MISSING:candidate", self.check(o))
        self.assertIn("ARGS_EXTRA:skip_code", self.check(reply(skip_code="rr_low")))
        self.assertIn("ARGS_MISSING:skip_code", self.check({"decision": "skip", "why": "x", "conf": 0.5}))
        self.assertIn("ARGS_EXTRA:candidate", self.check({"decision": "skip", "skip_code": "other", "candidate": 1, "why": "x", "conf": 0.5}))
        self.assertTrue(self.check(reply(candidate=11)))                         # 1..10
        self.assertTrue(self.check(reply(size_mult=0.9)))                        # не выше потолка схемы
        self.assertTrue(self.check(reply(setup="none")))
        self.assertTrue(self.check(reply(schema="tb.entry/1")))                  # лишних полей нет

    def test_price_keys_found_on_any_listed_key(self):
        for k in validate.PRICE_KEYS:
            self.assertEqual(validate.price_keys(reply(**{k: 1})), [k])
        self.assertEqual(validate.price_keys(reply()), [])
        self.assertEqual(validate.price_keys({"decision": "skip", "watch": {"candidate": 1, "level": 1.0}}), ["level"])         # и внутри watch
        self.assertEqual(validate.price_keys({"STOP": 1, "Tp": [1]}), ["stop", "tp"])

    def test_role_declares_reply_schema_and_keeps_output_schema(self):
        reg = roles_runtime.load(_bootstrap.ROOT)
        self.assertEqual(reg.errors, [])
        r = reg.get("entry_scout")
        self.assertEqual((r.output_schema, r.reply_schema), ("entry/1", "entry_reply/1"))
        self.assertIs(r.schema, validate.ENTRY)
        self.assertIs(r.reply_obj, validate.ENTRY_REPLY)
        self.assertIn("RESPONSE SCHEMA (entry_reply/1)", r.system)               # модель видит схему ответа без цен
        self.assertNotIn('"stop"', r.system.split("RESPONSE SCHEMA")[1])
        self.assertIsNone(reg.get("position_manager").reply_schema)
        raw = dict(json.load(open(_bootstrap.ROOT + "/config/roles/entry_scout.json", encoding="utf-8")))
        names, evt = set(catalog.TOOLS), set(bus.EVENT_TYPES)
        self.assertIsNone(roles_runtime._check(raw, "entry_scout", names, evt))
        self.assertIsNotNone(roles_runtime._check(dict(raw, reply_schema="nope/9"), "entry_scout", names, evt))
        self.assertIsNotNone(roles_runtime._check(dict(raw, output_schema="pm/1"), "entry_scout", names, evt))


class Expand(unittest.TestCase):
    """entryplan.expand: цены и параметры из кандидата, слияние тегов и размера."""

    def run_expand(self, c, **a):
        a.setdefault("candidate", 1)
        a.setdefault("setup", "level_reclaim")
        a.setdefault("why", "почему | факт 1 | риск")
        return entryplan.expand("es_take", a, [c], "SOL", 77)

    def test_prices_are_the_candidates(self):
        obj, errs, c = self.run_expand(cand(zone={"low": 144.9, "high": 145.1}, entry_type="limit"))
        self.assertEqual(errs, [])
        self.assertEqual((obj["level"], obj["stop"], obj["tp"], obj["horizon"], obj["side"], obj["zone"], obj["entry_type"], obj["event_seq"], obj["sym"]),
                         (145.0, 143.4, [148.2, 150.5], "intraday", "long", {"low": 144.9, "high": 145.1}, "limit", 77, "SOL"))
        self.assertEqual(schema_mod.validate(validate.ENTRY, obj), [])

    def test_tags_candidate_first_then_model_without_repeats_up_to_two(self):
        obj = self.run_expand(cand(tags=["no-confirm"]), tags=["blackout", "no-confirm"])[0]
        self.assertEqual(obj["tags"], ["no-confirm", "blackout"])
        self.assertEqual(self.run_expand(cand(tags=["no-confirm", "blackout"]), tags=["blackout"])[0]["tags"], ["no-confirm", "blackout"])
        self.assertEqual(self.run_expand(cand(), tags=["blackout"])[0]["tags"], ["blackout"])
        self.assertNotIn("tags", self.run_expand(cand())[0])
        self.assertEqual(entryplan.merge_tags(["blackout"], ["no-confirm", "blackout"], ["no-confirm"]), ["blackout", "no-confirm"])
        self.assertEqual(entryplan.merge_tags(["x-unknown"], None), [])
        obj = self.run_expand(cand(tags=["no-confirm"]), tags=["blackout"])[0]
        self.assertEqual(schema_mod.validate(validate.ENTRY, obj), [])           # лимит схемы entry/1 (2 тега) не нарушен

    def test_size_mult_only_decreases(self):
        self.assertEqual(self.run_expand(cand(size_mult=0.4), size_mult=0.3)[0]["size_mult"], 0.3)
        self.assertEqual(self.run_expand(cand(size_mult=0.4), size_mult=0.5)[0]["size_mult"], 0.4)          # потолок дня из кандидата
        self.assertEqual(self.run_expand(cand(), size_mult=0.2)[0]["size_mult"], 0.2)
        self.assertEqual(self.run_expand(cand(size_mult=0.4))[0]["size_mult"], 0.4)

    def test_old_codes(self):
        self.assertEqual(self.run_expand(cand(), candidate=2)[1], ["NO_CANDIDATE:2_OF:1"])
        self.assertEqual(self.run_expand(cand(ok=False, fail="rr_low:1.1"))[1], ["CANDIDATE_FAILED:rr_low:1.1"])
        self.assertEqual(self.run_expand(cand(), candidate="x")[1], ["RANGE:candidate"])

    def test_reply_call_mapping(self):
        self.assertEqual(entryplan.reply_call(reply(size_mult=0.2))[0], "es_take")
        n, a = entryplan.reply_call({"decision": "skip", "skip_code": "rr_low", "watch": {"candidate": 2}, "why": "w", "conf": 0.4})
        self.assertEqual((n, a["candidate"], a["code"]), ("es_watch", 2, "rr_low"))
        self.assertEqual(entryplan.reply_call({"decision": "skip", "skip_code": "other", "why": "w", "conf": 0.4})[0], "es_skip")
        obj = entryplan.expand(*entryplan.reply_call({"decision": "skip", "skip_code": "rr_low", "watch": {"candidate": 1}, "why": "w", "conf": 0.4}), [cand()], "SOL", 5)[0]
        self.assertEqual((obj["skip_code"], obj["watch"]["level"], obj["watch"]["stop"], obj["watch"]["tp"]), ("rr_low", 145.0, 143.4, 148.2))


class Precheck(unittest.TestCase):
    def ctx(self, cands, **kw):
        c = validate.entry_context(TE.SCAN_BRIEF, TE.EV)
        c["cands"] = cands
        c.update(kw)
        return c

    def test_no_candidates_only_when_list_has_no_ok(self):
        bad = [cand(ok=False, fail="rr_low:1.1")]
        self.assertEqual(validate.entry_precheck(self.ctx(bad)), "NO_CANDIDATES")
        self.assertIsNone(validate.entry_precheck(self.ctx(None)))                # кандидатов не считали (монета занята, урезанное досье)
        self.assertIsNone(validate.entry_precheck(self.ctx([])))
        self.assertIsNone(validate.entry_precheck(self.ctx(bad + [cand(n=2)])))
        self.assertIn("NO_CANDIDATES", validate.ENTRY_PRECHECK_CODES)

    def test_other_reasons_come_first(self):
        bad = [cand(ok=False, fail="rr_low:1.1")]
        self.assertEqual(validate.entry_precheck(self.ctx(bad, slots=0)), "NO_SLOTS")
        self.assertEqual(validate.entry_precheck(self.ctx(bad, busy=["SOL"])), "SYM_BUSY")
        self.assertEqual(validate.entry_precheck(self.ctx(bad, pause_sym=5)), "PAUSE_SYM")

    def test_flagged_stale_idea_keeps_the_model(self):
        """Живая идея помечена idea_stale и пришёл другой сигнал: es_revise (keep/cancel) возможен и без ok-кандидата, модель зовётся."""
        self.assertIsNone(validate.entry_precheck(self.ctx([cand(ok=False, fail="rr_low:1.1")], stale_ids=["I1"])))


class ReplyRun(BrainBase):
    """Раннер: JSON-путь (модель без инструментов), режим инструментов, предпроверка."""
    setUp, scan_job = TE.Scout.setUp, TE.Scout.scan_job
    tool_brief = TE.Scout.tool_brief

    def json_runner(self, answers, **flags):
        r = self.runner({"ds-flash": answers})
        r.model_flags = lambda p: dict(NO_TOOLS, **flags)
        return r

    def ok_cand(self, b=None):
        return [c for c in (b or self.gw.brief)["cands"] if c.get("ok")][0]

    def brain_decisions(self):
        return [x for x in self.decisions() if x["what"] == "brain_decision"]

    def test_property9_price_key_rejected_then_retry_accepted(self):
        self.gw.brief = self.tool_brief()
        n = self.ok_cand()["n"]
        r = self.json_runner([reply(candidate=n, stop=1.0, level=2.0), reply(candidate=n)])
        j, s = self.scan_job()
        r.step()
        self.assertEqual(self.get_job(j["id"])["result"]["roles"][0]["code"], "OK", self.get_job(j["id"])["result"])
        c = r.client_for("ds-flash")
        self.assertEqual(len(c.calls), 2)
        self.assertIn("PRICE_KEY", c.calls[1]["req"].messages[-1].content if hasattr(c.calls[1]["req"].messages[-1], "content") else str(c.calls[1]["req"].messages[-1]))
        sub = self.gw.tool_calls("submit_plan")
        self.assertEqual(len(sub), 1)
        plan = sub[0]["args"]["plan"]
        oc = self.ok_cand()
        self.assertEqual((plan["stop"]["price"], [t["price"] for t in plan["targets"]]), (oc["stop"], oc["tp"]))      # цены плана = цены кандидата, не ответа

    def test_property9_every_price_field_blocks_the_plan(self):
        self.gw.brief = self.tool_brief()
        n = self.ok_cand()["n"]
        for i, (k, v) in enumerate([("level", 145.0), ("zone", {"low": 1, "high": 2}), ("stop", 1.0), ("tp", [2.0]), ("price", 3.0), ("sl", 1.0),
                                    ("horizon", "scalp"), ("confirm", "none"), ("entry_type", "limit")]):
            r = self.json_runner([reply(candidate=n, **{k: v})] * 3)
            j, s = self.scan_job(jid="J-p%d" % i)
            r.step()
            res = self.get_job(j["id"])["result"]["roles"][0]
            self.assertEqual(res["code"], "BAD_FORMAT", (k, res))
            self.assertEqual(self.gw.tool_calls("submit_plan"), [], k)
            self.assertIn("PRICE_KEY:%s" % k, str(self.brain_decisions()[-1]["why"]), k)           # причина отказа записана в журнал решений

    def test_level_stop_tp_of_expanded_object_equal_candidate(self):
        self.gw.brief = self.tool_brief()
        oc = self.ok_cand()
        ctx = {"cands": self.gw.brief["cands"], "sym": "SOL", "event_seq": 5}
        reg = roles_runtime.load(_bootstrap.ROOT)
        obj, errs = entryplan.check_reply(json.dumps(reply(candidate=oc["n"])), reg.get("entry_scout"), ctx)
        self.assertEqual(errs, [])
        self.assertEqual((obj["level"], obj["stop"], obj["tp"], obj["side"], obj["horizon"]), (oc["level"], oc["stop"], oc["tp"], oc["side"], oc["horizon"]))
        self.assertEqual(ctx["_cand"], {"n": oc["n"], "geometry_ver": oc["geometry_ver"]})
        errs = entryplan.check_reply(json.dumps(reply(candidate=oc["n"], stop=oc["stop"])), reg.get("entry_scout"), ctx)[1]
        self.assertEqual(errs, ["PRICE_KEY:stop"])
        far = len(self.gw.brief["cands"]) + 1
        self.assertEqual(entryplan.check_reply(json.dumps(reply(candidate=far)), reg.get("entry_scout"), ctx)[1], ["NO_CANDIDATE:%d_OF:%d" % (far, far - 1)])
        self.assertEqual(entryplan.check_reply(json.dumps(reply(candidate=11)), reg.get("entry_scout"), ctx)[1][0][:5], "RANGE")      # вне 1..10 отсекает схема ответа
        bad = [c for c in self.gw.brief["cands"] if c.get("fail")]
        if bad:
            self.assertTrue(entryplan.check_reply(json.dumps(reply(candidate=bad[0]["n"])), reg.get("entry_scout"), ctx)[1][0].startswith("CANDIDATE_FAILED"))

    def test_plan_from_es_take_equals_plan_from_json_path(self):
        from fakes.fake_llm import Tool
        self.gw.brief = self.tool_brief()
        oc = self.ok_cand()
        why = "возврат уровня | RR %.2f | стоп за свингом" % oc["rr_net"]
        thesis = "возврат выше дневного уровня 145.0 после прокола, оборот 3.1x"
        r = self.runner({"ds-flash": [Tool("es_take", {"candidate": oc["n"], "setup": "level_reclaim", "thesis": thesis, "why": why}), "готово"]})
        j, s = self.scan_job(jid="J-tool")
        r.step()
        r = self.json_runner([reply(candidate=oc["n"], setup="level_reclaim", thesis=thesis, why=why, conf=0.6)])
        j2, s2 = self.scan_job(jid="J-json")
        r.step()
        sub = self.gw.tool_calls("submit_plan")
        self.assertEqual(len(sub), 2, self.get_job("J-json")["result"])
        a, b = (copy.deepcopy(x["args"]["plan"]) for x in sub)
        for p in (a, b):
            for k in ("source", "id", "created", "ts"):
                p.pop(k, None)
        self.assertEqual(a, b)

    def test_basis_cand_in_decision_both_paths(self):
        from fakes.fake_llm import Tool
        self.gw.brief = self.tool_brief()
        oc = self.ok_cand()
        want = {"n": oc["n"], "geometry_ver": oc["geometry_ver"]}
        self.assertTrue(want["geometry_ver"])
        r = self.runner({"ds-flash": [Tool("es_take", {"candidate": oc["n"], "setup": "level_reclaim", "why": "возврат уровня | RR 2 | риск"}), "готово"]})
        self.scan_job(jid="J-t")
        r.step()
        r = self.json_runner([reply(candidate=oc["n"])])
        self.scan_job(jid="J-j")
        r.step()
        d = [x for x in self.brain_decisions() if x["res"]["code"] in ("OK", "QUEUED")]
        self.assertEqual(len(d), 2, self.brain_decisions())
        self.assertEqual([x["basis"].get("cand") for x in d], [want, want])

    def test_skip_decisions_have_no_cand_and_plan_tags_not_widened(self):
        self.gw.brief = self.tool_brief()
        r = self.json_runner([{"decision": "skip", "skip_code": "no_setup", "why": "нет сетапа | цена между уровнями 145.2 | риск", "conf": 0.5}])
        self.scan_job()
        r.step()
        d = self.brain_decisions()
        self.assertEqual([x["res"]["code"] for x in d], ["SKIP"])
        self.assertNotIn("cand", d[0]["basis"])
        obj = entryplan.expand("es_take", {"candidate": 1, "setup": "level_reclaim", "tags": ["blackout"], "why": "w"}, [cand(tags=["no-confirm"])], "SOL", 1)[0]
        ctx = validate.entry_context(self.tool_brief(), TE.EV)
        ctx["event_seq"] = 1
        plan = entryplan.build(obj, ctx)
        self.assertLessEqual(len(plan["tags"].get("extra", [])), 3)               # теги модели и кандидата (до 2) + off-plan; лимит плана (5) не превышен
        self.assertEqual(plan["tags"]["extra"][:2], ["no-confirm", "blackout"])

    def test_skip_with_watch_creates_idea_from_candidate(self):
        from tb.core import ideas
        self.gw.brief = self.tool_brief()
        oc = [c for c in self.gw.brief["cands"] if c.get("fail")] or [self.gw.brief["cands"][0]]
        r = self.json_runner([{"decision": "skip", "skip_code": "no_confirm", "watch": {"candidate": oc[0]["n"]}, "why": "жду уровень | до него 0.2% | риск", "conf": 0.5}])
        self.scan_job()
        r.step()
        rows = [i for i in ideas.load_ideas(self.root) if i.get("src") == "entry_scout"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["level"], rows[0]["stop"]), (oc[0]["level"], oc[0]["stop"]))
        self.assertEqual(self.get_job("J-s1")["result"]["roles"][0]["code"], "SKIP")

    def test_strict_response_format_is_the_reply_schema(self):
        self.gw.brief = self.tool_brief()
        r = self.json_runner([reply(candidate=self.ok_cand()["n"])], json_mode=False, strict=True)
        self.scan_job()
        r.step()
        rf = r.client_for("ds-flash").calls[0]["req"].response_format
        self.assertIs(rf["schema"], validate.ENTRY_REPLY)

    def all_failing(self):
        b = self.tool_brief()
        for c in b["cands"]:
            c["ok"], c["fail"] = False, "rr_low:1.1"
        return b

    def test_no_candidates_skips_without_model_call(self):
        self.gw.brief = self.all_failing()
        r = self.json_runner([reply()])
        j, s = self.scan_job()
        r.step()
        res = self.get_job(j["id"])["result"]["roles"][0]
        self.assertEqual(res["code"], "NO_CANDIDATES")
        self.assertEqual(self.factory.calls(), 0)
        self.assertEqual(self.gw.tool_calls("submit_plan"), [])
        self.assertEqual([x["res"]["code"] for x in self.brain_decisions()], ["NO_CANDIDATES"])

    def test_sym_busy_comes_before_no_candidates(self):
        b = self.all_failing()
        b["plans"] = [{"id": "PL-1028-SOL-01", "sym": "SOL", "side": "long", "status": "armed", "rev": 1}]
        self.gw.brief = b
        r = self.json_runner([reply()])
        j, s = self.scan_job()
        r.step()
        self.assertEqual(self.get_job(j["id"])["result"]["roles"][0]["code"], "SYM_BUSY")
        self.assertEqual(self.factory.calls(), 0)

    def test_busy_coin_brief_has_no_candidates_list(self):
        """Монета занята: бриф не считает кандидатов (cands None), раннер оставляет None, а не []: NO_CANDIDATES не применяется."""
        b = self.tool_brief()
        b["cands"] = None
        b["plans"] = [{"id": "PL-1028-SOL-01", "sym": "SOL", "side": "long", "status": "armed", "rev": 1}]
        self.gw.brief = b
        r = self.json_runner([reply()])
        j, s = self.scan_job()
        r.step()
        self.assertEqual(self.get_job(j["id"])["result"]["roles"][0]["code"], "SYM_BUSY")

    def test_brief_without_cands_still_calls_the_model(self):
        self.gw.brief = copy.deepcopy(TE.SCAN_BRIEF)
        r = self.json_runner([{"decision": "skip", "skip_code": "no_setup", "why": "нет уровня | цена 145.2 | риск", "conf": 0.5}])
        j, s = self.scan_job()
        r.step()
        self.assertEqual(self.factory.calls(), 1)
        self.assertEqual(self.get_job(j["id"])["result"]["roles"][0]["code"], "SKIP")

    def test_json_path_rejects_unknown_candidate_with_old_codes(self):
        self.gw.brief = self.tool_brief()
        n = len(self.gw.brief["cands"]) + 3
        r = self.json_runner([reply(candidate=n), reply(candidate=n), reply(candidate=n)])
        j, s = self.scan_job()
        r.step()
        res = self.get_job(j["id"])["result"]["roles"][0]
        self.assertEqual(res["code"], "BAD_FORMAT")
        self.assertEqual(self.gw.tool_calls("submit_plan"), [])
        self.assertIn("NO_CANDIDATE:%d_OF:%d" % (n, len(self.gw.brief["cands"])), str(self.brain_decisions()[-1]["why"]))

    def test_tool_mode_text_rejects_full_entry_json(self):
        """Режим инструментов: текст без вызова инструмента проверяется только по entry_reply/1; полный tb.entry/1 с ценами отклоняется PRICE_KEY, план не уходит."""
        self.gw.brief = self.tool_brief()
        oc = self.ok_cand()
        full = TE.propose(level=oc["level"], stop=oc["stop"], tp=oc["tp"], side=oc["side"], horizon=oc["horizon"])
        r = self.runner({"ds-flash": [full, full, full]})
        j, s = self.scan_job()
        r.step()
        self.assertEqual(self.get_job(j["id"])["result"]["roles"][0]["code"], "BAD_FORMAT", self.get_job(j["id"])["result"])
        self.assertEqual(self.gw.tool_calls("submit_plan"), [])
        why = str(self.brain_decisions()[-1]["why"])
        self.assertIn("PRICE_KEY", why)
        self.assertIn("schema", why)

    def test_tool_mode_text_reply_form_is_accepted_and_json_path_rejects_full_entry(self):
        self.gw.brief = self.tool_brief()
        oc = self.ok_cand()
        r = self.runner({"ds-flash": [TE.propose(), reply(candidate=oc["n"])]})          # полный entry/1, затем ответ без цен после просьбы повтора
        j, s = self.scan_job()
        r.step()
        self.assertEqual(self.get_job(j["id"])["result"]["roles"][0]["code"], "OK", self.get_job(j["id"])["result"])
        self.assertEqual(len(self.gw.tool_calls("submit_plan")), 1)
        errs = entryplan.check_reply(json.dumps(TE.propose()), roles_runtime.load(_bootstrap.ROOT).get("entry_scout"), {"cands": [cand()], "sym": "SOL"})[1]
        self.assertEqual(errs, ["PRICE_KEY:confirm,horizon,level,schema,stop,tp"])
        schema_only = {"schema": "tb.entry/1", "decision": "skip", "skip_code": "other", "why": "w", "conf": 0.5}
        self.assertEqual(entryplan.check_reply(json.dumps(schema_only), roles_runtime.load(_bootstrap.ROOT).get("entry_scout"), {})[1], ["PRICE_KEY:schema"])


if __name__ == "__main__":
    unittest.main()
