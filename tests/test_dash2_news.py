"""Новый дашборд: тема news (читатель, источники, календарь, окно блокировки): белый список, пустые данные, битые файлы, ограничения, действие intel.refresh."""
import json
import os
import shutil
import tempfile
import time
import unittest

import _bootstrap  # noqa: F401
from tb.dash2 import ctlbridge, topics
from tb.control import protocol

NOW = 1_800_000_000.0


def w(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


class NewsTopic(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbdash2news_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def facts(self, **kw):
        d = {"ts": NOW - 5, "sources": {"coindesk": {"ok": True, "age_s": 120, "fails": 0, "v": True}, "theblock": {"ok": False, "age_s": 4000, "fails": 3, "v": False, "token": "SECRET"}},
             "degraded": ["theblock"], "calendar": {"next": [{"id": "cpi", "type": "CPI", "in_min": 125, "impact": 3, "v": True, "path": "C:/x"}], "blackout": {"name": "FOMC", "from": NOW - 600, "to": NOW + 3000, "in_min": -10, "v": False, "src": "watch"}}}
        d.update(kw)
        w(self.root, "runtime/intel/facts.json", json.dumps(d))

    def digest(self, **kw):
        d = {"schema": "tb.digest/1", "ts": NOW - 30, "status": "ok", "stale": False, "reader": {"calls_h": 3, "usd_h": 0.00041234, "usd_day": 0.002, "pending": 4, "ok_n": 120, "fail_n": 2, "susp_n": 3, "err": None, "pause_until": None}, "items": {}}
        d.update(kw)
        w(self.root, "runtime/intel/digest.json", json.dumps(d))

    def test_empty_root(self):
        n = topics.news(self.root, NOW)
        self.assertEqual((n["items"], n["sources"], n["degraded"], n["calendar"]), ([], [], [], {"next": [], "blackout": None}))
        self.assertFalse(n["reader"]["present"])
        self.assertIsNone(n["reader"]["status"])
        self.assertIsNone(n["reader"]["pending"])

    def test_sources_calendar_blackout_whitelist(self):
        self.facts()
        n = topics.news(self.root, NOW)
        self.assertEqual([s["id"] for s in n["sources"]], ["coindesk", "theblock"])
        self.assertEqual(set(n["sources"][1]), {"id", "ok", "degraded", "verified", "fails", "at"})
        self.assertTrue(n["sources"][1]["degraded"])
        self.assertEqual(n["sources"][0]["at"] % 60, 0)                   # момент, а не возраст: тема не меняется каждую секунду
        self.assertEqual(n["degraded"], ["theblock"])
        e = n["calendar"]["next"][0]
        self.assertEqual((e["type"], e["impact"], e["verified"]), ("CPI", 3, True))
        self.assertEqual(set(e), {"id", "type", "impact", "verified", "at"})
        self.assertEqual(n["calendar"]["blackout"], {"name": "FOMC", "from": NOW - 600, "to": NOW + 3000, "verified": False})
        self.assertNotIn("SECRET", json.dumps(n))
        self.assertNotIn("C:/x", json.dumps(n))

    def test_reader_state_and_limits(self):
        w(self.root, "config/intel.json", json.dumps({"reader": {"enabled": True, "provider": "deepseek", "model": "flash", "max_calls_hour": 12, "max_usd_day": 0.05}}))
        self.digest(status="limit", stale=True)
        r = topics.news(self.root, NOW)["reader"]
        self.assertTrue(r["enabled"])
        self.assertEqual((r["status"], r["stale"], r["pending"], r["ok_n"], r["fail_n"], r["susp_n"], r["calls_h"]), ("limit", True, 4, 120, 2, 3, 3))
        self.assertEqual((r["max_calls_h"], r["max_usd_day"], r["usd_h"]), (12, 0.05, 0.0004))
        self.assertEqual(r["profile"], "deepseek.flash")

    def test_unknown_status_and_long_text_are_clipped(self):
        self.digest(status="<script>", reader={"err": "x" * 500 + "\x00\n", "pause_until": "never", "pending": True, "calls_h": 1})
        r = topics.news(self.root, NOW)["reader"]
        self.assertIsNone(r["status"])
        self.assertEqual(len(r["err"]), 80)
        self.assertNotIn("\x00", r["err"])
        self.assertIsNone(r["pause_until"])
        self.assertIsNone(r["pending"])                                     # bool не число

    def test_limits_on_lists_and_text(self):
        srcs = {("s%02d" % i): {"ok": True, "age_s": 1, "v": True} for i in range(60)}
        evs = [{"id": "e%d" % i, "type": "T" * 200, "in_min": i, "impact": 1, "v": True} for i in range(20)]
        self.facts(sources=srcs, degraded=["d%d" % i for i in range(60)], calendar={"next": evs, "blackout": {"name": "N" * 300, "from": 1, "to": 2}})
        n = topics.news(self.root, NOW)
        self.assertEqual((len(n["sources"]), len(n["degraded"]), len(n["calendar"]["next"])), (24, 24, 6))
        self.assertEqual(len(n["calendar"]["next"][0]["type"]), 40)
        self.assertEqual(len(n["calendar"]["blackout"]["name"]), 60)

    def test_broken_files(self):
        w(self.root, "runtime/intel/facts.json", "{broken")
        w(self.root, "runtime/intel/digest.json", "[1, 2")
        w(self.root, "config/intel.json", "{{{")
        n = topics.news(self.root, NOW)
        self.assertEqual(n["sources"], [])
        self.assertFalse(n["reader"]["present"])
        json.dumps(n)

    def test_odd_shapes(self):
        self.facts(sources={"a": "x", "b": None}, calendar={"next": ["x", None, 5], "blackout": "yes"}, degraded="oops")
        n = topics.news(self.root, NOW)
        self.assertEqual((n["sources"], n["calendar"]["next"], n["calendar"]["blackout"]), ([], [], None))

    def test_refresh_action_schema_and_bridge(self):
        self.assertIn("intel.refresh", ctlbridge.FULL_ACTIONS)
        self.assertEqual(ctlbridge.FULL_KEYS["intel.refresh"], ("source",))
        self.assertIn("intel.refresh", protocol.NO_NONCE_ACTIONS)              # безрисковое: без подтверждения
        ok, _, a = protocol.validate_args("intel.refresh", {})
        self.assertTrue(ok)
        self.assertEqual(a["source"], "all")
        self.assertTrue(protocol.validate_args("intel.refresh", {"source": "coindesk"})[0])
        self.assertFalse(protocol.validate_args("intel.refresh", {"source": "../etc"})[0])
        self.assertFalse(protocol.validate_args("intel.refresh", {"source": 5})[0])


if __name__ == "__main__":
    unittest.main()
