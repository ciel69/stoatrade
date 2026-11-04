"""Сборщик одного рынка сводного рынка: процесс `cmkt@<venue><p|s>` (R1.7, задача 14; Requirements 1.3, 1.4, 4.1–4.4, 5.6, 5.10, 8.2,
11.3, 12.2, 12.3, 18.2; design.md, «Сборщик», «Синхронизация»).

Запуск: `python -m tb.cmkt.collector run --market binance_perp` (принимаются и `binancep`, `cmkt@binancep`). Битый config/cmkt.json,
неизвестный или выключенный рынок, нет адаптера -> код выхода 2 (причина в heartbeat). Окружение процесса чистится от секретов
(`platform.services.labels_secret`); ключей и подписи нет — только публичные каналы через адаптер `tb.cmkt.ex.<venue>`.

Что делает:
- читает `runtime/cmkt/focus.json` (пишет сервис сводки) раз в 2 с; монеты без инструмента на рынке — `nl` (NOT_LISTED);
- планирует подписки (`SubPlanner`, изменения фокуса — дельтой), держит соединения `VenueConn` (пинг, живость, переподключение
  1 → 60 с ±20 %, не больше `conn_per_s` новых соединений в секунду); у биржи с принудительным разрывом (`adapter.conn_max_s`,
  Binance 24 ч) — новое соединение за 10 мин до срока, старое закрывается после первых событий нового, стаканы без RESYNC;
- ведёт по монете: `BookState` (правило яруса адаптера; у стакана со снимком вне потока — частичный стакан на время RESYNC,
  статус `part`), `TapeBuckets` (CVD по `t_rx`), тикер (марк, индекс, bid/ask, funding, OI), историю OI, ликвидации (3600 с),
  `WallTracker` (стены по абсолютной цене уровня, концы touched/pulled/faded), ряд середин по `t_ex − offset`;
- восстановление стаканов: `ResyncQueue` (позиции > планы > ядро > deep > light, не чаще раза в 10 с на монету) и `RestBudget`
  (снимки вне потока из остатка веса; пауза OI `light` при RESYNC больше 4 стаканов); у бирж со снимком по WS — повторная подписка;
- время: `t_rx` до разбора (VenueConn), `ClockOffset` раз в `time_sync_s` по серверному времени, `LagMeter`, `Liveness`;
- раз в секунду на границе `T` публикует `runtime/cmkt/ex/<venue>_<kind>.json` атомарно (`filelock.atomic_write`): только события с
  `t_rx ≤ T` (позже пришедшие ждут следующей секунды); монеты `deep` — каждую секунду, `light` — при переходе через кратную
  `store.light_every_s` (5) секунду; heartbeat `runtime/platform/cmkt@<venue><p|s>.hb.json` раз в 5 с.
- деградация: сбой разбора, события или расчёта одной монеты не роняет процесс (счётчик `err`, стакан монеты -> RESYNC).

Формат `ex/<market>.json` (схема `tb.cmkt_ex/1`, для сводки — задача 15):

    {"schema": "tb.cmkt_ex/1", "mkt": "binance_perp", "venue": "binance", "kind": "perp",
     "T": целая секунда публикации (T_pub), "t_wr": время записи, "lt": T последней публикации монет light, "le": шаг light, с,
     "st": "ok" | "meta" (нет метаданных) | "down" (нет живых соединений) | "err", "err": None | "PARSE" | "HOST_DENIED" | ...,
     "conns": [соединений, живых], "lag_ms": задержка последнего сообщения рынка, "lag_p50": p50 за 30 с, "t_ex": время биржи
     последнего сообщения, "off_ms": offset (часы биржи − локальные), "rtt_ms": RTT лучшего замера, "off_age_s": возраст оценки,
     "liq_partial": bool, "resync": стаканов не в ok, "cols": COLS, "syms": {SYM: [значения по COLS]},
     поля адаптера `doc_extra(kind, T, now)` (только у Coinbase: "fx": {"usdt_usd": середина USDT-USD | None, "age_s": возраст
     котировки на T по t_rx, "src": "ws" | "rest"} — курс для cm_prem_cb_bps, Requirement 10.3)}

    COLS (значение None — нет данных):
      tier "d" | "l";  st — стакан "ok" | "part" (частичный на время RESYNC) | "resync" | "nobook" (стакана нет, Coinbase) | "nl" | "err";
      a — возраст живости соединений монеты, с (None — соединение не подключено); lag — задержка последнего сообщения монеты, мс;
      tx — t_ex последнего сообщения монеты; mid — середина своего стакана (цена за монету; без стакана — bid/ask тикера);
      mx — [[секунда t_ex − offset, середина]] новые точки ряда середин; dp — глубина выдачи, %;
      bands — [[bid_usd, ask_usd] | None] по полосам 0.25, 0.5, 1, 2 % (None — глубина не покрывает полосу или полосы нет в конфиге);
      bk — корзины 25 б.п. до ±200 б.п. [[bid_usd ×8], [ask_usd ×8]] (book.max_bps / bucket_bps) только у deep (иначе None);
      walls — устойчивые стены [[“b”|“a”, цена уровня за монету, usd, возраст_с]] до 3 на сторону;
      pulled — снятые до касания за 15 мин [n_bid, usd_bid, n_ask, usd_ask];
      tape — окна 1/5/15 мин [[buy_usd, sell_usd, cov]]; tk — [mark, index, bid, ask, funding (доля), funding_h, turn24_usd];
      oi — [монеты сейчас, на T−60, T−300, T−900] (свежесть по Requirement 7.3) или None; liq — [long5_usd, short5_usd, long1h_usd,
      short1h_usd, long5_coins, short5_coins] или None (у рынка нет потока ликвидаций); w — оборот 24 ч, USD (вес покрытия);
      fs — секунд в фокусе; q — котировка ("USDT" | "USD").

`market_state(doc, sym)` переводит строку в состояние рынка для `tb.logic.cmkt.features` (без кода включения — его ставит сводка).
"""
import argparse
import asyncio
import inspect
import json
import math
import os
import re
import statistics
import sys
import time
from collections import deque

from tb import paths
from tb.cmkt import adapter as A
from tb.cmkt import conf
from tb.cmkt import focus as FO
from tb.core import filelock
from tb.logic import cmkt as L

SCHEMA = "tb.cmkt_ex/1"
COLS = ("tier", "st", "a", "lag", "tx", "mid", "mx", "dp", "bands", "bk", "walls", "pulled", "tape", "tk", "oi", "liq", "w", "fs", "q")
BAND_LABELS = ("025", "05", "1", "2")
FOCUS_EVERY_S = 2.0
HB_EVERY_S = 5.0
MAINT_S = 0.25
ROTATE_LEAD_S = 600.0                 # новое соединение за 10 мин до принудительного разрыва
ROTATE_WAIT_S = 60.0                  # старое закрывается после первых событий нового (не дольше)
PART_FRESH_S = 5.0                    # частичный стакан годен, если обновлялся не дольше 5 с назад
TICKER_FRESH_S = 120.0
LIQ_KEEP_S = 3600.0
OI_KEEP_S = 1020.0
OI_SAMPLE_S = 5.0
PARSE_ERR_SHARE = 0.05                # > 5 % за минуту — рынок с err=PARSE
MX_KEEP = 6
META_RETRY_S = 30.0
EXIT_CONF = 2
_RE_M1 = re.compile(r"^([a-z][a-z0-9]{0,10})_(perp|spot)$")
_RE_M2 = re.compile(r"^([a-z][a-z0-9]{0,10})([ps])$")


# ---------------------------------------------------------------- имена
def parse_market(s):
    """'binance_perp' | 'binancep' | 'cmkt@binancep' -> ('binance', 'perp'); иначе None."""
    s = str(s or "").strip().lower()
    if s.startswith("cmkt@"):
        s = s[5:]
    m = _RE_M1.match(s)
    if m:
        return m.group(1), m.group(2)
    m = _RE_M2.match(s)
    if m:
        return m.group(1), "perp" if m.group(2) == "p" else "spot"
    return None


def service_name(venue, kind):
    return "cmkt@%s%s" % (venue, kind[0])


def ex_path(mkt, root=None):
    return paths.runtime("cmkt", "ex", "%s.json" % mkt, root=root)


def read_ex(mkt, root=None):
    """ex/<market>.json или None (нет, битый, чужая схема). Не бросает."""
    try:
        with open(ex_path(mkt, root), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("schema") == SCHEMA else None


# ---------------------------------------------------------------- компактные числа
def _g(x, n=8):
    """Число с n значащими цифрами или None."""
    v = L.num(x)
    if v is None:
        return None
    return float("%.*g" % (n, v))


def _i(x):
    v = L.num(x)
    return None if v is None else int(round(v))


def _r(x, nd):
    v = L.num(x)
    return None if v is None else round(v, nd)


# ---------------------------------------------------------------- ресурсы процесса
def rss_mb():
    """Рабочий набор процесса, МБ (Windows — psapi, Linux — /proc), или None."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class _PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]
            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
            if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
                return round(pmc.WorkingSetSize / 1048576.0, 1)
            return None
        with open("/proc/self/statm") as f:
            return round(int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576.0, 1)
    except Exception:                                      # noqa: BLE001 — замер справочный
        return None


def _pct(vals, q):
    v = sorted(vals)
    if not v:
        return None
    return v[min(len(v) - 1, int(math.ceil(q * len(v))) - 1)]


def _accepts(fn, name):
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- состояние монеты
class Coin(object):
    __slots__ = ("sym", "tier", "prio", "inst", "book", "part", "part_t", "tape", "walls", "tk", "tk_t", "oi_now", "oi_t", "oi_hist",
                 "oi_chg", "liq", "lag", "t_ex", "since", "mx", "mx_last", "kinds", "cids", "trade_cids", "book_cids", "created", "err")

    def __init__(self, sym, tier, prio, now, book_cfg):
        self.sym, self.tier, self.prio = sym, tier, prio
        self.inst = None
        self.book = self.part = None
        self.part_t = None
        self.tape = A.TapeBuckets()
        self.walls = L.WallTracker(book_cfg["wall_persist_s"], book_cfg["touch_bps"], book_cfg["pull_drop_share"],
                                   book_cfg["pull_window_s"])
        self.tk, self.tk_t = {}, {}
        self.oi_now = self.oi_t = None
        self.oi_hist = deque()
        self.oi_chg = deque(maxlen=10)
        self.liq = deque()
        self.lag = self.t_ex = None
        self.since = None
        self.mx = deque(maxlen=MX_KEEP)
        self.mx_last = None
        self.kinds, self.cids, self.trade_cids, self.book_cids = set(), set(), set(), set()
        self.created = now
        self.err = 0


TK_FIELDS = ("mark", "index", "bid", "ask", "funding", "funding_h", "turn24_usd")


# ---------------------------------------------------------------- сборщик
class Collector(object):
    """Состояние и логика сборщика одного рынка. Синхронные методы (`apply_focus`, `on_events`, `publish`, `tick`, `resync_jobs`)
    тестируются без сети; `run()` — асинхронный цикл процесса (соединения, расписание, REST в пуле потоков)."""

    def __init__(self, venue, kind, cfg=None, root=None, adapter=None, clock=time.time, allow_plain=False, rng=None, opener=None,
                 urls=None):
        self.cfg = cfg if cfg is not None else conf.defaults()
        self.url_override = dict(urls or {})          # только тесты: {conn_key: ws://127.0.0.1:...}
        self.venue, self.kind = venue, kind
        self.root, self.clock, self.allow_plain, self.rng = root, clock, allow_plain, rng
        self.adapter = adapter if adapter is not None else _load_adapter(venue, self.cfg)
        self.mkt = self.adapter.mkt(kind)
        self.name = service_name(venue, kind)
        self.sync, self.book_cfg, self.lim = self.cfg["sync"], self.cfg["book"], self.cfg["limits"]
        self.light_every = int((self.cfg.get("store") or {}).get("light_every_s", 5))
        self.planner = A.SubPlanner(A.subs_per_conn(self.cfg, venue))
        self.budget = A.RestBudget.from_cfg(self.cfg, venue, kind, n_light=int(self.cfg["focus"]["max_light"]))
        self.http = A.Http(self.cfg, venue, self.budget, clock=clock, allow_plain=allow_plain, opener=opener)
        self.http_snap = A.Http(self.cfg, venue, None, clock=clock, allow_plain=allow_plain, opener=opener)   # вес снимка списан очередью
        self.offset = A.ClockOffset(float(self.sync["time_sync_s"]))
        self.rq = A.ResyncQueue(float(self.book_cfg["resync_min_s"]))
        self.coins = {}
        self.conns, self.tasks, self.cmeta = {}, {}, {}
        self.open_q = deque()
        self.conn_rate = A.RateLimiter(float(self.lim["conn_per_s"]))
        self.rotate_lead_s = ROTATE_LEAD_S
        self.focus_doc = None
        self.meta_ok = False
        self.cut = None                                # события с t_rx > cut ждут публикации своей секунды
        self.deferred = deque()
        self.last_T = self.lt = None
        self.clock_back = 0
        self.err_code = None
        self.counters = {"events": 0, "no_tex": 0, "err": 0, "deferred": 0, "pub": 0, "pub_bytes": 0, "pub_err": 0, "resync_ws": 0,
                         "resync_rest": 0, "rest_fail": 0, "host_denied": 0, "rotations": 0, "drops": 0}
        self.proc_ms = deque(maxlen=4096)
        self.cpu = deque(maxlen=600)                   # (t, process_time) раз в секунду
        self.net = deque(maxlen=120)                   # (t, байт всего)
        self.disk = deque(maxlen=120)                  # (t, байт записи всего)
        self.parse = deque(maxlen=240)                 # (t, msgs, parse_err)
        self.subs_t = {}                               # sym -> время запроса подписки (подписка ≤ 10 с)
        self.sub_lat = deque(maxlen=200)
        self._closed_stats = {"msgs": 0, "bytes": 0, "parse_err": 0, "sub_rejected": 0, "reconnects": 0, "side_unknown": 0, "dups": 0}
        self._jobs = {}
        self._t_focus = self._t_hb = None
        self._poll_t = {}
        self._t_time_fail = self._t_meta_fail = None
        self._has_poll = type(self.adapter).poll is not A.VenueAdapter.poll
        self._liq_partial = self.mkt in tuple(getattr(self.adapter, "liq_partial", ()) or ())
        self._plan_rs = _accepts(self.adapter.plan, "resync")
        self._meta_now = _accepts(self.adapter.load_meta, "now")
        self._meta_kind = _accepts(self.adapter.load_meta, "kind")   # адаптер грузит только инструменты своего рынка
        self._time_kind = _accepts(self.adapter.server_time, "kind")
        self._planned_rs = frozenset()
        self._crc = type(self.adapter).checksum is not A.VenueAdapter.checksum
        self._book_req = getattr(self.adapter, "book_req", None)
        self.api_conn = self.api_task = None           # соединение снимков WS API (Binance perp: urls()["wsapi"])
        mod = sys.modules.get(type(self.adapter).__module__)
        self.sub_req_per_hour = L.num(getattr(self.adapter, "sub_req_per_hour", None) or getattr(mod, "SUB_REQ_PER_HOUR", None))
        self.sub_per_msg = int(getattr(mod, "PER_MSG", 10) or 10)
        self.sub_hist = {}                             # cid -> deque[(t, sub_msgs всего)] за час
        self.pending_subs = deque()                    # (cid, op, [Sub]) — ждут места в пределе запросов подписки в час

    # ------------------------------------------------------------ метаданные
    def load_meta(self, now=None):
        """Загрузка инструментов адаптером (раз в 6 ч). -> True при успехе. Не бросает."""
        now = self.clock() if now is None else now
        try:
            res = self._call_meta(now)
        except Exception:                                   # noqa: BLE001 — сеть, формат, бюджет: повтор позже
            return False
        return self._meta_done(res, now)

    def _call_meta(self, now):
        kw = {"now": now} if self._meta_now else {}
        if self._meta_kind:
            kw["kind"] = self.kind
        return self.adapter.load_meta(self.http, **kw)

    def _server_time(self):
        return self.adapter.server_time(self.http, kind=self.kind) if self._time_kind else self.adapter.server_time(self.http)

    def measure_offset(self):
        """Замер offset (блокирующий; 5 запросов серверного времени); адаптеру — offset_s (возраст событий OKX). -> (offset, rtt)."""
        res = self.offset.measure(self._server_time, self.clock)
        if self.offset.offset is not None:
            self.adapter.offset_s = self.offset.offset
        return res

    def _meta_done(self, res, now):
        if isinstance(res, dict):
            for k, insts in res.items():
                if k == self.kind and isinstance(insts, (list, tuple)):
                    self.adapter.meta.update(self.mkt, insts, now)
        if self.mkt not in self.adapter.meta.t:
            return False
        self.meta_ok = True
        for c in self.coins.values():
            c.inst = self.adapter.meta.get(self.mkt, c.sym)
        return True

    # ------------------------------------------------------------ фокус и подписки
    def _focus_sets(self, doc):
        if isinstance(doc, dict):
            deep = [s for s in doc.get("deep") or () if isinstance(s, str)]
            light = [s for s in doc.get("light") or () if isinstance(s, str) and s not in deep]
            return deep, light, doc.get("why") or {}, doc.get("since") or {}
        core = list(self.cfg["focus"]["core"])                           # нет focus.json: только ядро (сборщик работает и без сводки)
        return core, [], dict((s, "core") for s in core), {}

    def apply_focus(self, doc, now=None):
        """Новый набор монет -> план подписок (дельтой). -> изменения SubPlanner {"add","remove","open","close"} или None (нет метаданных)."""
        now = self.clock() if now is None else now
        self.focus_doc = doc if isinstance(doc, dict) else self.focus_doc
        deep, light, why, since = self._focus_sets(doc if isinstance(doc, dict) else self.focus_doc)
        want = dict([(s, "deep") for s in deep] + [(s, "light") for s in light])
        for sym in [s for s in self.coins if s not in want]:
            self.rq.drop(sym)
            self.subs_t.pop(sym, None)
            self._retire(self.coins.pop(sym))
        for sym, tier in want.items():
            r = why.get(sym)
            prio = r if r in ("pos", "plan", "core") else tier
            c = self.coins.get(sym)
            if c is None:
                c = self.coins[sym] = Coin(sym, tier, prio, now, self.book_cfg)
                c.inst = self.adapter.meta.get(self.mkt, sym) if self.meta_ok else None
                self._new_book(c, now)
                self.subs_t[sym] = now
            elif c.tier != tier:
                c.tier = tier
                self._new_book(c, now)
            c.prio = prio
            t = L.num(since.get(sym))
            c.since = t if t is not None else (c.since if c.since is not None else now)
        if not self.meta_ok:
            return None
        listed = lambda s: self.adapter.meta.listed(self.mkt, s)          # noqa: E731
        d_l = [s for s in deep if listed(s)]
        l_l = [s for s in light if listed(s)]
        rs = self.resync_set()
        self._planned_rs = rs
        try:
            raw = (self.adapter.plan(self.kind, d_l, l_l, resync=sorted(rs)) if self._plan_rs
                   else self.adapter.plan(self.kind, d_l, l_l))
            plan = [s for s in raw or () if self.adapter.valid_topic(s.topic)]
        except Exception:                                    # noqa: BLE001 — сбой плана адаптера: подписки прежние
            self.counters["err"] += 1
            return None
        out = self.planner.update(plan)
        for c in self.coins.values():
            c.kinds, c.cids, c.trade_cids, c.book_cids = set(), set(), set(), set()
        for s in plan:
            cid = self.planner.where.get(s.topic)
            if s.sym == "*" and cid is not None:                 # общий поток (ликвидации всех монет): фильтр по фокусу — здесь
                for c in self.coins.values():
                    if self.adapter.meta.listed(self.mkt, c.sym):
                        c.kinds.add(s.kind)
                        c.cids.add(cid)
                continue
            c = self.coins.get(s.sym)
            if c is None or cid is None:
                continue
            c.kinds.add(s.kind)
            c.cids.add(cid)
            if s.kind == "trades":
                c.trade_cids.add(cid)
            elif s.kind in ("book", "book_part"):
                c.book_cids.add(cid)
        for c in self.coins.values():                        # лента новой монеты: непрерывна, если соединение уже живо
            if c.tape.last_sec is None and c.trade_cids:
                up = any(self.conns.get(cid) is not None and self.conns[cid].connected for cid in c.trade_cids)
                c.tape.set_up(up, now)
        return out

    def resync_set(self):
        """Монеты deep со стаканом по снимку вне потока, который сейчас не в ok: адаптер даёт им частичный канал (plan(..., resync=))."""
        return frozenset(s for s, c in self.coins.items() if c.tier == "deep" and c.book is not None and c.book.rest
                         and c.book.status != "ok")

    def _new_book(self, c, now):
        try:
            c.book = self.adapter.book_state(self.kind, c.sym, c.tier)
        except Exception:                                    # noqa: BLE001
            c.book = A.BookState(self.mkt, c.sym, "replace")
        c.book.since = now
        c.part = None
        if c.tier == "deep":                                 # частичный стакан deep (book_part на время RESYNC) — по правилу яруса light
            try:
                c.part = self.adapter.book_state(self.kind, c.sym, "light")
            except Exception:                                # noqa: BLE001
                c.part = A.BookState(self.mkt, c.sym, "replace")
        c.part_t = None
        if c.book.rest:
            self.rq.request(c.sym, c.prio, now)              # стакан со снимком вне потока: нужен первый снимок

    def _retire(self, c):
        self._closed_stats["side_unknown"] += c.tape.side_unknown
        self._closed_stats["dups"] += c.tape.dups

    # ------------------------------------------------------------ события
    def on_events(self, evs, t_rx):
        """Нормализованные события соединения. Пришедшие позже текущей границы публикации ждут своей секунды (без заглядывания вперёд)."""
        if self.cut is not None and t_rx > self.cut:
            self.deferred.append((t_rx, evs))
            self.counters["deferred"] += 1
            return
        self._apply(evs, t_rx)

    def release(self, upto):
        """Применить отложенные события с t_rx ≤ upto."""
        n = len(self.deferred)
        keep = deque()
        for _ in range(n):
            t, evs = self.deferred.popleft()
            if t <= upto:
                self._apply(evs, t)
            else:
                keep.append((t, evs))
        self.deferred.extend(keep)

    def _apply(self, evs, t_rx):
        off = self.offset.offset or 0.0
        for e in evs:
            c = self.coins.get(getattr(e, "sym", None))
            if c is None:
                continue
            self.counters["events"] += 1
            try:
                if isinstance(e, A.Trade):
                    self._lag(c, e, t_rx, off)
                    c.tape.add(t_rx, e.px * e.coins, e.side, e.tid)
                    c.walls.trade(t_rx, e.px)
                elif isinstance(e, A.BookDelta):
                    self._lag(c, e, t_rx, off)
                    crc = self._crc_fn(e) if self._crc else None
                    if getattr(e, "part", False) and c.part is not None:
                        if c.part.apply_delta(e, t_rx, crc) == "ok":
                            c.part_t = t_rx
                    elif c.book.apply_delta(e, t_rx, crc) == "gap":
                        self.rq.request(c.sym, c.prio, t_rx)
                elif isinstance(e, A.BookSnapshot):
                    self._lag(c, e, t_rx, off)
                    if c.part is not None and (getattr(e, "part", False) or (c.book.rest and not e.rest)):
                        c.part.apply_snapshot(e, t_rx)        # частичный стакан deep на время RESYNC (Binance @depth20)
                        c.part_t = t_rx
                    else:
                        c.book.apply_snapshot(e, t_rx)
                        if c.book.status == "ok":
                            self.rq.done(c.sym)
                elif isinstance(e, A.Ticker):
                    for k in TK_FIELDS:
                        v = getattr(e, k)
                        if v is not None:
                            c.tk[k], c.tk_t[k] = v, t_rx
                    if e.oi_coins is not None:
                        self._oi(c, t_rx, float(e.oi_coins))
                elif isinstance(e, A.Liquidation):
                    if e.liq_side in ("long", "short") and L.num(e.px) and L.num(e.coins):
                        c.liq.append((t_rx, e.liq_side, e.px * e.coins, e.coins))
                        while c.liq and t_rx - c.liq[0][0] > LIQ_KEEP_S:
                            c.liq.popleft()
            except Exception:                                # noqa: BLE001 — сбой одной монеты не роняет процесс
                self.counters["err"] += 1
                c.err += 1
                try:
                    c.book.resync("err", t_rx)
                    self.rq.request(c.sym, c.prio, t_rx)
                except Exception:                            # noqa: BLE001
                    pass
        self.proc_ms.append((self.clock() - t_rx) * 1000.0)

    def _crc_fn(self, d):
        """Контрольная сумма стакана адаптером (OKX); None адаптера — проверка не выполняется (сумма считается совпавшей)."""
        def fn(st):
            v = self.adapter.checksum(st)
            return d.checksum if v is None else v
        return fn

    def _lag(self, c, e, t_rx, off):
        t_ex = e.t_ex
        if t_ex is None:
            self.counters["no_tex"] += 1
            t_ex = t_rx
        c.t_ex = t_ex
        c.lag = (t_rx - (t_ex - off)) * 1000.0

    def _oi(self, c, t, v):
        if c.oi_now is not None and v != c.oi_now:
            c.oi_chg.append(t)
        c.oi_now, c.oi_t = v, t
        if not c.oi_hist or t - c.oi_hist[-1][0] >= OI_SAMPLE_S:
            c.oi_hist.append((t, v))
        while c.oi_hist and t - c.oi_hist[0][0] > OI_KEEP_S:
            c.oi_hist.popleft()

    # ------------------------------------------------------------ соединения (вызовы из цикла)
    def on_status(self, cid, conn, ev, info):
        now = self.clock()
        m = self.cmeta.setdefault(cid, {})
        if conn is not self.conns.get(cid):                  # новое соединение смены (до переключения): без RESYNC
            return
        if ev in ("connect", "reconnect"):
            if ev == "reconnect" and m.get("up", False):
                self._conn_down(cid, (info or {}).get("t_last") or now)
            m["up"], m["t_up"] = True, now
            for c in self.coins.values():
                if cid in c.trade_cids:
                    c.tape.set_up(True, now)
        elif ev == "rejected":
            pass

    def _conn_down(self, cid, t_down):
        """Обрыв соединения: стаканы его монет -> RESYNC, лента — разрыв с момента обрыва (Requirement 12.2, 5.10)."""
        now = self.clock()
        self.counters["drops"] += 1
        for c in self.coins.values():
            if cid in c.trade_cids:
                c.tape.set_up(False, t_down)
                for s in range(int(math.ceil(t_down)), int(math.ceil(now)) + 1):
                    i = c.tape._slot(s)
                    if i is not None:
                        c.tape.cont[i] = 0
            if cid in c.book_cids and c.book is not None:
                c.book.resync("reconnect", now)
                if c.book.rest:
                    self.rq.request(c.sym, c.prio, now)
        m = self.cmeta.setdefault(cid, {})
        m["up"] = False

    # ------------------------------------------------------------ обслуживание
    def tick(self, now=None):
        """Проверки раз в 0.25 с: обрыв соединений, перекрёст стакана, долгий RESYNC, пауза OI light, доля ошибок разбора."""
        now = self.clock() if now is None else now
        for cid, conn in list(self.conns.items()):
            m = self.cmeta.setdefault(cid, {})
            if m.get("up") and not conn.connected:
                self._conn_down(cid, now)
        n_rest = 0
        gap = float(self.book_cfg["resync_min_s"])
        for c in self.coins.values():
            b = c.book
            if b is None or not ("book" in c.kinds or "book_part" in c.kinds):
                continue
            b.tick(now)
            if b.status != "ok":
                if b.rest:
                    n_rest += 1
                t0 = b.since if b.since is not None else c.created
                if now - t0 >= gap and c.sym not in self.rq.req:
                    self.rq.request(c.sym, c.prio, now)
        self.budget.set_resync(n_rest)
        st = self.conn_stats()
        self.parse.append((now, st["msgs"], st["parse_err"]))
        old = next((r for r in self.parse if now - r[0] <= 60.0), None)
        if old is not None:
            dm, dp = st["msgs"] - old[1], st["parse_err"] - old[2]
            self.err_code = "PARSE" if dm >= 20 and dp > PARSE_ERR_SHARE * dm else (None if self.err_code == "PARSE" else self.err_code)

    def resync_jobs(self, now=None):
        """Следующее восстановление стакана: ("ws", sym, None) — повторная подписка; ("rest", sym, limit) — снимок вне потока
        (вес уже списан из остатка RestBudget); None — нечего, рано или нет веса."""
        now = self.clock() if now is None else now
        nxt = self.rq.next(now, self.budget, lambda p: self.adapter.snapshot_cost(self.kind, p))
        if nxt is None:
            return None
        sym, prio, limit = nxt
        c = self.coins.get(sym)
        if c is None:
            self.rq.drop(sym)
            return None
        w = self.adapter.snapshot_cost(self.kind, prio)[1]
        if not w and not c.book.rest:
            self.rq.done(sym)
            return ("ws", sym, None)
        return ("rest", sym, limit)

    def resub_texts(self, sym):
        """-> {cid: [тексты]} повторной подписки стакана монеты."""
        out = {}
        for cid in sorted(self.coins[sym].cids):
            subs = [s for s in self.planner.subs(cid) if s.sym == sym]
            if any(s.kind in ("book", "book_part") for s in subs):
                out[cid] = self.adapter.resub(self.kind, sym, subs)
        return out

    def rest_snapshot(self, sym, limit):
        """Снимок вне потока (блокирующий REST/WS API адаптера). -> BookSnapshot | None. Не бросает."""
        try:
            return self.adapter.book_rest(self.http_snap, self.kind, sym, limit)
        except Exception:                                    # noqa: BLE001
            return None

    def apply_rest(self, sym, snap, now=None):
        now = self.clock() if now is None else now
        c = self.coins.get(sym)
        if c is None:
            return False
        if not isinstance(snap, A.BookSnapshot):
            self.counters["rest_fail"] += 1
            return False
        if not snap.rest:
            snap = snap._replace(rest=True)
        c.book.apply_snapshot(snap, now)
        ok = c.book.status == "ok"
        self.rq.done(sym, ok)
        self.counters["resync_rest"] += 1
        return ok

    # ------------------------------------------------------------ расчёт и публикация
    def _book_now(self, c, now):
        b = c.book
        if b is not None and b.status == "ok":
            return b, "ok"
        if c.part is not None and c.part.status == "ok" and c.part_t is not None and now - c.part_t <= PART_FRESH_S:
            return c.part, "part"
        return None, "resync"

    def _conn_age(self, c, now):
        ages = []
        for cid in c.cids:
            conn = self.conns.get(cid)
            if conn is None or not conn.connected:
                return None
            a = conn.live.age(now)
            if a is None:
                return None
            ages.append(a)
        return round(max(ages), 1) if ages else None

    def _row(self, c, T, now):
        inst = c.inst
        quote = inst.quote if inst is not None else "USDT"
        tier = "d" if c.tier == "deep" else "l"
        fs = int(max(0.0, T - c.since)) if c.since is not None else 0
        if inst is None or not self.adapter.meta.listed(self.mkt, c.sym):
            return [tier, "nl", None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, fs, quote]
        has_book = "book" in c.kinds or "book_part" in c.kinds
        cs = inst.contract_size if inst.kind == "perp" else 1.0
        cm = inst.coin_mult
        mid = dp = bands = bk = None
        walls, pulled = [], [0, 0, 0, 0]
        if has_book:
            book, st = self._book_now(c, now)
            if book is not None:
                bids, asks = book.levels(cs or 1.0, cm)
                mid = L.book_mid(bids, asks)
                bb = L.coin_px(book.bound_bid, cm) if book.bound_bid is not None else None
                ba = L.coin_px(book.bound_ask, cm) if book.bound_ask is not None else None
                dp = L.depth_pct(bids, asks, mid, bb, ba)
                bd = L.bands(bids, asks, mid, dp, self.book_cfg["bands_pct"])
                bands = [[_i(bd[k][0]), _i(bd[k][1])] if bd.get(k) else None for k in BAND_LABELS]
                if c.tier == "deep":
                    k = L.buckets(bids, asks, mid, self.book_cfg["bucket_bps"], self.book_cfg["max_bps"])
                    bk = [[_i(x) for x in k["bid"]], [_i(x) for x in k["ask"]]]
                cands = L.wall_candidates(bids, asks, mid, self.book_cfg["wall_min_market_usd"], self.book_cfg["wall_mult"])
                lv = {}

                def lookup(side, px):
                    if not lv:
                        lv.update((("bid", p), p * a) for p, a in bids)
                        lv.update((("ask", p), p * a) for p, a in asks)
                    return lv.get((side, px), 0.0)
                c.walls.update(T, cands, lookup)
                if st == "ok" and book.t_ex is not None and mid:
                    sec = int(math.floor(book.t_ex - (self.offset.offset or 0.0)))
                    if c.mx_last is None or sec > c.mx_last:
                        c.mx.append([sec, _g(mid)])
                        c.mx_last = sec
            walls = [["b" if w["side"] == "bid" else "a", _g(w["px"]), _i(w["usd"]), int(w["age_s"])] for w in c.walls.stable(T)]
            e = c.walls.ends(T)
            pulled = [e["bid"][0], _i(e["bid"][1]), e["ask"][0], _i(e["ask"][1])]
        else:
            st = "nobook"
        tk = []
        for k in TK_FIELDS:
            v = c.tk.get(k) if k in c.tk and T - c.tk_t[k] <= TICKER_FRESH_S else None
            tk.append(_r(v, 3) if k == "funding_h" else _g(v, 6 if k == "funding" else 8))
        if mid is None and tk[2] and tk[3] and tk[2] < tk[3]:
            mid = (tk[2] + tk[3]) / 2.0                     # без стакана (Coinbase) — середина тикера
        c.tape.tick(T)
        tape = []
        for _, w in L.WINDOWS:
            b, s, cov = c.tape.window(T, w)
            tape.append([_i(b), _i(s), round(cov, 3)])
        oi = self._oi_row(c, T)
        liq = None
        if "liq" in c.kinds:
            l5, l1, c5 = [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]
            for t, side, usd, coins in c.liq:
                if t > T or T - t > LIQ_KEEP_S:
                    continue
                j = 0 if side == "long" else 1
                l1[j] += usd
                if T - t <= 300:
                    l5[j] += usd
                    c5[j] += coins
            liq = [_i(l5[0]), _i(l5[1]), _i(l1[0]), _i(l1[1]), _g(c5[0], 9), _g(c5[1], 9)]
        w = L.num(c.tk.get("turn24_usd"))
        if w is None:
            w = (tape[2][0] or 0) + (tape[2][1] or 0)
            w = w * 96.0 if w > 0 else None                  # оценка по 15 мин ленты, пока тикер не дал оборот
        mx = [list(x) for x in c.mx]
        c.mx.clear()
        return [tier, st, self._conn_age(c, now), _i(c.lag), _r(c.t_ex, 3), _g(mid), mx or None, _r(dp, 3), bands, bk, walls or None,
                pulled, tape, tk, oi, liq, _i(w), fs, quote]

    def _oi_row(self, c, T):
        if c.oi_now is None or self.kind != "perp":
            return None
        iv = L.num((getattr(self.adapter, "oi_every_s", None) or {}).get(self.kind))     # интервал биржи (замер задачи 2)
        if iv is None and self._has_poll:
            iv = float(self.lim["oi_poll_s"]["deep" if c.tier == "deep" else "light"])  # OI по REST с шагом конфига
        if iv is None:
            ts = list(c.oi_chg)
            gaps = [b - a for a, b in zip(ts, ts[1:]) if b > a]
            iv = min(60.0, statistics.median(gaps)) if gaps else 10.0
        now = c.oi_now if T - c.oi_t <= max(1.5 * iv, 5.0) else None
        out = [_g(now, 10)]
        tol = max(iv, 10.0)
        for _, w in L.WINDOWS:
            tgt = T - w
            best = None
            for t, v in c.oi_hist:
                d = abs(t - tgt)
                if t <= T and d <= tol and (best is None or d < best[0]):
                    best = (d, v)
            out.append(_g(best[1], 10) if best else None)
        return out

    def conn_stats(self):
        st = dict(self._closed_stats)
        for conn in self.conns.values():
            for k in ("msgs", "bytes", "parse_err", "sub_rejected", "reconnects"):
                st[k] += conn.stats.get(k, 0)
        return st

    def _market_lag(self):
        best = None
        for conn in self.conns.values():
            if conn._t_last is not None and conn.lag.last_ms is not None and (best is None or conn._t_last > best._t_last):
                best = conn
        if best is None:
            return None, None
        return best.lag.last_ms, best.lag.p50()

    def market_status(self, now):
        if self.err_code:
            return "err"
        if not self.meta_ok:
            return "meta"
        alive = [c for c in self.conns.values() if c.alive(now)]
        if self.conns and not alive:
            return "down"
        return "ok"

    def publish(self, T, now=None):
        """Состояние рынка на секунду T -> ex/<market>.json атомарно. -> документ или None (T не новее прошлой публикации)."""
        now = self.clock() if now is None else now
        T = int(T)
        if self.last_T is not None and T <= self.last_T:
            self.clock_back += 1
            return None
        le = max(1, self.light_every)
        with_light = self.lt is None or T // le != self.lt // le
        rows = {}
        for sym, c in self.coins.items():
            if c.tier == "light" and not with_light:
                continue
            try:
                rows[sym] = self._row(c, T, now)
            except Exception:                                # noqa: BLE001 — сбой расчёта монеты: строка с ошибкой
                self.counters["err"] += 1
                c.err += 1
                rows[sym] = ["d" if c.tier == "deep" else "l", "err"] + [None] * (len(COLS) - 2)
        if with_light:
            self.lt = T
        lag, p50 = self._market_lag()
        rtt, age = self.offset.quality(now)
        t_ex = max((c.t_ex for c in self.coins.values() if c.t_ex is not None), default=None)
        alive = sum(1 for c in self.conns.values() if c.alive(now))
        doc = {"schema": SCHEMA, "mkt": self.mkt, "venue": self.venue, "kind": self.kind, "T": T, "t_wr": round(now, 3), "lt": self.lt,
               "le": le, "st": self.market_status(now), "err": self.err_code, "conns": [len(self.conns), alive], "lag_ms": _i(lag),
               "lag_p50": _i(p50), "t_ex": _r(t_ex, 3),
               "off_ms": _i(self.offset.offset * 1000.0) if self.offset.offset is not None else None, "rtt_ms": _r(rtt, 1),
               "off_age_s": _i(age), "liq_partial": self._liq_partial,
               "resync": sum(1 for c in self.coins.values() if c.book is not None and c.book.status != "ok"
                             and ("book" in c.kinds or "book_part" in c.kinds)),
               "cols": list(COLS), "syms": rows}
        extra = getattr(self.adapter, "doc_extra", None)              # поля рынка вне монет (Coinbase: "fx" — курс USDT-USD на T)
        if extra is not None:
            try:
                for k, v in (extra(self.kind, T, now) or {}).items():
                    doc.setdefault(k, v)
            except Exception:                                # noqa: BLE001
                self.counters["err"] += 1
        try:
            text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            filelock.atomic_write(ex_path(self.mkt, self.root), text)
        except (OSError, ValueError):
            self.counters["pub_err"] += 1
            return doc
        self.last_T = T
        self.counters["pub"] += 1
        self.counters["pub_bytes"] += len(text.encode("utf-8"))
        return doc

    # ------------------------------------------------------------ heartbeat
    def heartbeat(self, now=None, extra=None):
        now = self.clock() if now is None else now
        st = self.conn_stats()
        self.cpu.append((now, time.process_time()))
        self.net.append((now, st["bytes"]))
        self.disk.append((now, self.counters["pub_bytes"]))

        def rate(q, span=60.0):
            old = next((r for r in q if now - r[0] <= span), None)
            if old is None or now - old[0] < 1.0:
                return None
            return (q[-1][1] - old[1]) / (now - old[0])
        cpu = rate(self.cpu)
        net = rate(self.net)
        disk = rate(self.disk)
        sec = [(b[1] - a[1]) / (b[0] - a[0]) for a, b in zip(self.cpu, list(self.cpu)[1:]) if b[0] - a[0] > 0 and now - b[0] <= 600]
        side_unknown = self._closed_stats["side_unknown"] + sum(c.tape.side_unknown for c in self.coins.values())
        dups = self._closed_stats["dups"] + sum(c.tape.dups for c in self.coins.values())
        subs = self.planner.counts()
        hb = {"market": self.mkt, "cfg_hash": conf.config_hash(self.cfg)[:16], "state": self.market_status(now), "err": self.err_code,
              "T": self.last_T, "coins": {"deep": sum(1 for c in self.coins.values() if c.tier == "deep"),
                                          "light": sum(1 for c in self.coins.values() if c.tier == "light")},
              "subs": subs["topics"], "conns": subs["conns"], "alive": sum(1 for c in self.conns.values() if c.alive(now)),
              "reconnects": st["reconnects"], "drops": self.counters["drops"], "rotations": self.counters["rotations"],
              "resyncs": sum(c.book.resyncs for c in self.coins.values() if c.book is not None),
              "resync_pending": self.rq.pending(), "resync_rest": self.counters["resync_rest"], "resync_ws": self.counters["resync_ws"],
              "rest": self.budget.used(now), "msgs": st["msgs"], "parse_err": st["parse_err"], "sub_rejected": st["sub_rejected"],
              "no_tex": self.counters["no_tex"], "side_unknown": side_unknown, "dups": dups, "err_n": self.counters["err"],
              "clock_back": self.clock_back, "deferred": self.counters["deferred"],
              "off_ms": _i(self.offset.offset * 1000.0) if self.offset.offset is not None else None,
              "cpu_cores": _r(cpu, 3), "cpu_p99_cores": _r(_pct(sec, 0.99), 3), "rss_mb": rss_mb(),
              "proc_ms_p50": _r(_pct(self.proc_ms, 0.5), 2), "proc_ms_p99": _r(_pct(self.proc_ms, 0.99), 2),
              "net_mbit": _r(net * 8 / 1e6, 3) if net is not None else None, "disk_kb_s": _r(disk / 1024.0, 2) if disk is not None else None,
              "disk_gb_day": _r(disk * 86400 / 1e9, 3) if disk is not None else None,
              "sub_lat_s_max": _r(max(self.sub_lat), 2) if self.sub_lat else None, "pub": self.counters["pub"],
              "adapter": dict(getattr(self.adapter, "stats", None) or {}), "liq_partial": self._liq_partial,
              "oi_every_s": (getattr(self.adapter, "oi_every_s", None) or {}).get(self.kind),
              "sub_req_hour": dict((cid, self._sub_sent(cid, now)) for cid in self.conns) if self.sub_req_per_hour else None,
              "sub_pending": len(self.pending_subs), "api_conn": bool(self.api_conn is not None and self.api_conn.connected)}
        hb.update(extra or {})
        write_hb(self.name, hb, self.root, now)
        return hb

    # ------------------------------------------------------------ асинхронный цикл процесса
    def _job(self, name, fn, done):
        """Блокирующий вызов в пуле потоков; один на имя; done(result) — в потоке цикла."""
        if name in self._jobs:
            return False
        loop = asyncio.get_event_loop()
        fut = loop.run_in_executor(None, fn)
        self._jobs[name] = fut

        def cb(f):
            self._jobs.pop(name, None)
            try:
                r = f.result()
            except Exception:                                # noqa: BLE001
                r = None
            try:
                done(r)
            except Exception:                                # noqa: BLE001
                self.counters["err"] += 1
        fut.add_done_callback(cb)
        return True

    def _make_conn(self, cid, subs):
        key = cid.rpartition("#")[0]
        url = self.url_override.get(key) or (self.adapter.urls(self.kind) or {}).get(key)
        if not url:
            self.counters["err"] += 1
            return None
        holder = {}

        def status(ev, info):
            self.on_status(cid, holder.get("c"), ev, info)
        try:
            conn = A.VenueConn(self.adapter, self.kind, cid, url, subs, on_events=self.on_events, on_status=status, cfg=self.cfg,
                               clock=self.clock, allow_plain=self.allow_plain, rng=self.rng, offset=self.offset)
        except A.HostDenied:
            self.counters["host_denied"] += 1
            self.err_code = A.HOST_DENIED
            return None
        holder["c"] = conn
        return conn

    def _start(self, cid, conn):
        self.conns[cid] = conn
        self.cmeta[cid] = {"up": False, "t_open": self.clock()}
        self.tasks[cid] = asyncio.ensure_future(self._run_conn(conn))

    async def _run_conn(self, conn):
        try:
            await conn.run()
        except asyncio.CancelledError:
            raise
        except Exception:                                    # noqa: BLE001 — соединение упало целиком: перезапуск обслуживанием
            self.counters["err"] += 1

    async def _close(self, cid, conn=None, task=None):
        """Закрыть соединение cid (по плану) или переданное старое соединение смены (conn, task; состояние cid не трогается)."""
        if conn is None:
            conn = self.conns.pop(cid, None)
            task = self.tasks.pop(cid, None)
            m = self.cmeta.pop(cid, None) or {}
            self.sub_hist.pop(cid, None)
            rot = m.get("rot")
            if rot is not None:
                try:
                    await rot.close()
                except Exception:                            # noqa: BLE001
                    pass
                if m.get("rot_task") is not None:
                    m["rot_task"].cancel()
        if conn is not None:
            st = conn.stats
            for k in ("msgs", "bytes", "parse_err", "sub_rejected", "reconnects"):
                self._closed_stats[k] += st.get(k, 0)
            try:
                await conn.close()
            except Exception:                                # noqa: BLE001
                pass
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):      # noqa: BLE001
                pass

    async def do_actions(self, out):
        """Изменения SubPlanner -> соединения: закрыть пустые, отписать, подписать, открыть новые (в очередь с темпом conn_per_s)."""
        if not out:
            return
        for cid in out["close"]:
            if cid in self.open_q:
                self.open_q.remove(cid)
            await self._close(cid)
        for cid, subs in out["remove"].items():
            if cid in self.conns and cid not in out["close"]:
                self._sub_op(cid, "unsubscribe", subs)
        for cid, subs in out["add"].items():
            if cid in self.conns:
                self._sub_op(cid, "subscribe", subs)
        for cid in out["open"]:
            if cid not in self.conns and cid not in self.open_q:
                self.open_q.append(cid)

    # --- предел запросов подписки в час на соединение (OKX 480, SUB_REQ_PER_HOUR адаптера)
    def _sub_sent(self, cid, now):
        """Запросов подписки соединения за последний час (по счётчику VenueConn.stats["sub_msgs"])."""
        conn = self.conns.get(cid)
        if conn is None:
            return 0
        h = self.sub_hist.setdefault(cid, deque([(now, 0)]))
        tot = conn.stats.get("sub_msgs", 0)
        if h[-1][1] != tot or now - h[-1][0] >= 60.0:
            h.append((now, tot))
        while len(h) > 1 and now - h[1][0] >= 3600.0:
            h.popleft()
        return tot - h[0][1]

    def _sub_room(self, cid, n, now=None):
        if not self.sub_req_per_hour:
            return True
        now = self.clock() if now is None else now
        conn = self.conns.get(cid)
        queued = len(conn._queue) if conn is not None else 0
        return self._sub_sent(cid, now) + queued + n <= self.sub_req_per_hour

    def _sub_op(self, cid, op, subs, now=None):
        """Подписка/отписка на соединении в пределе запросов в час; иначе — в очередь ожидания (повтор раз в 0.25 с)."""
        n = max(1, int(math.ceil(len(subs) / float(self.sub_per_msg))))
        if not self._sub_room(cid, n, now):
            self.pending_subs.append((cid, op, list(subs)))
            return False
        conn = self.conns[cid]
        conn.subscribe(subs) if op == "subscribe" else conn.unsubscribe(subs)
        return True

    def flush_subs(self, now=None):
        for _ in range(len(self.pending_subs)):
            cid, op, subs = self.pending_subs.popleft()
            if cid not in self.conns:
                continue
            # план мог измениться, пока запрос ждал: подписываем то, что всё ещё в плане соединения, отписываем то, чего в нём уже нет
            subs = [s for s in subs if (self.planner.where.get(s.topic) == cid) == (op == "subscribe")]
            if subs:
                self._sub_op(cid, op, subs, now)

    # --- снимки WS API (Binance perp)
    def _api(self, key):
        """Соединение снимков WS API (без подписок); None — ещё не подключено (снимок пойдёт запасным REST)."""
        if self.api_conn is None:
            url = self.url_override.get(key) or (self.adapter.urls(self.kind) or {}).get(key)
            if not url:
                return None
            try:
                self.api_conn = A.VenueConn(self.adapter, self.kind, "api#%s" % key, url, [], on_events=self.on_events, cfg=self.cfg,
                                            clock=self.clock, allow_plain=self.allow_plain, rng=self.rng, offset=self.offset)
            except A.HostDenied:
                self.counters["host_denied"] += 1
                return None
            self.api_task = asyncio.ensure_future(self._run_conn(self.api_conn))
            return None
        return self.api_conn if self.api_conn.connected else None

    async def _open_due(self, now):
        while self.open_q and self.conn_rate.take(now):
            cid = self.open_q.popleft()
            subs = self.planner.subs(cid)
            if not subs:
                continue
            conn = self._make_conn(cid, subs)
            if conn is not None:
                self._start(cid, conn)

    async def _rotate(self, now):
        """Смена соединения до принудительного разрыва биржи (adapter.conn_max_s)."""
        cmax = L.num(getattr(self.adapter, "conn_max_s", None))
        if not cmax:
            return
        for cid in list(self.conns):
            m = self.cmeta.get(cid) or {}
            rot = m.get("rot")
            if rot is None:
                if m.get("up") and m.get("t_up") is not None and now - m["t_up"] >= cmax - self.rotate_lead_s:
                    new = self._make_conn(cid, self.planner.subs(cid))
                    if new is not None:
                        m["rot"], m["rot_t"] = new, now
                        m["rot_task"] = asyncio.ensure_future(self._run_conn(new))
                continue
            if rot.stats.get("events", 0) > 0 or now - m["rot_t"] >= ROTATE_WAIT_S:
                old, old_task = self.conns[cid], self.tasks.get(cid)
                self.conns[cid], self.tasks[cid] = rot, m["rot_task"]
                self.cmeta[cid] = {"up": rot.connected, "t_up": now, "t_open": m["rot_t"]}
                self.sub_hist.pop(cid, None)
                self.counters["rotations"] += 1
                await self._close(cid, old, old_task)

    def _sub_latency(self, now):
        for sym, t in list(self.subs_t.items()):
            c = self.coins.get(sym)
            if c is None:
                del self.subs_t[sym]
                continue
            if c.t_ex is not None or (c.tk_t and max(c.tk_t.values()) >= t):
                self.sub_lat.append(now - t)
                del self.subs_t[sym]

    def _maint_jobs(self, now):
        if ((not self.meta_ok or self.adapter.meta.due(self.mkt, now)) and "meta" not in self._jobs
                and (self._t_meta_fail is None or now - self._t_meta_fail >= META_RETRY_S)):
            def meta_done(res):
                ok = res is not None and self._meta_done(res, self.clock())
                if ok:
                    self._t_focus = None                     # пересчитать подписки
                else:
                    self._t_meta_fail = self.clock()
            self._job("meta", lambda: self._call_meta(now), meta_done)
        if self.offset.due(now) and (self._t_time_fail is None or now - self._t_time_fail >= float(self.sync["time_sync_s"])):
            def time_done(res):
                if not res or res[0] is None:
                    self._t_time_fail = self.clock()
            self._job("time", self.measure_offset, time_done)
        if self._has_poll and self.meta_ok and "poll" not in self._jobs:
            ps = self.lim["oi_poll_s"]
            due = []
            for c in self.coins.values():
                if not self.adapter.meta.listed(self.mkt, c.sym):
                    continue
                every = float(ps["deep"] if c.tier == "deep" else ps["light"])
                if c.tier == "light" and not self.budget.oi_allowed("light"):
                    continue
                if now - self._poll_t.get(c.sym, -1e18) >= every:
                    due.append(c.sym)
            if due:
                for s in due:
                    self._poll_t[s] = now

                def poll_done(evs):
                    if evs:
                        self.on_events(list(evs), self.clock())
                self._job("poll", lambda: self.adapter.poll(self.http, self.kind, due, now), poll_done)
        side = getattr(self.adapter, "side_poll", None)                # REST рынка вне монет фокуса (курс USDT-USD Coinbase)
        if side is not None and self.meta_ok and "side" not in self._jobs:
            side_due = getattr(self.adapter, "side_due", None)
            if side_due is None or side_due(self.kind, now):
                def side_done(evs):
                    if evs:
                        self.on_events(list(evs), self.clock())
                self._job("side", lambda: side(self.http, self.kind, now), side_done)
        job = None if "snap" in self._jobs else self.resync_jobs(now)
        if job is not None:
            mode, sym, limit = job
            if mode == "ws":
                for cid, texts in self.resub_texts(sym).items():
                    if cid in self.conns and texts and self._sub_room(cid, len(texts), now):
                        self.conns[cid].send_later(texts)
                        self.counters["resync_ws"] += 1
            else:
                req = None
                if self._book_req is not None:
                    try:
                        req = self._book_req(self.kind, sym, limit)
                    except Exception:                        # noqa: BLE001
                        req = None
                api = self._api(req[0]) if req else None
                if api is not None:
                    api.send_later([req[1]])                 # снимок WS API: ответ придёт событием BookSnapshot(rest=True)
                    self.counters["resync_wsapi"] = self.counters.get("resync_wsapi", 0) + 1
                else:
                    self._job("snap", lambda: self.rest_snapshot(sym, limit), lambda snap: self.apply_rest(sym, snap))

    async def run(self, stop=None, max_seconds=0.0):
        """Цикл процесса: метаданные, фокус раз в 2 с, публикация на границе секунды, обслуживание раз в 0.25 с, heartbeat раз в 5 с."""
        stop = stop or asyncio.Event()
        t_end = self.clock() + max_seconds if max_seconds else None
        self.cut = math.floor(self.clock()) + 1
        self.heartbeat(self.clock())
        try:
            while not stop.is_set():
                now = self.clock()
                if t_end is not None and now >= t_end:
                    break
                T = int(math.floor(now))
                if self.last_T is None or T > self.last_T:
                    self.release(T)
                    self.publish(T, now)
                    self.cut = T + 1
                    self.release(self.cut)
                elif T < self.last_T:
                    self.cut = self.last_T + 1                # шаг часов назад: не публикуем, пока T не обгонит прошлую
                if self._t_focus is None or now - self._t_focus >= FOCUS_EVERY_S or now < self._t_focus:
                    self._t_focus = now
                    out = self.apply_focus(FO.read(self.root), now)
                    await self.do_actions(out)
                self.tick(now)
                if self._plan_rs and self.meta_ok and self.resync_set() != self._planned_rs:
                    await self.do_actions(self.apply_focus(self.focus_doc, now))    # частичный канал монетам в RESYNC
                await self._open_due(now)
                self.flush_subs(now)
                await self._rotate(now)
                self._maint_jobs(now)
                self._sub_latency(now)
                if self._t_hb is None or now - self._t_hb >= HB_EVERY_S or now < self._t_hb:
                    self._t_hb = now
                    self.heartbeat(now)
                nxt = math.floor(self.clock()) + 1
                delay = max(0.0, min(MAINT_S, nxt - self.clock() + 0.002))
                try:
                    await asyncio.wait_for(stop.wait(), delay)
                except asyncio.TimeoutError:
                    pass
        finally:
            for cid in list(self.conns):
                await self._close(cid)
            if self.api_conn is not None:
                await self._close("api", self.api_conn, self.api_task)
                self.api_conn = self.api_task = None
            self.heartbeat(self.clock(), {"stopped": True})


# ---------------------------------------------------------------- состояние для сводки (задача 15)
def market_state(doc, sym):
    """Строка монеты из ex/<market>.json -> состояние рынка по формату tb.logic.cmkt.features (поле code ставит сводка; здесь None).
    Добавлены служебные поля st, a, lag, tier для правил включения. None — монеты нет в документе."""
    if not isinstance(doc, dict):
        return None
    row = (doc.get("syms") or {}).get(sym)
    if not isinstance(row, list):
        return None
    cols = doc.get("cols") or COLS
    r = dict(zip(cols, row))
    out = {"kind": doc.get("kind"), "quote": r.get("q") or "USDT", "code": None, "w": r.get("w"), "has_book": r.get("st") != "nobook",
           "mid": r.get("mid"), "st": r.get("st"), "a": r.get("a"), "lag": r.get("lag"), "tier": "deep" if r.get("tier") == "d" else "light"}
    bands = r.get("bands")
    if isinstance(bands, list):
        out["bands"] = dict((k, v) for k, v in zip(BAND_LABELS, bands))
    bk = r.get("bk")
    if isinstance(bk, list) and len(bk) == 2:
        out["buckets"] = {"bid": bk[0], "ask": bk[1]}
    out["walls"] = [{"side": "bid" if w[0] == "b" else "ask", "px": w[1], "usd": w[2], "age_s": w[3]} for w in r.get("walls") or ()]
    p = r.get("pulled")
    if isinstance(p, list) and len(p) == 4:
        out["pulled"] = {"bid": [p[0], p[1]], "ask": [p[2], p[3]]}
    t = r.get("tape")
    if isinstance(t, list):
        out["tape"] = dict((lab, v) for (lab, _), v in zip(L.WINDOWS, t))
    tk = r.get("tk")
    if isinstance(tk, list) and len(tk) == len(TK_FIELDS):
        d = dict(zip(TK_FIELDS, tk))
        out.update({"mark": d["mark"] or (r.get("mid") if doc.get("kind") == "perp" else None), "fund": d["funding"],
                    "fund_h": d["funding_h"], "index": d["index"]})
        if out["w"] is None and d["turn24_usd"] is not None:
            out["w"] = d["turn24_usd"]
    oi = r.get("oi")
    if isinstance(oi, list) and len(oi) == 4:
        out["oi"] = {"now": oi[0], "1m": oi[1], "5m": oi[2], "15m": oi[3]}
    lq = r.get("liq")
    out["liq"] = {"has": isinstance(lq, list), "partial": bool(doc.get("liq_partial"))}
    if isinstance(lq, list) and len(lq) == 6:
        out["liq"].update({"5m": [lq[0], lq[1]], "1h": [lq[2], lq[3]], "5m_coins": [lq[4], lq[5]]})
    return out


# ---------------------------------------------------------------- процесс
def write_hb(name, extra, root=None, now=None):
    """Heartbeat сервиса в формате tb.platform.services.write_hb (runtime/platform/<name>.hb.json)."""
    from tb.platform import services as S
    S.write_hb(name, extra, root=root, now=now)


def scrub_env(env=None):
    """Убрать из окружения процесса переменные секретов (то же правило, что у сервиса labels). -> число убранных."""
    from tb.platform import services as S
    env = os.environ if env is None else env
    gone = [k for k in list(env) if S.labels_secret(k)]
    for k in gone:
        env.pop(k, None)
    return len(gone)


def _load_adapter(venue, cfg):
    from tb.cmkt import ex
    return ex.load(venue, cfg=cfg)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tb.cmkt.collector")
    ap.add_argument("cmd", nargs="?", default="run", choices=("run",))
    ap.add_argument("--market", required=True)
    ap.add_argument("--root", default=None)
    ap.add_argument("--max-seconds", type=float, default=0.0, dest="max_seconds")
    a = ap.parse_args(argv)
    scrub_env()
    root = a.root or None
    m = parse_market(a.market)
    if m is None:
        sys.stderr.write("cmkt.collector: E_MARKET:%s\n" % a.market)
        return EXIT_CONF
    venue, kind = m
    name = service_name(venue, kind)
    cfg, errs = conf.load(root)
    if errs:
        write_hb(name, {"state": "E_CONF", "errors": errs}, root)
        sys.stderr.write("cmkt.collector: %s\n" % ", ".join(errs))
        return EXIT_CONF
    if (venue, kind) not in conf.markets(cfg, enabled_only=True):
        write_hb(name, {"state": "OFF", "errors": ["E_MARKET:%s_%s" % (venue, kind)]}, root)
        sys.stderr.write("cmkt.collector: рынок %s_%s выключен или не задан в config/cmkt.json\n" % (venue, kind))
        return EXIT_CONF
    try:
        adapter = _load_adapter(venue, cfg)
    except ValueError as e:
        write_hb(name, {"state": "E_ADAPTER", "errors": ["E_ADAPTER:%s" % venue]}, root)
        sys.stderr.write("cmkt.collector: %s\n" % e)
        return EXIT_CONF
    col = Collector(venue, kind, cfg, root=root, adapter=adapter)
    try:
        asyncio.run(col.run(max_seconds=a.max_seconds))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
