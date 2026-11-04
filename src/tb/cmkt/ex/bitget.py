"""Адаптер Bitget для сводного рынка: `perp` (`USDT-FUTURES`, линейные USDT) и `spot` к USDT (R1.7, задача 11; Requirements 2.1, 5.1,
5.2, 5.9, 9.1, 12.1; design.md, «Таблица бирж»; факты живой разведки 2026-11-04 — exchanges.md, разделы 6 и 11).

- WS `wss://ws.bitget.com/v2/ws/public` (оба рынка; вид рынка — `instType` аргумента: `USDT-FUTURES` / `SPOT`); пинг — текст `ping`
  раз в `ping_s`, живость по ответу `pong` (свой пинг по протоколу Bitget: `ccxt.pro` рвал соединения на пинг-понге, exchanges.md п. 11);
  REST `https://api.bitget.com/api/v2/*`;
- стакан `books` (ярус `deep`; 500 уровней у BTC/ETH, 200 у DOGE): снимок при подписке, далее `update`; непрерывность `pseq` = прошлый
  `seq` (правило `prev`); поля `checksum` у Bitget нет (ни в снимке, ни в обновлениях, 2026-11-04) — контрольная сумма не проверяется;
  восстановление — повторная подписка `books`; ярус `light` — `books15` (каждое сообщение — снимок, события с `part=True`);
- сделки `trade` (`side` тейкера, размер в монетах); первое сообщение подписки — `snapshot` истории (до 50 сделок): отбрасывается как
  повтор (счётчик `hist`);
- тикер `ticker`: `perp` — марк, индекс, bid/ask, `fundingRate` текущего периода, `holdingAmount` (OI в монетах, меняется раз в ~10 с),
  `quoteVolume`; интервала funding в тикере нет — `fundInterval` из `contracts`; `spot` — bid/ask и оборот;
- публичного канала ликвидаций нет: покрытие ликвидаций Bitget пустое (тем `liq` нет, `liq` строки — None);
- размеры в монетах (`sizeMultiplier` — шаг ордера, не размер контракта), `PEPEUSDT` без префикса; предел — 50 тем на соединение
  (`limits.subs_per_conn.bitget`), 10 сообщений в секунду.

Только публичные каналы, без ключей и подписи.
"""
import json
import re

from tb.cmkt import adapter as A
from tb.cmkt.ex import _common as C
from tb.logic import cmkt as L

VENUE = "bitget"
WS_URL = "wss://ws.bitget.com/v2/ws/public"
REST = ("api.bitget.com",)
INST_TYPE = {"perp": "USDT-FUTURES", "spot": "SPOT"}
CONN = {"perp": "mix", "spot": "spot"}               # conn_key соединения -> instType аргументов подписки
CONN_TYPE = {"mix": "USDT-FUTURES", "spot": "SPOT"}
PER_MSG = 20                                        # аргументов в одном запросе (предел биржи — 4096 байт на запрос)
CHANNELS = {"books": "book", "books15": "book_part", "trade": "trades", "ticker": "ticker"}
_RE_TOPIC = re.compile(r"^(books|books15|trade|ticker):([A-Z0-9]{2,30})$")
_RE_ERR = re.compile(r"channel:([a-z0-9]+),\s*instId:([A-Za-z0-9]+)")


def inst_rows(kind, items):
    """mix/market/contracts и spot/public/symbols -> нормализованные строки select_instruments (размер контракта 1: размеры в монетах)."""
    out = []
    for s in items or ():
        if not isinstance(s, dict):
            continue
        if kind == "perp":
            st = "trading" if s.get("symbolStatus") in ("normal", None) else str(s.get("symbolStatus")).lower()
            pp = L.num(s.get("pricePlace"))
            step = L.num(s.get("priceEndStep")) or 1.0
            fi = L.num(s.get("fundInterval"))
            out.append({"native": s.get("symbol"), "base": s.get("baseCoin"), "quote": s.get("quoteCoin"), "settle": s.get("quoteCoin"),
                        "type": "linear_perp" if s.get("symbolType") == "perpetual" else "future", "contract_size": 1.0,
                        "tick": step * 10 ** -pp if pp is not None else None, "funding_h": fi if fi else None, "status": st})
        else:
            st = "trading" if s.get("status") in ("online", None) else str(s.get("status")).lower()
            pp = L.num(s.get("pricePrecision"))
            out.append({"native": s.get("symbol"), "base": s.get("baseCoin"), "quote": s.get("quoteCoin"), "type": "spot",
                        "tick": 10 ** -pp if pp is not None else None, "status": st})
    return out


class BitgetAdapter(A.VenueAdapter):
    venue = VENUE
    markets = ("perp", "spot")
    book_rules = {"perp": {"deep": "prev", "light": "replace"}, "spot": {"deep": "prev", "light": "replace"}}
    liq_partial = ()                                # публичного канала ликвидаций нет: тем liq нет, в ex/<market>.json liq = None
    oi_every_s = {"perp": 10.0}                     # holdingAmount тикера меняется раз в ~10 с (задача 2)

    def __init__(self, cfg=None, meta=None):
        super().__init__(cfg, meta)
        self.rest = C.HostChain(REST)
        self.stats = C.new_stats()
        self.stats["hist"] = 0

    def hosts(self):
        return {"ws": ["ws.bitget.com"], "rest": list(REST)}

    def urls(self, kind):
        return {CONN[kind]: WS_URL}

    # --- метаданные
    def load_meta(self, http, now=0.0, force=False, kind=None):
        """{kind: [Inst]}; kind — только этот рынок (сборщик рынка не качает чужой список: у Gate списки 1.1–1.4 МБ)."""
        out = {}
        for kind in ((kind,) if kind in self.markets else self.markets):
            mkt = self.mkt(kind)
            if not force and not self.meta.due(mkt, now):
                continue
            path = "/api/v2/mix/market/contracts?productType=USDT-FUTURES" if kind == "perp" else "/api/v2/spot/public/symbols"
            try:
                r = self.rest.get_json(http, path, 1)
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
        out, ck = [], CONN[kind]
        for tier, syms in (("deep", syms_deep), ("light", syms_light)):
            for s in syms or ():
                n = self.native(kind, s)
                if n is None:
                    continue
                out.append(A.Sub(ck, ("books:" if tier == "deep" else "books15:") + n, "book" if tier == "deep" else "book_part", s))
                out.append(A.Sub(ck, "trade:" + n, "trades", s))
                out.append(A.Sub(ck, "ticker:" + n, "ticker", s))
        return out

    def valid_topic(self, topic):
        return isinstance(topic, str) and bool(_RE_TOPIC.match(topic))

    def sub_msgs(self, subs, op="subscribe"):
        args = []
        for s in subs or ():
            it = CONN_TYPE.get(s.conn_key.split("#")[0])
            if it is None or not self.valid_topic(s.topic):
                self.stats["invalid_topic"] += 1
                continue
            ch, _, n = s.topic.partition(":")
            args.append({"instType": it, "channel": ch, "instId": n})
        return [json.dumps({"op": op, "args": args[i:i + PER_MSG]}, separators=(",", ":")) for i in range(0, len(args), PER_MSG)]

    def ping(self):
        return "ping"

    # --- время
    def server_time(self, http, kind="perp"):
        r = self.rest.get_json(http, "/api/v2/public/time", 1, "time")
        return float(((r or {}).get("data") or {})["serverTime"]) / 1000.0

    # --- разбор
    def parse(self, kind, raw, t_rx):
        if raw == "pong":
            return [A.Pong(self.mkt(kind), t_rx)]
        m = json.loads(raw)
        if not isinstance(m, dict):
            self.stats["unknown"] += 1
            return []
        if m.get("event") is not None:
            return self._event(kind, m, t_rx)
        arg = m.get("arg") or {}
        ch = arg.get("channel")
        if ch not in CHANNELS or not isinstance(m.get("data"), list):
            self.stats["unknown"] += 1
            return []
        i = self._inst(kind, arg.get("instId"))
        if i is None:
            return []
        return getattr(self, "_" + ch.rstrip("0123456789"))(kind, i, m, t_rx, ch)

    def _event(self, kind, m, t_rx):
        ev, mkt, a = m.get("event"), self.mkt(kind), m.get("arg") or {}
        if ev == "subscribe":
            return [A.SubAck(mkt, t_rx, ("%s:%s" % (a.get("channel"), a.get("instId")),), True)]
        if ev == "error":
            if a.get("channel") and a.get("instId"):
                topic = "%s:%s" % (a["channel"], a["instId"])
            else:
                g = _RE_ERR.search(str(m.get("msg") or ""))
                if not g:
                    self.stats["unknown"] += 1
                    return []
                topic = "%s:%s" % (g.group(1), g.group(2))
            return [A.SubAck(mkt, t_rx, (topic,), False, "%s %s" % (m.get("code"), m.get("msg")))]
        return []                                                     # unsubscribe и прочие служебные

    def _inst(self, kind, native):
        i = self.meta.native(self.mkt(kind), str(native or ""))
        if i is None:
            self.stats["skip"] += 1
        return i

    def _books(self, kind, i, m, t_rx, ch):
        mkt, out = self.mkt(kind), []
        part = ch != "books"
        for d in m["data"]:
            t_ex = C.ms(d.get("ts")) or C.ms(m.get("ts"))
            seq = d.get("seq")
            seq = int(seq) if isinstance(seq, (int, float)) or (isinstance(seq, str) and seq.isdigit()) else None
            if m.get("action") == "snapshot" or part:
                out.append(A.BookSnapshot(mkt, i.sym, t_ex, t_rx, seq, C.lv(d.get("bids")), C.lv(d.get("asks")), False, part))
            else:
                ps = d.get("pseq")
                ps = int(ps) if isinstance(ps, (int, float)) or (isinstance(ps, str) and ps.isdigit()) else None
                out.append(A.BookDelta(mkt, i.sym, t_ex, t_rx, None, seq, ps, None, C.lv(d.get("bids")), C.lv(d.get("asks"))))
        return out

    def _trade(self, kind, i, m, t_rx, ch):
        if m.get("action") == "snapshot":                            # история последних сделок при подписке — повтор, не в CVD
            self.stats["hist"] += len(m["data"])
            return []
        mkt, out = self.mkt(kind), []
        for t in m["data"]:
            px = L.coin_px(t.get("price"), i.coin_mult)
            q = L.coins(t.get("size"), 1.0, i.coin_mult)
            if px is None or q is None:
                self.stats["unknown"] += 1
                continue
            side = {"buy": 1, "sell": -1}.get(t.get("side"), 0)
            out.append(A.Trade(mkt, i.sym, C.ms(t.get("ts")) or C.ms(m.get("ts")), t_rx, px, q, side,
                               None if t.get("tradeId") is None else str(t["tradeId"])))
        return out

    def _ticker(self, kind, i, m, t_rx, ch):
        mkt, out = self.mkt(kind), []
        for d in m["data"]:
            cp = lambda k: L.coin_px(d.get(k), i.coin_mult) if d.get(k) not in (None, "") else None     # noqa: E731
            t_ex = C.ms(d.get("ts")) or C.ms(m.get("ts"))
            turn = L.num(d.get("quoteVolume"))
            if kind == "perp":
                oi = L.coins(d.get("holdingAmount"), 1.0, i.coin_mult) if d.get("holdingAmount") not in (None, "") else None
                out.append(A.Ticker(mkt, i.sym, t_ex, t_rx, mark=cp("markPrice"), index=cp("indexPrice"), bid=cp("bidPr"), ask=cp("askPr"),
                                    funding=L.num(d.get("fundingRate")), funding_h=i.funding_h, oi_coins=oi, turn24_usd=turn))
            else:
                out.append(A.Ticker(mkt, i.sym, t_ex, t_rx, bid=cp("bidPr"), ask=cp("askPr"), turn24_usd=turn))
        return out


ADAPTER = BitgetAdapter
