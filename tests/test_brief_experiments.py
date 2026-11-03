"""Блок досье `experiments` по семействам и справка меток (R2, задача 36; свойство 32; Requirements 19.5, 19.7, 19.8).

Худший случай блока (5 подтверждённых + 5 открытых строк со всеми полями) по brief.tokens не больше прежнего худшего случая (строки с win, без ref);
справка ref: [n, mean, lo, hi] есть только у семейств policy_map, не меняет confirmed и ранг карточек (playbook.match); без файла справки блок
работает как прежде; поля win в строках нет; `./tb exp stats --json` берёт k и alpha_adj из group_stats (hyp_k). Временный корень, сети нет."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import time
import unittest

import _bootstrap  # noqa: F401
from tb import paths
from tb.brain import brief, exp_cli, playbook
from tb.core import expstats as X
from tb.core import hyps

NOW = 1793750400.0
PROJECT = _bootstrap.ROOT


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def worst_families():
    """10 семейств (без other) с самыми длинными именами, семейства policy_map — обязательно (у них может быть ref)."""
    pm = sorted(hyps.policy_map())
    rest = sorted([f for f in hyps.families() if f != "other" and f not in pm], key=lambda f: (-len(f), f))
    return pm + rest[:10 - len(pm)]


# крайние значения полей: n сделок 4 знака, R с двумя знаками до запятой; n кластеров справки <= 14 суток * 24 часа * 2 стороны = 672
BIG_N, BIG_R, WIN, REF_N = 9999, -10.55, 0.67, 14 * 24 * 2


def old_worst():
    """Прежний худший случай (до задачи 36): строки {hyp, n, avg_r, win, live_n, live_avg_r}."""
    row = lambda h: {"hyp": h, "n": BIG_N, "avg_r": BIG_R, "win": WIN, "live_n": BIG_N, "live_avg_r": BIG_R}
    fams = sorted([f for f in hyps.families() if f != "other"], key=lambda f: (-len(f), f))[:10]
    return {"min_n": X.MIN_N, "confirmed": [row(h) for h in fams[:5]], "open": [row(h) for h in fams[5:]]}


def new_worst():
    p = {"n": BIG_N, "avg_r": BIG_R}
    ref = {"n": REF_N, "mean": BIG_R, "lo": BIG_R, "hi": BIG_R}
    pm = hyps.policy_map()
    rows = [brief._exp_row(h, p, {"n": BIG_N, "avg_r": BIG_R}, ref if h in pm else None) for h in worst_families()]
    return {"min_n": X.MIN_N, "confirmed": rows[:5], "open": rows[5:]}


class Ctx(object):
    def __init__(self, root, env="mainnet"):
        self.root = root
        self.health = {"data": {"env": env}}


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbbx_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def trades(self, rows):
        p = paths.state("trades.jsonl", root=self.root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows))

    def trade(self, i, hyp, r, mode="paper"):
        tid = "BB-%s-%s-%03d" % (mode[0].upper(), hyp, i)
        ts = iso(NOW - 3600 - i)
        return [{"ev": "open", "id": tid, "ts_open": ts, "sym": "SOL", "side": "long", "entry": 100.0, "lev": 5.0, "margin": 4.0, "notional": 20.0,
                 "sl": 99.0, "tp": 103.0, "risk_usdt": 0.2, "mode": mode, "stype": "intraday", "venue": "platform", "hyp": hyp},
                {"ev": "close", "id": tid, "ts_close": ts, "exit": 101.0, "reason": "tp" if r > 0 else "sl", "pnl_usdt": r * 0.2, "r": r}]

    def history(self):
        rows = []
        for i in range(30):                                       # подтверждается: 20 x +1R, 10 x -0.5R; варианты написания сводятся
            rows += self.trade(i, "liq_cascade_reclaim" if i % 2 else "liq-cascade-reclaim", 1.0 if i < 20 else -0.5)
        for i in range(3):
            rows += self.trade(100 + i, "oi-flush-reclaim", -0.2)
        for i in range(4):
            rows += self.trade(200 + i, "range-fade-lower-edge", 0.3)
        for i in range(2):
            rows += self.trade(300 + i, "liq-cascade-reclaim", 0.4, mode="live")
        self.trades(rows)

    def ref_file(self, fams=None, env="mainnet"):
        fams = fams or {"liq-cascade-reclaim": (40, -0.123, -0.31, 0.064), "oi-flush-reclaim": (1, 0.5, None, None),
                        "range-fade": (50, 0.2, 0.1, 0.3)}           # range-fade: нет в policy_map -> читатели игнорируют
        doc = {"schema": "tb.hyp_ref/1", "ts": int(NOW), "window_days": 14, "a": int(NOW) - 14 * 86400, "b": int(NOW), "families": {}}
        for f, (n, m, lo, hi) in fams.items():
            doc["families"][f] = {"policy": "P4", "policy_ver": 1, "kinds": ["liq_cascade"], "env": {env: {"n": n, "n_runs": n, "mean": m, "lo": lo, "hi": hi}}}
        p = paths.runtime("learn", "hyp_ref.json", root=self.root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(doc, f)

    def block(self, env="mainnet"):
        return brief._b_experiments(Ctx(self.root, env))


class TestTokenBudget(unittest.TestCase):
    def test_property32_worst_case_not_above_previous(self):
        old, new = brief.tokens(old_worst()), brief.tokens(new_worst())
        self.assertLessEqual(new, old, (new, old))
        self.assertTrue(all("win" not in r for r in new_worst()["confirmed"] + new_worst()["open"]))
        self.assertEqual(sum(1 for r in new_worst()["confirmed"] + new_worst()["open"] if "ref" in r), len(hyps.policy_map()))

    def test_ref_compact_shape(self):
        r = brief._exp_row("liq-cascade-reclaim", {"n": 3, "avg_r": 0.1234}, None, {"n": 12, "mean": 0.1234, "lo": None, "hi": None})
        self.assertEqual(r, {"hyp": "liq-cascade-reclaim", "n": 3, "avg_r": 0.12, "ref": [12, 0.12, None, None]})


class TestBlock(Base):
    def test_families_ref_only_policy_map_no_win(self):
        self.history()
        self.ref_file()
        b = self.block()
        conf = {r["hyp"]: r for r in b["confirmed"]}
        opn = {r["hyp"]: r for r in b["open"]}
        self.assertEqual(list(conf), ["liq-cascade-reclaim"])
        self.assertEqual(conf["liq-cascade-reclaim"]["n"], 30)
        self.assertEqual((conf["liq-cascade-reclaim"]["live_n"], conf["liq-cascade-reclaim"]["live_avg_r"]), (2, 0.4))
        self.assertEqual(conf["liq-cascade-reclaim"]["ref"], [40, -0.12, -0.31, 0.06])     # справка с отрицательным средним не снимает confirmed
        self.assertEqual(opn["oi-flush-reclaim"]["ref"], [1, 0.5, None, None])
        self.assertNotIn("ref", opn["range-fade"])                                      # семейство без policy_map справки не имеет
        for r in b["confirmed"] + b["open"]:
            self.assertNotIn("win", r)

    def test_without_ref_file_same_status_and_rank(self):
        self.history()
        no_ref = self.block()
        self.assertTrue(all("ref" not in r for r in no_ref["confirmed"] + no_ref["open"]))
        self.ref_file()
        with_ref = self.block()
        strip = lambda b: dict((k, [dict((kk, vv) for kk, vv in r.items() if kk != "ref") for r in b[k]]) for k in ("confirmed", "open"))
        self.assertEqual(strip(no_ref), strip(with_ref))
        self.assertEqual(playbook.exp_index(no_ref), playbook.exp_index(with_ref))
        cards, _ = playbook.load(PROJECT)
        for kinds in (["liq_cascade"], ["oi_flush"], ["liq_cascade", "range_5m"], ["oi_spike"]):
            for reg in (None, "range", "trend_up"):
                self.assertEqual(playbook.match(cards, kinds, reg, playbook.exp_index(no_ref)),
                                 playbook.match(cards, kinds, reg, playbook.exp_index(with_ref)), (kinds, reg))

    def test_confirmed_family_plus_two(self):
        self.history()
        cards, _ = playbook.load(PROJECT)
        idx = playbook.exp_index(self.block())
        self.assertTrue(idx["liq-cascade-reclaim"]["confirmed"])
        card = [c for c in cards if c["hyp"] == "liq-cascade-reclaim"][0]
        base = playbook.match([card], card["kinds"], None, {})
        up = playbook.match([card], card["kinds"], None, idx)
        self.assertEqual(up[0]["exp"], {"n": 30, "avg_r": idx["liq-cascade-reclaim"]["avg_r"]})
        self.assertNotIn("exp", base[0])
        two = [c for c in cards if set(c["kinds"]) & {"liq_cascade"}]
        if len(two) > 1:
            self.assertEqual(playbook.match(two, ["liq_cascade"], None, idx)[0]["hyp"], "liq-cascade-reclaim")

    def test_exp_index_by_family(self):
        idx = playbook.exp_index({"confirmed": [{"hyp": "liq_cascade_reclaim", "n": 31, "avg_r": 0.3}],
                                  "open": [{"hyp": "liq-cascade-reclaim", "n": 4, "avg_r": -0.1}, {"hyp": "probe-x", "n": 2, "avg_r": 0.1}]})
        self.assertEqual(idx["liq-cascade-reclaim"], {"n": 31, "avg_r": 0.3, "confirmed": True})     # открытая строка не затирает подтверждённую
        self.assertIn("probe-x", idx)                                                                 # неизвестный тег не склеивается в other

    def test_env_testnet_ref(self):
        self.history()
        self.ref_file(env="testnet")
        self.assertTrue(all("ref" not in r for r in self.block("mainnet")["confirmed"]))
        self.assertIn("ref", self.block("testnet")["confirmed"][0])

    def test_broken_ref_file_ignored(self):
        self.history()
        p = paths.runtime("learn", "hyp_ref.json", root=self.root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("{not json")
        b = self.block()
        self.assertEqual([r["hyp"] for r in b["confirmed"]], ["liq-cascade-reclaim"])
        self.assertEqual(exp_cli.hyp_ref(self.root), {})


class TestExpCli(Base):
    def cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = exp_cli.main(list(argv), root=self.root)
        return code, out.getvalue()

    def test_stats_json_k_from_group_stats(self):
        self.history()
        code, out = self.cli("stats", "--by", "hyp", "--json")
        self.assertEqual(code, 0)
        d = json.loads(out)
        rows = X.group_stats(X.load_trades("all", self.root, with_rules=False), "hyp")
        self.assertEqual(d["k"], rows[0]["k"])
        self.assertEqual(d["k"], 1)                                   # одно семейство с paper n >= 30, а групп (ключ, режим) больше
        self.assertGreater(len(d["groups"]), d["k"])
        self.assertEqual(d["alpha_adj"], rows[0]["alpha_adj"])
        self.assertEqual(d["hyp_ref"], {})
        d2 = json.loads(self.cli("stats", "--by", "setup", "--json")[1])
        self.assertEqual(d2["k"], len(d2["groups"]))                  # прочие разрезы: k = число групп, как раньше

    def test_stats_and_show_print_ref(self):
        self.history()
        self.ref_file()
        _c, out = self.cli("stats", "--by", "hyp")
        self.assertIn("Справка меток", out)
        self.assertIn("liq-cascade-reclaim: политика P4", out)
        self.assertNotIn("range-fade: политика", out)
        d = json.loads(self.cli("stats", "--by", "hyp", "--json")[1])
        self.assertEqual(sorted(d["hyp_ref"]), ["liq-cascade-reclaim", "oi-flush-reclaim"])
        self.assertEqual(d["hyp_ref"]["liq-cascade-reclaim"]["n"], 40)
        exp_rows = [dict(r, exp="e1", var="a") if r.get("ev") == "open" else r for r in self.trade(900, "liq-cascade-reclaim", 0.5)]
        self.trades(exp_rows)
        _c, out = self.cli("show", "e1")
        self.assertIn("liq-cascade-reclaim: политика P4", out)
        d = json.loads(self.cli("show", "e1", "--json")[1])
        self.assertEqual(d["hyps"], ["liq-cascade-reclaim"])
        self.assertEqual(sorted(d["hyp_ref"]), ["liq-cascade-reclaim"])


if __name__ == "__main__":
    unittest.main()
