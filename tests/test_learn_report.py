"""Сборка отчёта tb.learning/1 (R0.2, задача 12): контракт полей против эталонов tests/fixtures_learning, свойства 1 (режимы), 9 (пустые источники),
10 (изоляция блока), 11 (детерминизм), 13 (нет секретов и сумм), 14 (только чтение), кеш и computing, meta.warnings, размер, dashapi.
Эталоны пересоздаются `py -3 tests/_learn_report_helpers.py` (при намеренном изменении контракта). Временный корень, сети нет."""
import copy
import json
import os
import random
import shutil
import threading
import time
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import _learn_report_helpers as H
from tb.core import expstats
from tb.dash import model
from tb.learn import cache, conf, dashapi, economics, memory_vs_base, report
from tb.llm import usage as llm_usage
from tb.memory import backtest

FORBIDDEN = ("pnl_usdt", "risk_usdt", "equity", "balance", "margin", "notional", "fees_usdt", "funding_usdt", "wallet", "capital", "pnl")
TOP = ("schema", "ok", "mode", "asof", "min_n", "alpha", "verdict", "order", "blocks", "meta")
META = ("built_ms", "cached", "excluded", "warnings", "sources")
BLOCK_BASE = ("id", "title", "status", "note", "reason")
VERDICT = ("state", "reason", "text", "n", "n_recent", "n_prev", "diff_r", "p", "alpha_adj", "trend", "guards", "params")
EST = ("v", "lo", "hi", "n", "lvl")
EXPECT = {"empty": ("few_data", "n_lt_min"), "few": ("few_data", "n_lt_min"), "nodiff": ("no_difference", "not_significant"),
          "confirmed": ("confirmed", "improved"), "down": ("no_difference", "not_significant"), "mode_unknown": ("no_difference", "no_baseline"),
          "no_labels": ("no_difference", "no_baseline"), "computing": ("no_difference", "no_baseline"), "full": ("confirmed", "improved"),
          "r2": ("no_difference", "not_significant")}


def clean(p):
    p = copy.deepcopy(p)
    p["meta"]["built_ms"] = 0
    return p


def dump(p):
    return json.dumps(clean(p), sort_keys=True, ensure_ascii=False)


def norm_skips(p):
    """Код причины пустого блока «Пропуски» (mode_unknown / no_whatif) знает о существовании случаев с неизвестным режимом, числа - нет."""
    p = copy.deepcopy(p)
    p["blocks"]["skips"]["reason"] = None
    return dump(p)


def walk(obj):
    """Все (ключ, значение) ответа рекурсивно."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            for x in walk(v):
                yield x
    elif isinstance(obj, list):
        for v in obj:
            for x in walk(v):
                yield x


def snapshot(root):
    out = {}
    for sub in ("state", "config", "runtime"):
        for d, _, fs in os.walk(os.path.join(root, sub)):
            for f in fs:
                p = os.path.join(d, f)
                st = os.stat(p)
                with open(p, "rb") as fh:
                    out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns, hash(fh.read()))
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root()
        cache.REPORT.clear()
        memory_vs_base.clear()

    def tearDown(self):
        memory_vs_base.clear()
        cache.REPORT.clear()
        shutil.rmtree(self.root, ignore_errors=True)

    def paper(self, n=40, seed=1, mode="paper", **kw):
        rng = random.Random(seed)
        rs = H.gauss_rs(rng, [(n, 0.1, 1.0)])
        ts = H.times(n)
        H._jsonl(self.root, "trades.jsonl", H.trade_recs(rs, ts, mode, **kw))
        H.put_cases(self.root, H.case_recs(rng, rs, ts, mode))
        return rs, ts


# ---------------------------------------------------------------- контракт против эталонов

class TestFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.built, cls.fix = {}, {}
        for n in H.SCENARIOS:
            cls.built[n] = H.build_scenario(n)
            with open(H.fixture_file(n), encoding="utf-8") as f:
                cls.fix[n] = json.load(f)

    def test_golden(self):
        """Ответ на синтетических данных совпадает с эталоном (после json-обмена); иначе перегенерировать эталоны сознательно."""
        for n in H.SCENARIOS:
            self.assertEqual(json.loads(H.dumps(self.built[n])), self.fix[n], n)

    def test_contract_fields(self):
        for n, p in self.fix.items():
            self.assertEqual(set(p), set(TOP), n)
            self.assertEqual((p["schema"], p["ok"], p["mode"], p["min_n"], p["alpha"]), ("tb.learning/1", True, "paper", expstats.MIN_N, expstats.ALPHA), n)
            self.assertEqual(p["asof"] % 30, 0)
            self.assertEqual(p["order"], list(report.ORDER))
            self.assertEqual(set(p["blocks"]), set(report.ORDER))
            self.assertEqual(set(p["meta"]), set(META))
            self.assertEqual(set(p["meta"]["excluded"]), {"shadow", "manual", "no_plan", "no_r"})
            self.assertEqual(set(p["meta"]["sources"]), {"trades", "cases", "labels_db"})
            self.assertEqual(set(p["verdict"]), set(VERDICT), n)
            self.assertEqual((p["verdict"]["state"], p["verdict"]["reason"]), EXPECT[n], n)
            self.assertLessEqual(len(p["verdict"]["text"]), 160)
            for bid, b in p["blocks"].items():
                self.assertEqual(b["id"], bid)
                self.assertEqual(b["title"], report.TITLES[bid])
                self.assertTrue(set(BLOCK_BASE) <= set(b), (n, bid))
                self.assertIn(b["status"], report.STATUSES)
                self.assertLessEqual(len(b["note"]), 200)
                if b["status"] == "stage":
                    self.assertEqual((b["stage"], b["roadmap"]), (2, "R2.6"))
                if b["status"] in ("no_data", "error"):
                    self.assertTrue(b["reason"], (n, bid))
                self.assertNotIn("warnings", b)

    def test_field_sets_stable_across_scenarios(self):
        """Набор полей блока не зависит от данных (поле без значения = null), кроме status=no_data у блоков без рядов."""
        for bid in ("result", "exits", "discipline", "economics", "skips", "vs_policy", "memory"):
            sets = {n: frozenset(p["blocks"][bid]) for n, p in self.fix.items() if p["blocks"][bid]["status"] != "stage"}
            self.assertEqual(len(set(sets.values())), 1, (bid, {n: sorted(s) for n, s in sets.items()}))

    def test_est_shape(self):
        for n, p in self.fix.items():
            for k, v in walk(p):
                if isinstance(v, dict) and "lvl" in v and k not in ("params",) + tuple(report.ORDER):     # у блоков versions, hyps lvl — уровень интервалов блока
                    self.assertEqual(set(v), set(EST), (n, k))
                    if v["lo"] is not None:
                        self.assertLessEqual(v["lo"], v["hi"])

    def test_scenarios_meaning(self):
        f = self.fix
        self.assertEqual(f["empty"]["verdict"]["n"], 0)
        self.assertTrue(all(b["status"] in ("no_data", "stage") for b in f["empty"]["blocks"].values()))
        self.assertEqual(f["few"]["verdict"]["n"], 12)
        self.assertEqual(f["few"]["blocks"]["result"]["status"], "few_data")
        self.assertIsNone(f["few"]["verdict"]["n_recent"])
        self.assertEqual(f["nodiff"]["verdict"]["trend"], "flat")
        self.assertGreater(f["nodiff"]["meta"]["excluded"]["manual"], 0)
        self.assertEqual(f["confirmed"]["verdict"]["trend"], "up")
        self.assertGreater(f["confirmed"]["verdict"]["diff_r"]["lo"], 0)
        self.assertEqual(f["down"]["verdict"]["trend"], "down")
        self.assertIn("стало хуже", f["down"]["verdict"]["text"])
        sk = f["mode_unknown"]["blocks"]["skips"]
        self.assertEqual((sk["status"], sk["reason"]), ("no_data", "mode_unknown"))
        self.assertEqual(sk["weeks"], [])
        vp = f["no_labels"]["blocks"]["vs_policy"]
        self.assertEqual((vp["status"], vp["reason"], f["no_labels"]["meta"]["sources"]["labels_db"]), ("no_data", "labels_off", False))
        mem = f["computing"]["blocks"]["memory"]
        self.assertEqual(mem["status"], "computing")
        self.assertEqual(mem["history"]["status"], "stage")
        full = f["full"]
        self.assertTrue(full["meta"]["sources"]["labels_db"])
        for bid in ("result", "vs_policy", "discipline", "skips", "exits", "economics", "memory"):
            self.assertEqual(full["blocks"][bid]["status"], "ok", bid)
        self.assertEqual(full["blocks"]["economics"]["scope"], "platform")
        self.assertTrue(full["blocks"]["result"]["series"]["50"])
        self.assertTrue(full["blocks"]["exits"]["series"]["50"])
        for n in ("empty", "full"):                                         # без таблиц цикла — заглушка этапа; гипотез без сделок с тегом нет
            self.assertEqual((f[n]["blocks"]["lessons"]["status"], f[n]["blocks"]["versions"]["status"]), ("stage", "stage"), n)
            self.assertEqual((f[n]["blocks"]["hyps"]["status"], f[n]["blocks"]["hyps"]["reason"]), ("no_data", "no_trades"), n)
        r2 = f["r2"]["blocks"]                                              # R2.6 (задача 20): блоки с данными для фронтенда
        for bid in ("lessons", "versions", "hyps"):
            self.assertEqual(r2[bid]["status"], "ok", bid)
        self.assertEqual(r2["lessons"]["best"]["lesson_id"], "L-0002")
        self.assertEqual([x["verdict"] for x in r2["versions"]["rulebook"]], ["few_data", "not_worse"])
        self.assertEqual(r2["versions"]["n_pre_r2"], 20)
        fam = dict((x["family"], x) for x in r2["hyps"]["families"])
        self.assertIsNotNone(fam["liq-cascade-reclaim"]["ref"])
        self.assertEqual(fam["oi-build-squeeze"]["n"], 0)

    def test_size_and_secrets(self):
        for n, p in self.fix.items():
            self.assertLessEqual(len(json.dumps(p, ensure_ascii=False).encode("utf-8")), report.MAX_BYTES, n)
            for k, _ in walk(p):
                kl = k.lower()
                self.assertNotIn(k, FORBIDDEN, (n, k))
                self.assertFalse(any(w in kl for w in report.SECRET_WORDS), (n, k))
            self.assertEqual(model.scrub(p), p, n)                         # scrub ничего не вырезает

    def test_strings_alphabet(self):
        def check(o, key=None):
            if isinstance(o, dict):
                for k, v in o.items():
                    check(v, k)
            elif isinstance(o, list):
                for v in o:
                    check(v, key)
            elif isinstance(o, str) and key not in report.TEXT_KEYS:
                self.assertIsNone(report.BAD_CHARS.search(o), (key, o))
                self.assertLessEqual(len(o), report.STR_MAX)
        for p in self.fix.values():
            check(p)

    def test_numbers_finite(self):
        for n, p in self.fix.items():
            for k, v in walk(p):
                if isinstance(v, float):
                    self.assertEqual(v, v)
                    self.assertNotIn(v, (float("inf"), float("-inf")))

    def test_front_fixture_names(self):
        self.assertEqual(os.path.basename(H.fixture_file("full")), "payload_full.json")
        self.assertEqual(os.path.basename(H.fixture_file("few")), "payload_few.json")


# ---------------------------------------------------------------- свойства

class TestProperties(Base):
    def test_p1_modes_do_not_mix(self):
        """Свойство 1: данные других режимов (сделки, случаи, пропуски) не меняют отчёт режима; 20 seed."""
        for seed in range(20):
            self.tearDown()
            self.setUp()
            rng = random.Random(seed)
            n = rng.randint(30, 45)
            self.paper(n, seed)
            memory_vs_base.clear()
            base = norm_skips(report.build(self.root, "paper", H.NOW))
            others = []
            for mode, st in (("live", 5000), ("testnet", 7000)):
                m = rng.randint(5, 40)
                rs = H.gauss_rs(rng, [(m, rng.uniform(-1, 1), 1.0)])
                ts = H.times(m, 5 * 3600)
                others += H.trade_recs(rs, ts, mode, prefix="X")
                H.put_cases(self.root, H.case_recs(rng, rs, ts, mode, start=st))
                H.put_cases(self.root, H.skip_recs(rng, 10, ts[:10], mode, start=st + 500))
            H._jsonl(self.root, "trades.jsonl", others, mode="a")
            H._jsonl(self.root, "llm_calls.jsonl", [])
            memory_vs_base.clear()
            self.assertEqual(norm_skips(report.build(self.root, "paper", H.NOW)), base, seed)
            live = report.build(self.root, "live", H.NOW)
            self.assertEqual(live["mode"], "live")
            self.assertEqual(live["verdict"]["n"], len([1 for _ in others if _.get("ev") == "close" and _["id"].endswith("-live")]))

    def test_p9_missing_sources(self):
        """Свойство 9: нет сделок / случаев / меток / журнала / битый learning.json / битый реестр -> ok, статусы допустимы, порядок полон."""
        def check(p):
            self.assertTrue(p["ok"])
            self.assertEqual(p["order"], list(report.ORDER))
            for b in p["blocks"].values():
                self.assertIn(b["status"], report.STATUSES)
            return p
        check(report.build(self.root, "paper", H.NOW))
        self.paper(40)
        os.remove(os.path.join(self.root, "state", "cases.jsonl"))
        cache_dir = os.path.join(self.root, "state")
        self.assertTrue(os.path.isdir(cache_dir))
        check(report.build(self.root, "paper", H.NOW))
        with open(os.path.join(self.root, "config", "learning.json"), "w", encoding="utf-8") as f:
            f.write("{не json")
        p = check(report.build(self.root, "paper", H.NOW))
        self.assertIn("E_CONF:file", p["meta"]["warnings"])
        with open(os.path.join(self.root, "config", "shadow_policies.json"), "w", encoding="utf-8") as f:
            f.write("мусор")
        p = check(report.build(self.root, "paper", H.NOW))
        self.assertEqual(p["blocks"]["vs_policy"]["status"], "error")
        self.assertEqual(p["blocks"]["vs_policy"]["reason"], "E_REGISTRY")
        shutil.rmtree(os.path.join(self.root, "state"))
        check(report.build(self.root, "paper", H.NOW))
        self.assertEqual(report.build(self.root, "paper", H.NOW)["verdict"]["state"], "few_data")

    def test_p10_block_error_isolated(self):
        self.paper(45)
        base = report.build(self.root, "paper", H.NOW)
        memory_vs_base.clear()
        secret = "BOOM-12345-секрет"
        with mock.patch.object(economics, "build", side_effect=RuntimeError(secret)):
            p = report.build(self.root, "paper", H.NOW)
        e = p["blocks"]["economics"]
        self.assertEqual((e["status"], e["reason"]), ("error", "E_BLOCK"))
        self.assertNotIn("BOOM", json.dumps(p, ensure_ascii=False))
        for bid in report.ORDER:
            if bid != "economics":
                self.assertEqual(p["blocks"][bid], base["blocks"][bid], bid)
        self.assertEqual(p["verdict"], base["verdict"])
        for bad in ("scalar", {"status": "weird"}):                       # блок с чужой формой тоже изолируется
            with mock.patch.object(economics, "build", return_value=bad):
                self.assertEqual(report.build(self.root, "paper", H.NOW)["blocks"]["economics"]["reason"], "E_BLOCK")

    def test_p11_deterministic_and_order_free(self):
        rng = random.Random(5)
        rs = H.gauss_rs(rng, [(66, 0.1, 1.0)])
        ts = H.times(66)
        pairs = [H.trade_recs([r], [t], "paper", prefix="P%03d" % i) for i, (r, t) in enumerate(zip(rs, ts))]
        H._jsonl(self.root, "trades.jsonl", [x for p in pairs for x in p])
        H.put_cases(self.root, H.case_recs(rng, rs, ts))
        usage = H.usage_recs(rng)
        H._jsonl(self.root, "llm_calls.jsonl", usage)
        a = dump(report.build(self.root, "paper", H.NOW))
        memory_vs_base.clear()
        self.assertEqual(dump(report.build(self.root, "paper", H.NOW)), a)
        rng.shuffle(pairs)
        H._jsonl(self.root, "trades.jsonl", [x for p in pairs for x in p])
        rows = list(usage)
        random.Random(9).shuffle(rows)
        H._jsonl(self.root, "llm_calls.jsonl", rows)
        memory_vs_base.clear()
        b = json.loads(dump(report.build(self.root, "paper", H.NOW)))
        a = json.loads(a)
        b["blocks"]["economics"], a["blocks"]["economics"] = b["blocks"]["economics"], a["blocks"]["economics"]
        self.assertEqual(b["verdict"], a["verdict"])
        self.assertEqual(b["blocks"]["result"], a["blocks"]["result"])
        self.assertEqual(b["blocks"]["exits"], a["blocks"]["exits"])
        self.assertEqual(b["blocks"]["economics"]["weeks"], a["blocks"]["economics"]["weeks"])

    def test_p13_no_money_no_secrets(self):
        """Свойство 13: суммы и риск сделок, имена с `key` не попадают в ответ; все имена полей чистые."""
        rng = random.Random(3)
        rs = H.gauss_rs(rng, [(70, 0.2, 1.0)])
        recs = H.trade_recs(rs, H.times(70))
        for r in recs:
            if r["ev"] == "open":
                r["risk_usdt"] = 4242.4242
                r["api_key"] = "SECRETVALUE-9f3"
                r["wallet_balance"] = 777777.777
            else:
                r["pnl_usdt"] = round(r["r"] * 4242.4242, 4)
                r["fees_usdt"] = 31.3131
        H._jsonl(self.root, "trades.jsonl", recs)
        H.put_cases(self.root, H.case_recs(rng, rs, H.times(70)))
        p = report.build(self.root, "paper", H.NOW)
        s = json.dumps(p, ensure_ascii=False)
        for bad in ("4242.4242", "SECRETVALUE", "777777", "31.3131", "4242.42"):
            self.assertNotIn(bad, s)
        for k, _ in walk(p):
            self.assertNotIn(k, FORBIDDEN)
            self.assertFalse(any(w in k.lower() for w in report.SECRET_WORDS), k)
        self.assertEqual(model.scrub(p), p)

    def test_p14_read_only(self):
        """Свойство 14: файлы state/config/runtime не меняются при build, get, detail, follow (в том числе база меток)."""
        mode, now = H.setup_scenario("full", self.root)
        before = snapshot(self.root)
        report.build(self.root, mode, now)
        report.get(self.root, mode, now)
        dashapi.detail(self.root, mode, now)
        dashapi.api("/api/learning", {"mode": [mode]}, self.root, now)
        dashapi.follow(self.root, mode, 0.1, now)
        self.assertEqual(snapshot(self.root), before)


# ---------------------------------------------------------------- meta, режим, размер

class TestMeta(Base):
    def test_built_ms_and_sources(self):
        self.paper(40, manual_every=10)
        p = report.build(self.root, "paper", H.NOW)
        self.assertIsInstance(p["meta"]["built_ms"], int)
        self.assertGreaterEqual(p["meta"]["built_ms"], 0)
        self.assertEqual(p["meta"]["sources"], {"trades": 36, "cases": 40, "labels_db": False})
        self.assertEqual(p["meta"]["excluded"]["manual"], 4)
        ticks = iter([0.0, 0.25])
        p = report.build(self.root, "paper", H.NOW, clock=lambda: next(ticks, 0.25))
        self.assertEqual(p["meta"]["built_ms"], 250)

    def test_asof_rounded(self):
        for now in (H.NOW, H.NOW + 1, H.NOW + 29, H.NOW + 30):
            self.assertEqual(report.build(self.root, "paper", now)["asof"], (int(now) // 30) * 30)

    def test_mode_normalized(self):
        for bad in (None, "all", "LIVE", " paper", 5, ["live"], ""):
            self.assertEqual(report.build(self.root, bad, H.NOW)["mode"], "paper")
        for m in ("paper", "testnet", "live"):
            self.assertEqual(report.build(self.root, m, H.NOW)["mode"], m)

    def test_warnings_collected(self):
        # capture_gt_1: выигрыш с r больше mfe_r
        n = 62
        rng = random.Random(2)
        rs = H.gauss_rs(rng, [(n, 0.3, 1.0)])
        recs = H.trade_recs(rs, H.times(n))
        for i, r in enumerate(recs):
            if r["ev"] == "open":
                r["plan_rev"] = "rev%d" % (i // 2 % 2)                   # две версии правил
            if r["ev"] == "close" and r["pnl_usdt"] > 0:
                r["path"]["mfe_r"] = round(r["r"] * 0.5, 3)
        H._jsonl(self.root, "trades.jsonl", recs)
        H.put_cases(self.root, H.case_recs(rng, rs, H.times(n)))
        H._jsonl(self.root, "llm_calls.jsonl", H.usage_recs(rng))
        with mock.patch.object(llm_usage, "MAX_READ", 100):
            p = report.build(self.root, "paper", H.NOW)
        w = p["meta"]["warnings"]
        for code in ("capture_gt_1", "usage_truncated", "mixed_rules"):
            self.assertIn(code, w)
        self.assertEqual(len(w), len(set(w)))
        self.assertNotIn("warnings", p["blocks"]["exits"])
        self.assertNotIn("warnings", p["blocks"]["economics"])
        with open(os.path.join(self.root, "config", "learning.json"), "w", encoding="utf-8") as f:
            json.dump({"schema": "tb.learning_cfg/1", "windows": [10], "bogus": 1}, f)
        w = report.build(self.root, "paper", H.NOW)["meta"]["warnings"]
        self.assertIn("E_CONF:windows", w)
        self.assertIn("E_CONF:bogus", w)

    def test_load_failure_is_warning(self):
        self.paper(31)
        with mock.patch.object(report.load, "usage_rows", side_effect=OSError("x")):
            p = report.build(self.root, "paper", H.NOW)
        self.assertTrue(p["ok"])
        self.assertIn("E_LOAD:usage", p["meta"]["warnings"])
        self.assertEqual(p["blocks"]["economics"]["status"], "no_data")


class TestSize(Base):
    def test_big_install_under_limit(self):
        n = 520
        rng = random.Random(8)
        rs = H.gauss_rs(rng, [(n, 0.1, 1.0)])
        ts = H.times(n, 3 * 3600)
        H._jsonl(self.root, "trades.jsonl", H.trade_recs(rs, ts))
        H.put_cases(self.root, H.case_recs(rng, rs, ts))
        H._jsonl(self.root, "llm_calls.jsonl", H.usage_recs(rng))
        H.write_cfg(self.root, {"schema": "tb.learning_cfg/1", "memory": {"max_trades": 100}})
        t0 = time.perf_counter()
        p = report.build(self.root, "paper", H.NOW)
        self.assertLess(time.perf_counter() - t0, 5.0)
        self.assertEqual(p["blocks"]["memory"]["reason"], "too_many_for_request")
        self.assertLessEqual(len(json.dumps(p, ensure_ascii=False).encode("utf-8")), report.MAX_BYTES)
        self.assertTrue(p["blocks"]["result"]["series"]["50"])

    def test_series_thinned_when_over_limit(self):
        mode, now = H.setup_scenario("full", self.root)
        full = report.build(self.root, mode, now)
        last = full["blocks"]["result"]["series"]["50"][-1]
        with mock.patch.object(report, "MAX_BYTES", 40000):
            p = report.build(self.root, mode, now)
        self.assertIn("series_thinned", p["meta"]["warnings"])
        self.assertLess(len(p["blocks"]["result"]["series"]["50"]), len(full["blocks"]["result"]["series"]["50"]))
        self.assertEqual(p["blocks"]["result"]["series"]["50"][-1], last)
        self.assertLessEqual(report._size(p), 40000)


class TestSanitize(unittest.TestCase):
    def test_strings(self):
        s = report.sanitize({"a": "ok_1.2:3/x-y z", "b": "Привет<script>", "c": "x" * 200, "note": "Текст, с кириллицей и знаками!" + "я" * 300,
                             "title": "Заголовок\x00\n", "list": ["a b", "я"], "t": ("x", 1)})
        self.assertEqual(s["a"], "ok_1.2:3/x-y z")
        self.assertEqual(s["b"], "??????" + "?script?")
        self.assertEqual(len(s["c"]), report.STR_MAX)
        self.assertEqual(len(s["note"]), report.TEXT_MAX)
        self.assertTrue(s["note"].startswith("Текст, с кириллицей"))
        self.assertEqual(s["title"], "Заголовок  ")
        self.assertEqual(s["list"], ["a b", "?"])
        self.assertEqual(s["t"], ["x", 1])

    def test_numbers(self):
        s = report.sanitize({"a": float("nan"), "b": float("inf"), "c": -float("inf"), "d": 0.123456789, "e": 0.0000123456, "f": 0.0, "g": 7, "h": True,
                             "i": None, "j": {1, 2}, "k": 1234.56789, "l": 0.001})
        self.assertEqual((s["a"], s["b"], s["c"]), (None, None, None))
        self.assertEqual((s["d"], s["e"], s["f"], s["g"], s["h"], s["i"], s["j"]), (0.1235, 1.23e-05, 0.0, 7, True, None, None))
        self.assertEqual((s["k"], s["l"]), (1234.5679, 0.001))

    def test_keys_to_str(self):
        self.assertEqual(report.sanitize({50: {"a": 1}}), {"50": {"a": 1}})


# ---------------------------------------------------------------- кеш, computing, dashapi

class TestCacheAndApi(Base):
    def test_cache_hit_and_signature(self):
        self.paper(35)
        a = report.get(self.root, "paper", H.NOW, mono=100.0)
        self.assertFalse(a["meta"]["cached"])
        t0 = time.perf_counter()
        b = report.get(self.root, "paper", H.NOW + 5, mono=101.0)
        self.assertLess(time.perf_counter() - t0, 0.05)
        self.assertTrue(b["meta"]["cached"])
        self.assertEqual(b["asof"], a["asof"])
        b["verdict"]["n"] = -1                                                # копия: кеш не портится
        self.assertEqual(report.get(self.root, "paper", H.NOW, mono=102.0)["verdict"]["n"], a["verdict"]["n"])
        self.assertNotEqual(a["mode"], "x")
        # другой режим - другой ключ
        self.assertFalse(report.get(self.root, "live", H.NOW, mono=102.0)["meta"]["cached"])
        # смена подписи: до min_interval кеш живёт, после - пересчёт
        rs37 = H.gauss_rs(random.Random(2), [(37, 0.1, 1.0)])
        H._jsonl(self.root, "trades.jsonl", H.trade_recs(rs37, H.times(37)))
        self.assertTrue(report.get(self.root, "paper", H.NOW, mono=105.0)["meta"]["cached"])
        c = report.get(self.root, "paper", H.NOW, mono=115.0)
        self.assertFalse(c["meta"]["cached"])
        self.assertEqual(c["verdict"]["n"], 37)
        # ttl 60 с
        self.assertFalse(report.get(self.root, "paper", H.NOW, mono=180.0)["meta"]["cached"])

    def test_cache_parallel_single_build(self):
        self.paper(35)
        calls, real = [], report.build

        def counting(*a, **kw):
            calls.append(1)
            time.sleep(0.2)
            return real(*a, **kw)
        out = []
        with mock.patch.object(report, "build", side_effect=counting):
            ts = [threading.Thread(target=lambda: out.append(report.get(self.root, "paper", H.NOW))) for _ in range(6)]
            [t.start() for t in ts]
            [t.join(20) for t in ts]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(out), 6)
        self.assertEqual(sum(1 for o in out if o["meta"]["cached"]), 5)

    def test_exception_not_cached(self):
        self.paper(35)
        with mock.patch.object(report, "build", side_effect=RuntimeError("x")):
            with self.assertRaises(RuntimeError):
                report.get(self.root, "paper", H.NOW)
        self.assertTrue(report.get(self.root, "paper", H.NOW)["ok"])

    def test_computing_not_cached_and_follow(self):
        H.setup_scenario("computing", self.root)
        gate, real = threading.Event(), backtest.walk_forward

        def slow(*a, **kw):
            gate.wait(30)
            return real(*a, **kw)
        try:
            with mock.patch.object(backtest, "walk_forward", side_effect=slow):
                p = dashapi.detail(self.root, "paper", H.NOW)
                self.assertEqual(p["blocks"]["memory"]["status"], "computing")
                self.assertTrue(dashapi.is_computing(p))
                p2 = dashapi.detail(self.root, "paper", H.NOW)                 # не из кеша: computing не кладётся
                self.assertFalse(p2["meta"]["cached"])
                self.assertIsNone(dashapi.follow(self.root, "paper", 0.2, H.NOW))      # не дождались
                gate.set()
                done = dashapi.follow(self.root, "paper", 20.0, H.NOW)
            self.assertIsNotNone(done)
            self.assertFalse(dashapi.is_computing(done))
            self.assertIn(done["blocks"]["memory"]["status"], ("ok", "few_data"))
            self.assertGreater(done["blocks"]["memory"]["n_eval"], 0)
            ref = backtest.walk_forward(backtest.closed_trades(__import__("tb.memory.cases", fromlist=["x"]).load(self.root),
                                                               __import__("tb.memory.cfg", fromlist=["x"]).load(self.root), "paper"),
                                        __import__("tb.memory.cfg", fromlist=["x"]).load(self.root))
            self.assertEqual(done["blocks"]["memory"]["n_eval"], ref["n_eval"])
            self.assertEqual(done["blocks"]["memory"]["brier_mem"], ref["brier_mem"])
            self.assertTrue(dashapi.detail(self.root, "paper", H.NOW)["ok"])          # готовый отчёт кешируется
            again = dashapi.follow(self.root, "paper", 0.1, H.NOW)                    # расчёт уже готов: отчёт собирается заново, без computing
            self.assertFalse(dashapi.is_computing(again))
        finally:
            gate.set()

    def test_follow_without_flight(self):
        self.assertIsNone(dashapi.follow(self.root, "live", 0.1))
        self.assertIsNone(dashapi.follow(self.root, "bogus", 0.1))

    def test_api(self):
        self.paper(35)
        self.assertIsNone(dashapi.api("/api/other", {}, self.root, H.NOW))
        self.assertIsNone(dashapi.api("/api/learning/x", {}, self.root, H.NOW))
        code, p = dashapi.api("/api/learning", {"mode": ["paper"]}, self.root, H.NOW)
        self.assertEqual((code, p["schema"], p["mode"]), (200, "tb.learning/1", "paper"))
        self.assertEqual(model.scrub(p), p)
        for q, want in (({}, "paper"), (None, "paper"), ({"mode": ["live"]}, "live"), ({"mode": "testnet"}, "testnet"), ({"mode": ["ALL"]}, "paper"),
                        ({"mode": []}, "paper"), ({"mode": ["  paper"]}, "paper")):
            self.assertEqual(dashapi.api("/api/learning", q, self.root, H.NOW)[1]["mode"], want, q)

    def test_failure_is_ok_false(self):
        with mock.patch.object(report, "get", side_effect=RuntimeError("секрет")):
            d = dashapi.detail(self.root, "live", H.NOW)
            self.assertEqual(d, {"ok": False, "mode": "live", "error": "failed"})
            self.assertEqual(dashapi.detail(self.root, "zzz", H.NOW)["mode"], "paper")
            code, p = dashapi.api("/api/learning", {"mode": ["live"]}, self.root, H.NOW)
        self.assertEqual((code, p["ok"], p["error"]), (500, False, "failed"))
        self.assertNotIn("секрет", json.dumps(p, ensure_ascii=False))

    def test_default_conf_is_used(self):
        self.assertEqual(conf.load(self.root)[0]["cache"], {"ttl_s": 60, "min_interval_s": 10})


if __name__ == "__main__":
    unittest.main()
