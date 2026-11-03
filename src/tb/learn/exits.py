"""Блок «Выходы» страницы «Учится ли мозг» (R0.2, задача 6). Чистый.

По сделкам мозга с известным путём (`mfe_r`): средний MFE и доля забранного (среднее r / mfe_r по выигрышным сделкам с mfe_r > 0,
как expstats.mfe_capture). Окна 50, 200 и весь период берутся по последним W сделкам мозга; ряды идут по сделкам с известным путём
от 30-й такой сделки.
"""
from tb.core import expstats as X
from tb.learn import stats as S

ID = "exits"
TITLE = "Выходы"


def _get(ctx, name, default=None):
    if isinstance(ctx, dict):
        return ctx.get(name, default)
    return getattr(ctx, name, default)


def _mfe(t):
    return S._fin(t.get("mfe_r"))


def _has_path(t):
    return _mfe(t) is not None


def ratios(trades):
    """Отношения r / mfe_r по выигрышным (pnl > 0) сделкам с mfe_r > 0 — тот же список, что у expstats."""
    out = []
    for t in trades:
        m = _mfe(t)
        if t.get("pnl") is not None and t["pnl"] > 0 and t.get("r") is not None and m is not None and m > 0:
            out.append(t["r"] / m)
    return out


def cell(seg):
    """Оценки по срезу сделок -> {n, n_path, path_share, mfe, capture, small}."""
    path = [t for t in seg if _has_path(t)]
    cap_est = S.mean_est(ratios(path))
    v = S.window_row(path)["mfe_capture"] if path else None       # точка ровно из group_stats
    cap_est["v"] = v
    if v is None:
        cap_est["lo"] = cap_est["hi"] = None
    return {"n": len(seg), "n_path": len(path), "path_share": (len(path) / len(seg)) if seg else None,
            "mfe": S.mean_est([_mfe(t) for t in path]), "capture": cap_est, "small": len(path) < X.MIN_N}


def _series(path, w, cfg):
    pts = int((cfg or {}).get("series_points", 120))
    out = []
    for p in S.rolling(path, w, lambda seg: seg, min_points=X.MIN_N, max_points=pts):
        seg = p["v"]
        c = cell(seg)
        out.append({"i": p["i"], "ts": seg[-1].get("t_close") or None, "n": len(seg), "capture": c["capture"], "mfe": c["mfe"]})
    return out


def build(ctx):
    cfg = _get(ctx, "cfg") or {}
    trades = list(_get(ctx, "trades") or [])
    path = [t for t in trades if _has_path(t)]
    blk = {"id": ID, "title": TITLE, "status": "ok", "note": "", "reason": None, "windows": {}, "all": cell(trades),
           "series": {}, "warnings": []}
    for w in (int(x) for x in cfg.get("windows", [50, 200])):
        blk["windows"][str(w)] = cell(trades[-w:] if trades else [])
        blk["series"][str(w)] = _series(path, w, cfg)
    if any(r > 1.0 for r in ratios(path)):
        blk["warnings"].append("capture_gt_1")
    if not path:
        blk["status"], blk["reason"] = "no_data", "no_path"
        blk["note"] = "У закрытых сделок нет данных о пути цены (MFE)."
    elif len(path) < X.MIN_N:
        blk["status"] = "few_data"
        blk["note"] = "Сделок с известным путём %d из %d: значения показаны, но выводов делать рано." % (len(path), X.MIN_N)
    else:
        blk["note"] = "Доля забранного: средняя доля максимального хода, которую выход оставил в прибыли (по выигрышным сделкам)."
    return blk
