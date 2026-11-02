"""Вердикт «учится ли мозг» (R0.2, задача 4). Чистая функция: без файлов, сети и модели.

Три состояния: few_data < no_difference < confirmed. Пороги: expstats.MIN_N, expstats.ALPHA, K = 1 (одна гипотеза), из конфига только
recent_n и guard_trades_ratio. Тексты формирует код по таблице шаблонов TEXTS (ключ (state, reason)), числа берутся из полей вердикта.
Вердикт не зависит от блоков «против политики», «экономика», «память против базы».
"""
import math

from tb.core import expstats as X
from tb.learn import stats as S

K = 1
VIOL_MIN = 10            # меньше известных rule_followed в любой из частей -> защита не блокирует, guard_unknown
VIOL_P = 0.05            # одностороннее p защиты по нарушениям
STATES = ("few_data", "no_difference", "confirmed")
RANK = {s: i for i, s in enumerate(STATES)}
DEFAULT_RECENT_N = 50
DEFAULT_RATIO = 0.5

TEXTS = {
    ("few_data", "n_lt_min"): "Сделок {n}: данных мало, нужно хотя бы {min_n} (ещё {need}), чтобы оценивать.",
    ("no_difference", "no_baseline"): "Сделок {n}: сравнивать недавние с прежними нечем, нужно {need2} (ещё {need}).",
    ("no_difference", "guard_viol"): "Сделок {n}: изменение R {diff} (интервал {lo}..{hi}), нарушений правил стало больше, улучшение не засчитано.",
    ("no_difference", "guard_trades"): "Сделок {n}: изменение R {diff} (интервал {lo}..{hi}), сделок в сутки стало слишком мало, улучшение не засчитано.",
    ("no_difference", "not_significant"): "Сделок {n}: разницы не видно, изменение R {diff} (интервал {lo}..{hi}) не доказано.",
    ("confirmed", "improved"): "Сделок {n}: улучшение подтверждено, R вырос на {diff} (интервал {lo}..{hi}).",
}
TEXT_DOWN = " Внимание: стало хуже."
TEXT_MAX = 160


def _fin(v):
    return S._fin(v)


def _fmt(v):
    v = _fin(v)
    return "н/д" if v is None else ("%+.2f" % v).replace(".", ",")


def _mean_se(xs):
    n = len(xs)
    m, sd = X._mean_sd(xs)
    se = None if sd is None else sd / math.sqrt(n)
    return m, (0.0 if se is None else se)


def _t_close(t):
    v = _fin(t.get("t_close"))
    if v is None:
        v = S.iso_epoch(t.get("ts_close"))
    return v or 0


def _t_open(t):
    v = S.iso_epoch(t.get("ts_open"))
    return v if v is not None else _t_close(t)


def _per_day(seg):
    """Сделок в сутки: n / max(1, число полных суток между первым и последним закрытием сегмента)."""
    if not seg:
        return 0.0
    ts = [_t_close(t) for t in seg]
    days = int((max(ts) - min(ts)) // S.DAY)
    return len(seg) / max(1, days)


def _guard_viol(cases, t_cut):
    """Доля rule_followed = no среди известных (yes/no/partial): до t_cut и с t_cut. Провал: доля недавних больше и p (односторонний) < 0.05."""
    k_new = n_new = k_old = n_old = 0
    for c in cases or []:
        rf = ((c.get("tags") or {}).get("rule_followed")) if isinstance(c, dict) else None
        if rf not in ("yes", "no", "partial"):
            continue
        ts = S.iso_epoch(c.get("ts"))
        if ts is None:
            continue
        if ts >= t_cut:
            n_new += 1
            k_new += rf == "no"
        else:
            n_old += 1
            k_old += rf == "no"
    g = {"ok": True, "guard_unknown": False, "share_recent": None, "share_prev": None, "n_recent": n_new, "n_prev": n_old, "p": None}
    if n_new < VIOL_MIN or n_old < VIOL_MIN:
        g["guard_unknown"] = True
        if n_new and n_old:
            g["share_recent"], g["share_prev"] = k_new / n_new, k_old / n_old
        return g
    tp = S.two_prop(k_new, n_new, k_old, n_old, one_sided="greater")
    g["share_recent"], g["share_prev"], g["p"] = tp["p1"], tp["p2"], tp["p"]
    g["ok"] = not (tp["p1"] > tp["p2"] and tp["p"] is not None and tp["p"] < VIOL_P)
    return g


def _cfg(cfg):
    v = (cfg or {}).get("verdict") or {}
    rn = v.get("recent_n", DEFAULT_RECENT_N)
    rn = rn if isinstance(rn, int) and not isinstance(rn, bool) and rn >= X.MIN_N else DEFAULT_RECENT_N
    ra = _fin(v.get("guard_trades_ratio", DEFAULT_RATIO))
    ra = ra if ra is not None and 0.0 <= ra <= 1.0 else DEFAULT_RATIO
    return rn, ra


def _text(state, reason, v, trend):
    n = v["n"]
    d = v["diff_r"] or {}
    s = TEXTS[(state, reason)].format(n=n, min_n=X.MIN_N, need=max(0, (X.MIN_N if state == "few_data" else 2 * X.MIN_N) - n),
                                      need2=2 * X.MIN_N, diff=_fmt(d.get("v")), lo=_fmt(d.get("lo")), hi=_fmt(d.get("hi")))
    if trend == "down":
        s += TEXT_DOWN
    return s[:TEXT_MAX]


def decide(trades, cases_all, cfg, now=None):
    """-> verdict: state, reason, text, n, n_recent, n_prev, diff_r (est), p, alpha_adj, trend, guards {viol, trades_day}, params.

    trades: сделки мозга (поле r, t_close/ts_close, ts_open), порядок восстанавливается по (закрытие, id); сделки без конечного R не считаются.
    cases_all: случаи режима (tags.rule_followed, ts). now не используется (вердикт не зависит от текущего времени)."""
    recent_n, ratio = _cfg(cfg)
    ts = [t for t in (trades or []) if isinstance(t, dict) and _fin(t.get("r")) is not None]
    ts.sort(key=lambda t: (_t_close(t), str(t.get("id"))))
    n = len(ts)
    alpha_adj = X.ALPHA / K
    params = {"min_n": X.MIN_N, "alpha": X.ALPHA, "k": K, "recent_n": recent_n, "guard_trades_ratio": ratio}
    v = {"state": "few_data", "reason": "n_lt_min", "text": "", "n": n, "n_recent": 0, "n_prev": 0, "diff_r": None, "p": None,
         "alpha_adj": alpha_adj, "trend": None, "guards": {"viol": None, "trades_day": None}, "params": params}
    if n < X.MIN_N:
        v["text"] = _text("few_data", "n_lt_min", v, None)
        return v
    if n < 2 * X.MIN_N:
        v["state"], v["reason"] = "no_difference", "no_baseline"
        v["text"] = _text("no_difference", "no_baseline", v, None)
        return v
    n_recent = min(recent_n, n - X.MIN_N)
    recent, prev = ts[-n_recent:], ts[:-n_recent]
    m1, se1 = _mean_se([float(t["r"]) for t in recent])
    m2, se2 = _mean_se([float(t["r"]) for t in prev])
    lvl = 1.0 - alpha_adj
    w = S.welch_summary(m1, se1, len(recent), m2, se2, len(prev), lvl=lvl)
    sig = w["p"] is not None and w["p"] < alpha_adj
    diff = w["diff"]
    gv = _guard_viol(cases_all, _t_open(recent[0]))
    tr, tp = _per_day(recent), _per_day(prev)
    gt = {"ok": tr >= ratio * tp, "recent": tr, "prev": tp, "ratio": (tr / tp) if tp > 0 else None, "min_ratio": ratio}
    v.update(n_recent=len(recent), n_prev=len(prev), diff_r=S.est(diff, w["lo"], w["hi"], len(recent), lvl), p=w["p"])
    v["guards"] = {"viol": gv, "trades_day": gt}
    v["trend"] = "down" if sig and diff < 0 else "up" if sig and diff > 0 else "flat"
    if diff > 0 and sig and gv["ok"] and gt["ok"]:
        state, reason = "confirmed", "improved"
    else:
        state = "no_difference"
        reason = "guard_viol" if not gv["ok"] else "guard_trades" if not gt["ok"] else "not_significant"
    v["state"], v["reason"] = state, reason
    v["text"] = _text(state, reason, v, v["trend"])
    return v
