"""Проверка кандидатов кодом (R2, Requirements 5.3, 5.4, 5.5, 5.13; design.md, «Меню, скрининг и holdout»). Чистые функции над строками выборки окна.

Кластер теста — кластер, где есть и прогоны «в условии» (hit=1), и «вне условия» (hit=0); значение кластера — среднее R после издержек каждой
группы, разность diff = in_r − out_r. n — число кластеров теста. Порядок — (t0 часа, ключ кластера). Тест только парный (stat.paired).
Скрининг: n ≥ min_n_menu, средняя разность в направлении claim в обеих половинах окна, одностороннее p < alpha / K.
Holdout: n ≥ min_n_hyp (иначе N_TOO_SMALL), тот же знак (иначе NO_OOS_SIGN), p < alpha / K_h, K_h = labels_max_new_per_day (иначе NOT_SIGNIFICANT).
Ключ DUP_ACTIVE — tb.logic.learn_rules.cond_h (16 hex, canon_cond), общий с правилами и версиями; ключ чёрного списка — learn_rules.block_h
(семейство условия: числовые признаки без порога, категориальные со значением).

Подготовка `prepared(win)` (строки метрик, индексы кластеров, столбцы признаков) кешируется в словаре окна под ключом "_prep": меню из 17 условий
и симуляции считают попадания столбцами, с той же семантикой, что learn_cond.cmp_val / matches_features.
"""
from tb.learnloop import clusters as CL
from tb.learnloop import stat as ST
from tb.logic.learn_cond import cmp_val, matches_features

HOLDOUT_DROP = ("NO_OOS_SIGN", "NOT_SIGNIFICANT", "N_TOO_SMALL")


# ---------------------------------------------------------------- подготовка и попадания

def prepared(src):
    """Окно ({"rows", ...}, кеш "_prep") или список строк -> {"m", "cid", "names", "hours", "r", "cols"}."""
    if isinstance(src, dict):
        p = src.get("_prep")
        if p is None:
            p = src["_prep"] = _prepare(src["rows"])
        return p
    return _prepare(src)


def _prepare(rows):
    m = CL.metric_rows(rows)
    names = sorted(set(r["cluster"] for r in m), key=lambda c: (int(c.split(":", 1)[0]), c))
    idx = dict((c, i) for i, c in enumerate(names))
    return {"m": m, "cid": [idx[r["cluster"]] for r in m], "names": names, "hours": [int(c.split(":", 1)[0]) for c in names],
            "r": [float(r["r_net"]) for r in m], "cols": {}}


def column(p, feature):
    c = p["cols"].get(feature)
    if c is None:
        c = p["cols"][feature] = [r["feats"].get(feature) for r in p["m"]]
    return c


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def hit_vector(p, cond):
    """Попадания условия по строкам метрик (семантика matches_features: отсутствующий признак условие не выполняет)."""
    if len(cond) != 1:
        return [matches_features(cond, r["feats"]) for r in p["m"]]
    c = cond[0]
    col, op, v = column(p, c["feature"]), c["op"], c["value"]
    if op == ">=":
        return [_num(x) and x >= v for x in col]
    if op == "<=":
        return [_num(x) and x <= v for x in col]
    return [x is not None and cmp_val(op, v, x) for x in col]


def _rule_cond(r):
    return (r.get("cond") or []) if isinstance(r, dict) else r


def tc_from_hits(p, hits, keep=None):
    """Кластеры теста по вектору попаданий (keep — вектор прогонов, прошедших активный набор). -> список по (t0, кластер)."""
    k = len(p["names"])
    si, ni, so, no = [0.0] * k, [0] * k, [0.0] * k, [0] * k
    if keep is None:
        for c, h, r in zip(p["cid"], hits, p["r"]):
            if h:
                si[c] += r
                ni[c] += 1
            else:
                so[c] += r
                no[c] += 1
    else:
        for c, h, r, kp in zip(p["cid"], hits, p["r"], keep):
            if not kp:
                continue
            if h:
                si[c] += r
                ni[c] += 1
            else:
                so[c] += r
                no[c] += 1
    out = []
    for c in range(k):
        if ni[c] and no[c]:
            ir, orr = si[c] / ni[c], so[c] / no[c]
            out.append({"cluster": p["names"][c], "t0": p["hours"][c] * CL.HOUR, "in_r": ir, "out_r": orr, "diff": ir - orr, "n_in": ni[c], "n_out": no[c]})
    return out


def pass_active(p, active):
    """Вектор прогонов, которые не отсёк ни один из правил active (условия skip_when или {"cond"}); пустой набор -> None."""
    conds = [c for c in (_rule_cond(r) for r in active or ()) if c]
    if not conds:
        return None
    cut = [False] * len(p["m"])
    for c in conds:
        cut = [a or b for a, b in zip(cut, hit_vector(p, c))]
    return [not x for x in cut]


def test_clusters(src, cond, active=()):
    """Кластеры теста условия cond по выборке окна (окно или строки). active — набор правил: прогоны, отсечённые хотя бы одним, исключаются до
    разбиения. -> [{"cluster", "t0", "in_r", "out_r", "diff", "n_in", "n_out"}] по (t0, cluster)."""
    p = prepared(src)
    return tc_from_hits(p, hit_vector(p, cond), pass_active(p, active))


# ---------------------------------------------------------------- числа и тесты

def effect(tc):
    """Модуль средней разности по кластерам теста, R: округление до 0.01, не больше 10."""
    if not tc:
        return 0.0
    m = sum(x["diff"] for x in tc) / len(tc)
    return round(min(abs(m), 10.0), 2)


def alpha(cfg):
    return float(cfg["verify"]["alpha"])


def k_h(cfg):
    """K_h фиксирован: не больше стольких кандидатов в сутки начинают holdout."""
    return int(cfg["verify"]["labels_max_new_per_day"])


def _sign_ok(m, side):
    return m is not None and (m < 0 if side == "lower" else m > 0)


def _r(x, nd=6):
    return None if x is None else round(float(x), nd)


def numbers(tc, side="lower"):
    """Числа парного теста по кластерам теста: n, diff, sd, se, t, p (одностороннее), p2."""
    st = ST.paired([x["diff"] for x in tc], side)
    return {"n": st["n"], "diff": _r(st["mean"]), "sd": _r(st["sd"]), "se": _r(st["se"]), "t": _r(st["t"]), "p": _r(st["p"], 10), "p2": _r(st["p2"], 10)}


def halves(win, tc):
    """Средняя разность в первой и второй половине окна [a, b) (граница — час середины)."""
    a, b = win["a"], win["b"]
    mid = a + (b - a) // 2 // CL.HOUR * CL.HOUR
    h1 = [x["diff"] for x in tc if x["t0"] < mid]
    h2 = [x["diff"] for x in tc if x["t0"] >= mid]
    return ST.mean(h1), ST.mean(h2)


def screen(win, cond, k, cfg, side="lower", tc=None):
    """Скрининг на окне поиска (win: {"rows", "a", "b"}). -> {"ok", "code", "n", "diff", "se", "p", "thr", "k", "halves", ...};
    code: BELOW_N | NO_OOS_SIGN | NOT_SIGNIFICANT | None (провал скрининга кандидатом не становится и в чёрный список не пишется)."""
    tc = test_clusters(win, cond) if tc is None else tc
    nums = numbers(tc, side)
    thr = alpha(cfg) / max(1, int(k))
    h1, h2 = halves(win, tc)
    out = dict(nums, thr=thr, k=int(k), halves=[_r(h1), _r(h2)], ok=False, code=None)
    if nums["n"] < int(cfg["verify"]["min_n_menu"]):
        out["code"] = "BELOW_N"
    elif not (_sign_ok(h1, side) and _sign_ok(h2, side)):
        out["code"] = "NO_OOS_SIGN"           # Requirement 5, Edge Cases: эффект пропал в одной половине окна поиска
    elif nums["p"] is None or not nums["p"] < thr:
        out["code"] = "NOT_SIGNIFICANT"
    else:
        out["ok"] = True
    return out


def holdout(win, cond, kh, cfg, side="lower"):
    """Один тест holdout (вызывается не раньше cut_ts окна holdout). -> ("hyp" | "dropped", код | None, числа)."""
    tc = test_clusters(win, cond)
    nums = numbers(tc, side)
    thr = alpha(cfg) / max(1, int(kh))
    nums.update(thr=thr, k_h=int(kh), clusters=[x["cluster"] for x in tc[:5]])
    if nums["n"] < int(cfg["verify"]["min_n_hyp"]):
        return "dropped", "N_TOO_SMALL", nums
    if not _sign_ok(nums["diff"], side):
        return "dropped", "NO_OOS_SIGN", nums
    if nums["p"] is None or not nums["p"] < thr:
        return "dropped", "NOT_SIGNIFICANT", nums
    return "hyp", None, nums
