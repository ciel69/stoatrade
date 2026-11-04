"""R1.7, задача 16: читатель сводного рынка tb.core.cmktdoc (Requirements 3.3, 14.1, 15.4, 16.1–16.4, 21.2, 21.3; свойства 1, 15).

Все файлы — во временном корне; ряды пишет настоящий писатель tb.cmkt.store.Store (тест может импортировать tb.cmkt, читатель — нет).
"""
import ast
import json
import math
import os
import random
import re
import shutil
import statistics
import tempfile
import time
import unittest

import _bootstrap  # noqa: F401
from tb import paths
from tb.cmkt import conf as C
from tb.cmkt import store as S
from tb.core import cmktdoc as D
from tb.logic import cmkt as L

DAY0 = 1793750400            # 2026-11-04 00:00:00 UTC
DAY1 = DAY0 + 86400
PERF = float(os.environ.get("TB_PERF_FACTOR") or 1.0)
RE_ID = re.compile(r"^[A-Za-z0-9_]{1,24}$")


def feats(rng, none_share=0.25):
    out = {}
    for n in L.CM_FEATURES:
        if rng.random() < none_share:
            out[n] = None
        elif L.SCALES[n] == 1:
            out[n] = rng.randint(0, 9)
        else:
            out[n] = round(rng.uniform(-50, 50), 3)
    return out


def full_feats():
    return dict((n, (7 if L.SCALES[n] == 1 else -123.456789)) for n in L.CM_FEATURES)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbcmktdoc_")
        self.cfg = C.defaults()
        self.store = S.Store(self.root, self.cfg, clock=lambda: 0.0, disk_free=lambda p: 10 ** 13)
        self.rng = random.Random(16)
        D._cfg_cache.clear()

    def tearDown(self):
        self.store.close()
        D._clock, D._sleep = time.time, time.sleep
        shutil.rmtree(self.root, ignore_errors=True)

    def series(self, t_from, t_to, deep=("BTC",), light=("SOL",), lag=0.4, skip=()):
        """Строки рядов deep каждую секунду и light при ts % 5 = 0, записанные через lag секунд после ts."""
        for ts in range(t_from, t_to + 1):
            rows = [{"sym": s, "tier": "deep", "f": feats(self.rng), "focus_s": 600 + ts - t_from, "flags": []}
                    for s in deep if (s, ts) not in skip]
            if ts % 5 == 0:
                rows += [{"sym": s, "tier": "light", "f": feats(self.rng), "focus_s": 900, "flags": []} for s in light if (s, ts) not in skip]
            self.store.write(ts, rows, wr_ts=ts + lag)


# ---------------------------------------------------------------- features_at
class FeaturesAt(Base):
    def test_property_1_row_strictly_before_t0(self):
        """Свойство 1: выбранная строка — floor(t0/step)×step − step < t0 своего яруса; v = cm_ver, без None, ≤ 1000 байт."""
        t_a = DAY0 + 1000
        self.series(t_a, t_a + 120)
        for _ in range(300):
            t0 = t_a + 12 + self.rng.random() * 100
            if self.rng.random() < 0.5:
                t0 = float(int(t0))
            for sym, tier, step in (("BTC", "deep", 1), ("SOL", "light", 5)):
                r = D.features_at(self.root, sym, t0, now=t0 + 60)
                self.assertIn("cm", r, (sym, t0, r))
                cm = r["cm"]
                self.assertEqual(cm["row_ts"], int(math.floor(t0 / step)) * step - step)
                self.assertLess(cm["row_ts"], t0)
                self.assertEqual(cm["tier"], tier)
                self.assertEqual(cm["v"], L.CM_VER)
                self.assertFalse([k for k, v in cm.items() if v is None])
                self.assertLessEqual(len(json.dumps(cm, separators=(",", ":")).encode("utf-8")), 1000)

    def test_values_equal_quantized_row(self):
        t_a = DAY0 + 500
        f = dict((n, (3 if L.SCALES[n] == 1 else 1.5)) for n in L.CM_FEATURES)        # укладывается в 1000 байт без отбрасывания
        f["cm_prem_cb_bps"] = None
        self.store.write(t_a, [{"sym": "ETH", "tier": "deep", "f": f, "focus_s": 77, "flags": []}], wr_ts=t_a + 1)
        cm = D.features_at(self.root, "ETH", t_a + 1, now=t_a + 30)["cm"]
        q = L.quantized(f)
        for n in L.CM_FEATURES:
            if q[n] is None:
                self.assertNotIn(n, cm)
            else:
                self.assertEqual(cm[n], q[n], n)
        self.assertEqual((cm["focus_s"], cm["row_ts"], cm["tier"]), (77, t_a, "deep"))
        # худший случай (все 40 признаков длинными числами): ≤ 1000 байт, первая волна на месте
        self.store.write(t_a + 1, [{"sym": "ETH", "tier": "deep", "f": full_feats(), "focus_s": 78, "flags": []}], wr_ts=t_a + 2)
        cm = D.features_at(self.root, "ETH", t_a + 2, now=t_a + 30)["cm"]
        self.assertLessEqual(len(json.dumps(cm, separators=(",", ":")).encode("utf-8")), 1000)
        for n in L.FIRST_WAVE:
            self.assertIn(n, cm)

    def test_light_coin_when_t0_minus_1_is_multiple_of_5(self):
        """Монета light при (t0 − 1) % 5 = 0: строка light на t0 − 1 есть, но берётся floor(t0/5)×5 − 5."""
        t_a = DAY0 + 2000
        self.series(t_a, t_a + 30)
        t0 = t_a + 21
        self.assertEqual((t0 - 1) % 5, 0)
        self.assertIsNotNone(S.read_row(self.root, "SOL", t0 - 1, self.cfg))
        cm = D.features_at(self.root, "SOL", t0, now=t0 + 60)["cm"]
        self.assertEqual((cm["row_ts"], cm["tier"]), (t0 - 6, "light"))

    def test_midnight_reads_previous_day_file(self):
        self.series(DAY1 - 20, DAY1 + 20)
        self.assertTrue(os.path.isfile(S.day_path(self.root, "20261104", self.cfg)))
        self.assertTrue(os.path.isfile(S.day_path(self.root, "20261105", self.cfg)))
        for t0, sym, rts in ((DAY1, "BTC", DAY1 - 1), (DAY1 + 0.7, "BTC", DAY1 - 1), (DAY1 + 1, "BTC", DAY1),
                             (DAY1 + 3, "SOL", DAY1 - 5), (DAY1 + 5, "SOL", DAY1)):
            cm = D.features_at(self.root, sym, t0, now=DAY1 + 100)["cm"]
            self.assertEqual(cm["row_ts"], rts, (t0, sym))

    def test_midnight_pending_when_new_day_file_absent(self):
        """Строка 00:00:00 ещё не записана (файла новых суток нет), строка 23:59:59 есть — ждём, а не db."""
        self.series(DAY1 - 10, DAY1 - 1)
        r = D.features_at(self.root, "BTC", DAY1 + 1, now=DAY1 + 1.2)
        self.assertEqual(r, {"pending": True, "row_ts": DAY1, "until": DAY1 + 4})
        self.assertEqual(D.features_at(self.root, "BTC", DAY1 + 1, now=DAY1 + 4), {"cm_missing": "no_row"})

    def test_deleted_file_is_db(self):
        t_a = DAY0 + 3000
        self.series(t_a, t_a + 10)
        self.assertIn("cm", D.features_at(self.root, "BTC", t_a + 5, now=t_a + 60))
        self.store.close()
        for suf in ("", "-wal", "-shm"):
            p = S.day_path(self.root, "20261104", self.cfg) + suf
            if os.path.exists(p):
                os.remove(p)
        self.assertEqual(D.features_at(self.root, "BTC", t_a + 5, now=t_a + 60), {"cm_missing": "db"})

    def test_corrupt_file_and_bad_args_never_raise(self):
        p = S.day_path(self.root, "20261104", self.cfg)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"not a database" * 100)
        self.assertEqual(D.features_at(self.root, "BTC", DAY0 + 100, now=DAY0 + 200), {"cm_missing": "db"})
        self.assertEqual(D.features_at(self.root, "BTC", None), {"cm_missing": "no_t0"})
        self.assertEqual(D.features_at(self.root, "BTC", "x"), {"cm_missing": "no_t0"})
        self.assertEqual(D.features_at(self.root, "BTC", float("nan")), {"cm_missing": "no_t0"})
        self.assertIn("cm_missing", D.features_at(self.root, None, DAY0 + 100, now=DAY0 + 200))
        self.assertIn("cm_missing", D.features_at(os.path.join(self.root, "nope"), "BTC", DAY0 + 100))

    def test_row_written_after_accept_s_is_late(self):
        t_a = DAY0 + 4000
        self.series(t_a, t_a + 3)
        self.store.write(t_a + 4, [{"sym": "BTC", "tier": "deep", "f": feats(self.rng), "focus_s": 1, "flags": []}], wr_ts=t_a + 4 + 3.5)
        self.store.write(t_a + 5, [{"sym": "BTC", "tier": "deep", "f": feats(self.rng), "focus_s": 1, "flags": []}], wr_ts=t_a + 5 + 3.0)
        self.assertEqual(D.features_at(self.root, "BTC", t_a + 5, now=t_a + 60), {"cm_missing": "late"})
        self.assertIn("cm", D.features_at(self.root, "BTC", t_a + 6, now=t_a + 60))     # ровно accept_s — вовремя
        # accept_s из config/cmkt.json
        os.makedirs(paths.config(root=self.root), exist_ok=True)
        with open(paths.config("cmkt.json", root=self.root), "w", encoding="utf-8") as f:
            json.dump({"sync": {"accept_s": 5}}, f)
        self.assertIn("cm", D.features_at(self.root, "BTC", t_a + 5, now=t_a + 60))

    def test_pending_no_row_not_focus(self):
        t_a = DAY0 + 5000
        self.series(t_a, t_a + 9, deep=("BTC",), light=("SOL",))
        t0 = t_a + 11                                         # строка t_a + 10 ещё не записана, t_a + 9 есть
        self.assertEqual(D.features_at(self.root, "BTC", t0, now=t0 + 0.1), {"pending": True, "row_ts": t_a + 10, "until": t_a + 14})
        self.assertEqual(D.features_at(self.root, "BTC", t0, now=t_a + 14), {"cm_missing": "no_row"})
        self.assertEqual(D.features_at(self.root, "DOGE", t0, now=t0 + 0.1), {"cm_missing": "not_focus"})
        # light: строки t_a + 10 нет, t_a + 5 есть; срок t_a + 14 раньше t0 — на t0 ответ окончательный
        t0l = t_a + 15
        self.assertEqual(D.features_at(self.root, "SOL", t0l, now=t0l), {"cm_missing": "no_row"})
        self.assertEqual(D.features_at(self.root, "SOL", t0l, now=t_a + 13.9), {"pending": True, "row_ts": t_a + 10, "until": t_a + 14})

    def test_wait_until_row_appears(self):
        t_a = DAY0 + 6000
        self.series(t_a, t_a + 9)
        t0 = t_a + 11
        clock = [t0 + 0.05]
        calls = []

        def sleep(dt):
            calls.append(dt)
            self.assertLessEqual(dt, D.WAIT_POLL_S + 1e-9)
            clock[0] += dt
            if len(calls) == 3:
                self.store.write(t_a + 10, [{"sym": "BTC", "tier": "deep", "f": feats(self.rng), "focus_s": 5, "flags": []}],
                                 wr_ts=clock[0])
        D._clock, D._sleep = (lambda: clock[0]), sleep
        r = D.features_at(self.root, "BTC", t0, wait=True)
        self.assertEqual(r["cm"]["row_ts"], t_a + 10)
        self.assertEqual(len(calls), 3)

    def test_wait_gives_final_answer_at_deadline(self):
        t_a = DAY0 + 7000
        self.series(t_a, t_a + 9)
        t0 = t_a + 11
        clock = [t0 + 0.05]

        def sleep(dt):
            clock[0] += dt
        D._clock, D._sleep = (lambda: clock[0]), sleep
        self.assertEqual(D.features_at(self.root, "BTC", t0, wait=True), {"cm_missing": "no_row"})
        self.assertGreaterEqual(clock[0], t_a + 14 - 1e-9)
        self.assertLess(clock[0], t_a + 14 + 0.3)

    def test_perf_under_5ms(self):
        t_a = DAY0 + 10000
        syms = ["S%02d" % i for i in range(40)]
        self.series(t_a, t_a + 600, deep=syms[:20], light=syms[20:])
        self.store.close()
        times = []
        for i in range(60):
            t0 = t_a + 30 + i * 7 + 0.4
            sym = syms[i % 40]
            t = time.perf_counter()
            r = D.features_at(self.root, sym, t0, now=t0 + 60)
            times.append((time.perf_counter() - t) * 1000.0)
            self.assertIn("cm", r)
        self.assertLessEqual(statistics.median(times), 5.0 * PERF, times)


# ---------------------------------------------------------------- снимок, досье и инструмент
MKTS = list(L.MARKETS)


def snap_row(tier="deep", focus_s=4000, flags=(), n_walls=3, f=None):
    f = full_feats() if f is None else f
    vals = [L.quantized(f)[n] for n in L.CM_FEATURES]
    mk = {}
    for i, m in enumerate(MKTS):
        perp = m.endswith("perp")
        mk[m] = [None if i != 3 else "STALE", 120 + i, 65000.12345678, 2.5e9 if perp else None, 0.01234 if perp else None,
                 -123000, 12.3456 if perp else None, 15000 if perp else None, 32000 if perp else None, 2]
    band = [123456789, 98765432, 11.11, 9, 0.91]
    bands = dict((k, [band, band, band, band]) for k in ("perp", "spot", "all"))
    walls = []
    for side in ("b", "a"):
        for j in range(n_walls):
            walls.append([side, 64000.5 + j, 2500000 + j * 1000, 0.42 + j, 3, ["binance_perp", "okx_perp"], 120 + j])
    cvd = dict((lab, dict((k, [12.34, -1234567, 8, 0.95]) for k in ("perp", "spot", "all"))) for lab, _ in L.WINDOWS)
    liq = [15000, 32000, 450000, 210000, 0.8, 0, 1, 5]
    return [tier, focus_s, vals, mk, bands, walls, cvd, liq, list(flags), 10, 0.88, 65000.1, 900]


def write_snap(root, ts, syms):
    doc = {"schema": L.SNAP_SCHEMA, "ts": ts, "t_wr": ts + 0.4, "skew_ms": 900, "clock_back": 0, "gap_s": 0, "spot_quote": "USDT",
           "cm_ver": L.CM_VER, "cols": list(L.CM_FEATURES), "mkts": MKTS, "n_ex": {"perp": 5, "spot": 5}, "usdt_usd": 1.0,
           "ex": dict((m, [None, 100, 5, 700]) for m in MKTS), "warn": [], "sym_cols": list(L.SNAP_SYM_COLS),
           "mk_cols": list(L.SNAP_MK_COLS), "syms": syms}
    p = paths.runtime("cmkt", "snap.json", root=root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return doc


def leaves_ok(test, obj, path="$"):
    """Свойство 15: только числа, перечисления и идентификаторы (строки из букв, цифр и _), ключи — идентификаторы."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            test.assertTrue(isinstance(k, str) and RE_ID.match(k), (path, k))
            test.assertIsNotNone(v, (path, k))
            leaves_ok(test, v, path + "." + k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            leaves_ok(test, v, "%s[%d]" % (path, i))
    elif isinstance(obj, str):
        test.assertTrue(RE_ID.match(obj), (path, obj))
    else:
        test.assertTrue(isinstance(obj, (int, float)) and not isinstance(obj, bool) and math.isfinite(obj), (path, obj))


def tokens(obj):
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":"))) / 4.0


class Snapshot(Base):
    def test_latest_age(self):
        write_snap(self.root, DAY0 + 100, {"BTC": snap_row()})
        self.assertEqual(D.latest(self.root, DAY0 + 104)["ts"], DAY0 + 100)
        self.assertIsNone(D.latest(self.root, DAY0 + 106))
        self.assertIsNotNone(D.latest(self.root, DAY0 + 1000, max_age_s=None))
        self.assertIsNone(D.latest(os.path.join(self.root, "nope"), DAY0))

    def cfg_brief(self, **kw):
        c = C.defaults()
        c["brief"].update(kw)
        return c

    def test_brief_compact_default_budget(self):
        write_snap(self.root, DAY0 + 100, {"BTC": snap_row(flags=["warmup", "clock_back"]), "ETH": snap_row(), "SOL": snap_row()})
        blk = D.brief_block(self.root, "SOL", DAY0 + 101, self.cfg_brief())
        self.assertLessEqual(tokens(blk), 90)
        leaves_ok(self, blk)
        sol = blk["syms"]["SOL"]
        for k in ("imb_025", "cvd_ps5", "oi5", "liq5_net", "fund_w", "prem_ps", "pull15", "n_ex", "cov", "lead", "flags"):
            self.assertIn(k, sol)
        self.assertNotIn("imb_05", sol)
        self.assertNotIn("pulled15_net", sol)
        self.assertNotIn("bands", sol)
        self.assertEqual(sol["lead"], MKTS[7])
        self.assertEqual(sol["flags"], ["stale_ex"])
        blk = D.brief_block(self.root, "BTC", DAY0 + 101, self.cfg_brief())
        self.assertEqual(blk["syms"]["BTC"]["flags"], ["stale_ex", "warmup"])

    def test_brief_none_is_absent_key(self):
        f = full_feats()
        f["cm_prem_ps_bps"] = None
        f["cm_cvd_ps_5m_pct"] = None
        f["cm_wall_pull_15m_pct"] = None
        write_snap(self.root, DAY0 + 100, {"SOL": snap_row(f=f)})
        sol = D.brief_block(self.root, "SOL", DAY0 + 101, self.cfg_brief())["syms"]["SOL"]
        self.assertNotIn("prem_ps", sol)
        self.assertNotIn("cvd_ps5", sol)
        self.assertNotIn("pull15", sol)
        leaves_ok(self, sol)

    def test_brief_full_at_600_tokens(self):
        write_snap(self.root, DAY0 + 100, {"BTC": snap_row(), "ETH": snap_row(), "SOL": snap_row()})
        blk = D.brief_block(self.root, "SOL", DAY0 + 101, self.cfg_brief(mode="full", max_tokens=600))
        self.assertLessEqual(tokens(blk), 600)
        leaves_ok(self, blk)
        sol = blk["syms"]["SOL"]
        for k in ("cvd5", "cvd15", "oi15", "oi_musd", "liq5", "liq1h", "prem_cb", "bands", "walls"):
            self.assertIn(k, sol, k)
        self.assertEqual(sorted(sol["bands"]), ["05", "1", "2"])
        self.assertEqual([len(sol["walls"][s]) for s in ("bid", "ask")], [2, 2])
        # три монеты full в 600 токенов: лишние монеты отбрасываются раньше полей первой
        blk3 = D.brief_block(self.root, "SOL", DAY0 + 101, self.cfg_brief(mode="full", max_tokens=600, syms=3))
        self.assertLessEqual(tokens(blk3), 600)
        self.assertEqual(list(blk3["syms"])[0], "SOL")
        if len(blk3["syms"]) < 3:
            self.assertEqual(blk3["syms"]["SOL"], sol)

    def test_brief_truncation_order(self):
        write_snap(self.root, DAY0 + 100, {"BTC": snap_row(), "ETH": snap_row(), "SOL": snap_row()})
        one = D.brief_block(self.root, "SOL", DAY0 + 101, self.cfg_brief(syms=1, max_tokens=600))["syms"]["SOL"]
        blk = D.brief_block(self.root, "SOL", DAY0 + 101, self.cfg_brief(syms=3, max_tokens=90))
        self.assertLessEqual(tokens(blk), 90)
        self.assertIn(list(blk["syms"]), (["SOL"], ["SOL", "BTC"]))  # монеты уходят первыми (с конца)
        self.assertEqual(blk["syms"]["SOL"], one)                     # поля первой монеты целы, пока есть лишние монеты
        blk = D.brief_block(self.root, "SOL", DAY0 + 101, self.cfg_brief(syms=3, max_tokens=600))
        self.assertEqual(list(blk["syms"]), ["SOL", "BTC", "ETH"])
        blk = D.brief_block(self.root, "BTC", DAY0 + 101, self.cfg_brief(syms=2, max_tokens=600))
        self.assertEqual(list(blk["syms"]), ["BTC", "ETH"])
        blk = D.brief_block(self.root, "SOL", DAY0 + 101, self.cfg_brief(mode="full", max_tokens=40))
        self.assertLessEqual(tokens(blk), 40)
        sol = blk["syms"]["SOL"]
        self.assertIn("imb_025", sol)                                # поля уходят с конца списка вида
        self.assertNotIn("walls", sol)

    def test_brief_stale_missing_not_focus(self):
        self.assertIsNone(D.brief_block(self.root, "SOL", DAY0 + 101, self.cfg_brief()))
        write_snap(self.root, DAY0 + 100, {"BTC": snap_row()})
        self.assertEqual(D.brief_block(self.root, "SOL", DAY0 + 112, self.cfg_brief()), {"stale_s": 12})
        self.assertEqual(D.brief_block(self.root, "SOL", DAY0 + 101, self.cfg_brief())["syms"], {"SOL": {"warn": "NOT_FOCUS"}})

    def test_detail_parts_bytes_and_trunc(self):
        write_snap(self.root, DAY0 + 100, {"BTC": snap_row(flags=["low_cov"])})
        c = C.defaults()
        r = D.detail(self.root, "BTC", None, DAY0 + 102, dict(c, tool={"max_bytes": 8192, "per_hour": 30}))
        for p in D.PARTS:
            self.assertIn(p, r, p)
        self.assertNotIn("trunc", r)
        self.assertEqual(r["warn"], ["LOW_COV"])
        self.assertEqual((r["sym"], r["ts"], r["age_s"], r["n_ex"]), ("BTC", DAY0 + 100, 2, 10))
        self.assertEqual(len(r["book"]["walls"]["bid"]), 3)
        self.assertEqual(r["venues"][MKTS[3]]["code"], "STALE")
        leaves_ok(self, r)
        full = len(json.dumps(r, separators=(",", ":")).encode("utf-8"))
        for mb in (512, 1024, 2048):
            c2 = dict(c, tool={"max_bytes": mb, "per_hour": 30})
            r2 = D.detail(self.root, "BTC", None, DAY0 + 102, c2)
            self.assertLessEqual(len(json.dumps(r2, ensure_ascii=False, separators=(",", ":")).encode("utf-8")), mb)
            leaves_ok(self, r2)
            if full > mb:
                tr = r2["trunc"]
                self.assertEqual(tr, list(D.TRUNC_ORDER[:len(tr)]))
                for p in tr:
                    self.assertNotIn(p, r2)
        r3 = D.detail(self.root, "BTC", ["oi", "cvd"], DAY0 + 102, c)
        self.assertEqual(sorted(p for p in D.PARTS if p in r3), ["cvd", "oi"])

    def test_detail_warns(self):
        self.assertEqual(D.detail(self.root, "BTC", None, DAY0, C.defaults()), {"sym": "BTC", "warn": ["STALE"]})
        write_snap(self.root, DAY0 + 100, {"BTC": snap_row()})
        r = D.detail(self.root, "DOGE", None, DAY0 + 101, C.defaults())
        self.assertEqual(r["warn"], ["NOT_FOCUS"])
        self.assertFalse([p for p in D.PARTS if p in r])
        r = D.detail(self.root, "BTC", None, DAY0 + 110, C.defaults())
        self.assertEqual(r["warn"], ["STALE"])
        self.assertFalse([p for p in D.PARTS if p in r])

    def test_reads_config_file_when_cfg_none(self):
        write_snap(self.root, DAY0 + 100, {"SOL": snap_row()})
        os.makedirs(paths.config(root=self.root), exist_ok=True)
        with open(paths.config("cmkt.json", root=self.root), "w", encoding="utf-8") as f:
            json.dump({"brief": {"mode": "full", "max_tokens": 600, "syms": 1}}, f)
        self.assertIn("bands", D.brief_block(self.root, "SOL", DAY0 + 101)["syms"]["SOL"])


# ---------------------------------------------------------------- слой
class Layer(unittest.TestCase):
    def test_no_tb_cmkt_import_transitive(self):
        """Requirement 3.3: tb.core.cmktdoc не импортирует tb.cmkt ни прямо, ни через внутренние импорты tb."""
        src_root = os.path.dirname(os.path.dirname(D.__file__))

        def deps(mod):
            parts = mod.split(".")
            p = os.path.join(os.path.dirname(src_root), *parts)
            path = p + ".py" if os.path.isfile(p + ".py") else os.path.join(p, "__init__.py")
            if not os.path.isfile(path):
                return set()
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            out = set()
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    out.update(a.name for a in n.names)
                elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
                    out.add(n.module)
                    out.update(n.module + "." + a.name for a in n.names)
            return set(x for x in out if x == "tb" or x.startswith("tb."))
        seen, stack = set(), ["tb.core.cmktdoc"]
        while stack:
            m = stack.pop()
            if m in seen:
                continue
            seen.add(m)
            stack.extend(deps(m) - seen)
        self.assertFalse([m for m in seen if m == "tb.cmkt" or m.startswith("tb.cmkt.")], seen)


if __name__ == "__main__":
    unittest.main()
