"""Статистические примитивы страницы «Учится ли мозг» (R0.2, задача 1). Чистые функции: без файлов, времени и сети.

Точки (PF, средний R, доля издержек, захват) берутся из tb.core.expstats.group_stats, поэтому числа сходятся с `./tb exp stats`.
Нечисловые и бесконечные значения заменяются на None. MIN_N и ALPHA не настраиваются: берутся из expstats.
"""
import datetime
import math
import random
from statistics import NormalDist

from tb.core import expstats as X

_ND = NormalDist()
DAY = 86400


def _fin(v):
    """Конечное число (float) или None; bool и строки не числа."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _p_ok(lvl):
    lvl = _fin(lvl)
    return lvl if lvl is not None and 0.0 < lvl < 1.0 else 0.95


def est(v, lo=None, hi=None, n=0, lvl=0.95):
    """-> {"v","lo","hi","n","lvl"}; не конечные значения -> None; интервал либо оба конца, либо ни одного."""
    lo, hi = _fin(lo), _fin(hi)
    if lo is None or hi is None:
        lo = hi = None
    n = n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else 0
    return {"v": _fin(v), "lo": lo, "hi": hi, "n": n, "lvl": _p_ok(lvl)}


def mean_est(xs, lvl=0.95):
    """Среднее с t-интервалом: m ± t(1-(1-lvl)/2, n-1) * s/sqrt(n). Нечисловые значения отбрасываются; n < 2 -> без интервала."""
    xs = [x for x in (_fin(v) for v in (xs or [])) if x is not None]
    n = len(xs)
    if not n:
        return est(None, n=0, lvl=lvl)
    m, sd = X._mean_sd(xs)
    if sd is None:
        return est(m, n=n, lvl=lvl)
    lvl = _p_ok(lvl)
    h = X.t_quantile(1.0 - (1.0 - lvl) / 2.0, n - 1) * sd / math.sqrt(n)
    return est(m, m - h, m + h, n, lvl)


def wilson_est(k, n, lvl=0.95):
    """Доля k/n с интервалом Уилсона; n = 0 -> всё None. Доли 0 и 1 дают корректный интервал внутри [0, 1]."""
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0 or not isinstance(k, int) or isinstance(k, bool):
        return est(None, n=0, lvl=lvl)
    k = min(max(k, 0), n)
    lvl = _p_ok(lvl)
    z = _ND.inv_cdf(1.0 - (1.0 - lvl) / 2.0)
    p = k / n
    d = 1.0 + z * z / n
    c = (p + z * z / (2.0 * n)) / d
    h = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / d
    lo = 0.0 if k == 0 else max(0.0, c - h)              # на краях аналитически 0 и 1 (без плавающего шума)
    hi = 1.0 if k == n else min(1.0, c + h)
    return est(p, lo, hi, n, lvl)


def welch_summary(m1, se1, n1, m2, se2, n2, lvl=0.95):
    """Разность средних m1 - m2 по сводкам (среднее, стандартная ошибка, n), метод Уэлча-Саттертвейта.

    -> {"diff","se","df","t","p","lo","hi"}; p, df, t, lo, hi = None, если se == 0 или n < 2 (совпадает с expstats._welch)."""
    out = {"diff": None, "se": None, "df": None, "t": None, "p": None, "lo": None, "hi": None}
    m1, m2, se1, se2 = _fin(m1), _fin(m2), _fin(se1), _fin(se2)
    if m1 is None or m2 is None or se1 is None or se2 is None or se1 < 0 or se2 < 0:
        return out
    out["diff"] = m1 - m2
    if not (isinstance(n1, int) and isinstance(n2, int)) or n1 < 2 or n2 < 2:
        return out
    va, vb = se1 * se1, se2 * se2
    se = math.sqrt(va + vb)
    out["se"] = se
    if se <= 0:
        return out
    df = (va + vb) ** 2 / (va * va / (n1 - 1) + vb * vb / (n2 - 1))
    t = out["diff"] / se
    out["df"], out["t"], out["p"] = _fin(df), _fin(t), _fin(X.t_two_sided_p(t, df))
    lvl = _p_ok(lvl)
    q = X.t_quantile(1.0 - (1.0 - lvl) / 2.0, df)
    out["lo"], out["hi"] = _fin(out["diff"] - q * se), _fin(out["diff"] + q * se)
    return out


def two_prop(k1, n1, k2, n2, one_sided=None):
    """Двухвыборочный z-критерий пропорций с объединённой долей. one_sided: "greater" (p1 > p2) | "less" | None (двусторонний).

    -> {"p1","p2","z","p"}; n1 или n2 = 0 -> всё None; одинаковая доля 0 или 1 (нулевая дисперсия) -> z = 0, p = 1 (или 0.5 односторонний)."""
    out = {"p1": None, "p2": None, "z": None, "p": None}
    ok = all(isinstance(v, int) and not isinstance(v, bool) for v in (k1, n1, k2, n2))
    if not ok or n1 <= 0 or n2 <= 0 or not 0 <= k1 <= n1 or not 0 <= k2 <= n2:
        return out
    p1, p2 = k1 / n1, k2 / n2
    pp = (k1 + k2) / (n1 + n2)
    out["p1"], out["p2"] = p1, p2
    se = math.sqrt(pp * (1.0 - pp) * (1.0 / n1 + 1.0 / n2))
    if se <= 0:
        z = 0.0
    else:
        z = (p1 - p2) / se
    out["z"] = z
    if one_sided == "greater":
        out["p"] = 1.0 - _ND.cdf(z)
    elif one_sided == "less":
        out["p"] = _ND.cdf(z)
    else:
        out["p"] = min(1.0, 2.0 * (1.0 - _ND.cdf(abs(z))))
    return out


def _pf(pairs):
    gw = sum(a for a, _ in pairs)
    gl = sum(b for _, b in pairs)
    if gl > 0:
        return gw / gl
    return math.inf if gw > 0 else None


def pf_est(pnls, level, b, seed):
    """PF по сумме (прибыль / убыток) с интервалом уровня `level`, бутстреп B повторов пар «прибыль, убыток» на random.Random(seed).

    -> {"v","lo","hi","n","lvl","pf_inf"}. Нет убытков и есть прибыль: pf_inf = True, v = hi = None."""
    xs = [x for x in (_fin(v) for v in (pnls or [])) if x is not None]
    level = _p_ok(level)
    n = len(xs)
    out = est(None, n=n, lvl=level)
    out["pf_inf"] = False
    if not n:
        return out
    pairs = [(x if x > 0 else 0.0, -x if x <= 0 else 0.0) for x in xs]
    point = _pf(pairs)
    if point == math.inf:
        out["pf_inf"] = True
    else:
        out["v"] = _fin(point)
    b = b if isinstance(b, int) and not isinstance(b, bool) and b > 0 else 0
    if not b or n < 2:
        return out
    rng = random.Random(seed)
    vals = []
    for _ in range(b):
        s = _pf([pairs[rng.randrange(n)] for _ in range(n)])
        if s is not None:
            vals.append(s)                       # inf остаётся: сортируется в конец
    if not vals:
        return out
    vals.sort()
    a = (1.0 - level) / 2.0
    lo = vals[min(len(vals) - 1, max(0, int(math.floor(a * len(vals)))))]
    hi = vals[min(len(vals) - 1, max(0, int(math.ceil((1.0 - a) * len(vals))) - 1))]
    out["lo"] = _fin(lo)
    out["hi"] = _fin(hi) if not out["pf_inf"] else None
    if out["lo"] is None:
        out["hi"] = None
    return out


def window_row(trades):
    """Одна строка expstats.group_stats для среза (приём exp_detail: фиксированный setup и режим) -> плоский словарь.

    Ключи: n, n_r, wins, avg_r, sd_r, se_r, pf, pf_inf, win_rate, fees_share, mfe_capture, small. Пустой срез: n = 0, остальное None."""
    ts = [dict(t, setup="_w", mode="_w") for t in (trades or [])]
    rows = X.group_stats(ts, by="setup") if ts else []
    keys = ("n", "n_r", "wins", "avg_r", "sd_r", "se_r", "pf", "pf_inf", "win_rate", "fees_share", "mfe_capture", "small")
    if not rows:
        return {"n": 0, "n_r": 0, "wins": 0, "avg_r": None, "sd_r": None, "se_r": None, "pf": None, "pf_inf": False,
                "win_rate": None, "fees_share": None, "mfe_capture": None, "small": True}
    r = rows[0]
    return {k: (r[k] if k in ("n", "n_r", "wins", "pf_inf", "small") else _fin(r[k])) for k in keys}


def _pick(count, max_points):
    """Индексы 0..count-1: все, если помещаются, иначе равномерно с сохранением первого и последнего."""
    if count <= max_points:
        return list(range(count))
    if max_points <= 1:
        return [count - 1]
    idx = [round(i * (count - 1) / (max_points - 1)) for i in range(max_points)]
    out = []
    for i in idx:
        if not out or i != out[-1]:
            out.append(i)
    return out


def rolling(trades, w, fn, min_points=30, max_points=120):
    """Ряд по порядковому номеру сделки: точка i (1-based) = fn(trades[max(0, i-w):i]) для i от min_points; окно растёт до w.

    -> [{"i": номер сделки, "v": fn(...)}]; больше max_points точек прореживается равномерно, последняя точка сохраняется."""
    trades = list(trades or [])
    n = len(trades)
    if not isinstance(w, int) or w < 1:
        return []
    first = max(1, int(min_points))
    if n < first:
        return []
    return [{"i": first + j, "v": fn(trades[max(0, first + j - w):first + j])} for j in _pick(n - first + 1, max(1, int(max_points)))]


# ---------------------------------------------------------------- время (UTC)

def iso_week(epoch):
    """Эпоха (секунды) -> "2026-W44" (ISO-неделя UTC)."""
    y, w, _ = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isocalendar()
    return "%04d-W%02d" % (y, w)


def week_range(asof, n):
    """Последние n ISO-недель UTC, включая неделю asof (последняя, partial = True), по возрастанию без пропусков.

    -> [{"week","start","end","partial"}]; start/end — эпоха понедельника 00:00 UTC и следующего понедельника (конец не включён)."""
    d = datetime.datetime.fromtimestamp(asof, datetime.timezone.utc)
    monday = datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc) - datetime.timedelta(days=d.weekday())
    out = []
    for k in range(max(0, int(n)) - 1, -1, -1):
        s = monday - datetime.timedelta(days=7 * k)
        se = int(s.timestamp())
        out.append({"week": iso_week(se), "start": se, "end": se + 7 * DAY, "partial": k == 0})
    return out


def iso_epoch(s):
    """"2026-10-27T08:59:35Z" (также со смещением или без) -> секунды UTC; не разбирается -> None."""
    if not isinstance(s, str) or not s:
        return None
    try:
        d = datetime.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return int(d.timestamp())
