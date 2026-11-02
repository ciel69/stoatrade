"""Кросс-биржевой поток через ccxt (только публичное чтение, без ключей): подтверждают ли другие биржи то, что идёт на Bybit. Всё — числа.

По небольшому набору монет (первые n_syms символов intel; BTC/ETH/SOL всегда первые) на bybit, binanceusdm, okx, bitget, каждая биржа своим потоком:
  сделки (раз в trades_s): последние N сделок (fetch_trades без since: since у Bybit игнорируется, у Bitget режет выдачу), склейка по id в ленту;
      CVD = агрессивные покупки минус продажи в USD (количество x contractSize x цена) за 5 и 15 мин; окна считаются от последней сделки по монете
      на всех биржах (часы бирж, а не локальные: локальные могут отставать на десятки секунд). Полная страница, которая не дотянулась до прошлого
      опроса, = разрыв: лента начинается заново, покрытие окна cov (0..1) честно меньше 1;
  стакан (раз в book_s): ликвидность bid/ask в полосах ±0.5/1/2 % от середины (USD с contractSize), дисбаланс (bid-ask)/(bid+ask)*100 по бирже и
      суммарно; полоса считается только у биржи, чья выдача стакана её покрывает (глубина выдачи depth_pct); стены суммарно по всем биржам:
      корзины bucket_pct % от середины, корзина в wall_mult раз крупнее медианы корзин стороны и не меньше min_wall_usd, до 3 на сторону;
  медленное (раз в slow_s, только другие биржи: у Bybit свои oi_/ls_ источники intel): long/short ratio (последнее), история OI (изменение за 1 и 24 ч),
      фандинг за 3 последних расчёта, ликвидации за 1 ч там, где ccxt отдаёт их публично (на 2026-11 ни одна из четырёх бирж: честно отсутствуют).
Метод, которого у биржи нет (has[...] пусто или NotSupported), больше не вызывается. Любая другая ошибка биржи = backoff этой биржи (VPN, гео-блок),
остальные работают. Результат: runtime/intel/xflow.json (числа и идентификаторы), сжатие в facts: summary() -> store.build_facts deriv[SYM]["x"].
"""
import concurrent.futures
import statistics
import time
from collections import deque

from tb import paths
from tb.core import filelock
from tb.intel import xex as ixex

DEFAULTS = {"enabled": True, "exchanges": list(ixex.DEFAULT_EXCHANGES), "n_syms": 8, "trades_s": 20.0, "book_s": 45.0, "slow_s": 300.0,
            "div_pct": 10.0, "min_cov": 0.9, "wall_mult": 5.0, "min_wall_usd": 200000.0, "bucket_pct": 0.1, "stale_s": 180.0, "slow_stale_s": 1200.0,
            "trades_limit": {"okx": 500}, "book_limit": {"bybit": 500, "binanceusdm": 1000, "okx": 1000, "bitget": 100}}
BANDS = (("05", 0.5), ("1", 1.0), ("2", 2.0))
KEEP_S = 960.0
BITGET_PREC = ("scale0", "scale1", "scale2")       # слияние уровней Bitget: выдача всего 100 уровней, у BTC без слияния это ±0.03 %


def fcfg(cfg):
    x = (cfg or {}).get("xflow") if isinstance((cfg or {}).get("xflow"), dict) else {}
    c = dict(DEFAULTS, **x)
    c["exchanges"] = [e for e in (c.get("exchanges") or []) if isinstance(e, str) and e.isalnum()]
    for k in ("trades_limit", "book_limit"):
        c[k] = dict(DEFAULTS[k], **(x.get(k) or {}))
    return c


def xflow_path(root=None):
    return paths.runtime("intel", "xflow.json", root=root)


_f = ixex._f


def _r(x, nd=0):
    return None if x is None else (int(round(x)) if nd == 0 else round(x, nd))


def _cs(ex, m):
    return _f(((ex.markets or {}).get(m) or {}).get("contractSize")) or 1.0


def _unsupported(e):
    return type(e).__name__ in ("NotSupported",)


class Tape(object):
    """Лента сделок одной монеты на одной бирже: (ts_s, знаковый USD), начало непрерывного участка start и последняя сделка newest."""

    def __init__(self):
        self.t = deque()
        self.ids = deque(maxlen=3000)
        self.idset = set()
        self.start = self.newest = None
        self.at = None                      # локальное время последнего успешного опроса

    def merge(self, trades, cs, page, now):
        rows = []
        for it in trades or ():
            if not isinstance(it, dict):
                continue
            ts, p, a = _f(it.get("timestamp")), _f(it.get("price")), _f(it.get("amount"))
            sign = {"buy": 1, "sell": -1}.get(it.get("side"))
            if not ts or not p or not a or sign is None:
                continue
            key = it.get("id") or (ts, p, a, sign)
            rows.append((ts / 1000.0, sign * a * cs * p, key))
        rows.sort(key=lambda r: r[0])
        self.at = now
        if not rows:
            return 0
        if self.newest is None or (len(rows) >= page and rows[0][0] > self.newest):     # первый опрос или разрыв: полная страница не дотянулась
            self.t.clear()
            self.ids.clear()
            self.idset.clear()
            self.start = rows[0][0]
        n = 0
        for ts, usd, key in rows:
            if key in self.idset or (self.newest is not None and ts < self.newest - 60):
                continue
            if len(self.ids) == self.ids.maxlen:
                self.idset.discard(self.ids[0])
            self.ids.append(key)
            self.idset.add(key)
            self.t.append((ts, usd))
            n += 1
        self.newest = max(self.newest or 0.0, rows[-1][0])
        while self.t and self.newest - self.t[0][0] > KEEP_S:
            self.t.popleft()
        self.start = max(self.start, self.newest - KEEP_S)
        return n

    def window(self, anchor, w):
        """-> {"net","buy","sell","cov"} за (anchor-w, anchor]; cov — доля окна, покрытая непрерывной лентой."""
        if self.newest is None:
            return None
        buy = sell = 0.0
        for ts, usd in reversed(self.t):
            if ts <= anchor - w:
                break
            if ts > anchor:
                continue
            if usd > 0:
                buy += usd
            else:
                sell -= usd
        cov = max(0.0, min(anchor, self.newest) - max(anchor - w, self.start)) / w
        return {"net": buy - sell, "buy": buy, "sell": sell, "cov": round(min(1.0, cov), 2)}


def book_stats(ob, cs, buckets_out=None, bucket_pct=0.1):
    """Стакан ccxt -> {"mid","depth_pct","bands": {"05": [bid_usd, ask_usd] | None}}; корзины для стен кладутся в buckets_out[(side, k)] -> [usd, max_usd, px]."""
    bids = [(_f(r[0]), _f(r[1])) for r in (ob or {}).get("bids") or [] if isinstance(r, (list, tuple)) and len(r) >= 2]
    asks = [(_f(r[0]), _f(r[1])) for r in (ob or {}).get("asks") or [] if isinstance(r, (list, tuple)) and len(r) >= 2]
    bids = [(p, a) for p, a in bids if p and a and p > 0 and a > 0]
    asks = [(p, a) for p, a in asks if p and a and p > 0 and a > 0]
    if not bids or not asks:
        return None
    bb, ba = max(p for p, _ in bids), min(p for p, _ in asks)
    if bb >= ba:
        return None
    mid = (bb + ba) / 2.0
    depth = min((mid - min(p for p, _ in bids)) / mid, (max(p for p, _ in asks) - mid) / mid) * 100.0
    bands = {}
    for name, pct in BANDS:
        if depth < pct * 0.98:
            bands[name] = None                                     # выдача стакана не покрывает полосу: не выдумываем
            continue
        bands[name] = [round(sum(p * a * cs for p, a in bids if (mid - p) / mid * 100 <= pct), 0),
                       round(sum(p * a * cs for p, a in asks if (p - mid) / mid * 100 <= pct), 0)]
    if buckets_out is not None:
        for side, rows in (("bid", bids), ("ask", asks)):
            for p, a in rows:
                dist = abs(p - mid) / mid * 100.0
                if dist > BANDS[-1][1]:
                    continue
                usd = p * a * cs
                b = buckets_out.setdefault((side, int(dist // bucket_pct)), [0.0, 0.0, p, set()])
                b[0] += usd
                if usd > b[1]:
                    b[1], b[2] = usd, p
                b[3].add(id(buckets_out))
    return {"mid": mid, "depth_pct": round(depth, 3), "bands": bands}


def walls(buckets, mid, mult, min_usd, bucket_pct):
    """Корзины всех бирж -> до 3 стен на сторону: {"side","px","usd","x","dist_pct","n_ex"}."""
    out = []
    for side in ("bid", "ask"):
        lv = {k: v for (s, k), v in buckets.items() if s == side}
        if len(lv) < 8:
            continue
        med = statistics.median(v[0] for v in lv.values())
        cand = [(v[0], k, v) for k, v in lv.items() if med > 0 and v[0] >= max(min_usd, mult * med)]
        for usd, k, v in sorted(cand, key=lambda c: c[0], reverse=True)[:3]:
            out.append({"side": side, "px": v[2], "usd": round(usd, 0), "x": round(usd / med, 1), "dist_pct": round(abs(v[2] - mid) / mid * 100.0, 2),
                        "n_ex": len(v[3])})
    return out


class XFlow(object):
    def __init__(self, root, cfg, clock=time.time, factory=None, log=None, parallel=True):
        self.root, self.cfg, self.clock = root, cfg, clock
        self.c = fcfg(cfg)
        self.log = log or (lambda m: None)
        self.col = ixex.Collector(root, cfg, clock, factory=factory, log=self.log)     # свои объекты бирж: xex живёт в другом потоке
        self.factory = factory
        self.parallel = parallel
        self.tapes = {}                     # (eid, sym) -> Tape
        self.books = {}                     # (eid, sym) -> {"at", "mid", "depth_pct", "bands", "buckets"}
        self.slow = {}                      # (eid, sym) -> {"at", "ls", "oi1h_pct", "oi24h_pct", "oi_usd", "fundh_pct", "liq_long_usd", "liq_short_usd"}
        self.st = {e: {"ok": None, "fails": 0, "next_try": 0.0, "err": None, "t_trades": -1e18, "t_book": -1e18, "t_slow": -1e18} for e in self.c["exchanges"]}
        self.nosup = {}                     # eid -> {вид: True}: метода нет, больше не вызываем
        self.prec = {}                      # (eid, sym) -> индекс BITGET_PREC
        self.reqs = deque(maxlen=5000)      # время запросов (бюджет)

    def _call(self, eid, kind, fn):
        if self.nosup.get(eid, {}).get(kind):
            return None
        self.reqs.append(self.clock())
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if _unsupported(e):
                self.nosup.setdefault(eid, {})[kind] = True
                return None
            raise

    def _has(self, ex, eid, name, kind):
        if not (getattr(ex, "has", None) or {}).get(name):
            self.nosup.setdefault(eid, {})[kind] = True
        return not self.nosup.get(eid, {}).get(kind)

    # ---- одна биржа: всё, у чего подошёл срок; исключения наружу (backoff в _run)
    def _exchange_round(self, eid, symbols, now):
        s = self.st[eid]
        do_t, do_b, do_s = (now - s["t_trades"] >= self.c["trades_s"], now - s["t_book"] >= self.c["book_s"],
                            eid != "bybit" and now - s["t_slow"] >= self.c["slow_s"])
        if not (do_t or do_b or do_s):
            return False
        ex = self.col._exchange(eid, now)
        mk = {sym: "%s/USDT:USDT" % sym for sym in symbols if "%s/USDT:USDT" % sym in (ex.markets or {})}
        if do_t:
            page = int(self.c["trades_limit"].get(eid, 1000))
            for sym, m in mk.items():
                if self._has(ex, eid, "fetchTrades", "trades"):
                    tr = self._call(eid, "trades", lambda: ex.fetch_trades(m, None, page))
                    if tr is not None:
                        self.tapes.setdefault((eid, sym), Tape()).merge(tr, _cs(ex, m), page, now)
            s["t_trades"] = now
        if do_b:
            for sym, m in mk.items():
                if not self._has(ex, eid, "fetchOrderBook", "book"):
                    break
                lim, params = int(self.c["book_limit"].get(eid, 100)), {}
                if eid == "bitget":
                    params = {"precision": BITGET_PREC[self.prec.get((eid, sym), 1)], "limit": "max"}
                    lim = None
                ob = self._call(eid, "book", lambda: ex.fetch_order_book(m, lim, params))
                if ob is None:
                    continue
                bk = {}
                st = book_stats(ob, _cs(ex, m), bk, self.c["bucket_pct"])
                if st is None:
                    continue
                st.update({"at": now, "buckets": bk})
                self.books[(eid, sym)] = st
                if eid == "bitget":                                   # слияние подстраивается: полоса ±1 % должна быть видна, но без лишней грубости
                    i = self.prec.get((eid, sym), 1)
                    self.prec[(eid, sym)] = min(2, i + 1) if st["depth_pct"] < 1.0 else (max(0, i - 1) if st["depth_pct"] >= 4.0 else i)
            s["t_book"] = now
        if do_s:
            for sym, m in mk.items():
                self.slow[(eid, sym)] = self._slow_one(ex, eid, m, now)
            s["t_slow"] = now
        return True

    def _slow_one(self, ex, eid, m, now):
        row = {"at": now}
        if self._has(ex, eid, "fetchLongShortRatioHistory", "ls"):
            r = self._call(eid, "ls", lambda: ex.fetch_long_short_ratio_history(m, "5m", None, 3)) or []
            r = sorted([x for x in r if isinstance(x, dict) and _f(x.get("longShortRatio")) and _f(x.get("timestamp"))], key=lambda x: x["timestamp"])
            if r:
                row["ls"] = round(_f(r[-1]["longShortRatio"]), 3)
        if self._has(ex, eid, "fetchOpenInterestHistory", "oih"):
            r = self._call(eid, "oih", lambda: ex.fetch_open_interest_history(m, "1h", None, 26)) or []
            row.update(oi_changes(r))
        if self._has(ex, eid, "fetchFundingRateHistory", "frh"):
            r = self._call(eid, "frh", lambda: ex.fetch_funding_rate_history(m, None, 3)) or []
            fs = [_f(x.get("fundingRate")) for x in sorted([x for x in r if isinstance(x, dict) and _f(x.get("timestamp"))], key=lambda x: x["timestamp"])[-3:]]
            fs = [x for x in fs if x is not None]
            if fs:
                row["fundh_pct"] = round(sum(fs) / len(fs) * 100.0, 5)
        if self._has(ex, eid, "fetchLiquidations", "liq"):
            r = self._call(eid, "liq", lambda: ex.fetch_liquidations(m, int(now * 1000 - 3600000))) or []
            lo = sh = 0.0
            for x in r:
                if not isinstance(x, dict):
                    continue
                usd = _f(x.get("quoteValue"))
                if usd is None and _f(x.get("contracts")) and _f(x.get("price")):
                    usd = _f(x["contracts"]) * (_f(x.get("contractSize")) or _cs(ex, m)) * _f(x["price"])
                side = x.get("side")
                if usd is None:
                    continue
                if side in ("long", "sell"):                             # ликвидация лонга исполняется продажей
                    lo += usd
                elif side in ("short", "buy"):
                    sh += usd
            row["liq_long_usd"], row["liq_short_usd"] = round(lo, 0), round(sh, 0)
        return row

    def _run(self, eid, symbols, now):
        s = self.st[eid]
        if now < s["next_try"]:
            return
        try:
            if self._exchange_round(eid, symbols, now):
                s.update({"ok": True, "fails": 0, "err": None})
        except Exception as e:  # noqa: BLE001 - ни одна биржа не роняет intel
            s["fails"] += 1
            s.update({"ok": False, "err": type(e).__name__[:40], "next_try": now + min(1800.0, 30.0 * 2 ** (s["fails"] - 1))})
            if s["fails"] in (1, 4):
                self.log("intel: xflow %s недоступен (%s)" % (eid, type(e).__name__))

    def step(self, symbols, now=None):
        """Опрос бирж, у которых подошёл срок, и запись xflow.json. Не бросает. -> документ или None."""
        now = self.clock() if now is None else now
        if not ixex.available() and self.factory is None:
            return None
        syms = list(dict.fromkeys(["BTC", "ETH", "SOL"] + [x for x in symbols or [] if isinstance(x, str)]))[:int(self.c["n_syms"])]
        exs = [e for e in self.c["exchanges"] if e in self.st]
        if self.parallel and len(exs) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(exs)) as pool:
                list(pool.map(lambda e: self._run(e, syms, now), exs))
        else:
            for e in exs:
                self._run(e, syms, now)
        try:
            doc = self.document(syms, now)
            filelock.atomic_write_json(xflow_path(self.root), doc, indent=None)
            return doc
        except (OSError, ValueError, TypeError) as e:
            self.log("intel: xflow не записан (%s)" % type(e).__name__)
            return None

    def req_per_min(self, now):
        return sum(1 for t in self.reqs if now - t <= 60)

    def document(self, syms, now):
        c = self.c
        out = {"ts_epoch": now, "req_min": self.req_per_min(now),
               "ex": {e: {"ok": s["ok"], "fails": s["fails"], "err": s["err"], "nosup": sorted(self.nosup.get(e, {}))} for e, s in self.st.items()}, "syms": {}}
        for sym in syms:
            row = {}
            tapes = {e: t for (e, s), t in self.tapes.items() if s == sym and t.at is not None and now - t.at <= c["stale_s"]}
            if tapes:
                anchor = max(t.newest for t in tapes.values() if t.newest is not None) if any(t.newest for t in tapes.values()) else None
                if anchor is not None:
                    row["cvd"] = {e: {"w5": _w(t.window(anchor, 300)), "w15": _w(t.window(anchor, 900))} for e, t in tapes.items()}
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
                row["walls"] = walls(agg, mid, c["wall_mult"], c["min_wall_usd"], c["bucket_pct"])
            sl = {e: {k: v for k, v in r.items() if k != "at"} for (e, s), r in self.slow.items() if s == sym and now - r["at"] <= c["slow_stale_s"]}
            if sl:
                row["slow"] = sl
            if row:
                out["syms"][sym] = row
        return out


def _w(w):
    return None if w is None else {"net": round(w["net"], 0), "buy": round(w["buy"], 0), "sell": round(w["sell"], 0), "cov": w["cov"]}


def oi_changes(points):
    """История OI ccxt (часовые точки) -> {"oi1h_pct","oi24h_pct","oi_usd"}: в монетах, если есть у обеих точек (без влияния цены), иначе в USD."""
    pts = sorted([p for p in points or [] if isinstance(p, dict) and _f(p.get("timestamp"))], key=lambda p: p["timestamp"])
    if len(pts) < 2:
        return {}
    last = pts[-1]
    out = {}
    v = _f(last.get("openInterestValue"))
    if v is not None:
        out["oi_usd"] = round(v, 0)
    for name, h in (("oi1h_pct", 1.0), ("oi24h_pct", 24.0)):
        want = last["timestamp"] - h * 3600000
        ref = min(pts[:-1], key=lambda p: abs(p["timestamp"] - want))
        if abs(ref["timestamp"] - want) > 0.25 * h * 3600000 + 600000:
            continue
        for k in ("openInterestAmount", "openInterestValue"):
            a, b = _f(last.get(k)), _f(ref.get(k))
            if a is not None and b:
                out[name] = round((a / b - 1.0) * 100.0, 3)
                break
    return out


def read(root=None):
    try:
        import json
        with open(xflow_path(root), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def summary(doc, now, cfg=None):
    """xflow.json -> {SYM: компактные поля для facts deriv[SYM]["x"]}. Устаревший документ -> {}."""
    c = fcfg(cfg)
    if not isinstance(doc, dict) or not isinstance(_f(doc.get("ts_epoch")), float) or now - doc["ts_epoch"] > c["stale_s"]:
        return {}
    out = {}
    for sym, r in (doc.get("syms") or {}).items():
        if not isinstance(r, dict):
            continue
        x = {}
        # --- CVD: Bybit и сумма других бирж, у которых окно покрыто (cov >= min_cov)
        cvd = r.get("cvd") if isinstance(r.get("cvd"), dict) else {}
        pct = {}
        for wk, w in (("w5", "5"), ("w15", "15")):
            bb = (cvd.get("bybit") or {}).get(wk)
            if isinstance(bb, dict):
                x["cvd%s_bybit_usd" % w] = bb["net"]
                x["cvd%s_bybit_cov" % w] = bb["cov"]
                if bb["cov"] >= c["min_cov"] and bb["buy"] + bb["sell"] > 0:
                    pct[("bybit", w)] = 100.0 * bb["net"] / (bb["buy"] + bb["sell"])
            oth = [v.get(wk) for e, v in cvd.items() if e != "bybit" and isinstance(v, dict) and isinstance(v.get(wk), dict)]
            ok = [o for o in oth if o["cov"] >= c["min_cov"]]
            if oth:
                x["cvd%s_x_n" % w] = len(ok)
                x["cvd%s_x_cov" % w] = min(o["cov"] for o in oth)
            if ok:
                net, tot = sum(o["net"] for o in ok), sum(o["buy"] + o["sell"] for o in ok)
                x["cvd%s_x_usd" % w] = round(net, 0)
                if tot > 0:
                    pct[("x", w)] = 100.0 * net / tot
                    x["cvd%s_x_pct" % w] = round(pct[("x", w)], 1)
        if ("bybit", "5") in pct:
            x["cvd5_bybit_pct"] = round(pct[("bybit", "5")], 1)
        if ("bybit", "5") in pct and ("x", "5") in pct:
            b, o, d = pct[("bybit", "5")], pct[("x", "5")], c["div_pct"]
            x["cvd_div"] = "bybit_sell_x_buy" if b <= -d and o >= d else ("bybit_buy_x_sell" if b >= d and o <= -d else "none")
        # --- стакан: полосы других бирж (только покрытые), дисбаланс Bybit отдельно
        ob = r.get("ob") if isinstance(r.get("ob"), dict) else {}
        for name, _ in BANDS:
            rows = [(v.get("bands") or {}).get(name) for e, v in ob.items() if e != "bybit" and isinstance(v, dict)]
            rows = [b for b in rows if isinstance(b, list) and len(b) == 2]
            if rows:
                bid, ask = sum(b[0] for b in rows), sum(b[1] for b in rows)
                x.update({"ob%s_x_bid_usd" % name: bid, "ob%s_x_ask_usd" % name: ask, "ob%s_x_n" % name: len(rows)})
                if bid + ask > 0:
                    x["ob%s_x_imb_pct" % name] = round(100.0 * (bid - ask) / (bid + ask), 1)
            bb = ((ob.get("bybit") or {}).get("bands") or {}).get(name)
            if isinstance(bb, list) and len(bb) == 2 and bb[0] + bb[1] > 0:
                x["ob%s_bybit_imb_pct" % name] = round(100.0 * (bb[0] - bb[1]) / (bb[0] + bb[1]), 1)
        ws = [w for w in r.get("walls") or [] if isinstance(w, dict) and w.get("side") in ("bid", "ask")]
        if ws:
            x["walls"] = [{k: w[k] for k in ("side", "px", "usd", "x", "dist_pct", "n_ex") if k in w} for w in ws[:6]]
        # --- медленное: другие биржи
        sl = r.get("slow") if isinstance(r.get("slow"), dict) else {}
        ls = [v["ls"] for v in sl.values() if isinstance(v, dict) and _f(v.get("ls"))]
        if ls:
            x["ls_x"] = round(sum(ls) / len(ls), 3)
        for k, name in (("oi1h_pct", "oi1h_x_pct"), ("oi24h_pct", "oi24h_x_pct")):
            pts = [(v[k], _f(v.get("oi_usd")) or 1.0) for v in sl.values() if isinstance(v, dict) and _f(v.get(k)) is not None]
            if pts:
                x[name] = round(sum(a * w for a, w in pts) / sum(w for _, w in pts), 3)        # взвешено по OI бирж в USD
        fh = [v["fundh_pct"] for v in sl.values() if isinstance(v, dict) and _f(v.get("fundh_pct")) is not None]
        if fh:
            x["fundh3_x_pct"] = round(sum(fh) / len(fh), 5)
        lq = [v for v in sl.values() if isinstance(v, dict) and _f(v.get("liq_long_usd")) is not None]
        if lq:
            x["liq1h_long_x_usd"] = round(sum(v["liq_long_usd"] for v in lq), 0)
            x["liq1h_short_x_usd"] = round(sum(v["liq_short_usd"] for v in lq), 0)
            x["liq_x_n"] = len(lq)
        if x:
            out[sym] = x
    return out
