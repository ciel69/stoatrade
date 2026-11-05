"""Потоковые кросс-биржевые данные через сокеты ccxt.pro (публичные каналы, без ключей): сделки, стакан, тикер и ликвидации в реальном времени.

Указание владельца 2026-11-01: для live-сделок данные должны идти по сокетам, а не опросом. Поток держит монеты открытых позиций и планов исполнителя
(всегда первыми), затем BTC/ETH/SOL и горячие; биржи из конфига xflow (bybit, binanceusdm, okx, bitget). Каждая монета на каждой бирже:
  watch_trades      -> лента сделок xflow.Tape (CVD за 5/15 мин; у потока разрывов нет, страница считается бесконечной);
  watch_order_book  -> xflow.book_stats (полосы ликвидности, корзины для стен);
  watch_ticker      -> цена, фандинг и открытый интерес из info биржи (где биржа их отдаёт в тикере), изменение OI за 5 мин;
  watch_liquidations -> кольцо ликвидаций: USD лонгов и шортов за 5 мин и 1 ч (ccxt.pro: bybit, binanceusdm, okx; у bitget канала нет).
Результат пишется в runtime/intel/xflow.json (cvd/ob/walls/slow по монетам + блок live), поэтому xflow.summary() -> facts deriv[SYM]["x"] и
сторож позиции читают его без изменений. Метод, которого у биржи нет, не вызывается; сбой одной биржи = её backoff, остальные работают.
Цикл asyncio живёт в отдельном потоке сервиса intel; никаких ордеров, ключей и импорта исполнителя здесь нет.
"""
import asyncio
import json
import threading
import time
from collections import deque

from tb import paths
from tb.core import filelock
from tb.intel import xflow

DEFAULTS = {"enabled": True, "n_syms": 8, "publish_s": 2.0, "resync_s": 30.0, "book_limit": {"bybit": 50, "binanceusdm": 20, "okx": None, "bitget": 15},
            # биржи потока: Bybit здесь не нужен (его сделки, стакан и ликвидации платформа уже слушает своими сокетами: flowwatch, wsx, liqwatch; REST ccxt к Bybit
            # с этой машины отвечает 403); binanceusdm оставлен: при гео-блокировке уходит в backoff и не мешает остальным
            "exchanges": ["okx", "bitget", "binanceusdm"],
            "liq_exchanges": ["binanceusdm"],                 # у okx канал ликвидаций «emulated» и падает ExchangeError, у bitget его нет
            "liq_keep_s": 3600.0, "backoff_s": 5.0, "backoff_max_s": 900.0}
KINDS = ("trades", "book", "ticker", "liq")
HAS = {"trades": "watchTrades", "book": "watchOrderBook", "ticker": "watchTicker", "liq": "watchLiquidations"}
OI_HIST_S = 600.0
_f = xflow._f


def scfg(cfg):
    x = (cfg or {}).get("xstream") if isinstance((cfg or {}).get("xstream"), dict) else {}
    c = dict(DEFAULTS, **x)
    c["book_limit"] = dict(DEFAULTS["book_limit"], **(x.get("book_limit") or {}))
    c["exchanges"] = [e for e in (c.get("exchanges") or []) if isinstance(e, str) and e.isalnum()]
    return c


def available():
    try:
        import ccxt.pro  # noqa: F401
        return True
    except ImportError:
        return False


def position_symbols(root):
    """Монеты открытых позиций и планов исполнителя (runtime/exec_snapshot.json), позиции первыми; пусто при любом сбое."""
    try:
        with open(paths.exec_snapshot_file(root=root), encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for key in ("positions", "plans"):
        for p in (snap.get(key) if isinstance(snap, dict) and isinstance(snap.get(key), list) else []):
            s = p.get("sym") if isinstance(p, dict) else None
            if isinstance(s, str) and s.isalnum() and s not in out:
                out.append(s)
    return out


def requested_symbols(root, now=None, ttl_s=1800.0):
    """Монеты, по которым мозг или сканер запросили структуру (runtime/structure_req.json): монеты событий, новые первыми. Пусто при любом сбое."""
    now = time.time() if now is None else now
    try:
        with open(paths.runtime("structure_req.json", root=root), encoding="utf-8") as f:
            req = (json.load(f) or {}).get("req") or {}
    except (OSError, ValueError, AttributeError):
        return []
    live = [(float(t), s) for s, t in req.items() if isinstance(s, str) and s.isalnum() and isinstance(t, (int, float)) and 0 <= now - t < ttl_s]
    return [s for _, s in sorted(live, reverse=True)]


def symbol_set(root, extra, n, now=None):
    """Позиции и планы первыми, затем монеты событий (запросы структуры), затем BTC/ETH/SOL, затем extra (горячие intel); не больше n."""
    out = []
    for s in position_symbols(root) + requested_symbols(root, now) + ["BTC", "ETH", "SOL"] + [x for x in extra or [] if isinstance(x, str)]:
        if s not in out:
            out.append(s)
    return out[:int(n)]


class LiqRing(object):
    """Кольцо ликвидаций одной монеты на одной бирже: (ts, usd лонгов, usd шортов)."""

    def __init__(self, keep_s=3600.0):
        self.rows, self.keep_s = deque(), float(keep_s)

    def add(self, items, cs, now):
        n = 0
        for x in items or ():
            if not isinstance(x, dict):
                continue
            usd = _f(x.get("quoteValue"))
            if usd is None and _f(x.get("contracts")) and _f(x.get("price")):
                usd = _f(x["contracts"]) * (_f(x.get("contractSize")) or cs) * _f(x["price"])
            if usd is None or usd <= 0:
                continue
            ts = (_f(x.get("timestamp")) or now * 1000.0) / 1000.0
            side = x.get("side")
            lo, sh = (usd, 0.0) if side in ("long", "sell") else ((0.0, usd) if side in ("short", "buy") else (0.0, 0.0))
            if lo or sh:
                self.rows.append((ts, lo, sh))
                n += 1
        while self.rows and now - self.rows[0][0] > self.keep_s:
            self.rows.popleft()
        return n

    def window(self, now, w):
        lo = sum(r[1] for r in self.rows if now - r[0] <= w)
        sh = sum(r[2] for r in self.rows if now - r[0] <= w)
        return round(lo, 0), round(sh, 0)


def ticker_row(t, cs, now, hist):
    """Тикер ccxt -> {"px","fund_pct","oi_usd","oi5_pct"} из стандартных полей и info биржи (openInterest/openInterestValue, fundingRate); hist: deque (ts, oi_usd)."""
    if not isinstance(t, dict):
        return None
    info = t.get("info") if isinstance(t.get("info"), dict) else {}
    px = _f(t.get("last")) or _f(info.get("lastPrice")) or _f(info.get("last"))
    if not px:
        return None
    row = {"px": px}
    fr = _f(info.get("fundingRate")) if _f(info.get("fundingRate")) is not None else _f(t.get("fundingRate"))
    if fr is not None:
        row["fund_pct"] = round(fr * 100.0, 5)
    oi_usd = _f(info.get("openInterestValue"))
    if oi_usd is None:
        oi = _f(info.get("openInterest")) if _f(info.get("openInterest")) is not None else _f(info.get("openInterestAmount"))
        if oi is not None:
            oi_usd = oi * cs * px
    if oi_usd is not None:
        row["oi_usd"] = round(oi_usd, 0)
        hist.append((now, oi_usd))
        while hist and now - hist[0][0] > OI_HIST_S:
            hist.popleft()
        ref = next((v for ts, v in hist if now - ts >= 300.0), None)
        if ref:
            row["oi5_pct"] = round((oi_usd / ref - 1.0) * 100.0, 3)
    return row


class XStream(object):
    """Состояние потока: ленты, стаканы, тикеры и ликвидации по (биржа, монета); документ xflow.json; управление задачами asyncio."""

    def __init__(self, root, cfg, clock=time.time, factory=None, log=None, symbols_fn=None):
        self.root, self.cfg, self.clock = root, cfg, clock
        self.c = scfg(cfg)
        self.fc = xflow.fcfg(cfg)
        self.log = log or (lambda m: None)
        self.factory = factory                      # eid -> объект биржи ccxt.pro (тесты: фейк)
        self.symbols_fn = symbols_fn or (lambda: [])
        self.tapes, self.books, self.ticks, self.liqs, self.oih = {}, {}, {}, {}, {}
        self.exchanges = list(self.c["exchanges"])
        self.st = {e: {"ok": None, "fails": 0, "err": None, "msgs": 0, "kinds": {}} for e in self.exchanges}      # kinds: backoff по каналу, один сбойный канал не глушит остальные
        self.nosup = {}
        self.exs = {}
        self.tasks = {}                             # (eid, sym, kind) -> task
        self.symbols = []
        self._stop = False
        self._thread = None
        self.loop = None
        self._locks = {}

    # ---- биржи
    async def _exchange(self, eid):
        lock = self._locks.get(eid)
        if lock is None:
            lock = self._locks[eid] = asyncio.Lock()
        async with lock:                                 # один объект биржи и одна загрузка рынков на все задачи (иначе RateLimitExceeded на старте)
            ex = self.exs.get(eid)
            if ex is None:
                if self.factory is not None:
                    ex = self.factory(eid)
                else:
                    import ccxt.pro as cp
                    ex = getattr(cp, eid)({"enableRateLimit": True, "options": {"defaultType": "swap"}})
                await ex.load_markets()
                self.exs[eid] = ex
            return ex

    def _has(self, ex, eid, kind):
        if self.nosup.get(eid, {}).get(kind):
            return False
        v = (getattr(ex, "has", None) or {}).get(HAS[kind])
        if not v:
            self.nosup.setdefault(eid, {})[kind] = True
            return False
        return True

    def _cs(self, ex, m):
        return _f(((getattr(ex, "markets", None) or {}).get(m) or {}).get("contractSize")) or 1.0

    # ---- задачи
    async def _watch(self, eid, sym, kind):
        s = self.st[eid]
        ks = s["kinds"].setdefault(kind, {"fails": 0, "next_try": 0.0})
        while not self._stop:
            now = self.clock()
            if now < ks["next_try"]:
                await asyncio.sleep(min(5.0, ks["next_try"] - now))
                continue
            try:
                ex = await self._exchange(eid)
                m = "%s/USDT:USDT" % sym
                if kind == "liq" and eid not in (self.c.get("liq_exchanges") or []):
                    self.nosup.setdefault(eid, {})[kind] = True
                    return
                if m not in (getattr(ex, "markets", None) or {}) or not self._has(ex, eid, kind):
                    return
                cs = self._cs(ex, m)
                if kind == "trades":
                    tr = await ex.watch_trades(m)
                    self.tapes.setdefault((eid, sym), xflow.Tape()).merge(tr, cs, 10 ** 9, self.clock())
                elif kind == "book":
                    lim = self.c["book_limit"].get(eid, 50)
                    ob = await (ex.watch_order_book(m, int(lim)) if lim else ex.watch_order_book(m))
                    bk = {}
                    stt = xflow.book_stats(ob, cs, bk, self.fc["bucket_pct"])
                    if stt is not None:
                        stt.update({"at": self.clock(), "buckets": bk})
                        self.books[(eid, sym)] = stt
                elif kind == "ticker":
                    t = await ex.watch_ticker(m)
                    row = ticker_row(t, cs, self.clock(), self.oih.setdefault((eid, sym), deque()))
                    if row:
                        row["at"] = self.clock()
                        self.ticks[(eid, sym)] = row
                elif kind == "liq":
                    lq = await ex.watch_liquidations(m)
                    self.liqs.setdefault((eid, sym), LiqRing(self.c["liq_keep_s"])).add(lq, cs, self.clock())
                s["msgs"] += 1
                ks["fails"] = 0
                if s["ok"] is not True:
                    s.update({"ok": True, "err": None})
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - ни одна биржа и ни один канал не роняет поток
                if type(e).__name__ == "NotSupported":
                    self.nosup.setdefault(eid, {})[kind] = True
                    return
                ks["fails"] += 1
                s["fails"] += 1
                s["err"] = type(e).__name__[:40]
                if not any(k.get("fails", 0) == 0 for k in s["kinds"].values()):
                    s["ok"] = False                                        # ни один канал биржи не живёт
                ks["next_try"] = self.clock() + min(float(self.c["backoff_max_s"]), float(self.c["backoff_s"]) * 2 ** (ks["fails"] - 1))
                if ks["fails"] in (1, 5):
                    self.log("intel: xstream %s/%s %s недоступен (%s)" % (eid, sym, kind, type(e).__name__))
                await asyncio.sleep(0.5)

    def _resync(self, loop):
        """Набор монет сменился: новые задачи запускаются, лишние отменяются (и un_watch, если биржа умеет)."""
        syms = list(self.symbols_fn() or [])
        self.symbols = syms
        want = {(e, s, k) for e in self.exchanges for s in syms for k in KINDS}
        for key in list(self.tasks):
            if key not in want:
                self.tasks.pop(key).cancel()
                self.tapes.pop(key[:2], None) if key[2] == "trades" else None
        for key in sorted(want):
            if key not in self.tasks:
                self.tasks[key] = loop.create_task(self._watch(*key))

    async def _main(self):
        loop = asyncio.get_event_loop()
        t_pub = t_sync = -1e18
        while not self._stop:
            now = self.clock()
            if now - t_sync >= float(self.c["resync_s"]):
                t_sync = now
                try:
                    self._resync(loop)
                except Exception as e:  # noqa: BLE001
                    self.log("intel: xstream resync (%s)" % type(e).__name__)
            if now - t_pub >= float(self.c["publish_s"]):
                t_pub = now
                try:
                    filelock.atomic_write_json(xflow.xflow_path(self.root), self.document(now), indent=None)
                except (OSError, ValueError, TypeError) as e:
                    self.log("intel: xflow.json не записан (%s)" % type(e).__name__)
            await asyncio.sleep(0.5)
        for t in self.tasks.values():
            t.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        for ex in self.exs.values():
            try:
                await ex.close()
            except Exception:  # noqa: BLE001
                pass
        await asyncio.sleep(0.25)                      # дать aiohttp закрыть соединения до остановки цикла

    def start(self):
        if self._thread is not None:
            return self._thread
        self._thread = threading.Thread(target=self._run_thread, name="xstream", daemon=True)
        self._thread.start()
        return self._thread

    def _run_thread(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        except Exception as e:  # noqa: BLE001
            self.log("intel: xstream остановлен (%s)" % type(e).__name__)
        finally:
            self.loop.close()

    def stop(self):
        self._stop = True

    # ---- документ
    def document(self, now):
        """Документ xflow.json: cvd/ob/walls/slow по монетам плюс live (тикеры бирж и ликвидации за 5 мин/1 ч). Тот же формат пишет
        tb.cmkt.compat при compat_xflow=true (тогда сервис intel этот поток не запускает)."""
        c = self.fc
        out = {"ts_epoch": now, "src": "xstream", "req_min": 0,
               "ex": {e: {"ok": s["ok"], "fails": s["fails"], "err": s["err"], "msgs": s["msgs"], "nosup": sorted(self.nosup.get(e, {})),
                          "down": sorted(k for k, v in s["kinds"].items() if v.get("fails", 0) > 0)} for e, s in self.st.items()},
               "syms": {}}
        for sym in self.symbols:
            row = {}
            tapes = {e: t for (e, s), t in self.tapes.items() if s == sym and t.at is not None and now - t.at <= c["stale_s"]}
            if tapes and any(t.newest for t in tapes.values()):
                anchor = max(t.newest for t in tapes.values() if t.newest is not None)
                row["cvd"] = {e: {"w5": xflow._w(t.window(anchor, 300)), "w15": xflow._w(t.window(anchor, 900))} for e, t in tapes.items()}
                row["cvd_anchor"] = anchor
            bks = {e: b for (e, s), b in self.books.items() if s == sym and now - b["at"] <= c["stale_s"]}
            if bks:
                row["ob"] = {e: {"mid": b["mid"], "depth_pct": b["depth_pct"], "bands": b["bands"]} for e, b in bks.items()}
                mids = sorted(b["mid"] for b in bks.values())
                mid = mids[len(mids) // 2]
                agg = {}
                for e, b in bks.items():
                    for k, v in b["buckets"].items():
                        a = agg.setdefault(k, [0.0, 0.0, v[2], set()])
                        a[0] += v[0]
                        if v[1] > a[1]:
                            a[1], a[2] = v[1], v[2]
                        a[3].add(e)
                row["walls"] = xflow.walls(agg, mid, c["wall_mult"], c["min_wall_usd"], c["bucket_pct"])
            live = {}
            for (e, s), t in self.ticks.items():
                if s == sym and now - t["at"] <= c["stale_s"]:
                    live[e] = {k: v for k, v in t.items() if k != "at"}
            slow = {}
            for (e, s), r in self.liqs.items():
                if s != sym:
                    continue
                l5, s5 = r.window(now, 300.0)
                l1, s1 = r.window(now, 3600.0)
                slow[e] = {"liq_long_usd": l1, "liq_short_usd": s1}
                live.setdefault(e, {}).update({"liq5_long_usd": l5, "liq5_short_usd": s5})
            for (e, s), t in self.ticks.items():
                if s == sym and now - t["at"] <= c["stale_s"] and e != "bybit":
                    sl = slow.setdefault(e, {})
                    if t.get("fund_pct") is not None:
                        sl["fundh_pct"] = t["fund_pct"]
                    if t.get("oi_usd") is not None:
                        sl["oi_usd"] = t["oi_usd"]
            if slow:
                row["slow"] = slow
            if live:
                row["live"] = live
            if row:
                out["syms"][sym] = row
        return out


def live_for(root, sym, now, max_age_s=30.0):
    """Сводка потока по монете для сторожа позиции и досье: единая реализация в tb.core.flowdoc.xlive_for (читают только файл xflow.json)."""
    from tb.core import flowdoc
    return flowdoc.xlive_for(root, sym, now, max_age_s)
