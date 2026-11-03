"""Статистика цикла обучения (R2, Requirements 5.3, 5.5; design.md, «Единица наблюдения»): парный t-тест разностей кластеров и t-тест против
уровня (только для сторожа). Чистые функции, без ввода-вывода и случайных величин; p двустороннее — `expstats.t_two_sided_p(t, df)`.

Одностороннее p в направлении claim: side="lower" — статистика должна быть отрицательной, side="higher" — положительной; p = p2 / 2, если знак
совпал, иначе 1 − p2 / 2. Нулевой разброс: среднее 0 -> p2 = 1; иначе |t| = inf, p2 = 0. Меньше двух значений -> None.
"""
import math

from tb.core.expstats import t_two_sided_p

SIDES = ("lower", "higher")


def _finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def sd(xs):
    """Выборочное стандартное отклонение (n − 1); меньше двух значений -> None."""
    xs = list(xs)
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    return math.sqrt(max(0.0, sum((x - m) ** 2 for x in xs) / (n - 1)))


def t_stat(xs, mu0=0.0):
    """-> {"n", "mean", "sd", "se", "t", "df", "p2"}; n < 2 -> t, p2 = None. Нечисла отклоняются (ValueError)."""
    xs = list(xs)
    if not all(_finite(x) for x in xs):
        raise ValueError("t_stat: только конечные числа")
    n = len(xs)
    m = mean(xs)
    s = sd(xs)
    out = {"n": n, "mean": m, "sd": s, "se": None, "t": None, "df": n - 1 if n else 0, "p2": None}
    if n < 2:
        return out
    se = s / math.sqrt(n)
    out["se"] = se
    dev = m - mu0
    if se == 0.0:
        if dev == 0.0:
            out["t"], out["p2"] = 0.0, 1.0
        else:
            out["t"], out["p2"] = math.copysign(math.inf, dev), 0.0
        return out
    t = dev / se
    out["t"] = t
    out["p2"] = t_two_sided_p(t, n - 1)
    return out


def one_sided(t, p2, side):
    """Одностороннее p по двустороннему p2 и знаку t в направлении side ("lower" | "higher")."""
    if side not in SIDES:
        raise ValueError("side: lower | higher")
    if t is None or p2 is None:
        return None
    hit = t < 0 if side == "lower" else t > 0
    return p2 / 2.0 if hit else 1.0 - p2 / 2.0


def paired_pvalue(diffs, side="lower"):
    """Парный тест: одновыборочный t разностей in_r − out_r против нуля, df = n − 1, одностороннее p в направлении side. n < 2 -> None."""
    st = t_stat(diffs, 0.0)
    return one_sided(st["t"], st["p2"], side)


def paired(diffs, side="lower"):
    """Числа парного теста: {"n", "mean", "sd", "se", "t", "df", "p2", "p"}."""
    st = t_stat(diffs, 0.0)
    st["p"] = one_sided(st["t"], st["p2"], side)
    return st


def t1_pvalue(xs, mu0, side):
    """Только для сторожа: одновыборочный t против уровня mu0, одностороннее p в направлении side. n < 2 -> None."""
    st = t_stat(xs, mu0)
    return one_sided(st["t"], st["p2"], side)
