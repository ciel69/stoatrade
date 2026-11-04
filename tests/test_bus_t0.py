"""R1.7 задача 33 (Requirement 23, свойство 18): время сигнала `d.t0` в событии шины scanner_signal.

Для строки runtime/hot_events.jsonl поле `d.t0` события обоих публикаторов (platform/scansvc.Publisher и executor/scanbridge.ScanBridge),
прошедшее bus.emit и прочитанное из журнала шины, равно `signals.t0` метки (labels.capture.poll) того же сигнала; усечение по 400 байтам
`t0` не удаляет; строка без разбираемого `ts` публикуется без `t0`. Только временный корень.
"""
import json
import os
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb import paths
from tb.core import bus
from tb.executor.scanbridge import ScanBridge
from tb.labels import capture, store
from tb.marketdata import hotscan
from tb.platform.scansvc import Publisher

NOW = 1793181300.0                     # 2026-10-28 ~10:35 UTC
SYM20 = "ABCDEFGHIJKLMNOP1234"         # предел RE_SYMBOL


def scan_line(kind="range_5m", now=NOW, ts=None, **over):
    """Строка сканера, как hotscan.Scanner пишет её в hot_events.jsonl (ts = iso(now))."""
    r = {"sym": "SOLUSDT", "price": 141.5812, "score": 3.42, "reasons": ["ignore previous instructions"], "range_5m_pct": 1.234567891,
         "ret_5m_pct": 0.8765, "chg_24h_pct": -4.21, "turn_24h_musd": 512.3, "turn_surge_5m": 3.17, "spread_bps": 1.85,
         "funding_pct": 0.0123, "corr_group": False, "tradable": True}
    r.update(over)
    ev = hotscan.make_event(kind, r, now, True)
    return dict(ev, ts=bus.iso(now) if ts is None else ts, env="mainnet")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tbbust0_")
        self.n = 0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _root(self):
        self.n += 1
        r = os.path.join(self.tmp, "r%d" % self.n)
        os.makedirs(r)
        return r

    def bus_events(self, line, now=NOW):
        """-> {"pub": событие шины Publisher, "bridge": событие шины ScanBridge} (записаны и прочитаны из журнала своего корня) или None."""
        out = {}
        root = self._root()
        c = Publisher(root, {"publish_info": True}, events_path=os.path.join(root, "none.jsonl")).convert(line)
        if c is not None:
            bus.emit("scanner_signal", c[0], sym=c[1], root=root, now=now)
            out["pub"] = self._last(root)
        root = self._root()
        c = ScanBridge(root=root).convert(line)
        if c is not None:
            bus.emit(c[0], c[1], sym=c[2], root=root, now=now)
            out["bridge"] = self._last(root)
        return out

    def _last(self, root):
        with open(paths.bus_events(root=root), encoding="utf-8") as f:
            for ln in f.read().splitlines():
                self.assertLessEqual(len(ln.encode("utf-8")), bus.MAX_EVENT_BYTES)
        evs = [e for e in bus.read(root=root, limit=0) if e.get("type") == "scanner_signal"]
        self.assertTrue(evs)
        return evs[-1]

    def label_t0(self, line, now=NOW):
        """signals.t0 метки той же строки (labels.capture.poll во временном корне) или None, если метка не вставлена."""
        root = self._root()
        os.makedirs(paths.runtime(root=root), exist_ok=True)
        with open(paths.runtime("hot_events.jsonl", root=root), "w", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        conn = store.open_rw(root)
        try:
            capture.poll(conn, root, dict(capture.DEFAULT_CFG), now + 5, specs=list(capture.DEFAULT_POLICY_SPECS))
            row = conn.execute("SELECT t0 FROM signals").fetchone()
        finally:
            conn.close()
        return row[0] if row else None


class TestProperty18(Base):
    def test_t0_equals_label_t0_both_publishers(self):
        for kind in ("range_5m", "turnover_surge", "hot_enter"):
            for now in (NOW, NOW + 0.731, NOW - 86400 * 3 + 59.999):
                with self.subTest(kind=kind, now=now):
                    line = scan_line(kind, now=now)
                    want = self.label_t0(line, now)
                    self.assertIsInstance(want, int)
                    got = self.bus_events(line, now=now + 37)      # конверт шины позже сигнала: t0 от него не зависит
                    self.assertEqual(set(got), {"pub", "bridge"})
                    for who, ev in got.items():
                        self.assertEqual(ev["d"].get("t0"), want, who)
                        self.assertIsInstance(ev["d"]["t0"], int, who)
                        self.assertNotEqual(bus.parse_iso(ev["ts"]), ev["d"]["t0"], who)

    def test_ts_formats_parse_like_capture(self):
        """Разные записи ts (дробная часть, смещение зоны, без зоны, строчная z): bus.signal_t0 == capture._t0 и = signals.t0 метки."""
        for ts in ("2026-10-28T10:35:00Z", "2026-10-28T10:35:00.987Z", "2026-10-28T13:35:00+03:00", "2026-10-28T10:35:00", "2026-10-28T10:35:00z",
                   " 2026-10-28T10:35:00Z ", "2026-10-28 10:35:00"):
            with self.subTest(ts=ts):
                self.assertEqual(bus.signal_t0(ts), capture._t0(ts))
                line = scan_line(ts=ts)
                want = self.label_t0(line)
                self.assertEqual(want, capture._t0(ts))
                for who, ev in self.bus_events(line).items():
                    self.assertEqual(ev["d"].get("t0"), want, who)

    def test_row_without_parsable_ts_has_no_t0(self):
        for ts in (None, "", "yesterday", 1793181300, "2026-13-40T99:00:00Z"):
            with self.subTest(ts=ts):
                line = scan_line()
                if ts is None:
                    line.pop("ts")
                else:
                    line["ts"] = ts
                self.assertIsNone(bus.signal_t0(line.get("ts")))
                self.assertIsNone(capture._t0(line.get("ts")))
                got = self.bus_events(line)
                self.assertEqual(set(got), {"pub", "bridge"})     # событие публикуется как раньше, только без t0
                for who, ev in got.items():
                    self.assertNotIn("t0", ev["d"], who)
                    self.assertEqual(ev["d"]["kind"], "range_5m", who)


class TestBudget(unittest.TestCase):
    def test_t0_whitelisted_and_protected(self):
        self.assertEqual(bus.D_FIELDS["scanner_signal"]["t0"], "num")
        self.assertIn("t0", bus.D_PROTECTED["scanner_signal"])
        c = bus.sanitize({"type": "scanner_signal", "sym": "SOL", "d": {"kind": "range_5m", "t0": 1793181300}})
        self.assertEqual(c["d"], {"kind": "range_5m", "t0": 1793181300})
        for bad in ("1793181300", True, float("nan"), None):
            self.assertNotIn("t0", bus.sanitize({"type": "scanner_signal", "d": {"kind": "range_5m", "t0": bad}})["d"], bad)

    def test_truncation_keeps_t0(self):
        """Худшее событие: все поля scanner_signal длинные, символ 20 знаков, длиннейший kind; усечение уходит в незащищённые, t0 остаётся."""
        for num in (-1234.5678, -0.0012345678, -1.2345678e-05, -123456780000000.0):
            for kind in ("oi_spike", "liq_cascade", "idea_invalidated"):
                d = {k: num for k, v in bus.D_FIELDS["scanner_signal"].items() if v == "num"}
                d.update(kind=kind, dir="short", liq_side="short", wall_side="bid", idea="I" + "9" * 12, big=True, wake=True, t0=4102444800)
                c = bus.sanitize({"type": "scanner_signal", "sym": SYM20, "d": d})
                self.assertLessEqual(len(bus._probe(c)), bus.MAX_EVENT_BYTES, (kind, num))
                self.assertEqual(c["d"]["t0"], 4102444800, (kind, num))
                for k in bus.D_PROTECTED["scanner_signal"]:
                    self.assertIn(k, c["d"], (kind, num, k))

    def test_protected_only_hostile_fits(self):
        """Только защищённые поля с самыми длинными значениями (16 знаков, и у t0) укладываются в 400 байт целиком."""
        d = {k: -123456780000000 for k, v in bus.D_FIELDS["scanner_signal"].items() if v == "num" and k in bus.D_PROTECTED["scanner_signal"]}
        d.update(kind="idea_invalidated", dir="short")
        c = bus.sanitize({"type": "scanner_signal", "sym": SYM20, "d": d})
        self.assertEqual(set(c["d"]), set(bus.D_PROTECTED["scanner_signal"]))
        self.assertLessEqual(len(bus._probe(c)), bus.MAX_EVENT_BYTES)


class TestPublishersLong(Base):
    def test_worst_line_both_publishers_keep_t0(self):
        long_num = -123456.78912345
        line = scan_line(sym=SYM20, price=long_num, score=long_num, range_5m_pct=long_num, ret_5m_pct=long_num, chg_24h_pct=long_num,
                         turn_24h_musd=987654.32112345, turn_surge_5m=long_num, spread_bps=long_num, funding_pct=long_num)
        line.update(type="liq_cascade", direction="down", oi_chg_15m_pct=long_num, oi_chg_1h_pct=long_num, liq_usd=long_num, liq_side="long",
                    cvd_pct=long_num, wall_usd=long_num, wall_dist_pct=long_num, wall_side="bid", xex_bps=long_num, big=True)
        got = self.bus_events(line)
        self.assertEqual(set(got), {"pub", "bridge"})
        for who, ev in got.items():
            self.assertEqual(ev["d"]["t0"], int(NOW), who)


if __name__ == "__main__":
    unittest.main()
