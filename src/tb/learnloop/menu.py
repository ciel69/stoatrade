"""Недельное меню срезов (R2, Requirements 4.1–4.5, 5.2; design.md, «Меню, скрининг и holdout»). Чистые функции над выборкой окна поиска.

Условия длины 1: шесть числовых признаков MENU_NUM × (`>= q80`, `<= q20`) по квантилям заполненных прогонов окна (значение порога фиксируется в
условии) и `coin_class = v` для словаря без `unknown`. Медиана, `side` и `utc_session` не строятся. Отсев: COVERAGE_LOW (признак заполнен меньше
feat_cover_min), TOO_BROAD (условие охватывает больше max_trade_loss_share заполненных прогонов окна), BLOCKED (семейство block_h или cond_h в чёрном списке),
BELOW_N (кластеров теста меньше min_n_menu). K — число оставшихся условий (≤ 17). Claim — {"metric": "mean_r", "dir": "lower", "vs": "base",
"effect": E}, E — модуль средней разности по кластерам теста окна (verify.effect).
Вторая итерация (задача 31): условия `utc_session = v` с тестом Уэлча — только при second_iteration.session_menu.enabled и достижимой
мощности (session_items); по умолчанию выключено, меню прежнее.
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
    s_items, s_dropped = session_items(win, cfg, blocked=is_blocked, side=side, k_base=len(items))   # выключено по умолчанию
    items.extend(s_items)
    dropped.extend(s_dropped)
    return {"items": items, "k": len(items), "dropped": dropped, "n_rows": n_rows}


def screen_menu(win, menu, cfg, side="lower"):
    """Скрининг всех условий меню с порогом alpha / K. -> [(item, результат verify.screen | verify.screen_welch)] в порядке меню."""
    k = menu["k"]
    out = []
    for it in menu["items"]:
        if it.get("test") == "welch":
            out.append((it, VF.screen_welch(win, it["cond"], k, cfg, side, groups=it.get("groups"))))
        else:
            out.append((it, VF.screen(win, it["cond"], k, cfg, side, tc=it["tc"])))
    return out


# ---------------------------------------------------------------- меню по utc_session (вторая итерация, задача 31; Requirement 18.8)
#
# Включается только блоком second_iteration.session_menu {"enabled": true, "power_min", "effect_r"} в config/learnloop.json (нет блока или
# enabled=false -> условий сессий нет, меню прежнее). Даже при enabled=true условие сессии входит в меню, только если достижимая мощность по
# потоку кластеров этой сессии в окне поиска не ниже power_min (цель задачи 9 — 0.5); иначе отсев LOW_POWER с числом мощности.
# Мощность — нормальное приближение одностороннего теста Уэлча при эффекте −effect_r: P = Φ(effect_r / se − z(1 − α')), se = √(s_in²/n_in +
# s_out²/n_out) по значениям кластеров окна; скрининг на окне поиска (α' = alpha / K), holdout на потоке окна, пересчитанном на holdout_days
# (α' = alpha / labels_max_new_per_day); мощность конвейера меню — произведение двух ступеней. По замеру задачи 9 (asia 15, us 11.2,
# europe 9.3, overlap ~6 кластеров в сутки) — см. design.md, «Замер на живых данных».

SESSION_LOW_POWER = "LOW_POWER"


def session_cfg(cfg):
    """Блок second_iteration.session_menu или None (меню по сессиям выключено)."""
    s = ((cfg or {}).get("second_iteration") or {}).get("session_menu")
    return s if isinstance(s, dict) and s.get("enabled") is True else None


def _phi(x):
    import math
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _z(p):
    """Квантиль стандартного нормального распределения (бисекция; p в (0, 1))."""
    lo, hi = -40.0, 40.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _phi(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def welch_power(n_in, n_out, sd_in, sd_out, effect, alpha_):
    """Мощность одностороннего теста Уэлча (нормальное приближение). Нет разброса или n < 2 -> 0.0."""
    import math
    if n_in < 2 or n_out < 2 or sd_in is None or sd_out is None:
        return 0.0
    se = math.sqrt(sd_in * sd_in / n_in + sd_out * sd_out / n_out)
    if se <= 0:
        return 0.0
    return _phi(float(effect) / se - _z(1.0 - float(alpha_)))


def session_power(groups, win, cfg, k):
    """Мощность меню по сессии: скрининг (окно поиска, alpha / K) × holdout (поток окна на holdout_days, alpha / K_h).
    -> {"power", "screen", "hold", "n_in", "n_out", "per_day"}."""
    from tb.learnloop import stat as ST
    sc = session_cfg(cfg) or {}
    eff = float(sc.get("effect_r") or 0.0)
    xs, ys = [x["r"] for x in groups["in"]], [x["r"] for x in groups["out"]]
    sd_in, sd_out = ST.sd(xs), ST.sd(ys)
    days = max(1e-9, (win["b"] - win["a"]) / 86400.0)
    hd = float(cfg["verify"]["holdout_days"])
    a = float(cfg["verify"]["alpha"])
    p_s = welch_power(len(xs), len(ys), sd_in, sd_out, eff, a / max(1, int(k)))
    f = hd / days
    p_h = welch_power(int(len(xs) * f), int(len(ys) * f), sd_in, sd_out, eff, a / int(cfg["verify"]["labels_max_new_per_day"]))
    return {"power": round(p_s * p_h, 4), "screen": round(p_s, 4), "hold": round(p_h, 4), "n_in": len(xs), "n_out": len(ys),
            "per_day": round(len(xs) / days, 2)}


def session_items(win, cfg, blocked=None, side="lower", k_base=0):
    """Условия utc_session = v для меню (только при session_cfg и мощности ≥ power_min). -> (items, dropped)."""
    sc = session_cfg(cfg)
    if sc is None:
        return [], []
    from tb.logic.learn_cond import menu_cat_values as _vals
    p = VF.prepared(win)
    n_rows = len(p["m"])
    broad = float(cfg["trial"]["max_trade_loss_share"])
    is_blocked = blocked if callable(blocked) else (lambda h, s=frozenset(blocked or ()): h in s)
    vals = list(_vals(VF.SESSION_FEATURE))
    k = int(k_base) + len(vals)
    items, dropped = [], []
    for v in vals:
        cond = [{"feature": VF.SESSION_FEATURE, "op": "=", "value": v}]
        h, bh = LR.cond_h(cond, side), LR.block_h(cond, side)
        g = VF.cluster_groups(win, cond)
        in_names = set(x["cluster"] for x in g["in"])
        share = (sum(1 for c in p["cid"] if p["names"][c] in in_names) / float(n_rows)) if n_rows else 0.0
        base = {"cond": cond, "cond_h": h, "block_h": bh, "share": round(share, 4)}
        if share > broad:
            dropped.append(dict(base, code="TOO_BROAD"))
            continue
        if is_blocked(bh) or is_blocked(h):
            dropped.append(dict(base, code="BLOCKED"))
            continue
        pw = session_power(g, win, cfg, k)
        if pw["power"] < float(sc["power_min"]):
            dropped.append(dict(base, code=SESSION_LOW_POWER, power=pw))
            continue
        nums = VF.welch_numbers(g["in"], g["out"], side)
        eff = round(min(abs(nums["diff"] or 0.0), 10.0), 2)
        items.append(dict(base, claim={"metric": "mean_r", "dir": side, "vs": "base", "effect": eff}, n=pw["n_in"], tc=[], test="welch",
                          groups=g, power=pw))
    return items, dropped
