"""Свечи для разметки (R1 label factory): нормализация ответа Bybit, отсев незакрытой свечи, агрегация, ATR, исходы по пути цены.

Чистый модуль (только stdlib): время и данные приходят аргументами. Строка свечи: Row = (open_ts_s:int, o, h, l, c, v),
время открытия в секундах epoch. Мусорные строки отбрасываются без исключения; нет данных = None, а не нулевой результат.
"""
import math

MIN_S = 60
GAP_MAX_MIN = 3        # пропуск длиннее этого числа минут - флаг gap
HALT_MIN = 30          # нулевой объём подряд дольше этого - флаг halt


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def from_bybit(list_rows, tf_s, now_s):
    """Ответ /v5/market/kline (result.list, строки [startMs, o, h, l, c, vol, turnover], новые первыми) -> Row по возрастанию времени.

    Отбрасывает незакрытую свечу (open_ts + tf_s > now_s), дубли по времени (остаётся последняя) и мусор (нечисла, цена <= 0, h < l).

    >>> from_bybit([["120000", "1", "2", "1", "2", "5"], ["60000", "1", "1", "1", "1", "3"]], 60, 120)
    [(60, 1.0, 1.0, 1.0, 1.0, 3.0)]
    """
    out = {}
    for r in list_rows or []:
        try:
            ts = int(int(float(r[0])) // 1000)
            vals = [_num(x) for x in list(r)[1:6]]
        except (TypeError, ValueError, IndexError):
            continue
        if len(vals) < 5 or any(v is None for v in vals):
            continue
        o, h, l, c, v = vals
        if min(o, h, l, c) <= 0 or h < l or v < 0:
            continue
        if ts + tf_s > now_s:
            continue
        out[ts] = (ts, o, h, l, c, v)
    return [out[k] for k in sorted(out)]


def closed_before(rows, t0_s, tf_s):
    """Свечи, закрытые не позже t0: open_ts + tf_s <= t0_s (порядок сохраняется)."""
    return [r for r in rows if r[0] + tf_s <= t0_s]


def agg(rows_1m, tf_min):
    """1м -> tf_min-минутные свечи с выравниванием по UTC (граница = кратное tf_min*60 с). Только полные корзины
    (все tf_min минут на месте): неполная корзина (хвост или дыра) не даёт свечи, чтобы не выдавать незакрытое за закрытое."""
    step = int(tf_min) * MIN_S
    if step <= 0:
        return []
    buckets = {}
    for r in sorted(rows_1m, key=lambda x: x[0]):
        buckets.setdefault(r[0] // step * step, {})[r[0]] = r
    out = []
    for b in sorted(buckets):
        d = buckets[b]
        if len(d) != int(tf_min) or any(b + i * MIN_S not in d for i in range(int(tf_min))):
            continue
        rs = [d[b + i * MIN_S] for i in range(int(tf_min))]
        out.append((b, rs[0][1], max(x[2] for x in rs), min(x[3] for x in rs), rs[-1][4], sum(x[5] for x in rs)))
    return out


def atr(rows, n=14):
    """ATR по Уайлдеру (как CandleBook.atr), в единицах цены; нужно n+1 свечей, иначе None."""
    if n < 1 or len(rows) < n + 1:
        return None
    trs = [max(c[2] - c[3], abs(c[2] - p[4]), abs(c[3] - p[4])) for p, c in zip(rows, rows[1:])]
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def _n_candles(minutes, tf_min):
    if not tf_min or tf_min <= 0 or minutes <= 0 or minutes % tf_min:
        return None
    return int(minutes // tf_min)


def ret_pct(rows, minutes, tf_min):
    """Доходность за последние `minutes` минут в %: close последней свечи к close свечи, предшествующей окну. Нужно minutes/tf_min + 1 свечей."""
    n = _n_candles(minutes, tf_min)
    if n is None or len(rows) < n + 1:
        return None
    base = rows[-n - 1][4]
    return (rows[-1][4] / base - 1) * 100 if base > 0 else None


def range_pct(rows, minutes, tf_min):
    """Размах (max high - min low) последних `minutes` минут в % от close последней свечи. Нужно minutes/tf_min свечей."""
    n = _n_candles(minutes, tf_min)
    if n is None or len(rows) < n or rows[-1][4] <= 0:
        return None
    w = rows[-n:]
    return (max(r[2] for r in w) - min(r[3] for r in w)) / rows[-1][4] * 100


def gaps(rows_1m, max_gap_min=GAP_MAX_MIN):
    """Пропуски в 1м свечах длиннее max_gap_min минут -> [(open_ts_до, open_ts_после, missing_min)], missing_min = число отсутствующих минут."""
    rs = sorted(rows_1m, key=lambda x: x[0])
    out = []
    for p, c in zip(rs, rs[1:]):
        miss = (c[0] - p[0]) // MIN_S - 1
        if miss > max_gap_min:
            out.append((p[0], c[0], int(miss)))
    return out


def path_stats(rows_1m, start_s, px0, side, minutes, atr_pct):
    """Исход за `minutes` минут от start_s по 1м свечам [start_s, start_s + minutes*60), в сторону side ('long'|'short').

    -> {ret_pct, mfe_pct, mae_pct, mfe_atr, mae_atr, t_mfe_min, t_mae_min, gap, halt}.
    ret_pct - направленная доходность по close последней свечи окна; mfe_pct >= max(0, ret_pct) - максимум благоприятного хода (по high/low),
    mae_pct <= min(0, ret_pct) - максимум неблагоприятного (отрицательный). t_*_min - минуты от start_s до конца свечи, на которой
    достигнут экстремум (порядок внутри свечи неизвестен; первое достижение; 0, если хода не было), всегда 0 <= t <= minutes.
    *_atr = pct / atr_pct (None без atr_pct > 0). gap: пропуск длиннее 3 мин внутри окна или недостающие края; halt: нулевой объём
    подряд дольше 30 мин. Нет свечей окна или px0 <= 0 -> все числа None, gap True.
    """
    if side not in ("long", "short"):
        raise ValueError("side must be long|short")
    end_s = start_s + int(minutes) * MIN_S
    w = sorted((r for r in rows_1m if start_s <= r[0] < end_s), key=lambda x: x[0])
    res = {"ret_pct": None, "mfe_pct": None, "mae_pct": None, "mfe_atr": None, "mae_atr": None,
           "t_mfe_min": None, "t_mae_min": None, "gap": True, "halt": False}
    if not w or not px0 or px0 <= 0:
        return res
    sign = 1.0 if side == "long" else -1.0
    ret = sign * (w[-1][4] / px0 - 1) * 100
    mfe, mae, t_mfe, t_mae = 0.0, 0.0, 0.0, 0.0
    for r in w:
        t = (r[0] + MIN_S - start_s) / MIN_S
        fav, adv = ((r[2], r[3]) if side == "long" else (r[3], r[2]))
        f = sign * (fav / px0 - 1) * 100
        a = sign * (adv / px0 - 1) * 100
        if f > mfe:
            mfe, t_mfe = f, t
        if a < mae:
            mae, t_mae = a, t
    mfe, mae = max(mfe, ret, 0.0), min(mae, ret, 0.0)          # close лежит внутри [low, high]; защита от округления
    edge_gap = (w[0][0] - start_s) // MIN_S > GAP_MAX_MIN or (end_s - (w[-1][0] + MIN_S)) // MIN_S > GAP_MAX_MIN
    run = best = 0
    for r in w:
        run = run + 1 if r[5] <= 0 else 0
        best = max(best, run)
    ok_atr = _num(atr_pct) is not None and atr_pct > 0
    res.update(ret_pct=ret, mfe_pct=mfe, mae_pct=mae,
               mfe_atr=mfe / atr_pct if ok_atr else None, mae_atr=mae / atr_pct if ok_atr else None,
               t_mfe_min=t_mfe, t_mae_min=t_mae, gap=bool(gaps(w) or edge_gap), halt=best > HALT_MIN)
    return res
