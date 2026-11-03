"""Сверка статусов уроков меток с базой (R2, задача 15; Requirements 6.3, 6.4, 6.9; design.md, «Путь урока меток», «Коды причин»; свойства 18, 19).

Шаг reconcile (раз в час). Источник правды — база (rule_rows, trials); статус урока меняет только lessons.transition и только вперёд:
  правило урока active или disabled (было активным)       урок hyp -> confirmed (TRIAL_PROMOTE; conf по числам испытания, срок ttl_confirmed_days)
  правило урока rejected с любым кодом                     урок hyp -> rejected с тем же кодом
  правило trial (испытание открыто или правило ждёт)       урок остаётся hyp
  правила нет, урок hyp                                    ничего: урок регистрирует заново шаг регистрации (trial.register_pending в
                                                           суточном register и часовом verdicts), счётчик unregistered
Расхождение назад (после восстановления базы из копии урок confirmed/expired/rejected, а база показывает открытое испытание или ожидающее
правило) разрешается в пользу урока: открытое испытание закрывается (inconclusive) или ожидающее правило отклоняется кодом урока
(для confirmed/expired — NO_VERDICT; для rejected — его код, если это код правила, иначе NO_VERDICT), learn_events RECONCILE_MISMATCH.
Истечение: урок confirmed с наступившим expires_ts -> expired (EXPIRED); правило не трогается (Requirement 6.4).
Модуль не импортирует brain/gateway/llm/venues/executor/control.
"""
from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.learnloop import trial as TR
from tb.learnloop import versions as V
from tb.memory import lessons as LS

STEP = "reconcile"
MISMATCH = "RECONCILE_MISMATCH"
RANK = {"active": 0, "trial": 1, "disabled": 2, "rejected": 3}


def lesson_rules(conn):
    """{lesson_id: последняя ревизия главного правила урока} (active > trial > disabled > rejected, затем последнее по seq)."""
    by = {}
    for r in RV.rules_latest(conn).values():
        lid = r.get("from_lesson")
        if not lid:
            continue
        key = (RANK.get(r.get("status"), 9), -int(r.get("seq") or 0))
        if lid not in by or key < by[lid][0]:
            by[lid] = (key, r)
    return dict((k, v[1]) for k, v in by.items())


def target(rule):
    """Целевой статус урока по правилу (Requirement 6.3): ("confirmed", "TRIAL_PROMOTE") | ("rejected", код) | ("hyp", None) | (None, None) — правила нет."""
    if rule is None:
        return None, None
    st = rule.get("status")
    if st in ("active", "disabled"):
        return "confirmed", "TRIAL_PROMOTE"
    if st == "rejected":
        code = rule.get("reason_code")
        return "rejected", code if code in LS.LABEL_REJECT_REASONS else "NO_VERDICT"
    return "hyp", None


def _nums(t):
    n = (t or {}).get("numbers") or {}
    k, d, se = n.get("n"), n.get("diff"), n.get("se")
    if isinstance(k, bool) or not isinstance(k, int) or isinstance(d, bool) or not isinstance(d, (int, float)):
        return None
    return {"n_closed": k, "diff": d, "se": se if isinstance(se, (int, float)) and not isinstance(se, bool) else None}


def _lesson_code(l):
    for h in reversed(l.get("history") or []):
        if h.get("status") == "rejected":
            return h.get("reason_code")
    return None


def _transition(root, l, status, code, now, mcfg, out, nums=None, action="forward"):
    try:
        res = LS.transition(root, l["lesson_id"], status, code, now, cfg=mcfg, nums=nums)
    except ValueError as e:                     # урок сменил статус параллельно (владелец) или переход недопустим: следующая сверка
        out.append({"lesson_id": l["lesson_id"], "action": "error", "to": status, "code": code, "err": str(e)[:120]})
        return False
    if res is not None:
        out.append({"lesson_id": l["lesson_id"], "action": action, "from": l["status"], "to": status, "code": code})
    return res is not None


def _mismatch(conn, l, rule, cfg, now, out):
    """База позади урока: закрыть открытое испытание или отклонить ожидающее правило кодом урока; RECONCILE_MISMATCH."""
    if l["status"] == "rejected":
        code = _lesson_code(l)
        code = code if code in V.REJECT_CODES else "NO_VERDICT"
    else:
        code = "NO_VERDICT"
    rid = rule["rule_id"]
    t = TR.by_rule(conn, rid)
    if t is not None and t["state"] == TR.OPEN:
        TR.close(conn, t, TR.INCONCLUSIVE, code, {"reconcile": l["status"]}, now, cfg, reason="RECONCILE")
        with V._tx(conn):
            store2.log_event(conn, STEP, MISMATCH, "code", ref=l["lesson_id"], body={"rule": rid, "trial": t["trial_id"], "lesson": l["status"],
                                                                                    "code": code}, error=True, now=now)
    else:
        with V._tx(conn):
            V.reject_rule(conn, rid, code, now, cfg, reason="RECONCILE")
            store2.log_event(conn, STEP, MISMATCH, "code", ref=l["lesson_id"], body={"rule": rid, "trial": (t or {}).get("trial_id"),
                                                                                    "lesson": l["status"], "code": code}, error=True, now=now)
    out.append({"lesson_id": l["lesson_id"], "action": "mismatch", "rule_id": rid, "code": code})


def reconcile(conn, root, cfg, now, mcfg=None):
    """Сверка уроков меток с rule_rows/trials (Requirement 6.9). Идемпотентна: повтор на тех же данных ничего не пишет.
    -> [действия {lesson_id, action: forward|expire|mismatch|unregistered|error, ...}]."""
    now = int(now)
    if mcfg is None:
        from tb.memory import cfg as memcfg
        mcfg = memcfg.load(root)
    out = []
    rules = lesson_rules(conn)
    ls = [l for l in LS.read_all(root).values() if LS.is_labels(l)]
    ls.sort(key=lambda l: l["lesson_id"])
    for l in ls:
        st = l.get("status")
        rule = rules.get(l["lesson_id"])
        tgt, code = target(rule)
        if st == "hyp":
            if tgt == "confirmed":
                _transition(root, l, "confirmed", code, now, mcfg, out, nums=_nums(TR.by_rule(conn, rule["rule_id"])))
            elif tgt == "rejected":
                _transition(root, l, "rejected", code, now, mcfg, out)
            elif tgt is None:
                out.append({"lesson_id": l["lesson_id"], "action": "unregistered"})
            continue
        if st in ("confirmed", "expired", "rejected") and tgt == "hyp":
            _mismatch(conn, l, rule, cfg, now, out)
        if st == "confirmed" and l.get("expires_ts") is not None and now >= float(l["expires_ts"]):
            _transition(root, l, "expired", "EXPIRED", now, mcfg, out, action="expire")
    return out


def step_reconcile(ctx):
    """Часовой шаг reconcile цикла (cycle.default_steps)."""
    acts = reconcile(ctx.conn, ctx.root, ctx.cfg, ctx.now, mcfg=ctx.mcfg)
    n = lambda a: sum(1 for x in acts if x["action"] == a)  # noqa: E731
    return {"code": "OK", "changed": n("forward") + n("expire"), "mismatch": n("mismatch"), "unregistered": n("unregistered"), "errors": n("error")}
