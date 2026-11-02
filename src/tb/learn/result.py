"""Блок «Результат» страницы «Учится ли мозг» (R0.2, задача 5). Чистый: сделки и конфиг приходят готовыми.

Окна W (последние min(W, n) сделок мозга), `all`, скользящие ряды, signal.interval_above_zero.
Значения r, win, pf, fees_share берутся из expstats.group_stats через stats.window_row, поэтому совпадают с `./tb exp stats` на том же срезе.
"""
from tb.core import expstats as X
from tb.learn import stats as S

ID = "result"
TITLE = "Результат"


def _get(ctx, name, default=None):
    if isinstance(ctx, dict):
        return ctx.get(name, default)
    return getattr(ctx, name, default)


def _pf_cfg(cfg):
    p = (cfg or {}).get("pf_bootstrap") or {}
    return p.get("level", 0.9), p.get("b", 500), p.get("seed", 20261102)


def _pf_point(row):
    """Точка PF из group_stats; при pf_inf значения нет."""
    return None if row.get("pf_inf") else row.get("pf")


def window(trades, cfg):
    """Оценки по срезу сделок -> {n, r, win, pf, pf_inf, fees_share, small}. Пустой срез даёт пустые оценки."""
    row = S.window_row(trades)
    rs = [t.get("r") for t in trades]
    level, b, seed = _pf_cfg(cfg)
    pf = S.pf_est([t.get("pnl") for t in trades], level, b, seed)
    pf["v"] = S._fin(_pf_point(row))                         # точка ровно из group_stats
    return {"n": row["n"],
            "r": S.mean_est(rs),
            "win": S.wilson_est(row["wins"], row["n"]) if row["n"] else S.est(None, n=0),
            "pf": {k: pf[k] for k in ("v", "lo", "hi", "n", "lvl")},
            "pf_inf": bool(row["pf_inf"]),
            "fees_share": row["fees_share"],
            "small": bool(row["small"])}


def _series(trades, w, cfg):
    """Ряд окна w: точка {i, ts, n, r (est), pf}; от 30-й сделки, не больше series_points точек."""
    pts = int((cfg or {}).get("series_points", 120))
    out = []
    for p in S.rolling(trades, w, lambda seg: seg, min_points=X.MIN_N, max_points=pts):
        seg = p["v"]
        row = S.window_row(seg)
        out.append({"i": p["i"], "ts": (seg[-1].get("t_close") or None) if seg else None, "n": len(seg),
                    "r": S.mean_est([t.get("r") for t in seg]), "pf": S._fin(_pf_point(row))})
    return out


def build(ctx):
    cfg = _get(ctx, "cfg") or {}
    trades = list(_get(ctx, "trades") or [])
    n = len(trades)
    wins = [int(w) for w in cfg.get("windows", [50, 200])]
    blk = {"id": ID, "title": TITLE, "status": "ok", "note": "", "reason": None,
           "windows": {}, "all": window(trades, cfg), "series": {}, "signal": {}}
    for w in wins:
        seg = trades[-w:] if n else []
        blk["windows"][str(w)] = window(seg, cfg)
        blk["series"][str(w)] = _series(trades, w, cfg) if n >= X.MIN_N else []
        lo = blk["windows"][str(w)]["r"]["lo"]
        blk["signal"][str(w)] = bool(lo is not None and lo > 0)
    if not n:
        blk["status"], blk["reason"] = "no_data", "no_trades"
        blk["note"] = "Закрытых сделок мозга в этом режиме пока нет."
    elif n < X.MIN_N:
        blk["status"] = "few_data"
        blk["note"] = "Сделок %d из %d: значения показаны, но выводов делать рано." % (n, X.MIN_N)
    else:
        blk["note"] = "Средний R после издержек по последним сделкам; интервал 95%, PF 90%."
    return blk
