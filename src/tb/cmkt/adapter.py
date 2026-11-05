"""Контракт адаптера биржи и общая механика сборщиков сводного рынка (R1.7, задача 6; Requirements 2.2, 2.3, 3.4, 5.1–5.5, 5.7, 5.8,
5.10, 5.11, 12.1, 12.2, 12.4; design.md, «Контракт адаптера», «REST-бюджет»).

Здесь всё, что одинаково у всех бирж, — адаптер биржи (`tb.cmkt.ex.<venue>`) даёт только форматы, каналы и правила своей биржи:

- нормализованные события `Trade`, `BookSnapshot`, `BookDelta`, `Ticker`, `Liquidation`, `Pong`, `SubAck`;
- `VenueAdapter` — базовый класс контракта (методы `hosts`, `urls`, `load_meta`, `plan`, `valid_topic`, `sub_msgs`, `resub`, `ping`, `parse`,
  `continuity`, `checksum`, `snapshot_cost`, `book_rest`, `server_time`, `poll`);
- `BookState` — стакан по снимку и дельтам с правилом непрерывности (`RULES`: Binance perp и spot, Gate `obu`, OKX/Bitget, Bybit,
  частичный стакан), граница REST-снимка, проверка контрольной суммы (если биржа её присылает), перекрёст дольше 2 с и пустая сторона;
- `TapeBuckets` — лента на `array` (900 секундных корзин по `t_rx`, дубли по идентификатору, непрерывность, `side_unknown`);
- `MetaCache`, `select_instruments`, `base_coin`, `match_markets` — инструменты (только линейные USDT-бессрочные и спот к USDT,
  множители `1000*`, обновление раз в 6 ч, `NOT_LISTED`, `MISMATCH`);
- `SubPlanner`, `RateLimiter`, `split_rejected` — подписки в пределах тем на соединение, сообщений подписки в секунду, соединений в секунду;
- `RestBudget`, `ResyncQueue` — общий REST-вес рынка в минуту с резервом OI, снимки из остатка, порядок восстановления
  позиции > планы > ядро > остальные, пауза OI `light` при `RESYNC` больше 4 стаканов, пауза 60 с после 429;
- `Http` — GET с белым списком хостов (`HOST_DENIED`), таймаутом 5 с и бюджетом;
- `ClockOffset`, `clock_skew`, `LagMeter`, `Liveness`, `Backoff` — время биржи, задержка, живость по пингу, переподключение 1 → 60 с ±20 %;
- `VenueConn` — соединение на WS-клиенте проекта `tb.marketdata.wsclient` (stdlib, без ccxt): подписки пачками с темпом, пинг, живость,
  переподключение, повтор отклонённых тем по одной.

Только публичные каналы без ключей и подписи. Модуль не импортирует биржевой и торговый код платформы.
"""
import asyncio
import binascii
import gzip
import heapq
import inspect
import json
import math
import random
import re
import time
import urllib.error
import urllib.request
from array import array
from collections import deque
from typing import NamedTuple, Optional
from urllib.parse import urlsplit

from tb.cmkt import conf
from tb.logic import cmkt as L
from tb.marketdata import wsclient

HOST_DENIED = conf.HOST_DENIED
META_TTL_S = 6 * 3600.0
TAPE_SLOTS = 900
CROSS_MAX_S = 2.0
RESYNC_HEAVY = 4                    # больше 4 стаканов в RESYNC — пауза OI яруса light (design.md, «REST-бюджет»)
PENALTY_S = 60.0                    # REST 429/418: пауза без повтора
PRIO = {"pos": 0, "plan": 1, "core": 2, "deep": 3, "light": 4}
QUOTE_SPOT = ("USDT",)
QUOTE_USD = ("USD",)                # только Coinbase (allow_usd)
_RE_VENUE = re.compile(r"^[a-z][a-z0-9]{0,10}$")


# ---------------------------------------------------------------- нормализованные события
class Trade(NamedTuple):
    mkt: str
    sym: str
    t_ex: float
    t_rx: float
    px: float                       # цена за монету
    coins: float
    side: int                       # +1 агрессор покупает, −1 продаёт, 0 — сторона не распознана
    tid: Optional[str] = None       # идентификатор сделки биржи (дубли учитываются один раз)


class BookSnapshot(NamedTuple):
    mkt: str
    sym: str
    t_ex: float
    t_rx: float
    seq: Optional[int]
    bids: list                      # [(px_str, size_str)] — исходные строки биржи, единицы инструмента
    asks: list
    rest: bool = False              # снимок вне потока (REST или WS API): уровни дальше границы снимка отбрасываются
    part: bool = False              # частичный стакан (канал яруса light или book_part на время RESYNC), не полный стакан deep


class BookDelta(NamedTuple):
    mkt: str
    sym: str
    t_ex: float
    t_rx: float
    first: Optional[int]
    last: Optional[int]
    prev: Optional[int]
    checksum: Optional[int]
    bids: list
    asks: list
    part: bool = False              # дельта частичного канала (Bybit orderbook.50): свой BookState яруса light


class Ticker(NamedTuple):
    mkt: str
    sym: str
    t_ex: float
    t_rx: float
    mark: Optional[float] = None    # цены за монету
    index: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    funding: Optional[float] = None     # ставка текущего периода, доля
    funding_h: Optional[float] = None
    oi_coins: Optional[float] = None
    turn24_usd: Optional[float] = None


class Liquidation(NamedTuple):
    mkt: str
    sym: str
    t_ex: float
    t_rx: float
    px: float
    coins: float
    liq_side: str                   # "long" | "short" — чья позиция ликвидирована (ликвидация лонга — ордер на продажу)


class Pong(NamedTuple):
    mkt: str
    t_rx: float


class SubAck(NamedTuple):
    mkt: str
    t_rx: float
    topics: tuple
    ok: bool
    reason: Optional[str] = None


class Sub(NamedTuple):
    conn_key: str                   # маршрут соединения биржи (например public / market у Binance perp)
    topic: str
    kind: str                       # trades | book | book_part | ticker | oi | liq
    sym: str


# ---------------------------------------------------------------- инструменты (Requirements 2.3, 5.7, 5.8)
class Inst(NamedTuple):
    native: str
    sym: str
    kind: str                       # perp | spot
    contract_size: Optional[float]
    coin_mult: float
    tick: Optional[float] = None
    funding_h: Optional[float] = None
    quote: str = "USDT"
    status: str = "trading"


_RE_MULT = re.compile(r"^(1(?:0+)|1M)([A-Z][A-Z0-9]*)$")


def base_coin(base):
    """Базовый актив инструмента -> (монета, множитель): '1000PEPE' -> ('PEPE', 1000), '1MBABYDOGE' -> ('BABYDOGE', 10**6),
    '10000SATS' -> ('SATS', 10000); '1INCH' и прочие -> (как есть, 1)."""
    b = str(base or "").upper()
    m = _RE_MULT.match(b)
    if not m or len(m.group(2)) < 2:
        return b, 1.0
    mult = 1e6 if m.group(1) == "1M" else float(m.group(1))
    return m.group(2), mult


def select_instruments(rows, kind, allow_usd=False):
    """Отбор инструментов при загрузке метаданных. rows — нормализованные адаптером словари: {"native", "base", "quote", "settle",
    "type": "linear_perp" | "inverse_perp" | "future" | "spot" | ..., "contract_size", "coin_mult" (если биржа даёт), "tick", "funding_h",
    "status": "trading" | ...}. perp: только линейные USDT-бессрочные (котировка и расчёт USDT, без срока); spot: только к USDT (у Coinbase —
    к USD при allow_usd); FDUSD, USDC и прочие котировки, обратные и срочные — отбрасываются. Несколько инструментов на одну монету —
    берётся инструмент без множителя. -> [Inst]."""
    out = {}
    for r in rows or ():
        if not isinstance(r, dict) or str(r.get("status") or "trading").lower() != "trading":
            continue
        t, q = r.get("type"), str(r.get("quote") or "").upper()
        if kind == "perp":
            if t != "linear_perp" or q != "USDT" or str(r.get("settle") or "").upper() != "USDT":
                continue
        elif kind == "spot":
            if t != "spot" or not (q in QUOTE_SPOT or (allow_usd and q in QUOTE_USD)):
                continue
        else:
            continue
        coin, mult = base_coin(r.get("base"))
        if L.num(r.get("coin_mult")):
            mult = float(r["coin_mult"])
        cs = L.num(r.get("contract_size")) if kind == "perp" else (L.num(r.get("contract_size")) or 1.0)
        if not coin or not r.get("native"):
            continue
        inst = Inst(str(r["native"]), coin, kind, cs if cs and cs > 0 else None, mult, L.num(r.get("tick")), L.num(r.get("funding_h")), q)
        key = (coin, q)
        if key in out and out[key].coin_mult == 1.0 and inst.coin_mult != 1.0:
            continue
        out[key] = inst
    return list(out.values())


class MetaCache(object):
    """Метаданные инструментов по рынкам: обновление раз в 6 ч; монета без известного размера контракта — NOT_LISTED."""

    def __init__(self, ttl_s=META_TTL_S):
        self.ttl_s = float(ttl_s)
        self.by_sym, self.by_native, self.t = {}, {}, {}

    def due(self, mkt, now):
        return mkt not in self.t or now - self.t[mkt] >= self.ttl_s or now < self.t[mkt]

    def update(self, mkt, insts, now):
        self.by_sym[mkt] = {}
        self.by_native[mkt] = {}
        for i in insts or ():
            self.by_sym[mkt][(i.sym, i.quote)] = i
            self.by_native[mkt][i.native] = i
        self.t[mkt] = now

    def get(self, mkt, sym, quote=None):
        d = self.by_sym.get(mkt) or {}
        if quote is not None:
            return d.get((sym, quote))
        for q in ("USDT", "USD"):
            if (sym, q) in d:
                return d[(sym, q)]
        return None

    def native(self, mkt, native):
        return (self.by_native.get(mkt) or {}).get(native)

    def listed(self, mkt, sym, quote=None):
        i = self.get(mkt, sym, quote)
        return i is not None and (i.kind == "spot" or bool(i.contract_size))

    def code(self, mkt, sym, quote=None):
        """Код NOT_LISTED или None."""
        return None if self.listed(mkt, sym, quote) else "NOT_LISTED"

    def syms(self, mkt):
        return sorted(set(s for s, _ in (self.by_sym.get(mkt) or {})))

    def coin_px(self, mkt, sym, px, quote=None):
        i = self.get(mkt, sym, quote)
        return None if i is None else L.coin_px(px, i.coin_mult)

    def coins(self, mkt, sym, qty, quote=None):
        i = self.get(mkt, sym, quote)
        if i is None:
            return None
        return L.coins(qty, i.contract_size if i.kind == "perp" else 1.0, i.coin_mult)


def match_markets(prices, ref_px=None, ref_age_s=None, pct=2.0, max_ref_age_s=150.0):
    """Requirement 5.8: prices {mkt: цена за монету} (Coinbase — переведена в USDT вызывающим); при 2 рынках ref_px — цена Bybit из
    runtime/prices.json не старше 150 с. -> (множество рынков MISMATCH, mm_unchecked)."""
    ref = ref_px if ref_age_s is not None and 0 <= ref_age_s <= max_ref_age_s else None
    return L.mismatch(prices, ref, pct)


# ---------------------------------------------------------------- стакан (Requirements 5.1–5.4)
def _seq_binance_perp(st, d):
    if not st.synced:                                 # первая дельта после снимка WS API: U ≤ lastUpdateId ≤ u
        if d.last is not None and d.last < st.seq:
            return "stale"
        return "ok" if d.first is not None and d.last is not None and d.first <= st.seq <= d.last else "gap"
    if d.prev == st.seq:                              # далее pu = u предыдущей
        return "ok"
    return "stale" if d.last is not None and d.last <= st.seq else "gap"


def _seq_binance_spot(st, d):
    if not st.synced:                                 # первая дельта после REST-снимка: U ≤ lastUpdateId + 1 ≤ u
        if d.last is not None and d.last <= st.seq:
            return "stale"
        return "ok" if d.first is not None and d.last is not None and d.first <= st.seq + 1 <= d.last else "gap"
    return _seq_next(st, d)


def _seq_next(st, d):                                 # Gate obu (и Binance spot после первой): U = u предыдущего + 1
    if d.first is not None and d.first == st.seq + 1:
        return "ok"
    return "stale" if d.last is not None and d.last <= st.seq else "gap"


def _seq_prev(st, d):                                 # OKX prevSeqId = seqId предыдущего; Bitget pseq = seq предыдущего
    return "ok" if d.prev is not None and d.prev == st.seq else "gap"


def _seq_bybit(st, d):                                # u растёт ровно на 1 на сообщение темы; u = 1 — сброс (новый снимок)
    return "ok" if d.last is not None and d.last == st.seq + 1 and d.last != 1 else "gap"


def _seq_replace(st, d):                              # частичный стакан снимками: дельт не бывает
    return "gap"


RULES = {
    "binance_perp": (_seq_binance_perp, True),        # (проверка, снимок вне потока)
    "binance_spot": (_seq_binance_spot, True),
    "next": (_seq_next, False),
    "prev": (_seq_prev, False),
    "bybit": (_seq_bybit, False),
    "replace": (_seq_replace, False),
}


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def crc32_levels(bids, asks, depth=25):
    """Контрольная сумма по исходным строкам уровней (формат OKX/Bitget: bid:size:ask:size чередованием по depth уровням), int32 со знаком."""
    parts = []
    for i in range(depth):
        if i < len(bids):
            parts.extend(bids[i])
        if i < len(asks):
            parts.extend(asks[i])
    v = binascii.crc32(":".join(parts).encode("utf-8")) & 0xFFFFFFFF
    return v - (1 << 32) if v >= (1 << 31) else v


class BookState(object):
    """Стакан одного рынка одной монеты. status: empty -> ok | resync. raw=True — уровни хранятся исходными строками (для контрольной
    суммы), иначе числами; перевод в монеты — только при раскладке (`levels`). Не бросает на плохих данных."""

    def __init__(self, mkt, sym, rule, raw=False, cross_max_s=CROSS_MAX_S, buffer_max=5000):
        if rule not in RULES:
            raise ValueError("rule: %s" % rule)
        self.mkt, self.sym, self.rule = mkt, sym, rule
        self.check, self.rest = RULES[rule]
        self.raw = bool(raw)
        self.cross_max_s = float(cross_max_s)
        self._clear_levels()
        self.seq, self.synced = None, False
        self.bound_bid = self.bound_ask = None
        self.status, self.reason, self.since = "empty", None, None
        self.cross_since = None
        self.buffer = deque(maxlen=buffer_max)
        self.resyncs = self.gaps = self.applied = self.stale = 0
        self.t_ex = None

    # --- служебное
    def _clear_levels(self):
        self.bids, self.asks = {}, {}
        # лучшие цены без полного прохода по уровням на каждой дельте (задача 23: best() по 400–1000 уровням был главной ценой
        # CPU сборщика): кучи (цена, ключ) с ленивым удалением — ключ на вершине, которого уже нет в стакане, снимается при чтении
        self._hb, self._ha = [], []

    def _key(self, px):
        return str(px) if self.raw else _f(px)

    def _set(self, side, px, size, bounded):
        p = _f(px)
        if p is None or p <= 0:
            return
        k = str(px) if self.raw else p                  # = _key(px) без второго преобразования цены
        s = _f(size)
        book = self.bids if side == "bid" else self.asks
        if s is None or s <= 0:
            book.pop(k, None)
            return
        if bounded and ((side == "bid" and self.bound_bid is not None and p < self.bound_bid)
                        or (side == "ask" and self.bound_ask is not None and p > self.bound_ask)):
            return                                     # уровень дальше границы REST-снимка (Requirement 5.3)
        if k not in book:
            h = self._hb if side == "bid" else self._ha
            heapq.heappush(h, (-p, k) if side == "bid" else (p, k))
            if len(h) > 2 * len(book) + 64:            # снятые уровни копятся в куче: пересборка по живым уровням
                self._rebuild(side)
        book[k] = str(size) if self.raw else s

    def _rebuild(self, side):
        book = self.bids if side == "bid" else self.asks
        sign = -1.0 if side == "bid" else 1.0
        h = [(sign * _f(k), k) for k in book]
        heapq.heapify(h)
        if side == "bid":
            self._hb = h
        else:
            self._ha = h

    def resync(self, reason, now=None):
        self.status, self.reason, self.since = "resync", reason, now
        self._clear_levels()
        self.seq, self.synced = None, False
        self.bound_bid = self.bound_ask = None
        self.cross_since = None
        self.buffer.clear()
        self.resyncs += 1
        if reason == "gap":
            self.gaps += 1

    # --- применение
    def apply_snapshot(self, snap, now=None, checksum=None):
        """Снимок: замена уровней; у снимка вне потока — граница по сторонам и повтор накопленных дельт (`U ≤ id ≤ u` и далее)."""
        self._clear_levels()
        self.seq, self.synced = snap.seq, False
        self.bound_bid = self.bound_ask = None
        for px, size in snap.bids or ():
            self._set("bid", px, size, False)
        for px, size in snap.asks or ():
            self._set("ask", px, size, False)
        if snap.rest:
            self.bound_bid = min((_f(p) for p in self.bids), default=None) if self.bids else None
            self.bound_ask = max((_f(p) for p in self.asks), default=None) if self.asks else None
        self.status, self.reason, self.since = "ok", None, now
        self.cross_since = None
        self.t_ex = snap.t_ex
        self.synced = not self.rest
        if self._sides_bad(now):
            return self.status
        if checksum is not None and not self._crc_ok(checksum):
            self.resync("checksum", now)
            return self.status
        pending = list(self.buffer)
        self.buffer.clear()
        for d in pending:
            r = self.apply_delta(d, now)
            if r == "gap":
                break
        return self.status

    def apply_delta(self, d, now=None, crc_fn=None):
        """-> "ok" | "stale" (уже учтена, отброшена) | "gap" (RESYNC) | "buffered" (ждёт снимка вне потока) | "ignored" (ждёт снимка в потоке)."""
        if self.status != "ok":
            if self.rest:
                self.buffer.append(d)
                return "buffered"
            return "ignored"
        if self.seq is None:
            v = "ok"
        else:
            v = self.check(self, d)
        if v == "stale":
            self.stale += 1
            return "stale"
        if v != "ok":
            self.resync("gap", now)
            return "gap"
        for px, size in d.bids or ():
            self._set("bid", px, size, self.bound_bid is not None)
        for px, size in d.asks or ():
            self._set("ask", px, size, self.bound_ask is not None)
        if d.last is not None:
            self.seq = d.last
        self.synced = True
        self.applied += 1
        self.t_ex = d.t_ex
        if d.checksum not in (None, 0) and crc_fn is not None:     # контрольная сумма проверяется, только если биржа её прислала
            if not self._crc_ok(d.checksum, crc_fn):
                self.resync("checksum", now)
                return "gap"
        if self._sides_bad(now):
            return "gap"
        return "ok"

    def _crc_ok(self, value, crc_fn=None):
        fn = crc_fn or (lambda st: crc32_levels(*st.raw_sorted()))
        try:
            return int(fn(self)) == int(value)
        except (TypeError, ValueError):
            return False

    def _sides_bad(self, now):
        """Пустая сторона -> RESYNC сразу; перекрёст (лучший bid ≥ лучшего ask) дольше cross_max_s -> RESYNC."""
        if not self.bids or not self.asks:
            self.resync("empty_side", now)
            return True
        bb, ba = self.best()
        if bb is not None and ba is not None and bb >= ba:
            if self.cross_since is None:
                self.cross_since = now
            elif now is not None and now - self.cross_since > self.cross_max_s:
                self.resync("cross", now)
                return True
        else:
            self.cross_since = None
        return False

    def tick(self, now):
        """Проверка перекрёста по времени без новых сообщений."""
        if self.status == "ok" and self.cross_since is not None and now - self.cross_since > self.cross_max_s:
            self.resync("cross", now)

    # --- чтение
    def best(self):
        hb, ha, bids, asks = self._hb, self._ha, self.bids, self.asks
        while hb and hb[0][1] not in bids:
            heapq.heappop(hb)
        while ha and ha[0][1] not in asks:
            heapq.heappop(ha)
        if len(hb) < len(bids):                        # уровни положены в словарь в обход _set: куча по словарю
            self._rebuild("bid")
            hb = self._hb
        if len(ha) < len(asks):
            self._rebuild("ask")
            ha = self._ha
        return (-hb[0][0] if hb else None), (ha[0][0] if ha else None)

    def raw_sorted(self):
        """Уровни исходными строками: bids по убыванию цены, asks по возрастанию."""
        bids = sorted(((str(p), str(s)) for p, s in self.bids.items()), key=lambda r: -float(r[0]))
        asks = sorted(((str(p), str(s)) for p, s in self.asks.items()), key=lambda r: float(r[0]))
        return bids, asks

    def levels(self, contract_size=1.0, coin_mult=1.0):
        """-> (bids, asks) [(цена за монету, монеты)], bids по убыванию, asks по возрастанию."""
        bids = sorted(L.norm_levels(list(self.bids.items()), contract_size, coin_mult), key=lambda r: -r[0])
        asks = sorted(L.norm_levels(list(self.asks.items()), contract_size, coin_mult), key=lambda r: r[0])
        return bids, asks

    def depth(self, contract_size=1.0, coin_mult=1.0):
        """Глубина выдачи, % (у снимка вне потока — до его границы)."""
        bids, asks = self.levels(contract_size, coin_mult)
        mid = L.book_mid(bids, asks)
        bb = L.coin_px(self.bound_bid, coin_mult) if self.bound_bid is not None else None
        ba = L.coin_px(self.bound_ask, coin_mult) if self.bound_ask is not None else None
        return L.depth_pct(bids, asks, mid, bb, ba)


# ---------------------------------------------------------------- лента (Requirements 5.9, 5.10, 8.2, 8.3)
class TapeBuckets(object):
    """Секундные корзины ленты по t_rx: корзина s — сделки с t_rx в (s − 1, s]; окно на T берёт корзины T − w + 1 … T (без заглядывания
    вперёд). Непрерывность корзины — соединение было живо всю секунду; разрыв ленты снижает покрытие окна, а не даёт нули."""

    def __init__(self, slots=TAPE_SLOTS, ids_keep=4096):
        self.n = int(slots)
        self.buy = array("d", [0.0]) * self.n
        self.sell = array("d", [0.0]) * self.n
        self.sec = array("q", [-1]) * self.n
        self.cont = array("b", [0]) * self.n
        self.up = False
        self.last_sec = None
        self.ids, self.idset = deque(), set()
        self.ids_keep = int(ids_keep)
        self.side_unknown = self.dups = self.trades = 0

    def _init(self, s, cont):
        i = s % self.n
        self.sec[i], self.buy[i], self.sell[i], self.cont[i] = s, 0.0, 0.0, 1 if cont else 0
        return i

    def _advance(self, s):
        if self.last_sec is None:
            self._init(s, False)                      # первая секунда наблюдения неполная
            self.last_sec = s
            return
        if s <= self.last_sec:
            return
        start = max(self.last_sec + 1, s - self.n + 1)
        for k in range(start, s + 1):
            self._init(k, self.up)
        self.last_sec = s

    def _slot(self, s):
        i = s % self.n
        return i if self.sec[i] == s else None

    def set_up(self, up, t):
        """Соединение живо / оборвано в момент t: секунда перехода считается неполной."""
        s = int(math.ceil(t))
        self._advance(s)
        i = self._slot(s)
        if i is not None:
            self.cont[i] = 0
        self.up = bool(up)

    def tick(self, T):
        self._advance(int(T))

    def add(self, t_rx, usd, side, tid=None):
        """Сделка: side +1/−1; иная сторона — не в CVD (side_unknown); повтор tid — один раз. -> True, если учтена."""
        if side not in (1, -1):
            self.side_unknown += 1
            return False
        if tid is not None:
            if tid in self.idset:
                self.dups += 1
                return False
            self.idset.add(tid)
            self.ids.append(tid)
            if len(self.ids) > self.ids_keep:
                self.idset.discard(self.ids.popleft())
        v = _f(usd)
        if v is None or v <= 0:
            return False
        s = int(math.ceil(t_rx))
        self._advance(s)
        i = self._slot(s)
        if i is None:
            return False                               # старше окна хранения
        if side > 0:
            self.buy[i] += v
        else:
            self.sell[i] += v
        self.trades += 1
        return True

    def window(self, T, w):
        """[buy USD, sell USD, cov] за корзины T − w + 1 … T; cov — доля непрерывных секунд окна."""
        T, w = int(T), int(w)
        b = s = 0.0
        cov = 0
        for k in range(T - w + 1, T + 1):
            i = self._slot(k)
            if i is None or (self.last_sec is not None and k > self.last_sec):
                continue
            b += self.buy[i]
            s += self.sell[i]
            cov += self.cont[i]
        return [b, s, cov / float(w) if w > 0 else 0.0]


# ---------------------------------------------------------------- подписки (Requirements 5.11, 12.1, 12.2)
class RateLimiter(object):
    """Темп «не больше rate в секунду» (корзина токенов, запас burst)."""

    def __init__(self, rate, burst=None):
        self.rate = float(rate)
        self.burst = float(burst if burst is not None else rate)
        self.tokens, self.t = self.burst, None

    def _fill(self, now):
        if self.t is None:
            self.t = now
        elif now > self.t:
            self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
            self.t = now

    def take(self, now):
        self._fill(now)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def wait(self, now):
        """Секунд до следующего токена."""
        self._fill(now)
        return 0.0 if self.tokens >= 1.0 else (1.0 - self.tokens) / self.rate


def subs_per_conn(cfg, venue):
    m = ((cfg or {}).get("limits") or {}).get("subs_per_conn") or {}
    return int(m.get(venue, m.get("default", 200)))


class SubPlanner(object):
    """Раскладка тем по соединениям в пределах тем на соединение; изменения фокуса — дельтой: оставшиеся темы не переезжают, новые
    занимают свободные места, пустые соединения закрываются. Соединение — '<conn_key>#<n>'."""

    def __init__(self, per_conn):
        self.per_conn = max(1, int(per_conn))
        self.conns = {}                                   # conn_id -> {topic: Sub}
        self.where = {}                                   # topic -> conn_id
        self._n = {}

    def update(self, subs):
        """-> {"add": {conn_id: [Sub]}, "remove": {conn_id: [Sub]}, "open": [conn_id], "close": [conn_id]}."""
        want = {}
        for s in subs or ():
            want.setdefault(s.topic, s)
        out = {"add": {}, "remove": {}, "open": [], "close": []}
        for topic in [t for t in self.where if t not in want]:
            cid = self.where.pop(topic)
            out["remove"].setdefault(cid, []).append(self.conns[cid].pop(topic))
        for topic, s in want.items():
            if topic in self.where:
                continue
            cid = next((c for c in sorted(self.conns, key=self._order) if c.split("#")[0] == s.conn_key and len(self.conns[c]) < self.per_conn),
                       None)
            if cid is None:
                n = self._n.get(s.conn_key, 0)
                self._n[s.conn_key] = n + 1
                cid = "%s#%d" % (s.conn_key, n)
                self.conns[cid] = {}
                out["open"].append(cid)
            self.conns[cid][topic] = s
            self.where[topic] = cid
            out["add"].setdefault(cid, []).append(s)
        for cid in [c for c, t in self.conns.items() if not t]:
            del self.conns[cid]
            if cid in out["open"]:
                out["open"].remove(cid)
            else:
                out["close"].append(cid)
            out["add"].pop(cid, None)
        return out

    @staticmethod
    def _order(cid):
        k, _, n = cid.rpartition("#")
        return (k, int(n) if n.isdigit() else 0)

    def subs(self, cid):
        return list((self.conns.get(cid) or {}).values())

    def counts(self):
        return {"conns": len(self.conns), "topics": len(self.where), "max_per_conn": max([len(t) for t in self.conns.values()] or [0])}


def split_rejected(topics):
    """Отказ биржи на запрос подписки: запрос из нескольких тем повторяется по одной (у Bybit одна ошибочная тема отклоняет весь запрос);
    запрос из одной темы — тема отклонена. -> (повторы [[тема]], отклонённые [тема])."""
    t = list(topics or ())
    if len(t) > 1:
        return [[x] for x in t], []
    return [], t


# ---------------------------------------------------------------- REST-бюджет и восстановление (Requirements 5.5, 12.4)
class RestBudget(object):
    """Общий вес cmkt рынка в минуту (скользящее окно 60 с) с резервом опроса OI: kind="oi" расходует резерв, остальные виды
    (snap, meta, time, rate) — только остаток. При паузе OI яруса light его часть резерва (oi_light) отдаётся остатку."""

    def __init__(self, weight_per_min, oi_reserve=0, oi_light=0, window_s=60.0):
        self.total = int(weight_per_min)
        self.reserve = min(int(oi_reserve or 0), self.total)
        self.oi_light = min(int(oi_light or 0), self.reserve)
        self.window_s = float(window_s)
        self.light_paused = False
        self.rows = deque()                               # (t, w, bucket) bucket: "oi" | "rest"
        self.penalty_until = None
        self.denied = 0

    @classmethod
    def from_cfg(cls, cfg, venue, kind, n_deep=None, n_light=None):
        """Вес рынка из config/cmkt.json; часть резерва OI яруса light — по опросу oi_poll_s.light (вес 1 на запрос)."""
        w = conf.rest_weight(cfg, venue, kind) or 0
        r = conf.oi_reserve(cfg, venue, kind) or 0
        light = 0
        if r and n_light is not None:
            light = int(math.ceil(n_light * 60.0 / cfg["limits"]["oi_poll_s"]["light"]))
        elif r:
            light = r // 2
        return cls(w, r, light)

    def _trim(self, now):
        while self.rows and now - self.rows[0][0] >= self.window_s:
            self.rows.popleft()

    def reserve_eff(self):
        return self.reserve - (self.oi_light if self.light_paused else 0)

    def cap(self, bucket):
        return self.reserve_eff() if bucket == "oi" else self.total - self.reserve_eff()

    def spent(self, now, bucket=None):
        self._trim(now)
        return sum(w for _, w, b in self.rows if bucket is None or b == bucket)

    def avail(self, kind, now):
        if self.penalty_until is not None and now < self.penalty_until:
            return 0
        b = "oi" if kind == "oi" else "rest"
        return max(0, self.cap(b) - self.spent(now, b))

    def spend(self, kind, w, now):
        """Списать вес; нет места — False (запрос не делается)."""
        w = int(w)
        if self.avail(kind, now) < w:
            self.denied += 1
            return False
        self.rows.append((now, w, "oi" if kind == "oi" else "rest"))
        return True

    def penalize(self, now, s=PENALTY_S):
        """429/418: пауза без повтора, вес не расходуется."""
        self.penalty_until = now + s

    def set_resync(self, n_resync):
        """Больше RESYNC_HEAVY стаканов в RESYNC — пауза OI яруса light (её часть резерва идёт на снимки)."""
        self.light_paused = n_resync > RESYNC_HEAVY
        return self.light_paused

    def oi_allowed(self, tier):
        return not (tier == "light" and self.light_paused)

    def used(self, now):
        return {"total": self.total, "oi": self.spent(now, "oi"), "rest": self.spent(now, "rest"), "reserve": self.reserve_eff(),
                "light_paused": self.light_paused, "denied": self.denied,
                "penalty_s": max(0.0, round(self.penalty_until - now, 1)) if self.penalty_until else 0.0}


class ResyncQueue(object):
    """Очередь восстановления стаканов рынка: порядок позиции > планы > ядро > остальные deep > light, внутри — по времени запроса;
    один стакан — не чаще раза в min_gap_s (10 с). Вес снимка — adapter.snapshot_cost(mkt, prio) из остатка RestBudget."""

    def __init__(self, min_gap_s=10.0):
        self.min_gap_s = float(min_gap_s)
        self.req = {}                                     # sym -> (prio, t_req)
        self.last = {}                                    # sym -> t последней попытки

    def request(self, sym, prio, now):
        p = PRIO.get(prio, prio) if not isinstance(prio, int) else prio
        old = self.req.get(sym)
        self.req[sym] = (min(p, old[0]) if old else p, old[1] if old else now)

    def drop(self, sym):
        self.req.pop(sym, None)

    def done(self, sym, ok=True):
        if ok:
            self.req.pop(sym, None)

    def pending(self):
        return len(self.req)

    def next(self, now, budget=None, cost=None):
        """-> (sym, prio, limit) следующего снимка или None (нечего, рано или нет веса). cost(prio) -> (limit, weight)."""
        for sym, (p, t) in sorted(self.req.items(), key=lambda kv: (kv[1][0], kv[1][1], kv[0])):
            lt = self.last.get(sym)
            if lt is not None and now - lt < self.min_gap_s:
                continue
            limit, w = cost(p) if cost else (None, 0)
            if budget is not None and w and not budget.spend("snap", w, now):
                return None                               # вес кончился: ждём окна, порядок сохраняется
            self.last[sym] = now
            return sym, p, limit
        return None


# ---------------------------------------------------------------- REST
class HostDenied(Exception):
    code = HOST_DENIED


class BudgetExceeded(Exception):
    code = "BUDGET"


class HttpError(Exception):
    def __init__(self, status, msg=""):
        super().__init__("HTTP %s %s" % (status, msg))
        self.status = status


def host_check(cfg, venue, url, allow_plain=False):
    """None — хост разрешён белым списком биржи; иначе HOST_DENIED. allow_plain (только тесты) разрешает 127.0.0.1."""
    u = urlsplit(url)
    if allow_plain and u.hostname == "127.0.0.1":
        return None
    if u.scheme not in ("https", "wss") and not allow_plain:
        return HOST_DENIED
    return conf.check_host(cfg, venue, u.hostname or "")


class Http(object):
    """GET публичных эндпоинтов биржи: белый список хостов, таймаут 5 с, вес из RestBudget, 429/418 — пауза 60 с. Без ключей и подписи."""

    def __init__(self, cfg, venue, budget=None, timeout=5.0, opener=None, clock=time.time, allow_plain=False):
        self.cfg, self.venue, self.budget = cfg, venue, budget
        self.timeout, self.clock, self.allow_plain = float(timeout), clock, allow_plain
        self.opener = opener or urllib.request.urlopen
        self.calls = self.errors = 0
        self.last_weight_hdr = None

    def get(self, url, weight=1, kind="meta", timeout=None):
        """-> (status, headers dict, тело bytes). HostDenied / BudgetExceeded / HttpError / OSError. Тело запрашивается сжатым (gzip),
        если биржа умеет; timeout — свой предел запроса (метаданные Gate — 1.1–1.4 МБ, около 5 с с IP машины), иначе 5 с."""
        if host_check(self.cfg, self.venue, url, self.allow_plain) is not None:
            raise HostDenied(url)
        now = self.clock()
        if self.budget is not None and not self.budget.spend(kind, weight, now):
            raise BudgetExceeded(url)
        req = urllib.request.Request(url, headers={"User-Agent": "tb-cmkt/1", "Accept": "application/json", "Accept-Encoding": "gzip"})
        self.calls += 1
        try:
            with self.opener(req, timeout=self.timeout if timeout is None else float(timeout)) as r:
                body = r.read()
                hdr = dict((k.lower(), v) for k, v in r.headers.items())
                status = getattr(r, "status", 200)
            if str(hdr.get("content-encoding") or "").lower() == "gzip":
                body = gzip.decompress(body)
        except urllib.error.HTTPError as e:
            self.errors += 1
            if e.code in (418, 429) and self.budget is not None:
                self.budget.penalize(self.clock())
            raise HttpError(e.code, str(e.reason))
        except OSError:
            self.errors += 1
            raise
        self.last_weight_hdr = hdr.get("x-mbx-used-weight-1m")
        return status, hdr, body

    def get_json(self, url, weight=1, kind="meta", timeout=None):
        return json.loads(self.get(url, weight, kind, timeout)[2].decode("utf-8"))


# ---------------------------------------------------------------- время, задержка, живость (Requirements 4.1–4.4)
def best_offset(samples):
    """samples [(t_send, t_server, t_recv)] -> (offset_s, rtt_s) по запросу с наименьшим RTT: offset = t_server − (t_send + t_recv) / 2."""
    ok = [(r - s, srv - (s + r) / 2.0) for s, srv, r in samples or () if _f(s) is not None and _f(srv) is not None and _f(r) is not None
          and r >= s]
    if not ok:
        return None, None
    rtt, off = min(ok)
    return off, rtt


class ClockOffset(object):
    """Сдвиг часов биржи (часы биржи − локальные) раз в time_sync_s по 5 запросам серверного времени."""

    def __init__(self, every_s=600.0, n=5):
        self.every_s, self.n = float(every_s), int(n)
        self.offset = self.rtt = self.t = None

    def due(self, now):
        return self.t is None or now - self.t >= self.every_s or now < self.t

    def measure(self, server_time, clock=time.time):
        """server_time() -> секунды биржи. -> (offset_s, rtt_s) или (None, None) при сбое всех запросов."""
        samples = []
        for _ in range(self.n):
            a = clock()
            try:
                srv = server_time()
            except Exception:                              # noqa: BLE001 — любой сбой сети/разбора: запрос не в счёт
                continue
            samples.append((a, srv, clock()))
        off, rtt = best_offset(samples)
        if off is not None:
            self.offset, self.rtt, self.t = off, rtt, clock()
        return off, rtt

    def quality(self, now):
        """(rtt_ms, age_s) — для logic.cmkt.offset_ok (лидерство)."""
        if self.t is None:
            return None, None
        return self.rtt * 1000.0, now - self.t


def clock_skew(offsets_ms, warn_ms=500, need=4):
    """CLOCK_SKEW: |offset| > warn_ms у need и более бирж (сбились локальные часы)."""
    return sum(1 for v in (offsets_ms or {}).values() if _f(v) is not None and abs(v) > warn_ms) >= need


class LagMeter(object):
    """lag = t_rx − (t_ex − offset) последнего сообщения стакана или сделок, мс; p50 за 30 с (справочно)."""

    SAMPLE_S = 0.02                   # точка окна p50 не чаще раза в 20 мс (≤ 1 500 за 30 с): p50 считается раз в секунду при
                                      # публикации, а сортировка всех сообщений окна (до 150 тыс. у Bybit perp) стоила до 10 % CPU (задача 23)

    def __init__(self, window_s=30.0):
        self.window_s = float(window_s)
        self.rows = deque()
        self.last_ms = None
        self.no_tex = 0

    def on_msg(self, t_rx, t_ex, offset_s=0.0):
        if t_ex is None:
            self.no_tex += 1
            t_ex = t_rx
        lag = (t_rx - (t_ex - (offset_s or 0.0))) * 1000.0
        self.last_ms = lag
        if self.rows and t_rx - self.rows[-1][0] < self.SAMPLE_S:
            return lag
        self.rows.append((t_rx, lag))
        while self.rows and t_rx - self.rows[0][0] > self.window_s:
            self.rows.popleft()
        return lag

    def p50(self):
        v = sorted(x for _, x in self.rows)
        return v[len(v) // 2] if v else None


class Liveness(object):
    """Живость соединения: последний ответ на пинг или любое сообщение не старше silent_s; пинг раз в ping_s."""

    def __init__(self, ping_s=10.0, silent_s=15.0):
        self.ping_s, self.silent_s = float(ping_s), float(silent_s)
        self.last_any = self.last_pong = self.last_ping = None

    def on_msg(self, t):
        self.last_any = t

    def on_pong(self, t):
        self.last_pong = self.last_any = t

    def need_ping(self, t):
        return self.last_ping is None or t - self.last_ping >= self.ping_s

    def pinged(self, t):
        self.last_ping = t

    def age(self, t):
        return None if self.last_any is None else t - self.last_any

    def alive(self, t):
        a = self.age(t)
        return a is not None and a <= self.silent_s


class Backoff(object):
    """Пауза переподключения base → max (удвоение), случайный разброс ±jitter."""

    def __init__(self, base=1.0, max_s=60.0, jitter=0.2, rng=None):
        self.base, self.max_s, self.jitter = float(base), float(max_s), float(jitter)
        self.rng = rng or random.Random()
        self.attempt = 0

    def next(self):
        d = min(self.max_s, self.base * (2 ** min(self.attempt, 30)))
        self.attempt += 1
        return max(0.0, d * (1.0 + self.jitter * (2.0 * self.rng.random() - 1.0)))

    def reset(self):
        self.attempt = 0


# ---------------------------------------------------------------- контракт адаптера (Requirement 2.2)
class VenueAdapter(object):
    """Базовый класс адаптера биржи. Новая биржа — модуль tb.cmkt.ex.<venue> с классом-наследником (ADAPTER) и запись в config/cmkt.json;
    сводка, логика и хранение не меняются. Методы по умолчанию — «нет у биржи»."""

    venue = ""
    markets = ()                       # ("perp", "spot")
    book_rules = {}                    # {kind: {"deep": правило, "light": правило}}
    raw_books = ()                     # рынки, чьи стаканы хранят исходные строки (контрольная сумма)
    liq_partial = ()                   # рынки с не больше одной ликвидации на символ в секунду
    conn_max_s = None                  # принудительный разрыв соединения биржей, с (Binance 24 ч): сборщик меняет соединение заранее

    def __init__(self, cfg=None, meta=None):
        self.cfg = cfg if cfg is not None else conf.defaults()
        self.meta = meta if meta is not None else MetaCache()

    def mkt(self, kind):
        return "%s_%s" % (self.venue, kind)

    def hosts(self):
        """{"ws": [...], "rest": [...]} — сверяется с белым списком конфига."""
        return {"ws": [], "rest": []}

    def urls(self, kind):
        """{conn_key: url} маршрутов WS рынка."""
        return {}

    def load_meta(self, http):
        """{kind: [Inst]} (через select_instruments)."""
        return {}

    def plan(self, kind, syms_deep, syms_light):
        """[Sub] рынка по монетам ярусов."""
        return []

    def valid_topic(self, topic):
        return isinstance(topic, str) and bool(topic)

    def sub_msgs(self, subs, op="subscribe"):
        """Тексты subscribe/unsubscribe пачками в пределах лимита биржи."""
        return []

    def resub(self, kind, sym, subs):
        """Тексты восстановления стакана повторной подпиской (биржи со снимком по WS)."""
        book = [s for s in subs if s.sym == sym and s.kind in ("book", "book_part")]
        return self.sub_msgs(book, "unsubscribe") + self.sub_msgs(book, "subscribe")

    def ping(self):
        """Текст пинга биржи или None (пинг кадром WS). Адаптер может принять вид рынка: ping(kind) (см. ping_text)."""
        return None

    def ws_headers(self, kind):
        """Дополнительные заголовки рукопожатия WS или None (Gate: X-Gate-Size-Decimal — размеры с дробной частью)."""
        return None

    def parse(self, kind, raw, t_rx):
        """Нормализованные события; pong -> [Pong]; ответ на подписку -> [SubAck]; служебные -> []."""
        return []

    def book_state(self, kind, sym, tier="deep"):
        rule = ((self.book_rules.get(kind) or {}).get(tier)) or "replace"
        return BookState(self.mkt(kind), sym, rule, raw=self.mkt(kind) in self.raw_books)

    def continuity(self, state, delta):
        """"ok" | "gap" | "stale" по правилу стакана."""
        return state.check(state, delta) if state.seq is not None else "ok"

    def checksum(self, state):
        """Контрольная сумма по исходным строкам или None (на 2026-11-04 ни одна биржа первой очереди её не даёт)."""
        return None

    def snapshot_cost(self, kind, prio):
        """(limit, вес) снимка вне потока; (None, 0) — снимок приходит по WS."""
        return None, 0

    def book_rest(self, http, kind, sym, limit=None):
        return None

    def server_time(self, http):
        raise NotImplementedError

    def poll(self, http, kind, syms, now):
        """REST-опрос (OI Binance); у остальных []."""
        return []


# ---------------------------------------------------------------- соединение (Requirements 4.4, 12.1, 12.2)
def _lim(cfg, sec, key, default):
    return ((cfg or {}).get(sec) or {}).get(key, default)


def ping_text(adapter, kind):
    """Текст пинга адаптера: ping(kind), если адаптер принимает вид рынка (у Gate `futures.ping` и `spot.ping`), иначе ping()."""
    try:
        takes_kind = "kind" in inspect.signature(adapter.ping).parameters
    except (TypeError, ValueError):
        takes_kind = False
    return adapter.ping(kind) if takes_kind else adapter.ping()


class _Client(wsclient.WSClient):
    """WSClient, который сообщает о служебных кадрах ping/pong (живость тихого рынка с пингом кадром WS)."""

    on_control = None

    async def read_message(self):
        buf = bytearray()
        kind = None
        while True:
            fin, op, payload = await asyncio.wait_for(wsclient.read_frame(self._reader, self.max_frame), self.silence_timeout)
            if op in (wsclient.OP_PING, wsclient.OP_PONG) and self.on_control is not None:
                self.on_control(op)
            if op == wsclient.OP_PING:
                await self._send_frame(wsclient.OP_PONG, payload)
            elif op == wsclient.OP_PONG:
                pass
            elif op == wsclient.OP_CLOSE:
                try:
                    await self._send_frame(wsclient.OP_CLOSE, payload[:2])
                except Exception:                          # noqa: BLE001
                    pass
                raise wsclient.WSClosed("server closed")
            elif op in (wsclient.OP_TEXT, wsclient.OP_BIN):
                if kind is not None:
                    raise wsclient.WSProtocolError("new data frame inside fragmented message")
                kind = op
                buf = bytearray(payload)
                if fin:
                    return self._finish(kind, buf)
            elif op == wsclient.OP_CONT:
                if kind is None:
                    raise wsclient.WSProtocolError("continuation without start")
                buf += payload
                if len(buf) > self.max_frame:
                    raise wsclient.WSProtocolError("message too large")
                if fin:
                    return self._finish(kind, buf)
            else:
                raise wsclient.WSProtocolError("unknown opcode %d" % op)


class VenueConn(object):
    """Одно WS-соединение рынка на tb.marketdata.wsclient.WSClient: белый список хостов, подписки пачками не чаще sub_msgs_per_s,
    пинг раз в ping_s текстом биржи (или кадром WS), живость по любому сообщению или pong (silent_s), переподключение 1 → 60 с ±20 %
    с повтором подписок, повтор отклонённых тем по одной (sub_rejected). on_events(events, t_rx) получает разобранные события;
    on_status(event, info) — "connect" | "reconnect" (стаканы соединения -> RESYNC, разрыв ленты) | "rejected"."""

    def __init__(self, adapter, kind, conn_id, url, subs=(), on_events=None, on_status=None, cfg=None, clock=time.time,
                 allow_plain=False, rng=None, offset=None):
        self.adapter, self.kind, self.conn_id, self.url = adapter, kind, conn_id, url
        self.cfg = cfg if cfg is not None else adapter.cfg
        if host_check(self.cfg, adapter.venue, url, allow_plain) is not None:
            raise HostDenied(url)
        self.clock = clock
        self.subs = list(subs)
        self.on_events = on_events or (lambda evs, t: None)
        self.on_status = on_status or (lambda ev, info: None)
        ping_s = float(_lim(self.cfg, "sync", "ping_s", 10))
        silent_s = float(_lim(self.cfg, "sync", "silent_s", 15))
        self.live = Liveness(ping_s, silent_s)
        self.pacer = RateLimiter(float(_lim(self.cfg, "limits", "sub_msgs_per_s", 5)))
        self.client = _Client(
            url, allow_plain=allow_plain, silence_timeout=silent_s, connect_timeout=10.0,
            backoff_base=float(_lim(self.cfg, "limits", "backoff_s", 1)), backoff_max=float(_lim(self.cfg, "limits", "backoff_max_s", 60)),
            jitter=0.2, keepalive_text=ping_text(adapter, kind), keepalive_interval=ping_s, rng=rng, name=conn_id,
            on_connect=self._on_connect, extra_headers=(getattr(adapter, "ws_headers", None) or (lambda k: None))(kind))
        self.client.on_control = lambda op: self.live.on_pong(self.clock()) if op == wsclient.OP_PONG else self.live.on_msg(self.clock())
        self.offset = offset
        self.lag = LagMeter()
        self.stats = {"msgs": 0, "bytes": 0, "events": 0, "parse_err": 0, "sub_msgs": 0, "sub_rejected": 0, "rejected": [],
                      "connects": 0, "reconnects": 0}
        self._sub_task = None
        self._t_last = None
        self._queue = deque()

    @property
    def connected(self):
        return self.client.connected

    def alive(self, now=None):
        return self.client.connected and self.live.alive(self.clock() if now is None else now)

    async def _on_connect(self, client):
        now = self.clock()
        self.stats["connects"] += 1
        self.live.on_msg(now)
        if self.stats["connects"] > 1:
            self.stats["reconnects"] += 1
            self.on_status("reconnect", {"t_last": self._t_last, "t": now})
        else:
            self.on_status("connect", {"t": now})
        self._queue.clear()
        self._queue.extend(self.adapter.sub_msgs(self.subs, "subscribe"))
        if self._sub_task is not None:
            self._sub_task.cancel()
        self._sub_task = asyncio.ensure_future(self._drain())

    async def _drain(self):
        """Отправка сообщений подписки не чаще sub_msgs_per_s."""
        try:
            while self._queue and self.client.connected:
                w = self.pacer.wait(self.clock())
                if w > 0:
                    await asyncio.sleep(w)
                    continue
                if not self.pacer.take(self.clock()):
                    continue
                text = self._queue.popleft()
                try:
                    await self.client.send(text)
                    self.stats["sub_msgs"] += 1
                except wsclient.WSError:
                    self._queue.appendleft(text)
                    return
        except asyncio.CancelledError:
            raise

    def _kick(self):
        if self.client.connected and (self._sub_task is None or self._sub_task.done()):
            self._sub_task = asyncio.ensure_future(self._drain())

    def subscribe(self, subs):
        new = [s for s in subs if s.topic not in set(x.topic for x in self.subs)]
        self.subs.extend(new)
        self._queue.extend(self.adapter.sub_msgs(new, "subscribe"))
        self._kick()

    def unsubscribe(self, subs):
        gone = set(s.topic for s in subs)
        drop = [s for s in self.subs if s.topic in gone]
        self.subs = [s for s in self.subs if s.topic not in gone]
        self._queue.extend(self.adapter.sub_msgs(drop, "unsubscribe"))
        self._kick()

    def send_later(self, texts):
        """Произвольные тексты (повторная подписка для восстановления стакана) — в той же очереди с темпом."""
        self._queue.extend(texts)
        self._kick()

    def _on_ack(self, ack):
        if ack.ok:
            return
        retry, rejected = split_rejected(ack.topics)
        by_topic = dict((s.topic, s) for s in self.subs)
        for one in retry:
            s = by_topic.get(one[0])
            if s is not None:
                self._queue.extend(self.adapter.sub_msgs([s], "subscribe"))
        for t in rejected:
            self.stats["sub_rejected"] += 1
            self.stats["rejected"] = (self.stats["rejected"] + [t])[-20:]
            self.subs = [s for s in self.subs if s.topic != t]
            self.on_status("rejected", {"topic": t, "reason": ack.reason})
        self._kick()

    async def run(self):
        """Чтение до close(): t_rx до разбора, разбор адаптером, живость, отказы подписок."""
        async for raw in self.client.messages():
            t_rx = self.clock()
            self._t_last = t_rx
            self.live.on_msg(t_rx)
            self.stats["msgs"] += 1
            self.stats["bytes"] += len(raw) if raw else 0
            try:
                evs = self.adapter.parse(self.kind, raw, t_rx) or []
            except Exception:                              # noqa: BLE001 — формат изменился: сообщение отбрасывается, parse_err
                self.stats["parse_err"] += 1
                continue
            out = []
            for e in evs:
                if isinstance(e, Pong):
                    self.live.on_pong(t_rx)
                elif isinstance(e, SubAck):
                    self._on_ack(e)
                else:
                    if isinstance(e, (Trade, BookDelta, BookSnapshot)):
                        self.lag.on_msg(t_rx, e.t_ex, self.offset.offset if self.offset is not None and self.offset.offset else 0.0)
                    out.append(e)
            if out:
                self.stats["events"] += len(out)
                self.on_events(out, t_rx)

    async def close(self):
        if self._sub_task is not None:
            self._sub_task.cancel()
        await self.client.close()


def mkt_name(venue, kind):
    return "%s_%s" % (venue, kind)


def valid_venue(v):
    return isinstance(v, str) and bool(_RE_VENUE.match(v))
