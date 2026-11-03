"""Справка меток для гипотез (R2, задача 36; Requirements 19.5, 19.7; design.md, «Справка меток»).

Шаг цикла `hypref` (раз в час, intervals.hypref_s): для каждого семейства из config/hypotheses.json: policy_map ({policy, kinds}) по строкам
policy_runs текущей включённой версии политики (config/shadow_policies.json) за последние hypref.window_days суток (14) считает
  n       число кластеров (час t0 // 3600, сторона входа);
  mean    среднее значений кластеров (значение кластера — средний r_net его заполненных прогонов, без флагов gap/halt);
  lo, hi  95% t-интервал среднего по значениям кластеров (n < 2 -> None);
отдельно по env_norm сигнала (clusters.env_norm: mainnet | testnet). Результат атомарно пишется в runtime/learn/hyp_ref.json.

Это единственное явное исключение из правила «расчёты цикла только по cluster_runs» (Requirement 5.14): справка решений не порождает,
статус гипотез (confirmed), ранг карточек, правила, guard и размер не трогает — ориентир для модели и владельца. База читается отдельным
соединением только для чтения на один вызов (labels.store.open_ro, Requirement 2.11), соединение цикла не используется. Читатели файла —
мозг (досье experiments, `./tb exp`) и страница (tb.learn.stages.hyps) — только читают его и tb.learnloop не импортируют.

Форма файла (tb.hyp_ref/1):
  {"schema", "ts", "window_days", "a", "b",
   "families": {семейство: {"policy", "policy_ver", "kinds",
                            "env": {"mainnet"|"testnet": {"n", "n_runs", "mean", "lo", "hi"}}}}}
Семейство без записи policy_map в файл не попадает; политика не найдена или выключена — семейство пропускается.
Модуль не импортирует brain/gateway/llm/venues/executor/control.
"""
import math

from tb import paths
from tb.core import expstats as X
from tb.core import hyps
from tb.knowledge import markers as KM
from tb.labels import store as LST
from tb.learnloop import clusters as CL

SCHEMA = "tb.hyp_ref/1"
DAY = 86400
HOUR = 3600
LVL = 0.95
ENVS = ("mainnet", "testnet")
DEFAULT_WINDOW_DAYS = 14
EXCL_FLAGS = CL.EXCL_FLAGS


def ref_path(root=None):
    return paths.runtime("learn", "hyp_ref.json", root=root)


def _window_days(cfg):
    try:
        return int(((cfg or {}).get("hypref") or {}).get("window_days") or DEFAULT_WINDOW_DAYS)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_DAYS


def _r(v):
    return None if v is None else round(float(v), 4)


def interval(values, lvl=LVL):
    """Значения кластеров -> (n, mean, lo, hi): 95% t-интервал mean ± t(0.975, n-1) * sd / sqrt(n); n < 2 — без интервала (lo = hi = None)."""
    n = len(values)
    if not n:
        return 0, None, None, None
    m, sd = X._mean_sd(values)
    if sd is None:
        return n, m, None, None
    h = X.t_quantile(1.0 - (1.0 - lvl) / 2.0, n - 1) * sd / math.sqrt(n)
    return n, m, m - h, m + h


def _policy(pols, pid):
    cand = [p for p in pols if p.get("id") == pid and p.get("enabled")]
    return max(cand, key=lambda p: int(p["ver"])) if cand else None


def family_ref(conn, policy, kinds, a, b):
    """Справка одного семейства: прогоны policy_runs политики (id, текущая ver) видов kinds с t0 ∈ [a, b) -> {env: {n, n_runs, mean, lo, hi}}."""
    kinds = [k for k in (kinds or []) if isinstance(k, str) and k]
    out = dict((e, {"n": 0, "n_runs": 0, "mean": None, "lo": None, "hi": None}) for e in ENVS)
    if not kinds:
        return out
    q = ("SELECT p.side, p.t0, p.filled, p.r_net, p.flags, s.side, s.env FROM policy_runs p JOIN signals s ON s.key = p.key "
         "WHERE p.policy_id=? AND p.policy_ver=? AND p.t0>=? AND p.t0<? AND p.type IN (%s)" % ",".join("?" * len(kinds)))
    groups = dict((e, {}) for e in ENVS)
    for pside, t0, filled, r_net, flags, sside, env in conn.execute(q, [policy["id"], int(policy["ver"]), int(a), int(b)] + kinds).fetchall():
        if not filled or r_net is None or (CL._flag_set(flags) & set(EXCL_FLAGS)):
            continue
        side = pside if pside in CL.SIDES else CL.entry_side(policy, sside)
        if side is None:
            continue
        groups[CL.env_norm(env)].setdefault((int(t0) // HOUR, side), []).append(float(r_net))
    for e in ENVS:
        vals = [sum(v) / len(v) for _k, v in sorted(groups[e].items())]
        n, m, lo, hi = interval(vals)
        out[e] = {"n": n, "n_runs": sum(len(v) for v in groups[e].values()), "mean": _r(m), "lo": _r(lo), "hi": _r(hi)}
    return out


def compute(conn, now, cfg=None, pmap=None, pols=None, root=None):
    """Документ справки (без записи). pmap — hyps.policy_map(), pols — labels.policies.load(root)."""
    from tb.labels import policies as LP
    pmap = hyps.policy_map() if pmap is None else pmap
    pols = LP.load(root) if pols is None else pols
    days = _window_days(cfg)
    b = int(now)
    a = b - days * DAY
    fams = {}
    for fam in sorted(pmap):
        ent = pmap[fam]
        pol = _policy(pols, ent.get("policy")) if isinstance(ent, dict) else None
        if pol is None:
            continue
        kinds = [k for k in (ent.get("kinds") or []) if isinstance(k, str)]
        fams[fam] = {"policy": pol["id"], "policy_ver": int(pol["ver"]), "kinds": kinds, "env": family_ref(conn, pol, kinds, a, b)}
    return {"schema": SCHEMA, "ts": b, "window_days": days, "a": a, "b": b, "families": fams}


def run(root, now, cfg=None, pmap=None, pols=None):
    """Соединение на вызов (open_ro), расчёт, атомарная запись runtime/learn/hyp_ref.json. -> отчёт шага."""
    from tb.labels import policies as LP
    if pols is None:
        try:
            pols = LP.load(root)
        except ValueError:
            return {"code": "NO_DATA", "reason": "NO_POLICIES"}
    try:
        conn = LST.open_ro(root)
    except LST.KnowledgeRestoring:
        return {"code": "NO_DATA", "reason": "KNOWLEDGE_RESTORING"}
    if conn is None:
        return {"code": "NO_DATA", "reason": "NO_DB"}
    try:
        doc = compute(conn, now, cfg, pmap, pols, root)
    finally:
        conn.close()
    KM.write_json(ref_path(root), doc)
    return {"code": "OK", "families": len(doc["families"]),
            "n": dict((f, d["env"]["mainnet"]["n"]) for f, d in doc["families"].items())}


def step_hypref(ctx):
    """Часовой шаг hypref цикла (cycle.default_steps)."""
    return run(ctx.root, ctx.now, ctx.cfg)
