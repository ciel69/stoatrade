"""Блок «Пропуски» страницы «Учится ли мозг» (R0.2, задача 7, требование 7). Чистая функция: файлов не читает.

ctx (любой объект с атрибутами): now, cfg, trades (сделки мозга с t_close, load.brain_trades),
skips = (случаи, причина) как возвращает load.skip_cases.
Пропуск верный, если whatif.net_dir_pct <= 0 (ровно 0 тоже верный). Упущенный ход в процентах цены после издержек, а не в R [решено: Q8].
"""
from tb.core import expstats as X
from tb.learn import stats

ID, TITLE = "skips", "Пропуски"
HALF = 4
MIN_PART = 30
NOTE = ("Упущенный ход показан в процентах цены после издержек, а не в R: у пропущенного сетапа нет стопа, "
        "и перевод в R был бы выдуманным числом. Рядом число сделок по неделям: рост доли верных при падении сделок означает замораживание.")


def _pct(c):
    return float(c["whatif"]["net_dir_pct"])


def _week_of(wr, epoch):
    for i, w in enumerate(wr):
        if w["start"] <= epoch < w["end"]:
            return i
    return None


def _missed(vals):
    return stats.mean_est([v for v in vals if v > 0])


def signal_for(weeks):
    """4 последние недели против 4 предыдущих по доле верных: improving | worse | flat | None (в любой половине меньше 30 пропусков)."""
    if len(weeks) < 2:
        return None
    recent = weeks[-HALF:]
    prev = weeks[max(0, len(weeks) - 2 * HALF):len(weeks) - len(recent)]
    n1, k1 = sum(w["n"] for w in recent), sum(w["_k"] for w in recent)
    n0, k0 = sum(w["n"] for w in prev), sum(w["_k"] for w in prev)
    if n1 < MIN_PART or n0 < MIN_PART:
        return None
    r = stats.two_prop(k1, n1, k0, n0)
    if r["p"] is not None and r["p"] < X.ALPHA:
        return "improving" if r["p1"] > r["p2"] else "worse"
    return "flat"


def build(ctx):
    sk = getattr(ctx, "skips", None) or ([], None)
    cases, reason = sk
    wr = stats.week_range(ctx.now, ctx.cfg["weeks"])
    buckets = [[] for _ in wr]
    for c in (cases or []):
        try:
            v = _pct(c)
        except (KeyError, TypeError, ValueError):
            continue
        e = stats.iso_epoch(c.get("ts"))
        i = _week_of(wr, e) if e is not None else None
        if i is not None and v == v and abs(v) != float("inf"):
            buckets[i].append(v)
    n_trades = [0] * len(wr)
    for t in (getattr(ctx, "trades", None) or []):
        e = t.get("t_close") if isinstance(t, dict) else None
        i = _week_of(wr, e) if isinstance(e, (int, float)) and e > 0 else None
        if i is not None:
            n_trades[i] += 1
    total = sum(len(b) for b in buckets)
    out = {"id": ID, "title": TITLE, "note": NOTE, "weeks": [], "total": None, "signal": None}
    if not total:
        out.update(status="no_data", reason=reason if reason in ("mode_unknown", "no_whatif") else "no_whatif")
        return out
    out["status"] = "ok" if total >= MIN_PART else "few_data"
    out["reason"] = None if total >= MIN_PART else "few_cases"
    rows = []
    for w, b, nt in zip(wr, buckets, n_trades):
        k = sum(1 for v in b if v <= 0)
        rows.append({"week": w["week"], "partial": w["partial"], "n": len(b), "_k": k, "correct": stats.wilson_est(k, len(b)),
                     "missed_pct": _missed(b), "trades": nt})
    out["signal"] = signal_for(rows)
    for r in rows:
        r.pop("_k")
    out["weeks"] = rows
    allv = [v for b in buckets for v in b]
    wrong = [v for v in allv if v > 0]
    out["total"] = {"n": total, "correct": stats.wilson_est(total - len(wrong), total), "wrong_n": len(wrong),
                    "missed_pct_sum": sum(wrong), "missed_pct_mean": stats.mean_est(wrong)}
    return out
