"""Кандидаты на вход для entry_scout (блок досье `cands`): код заранее считает уровень, стоп, цели, расстояния, RR после издержек и горизонт,
чтобы модель выбирала сетап, а не считала. Чистый модуль: без файлов и сети, формулы те же, что у validate._entry (кандидат ok=true проходит его числовые проверки).

Пул уровней монеты события: struct.s[sym] res/sup, day/pd/wk, levels[sym] swing_5m/swing_1h и day/prev_day/week. Близкие уровни (ближе CLUSTER_PCT) сливаются.
Кандидат long: уровень в пределах допустимой дистанции от цены, стоп за ближайшим уровнем ниже (не ближе пола стопа validate), цели = следующие уровни выше цены;
short зеркально. Без уровня для стопа или цели кандидата нет (стоп и цели не выдумываются).
"""
from tb.brain import validate as V

CLUSTER_PCT = 0.15          # уровни ближе этого (% цены) считаются одним
STOP_BUF_FRAC = 0.1         # стоп за уровнем на 0.1 ATR(5м) (не вплотную к уровню)
TP1_MAX_R = 1.5             # первая цель не дальше 1.5R: ближайший уровень часто дальше хода цены (разбор 2026-11-01: ходы 1.4-2.1R возвращались в безубыток)
SCALP_MIN_TP_COSTS = 3.0     # scalp только если первая цель дальше трёх издержек круга: иначе комиссии съедают сделку (BTC 2026-10-31: -0.655 USDT, из них ~0.56 комиссии)
CHASE_ZONE_PCT = 0.3        # momentum_chase: зона входа ±0.3% у текущей цены (вход по рынку сейчас)
CHASE_STOP_ATR = 0.25       # стоп за началом импульсной свечи: её диапазон плюс четверть ATR(5м)
CHASE_TP_R = 2.5            # нет уровня по ходу движения не ближе 1R -> цель 2.5R
MAX_OK = 3
MAX_FAIL = 2


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _px(x):
    return float("%.6g" % x)


def pool(brief, sym):
    """-> отсортированный список цен уровней монеты (без дублей ближе CLUSTER_PCT)."""
    raw = []
    st = (((brief.get("struct") or {}).get("s") or {}).get(sym)) if isinstance(brief.get("struct"), dict) else None
    if isinstance(st, dict):
        for k in ("res", "sup"):
            raw += [r[0] for r in (st.get(k) or []) if isinstance(r, list) and r]
        for k in ("day", "wk"):
            raw += list(st.get(k) or [])[:2]
        raw += list(st.get("pd") or [])[:2]
    lv = (brief.get("levels") or {}).get(sym) if isinstance(brief.get("levels"), dict) else None
    if isinstance(lv, dict):
        raw += list(lv.get("swing_5m") or []) + list(lv.get("swing_1h") or [])
        for k in ("day", "prev_day", "week"):
            v = lv.get(k)
            if isinstance(v, dict):
                raw += [v.get("hi"), v.get("lo")]
    out = []
    for p in sorted(x for x in (_num(v) for v in raw) if x and x > 0):
        if not out or (p - out[-1]) / out[-1] * 100.0 > CLUSTER_PCT:
            out.append(p)
    return out


def _horizon(ctx, stop_pct, rr, wide_pct=None, tp1_pct=None):
    """-> (горизонт, rr_net, причина отказа или None): самый короткий горизонт, где стоп не шире max_stop_pct и RR после издержек не ниже min_rr.
    stop_pct: от уровня (для RR, как validate); wide_pct: как мерит шлюз исполнителя (validate.exec_stop_pct), для max_stop_pct."""
    wide_pct = stop_pct if wide_pct is None else wide_pct
    cost = ctx.get("cost_rt_pct")
    hz = ctx.get("horizons")
    if hz:
        order = [h for h in V.SESSION_ORDER if h in hz]
        mh = ctx.get("max_horizon")
        if mh in V.SESSION_ORDER and not ctx.get("off_plan_ok"):
            order = [h for h in order if V.SESSION_ORDER.index(h) <= V.SESSION_ORDER.index(mh)]
        geos = [(h, hz[h].get("max_stop_pct"), hz[h].get("min_rr")) for h in order]
    else:
        geos = [(ctx.get("session_type"), ctx.get("max_stop_pct"), ctx.get("min_rr"))]
    net = rr if cost is None else (rr * stop_pct - cost) / (stop_pct + cost)
    near = None                                        # горизонт, где стоп помещается и RR ближе всего к порогу (для отказа с числом)
    for h, ms, mr in geos:
        if h == "scalp" and cost is not None and tp1_pct is not None and tp1_pct < SCALP_MIN_TP_COSTS * cost:
            continue                                   # цель ближе трёх издержек: не scalp (переходит в intraday со сроком 4 ч)
        ms = V.ENTRY_MAX_STOP_PCT if ms is None else ms
        need = mr if (mr is not None and cost is not None) else V.ENTRY_MIN_RR
        val = net if (mr is not None and cost is not None) else rr
        if wide_pct > ms + 1e-9:
            continue
        if val >= need - 1e-9:
            return h, round(val, 2), None
        if near is None or val - need > near[2]:
            near = (h, val, val - need, need)
    if near is None:
        return (geos[-1][0] if geos else None), round(net, 2), "stop_wide"
    return near[0], round(near[1], 2), "rr_low:need_%.2f" % near[3]


def candidates(brief, sym, ctx=None):
    """-> [кандидат] (ok сначала, по убыванию rr_net) или None. ctx: validate.entry_context(brief, {"sym": sym}) (считается сам, если не передан)."""
    if not isinstance(brief, dict) or not sym:
        return None
    ctx = ctx or V.entry_context(brief, {"sym": sym})
    px = ctx.get("px")
    lv = pool(brief, sym)
    ch = chase(ctx, lv) if px else None
    if not px or len(lv) < 2:
        if ch:
            ch["n"] = 1
        return [ch] if ch else None
    a5 = ctx.get("a5_pct") or 0.0
    floor = max(V.ENTRY_MIN_STOP_PCT, V.ENTRY_STOP_ATR_MULT * a5, 3.0 * (ctx.get("spread_bps") or 0.0) / 100.0)
    max_dist = V.ENTRY_MAX_LEVEL_DIST_LOOSE_PCT if V._loose(ctx) else V.ENTRY_MAX_LEVEL_DIST_PCT
    buf = STOP_BUF_FRAC * a5 / 100.0
    dsm = ctx.get("day_size_mult")
    size_mult = round(min(dsm, V.ENTRY_SIZE_MULT_MAX), 2) if dsm is not None and dsm < 1.0 else None      # план дня ограничил размер: validate требует size_mult не выше
    ok, bad = [], []
    for side, sign in (("long", 1), ("short", -1)):
        for L in lv:
            dist = abs(L - px) / px * 100.0
            if dist > max_dist:
                continue
            stops = sorted((s for s in lv if (L - s) * sign > 0 and abs(L - s) / L * 100.0 >= floor), key=lambda s: abs(L - s))      # ближайший уровень с другой стороны за полом стопа
            if not stops:
                continue
            stop = _px(stops[0] * (1 - sign * buf))
            if (px - stop) * sign <= 0:
                continue                                   # цена уже за стопом (validate: STOP_WRONG_SIDE)
            ref = max(L, px) if sign > 0 else min(L, px)
            tps = sorted((t for t in lv if (t - ref) * sign > 0), key=lambda t: abs(t - ref))[:2]      # цели за худшей ценой входа: validate не даст TP_REACHED
            if not tps:
                continue
            stop_pct = abs(L - stop) / L * 100.0
            wide = V.exec_stop_pct(L, px, stop)
            cap = ref + sign * TP1_MAX_R * abs(ref - stop)
            variants = ([[_px(cap), tps[0]]] if (tps[0] - cap) * sign > 0 else []) + [tps]      # сначала первая цель на 1.5R (вторая: уровень), если она проходит порог RR горизонта
            pick = None
            for v in variants:
                rr = V._entry_rr(side, L, px, stop, v)
                if rr is None or rr <= 0:
                    continue
                res = _horizon(ctx, stop_pct, rr, wide, abs(v[0] - ref) / ref * 100.0)
                if pick is None or not res[2]:
                    pick = (v,) + res
                if not res[2]:
                    break
            if pick is None:
                continue
            tps, h, net, why = pick
            row = {"side": side, "level": _px(L), "stop": stop, "tp": [_px(t) for t in tps], "dist_pct": round(dist, 2), "stop_pct": round(wide, 2),
                   "rr_net": net, "horizon": h}
            if size_mult is not None:
                row["size_mult"] = size_mult
            if h == "scalp":
                row["entry_type"] = "limit"                # maker у уровня после свечи: круг ~0.07% вместо 0.24%
            if ctx.get("range_5m_pct") is not None and ctx["range_5m_pct"] > stop_pct and not V._loose(ctx):
                why = why or "range_gt_stop"
            if why:
                row["fail"] = why
                bad.append(row)
            else:
                row["ok"] = True
                ok.append(row)
    ok.sort(key=lambda r: -r["rr_net"])
    bad.sort(key=lambda r: -r["rr_net"])
    out = ([ch] if ch else []) + ok[:MAX_OK] + bad[:MAX_FAIL]      # импульс первым: он живёт минуты
    for i, r in enumerate(out):
        r["n"] = i + 1                                                # номер для es_take / es_watch (модели не нужно считать позиции)
    return out or None


def chase(ctx, lv):
    """momentum_chase (уровни риска 4-5): вход по рынку ПО импульсу события, без подтверждения свечой. -> кандидат или None.
    Стоп за началом импульсной свечи (диапазон 5м + CHASE_STOP_ATR ATR), цель: ближайший уровень по ходу не ближе 1R, иначе CHASE_TP_R R;
    размер не выше min(0.5, no_confirm.size_mult_max, размер дня); горизонт по ширине стопа."""
    if (ctx.get("risk_level") or 0) < V.CHASE_MIN_LEVEL or ctx.get("no_confirm_allowed") is False or (ctx.get("stop_streak") or 0) >= 2:
        return None
    px, ret5 = ctx.get("px"), ctx.get("ret_5m")
    if not px or not ret5:
        return None
    sign = 1 if ret5 > 0 else -1
    if not V.chase_impulse(ctx, sign):
        return None
    side = "long" if sign > 0 else "short"
    a5 = ctx.get("a5_pct") or 0.0
    r5 = ctx.get("range_5m_pct") or V.CHASE_RANGE_ATR * a5
    floor = max(V.ENTRY_MIN_STOP_PCT, V.ENTRY_STOP_ATR_MULT * a5, 3.0 * (ctx.get("spread_bps") or 0.0) / 100.0)
    stop_pct = max(r5 + CHASE_STOP_ATR * a5, floor)
    stop = _px(px * (1 - sign * stop_pct / 100.0))
    risk = abs(px - stop)
    tps = [t for t in sorted((t for t in lv if (t - px) * sign >= risk), key=lambda t: abs(t - px))][:2] or [_px(px + sign * CHASE_TP_R * risk)]
    rr = V._entry_rr(side, px, px, stop, tps)
    if not rr or rr <= 0:
        return None
    h, net, why = _horizon(ctx, stop_pct, rr, stop_pct, abs(tps[0] - px) / px * 100.0)
    caps = [V.ENTRY_SIZE_MULT_MAX] + [x for x in (ctx.get("no_confirm_max"), ctx.get("day_size_mult")) if x]
    z = CHASE_ZONE_PCT / 100.0
    row = {"setup": V.CHASE_SETUP, "side": side, "level": _px(px), "stop": stop, "tp": [_px(t) for t in tps], "dist_pct": 0.0, "stop_pct": round(stop_pct, 2),
           "rr_net": net, "horizon": h, "confirm": "none", "zone": {"low": _px(px * (1 - z)), "high": _px(px * (1 + z))}, "size_mult": round(min(caps), 2),
           "tags": ["no-confirm"], "entry_type": "market"}
    if why:
        row["fail"] = why
    else:
        row["ok"] = True
    return row
