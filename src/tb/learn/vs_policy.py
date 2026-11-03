"""Блок «Мозг против теневой политики» страницы «Учится ли мозг» (R0.2, задача 9, Requirement 11).

Сравнивает средний R сделок мозга режима (закрытых за последние policy_days суток) со средним R_net текущей версии каждой включённой теневой политики
(config/shadow_policies.json). Критерий: непарный Уэлч по сводкам (tb.learn.stats.welch_summary), порог значимости alpha / K (Бонферрони, K - число
включённых политик реестра). Версии политик не смешиваются; при нескольких ctx_ver одной политики в окне берётся самый свежий (ctx_changed = True).
Результаты политик читаются только через tb.labels.query.policy_stats (open_ro), плюс один запрос свежей ctx_ver на политику; ничего не пишется.
Блок - доказательство, а не вход вердикта (Requirement 4.11). Нет базы / нет таблицы / пусто -> no_data, labels_off; плохой реестр -> error, E_REGISTRY.

build(ctx): ctx с атрибутами root, mode, now, cfg (конфиг learning.json), trades (сделки мозга режима из tb.learn.load.brain_trades; поле t_close).
"""
import sqlite3
import time

from tb.core import expstats
from tb.labels import policies, query, store
from tb.learn import conf
from tb.learn.stats import DAY, est, iso_epoch, mean_est, welch_summary

ID = "vs_policy"
TITLE = "Мозг против теневой политики"
BY = ("policy_id", "policy_ver", "ctx_ver")
CAVEATS = ("unpaired", "selection", "costs_model", "correlated")
REAL_MODES = ("testnet", "live")
LVL = 0.95
LABELS_OFF = "labels_off"
E_REGISTRY = "E_REGISTRY"
VERDICTS = ("better", "worse", "unclear", "few_data")
SUMMARIES = ("above_baseline", "below_baseline", "unclear", "few_data")
NOTES = {"ok": "Средний R мозга против средних R_net теневых политик за окно; сравнение без парности, с поправкой Бонферрони.",
         "few_data": "Данных мало: у мозга или у базовых политик меньше %d наблюдений." % expstats.MIN_N,
         "labels_off": "База меток пуста или недоступна (сервис labels не писал в неё).",
         "E_REGISTRY": "Реестр теневых политик не прошёл проверку."}


def _empty(status, reason, since, caveats, k=None, alpha_adj=None, brain=None):
    return {"id": ID, "title": TITLE, "status": status, "reason": reason, "note": NOTES.get(reason) or NOTES.get(status, ""),
            "since": since, "caveats": caveats, "k": k, "alpha_adj": alpha_adj,
            "brain": brain if brain is not None else {"n": 0, "r": est(None, n=0, lvl=LVL)}, "rows": [], "summary": "few_data"}


def _caveats(mode):
    return list(CAVEATS) + (["sim_vs_real"] if mode in REAL_MODES else [])


def _brain_r(trades, since):
    """R сделок мозга, закрытых не раньше since (эпоха). Нет t_close - берётся из ts_close; нет R или не разобралось время - сделка пропускается."""
    out = []
    for t in trades or []:
        r = t.get("r")
        if isinstance(r, bool) or not isinstance(r, (int, float)):
            continue
        tc = t.get("t_close")
        if tc is None:
            tc = iso_epoch(t.get("ts_close"))
        if tc is None or tc < since:
            continue
        out.append(float(r))
    return out


def current(pols):
    """{id: политика} - наибольший ver среди включённых."""
    cur = {}
    for p in policies.active(pols):
        if p["id"] not in cur or p["ver"] > cur[p["id"]]["ver"]:
            cur[p["id"]] = p
    return cur


def _fresh_ctx(root, cur, since):
    """{(id, ver): ctx_ver самой свежей строки окна}; один запрос на политику через store.open_ro. None - базы нет."""
    conn = store.open_ro(root)
    if conn is None:
        return None
    out = {}
    try:
        for p in cur.values():
            row = conn.execute("SELECT ctx_ver FROM policy_runs WHERE policy_id=? AND policy_ver=? AND t0>=? ORDER BY t0 DESC LIMIT 1",
                               (p["id"], p["ver"], since)).fetchone()
            if row:
                out[(p["id"], p["ver"])] = row[0]
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return out


def _pol_est(mean, se, n):
    """Оценка среднего R политики с t-интервалом уровня LVL по её стандартной ошибке."""
    if mean is None or not n:
        return est(None, n=n or 0, lvl=LVL)
    if se is None or n < 2:
        return est(mean, n=n, lvl=LVL)
    h = expstats.t_quantile(1.0 - (1.0 - LVL) / 2.0, n - 1) * se
    return est(mean, mean - h, mean + h, n, LVL)


def verdict_of(nb, nf, diff, p, alpha_adj):
    """Вердикт строки: few_data при нехватке наблюдений с любой стороны; better/worse только при p < alpha_adj; иначе unclear."""
    if nb < expstats.MIN_N or nf < expstats.MIN_N:
        return "few_data"
    if diff is None or p is None or not p < alpha_adj:
        return "unclear"
    return "better" if diff > 0 else ("worse" if diff < 0 else "unclear")


def summary_of(rows):
    """Итог по базовым политикам с достаточными данными (verdict != few_data)."""
    base = [r["verdict"] for r in rows if r["baseline"] and r["verdict"] != "few_data"]
    if not base:
        return "few_data"
    if "worse" in base:
        return "below_baseline"
    return "above_baseline" if all(v == "better" for v in base) else "unclear"


def _row(p, st, ctx_changed, brain, nb, k, alpha_adj, baseline):
    nf = int(st["filled"]) if st else 0
    mean = st["avg_r_net"] if st else None
    se = st["se_r"] if st else None
    w = welch_summary(brain["r"]["v"], brain["se"], nb, mean, se, nf, lvl=1.0 - alpha_adj) if nf and mean is not None else welch_summary(None, None, nb, None, None, nf)
    diff = est(w["diff"], w["lo"], w["hi"], nb + nf, 1.0 - alpha_adj)
    return {"id": p["id"], "ver": p["ver"], "name": p["name"], "baseline": baseline, "ctx_changed": ctx_changed,
            "n_runs": int(st["n"]) if st else 0, "n_filled": nf, "fill_rate": st["fill_rate"] if st else None,
            "r": _pol_est(mean, se, nf), "pf": st["pf"] if st else None, "pf_inf": bool(st and st["pf_inf"]),
            "diff": diff, "p": w["p"], "alpha_adj": alpha_adj, "k": k,
            "verdict": verdict_of(nb, nf, w["diff"], w["p"], alpha_adj)}


def compute(root, mode, now, cfg, trades):
    now = time.time() if now is None else now
    since = int(now - cfg["policy_days"] * DAY)
    caveats = _caveats(mode)
    try:
        pols = policies.load(root)
    except ValueError:
        return _empty("error", E_REGISTRY, since, caveats)
    act = policies.active(pols)
    cur = current(pols)
    k = max(1, len(act))
    alpha_adj = expstats.bonferroni(k)
    rs = _brain_r(trades, since)
    nb = len(rs)
    bm = mean_est(rs, LVL)
    brain = {"n": nb, "r": bm}
    try:
        stats = query.policy_stats(root, since, by=BY)
        fresh = _fresh_ctx(root, cur, since) if stats else None
    except sqlite3.DatabaseError:
        return _empty("no_data", LABELS_OFF, since, caveats, k, alpha_adj, brain)
    if not stats or fresh is None or not cur:
        return _empty("no_data", LABELS_OFF, since, caveats, k, alpha_adj, brain)
    # сводка мозга для Уэлча: среднее и стандартная ошибка
    se_b = None
    if nb > 1:
        sd = expstats._mean_sd(rs)[1]
        se_b = sd / (nb ** 0.5)
    bsum = {"r": bm, "se": se_b}
    base_ids = set(cfg["baseline_policies"])
    rows = []
    for pid in sorted(cur):
        p = cur[pid]
        mine = [s for s in stats if s["policy_id"] == pid and s["policy_ver"] == p["ver"]]
        want = fresh.get((pid, p["ver"]))
        pick = [s for s in mine if s["ctx_ver"] == want]
        if mine and not pick:                                    # гонка между двумя чтениями: самая многочисленная группа
            pick = [max(mine, key=lambda s: s["n"])]
        rows.append(_row(p, pick[0] if pick else None, len(mine) > 1, bsum, nb, k, alpha_adj, pid in base_ids))
    summ = summary_of(rows)
    status = "few_data" if summ == "few_data" else "ok"
    return {"id": ID, "title": TITLE, "status": status, "reason": None, "note": NOTES[status], "since": since, "caveats": caveats, "k": k,
            "alpha_adj": alpha_adj, "brain": brain, "rows": rows, "summary": summ}


def build(ctx):
    root = getattr(ctx, "root", None)
    cfg = getattr(ctx, "cfg", None) or conf.load(root)[0]
    return compute(root, getattr(ctx, "mode", "paper"), getattr(ctx, "now", None), cfg, getattr(ctx, "trades", None))
