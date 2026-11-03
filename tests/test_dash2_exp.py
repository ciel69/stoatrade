"""Операция `exp` нового дашборда (tb.dash2.topics.exp_detail): эксперименты и разрезы по журналу сделок, как `./tb exp stats`, без вердиктов.
Временный корень, без платформы, сети и живых команд."""
import asyncio
import json
import os
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb.dash2 import server as S, topics

try:
    import websockets  # noqa: F401
    from websockets.asyncio.client import connect
    HAVE_WS = True
except ImportError:
    HAVE_WS = False


def journal(root, lines):
    p = os.path.join(root, "state", "trades.jsonl")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for x in lines:
            f.write((x if isinstance(x, str) else json.dumps(x)) + "\n")


def trade(i, r, mode="paper", sym="BTCUSDT", side="long", **kw):
    """Пара записей open + close; r > 0 выигрыш. Риск 1 USDT, поэтому pnl = r."""
    o = {"ev": "open", "id": "T%d" % i, "sym": sym, "side": side, "mode": mode, "ts_open": "2026-11-01T10:%02d:00Z" % (i % 60), "entry": 100, "sl": 99, "tp": 102, "margin": 5,
         "lev": 5, "risk_usdt": 1.0, "venue": "bybit", "api_key": "SECRET-KEY"}
    o.update({k: v for k, v in kw.items() if k in ("exp", "var", "hyp", "setup", "stype", "tags")})
    c = {"ev": "close", "id": "T%d" % i, "ts_close": "2026-11-02T%02d:%02d:00Z" % (i // 60 % 24, i % 60), "exit": 101, "pnl_usdt": r, "r": r, "fees_usdt": 0.01, "reason": kw.get("reason", "tp"),
         "err": kw.get("err")}
    return [o, c]


def many(start, rs, **kw):
    out = []
    for j, r in enumerate(rs):
        out += trade(start + j, r, **kw)
    return out


class ExpDetail(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbdash2e_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_empty_root_gives_empty_rows_and_no_crash(self):
        d = topics.exp_detail(self.root, "exp", "all")
        self.assertEqual((d["ok"], d["rows"], d["k"], d["alpha_adj"], d["total_closed"], d["confirmed"]), (True, [], 0, None, 0, []))

    def test_unknown_by_and_mode_fall_back(self):
        d = topics.exp_detail(self.root, "../etc", "evil")
        self.assertEqual(d["by"], "exp")
        self.assertIn(d["mode"], topics.MODES)

    def test_small_group_is_marked_without_p_and_k_counts_groups(self):
        journal(self.root, many(1, [1.0, -1.0, 2.0], exp="E1", var="a") + many(10, [1.0], exp="E2", var="b"))
        d = topics.exp_detail(self.root, "exp", "paper")
        self.assertEqual((d["k"], d["rows_total"], d["total_closed"]), (2, 2, 4))
        self.assertAlmostEqual(d["alpha_adj"], 0.025)
        r = {x["name"]: x for x in d["rows"]}
        self.assertEqual((r["E1"]["n"], r["E1"]["small"], r["E1"]["p"], r["E1"]["confirmed"]), (3, True, None, False))
        self.assertEqual(len(r["E1"]["trades"]), 3)
        self.assertEqual(set(r["E1"]["trades"][0]), {"id", "sym", "side", "mode", "r", "pnl", "ts", "reason"})

    def test_hypothesis_with_enough_wins_is_confirmed_like_the_brief(self):
        rs = [1.0, 1.5, 0.8, 1.2] * 8            # n = 32, средний R заметно выше нуля
        # семейства словаря: написания range-fade сводятся в одно, trend_pullback -> trend-pullback, неизвестный тег -> other (Requirement 19.3)
        journal(self.root, many(1, rs[:16], hyp="Range_Fade") + many(50, rs[16:], hyp="range-fade-lower-edge") + many(100, [1.0, -1.0] * 15, hyp="trend_pullback")
                + many(200, [1.0], hyp="H-new"))
        d = topics.exp_detail(self.root, "hyp", "paper")
        r = {x["name"]: x for x in d["rows"]}
        self.assertEqual(set(r), {"range-fade", "trend-pullback", "other"})
        self.assertEqual((d["k"], d["rows_total"]), (2, 3))                  # K = семейства кроме other с бумажной n >= 30, а не число строк
        self.assertAlmostEqual(d["alpha_adj"], 0.025)
        self.assertEqual(r["range-fade"]["n"], 32)
        self.assertFalse(r["range-fade"]["small"])
        self.assertIsNotNone(r["range-fade"]["p"])
        self.assertTrue(r["range-fade"]["confirmed"])
        self.assertFalse(r["trend-pullback"]["confirmed"])
        self.assertFalse(r["other"]["confirmed"])
        self.assertEqual([c["hyp"] for c in d["confirmed"]], ["range-fade"])
        # на других разрезах значок «подтверждена» не выдаётся, а перечень подтверждённых остаётся
        d2 = topics.exp_detail(self.root, "symbol", "paper")
        self.assertTrue(all(not x["confirmed"] for x in d2["rows"]))
        self.assertEqual([c["hyp"] for c in d2["confirmed"]], ["range-fade"])

    def test_other_family_is_never_confirmed(self):
        rs = [1.0, 1.5, 0.8, 1.2] * 8
        journal(self.root, many(1, rs, hyp="probe-x"))                       # сильная выборка, но тег неизвестен -> other
        d = topics.exp_detail(self.root, "hyp", "paper")
        r = {x["name"]: x for x in d["rows"]}
        self.assertEqual(list(r), ["other"])
        self.assertIsNotNone(r["other"]["p"])
        self.assertFalse(r["other"]["below_alpha"])
        self.assertFalse(r["other"]["confirmed"])
        self.assertEqual((d["k"], d["confirmed"]), (1, []))

    def test_modes_are_not_mixed_and_filter_works(self):
        journal(self.root, many(1, [1.0, 1.0], exp="E", mode="paper") + many(10, [-1.0], exp="E", mode="live"))
        both = topics.exp_detail(self.root, "exp", "all")
        self.assertEqual(sorted((x["mode"], x["n"]) for x in both["rows"]), [("live", 1), ("paper", 2)])
        live = topics.exp_detail(self.root, "exp", "live")
        self.assertEqual([(x["mode"], x["n"]) for x in live["rows"]], [("live", 1)])

    def test_untagged_trades_are_skipped_and_error_tags_split(self):
        journal(self.root, many(1, [1.0], exp="E") + many(5, [-1.0]) + trade(9, -1.0, err=["late_entry", "no_stop"]) + trade(10, 1.0, err=["late_entry"]))
        d = topics.exp_detail(self.root, "exp", "paper")
        self.assertEqual([x["name"] for x in d["rows"]], ["E"])
        e = topics.exp_detail(self.root, "err", "paper")
        self.assertEqual({x["name"]: x["n"] for x in e["rows"]}, {"late_entry": 2, "no_stop": 1})
        self.assertEqual(e["unreviewed"], 2)

    def test_other_cuts(self):
        journal(self.root, trade(1, 1.0, sym="ETHUSDT", side="short", stype="scalp", reason="sl") + trade(2, -1.0, sym="ETHUSDT", side="long", stype="scalp", reason="tp"))
        for by, keys in (("symbol", ["ETHUSDT"]), ("side", ["long", "short"]), ("stype", ["scalp"]), ("reason", ["sl", "tp"]), ("venue", ["bybit"])):
            d = topics.exp_detail(self.root, by, "paper")
            self.assertEqual(sorted(x["name"] for x in d["rows"]), keys, by)

    def test_broken_lines_are_skipped(self):
        journal(self.root, ["не json", "[1, 2]", '{"ev": "close", "id": "ghost"}'] + many(1, [1.0], exp="E") + ['{"ev": "open"}'])
        d = topics.exp_detail(self.root, "exp", "paper")
        self.assertEqual([(x["name"], x["n"]) for x in d["rows"]], [("E", 1)])

    def test_no_secrets_and_size_limits(self):
        journal(self.root, many(1, [1.0] * 40, exp="E") + [x for i in range(80) for x in trade(100 + i, 0.5, exp="X%03d" % i)])
        d = topics.exp_detail(self.root, "exp", "paper")
        self.assertNotIn("SECRET-KEY", json.dumps(d))
        self.assertEqual(d["rows_total"], 81)
        self.assertEqual(len(d["rows"]), topics.EXP_ROWS_OUT)
        self.assertEqual(d["k"], 81)
        big = [x for x in d["rows"] if x["name"] == "E"][0]
        self.assertEqual((big["n"], len(big["trades"])), (40, topics.EXP_TRADES_OUT))

    def test_experiment_meta_is_limited_and_whitelisted(self):
        journal(self.root, many(1, [1.0], exp="E", var="a"))
        p = os.path.join(self.root, "config", "experiments.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"E": {"hypothesis": "h" * 500, "prereg": "p", "started": "2026-11-01", "min_n": 30, "token": "SECRET-T"}}, f)
        d = topics.exp_detail(self.root, "variant", "paper")
        self.assertEqual(d["rows"][0]["name"], "E/a")
        self.assertEqual(len(d["meta"]["E"]["hypothesis"]), 200)
        self.assertNotIn("SECRET-T", json.dumps(d))
        self.assertEqual(set(d["meta"]["E"]), {"hypothesis", "prereg", "started", "min_n"})


@unittest.skipUnless(HAVE_WS, "нет websockets")
class ExpServer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _bootstrap.require_localhost()
        self.root = tempfile.mkdtemp(prefix="tbdash2es_")
        self.addCleanup(shutil.rmtree, self.root, True)
        journal(self.root, many(1, [1.0, -1.0], exp="E"))
        self.ready = asyncio.get_event_loop().create_future()
        self.task = asyncio.ensure_future(S.serve(port=0, root=self.root, ready=self._ready))
        await asyncio.wait_for(self.ready, 5)

    def _ready(self, hub, srv):
        self.port = list(srv.sockets)[0].getsockname()[1]
        hub.port = self.port
        self.ready.set_result(True)

    async def asyncTearDown(self):
        self.task.cancel()
        try:
            await self.task
        except BaseException:
            pass

    async def test_exp_answers_only_asker_and_survives_garbage(self):
        async with connect("ws://127.0.0.1:%d/ws" % self.port) as a, connect("ws://127.0.0.1:%d/ws" % self.port) as b:
            async def reply(ws):
                for _ in range(60):
                    m = json.loads(await asyncio.wait_for(ws.recv(), 3))
                    if m["topic"] == "exp_detail":
                        return m
                self.fail("ответа exp_detail нет")
            await a.send(json.dumps({"op": "exp", "by": {"x": 1}, "mode": 5}))
            m = await reply(a)
            self.assertTrue(m["data"]["ok"])
            self.assertEqual(m["data"]["by"], "exp")
            await a.send(json.dumps({"op": "exp", "by": "symbol", "mode": "paper"}))
            m = await reply(a)
            self.assertEqual((m["data"]["by"], m["data"]["rows"][0]["name"]), ("symbol", "BTCUSDT"))
            seen = []
            try:
                while True:
                    seen.append(json.loads(await asyncio.wait_for(b.recv(), 0.5))["topic"])
            except asyncio.TimeoutError:
                pass
            self.assertNotIn("exp_detail", seen)


if __name__ == "__main__":
    unittest.main()
