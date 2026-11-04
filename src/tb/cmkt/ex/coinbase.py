"""Адаптер Coinbase для сводного рынка: только `spot` пар `BASE-USD` и курс `USDT-USD` (R1.7, задача 13; Requirements 2.1, 2.3, 5.9,
10.3; design.md, «Таблица бирж», решение 14; факты живой разведки 2026-11-04 — exchanges.md, раздел 8).

- WS `wss://ws-feed.exchange.coinbase.com` без ключа: каналы `ticker` и `matches` (стакан не собирается — `has_book = False`, у монет
  статус `nobook`, середина — `best_bid`/`best_ask` тикера) и `heartbeat` пары `USDT-USD` (раз в секунду: живость соединения тихого
  рынка); пинг — кадром WS; REST `https://api.exchange.coinbase.com` — метаданные (`/products`), время (`/time`), курс;
- сделки `matches`: `side` — сторона мейкера (документация Coinbase; сверено на фикстуре: у одной сделки `side` в `ticker` и
  `last_match` противоположны), агрессор противоположный: `sell` -> +1 (покупка тейкера), `buy` -> −1; первое сообщение подписки —
  `last_match` (последняя сделка до подписки) — повтор, не в CVD (счётчик `hist`);
- тикер `ticker` приходит только на сделку (BTC 5/с, `USDT-USD` медиана 14 с, паузы до 31 с): `best_bid`, `best_ask`, оборот
  `volume_24h × price` (USD);
- курс `USDT-USD` (середина `best_bid`/`best_ask`, USD за 1 USDT) для `cm_prem_cb_bps`: из `ticker`, а при возрасте котировки больше
  `FX_LEAD_S` до предела `FX_MAX_AGE_S` (10 с) — REST `GET /products/USDT-USD/ticker` (`bid`, `ask`) не чаще раза в `FX_REST_S` (10 с;
  6 запросов в минуту из 30) через крючки сборщика `side_due`/`side_poll`; курс на секунду `T` (котировки с `t_rx ≤ T`) публикуется
  в `ex/coinbase_spot.json` полем `fx` (`doc_extra`); возраст считается по `t_rx` получения котировки (REST `time` — время последней
  сделки, а не котировки);
- только пары к USD (`BTC-USDT` и прочие к USDT, USDC, EUR у Coinbase отбрасываются: премия считается к USD).

Только публичные каналы, без ключей и подписи.
"""
import json
import re
import time
from collections import deque
from datetime import datetime, timezone

from tb.cmkt import adapter as A
from tb.cmkt.ex import _common as C
from tb.logic import cmkt as L

VENUE = "coinbase"
WS_URL = "wss://ws-feed.exchange.coinbase.com"
REST = ("api.exchange.coinbase.com",)
FX_PRODUCT = "USDT-USD"
FX_SYM = "USDT"
FX_MAX_AGE_S = 10.0                                 # курс не старше 10 с (Requirement 10.3)
FX_LEAD_S = 1.0                                     # REST запрашивается, когда котировке больше 9 с, — до истечения 10 с
FX_REST_S = 10.0                                    # REST курса не чаще раза в 10 с
PER_MSG = 50                                        # пар в одном запросе подписки
CHANNELS = ("ticker", "matches", "heartbeat")
_RE_TOPIC = re.compile(r"^(ticker|matches|heartbeat):([A-Z0-9]{1,20}-USD)$")
_RE_PRODUCT = re.compile(r"\b([A-Z0-9]{1,20}-[A-Z]{2,5})\b")


def iso_ts(s):
    """ISO-8601 Coinbase ('2026-11-04T09:29:24.139118Z') -> секунды или None."""
    if not isinstance(s, str) or not s:
        return None
    try:
        t = s[:-1] + "+00:00" if s.endswith("Z") else s
        head, dot, rest = t.partition(".")
        if dot:                                                       # дробь секунды до 6 знаков (fromisoformat старых версий)
            frac = re.match(r"\d+", rest)
            digits = frac.group(0) if frac else ""
            t = head + "." + (digits[:6].ljust(6, "0")) + rest[len(digits):]
        d = datetime.fromisoformat(t)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def inst_rows(items):
    """/products -> нормализованные строки select_instruments: только пары к USD, `status = online`, торговля не отключена."""
    out = []
    for s in items or ():
        if not isinstance(s, dict) or str(s.get("quote_currency") or "").upper() != "USD":
            continue
        ok = s.get("status") == "online" and not s.get("trading_disabled") and not s.get("cancel_only")
        out.append({"native": s.get("id"), "base": s.get("base_currency"), "quote": "USD", "type": "spot", "tick": s.get("quote_increment"),
                    "status": "trading" if ok else str(s.get("status") or "off").lower()})
    return out


class CoinbaseAdapter(A.VenueAdapter):
    venue = VENUE
    markets = ("spot",)
    book_rules = {}                                 # стакан не собирается
    liq_partial = ()

    def __init__(self, cfg=None, meta=None, clock=time.time):
        super().__init__(cfg, meta)
        self.rest = C.HostChain(REST)
        self.stats = C.new_stats()
        self.stats.update({"hist": 0, "fx_rest": 0, "fx_err": 0})
        self.clock = clock
        self.fx_q = deque(maxlen=8)                 # (t_rx, середина, src) котировок USDT-USD по времени получения
        self.fx_rest_t = None                       # время последнего запроса курса REST
        self._pending = deque(maxlen=256)           # (op, темы) запросов по порядку отправки: ответ Coinbase не несёт номера запроса

    def hosts(self):
        return {"ws": ["ws-feed.exchange.coinbase.com"], "rest": list(REST)}

    def urls(self, kind):
        return {"main": WS_URL}

    # --- метаданные
    def load_meta(self, http, now=0.0, force=False, kind=None):
        mkt = self.mkt("spot")
        if not force and not self.meta.due(mkt, now):
            return {}
        try:
            r = self.rest.get_json(http, "/products", 1)
        except (A.HttpError, A.BudgetExceeded, ValueError, OSError):
            return {}
        insts = A.select_instruments(inst_rows(r if isinstance(r, list) else ()), "spot", allow_usd=True)
        if not insts:
            return {}
        self.meta.update(mkt, insts, now)
        return {"spot": insts}

    def native(self, kind, sym):
        i = self.meta.get(self.mkt(kind), sym, "USD")
        return i.native if i is not None else None

    # --- подписки
    def plan(self, kind, syms_deep, syms_light, resync=()):
        """ticker и matches монет обоих ярусов (стакана нет — ярусы не различаются) и всегда курс USDT-USD (ticker + heartbeat)."""
        out = []
        for syms in (syms_deep, syms_light):
            for s in syms or ():
                n = self.native(kind, s)
                if n is None or s == FX_SYM:
                    continue
                out.append(A.Sub("main", "ticker:" + n, "ticker", s))
                out.append(A.Sub("main", "matches:" + n, "trades", s))
        if self.native(kind, FX_SYM) == FX_PRODUCT:
            out.append(A.Sub("main", "ticker:" + FX_PRODUCT, "fx", FX_SYM))
            out.append(A.Sub("main", "heartbeat:" + FX_PRODUCT, "hb", FX_SYM))
        return out

    def valid_topic(self, topic):
        return isinstance(topic, str) and bool(_RE_TOPIC.match(topic))

    def sub_msgs(self, subs, op="subscribe"):
        by_ch = {}
        for s in subs or ():
            if not self.valid_topic(s.topic):
                self.stats["invalid_topic"] += 1
                continue
            ch, _, p = s.topic.partition(":")
            by_ch.setdefault(ch, []).append(p)
        out = []
        for ch in CHANNELS:
            ps = by_ch.get(ch) or []
            for i in range(0, len(ps), PER_MSG):
                chunk = ps[i:i + PER_MSG]
                self._pending.append((op, tuple("%s:%s" % (ch, p) for p in chunk)))
                out.append(json.dumps({"type": op, "channels": [{"name": ch, "product_ids": chunk}]}, separators=(",", ":")))
        return out

    def ping(self):
        return None                                  # пинг кадром WS; heartbeat USDT-USD — сообщение раз в секунду

    # --- время
    def server_time(self, http, kind="spot"):
        r = self.rest.get_json(http, "/time", 1, "time")
        return float(r["epoch"])

    # --- курс USDT-USD
    def _fx_put(self, t, bid, ask, src):
        b, a = L.num(bid), L.num(ask)
        if not b or not a or b <= 0 or a < b:
            return False
        self.fx_q.append((float(t), (b + a) / 2.0, src))
        return True

    def fx_at(self, T):
        """(середина, t_rx, src) последней котировки с t_rx ≤ T или None."""
        best = None
        for t, mid, src in self.fx_q:
            if t <= T and (best is None or t >= best[1]):
                best = (mid, t, src)
        return best

    def side_due(self, kind, now):
        """Нужен ли REST курса: котировке больше FX_MAX_AGE_S − FX_LEAD_S и прошлый запрос был не раньше FX_REST_S назад."""
        if self.native(kind, FX_SYM) != FX_PRODUCT:
            return False
        last = max((t for t, _, _ in self.fx_q), default=None)
        if last is not None and now - last < FX_MAX_AGE_S - FX_LEAD_S:
            return False
        return self.fx_rest_t is None or now - self.fx_rest_t >= FX_REST_S or now < self.fx_rest_t

    def side_poll(self, http, kind, now):
        """REST GET /products/USDT-USD/ticker (вес 1, остаток бюджета); курс запоминается с временем получения ответа. -> []."""
        self.fx_rest_t = now
        try:
            r = self.rest.get_json(http, "/products/%s/ticker" % FX_PRODUCT, 1, "rate")
        except (A.HttpError, A.BudgetExceeded, ValueError, OSError):
            self.stats["fx_err"] += 1
            return []
        if isinstance(r, dict) and self._fx_put(self.clock(), r.get("bid"), r.get("ask"), "rest"):
            self.stats["fx_rest"] += 1
        else:
            self.stats["fx_err"] += 1
        return []

    def doc_extra(self, kind, T, now=None):
        """Поле `fx` документа рынка: курс USDT-USD на секунду T (котировки с t_rx ≤ T) и его возраст."""
        v = self.fx_at(T)
        if v is None:
            return {"fx": {"usdt_usd": None, "age_s": None, "src": None}}
        mid, t, src = v
        return {"fx": {"usdt_usd": float("%.8g" % mid), "age_s": round(max(0.0, T - t), 2), "src": src}}

    # --- разбор
    def parse(self, kind, raw, t_rx):
        m = json.loads(raw)
        if not isinstance(m, dict):
            self.stats["unknown"] += 1
            return []
        ty = m.get("type")
        mkt = self.mkt(kind)
        if ty == "heartbeat":
            return []                                                 # живость: любое сообщение соединения
        if ty == "subscriptions":                                     # ответ на очередной запрос (subscribe или unsubscribe)
            if self._pending:
                self._pending.popleft()
            topics = tuple("%s:%s" % (c.get("name"), p) for c in m.get("channels") or () if isinstance(c, dict)
                           for p in c.get("product_ids") or ())
            return [A.SubAck(mkt, t_rx, topics, True)] if topics else []
        if ty == "error":
            return self._error(mkt, m, t_rx)
        pid = str(m.get("product_id") or "")
        if ty == "ticker":
            if pid == FX_PRODUCT:
                self._fx_put(t_rx, m.get("best_bid"), m.get("best_ask"), "ws")
            i = self._inst(pid)
            if i is None:
                return []
            px, vol = L.num(m.get("price")), L.num(m.get("volume_24h"))
            cp = lambda k: L.coin_px(m.get(k), i.coin_mult) if m.get(k) not in (None, "") else None     # noqa: E731
            return [A.Ticker(mkt, i.sym, iso_ts(m.get("time")), t_rx, bid=cp("best_bid"), ask=cp("best_ask"),
                             turn24_usd=vol * px if vol is not None and px else None)]
        if ty == "last_match":                                        # последняя сделка до подписки — повтор, не в CVD
            self.stats["hist"] += 1
            return []
        if ty == "match":
            i = self._inst(pid)
            if i is None:
                return []
            px = L.coin_px(m.get("price"), i.coin_mult)
            q = L.coins(m.get("size"), 1.0, i.coin_mult)
            if px is None or q is None:
                self.stats["unknown"] += 1
                return []
            side = {"sell": 1, "buy": -1}.get(m.get("side"), 0)       # side — сторона мейкера: агрессор противоположный
            return [A.Trade(mkt, i.sym, iso_ts(m.get("time")), t_rx, px, q, side,
                            None if m.get("trade_id") is None else str(m["trade_id"]))]
        self.stats["unknown"] += 1
        return []

    def _error(self, mkt, m, t_rx):
        """Ошибка запроса: пара из текста причины -> самый ранний ждущий ответа запрос с этой парой (ответы приходят по порядку).
        Запрос подписки из нескольких тем соединение повторит по одной, запрос из одной темы будет отклонён окончательно."""
        text = ("%s %s" % (m.get("message") or "", m.get("reason") or "")).strip()
        g = _RE_PRODUCT.search(str(m.get("reason") or "")) or _RE_PRODUCT.search(text)
        ent = None
        if g:
            p = ":" + g.group(1)
            ent = next((e for e in self._pending if any(t.endswith(p) for t in e[1])), None)
        if ent is None and self._pending:
            ent = self._pending[0]
        if ent is None:
            self.stats["unknown"] += 1
            return []
        self._pending.remove(ent)
        if ent[0] != "subscribe":
            return []
        return [A.SubAck(mkt, t_rx, ent[1], False, text)]

    def _inst(self, native):
        i = self.meta.native(self.mkt("spot"), native)
        if i is None:
            self.stats["skip"] += 1
        return i


ADAPTER = CoinbaseAdapter
