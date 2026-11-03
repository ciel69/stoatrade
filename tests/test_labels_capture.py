"""tb.labels.capture (R1.1, задача 7): ключ, разбор события, курсор, неполная строка (свойства 1 и 2), усечение, дубли, фильтры, созревание."""
import json
import os
import random
import shutil
import sqlite3
import tempfile
import time
import unittest

import _bootstrap  # noqa: F401
from tb.labels import capture, store

T0 = 1_793_000_000                                   # 2026-10-26T..Z, кратно 1, фиксированное


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def ev(i=0, sym="DOGE", typ="range_5m", **kw):
    d = {"id": "e%d" % i, "ts": iso(T0 + i), "wake": True, "type": typ, "sym": sym, "price": 0.2, "score": 7.5, "range_5m_pct": 1.2, "ret_5m_pct": 0.9,
         "direction": "up", "spread_bps": 3.0, "turn_surge_5m": 4.0, "corr_group": "meme", "tradable": True}
    d.update(kw)
    return d


def line(d):
    return json.dumps(d, ensure_ascii=False) + "\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbcap_")
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "runtime"))
        self.path = os.path.join(self.root, "runtime", "hot_events.jsonl")
        self.conn = store.open_rw(self.root)
        self.addCleanup(self.conn.close)
        self.cfg = capture.load_cfg(self.root)

    def append(self, text):
        with open(self.path, "ab") as f:
            f.write(text.encode("utf-8"))

    def poll(self, now=T0 + 10):
        return capture.poll(self.conn, self.root, self.cfg, now)

    def keys(self):
        return sorted(r[0] for r in self.conn.execute("SELECT key FROM signals"))

    def row(self, key=None):
        self.conn.row_factory = sqlite3.Row
        try:
            q = "SELECT * FROM signals" + (" WHERE key=?" if key else "")
            return self.conn.execute(q, (key,) if key else ()).fetchone()
        finally:
            self.conn.row_factory = None


class TestParse(unittest.TestCase):
    def test_sig_key(self):
        self.assertEqual(capture.sig_key(1_700_000_000, "BTC", "range_5m"), "S-20231114221320-BTC-range_5m")

    def test_parse_fields(self):
        s = capture.parse_event(line(ev(oi_chg_15m_pct=5.0, liq_usd=1000)))
        self.assertEqual((s["t0"], s["sym"], s["type"], s["side"]), (T0, "DOGE", "range_5m", "long"))
        self.assertEqual(s["key"], capture.sig_key(T0, "DOGE", "range_5m"))
        self.assertEqual((s["score"], s["price"], s["wake"], s["tradable"], s["corr_group"], s["ev_id"]), (7.5, 0.2, 1, 1, "meme", "e0"))
        for k in ("range_5m_pct", "ret_5m_pct", "turn_surge_5m", "spread_bps", "oi_chg_15m_pct", "liq_usd"):
            self.assertIn(k, s["ev"])
        self.assertNotIn("no_spread", s["ev"])

    def test_direction(self):
        self.assertEqual(capture.parse_event(line(ev(direction="down")))["side"], "short")
        for d in (None, "", "flat", 1):
            self.assertIsNone(capture.parse_event(line(ev(direction=d)))["side"])

    def test_tradable(self):
        self.assertEqual(capture.parse_event(line(ev(tradable=False)))["tradable"], 0)
        e = ev()
        del e["tradable"]
        self.assertEqual(capture.parse_event(line(e))["tradable"], 1)          # liqwatch/flowwatch: поля нет -> true

    def test_no_spread_flag(self):
        e = ev(spread_bps=None)
        self.assertTrue(capture.parse_event(line(e))["ev"]["no_spread"])
        del e["spread_bps"]
        self.assertTrue(capture.parse_event(line(e))["ev"]["no_spread"])

    def test_bad_lines(self):
        bad = ["", "{", "[1]", "null", json.dumps({"ts": iso(T0), "type": "x"}), json.dumps({"sym": "A", "type": "x"}), json.dumps({"sym": "A", "ts": iso(T0)}),
               json.dumps({"sym": "A", "type": "x", "ts": "вчера"}), json.dumps({"sym": "A", "type": "x", "ts": 5})]
        for b in bad:
            self.assertIsNone(capture.parse_event(b), b)

    def test_ts_variants(self):
        self.assertEqual(capture.parse_event(line(ev(ts="2026-10-26T12:00:00+00:00")))["t0"], capture.parse_event(line(ev(ts="2026-10-26T12:00:00Z")))["t0"])


class TestMature(unittest.TestCase):
    def test_defaults_and_registry(self):
        cfg = {"grace_min": 10}
        self.assertEqual(capture.mature_ts(1000, cfg, capture.DEFAULT_POLICY_SPECS), 1000 + (270 + 10) * 60)     # P2/P4: 30 + 240
        self.assertEqual(capture.mature_ts(1000, cfg, [(2, "scalp")]), 1000 + (240 + 10) * 60)                   # минимум 240 мин
        self.assertEqual(capture.mature_ts(1000, cfg, [(30, "swing")]), 1000 + (1470 + 10) * 60)

    def test_policy_file(self):
        root = tempfile.mkdtemp(prefix="tbcap_")
        self.addCleanup(shutil.rmtree, root, True)
        self.assertEqual(capture.policy_specs(root), capture.DEFAULT_POLICY_SPECS)
        os.makedirs(os.path.join(root, "config"))
        with open(os.path.join(root, "config", "shadow_policies.json"), "w") as f:
            json.dump({"policies": [{"id": "A", "entry_ttl_min": 20, "max_horizon": "swing", "enabled": True},
                                    {"id": "B", "entry_ttl_min": 99, "max_horizon": "position", "enabled": False}]}, f)
        self.assertEqual(capture.policy_specs(root), [(20.0, "swing")])


class TestPoll(Base):
    def test_basic_capture(self):
        self.append(line(ev(0)) + line(ev(1, sym="SOL", direction=None)))
        r = self.poll()
        self.assertEqual((r["new"], r["bad_lines"]), (2, 0))
        s = self.row(capture.sig_key(T0, "DOGE", "range_5m"))
        self.assertEqual((s["status"], s["side"], s["captured_ts"], s["schema_ver"], s["attempts"]), ("pending", "long", T0 + 10, 1, 0))
        self.assertEqual(s["mature_ts"], T0 + (270 + 10) * 60)
        self.assertEqual(s["next_try_ts"], s["mature_ts"])
        self.assertEqual(json.loads(s["ev"])["range_5m_pct"], 1.2)
        self.assertIsNone(self.row(capture.sig_key(T0 + 1, "SOL", "range_5m"))["side"])        # без направления сохраняется

    def test_incomplete_last_line_waits(self):
        """Свойство 2: строка без \\n не читается и подхватывается целиком позже."""
        full = line(ev(1))
        self.append(line(ev(0)) + full[:25])
        r = self.poll()
        self.assertEqual(r["new"], 1)
        off1 = r["offset"]
        self.assertEqual(off1, len(line(ev(0)).encode("utf-8")))
        self.assertEqual(self.poll()["new"], 0)
        self.append(full[25:])
        r = self.poll()
        self.assertEqual(r["new"], 1)
        self.assertEqual(len(self.keys()), 2)

    def test_incomplete_only_line_bad_not_counted(self):
        self.append('{"sym": "A", "ty')
        r = self.poll()
        self.assertEqual((r["new"], r["bad_lines"], r["offset"]), (0, 0, 0))

    def test_restart_continues(self):
        self.append(line(ev(0)))
        self.poll()
        self.conn.close()
        self.conn = store.open_rw(self.root)
        self.append(line(ev(1)))
        r = self.poll()
        self.assertEqual(r["new"], 1)
        self.assertEqual(len(self.keys()), 2)

    def test_duplicates_ignored(self):
        self.append(line(ev(0)) + line(ev(0, id="other")))
        self.assertEqual(self.poll()["new"], 1)
        self.assertEqual(len(self.keys()), 1)

    def test_bad_lines_counted_and_skipped(self):
        self.append("garbage\n" + line(ev(0)) + '{"sym":"A","type":"range_5m","ts":"x"}\n\n' + line(ev(1)))
        r = self.poll()
        self.assertEqual((r["new"], r["bad_lines"]), (2, 2))

    def test_type_filter(self):
        self.append(line(ev(0, typ="idea_hit")) + line(ev(1, typ="patrol")) + line(ev(2, typ="hot_enter")))
        r = self.poll()
        self.assertEqual((r["new"], r["filtered"]), (1, 2))
        self.assertEqual(self.keys(), [capture.sig_key(T0 + 2, "DOGE", "hot_enter")])
        self.cfg = dict(self.cfg, capture_types=["patrol"])
        self.append(line(ev(3, typ="patrol")))
        self.assertEqual(self.poll()["new"], 1)

    def test_tradable_false_saved(self):
        self.append(line(ev(0, tradable=False)))
        self.poll()
        self.assertEqual(self.row()["tradable"], 0)

    def test_truncation_rereads_from_start(self):
        self.append("".join(line(ev(i)) for i in range(5)))
        self.poll()
        with open(self.path, "wb") as f:                                   # файл ротирован: короче курсора, новое начало
            f.write(line(ev(10)).encode("utf-8"))
        r = self.poll()
        self.assertEqual(r["new"], 1)
        self.assertEqual(len(self.keys()), 6)

    def test_head_change_rereads_from_start(self):
        """Первые 4 КБ изменились при том же или большем размере -> чтение с начала, дубли отсекает ключ."""
        old = "".join(line(ev(i)) for i in range(3))
        self.append(old)
        self.poll()
        new = "".join(line(ev(i + 100)) for i in range(3)) + line(ev(200)) + line(ev(201))
        self.assertGreater(len(new), len(old))
        with open(self.path, "wb") as f:
            f.write(new.encode("utf-8"))
        r = self.poll()
        self.assertEqual(r["new"], 5)
        self.assertEqual(len(self.keys()), 8)

    def test_missing_file_ok(self):
        self.assertEqual(self.poll()["new"], 0)

    def test_growth_beyond_4kb_keeps_cursor(self):
        """Голова хэшируется по min(4 КБ, смещение): рост файла за 4 КБ не сбрасывает курсор."""
        n = 0
        for _ in range(6):
            self.append("".join(line(ev(n + i, sym="C%d" % (n + i))) for i in range(10)))
            n += 10
            r = self.poll()
            self.assertEqual(r["new"], 10)
        self.assertGreater(os.path.getsize(self.path), capture.HEAD_BYTES)
        self.assertEqual(len(self.keys()), 60)

    def test_property_stable_keys(self):
        """Свойство 1: повторное чтение, перезапуск, чтение с начала дают то же множество ключей (случайные нарезки дозаписи)."""
        rnd = random.Random(7)
        lines = [line(ev(i, sym=rnd.choice(["A", "B", "C"]), typ=rnd.choice(["range_5m", "hot_enter", "oi_spike"]))) for i in range(80)]
        lines += lines[:20]                                                # повторы тех же событий
        blob = "".join(lines).encode("utf-8")
        i = 0
        while i < len(blob):
            j = min(len(blob), i + rnd.randint(1, 900))
            with open(self.path, "ab") as f:
                f.write(blob[i:j])
            i = j
            if rnd.random() < 0.3:
                self.poll()
            if rnd.random() < 0.1:
                self.conn.close()
                self.conn = store.open_rw(self.root)
        self.poll()
        got = self.keys()
        self.assertEqual(self.poll()["new"], 0)
        self.conn.execute("DELETE FROM cursor")                            # чтение с начала
        self.assertEqual(self.poll()["new"], 0)
        self.assertEqual(self.keys(), got)
        expected = sorted({capture.parse_event(l)["key"] for l in lines})
        self.assertEqual(got, expected)

    def test_no_secrets_or_usdt_fields(self):
        self.append(line(ev(0, balance_usdt=100, api_key="k")))
        self.poll()
        self.assertNotIn("balance", self.row()["ev"])
        self.assertNotIn("api_key", self.row()["ev"])


if __name__ == "__main__":
    unittest.main()
