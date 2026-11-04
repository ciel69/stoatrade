"""R1.7, задача 7: монеты в фокусе tb.cmkt.focus (Requirements 1.1, 1.2, 1.4, 1.5, 1.6, 1.7, 12.3, 17.6).

Файлы — во временном корне; боевые runtime/ и state/ не трогаются.
"""
import ast
import datetime
import json
import os
import random
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb.cmkt import conf as C
from tb.cmkt import focus as FO
from tb.logic import cmkt as L

NOW = 1793750400.0


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Src(object):
    """Подменённые источники фокуса."""

    def __init__(self):
        self.pos, self.plans, self.req, self.sig, self.top = [], [], {}, [], ["BTC", "ETH", "SOL"] + ["T%02d" % i for i in range(40)]
        self.heat_ok = True

    def exec(self, now):
        return list(self.pos), list(self.plans)

    def struct(self, now, ttl_s):
        live = sorted(((t, s) for s, t in self.req.items() if 0 <= now - t < ttl_s), reverse=True)
        return [s for _, s in live]

    def signals(self, now, keep_s):
        last = {}
        for t, s in self.sig:
            if t <= now and now - t <= keep_s:
                last[s] = max(t, last.get(s, -1))
        return [s for _, s in sorted(((t, s) for s, t in last.items()), reverse=True)]

    def heat(self, now, top_n):
        return list(self.top[:top_n]) if self.heat_ok else None


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbcmkt_focus_")
        self.cfg = C.defaults()
        self.src = Src()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def focus(self, **f):
        self.cfg["focus"].update(f)
        return FO.Focus(self.root, self.cfg, sources=self.src, clock=lambda: NOW)


class TestSelect(Base):
    def test_default_tiers(self):
        doc = self.focus().compute(NOW)
        self.assertEqual(doc["deep"][:3], ["BTC", "ETH", "SOL"])
        self.assertEqual(len(doc["deep"]), 10)                           # ядро + топ-10 по обороту (BTC, ETH, SOL в нём же)
        self.assertEqual(doc["deep"][3:], ["T%02d" % i for i in range(7)])
        self.assertEqual(len(doc["light"]), 30 - 10)                     # остальной топ-30
        self.assertEqual(doc["why"]["T00"], "top")
        self.assertEqual(doc["why"][doc["light"][0]], "top")
        self.assertEqual(doc["why"]["BTC"], "core")
        self.assertEqual(doc["warn"], [])

    def test_positions_and_plans_never_evicted(self):
        self.src.pos = ["P%02d" % i for i in range(18)]
        self.src.plans = ["Q%02d" % i for i in range(7)]
        doc = self.focus().compute(NOW)
        self.assertEqual(doc["deep"], self.src.pos + self.src.plans)     # deep расширен до 25 > max_deep
        self.assertEqual(doc["light"][:3], ["BTC", "ETH", "SOL"])        # ядро вытеснено в light первым из остальных
        self.assertEqual(doc["why"]["P00"], "pos")
        self.assertEqual(doc["why"]["Q00"], "plan")

    def test_eviction_order(self):
        self.src.top = ["BTC", "ETH", "SOL"] + ["T%02d" % i for i in range(30)]
        self.src.req = {"R1": NOW - 100, "R2": NOW - 10}
        self.src.sig = [(NOW - 50, "S1"), (NOW - 5, "S2"), (NOW - 500, "S3")]
        doc = self.focus(max_light=6, deep_top_n=5, max_deep=5).compute(NOW)
        self.assertEqual(doc["deep"], ["BTC", "ETH", "SOL", "T00", "T01"])
        self.assertEqual(doc["light"], ["R2", "R1", "S2", "S1", "S3", "T02"])    # запросы (новее) > сигналы (новее) > топ
        self.assertEqual(doc["compat"], ["R2", "R1", "BTC", "ETH", "SOL", "T00", "T01", "T02"])
        doc = self.focus(max_light=6, deep_top_n=5, max_deep=4).compute(NOW)      # deep мал: T01 вытеснен в light как топ
        self.assertEqual(doc["deep"], ["BTC", "ETH", "SOL", "T00"])
        self.assertEqual(doc["light"], ["R2", "R1", "S2", "S1", "S3", "T01"])

    def test_compat_symbols_always_in_focus(self):
        """Requirement 17.6: монеты compat_symbols (до 8) входят в фокус даже при пределе light."""
        self.src.req = dict(("R%d" % i, NOW - i) for i in range(6))
        self.src.pos = ["PX"]
        doc = self.focus(max_light=2).compute(NOW)
        self.assertEqual(len(doc["compat"]), 8)
        self.assertEqual(doc["compat"][:6], ["PX", "R0", "R1", "R2", "R3", "R4"])
        members = set(doc["deep"]) | set(doc["light"])
        for s in doc["compat"]:
            self.assertIn(s, members)
        self.assertEqual(doc["compat"], L.compat_symbols(["PX"], ["R%d" % i for i in range(6)], doc["top"], 8))

    def test_limits_property(self):
        rng = random.Random(5)
        for _ in range(200):
            self.src.pos = ["P%d" % i for i in range(rng.randint(0, 6))]
            self.src.plans = ["Q%d" % i for i in range(rng.randint(0, 6))]
            self.src.req = dict(("R%d" % i, NOW - rng.randint(0, 3000)) for i in range(rng.randint(0, 40)))
            self.src.sig = [(NOW - rng.randint(0, 12000), "S%d" % rng.randint(0, 300)) for _ in range(rng.randint(0, 400))]
            f = self.focus(max_deep=rng.randint(5, 40), max_light=rng.randint(0, 200), deep_top_n=rng.randint(0, 5))
            doc = f.compute(NOW)
            fixed = self.src.pos + self.src.plans
            self.assertEqual(doc["deep"][:len(fixed)], fixed)
            self.assertLessEqual(len(doc["deep"]), max(f.f["max_deep"], len(fixed)))
            self.assertLessEqual(len(doc["light"]), f.f["max_light"] + FO.COMPAT_N)
            self.assertEqual(len(set(doc["deep"]) | set(doc["light"])), len(doc["deep"]) + len(doc["light"]))
            self.assertTrue(all(s in doc["compat"] for s in doc["light"][f.f["max_light"]:]))     # сверх предела — только совместимые


class TestDynamics(Base):
    def test_hold_and_since(self):
        """Requirements 1.4, 1.5: since — вход в фокус; выпавшая монета держится hold_s до отписки, потом уходит; новый вход — новый since."""
        self.src.sig = [(NOW, "DOGE")]
        f = self.focus(keep_signal_min=10, hold_s=600)
        d = f.compute(NOW + 1)
        self.assertIn("DOGE", d["light"])
        self.assertEqual(d["since"]["DOGE"], NOW + 1)
        d = f.compute(NOW + 600)                                        # сигнал ещё в окне 10 мин
        self.assertEqual(d["why"]["DOGE"], "signal")
        self.assertEqual(f.focus_s("DOGE", NOW + 600), 599)
        d = f.compute(NOW + 601)                                        # окно сигнала кончилось: удержание
        self.assertEqual(d["why"]["DOGE"], "hold")
        self.assertEqual(d["since"]["DOGE"], NOW + 1)
        d = f.compute(NOW + 601 + 599)
        self.assertIn("DOGE", d["light"])
        d = f.compute(NOW + 601 + 600)
        self.assertNotIn("DOGE", d["light"] + d["deep"])
        self.assertIsNone(f.focus_s("DOGE", NOW + 1201))
        self.src.sig.append((NOW + 1300, "DOGE"))
        d = f.compute(NOW + 1300)
        self.assertEqual(d["since"]["DOGE"], NOW + 1300)
        # возврат источника во время удержания не сбрасывает since
        self.src.sig = [(NOW + 1300, "DOGE")]
        f.compute(NOW + 1300 + 601)
        self.src.sig.append((NOW + 2000, "DOGE"))
        d = f.compute(NOW + 2000)
        self.assertEqual((d["why"]["DOGE"], d["since"]["DOGE"]), ("signal", NOW + 1300))

    def test_tier_change_keeps_since(self):
        self.src.pos = ["XRP"]
        f = self.focus()
        d = f.compute(NOW)
        self.assertIn("XRP", d["deep"])
        self.src.pos = []
        self.src.req = {"XRP": NOW + 5}
        d = f.compute(NOW + 10)
        self.assertIn("XRP", d["light"])
        self.assertEqual(d["since"]["XRP"], NOW)

    def test_heat_stale_and_no_heat(self):
        f = self.focus()
        d = f.compute(NOW)
        top = list(d["top"])
        self.assertEqual(len(top), 30)
        self.src.heat_ok = False                                        # heat.json старше 300 с или нет
        d = f.compute(NOW + 400)
        self.assertEqual(d["top"], top)                                 # последний известный список
        self.assertNotIn("FOCUS_NO_HEAT", d["warn"])
        f2 = FO.Focus(tempfile.mkdtemp(prefix="tbcmkt_focus2_"), self.cfg, sources=self.src, clock=lambda: NOW)
        self.addCleanup(shutil.rmtree, f2.root, True)
        d = f2.compute(NOW)
        self.assertIn("FOCUS_NO_HEAT", d["warn"])
        self.assertEqual(d["deep"], ["BTC", "ETH", "SOL"])              # только ядро и динамика
        self.assertEqual(d["light"], [])

    def test_top_refresh_every_300s(self):
        f = self.focus()
        f.compute(NOW)
        self.src.top = ["NEW"] + self.src.top
        self.assertNotIn("NEW", f.compute(NOW + 299)["top"])
        self.assertIn("NEW", f.compute(NOW + 300)["top"])

    def test_budget_shrinks_light_to_40(self):
        """Requirement 12.3: бюджет превышен 10 мин подряд -> light −20 за шаг до 40, жёлтый BUDGET."""
        self.src.sig = [(NOW, "S%03d" % i) for i in range(150)]
        f = self.focus()
        self.assertEqual(len(f.compute(NOW)["light"]), 120)
        t = NOW
        caps = []
        for _ in range(80):
            t += 60
            caps.append(f.budget(True, t))
        self.assertEqual(sorted(set(caps), reverse=True), [120, 100, 80, 60, 40])
        self.assertEqual((caps[9], caps[10]), (120, 100))               # первый шаг — через 10 мин от начала превышения
        d = f.compute(t)
        self.assertEqual(len(d["light"]), 40)
        self.assertIn("BUDGET", d["warn"])
        f.budget(False, t + 60)
        self.assertEqual(f.budget(True, t + 120), 40)                    # прерывание — счёт заново

    def test_signal_coverage_on_synthetic_stream(self):
        """Покрытие на фикстуре сигналов: сигнал монеты, у которой был сигнал за keep_signal_min до t0 (не позже чем за 5 мин), застаёт
        её в фокусе прогретой (since ≤ t0 − warm_s), пока набор в пределах ярусов."""
        rng = random.Random(42)
        coins = ["C%03d" % i for i in range(110)]
        weights = [1.0 / (i + 1) ** 0.8 for i in range(len(coins))]
        t, sig = NOW, []
        while t < NOW + 8 * 3600:
            t += rng.expovariate(1 / 25.0)
            sig.append((int(t), rng.choices(coins, weights)[0]))
        self.src.sig = []
        f = self.focus()
        warm = self.cfg["focus"]["warm_s"]
        keep = self.cfg["focus"]["keep_signal_min"] * 60
        hit = n = 0
        nxt = 0
        step = NOW
        prev_by = {}
        while nxt < len(sig):
            step += 10
            while nxt < len(sig) and sig[nxt][0] <= step:
                t0, s = sig[nxt]
                p = prev_by.get(s)
                if p is not None and t0 - keep <= p <= t0 - warm:
                    n += 1
                    fs = f.focus_s(s, t0)
                    hit += 1 if fs is not None and fs >= warm else 0
                prev_by[s] = t0
                self.src.sig.append(sig[nxt])
                nxt += 1
            d = f.compute(step)
            self.assertLessEqual(len(d["light"]), 120)
        self.assertGreater(n, 300)
        self.assertEqual(hit, n)


class TestFiles(Base):
    def rt(self, *p):
        return os.path.join(self.root, "runtime", *p)

    def put(self, name, obj):
        os.makedirs(os.path.dirname(self.rt(name)), exist_ok=True)
        with open(self.rt(name), "w", encoding="utf-8") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))

    def test_file_sources(self):
        self.put("exec_snapshot.json", {"positions": [{"sym": "BTC"}, {"sym": "ARB"}], "plans": [{"sym": "ARB"}, {"sym": "OP"}]})
        self.put("exec_snapshot@okx.json", {"positions": [{"sym": "SUI"}], "plans": [{"sym": "bad sym"}]})
        self.put("structure_req.json", {"req": {"WIF": NOW - 10, "OLD": NOW - 4000, "TIA": NOW - 100}})
        self.put("heat.json", {"ts_epoch": NOW - 30, "rows": [{"sym": "AAA", "turn_24h_musd": 900, "xturn_24h_musd": 10},
                                                              {"sym": "BBB", "turn_24h_musd": 50, "xturn_24h_musd": None},
                                                              {"sym": "CCC", "turn_24h_musd": 5, "xturn_24h_musd": 500}]})
        self.assertEqual(FO.read_exec(self.root), (["BTC", "ARB", "SUI"], ["OP"]))
        self.assertEqual(FO.read_struct(self.root, NOW, 1800), ["WIF", "TIA"])
        self.assertEqual(FO.read_heat(self.root, NOW, 30), ["CCC", "BBB", "AAA"])                # межбиржевой оборот, иначе Bybit
        self.assertIsNone(FO.read_heat(self.root, NOW + 300, 30))                                  # старше 300 с
        lines = [{"ts": iso(NOW - 100), "type": "range_5m", "sym": "PEPE"}, {"ts": iso(NOW - 50), "type": "scan_start"},
                 {"ts": iso(NOW - 20), "type": "book_wall", "sym": "DOGE"}, {"ts": iso(NOW - 99999), "type": "oi_spike", "sym": "OLDC"}]
        self.put("hot_events.jsonl", "\n".join(json.dumps(x) for x in lines) + "\n" + '{"ts": "%s", "sym": "HALF"' % iso(NOW))
        tail = FO.SignalTail(self.root)
        tail.poll(NOW, 10800)
        self.assertEqual(tail.recent(NOW, 10800), ["DOGE", "PEPE"])                                # неполная строка не читается
        with open(self.rt("hot_events.jsonl"), "a", encoding="utf-8") as f:
            f.write(', "type": "x"}\n' + json.dumps({"ts": iso(NOW + 5), "type": "x", "sym": "WLD"}) + "\n")
        tail.poll(NOW + 10, 10800)
        self.assertEqual(tail.recent(NOW + 10, 10800), ["WLD", "HALF", "DOGE", "PEPE"])
        self.put("hot_events.jsonl", json.dumps({"ts": iso(NOW + 20), "type": "x", "sym": "NEAR"}) + "\n")    # файл усечён
        tail.poll(NOW + 30, 10800)
        self.assertEqual(tail.recent(NOW + 30, 10800)[0], "NEAR")

    def test_focus_json_write_read_and_restart(self):
        self.src.sig = [(NOW, "DOGE")]
        clock = [NOW]
        f = FO.Focus(self.root, self.cfg, sources=self.src, clock=lambda: clock[0])
        d = f.step()
        got = FO.read(self.root)
        self.assertEqual(got["deep"], d["deep"])
        self.assertEqual(got["schema"], FO.SCHEMA)
        self.assertEqual(FO.tier_of(got, "DOGE"), "light")
        self.assertEqual(FO.tier_of(got, "BTC"), "deep")
        self.assertIsNone(FO.tier_of(got, "XXX"))
        self.assertIsNone(FO.read(self.root, NOW + 100, max_age_s=30))
        mtime = os.path.getmtime(FO.focus_path(self.root))
        clock[0] = NOW + 1
        self.assertIs(f.step(), d)                                       # пересчёт не чаще раза в 2 с
        # перезапуск сервиса: since и топ берутся из свежего файла
        clock[0] = NOW + 20
        f2 = FO.Focus(self.root, self.cfg, sources=self.src, clock=lambda: clock[0])
        d2 = f2.compute()
        self.assertEqual(d2["since"]["DOGE"], NOW)
        self.assertGreaterEqual(os.path.getmtime(FO.focus_path(self.root)), mtime)
        clock[0] = NOW + 500                                             # файл старше 60 с: сбор прерывался, since заново
        f3 = FO.Focus(self.root, self.cfg, sources=self.src, clock=lambda: clock[0])
        self.assertEqual(f3.compute()["since"]["BTC"], NOW + 500)

    def test_conf_limits(self):
        """Requirement 1.6: max_deep > 40 и max_light > 200 отклоняются кодом E_CONF:focus.*."""
        for k, v in (("max_deep", 41), ("max_light", 201)):
            raw = C.defaults()
            raw["focus"][k] = v
            self.assertIn("E_CONF:focus." + k, C.check(raw))
        raw = C.defaults()
        raw["focus"].update(max_deep=40, max_light=200)
        self.assertEqual(C.check(raw), [])


class TestNoWritePath(unittest.TestCase):
    def test_brain_gateway_mcp_cannot_change_focus(self):
        """Requirement 1.7: у мозга, шлюза и MCP нет пути записи в набор фокуса: они не импортируют tb.cmkt и не знают focus.json /
        structure_req.json; писатель focus.json — только tb.cmkt.focus.write."""
        src = os.path.join(_bootstrap.SRC, "tb")
        bad = {}
        for layer in ("brain", "gateway", "mcp"):
            for d, _, fs in os.walk(os.path.join(src, layer)):
                for fn in fs:
                    if not fn.endswith(".py"):
                        continue
                    p = os.path.join(d, fn)
                    with open(p, encoding="utf-8") as fh:
                        text = fh.read()
                    tree = ast.parse(text)
                    hits = []
                    for n in ast.walk(tree):
                        if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("tb.cmkt"):
                            hits.append(n.module)
                        elif isinstance(n, ast.ImportFrom) and n.module == "tb" and any(a.name == "cmkt" for a in n.names):
                            hits.append("tb.cmkt")
                        elif isinstance(n, ast.Import) and any(a.name.startswith("tb.cmkt") for a in n.names):
                            hits.append("tb.cmkt")
                        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and "focus.json" in n.value:
                            hits.append("focus.json")
                    if hits:
                        bad[os.path.relpath(p, src)] = hits
        self.assertEqual(bad, {})
        with open(os.path.join(src, "cmkt", "focus.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        writers = sorted(fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
                         for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "atomic_write")
        self.assertEqual(writers, ["write"])


if __name__ == "__main__":
    unittest.main()
