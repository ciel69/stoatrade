"""Кросс-биржевой поток (только публичное чтение, без ключей): подтверждают ли другие биржи то, что идёт на Bybit. Всё — числа.

Файл runtime/intel/xflow.json пишет один производитель: поток по сокетам tb.xdata.xstream (сервис intel) или, при config/cmkt.json
compat_xflow=true, сводный рынок tb.cmkt.compat (формат XStream.document, src="cmkt"). Опросный класс XFlow удалён (R1.7, задача 22;
вызовов не было). Здесь остаются общие части формата:
  Tape — лента сделок одной монеты на одной бирже: CVD = агрессивные покупки минус продажи в USD (количество x contractSize x цена) за 5 и 15 мин,
      покрытие окна cov (0..1) честно меньше 1 при разрыве;
  book_stats — ликвидность bid/ask в полосах ±0.5/1/2 % от середины (USD с contractSize); полоса считается только у биржи, чья выдача стакана
      её покрывает (глубина выдачи depth_pct); корзины bucket_pct % для стен;
  walls — стены суммарно по всем биржам: корзина в wall_mult раз крупнее медианы корзин стороны и не меньше min_wall_usd, до 3 на сторону;
  oi_changes, read, summary — чтение документа и сжатие в facts: summary() -> store.build_facts deriv[SYM]["x"].
"""
import statistics
from collections import deque

from tb import paths
from tb.intel import xex as ixex

DEFAULTS = {"enabled": True, "exchanges": list(ixex.DEFAULT_EXCHANGES), "n_syms": 8, "trades_s": 20.0, "book_s": 45.0, "slow_s": 300.0,
            "div_pct": 10.0, "min_cov": 0.9, "wall_mult": 5.0, "min_wall_usd": 200000.0, "bucket_pct": 0.1, "stale_s": 180.0, "slow_stale_s": 1200.0,
            "trades_limit": {"okx": 500}, "book_limit": {"bybit": 500, "binanceusdm": 1000, "okx": 1000, "bitget": 100}}
BANDS = (("05", 0.5), ("1", 1.0), ("2", 2.0))
KEEP_S = 960.0


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
