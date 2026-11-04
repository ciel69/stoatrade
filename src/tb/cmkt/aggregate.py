"""Сводка сводного рынка: процесс `cmkt` (R1.7, задача 15; Requirements 1.3, 4.4–4.7, 5.8, 10.4, 11.1, 11.2, 13.2; design.md, «Сводка»,
«Синхронизация», «Снимок»).

Запуск: `python -m tb.cmkt.aggregate run [--root R] [--max-seconds N]`. Битый config/cmkt.json -> код выхода 2 (причина в heartbeat
`runtime/platform/cmkt.hb.json`). Процесс же ведёт фокус (`tb.cmkt.focus.Focus` -> `runtime/cmkt/focus.json`, пишет только сводка)
и ротацию рядов. Окружение чистится от секретов, как у сборщиков.

Такт 1 с по `time.monotonic` (метки — `time.time`): на `T_agg = T + 0.3 с` строится снимок `ts = T`:

1. Чтение `runtime/cmkt/ex/<рынок>.json` всех рынков конфига (`conf.markets`, включая выключенные — у них `OFF`): повтор 3 раза по
   50 мс при `PermissionError` или недописанном JSON; файл не перечитывается, пока не изменились mtime и размер. Сводка держит до 3
   последних документов рынка и берёт новейший с `T_pub ≤ ts` (без заглядывания вперёд: сборщик публикует на `T` только события с
   `t_rx ≤ T`); строки монет `light` (сборщик пишет их раз в 5 с) берутся из последней публикации `light` с `lt ≤ ts`.
2. Правила включения (`logic.cmkt.market_code`): `OFF` (биржа выключена), `NOT_LISTED` (строка `nl` или нет метаданных), `DOWN`
   (рынок `down`/`err` — нет живых соединений, `PARSE`, `HOST_DENIED`), `SILENT` (возраст живости соединения монеты на `ts` > `silent_s`),
   `STALE` (`ts − T_pub > file_stale_s`; для `light` — от отметки, кратной 5; нет строки монеты в файле; строка `err`), `RESYNC`
   (стакан восстанавливается; частичный стакан `part` допускается), `LAG` (задержка последнего сообщения монеты > `lag_max_ms`,
   переопределение биржи ≤ 2000 мс), затем `MISMATCH` (Requirement 5.8: ≥ 3 рынков — медиана цены за монету, 2 — цена Bybit из
   `runtime/prices.json` не старше 150 с, иначе флаг `mm_unchecked`). Исключённый рынок остаётся в снимке с кодом и `lag_ms` и
   участвует только в знаменателях покрытия; рынок старше 1 ч и `NOT_LISTED` в покрытие не входят.
3. `logic.cmkt.features` по каждой монете фокуса; лидерство (`lead_market`) — по ряду середин `t_ex − offset` (точки `mx` строк),
   пересчёт раз в `LEAD_EVERY_S` на монету (разнесено по монетам), только при качественной оценке `offset`; коды `cm_lead_ex` —
   `logic.cmkt.market_codes(рынки конфига)`: 0–10 у рынков `MARKETS`, 11+ у новых бирж в порядке конфига.
4. `runtime/cmkt/snap.json` раз в `sync.snap_every_s` (2 с) и всегда, когда ни один рынок не включён (Requirement 11.1); строки рядов
   (`tb.cmkt.store.Store`: `deep` каждую секунду, `light` при `ts % 5 = 0`; `wr_ts` — время вставки; `gap_s` — пропущенные секунды
   яруса; `by_ex`; флаги) каждую секунду; совместимый `runtime/intel/xflow.json` — точка подключения `Aggregator.compat` (задача 22).
5. `ts` строго растёт: шаг часов назад — такты пропускаются до `ts` больше последнего, счётчик `clock_back`, флаг `clock_back` у
   строк первого снимка после шага.

Формат `snap.json` (схема `tb.cmkt/1`; разбор строки монеты — `logic.cmkt.snap_sym`):

    {"schema", "ts", "t_wr", "skew_ms", "clock_back", "gap_s", "spot_quote": "USDT", "cm_ver", "cols": CM_FEATURES,
     "mkts": [имя рынка по коду cm_lead_ex], "n_ex": {"perp": n, "spot": n} (включённых рынков), "usdt_usd": курс | None,
     "ex": {рынок: [code | None, lag_ms, off_ms, age_ms]}, "warn": [...], "sym_cols": SNAP_SYM_COLS, "mk_cols": SNAP_MK_COLS,
     "syms": {SYM: [tier, focus_s, [значения по cols — те же, что в ряду], {рынок: [по mk_cols]}, {вид: [[bid, ask, imb, n_ex, cov] | None
               ×4 полосы]}, [[side, px, usd, dist_pct, n_ex, [рынки], age_s]], {окно: {вид: [pct, net, n_ex, cov]}},
               [l5_long, l5_short, l1h_long, l1h_short, liq_cov, partial, ok, n_ex], [флаги], n_ex, cov, mid, skew_ms]}}
"""
import argparse
import json
import math
import os
import sys
import time
import zlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from tb import paths
from tb.cmkt import conf
from tb.cmkt import focus as FO
from tb.cmkt import store as ST
from tb.cmkt.collector import BAND_LABELS, COLS, SCHEMA as EX_SCHEMA, ex_path, market_state, scrub_env, write_hb
from tb.core import filelock
from tb.logic import cmkt as L

SERVICE = "cmkt"
SNAP_FILE = "snap.json"
AGG_DELAY_S = 0.3                     # T_agg = T + 0.3 с: сборщики успевают опубликовать T
READ_TRIES = 3
READ_PAUSE_S = 0.05
DOCS_KEEP = 3
LIGHT_KEEP = 2
OLD_MKT_S = 3600.0                    # рынок старше 1 ч не участвует и в знаменателях покрытия
LEAD_EVERY_S = 30                     # пересчёт лидерства монеты (разнесено по монетам crc32(sym) % LEAD_EVERY_S)
LEAD_KEEP_S = 60                      # результат лидерства годен не дольше
SERIES_KEEP_S = L.LEAD_WINDOW_S + 10
PRICES_MAX_AGE_S = 150.0
USDT_RATE_MAX_AGE_S = 11.0           # Coinbase: запасной REST раз в 10 с + время ответа (замер задачи 13: возраст курса до ~11 с)
HB_EVERY_S = 5.0
EXIT_CONF = 2
WARM_STEPS = (60, 300, 900, 3600)    # окна признаков: признак None, пока монета в фокусе меньше окна (Requirement 1.4)


def snap_path(root=None):
    return paths.runtime("cmkt", SNAP_FILE, root=root)


def read_snap(root=None):
    """snap.json или None (нет, битый, чужая схема). Не бросает."""
    try:
        with open(snap_path(root), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("schema") == L.SNAP_SCHEMA else None


def _i(x):
    v = L.num(x)
    return None if v is None else int(round(v))


def _r(x, nd):
    v = L.num(x)
    return None if v is None else round(v, nd)


def _pct(vals, q):
    v = sorted(vals)
    if not v:
        return None
    return v[min(len(v) - 1, max(0, int(math.ceil(q * len(v))) - 1))]


class _Mkt(object):
    """Состояние одного рынка в сводке: последние документы, кэш строк light, ряды середин для лидерства."""
    __slots__ = ("mkt", "venue", "kind", "enabled", "docs", "light", "sig", "series", "mx_T", "read_err")

    def __init__(self, mkt, venue, kind, enabled):
        self.mkt, self.venue, self.kind, self.enabled = mkt, venue, kind, enabled
        self.docs = deque(maxlen=DOCS_KEEP)          # документы по возрастанию T
        self.light = deque(maxlen=LIGHT_KEEP)        # (lt, {sym: строка}) публикаций light
        self.sig = None                              # (mtime_ns, size) последнего прочитанного файла
        self.series = {}                             # sym -> {секунда t_ex − offset: середина}
        self.mx_T = None                             # T последнего документа, из которого взяты точки mx
        self.read_err = 0

    def ingest(self, doc):
        if not isinstance(doc, dict) or doc.get("schema") != EX_SCHEMA or not isinstance(doc.get("T"), int):
            return False
        if self.docs and doc["T"] <= self.docs[-1]["T"]:
            return False
        self.docs.append(doc)
        if doc.get("lt") == doc["T"]:                # публикация с монетами light
            cols = doc.get("cols") or COLS
            ti = list(cols).index("tier") if "tier" in cols else 0
            rows = dict((s, r) for s, r in (doc.get("syms") or {}).items() if isinstance(r, list) and len(r) > ti and r[ti] == "l")
            self.light.append((doc["T"], rows, cols))
        return True

    def pick(self, ts):
        """Новейший документ с T ≤ ts или None."""
        for d in reversed(self.docs):
            if d["T"] <= ts:
                return d
        return None

    def light_rows(self, ts):
        for lt, rows, cols in reversed(self.light):
            if lt <= ts:
                return lt, rows, cols
        return None, {}, COLS


class Aggregator(object):
    """Сводка: синхронный такт `tick(ts, now)` (тестируется без процесса) и цикл `run()`."""

    def __init__(self, root=None, cfg=None, clock=time.time, store=None, focus=None, sleep=time.sleep, write_snap=True):
        self.root = root
        self.cfg = cfg if cfg is not None else conf.defaults()
        self.clock, self.sleep = clock, sleep
        self.sync = L._cfg(self.cfg, "sync")
        self.cov = L._cfg(self.cfg, "cov")
        self.le = int((self.cfg.get("store") or {}).get("light_every_s", 5))
        self.snap_every = int(self.sync.get("snap_every_s", 2))
        self.store = store if store is not None else ST.Store(root, self.cfg, clock=clock)
        self.focus = focus                          # tb.cmkt.focus.Focus или None (тогда читается focus.json)
        self.write_snap_file = write_snap
        allm = conf.markets(self.cfg, enabled_only=False)
        on = set(conf.markets(self.cfg, enabled_only=True))
        self.mkts = [_Mkt("%s_%s" % (v, k), v, k, (v, k) in on) for v, k in allm]
        self.codes = L.market_codes([m.mkt for m in self.mkts])
        self.code_names = [None] * len(self.codes)
        for m, i in self.codes.items():
            self.code_names[i] = m
        self.last_ts = None
        self.last_deep_ts = self.last_light_ts = None
        self.last_snap_ts = None
        self.clock_back = 0
        self._cb_flag = False
        self.lead = {}                               # sym -> (ts расчёта, результат)
        self._prices = (None, None, None)            # (sig, ts_epoch, prices)
        self.compat = None                           # задача 22: fn(ts, ctx) -> пишет runtime/intel/xflow.json при compat_xflow
        self.tick_ms = deque(maxlen=600)
        self.stats = {"ticks": 0, "snaps": 0, "snap_err": 0, "rows": 0, "store_code": None, "read_err": 0, "skipped": 0,
                      "gap_s_max": 0, "lead_runs": 0, "compat_err": 0}
        self.last = None                             # последний снимок (словарь для тестов и heartbeat)
        self._t_hb = None
        self._pool = None
        self._base = {}                              # (рынок, монета) -> (T_pub, id строки, состояние market_state)
        self._memo = {}                              # монета light -> (ключ входов, out features, строка снимка)

    # ------------------------------------------------------------ чтение файлов рынков
    def _read_file(self, m):
        """-> (документ | None, ошибка чтения). Файл не перечитывается, пока не изменились mtime и размер."""
        path = ex_path(m.mkt, self.root)
        try:
            st = os.stat(path)
        except OSError:
            return None, False
        sig = (st.st_mtime_ns, st.st_size)
        if sig == m.sig:
            return None, False
        for k in range(READ_TRIES):
            try:
                with open(path, "rb") as f:
                    doc = json.loads(f.read().decode("utf-8"))
                m.sig = sig
                return doc, False
            except FileNotFoundError:
                return None, False
            except (PermissionError, ValueError, UnicodeDecodeError):
                if k + 1 < READ_TRIES:
                    self.sleep(READ_PAUSE_S)
            except OSError:
                break
        return None, True

    def read_all(self):
        """Чтение файлов рынков параллельно (открытие только что заменённого файла на Windows ждёт проверку антивируса, около 5 мс;
        потоки перекрывают ожидание), разбор и учёт — в потоке такта."""
        todo = [m for m in self.mkts if m.enabled]
        if self._pool is None and len(todo) > 1:
            self._pool = ThreadPoolExecutor(max_workers=min(16, len(todo)), thread_name_prefix="cmkt-read")
        res = list(self._pool.map(self._read_file, todo)) if self._pool is not None else [self._read_file(m) for m in todo]
        for m, (doc, err) in zip(todo, res):
            if err:
                m.read_err += 1
                self.stats["read_err"] += 1
            elif doc is not None:
                m.ingest(doc)

    def close(self):
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None
        self.store.close()

    # ------------------------------------------------------------ вспомогательные источники
    def _ref_prices(self):
        """Цены Bybit из runtime/prices.json (сканер) для сверки при 2 рынках: (ts_epoch, {SYM: цена}) или (None, {})."""
        path = paths.runtime("prices.json", root=self.root)
        try:
            st = os.stat(path)
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            return None, {}
        if sig != self._prices[0]:
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
            except (OSError, ValueError):
                return self._prices[1], self._prices[2] or {}
            ok = isinstance(d, dict) and "test" not in str(d.get("env") or "").lower() and isinstance(d.get("prices"), dict)
            self._prices = (sig, L.num(d.get("ts_epoch")) if ok else None, d.get("prices") if ok else {})
        return self._prices[1], self._prices[2] or {}

    def _ref_px(self, sym, now):
        ts, px = self._ref_prices()
        if ts is None or not 0 <= now - ts <= PRICES_MAX_AGE_S:
            return None
        v = L.num(px.get(sym))
        if v is None:
            v = L.num(px.get("1000" + sym))
            v = v / 1000.0 if v else None
        return v

    def _focus_doc(self, now):
        if self.focus is not None:
            try:
                return self.focus.step(now)
            except Exception:                                # noqa: BLE001 — сбой фокуса не останавливает снимок
                return self.focus.doc
        return FO.read(self.root)

    # ------------------------------------------------------------ коды включения
    def _ex_level(self, m, doc, ts):
        """Код рынка целиком (поле ex снимка)."""
        if not m.enabled:
            return "OFF"
        if doc is None:
            return "STALE" if m.docs else "DOWN"
        st = doc.get("st")                         # живость соединений — у строк монет; здесь down/err сборщика
        return L.market_code(ts, doc.get("T"), 0.0, L.num(doc.get("lag_ms")), "ok", self.sync, m.venue,
                             connected=st not in ("down", "err"), listed=st != "meta")

    def _coin_state(self, m, doc, sym, ts, light):
        """-> (код, состояние для features | None, T_pub, lag_ms). Состояние None — рынок не входит и в знаменатели покрытия."""
        row, t_pub = None, doc["T"]
        rows = doc.get("syms") or {}
        cols = doc.get("cols") or COLS
        if sym in rows:
            row = rows[sym]
            if isinstance(row, list) and row and row[0] == "l":
                t_pub = doc.get("lt") if isinstance(doc.get("lt"), int) else doc["T"]
        else:
            lt, lrows, lcols = light
            if sym in lrows:
                row, t_pub, cols = lrows[sym], lt, lcols
        if not isinstance(row, list):
            return "STALE", None, None, None
        key = (m.mkt, sym)
        hit = self._base.get(key)
        if hit is not None and hit[0] == t_pub and hit[1] is row:
            base = hit[2]                                # та же строка (монета light между публикациями): разбор не повторяется
        else:
            base = market_state({"kind": m.kind, "liq_partial": doc.get("liq_partial"), "cols": cols, "syms": {sym: row}}, sym)
            if base is None:
                return "STALE", None, None, None
            base["fs"] = dict(zip(cols, row)).get("fs")
            self._base[key] = (t_pub, row, base)
        s = dict(base)
        rst = s.get("st")
        lag = L.num(s.get("lag"))
        if lag is None:
            lag = L.num(doc.get("lag_ms"))
        if rst == "nl":
            return "NOT_LISTED", None, t_pub, lag
        if ts - t_pub > OLD_MKT_S:
            return "STALE", None, t_pub, lag
        if rst == "err":
            code = "STALE"
        else:
            a = L.num(s.get("a"))
            age = a + max(0, ts - t_pub) if a is not None else None
            code = L.market_code(ts, t_pub, age, lag, "resync" if rst == "resync" else "ok", self.sync, m.venue,
                                 light=s.get("tier") == "light", light_every_s=self.le,
                                 connected=doc.get("st") not in ("down", "err"), listed=doc.get("st") != "meta")
        s["code"] = code
        return code, s, t_pub, lag

    def _usdt_usd(self, picked, ts):
        """Курс USDT/USD не старше 10 с: поле `fx` документа рынка (Coinbase, задача 13: {"usdt_usd", "age_s"} на T_pub), иначе
        середина строки USDT рынка к USD."""
        for m, doc, _ in picked:
            if doc is None or ts - doc["T"] > USDT_RATE_MAX_AGE_S or doc.get("st") in ("down", "err"):
                continue
            fx = doc.get("fx") if isinstance(doc.get("fx"), dict) else {}
            v, age = L.num(fx.get("usdt_usd")), L.num(fx.get("age_s"))
            if v and v > 0 and age is not None and age + (ts - doc["T"]) <= USDT_RATE_MAX_AGE_S:
                return v
            if fx:
                continue
            row = (doc.get("syms") or {}).get("USDT")
            if isinstance(row, list):
                r = dict(zip(doc.get("cols") or COLS, row))
                if r.get("q") == "USD" and L.num(r.get("mid")):
                    return L.num(r["mid"])
        return None

    # ------------------------------------------------------------ лидерство
    def _ingest_mx(self, m, doc, light, ts):
        if doc is None or (m.mx_T is not None and doc["T"] <= m.mx_T):
            return
        m.mx_T = doc["T"]
        cols = doc.get("cols") or COLS
        try:
            ix = list(cols).index("mx")
        except ValueError:
            return
        lo = ts - SERIES_KEEP_S
        for sym, row in (doc.get("syms") or {}).items():
            if not isinstance(row, list) or len(row) <= ix or not row[ix]:
                continue
            ser = m.series.setdefault(sym, {})
            for p in row[ix]:
                if isinstance(p, list) and len(p) == 2 and isinstance(p[0], int) and p[0] <= ts and L.num(p[1]):
                    ser[p[0]] = float(p[1])
            if len(ser) > SERIES_KEEP_S + 20:
                for k in [k for k in ser if k < lo]:
                    del ser[k]

    def _lead(self, sym, inc_mkts, ts, quality):
        prev = self.lead.get(sym)
        due = (ts + zlib.crc32(sym.encode("utf-8"))) % LEAD_EVERY_S == 0
        if not due:
            return prev[1] if prev is not None and ts - prev[0] <= LEAD_KEEP_S else None
        series = {}
        for m in inc_mkts:
            ser = m.series.get(sym)
            if ser and len(ser) >= L.LEAD_MIN_NONZERO:
                series[m.mkt] = ser
        res = None
        if len(series) >= 2:
            self.stats["lead_runs"] += 1
            try:
                res = L.lead_market(series, ts, quality, self.sync)
            except (ValueError, ZeroDivisionError, TypeError):
                res = None
        self.lead[sym] = (ts, res)
        return res

    # ------------------------------------------------------------ такт
    def tick(self, ts=None, now=None):
        """Снимок ts (по умолчанию floor(now − 0.3)). -> снимок (словарь) или None (ts не новее прошлого: шаг часов назад)."""
        t_start = time.perf_counter()
        now = self.clock() if now is None else now
        ts = int(math.floor(now - AGG_DELAY_S)) if ts is None else int(ts)
        if self.last_ts is not None and ts <= self.last_ts:
            self.clock_back += 1
            self._cb_flag = True
            self.stats["skipped"] += 1
            return None
        gap = ts - self.last_ts - 1 if self.last_ts is not None else 0
        self.stats["gap_s_max"] = max(self.stats["gap_s_max"], gap)
        self.read_all()
        fdoc = self._focus_doc(now)
        deep, light, since, fwarn = self._focus_sets(fdoc)
        picked = []
        ex = {}
        n_kind = {"perp": 0, "spot": 0}
        skew_in = []
        offsets = {}
        quality = {}
        for m in self.mkts:
            doc = m.pick(ts) if m.enabled else None
            lrows = m.light_rows(ts) if doc is not None else (None, {}, COLS)
            code = self._ex_level(m, doc, ts)
            lag = L.num(doc.get("lag_ms")) if doc is not None else None
            ex[m.mkt] = [code, _i(lag), _i(doc.get("off_ms")) if doc else None, int((ts - doc["T"]) * 1000) if doc else None]
            if code is None:
                n_kind[m.kind] = n_kind.get(m.kind, 0) + 1
                skew_in.append((ts - doc["T"], lag or 0.0))
            if doc is not None and doc.get("off_ms") is not None:
                offsets.setdefault(m.venue, doc["off_ms"])
            if doc is not None:
                rtt, age = L.num(doc.get("rtt_ms")), L.num(doc.get("off_age_s"))
                quality[m.mkt] = (rtt, age + (ts - doc["T"]) if age is not None else None)
                self._ingest_mx(m, doc, lrows, ts)
            picked.append((m, doc, lrows))
        usdt = self._usdt_usd([p for p in picked if p[0].enabled and p[0].kind == "spot"], ts)
        rows, syms = [], {}
        any_inc = False
        cb_flag = self._cb_flag
        deep_set = set(deep)
        for sym in deep + light:
            tier = "deep" if sym in deep_set else "light"
            mk, codes, pubs = {}, {}, {}
            for m, doc, lrows in picked:
                if not m.enabled:
                    codes[m.mkt] = ("OFF", None)
                    continue
                if doc is None:
                    codes[m.mkt] = ("STALE" if m.docs else "DOWN", None)    # есть только публикации позже ts — без заглядывания вперёд
                    continue
                code, s, t_pub, lag = self._coin_state(m, doc, sym, ts, lrows)
                codes[m.mkt] = (code, lag)
                if s is not None:
                    mk[m.mkt] = s
                    pubs[m.mkt] = (t_pub, lag)
            mm_unchecked = self._mismatch(sym, mk, codes, usdt, now)
            inc = [m for m, _, _ in picked if m.mkt in mk and mk[m.mkt].get("code") is None]
            any_inc = any_inc or bool(inc)
            fs = self._focus_s(sym, since, mk, ts)
            lead = self._lead(sym, inc, ts, quality) if fs >= L.LEAD_WINDOW_S else None
            sk = L.skew_ms([(ts - pubs[x.mkt][0], pubs[x.mkt][1] or 0.0) for x in inc])
            # входы features монеты: строки рынков (T_pub) и коды, ступень прогрева, курс, лидер — у монеты light между публикациями
            # они те же, результат берётся из памяти (такт 140 монет укладывается в 100 мс)
            key = (tier, tuple((k, c[0], pubs[k][0] if k in pubs else None) for k, c in codes.items()), sum(fs >= w for w in WARM_STEPS),
                   _r(usdt, 6) if any(s.get("quote") == "USD" for s in mk.values()) else None, mm_unchecked,
                   (lead or {}).get("mkt"), (lead or {}).get("lag_s"), cb_flag)
            hit = self._memo.get(sym)
            if hit is not None and hit[0] == key:
                out, flags = hit[1], hit[2]
                srow = list(hit[3])
                srow[1], srow[12] = int(fs), sk
            else:
                try:
                    out = L.features(mk, fs, self.cfg, usdt_usd=usdt, lead=lead, mm_unchecked=mm_unchecked, mkt_index=self.codes)
                except (ValueError, TypeError, ZeroDivisionError, KeyError):
                    out = {"f": dict.fromkeys(L.CM_FEATURES), "flags": ["low_cov"], "bands": {}, "walls": [], "cvd": {}, "liq": None,
                           "by_ex": [], "n_ex": 0, "cov": None, "mid": None}
                flags = list(out["flags"]) + (["clock_back"] if cb_flag else [])
                srow = self._snap_row(tier, fs, out, mk, codes, flags, sk)
                self._memo[sym] = (key, out, flags, srow)
            if self.store.due(tier, ts):
                last = self.last_deep_ts if tier == "deep" else self.last_light_ts
                step = 1 if tier == "deep" else self.le
                g = max(0, ts - last - step) if last is not None else 0
                rows.append({"sym": sym, "tier": tier, "f": out["f"], "flags": flags, "by_ex": out["by_ex"], "focus_s": fs,
                             "skew_ms": sk, "gap_s": g})
            syms[sym] = srow
        if len(self._memo) > len(syms) + 64 or len(self._base) > (len(syms) + 16) * max(1, len(self.mkts)):
            self._memo = dict((s, v) for s, v in self._memo.items() if s in syms)
            self._base = dict((k, v) for k, v in self._base.items() if k[1] in syms)
        if rows:
            if any(r["tier"] == "deep" for r in rows):
                self.last_deep_ts = ts
            if any(r["tier"] == "light" for r in rows):
                self.last_light_ts = ts
        if self.store.due("deep", ts) and not any(r["tier"] == "deep" for r in rows):
            self.last_deep_ts = ts
        if self.store.due("light", ts) and not any(r["tier"] == "light" for r in rows):
            self.last_light_ts = ts
        warn = list(fwarn)
        if self._clock_skew(offsets):
            warn.append("CLOCK_SKEW")
        snap = {"schema": L.SNAP_SCHEMA, "ts": ts, "t_wr": None, "skew_ms": L.skew_ms(skew_in), "clock_back": self.clock_back,
                "gap_s": gap, "spot_quote": "USDT", "cm_ver": L.CM_VER, "cols": list(L.CM_FEATURES), "mkts": list(self.code_names),
                "n_ex": n_kind, "usdt_usd": _r(usdt, 6), "ex": ex, "warn": warn, "sym_cols": list(L.SNAP_SYM_COLS),
                "mk_cols": list(L.SNAP_MK_COLS), "syms": syms}
        self.last_ts = ts
        self._cb_flag = False
        if self.write_snap_file and (self.last_snap_ts is None or ts - self.last_snap_ts >= self.snap_every or not any_inc):
            self._write_snap(snap)
        res = self.store.write(ts, rows)
        self.stats["store_code"] = res.get("code")
        self.stats["rows"] += res.get("n", 0)
        self.stats["ticks"] += 1
        if self.compat is not None and self.cfg.get("compat_xflow"):
            try:
                self.compat(ts, {"snap": snap, "picked": picked, "focus": fdoc})
            except Exception:                                # noqa: BLE001 — совместимый документ не роняет сводку
                self.stats["compat_err"] += 1
        self.last = snap
        self.tick_ms.append((time.perf_counter() - t_start) * 1000.0)
        return snap

    def _focus_sets(self, fdoc):
        if isinstance(fdoc, dict):
            deep = [s for s in fdoc.get("deep") or () if isinstance(s, str)]
            light = [s for s in fdoc.get("light") or () if isinstance(s, str) and s not in deep]
            return deep, light, fdoc.get("since") or {}, [w for w in fdoc.get("warn") or () if isinstance(w, str)]
        return list(self.cfg["focus"]["core"]), [], {}, ["FOCUS_NONE"]

    def _focus_s(self, sym, since, mk, ts):
        t = L.num(since.get(sym))
        if t is not None:
            return max(0, int(ts - t))
        fs = [L.num(s.get("fs")) for s in mk.values() if L.num(s.get("fs")) is not None]
        return int(max(fs)) if fs else 0

    def _mismatch(self, sym, mk, codes, usdt, now):
        prices = {}
        for mkt, s in mk.items():
            if s.get("code") is not None:
                continue
            px = L.num(s.get("mid"))
            if not px:
                continue
            if s.get("quote") == "USD":
                if not usdt:
                    continue
                px = px / usdt
            prices[mkt] = px
        if not prices:
            return False
        ref = self._ref_px(sym, now) if len(prices) < 3 else None
        bad, unchecked = L.mismatch(prices, ref, float(self.cov.get("mismatch_pct", 2.0)))
        for mkt in bad:
            mk[mkt]["code"] = "MISMATCH"
            codes[mkt] = ("MISMATCH", codes[mkt][1])
        return unchecked

    def _clock_skew(self, offsets):
        warn_ms = float(self.sync.get("clock_warn_ms", 500))
        return sum(1 for v in offsets.values() if L.num(v) is not None and abs(v) > warn_ms) >= 4

    # ------------------------------------------------------------ компактная строка снимка
    def _snap_row(self, tier, fs, out, mk, codes, flags, sk):
        q = L.quantized(out["f"])
        vals = [q[n] for n in L.CM_FEATURES]
        byx = dict((r["mkt"], r) for r in out.get("by_ex") or ())
        mkr = {}
        for mkt, (code, lag) in codes.items():
            s = mk.get(mkt)
            if s is None:
                if code not in ("OFF",):
                    mkr[mkt] = [code, _i(lag)] + [None] * (len(L.SNAP_MK_COLS) - 2)
                continue
            b = byx.get(mkt) or {}
            f8 = L.fund_8h(s.get("fund"), s.get("fund_h")) if s.get("kind") == "perp" else None
            mkr[mkt] = [code, _i(lag), _r(s.get("mid"), 8), _i(L.oi_usd(s)) if s.get("kind") == "perp" else None,
                        _r(f8 * 100.0, 6) if f8 is not None else None, _i(b.get("cvd5")), _r(b.get("doi5"), 4), _i(b.get("liq5_long")),
                        _i(b.get("liq5_short")), b.get("pulled15")]
        bands = {}
        for kind, rowd in (out.get("bands") or {}).items():
            bands[kind] = [[_i(r["bid"]), _i(r["ask"]), _r(r["imb"], 2), r["n_ex"], _r(r["cov"], 3)] if r else None
                           for r in (rowd.get(lab) for lab in BAND_LABELS)]
        walls = [["b" if w["side"] == "bid" else "a", _r(w["px"], 10), _i(w["usd"]), _r(w["dist_pct"], 3), w["n_ex"], w["mkts"], w["age_s"]]
                 for w in out.get("walls") or ()]
        cvd = dict((lab, dict((k, [_r(v["pct"], 2), _i(v["net"]), v["n_ex"], _r(v["cov"], 3)]) for k, v in kinds.items()))
                   for lab, kinds in (out.get("cvd") or {}).items())
        lq = out.get("liq")
        liq = ([_i(lq["5m"][0]), _i(lq["5m"][1]), _i(lq["1h"][0]), _i(lq["1h"][1]), _r(lq["liq_cov"], 3), int(bool(lq["partial"])),
                int(bool(lq["ok"])), lq["n_ex"]] if lq else None)
        return [tier, int(fs), vals, mkr, bands, walls, cvd, liq, flags, out.get("n_ex"), _r(out.get("cov"), 3), _r(out.get("mid"), 10), sk]

    def _write_snap(self, snap):
        snap["t_wr"] = round(self.clock(), 3)
        try:
            filelock.atomic_write(snap_path(self.root), json.dumps(snap, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
            self.last_snap_ts = snap["ts"]
            self.stats["snaps"] += 1
        except (OSError, ValueError):
            self.stats["snap_err"] += 1

    # ------------------------------------------------------------ heartbeat и цикл
    def heartbeat(self, now=None, extra=None):
        now = self.clock() if now is None else now
        snap = self.last or {}
        hb = {"state": "ok", "cfg_hash": conf.config_hash(self.cfg)[:16], "ts": self.last_ts, "clock_back": self.clock_back,
              "ticks": self.stats["ticks"], "skipped": self.stats["skipped"], "snaps": self.stats["snaps"], "snap_err": self.stats["snap_err"],
              "snap_ts": self.last_snap_ts, "rows": self.stats["rows"], "store_code": self.stats["store_code"],
              "store": dict((k, self.store.stats.get(k)) for k in ("disk_low", "corrupt", "errors", "write_ms", "file", "rotate_fail")),
              "read_err": self.stats["read_err"], "gap_s_max": self.stats["gap_s_max"], "lead_runs": self.stats["lead_runs"],
              "tick_ms_p50": _r(_pct(self.tick_ms, 0.5), 2), "tick_ms_p99": _r(_pct(self.tick_ms, 0.99), 2),
              "tick_ms_max": _r(max(self.tick_ms) if self.tick_ms else None, 2), "n_syms": len(snap.get("syms") or {}),
              "n_ex": snap.get("n_ex"), "skew_ms": snap.get("skew_ms"), "warn": snap.get("warn") or [],
              "ex": dict((m, v[0]) for m, v in (snap.get("ex") or {}).items()), "compat_xflow": bool(self.cfg.get("compat_xflow")),
              "compat_err": self.stats["compat_err"]}
        hb.update(extra or {})
        write_hb(SERVICE, hb, self.root, now)
        return hb

    def run(self, stop=None, max_seconds=0.0, mono=time.monotonic):
        """Цикл процесса: такт на T + 0.3 с (расписание по monotonic), ротация рядов, heartbeat раз в 5 с. stop() -> True — выход."""
        t_end = mono() + max_seconds if max_seconds else None
        self.heartbeat()
        try:
            while not (stop and stop()):
                m0 = mono()
                if t_end is not None and m0 >= t_end:
                    break
                now = self.clock()
                ts = int(math.floor(now - AGG_DELAY_S))
                if self.last_ts is None or ts > self.last_ts:
                    self.tick(ts, now)
                    self.store.maybe_rotate(now)
                elif ts < self.last_ts:
                    self.tick(ts, now)                       # шаг часов назад: счётчик clock_back, снимка нет
                if self._t_hb is None or not 0 <= m0 - self._t_hb < HB_EVERY_S:
                    self._t_hb = m0
                    self.heartbeat(now)
                wall = self.clock()
                nxt = math.floor(wall - AGG_DELAY_S) + 1 + AGG_DELAY_S       # следующая граница T_agg по реальным часам
                deadline = mono() + max(0.01, min(1.0, nxt - wall))
                while True:
                    left = deadline - mono()
                    if left <= 0 or (stop and stop()):
                        break
                    self.sleep(min(left, 0.25))
        finally:
            self.close()
            self.heartbeat(None, {"stopped": True})


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tb.cmkt.aggregate")
    ap.add_argument("cmd", nargs="?", default="run", choices=("run",))
    ap.add_argument("--root", default=None)
    ap.add_argument("--max-seconds", type=float, default=0.0, dest="max_seconds")
    a = ap.parse_args(argv)
    scrub_env()
    root = a.root or None
    cfg, errs = conf.load(root)
    if errs:
        write_hb(SERVICE, {"state": "E_CONF", "errors": errs}, root)
        sys.stderr.write("cmkt.aggregate: %s\n" % ", ".join(errs))
        return EXIT_CONF
    if not cfg.get("enabled", True):
        write_hb(SERVICE, {"state": "OFF"}, root)
        return 0
    agg = Aggregator(root, cfg, focus=FO.Focus(root, cfg))
    try:
        agg.run(max_seconds=a.max_seconds)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
