"""Единая геометрия входа: уровень, стоп, цели, горизонт, тип входа, отказы, momentum_chase (R1.2; перенесено из brain/cands.py, validate.py, entryplan.py).

Чистый модуль: без файлов, сети и импорта tb.brain. Параметры профилей приходят аргументом (config/geometry.json читает вызывающий);
DEFAULT_PROFILES равны файлу и нужны, когда конфиг не передан (тест сверяет их с config/geometry.json).
Формулы и формат строк кандидатов прежние: кандидат ok=true проходит числовые проверки validate._entry.

Пул уровней: близкие уровни (ближе cluster_pct) сливаются. long: уровень в пределах допустимой дистанции от цены, стоп за ближайшим уровнем ниже
(не ближе пола стопа), цели = следующие уровни выше; short зеркально. Без уровня для стопа или цели кандидата нет (стоп и цели не выдумываются).
Для теней: nearest_ok (ближайший к цене ok-кандидат стороны) и atr_plan (профиль atr: стоп от ATR и пола, цель в R, вход по рынку).
"""
GEOMETRY_VER = "g1"
DEFAULT_CLUSTER_PCT = 0.15           # уровни ближе этого (% цены) считаются одним
DEFAULT_PROFILES = {
    "base": {"stop_buf_atr": 0.1,            # стоп за уровнем на 0.1 ATR(5м) (не вплотную к уровню)
             "tp1_min_r": 1.0,               # первая цель не ближе 1R: уровень в 0.2-0.6R отдавал половину позиции за комиссию
             "tp1_max_r": 1.5,               # первая цель не дальше 1.5R: ходы 1.4-2.1R возвращались в безубыток
             "scalp_min_tp_costs": 3.0,      # scalp только если первая цель дальше трёх издержек круга
             "max_ok": 3, "max_fail": 2},
    "chase": {"zone_pct": 0.3,               # зона входа ±0.3% у текущей цены (вход по рынку сейчас)
              "stop_atr": 0.25,              # стоп за началом импульсной свечи: её диапазон плюс четверть ATR(5м)
              "tp_r": 2.5,                   # нет уровня по ходу движения не ближе 1R -> цель 2.5R
              "surge_x": 10.0,               # импульс: оборот 5м от 10x нормы ...
              "range_atr": 4.0},             # ... И свеча от 4 ATR(5м), движение в сторону входа

    "atr": {"k_atr": 1.5, "tp_r": 1.5},      # только тени: стоп max(пол по рынку, k_atr*ATR(5м)), цель tp_r R
}

# ---- константы правил входа (раньше в brain/validate.py; validate импортирует их под прежними именами)
SESSION_ORDER = ("scalp", "intraday", "swing", "position")
MAX_HOLD_MIN = {"scalp": 60, "intraday": 240, "swing": 1440, "position": 4320}
ENTRY_MIN_RR = 2.0                   # риск/прибыль брутто не ниже 2, когда в досье нет порога сессии (по средней цене целей при равных долях)
ENTRY_SIZE_MULT_MAX = 0.5            # потолок size_mult, который вообще может назвать модель (только уменьшение)
ENTRY_MIN_STOP_PCT = 0.1             # стоп не ближе 0.1% к уровню входа (и не ближе ENTRY_STOP_ATR_MULT*ATR(5м) и трёх спредов)
ENTRY_STOP_ATR_MULT = 1.5            # стоп не ближе 1.5 ATR(5м): стоп в одну свечу выбивало шумом за минуты
ENTRY_STOP_COST_MULT = 2.0           # стоп не ближе двух издержек круга: Bybit taker 0.24% -> пол 0.48%, лимитный вход (maker 0.18%) -> пол 0.35%
CHASE_SETUP = "momentum_chase"       # вход по рынку ПО импульсу события (указание владельца 2026-10-31)
CHASE_MIN_LEVEL = 4                  # только уровни риска 4-5
CHASE_SURGE_X = DEFAULT_PROFILES["chase"]["surge_x"]        # значения по умолчанию профиля chase (реэкспорт для validate); рабочие пороги читаются из профиля
CHASE_RANGE_ATR = DEFAULT_PROFILES["chase"]["range_atr"]
ENTRY_MAX_STOP_PCT = 10.0
ENTRY_MAX_LEVEL_DIST_PCT = 3.0       # уровень входа не дальше 3% от цены
ENTRY_MAX_LEVEL_DIST_LOOSE_PCT = 5.0  # на уровнях риска 4-5 (paper-разведка входов): до 5%
LOOSE_RISK_LEVEL = 4                 # с этого уровня риска мягче правила диапазона свечи и дальности уровня


def _px(x):
    return float("%.6g" % x)


def _profiles(profiles):
    """-> (base, chase, atr, cluster_pct). profiles: словарь профилей или весь config/geometry.json (с ключами profiles и cluster_pct); пропуски берутся из DEFAULT_PROFILES."""
    p = profiles if isinstance(profiles, dict) else {}
    cluster = p.get("cluster_pct", DEFAULT_CLUSTER_PCT)
    if isinstance(p.get("profiles"), dict):
        p = p["profiles"]
    out = []
    for k in ("base", "chase", "atr"):
        d = dict(DEFAULT_PROFILES[k])
        if isinstance(p.get(k), dict):
            d.update(p[k])
        out.append(d)
    return out[0], out[1], out[2], cluster


def stop_floor_pct(ctx, entry_type=None):
    """Пол ширины стопа от уровня в %: 0.1%, 1.5 ATR(5м), три спреда и две издержки круга (одна формула для validate и cands).
    entry_type="limit": издержки круга с maker-входом (cost_rt_maker_pct), если досье их даёт."""
    ctx = ctx or {}
    cost = ctx.get("cost_rt_pct") or 0.0
    if entry_type == "limit" and ctx.get("cost_rt_maker_pct"):
        cost = min(cost, ctx["cost_rt_maker_pct"])
    return max(ENTRY_MIN_STOP_PCT, ENTRY_STOP_ATR_MULT * (ctx.get("a5_pct") or 0.0), 3.0 * (ctx.get("spread_bps") or 0.0) / 100.0, ENTRY_STOP_COST_MULT * cost)


def _loose(ctx):
    """Уровень риска 4-5: разведка входов в paper. Диапазон свечи события не закрывает вход (вход ждёт новую свечу у уровня), уровень допустим дальше."""
    return (ctx.get("risk_level") or 0) >= LOOSE_RISK_LEVEL


def chase_impulse(ctx, sign, profile=None):
    """Событие экстремально импульсное и идёт в сторону sign: оборот 5м от surge_x нормы И диапазон свечи от range_atr ATR(5м), ret_5m в ту же сторону.
    Пороги из профиля chase (по умолчанию DEFAULT_PROFILES). Любое изменение порога в config/geometry.json требует нового geometry_ver."""
    profile = profile or DEFAULT_PROFILES["chase"]
    surge, ret5, r5, a5 = ctx.get("surge_x"), ctx.get("ret_5m"), ctx.get("range_5m_pct"), ctx.get("a5_pct")
    strong = surge is not None and surge >= profile["surge_x"] and r5 is not None and bool(a5) and r5 >= profile["range_atr"] * a5
    return bool(strong and ret5 and ret5 * sign > 0)


def exec_stop_pct(level, px, stop, entry_type=None):
    """Ширина стопа так, как её мерит шлюз исполнителя (plan_schema.entry_ref: ref_price = цена при решении, у limit цена заявки), и не меньше, чем от уровня.
    Цена ещё не дошла до уровня -> стоп от неё шире (PENDLE 2026-10-31: 0.89% от цены при 0.8% scalp, шлюз отклонил E_STOP_PCT)."""
    d = abs(level - stop) / level * 100.0
    if entry_type != "limit" and px:
        d = max(d, abs(px - stop) / px * 100.0)
    return d


def _entry_rr(side, level, px, stop, tps):
    """Риск/прибыль по худшей цене входа (long: max(уровень, цена); short: min) и средней цене целей (равные доли) -> число или None."""
    ref = level if px is None else (max(level, px) if side == "long" else min(level, px))
    risk = (ref - stop) if side == "long" else (stop - ref)
    reward = (sum(tps) / len(tps) - ref) if side == "long" else (ref - sum(tps) / len(tps))
    if risk <= 0:
        return None
    return reward / risk


def pool(levels, cluster_pct=DEFAULT_CLUSTER_PCT):
    """-> отсортированный список цен уровней без дублей ближе cluster_pct (% цены). levels: любые числа, мусор и неположительные отбрасываются."""
    out = []
    for p in sorted(float(v) for v in (levels or []) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0):
        if not out or (p - out[-1]) / out[-1] * 100.0 > cluster_pct:
            out.append(p)
    return out


def _horizon(ctx, stop_pct, rr, wide_pct=None, tp1_pct=None, base=None):
    """-> (горизонт, rr_net, причина отказа или None): самый короткий горизонт, где стоп не шире max_stop_pct и RR после издержек не ниже min_rr.
    stop_pct: от уровня (для RR, как validate); wide_pct: как мерит шлюз исполнителя (exec_stop_pct), для max_stop_pct. base: профиль base (scalp_min_tp_costs)."""
    scalp_min = (base or DEFAULT_PROFILES["base"])["scalp_min_tp_costs"]
    wide_pct = stop_pct if wide_pct is None else wide_pct
    cost = ctx.get("cost_rt_pct")
    hz = ctx.get("horizons")
    if hz:
        order = [h for h in SESSION_ORDER if h in hz]
        mh = ctx.get("max_horizon")
        if mh in SESSION_ORDER and not ctx.get("off_plan_ok"):
            order = [h for h in order if SESSION_ORDER.index(h) <= SESSION_ORDER.index(mh)]
        geos = [(h, hz[h].get("max_stop_pct"), hz[h].get("min_rr")) for h in order]
    else:
        geos = [(ctx.get("session_type"), ctx.get("max_stop_pct"), ctx.get("min_rr"))]
    net = rr if cost is None else (rr * stop_pct - cost) / (stop_pct + cost)
    near = None                                        # горизонт, где стоп помещается и RR ближе всего к порогу (для отказа с числом)
    for h, ms, mr in geos:
        if h == "scalp" and cost is not None and tp1_pct is not None and tp1_pct < scalp_min * cost:
            continue                                   # цель ближе трёх издержек: не scalp (переходит в intraday со сроком 4 ч)
        ms = ENTRY_MAX_STOP_PCT if ms is None else ms
        need = mr if (mr is not None and cost is not None) else ENTRY_MIN_RR
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


def _num(x):
    if isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _no_confirm_variants(ctx, ok_rows, profile):
    """Варианты входа без подтверждающей свечи для риска 4-5: тот же уровень/стоп/цели,
    но вход в зоне вокруг уровня сразу, с уменьшенным size_mult и тегом no-confirm.
    Это осознанно более рискованный вход (без ожидания 5м закрытия); уровень 3 и ниже
    такие варианты не получает. -> [row] (не больше одного на сторону)."""
    if (ctx.get("risk_level") or 0) < CHASE_MIN_LEVEL or ctx.get("no_confirm_allowed") is False or (ctx.get("stop_streak") or 0) >= 2:
        return []
    cap = ctx.get("no_confirm_max")
    cap = ENTRY_SIZE_MULT_MAX if cap is None else min(ENTRY_SIZE_MULT_MAX, float(cap))
    if cap <= 0:
        return []
    z = float(profile.get("zone_pct") or 0.3) / 100.0
    out, seen = [], set()
    for r in ok_rows:
        side, lvl = r.get("side"), _num(r.get("level"))
        if side in seen or not lvl:
            continue
        size = min(cap, _num(r.get("size_mult")) or cap)
        v = dict(r)
        v["confirm"] = "none"
        v["entry_type"] = "market"
        v["zone"] = {"low": _px(lvl * (1 - z)), "high": _px(lvl * (1 + z))}
        v["size_mult"] = round(size, 2)
        v["tags"] = list(dict.fromkeys(list(r.get("tags") or []) + ["no-confirm"]))
        v["high_risk"] = True
        out.append(v)
        seen.add(side)
    return out


def candidates(levels, ctx, profiles=None, high_risk=False):
    """-> [кандидат] (ok сначала, по убыванию rr_net) или None.
    levels: цены уровней монеты (кластеризуются здесь); ctx: словарь вида validate.entry_context (px, a5_pct, spread_bps, range_5m_pct, cost_rt_pct, cost_rt_maker_pct,
    min_rr, max_stop_pct, horizons, max_horizon, off_plan_ok, risk_level, day_size_mult, surge_x, ret_5m, stop_streak, session_type, no_confirm_allowed, no_confirm_max);
    profiles: профили (config/geometry.json целиком или только profiles), по умолчанию DEFAULT_PROFILES."""
    if not isinstance(ctx, dict):
        return None
    base, chp, _atr, cluster = _profiles(profiles)
    px = ctx.get("px")
    lv = pool(levels, cluster)
    ch = chase(ctx, lv, chp, base) if px else None
    if not px or len(lv) < 2:
        if ch:
            ch["n"] = 1
        return [ch] if ch else None
    a5 = ctx.get("a5_pct") or 0.0
    floor_taker = stop_floor_pct(ctx)
    floor = stop_floor_pct(ctx, "limit")                   # пол для лимитного входа (maker) не выше пола по рынку: стоп между ними требует лимитный вход
    max_dist = ENTRY_MAX_LEVEL_DIST_LOOSE_PCT if _loose(ctx) else ENTRY_MAX_LEVEL_DIST_PCT
    buf = base["stop_buf_atr"] * a5 / 100.0
    dsm = ctx.get("day_size_mult")
    size_mult = round(min(dsm, ENTRY_SIZE_MULT_MAX), 2) if dsm is not None and dsm < 1.0 else None      # план дня ограничил размер: validate требует size_mult не выше
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
            risk = abs(ref - stop)
            tps = sorted((t for t in lv if (t - ref) * sign >= base["tp1_min_r"] * risk), key=lambda t: abs(t - ref))[:2]      # цели за худшей ценой входа и не ближе 1R
            if not tps:
                continue
            stop_pct = abs(L - stop) / L * 100.0
            entry_type = "limit" if stop_pct < floor_taker else None      # узкий стоп окупается только maker-входом (круг 0.18% вместо 0.24%)
            wide = exec_stop_pct(L, px, stop, entry_type)
            cap = ref + sign * base["tp1_max_r"] * risk
            variants = ([[_px(cap), tps[0]]] if (tps[0] - cap) * sign > 0 else []) + [tps]      # сначала первая цель на tp1_max_r (вторая: уровень), если она проходит порог RR горизонта
            pick = None
            for v in variants:
                rr = _entry_rr(side, L, px, stop, v)
                if rr is None or rr <= 0:
                    continue
                res = _horizon(ctx, stop_pct, rr, wide, abs(v[0] - ref) / ref * 100.0, base)
                if pick is None or not res[2]:
                    pick = (v,) + res
                if not res[2]:
                    break
            if pick is None:
                continue
            tps, h, net, why = pick
            if h not in SESSION_ORDER:
                continue                                   # горизонт не определён (нет сессии/порогов): такого кандидата модели не показываем
            row = {"side": side, "level": _px(L), "stop": stop, "tp": [_px(t) for t in tps], "dist_pct": round(dist, 2), "stop_pct": round(wide, 2),
                   "rr_net": net, "horizon": h}
            if size_mult is not None:
                row["size_mult"] = size_mult
            if h == "scalp" or entry_type == "limit":
                row["entry_type"] = "limit"                # maker у уровня после свечи: дешевле круг; обязателен для scalp и для стопа уже пола по рынку
            if ctx.get("range_5m_pct") is not None and ctx["range_5m_pct"] > stop_pct and not _loose(ctx):
                why = why or "range_gt_stop"
            if why:
                row["fail"] = why
                bad.append(row)
            else:
                row["ok"] = True
                ok.append(row)
    ok.sort(key=lambda r: -r["rr_net"])
    bad.sort(key=lambda r: -r["rr_net"])
    extra = _no_confirm_variants(ctx, ok[:base["max_ok"]], chp) if high_risk else []      # риск 4-5: ранний вход без свечи, уменьшенный размер
    out = ([ch] if ch else []) + ok[:base["max_ok"]] + bad[:base["max_fail"]] + extra      # импульс первым: он живёт минуты
    for i, r in enumerate(out):
        r["n"] = i + 1                                                # номер для es_take / es_watch (модели не нужно считать позиции)
    return out or None


def chase(ctx, lv, profile=None, base=None):
    """momentum_chase (уровни риска 4-5): вход по рынку ПО импульсу события, без подтверждения свечой. -> кандидат или None.
    Стоп за началом импульсной свечи (диапазон 5м + stop_atr ATR), цель: ближайший уровень по ходу не ближе 1R, иначе tp_r R;
    размер не выше min(0.5, no_confirm.size_mult_max, размер дня); горизонт по ширине стопа. lv: уже кластеризованный пул."""
    profile = profile or DEFAULT_PROFILES["chase"]
    if (ctx.get("risk_level") or 0) < CHASE_MIN_LEVEL or ctx.get("no_confirm_allowed") is False or (ctx.get("stop_streak") or 0) >= 2:
        return None
    px, ret5 = ctx.get("px"), ctx.get("ret_5m")
    if not px or not ret5:
        return None
    sign = 1 if ret5 > 0 else -1
    if not chase_impulse(ctx, sign, profile):
        return None
    side = "long" if sign > 0 else "short"
    a5 = ctx.get("a5_pct") or 0.0
    r5 = ctx.get("range_5m_pct") or profile["range_atr"] * a5
    floor = stop_floor_pct(ctx)
    stop_pct = max(r5 + profile["stop_atr"] * a5, floor)
    stop = _px(px * (1 - sign * stop_pct / 100.0))
    risk = abs(px - stop)
    tps = [t for t in sorted((t for t in lv if (t - px) * sign >= risk), key=lambda t: abs(t - px))][:2] or [_px(px + sign * profile["tp_r"] * risk)]
    rr = _entry_rr(side, px, px, stop, tps)
    if not rr or rr <= 0:
        return None
    h, net, why = _horizon(ctx, stop_pct, rr, stop_pct, abs(tps[0] - px) / px * 100.0, base)
    if h not in SESSION_ORDER:
        return None
    caps = [ENTRY_SIZE_MULT_MAX] + [x for x in (ctx.get("no_confirm_max"), ctx.get("day_size_mult")) if x]
    z = profile["zone_pct"] / 100.0
    row = {"setup": CHASE_SETUP, "side": side, "level": _px(px), "stop": stop, "tp": [_px(t) for t in tps], "dist_pct": 0.0, "stop_pct": round(stop_pct, 2),
           "rr_net": net, "horizon": h, "confirm": "none", "zone": {"low": _px(px * (1 - z)), "high": _px(px * (1 + z))}, "size_mult": round(min(caps), 2),
           "tags": ["no-confirm"], "entry_type": "market"}
    if why:
        row["fail"] = why
    else:
        row["ok"] = True
    return row


def nearest_ok(cands, side, px, include_chase=False):
    """-> ok-кандидат стороны side с минимальным |level - px| (при равенстве меньший n) или None. Импульсный momentum_chase по умолчанию не берётся:
    тени входят на уровне кандидата, а chase есть вход по рынку."""
    best = None
    for c in cands or []:
        if not isinstance(c, dict) or not c.get("ok") or c.get("side") != side or not isinstance(c.get("level"), (int, float)):
            continue
        if c.get("setup") == CHASE_SETUP and not include_chase:
            continue
        d = abs(c["level"] - px)
        if best is None or d < best[0] - 1e-12:
            best = (d, c)
    return best[1] if best else None


def atr_plan(side, ctx, profile=None):
    """Профиль atr (тени с входом по рынку): стоп max(stop_floor_pct(ctx, "market"), k_atr * a5_pct) от цены, одна цель на tp_r R.
    -> {side, level (=px), stop, tp[], stop_pct, rr, entry_type: market} или None (нет цены, неверная сторона)."""
    profile = profile or DEFAULT_PROFILES["atr"]
    px = ctx.get("px") if isinstance(ctx, dict) else None
    if side not in ("long", "short") or not isinstance(px, (int, float)) or isinstance(px, bool) or px <= 0:
        return None
    sign = 1 if side == "long" else -1
    stop_pct = max(stop_floor_pct(ctx, "market"), profile["k_atr"] * (ctx.get("a5_pct") or 0.0))
    stop = _px(px * (1 - sign * stop_pct / 100.0))
    risk = abs(px - stop)
    tp = _px(px + sign * profile["tp_r"] * risk)
    return {"side": side, "level": _px(px), "stop": stop, "tp": [tp], "stop_pct": round(stop_pct, 2), "rr": profile["tp_r"], "entry_type": "market"}
