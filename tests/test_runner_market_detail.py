"""R1.7, задача 21: get_market_detail в режимах инструментов раннера (es_* у entry_scout, pm_* у position_manager).
Модель выбирает только части, монету подставляет раннер из задания/плана; вызов чтения не действие (действие после него исполняется один раз);
без tools_optional инструмента в списке нет. Фейковые шлюз и модель, временный корень."""
import copy
import json
import os

import _bootstrap  # noqa: F401
import test_brain_entry as E
from _brain import PLAN, BrainBase
from fakes.fake_llm import Tool
from tb.brain import runner as runner_mod

MD = "get_market_detail"
SKIP = Tool("es_skip", {"code": "no_setup", "why": "нет сетапа | цена между уровнями | риск"})


def md_answer(a):
    return {"ok": True, "sym": a.get("sym"), "warn": ["LOW_COV"], "oi": {"musd": 123.4}}


class Base(BrainBase):
    def setUp(self):
        BrainBase.setUp(self)
        self.set_platform(mode="paper")
        self.gw.responses[MD] = md_answer

    def drop_optional(self, rid):
        p = self.p("config", "roles", rid + ".json")
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        raw.pop("tools_optional", None)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(raw, f)

    def names(self, pid, i=0):
        return [t["name"] for t in self.factory.clients[pid].calls[i]["req"].tools]

    def tool_result_seen(self, pid):
        """Ответ инструмента дошёл до модели: во втором запросе есть его числа."""
        return "123.4" in repr(self.factory.clients[pid].calls[1]["req"].messages)


class EntryScout(Base):
    def setUp(self):
        Base.setUp(self)
        self.gw.brief = copy.deepcopy(E.SCAN_BRIEF)
        self.gw.brief["cands"] = [copy.deepcopy(E.REPLY_CAND)]
        self.gw.responses["list_plans"] = {"ok": True, "plans": []}

    def scan_job(self, jid="J-s1", sym="SOL"):
        s = self.emit("scanner_signal", {"kind": "hot_enter", "px": 145.2, "wake": True}, plan=None, sym=sym)
        return self.job(jid, seqs=[s], types=("scanner_signal",), key="sym:%s" % sym, plan=None, kinds=["hot_enter"])

    def test_detail_then_skip(self):
        r = self.runner({"ds-flash": [Tool(MD, {"parts": ["oi", "book", "bogus"], "sym": "BTC"}), SKIP]})
        j = self.scan_job()
        r.step()
        self.assertIn(MD, self.names("ds-flash"))
        c = self.gw.tool_calls(MD)
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["args"], {"sym": "SOL", "parts": ["oi", "book"]})         # монета из задания, не от модели; лишняя часть отброшена
        self.assertEqual((c[0]["agent"], c[0]["meta"].get("role")), ("api", "entry_scout"))
        self.assertTrue(self.tool_result_seen("ds-flash"))
        self.assertEqual(self.get_job(j["id"])["result"]["roles"][0]["code"], "SKIP")      # действие после чтения исполнилось
        sk = [x for x in self.decisions() if x["what"] == "brain_decision"]
        self.assertEqual(len(sk), 1)

    def test_repeated_reads_go_to_gateway_action_once(self):
        """Чтение не действие: повторы не блокирует раннер (их ограничивает шлюз), действие после них исполняется один раз."""
        r = self.runner({"ds-flash": [Tool(MD, {}), Tool(MD, {"parts": ["liq"]}), SKIP]})
        self.scan_job()
        r.step()
        self.assertEqual([c["args"] for c in self.gw.tool_calls(MD)], [{"sym": "SOL"}, {"sym": "SOL", "parts": ["liq"]}])
        self.assertEqual(len([x for x in self.decisions() if x["what"] == "brain_decision"]), 1)

    def test_note_and_spec(self):
        r = self.runner({"ds-flash": [SKIP]})
        self.scan_job()
        r.step()
        req = self.factory.clients["ds-flash"].calls[0]["req"]
        spec = [t for t in req.tools if t["name"] == MD][0]
        self.assertEqual(set(spec["schema"]["properties"]), {"parts"})                   # sym модели не даётся
        self.assertIn(runner_mod.MD_NOTE.strip(), req.system)

    def test_no_tool_without_tools_optional(self):
        self.drop_optional("entry_scout")
        r = self.runner({"ds-flash": [Tool(MD, {}), SKIP]})
        self.scan_job()
        r.step()
        self.assertNotIn(MD, self.names("ds-flash"))
        self.assertNotIn(runner_mod.MD_NOTE.strip(), self.factory.clients["ds-flash"].calls[0]["req"].system)
        self.assertEqual(self.gw.tool_calls(MD), [])                                      # вызов неизвестного инструмента в шлюз не идёт


class PositionManager(Base):
    def test_detail_then_hold(self):
        r = self.runner({"ds-pro": [Tool(MD, {}), Tool("pm_hold", {"reason": "NO_ACTION", "why": "держу | стена далеко | риск 0"})]})
        self.job("J-1")
        r.step()
        self.assertIn(MD, self.names("ds-pro"))
        c = self.gw.tool_calls(MD)
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["args"], {"sym": "SOL"})
        self.assertTrue(self.tool_result_seen("ds-pro"))
        self.assertEqual(self.get_job("J-1")["result"]["outcome"], "hold")
        why = [x for x in self.decisions() if x["what"] == "brain_why"]
        self.assertEqual(len(why), 1)
        self.assertEqual(why[0]["args"]["tool"], "pm_hold")

    def test_rate_refusal_reaches_model_and_action_still_runs(self):
        self.gw.responses[MD] = {"ok": False, "status": "denied", "errors": [{"code": "E_RATE_MARKET", "path": "", "msg": "limit"}]}
        r = self.runner({"ds-pro": [Tool(MD, {}), Tool("pm_close", {"reason": "INVALIDATION"})]})
        self.job("J-1")
        r.step()
        self.assertIn("E_RATE_MARKET", repr(self.factory.clients["ds-pro"].calls[1]["req"].messages))
        self.assertEqual(len(self.gw.tool_calls("close_plan")), 1)

    def test_sym_from_plan_id_when_job_has_none(self):
        self.assertEqual(runner_mod.Runner._md_sym({}, {}, PLAN), "SOL")
        self.assertEqual(runner_mod.Runner._md_sym({}, {"sym": "ETH"}, PLAN), "ETH")
        self.assertIsNone(runner_mod.Runner._md_sym({}, {"sym": "x; rm"}, "bad"))

    def test_no_tool_without_tools_optional(self):
        self.drop_optional("position_manager")
        r = self.runner({"ds-pro": [Tool("pm_hold", {"reason": "NO_ACTION"})]})
        self.job("J-1")
        r.step()
        self.assertNotIn(MD, self.names("ds-pro"))


class Rights(BrainBase):
    def test_shipped_team_agents_have_read_right(self):
        """Агенты команд из поставки пускаются к инструменту правом read."""
        from tb.gateway import catalog
        self.assertEqual(catalog.TOOLS[MD].right, "read")
        with open(os.path.join(E._bootstrap.ROOT, "config", "agents.json"), encoding="utf-8") as f:
            ag = json.load(f)["agents"]
        with open(os.path.join(E._bootstrap.ROOT, "config", "brain.json"), encoding="utf-8") as f:
            teams = json.load(f).get("teams") or {}
        for t in teams.values():
            a = ag.get(t.get("agent_id"))
            if a is not None:
                self.assertIn("@read", a["tools"], t.get("agent_id"))


if __name__ == "__main__":
    import unittest
    unittest.main()
