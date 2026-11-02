"""Симулятор плана по минутным свечам (R1.3, Requirement 7). Чистая функция, без часов, файлов и сети.

`run(plan, rows_1m, t0_s, costs, manage) -> dict`. Строка свечи - кортеж (open_ts_s, o, h, l, c, v).

plan:   side long|short; entry {kind: market|candle_confirm|candle_confirm_limit|limit, level, ttl_min}; stop; tps[]; fracs[] (доли
        начального размера; не заданы = поровну); max_hold_min; tick.
costs:  fee (taker), maker_fee, slip (доли), stop_slip_bps, spread_bps (синтетические bid/ask = цена -/+ spread/2).
manage: latency_s (по умолчанию 60); through_ticks (по умолчанию 1); min_gap_bps (по умолчанию 8, как stop_moves) и блок
        auto_protect (be_r, trail_activate_r, trail_lock_frac, min_profit_r, stop_slip_bps, enabled) - вложенным ключом
        `auto_protect` либо плоско в самом manage. Без блока автозащиты нет.

Порядок внутри 1м свечи (консервативный, 7.4): подтверждение входа -> вход (рынок по open, лимит по уровню) -> стоп (если свеча задела
стоп, он исполняется раньше тейка; гэп за стопом = по open плюс проскальзывание) -> тейки по порядку (гэп за тейком = по цене тейка) ->
таймаут по close -> автозащита по close (новый стоп действует со СЛЕДУЮЩЕЙ свечи). Свеча подтверждения на стоп не проверяется, проверка
начинается со свечи входа. Заполненный лимит и стоп в одной свече: заполнение, затем стоп.

Результат: filled, entry_ts, entry_px, exit_reason (sl|tp|be|trail|timeout|ttl|no_fill), parts[{ts, px, frac, why}], r_gross (по ценам с
проскальзыванием, без комиссий), r_net = r_gross - fees_r, fees_r, mfe_r и mae_r (обе величины неотрицательны, в R; свеча со стопом в mfe
не входит), hold_min ((ts последней части - entry_ts)/60; части внутри свечи несут её open_ts, таймаут - её конец), truncated.
R-единица = |entry_px - stop|; размер нормирован на 1.

ttl: вход не случился до t0 + ttl_min*60, свечи это покрывали. no_fill: данных до срока не хватило (truncated=true) или не было вообще.
Выход по max_hold_min - соглашение симулятора (исполнитель срок сам позицию не закрывает).
"""
from tb.logic import paper
from tb.logic import rules

DEFAULT_LATENCY_S = 60
DEFAULT_TTL_MIN = 60
DEFAULT_MAX_HOLD_MIN = 240
DEFAULT_SPREAD_BPS = 2.0
DEFAULT_GAP_BPS = 8.0
STEP_TICKS = 2          # трейл двигает стоп не меньше чем на 2 тика (stop_moves.min_step_ticks)
_KINDS = ("market", "candle_confirm", "candle_confirm_limit", "limit")


def _num(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _empty(reason, truncated=False, error=None):
    out = {"filled": False, "entry_ts": None, "entry_px": None, "exit_reason": reason, "parts": [], "r_gross": 0.0, "r_net": 0.0,
           "fees_r": 0.0, "mfe_r": 0.0, "mae_r": 0.0, "hold_min": 0.0, "truncated": bool(truncated)}
    if error:
        out["error"] = error
    return out


def _protect_cfg(manage):
    ap = manage.get("auto_protect")
    if not isinstance(ap, dict):
        ap = manage if any(k in manage for k in ("be_r", "trail_activate_r", "trail_lock_frac")) else None
    if not isinstance(ap, dict) or not ap.get("enabled", True):
        return None
    return ap


def _confirm_5m(rows, t0_s, deadline):
    """-> (конец подтверждающей 5м свечи, close, был_ли_полный_ряд) для каждой полной 5м корзины UTC с началом >= t0 (по порядку)."""
    by = {}
    for r in rows:
        b = int(r[0]) // 300 * 300
        if b >= t0_s:
            by.setdefault(b, []).append(r)
    for b in sorted(by):
        grp = by[b]
        if len(grp) < 5 or b + 300 > deadline:
            continue
        yield b + 300, grp[-1][4]


def run(plan, rows_1m, t0_s, costs, manage):
    manage = manage or {}
    costs = costs or {}
    rows = sorted((tuple(r) for r in (rows_1m or [])), key=lambda r: r[0])
    side = str(plan.get("side") or "")
    if side not in ("long", "short"):
        return _empty("no_fill", error="BAD_SIDE")
    s = 1 if side == "long" else -1
    ent = plan.get("entry") or {}
    kind = ent.get("kind") or "market"
    if kind not in _KINDS:
        return _empty("no_fill", error="BAD_ENTRY")
    level = _num(ent.get("level"), 0.0)
    stop = _num(plan.get("stop"), 0.0)
    tick = _num(plan.get("tick"), 0.0) or 0.01
    through = manage.get("through_ticks", 1)
    ttl_min = _num(ent.get("ttl_min"), DEFAULT_TTL_MIN)
    latency = _num(manage.get("latency_s"), DEFAULT_LATENCY_S)
    deadline = t0_s + ttl_min * 60.0
    fee, mfee = _num(costs.get("fee")), _num(costs.get("maker_fee"), _num(costs.get("fee")))
    slip_bps = _num(costs.get("slip")) * 1e4
    stop_slip = _num(costs.get("stop_slip_bps"), 5.0)
    hs = _num(costs.get("spread_bps"), DEFAULT_SPREAD_BPS) / 2e4
    open_side, close_side = ("buy", "sell") if s == 1 else ("sell", "buy")
    max_hold = _num(plan.get("max_hold_min"), DEFAULT_MAX_HOLD_MIN) * 60.0
    gap = _num(manage.get("min_gap_bps"), DEFAULT_GAP_BPS) / 1e4
    ap = _protect_cfg(manage)
    if ap is not None:
        ap = dict(ap)
        ap.setdefault("stop_slip_bps", stop_slip)

    def covers():
        return bool(rows) and rows[-1][0] + 60 >= deadline

    # ---- вход
    start = t0_s + latency            # с какого времени можно ставить заявку / входить
    if kind in ("candle_confirm", "candle_confirm_limit"):
        conf = None
        for end, close in _confirm_5m(rows, t0_s, deadline):
            if (s == 1 and close > level) or (s == -1 and close < level):
                conf = end
                break
        if conf is None:
            return _empty("ttl") if covers() else _empty("no_fill", truncated=True)
        start = conf
    i0, fill_px, fill_fee, limit_fill = None, None, 0.0, False
    if kind in ("market", "candle_confirm"):
        for i, r in enumerate(rows):
            if r[0] >= start:
                i0 = i
                break
        if i0 is None:
            return _empty("no_fill", truncated=True)
        if rows[i0][0] >= deadline:
            return _empty("ttl")
        px0 = rows[i0][1]
        f = paper.fill_market(open_side, 1.0, px0 * (1 - hs), px0 * (1 + hs), slip_bps, fee)
        fill_px, fill_fee = f["px"], f["fee"]
    else:
        ob = "buy" if s == 1 else "sell"
        for i, r in enumerate(rows):
            if r[0] < start:
                continue
            if r[0] >= deadline:
                break
            if paper.limit_touch(ob, level, r[3] if s == 1 else r[2], tick, through):
                i0, fill_px, fill_fee, limit_fill = i, level, level * mfee, True
                break
        if i0 is None:
            return _empty("ttl") if covers() else _empty("no_fill", truncated=True)
    if not (stop > 0) or s * (fill_px - stop) <= 0:
        return _empty("no_fill", error="BAD_STOP")

    # ---- позиция
    ru = abs(fill_px - stop)
    entry_ts = rows[i0][0]
    entry_rate = fill_fee / fill_px
    tps = [_num(x) for x in (plan.get("tps") or [])]
    fr = list(plan.get("fracs") or [])
    if tps and len(fr) != len(tps):
        fr = [1.0 / len(tps)] * len(tps)
    fr = [_num(x) for x in fr]
    cur_stop, stop_why = stop, "sl"
    be_done = False
    next_tp, remaining = 0, 1.0
    parts = []
    exit_fees = 0.0
    best = fill_px            # лучшая цена (для трейла и mfe), по экстремумам свечей
    worst = fill_px
    reason, truncated = None, False
    pos = {"qty": 1.0, "avg": fill_px, "r_unit": ru, "initial_stop": stop, "entries": [{"qty": 1.0, "px": fill_px, "fee": fill_fee}]}
    limits = {"auto_protect": ap} if ap is not None else {}

    def add_part(ts, px, frac, why, rate):
        nonlocal remaining, exit_fees
        parts.append({"ts": ts, "px": px, "frac": frac, "why": why})
        remaining -= frac
        exit_fees += px * frac * rate

    for j in range(i0, len(rows)):
        ts, o, h, l, c = rows[j][0], rows[j][1], rows[j][2], rows[j][3], rows[j][4]
        end = ts + 60
        last = l if s == 1 else h
        ref = min(cur_stop, o) if s == 1 else max(cur_stop, o)     # гэп: цена отсчёта стопа = open, иначе уровень стопа
        hit = paper.stop_trigger(side, cur_stop, last, ref * (1 - hs), ref * (1 + hs), stop_slip)
        if hit is not None:
            worst = min(worst, l) if s == 1 else max(worst, h)
            add_part(ts, hit["px"], remaining, stop_why, fee)
            reason = stop_why
            break
        # тейки по порядку
        while next_tp < len(tps) and remaining > 1e-9:
            tp = tps[next_tp]
            if not paper.limit_touch(close_side, tp, h if s == 1 else l, tick, through):
                break
            frac = min(fr[next_tp], remaining)
            add_part(ts, tp, frac, "tp%d" % (next_tp + 1), mfee)
            next_tp += 1
        hi = c if (limit_fill and j == i0) else (h if s == 1 else l)
        best = max(best, hi) if s == 1 else min(best, hi)
        worst = min(worst, l) if s == 1 else max(worst, h)
        if remaining <= 1e-9:
            reason = "tp"
            break
        if end >= entry_ts + max_hold:
            f = paper.fill_market(close_side, 1.0, c * (1 - hs), c * (1 + hs), slip_bps, fee)
            add_part(end, f["px"], remaining, "timeout", fee)
            reason = "timeout"
            break
        # автозащита по close: действует со следующей свечи
        if ap is not None:
            pnl_r = s * (c - fill_px) / ru
            be = _num(ap.get("be_r"), 0.0)
            if not be_done and be > 0 and pnl_r >= be:
                to = fill_px * (1 + s * (2.0 * entry_rate * 1e4 + rules.BE_EXTRA_BPS) / 1e4)
                fl = rules.profit_floor({"pos": pos}, limits, s)
                if fl is not None:
                    to = max(to, fl) if s == 1 else min(to, fl)
                if s * (c - to) >= c * gap:
                    be_done = True
                    if s * (to - cur_stop) > 0:
                        cur_stop, stop_why = to, "be"
            act, frac_l = _num(ap.get("trail_activate_r"), 0.0), _num(ap.get("trail_lock_frac"), 0.0)
            if act > 0 and 0 < frac_l < 1 and pnl_r >= act:
                mfe = s * (best - fill_px) / ru
                if mfe >= act:
                    to = fill_px + s * ru * mfe * frac_l
                    fl = rules.profit_floor({"pos": pos}, limits, s)
                    if fl is None or s * (to - fl) >= 0:
                        to = min(to, c * (1 - gap)) if s == 1 else max(to, c * (1 + gap))
                        if s * (to - cur_stop) > 0 and s * (to - cur_stop) >= STEP_TICKS * tick - 1e-12:
                            cur_stop, stop_why = to, "trail"
    else:
        # свечи кончились раньше срока: выход по close последней
        c = rows[-1][4]
        f = paper.fill_market(close_side, 1.0, c * (1 - hs), c * (1 + hs), slip_bps, fee)
        add_part(rows[-1][0] + 60, f["px"], remaining, "timeout", fee)
        reason, truncated = "timeout", True

    r_gross = sum(p["frac"] * s * (p["px"] - fill_px) / ru for p in parts)
    fees_r = (fill_fee + exit_fees) / ru
    mfe = max(0.0, s * (best - fill_px) / ru)
    mae = max(0.0, -s * (worst - fill_px) / ru)
    return {"filled": True, "entry_ts": entry_ts, "entry_px": fill_px, "exit_reason": reason, "parts": parts, "r_gross": r_gross,
            "r_net": r_gross - fees_r, "fees_r": fees_r, "mfe_r": mfe, "mae_r": mae, "hold_min": (parts[-1]["ts"] - entry_ts) / 60.0,
            "truncated": truncated}
