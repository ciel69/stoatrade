"""Общие помощники тестов разметки и сервиса labels (R1, задача 12): временный корень с конфигами, синтетический рынок, фейковый fetch. Сеть не используется."""
import json
import os
import random
import shutil
import tempfile
import time

import _bootstrap  # noqa: F401
from tb.labels import capture, klines
from tb.logic import bars

PROJECT = _bootstrap.ROOT
CFG_FILES = ("risk_profile.json", "exec_limits.json", "labels.json", "shadow_policies.json", "geometry.json", "memory.json")
BASE = 1793577600                       # 2026-11-02 00:00 UTC
T0 = BASE + 12 * 3600 + 37              # сигнал не на границе минуты
SPREAD = 3.0


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def mk_root(test):
    root = tempfile.mkdtemp(prefix="tblab_")
    test.addCleanup(shutil.rmtree, root, True)
    os.makedirs(os.path.join(root, "config"))
    os.makedirs(os.path.join(root, "runtime"))
    for n in CFG_FILES:
        shutil.copy(os.path.join(PROJECT, "config", n), os.path.join(root, "config", n))
    return root


def hot_event(i=0, t=T0, sym="DOGEUSDT", typ="range_5m", **kw):
    d = {"id": "e%d" % i, "ts": iso(t + i), "wake": True, "type": typ, "sym": sym, "price": 100.0, "score": 7.5, "range_5m_pct": 1.2, "ret_5m_pct": 0.9,
         "direction": "up", "spread_bps": SPREAD, "turn_surge_5m": 4.0, "corr_group": "meme", "tradable": True}
    d.update(kw)
    return d


def write_events(root, events, mode="ab"):
    with open(os.path.join(root, "runtime", "hot_events.jsonl"), mode) as f:
        for e in events:
            f.write((json.dumps(e, ensure_ascii=False) + "\n").encode("utf-8"))


def walk(t_from, t_to, seed=1, px=100.0):
    """Минутные свечи случайного блуждания [t_from, t_to) (кратно 60), детерминированно."""
    rng = random.Random(seed)
    rows, t = [], t_from // 60 * 60
    while t < t_to:
        o = px
        c = o * (1 + rng.uniform(-0.001, 0.001))
        h = max(o, c) * (1 + rng.uniform(0, 0.0005))
        l = min(o, c) * (1 - rng.uniform(0, 0.0005))
        rows.append((t, o, h, l, c, rng.uniform(5, 50)))
        px = c
        t += 60
    return rows


class Market(object):
    """Фейковая биржа: fetch(sym, interval, start, end) отдаёт срезы одного ряда 1м (и его агрегаты 5м/1ч); ведёт журнал вызовов."""

    def __init__(self, rows=None, seed=1):
        self.rows = rows if rows is not None else walk(T0 - 210 * 3600, T0 + 6 * 3600, seed)
        self.calls = []
        self.fail = None                # исключение для всех вызовов
        self.empty = False
        self._agg = {}
        self._set()

    def _set(self):
        self.by = {"1": self.rows, "5": bars.agg(self.rows, 5), "60": bars.agg(self.rows, 60)}

    def drop(self, t_from, t_to):
        self.rows = [r for r in self.rows if not (t_from <= r[0] < t_to)]
        self._set()

    def fetch(self, sym, interval, start_s, end_s):
        self.calls.append((sym, interval, start_s, end_s))
        if self.fail is not None:
            raise self.fail
        if self.empty:
            return []
        return [r for r in self.by[interval] if start_s <= r[0] <= end_s]

    def row(self, ts):
        return next(r for r in self.rows if r[0] == ts)


class FakeBtc(object):
    def __init__(self, market):
        self.m = market
        self.calls = []

    def get(self, t0_s):
        self.calls.append(t0_s)
        end = t0_s // 300 * 300 - 300
        return [r for r in self.m.by["5"] if end - 299 * 300 <= r[0] <= end]


def capture_all(root, conn, events, now=None):
    """Записать события в hot_events.jsonl и прогнать capture.poll. -> cfg."""
    write_events(root, events)
    cfg = capture.load_cfg(root)
    capture.poll(conn, root, cfg, T0 + 10 if now is None else now)
    return cfg


def sig_row(conn, key=None):
    from tb.labels import label
    cur = conn.execute("SELECT key FROM signals" + (" WHERE key=?" if key else " ORDER BY t0 LIMIT 1"), (key,) if key else ())
    k = cur.fetchone()[0]
    cols = label._COLS
    return dict(zip(cols, conn.execute("SELECT %s FROM signals WHERE key=?" % ", ".join(cols), (k,)).fetchone()))
