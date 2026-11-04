"""Недельная ретроспектива цикла обучения (R2 вторая итерация, задача 28; Requirements 12.1–12.5, 17.1, 17.4, 17.6).

  retro_digest  сводка завершённой недели ISO UTC кодом — только числа, перечисления и идентификаторы (port.clean): прогоны cluster_runs,
                кандидаты по источникам и статусам (коды отказов), испытания (открытые, закрытые с вердиктами и числами), изменения правил
                (продвижения, откаты, отказы — по reason_code), версии rulebook, итоги ревью дня и учёт суток недели.
  run_retro     шаг retro (daily; работа — раз в неделю): для прошедшей недели (candidates.week_id) ставит задание роли retro через порт
                (candidates.submit_review(kind="week", extra={"week": digest})) или помечает неделю llm=false (LLM_FALSE с кодом port.available).
                Идемпотентен: отметка недели в meta.learnloop_retro (learn_events может быть урезан per_day) и событие step="retro" с ref=неделя;
                повтор в ту же неделю -> NOT_DUE. NO_DATA/NO_MENU (окна поиска нет) неделю не закрывают — повтор в следующие сутки.
Ответ роли (tb.retro/1) принимает candidates.collect -> accept_llm(kind="week"): двухуровневая проверка (лишние поля ответа, правки промптов,
числа правил, свободный текст отклоняются), принятые предложения — кандидаты src=llm, далее holdout, урок hyp и испытание; правило без
испытания не создаётся. Модуль не импортирует tb.brain, tb.gateway, tb.llm.
"""
import json

from tb.knowledge import store2
from tb.labels import store as LST
from tb.learnloop import candidates as CA
from tb.learnloop import port as PT
from tb.learnloop import trial as TR
from tb.learnloop import versions as V

DAY = 86400
WEEK = 7 * DAY
STEP = CA.LLM_KINDS["week"]["step"]           # "retro"
KIND = "week"
META_RETRO = "learnloop_retro"               # {"week": W-YYYY-WW, "code", "job", "ts"} — последняя закрытая неделя
DONE_CODES = (CA.LLM_SUBMIT, CA.LLM_FALSE, CA.LLM_OK, CA.LLM_REJECT)
MAX_IDS = 20


def week_bounds(now):
    """Границы прошедшей недели ISO (понедельник 00:00 UTC) -> (a, b): b — начало текущей недели."""
    d = int(now) // DAY
    b = (d - (d + 3) % 7) * DAY              # 1970-01-01 — четверг: (d + 3) % 7 = номер дня недели, понедельник = 0
    return b - WEEK, b


def _iso_range(a, b):
    return store2.iso(a), store2.iso(b)


def _j(v):
    if isinstance(v, str) and v[:1] in ("{", "["):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _counts(rows):
    out = {}
    for k in rows:
        k = str(k if k is not None else "none")
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def _candidates(conn, env, a, b):
    by = {}
    for src, status, code in conn.execute("SELECT src, status, code FROM lesson_candidates WHERE env_norm=? AND ts>=? AND ts<?", (env, a, b)).fetchall():
        d = by.setdefault(src, {"n": 0, "status": {}, "codes": {}})
        d["n"] += 1
        d["status"][status] = d["status"].get(status, 0) + 1
        if code:
            d["codes"][code] = d["codes"].get(code, 0) + 1
    hyp = conn.execute("SELECT COUNT(*) FROM lesson_candidates WHERE env_norm=? AND status='hyp' AND holdout_end>=? AND holdout_end<?", (env, a, b)).fetchone()[0]
    return {"by_src": by, "hyp_week": int(hyp or 0)}


def _trials(conn, env, a, b):
    opened = closed = 0
    open_now = 0
    verdicts, items = {}, []
    for t in TR.trials(conn):
        if t.get("env_norm") != env:
            continue
        if t.get("state") == TR.OPEN:
            open_now += 1
        if a <= int(t.get("start_ts") or 0) < b:
            opened += 1
        ct = t.get("closed_ts")
        if t.get("state") == TR.CLOSED and ct is not None and a <= int(ct) < b:
            closed += 1
            v = t.get("verdict") or "none"
            verdicts[v] = verdicts.get(v, 0) + 1
            nums = t.get("numbers") if isinstance(t.get("numbers"), dict) else {}
            if len(items) < MAX_IDS:
                items.append({"trial": t["trial_id"], "rule": t["rule_id"], "verdict": v, "code": nums.get("code"), "n": _num(nums.get("n")),
                              "diff": _num(nums.get("diff")), "p": _num(nums.get("p"))})
    return {"opened": opened, "closed": closed, "open_now": open_now, "verdicts": dict(sorted(verdicts.items())), "closed_items": items}


def _rules(conn, env, a, b):
    ia, ib = _iso_range(a, b)
    rows = conn.execute("SELECT rule_id, status, reason_code FROM rule_rows WHERE env_norm=? AND ts>=? AND ts<? ORDER BY seq", (env, ia, ib)).fetchall()
    out = {"status": _counts(r[1] for r in rows), "reasons": _counts(r[2] for r in rows if r[2]), "promoted": [], "rolled_back": [], "rejected": []}
    for rid, st, rc in rows:
        key = {"active": "promoted", "disabled": "rolled_back", "rejected": "rejected"}.get(st)
        if key and rid not in out[key] and len(out[key]) < MAX_IDS:
            out[key].append(rid)
    act = conn.execute("SELECT COUNT(DISTINCT rule_id) FROM rule_rows r WHERE env_norm=? AND seq=(SELECT MAX(seq) FROM rule_rows WHERE rule_id=r.rule_id) "
                       "AND status='active'", (env,)).fetchone()[0]
    out["active_now"] = int(act or 0)
    return out


def _versions(conn, a, b):
    ia, ib = _iso_range(a, b)
    rows = conn.execute("SELECT ver, reason_code FROM rulebook_versions WHERE ts>=? AND ts<? ORDER BY seq", (ia, ib)).fetchall()
    last = conn.execute("SELECT ver FROM rulebook_versions ORDER BY seq DESC LIMIT 1").fetchone()
    return {"n": len(rows), "reasons": _counts(r[1] for r in rows), "vers": [r[0] for r in rows][:MAX_IDS], "current": last[0] if last else None}


def _events(conn, a, b):
    ia, ib = _iso_range(a, b)
    dr = conn.execute("SELECT code FROM learn_events WHERE step=? AND ts>=? AND ts<?", (CA.LLM_KINDS["day"]["step"], ia, ib)).fetchall()
    days = {}
    for ref, body in conn.execute("SELECT ref, body FROM learn_events WHERE step=? AND ref>=? AND ref<? ORDER BY seq",
                                  (V.DAY_STEP, ia[:10], ib[:10])).fetchall():
        days[ref] = _j(body) or {}
    errs = conn.execute("SELECT COUNT(*) FROM learn_events WHERE ts>=? AND ts<? AND code IN (?, ?)", (ia, ib, "STEP_ERROR", "CHAIN_BROKEN")).fetchone()[0]
    return {"day_review": _counts(r[0] for r in dr),
            "days": {"n": len(days), "counted": sum(1 for d in days.values() if d.get("counted")), "full": sum(1 for d in days.values() if d.get("full"))},
            "step_errors": int(errs or 0)}


def retro_digest(conn, now, env="mainnet"):
    """Сводка прошедшей недели кодом (Requirement 12.1, 17.6): только числа, перечисления и идентификаторы; проходит port.clean."""
    a, b = week_bounds(now)
    d = {"week_id": CA.week_id(now), "a": a, "b": b, "env": env,
         "runs": CA._day_numbers(conn, env, a, b),
         "candidates": _candidates(conn, env, a, b),
         "trials": _trials(conn, env, a, b),
         "rules": _rules(conn, env, a, b),
         "versions": _versions(conn, a, b),
         "events": _events(conn, a, b)}
    return PT.clean(d)


# ---------------------------------------------------------------- шаг цикла

def _mark(conn):
    try:
        row = conn.execute("SELECT v FROM meta WHERE k=?", (META_RETRO,)).fetchone()
        v = json.loads(row[0]) if row and row[0] else None
        return v if isinstance(v, dict) else None
    except (ValueError, TypeError):
        return None


def week_done(conn, pid):
    """Неделя уже закрыта: отметка meta или событие step=retro с ref=неделя и кодом задания/llm=false/ответа."""
    m = _mark(conn)
    if m and m.get("week") == pid:
        return True
    row = conn.execute("SELECT 1 FROM learn_events WHERE step=? AND ref=? AND code IN (%s) LIMIT 1" % ",".join("?" * len(DONE_CODES)),
                       (STEP, pid) + DONE_CODES).fetchone()
    return row is not None


def run_retro(conn, root, now, cfg, policy=None, env="mainnet"):
    """Шаг retro -> {"code": NOT_DUE | LLM_SUBMIT | LLM_PENDING | LLM_FALSE | NO_DATA | NO_MENU, "why", "id", "job"}."""
    now = int(now)
    pid = CA.week_id(now)
    if week_done(conn, pid):
        return {"code": "NOT_DUE", "why": None, "id": pid, "job": None}
    ok, _why = PT.available(root, now, CA.LLM_KINDS[KIND]["role"])
    extra = {"week": retro_digest(conn, now, env)} if ok else None          # без модели сводка не нужна (llm=false)
    rep = CA.submit_review(conn, root, now, cfg, KIND, policy=policy, env=env, extra=extra)
    if rep["code"] in (CA.LLM_SUBMIT, CA.LLM_FALSE, CA.LLM_PENDING):
        with LST.tx(conn):
            V.meta_put(conn, META_RETRO, {"week": pid, "code": rep["code"], "job": rep.get("job"), "why": rep.get("why"), "ts": now})
    return {"code": rep["code"], "why": rep.get("why"), "id": pid, "job": rep.get("job")}


def step_retro(ctx):
    """Суточный шаг цикла: раз в неделю UTC (после завершения недели) задание роли retro или llm=false; иначе NOT_DUE."""
    return run_retro(ctx.conn, ctx.root, ctx.now, ctx.cfg, policy=ctx.policy)
