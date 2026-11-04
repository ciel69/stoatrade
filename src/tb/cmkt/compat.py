"""Совместимый `runtime/intel/xflow.json` из сводного рынка (R1.7, задача 22; Requirements 16.5, 17.2–17.7; design.md, «Миграция
существующих модулей», свойство 20).

При `config/cmkt.json: compat_xflow = true` (и `enabled = true`, `conf.compat_active`) сводка (`tb.cmkt.aggregate.Aggregator.compat`)
раз в `logic.cmkt.COMPAT_PUBLISH_S` (2 с) пишет документ в формате `XStream.document` (`xdata/xstream.py`) с `src = "cmkt"`, а сервис
`intel` при этом флаге `XStream` не запускает: у файла один писатель. Охват первой итерации — ровно как у `xstream`:

- монеты — `focus.json: compat` (`logic.cmkt.compat_symbols`: позиции и планы, запросы структуры, BTC/ETH/SOL, затем топ по обороту
  из фокуса; до 8), все они в фокусе;
- рынки `okx_perp`, `bitget_perp`, `binance_perp` под именами `okx`, `bitget`, `binanceusdm` (без Bybit и без спота);
- стакан — поле `cx` документа рынка (сборщик: обрезка до глубины xstream — Binance 20, Bitget 15, OKX без обрезки;
  `logic.cmkt.compat_book_row`), полосы ±0.5/1/2 %, стены `logic.cmkt.compat_walls` по корзинам 0.1 % всех трёх бирж;
- CVD — окна 5 и 15 мин ленты рынка (`tape`), тикер — `tk` (марк, funding) и `oi` (OI в монетах сейчас и на T−300),
  ликвидации — `liq` только у `binanceusdm`.

Рынок монеты входит, если его код в снимке `None`, `LAG` или `RESYNC` (у `RESYNC` нет стакана, остальное — как есть); `OFF`, `DOWN`,
`SILENT`, `STALE`, `NOT_LISTED`, `MISMATCH` — рынка у монеты нет (у `xstream` устаревшие данные жили 180 с, здесь — не дольше
кода сводки). Отличия значений от `xstream`: `oi5_pct` — изменение OI в монетах за 300 с (у `xstream` — в USD от самой старой точки
истории не моложе 300 с, до 600 с), `cvd_anchor` — секунда снимка (у `xstream` — последняя сделка), `ex.*.msgs` — монет с данными
рынка (у `xstream` — сообщений). Потребители (`intel.xflow.summary`, `core.flowdoc.xlive_for`) этих полей не читают.
Модуль не импортирует `tb.intel` и `tb.xdata` (Requirement 3.2): путь и формат повторены здесь.
"""
from tb import paths
from tb.cmkt.collector import COLS
from tb.core import filelock
from tb.logic import cmkt as L

SRC = "cmkt"
OK_CODES = (None, "LAG", "RESYNC")


def xflow_path(root=None):
    return paths.runtime("intel", "xflow.json", root=root)


def _row(doc, lrows, sym):
    """Строка монеты рынка (deep из документа, light — из последней публикации light) -> {колонка: значение} или None."""
    if doc is None:
        return None
    row, cols = (doc.get("syms") or {}).get(sym), doc.get("cols") or COLS
    if not isinstance(row, list) and lrows:
        _, rows, lcols = lrows
        row, cols = rows.get(sym), lcols
    if not isinstance(row, list):
        return None
    r = dict(zip(cols, row))
    return None if r.get("st") in ("nl", "err") else r


def _w(t):
    if not isinstance(t, (list, tuple)) or len(t) < 3 or L.num(t[0]) is None or L.num(t[1]) is None:
        return None
    b, s = L.num(t[0]), L.num(t[1])
    return {"net": round(b - s, 0), "buy": round(b, 0), "sell": round(s, 0), "cov": round(L.num(t[2]) or 0.0, 2)}


def _ticker(r):
    """Тикер рынка как `xstream.ticker_row`: {"px", "fund_pct", "oi_usd", "oi5_pct"} или None (нет цены)."""
    tk = r.get("tk") if isinstance(r.get("tk"), list) else []
    px = (L.num(tk[0]) if tk else None) or L.num(r.get("mid"))
    if not px:
        return None
    out = {"px": px}
    fr = L.num(tk[4]) if len(tk) > 4 else None
    if fr is not None:
        out["fund_pct"] = round(fr * 100.0, 5)
    oi = r.get("oi") if isinstance(r.get("oi"), list) else []
    now = L.num(oi[0]) if oi else None
    if now is not None:
        out["oi_usd"] = round(now * px, 0)
        ref = L.num(oi[2]) if len(oi) > 2 else None
        if ref:
            out["oi5_pct"] = round((now / ref - 1.0) * 100.0, 3)
    return out


def _ex_state(eid, m, doc, code, n):
    nosup = [] if eid in L.COMPAT_LIQ else ["liq"]                 # как у xstream: ликвидации только у liq_exchanges
    if m is None or not m.enabled:
        return {"ok": None, "fails": 0, "err": "OFF", "msgs": 0, "nosup": nosup, "down": []}
    ok = doc is not None and code is None
    down = [] if ok else sorted(k for k in L.COMPAT_KINDS if k not in nosup)
    return {"ok": ok if doc is not None else None, "fails": 0 if ok else 1, "err": None if ok else (code or "DOWN"), "msgs": n,
            "nosup": nosup, "down": down}


def document(ts, snap, picked, focus):
    """Документ формата `XStream.document` по такту сводки. snap — снимок (коды рынков монет), picked — [(рынок, документ, light)]
    такта, focus — focus.json (`compat`)."""
    by = dict((m.mkt, (m, d, lr)) for m, d, lr in picked or ())
    syms = [s for s in ((focus or {}).get("compat") if isinstance(focus, dict) else None) or L.COMPAT_CORE if isinstance(s, str)]
    syms = syms[:L.COMPAT_N]
    ssyms = (snap or {}).get("syms") or {}
    out = {"ts_epoch": float(ts), "src": SRC, "req_min": 0, "ex": {}, "syms": {}}
    n_by = dict((e, 0) for _, e, _ in L.COMPAT_MARKETS)
    for sym in syms:
        srow = ssyms.get(sym)
        mkr = srow[3] if isinstance(srow, list) and len(srow) > 3 and isinstance(srow[3], dict) else {}
        rows = []
        for mkt, eid, _ in L.COMPAT_MARKETS:
            m, doc, lr = by.get(mkt, (None, None, None))
            if m is None or not m.enabled or doc is None:
                continue
            code = (mkr.get(mkt) or ["STALE"])[0]
            if code not in OK_CODES:
                continue
            r = _row(doc, lr, sym)
            if r is None:
                continue
            rows.append((eid, r, ((doc.get("cx") or {}).get(sym)) if code != "RESYNC" else None))
        row = {}
        cvd = {}
        for eid, r, _ in rows:
            tape = r.get("tape") if isinstance(r.get("tape"), list) else []
            w5, w15 = (_w(tape[1]) if len(tape) > 1 else None), (_w(tape[2]) if len(tape) > 2 else None)
            if w5 is not None and w15 is not None:
                cvd[eid] = {"w5": w5, "w15": w15}
        if cvd:
            row["cvd"] = cvd
            row["cvd_anchor"] = float(ts)
        books = {}
        for eid, _, cx in rows:
            b = L.compat_book_unpack(cx) if cx is not None else None
            if b is not None:
                books[eid] = b
        if books:
            row["ob"] = dict((e, {"mid": b["mid"], "depth_pct": b["depth_pct"], "bands": b["bands"]}) for e, b in books.items())
            mid, agg = L.compat_merge(books)
            row["walls"] = L.compat_walls(agg, mid, L.COMPAT_WALL_MULT, L.COMPAT_MIN_WALL_USD, L.COMPAT_BUCKET_PCT)
        live, slow, ticks = {}, {}, {}
        for eid, r, _ in rows:
            t = _ticker(r)
            if t is not None:
                ticks[eid] = t
                live[eid] = dict(t)
        for eid, r, _ in rows:
            lq = r.get("liq")
            if eid not in L.COMPAT_LIQ or not isinstance(lq, list) or len(lq) < 4:
                continue
            l5, s5, l1, s1 = (L.num(x) or 0.0 for x in lq[:4])
            slow[eid] = {"liq_long_usd": round(l1, 0), "liq_short_usd": round(s1, 0)}
            live.setdefault(eid, {}).update({"liq5_long_usd": round(l5, 0), "liq5_short_usd": round(s5, 0)})
        for eid, t in ticks.items():                       # как у xstream: строка slow есть у каждой биржи с тикером (может быть пустой)
            sl = slow.setdefault(eid, {})
            if t.get("fund_pct") is not None:
                sl["fundh_pct"] = t["fund_pct"]
            if t.get("oi_usd") is not None:
                sl["oi_usd"] = t["oi_usd"]
        if slow:
            row["slow"] = slow
        if live:
            row["live"] = live
        for eid, _, _ in rows:
            n_by[eid] += 1
        if row:
            out["syms"][sym] = row
    sex = (snap or {}).get("ex") or {}
    for mkt, eid, _ in L.COMPAT_MARKETS:
        m, doc, _ = by.get(mkt, (None, None, None))
        code = (sex.get(mkt) or [None])[0] if doc is not None else "DOWN"
        out["ex"][eid] = _ex_state(eid, m, doc, code, n_by[eid])
    return out


class Writer(object):
    """Точка подключения `Aggregator.compat`: fn(ts, ctx) -> документ (записан) или None (рано, раз в COMPAT_PUBLISH_S)."""

    def __init__(self, root=None, publish_s=L.COMPAT_PUBLISH_S):
        self.root = root
        self.publish_s = float(publish_s)
        self.last_ts = None
        self.writes = self.errors = 0

    def __call__(self, ts, ctx):
        if self.last_ts is not None and 0 <= ts - self.last_ts < self.publish_s:
            return None
        doc = document(ts, ctx.get("snap"), ctx.get("picked"), ctx.get("focus"))
        try:
            filelock.atomic_write_json(xflow_path(self.root), doc, indent=None)
        except (OSError, ValueError, TypeError):
            self.errors += 1
            raise
        self.last_ts = ts
        self.writes += 1
        return doc
