"""Недельное меню срезов (R2, Requirements 4.1–4.5, 5.2; design.md, «Меню, скрининг и holdout»). Чистые функции над выборкой окна поиска.

Условия длины 1: шесть числовых признаков MENU_NUM × (`>= q80`, `<= q20`) по квантилям заполненных прогонов окна (значение порога фиксируется в
условии) и `coin_class = v` для словаря без `unknown`. Медиана, `side` и `utc_session` не строятся. Отсев: COVERAGE_LOW (признак заполнен меньше
feat_cover_min), TOO_BROAD (условие охватывает больше max_trade_loss_share заполненных прогонов окна), BLOCKED (семейство block_h или cond_h в чёрном списке),
BELOW_N (кластеров теста меньше min_n_menu). K — число оставшихся условий (≤ 17). Claim — {"metric": "mean_r", "dir": "lower", "vs": "base",
"effect": E}, E — модуль средней разности по кластерам теста окна (verify.effect).
"""
from tb.learnloop import verify as VF
from tb.logic import learn_rules as LR
from tb.logic.learn_cond import MENU_CAT, MENU_NUM, is_num, menu_cat_values

Q_HI, Q_LO = 0.8, 0.2
Q_ROUND = 6
K_MAX = len(MENU_NUM) * 2 + sum(len(menu_cat_values(f)) for f in MENU_CAT)


def quantile(xs, q):
    """Квантиль с линейной интерполяцией по отсортированным значениям (как numpy по умолчанию). Пусто -> None."""
    v = sorted(xs)
    if not v:
        return None
    pos = (len(v) - 1) * float(q)
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def _qv(x):
    y = round(float(x), Q_ROUND)
    return 0.0 if y == 0 else y


def conditions(p, cfg):
    """Условия меню по подготовленным строкам метрик окна (verify.prepared). -> (условия [{"cond", "feature", "q"}], отсев [{"feature", "code", "cover"}])."""
    n = len(p["m"])
    cover_min = float(cfg["verify"]["feat_cover_min"])
    conds, skipped = [], []
    for f in MENU_NUM:
        xs = [x for x in VF.column(p, f) if is_num(x)]
        cover = len(xs) / float(n) if n else 0.0
        if not xs or cover < cover_min:
            skipped.append({"feature": f, "code": "COVERAGE_LOW", "cover": round(cover, 4)})
            continue
        conds.append({"cond": [{"feature": f, "op": ">=", "value": _qv(quantile(xs, Q_HI))}], "feature": f, "q": Q_HI})
        conds.append({"cond": [{"feature": f, "op": "<=", "value": _qv(quantile(xs, Q_LO))}], "feature": f, "q": Q_LO})
    for f in MENU_CAT:
        have = sum(1 for x in VF.column(p, f) if x is not None and x != "unknown")
        cover = have / float(n) if n else 0.0
        if not have or cover < cover_min:
            skipped.append({"feature": f, "code": "COVERAGE_LOW", "cover": round(cover, 4)})
            continue
        for v in menu_cat_values(f):
            conds.append({"cond": [{"feature": f, "op": "=", "value": v}], "feature": f, "q": None})
    return conds, skipped


def build_menu(win, cfg, blocked=None, side="lower"):
    """Меню по выборке окна поиска ({"rows", "a", "b"}). blocked — множество ключей или функция ключ -> bool (чёрный список); условие отсекается
    (BLOCKED), если в списке его семейство block_h (порог числового признака не важен: квантиль окна сдвигается) или точный cond_h (прежние записи).
    -> {"items": [{"cond", "claim", "cond_h", "block_h", "n", "share", "tc"}], "k", "dropped": [{"cond", "code", ...}], "n_rows"}; K = len(items)."""
    p = VF.prepared(win)
    n_rows = len(p["m"])
    broad = float(cfg["trial"]["max_trade_loss_share"])
    min_n = int(cfg["verify"]["min_n_menu"])
    is_blocked = blocked if callable(blocked) else (lambda h, s=frozenset(blocked or ()): h in s)
    conds, skipped = conditions(p, cfg)
    items, dropped = [], [dict(s) for s in skipped]
    for c in conds:
        cond = c["cond"]
        h = LR.cond_h(cond, side)
        bh = LR.block_h(cond, side)
        hits = VF.hit_vector(p, cond)
        share = sum(hits) / float(n_rows) if n_rows else 0.0
        base = {"cond": cond, "cond_h": h, "block_h": bh, "share": round(share, 4)}
        if share > broad:
            dropped.append(dict(base, code="TOO_BROAD"))
            continue
        if is_blocked(bh) or is_blocked(h):
            dropped.append(dict(base, code="BLOCKED"))
            continue
        tc = VF.tc_from_hits(p, hits)
        if len(tc) < min_n:
            dropped.append(dict(base, code="BELOW_N", n=len(tc)))
            continue
        claim = {"metric": "mean_r", "dir": side, "vs": "base", "effect": VF.effect(tc)}
        items.append(dict(base, claim=claim, n=len(tc), tc=tc))
    return {"items": items, "k": len(items), "dropped": dropped, "n_rows": n_rows}


def screen_menu(win, menu, cfg, side="lower"):
    """Скрининг всех условий меню с порогом alpha / K. -> [(item, результат verify.screen)] в порядке меню."""
    k = menu["k"]
    return [(it, VF.screen(win, it["cond"], k, cfg, side, tc=it["tc"])) for it in menu["items"]]
