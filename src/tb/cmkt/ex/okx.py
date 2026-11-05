"""Адаптер OKX для сводного рынка: `perp` (`SWAP`, линейные USDT) и `spot` к USDT (R1.7, задача 10; Requirements 2.1, 3.4, 5.1, 5.2,
5.7, 5.9, 7.5, 9.1, 12.1; design.md, «Таблица бирж»; факты живой разведки 2026-11-04 — exchanges.md, раздел 5).

- WS `wss://ws.okx.com:443/ws/v5/public` (на 8443 биржа шлёт `notice 64010` о выводе порта; уведомления считаются, не рвут разбор);
  пинг текстом `ping` → `pong`; REST `https://www.okx.com/api/v5/*`;
- стакан `books` (400 уровней) для обоих ярусов: снимок при подписке, далее `update`; непрерывность `prevSeqId` = прошлый `seqId`
  (правило `prev`); поле `checksum` на 2026-11-04 всегда 0 — не проверяется; ненулевая сумма проверяется по исходным строкам
  (стаканы OKX хранят строки, `raw_books`), несовпадение — RESYNC; восстановление — повторная подписка `books`;
- сделки `trades` (`side` тейкера), размер `sz × ctVal` у `SWAP`; `tickers` (bid/ask), `mark-price`, `funding-rate` (ставка текущего
  периода, интервал `nextFundingTime − fundingTime`), `open-interest` (`oiCcy` — монеты; раз в 5–16 с, свежесть 16 с);
- ликвидации `liquidation-orders` (`instType: SWAP`; не больше 1 на инструмент в секунду — `liq_partial`); биржа повторяет старые
  события (возраст до 20 ч) — дедупликация по (`instId`, `ts`, `sz`) и отбор по возрасту `LIQ_MAX_AGE_S` (по часам биржи:
  `t_rx + offset_s`, offset ставит сборщик); `*-USDC-SWAP` и прочие котировки отбрасываются;
- предел биржи — 480 запросов подписки в час на соединение (`SUB_REQ_PER_HOUR`): темы идут пачками по `PER_MSG` в одном запросе.

Только публичные каналы, без ключей и подписи.
"""
import json
import re

from tb.cmkt import adapter as A
from tb.cmkt.ex import _common as C
from tb.logic import cmkt as L

VENUE = "okx"
WS_URL = "wss://ws.okx.com:443/ws/v5/public"
REST = ("www.okx.com",)
INST_TYPE = {"perp": "SWAP", "spot": "SPOT"}
PER_MSG = 20                                   # аргументов в одном запросе подписки (64 КБ на запрос — с большим запасом)
SUB_REQ_PER_HOUR = 480
LIQ_MAX_AGE_S = 60.0
LIQ_TOPIC = "liquidation-orders:SWAP"
CHANNELS = {"books": "book", "trades": "trades", "tickers": "ticker", "mark-price": "ticker", "funding-rate": "ticker",
            "open-interest": "oi"}
_RE_TOPIC = re.compile(r"^(books|trades|tickers|mark-price|funding-rate|open-interest):([A-Z0-9]{1,20}-USDT(-SWAP)?)$")
_RE_ERR = re.compile(r"channel:([a-z\-]+),\s*(?:instId|instType):([A-Za-z0-9\-]+)")


def inst_rows(kind, items):
    """public/instruments -> нормализованные строки select_instruments (`ctVal` — размер контракта в монетах `ctValCcy`)."""
    out = []
    for s in items or ():
        if not isinstance(s, dict):
            continue
        st = "trading" if s.get("state") == "live" else str(s.get("state") or "").lower()
        if kind == "perp":
            uly = str(s.get("uly") or s.get("instFamily") or "")
            quote = uly.split("-")[1] if "-" in uly else ""
            linear = s.get("ctType") == "linear" and str(s.get("instId") or "").endswith("-SWAP")
            out.append({"native": s.get("instId"), "base": s.get("ctValCcy") if linear else uly.split("-")[0], "quote": quote,
                        "settle": s.get("settleCcy"), "type": "linear_perp" if linear else "inverse_perp",
                        "contract_size": L.num(s.get("ctVal")), "tick": s.get("tickSz"), "status": st})
        else:
            out.append({"native": s.get("instId"), "base": s.get("baseCcy"), "quote": s.get("quoteCcy"), "type": "spot",
                        "tick": s.get("tickSz"), "status": st})
    return out


def topic_arg(topic):
    ch, _, v = topic.partition(":")
    return {"channel": ch, "instType": v} if ch == "liquidation-orders" else {"channel": ch, "instId": v}


class OkxAdapter(A.VenueAdapter):
    venue = VENUE
    markets = ("perp", "spot")
    book_rules = {"perp": {"deep": "prev", "light": "prev"}, "spot": {"deep": "prev", "light": "prev"}}
    raw_books = ("okx_perp", "okx_spot")
    liq_partial = ("okx_perp",)
    oi_every_s = {"perp": 16.0}                  # open-interest раз в 5–16 с (задача 2)

    def __init__(self, cfg=None, meta=None):
        super().__init__(cfg, meta)
        self.rest = C.HostChain(REST)
        self.stats = C.new_stats()
        self.offset_s = 0.0                      # часы биржи − локальные (ставит сборщик по ClockOffset): возраст ликвидаций
        self._liq = C.Dedup()

    def hosts(self):
        return {"ws": ["ws.okx.com"], "rest": list(REST)}

    def urls(self, kind):
        return {"main": WS_URL}

    # --- метаданные
    def load_meta(self, http, now=0.0, force=False):
        out = {}
        for kind in self.markets:
            mkt = self.mkt(kind)
            if not force and not self.meta.due(mkt, now):
                continue
            try:
                r = self.rest.get_json(http, "/api/v5/public/instruments?instType=%s" % INST_TYPE[kind], 1)
            except (A.HttpError, A.BudgetExceeded, ValueError, OSError):
                continue
            insts = A.select_instruments(inst_rows(kind, (r or {}).get("data")), kind)
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
        perp = kind == "perp"
        for tier, syms in (("deep", syms_deep), ("light", syms_light)):
            for s in syms or ():
                n = self.native(kind, s)
                if n is None:
                    continue
                out.append(A.Sub("main", "books:" + n, "book" if tier == "deep" else "book_part", s))
                out.append(A.Sub("main", "trades:" + n, "trades", s))
                out.append(A.Sub("main", "tickers:" + n, "ticker", s))
                if perp:
                    out.append(A.Sub("main", "mark-price:" + n, "ticker", s))
                    out.append(A.Sub("main", "funding-rate:" + n, "ticker", s))
                    out.append(A.Sub("main", "open-interest:" + n, "oi", s))
        if perp and out:
            out.append(A.Sub("main", LIQ_TOPIC, "liq", "*"))
        return out

    def valid_topic(self, topic):
        return isinstance(topic, str) and (topic == LIQ_TOPIC or bool(_RE_TOPIC.match(topic)))

    def sub_msgs(self, subs, op="subscribe"):
        args = []
        for s in subs or ():
            if self.valid_topic(s.topic):
                args.append(topic_arg(s.topic))
            else:
                self.stats["invalid_topic"] += 1
        return [json.dumps({"op": op, "args": args[i:i + PER_MSG]}, separators=(",", ":")) for i in range(0, len(args), PER_MSG)]

    def ping(self):
        return "ping"

    def checksum(self, state):
        """CRC32 OKX по исходным строкам 25 уровней (bid:size:ask:size); проверяется, только если биржа прислала ненулевую сумму."""
        if not state.raw:
            return None
        return A.crc32_levels(*state.raw_sorted())

    # --- время
    def server_time(self, http, kind="perp"):
        r = self.rest.get_json(http, "/api/v5/public/time", 1, "time")
        return float(((r or {}).get("data") or [{}])[0]["ts"]) / 1000.0

    # --- разбор
    def parse(self, kind, raw, t_rx):
        if raw == "pong":
            return [A.Pong(self.mkt(kind), t_rx)]
        m = json.loads(raw)
        if not isinstance(m, dict):
            self.stats["unknown"] += 1
            return []
        ev = m.get("event")
        if ev is not None:
            return self._event(kind, m, t_rx)
        arg = m.get("arg") or {}
        ch = arg.get("channel")
        if ch == "liquidation-orders":
            return self._liqs(kind, m.get("data"), t_rx)
        if ch not in CHANNELS or not isinstance(m.get("data"), list):
            self.stats["unknown"] += 1
            return []
        i = self._inst(kind, arg.get("instId"))
        if i is None:
            return []
        return getattr(self, "_" + ch.replace("-", "_"))(kind, i, m, t_rx)

    def _event(self, kind, m, t_rx):
        ev, mkt = m.get("event"), self.mkt(kind)
        if ev == "subscribe":
            a = m.get("arg") or {}
            v = a.get("instId") or a.get("instType")
            return [A.SubAck(mkt, t_rx, ("%s:%s" % (a.get("channel"), v),), True)]
        if ev == "error":
            g = _RE_ERR.search(str(m.get("msg") or ""))
            if g:                                                     # отклонена одна тема (имя в тексте ошибки)
                return [A.SubAck(mkt, t_rx, ("%s:%s" % (g.group(1), g.group(2)),), False, str(m.get("code")))]
            self.stats["unknown"] += 1
            return []
        if ev == "notice":                                            # 64010: вывод порта 8443; 64008: обновление сервиса
            self.stats["notice"] += 1
            return []
        return []                                                     # unsubscribe, channel-conn-count и прочие служебные

    def _inst(self, kind, native):
        n = str(native or "")
        i = self.meta.native(self.mkt(kind), n)
        if i is None:
            self.stats["skip"] += 1
        return i

    def _cs(self, kind, i):
        return (i.contract_size or 1.0) if kind == "perp" else 1.0

    def _books(self, kind, i, m, t_rx):
        mkt, out = self.mkt(kind), []
        for d in m["data"]:
            t_ex = C.ms(d.get("ts"))
            if m.get("action") == "snapshot":
                out.append(A.BookSnapshot(mkt, i.sym, t_ex, t_rx, d.get("seqId"), C.lv(d.get("bids")), C.lv(d.get("asks"))))
            else:
                cs = d.get("checksum")
                out.append(A.BookDelta(mkt, i.sym, t_ex, t_rx, None, d.get("seqId"), d.get("prevSeqId"),
                                       int(cs) if isinstance(cs, (int, float)) or (isinstance(cs, str) and cs.lstrip("-").isdigit()) else None,
                                       C.lv(d.get("bids")), C.lv(d.get("asks"))))
        return out

    def _trades(self, kind, i, m, t_rx):
        mkt, out = self.mkt(kind), []
        for t in m["data"]:
            px = L.coin_px(t.get("px"), i.coin_mult)
            q = L.coins(t.get("sz"), self._cs(kind, i), i.coin_mult)
            if px is None or q is None:
                self.stats["unknown"] += 1
                continue
            side = {"buy": 1, "sell": -1}.get(t.get("side"), 0)
            out.append(A.Trade(mkt, i.sym, C.ms(t.get("ts")), t_rx, px, q, side, None if t.get("tradeId") is None else str(t["tradeId"])))
        return out

    def _tickers(self, kind, i, m, t_rx):
        mkt, out = self.mkt(kind), []
        for t in m["data"]:
            last = L.num(t.get("last"))
            vol = L.num(t.get("volCcy24h"))
            turn = (vol * last if vol is not None and last else None) if kind == "perp" else vol    # SWAP: в монетах; SPOT: в USDT
            out.append(A.Ticker(mkt, i.sym, C.ms(t.get("ts")), t_rx, bid=L.coin_px(t.get("bidPx"), i.coin_mult),
                                ask=L.coin_px(t.get("askPx"), i.coin_mult), turn24_usd=turn))
        return out

    def _mark_price(self, kind, i, m, t_rx):
        return [A.Ticker(self.mkt(kind), i.sym, C.ms(t.get("ts")), t_rx, mark=L.coin_px(t.get("markPx"), i.coin_mult)) for t in m["data"]]

    def _funding_rate(self, kind, i, m, t_rx):
        out = []
        for t in m["data"]:
            ft, nft = L.num(t.get("fundingTime")), L.num(t.get("nextFundingTime"))
            fh = (nft - ft) / 3.6e6 if ft and nft and nft > ft else None
            out.append(A.Ticker(self.mkt(kind), i.sym, C.ms(t.get("ts")), t_rx, funding=L.num(t.get("fundingRate")), funding_h=fh))
        return out

    def _open_interest(self, kind, i, m, t_rx):
        out = []
        for t in m["data"]:
            oi = L.num(t.get("oiCcy"))                               # монеты базового актива
            if oi is None:
                oi = L.coins(t.get("oi"), self._cs(kind, i), 1.0)
            elif i.coin_mult != 1.0:
                oi *= i.coin_mult
            out.append(A.Ticker(self.mkt(kind), i.sym, C.ms(t.get("ts")), t_rx, oi_coins=oi))
        return out

    def _liqs(self, kind, data, t_rx):
        out, mkt = [], self.mkt(kind)
        now_ex = t_rx + (self.offset_s or 0.0)
        for r in data or ():
            inst_id = str((r or {}).get("instId") or "")
            if not inst_id.endswith("-USDT-SWAP"):
                self.stats["skip"] += 1
                continue
            i = self._inst(kind, inst_id)
            if i is None:
                continue
            for d in r.get("details") or ():
                key = (inst_id, str(d.get("ts")), str(d.get("sz")))
                if self._liq.seen(key):
                    self.stats["liq_dup"] += 1
                    continue
                t_ex = C.ms(d.get("ts"))
                if t_ex is None or now_ex - t_ex > LIQ_MAX_AGE_S:
                    self.stats["liq_old"] += 1
                    continue
                px = L.coin_px(d.get("bkPx"), i.coin_mult)
                q = L.coins(d.get("sz"), self._cs(kind, i), i.coin_mult)
                side = d.get("posSide") if d.get("posSide") in ("long", "short") else {"sell": "long", "buy": "short"}.get(d.get("side"))
                if not px or not q or side is None:
                    self.stats["unknown"] += 1
                    continue
                out.append(A.Liquidation(mkt, i.sym, t_ex, t_rx, px, q, side))
        return out


ADAPTER = OkxAdapter
