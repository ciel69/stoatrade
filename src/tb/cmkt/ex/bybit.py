"""Адаптер Bybit для сводного рынка: `perp` (linear USDT) и `spot` к USDT (R1.7, задача 9; Requirements 2.1, 5.1, 5.5, 5.9, 5.11, 6.3,
9.1; design.md, «Таблица бирж»; факты живой разведки 2026-11-04 — exchanges.md, разделы 0, 3, 4).

- WS `wss://stream.bybit.com/v5/public/linear` и `.../spot`; REST — `api.bybitglobal.com` (`api.bybit.com` с IP машины отвечает 403),
  официальный хост — запасной;
- стакан: `orderbook.1000` (ярус `deep`, 200 мс; `orderbook.500` у Bybit нет, `orderbook.200` не покрывает ±0.25 % у BTC и ETH) и
  `orderbook.50` (ярус `light`, события с `part=True`); `snapshot` при подписке, далее `delta`, `u` растёт ровно на 1 на сообщение
  темы (правило `bybit`, `u = 1` — сброс); разрыв — повторная подписка темы (`resub`);
- каждая тема сверяется со списком допустимых до отправки (`valid_topic`); одна ошибочная тема отклоняет весь запрос из 10 тем, поэтому
  ответ с отказом даёт SubAck со всеми темами запроса — соединение повторяет их по одной (`adapter.split_rejected`);
- сделки `publicTrade` (`S` — сторона тейкера); тикер `tickers` (100 мс, дельты — только изменившиеся поля, прочие `None`):
  марк, индекс, bid/ask, `fundingRate` текущего периода, `fundingIntervalHour` (интервал прямо в тикере, `instruments-info` — запасной),
  `openInterest` в монетах инструмента; тикер спота без bid/ask — середина спота берётся из стакана;
- ликвидации `allLiquidation` (до 3 в секунду на символ — без `liq_partial`); `S = Buy` — ликвидирован лонг (документация v5).

Только публичные каналы, без ключей и подписи.
"""
import json
import re

from tb.cmkt import adapter as A
from tb.cmkt.ex import _common as C
from tb.logic import cmkt as L

VENUE = "bybit"
WS = {"perp": "wss://stream.bybit.com/v5/public/linear", "spot": "wss://stream.bybit.com/v5/public/spot"}
REST = ("api.bybitglobal.com", "api.bybit.com")
CATEGORY = {"perp": "linear", "spot": "spot"}
DEPTH_DEEP, DEPTH_LIGHT = 1000, 50
DEPTHS = {"perp": (1, 50, 200, 1000), "spot": (1, 50, 200, 1000)}     # orderbook.500 нет ни у linear, ни у spot (задача 2)
PER_MSG = 10                                   # тем в одном запросе подписки
_RE_TOPIC = re.compile(r"^(?:(publicTrade|tickers|allLiquidation)|orderbook\.(\d+))\.([A-Z0-9]{2,30})$")


def inst_rows(kind, items):
    """instruments-info -> нормализованные строки select_instruments."""
    out = []
    for s in items or ():
        if not isinstance(s, dict):
            continue
        st = "trading" if s.get("status") == "Trading" else str(s.get("status") or "").lower()
        tick = (s.get("priceFilter") or {}).get("tickSize")
        if kind == "perp":
            fi = L.num(s.get("fundingInterval"))
            out.append({"native": s.get("symbol"), "base": s.get("baseCoin"), "quote": s.get("quoteCoin"), "settle": s.get("settleCoin"),
                        "type": "linear_perp" if s.get("contractType") == "LinearPerpetual" else "future", "contract_size": 1.0,
                        "tick": tick, "funding_h": fi / 60.0 if fi else None, "status": st})
        else:
            out.append({"native": s.get("symbol"), "base": s.get("baseCoin"), "quote": s.get("quoteCoin"), "type": "spot", "tick": tick,
                        "status": st})
    return out


class BybitAdapter(A.VenueAdapter):
    venue = VENUE
    markets = ("perp", "spot")
    book_rules = {"perp": {"deep": "bybit", "light": "bybit"}, "spot": {"deep": "bybit", "light": "bybit"}}
    liq_partial = ()
    oi_every_s = {"perp": 9.0}                   # openInterest тикера меняется раз в ~9 с (задача 2)

    def __init__(self, cfg=None, meta=None):
        super().__init__(cfg, meta)
        self.rest = C.HostChain(REST)
        self.stats = C.new_stats()
        self._id = 0
        self._reqs = C.IdMap()

    def hosts(self):
        return {"ws": ["stream.bybit.com"], "rest": list(REST)}

    def urls(self, kind):
        return {"main": WS[kind]}

    # --- метаданные
    def load_meta(self, http, now=0.0, force=False):
        out = {}
        for kind in self.markets:
            mkt = self.mkt(kind)
            if not force and not self.meta.due(mkt, now):
                continue
            items, cursor, ok = [], "", True
            for _ in range(20):                                      # постранично по 1000
                path = "/v5/market/instruments-info?category=%s&limit=1000" % CATEGORY[kind]
                if cursor:
                    path += "&cursor=" + cursor
                try:
                    r = self.rest.get_json(http, path, 1)
                except (A.HttpError, A.BudgetExceeded, ValueError, OSError):
                    ok = False
                    break
                res = (r or {}).get("result") or {}
                items.extend(res.get("list") or ())
                cursor = res.get("nextPageCursor") or ""
                if not cursor:
                    break
            if not ok:
                continue
            insts = A.select_instruments(inst_rows(kind, items), kind)
            if insts:
                self.meta.update(mkt, insts, now)
                out[kind] = insts
        return out

    def native(self, kind, sym):
        i = self.meta.get(self.mkt(kind), sym)
        return i.native if i is not None else None

    # --- подписки
    def plan(self, kind, syms_deep, syms_light, resync=()):
        out = []
        for tier, syms in (("deep", syms_deep), ("light", syms_light)):
            for s in syms or ():
                n = self.native(kind, s)
                if n is None:
                    continue
                if tier == "deep":
                    out.append(A.Sub("main", "orderbook.%d.%s" % (DEPTH_DEEP, n), "book", s))
                else:
                    out.append(A.Sub("main", "orderbook.%d.%s" % (DEPTH_LIGHT, n), "book_part", s))
                out.append(A.Sub("main", "publicTrade." + n, "trades", s))
                out.append(A.Sub("main", "tickers." + n, "ticker", s))
                if kind == "perp":
                    out.append(A.Sub("main", "allLiquidation." + n, "liq", s))
        return out

    def valid_topic(self, topic, kind=None):
        """Тема из списка допустимых тем биржи (orderbook.<1|50|200|1000>, publicTrade, tickers, allLiquidation — только linear)."""
        m = _RE_TOPIC.match(topic) if isinstance(topic, str) else None
        if not m:
            return False
        if m.group(2) is not None:
            return int(m.group(2)) in DEPTHS.get(kind or "perp", DEPTHS["perp"])
        return not (m.group(1) == "allLiquidation" and kind == "spot")

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
            rid = "s%d" % self._id
            chunk = topics[i:i + PER_MSG]
            self._reqs.put(rid, chunk, op)
            out.append(json.dumps({"op": op, "args": chunk, "req_id": rid}, separators=(",", ":")))
        return out

    def ping(self):
        return '{"op":"ping"}'

    # --- время
    def server_time(self, http, kind="perp"):
        r = self.rest.get_json(http, "/v5/market/time", 1, "time")
        res = (r or {}).get("result") or {}
        if L.num(res.get("timeNano")):
            return float(res["timeNano"]) / 1e9
        return float(r["time"]) / 1000.0

    # --- разбор
    def parse(self, kind, raw, t_rx):
        m = json.loads(raw)
        if not isinstance(m, dict):
            self.stats["unknown"] += 1
            return []
        topic = m.get("topic")
        if isinstance(topic, str):
            return self._topic(kind, topic, m, t_rx)
        op = m.get("op")
        if op in ("ping", "pong") or m.get("ret_msg") == "pong":
            return [A.Pong(self.mkt(kind), t_rx)]
        if op in ("subscribe", "unsubscribe"):
            ent = self._reqs.pop(m.get("req_id"))
            if ent is None or ent[1] != "subscribe":
                return []
            ok = m.get("success") is True
            return [A.SubAck(self.mkt(kind), t_rx, ent[0], ok, None if ok else str(m.get("ret_msg")))]
        self.stats["unknown"] += 1
        return []

    def _inst(self, kind, native):
        i = self.meta.native(self.mkt(kind), str(native or ""))
        if i is None:
            self.stats["skip"] += 1
        return i

    def _topic(self, kind, topic, m, t_rx):
        mkt = self.mkt(kind)
        ch, _, rest = topic.partition(".")
        d = m.get("data")
        if ch == "orderbook":
            depth, _, native = rest.partition(".")
            i = self._inst(kind, native)
            if i is None or not isinstance(d, dict):
                return []
            part = depth != str(DEPTH_DEEP)
            t_ex = C.ms(m.get("cts")) or C.ms(m.get("ts"))
            u = d.get("u")
            if m.get("type") == "snapshot":
                return [A.BookSnapshot(mkt, i.sym, t_ex, t_rx, u, C.lv(d.get("b")), C.lv(d.get("a")), False, part)]
            return [A.BookDelta(mkt, i.sym, t_ex, t_rx, None, u, None, None, C.lv(d.get("b")), C.lv(d.get("a")), part)]
        if ch == "publicTrade":
            out = []
            for t in d or ():
                i = self._inst(kind, t.get("s") or rest)
                if i is None:
                    continue
                px = L.coin_px(t.get("p"), i.coin_mult)
                q = L.coins(t.get("v"), i.contract_size or 1.0, i.coin_mult)
                if px is None or q is None:
                    self.stats["unknown"] += 1
                    continue
                side = {"Buy": 1, "Sell": -1}.get(t.get("S"), 0)
                out.append(A.Trade(mkt, i.sym, C.ms(t.get("T")) or C.ms(m.get("ts")), t_rx, px, q, side,
                                   None if t.get("i") is None else str(t["i"])))
            return out
        if ch == "tickers":
            if not isinstance(d, dict):
                return []
            i = self._inst(kind, d.get("symbol") or rest)
            if i is None:
                return []
            cp = lambda k: L.coin_px(d.get(k), i.coin_mult) if d.get(k) not in (None, "") else None     # noqa: E731
            oi = L.coins(d.get("openInterest"), i.contract_size or 1.0, i.coin_mult) if d.get("openInterest") not in (None, "") else None
            fh = L.num(d.get("fundingIntervalHour"))
            if not fh and kind == "perp" and m.get("type") == "snapshot":
                fh = i.funding_h                                          # запасной источник интервала — instruments-info
            return [A.Ticker(mkt, i.sym, C.ms(m.get("ts")), t_rx, mark=cp("markPrice"), index=cp("indexPrice"),
                             bid=cp("bid1Price"), ask=cp("ask1Price"), funding=L.num(d.get("fundingRate")), funding_h=fh or None,
                             oi_coins=oi, turn24_usd=L.num(d.get("turnover24h")))]
        if ch == "allLiquidation":
            out = []
            for t in d or ():
                i = self._inst(kind, t.get("s") or rest)
                if i is None:
                    continue
                px = L.coin_px(t.get("p"), i.coin_mult)
                q = L.coins(t.get("v"), i.contract_size or 1.0, i.coin_mult)
                side = {"Buy": "long", "Sell": "short"}.get(t.get("S"))        # S — сторона ликвидированной позиции (Buy — лонг)
                if not px or not q or side is None:
                    self.stats["unknown"] += 1
                    continue
                out.append(A.Liquidation(mkt, i.sym, C.ms(t.get("T")) or C.ms(m.get("ts")), t_rx, px, q, side))
            return out
        self.stats["unknown"] += 1
        return []


ADAPTER = BybitAdapter
