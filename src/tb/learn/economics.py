"""Блок «Экономика» страницы «Учится ли мозг» (R0.2, задача 8): токены и USD на модель против результата в R по неделям ISO (UTC).

Расход берётся из журнала вызовов tb.llm.usage (вся платформа, не делится по режимам), результат (n_trades, r_sum) - из сделок мозга режима.
Поля токенов названы tok_in/tok_out: model.scrub вырезает поля со словами key/token. В ответ не попадают PnL в USDT, баланс, риск сделки и капитал:
допустимы только llm_usd (расход на модель) и отношения к R. Отношения сумм (tok_per_trade, usd_per_trade, usd_per_r) - расчётные величины без интервала.
Чистое ядро `compute`; `build(ctx)` собирает входы (сделки и настройки из ctx, журнал вызовов - из ctx.usage либо load.usage_rows).
"""
import math

from tb.learn import load, stats

DERIVED = ("tok_per_trade", "usd_per_trade", "usd_per_r")
HALF = 4                  # сравнение последних 4 недель с предыдущими 4
SIGNAL_MIN_TRADES = 30    # сделок в каждой половине для сигнала
SIGNAL_REL = 0.2          # относительное изменение usd_per_r, меньшее которого считается flat (интервала у отношения нет)
NOTE = "Расход модели считается по всей платформе (не делится по режимам), результат - сделки мозга выбранного режима."


def _g(ctx, name, default=None):
    if isinstance(ctx, dict):
        return ctx.get(name, default)
    return getattr(ctx, name, default)


def _num(v):
    v = stats._fin(v)
    return v if v is not None and v >= 0 else 0.0


def _ratio(num, den):
    return num / den if den > 0 else None


def _week_row(w, rows, trades):
    calls = tok_in = tok_out = 0
    usds, rs = [], []
    for r in rows:
        t = stats._fin(r.get("t"))
        if t is None or not w["start"] <= t < w["end"]:
            continue
        calls += 1
        tok_in += int(_num(r.get("in")))
        tok_out += int(_num(r.get("out")))
        usds.append(_num(r.get("usd")))
    for t in trades:
        tc, r = t.get("t_close"), stats._fin(t.get("r"))
        if r is not None and isinstance(tc, (int, float)) and not isinstance(tc, bool) and w["start"] <= tc < w["end"]:
            rs.append(r)
    usd, r_sum, n_tr = math.fsum(usds), math.fsum(rs), len(rs)       # fsum: результат не зависит от порядка строк
    tpt = _ratio(tok_in + tok_out, n_tr)
    upt = _ratio(usd, n_tr)
    upr = _ratio(usd, r_sum)                       # только при r_sum > 0; нулевой расход даёт 0.0 (корректное значение)
    return {"week": w["week"], "partial": bool(w["partial"]), "calls": calls, "tok_in": tok_in, "tok_out": tok_out,
            "llm_usd": round(usd, 6), "n_trades": n_tr, "r_sum": round(r_sum, 4),
            "tok_per_trade": None if tpt is None else int(round(tpt)),
            "usd_per_trade": None if upt is None else round(upt, 6),
            "usd_per_r": None if upr is None else round(upr, 6)}


def _half(rows):
    usd = sum(w["llm_usd"] for w in rows)
    r = sum(w["r_sum"] for w in rows)
    return usd, r, sum(w["n_trades"] for w in rows)


def signal(weeks):
    """usd_per_r последних 4 недель против предыдущих 4: falling | rising | flat | None (нет двух половин, r_sum <= 0 или сделок < 30)."""
    if len(weeks) < 2 * HALF:
        return None
    (u1, r1, n1), (u0, r0, n0) = _half(weeks[-HALF:]), _half(weeks[-2 * HALF:-HALF])
    if r1 <= 0 or r0 <= 0 or n1 < SIGNAL_MIN_TRADES or n0 < SIGNAL_MIN_TRADES:
        return None
    a, b = u1 / r1, u0 / r0
    if b == 0:
        return "flat" if a == 0 else "rising"
    rel = (a - b) / b
    return "falling" if rel < -SIGNAL_REL else "rising" if rel > SIGNAL_REL else "flat"


def compute(trades, rows, usage_meta, cfg, now):
    """-> блок economics. trades: сделки мозга (поля r, t_close); rows: строки журнала вызовов; usage_meta: {"truncated": bool}."""
    cfg = cfg or {}
    wr = stats.week_range(now, cfg.get("weeks", 8))
    rows = list(rows or [])
    warnings = ["usage_truncated"] if (usage_meta or {}).get("truncated") else []
    block = {"id": "economics", "title": "Экономика", "scope": "platform", "note": NOTE, "derived": list(DERIVED), "ci": None,
             "weeks": [], "signal": None, "warnings": warnings}
    if not rows:
        block.update(status="no_data", reason="no_usage", note="Журнала вызовов модели нет или он пуст: расход не посчитать.")
        return block
    block["weeks"] = [_week_row(w, rows, trades or []) for w in wr]
    block["signal"] = signal(block["weeks"])
    block["status"] = "ok"
    return block


def build(ctx):
    cfg = _g(ctx, "cfg") or {}
    now = _g(ctx, "now")
    wr = stats.week_range(now, cfg.get("weeks", 8))
    if _g(ctx, "usage") is not None:
        rows, meta = _g(ctx, "usage"), _g(ctx, "usage_meta") or {}
    else:
        rows, meta = load.usage_rows(_g(ctx, "root"), wr[0]["start"] if wr else None)
    return compute(_g(ctx, "trades") or [], rows, meta, cfg, now)
