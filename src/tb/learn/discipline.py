"""Блок «Дисциплина» страницы «Учится ли мозг» (R0.2, задача 7, требование 6). Чистая функция: файлов не читает.

ctx (любой объект с атрибутами): now (секунды эпохи), cfg (tb.learn.conf), cases (закрытые сделки-случаи режима, load.cases).
Случай «неизвестный rule_followed» (None или "na") входит в n_cases и err_*, но не в знаменатель viol.
Теги ошибок: значения словаря памяти, обрезаны до 32 символов; "good" и "good-process-bad-luck" ошибками не считаются (как в memory/lessons.py).
"""
from tb.core import expstats as X
from tb.learn import stats

ID, TITLE = "discipline", "Дисциплина"
TAG_LEN = 32
NOT_ERR = ("good", "good-process-bad-luck")
KNOWN_RF = ("yes", "no", "partial")
HALF = 4                                                 # недель в каждой половине сравнения
MIN_PART = 30                                            # случаев с известным rule_followed в каждой половине
NOTE = "Доля случаев с нарушением правил (rule_followed = no) и доля случаев с ошибкой по неделям UTC; неизвестная отметка в долю нарушений не входит."


def _tags(c):
    """Уникальные теги ошибок случая (строки, обрезанные до TAG_LEN, без «хороших»)."""
    t = (c.get("tags") or {}).get("err")
    out = []
    if isinstance(t, list):
        for x in t:
            if isinstance(x, str) and x and x not in NOT_ERR:
                x = x[:TAG_LEN]
                if x not in out:
                    out.append(x)
    return out


def _rf(c):
    v = (c.get("tags") or {}).get("rule_followed")
    return v if v in KNOWN_RF else None


def _top(counter, k):
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:k]


def _week_of(wr, epoch):
    for i, w in enumerate(wr):
        if w["start"] <= epoch < w["end"]:
            return i
    return None


def signal_for(weeks):
    """4 последние недели против 4 предыдущих по доле нарушений: falling | rising | flat | None (мало случаев с известным rule_followed)."""
    if len(weeks) < 2:
        return None
    recent = weeks[-HALF:]
    prev = weeks[max(0, len(weeks) - 2 * HALF):len(weeks) - len(recent)]
    n1, k1 = sum(w["n_rf"] for w in recent), sum(w["_k_no"] for w in recent)
    n0, k0 = sum(w["n_rf"] for w in prev), sum(w["_k_no"] for w in prev)
    if n1 < MIN_PART or n0 < MIN_PART:
        return None
    r = stats.two_prop(k1, n1, k0, n0)
    if r["p"] is not None and r["p"] < X.ALPHA:
        return "falling" if r["p1"] < r["p2"] else "rising"
    return "flat"


def build(ctx):
    wr = stats.week_range(ctx.now, ctx.cfg["weeks"])
    buckets = [[] for _ in wr]
    for c in (getattr(ctx, "cases", None) or []):
        if not isinstance(c, dict):
            continue
        e = stats.iso_epoch(c.get("ts"))
        i = _week_of(wr, e) if e is not None else None
        if i is not None:
            buckets[i].append(c)
    total = sum(len(b) for b in buckets)
    out = {"id": ID, "title": TITLE, "note": NOTE, "weeks": [], "err_total": [], "n_cases": total, "signal": None}
    if not total:
        out.update(status="no_data", reason="no_cases")
        return out
    out["status"] = "ok" if total >= MIN_PART else "few_data"
    out["reason"] = None if total >= MIN_PART else "few_cases"
    allc = {}
    rows = []
    for w, b in zip(wr, buckets):
        k_no = sum(1 for c in b if _rf(c) == "no")
        n_rf = sum(1 for c in b if _rf(c) is not None)
        cnt = {}
        n_err = 0
        for c in b:
            ts = _tags(c)
            n_err += 1 if ts else 0
            for t in ts:
                cnt[t] = cnt.get(t, 0) + 1
                allc[t] = allc.get(t, 0) + 1
        rows.append({"week": w["week"], "partial": w["partial"], "n_cases": len(b), "n_rf": n_rf, "_k_no": k_no,
                     "viol": stats.wilson_est(k_no, n_rf), "err_any": stats.wilson_est(n_err, len(b)),
                     "err_top": [[t, n] for t, n in _top(cnt, 3)]})
    out["signal"] = signal_for(rows)
    for r in rows:
        r.pop("_k_no")
    out["weeks"] = rows
    out["err_total"] = [{"tag": t, "n": n, "share": stats.wilson_est(n, total)} for t, n in _top(allc, 8)]
    return out
