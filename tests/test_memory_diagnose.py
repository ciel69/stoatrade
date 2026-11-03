"""R2.0 (задача 1): диагноз «почему уроков 0» — воронка, коды отсева, причины, только чтение, --out лишь в runtime/."""
import contextlib
import hashlib
import io
import json
import os
import unittest

import _bootstrap  # noqa: F401
from _memory_helpers import NOW, TmpRoot, mk
from tb.memory import cases, cli, diagnose, lessons


def fixture_63(n=63, regime_known=11):
    """Пул замера: n закрытых сделок, режим известен у regime_known; R разнесены по признакам, чтобы были кандидаты."""
    out = []
    for i in range(n):
        reg = ("range", "trend")[i % 2] if i < regime_known else "unknown"
        conf = i % 3 != 0
        r = (0.6 + (i % 4) * 0.2) if conf else (-1.0 - (i % 5) * 0.1)
        out.append(mk(i, sym="S%d" % i, side="long" if i % 2 else "short", r=round(r, 2), confirmed=conf, regime=reg,
                      coin_class=("meme", "alt_mid", "major")[i % 3], utc_session=("asia", "europe", "us")[i % 3],
                      range_5m_pct=round(0.5 + (i % 6) * 0.6, 2), stop_pct=round(1.0 + (i % 4) * 0.5, 2), rr_planned=2.0))
    return out


def state_hashes(root):
    out = {}
    base = os.path.join(root, "state")
    for d, _, fs in os.walk(base):
        for f in fs:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, base)] = hashlib.sha256(fh.read()).hexdigest()
    return out


class DiagBase(TmpRoot):
    def run_cli(self, *argv):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            code = cli.main(["lesson", "diagnose"] + list(argv), root=self.root)
        return code, buf.getvalue(), err.getvalue()

    def rep(self):
        return diagnose.diagnose(self.root, self.cfg, NOW)


class TestFunnel(DiagBase):
    def test_empty_pool(self):
        r = self.rep()
        self.assertEqual(r["funnel"]["cases"], 0)
        self.assertEqual(r["funnel"]["pool"], 0)
        self.assertEqual(r["drops"]["POOL_TOO_SMALL"], 1)
        self.assertEqual(r["funnel"]["conditions"], 0)
        self.assertIn("DATA_VOLUME", r["reasons"])
        self.assertNotIn("NONE", r["reasons"])
        self.assertNotIn("WIRING", r["reasons"])

    def test_pool_below_min_n(self):
        self.put(fixture_63(n=3, regime_known=3))
        r = self.rep()
        self.assertEqual(r["funnel"]["pool"], 3)
        self.assertEqual(r["drops"]["POOL_TOO_SMALL"], 1)
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("POOL_TOO_SMALL", out)

    def test_fixture_63_reasons(self):
        self.put(fixture_63())
        self.put([mk(500 + i, sym="I%d" % i, type="idea") for i in range(5)])
        r = self.rep()
        f = r["funnel"]
        self.assertEqual((f["cases"], f["closed"], f["pool"], f["regime_known"]), (68, 63, 63, 11))
        self.assertIn("DATA_QUALITY", r["reasons"])
        self.assertIn("MULTIPLICITY", r["reasons"])
        self.assertGreaterEqual(r["k"] * r["alpha"], 1)
        self.assertEqual(r["drops"]["POOL_TOO_SMALL"], 0)
        # воронка монотонна и согласована с кодами отсева
        self.assertGreaterEqual(f["conditions"], f["pass_n"])
        self.assertGreaterEqual(f["pass_n"], f["pass_diff"])
        self.assertGreaterEqual(f["pass_diff"], f["pass_share"])
        self.assertEqual(f["conditions"] - f["pass_n"], r["drops"]["BELOW_N"])
        self.assertEqual(f["pass_n"] - r["drops"]["DUP_ACTIVE"] - f["pass_diff"], r["drops"]["BELOW_DIFF"])
        self.assertEqual(f["pass_diff"] - f["pass_share"], r["drops"]["BELOW_SHARE"])
        self.assertEqual(f["unique"] - f["would_create"], r["drops"]["BUDGET"])
        self.assertLessEqual(f["would_create"], self.cfg["lessons"]["max_new_per_day"])
        # число «создано бы» совпадает с кодовым propose
        self.assertEqual(f["would_create"], len(lessons.propose(self.root, cases.load(self.root), self.cfg, NOW)))
        self.assertEqual(set(r["drops"]), set(diagnose.DROP_CODES))

    def test_none_when_clean(self):
        cfg = json.loads(json.dumps(self.cfg))
        cfg["lessons"]["thresholds"], cfg["lessons"]["le_thresholds"] = {}, {}
        rows = [mk(i, sym="C%d" % i, r=0.5, confirmed=True, regime="range", coin_class="major", rr_planned=2.0) for i in range(40)]
        self.put(rows)
        r = diagnose.diagnose(self.root, cfg, NOW)
        self.assertEqual(r["reasons"], ["NONE"])

    def test_dup_active(self):
        self.put(fixture_63())
        lessons.create(self.root, [{"feature": "confirmed", "op": "=", "value": False}], {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 1.0},
                       "owner", cases.load(self.root), self.cfg, NOW)
        r = self.rep()
        self.assertGreaterEqual(r["drops"]["DUP_ACTIVE"], 1)
        self.assertTrue(r["lessons"]["file"])
        self.assertEqual(r["lessons"]["by_status"]["hyp"], 1)
        self.assertEqual(r["lessons"]["line"], "lessons: 1 (hyp 1, confirmed 0, rejected 0, expired 0, merged 0)")


class TestEdges(DiagBase):
    def test_no_lessons_file(self):
        self.put(fixture_63(n=10))
        r = self.rep()
        self.assertFalse(r["lessons"]["file"])
        self.assertEqual(r["lessons"]["line"], "lessons: 0 (файла нет)")
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("lessons: 0 (файла нет)", out)
        self.assertFalse(os.path.exists(lessons.lessons_path(self.root)))

    def test_truncated_last_line(self):
        self.put(fixture_63(n=10))
        with open(cases.cases_path(self.root), "ab") as f:
            f.write(b'{"schema":"tb.case/1","seq":11,"case_id":"C-trunc')
        r = self.rep()
        self.assertEqual(r["funnel"]["cases"], 10)
        self.assertEqual(r["cases_broken"], 1)
        self.assertTrue(any("битых строк" in n for n in r["notes"]))
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("битых строк cases.jsonl пропущено 1", out)

    def test_deterministic(self):
        self.put(fixture_63())
        a, b = self.rep(), self.rep()
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        c1, o1, _ = self.run_cli("--json")
        c2, o2, _ = self.run_cli("--json")
        self.assertEqual(json.loads(o1)["funnel"], json.loads(o2)["funnel"])
        self.assertEqual(json.loads(o1)["reasons"], json.loads(o2)["reasons"])


class TestNoWrite(DiagBase):
    def test_state_unchanged(self):
        self.put(fixture_63())
        lessons.create(self.root, [{"feature": "blackout", "op": "=", "value": True}], {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 1.0},
                       "owner", cases.load(self.root), self.cfg, NOW)
        before = state_hashes(self.root)
        for argv in ((), ("--json",), ("--text",), ("--out", "runtime/diag/r.json")):
            code, _, _ = self.run_cli(*argv)
            self.assertEqual(code, 0, argv)
        self.assertEqual(state_hashes(self.root), before)
        with open(os.path.join(self.root, "runtime", "diag", "r.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["schema"], "tb.lesson_diagnose/1")

    def test_no_files_created_in_empty_state(self):
        before = state_hashes(self.root)
        self.assertEqual(self.run_cli()[0], 0)
        self.assertEqual(state_hashes(self.root), before)
        self.assertEqual(os.listdir(os.path.join(self.root, "state")), [])

    def test_out_only_in_runtime(self):
        for bad in ("state/diag.json", "diag.json", "runtime/../state/x.json", os.path.join(self.root, "config", "x.json"), "runtime"):
            code, _, err = self.run_cli("--out", bad)
            self.assertEqual(code, 2, bad)
            self.assertIn("runtime/", err)
        self.assertEqual(os.listdir(os.path.join(self.root, "state")), [])
        self.assertFalse(os.path.exists(os.path.join(self.root, "diag.json")))
        p = diagnose.out_path(os.path.join(self.root, "runtime", "a.json"), self.root)
        self.assertTrue(p.endswith("a.json"))


if __name__ == "__main__":
    unittest.main()
