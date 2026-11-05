"""R1.7, задача 23: нагрузка и бюджет сводного рынка (Requirements 12.1, 12.3, 12.5; design.md, «Нагрузка»). Медленный набор:
`./tb test --slow tests/test_cmkt_load.py`; запись трафика — `TB_CMKT_LOAD_REC=<папка>` (без TB_SLOW_TESTS=1 все тесты пропускаются).

1. `TestBookBest` — регрессия дефекта, найденного задачей 23: `BookState.best()` проходил все уровни на каждой дельте (стаканы 400–1000
   уровней, Binance/OKX/Bybit `deep`) и съедал до 80 % CPU сборщика; теперь лучшие цены — кучи с ленивым удалением.
2. `TestSyntheticCollector` — без сети и без записи: настоящий `Collector` с адаптером `fakex` (20 `deep` × 400 уровней, 120 `light`)
   получает синтетический поток по расписанию × 4 от замеренного самого нагруженного рынка (OKX `perp`, ≈ 2 900 сообщ./с) и всплеск
   × 10 на 10 с; задержка обработки `t_proc − t_rx` (t_rx — плановое время прихода, отставание очереди входит) и CPU процесса.
3. `TestBudgetShrink` — деградация `BUDGET`: сводка (`service.Summary`) суммирует heartbeat сборщиков; бюджет превышен
   `budget.over_min` минут подряд -> ярус `light` −`shrink_step` за шаг до `light_min`, жёлтый `BUDGET` в focus.json; без превышения
   сжатие не идёт.
4. `TestRecordedReplay` — запись живого трафика 600 с через 11 процессов сборщиков (этот файл, режим `replay-child`) и процесс сводки
   (`python -m tb.cmkt.service`) во временном корне; замер счётчиками процессов ОС раз в секунду: CPU (среднее, p99 секундных,
   p99 минутных), сумма RSS, байты записи (пересчёт на сутки), трафик, задержка обработки. Два прогона: `x4` — условие задачи 23
   (4 круга записи на скорости ×4, то есть ×4 объёма живого трафика, 600 с записи, всплеск ×10 на 10 с; проверяются целостность и
   отсутствие потерь) и `x1` — бюджет Requirement 12.3 в темпе живого трафика (скорость ×1, одна запись 600 с; установившиеся CPU,
   RSS, задержка, трафик, диск против `config/cmkt.json: budget`). Всплеск и хвост очереди из установившегося p99 исключены
   (REQ 12.3 — про штатный поток; пик меряется отдельно: задержка в всплеске и отставание `behind_max_s`).
   Запись — гигабайты, в репозиторий не входит: папка в `TB_CMKT_LOAD_REC`
   (файлы `<market>.rec` и `<market>.rec.idx.json` скрипта записи задачи 23, фокус записи в `focus_src/`); без неё тест пропускается.

Формат записи `<market>.rec`: подряд записи `struct '<cdI'` (тип, t, длина) + данные: `M` — conn_id + b"\\0" + сырой текст сообщения WS
(t = t_rx), `X` — pickle результата `load_meta`, `O` — json [offset, rtt], `S` — pickle (sym, BookSnapshot) снимка вне потока,
`P` — pickle [события] REST-опроса, `Q` — pickle (id, sym, kind) запроса снимка WS API Binance. Только публичные данные бирж.
"""
import asyncio
import json
import math
import os
import pickle
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from collections import deque

import _bootstrap  # noqa: F401
from tb.cmkt import adapter as A
from tb.cmkt import collector as C
from tb.cmkt import conf

SLOW = os.environ.get("TB_SLOW_TESTS") == "1"
REC_DIR = os.environ.get("TB_CMKT_LOAD_REC") or ""
HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
HDR = struct.Struct("<cdI")
LAT_BINS = 20000                                  # 0.1 мс; последняя корзина — 2 с и больше


def budget():
    cfg, errs = conf.load(PROJ)
    return (cfg if not errs else conf.defaults())["budget"]


# ---------------------------------------------------------------- общий повтор потока в сборщик
class ReplayConn(object):
    """Соединение без сокета: сообщения подаёт повтор; живость поддерживается повтором (кадров ping/pong в записи нет)."""

    def __init__(self, cid, cfg):
        self.conn_id = cid
        self.live = A.Liveness(float(cfg["sync"]["ping_s"]), float(cfg["sync"]["silent_s"]))
        self.lag = A.LagMeter()
        self.stats = {"msgs": 0, "bytes": 0, "events": 0, "parse_err": 0, "sub_msgs": 0, "sub_rejected": 0, "rejected": [],
                      "connects": 0, "reconnects": 0}
        self._t_last = None
        self._queue = deque()
        self.connected = True
        self.on_status = lambda ev, info: None
        self._stop = None

    def alive(self, now=None):
        return self.connected

    def subscribe(self, subs):
        pass

    def unsubscribe(self, subs):
        pass

    def send_later(self, texts):
        pass

    async def run(self):
        self.stats["connects"] += 1
        self.live.on_msg(time.time())
        self.on_status("connect", {"t": time.time()})
        self._stop = asyncio.Event()
        await self._stop.wait()

    async def close(self):
        self.connected = False
        if self._stop is not None:
            self._stop.set()


class Schedule(object):
    """Виртуальное время потока v (с от начала) -> стена: скорость speed; всплеск — с burst_at (стеночные с от старта) burst_s секунд
    со скоростью burst_x."""

    def __init__(self, start, speed, burst_at=None, burst_x=10.0, burst_s=10.0):
        self.start, self.speed = start, float(speed)
        self.b = None
        if burst_at is not None:
            vb = burst_at * self.speed
            self.b = (vb, vb + burst_s * burst_x, float(burst_x), float(burst_at), float(burst_s))

    def wall(self, v):
        if self.b is None or v <= self.b[0]:
            return self.start + v / self.speed
        vb, ve, x, wb, ws = self.b
        if v <= ve:
            return self.start + wb + (v - vb) / x
        return self.start + wb + ws + (v - ve) / self.speed


def make_replay_collector(venue, kind, cfg, root, adapter=None):
    """Настоящий Collector без сети: соединения — ReplayConn, REST (метаданные, offset, снимки, опрос) отключён."""
    col = C.Collector(venue, kind, cfg, root=root, adapter=adapter)

    def make_conn(cid, subs):
        conn = ReplayConn(cid, cfg)
        conn.on_status = lambda ev, info, _c=cid, _k=conn: col.on_status(_c, _k, ev, info)
        return conn
    col._make_conn = make_conn
    col._api = lambda key: None
    col._book_req = None
    col.rest_snapshot = lambda sym, limit: None
    col.measure_offset = lambda: (col.offset.offset, col.offset.rtt)
    col.offset.offset, col.offset.rtt, col.offset.t = 0.0, 0.05, time.time()
    col.adapter.poll = lambda *x, **k: []
    if getattr(col.adapter, "side_poll", None) is not None:
        col.adapter.side_poll = lambda *x, **k: []
    return col


class Feeder(object):
    """Подача потока [(тип, t, данные)] в сборщик по расписанию. t_rx события = плановое время прихода; t_ex — тем же расписанием.

    Задержка `t_proc − t_rx` пишется в общую гистограмму, а также отдельно в установившуюся и в всплеск: бюджет Requirement 12.3
    (p99 ≤ 300 мс) относится к штатному потоку; секунды всплеска ×10 и хвост очереди после него считаются отдельно."""

    def __init__(self, col, sch, t0, off_s=0.0, skip=None):
        self.col, self.sch, self.t0, self.off = col, sch, t0, off_s
        self.hist = [0] * (LAT_BINS + 1)
        self.steady_hist = [0] * (LAT_BINS + 1)
        self.burst_hist = [0] * (LAT_BINS + 1)
        self.skip = skip                                    # (t_from, t_to) стеночного времени: всплеск + восстановление
        self.lat_max = self.lat_max_steady = self.lat_max_burst = 0.0
        self.sec_bytes = {}                                 # стеночная секунда -> байты полезной нагрузки (трафик без всплеска)
        self.n_msg = self.n_ev = self.bytes = 0
        self.behind_max = 0.0
        self.snaps = getattr(col.adapter, "_snaps", None)
        self._yield = time.perf_counter()
        self._live = 0.0
        self._default = None

    def _lat_add(self, tw, lat):
        """Корзина 0.1 мс (раньше плана на < 1 мс — 0), отдельно установившаяся часть и всплеск."""
        i = min(LAT_BINS, max(0, int(lat * 10)))
        self.hist[i] += 1
        self.lat_max = max(self.lat_max, lat)
        if self.skip is not None and self.skip[0] <= tw < self.skip[1]:
            self.burst_hist[i] += 1
            self.lat_max_burst = max(self.lat_max_burst, lat)
        else:
            self.steady_hist[i] += 1
            self.lat_max_steady = max(self.lat_max_steady, lat)

    def lat_q(self, q, hist=None):
        h = self.hist if hist is None else hist
        n = sum(h)
        if not n:
            return None
        acc = 0
        for i, x in enumerate(h):
            acc += x
            if acc >= q * n:
                return i / 10.0
        return None

    async def run(self, records, base=0.0, loop=0):
        col, sch, off, parse, kind = self.col, self.sch, self.off, self.col.adapter.parse, self.col.kind
        for typ, t, data in records:
            v = max(base, base + (t - self.t0))
            tw = sch.wall(v)
            now = time.time()
            if tw - now > 0.001:
                await asyncio.sleep(tw - now)
                self._yield = time.perf_counter()
                now = time.time()
            else:
                self.behind_max = max(self.behind_max, now - tw)
                if time.perf_counter() - self._yield > 0.005:
                    await asyncio.sleep(0)
                    self._yield = time.perf_counter()
            if now - self._live >= 1.0:
                for conn in col.conns.values():
                    conn.live.on_msg(now)
                self._live = now
            if typ == b"M":
                i = data.index(b"\0")
                cid = data[:i].decode()
                raw = data[i + 1:].decode("utf-8", "replace")
                conn = col.conns.get(cid)
                if conn is None:
                    if self._default is None or self._default not in col.conns.values():
                        self._default = next(iter(col.conns.values()), None)
                    conn = self._default
                self.n_msg += 1
                self.bytes += len(raw)
                sk = int(tw - sch.start)
                self.sec_bytes[sk] = self.sec_bytes.get(sk, 0) + len(raw)
                if conn is not None:
                    conn.stats["msgs"] += 1
                    conn.stats["bytes"] += len(raw)
                    conn._t_last = tw
                    conn.live.on_msg(tw)
                try:
                    evs = parse(kind, raw, tw) or []
                except Exception:                              # noqa: BLE001
                    if conn is not None:
                        conn.stats["parse_err"] += 1
                    continue
                out = []
                for e in evs:
                    if isinstance(e, (A.Pong, A.SubAck)):
                        continue
                    rep = {}
                    te = getattr(e, "t_ex", None)
                    if te is not None:
                        rep["t_ex"] = sch.wall(base + (te - off - self.t0)) + off
                    if loop > 0 and getattr(e, "tid", None) is not None:
                        rep["tid"] = (loop, e.tid)              # круг повтора: те же id сделок — не дубли
                    if rep:
                        e = e._replace(**rep)
                    if conn is not None and isinstance(e, (A.Trade, A.BookDelta, A.BookSnapshot)):
                        conn.lag.on_msg(tw, e.t_ex, off)
                    out.append(e)
                if out:
                    self.n_ev += len(out)
                    col.on_events(out, tw)
                    self._lat_add(tw, (time.time() - tw) * 1000.0)
            elif typ == b"Q" and self.snaps is not None:
                rid, sym, k = pickle.loads(data)
                self.snaps.put(rid, (sym,), k)
            elif typ == b"S":
                sym, snap = pickle.loads(data)
                if isinstance(snap, A.BookSnapshot):
                    te = snap.t_ex
                    snap = snap._replace(t_rx=tw, t_ex=(sch.wall(base + (te - off - self.t0)) + off) if te else None)
                    col.apply_rest(sym, snap, tw)
            elif typ == b"P":
                evs = [e._replace(t_rx=tw) if hasattr(e, "t_rx") else e for e in pickle.loads(data)]
                if evs:
                    col.on_events(evs, tw)


async def wait_ready(col, timeout=90.0):
    """Метаданные, фокус и все соединения плана открыты (1 в секунду)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        await asyncio.sleep(0.25)
        n = col.planner.counts().get("conns") or 0
        if col.meta_ok and col.coins and n > 0 and not col.open_q and len(col.conns) >= n:
            return True
    return False


# ---------------------------------------------------------------- запись: чтение
def read_records(path):
    with open(path, "rb", buffering=8 << 20) as f:
        while True:
            h = f.read(HDR.size)
            if len(h) < HDR.size:
                return
            typ, t, n = HDR.unpack(h)
            yield typ, t, f.read(n)


def replay_child(argv):
    """Процесс сборщика одного рынка на повторе записи (вызов: python tests/test_cmkt_load.py replay-child ...)."""
    import argparse
    import http.client
    import socket
    import urllib.request
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--rec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--speed", type=float, default=4.0)
    ap.add_argument("--loops", type=int, default=4)
    ap.add_argument("--burst-at", type=float, default=None, dest="burst_at")
    ap.add_argument("--burst-x", type=float, default=10.0, dest="burst_x")
    ap.add_argument("--burst-s", type=float, default=10.0, dest="burst_s")
    a = ap.parse_args(argv)
    C.scrub_env()

    def no_net(*x, **k):
        raise OSError("replay: внешняя сеть запрещена")
    socket.create_connection = no_net                     # self-pipe цикла asyncio (socketpair) не трогаем
    urllib.request.urlopen = no_net
    http.client.HTTPConnection.connect = no_net
    http.client.HTTPSConnection.connect = no_net
    venue, kind = C.parse_market(a.market)
    cfg, errs = conf.load(a.root)
    if errs:
        sys.stderr.write("E_CONF %s\n" % errs)
        return 2
    col = make_replay_collector(venue, kind, cfg, a.root)
    path = os.path.join(a.rec, "%s_%s.rec" % (venue, kind))
    with open(path + ".idx.json", encoding="utf-8") as f:
        idx = json.load(f)
    t0, D = idx["t_first"], idx["t_last"] - idx["t_first"]
    meta = off = None
    for typ, t, data in read_records(path):
        if typ == b"X" and meta is None:
            meta = pickle.loads(data)
        elif typ == b"O" and off is None:
            off = json.loads(data.decode())
        if meta is not None and off is not None:
            break
        if t > t0 + 120:
            break
    off_s = float(off[0]) if off and off[0] is not None else 0.0
    col.offset.offset, col.offset.rtt, col.offset.t = off_s, float(off[1]) if off and off[1] else 0.05, time.time()
    col.adapter.offset_s = off_s
    col._call_meta = lambda now: meta or {}
    out = {"market": a.market}
    stop = asyncio.Event()

    async def main():
        task = asyncio.ensure_future(col.run(stop=stop))
        try:
            col.load_meta(time.time())
            col._t_focus = None
            ok = await wait_ready(col)
            await asyncio.sleep(1.0)
            sch = Schedule(time.time() + 0.5, a.speed, a.burst_at, a.burst_x, a.burst_s)
            skip = None
            if a.burst_at is not None:
                skip = (sch.start + a.burst_at, sch.start + a.burst_at + a.burst_s + 45.0)
            fd = Feeder(col, sch, t0, off_s, skip=skip)
            out.update({"ready": ok, "start": sch.start, "conns": len(col.conns), "coins": len(col.coins),
                        "deep": sum(1 for c in col.coins.values() if c.tier == "deep")})
            for loop in range(a.loops):
                if loop > 0:                               # новый круг: обрыв и переподключение (стаканы -> RESYNC)
                    now = time.time()
                    for cid, conn in list(col.conns.items()):
                        col._conn_down(cid, now)
                        col.on_status(cid, conn, "connect", {"t": now})
                await fd.run(read_records(path), base=loop * D, loop=loop)
            out.update({"msgs": fd.n_msg, "events": fd.n_ev, "bytes": fd.bytes, "behind_max_s": round(fd.behind_max, 3),
                        "wall_s": round(time.time() - sch.start, 1), "lat_hist_01ms": fd.hist,
                        "lat_hist_steady_01ms": fd.steady_hist, "lat_hist_burst_01ms": fd.burst_hist,
                        "lat_max_ms": round(fd.lat_max, 1), "lat_max_steady_ms": round(fd.lat_max_steady, 1),
                        "lat_max_burst_ms": round(fd.lat_max_burst, 1), "sec_bytes": fd.sec_bytes, "rec_s": D})
        finally:
            await asyncio.sleep(1.5)
            stop.set()
            await task
    asyncio.run(main())
    out["parse_err"] = sum(c.stats.get("parse_err", 0) for c in col.conns.values())
    out["err_n"] = col.counters.get("err")
    listed = getattr(getattr(col.adapter, "meta", None), "listed", None)

    def _listed(sym):
        """Монета фокуса без инструмента на рынке (TradFi из планов) стакана не имеет по замыслу — в целостность не входит."""
        if not col.meta_ok or listed is None:
            return True
        try:
            return bool(listed(col.mkt, sym))
        except Exception:                                  # noqa: BLE001
            return True
    out["books_bad"] = sum(1 for c in col.coins.values()
                           if c.tier == "deep" and _listed(c.sym) and (c.book is None or c.book.status != "ok"))
    out["coins_ok"] = sum(1 for c in col.coins.values()
                          if c.tier == "deep" and _listed(c.sym) and c.book is not None and c.book.status == "ok")
    out["bad_deep"] = [["%s" % c.sym, "none" if c.book is None else c.book.status,
                        "" if c.book is None else (c.book.reason or "")]
                       for c in col.coins.values()
                       if c.tier == "deep" and _listed(c.sym) and (c.book is None or c.book.status != "ok")][:60]
    with open(os.path.join(a.out, "replay_%s_%s.json" % (venue, kind)), "w", encoding="utf-8") as f:
        json.dump(out, f)
    return 0


# ---------------------------------------------------------------- счётчики процессов ОС
class ProcMeter(object):
    """CPU (user + kernel, с), рабочий набор (байт), байты записи IO процесса по pid. Windows — kernel32/psapi, Linux — /proc."""

    def __init__(self):
        self.win = os.name == "nt"
        if self.win:
            import ctypes
            from ctypes import wintypes
            self.ct, self.wt = ctypes, wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t), ("a", ctypes.c_size_t), ("b", ctypes.c_size_t),
                            ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", ctypes.c_size_t), ("f", ctypes.c_size_t)]

            class IOC(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in ("ro", "wo", "oo", "rb", "wb", "ob")]
            self.PMC, self.IOC = PMC, IOC
            self.k32, self.psapi = ctypes.windll.kernel32, ctypes.windll.psapi
            self.k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            self.k32.GetProcessIoCounters.argtypes = [wintypes.HANDLE, ctypes.POINTER(IOC)]
            self.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]

    def sample(self, p):
        if self.win:
            ct, wt = self.ct, self.wt
            h = wt.HANDLE(int(p._handle))
            fts = [wt.FILETIME() for _ in range(4)]
            if not self.k32.GetProcessTimes(h, *[ct.byref(x) for x in fts]):
                return None
            ft = lambda x: ((x.dwHighDateTime << 32) | x.dwLowDateTime) / 1e7    # noqa: E731
            pmc = self.PMC()
            pmc.cb = ct.sizeof(self.PMC)
            self.psapi.GetProcessMemoryInfo(h, ct.byref(pmc), pmc.cb)
            io = self.IOC()
            self.k32.GetProcessIoCounters(h, ct.byref(io))
            return {"cpu": ft(fts[2]) + ft(fts[3]), "ws": pmc.WorkingSetSize, "wr": io.wb}
        try:
            with open("/proc/%d/stat" % p.pid) as f:
                st = f.read().rsplit(")", 1)[1].split()
            tck = os.sysconf("SC_CLK_TCK")
            with open("/proc/%d/statm" % p.pid) as f:
                ws = int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
            wr = 0
            try:
                with open("/proc/%d/io" % p.pid) as f:
                    for ln in f:
                        if ln.startswith("write_bytes:"):
                            wr = int(ln.split()[1])
            except OSError:
                pass
            return {"cpu": (int(st[11]) + int(st[12])) / float(tck), "ws": ws, "wr": wr}
        except (OSError, ValueError, IndexError):
            return None


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, int(math.ceil(q * len(v))) - 1))] if v else None


def series_metrics(rows, names):
    """rows [(t, {name: sample})] в окне -> CPU среднее/p99 секундных/максимум минутных, RSS, запись (ГБ в сутки)."""
    rows = [r for r in rows if all(n in r[1] for n in names)]
    if len(rows) < 3:
        return None
    cores = []
    for (ta, a), (tb, b) in zip(rows, rows[1:]):
        if tb > ta:
            cores.append((tb, sum(b[n]["cpu"] - a[n]["cpu"] for n in names) / (tb - ta)))
    span = rows[-1][0] - rows[0][0]
    tot = lambda r, k: sum(r[1][n][k] for n in names)      # noqa: E731
    mins = []
    for k in range(int(span // 60) + 1):
        seg = [c for t, c in cores if rows[0][0] + 60 * k < t <= rows[0][0] + 60 * (k + 1)]
        if len(seg) >= 50:
            mins.append(sum(seg) / len(seg))
    return {"span_s": round(span, 1), "cpu_avg": (tot(rows[-1], "cpu") - tot(rows[0], "cpu")) / span,
            "cpu_p99_1s": pct([c for _, c in cores], 0.99), "cpu_max_1s": max(c for _, c in cores),
            "cpu_p99_1min": pct(mins, 0.99) if mins else None, "cpu_max_1min": max(mins) if mins else None,
            "rss_mb_max": max(tot(r, "ws") for r in rows) / 1048576.0,
            "disk_gb_day": (tot(rows[-1], "wr") - tot(rows[0], "wr")) / span * 86400 / 1e9,
            "per_proc_cpu": dict((n, (rows[-1][1][n]["cpu"] - rows[0][1][n]["cpu"]) / span) for n in names)}


# ---------------------------------------------------------------- 1. регрессия best()
@unittest.skipUnless(SLOW, "TB_SLOW_TESTS=1")
class TestBookBest(unittest.TestCase):
    def _book(self, raw):
        st = A.BookState("x_perp", "BTC", "bybit", raw=raw)
        rng = random.Random(7)
        bids = [("%.1f" % (50000 - i * 0.5), "%.3f" % rng.uniform(0.1, 5)) for i in range(1000)]
        asks = [("%.1f" % (50000.5 + i * 0.5), "%.3f" % rng.uniform(0.1, 5)) for i in range(1000)]
        st.apply_snapshot(A.BookSnapshot("x_perp", "BTC", 1.0, 1.0, 1, bids, asks, False), 1.0)
        return st, rng

    def test_best_matches_full_scan_and_is_cheap(self):
        for raw in (False, True):
            st, rng = self._book(raw)
            u = 1
            t = time.perf_counter()
            for k in range(20000):
                u += 1
                mid = 50000.25 + rng.randint(-40, 40) * 0.5
                b = [("%.1f" % (mid - 0.25 - rng.randint(0, 30) * 0.5), "0" if rng.random() < 0.3 else "%.3f" % rng.uniform(0.1, 5))
                     for _ in range(5)]
                a = [("%.1f" % (mid + 0.25 + rng.randint(0, 30) * 0.5), "0" if rng.random() < 0.3 else "%.3f" % rng.uniform(0.1, 5))
                     for _ in range(5)]
                st.apply_delta(A.BookDelta("x_perp", "BTC", 1.0, 1.0, u, u, None, None, b, a), 1.0)
                if k % 997 == 0 and st.status == "ok":
                    full = (max(float(p) for p in st.bids), min(float(p) for p in st.asks))
                    self.assertEqual(st.best(), full)
            dt = time.perf_counter() - t
            # прежняя реализация: ~20 000 × 2 000 уровней ≈ 40 млн преобразований (десятки секунд); теперь — доли секунды
            self.assertLess(dt / 20000 * 1e6, 150.0, "дельта стакана 1000 уровней дороже 150 мкс (raw=%s)" % raw)
            self.assertLessEqual(len(st._hb), 2 * len(st.bids) + 64 + 1)
            self.assertLessEqual(len(st._ha), 2 * len(st.asks) + 64 + 1)


# ---------------------------------------------------------------- 1б. вес снимков не уходит на монеты вне рынка (быстрый, всегда)
class TestUnlistedResync(unittest.TestCase):
    def test_unlisted_deep_coins_do_not_spend_snapshot_weight(self):
        from tb.cmkt.ex import fakex as F
        root = tempfile.mkdtemp(prefix="tb_cmkt_nl_")
        try:
            cfg = conf.defaults()
            ad = F.FakexAdapter(cfg)                       # perp deep — снимок вне потока (binance_perp)
            t = [1793750400.0]
            col = C.Collector("fakex", "perp", cfg, root=root, adapter=ad, clock=lambda: t[0])
            col.budget = A.RestBudget(160, 0, 0)          # вес снимков как у Binance perp: 160 в минуту, снимок 20 (8 в минуту)
            col.load_meta(t[0])
            nl = ["XAU", "SOXL", "MU", "CL", "SPCX", "SKHYNIX"]         # TradFi из топа/планов: на рынке нет
            col.apply_focus({"deep": nl + ["BTC", "ETH"], "light": [], "why": dict([(s, "plan") for s in nl] + [("BTC", "core"),
                                                                                                                  ("ETH", "core")])}, t[0])
            got = []
            for _ in range(240):                           # 60 с
                col.tick(t[0])
                j = col.resync_jobs(t[0])
                if j is not None:
                    got.append(j[1])
                    if j[1] in ("BTC", "ETH"):
                        col.rq.done(j[1])
                t[0] += 0.25
            self.assertEqual(sorted(set(got)), ["BTC", "ETH"], got)
            self.assertFalse(set(nl) & set(col.rq.req))
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- 2. синтетический поток без сети
N_DEEP, N_LIGHT, DEPTH = 20, 120, 400
BASE_RATE = 2900.0                                # сообщ./с самого нагруженного рынка (OKX perp, запись 2026-11-04, 140 монет)


def synth_stream(seconds, rng, t0=1793750400.0):
    """Синтетический рынок fakex: deep — снимок 400 уровней в потоке, затем дельты (правило bybit), сделки, тикеры; light — частичные
    стаканы book5 снимками. Доли сообщений как у OKX perp. -> (инструменты, [(b"M", t, данные)])."""
    syms = ["C%03d" % i for i in range(N_DEEP + N_LIGHT)]
    insts = [{"native": s + "USDT", "base": s, "quote": "USDT", "settle": "USDT", "type": "linear_perp", "contract_size": 1,
              "funding_h": 8} for s in syms]
    deep, light = syms[:N_DEEP], syms[N_DEEP:]
    px = dict((s, 100.0 + i) for i, s in enumerate(syms))
    u = dict((s, 1) for s in deep)
    recs = []
    cid = b"main#0\0"
    for s in deep:
        p = px[s]
        snap = {"ch": "book", "type": "snap", "s": s + "USDT", "u": 1, "E": int(t0 * 1000),
                "b": [["%.4f" % (p - 0.01 * (i + 1)), "%d" % rng.randint(1, 900)] for i in range(DEPTH)],
                "a": [["%.4f" % (p + 0.01 * (i + 1)), "%d" % rng.randint(1, 900)] for i in range(DEPTH)]}
        recs.append((b"M", t0, cid + json.dumps(snap, separators=(",", ":")).encode()))
    # доли: дельты deep 55 %, сделки 25 %, тикеры 8 %, частичные light 12 %
    n = int(seconds * BASE_RATE)
    for k in range(n):
        t = t0 + 0.05 + k / BASE_RATE
        r = rng.random()
        if r < 0.55:
            s = deep[rng.randrange(N_DEEP)]
            u[s] += 1
            p = px[s]
            m = {"ch": "book", "type": "delta", "s": s + "USDT", "u": u[s], "E": int(t * 1000),
                 "b": [["%.4f" % (p - 0.01 * rng.randint(1, 60)), "0" if rng.random() < 0.3 else "%d" % rng.randint(1, 900)]
                       for _ in range(rng.randint(1, 6))],
                 "a": [["%.4f" % (p + 0.01 * rng.randint(1, 60)), "0" if rng.random() < 0.3 else "%d" % rng.randint(1, 900)]
                       for _ in range(rng.randint(1, 6))]}
        elif r < 0.80:
            s = syms[rng.randrange(len(syms))]
            m = {"ch": "trade", "s": s + "USDT", "E": int(t * 1000), "p": "%.4f" % px[s], "q": "%d" % rng.randint(1, 50),
                 "S": rng.choice(("buy", "sell")), "id": k}
        elif r < 0.88:
            s = syms[rng.randrange(len(syms))]
            m = {"ch": "ticker", "s": s + "USDT", "E": int(t * 1000), "mark": px[s], "index": px[s], "bid": px[s] - 0.01,
                 "ask": px[s] + 0.01, "fr": 0.0001, "oi": 100000 + k % 1000, "turn": 5e7}
        else:
            s = light[rng.randrange(N_LIGHT)]
            p = px[s]
            m = {"ch": "book", "type": "snap", "s": s + "USDT", "u": k, "E": int(t * 1000),
                 "b": [["%.4f" % (p - 0.01 * (i + 1)), "%d" % rng.randint(1, 900)] for i in range(5)],
                 "a": [["%.4f" % (p + 0.01 * (i + 1)), "%d" % rng.randint(1, 900)] for i in range(5)]}
        recs.append((b"M", t, cid + json.dumps(m, separators=(",", ":")).encode()))
    return {"perp": insts, "spot": []}, recs, deep, light


@unittest.skipUnless(SLOW, "TB_SLOW_TESTS=1")
class TestSyntheticCollector(unittest.TestCase):
    def test_x4_and_burst_x10_latency_and_cpu(self):
        from tb.cmkt.ex import fakex as F
        from tb.cmkt import focus as FO
        rng = random.Random(23)
        seconds = 60.0                             # 60 с потока: × 4 -> 15 с стены, из них 10 с всплеска × 10 (= 100 с потока) не помещаются,
        insts, recs, deep, light = synth_stream(seconds + 100.0, rng)   # поэтому поток 160 с: 4 с × 4, 10 с × 10, 11 с × 4
        root = tempfile.mkdtemp(prefix="tb_cmkt_load_")
        try:
            cfg = conf.defaults()
            FO.write(root, {"schema": FO.SCHEMA, "ts": time.time(), "deep": deep, "light": light,
                            "why": dict([(s, "top") for s in deep + light]), "since": {}, "compat": []})
            ad = F.FakexAdapter(cfg, instruments=insts, rules={"perp": {"deep": "bybit", "light": "replace"}})
            col = make_replay_collector("fakex", "perp", cfg, root, adapter=ad)
            col._call_meta = lambda now: ad.load_meta(None, now=now)
            res = {}

            async def main():
                stop = asyncio.Event()
                task = asyncio.ensure_future(col.run(stop=stop))
                try:
                    col.load_meta(time.time())
                    col._t_focus = None
                    self.assertTrue(await wait_ready(col, 30.0))
                    sch = Schedule(time.time() + 0.3, 4.0, burst_at=4.0, burst_x=10.0, burst_s=10.0)
                    fd = Feeder(col, sch, recs[0][1])
                    c0, w0 = time.process_time(), time.time()
                    await fd.run(recs)
                    res.update({"cpu": (time.process_time() - c0) / max(1e-6, time.time() - w0), "wall": time.time() - w0,
                                "p99": fd.lat_q(0.99), "p50": fd.lat_q(0.5), "behind": fd.behind_max, "n": fd.n_msg})
                finally:
                    stop.set()
                    await task
            asyncio.run(main())
            ok_books = sum(1 for c in col.coins.values() if c.tier == "deep" and c.book is not None and c.book.status == "ok")
            self.assertEqual(ok_books, N_DEEP, "стаканы deep не в порядке после повтора")
            self.assertGreater(col.counters["pub"], 10)
            lim = budget()
            msg = "синтетика × 4 / всплеск × 10: %s" % json.dumps(dict((k, round(v, 3)) for k, v in res.items()))
            sys.stderr.write("\ntest_cmkt_load: %s\n" % msg)
            self.assertLessEqual(res["p99"], float(lim["proc_p99_ms"]), msg)
            self.assertLess(res["cpu"], 1.0, msg)          # один процесс не упирается в ядро (иначе очередь растёт без предела)
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- 3. деградация BUDGET
@unittest.skipUnless(SLOW, "TB_SLOW_TESTS=1")
class TestBudgetShrink(unittest.TestCase):
    def test_light_shrinks_by_step_to_min_and_marks_budget(self):
        from tb.cmkt import focus as FO
        from tb.cmkt import service as SV
        from tb.platform import services as S
        root = tempfile.mkdtemp(prefix="tb_cmkt_budget_")
        try:
            os.makedirs(os.path.join(root, "runtime"), exist_ok=True)
            cfg = conf.defaults()
            bud = cfg["budget"]
            t = [1793750400.0]
            clock = lambda: t[0]                         # noqa: E731
            syms = ["C%03d" % i for i in range(200)]
            with open(os.path.join(root, "runtime", "heat.json"), "w", encoding="utf-8") as f:
                json.dump({"ts_epoch": t[0], "rows": [{"sym": s, "xturn_24h_musd": 1000.0 - i} for i, s in enumerate(syms[:30])]}, f)
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t[0]))
            with open(os.path.join(root, "runtime", "hot_events.jsonl"), "w", encoding="utf-8") as f:
                for s in syms[30:150]:
                    f.write(json.dumps({"ts": ts, "sym": s + "USDT", "type": "x"}) + "\n")
            fo = FO.Focus(root, cfg, clock=clock)
            svc = SV.Summary(root, cfg, clock=clock, focus=fo)
            mk = conf.markets(cfg, enabled_only=True)
            over_cpu = float(bud["cpu_avg_cores"]) / len(mk) * 1.2

            def step(cpu, seconds):
                end = t[0] + seconds
                while t[0] < end:
                    for v, k in mk:
                        S.write_hb(C.service_name(v, k), {"cpu_cores": cpu, "rss_mb": 50.0, "proc_ms_p99": 5.0}, root, t[0])
                    svc.heartbeat(t[0])
                    fo.step(t[0], force=True)
                    t[0] += 5.0
            step(over_cpu * 0.5, 15 * 60)               # в бюджете: сжатия нет
            self.assertEqual(fo.light_cap, cfg["focus"]["max_light"])
            self.assertNotIn("BUDGET", fo.doc["warn"])
            self.assertEqual(svc.budget["over"], [])
            step(over_cpu, bud["over_min"] * 60 - 10)   # меньше over_min минут подряд: ещё нет
            self.assertEqual(fo.light_cap, cfg["focus"]["max_light"])
            step(over_cpu, 20)
            self.assertIn("cpu_avg_cores", svc.budget["over"])
            self.assertEqual(fo.light_cap, cfg["focus"]["max_light"] - bud["shrink_step"])
            self.assertIn("BUDGET", fo.doc["warn"])
            self.assertEqual(len(fo.doc["light"]), fo.light_cap)
            d = FO.read(root)
            self.assertEqual((d["light_cap"], len(d["light"]), "BUDGET" in d["warn"]), (fo.light_cap, fo.light_cap, True))
            step(over_cpu, 60 * 60)                      # дальше шагами до light_min, не ниже
            self.assertEqual(fo.light_cap, bud["light_min"])
            self.assertEqual(len(fo.doc["deep"]), 13)    # ядро 3 + топ-10: deep не сжимается
            step(over_cpu * 0.5, 30 * 60)                # бюджет вернулся: ярус не растёт сам (возврат — перезапуском сервиса)
            self.assertEqual(fo.light_cap, bud["light_min"])
        finally:
            svc.close()
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- 4. повтор записи живого трафика
@unittest.skipUnless(SLOW, "TB_SLOW_TESTS=1")
class TestRecordedReplay(unittest.TestCase):
    """Запись 600 с: повтор через настоящие сборщики и сводку во временном корне; счётчики процессов ОС раз в секунду.

    Бюджет Requirement 12.3 определён для штатного (живого) потока: его проверяет прогон ×1. Прогон ×4 (условие задачи 23)
    показывает запас: на нём проверяются целостность и отсутствие потерь, а не бюджет."""

    WARM_S = 60.0
    RECOVERY_S = 45.0                 # всплеск и хвост очереди после него — вне установившегося p99

    def _require_rec(self):
        if not REC_DIR or not os.path.isdir(REC_DIR):
            self.skipTest("нет записи живого трафика: TB_CMKT_LOAD_REC=<папка с *.rec задачи 23> (запись 600 с — гигабайты, "
                          "в репозиторий не входит; скрипт записи — вне репозитория, см. отчёт задачи 23)")
        cfg, errs = conf.load(PROJ)
        self.assertEqual(errs, [])
        mk = ["%s_%s" % m for m in conf.markets(cfg, enabled_only=True)]
        miss = [m for m in mk if not os.path.exists(os.path.join(REC_DIR, m + ".rec.idx.json"))]
        if miss:
            self.skipTest("в записи нет рынков: %s" % ", ".join(miss))
        return cfg, mk

    def test_replay_x4_stress_with_burst(self):
        """Условие задачи 23: 4 круга записи на скорости ×4 (объём живого трафика ×4, 600 с записи) и всплеск ×10 на 10 с.

        Цифры — `metrics_x4.json`; проходят только инварианты: ни одной ошибки разбора и ошибок сборщика, отставание подачи
        ограничено. Бюджет на ×4 не проверяется: 4× трафика заведомо больше предела, рассчитанного на живой поток."""
        cfg, mk = self._require_rec()
        met = self._replay(cfg, mk, speed=4.0, loops=4, burst_at=225.0, tag="x4")
        msg = json.dumps(met, ensure_ascii=False)
        self.assertEqual(met["parse_err"], 0, msg)
        self.assertEqual(met["err_n"], 0, msg)
        self.assertLessEqual(met["behind_max_s"], 15.0, msg)

    def test_replay_live_rate_within_budget(self):
        """Бюджет Requirement 12.3 на записи в темпе живого трафика: скорость ×1, одна запись 600 с.

        Критерии: среднее CPU ≤ `budget.cpu_avg_cores`; p99 минутного CPU ≤ `budget.cpu_p99_cores` (Requirement 12.3 — минутного,
        поэтому секундный p99 справочно); сумма RSS ≤ `budget.rss_mb`; задержка обработки p99 ≤ `budget.proc_p99_ms`; входящий
        трафик ≤ `budget.net_mbit`; запись ≤ `budget.disk_write_gb_day`. Всплеск ×10 и целостность на ×4 — отдельный тест."""
        cfg, mk = self._require_rec()
        met = self._replay(cfg, mk, speed=1.0, loops=1, burst_at=None, tag="x1")
        lim = budget()
        msg = json.dumps(met, ensure_ascii=False)
        self.assertEqual(met["parse_err"], 0, msg)
        self.assertEqual(met["err_n"], 0, msg)
        self.assertLessEqual(met["cpu_avg"], lim["cpu_avg_cores"], msg)
        self.assertLessEqual(met["cpu_p99_1min"], lim["cpu_p99_cores"], msg)
        self.assertLessEqual(met["rss_mb_max"], lim["rss_mb"], msg)
        self.assertLessEqual(met["proc_p99_ms"], lim["proc_p99_ms"], msg)
        self.assertLessEqual(met["traffic_mbit"], lim["net_mbit"], msg)
        self.assertLessEqual(met["disk_gb_day"], lim["disk_write_gb_day"], msg)

    def _replay(self, cfg0, mk, speed, loops, burst_at, tag):
        root = tempfile.mkdtemp(prefix="tb_cmkt_replay_")
        out = os.path.join(root, "out")
        os.makedirs(out)
        procs, logs, hb = {}, [], {}
        try:
            self._prep_root(root, cfg0)
            env = dict(os.environ)
            env["PYTHONPATH"] = os.path.join(PROJ, "src")
            env.pop("TB_ROOT", None)

            def spawn(name, args):
                lf = open(os.path.join(out, name + ".log"), "w", encoding="utf-8")
                logs.append(lf)
                procs[name] = subprocess.Popen([sys.executable] + args, env=env, stdout=lf, stderr=subprocess.STDOUT, cwd=root)
            spawn("cmkt", ["-m", "tb.cmkt.service", "run", "--root", root, "--max-seconds", "7200"])
            time.sleep(4.0)
            t_start = time.time()
            for m in mk:
                args = [os.path.abspath(__file__), "replay-child", "--market", m, "--root", root, "--rec", REC_DIR, "--out", out,
                        "--speed", str(speed), "--loops", str(loops)]
                if burst_at is not None:
                    args += ["--burst-at", str(burst_at)]
                spawn(m, args)
            meter = ProcMeter()
            rows = []
            while True:
                t = time.time()
                row = {}
                for n, p in procs.items():
                    if p.poll() is None:
                        s = meter.sample(p)
                        if s is not None:
                            row[n] = s
                rows.append((t, row))
                if not any(p.poll() is None for n, p in procs.items() if n != "cmkt") or t - t_start > 3600:
                    break
                time.sleep(max(0.0, 1.0 - (time.time() - t)))
            try:
                with open(os.path.join(root, "runtime", "platform", "cmkt.hb.json"), encoding="utf-8") as f:
                    hb = json.load(f)
            except (OSError, ValueError):
                pass
        finally:
            for p in procs.values():
                if p.poll() is None:
                    p.terminate()
            for p in procs.values():
                try:
                    p.wait(15)
                except subprocess.TimeoutExpired:
                    p.kill()
            for lf in logs:
                lf.close()
        rep = {}
        for m in mk:
            try:
                with open(os.path.join(out, "replay_%s.json" % m), encoding="utf-8") as f:
                    rep[m] = json.load(f)
            except (OSError, ValueError):
                self.fail("сборщик %s не завершил повтор %s (лог %s)" % (m, tag, os.path.join(out, m + ".log")))
        starts = [r["start"] for r in rep.values() if r.get("start")]
        lo = max(starts) + self.WARM_S
        hi = min(r["start"] + r["wall_s"] for r in rep.values())
        names = ["cmkt"] + mk
        met = series_metrics([r for r in rows if lo <= r[0] <= hi], names)
        self.assertIsNotNone(met, "мало замеров в окне %s" % tag)
        steady = [sum(x) for x in zip(*[(r.get("lat_hist_steady_01ms") or r["lat_hist_01ms"]) for r in rep.values()])]
        burst = [sum(x) for x in zip(*[(r.get("lat_hist_burst_01ms") or [0] * (LAT_BINS + 1)) for r in rep.values()])]

        def q(hist, frac):
            n = sum(hist)
            acc = 0
            for i, x in enumerate(hist):
                acc += x
                if n and acc >= frac * n:
                    return i / 10.0
            return None

        wall = max(r["wall_s"] for r in rep.values())
        sec = {}
        for r in rep.values():
            for k, v in (r.get("sec_bytes") or {}).items():
                sec[int(k)] = sec.get(int(k), 0) + v
        if burst_at is None:
            keep = list(sec.values())
        else:
            lo_b, hi_b = burst_at, burst_at + 10.0 + self.RECOVERY_S
            keep = [v for k, v in sec.items() if not (lo_b <= k < hi_b)]
        steady_s = max(1, len(keep))
        met.update({"mode": tag, "speed": speed, "loops": loops, "burst_at": burst_at, "window_s": round(hi - lo, 1),
                    "proc_p50_ms": q(steady, 0.5), "proc_p99_ms": q(steady, 0.99),
                    "proc_max_ms": max(r.get("lat_max_steady_ms") or 0 for r in rep.values()),
                    "burst_p99_ms": q(burst, 0.99), "burst_max_ms": max(r.get("lat_max_burst_ms") or 0 for r in rep.values()),
                    "behind_max_s": max(r.get("behind_max_s") or 0 for r in rep.values()),
                    "traffic_mbit": sum(keep) * 8 / steady_s / 1e6,
                    "traffic_mbit_run": sum(sec.values()) * 8 / max(1.0, wall) / 1e6,
                    "traffic_mbit_x1": (sum(sec.values()) * 8 / max(1.0, wall) / 1e6) / speed,
                    "agg_tick_ms_p99": hb.get("tick_ms_p99"),
                    "parse_err": sum(r.get("parse_err") or 0 for r in rep.values()),
                    "err_n": sum(r.get("err_n") or 0 for r in rep.values()),
                    "books_bad": sum(r.get("books_bad") or 0 for r in rep.values()),
                    "books_ok": sum(r.get("coins_ok") or 0 for r in rep.values()),
                    "markets": dict((m, dict((k, r.get(k)) for k in ("msgs", "events", "bytes", "behind_max_s", "wall_s", "conns",
                                                                     "coins", "deep", "parse_err", "books_bad", "coins_ok", "bad_deep")))
                                    for m, r in rep.items())})
        p = os.path.join(tempfile.gettempdir(), "tb_cmkt_load_%s.json" % tag)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(met, f, indent=1)
        with open(os.path.join(out, "metrics_%s.json" % tag), "w", encoding="utf-8") as f:
            json.dump(met, f, indent=1)
        sys.stderr.write("\ntest_cmkt_load %s: %s\nmetrics: %s\n" % (
            tag, json.dumps(dict((k, v) for k, v in met.items() if k not in ("per_proc_cpu", "markets")), ensure_ascii=False), p))
        shutil.rmtree(root, ignore_errors=True)
        return met

    def _prep_root(self, root, cfg0):
        """Корень повтора: config/cmkt.json проекта, источники фокуса записи (focus_src/) с обновлённым временем."""
        os.makedirs(os.path.join(root, "config"))
        os.makedirs(os.path.join(root, "runtime"))
        shutil.copy(os.path.join(PROJ, "config", "cmkt.json"), os.path.join(root, "config", "cmkt.json"))
        src = os.path.join(REC_DIR, "focus_src")
        now = time.time()
        with open(os.path.join(src, "heat.json"), encoding="utf-8") as f:
            heat = json.load(f)
        heat["ts_epoch"] = now
        with open(os.path.join(root, "runtime", "heat.json"), "w", encoding="utf-8") as f:
            json.dump(heat, f)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        with open(os.path.join(src, "hot_events.jsonl"), encoding="utf-8") as f, \
                open(os.path.join(root, "runtime", "hot_events.jsonl"), "w", encoding="utf-8") as g:
            for ln in f:
                if ln.strip():
                    r = json.loads(ln)
                    r["ts"] = ts
                    g.write(json.dumps(r) + "\n")
        shutil.copy(os.path.join(src, "exec_snapshot.json"), os.path.join(root, "runtime", "exec_snapshot.json"))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "replay-child":
        sys.exit(replay_child(sys.argv[2:]))
    unittest.main()
