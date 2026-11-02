"""Блок досье `crowd`: настроение толпы по монете события (указание владельца 2026-11-01: рынок двигают люди и киты, а не только математика уровней).

Чистый модуль без файлов и сети: берёт уже собранные числа досье (структура монеты, событие сканера, деривативы, ордерфлоу, индекс страха) и сводит их
в флаги, настроение (mood), сторону давления толпы (bias) и предупреждения. Только числа и enum: текста третьих лиц здесь нет, это данные, не инструкции.
Это эвристика опытных трейдеров, НЕ доказанное преимущество: статистика по сделкам с флагами копится в журнале (ctx входа), выводы при n >= 30.

Входы (все необязательны, None = нет данных):
  st   = struct.s[sym]  (r24h, r1h, r4h, a5_pct, vx, tx, dpos, reg, fu)
  ev   = recent[-1].d по монете (ret_5m_pct, range_5m_pct, turn_surge_5m, funding_pct, oi_chg_15m_pct, liq_usd, liq_side)
  der  = intel.deriv[sym] (fund_pct, oi24_pct, ls, basis_bps)
  flow = brief.flow (cvd_5m_pct, imb_pct, big_5m_usd, warm_s)
  fng  = индекс страха и жадности (0..100)
"""

DUMP_24H_PCT = -20.0        # монета в сливе за сутки: паника, стопы лонгов, ликвидации
PUMP_24H_PCT = 25.0         # монета в разгоне за сутки: эйфория, FOMO, толпа в лонге
IMPULSE_ATR = 2.0           # свеча события от 2 ATR(5м) = импульс (кит или каскад), а не шум
IMPULSE_PCT = 1.5           # без ATR: импульс от 1.5% за 5 минут
SURGE_X = 3.0               # всплеск оборота 5м от 3x нормы
CROWD_LS_LONG = 2.0         # соотношение лонг/шорт: толпа в лонге
CROWD_LS_SHORT = 0.5        # толпа в шорте
CROWD_FUND_PCT = 0.05       # funding 8ч от 0.05%: платящая сторона переполнена
OI_BUILD_PCT = 15.0         # открытый интерес вырос за сутки на 15%+: позиции набираются (в сливе = пойманные лонги или давящие шорты)
OI_FLUSH_PCT = -15.0        # открытый интерес упал на 15%+: позиции вынесло, движение часто выдыхается
FLOW_PCT = 20.0             # перекос агрессии CVD за 5 минут от 20%
BOOK_PCT = 30.0             # перекос стакана у цены от 30%
FLOW_WARM_S = 120           # меньше 120 с ленты: CVD неполный, не учитываем
FNG_FEAR = 25
FNG_GREED = 75
MOODS = ("panic", "euphoria", "crowded_long", "crowded_short", "calm", "unclear")
BIASES = ("long", "short", "none")


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _r(x, n=2):
    v = _num(x)
    return None if v is None else round(v, n)


def build(st=None, ev=None, der=None, flow=None, fng=None):
    """-> dict блока crowd или None, если данных нет вовсе. Поля: числа (r24h, r1h, imp_atr, surge, fund, ls, oi24, oi15, cvd5, imb, fng),
    flags (коды), mood, bias (сторона, которую поддерживает давление толпы на продолжение), warn (коды предупреждений для сторон)."""
    st, ev, der, flow = st or {}, ev or {}, der or {}, flow or {}
    r24, r1 = _num(st.get("r24h")), _num(st.get("r1h"))
    a5 = _num(st.get("a5_pct"))
    ret5, surge = _num(ev.get("ret_5m_pct")), _num(ev.get("turn_surge_5m"))
    fund = _num(der.get("fund_pct"))
    if fund is None:
        fund = _num(ev.get("funding_pct"))
    if fund is None:
        fund = _num(st.get("fu"))
    ls, oi24, oi15 = _num(der.get("ls")), _num(der.get("oi24_pct")), _num(ev.get("oi_chg_15m_pct"))
    warm = _num(flow.get("warm_s")) or 0.0
    cvd = _num(flow.get("cvd_5m_pct")) if warm >= FLOW_WARM_S else None
    imb = _num(flow.get("imb_pct"))
    liq_side = ev.get("liq_side") if ev.get("liq_side") in ("long", "short") else None
    fng_v = _num(fng)
    if all(v is None for v in (r24, ret5, fund, ls, oi24, cvd, imb, fng_v)):
        return None
    flags = []
    imp_atr = None
    if ret5 is not None:
        imp_atr = ret5 / a5 if a5 else None
        strong = (imp_atr is not None and abs(imp_atr) >= IMPULSE_ATR) or (imp_atr is None and abs(ret5) >= IMPULSE_PCT)
        if strong:
            flags.append("impulse_down" if ret5 < 0 else "impulse_up")
    if surge is not None and surge >= SURGE_X:
        flags.append("surge")
    if r24 is not None:
        if r24 <= DUMP_24H_PCT:
            flags.append("dump_24h")
        elif r24 >= PUMP_24H_PCT:
            flags.append("pump_24h")
    if (ls is not None and ls >= CROWD_LS_LONG) or (fund is not None and fund >= CROWD_FUND_PCT):
        flags.append("crowded_long")
    if (ls is not None and ls <= CROWD_LS_SHORT) or (fund is not None and fund <= -CROWD_FUND_PCT):
        flags.append("crowded_short")
    if oi24 is not None:
        if oi24 >= OI_BUILD_PCT:
            flags.append("oi_building")
        elif oi24 <= OI_FLUSH_PCT:
            flags.append("oi_flush")
    if cvd is not None:
        if cvd <= -FLOW_PCT:
            flags.append("flow_sell")
        elif cvd >= FLOW_PCT:
            flags.append("flow_buy")
    if imb is not None:
        if imb <= -BOOK_PCT:
            flags.append("book_ask_heavy")
        elif imb >= BOOK_PCT:
            flags.append("book_bid_heavy")
    if liq_side:
        flags.append("liq_%s" % liq_side)
    if fng_v is not None:
        if fng_v <= FNG_FEAR:
            flags.append("fear")
        elif fng_v >= FNG_GREED:
            flags.append("greed")
    f = set(flags)
    # настроение: паника = слив суток и давление вниз сейчас; эйфория зеркально; иначе переполненная сторона; иначе спокойно
    if "dump_24h" in f and ("impulse_down" in f or "flow_sell" in f or "liq_long" in f):
        mood = "panic"
    elif "pump_24h" in f and ("impulse_up" in f or "flow_buy" in f or "liq_short" in f):
        mood = "euphoria"
    elif "crowded_long" in f and "crowded_short" not in f:
        mood = "crowded_long"
    elif "crowded_short" in f and "crowded_long" not in f:
        mood = "crowded_short"
    elif r24 is None and ret5 is None:
        mood = "unclear"
    else:
        mood = "calm"
    # сторона давления: кто вынужден закрываться дальше. Паника + толпа в лонге + растущий OI = пойманные лонги, продолжение вниз;
    # паника + толпа в шорте + падающий OI = шорты уже закрываются, выдох, возможен возврат (только после реакции у уровня). Эйфория зеркально.
    bias = "none"
    if mood == "panic":
        bias = "long" if ("crowded_short" in f and "oi_flush" in f) else "short"
    elif mood == "euphoria":
        bias = "short" if ("crowded_long" in f and "oi_flush" in f) else "long"
    elif mood == "crowded_long" and ("impulse_down" in f or "flow_sell" in f):
        bias = "short"                   # лонги переполнены, а цена пошла против них: топливо для слива
    elif mood == "crowded_short" and ("impulse_up" in f or "flow_buy" in f):
        bias = "long"                    # шорты переполнены, цена растёт: топливо для сквиза
    warn = []
    if mood == "panic":
        warn.append("long_against_panic")        # лонг только после реакции (sweep_reject / level_reclaim) и свечи, никакого trend_pullback «на отскок»
    if mood == "euphoria":
        warn.append("short_against_euphoria")
    if "impulse_down" in f and "surge" not in f:
        warn.append("thin_dump")                 # падение без оборота: один крупный продавец, стоп по дороге гэпнет
    if "impulse_up" in f and "surge" not in f:
        warn.append("thin_pump")
    out = {"r24h": _r(r24, 1), "r1h": _r(r1, 2), "imp_atr": _r(imp_atr, 1), "surge": _r(surge, 1), "fund": _r(fund, 4), "ls": _r(ls, 2), "oi24": _r(oi24, 1),
           "oi15": _r(oi15, 2), "cvd5": _r(cvd, 1), "imb": _r(imb, 1), "fng": None if fng_v is None else int(fng_v),
           "flags": flags, "mood": mood, "bias": bias, "warn": warn}
    return {k: v for k, v in out.items() if v is not None and v != []}
