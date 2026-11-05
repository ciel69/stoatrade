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
K_MAX = len(MENU_NUM) * 2 + sum(len(menu_cat_values(f)) for f in MENU_CAT)       # R1.7 первая волна: 10 × 2 + 5 = 25 (было 17)


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


def conditions(p, cfg, num=None):
    """Условия меню по подготовленным строкам метрик окна (verify.prepared). num — числовые признаки (по умолчанию MENU_NUM; другой набор —
    только для проверки волны). -> (условия [{"cond", "feature", "q"}], отсев [{"feature", "code", "cover"}]).
    Прежние строки cluster_runs без признаков cm_ дают None: признак с заполнением ниже feat_cover_min отсекается COVERAGE_LOW."""
    n = len(p["m"])
    cover_min = float(cfg["verify"]["feat_cover_min"])
    conds, skipped = [], []
    for f in (MENU_NUM if num is None else num):
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


def build_menu(win, cfg, blocked=None, side="lower", num=None):
    """Меню по выборке окна поиска ({"rows", "a", "b"}). blocked — множество ключей или функция ключ -> bool (чёрный список); условие отсекается
    (BLOCKED), если в списке его семейство block_h (порог числового признака не важен: квантиль окна сдвигается) или точный cond_h (прежние записи).
    num — набор числовых признаков (None — MENU_NUM; иной — только проверка волны wave_check).
    -> {"items": [{"cond", "claim", "cond_h", "block_h", "n", "share", "tc"}], "k", "dropped": [{"cond", "code", ...}], "n_rows"}; K = len(items)."""
    p = VF.prepared(win)
    n_rows = len(p["m"])
    broad = float(cfg["trial"]["max_trade_loss_share"])
    min_n = int(cfg["verify"]["min_n_menu"])
    is_blocked = blocked if callable(blocked) else (lambda h, s=frozenset(blocked or ()): h in s)
    conds, skipped = conditions(p, cfg, num)
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


# ---------------------------------------------------------------- критерий волны признаков (R1.7, Requirement 15.7; задача 19)
#
# `./tb learn menu-wave --check <признаки>` (tb.knowledge.learn_cli): только чтение, вывод чисел. По каждому признаку волны на окне заполнения
# (последние `days` суток окна поиска; для первой волны — 7, Requirement 15.8): (а) заполнение среди заполненных прогонов P2 ≥ feat_cover_min;
# (б) q20 < q80; (в) |Спирмен| с каждым числовым признаком меню (и другими признаками волны) ≤ WAVE_RHO_MAX. (г) мощность конвейера медианного
# условия меню при истинном эффекте −WAVE_EFFECT_R по замеренным потоку и σ_d (метод задачи 9 спеки R2, design.md R2, «Замер на живых данных»)
# на окне поиска: меню с волной (K = K меню без волны + 2 × признаков волны — верхняя граница) ≥ WAVE_POWER_MIN и снижение относительно меню
# без волны ≤ WAVE_DROP_MAX; (г) оценивается только на установившемся окне (окно поиска не короче discovery_days), иначе None.
#
# Метод задачи 9: условия меню, вошедшие в K (не TOO_BROAD, не BLOCKED, n ≥ min_n_menu); по каждому — поток (кластеры теста в сутки окна),
# σ_d (разброс разностей кластеров), среднее базы (out_r); медианы по условиям. Мощность ступени — Φ(δ√n / σ_d − z(1 − α')):
#   скрининг: n = поток × discovery_days, α' = alpha / K, × вероятность знака в обеих половинах Φ(δ√(n/2) / σ_d)²; 0 при n < min_n_menu;
#   holdout:  n = поток × holdout_days, α' = alpha / labels_max_new_per_day; 0 при n < min_n_hyp;
#   испытание: n = n_target, α' = alpha / max_parallel, × P(среднее «в условии» ≤ −min_effect_r) при истинном среднем база − δ и σ прогона;
#             0, если n_target недостижим за reach_factor × max_days при потоке условия.
# Конвейер — произведение трёх ступеней.

WAVE_EFFECT_R = 0.3
WAVE_POWER_MIN = 0.5
WAVE_DROP_MAX = 0.05
WAVE_RHO_MAX = 0.7


def _ranks(xs):
    """Ранги с усреднением связок (1..n)."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for t in range(i, j + 1):
            r[order[t]] = avg
        i = j + 1
    return r


def spearman(xs, ys):
    """Корреляция Спирмена по парам, где оба значения — числа. -> (rho | None, n пар); меньше 3 пар или нет разброса -> None."""
    pairs = [(x, y) for x, y in zip(xs, ys) if is_num(x) and is_num(y)]
    n = len(pairs)
    if n < 3:
        return None, n
    rx, ry = _ranks([q[0] for q in pairs]), _ranks([q[1] for q in pairs])
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((u - mx) * (w - my) for u, w in zip(rx, ry))
    sxx = sum((u - mx) ** 2 for u in rx)
    syy = sum((w - my) ** 2 for w in ry)
    if sxx <= 0 or syy <= 0:
        return None, n
    return sxy / (sxx * syy) ** 0.5, n


def _stage(n, sd_d, effect, alpha_):
    import math
    if n <= 0 or not sd_d or sd_d <= 0:
        return 0.0
    return _phi(float(effect) * math.sqrt(n) / sd_d - _z(1.0 - float(alpha_)))


def pipeline_power(flow, sd_d, k, cfg, base_mean=None, sd_run=None, effect=WAVE_EFFECT_R):
    """Мощность конвейера условия по потоку (кластеров теста в сутки) и σ_d при истинном эффекте −effect (метод задачи 9 R2).
    -> {"screen", "holdout", "trial", "pipeline", "n_screen", "n_holdout"} (доли 0..1, округление 4)."""
    import math
    v, t = cfg["verify"], cfg["trial"]
    a = float(v["alpha"])
    flow = float(flow or 0.0)
    n_s = flow * float(v["discovery_days"])
    n_h = flow * float(v["holdout_days"])
    p_s = 0.0
    if n_s >= int(v["min_n_menu"]) and sd_d:
        p_s = _stage(n_s, sd_d, effect, a / max(1, int(k))) * _phi(float(effect) * math.sqrt(n_s / 2.0) / sd_d) ** 2
    p_h = _stage(n_h, sd_d, effect, a / int(v["labels_max_new_per_day"])) if n_h >= int(v["min_n_hyp"]) else 0.0
    n_t = int(t["n_target"])
    p_t = 0.0
    if flow * float(t["reach_factor"]) * float(t["max_days"]) >= n_t:
        p_t = _stage(n_t, sd_d, effect, a / max(1, int(t["max_parallel"])))
        s = float(sd_run) if sd_run else float(sd_d or 0.0)
        if s > 0:
            mu = float(base_mean or 0.0) - float(effect)
            p_t *= _phi((-float(t["min_effect_r"]) - mu) * math.sqrt(n_t) / s)
    return {"screen": round(p_s, 4), "holdout": round(p_h, 4), "trial": round(p_t, 4), "pipeline": round(p_s * p_h * p_t, 4),
            "n_screen": round(n_s, 1), "n_holdout": round(n_h, 1)}


def _median(xs):
    v = sorted(x for x in xs if x is not None)
    if not v:
        return None
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2.0


def _cond_stats(tcs, days):
    """Медианы по условиям: поток (кластеры теста в сутки), σ_d, среднее базы. tcs — списки кластеров теста условий."""
    from tb.learnloop import stat as ST
    flows = [len(tc) / days for tc in tcs] if days > 0 else []
    sds = [ST.sd([x["diff"] for x in tc]) for tc in tcs]
    bases = [ST.mean([x["out_r"] for x in tc]) for tc in tcs]
    return {"n_cond": len(tcs), "flow": _median(flows), "sd_d": _median(sds), "base_mean": _median(bases)}


def _r4(x):
    return None if x is None else round(float(x), 4)


def wave_check(win, cfg, feats, blocked=None, fill_days=None, side="lower", effect=WAVE_EFFECT_R):
    """Критерий волны (Requirement 15.7) по окну поиска win ({"rows", "a", "b"}) для признаков feats. fill_days — длина окна заполнения
    (последние сутки окна; None — всё окно), на нём (а), (б), (в); (г) — на всём окне. Только чтение.
    -> {"feats": {признак: {"cover", "n", "q20", "q80", "rho", "rho_max", "a", "b", "c"}}, "power": {"base", "wave", "drop", "steady", ...},
        "window": {...}, "ok_a", "ok_b", "ok_c", "ok_d" (None — окно не установившееся), "ok"}."""
    from tb.learnloop import stat as ST
    feats = tuple(dict.fromkeys(feats))
    cover_min = float(cfg["verify"]["feat_cover_min"])
    broad = float(cfg["trial"]["max_trade_loss_share"])
    min_n = int(cfg["verify"]["min_n_menu"])
    a, b = int(win["a"]), int(win["b"])
    days = (b - a) / 86400.0
    fa = a if not fill_days else max(a, b - int(float(fill_days) * 86400))
    pf = VF.prepared([r for r in win["rows"] if int(r["t0"]) >= fa])
    n_fill = len(pf["m"])
    others = tuple(f for f in MENU_NUM if f not in feats)
    out = {}
    for f in feats:
        col = VF.column(pf, f)
        xs = [x for x in col if is_num(x)]
        cover = len(xs) / float(n_fill) if n_fill else 0.0
        q20, q80 = (quantile(xs, Q_LO), quantile(xs, Q_HI)) if xs else (None, None)
        rho = {}
        for g in others + tuple(x for x in feats if x != f):
            r, n = spearman(col, VF.column(pf, g))
            rho[g] = {"rho": _r4(r), "n": n}
        vals = [abs(v["rho"]) for v in rho.values() if v["rho"] is not None]
        rmax = max(vals) if vals else None
        out[f] = {"cover": round(cover, 4), "n": n_fill, "q20": None if q20 is None else _qv(q20), "q80": None if q80 is None else _qv(q80),
                  "rho": rho, "rho_max": _r4(rmax), "a": cover >= cover_min, "b": q20 is not None and q20 < q80,
                  "c": rmax is None or rmax <= WAVE_RHO_MAX}
    # (г) мощность конвейера: меню без волны (K — фактическое) и меню с волной (K — верхняя граница K без волны + 2 × признаков)
    base = build_menu(win, cfg, blocked=blocked, side=side, num=others)
    p = VF.prepared(win)
    n_rows = len(p["m"])
    wave_tcs = []
    for f in feats:
        xs = [x for x in VF.column(p, f) if is_num(x)]
        if not xs:
            continue
        for op, q in ((">=", Q_HI), ("<=", Q_LO)):
            hits = VF.hit_vector(p, [{"feature": f, "op": op, "value": _qv(quantile(xs, q))}])
            share = sum(hits) / float(n_rows) if n_rows else 0.0
            tc = VF.tc_from_hits(p, hits)
            if share <= broad and len(tc) >= min_n:
                wave_tcs.append(tc)
    base_tcs = [it["tc"] for it in base["items"] if it.get("tc")]
    sd_run = ST.sd(p["r"])
    k_base = base["k"]
    k_wave = k_base + 2 * len(feats)
    sb = _cond_stats(base_tcs, days)
    sw = _cond_stats(base_tcs + wave_tcs, days)
    pb = pipeline_power(sb["flow"], sb["sd_d"], k_base, cfg, sb["base_mean"], sd_run, effect) if sb["flow"] else None
    pw = pipeline_power(sw["flow"], sw["sd_d"], k_wave, cfg, sw["base_mean"], sd_run, effect) if sw["flow"] else None
    steady = days >= float(cfg["verify"]["discovery_days"]) - 1e-9
    drop = round(pb["pipeline"] - pw["pipeline"], 4) if pb and pw else None
    ok_d = bool(pw and pw["pipeline"] >= WAVE_POWER_MIN and drop is not None and drop <= WAVE_DROP_MAX) if steady else None

    def _fmt(st, pp, k):
        return dict({"k": k, "n_cond": st["n_cond"], "flow": _r4(st["flow"]), "sd_d": _r4(st["sd_d"]), "base_mean": _r4(st["base_mean"])}, **(pp or {}))
    rep = {"feats": out,
           "window": {"a": a, "b": b, "days": round(days, 2), "fill_a": fa, "fill_days": round((b - fa) / 86400.0, 2), "n_rows": n_rows, "n_fill": n_fill},
           "power": {"base": _fmt(sb, pb, k_base), "wave": _fmt(sw, pw, k_wave), "drop": drop, "steady": steady, "effect_r": -float(effect),
                     "sd_run": _r4(sd_run), "n_wave_cond": len(wave_tcs)},
           "ok_a": all(v["a"] for v in out.values()), "ok_b": all(v["b"] for v in out.values()), "ok_c": all(v["c"] for v in out.values()),
           "ok_d": ok_d}
    rep["ok"] = bool(rep["ok_a"] and rep["ok_b"] and rep["ok_c"] and ok_d)
    return rep
