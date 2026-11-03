"""Издержки круга в % номинала из risk_profile.costs (чистый расчёт; раньше brain/brief._cost_rt_pct, числа прежние).

round_trip_pct(prof, venue, maker=False): 2 x (fee + slip) x 100 (вход и выход по рынку), как guard.round_trip_cost_pct.
maker=True: вход лимитом post-only (maker_fee площадки), выход по стопу рынком: (maker_fee + fee + 2 x slip) x 100; нет maker_fee -> None.
Нет издержек площадки и нет taker_fee_pct -> None. Результат округлён до 2 знаков.
"""
import math


def _num(x):
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def round_trip_pct(prof, venue, maker=False):
    prof = prof if isinstance(prof, dict) else {}
    costs = prof.get("costs") if isinstance(prof.get("costs"), dict) else {}
    cc = costs.get(venue) if isinstance(costs.get(venue), dict) else None
    if cc is None:
        fee = _num(prof.get("taker_fee_pct"))
        cc = {"fee": fee, "slip": 0.0005} if fee is not None else None
    if cc is None or _num(cc.get("fee")) is None:
        return None
    slip = _num(cc.get("slip")) or 0.0
    if maker:
        mf = _num(cc.get("maker_fee"))
        return None if mf is None else round((mf + cc["fee"] + 2.0 * slip) * 100.0, 2)
    return round(2.0 * (cc["fee"] + slip) * 100.0, 2)
