"""Адаптер Binance для сводного рынка: USDT-M `perp` и `spot` к USDT (R1.7, задача 8; Requirements 2.1, 5.1, 5.3, 5.5, 5.6, 5.9, 7.5,
9.1, 12.4; design.md, «Таблица бирж»; факты живой разведки 2026-11-04 — exchanges.md, разделы 0–2).

`perp`:
- маршруты `wss://fstream.binance.com/public/stream` (стакан `@depth@100ms`, частичный `@depth20@100ms`/`@depth20@500ms`) и
  `wss://fstream.binance.com/market/stream` (`@aggTrade`, `@markPrice@1s`, `!forceOrder@arr`); старый `/stream` сделки и марк молча
  не отдаёт;
- снимок стакана — WS API `wss://ws-fapi.binance.com/ws-fapi/v1`, метод `depth` (`book_req`; вес 20/10/5 при 1000/500/100, общий
  лимит 2400 на IP); REST `fapi.binance.com` с IP машины отвечает 451, запасной REST-снимок — `www.binance.com/fapi/v1/depth`;
- непрерывность: первая дельта после снимка `U ≤ lastUpdateId ≤ u`, далее `pu` = прошлый `u` (правило `binance_perp`);
- на время RESYNC стакана — частичный `@depth20@100ms` (`plan(..., resync=...)`, события с `part=True`);
- OI — только REST (`poll`): `fapi.binance.com`, при отказе `www.binance.com` (неофициальный хост), при отказе обоих OI нет;
- ликвидации `!forceOrder@arr` (не больше 1 на символ в секунду — `liq_partial`), символы `*USDC` отбрасываются;
- биржа рвёт соединение через 24 ч (`conn_max_s`): сборщик открывает новое за 10 мин до срока.

`spot`: `wss://data-stream.binance.vision/stream` и REST `https://data-api.binance.vision/api/v3/*` (основные хосты — 451); первая
дельта после REST-снимка `U ≤ lastUpdateId + 1 ≤ u`, далее `U` = прошлый `u` + 1 (правило `binance_spot`); снимок `limit=1000`, вес 50;
`@bookTicker` спота без поля времени — середина спота берётся из стакана.

Агрессор `@aggTrade`: `m=true` — покупатель мейкер, агрессор продаёт. Цены и размеры приводятся к монетам через `tb.logic.cmkt`
(`1000PEPEUSDT` — цена за 1000 монет). Только публичные каналы, без ключей и подписи.
"""
import json
import re
import time

from tb.cmkt import adapter as A
from tb.cmkt.ex import _common as C
from tb.logic import cmkt as L

VENUE = "binance"
WS_PUBLIC = "wss://fstream.binance.com/public/stream"
WS_MARKET = "wss://fstream.binance.com/market/stream"
WS_API = "wss://ws-fapi.binance.com/ws-fapi/v1"
WS_SPOT = "wss://data-stream.binance.vision/stream"
REST_PERP = ("fapi.binance.com", "www.binance.com")
REST_SPOT = ("api.binance.com", "data-api.binance.vision")
LIQ_TOPIC = "!forceOrder@arr"
PER_MSG = 50                                   # потоков в одном SUBSCRIBE
DEPTH_WEIGHT_PERP = {1000: 20, 500: 10, 100: 5}
DEPTH_WEIGHT_SPOT = {1000: 50, 500: 25, 100: 5}
FUNDING_H_DEFAULT = 8.0                        # символа нет в fundingInfo — 8 ч
_RE_TOPIC = re.compile(r"^([a-z0-9]{2,30})@(aggTrade|depth@100ms|depth20@(100|500|1000)ms|markPrice@1s|bookTicker)$")


def perp_rows(info, funding=None):
    """exchangeInfo `fapi` + fundingInfo -> нормализованные строки для select_instruments. Только `PERPETUAL` (TradFi и срочные — нет)."""
    fh = {}
    for r in funding or ():
        if isinstance(r, dict) and r.get("symbol") and L.num(r.get("fundingIntervalHours")):
            fh[r["symbol"]] = float(r["fundingIntervalHours"])
    out = []
    for s in (info or {}).get("symbols") or ():
        if not isinstance(s, dict):
            continue
        ct = s.get("contractType")
        tick = next((f.get("tickSize") for f in s.get("filters") or () if isinstance(f, dict) and f.get("filterType") == "PRICE_FILTER"),
                    None)
        out.append({"native": s.get("symbol"), "base": s.get("baseAsset"), "quote": s.get("quoteAsset"), "settle": s.get("marginAsset"),
                    "type": "linear_perp" if ct == "PERPETUAL" else "future", "contract_size": 1.0, "tick": tick,
                    "funding_h": fh.get(s.get("symbol"), FUNDING_H_DEFAULT),
                    "status": "trading" if s.get("status") == "TRADING" else str(s.get("status") or "").lower()})
    return out


def spot_rows(info):
    out = []
    for s in (info or {}).get("symbols") or ():
        if not isinstance(s, dict):
            continue
        tick = next((f.get("tickSize") for f in s.get("filters") or () if isinstance(f, dict) and f.get("filterType") == "PRICE_FILTER"),
                    None)
        out.append({"native": s.get("symbol"), "base": s.get("baseAsset"), "quote": s.get("quoteAsset"), "type": "spot", "tick": tick,
                    "status": "trading" if s.get("status") == "TRADING" else str(s.get("status") or "").lower()})
    return out


class BinanceAdapter(A.VenueAdapter):
    venue = VENUE
    markets = ("perp", "spot")
    book_rules = {"perp": {"deep": "binance_perp", "light": "replace"}, "spot": {"deep": "binance_spot", "light": "replace"}}
    liq_partial = ("binance_perp",)
    conn_max_s = 24 * 3600.0
    oi_every_s = {"perp": None}                  # OI по REST с шагом oi_poll_s конфига (10 с deep, 60 с light)

    def __init__(self, cfg=None, meta=None):
        super().__init__(cfg, meta)
        self.rest = {"perp": C.HostChain(REST_PERP), "spot": C.HostChain(REST_SPOT)}
        self.stats = C.new_stats()
        self._id = 0
        self._subs = C.IdMap()
        self._snaps = C.IdMap()

    # --- маршруты и хосты
    def hosts(self):
        return {"ws": ["fstream.binance.com", "ws-fapi.binance.com", "data-stream.binance.vision"],
                "rest": list(REST_PERP) + list(REST_SPOT)}

    def urls(self, kind):
        if kind == "perp":
            return {"public": WS_PUBLIC, "market": WS_MARKET, "wsapi": WS_API}
        return {"main": WS_SPOT}

    # --- метаданные
    def load_meta(self, http, now=0.0, force=False):
        """{kind: [Inst]} по REST с кешем MetaCache (6 ч); сбой загрузки оставляет прежние метаданные."""
        out = {}
        for kind in self.markets:
            mkt = self.mkt(kind)
            if not force and not self.meta.due(mkt, now):
                continue
            try:
                if kind == "perp":
                    info = self.rest["perp"].get_json(http, "/fapi/v1/exchangeInfo", 1)
                    try:
                        fund = self.rest["perp"].get_json(http, "/fapi/v1/fundingInfo", 1)
                    except (A.HttpError, A.BudgetExceeded, ValueError):
                        fund = []
                    rows = perp_rows(info, fund)
                else:
                    info = self.rest["spot"].get_json(http, "/api/v3/exchangeInfo?permissions=SPOT&showPermissionSets=false"
                                                            "&symbolStatus=TRADING", 20)
                    rows = spot_rows(info)
            except (A.HttpError, A.BudgetExceeded, ValueError, OSError):
                continue
            insts = A.select_instruments(rows, kind)
            if insts:
                self.meta.update(mkt, insts, now)
                out[kind] = insts
        return out

    def native(self, kind, sym):
        i = self.meta.get(self.mkt(kind), sym)
        return i.native if i is not None else None

    # --- подписки
    def plan(self, kind, syms_deep, syms_light, resync=()):
        """[Sub]: perp — стакан на маршруте public, сделки/марк/ликвидации на market; монеты в RESYNC (resync) получают частичный
        @depth20@100ms на время восстановления. OI — не тема (REST, poll)."""
        out, rs = [], set(resync or ())
        perp = kind == "perp"
        book_conn, tape_conn = ("public", "market") if perp else ("main", "main")
        for tier, syms in (("deep", syms_deep), ("light", syms_light)):
            for s in syms or ():
                n = self.native(kind, s)
                if n is None:
                    continue
                n = n.lower()
                if tier == "deep":
                    out.append(A.Sub(book_conn, n + "@depth@100ms", "book", s))
                    if s in rs:
                        out.append(A.Sub(book_conn, n + "@depth20@100ms", "book_part", s))
                else:
                    out.append(A.Sub(book_conn, n + ("@depth20@500ms" if perp else "@depth20@1000ms"), "book_part", s))
                out.append(A.Sub(tape_conn, n + "@aggTrade", "trades", s))
                if perp:
                    out.append(A.Sub(tape_conn, n + "@markPrice@1s", "ticker", s))
        if perp and out:
            out.append(A.Sub("market", LIQ_TOPIC, "liq", "*"))
        return out

    def valid_topic(self, topic):
        return isinstance(topic, str) and (topic == LIQ_TOPIC or bool(_RE_TOPIC.match(topic)))

    def sub_msgs(self, subs, op="subscribe"):
        topics = []
        for s in subs or ():
            if self.valid_topic(s.topic):
                topics.append(s.topic)
            else:
                self.stats["invalid_topic"] += 1
        out = []
        for i in range(0, len(topics), PER_MSG):
            self._id += 1
            chunk = topics[i:i + PER_MSG]
            self._subs.put(self._id, chunk, op)
            out.append(json.dumps({"method": "SUBSCRIBE" if op == "subscribe" else "UNSUBSCRIBE", "params": chunk, "id": self._id},
                                  separators=(",", ":")))
        return out

    def resub(self, kind, sym, subs):
        """Стакан Binance восстанавливается снимком вне потока (book_req / book_rest), не переподпиской."""
        return []

    def ping(self):
        return None                                  # пинг кадром WS; биржа отвечает pong

    # --- снимок стакана
    def snapshot_cost(self, kind, prio):
        p = A.PRIO.get(prio, 9) if isinstance(prio, str) else int(prio)
        if kind == "spot":
            return 1000, DEPTH_WEIGHT_SPOT[1000]
        limit = 1000 if p <= A.PRIO["core"] else 500
        return limit, DEPTH_WEIGHT_PERP[limit]

    def book_req(self, kind, sym, limit=1000):
        """Запрос снимка `perp` через WS API: -> (conn_key, текст) или None (спот — REST book_rest). Вес списывает ResyncQueue."""
        if kind != "perp":
            return None
        n = self.native(kind, sym)
        if n is None:
            return None
        self._id += 1
        rid = "d%d" % self._id
        self._snaps.put(rid, (sym,), kind)
        limit = limit if limit in DEPTH_WEIGHT_PERP else 1000
        return "wsapi", json.dumps({"id": rid, "method": "depth", "params": {"symbol": n, "limit": limit}}, separators=(",", ":"))

    def book_rest(self, http, kind, sym, limit=None):
        """Снимок по REST (spot — основной путь; perp — запасной через www.binance.com). Вес уже списан ResyncQueue (weight=0)."""
        n = self.native(kind, sym)
        if n is None:
            return None
        limit = limit or 1000
        path = ("/fapi/v1/depth?symbol=%s&limit=%d" if kind == "perp" else "/api/v3/depth?symbol=%s&limit=%d") % (n, limit)
        t_rx = time.time()
        try:
            r = self.rest[kind].get_json(http, path, 0, "snap")
        except (A.HttpError, A.BudgetExceeded, ValueError, OSError):
            self.stats["snap_err"] += 1
            return None
        return self._snap(kind, sym, r, t_rx)

    def _snap(self, kind, sym, r, t_rx):
        if not isinstance(r, dict) or r.get("lastUpdateId") is None:
            self.stats["snap_err"] += 1
            return None
        return A.BookSnapshot(self.mkt(kind), sym, C.ms(r.get("E")), t_rx, int(r["lastUpdateId"]), C.lv(r.get("bids")), C.lv(r.get("asks")),
                              True)

    # --- время
    def server_time(self, http, kind="perp"):
        r = self.rest[kind].get_json(http, "/fapi/v1/time" if kind == "perp" else "/api/v3/time", 1, "time")
        return float(r["serverTime"]) / 1000.0

    # --- OI (REST)
    def poll(self, http, kind, syms, now):
        """OI `perp` по REST (вес 1, бюджет «oi»): -> [Ticker(oi_coins)]. Нехватка веса — остаток монет в этот раз не опрашивается;
        отказ всех хостов — OI нет (None по свежести, Requirement 7.5)."""
        if kind != "perp":
            return []
        out, mkt = [], self.mkt(kind)
        for s in syms or ():
            i = self.meta.get(mkt, s)
            if i is None:
                continue
            try:
                r = self.rest["perp"].get_json(http, "/fapi/v1/openInterest?symbol=%s" % i.native, 1, "oi")
            except A.BudgetExceeded:
                break
            except (A.HttpError, ValueError, OSError):
                self.stats["oi_err"] += 1
                break
            oi = L.coins((r or {}).get("openInterest"), i.contract_size or 1.0, i.coin_mult)
            if oi is not None:
                out.append(A.Ticker(mkt, s, C.ms(r.get("time")), now, oi_coins=oi))
        return out

    # --- разбор
    def parse(self, kind, raw, t_rx):
        m = json.loads(raw)
        if not isinstance(m, dict):
            self.stats["unknown"] += 1
            return []
        if "stream" in m:
            return self._stream(kind, str(m["stream"]), m.get("data") or {}, t_rx)
        if "id" in m:
            return self._reply(kind, m, t_rx)
        self.stats["unknown"] += 1
        return []

    def _reply(self, kind, m, t_rx):
        rid = m.get("id")
        ent = self._snaps.pop(rid) if isinstance(rid, str) else None
        if ent is not None:                                   # ответ WS API depth (book_req)
            sym = ent[0][0]
            if m.get("status") != 200 or not isinstance(m.get("result"), dict):
                self.stats["snap_err"] += 1
                return []
            s = self._snap(ent[1], sym, m["result"], t_rx)
            return [s] if s is not None else []
        ent = self._subs.pop(rid)
        if ent is None:
            return []
        topics, op = ent
        if op != "subscribe":
            return []
        ok = "error" not in m
        return [A.SubAck(self.mkt(kind), t_rx, topics, ok, None if ok else str((m.get("error") or {}).get("msg")))]

    def _inst(self, kind, native):
        i = self.meta.native(self.mkt(kind), str(native or "").upper())
        if i is None:
            self.stats["skip"] += 1
        return i

    def _stream(self, kind, name, d, t_rx):
        mkt = self.mkt(kind)
        if name == LIQ_TOPIC:
            return self._liq(kind, d, t_rx)
        native, _, ch = name.partition("@")
        if not isinstance(d, dict):
            self.stats["unknown"] += 1
            return []
        i = self._inst(kind, d.get("s") or native)
        if i is None:
            return []
        t_ex = C.ms(d.get("E"))
        if ch.startswith("depth20") or ch.startswith("depth10") or ch.startswith("depth5"):
            if kind == "perp":
                return [A.BookSnapshot(mkt, i.sym, t_ex, t_rx, d.get("u"), C.lv(d.get("b")), C.lv(d.get("a")), False, True)]
            return [A.BookSnapshot(mkt, i.sym, None, t_rx, d.get("lastUpdateId"), C.lv(d.get("bids")), C.lv(d.get("asks")), False, True)]
        if ch.startswith("depth"):
            return [A.BookDelta(mkt, i.sym, t_ex, t_rx, d.get("U"), d.get("u"), d.get("pu") if kind == "perp" else None, None,
                                C.lv(d.get("b")), C.lv(d.get("a")))]
        if ch == "aggTrade":
            px = L.coin_px(d.get("p"), i.coin_mult)
            q = L.coins(d.get("q"), i.contract_size or 1.0, i.coin_mult)
            if px is None or q is None:
                self.stats["unknown"] += 1
                return []
            side = -1 if d.get("m") is True else (1 if d.get("m") is False else 0)
            return [A.Trade(mkt, i.sym, C.ms(d.get("T")) or t_ex, t_rx, px, q, side, None if d.get("a") is None else str(d["a"]))]
        if ch.startswith("markPrice"):
            return [A.Ticker(mkt, i.sym, t_ex, t_rx, mark=L.coin_px(d.get("p"), i.coin_mult), index=L.coin_px(d.get("i"), i.coin_mult),
                             funding=L.num(d.get("r")), funding_h=i.funding_h or FUNDING_H_DEFAULT)]
        if ch == "bookTicker":
            return [A.Ticker(mkt, i.sym, t_ex, t_rx, bid=L.coin_px(d.get("b"), i.coin_mult), ask=L.coin_px(d.get("a"), i.coin_mult))]
        self.stats["unknown"] += 1
        return []

    def _liq(self, kind, d, t_rx):
        o = (d or {}).get("o") if isinstance(d, dict) else None
        if not isinstance(o, dict):
            self.stats["unknown"] += 1
            return []
        s = str(o.get("s") or "")
        if s.endswith("USDC"):
            self.stats["skip"] += 1
            return []
        i = self._inst(kind, s)
        if i is None:
            return []
        px = L.coin_px(o.get("ap") if L.num(o.get("ap")) else o.get("p"), i.coin_mult)
        q = L.coins(o.get("z") if L.num(o.get("z")) else o.get("q"), i.contract_size or 1.0, i.coin_mult)
        side = {"SELL": "long", "BUY": "short"}.get(str(o.get("S") or "").upper())    # ордер на продажу — ликвидирован лонг
        if not px or not q or side is None:
            self.stats["unknown"] += 1
            return []
        return [A.Liquidation(self.mkt(kind), i.sym, C.ms(o.get("T")) or C.ms(d.get("E")), t_rx, px, q, side)]


ADAPTER = BinanceAdapter
