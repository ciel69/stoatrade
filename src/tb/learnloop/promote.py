"""Продвижение правил (R2, задача 15; Requirements 10.1–10.7; design.md, «Автономность и автопродвижение», «tb.learnloop.rules / versions / ...»,
«Коды причин»; свойства 23, 24, 25, 26).

Шаг promote (раз в час, после verdicts):
  promotion.mode != "auto"   ничего не меняется (аварийная остановка, Requirement 10.6), код OFF.
  ожидающие правила          trial.promoted: испытание закрыто вердиктом promote, правило trial (rule_rows.wait). Нет — код OK без проверок.
  готовность                 versions.promotion_ready (прогрев, сторож, база). Не готово -> каждое ожидающее правило wait=READY (без срока), выход.
  порядок                    по модулю среднего предельных хитов (numbers.in_mean испытания) по убыванию, затем trial_id.
  пересечение по строкам     доля прогонов-хитов одного правила, отсекаемых другим, в выборке окна 7 суток (rules.overlap_rows, обе стороны) выше
                             overlap_share_max — с правилом, продвинутым в этом проходе, или с активным правилом, продвинутым после старта испытания
                             (нет в активном наборе критерия): правило rejected NO_VERDICT (OVERLAP) без чёрного списка (Requirement 10.5, 9.9).
  лимит                      активных правил уже max_active_rules -> wait=MAX_ACTIVE (без срока, Requirement 10.4).
  потеря входов              loss_share: доля заполненных прогонов политики проверки в области применимости за 7 суток, которые отсёк бы активный
                             набор вместе с правилом, >= watch.disable_trade_loss_share -> wait=LOSS_CAP; непрерывно дольше loss_cap_wait_days
                             (wait_since_ts) -> правило rejected LOSS_CAP без чёрного списка (Requirement 10.3, 10.7). Та же мера, что у
                             TRADE_STARVATION сторожа (задача 16 вызывает loss_window / loss_share), поэтому продвижение само его не вызывает.
  продвижение                versions.promote (версия PROMOTE, копия promote), событие шины rulebook_promoted (info), learn_events PROMOTE;
                             после всех продвижений прохода — trial.check_overlaps для открытых испытаний.
Урок меток в confirmed (или rejected) переводит шаг сверки (learnloop.reconcile), не этот шаг: падение между версией и переходом урока
исправляется следующей сверкой. Модуль не импортирует brain/gateway/llm/venues/executor/control.
"""
from tb.core import bus
from tb.knowledge import store2
from tb.learnloop import clusters as CL
from tb.learnloop import conf as LC
from tb.learnloop import rules as RL
from tb.learnloop import trial as TR
from tb.learnloop import versions as V
from tb.logic import learn_rules as LR

STEP = "promote"
DAY = CL.DAY
LOSS_WINDOW_DAYS = 7
EVENT = "rulebook_promoted"
OFF = "OFF"


# ---------------------------------------------------------------- меры по выборке окна

def _act(r):
    return dict(r, status="active")


def in_scope(row, scope):
    """Прогон в области применимости (как trial.analyze: у прогонов политики области сторона по построению scope.side)."""
    f = RL.row_feats(row)
    if f.get("side_rel") is None and scope.get("policy") is not None and row.get("policy_id") == scope.get("policy"):
        f["side_rel"] = scope.get("side")
    return LR.scope_ok(f, scope) is True


def src_of(t):
    """Источник испытания (колонки trials): политика, версия, ctx_ver, env_norm."""
    return {"policy": t["policy_id"], "policy_ver": int(t["policy_ver"]), "ctx_ver": t["ctx_ver"], "env_norm": t["env_norm"]}


def loss_window(conn, src, cfg, now, days=LOSS_WINDOW_DAYS):
    """Выборка окна cluster_runs источника за последние `days` суток: [b − days, b), b — последняя граница часа с cut_ts(b) <= now; ctx_ver —
    текущая версия контекста на b (clusters.current_ctx), без строк — записанная в источнике."""
    b = CL.last_b(now, cfg)
    a = b - int(days) * DAY
    ctx = CL.current_ctx(conn, src["policy"], src["policy_ver"], src["env_norm"], b)[0] or src["ctx_ver"]
    return CL.window(conn, src["policy"], src["policy_ver"], ctx, src["env_norm"], a, b, cfg, now)


def loss_share(win, rules, scope):
    """Requirement 10.7 / 11.6: доля заполненных прогонов в области применимости scope, которые отсекает набор rules (хит хотя бы одного правила).
    -> {"share" (точная), "n" (заполненных прогонов в области), "cut", "clusters" (кластеров с такими прогонами)}. Пустая выборка -> share 0.0."""
    rows = [r for r in (win.get("rows") if isinstance(win, dict) else win) or () if r.get("filled") and in_scope(r, scope or {})]
    acts = [_act(r) for r in rules or ()]
    cut = sum(1 for r in rows if any(RL.row_hit(a, r) for a in acts))
    return {"share": (cut / float(len(rows))) if rows else 0.0, "n": len(rows), "cut": cut, "clusters": len(set(r.get("cluster") for r in rows))}


def pair_overlap(win, rule_a, rule_b):
    """Пересечение двух правил по строкам: большая из долей «хиты одного, отсекаемые другим» (Requirement 10.5)."""
    return max(RL.overlap_rows(win, rule_a, _act(rule_b)), RL.overlap_rows(win, rule_b, _act(rule_a)))


def _r(x, nd=4):
    return None if x is None else round(float(x), nd)


def _prio(p):
    n = p["trial"].get("numbers") or {}
    m = n.get("in_mean")
    return (-abs(float(m)) if isinstance(m, (int, float)) and not isinstance(m, bool) else 0.0, p["trial"]["trial_id"])


def _log(conn, code, ref, body, now, error=False):
    store2.log_event(conn, STEP, code, "code", ref=ref, body=body, error=error, now=now)


def _wait(conn, rid, wait, now, rep, body=None):
    if V.set_wait(conn, rid, wait, now) is not None:
        _log(conn, wait, rid, body, now)
    rep["waiting"].append((rid, wait))


# ---------------------------------------------------------------- шаг

def run(conn, root, cfg, now, state=None, ready=None, emit=None):
    """Один проход продвижения. ready — готовый versions.promotion_ready (для тестов), иначе считается здесь; emit(type, d, root, now) — запись
    в шину (по умолчанию core.bus.emit). -> {"code", "promoted": [{rule_id, ver, trial_id}], "waiting": [(rule_id, wait)], "rejected":
    [(rule_id, код, причина)], "overlap_closed": [...], "ready": {...}|None}."""
    now = int(now)
    rep = {"code": "OK", "promoted": [], "waiting": [], "rejected": [], "refused": [], "overlap_closed": [], "ready": None}
    if LC.promotion_mode(cfg) != "auto":
        rep["code"] = OFF
        return rep
    pend = sorted(TR.promoted(conn), key=_prio)
    if not pend:
        return rep
    rd = ready if ready is not None else V.promotion_ready(conn, cfg, now, root=root, state=state)
    rep["ready"] = {"ok": bool(rd.get("ok")), "failed": list(rd.get("failed") or [])}
    if not rd.get("ok"):
        for p in pend:
            _wait(conn, p["rule"]["rule_id"], "READY", now, rep, {"failed": rep["ready"]["failed"]})
        return rep
    limit = int(cfg["rulebook"]["max_active_rules"])
    cap = float(cfg["watch"]["disable_trade_loss_share"])
    cap_wait_s = int(cfg["promotion"]["loss_cap_wait_days"]) * DAY
    omax = float(cfg["trial"]["overlap_share_max"])
    emit = emit or (lambda type_, d, root_, now_: bus.emit(type_, d, root=root_, now=now_))
    passed = []                                         # продвинутые в этом проходе
    for p in pend:
        t, r = p["trial"], p["rule"]
        rid = r["rule_id"]
        src = src_of(t)
        active = V.active_rules(conn)
        win = loss_window(conn, src, cfg, now)
        known = set(a.get("rule_id") for a in (t.get("criterion") or {}).get("active") or ())
        other = [a for a in active if a["rule_id"] not in known and a["rule_id"] != rid]
        hit = None
        for a in sorted(other, key=lambda x: (x["rule_id"] not in passed, x["rule_id"])):
            sh = pair_overlap(win, r, a)
            if sh > omax:
                hit = (a["rule_id"], sh)
                break
        if hit is not None:
            V.reject_rule(conn, rid, "NO_VERDICT", now, cfg, reason=RL.OVERLAP)
            _log(conn, "NO_VERDICT", rid, {"reason": RL.OVERLAP, "with": hit[0], "share": _r(hit[1])}, now)
            rep["rejected"].append((rid, "NO_VERDICT", RL.OVERLAP))
            continue
        if len(active) >= limit:
            _wait(conn, rid, "MAX_ACTIVE", now, rep, {"active": len(active), "limit": limit})
            continue
        ls = loss_share(win, active + [r], r.get("scope"))
        if ls["share"] >= cap:
            prev = V.rule(conn, rid) or {}
            since = prev.get("wait_since_ts")
            if prev.get("wait") == "LOSS_CAP" and since is not None and now - int(since) > cap_wait_s:
                V.reject_rule(conn, rid, "LOSS_CAP", now, cfg)
                _log(conn, "LOSS_CAP", rid, {"share": _r(ls["share"]), "since": int(since), "rejected": True}, now)
                rep["rejected"].append((rid, "LOSS_CAP", None))
            else:
                _wait(conn, rid, "LOSS_CAP", now, rep, {"share": _r(ls["share"]), "n": ls["n"], "cap": cap})
            continue
        n = t.get("numbers") or {}
        nums = {"trial": t["trial_id"], "loss_share": _r(ls["share"]), "loss_n": ls["n"], "n": n.get("n"), "in_mean": n.get("in_mean"),
                "diff": n.get("diff"), "p": n.get("p"), "cut_ts": t.get("cut_ts")}
        try:
            res = V.promote(conn, rid, now, cfg, root=root, trial_id=t["trial_id"], numbers=nums)
        except V.RuleRefused as e:                      # не ужесточение или гонка статуса: правило ждёт, проход продолжается
            _log(conn, e.code, rid, {"stage": STEP}, now, error=True)
            rep["refused"].append((rid, e.code))
            continue
        passed.append(rid)
        seq, err = None, None
        try:
            seq = emit(EVENT, {"ver": res["ver"], "rule": rid, "trial": t["trial_id"]}, root, now)
        except (OSError, ValueError) as e:              # версия записана; событие шины вторично
            err = type(e).__name__
        _log(conn, "PROMOTE", rid, {"ver": res["ver"], "trial": t["trial_id"], "loss_share": nums["loss_share"], "bus_seq": seq, "bus_err": err,
                                    "backup_err": bool(res.get("backup_error"))}, now)
        rep["promoted"].append({"rule_id": rid, "ver": res["ver"], "trial_id": t["trial_id"]})
    if passed:
        rep["overlap_closed"] = TR.check_overlaps(conn, cfg, now)
    return rep


def step_promote(ctx):
    """Часовой шаг promote цикла (cycle.default_steps)."""
    st = ctx.cycle.state if ctx.cycle is not None else None
    rep = run(ctx.conn, ctx.root, ctx.cfg, ctx.now, state=st)
    return {"code": rep["code"], "promoted": len(rep["promoted"]), "waiting": len(rep["waiting"]), "rejected": len(rep["rejected"]),
            "ready": (rep["ready"] or {}).get("ok")}
