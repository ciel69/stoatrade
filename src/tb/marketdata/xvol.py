"""Обороты монет на других биржах для тепловой карты сканера: runtime/xvol.json (пишет intel.xex через ccxt) -> поля строки карты.
Слой marketdata не импортирует intel (тексты третьих лиц изолированы от торговли): здесь только чтение числового файла и расчёт.
"""
import re

XVOL_STALE_S = 900


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


_RE_MULT = re.compile(r"^(1000000|100000|10000|1000|100)([A-Z].*)$")
PX_TOL = 0.05


def _match(vols, sym, px):
    """Оборот той же монеты на другой бирже: тот же тикер или без множителя (Bybit 1000PEPE = OKX PEPE), цена совпадает в пределах PX_TOL
    с поправкой на множитель (иначе это другой токен с тем же тикером). -> USD или None."""
    m = _RE_MULT.match(sym)
    for key, k in ((sym, 1.0),) + (((m.group(2), float(m.group(1))),) if m else ()):
        r = vols.get(key)
        if not isinstance(r, list) or len(r) != 2:
            continue
        v, opx = _f(r[0]), _f(r[1])
        if v and opx and px and abs(opx * k / px - 1.0) <= PX_TOL:
            return v
    return None


def heat_fields(xvol, sym, bybit_turn_musd, now, px=None):
    """Поля тепловой карты по монете: суммарный оборот 24 ч по биржам (млн USD), доля Bybit (%), лидер оборота, число бирж. Нет данных -> {}."""
    if not isinstance(xvol, dict) or not isinstance(bybit_turn_musd, (int, float)) or bybit_turn_musd <= 0 or not _f(px):
        return {}
    other = {}
    for e, d in (xvol.get("ex") or {}).items():
        if not isinstance(d, dict) or not isinstance(d.get("at"), (int, float)) or now - d["at"] > XVOL_STALE_S:
            continue
        v = _match(d.get("vols") or {}, sym, float(px))
        if v:
            other[e] = v / 1e6
    if not other:
        return {}
    allv = dict(other, bybit=float(bybit_turn_musd))
    tot = sum(allv.values())
    return {"xturn_24h_musd": round(tot, 2), "bybit_share_pct": round(bybit_turn_musd / tot * 100.0, 1), "xlead": max(allv, key=allv.get), "xn": len(allv)}
