"""R1.7 задача 18 (Requirement 14): признаки сводного рынка в метках — ev.cm | ev.cm_missing при захвате, отложенная вставка при pending
(курсор, порядок, повтор), монета без строки предыдущего шага — без ожидания, backfill без перезаписи, покрытие, размер ev.cm, wall_*.
Ряды пишет tb.cmkt.store на временном корне; сеть и реальные state/runtime не используются."""
import contextlib
import io
import json
import os
import time
import unittest

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.cmkt import conf as C
from tb.cmkt import store as CS
from tb.core import cmktdoc as D
from tb.labels import capture, cli, service, store
from tb.logic import cmkt as L
from tb.platform import services as PS
from tb.platform import status as PST

DAY0 = 1793750400            # 2026-11-04 00:00:00 UTC
PERF = float(os.environ.get("TB_PERF_FACTOR") or 1.0)


def feats(k=1.5):
    return dict((n, (3 if L.SCALES[n] == 1 else k)) for n in L.CM_FEATURES)


def full_feats():
    return dict((n, (7 if L.SCALES[n] == 1 else -123.456789)) for n in L.CM_FEATURES)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        self.conn = store.open_rw(self.root)
        self.addCleanup(self.conn.close)
        self.cfg = capture.load_cfg(self.root)
        self.cstore = CS.Store(self.root, C.defaults(), clock=lambda: 0.0, disk_free=lambda p: 10 ** 13)
        self.addCleanup(self.cstore.close)
        D._cfg_cache.clear()
        self.path = os.path.join(self.root, "runtime", "hot_events.jsonl")

    def series(self, t_from, t_to, syms=("BTC",), lag=0.4, focus_s=600, f=None):
        for ts in range(t_from, t_to + 1):
            self.cstore.write(ts, [{"sym": s, "tier": "deep", "f": f or feats(), "focus_s": focus_s, "flags": []} for s in syms], wr_ts=ts + lag)

    def events(self, *evs):
        H.write_events(self.root, evs)

    def poll(self, now):
        return capture.poll(self.conn, self.root, self.cfg, now)

    def evs(self):
        return dict((k, json.loads(e)) for k, e in self.conn.execute("SELECT key, ev FROM signals"))

    def ev_of(self, sym):
        out = [json.loads(e) for (e,) in self.conn.execute("SELECT ev FROM signals WHERE sym=?", (sym,))]
        self.assertEqual(len(out), 1, sym)
        return out[0]

    def cursor(self):
        r = self.conn.execute("SELECT offset FROM cursor WHERE src=?", (capture.SRC,)).fetchone()
        return r[0] if r else None


def ev(i, t, sym, **kw):
    return H.hot_event(i, t - i, sym=sym, **kw)          # hot_event прибавляет i к t: t0 ровно t


class Capture(Base):
    def test_with_row(self):
        t0 = DAY0 + 1000
        self.series(t0 - 20, t0 - 1)
        self.events(ev(0, t0, "BTC"))
        self.assertEqual(self.poll(t0 + 30)["new"], 1)
        e = self.ev_of("BTC")
        cm = e["cm"]
        self.assertNotIn("cm_missing", e)
        self.assertEqual((cm["v"], cm["tier"], cm["focus_s"], cm["row_ts"]), (L.CM_VER, "deep", 600, t0 - 1))
        self.assertFalse([k for k, v in cm.items() if v is None])
        for n in L.CM_FEATURES:
            self.assertIn(n, cm)
        self.assertEqual(e["range_5m_pct"], 1.2)                                       # поля R1 на месте

    def test_usdt_suffix_and_size_limit(self):
        t0 = DAY0 + 1100
        self.series(t0 - 5, t0 - 1, f=full_feats())
        self.events(ev(0, t0, "BTCUSDT"))
        self.poll(t0 + 30)
        cm = self.ev_of("BTCUSDT")["cm"]
        self.assertLessEqual(len(json.dumps(cm, separators=(",", ":"), ensure_ascii=False).encode("utf-8")), L.EV_CM_MAX_BYTES)
        for n in L.FIRST_WAVE:
            self.assertIn(n, cm)

    def test_without_row(self):
        t0 = DAY0 + 1200
        self.events(ev(0, t0, "BTC"))
        self.poll(t0 + 30)                                                              # рядов нет вовсе
        self.assertEqual(self.ev_of("BTC")["cm_missing"], "db")
        self.series(t0 - 5, t0 + 1)
        self.events(ev(1, t0 + 1, "DOGE"))                                              # монеты нет в рядах
        self.poll(t0 + 40)
        e = self.ev_of("DOGE")
        self.assertEqual(e["cm_missing"], "not_focus")
        self.assertNotIn("cm", e)

    def test_late(self):
        t0 = DAY0 + 1300
        self.series(t0 - 5, t0 - 2)
        self.cstore.write(t0 - 1, [{"sym": "BTC", "tier": "deep", "f": feats(), "focus_s": 600, "flags": []}], wr_ts=t0 - 1 + 3.5)
        self.events(ev(0, t0, "BTC"))
        self.poll(t0 + 30)
        self.assertEqual(self.ev_of("BTC")["cm_missing"], "late")

    def test_wall_fields(self):
        t0 = DAY0 + 1400
        self.events(ev(0, t0, "BTC", typ="book_wall", wall_usd=750000.0, wall_dist_pct=0.42, wall_side="bid"),
                    ev(1, t0 + 1, "ETH", typ="book_wall", wall_usd=1e6, wall_side="sideways"))
        self.poll(t0 + 30)
        e = self.ev_of("BTC")
        self.assertEqual((e["wall_usd"], e["wall_dist_pct"], e["wall_side"]), (750000.0, 0.42, "bid"))
        self.assertNotIn("wall_side", self.ev_of("ETH"))                               # не из перечисления — не пишется
        self.assertIn("wall_usd", capture.EV_NUM_FIELDS)
        self.assertIn("wall_dist_pct", capture.EV_NUM_FIELDS)


class Pending(Base):
    def test_deferred_insert_cursor_order_and_retry(self):
        t0 = DAY0 + 2000
        self.series(t0 - 10, t0 - 2)                                                   # строка t0-1 ещё не записана, t0-2 есть
        self.events(ev(0, t0 - 5, "ETH"))                                              # до отложенного: вставляется
        before = os.path.getsize(self.path)
        self.events(ev(1, t0, "BTC"), ev(2, t0, "DOGE"), {"bad": 1})                  # BTC ждёт строку; DOGE и битая строка — хвост
        r = self.poll(t0 + 0.5)
        self.assertEqual(r["new"], 1)
        self.assertEqual(r["bad_lines"], 0)                                            # хвост за отложенной строкой не разбирался
        self.assertEqual(r["offset"], before)
        self.assertEqual(self.cursor(), before)                                        # курсор перед первой отложенной строкой
        self.assertEqual([s for (s,) in self.conn.execute("SELECT sym FROM signals")], ["ETH"])
        self.assertEqual(self.ev_of("ETH")["cm_missing"], "not_focus")
        # второй проход до срока — снова ждёт, ничего не вставлено
        r = self.poll(t0 + 1)
        self.assertEqual((r["new"], self.cursor()), (0, before))
        # строка появилась: следующий проход вставляет отложенный сигнал и хвост по порядку
        self.cstore.write(t0 - 1, [{"sym": "BTC", "tier": "deep", "f": feats(2.5), "focus_s": 700, "flags": []}], wr_ts=t0 - 1 + 1.5)
        r = self.poll(t0 + 2)
        self.assertEqual((r["new"], r["bad_lines"]), (2, 1))
        self.assertEqual(self.cursor(), os.path.getsize(self.path))
        btc = self.ev_of("BTC")["cm"]
        self.assertEqual((btc["row_ts"], btc["focus_s"]), (t0 - 1, 700))
        self.assertEqual(self.ev_of("DOGE")["cm_missing"], "not_focus")
        caps = dict(self.conn.execute("SELECT sym, captured_ts FROM signals"))
        self.assertEqual(caps["ETH"], int(t0 + 0.5))
        self.assertEqual(caps["BTC"], int(t0 + 2))

    def test_deadline_gives_no_row(self):
        t0 = DAY0 + 2100
        self.series(t0 - 10, t0 - 2)
        self.events(ev(0, t0, "BTC"))
        self.assertEqual(self.poll(t0 + 1)["new"], 0)
        self.assertEqual(self.poll(t0 - 1 + 3 + 1)["new"], 1)                         # срок row_ts + accept_s + 1 наступил
        self.assertEqual(self.ev_of("BTC")["cm_missing"], "no_row")

    def test_coin_without_previous_row_does_not_wait(self):
        t0 = DAY0 + 2200
        self.series(t0 - 10, t0 - 2, syms=("BTC",))
        self.events(ev(0, t0, "SOL"), ev(1, t0, "BTC"))                                # SOL вне рядов, BTC ждёт
        t = time.time()
        r = self.poll(t0 + 0.2)
        self.assertLess(time.time() - t, 2.0 * PERF)                                   # без ожидания внутри прохода
        self.assertEqual(r["new"], 1)
        self.assertEqual(self.ev_of("SOL")["cm_missing"], "not_focus")


class Backfill(Base):
    def test_backfill_adds_cm_without_overwrite(self):
        t0 = DAY0 + 3000
        self.events(ev(0, t0, "BTC"), ev(1, t0 + 10, "ETH"), ev(2, t0 + 20, "SOL"))
        self.poll(t0 + 60)                                                             # рядов ещё нет: cm_missing=db у всех
        self.assertEqual(set(e.get("cm_missing") for e in self.evs().values()), {"db"})
        # у ETH уже есть ev.cm (подменим), у SOL — сигнал до R1.7 (без обоих полей)
        keys = dict(self.conn.execute("SELECT sym, key FROM signals"))
        mark = {"v": "old", "tier": "deep", "focus_s": 1, "row_ts": 1}
        e = self.evs()[keys["ETH"]]
        e.pop("cm_missing")
        e["cm"] = mark
        self.conn.execute("UPDATE signals SET ev=? WHERE key=?", (json.dumps(e), keys["ETH"]))
        e = self.evs()[keys["SOL"]]
        e.pop("cm_missing")
        self.conn.execute("UPDATE signals SET ev=? WHERE key=?", (json.dumps(e), keys["SOL"]))
        self.series(t0 - 5, t0 + 15, syms=("BTC", "ETH"))
        self.events(ev(3, t0 + 5, "BTC", typ="turnover_surge"))                       # новый сигнал только в догрузке
        rep = cli.backfill(self.conn, self.root, DAY0, now_s=t0 + 3600)
        self.assertEqual((rep["new"], rep["cm_added"]), (1, 1))
        got = dict((k, self.evs()[v]) for k, v in keys.items())
        self.assertEqual(got["BTC"]["cm"]["row_ts"], t0 - 1)
        self.assertNotIn("cm_missing", got["BTC"])
        self.assertEqual(got["ETH"]["cm"], mark)                                       # не перезаписан
        self.assertEqual(got["SOL"]["cm_missing"], "not_focus")                        # дополнен кодом
        self.assertEqual(got["BTC"]["range_5m_pct"], 1.2)
        new = [e for k, e in self.evs().items() if k not in keys.values()]
        self.assertEqual(new[0]["cm"]["row_ts"], t0 + 4)
        # повтор: ничего не меняется
        before = self.evs()
        rep = cli.backfill(self.conn, self.root, DAY0, now_s=t0 + 3600, restart=True)
        self.assertEqual((rep["new"], self.evs()), (0, before))


class Coverage(Base):
    def seed(self):
        t0 = DAY0 + 4000
        for ts in range(t0 - 5, t0 + 31):                                              # ETH в фокусе меньше warm_s
            self.cstore.write(ts, [{"sym": "BTC", "tier": "deep", "f": feats(), "focus_s": 600, "flags": []},
                                   {"sym": "ETH", "tier": "deep", "f": feats(), "focus_s": 100, "flags": []}], wr_ts=ts + 0.4)
        f = feats()
        f[L.FIRST_WAVE[0]] = None
        self.series(t0 + 31, t0 + 40, syms=("XRP",), f=f)
        self.cstore.write(t0 + 41, [{"sym": "XRP", "tier": "deep", "f": feats(), "focus_s": 600, "flags": []}], wr_ts=t0 + 41 + 9)
        self.events(ev(0, t0, "BTC"), ev(1, t0 + 1, "BTC"), ev(2, t0 + 2, "ETH"), ev(3, t0 + 3, "DOGE"),
                    ev(4, t0 + 40, "XRP"), ev(5, t0 + 42, "XRP", typ="turnover_surge"))  # последний — late
        self.poll(t0 + 600)
        self.conn.execute("INSERT INTO signals(key, t0, sym, type, ev, status, mature_ts, next_try_ts, captured_ts, schema_ver)"
                          " SELECT 'S-legacy', t0, sym, type, '{}', status, mature_ts, next_try_ts, captured_ts, schema_ver FROM signals LIMIT 1")
        return t0

    def test_cm_coverage(self):
        t0 = self.seed()
        cov = capture.cm_coverage(self.conn, None, 300)
        # фокус >= 300: BTC×2, XRP (cm), XRP late -> 3/4; всех 7, с ev.cm 4 (BTC×2, ETH, XRP)
        self.assertEqual((cov["n_focus"], cov["focus"]), (4, 0.75))
        self.assertEqual((cov["n_all"], cov["n_cm"], cov["all"], cov["legacy"]), (7, 4, round(4 / 7.0, 4), 1))
        self.assertEqual(cov["missing"], {"not_focus": 1, "late": 1})
        self.assertEqual(cov["first_wave"][L.FIRST_WAVE[0]], 0.75)
        self.assertEqual(cov["first_wave"][L.FIRST_WAVE[1]], 1.0)
        self.assertEqual(set(cov["first_wave"]), set(L.FIRST_WAVE))
        self.assertEqual(capture.cm_coverage(self.conn, t0 + 41, 300)["n_all"], 1)
        # warm_s из config/cmkt.json
        with open(os.path.join(self.root, "config", "cmkt.json"), "w", encoding="utf-8") as f:
            json.dump({"focus": {"warm_s": 50}}, f)
        self.assertEqual(capture.warm_s(self.root), 50)

    def test_stats_command(self):
        self.seed()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["stats"], root=self.root), 0)
        d = json.loads(out.getvalue())
        self.assertEqual((d["cm"]["focus"], d["cm"]["n_all"], d["cm"]["warm_s"]), (0.75, 7, 300))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["stats", "--by", "type", "--text"], root=self.root), 0)
        self.assertIn("сводный рынок", out.getvalue())
        self.assertIn(L.FIRST_WAVE[0], out.getvalue())

    def test_heartbeat_field_read_by_status(self):
        t0 = self.seed()
        svc = service.Service(self.root, log=lambda *_: None, conn=self.conn, clock=lambda: float(t0 + 700))
        svc.heartbeat(t0 + 700, force=True)
        _age, hb = PS.read_hb("labels", self.root, t0 + 700)
        self.assertEqual(hb["cm_cov_24h"]["focus"], 0.75)
        self.assertEqual(PST.cmkt_cov(hb), 0.75)


if __name__ == "__main__":
    unittest.main()
