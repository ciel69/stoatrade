"""Условия цикла обучения (R2, Requirements 5.1, 8.7): словарь признаков правил, операторы и сравнение. Чистый модуль, только stdlib.

`RULE_FEATURES` — девять признаков, которые есть и на метках на момент сигнала, и в контексте решения мозга; условия, проверяемые на метках,
ограничены ими. Меню первой итерации строится по `MENU_NUM` (`>= q80`, `<= q20`) и `MENU_CAT` (`=` каждому значению словаря без `unknown`).
`cmp_val` и `matches_features` перенесены из `tb.memory.lessons` без изменения поведения (там `_cmp_val` остаётся псевдонимом).
Имя модуля отличается от `tb.logic.conds` и `tb.logic.rules` (условия планов исполнителя), они здесь не используются.
"""

RULE_FEATURES = ("coin_class", "utc_session", "side", "range_5m_pct", "ret_5m_pct", "turn_surge_5m", "chg_24h_pct", "spread_bps", "funding_pct")
MENU_NUM = ("range_5m_pct", "ret_5m_pct", "turn_surge_5m", "chg_24h_pct", "spread_bps", "funding_pct")
MENU_CAT = ("coin_class",)                       # utc_session — вторая итерация (Requirement 18.8)

# словарь значений категориальных признаков правил (совпадает с tb.memory.cases: COIN_CLASSES, SESSIONS; side — только стороны входа)
CAT_VALUES = {
    "coin_class": ("meme", "major", "alt_mid", "alt_small", "stock_token", "unknown"),
    "utc_session": ("asia", "europe", "us", "overlap"),
    "side": ("long", "short"),
}
NUM_FEATURES = tuple(f for f in RULE_FEATURES if f not in CAT_VALUES)
OPS = ("=", ">=", "<=", "in")
CAT_OPS = ("=", "in")
NUM_OPS = (">=", "<=")
MAX_COND = 3
MAX_IN = 8


def menu_cat_values(feature):
    """Значения категориального признака для меню (без `unknown`)."""
    return tuple(v for v in CAT_VALUES.get(feature, ()) if v != "unknown")


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate_cond(cond, max_len=MAX_COND):
    """-> список ошибок. Конъюнкция 1..max_len условий {feature, op, value} только по RULE_FEATURES и словарю (пусто — условие в словаре)."""
    if not isinstance(cond, list) or not (1 <= len(cond) <= max_len):
        return ["cond: список из 1-%d условий" % max_len]
    errs = []
    seen = set()
    for c in cond:
        if not isinstance(c, dict) or set(c) != {"feature", "op", "value"}:
            errs.append("cond: элемент {feature, op, value}")
            continue
        f, op, v = c["feature"], c["op"], c["value"]
        if f not in RULE_FEATURES:
            errs.append("cond: признак %r вне RULE_FEATURES" % (f,))
            continue
        if f in seen:
            errs.append("cond: признак %s повторён" % f)
        seen.add(f)
        if f in CAT_VALUES:
            if op not in CAT_OPS:
                errs.append("cond: для %s допустимы = и in" % f)
                continue
            if op == "in" and (not isinstance(v, list) or not v or len(v) > MAX_IN):
                errs.append("cond: in требует непустой список (до %d)" % MAX_IN)
                continue
            for x in (v if op == "in" else [v]):
                if isinstance(x, bool) or x not in CAT_VALUES[f]:
                    errs.append("cond: %s=%r не из словаря" % (f, x))
        else:
            if op not in NUM_OPS:
                errs.append("cond: для %s допустимы >= и <=" % f)
                continue
            if not is_num(v) or v != v or v in (float("inf"), float("-inf")):
                errs.append("cond: %s нужно конечное число" % f)
    return errs


def cmp_val(op, v, x):
    """Значение признака x против порога/значения v оператором op. None и нечисло для >=/<= -> False; границы включены."""
    if x is None:
        return False
    if op == "=":
        return x == v
    if op == "in":
        return x in v
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    return x >= v if op == ">=" else x <= v


def matches_features(cond, feats):
    """Все условия конъюнкции выполнены признаками feats (отсутствующий признак условие не выполняет)."""
    return all(cmp_val(c["op"], c["value"], feats.get(c["feature"])) for c in cond)


def missing(cond, feats):
    """Признаки условия, которых нет в feats (значение None или ключа нет), по порядку условия."""
    return [c["feature"] for c in cond if feats.get(c["feature"]) is None]
