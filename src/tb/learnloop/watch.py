"""Сторож отката (R2, задача 16; Requirements 11.1–11.7, 7.16; design.md, «Надзор за надзором и аварийные средства», «Сторож против нуля,
испытание против базы», «Потеря входов», «Смена версии»; свойства 14, 16, 22).

Шаг watch (часовой, группа watch). Все числа — только из выборок окна cluster_runs (Requirement 5.17): b — последняя граница часа с
cut_ts(b) <= now (clusters.last_b), поэтому к расчёту все кластеры окна уже закрыты и записаны, а решение не принимается раньше cut_ts.

Откат (ROLLBACK), для каждого активного правила действующей версии:
  участок     источник испытания правила (политика, env_norm); версия политики — текущая (запись политики проверки), ctx_ver — текущая
              на b (clusters.current_ctx); начало — max(час после продвижения, начало непрерывного участка ctx_ver). Смена версии политики или
              ctx_ver даёт новый участок: отсчёт точек заново, правило остаётся активным (Requirement 11.1).
  кластеры    кластеры участка, где есть заполненные прогоны метрик (без gap/halt) в области применимости с hit=1 по правилу; значение
              кластера — среднее r_net этих прогонов («как без правила»: R отсечённых входов). Порядок (час, кластер).
  точки       looks (50, 100, 200): по первым N таким кластерам, порог alpha_watch / len(looks) (0.05 / 3); каждая точка — один раз на
              участок. Затем раз в неделю после точки 200 (граница b200 + k x 7 суток, b200 — час после 200-го кластера) по скользящим
              последним weekly_window (200) кластерам до этой границы с порогом alpha_watch_weekly (0.001), без ограничения числа недель;
              пропущенные недели не досчитываются (проверяется только последняя наступившая). Вне точек решения нет.
  решение     среднее значений кластеров > rollback_delta_r И одностороннее p t-теста против нуля (stat.t1_pvalue, в сторону «больше») <
              порога точки -> versions.disable(ROLLBACK): новая версия без правила, disabled, чёрный список cooldown_days, копия rollback,
              числа (n, mean, p, точка, порог) в rulebook_versions.numbers, событие шины rulebook_rollback (info), learn_events ROLLBACK.
              Мёртвая зона: среднее от −min_effect_r до +rollback_delta_r (или выше без значимости) — правило остаётся активным.
Потеря входов (TRADE_STARVATION): по выборке окна starvation_window_days (7) суток [b − 7 сут, b) каждого источника активных правил — доля
  заполненных прогонов в области применимости, отсечённых активным набором (promote.loss_share, та же мера, что LOSS_CAP продвижения), выше
  disable_trade_loss_share (0.4) при >= disable_min_considered (30) кластерах -> отключается одно правило с наибольшим числом отсечённых прогонов
  окна (равенство — более позднее продвижение, затем больший rule_id); остальные — на следующем проходе.
Аренда: после успешной проверки (всё выше без исключения) атомарно пишется runtime/watch_lease.json {ts, rulebook_ver}; при любой ошибке шага
  аренда не обновляется (исключение уходит в цикл: STEP_ERROR), и через lease_h правила переходят в режим без применения. Пустой rulebook —
  успешная проверка: аренда пишется (иначе условие WATCH готовности продвижения не выполнилось бы никогда).
Состояние точек — meta.learnloop_watch {rule_id: {"seg", "done": [N], "week": k}}; повтор на тех же данных ничего не пишет (свойство 14).
Модуль не импортирует brain/gateway/llm/venues/executor/control.
"""
import datetime
import json

from tb.core import bus
from tb.knowledge import markers as KM
from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.learnloop import clusters as CL
from tb.learnloop import promote as PR
from tb.learnloop import rules as RL
from tb.learnloop import stat as ST
from tb.learnloop import trial as TR
from tb.learnloop import versions as V

STEP = "watch"
EVENT = "rulebook_rollback"
META = "learnloop_watch"
HOUR = CL.HOUR
DAY = CL.DAY
WEEK = 7 * DAY
SIDE = "higher"
ROLLBACK, STARVATION = "ROLLBACK", "TRADE_STARVATION"


# ---------------------------------------------------------------- чистые функции

def decide(xs, thr, delta):
    """Решение в точке: значения кластеров xs, порог p, rollback_delta_r. -> {"n", "mean", "sd", "p", "thr", "rollback"}.
    Откат — среднее > delta и одностороннее p (t против нуля, в сторону положительного среднего) < thr."""
    xs = [float(x) for x in xs]
    m = ST.mean(xs)
    p = ST.t1_pvalue(xs, 0.0, SIDE) if len(xs) >= 2 else None
    rb = m is not None and p is not None and m > float(delta) and p < float(thr)
    return {"n": len(xs), "mean": _r(m), "sd": _r(ST.sd(xs)), "p": _r(p, 10), "thr": _r(thr, 10), "rollback": bool(rb)}


def cut_clusters(rows, rule):
    """Кластеры с отсечённой правилом частью: [(hour, cluster, среднее r_net прогонов-хитов)] по (час, кластер). Строки — заполненные прогоны
    метрик (clusters.metric_rows) в области применимости с hit=1."""
    by = {}
    for r in CL.metric_rows(rows):
        if RL.row_hit(rule, r):
            by.setdefault(r["cluster"], []).append(float(r["r_net"]))
    out = [(int(c.split(":", 1)[0]), c, sum(v) / len(v)) for c, v in by.items()]
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def looks_due(clusters, b, st, cfg):
    """Точки, наступившие на границе b, ещё не пройденные на участке. -> [(метка, значения, порог)]: ("L50", первые 50, alpha/3) ...,
    затем ("W<k>", последние weekly_window до b200 + k недель, alpha_watch_weekly) для последней наступившей недели k > st.week."""
    w = cfg["watch"]
    looks = [int(x) for x in w["looks"]]
    thr = float(w["alpha_watch"]) / len(looks)
    done = set(int(x) for x in st.get("done") or [])
    out = []
    for n in looks:
        if n not in done and len(clusters) >= n:
            out.append(("L%d" % n, [x[2] for x in clusters[:n]], thr))
    last = looks[-1]
    if len(clusters) < last or last not in done | set(int(l[1:]) for l, _x, _t in out):
        return out
    b200 = (clusters[last - 1][0] + 1) * HOUR
    k = (int(b) - b200) // WEEK
    if k >= 1 and k > int(st.get("week") or 0):
        end = b200 + k * WEEK
        xs = [x[2] for x in clusters if x[0] * HOUR < end][-int(w["weekly_window"]):]
        out.append(("W%d" % k, xs, float(w["alpha_watch_weekly"])))
    return out


def _r(x, nd=6):
    return None if x is None else round(float(x), nd)


# ---------------------------------------------------------------- состояние и чтение

def state_get(conn):
    try:
        row = conn.execute("SELECT v FROM meta WHERE k=?", (META,)).fetchone()
        d = json.loads(row[0]) if row and row[0] else {}
    except (ValueError, TypeError):
        d = {}
    return d if isinstance(d, dict) else {}


def _state_put(conn, st):
    with V._tx(conn):
        V.meta_put(conn, META, st)


def _ts(iso):
    try:
        return int(datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp())
    except (TypeError, ValueError):
        return None


def promoted_ts(conn, rule):
    """Момент продвижения правила: время версии rulebook_ver_in (последняя ревизия active). Нет — None."""
    v = RV.get_version(conn, rule.get("rulebook_ver_in")) if rule.get("rulebook_ver_in") else None
    return _ts((v or {}).get("ts"))


def _src(t, policy):
    """Источник испытания с текущей версией политики (смена версии — новый участок)."""
    src = PR.src_of(t)
    if isinstance(policy, dict) and policy.get("id") == src["policy"] and policy.get("ver") is not None:
        src["policy_ver"] = int(policy["ver"])
    return src


def segment(conn, rule, t, cfg, now, policy=None):
    """Участок сторожа правила: {"key", "src", "ctx", "a", "b"} или None (данных версии на участке ещё нет / окно пусто)."""
    ts = promoted_ts(conn, rule)
    if ts is None or t is None:
        return None
    src = _src(t, policy)
    b = CL.last_b(now, cfg)
    ctx, start = CL.current_ctx(conn, src["policy"], src["policy_ver"], src["env_norm"], b)
    if ctx is None:
        return None
    a = max(CL.hour_ceil(ts), CL.hour_floor(start))
    if a >= b:
        return None
    key = "%s:%d:%s:%s:%d" % (src["policy"], src["policy_ver"], ctx, src["env_norm"], a)
    return {"key": key, "src": src, "ctx": ctx, "a": a, "b": b}


def _full_rule(conn, rid):
    row = V.rule(conn, rid)
    return row, (RV.row_rule(row) if row else None)


# ---------------------------------------------------------------- действия

def _emit(emit, root, now, d):
    try:
        return emit(EVENT, d, root, now), None
    except (OSError, ValueError) as e:            # версия записана; событие шины вторично
        return None, type(e).__name__


def _disable(conn, root, cfg, now, rid, code, nums, emit):
    res = V.disable(conn, [rid], code, now, cfg, root=root, numbers=nums)
    d = {"ver": res["ver"], "rule": rid, "code": code, "n": nums.get("n"), "mean_r": nums.get("mean"), "p": nums.get("p")}
    seq, err = _emit(emit, root, now, dict((k, v) for k, v in d.items() if v is not None))
    store2.log_event(conn, STEP, code, "code", ref=rid, body=dict(nums, ver=res["ver"], bus_seq=seq, bus_err=err,
                                                                  backup_err=bool(res.get("backup_error"))), now=now)
    return res


def check_rule(conn, root, cfg, now, rid, st_all, policy=None, emit=None):
    """Точки одного активного правила. -> {"rule_id", "seg", "looks": [...], "rollback": ver|None}; меняет st_all[rid]."""
    row, rule = _full_rule(conn, rid)
    t = TR.by_rule(conn, rid)
    rep = {"rule_id": rid, "seg": None, "looks": [], "rollback": None}
    if rule is None or t is None:
        return rep
    seg = segment(conn, row, t, cfg, now, policy)
    if seg is None:
        return rep
    rep["seg"] = seg["key"]
    st = st_all.get(rid)
    if not isinstance(st, dict) or st.get("seg") != seg["key"]:
        st = {"seg": seg["key"], "done": [], "week": 0}          # новый участок: отсчёт точек заново
        st_all[rid] = st
    s = seg["src"]
    win = CL.window(conn, s["policy"], s["policy_ver"], seg["ctx"], s["env_norm"], seg["a"], seg["b"], cfg, now)
    cl = cut_clusters(win["rows"], rule)
    delta = float(cfg["watch"]["rollback_delta_r"])
    for label, xs, thr in looks_due(cl, seg["b"], st, cfg):
        d = decide(xs, thr, delta)
        d.update(look=label, seg=seg["key"], a=seg["a"], b=seg["b"], cut_ts=win["cut_ts"])
        rep["looks"].append(d)
        if label.startswith("L"):
            st["done"] = sorted(set(st.get("done") or []) | {int(label[1:])})
        else:
            st["week"] = int(label[1:])
        if d["rollback"]:
            nums = {"n": d["n"], "mean": d["mean"], "p": d["p"], "thr": d["thr"], "look": label, "seg": seg["key"], "cut_ts": win["cut_ts"],
                    "trial": t["trial_id"]}
            rep["rollback"] = _disable(conn, root, cfg, now, rid, ROLLBACK, nums, emit)["ver"]
            st_all.pop(rid, None)
            break
        store2.log_event(conn, STEP, "LOOK", "code", ref=rid, body={k: d[k] for k in ("look", "n", "mean", "p", "thr", "seg")}, now=now)
    return rep


def starvation(conn, root, cfg, now, policy=None, emit=None):
    """TRADE_STARVATION (Requirement 11.6). -> {"groups": [{src, share, n, clusters}], "disabled": rule_id|None, "ver"}."""
    w = cfg["watch"]
    cap, need, days = float(w["disable_trade_loss_share"]), int(w["disable_min_considered"]), int(w["starvation_window_days"])
    groups = {}
    for r in V.active_rules(conn):
        t = TR.by_rule(conn, r["rule_id"])
        if t is None:
            continue
        src = _src(t, policy)
        groups.setdefault((src["policy"], src["policy_ver"], src["env_norm"]), (src, []))[1].append(r)
    rep = {"groups": [], "disabled": None, "ver": None}
    worst = None
    for key in sorted(groups):
        src, rules = groups[key]
        win = PR.loss_window(conn, src, cfg, now, days=days)
        ls = PR.loss_share(win, rules, rules[0].get("scope"))
        g = {"src": "%s:%d:%s" % key, "share": _r(ls["share"], 4), "n": ls["n"], "clusters": ls["clusters"], "cut": ls["cut"]}
        rep["groups"].append(g)
        if ls["clusters"] < need or not ls["share"] > cap:
            continue
        rows = [x for x in win["rows"] if x.get("filled") and PR.in_scope(x, rules[0].get("scope") or {})]
        for r in rules:
            n_cut = sum(1 for x in rows if RL.row_hit(dict(r, status="active"), x))
            row = V.rule(conn, r["rule_id"]) or {}
            k = (n_cut, V.ver_num(row.get("rulebook_ver_in")), r["rule_id"])
            if worst is None or k > worst[0]:
                worst = (k, r["rule_id"], g)
    if worst is not None:
        (n_cut, _vn, rid), g = worst[0], worst[2]
        nums = {"n": g["n"], "share": g["share"], "clusters": g["clusters"], "cut_rule": n_cut, "src": g["src"], "cap": cap, "mean": None, "p": None}
        rep["disabled"] = rid
        rep["ver"] = _disable(conn, root, cfg, now, rid, STARVATION, nums, emit)["ver"]
    return rep


def write_lease(root, now, ver):
    """Атомарная запись аренды runtime/watch_lease.json (tmp + os.replace)."""
    return KM.write_json(RV.lease_path(root), {"ts": int(now), "rulebook_ver": ver})


def check(conn, root, cfg, now, policy=None, emit=None, lease=True):
    """Одна проверка сторожа: точки каждого активного правила, затем TRADE_STARVATION, затем аренда. Исключение — аренда не пишется.
    -> {"code", "rules": [...], "rollbacks": [rule_id], "starvation": {...}, "ver", "lease"}."""
    now = int(now)
    emit = emit or (lambda type_, d, root_, now_: bus.emit(type_, d, root=root_, now=now_))
    st_all = state_get(conn)
    before = json.dumps(st_all, sort_keys=True)
    act = [r["rule_id"] for r in V.active_rules(conn)]
    for rid in [k for k in st_all if k not in act]:
        st_all.pop(rid, None)                                  # правило уже не активно: участок забыт
    out = {"code": "OK", "rules": [], "rollbacks": [], "starvation": None, "ver": None, "lease": None}
    for rid in act:
        rep = check_rule(conn, root, cfg, now, rid, st_all, policy, emit)
        out["rules"].append(rep)
        if rep["rollback"]:
            out["rollbacks"].append(rid)
    out["starvation"] = starvation(conn, root, cfg, now, policy, emit)
    if json.dumps(st_all, sort_keys=True) != before:
        _state_put(conn, st_all)
    if out["rollbacks"]:
        out["code"] = ROLLBACK
    elif out["starvation"]["disabled"]:
        out["code"] = STARVATION
    cur = V.current(conn)
    out["ver"] = (cur or {}).get("ver")
    if lease:
        write_lease(root, now, out["ver"])
        out["lease"] = now
    return out


def step_watch(ctx):
    """Часовой шаг watch цикла (cycle.default_steps)."""
    try:
        pol = ctx.policy
    except (ValueError, OSError):
        pol = None
    rep = check(ctx.conn, ctx.root, ctx.cfg, ctx.now, policy=pol)
    return {"code": rep["code"], "active": len(rep["rules"]), "rollbacks": len(rep["rollbacks"]),
            "starvation": (rep["starvation"] or {}).get("disabled"), "ver": rep["ver"], "lease": rep["lease"]}
