"""Контрфактический симулятор выхода (R4.1, R4.4; Requirement 3, design.md «Контрфактическая оценка»).

Чистая логика (`tb.logic`): без часов, файлов и сети. Оценивает вариант политики выхода на минутном пути и даёт числа,
сравнимые с базой (`X-0001` — пол без выхода по сроку).

Правила политики вычисляет `rules.evaluate_manage` на синтетическом `rt` — тот же код, что у исполнителя (Requirement 3.1):
своих копий стопов и трейла здесь нет. `exitpol.compile` вызывается один раз на прогон, дальше правила передаются в
`evaluate_manage` через `plan.manage` (тот же `xp_*`/`auto_*` состав), `qty.close_qty` применяется к долям при заданном `lot`.

Путь и вход
    `rows` — минутные свечи `(ts_s, o, h, l, c, v)`; первая свеча пути — свеча входа (путь начинается со свечи входа,
    `entry_ts = rows[0][0]`, как в `logic/sim.py`). Цена входа: `plan["entry_px"]` (запись пути), иначе считается как в
    `sim.run`: рыночный вход — по открытию свечи входа со спредом и `costs.slip`, лимитный — по `plan.entry.level` при
    заходе за уровень; лимит без захода в путь не заполняется (`filled=False`). Свеча входа проверяется на стоп со входа
    (Requirement 3.9); после лимитного заполнения в этой же свече благоприятная сторона свечи не используется ни для
    фиксаций, ни для правил, ни для MFE — только для стопа.

Порядок внутри свечи (Requirement 3.2)
    Правила по цене (`be`, ступени удержания, активация трейла, `partial`, `near_tp`) срабатывают на благоприятном
    экстремуме свечи (`high` для long, `low` для short), правила по времени (`stall`, `decay`, `timeout`) — по закрытию.
    `S` (основной): стоп, задетый `[low, high]`, исполняется раньше любой фиксации, новый стоп сработавшего в свече
    правила действует со следующей свечи. `T`: сначала фиксации на благоприятном экстремуме (и новые стопы), затем
    неблагоприятный экстремум — новый стоп действует внутри этой же свечи. Тейки плана идут раньше фиксаций политики
    (Requirement 3, Edge Cases), затем правила политики в порядке `exitpol.compile`.

Неоднозначная свеча и `d_lo` (Requirement 3.3, 6.4; ревизия 2026-11-04)
    Свеча неоднозначна (`amb`), если в `[low, high]` попали одновременно стоп (действующий или новый стоп сработавшего в
    ней правила) и уровень фиксации/правила по цене (тейк плана, `at_r` компонента, цена заявки по времени). Для пары
    `d_lo = min(r_var(S) - r_base(S), r_var(T) - r_base(T))` — обе стороны пары в одном порядке; на пути без
    неоднозначных свечей `S` и `T` дают одно и то же, поэтому `d_lo = d`.

Издержки (Requirement 3.4–3.6)
    taker — комиссия `costs.fee` и проскальзывание `costs.slip`; стоп — `costs.stop_slip_bps` от худшей из цены стопа и
    открытия свечи (гэп), с полспредом `costs.spread_bps`, как `paper.stop_trigger`; maker — комиссия `costs.maker_fee`
    без проскальзывания. Maker-заявка компонента по цене стоит на уровне срабатывания (без полспреда) и заполняется,
    только если свеча прошла уровень не меньше чем на `maker.through_ticks` тика и не меньше `maker.through_bps` б.п.;
    компонент по времени с `exec="maker"` ставит заявку по закрытию свечи срабатывания и заполняется таким же проходом в
    следующей свече, иначе через `maker.wait_s` (одна свеча) исполняется рыночно по закрытию следующей свечи. Частичное
    заполнение — «всё или ничего» (Requirement 3.6). `taker_only` — пересчёт для `ROBUST_COST`: maker-части исполняются
    как taker по тому же уровню (комиссия taker и `costs.slip`), компоненты по времени — рыночно по закрытию свечи
    срабатывания.

Конец пути и `cut` (Requirement 3.8, свойство 25)
    Остаток, доживший до конца пути, закрывается рыночно по закрытию последней свечи (`exit_reason="end"`); это модель
    «мозг держит» для базы `X-0001`. `pair` помечает пару `cut`, если хотя бы одна сторона дошла до конца пути, и такая
    пара остаётся в разностях.

Мозг (хронология вмешательств, `timeline`)
    `timeline` — список событий по возрастанию `ts` (секунды): `{"ts", "kind", "stop"|"price"|"frac"|"why"}`;
    `kind ∈ {move_stop|close|partial_close}`. Неизвестный `kind` пропускается; событие применяется в свече, в которую
    попало, до проверки стопа. Схема намеренно минимальна: полный формат хронологии задаёт источник путей сделок.
"""
import math

from tb.logic import candles, exitpol, paper, qty, rules

SIM_VER = "xs1"

DEFAULT_TICK = 0.01
DEFAULT_THROUGH_TICKS = 1
DEFAULT_THROUGH_BPS = 2.0          # Requirement 3.5: maker заполняется при проходе на 1 тик и на 2 б.п.
DEFAULT_WAIT_S = 60.0              # maker.wait_s: одна минутная свеча до перехода в taker
DEFAULT_STOP_SLIP_BPS = 5.0        # auto_protect.stop_slip_bps (Requirement 3.4)
DEFAULT_SPREAD_BPS = 2.0
DEFAULT_GAP_BPS = 8.0              # stop_moves.min_gap_bps_from_price исполнителя
DEFAULT_STEP_TICKS = 2             # stop_moves.min_step_ticks исполнителя
TIME_RULES = ("xp_stall", "xp_decay", "xp_timeout")   # правила по времени: только по закрытию свечи
_LIMIT_KINDS = ("limit", "candle_confirm_limit")
_EPS = 1e-12


# ---------------------------------------------------------------- мелочи
def _f(x):
    """Число или None (bool и нечисловое — None)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    v = float(x)
    return v if math.isfinite(v) else None


def _num(x, d=0.0):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return d
    return v if math.isfinite(v) else d


def _side(plan):
    s = (plan or {}).get("side")
    return 1 if s == "long" else (-1 if s == "short" else None)


def _rows(rows):
    try:
        out = [tuple(r) for r in (rows or [])]
    except TypeError:
        return []
    out.sort(key=lambda r: r[0])
    return [r for r in out if len(r) >= 5]


def _costs(costs):
    c = costs if isinstance(costs, dict) else {}
    fee = _num(c.get("fee"), 0.0)
    return {"fee": fee, "mfee": _num(c.get("maker_fee"), fee), "slip": _num(c.get("slip"), 0.0),
            "stop_slip": _num(c.get("stop_slip_bps"), DEFAULT_STOP_SLIP_BPS),
            "hs": _num(c.get("spread_bps"), DEFAULT_SPREAD_BPS) / 2e4}


def _maker(maker):
    m = maker if isinstance(maker, dict) else {}
    ticks = _num(m.get("through_ticks"), DEFAULT_THROUGH_TICKS)
    return {"through_ticks": ticks if ticks > 0 else DEFAULT_THROUGH_TICKS,
            "through_bps": _num(m.get("through_bps"), DEFAULT_THROUGH_BPS),
            "wait_s": _num(m.get("wait_s"), DEFAULT_WAIT_S)}


def _floor_policy(fp):
    """Пол -> политика. Принимает политику (`schema`), секцию `exec_limits.auto_protect` (`be_r`, ...) или None."""
    if isinstance(fp, dict) and "schema" in fp:
        return fp
    if isinstance(fp, dict):
        return exitpol.floor(fp)
    return {"schema": exitpol.SCHEMA}


def _ctx(plan, ctx):
    out = dict(ctx) if isinstance(ctx, dict) else {}
    if _f(out.get("max_hold_min")) is None:
        mh = _f(plan.get("max_hold_min"))
        if mh is not None and mh > 0:
            out["max_hold_min"] = mh
    if _f(out.get("entry_px")) is None:
        epx = _f(plan.get("entry_px"))
        if epx is not None and epx > 0:
            out["entry_px"] = epx
    return out


def _eff_min_profit(pol, fl):
    vals = []
    for src in (pol, fl):
        if isinstance(src, dict):
            v = _f(src.get("min_profit_r"))
            if v is not None:
                vals.append(v)
    return max(vals) if vals else exitpol.PROFIT_MIN_R


def _empty(reason="no_fill", error=None):
    out = {"filled": False, "entry_ts": None, "entry_px": None, "parts": [], "r_gross": 0.0, "fees_r": 0.0, "r_net": 0.0,
           "mfe_r": 0.0, "mae_r": 0.0, "exit_reason": reason, "hold_min": 0.0, "amb": False, "end_ts": None}
    if error:
        out["error"] = error
    return out


# ---------------------------------------------------------------- вход и цены исполнения
def _entry(plan, rows, c, mk, tick, s):
    """-> {"px", "fee_rate", "limit_fill"} | None (лимит не заполнен).

    Цена из пути (`entry_px`/`fill_px`) приоритетна: путь уже хранит фактическое заполнение, повторно спред и
    проскальзывание не начисляются (комиссия входа — по способу входа).
    """
    ent = plan.get("entry") if isinstance(plan.get("entry"), dict) else {}
    kind = ent.get("kind") or "market"
    px0 = _f(plan.get("entry_px")) or _f(plan.get("fill_px"))
    if px0 is not None and px0 > 0:
        return {"px": px0, "fee_rate": c["mfee"] if kind in _LIMIT_KINDS else c["fee"], "limit_fill": kind in _LIMIT_KINDS}
    o = _num(rows[0][1], 0.0)
    if o <= 0:
        return None
    if kind not in _LIMIT_KINDS:
        f = paper.fill_market("buy" if s == 1 else "sell", 1.0, o * (1 - c["hs"]), o * (1 + c["hs"]),
                              c["slip"] * 1e4, c["fee"])
        return {"px": f["px"], "fee_rate": c["fee"], "limit_fill": False}
    level = _f(ent.get("level"))
    if level is None or level <= 0:
        return None
    last = rows[0][3] if s == 1 else rows[0][2]
    if not paper.limit_touch("buy" if s == 1 else "sell", level, last, tick, mk["through_ticks"]):
        return None
    return {"px": level, "fee_rate": c["mfee"], "limit_fill": True}


def _worse(s, px, frac):
    """Цена taker-исполнения с проскальзыванием `frac` (доля): для long-закрытия ниже, для short выше."""
    return px * (1 - s * frac)


def _stop_fill(s, stop_px, adv, ref, c):
    """Цена стопа (`paper.stop_trigger`) | None. `ref` — худшая из цены стопа и открытия свечи (гэп, Requirement 3.4)."""
    hit = paper.stop_trigger("long" if s == 1 else "short", stop_px, adv,
                             ref * (1 - c["hs"]), ref * (1 + c["hs"]), c["stop_slip"])
    return None if hit is None else hit["px"]


def _touched(s, level, fav):
    """Свеча дошла до уровня (касание)."""
    return fav >= level - _EPS if s == 1 else fav <= level + _EPS


def _passed(s, level, fav, tick, mk):
    """Maker (Requirement 3.5): свеча прошла уровень не меньше чем на through_ticks тика И не меньше through_bps б.п."""
    need = max(mk["through_ticks"] * tick, mk["through_bps"] / 1e4 * abs(level))
    return fav >= level + need - _EPS if s == 1 else fav <= level - need + _EPS


def _fix_px(s, level, o, c, maker):
    """Цена фиксации: maker — по уровню без проскальзывания; taker — уровень минус slip, при гэпе — по открытию."""
    if maker:
        return level
    gapped = o >= level - _EPS if s == 1 else o <= level + _EPS
    return _worse(s, o if gapped else level, c["slip"])


def _quote(s, close, c):
    """Цена maker-заявки по времени: исполнитель ставит её по закрытию (long — от ask, short — от bid)."""
    return close * (1 + s * c["hs"])


# ---------------------------------------------------------------- симулятор
def run(plan, rows, policy, floor_policy=None, costs=None, order="S", maker=None, timeline=None,
        ctx=None, lot=None, taker_only=False):
    """-> {"filled", "entry_ts", "entry_px", "parts":[{ts, px, frac, why, exec}], "r_gross", "fees_r", "r_net",
    "mfe_r", "mae_r", "exit_reason", "hold_min", "amb", "end_ts"}.

    `plan`: `side`, `stop`, `tps[]`, `fracs[]`, `tick`, `max_hold_min`, `entry` (kind/level/ttl_min), `entry_px` (из пути).
    `policy` — политика `tb.exit_policy/1` (нормализуется с полом), `floor_policy` — пол (политика, `auto_protect` или None).
    `costs`: `fee`, `maker_fee`, `slip` (доли), `stop_slip_bps`, `spread_bps`. `order ∈ {S, T}`.
    `maker`: `through_ticks` (1), `through_bps` (2), `wait_s` (60). `timeline`: вмешательства мозга. `ctx`: `max_hold_min`,
    `atr5_pct`, `sig_type`, `horizon`, `stop_moves` (`min_gap_bps_from_price`, `min_step_ticks`, `min_interval_s`).
    `lot`: `qty`, `qty_step`, `min_qty` -> доли через `qty.close_qty`. `taker_only` —
    maker-части по цене taker (`ROBUST_COST`, Requirement 6.2).

    `exit_reason`: `sl` | `be` | `trail` (стоп), `tp` (тейк плана), `rule` (компонент политики), `timeout` (xp_timeout),
    `manual` (мозг), `end` (путь кончился), `no_fill`. `mfe_r`/`mae_r` — по экстремумам свечей до выхода (свеча стопа в MFE
    не входит, как в `logic/sim.py`). `end_ts` — время последней части.
    """
    plan = plan if isinstance(plan, dict) else {}
    s = _side(plan)
    c = _costs(costs)
    mk = _maker(maker)
    fl = _floor_policy(floor_policy)
    pol = policy if isinstance(policy, dict) else {}
    rows = _rows(rows)
    order = "T" if str(order or "S").upper() == "T" else "S"
    if s is None:
        return _empty(error="BAD_SIDE")
    if not rows:
        return _empty(error="NO_ROWS")
    tick = _f(plan.get("tick")) or DEFAULT_TICK
    stop0 = _f(plan.get("stop"))
    if stop0 is None and isinstance(plan.get("stop"), dict):
        stop0 = _f(plan["stop"].get("price"))
    ent = _entry(plan, rows, c, mk, tick, s)
    if ent is None:
        return _empty()
    entry_px, entry_ts = ent["px"], rows[0][0]
    if stop0 is None or stop0 <= 0 or s * (entry_px - stop0) <= 0:
        return _empty(error="BAD_STOP")
    ru = abs(entry_px - stop0)
    cc = _ctx(plan, ctx)

    # правила политики считает exitpol.compile один раз; исполняет их rules.evaluate_manage (Requirement 3.1)
    compiled = exitpol.compile(pol, fl, cc)
    plan2 = {"side": plan.get("side"), "manage": compiled, "targets": plan.get("targets") or [],
             "lifecycle": plan.get("lifecycle") if isinstance(plan.get("lifecycle"), dict) else {}}
    sm = cc.get("stop_moves") if isinstance(cc.get("stop_moves"), dict) else {}
    limits = {"auto_protect": {"enabled": False, "stop_slip_bps": c["stop_slip"], "min_profit_r": _eff_min_profit(pol, fl)},
              "exit_ctx": cc, "tick": tick,
              "stop_moves": {"min_gap_bps_from_price": _num(sm.get("min_gap_bps_from_price"), DEFAULT_GAP_BPS),
                             "min_step_ticks": _num(sm.get("min_step_ticks"), DEFAULT_STEP_TICKS),
                             "min_interval_s": _num(sm.get("min_interval_s"), 0)},
              "connectivity": {"public_price_stale_s": 1e9}}
    tps = [_num(x) for x in (plan.get("tps") or [])]
    fr = [_num(x) for x in (plan.get("fracs") or [])]
    if tps and len(fr) != len(tps):
        fr = [1.0 / len(tps)] * len(tps)
    tps = [(tps[i], fr[i]) for i in range(len(tps))]

    parts, fees = [], entry_px * ent["fee_rate"]
    remaining, cur_stop, stop_why = 1.0, stop0, "sl"
    best = worst = entry_px
    t_best = entry_ts
    next_tp = 0
    reason, amb = None, False
    pending = None                      # maker-заявка компонента по времени: {"px", "why", "idx", "until"}
    wait_candles = max(1, int(round(mk["wait_s"] / 60.0)))
    rt = {"status": "managing", "ts": {"entered": float(entry_ts)},
          "pos": {"qty": 1.0, "avg": entry_px, "initial_stop": stop0, "cur_stop": cur_stop, "r_unit": ru,
                  "best_px": best, "t_best": float(t_best), "side": plan.get("side"),
                  "entries": [{"qty": 1.0, "px": entry_px, "fee": entry_px * ent["fee_rate"]}]},
          "rules": {}}
    tl = [dict(e) for e in (timeline or []) if isinstance(e, dict) and _f(e.get("ts")) is not None]
    tl.sort(key=lambda e: _f(e.get("ts")))

    def add(ts, px, frac, why, rate, kind):
        nonlocal remaining, fees
        frac = min(_num(frac), remaining)
        if frac <= _EPS or px is None or px <= 0:
            return 0.0
        parts.append({"ts": ts, "px": px, "frac": frac, "why": why, "exec": kind})
        remaining = max(0.0, remaining - frac)
        fees += px * frac * rate
        return frac

    def want_frac(w):
        """Доля к закрытию: точная (пути прогонов делимы) или через `qty.close_qty` при `lot`."""
        w = min(_num(w), remaining)
        if not lot or w <= 0:
            return w
        q0 = _num(lot.get("qty"), 0.0)
        if q0 <= 0:
            return w
        got, full = qty.close_qty(w * q0, remaining * q0, _num(lot.get("qty_step"), 0.0), lot.get("min_qty"))
        return remaining if full else min(remaining, got / q0)

    def in_range(px, row):
        return row[3] - _EPS <= px <= row[2] + _EPS

    def eval_at(price, now):
        v = candles.MarketView(None, price, price, price, price, now, None, now)
        return rules.evaluate_manage(plan2, rt, v, now, limits)

    def stop_why_of(rid):
        return "be" if str(rid) == "auto_be" else "trail"

    for j, row in enumerate(rows):
        ts, o, h, l, cl = row[0], row[1], row[2], row[3], row[4]
        end = ts + 60
        fav, adv = (h, l) if s == 1 else (l, h)
        best_prev, worst_prev = best, worst
        worst = min(worst_prev, l) if s == 1 else max(worst_prev, h)
        if j == 0 and ent["limit_fill"]:
            fav = None                  # Requirement 3.9: свеча входа после лимитного заполнения — только для стопа

        # ---- вмешательства мозга
        brain_closed = False
        while tl and _f(tl[0].get("ts")) < end:
            ev = tl.pop(0)
            kind = ev.get("kind")
            px = _f(ev.get("price")) or _f(ev.get("px")) or o
            if kind in ("move_stop", "stop", "adjust"):
                st = _f(ev.get("stop"))
                if st is not None and st > 0 and rules.is_tighter(s, st, cur_stop):
                    cur_stop, stop_why = st, "brain"
                    rt["pos"]["cur_stop"] = cur_stop
            elif kind in ("close", "partial_close", "partial"):
                frac = 1.0 if kind == "close" else _num(ev.get("frac"), 0.0)
                add(ts, _worse(s, px, c["slip"]), want_frac(frac), ev.get("why") or "manual", c["fee"], "taker")
                if kind == "close" and remaining <= _EPS:
                    reason, brain_closed = "manual", True
                    break
        if brain_closed:
            best = best_prev
            if remaining <= _EPS:
                break

        # ---- правила цены: считаются всегда (и для amb, и для обоих порядков)
        rt["pos"]["qty"] = remaining
        rt["pos"]["cur_stop"] = cur_stop
        rt["pos"]["best_px"] = best if fav is None else (max(best_prev, fav) if s == 1 else min(best_prev, fav))
        acts, out, stop_acts, new_stop = [], {}, [], None
        if fav is not None:
            out = eval_at(fav, ts)
            acts = [a for a in (out.get("actions") or []) if str(a.get("rule")) not in TIME_RULES]
            stop_acts = [a for a in acts if a.get("type") == "move_stop"]
            new_stop = _f(stop_acts[0].get("to")) if stop_acts else None

        # ---- неоднозначная свеча: стоп (действующий или новый) и фиксация/правило по цене одновременно в [low, high]
        if fav is not None:
            patch_fired = {rid: int((rs or {}).get("fired", 0)) for rid, rs in (out.get("rt_patch") or {}).get("rules", {}).items()}
            fired_now = {rid for rid, n in patch_fired.items()
                         if rid not in TIME_RULES and n > int((rt["rules"].get(rid) or {}).get("fired", 0))}
            fix_in = bool(fired_now) or any(in_range(lv, row) for lv, _ in tps[next_tp:])
            fix_in = fix_in or (pending is not None and in_range(pending["px"], row))
            stop_in = in_range(cur_stop, row) or (new_stop is not None and in_range(new_stop, row))
            if fix_in and stop_in:
                amb = True

        # ---- порядок S: стоп раньше любой фиксации
        stopped = False
        if remaining > _EPS and order == "S":
            ref = min(cur_stop, o) if s == 1 else max(cur_stop, o)
            px_stop = _stop_fill(s, cur_stop, adv, ref, c)
            if px_stop is not None:
                add(ts, px_stop, remaining, stop_why, c["fee"], "taker")
                reason, stopped = stop_why, True

        applied = set()
        if remaining > _EPS:
            # --- тейки плана (раньше фиксаций политики)
            while fav is not None and next_tp < len(tps) and remaining > _EPS:
                lvl, frac = tps[next_tp]
                if not paper.limit_touch("sell" if s == 1 else "buy", lvl, fav, tick, mk["through_ticks"]):
                    break
                add(ts, lvl, want_frac(frac), "tp%d" % (next_tp + 1), c["mfee"], "maker")
                next_tp += 1
                if remaining <= _EPS:
                    reason = "tp"
            # --- фиксации и стопы правил политики
            for a in acts:
                typ = a.get("type")
                if typ not in ("partial_close", "close"):
                    continue
                frac_req = _f(a.get("frac_initial"))
                if frac_req is None:
                    frac_req = _f(a.get("frac")) or 1.0
                at_r = _f(a.get("at_r"))
                level = entry_px + s * at_r * ru if at_r is not None else fav
                if level is None or fav is None:
                    continue
                is_maker = a.get("exec") == "maker" and not taker_only
                if is_maker:
                    if not _passed(s, level, fav, tick, mk):
                        continue            # касание без прохода: заявка не заполнена, правило не сработало
                elif not _touched(s, level, fav):
                    continue
                px = _fix_px(s, level, o, c, is_maker)
                why = "timeout" if str(a.get("rule")) == "xp_timeout" else "rule"
                if add(ts, px, want_frac(frac_req), why, c["mfee"] if is_maker else c["fee"],
                       "maker" if is_maker else "taker") > 0:
                    applied.add(str(a.get("rule")))
                if remaining <= _EPS:
                    reason = why
                    break
            if remaining > _EPS:
                patch = (out.get("rt_patch") or {}).get("rules") or {}
                keep = applied | {str(a.get("rule")) for a in stop_acts}
                for rid, rs in patch.items():
                    if rid in keep:
                        rt["rules"][rid] = rs

        # ---- порядок T: фиксации уже исполнены, затем неблагоприятный экстремум (новый стоп — внутри свечи)
        if remaining > _EPS and order == "T":
            if new_stop is not None:
                cur_stop, stop_why = new_stop, stop_why_of(stop_acts[0].get("rule"))
                ref = cur_stop                     # стоп поставлен внутри свечи: гэпа через него быть не может
            else:
                ref = min(cur_stop, o) if s == 1 else max(cur_stop, o)
            px_stop = _stop_fill(s, cur_stop, adv, ref, c)
            if px_stop is not None:
                add(ts, px_stop, remaining, stop_why, c["fee"], "taker")
                reason, stopped = stop_why, True
        elif remaining > _EPS and order == "S" and new_stop is not None:
            cur_stop, stop_why = new_stop, stop_why_of(stop_acts[0].get("rule"))     # со следующей свечи
        rt["pos"]["cur_stop"] = cur_stop

        # ---- MFE/MAE свечи
        if not stopped and fav is not None:
            better = fav > best_prev if s == 1 else fav < best_prev
            best = fav if better else best_prev
            if better:
                t_best = ts
        if remaining <= _EPS:
            break

        # ---- maker-заявка по времени из прошлой свечи (Requirement 3.5)
        if pending is not None and j > pending["idx"]:
            if _passed(s, pending["px"], cl if fav is None else fav, tick, mk):
                add(ts, pending["px"], remaining, pending["why"], c["mfee"], "maker")
                reason, pending = pending["why"], None
                if remaining <= _EPS:
                    break
            elif j >= pending["until"]:
                add(end, _worse(s, cl, c["slip"]), remaining, pending["why"], c["fee"], "taker")
                reason, pending = pending["why"], None
                if remaining <= _EPS:
                    break

        # ---- правила по времени: по закрытию свечи
        rt["pos"].update({"cur_stop": cur_stop, "qty": remaining, "best_px": best, "t_best": float(t_best)})
        out_t = eval_at(cl, end)
        t_acts = [a for a in (out_t.get("actions") or []) if str(a.get("rule")) in TIME_RULES]
        t_stop = None
        for a in t_acts:
            if a.get("type") == "move_stop":
                to = _f(a.get("to"))
                if to is not None and rules.is_tighter(s, to, cur_stop):
                    t_stop = to if t_stop is None else (max(t_stop, to) if s == 1 else min(t_stop, to))
                continue
            if a.get("type") not in ("close", "partial_close"):
                continue
            why = "timeout" if str(a.get("rule")) == "xp_timeout" else "rule"
            frac = _f(a.get("frac")) or 1.0
            if a.get("exec") == "maker" and not taker_only:
                pending = {"px": _quote(s, cl, c), "why": why, "idx": j, "until": j + wait_candles}
            else:
                add(end, _worse(s, cl, c["slip"]), want_frac(frac), why, c["fee"], "taker")
                reason = why
        if t_stop is not None:
            cur_stop, stop_why = t_stop, "trail"
            rt["pos"]["cur_stop"] = cur_stop
        if t_acts:
            for rid, rs in ((out_t.get("rt_patch") or {}).get("rules") or {}).items():
                rt["rules"][rid] = rs
        if remaining <= _EPS:
            break

    if remaining > _EPS:
        last = rows[-1]
        add(last[0] + 60, _worse(s, last[4], c["slip"]), remaining, "end", c["fee"], "taker")
        reason = "end"

    r_gross = sum(p["frac"] * s * (p["px"] - entry_px) / ru for p in parts)
    mfe = max(0.0, s * (best - entry_px) / ru)
    mae = max(0.0, -s * (worst - entry_px) / ru)
    return {"filled": True, "entry_ts": entry_ts, "entry_px": entry_px, "parts": parts, "r_gross": r_gross,
            "fees_r": fees / ru, "r_net": r_gross - fees / ru, "mfe_r": mfe, "mae_r": mae,
            "exit_reason": reason or "no_fill",
            "hold_min": (parts[-1]["ts"] - entry_ts) / 60.0 if parts else 0.0,
            "amb": bool(amb), "end_ts": parts[-1]["ts"] if parts else entry_ts}


def pair(plan, rows, var, base, floor_policy=None, costs=None, ctx=None, maker=None, lot=None):
    """-> {"d", "d_lo", "d_taker", "amb", "cut"} — разности варианта и базы на одном пути (Requirement 3.3, 6.2, 6.4).

    `d` — `r_net(вариант, S) - r_net(база, S)`; `d_lo` — худшая для варианта разность пары в одном порядке (`min` из `S`
    и `T`), порядок `T` считается только на путях с неоднозначными свечами; `d_taker` — та же разность, когда maker-части
    обеих сторон оценены по цене taker (`ROBUST_COST`); `cut` — хотя бы одна сторона закрыта по концу пути. Если путь не
    заполнен у одной из сторон, все разности None.
    """
    a = run(plan, rows, var, floor_policy, costs, order="S", maker=maker, ctx=ctx, lot=lot)
    b = run(plan, rows, base, floor_policy, costs, order="S", maker=maker, ctx=ctx, lot=lot)
    cut = a["exit_reason"] == "end" or b["exit_reason"] == "end"
    amb = bool(a["amb"] or b["amb"])
    if not (a["filled"] and b["filled"]):
        return {"d": None, "d_lo": None, "d_taker": None, "amb": amb, "cut": cut}
    d = a["r_net"] - b["r_net"]
    d_lo = d
    if amb:
        at = run(plan, rows, var, floor_policy, costs, order="T", maker=maker, ctx=ctx, lot=lot)
        bt = run(plan, rows, base, floor_policy, costs, order="T", maker=maker, ctx=ctx, lot=lot)
        if at["filled"] and bt["filled"]:
            d_lo = min(d, at["r_net"] - bt["r_net"])
    a2 = run(plan, rows, var, floor_policy, costs, order="S", maker=maker, ctx=ctx, lot=lot, taker_only=True)
    b2 = run(plan, rows, base, floor_policy, costs, order="S", maker=maker, ctx=ctx, lot=lot, taker_only=True)
    d_taker = (a2["r_net"] - b2["r_net"]) if (a2["filled"] and b2["filled"]) else None
    return {"d": d, "d_lo": d_lo, "d_taker": d_taker, "amb": amb, "cut": cut}


def replay_brain(plan, rows, policy, floor_policy=None, costs=None, timeline=None, ctx=None, lot=None):
    """Прогон варианта с хронологией вмешательств мозга (порядок `S`): `d_b` испытания считается на парах таких прогонов
    (Requirement 6.5). Тот же `run`, поэтому доли округляются одним `qty.close_qty` при `lot`."""
    return run(plan, rows, policy, floor_policy, costs, order="S", timeline=timeline, ctx=ctx, lot=lot)


def path_mfe(rows, entry_px, stop, side, horizon_min):
    """MFE пути на фиксированном горизонте от входа в R (признак хвоста, Requirement 6.3; свойство 27).

    Один и тот же для варианта и базы: зависит только от пути, входа и стопа, а не от политики. Свечи с началом раньше
    `entry_ts + horizon_min` минут; нет данных — 0.0.
    """
    s = 1 if side == "long" else (-1 if side == "short" else 0)
    rr = _rows(rows)
    epx, stp = _f(entry_px), _f(stop)
    if s == 0 or not rr or epx is None or epx <= 0 or stp is None or abs(epx - stp) <= _EPS:
        return 0.0
    lim = rr[0][0] + _num(horizon_min) * 60.0
    best = epx
    for r in rr:
        if r[0] >= lim:
            break
        best = max(best, r[2]) if s == 1 else min(best, r[3])
    return max(0.0, s * (best - epx) / abs(epx - stp))
