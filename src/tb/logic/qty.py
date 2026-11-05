"""Округление объёма закрытия к лоту биржи (R4, Requirement 3.11). Чистая логика: исполнитель, контрфактический симулятор
выходов и сравнение с фактом считают доли одной функцией. Перенесено из executor/core.py без изменения поведения.
"""
import math


def floor_step(x, step):
    """Округление вниз к шагу без накопления ошибки float (то же, что executor/gate.floor_step).

    >>> floor_step(0.07, 0.01), floor_step(0.149999, 0.01), floor_step(3.0, 0)
    (0.07, 0.14, 3.0)
    """
    step = float(step or 0)
    if step <= 0:
        return float(x)
    return round(math.floor(float(x) / step + 1e-9) * step, 12)


def close_qty(want, remaining, step, min_qty=None, full_req=False):
    """Объём закрытия по шагу и минимальному лоту биржи -> (qty, full).

    Доля округляется вниз к шагу. Доля меньше минимального лота (lot = max(шаг, min_qty)) раньше давала 0 и отказ E_STATUS_FORBIDS
    (инцидент ETH 2026-11-03: остаток 0.01 при шаге 0.01, frac 0.5): теперь неделимый остаток (меньше двух лотов) закрывается целиком,
    делимый закрывается одним минимальным лотом. Это только снижение риска, больше остатка не закрывается.

    >>> close_qty(0.005, 0.01, 0.01), close_qty(0.003, 0.05, 0.01), close_qty(0.07, 0.14, 0.01)
    ((0.01, True), (0.01, False), (0.07, False))
    """
    step = float(step or 0) or 1e-12
    remaining = float(remaining)
    qty = min(remaining, floor_step(want, step))
    full = bool(full_req) or qty >= remaining - step / 2.0
    lot = max(step, float(min_qty or 0))
    if not full and remaining > 0 and qty < lot - step / 2.0:
        if remaining < 2.0 * lot - step / 2.0:
            return remaining, True
        qty = lot
    return (remaining if full else qty), full
