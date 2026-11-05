"""R1.7, задача 21: инструмент шлюза get_market_detail (Requirement 21): схема, право, свой предел вызовов в час (независим от get_similar_cases),
предел байт вместе с полями шлюза, усечение частями и `trunc`, NOT_FOCUS/STALE без чисел, только числа и перечисления, описания ролей.
Всё во временном корне; снимок runtime/cmkt/snap.json пишется помощником теста читателя."""
import copy
import json
import os
import re
import time
import unittest

import _bootstrap  # noqa: F401
import _gw
from test_cmktdoc import leaves_ok, snap_row, write_snap
from tb import paths
from tb.brain import roles_runtime
from tb.core import cmktdoc
from tb.gateway import catalog
from tb.gateway.rpc import Gateway
from tb.memory import cfg as memcfg

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
TOOL = "get_market_detail"
SERVICE = ("ok", "status", "errors", "warnings", "dropped_fields")


def agents():
    lim = {"symbols": ["*"], "modes": ["paper"], "calls_per_min": 500, "plans_per_hour": 0}
    return {"agents": {
        "rd": {"role": "brain-api", "auth": "local", "tools": ["@read", "@memory.read"], "limits": lim},
        "sol": {"role": "brain-api", "auth": "local", "tools": ["@read"], "limits": dict(lim, symbols=["SOL"])},
        "no": {"role": "x", "auth": "local", "tools": ["@memory.read"], "limits": lim},
    }}


def nbytes(obj):
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class MarketGw(_gw.GwBase):
    def setUp(self):
        _gw.GwBase.setUp(self)
        cmktdoc._cfg_cache.clear()
        self.write_agents(agents())
        self.gw = Gateway(self.root)
        self.now = int(time.time())

    def snap(self, syms=None, ts=None):
        return write_snap(self.root, self.now if ts is None else ts, syms if syms is not None else {"BTC": snap_row(flags=["low_cov"]), "SOL": snap_row()})

    def set_tool(self, **kw):
        tool = {"max_bytes": 2048, "per_hour": 30}
        tool.update(kw)
        with open(paths.config("cmkt.json", root=self.root), "w", encoding="utf-8") as f:
            json.dump({"tool": tool}, f)

    def md(self, args, agent="rd"):
        return self.gw.call(agent, TOOL, args)

    def payload(self, r):
        return dict((k, v) for k, v in r.items() if k not in SERVICE)


class TestSchema(unittest.TestCase):
    def test_registered_read_only(self):
        t = catalog.TOOLS[TOOL]
        self.assertEqual(t.right, "read")
        self.assertFalse(t.write)
        self.assertEqual(set(t.schema["properties"]), {"sym", "parts"})
        self.assertEqual(t.schema["required"], ["sym"])
        self.assertEqual(t.schema["properties"]["sym"]["pattern"], catalog.common.RE_SYMBOL.pattern)
        parts = t.schema["properties"]["parts"]
        self.assertEqual(parts["type"], "array")
        self.assertEqual(parts["items"]["enum"], ["book", "cvd", "oi", "liq", "funding", "venues"])
        self.assertEqual(set(t.out) - set(catalog.Tool("x", "", {}, "read", None).out),
                         {"sym", "ts", "age_s", "n_ex", "cov", "warn", "book", "cvd", "oi", "liq", "funding", "venues", "trunc"})
        sch = t.input_schema()                       # для MCP: внутренние ключи убраны, лишние поля запрещены
        self.assertFalse(sch["additionalProperties"])
        self.assertNotIn("free", json.dumps(sch))

    def test_gateway_code_has_no_exchange_names(self):
        """Имена рынков отдаёт только cmktdoc: в обработчике и регистрации инструмента их нет (слово gate — только как биржа, не gateway)."""
        rx = re.compile(r"\b(binance|bybit|okx|bitget|gate|coinbase)\b")
        with open(os.path.join(ROOT, "src", "tb", "gateway", "catalog.py"), encoding="utf-8") as f:
            src = f.read().lower()
        start = src.index("def h_market_detail")
        handler = src[start:src.index("def h_lessons", start)]
        reg = src[src.index('_reg(tool("get_market_detail"'):src.index('_reg(tool("list_lessons"')]
        self.assertEqual(rx.findall(handler + reg), [])


class TestCalls(MarketGw):
    def test_right_and_args(self):
        self.snap()
        self.assertEqual(self.code(self.md({"sym": "SOL"}, agent="no")), "E_TOOL_DENIED")
        self.assertEqual(self.code(self.md({})), "E_ARGS")
        self.assertEqual(self.code(self.md({"sym": "sol; rm"})), "E_ARGS")
        self.assertEqual(self.code(self.md({"sym": "SOL", "parts": ["book", "news"]})), "E_ARGS")
        self.assertEqual(self.code(self.md({"sym": "SOL", "parts": ["book"] * 7})), "E_ARGS")
        self.assertEqual(self.code(self.md({"sym": "BTC"}, agent="sol")), "E_SYMBOL_FORBIDDEN")
        self.assertTrue(self.md({"sym": "SOL"}, agent="sol")["ok"])

    def test_full_answer_numbers_and_enums_only(self):
        self.snap()
        self.set_tool(max_bytes=8192)
        r = self.md({"sym": "BTC"})
        self.assertIs(r["ok"], True)
        for p in cmktdoc.PARTS:
            self.assertIn(p, r, p)
        self.assertNotIn("trunc", r)
        self.assertEqual((r["sym"], r["ts"], r["n_ex"], r["warn"]), ("BTC", self.now, 10, ["LOW_COV"]))
        leaves_ok(self, self.payload(r))
        self.assertLessEqual(nbytes(r), 8192)
        # ответ шлюза совпадает с читателем (шлюз ничего не досчитывает)
        self.assertEqual(self.payload(r), cmktdoc.detail(self.root, "BTC", now=self.now))

    def test_parts_filter(self):
        self.snap()
        self.set_tool(max_bytes=8192)
        r = self.md({"sym": "SOL", "parts": ["oi", "cvd"]})
        self.assertEqual(sorted(p for p in cmktdoc.PARTS if p in r), ["cvd", "oi"])
        r = self.md({"sym": "SOL", "parts": []})                  # пустой список = все части
        self.assertEqual([p for p in cmktdoc.PARTS if p in r], list(cmktdoc.PARTS))

    def test_bytes_limit_and_trunc_order(self):
        self.snap()
        self.set_tool(max_bytes=8192)
        full = nbytes(self.md({"sym": "BTC"}))
        for mb in (512, 700, 1024, 2048):
            self.set_tool(max_bytes=mb)
            r = self.md({"sym": "BTC"})
            self.assertIs(r["ok"], True)
            self.assertLessEqual(nbytes(r), mb)                    # вместе с полем ok шлюза
            leaves_ok(self, self.payload(r))
            if full > mb:
                tr = r["trunc"]
                self.assertEqual(tr, list(cmktdoc.TRUNC_ORDER[:len(tr)]))
                for p in tr:
                    self.assertNotIn(p, r)
                for p in cmktdoc.PARTS:
                    if p not in tr:
                        self.assertIn(p, r, (mb, p))
        self.set_tool(max_bytes=512)
        self.assertIn("venues", self.md({"sym": "BTC"})["trunc"])

    def test_gateway_trims_its_own_overhead(self):
        """Читатель уложился ровно в предел без поля ok: шлюз досекает следующую часть по тому же порядку."""
        self.snap()
        self.set_tool(max_bytes=8192)
        base = cmktdoc.detail(self.root, "BTC", parts=["book", "cvd"], now=self.now)
        mb = nbytes(base)
        self.assertGreaterEqual(mb, 512)                             # предел ниже 512 не допускается конфигом
        self.assertNotIn("trunc", base)
        orig = cmktdoc.detail
        try:
            cmktdoc.detail = lambda root, sym, parts=None, now=None, cfg=None: copy.deepcopy(base)
            self.set_tool(max_bytes=mb)
            r = self.md({"sym": "BTC", "parts": ["book", "cvd"]})
        finally:
            cmktdoc.detail = orig
        self.assertLessEqual(nbytes(r), mb)
        self.assertEqual(r["trunc"], ["cvd"])
        self.assertIn("book", r)

    def test_not_focus_and_stale_without_numbers(self):
        r = self.md({"sym": "SOL"})                                # снимка нет
        self.assertEqual((r["ok"], r["warn"]), (True, ["STALE"]))
        self.assertFalse([p for p in cmktdoc.PARTS if p in r])
        self.snap()
        r = self.md({"sym": "DOGE"})
        self.assertEqual((r["ok"], r["warn"]), (True, ["NOT_FOCUS"]))
        self.assertFalse([p for p in cmktdoc.PARTS if p in r])
        self.assertFalse([k for k in ("n_ex", "cov") if k in r])
        self.snap(ts=self.now - 60)
        r = self.md({"sym": "SOL"})
        self.assertEqual(r["warn"], ["STALE"])
        self.assertFalse([p for p in cmktdoc.PARTS if p in r])

    def test_reads_only(self):
        self.snap()
        before = sorted(os.path.relpath(os.path.join(d, n), self.root) for d, _, fs in os.walk(self.root) for n in fs)
        self.md({"sym": "SOL"})
        after = sorted(os.path.relpath(os.path.join(d, n), self.root) for d, _, fs in os.walk(self.root) for n in fs)
        new = [p for p in after if p not in before]
        self.assertTrue(all("audit" in p for p in new), new)       # пишется только аудит шлюза


class TestRate(MarketGw):
    def mem_cfg(self, per_hour):
        c = copy.deepcopy(memcfg.load())
        c["tool"] = dict(c.get("tool") or {}, calls_per_hour=per_hour)
        with open(paths.config("memory.json", root=self.root), "w", encoding="utf-8") as f:
            json.dump(c, f)

    def test_own_counter_per_hour(self):
        self.snap()
        self.set_tool(per_hour=3)
        for _ in range(3):
            self.assertTrue(self.md({"sym": "SOL"})["ok"])
        self.assertEqual(self.code(self.md({"sym": "SOL"})), "E_RATE_MARKET")
        self.assertEqual(self.code(self.md({"sym": "BTC"})), "E_RATE_MARKET")

    def test_independent_of_similar_cases(self):
        self.snap()
        self.set_tool(per_hour=2)
        self.mem_cfg(2)
        for _ in range(2):
            self.assertTrue(self.gw.call("rd", "get_similar_cases", {"sym": "SOL", "side": "long"})["ok"])
        self.assertEqual(self.code(self.gw.call("rd", "get_similar_cases", {"sym": "SOL", "side": "long"})), "E_RATE_MEMORY")
        for _ in range(2):                                         # память исчерпана, подробности рынка доступны
            self.assertTrue(self.md({"sym": "SOL"})["ok"])
        self.assertEqual(self.code(self.md({"sym": "SOL"})), "E_RATE_MARKET")
        self.mem_cfg(10)                                           # и наоборот: исчерпанный рынок не трогает счётчик памяти
        self.assertTrue(self.gw.call("rd", "get_similar_cases", {"sym": "SOL", "side": "long"})["ok"])


class TestRoles(unittest.TestCase):
    def test_roles_get_tool_and_one_line(self):
        reg = roles_runtime.load(ROOT)
        for rid in ("entry_scout", "position_manager"):
            with open(os.path.join(ROOT, "config", "roles", rid + ".json"), encoding="utf-8") as f:
                raw = json.load(f)
            self.assertIn(TOOL, raw["tools_optional"], rid)
            self.assertNotIn(TOOL, raw["tools"], rid)
            role = reg.get(rid)
            self.assertIsNotNone(role, reg.errors)
            self.assertIn(TOOL, role.tools)
            lines = [ln for ln in role.prompt.splitlines() if TOOL in ln]
            self.assertEqual(len(lines), 1, rid)
            ln = lines[0].lower()
            for needle in ("brief.cmkt", "do not recompute", "not_focus"):
                self.assertIn(needle, ln, (rid, needle))


if __name__ == "__main__":
    unittest.main()
