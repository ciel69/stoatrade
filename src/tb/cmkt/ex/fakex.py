"""Поддельная биржа `fakex` для тестов контракта адаптера (R1.7, задача 6; Requirement 2.2).

- `FakexAdapter` — адаптер по контракту `tb.cmkt.adapter.VenueAdapter` с простым JSON-протоколом; правило непрерывности стакана
  каждого рынка задаётся параметром (`binance_perp`, `binance_spot`, `next`, `prev`, `bybit`), поэтому на ней проверяются все виды правил.
- `book_script` — сценарий стакана: снимок (в потоке или вне его), дельты с номерами по правилу, эталонный стакан, разрыв номеров,
  дельты за границей REST-снимка, неверная контрольная сумма.
- `FakeServer` — WS-сервер на 127.0.0.1 (только тесты): подписки с ответом и отказом тем, pong на пинг, сценарии разрыва соединения,
  молчания, задержек; воспроизведение фикстур `tests/fixtures_cmkt/<venue>/*.jsonl` (`load_fixture`).

Модуль не ходит во внешнюю сеть и не содержит ключей.
"""
import asyncio
import base64
import hashlib
import json
import random
import re

from tb.cmkt import adapter as A
from tb.marketdata import wsclient as W

VENUE = "fakex"
_RE_TOPIC = re.compile(r"^(trade|book|book5|ticker|liq)\.[A-Z0-9]{2,30}$")

# инструменты биржи (нормализованный вид адаптера): годные и те, что отбор обязан отбросить
INSTRUMENTS = {
    "perp": [
        {"native": "BTCUSDT", "base": "BTC", "quote": "USDT", "settle": "USDT", "type": "linear_perp", "contract_size": 0.001, "funding_h": 8},
        {"native": "ETHUSDT", "base": "ETH", "quote": "USDT", "settle": "USDT", "type": "linear_perp", "contract_size": 0.01, "funding_h": 4},
        {"native": "1000PEPEUSDT", "base": "1000PEPE", "quote": "USDT", "settle": "USDT", "type": "linear_perp", "contract_size": 1,
         "funding_h": 8},
        {"native": "BTCUSD", "base": "BTC", "quote": "USD", "settle": "BTC", "type": "inverse_perp", "contract_size": 100},
        {"native": "BTCUSDC", "base": "BTC", "quote": "USDC", "settle": "USDC", "type": "linear_perp", "contract_size": 0.001},
        {"native": "BTCUSDT_261225", "base": "BTC", "quote": "USDT", "settle": "USDT", "type": "future", "contract_size": 0.001},
        {"native": "XYZUSDT", "base": "XYZ", "quote": "USDT", "settle": "USDT", "type": "linear_perp", "contract_size": None},
        {"native": "OLDUSDT", "base": "OLD", "quote": "USDT", "settle": "USDT", "type": "linear_perp", "contract_size": 1,
         "status": "delisted"},
    ],
    "spot": [
        {"native": "BTCUSDT", "base": "BTC", "quote": "USDT", "type": "spot"},
        {"native": "ETHUSDT", "base": "ETH", "quote": "USDT", "type": "spot"},
        {"native": "PEPEUSDT", "base": "PEPE", "quote": "USDT", "type": "spot"},
        {"native": "BTCFDUSD", "base": "BTC", "quote": "FDUSD", "type": "spot"},
        {"native": "BTCUSDC", "base": "BTC", "quote": "USDC", "type": "spot"},
        {"native": "ETHBTC", "base": "ETH", "quote": "BTC", "type": "spot"},
        {"native": "BTCUSD", "base": "BTC", "quote": "USD", "type": "spot"},
    ],
}
DEFAULT_RULES = {"perp": {"deep": "binance_perp", "light": "replace"}, "spot": {"deep": "binance_spot", "light": "replace"}}


class FakexAdapter(A.VenueAdapter):
    venue = VENUE
    markets = ("perp", "spot")

    def __init__(self, cfg=None, meta=None, base_url="ws://127.0.0.1:1/ws", rules=None, raw_books=(), instruments=None,
                 server_time=None):
        super().__init__(cfg, meta)
        self.base_url = base_url
        self.book_rules = dict(DEFAULT_RULES, **(rules or {}))
        self.raw_books = tuple(raw_books)
        self.instruments = instruments if instruments is not None else INSTRUMENTS
        self.rest_books = {}                       # (kind, sym) -> BookSnapshot вне потока (подменённый REST)
        self.rest_calls = []
        self._server_time = server_time

    def hosts(self):
        return {"ws": ["fakex.test"], "rest": ["api.fakex.test"]}

    def urls(self, kind):
        return {"main": self.base_url}

    def load_meta(self, http=None, now=0.0):
        out = {k: A.select_instruments(self.instruments.get(k) or [], k) for k in self.markets}
        for k, insts in out.items():
            self.meta.update(self.mkt(k), insts, now)
        return out

    def native(self, kind, sym):
        i = self.meta.get(self.mkt(kind), sym)
        return i.native if i else sym + "USDT"

    def plan(self, kind, syms_deep, syms_light):
        out = []
        for tier, syms in (("deep", syms_deep), ("light", syms_light)):
            for s in syms or ():
                n = self.native(kind, s)
                out.append(A.Sub("main", "trade." + n, "trades", s))
                out.append(A.Sub("main", ("book." if tier == "deep" else "book5.") + n, "book" if tier == "deep" else "book_part", s))
                out.append(A.Sub("main", "ticker." + n, "ticker", s))
                if kind == "perp":
                    out.append(A.Sub("main", "liq." + n, "liq", s))
        return out

    def valid_topic(self, topic):
        return isinstance(topic, str) and bool(_RE_TOPIC.match(topic))

    def sub_msgs(self, subs, op="subscribe"):
        topics = [s.topic for s in subs if self.valid_topic(s.topic)]
        return [json.dumps({"op": op, "args": topics[i:i + 10]}, separators=(",", ":")) for i in range(0, len(topics), 10)]

    def ping(self):
        return '{"op":"ping"}'

    def _sym(self, kind, native):
        i = self.meta.native(self.mkt(kind), native)
        return (i.sym, i) if i else (None, None)

    def parse(self, kind, raw, t_rx):
        m = json.loads(raw)
        mkt = self.mkt(kind)
        op = m.get("op")
        if op == "pong":
            return [A.Pong(mkt, t_rx)]
        if op in ("subscribe", "unsubscribe"):
            return [A.SubAck(mkt, t_rx, tuple(m.get("args") or ()), bool(m.get("ok")), m.get("msg"))]
        ch = m.get("ch")
        sym, inst = self._sym(kind, m.get("s"))
        if not ch or sym is None:
            return []
        t_ex = m["E"] / 1000.0 if isinstance(m.get("E"), (int, float)) else None
        if ch == "book":
            bids, asks = [tuple(x) for x in m.get("b") or ()], [tuple(x) for x in m.get("a") or ()]
            if m.get("type") == "snap":
                return [A.BookSnapshot(mkt, sym, t_ex, t_rx, m.get("u"), bids, asks, bool(m.get("rest")))]
            return [A.BookDelta(mkt, sym, t_ex, t_rx, m.get("U"), m.get("u"), m.get("pu"), m.get("cs"), bids, asks)]
        if ch == "trade":
            side = {"buy": 1, "sell": -1}.get(m.get("S"), 0)
            px = A.L.coin_px(m.get("p"), inst.coin_mult)
            q = A.L.coins(m.get("q"), inst.contract_size or 1.0, inst.coin_mult)
            if px is None or q is None:
                return []
            return [A.Trade(mkt, sym, t_ex, t_rx, px, q, side, None if m.get("id") is None else str(m["id"]))]
        if ch == "ticker":
            cp = lambda k: A.L.coin_px(m.get(k), inst.coin_mult) if m.get(k) is not None else None    # noqa: E731
            oi = A.L.coins(m.get("oi"), inst.contract_size or 1.0, inst.coin_mult) if m.get("oi") is not None else None
            return [A.Ticker(mkt, sym, t_ex, t_rx, cp("mark"), cp("index"), cp("bid"), cp("ask"), A.L.num(m.get("fr")),
                             A.L.num(m.get("fh")) or inst.funding_h, oi, A.L.num(m.get("turn")))]
        if ch == "liq":
            px = A.L.coin_px(m.get("p"), inst.coin_mult)
            q = A.L.coins(m.get("q"), inst.contract_size or 1.0, inst.coin_mult)
            side = "long" if m.get("S") == "sell" else "short"            # ликвидация лонга — ордер на продажу
            return [A.Liquidation(mkt, sym, t_ex, t_rx, px, q, side)] if px and q else []
        return []

    def checksum(self, state):
        if state.mkt not in self.raw_books:
            return None
        return A.crc32_levels(*state.raw_sorted())

    def snapshot_cost(self, kind, prio):
        rule = (self.book_rules.get(kind) or {}).get("deep")
        if not A.RULES.get(rule, (None, False))[1]:
            return None, 0
        return (1000, 20) if prio <= A.PRIO["core"] else (500, 10)

    def book_rest(self, http, kind, sym, limit=None):
        self.rest_calls.append((kind, sym, limit))
        return self.rest_books.get((kind, sym))

    def server_time(self, http=None):
        if self._server_time is None:
            raise A.HttpError(503, "no server time")
        return self._server_time()


ADAPTER = FakexAdapter


# ---------------------------------------------------------------- сценарии стакана
def _px(x):
    return "%.2f" % x


def _ref_apply(book, side, px, size):
    d = book[side]
    if float(size) == 0:
        d.pop(px, None)
    else:
        d[px] = size


def book_script(rule, rng=None, n=120, depth=20, rest_depth=12, gap_at=None, native="BTCUSDT", far_every=7, crc=False,
                bad_crc_at=None, t0=1793750400.0):
    """Сценарий стакана монеты по правилу `rule`. -> {"msgs": [текст] (в порядке потока), "rest": текст снимка вне потока | None,
    "rest_after": число сообщений потока до снимка вне потока, "ref": {"bid": {px: size}, "ask": {...}} после всех дельт,
    "bounds": (мин. bid, макс. ask) REST-снимка | None, "gap_at": индекс пропущенной дельты}.
    Цены bid 99.xx, ask 100.xx (без перекрёста); часть дельт добавляет уровни дальше границы REST-снимка; crc — дельты несут
    контрольную сумму эталона (bad_crc_at — неверную)."""
    rng = rng or random.Random(0)
    book = {"bid": {}, "ask": {}}
    for i in range(depth):
        book["bid"][_px(99.99 - i * 0.01)] = str(rng.randint(1, 50))
        book["ask"][_px(100.01 + i * 0.01)] = str(rng.randint(1, 50))
    rest_rule = A.RULES[rule][1]
    uid = 1000
    snap_at = 3 if rest_rule else 0                    # у снимка вне потока: 3 дельты до снимка (часть отбрасывается)
    msgs, rest, rest_after, bounds = [], None, None, None
    deltas = []

    def snapshot_msg(u, rest_flag):
        b = sorted(book["bid"].items(), key=lambda r: -float(r[0]))
        a = sorted(book["ask"].items(), key=lambda r: float(r[0]))
        if rest_flag:
            b, a = b[:rest_depth], a[:rest_depth]
        return {"ch": "book", "type": "snap", "s": native, "u": u, "b": [list(x) for x in b], "a": [list(x) for x in a], "rest": rest_flag,
                "E": int(t0 * 1000)}, b, a

    if not rest_rule:
        m, _, _ = snapshot_msg(uid, False)
        if rule == "prev":
            m["pu"] = -1
        msgs.append(json.dumps(m))
    prev_u = uid
    for k in range(n):
        span = rng.randint(1, 4) if rule in ("binance_perp", "binance_spot", "next") else 1
        first, last = prev_u + 1, prev_u + span
        if rule == "prev":
            last = prev_u + rng.randint(1, 9)
        changes = {"bid": [], "ask": []}
        for _ in range(rng.randint(1, 4)):
            side = rng.choice(("bid", "ask"))
            i = rng.randint(0, depth + 4)
            px = _px(99.99 - i * 0.01) if side == "bid" else _px(100.01 + i * 0.01)
            size = "0" if rng.random() < 0.25 and len(book[side]) > 5 else str(rng.randint(1, 80))
            changes[side].append([px, size])
        if far_every and k % far_every == 0:
            changes["bid"].append([_px(95.00 - rng.randint(0, 50) * 0.01), str(rng.randint(1, 9))])
            changes["ask"].append([_px(105.00 + rng.randint(0, 50) * 0.01), str(rng.randint(1, 9))])
        for side in ("bid", "ask"):
            for px, size in changes[side]:
                _ref_apply(book, side, px, size)
        d = {"ch": "book", "type": "delta", "s": native, "b": changes["bid"], "a": changes["ask"], "E": int(t0 * 1000) + k * 100}
        if rule == "prev":
            d.update({"u": last, "pu": prev_u})
        elif rule == "bybit":
            d.update({"u": last})
        else:
            d.update({"U": first, "u": last, "pu": prev_u})
        if crc:
            b = sorted(book["bid"].items(), key=lambda r: -float(r[0]))
            a = sorted(book["ask"].items(), key=lambda r: float(r[0]))
            d["cs"] = A.crc32_levels(b, a) + (1 if bad_crc_at is not None and k == bad_crc_at else 0)
        deltas.append((k, d))
        prev_u = last
        if rest_rule and k == snap_at:
            m, b, a = snapshot_msg(last, True)            # lastUpdateId = u дельты k (Binance perp: U ≤ L ≤ u; spot: следующая U = L + 1)
            rest = json.dumps(m)
            bounds = (min(float(p) for p, _ in b), max(float(p) for p, _ in a))
    for k, d in deltas:
        if gap_at is not None and k == gap_at:
            continue
        msgs.append(json.dumps(d))
        if rest_rule and k == snap_at:
            rest_after = len(msgs)
    return {"msgs": msgs, "rest": rest, "rest_after": rest_after, "ref": book, "bounds": bounds, "gap_at": gap_at}


def bounded_ref(script):
    """Эталон с учётом границы REST-снимка (уровни дальше неё биржа после снимка не отражает в нашем стакане)."""
    ref, b = script["ref"], script["bounds"]
    if not b:
        return {"bid": dict(ref["bid"]), "ask": dict(ref["ask"])}
    return {"bid": dict((p, s) for p, s in ref["bid"].items() if float(p) >= b[0]),
            "ask": dict((p, s) for p, s in ref["ask"].items() if float(p) <= b[1])}


# ---------------------------------------------------------------- фикстуры
def load_fixture(path):
    """Строки фикстуры {"t": t_rx, "m": текст} -> [(t_rx, текст)]; REST-строки ({"rest", "body"}) пропускаются."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and isinstance(r.get("m"), str):
                out.append((float(r.get("t") or 0.0), r["m"]))
    return out


# ---------------------------------------------------------------- WS-сервер на 127.0.0.1 (только тесты)
class _Peer(object):
    def __init__(self, server, reader, writer, index):
        self.server, self.r, self.w, self.index = server, reader, writer, index
        self.topics, self.received = [], []
        self.sub_event = asyncio.Event()
        self.closed = False

    async def send(self, text):
        self.w.write(W.encode_frame(W.OP_TEXT, text.encode("utf-8"), mask=False))
        await self.w.drain()

    def drop(self):
        self.closed = True
        try:
            self.w.close()
        except Exception:                                   # noqa: BLE001
            pass

    async def reader(self):
        try:
            while not self.closed:
                fin, op, payload = await W.read_frame(self.r, 1 << 20, expect_masked=True)
                if op == W.OP_PING:
                    self.w.write(W.encode_frame(W.OP_PONG, payload, mask=False))
                    await self.w.drain()
                    continue
                if op == W.OP_CLOSE:
                    self.drop()
                    return
                if op != W.OP_TEXT:
                    continue
                text = payload.decode("utf-8")
                self.received.append(text)
                await self.server.on_text(self, text)
        except (asyncio.IncompleteReadError, ConnectionError, W.WSError, OSError):
            self.closed = True


class FakeServer(object):
    """WS-сервер поддельной биржи. script(peer) — корутина сценария соединения (по умолчанию держит соединение); reject — темы, на
    которые биржа отвечает отказом всего запроса; answer_ping — отвечать pong на текстовый пинг."""

    def __init__(self, script=None, reject=(), answer_ping=True):
        self.script = script
        self.reject = set(reject)
        self.answer_ping = answer_ping
        self.peers = []
        self.server = None
        self.url = None

    async def start(self):
        self.server = await asyncio.start_server(self._on, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.url = "ws://127.0.0.1:%d/ws" % self.port
        return self

    async def stop(self):
        for p in self.peers:
            p.drop()
        self.server.close()
        await self.server.wait_closed()

    async def on_text(self, peer, text):
        try:
            m = json.loads(text)
        except ValueError:
            return
        op = m.get("op")
        if op == "ping":
            if self.answer_ping:
                await peer.send('{"op":"pong"}')
            return
        if op in ("subscribe", "unsubscribe"):
            args = list(m.get("args") or ())
            bad = [a for a in args if a in self.reject]
            ok = not bad
            if ok and op == "subscribe":
                peer.topics.extend(a for a in args if a not in peer.topics)
                peer.sub_event.set()
            elif ok:
                peer.topics = [t for t in peer.topics if t not in args]
            await peer.send(json.dumps({"op": op, "ok": ok, "args": args, "msg": ("bad topic %s" % bad[0]) if bad else None}))

    async def _on(self, reader, writer):
        peer = None
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
            key = [ln.split(":", 1)[1].strip() for ln in head.split("\r\n") if ln.lower().startswith("sec-websocket-key")][0]
            acc = base64.b64encode(hashlib.sha1((key + W.GUID).encode()).digest()).decode()
            writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          "Sec-WebSocket-Accept: %s\r\n\r\n" % acc).encode())
            await writer.drain()
            peer = _Peer(self, reader, writer, len(self.peers))
            self.peers.append(peer)
            rd = asyncio.ensure_future(peer.reader())
            try:
                if self.script is not None:
                    await self.script(peer)
                else:
                    await rd
            finally:
                rd.cancel()
        except (asyncio.IncompleteReadError, ConnectionError, W.WSError, OSError, IndexError):
            pass
        finally:
            if peer is not None:
                peer.closed = True
            try:
                writer.close()
            except Exception:                               # noqa: BLE001
                pass


async def wait_topics(peer, n, timeout=5.0):
    """Ждать, пока на соединении подписано не меньше n тем."""
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while len(peer.topics) < n and loop.time() < end:
        peer.sub_event.clear()
        try:
            await asyncio.wait_for(peer.sub_event.wait(), max(0.01, end - loop.time()))
        except asyncio.TimeoutError:
            break
    return len(peer.topics) >= n
