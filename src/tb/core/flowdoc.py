"""Ордерфлоу монеты для досье мозга и дашборда (чтение): runtime/flow.json пишет только marketdata/flowwatch.py (поток сделок и стакана Bybit). Здесь лишь компактная выжимка по одной
монете, чтобы слои brain/gateway/dash не импортировали marketdata. Только числа, enum и идентификаторы; сети нет."""
from tb import paths
from tb.core import ideas

MAX_AGE_S = 30.0
XFLOW_MAX_AGE_S = 30.0


def _f(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def xlive_for(root, sym, now, max_age_s=XFLOW_MAX_AGE_S):
    """Кросс-биржевой поток (runtime/intel/xflow.json, пишет tb.xdata.xstream или intel/xflow): {"liq5_long_usd","liq5_short_usd","liq1h_long_usd","liq1h_short_usd",
    "walls":[...],"oi5_pct","fund_pct","n_ex"} по монете (суммы и средние по биржам; стены кросс-биржевые) или None (нет файла, устарел, монеты нет)."""
    doc = ideas.read_json(paths.runtime("intel", "xflow.json", root=root))
    ts = _f((doc or {}).get("ts_epoch")) if isinstance(doc, dict) else None
    if ts is None or now - ts > max_age_s:
        return None
    r = (doc.get("syms") or {}).get(sym)
    if not isinstance(r, dict):
        return None
    out = {}
    live = r.get("live") if isinstance(r.get("live"), dict) else {}
    rows = [v for v in live.values() if isinstance(v, dict)]
    if rows:
        for k in ("liq5_long_usd", "liq5_short_usd"):
            vals = [_f(v.get(k)) for v in rows if _f(v.get(k)) is not None]
            if vals:
                out[k] = round(sum(vals), 0)
        for k, nd in (("oi5_pct", 3), ("fund_pct", 5)):
            vals = [_f(v.get(k)) for v in rows if _f(v.get(k)) is not None]
            if vals:
                out[k] = round(sum(vals) / len(vals), nd)
        out["n_ex"] = len(rows)
    slow = r.get("slow") if isinstance(r.get("slow"), dict) else {}
    lq = [v for v in slow.values() if isinstance(v, dict) and _f(v.get("liq_long_usd")) is not None]
    if lq:
        out["liq1h_long_usd"] = round(sum(_f(v["liq_long_usd"]) for v in lq), 0)
        out["liq1h_short_usd"] = round(sum(_f(v.get("liq_short_usd")) or 0.0 for v in lq), 0)
    ws = [w for w in r.get("walls") or [] if isinstance(w, dict) and w.get("side") in ("bid", "ask")]
    if ws:
        out["walls"] = ws[:6]
    return out or None


def deriv_for(root, sym, now, max_age_s=900.0):
    """Деривативы монеты из runtime/intel/facts.json (пишет сервис intel): {"oi24_pct","ls","fund_pct", ...} или None. Исполнитель и сторож позиции читают только этот файл."""
    f = ideas.read_json(paths.runtime("intel", "facts.json", root=root))
    if not isinstance(f, dict) or f.get("schema") != "tb.facts/1":
        return None
    ts = _f(f.get("ts"))
    if ts is None or now - ts > max_age_s:
        return None
    r = ((f.get("deriv") or {}).get(sym)) if isinstance(f.get("deriv"), dict) else None
    if not isinstance(r, dict):
        return None
    out = {"oi24_pct": _f(r.get("oi_chg_24h_pct")), "ls": _f(r.get("ls_ratio")), "fund_pct": _f(r.get("funding_pct"))}
    return {k: v for k, v in out.items() if v is not None} or None


def flow_for(root, sym, now, max_age_s=MAX_AGE_S):
    """-> {cvd_5m_pct, cvd_1m_usd, big_5m_usd, imb_pct, spread_bps, walls[<=2], xex_bps, warm_s, age_s} по монете или None (нет файла, устарел, монеты нет в потоке).
    cvd_5m_pct: перекос агрессии (покупки минус продажи) к объёму за 5 минут; imb_pct: перекос заявок bid/ask у цены; walls: крупные устойчивые стены; warm_s: сколько секунд ленты накоплено
    (меньше 120: CVD неполный)."""
    doc = ideas.read_json(paths.runtime("flow.json", root=root))
    if not isinstance(doc, dict) or not isinstance(doc.get("syms"), list):
        return None
    ts = doc.get("ts_epoch")
    if not isinstance(ts, (int, float)) or now - ts > max_age_s:
        return None
    row = next((r for r in doc["syms"] if isinstance(r, dict) and r.get("sym") == sym), None)
    if row is None:
        return None
    walls = [{"side": w.get("side"), "usd": w.get("usd"), "dist_pct": w.get("dist_pct")} for w in (row.get("walls") or []) if isinstance(w, dict) and w.get("stable") and w.get("side") in ("bid", "ask")][:2]
    out = {"cvd_5m_pct": row.get("cvd_5m_pct"), "cvd_1m_usd": row.get("cvd_1m_usd"), "big_5m_usd": row.get("big_5m_usd"), "imb_pct": row.get("imb_pct"),
           "spread_bps": row.get("spread_bps"), "walls": walls or None, "xex_bps": row.get("xex_bps"), "warm_s": row.get("warm_s"), "age_s": int(max(0.0, now - ts))}
    return {k: v for k, v in out.items() if v is not None}
