"""Адаптер Gate для сводного рынка: `perp` (фьючерсы `usdt`, линейные) и `spot` к USDT (R1.7, задача 12; Requirements 2.1, 5.1, 5.3,
5.5, 5.7, 5.9, 9.1; design.md, «Таблица бирж»; факты живой разведки 2026-11-04 — exchanges.md, раздел 7).

- WS `wss://fx-ws.gateio.ws/v4/ws/usdt` (`perp`) и `wss://api.gateio.ws/ws/v4/` (`spot`); пинг по протоколу Gate — `futures.ping` /
  `spot.ping` (`ping(kind)`), ответ `*.pong` -> живость; REST `https://api.gateio.ws/api/v4/*` только для метаданных и времени;
- стакан `deep` — Order Book V2 `futures.obu` / `spot.obu` с `ob.<c>.400` (400 уровней): первое сообщение `full: true` — снимок по WS,
  далее `U` = прошлый `u` + 1 (правило `next`), разрыв -> RESYNC и повторная подписка темы (новый снимок по WS); REST-снимок не нужен
  (REST фьючерсов ограничен 300 уровнями, REST спота с `limit` ≥ 1000 отдаёт кеш до 50 с); `light` — частичный стакан
  `futures.order_book` `[c, "20", "0"]` / `spot.order_book` `[p, "20", "100ms"]` (каждое сообщение — снимок 20 уровней, `part=True`);
- соединение фьючерсов — с заголовком `X-Gate-Size-Decimal: 1` (`ws_headers`): без него биржа отбрасывает дробную часть контракта
  (живая проверка 2026-11-04: 22 % сделок за 15 с приходят с `size` 0), с ним размеры — строки с дробью; сделка с `size` 0 (соединение
  без заголовка) не учитывается (счётчик `size0`);
- сделки `futures.trades` (`size` > 0 — покупка тейкера, в контрактах; монеты = |size| × `quanto_multiplier`), `spot.trades`
  (`side` тейкера, `amount` в монетах);
- тикер `futures.tickers` (~1 с): `total_size` — OI в контрактах (× `quanto_multiplier`), `funding_rate` — ставка ближайшего расчёта,
  `funding_interval` (с), марк, индекс, оборот `volume_24h_quote`; `spot.tickers`: `highest_bid`/`lowest_ask`, `quote_volume`;
- ликвидации `futures.public_liquidates` с `["!all"]` (публичен): сверка с REST `/futures/usdt/liq_orders` на событиях фикстуры
  (2026-11-04) — `price` = `order_price` (цена ордера ликвидации), `size` = `order_size` со знаком ордера (< 0 — продажа, ликвидирован
  лонг; > 0 — покупка, ликвидирован шорт); без заголовка размера дробная часть отбрасывается (`size` 0 — событие без объёма,
  счётчик `liq_zero`); полнота — живая проверка 240 с (2026-11-04): поток не ограничен одним событием на контракт в секунду (каскад
  CNPY — до 10 событий в секунду, WS отдаёт частичные исполнения, REST — ордер целиком), поэтому без `liq_partial` (LIQ_PARTIAL);
  события приходят пачками раз в 2 с (время события — `time`, учёт в окнах — по `t_rx`);
- предел — 100 тем на соединение (`limits.subs_per_conn.gate`); темы одного канала идут одним запросом (`payload` до `PER_MSG`), частичный
  стакан — запрос на монету.

Только публичные каналы, без ключей и подписи.
"""
import json
import re
import time

from tb.cmkt import adapter as A
from tb.cmkt.ex import _common as C
from tb.logic import cmkt as L

VENUE = "gate"
WS = {"perp": "wss://fx-ws.gateio.ws/v4/ws/usdt", "spot": "wss://api.gateio.ws/ws/v4/"}
REST = ("api.gateio.ws",)
PFX = {"perp": "futures", "spot": "spot"}
DEPTH_DEEP, DEPTH_LIGHT = 400, 20
PER_MSG = 20
LIQ_TOPIC = "futures.public_liquidates:!all"
SIZE_HDR = {"X-Gate-Size-Decimal": "1"}
META_TIMEOUT_S = 30.0                              # списки инструментов 1.1–1.4 МБ (контракты без gzip): около 5 с с IP машины
LIQ_PARTIAL = False                                 # живая проверка 2026-11-04: до 10 событий на контракт в секунду (не liq_partial)
_RE_TOPIC = re.compile(r"^(futures|spot)\.(obu:ob\.[A-Z0-9]{1,30}_USDT\.400|order_book:[A-Z0-9]{1,30}_USDT|trades:[A-Z0-9]{1,30}_USDT"
                       r"|tickers:[A-Z0-9]{1,30}_USDT)$")
LIST_CHANNELS = ("obu", "trades", "tickers", "public_liquidates")


def inst_rows(kind, items):
    """futures/usdt/contracts и spot/currency_pairs -> нормализованные строки select_instruments (`quanto_multiplier` — монет в контракте)."""
    out = []
    for s in items or ():
        if not isinstance(s, dict):
            continue
        if kind == "perp":
            name = str(s.get("name") or "")
            base, _, quote = name.partition("_")
            st = str(s.get("status") or "trading").lower()
            fi = L.num(s.get("funding_interval"))
            out.append({"native": name, "base": base, "quote": quote, "settle": "USDT" if quote == "USDT" else quote,
                        "type": "linear_perp" if s.get("type", "direct") == "direct" else "inverse_perp",
                        "contract_size": L.num(s.get("quanto_multiplier")), "tick": L.num(s.get("order_price_round")),
                        "funding_h": fi / 3600.0 if fi else None, "status": "trading" if st == "trading" else st})
        else:
            pp = L.num(s.get("precision"))
            st = str(s.get("trade_status") or "").lower()
            out.append({"native": s.get("id"), "base": s.get("base"), "quote": s.get("quote"), "type": "spot",
                        "tick": 10 ** -pp if pp is not None else None, "status": "trading" if st == "tradable" else st})
    return out


def _lv(rows):
    """Уровни Gate: [[p, s]] (obu, spot.order_book) или [{"p", "s"}] (futures.order_book, размер числом) -> [(px_str, size_str)]."""
    out = []
    for r in rows or ():
        if isinstance(r, dict):
            if r.get("p") is not None and r.get("s") is not None:
                out.append((str(r["p"]), str(r["s"])))
        elif isinstance(r, (list, tuple)) and len(r) >= 2:
            out.append((str(r[0]), str(r[1])))
    return out


def _int(x):
    v = L.num(x)
    return int(v) if v is not None else None


class GateAdapter(A.VenueAdapter):
    venue = VENUE
    markets = ("perp", "spot")
    book_rules = {"perp": {"deep": "next", "light": "replace"}, "spot": {"deep": "next", "light": "replace"}}
    liq_partial = ("gate_perp",) if LIQ_PARTIAL else ()
    oi_every_s = {"perp": 4.0}                      # total_size меняется раз в ~1 с, паузы тикера до 5 с (живая проверка): свежесть 6 с

    def __init__(self, cfg=None, meta=None):
        super().__init__(cfg, meta)
        self.rest = C.HostChain(REST)
        self.stats = C.new_stats()
        self.stats.update({"liq_zero": 0, "size0": 0})
        self._id = 0
        self._reqs = C.IdMap()

    def hosts(self):
        return {"ws": ["fx-ws.gateio.ws", "api.gateio.ws"], "rest": list(REST)}

    def urls(self, kind):
        return {"main": WS[kind]}

    # --- метаданные
    def load_meta(self, http, now=0.0, force=False, kind=None):
        """{kind: [Inst]}; kind — только этот рынок (сборщик рынка не качает чужой список: у Gate списки 1.1–1.4 МБ)."""
        out = {}
        for kind in ((kind,) if kind in self.markets else self.markets):
            mkt = self.mkt(kind)
            if not force and not self.meta.due(mkt, now):
                continue
            path = "/api/v4/futures/usdt/contracts" if kind == "perp" else "/api/v4/spot/currency_pairs"
            try:
                r = self.rest.get_json(http, path, 1, timeout=META_TIMEOUT_S)
            except (A.HttpError, A.BudgetExceeded, ValueError, OSError):
                continue
            insts = A.select_instruments(inst_rows(kind, r if isinstance(r, list) else ()), kind)
            if insts:
                self.meta.update(mkt, insts, now)
                out[kind] = insts
        return out

    def native(self, kind, sym):
        i = self.meta.get(self.mkt(kind), sym)
        return i.native if i is not None else None

    # --- подписки
    def plan(self, kind, syms_deep, syms_light, resync=()):
        out, p = [], PFX[kind]
        for tier, syms in (("deep", syms_deep), ("light", syms_light)):
            for s in syms or ():
                n = self.native(kind, s)
                if n is None:
                    continue
                if tier == "deep":
                    out.append(A.Sub("main", "%s.obu:ob.%s.%d" % (p, n, DEPTH_DEEP), "book", s))
                else:
                    out.append(A.Sub("main", "%s.order_book:%s" % (p, n), "book_part", s))
                out.append(A.Sub("main", "%s.trades:%s" % (p, n), "trades", s))
                out.append(A.Sub("main", "%s.tickers:%s" % (p, n), "ticker", s))
        if kind == "perp" and out:
            out.append(A.Sub("main", LIQ_TOPIC, "liq", "*"))
        return out

    def valid_topic(self, topic):
        return isinstance(topic, str) and (topic == LIQ_TOPIC or bool(_RE_TOPIC.match(topic)))

    def _req(self, ch, op, payload, topics):
        self._id += 1
        self._reqs.put(self._id, topics, op)
        return json.dumps({"time": int(time.time()), "id": self._id, "channel": ch, "event": op, "payload": payload}, separators=(",", ":"))

    def sub_msgs(self, subs, op="subscribe"):
        by_ch = {}
        for s in subs or ():
            if not self.valid_topic(s.topic):
                self.stats["invalid_topic"] += 1
                continue
            ch, _, v = s.topic.partition(":")
            by_ch.setdefault(ch, []).append((v, s.topic))
        out = []
        for ch, items in by_ch.items():
            if ch.endswith(".order_book"):                           # частичный стакан: запрос на монету
                for v, t in items:
                    pl = [v, str(DEPTH_LIGHT), "0"] if ch.startswith("futures") else [v, str(DEPTH_LIGHT), "100ms"]
                    out.append(self._req(ch, op, pl, [t]))
                continue
            for i in range(0, len(items), PER_MSG):
                chunk = items[i:i + PER_MSG]
                out.append(self._req(ch, op, [v for v, _ in chunk], [t for _, t in chunk]))
        return out

    def ws_headers(self, kind):
        """Фьючерсы: без X-Gate-Size-Decimal биржа отбрасывает дробную часть контракта (живая проверка 2026-11-04: 22 % сделок с
        `size` 0 за 15 с); с заголовком размеры — строки с дробью (сделки, стакан, OI, ликвидации)."""
        return dict(SIZE_HDR) if kind == "perp" else None

    def ping(self, kind="perp"):
        return json.dumps({"time": int(time.time()), "channel": "%s.ping" % PFX[kind]}, separators=(",", ":"))

    # --- время
    def server_time(self, http, kind="perp"):
        r = self.rest.get_json(http, "/api/v4/spot/time", 1, "time")
        return float(r["server_time"]) / 1000.0

    # --- разбор
    def parse(self, kind, raw, t_rx):
        m = json.loads(raw)
        if not isinstance(m, dict):
            self.stats["unknown"] += 1
            return []
        ch = str(m.get("channel") or "")
        mkt = self.mkt(kind)
        if ch.endswith(".pong"):
            return [A.Pong(mkt, t_rx)]
        ev = m.get("event")
        if ev in ("subscribe", "unsubscribe"):
            return self._ack(kind, m, ch, ev, t_rx)
        res = m.get("result")
        name = ch.partition(".")[2]
        fn = getattr(self, "_" + name, None) if name in ("obu", "order_book", "trades", "tickers", "public_liquidates") else None
        if fn is None or res is None:
            self.stats["unknown"] += 1
            return []
        return fn(kind, m, res, t_rx)

    def _ack(self, kind, m, ch, ev, t_rx):
        ent = self._reqs.pop(m.get("id")) if m.get("id") is not None else None
        if ent is not None:
            topics, op = ent
        else:
            pl = m.get("payload") or []
            if ch.endswith(".order_book"):
                topics = ("%s:%s" % (ch, pl[0]),) if pl else ()
            else:
                topics = tuple("%s:%s" % (ch, v) for v in pl)
            op = ev
        if op != "subscribe" or not topics:
            return []
        err = m.get("error")
        ok = not err and ((m.get("result") or {}).get("status") in ("success", None))
        reason = None if ok else str((err or {}).get("message") if isinstance(err, dict) else err)
        return [A.SubAck(self.mkt(kind), t_rx, tuple(topics), ok, reason)]

    def _inst(self, kind, native):
        i = self.meta.native(self.mkt(kind), str(native or ""))
        if i is None:
            self.stats["skip"] += 1
        return i

    def _cs(self, kind, i):
        return (i.contract_size or 1.0) if kind == "perp" else 1.0

    def _obu(self, kind, m, r, t_rx):
        if not isinstance(r, dict):
            self.stats["unknown"] += 1
            return []
        parts = str(r.get("s") or "").split(".")
        i = self._inst(kind, parts[1] if len(parts) == 3 else None)
        if i is None:
            return []
        t_ex = C.ms(r.get("t")) or C.ms(m.get("time_ms"))
        if r.get("full") is True:                                    # снимок по WS (первое сообщение темы и после переподписки)
            return [A.BookSnapshot(self.mkt(kind), i.sym, t_ex, t_rx, _int(r.get("u")), _lv(r.get("b")), _lv(r.get("a")))]
        return [A.BookDelta(self.mkt(kind), i.sym, t_ex, t_rx, _int(r.get("U")), _int(r.get("u")), None, None, _lv(r.get("b")),
                            _lv(r.get("a")))]

    def _order_book(self, kind, m, r, t_rx):
        if not isinstance(r, dict):
            self.stats["unknown"] += 1
            return []
        i = self._inst(kind, r.get("contract") or r.get("s"))
        if i is None:
            return []
        seq = _int(r.get("id") if r.get("id") is not None else r.get("lastUpdateId"))
        return [A.BookSnapshot(self.mkt(kind), i.sym, C.ms(r.get("t")) or C.ms(m.get("time_ms")), t_rx, seq, _lv(r.get("bids")),
                               _lv(r.get("asks")), False, True)]

    def _trades(self, kind, m, r, t_rx):
        mkt, out = self.mkt(kind), []
        for t in (r if isinstance(r, list) else [r]):
            if not isinstance(t, dict):
                self.stats["unknown"] += 1
                continue
            i = self._inst(kind, t.get("contract") or t.get("currency_pair"))
            if i is None:
                continue
            px = L.coin_px(t.get("price"), i.coin_mult)
            if kind == "perp":
                sz = L.num(t.get("size"))
                if sz == 0:                                          # без X-Gate-Size-Decimal: сделка меньше 1 контракта
                    self.stats["size0"] += 1
                    continue
                side = 1 if sz is not None and sz > 0 else (-1 if sz is not None and sz < 0 else 0)
                q = L.coins(abs(sz), self._cs(kind, i), i.coin_mult) if sz is not None else None
            else:
                side = {"buy": 1, "sell": -1}.get(t.get("side"), 0)
                q = L.coins(t.get("amount"), 1.0, i.coin_mult)
            if px is None or q is None:
                self.stats["unknown"] += 1
                continue
            out.append(A.Trade(mkt, i.sym, C.ms(t.get("create_time_ms")) or C.ms(m.get("time_ms")), t_rx, px, q, side,
                               None if t.get("id") is None else str(t["id"])))
        return out

    def _tickers(self, kind, m, r, t_rx):
        mkt, out = self.mkt(kind), []
        for d in (r if isinstance(r, list) else [r]):
            if not isinstance(d, dict):
                self.stats["unknown"] += 1
                continue
            i = self._inst(kind, d.get("contract") or d.get("currency_pair"))
            if i is None:
                continue
            cp = lambda k: L.coin_px(d.get(k), i.coin_mult) if d.get(k) not in (None, "") else None     # noqa: E731
            t_ex = C.ms(d.get("t")) or C.ms(m.get("time_ms"))
            if kind == "perp":
                oi = L.coins(d.get("total_size"), self._cs(kind, i), i.coin_mult) if d.get("total_size") not in (None, "") else None
                fi = L.num(d.get("funding_interval"))
                out.append(A.Ticker(mkt, i.sym, t_ex, t_rx, mark=cp("mark_price"), index=cp("index_price"),
                                    funding=L.num(d.get("funding_rate")), funding_h=fi / 3600.0 if fi else i.funding_h, oi_coins=oi,
                                    turn24_usd=L.num(d.get("volume_24h_quote"))))
            else:
                out.append(A.Ticker(mkt, i.sym, t_ex, t_rx, bid=cp("highest_bid"), ask=cp("lowest_ask"),
                                    turn24_usd=L.num(d.get("quote_volume"))))
        return out

    def _public_liquidates(self, kind, m, r, t_rx):
        mkt, out = self.mkt(kind), []
        for d in (r if isinstance(r, list) else [r]):
            if not isinstance(d, dict):
                self.stats["unknown"] += 1
                continue
            i = self._inst(kind, d.get("contract"))
            if i is None:
                continue
            sz = L.num(d.get("size"))
            if sz == 0:                                              # размер меньше 1 контракта (дробная часть отброшена биржей)
                self.stats["liq_zero"] += 1
                continue
            px = L.coin_px(d.get("price"), i.coin_mult)
            q = L.coins(abs(sz), self._cs(kind, i), i.coin_mult) if sz is not None else None
            if not px or not q:
                self.stats["unknown"] += 1
                continue
            out.append(A.Liquidation(mkt, i.sym, C.ms(d.get("time")) or C.ms(m.get("time_ms")), t_rx, px, q,
                                     "long" if sz < 0 else "short"))  # size < 0 — ордер на продажу: ликвидирован лонг
        return out


ADAPTER = GateAdapter
