"""Вкладка «Память» нового дашборда (R2, задача 17; Requirements 13.1–13.6, 11.11): контракт данных операциями сокета и аварийные действия владельца.

Чтение (ответ только своему соединению, тема `<op>_detail`; схемы закреплены в tests/test_dash2_memory.py):
memory_cases    случаи state/cases.jsonl: число по типам и статусам, закрытые сделки по режимам (режимы не смешиваются), последние случаи;
                поиск похожих — заглушка «появится на этапе 3».
memory_lessons  уроки state/lessons.jsonl (мозга и меток): статус, источник, n, эффект с 95% t-интервалом, срок, испытание и правило урока.
memory_rules    rulebook: режим применения (rulebook_view.active), версии, правила, ожидающие правила (wait READY | MAX_ACTIVE | LOSS_CAP, начало
                ожидания, срок: у LOSS_CAP — wait_since_ts + promotion.loss_cap_wait_days, у остальных срока нет), доля feat_missing_ids по
                правилам и доля решений без события (rule_evals за 7 суток, как learnloop.cycle.no_event_share: причины no_event и stale);
                coverage — покрытие правилами (доля решений entry_scout/chat вне области правил по видам события, справочно, без цвета).
memory_path     путь «кандидат → урок → испытание → правило → версия» по lesson_candidates, lessons.jsonl, trials, rule_rows и воронка.
Каждое число — объект {"v", "n", "small"} (small: n < 30). База знаний открывается на один вызов через labels.store.open_ro и сразу
закрывается; постоянных соединений нет. Нет файла или таблиц цикла -> state="no_data" (причина в reason); маркер восстановления ->
state="restoring" («база восстанавливается»); ошибка чтения -> state="error". Блоки по случаям и урокам (JSONL) работают и без базы.

Действия (ответ `memory_action_result`; вызывают функции tb.knowledge.rulebook_cli и tb.knowledge.exits_cli — те же, что
`./tb rulebook disable|disable-all`, `./tb memory lesson reject` и `./tb exits rollback`; каждая пишет learn_events с actor=OWNER):
lesson_reject      урок меток -> rejected (OWNER), без диалога.
rule_disable       одно правило (OWNER_DISABLE, чёрный список бессрочно) — только с подтверждением рисков.
rules_disable_all  выключатель apply=false, затем версия OWNER_DISABLE без правил — только с подтверждением рисков.
exits_rollback     «Выходы: вернуть границу» (R4, Requirement 16.6): версия OWNER_ROLLBACK с политикой пола защиты
                   (`exits_cli.owner_rollback`: версия, снимок и аренда) — только с подтверждением рисков.
Подтверждение рисков (единый диалог «принимаю риски», как у ctl в dash2: без токена и фраз): kind="challenge" возвращает список последствий
`warnings` [{id, text}] без записи; команда (kind="command") исполняется, только если `accepted` — ровно все id предупреждений действия,
иначе E_CONFIRM с тем же списком. Операций «подтвердить урок» и «продвинуть правило» нет.
Защита соединения: CSRF соединения (выдаётся в hello, поле memory.csrf; константное сравнение), лимит частоты RATE_PER_MIN на соединение,
проверка идентификаторов по шаблону. Слой: не импортирует tb.learnloop, биржу, исполнителя, процессы (запись — только через rulebook_cli).
"""
import hmac
import json
import math
import re
import secrets
import sqlite3
import time

from tb import paths
from tb.knowledge import rulebook_view as RV
from tb.labels import store as LST
from tb.logic import learn_rules as LR

SMALL_N = 30
WINDOW_S = 7 * 86400                     # окно долей rule_evals (как NO_EVENT_WINDOW_S цикла)
NO_EVENT_YELLOW = 0.2                    # порог жёлтого RULES_NO_EVENT (design.md, «Записи rule_eval»)
DAY = 86400
WAITS = ("READY", "MAX_ACTIVE", "LOSS_CAP")
CASES_RECENT = 20
LESSONS_OUT = 200
VERSIONS_OUT = 20
RULES_OUT = 100
PATHS_OUT = 60
RATE_PER_MIN = 6
RE_RULE = re.compile(r"^RL\d{4,8}$")
RE_LESSON = re.compile(r"^L-\d{4,8}$")
RE_CID = re.compile(r"^[A-Za-z0-9._:-]{1,24}$")

READ_OPS = {"memory_cases": "memory_cases_detail", "memory_lessons": "memory_lessons_detail", "memory_rules": "memory_rules_detail",
            "memory_path": "memory_path_detail"}
ACTIONS = ("lesson_reject", "rule_disable", "rules_disable_all", "exits_rollback")
RESULT_TOPIC = "memory_action_result"
BLOCK_FOREVER = {"id": "block_forever", "text": "условие правила уходит в чёрный список бессрочно: цикл не вернёт его в испытание; снять — ./tb rulebook unblock"}
EXITS_FLOOR = {"id": "exits_floor", "text": "политика выхода вернётся к полу защиты: новая версия OWNER_ROLLBACK, снимок и аренда; снять — только новое продвижение циклом"}
WARNINGS = {
    "lesson_reject": [],
    "rule_disable": [{"id": "rule_off", "text": "правило перестанет применяться: новая версия rulebook без него (правило в испытании — испытание закрывается)"},
                     BLOCK_FOREVER],
    "rules_disable_all": [{"id": "switch_off", "text": "применение всех правил выключается сразу; включить снова — ./tb rulebook apply on"},
                          {"id": "all_off", "text": "все активные правила выключаются, испытания закрываются, новая версия rulebook без правил"},
                          BLOCK_FOREVER],
    "exits_rollback": [EXITS_FLOOR, {"id": "exits_open_positions", "text": "открытые позиции продолжают свою версию выхода: откат действует на новые входы"}],
}
MSG = {"OK": "готово", "CONFIRM": "нужно подтверждение рисков", "E_CONFIRM": "действие с рисками: нужно подтверждение «принимаю риски»",
       "E_CSRF": "сессия устарела: обновите страницу", "E_RATE": "слишком часто: подождите минуту", "E_ARGS": "неверные аргументы",
       "E_DB": "база знаний недоступна", "E_RETRY": "выключатель записан (правила не применяются), запись версии не сделана: повторите после восстановления базы",
       "E_INTERNAL": "действие не выполнено", "E_AUTH": "соединение не готово: обновите страницу"}
REASON = {"no_data": "нет данных: таблиц цикла нет", "restoring": "база восстанавливается", "error": "база знаний не читается"}


# ---------------------------------------------------------------- числа

def _fin(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _r(x, nd=4):
    return round(float(x), nd) if _fin(x) else None


def num(v, n):
    """Число с размером выборки: {"v", "n", "small"} (small — n < 30)."""
    n = int(n or 0)
    return {"v": _r(v), "n": n, "small": n < SMALL_N}


def share(k, n):
    n = int(n or 0)
    return num(float(k or 0) / n if n else None, n)


def effect(diff, se, n):
    """Эффект с 95% t-интервалом: {"v", "lo", "hi", "n", "small"}; интервала нет без se или при n < 2."""
    n = int(n) if _fin(n) else 0
    out = dict(num(diff, n), lo=None, hi=None)
    if _fin(diff) and _fin(se) and se >= 0 and n >= 2:
        from tb.core import expstats
        t = expstats.t_quantile(0.975, n - 1)
        out.update(lo=_r(diff - t * se), hi=_r(diff + t * se))
    return out


def _int(x):
    return int(x) if _fin(x) else None


def _s(x, n=64):
    return str(x)[:n] if isinstance(x, (str, int)) and not isinstance(x, bool) else None


# ---------------------------------------------------------------- база знаний на один вызов

def _rows(cur):
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _has(conn, t):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def _open(root, tables=("rule_rows", "rulebook_versions")):
    """-> (соединение | None, state): ok | no_data | restoring | error. Соединение закрывает вызывающий."""
    try:
        conn = LST.open_ro(root)
    except LST.KnowledgeRestoring:
        return None, "restoring"
    except sqlite3.Error:
        return None, "error"
    if conn is None:
        return None, "no_data"
    try:
        ok = all(_has(conn, t) for t in tables)
    except sqlite3.Error:
        conn.close()
        return None, "error"
    if not ok:
        conn.close()
        return None, "no_data"
    return conn, "ok"


def _env(state, **kw):
    out = {"ok": True, "state": state, "reason": REASON.get(state)}
    out.update(kw)
    return out


def _dec(v):
    if isinstance(v, str) and v[:1] in ("{", "["):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _cond_txt(cond):
    from tb.memory import lessons as LS
    try:
        return LS.render_cond(cond)[:200] if isinstance(cond, list) else None
    except (KeyError, TypeError):
        return None


def _learn_cfg(root):
    try:
        with open(paths.config("learnloop.json", root=root), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _cap_wait_s(root):
    v = (_learn_cfg(root).get("promotion") or {}).get("loss_cap_wait_days")
    return int(v * DAY) if _fin(v) and v > 0 else None


# ---------------------------------------------------------------- memory_cases

def memory_cases(root=None, now=None):
    from tb.memory import cases as CS
    now = time.time() if now is None else now
    cs = CS.load(root)
    similar = {"state": "stage3", "reason": "появится на этапе 3"}
    if not cs:
        return _env("no_data", reason="нет данных: случаев пока нет", total=0, by_type={}, by_status={}, modes=[], recent=[], similar=similar)
    by_type, by_status = {}, {}
    modes = {}
    for c in cs:
        t = c.get("type") or "?"
        st = (c.get("result") or {}).get("status") or "?"
        by_type[t] = by_type.get(t, 0) + 1
        by_status[st] = by_status.get(st, 0) + 1
        r = (c.get("result") or {}).get("r")
        if t == "trade" and st == "closed" and _fin(r) and CS.eligible(c):
            modes.setdefault((c.get("src") or {}).get("mode") or "?", []).append(float(r))
    mrows = []
    for m in sorted(modes):
        xs = modes[m]
        mrows.append({"mode": m, "n": len(xs), "mean_r": num(sum(xs) / len(xs), len(xs)), "win_share": share(sum(1 for x in xs if x > 0), len(xs))})
    recent = []
    for c in sorted(cs, key=lambda c: (c.get("ts") or "", c["case_id"]), reverse=True)[:CASES_RECENT]:
        sit, res, src, dec = c.get("situation") or {}, c.get("result") or {}, c.get("src") or {}, c.get("decision") or {}
        recent.append({"case_id": _s(c.get("case_id"), 48), "ts": _s(c.get("ts"), 32), "type": _s(c.get("type"), 8), "mode": _s(src.get("mode"), 8),
                       "sym": _s(sit.get("sym"), 20), "side": _s(sit.get("side"), 6), "setup": _s(sit.get("setup"), 24), "action": _s(dec.get("action"), 12),
                       "status": _s(res.get("status"), 8), "r": _r(res.get("r"), 3), "rulebook_ver": _s(src.get("rulebook_ver"), 8),
                       "eligible": bool(CS.eligible(c))})
    return _env("ok", total=len(cs), by_type=by_type, by_status=by_status, modes=mrows, recent=recent, similar=similar)


# ---------------------------------------------------------------- memory_lessons

def _trials_rules(conn):
    """-> ({lesson_id: [испытания]}, {lesson_id: [правила, последние ревизии]})."""
    tr, ru = {}, {}
    if _has(conn, "trials"):
        for t in _rows(conn.execute("SELECT trial_id, rule_id, lesson_id, state, verdict, start_ts, deadline_ts, cut_ts, closed_ts FROM trials ORDER BY start_ts")):
            tr.setdefault(t["lesson_id"], []).append(t)
    for rid, r in RV.rules_latest(conn).items():
        ru.setdefault(r.get("from_lesson"), []).append(r)
    return tr, ru


def _trial_brief(t):
    return None if t is None else {"trial_id": _s(t.get("trial_id"), 16), "rule_id": _s(t.get("rule_id"), 16), "state": _s(t.get("state"), 12),
                                   "verdict": _s(t.get("verdict"), 16), "start_ts": _int(t.get("start_ts")), "deadline_ts": _int(t.get("deadline_ts")),
                                   "closed_ts": _int(t.get("closed_ts"))}


def _block_key(b):
    """Ключ снятия запрета `./tb rulebook unblock` — block_h семейства условия правила (Requirement 7.14); поле ответа прежнее (cond_h),
    сменилось только значение. Тело без cond — прежний cond_h."""
    c = b.get("cond")
    return LR.block_h(c, "lower") if isinstance(c, list) and c else b.get("cond_h")


def _rule_brief(r, cur_ids=()):
    if r is None:
        return None
    b = r.get("body") or {}
    return {"rule_id": _s(r.get("rule_id"), 16), "rev": _int(r.get("rev")), "kind": _s(r.get("kind"), 24), "status": _s(r.get("status"), 12),
            "wait": _s(r.get("wait"), 12), "reason_code": _s(r.get("reason_code"), 24), "from_lesson": _s(r.get("from_lesson"), 16),
            "ver_in": _s(r.get("rulebook_ver_in"), 12), "ver_out": _s(r.get("rulebook_ver_out"), 12), "cond_txt": _cond_txt(b.get("cond")),
            "cond_h": _s(_block_key(b), 32), "env_norm": _s(r.get("env_norm"), 16), "ts": _s(r.get("ts"), 32), "in_version": r.get("rule_id") in cur_ids}


def memory_lessons(root=None, now=None):
    from tb.memory import lessons as LS
    now = time.time() if now is None else now
    ls = LS.read_all(root)
    conn, db = _open(root)
    tr, ru = {}, {}
    if conn is not None:
        try:
            tr, ru = _trials_rules(conn)
        except sqlite3.Error:
            db = "error"
        finally:
            conn.close()
    rows, by_status, by_src = [], {}, {}
    for lid in sorted(ls, reverse=True):
        l = ls[lid]
        ev = l.get("evidence") or {}
        src = "labels" if LS.is_labels(l) else "brain"
        st = l.get("status")
        by_status[st] = by_status.get(st, 0) + 1
        by_src[src] = by_src.get(src, 0) + 1
        n = ev.get("n_closed") if _fin(ev.get("n_closed")) else ev.get("n")
        cl = l.get("claim") or {}
        ts = tr.get(lid) or []
        rs = ru.get(lid) or []
        hist = l.get("history") or []
        rows.append({"lesson_id": _s(lid, 16), "src": src, "status": _s(st, 12), "reason_code": _s((hist[-1] if hist else {}).get("reason_code"), 24),
                     "txt": _s(l.get("txt"), 160), "cond_txt": _cond_txt(l.get("cond")),
                     "claim": {"metric": _s(cl.get("metric"), 16), "dir": _s(cl.get("dir"), 8), "vs": _s(cl.get("vs"), 8)},
                     "created_by": _s(l.get("created_by"), 12), "conf": _r(l.get("conf"), 3), "n": _int(n) or 0,
                     "effect": effect(ev.get("diff"), ev.get("se"), n), "p": _r(ev.get("p"), 6), "ts": _s(l.get("ts"), 32),
                     "expires_ts": _int(l.get("expires_ts")), "trial": _trial_brief(ts[-1] if ts else None),
                     "rule": _rule_brief(sorted(rs, key=lambda r: r.get("seq") or 0)[-1] if rs else None),
                     "can_reject": src == "labels" and st in LS.ACTIVE})
    total = len(rows)
    state = "ok" if rows else "no_data"
    return _env(state, reason=None if rows else "нет данных: уроков пока нет", db=db, db_reason=REASON.get(db), total=total, by_status=by_status,
                by_src=by_src, small_n=SMALL_N, rows=rows[:LESSONS_OUT])


# ---------------------------------------------------------------- memory_rules

def _window_shares(conn, now):
    """Доли за WINDOW_S: feat_missing_ids по правилам и в целом (строки rule_id<>''), решения без события (строки rule_id='')."""
    t0 = int(now) - WINDOW_S
    by_rule = []
    if not _has(conn, "rule_evals"):
        return ({"window_days": WINDOW_S // DAY, "all": share(0, 0), "by_rule": []},
                dict(share(0, 0), window_days=WINDOW_S // DAY, threshold=NO_EVENT_YELLOW, yellow=False, why={}), _coverage(None))
    for rid, n, m in conn.execute("SELECT rule_id, COUNT(*), SUM(missing) FROM rule_evals WHERE rule_id<>'' AND ts>=? GROUP BY rule_id ORDER BY rule_id",
                                  (t0,)).fetchall():
        by_rule.append(dict(share(m, n), rule_id=_s(rid, 32), missing=int(m or 0)))
    m, mm = conn.execute("SELECT COUNT(*), SUM(missing) FROM rule_evals WHERE rule_id<>'' AND ts>=?", (t0,)).fetchone()
    w = RV.rule_eval_why(conn, now, WINDOW_S)          # без события = no_event + stale (+ прежние no_event=1); out_of_scope — норма, не в доле
    n, ne = w["n"], w["fail"]
    fm = {"window_days": WINDOW_S // DAY, "all": share(mm, m), "by_rule": by_rule}
    noev = dict(share(ne, n), window_days=WINDOW_S // DAY, threshold=NO_EVENT_YELLOW, yellow=bool(n) and float(ne or 0) / n > NO_EVENT_YELLOW,
                why=dict((k, int(v)) for k, v in w["why"].items()))
    return fm, noev, _coverage(w["coverage"])


def _coverage(cov):
    """Покрытие правилами за WINDOW_S (справочно, без цвета): решения entry_scout/chat с известной причиной события — в области правил и вне её
    (вид события вне видов политики), доля вне области (объект share) и разбивка по виду. Нет данных -> нули."""
    cov = cov or {}
    by_kind = sorted(((_s(k, 24) or "?", int(v)) for k, v in (cov.get("by_kind") or {}).items()), key=lambda kv: (-kv[1], kv[0]))
    n, oos = int(cov.get("n") or 0), int(cov.get("out_of_scope") or 0)
    return {"window_days": WINDOW_S // DAY, "n": n, "in_scope": int(cov.get("in_scope") or 0), "out_of_scope": oos, "share": share(oos, n),
            "by_kind": [{"kind": k, "n": v} for k, v in by_kind[:20]]}


def memory_rules(root=None, now=None):
    now = time.time() if now is None else now
    try:
        view = RV.active(root, now)
    except Exception:  # noqa: BLE001 - режим применения не должен ронять блок
        view = {"apply": False, "why": "DB", "ver": None}
    mode = {"apply": bool(view.get("apply")), "why": _s(view.get("why"), 12), "ver": _s(view.get("ver"), 12)}
    conn, state = _open(root)
    if conn is None:
        return _env(state, mode=mode)
    try:
        vers = RV.read_versions(conn)
        latest = RV.rules_latest(conn)
        fm, noev, cov = _window_shares(conn, now)
    except sqlite3.Error:
        return _env("error", mode=mode)
    finally:
        conn.close()
    cur = vers[-1] if vers else None
    cur_ids = set((cur or {}).get("rule_ids") or [])
    cap_s = _cap_wait_s(root)
    waiting = []
    for rid, r in sorted(latest.items()):
        w = r.get("wait")
        if r.get("status") != "trial" or w not in WAITS:
            continue
        since = _int(r.get("wait_since_ts"))
        dl = since + cap_s if (w == "LOSS_CAP" and since is not None and cap_s) else None
        waiting.append({"rule_id": _s(rid, 16), "wait": w, "since_ts": since, "waited_s": (int(now) - since) if since is not None else None,
                        "deadline_ts": dl, "left_s": (dl - int(now)) if dl is not None else None, "from_lesson": _s(r.get("from_lesson"), 16),
                        "cond_txt": _cond_txt((r.get("body") or {}).get("cond"))})
    rules = [_rule_brief(r, cur_ids) for _rid, r in sorted(latest.items(), key=lambda kv: kv[1].get("seq") or 0, reverse=True)][:RULES_OUT]
    counts = {}
    for r in latest.values():
        counts[r.get("status")] = counts.get(r.get("status"), 0) + 1
    out_vers = [{"ver": _s(v.get("ver"), 12), "parent": _s(v.get("parent"), 12), "reason_code": _s(v.get("reason_code"), 24),
                 "rule_ids": [_s(x, 16) for x in (v.get("rule_ids") or [])][:50], "trial_id": _s(v.get("trial_id"), 16), "ts": _s(v.get("ts"), 32)}
                for v in reversed(vers[-VERSIONS_OUT:])]
    active = [x for x in rules if x["status"] == "active" and x["in_version"]]
    return _env("ok", mode=mode, current=_s((cur or {}).get("ver"), 12), versions=out_vers, versions_total=len(vers), counts=counts, active=active,
                waiting=waiting, rules=rules, rules_total=len(latest), feat_missing=fm, no_event=noev, coverage=cov)


# ---------------------------------------------------------------- memory_path

def memory_path(root=None, now=None, lesson_id=None):
    from tb.memory import lessons as LS
    now = time.time() if now is None else now
    if lesson_id is not None and not (isinstance(lesson_id, str) and RE_LESSON.match(lesson_id)):
        return {"ok": False, "state": "error", "reason": "неверный идентификатор урока", "error": "bad_args"}
    conn, state = _open(root, ("rule_rows", "rulebook_versions", "lesson_candidates", "trials"))
    if conn is None:
        return _env(state, funnel=None, paths=[])
    try:
        n_cand = conn.execute("SELECT COUNT(*) FROM lesson_candidates").fetchone()[0]
        n_hyp = conn.execute("SELECT COUNT(*) FROM lesson_candidates WHERE lesson_id IS NOT NULL").fetchone()[0]
        q = "SELECT id, menu_id, src, status, code, p_screen, lesson_id, holdout_start, holdout_end, ts FROM lesson_candidates"
        cands = _rows(conn.execute(q + " WHERE lesson_id=? ORDER BY id DESC", (lesson_id,)) if lesson_id else conn.execute(q + " ORDER BY id DESC LIMIT ?",
                                                                                                                              (PATHS_OUT,)))
        tr, ru = _trials_rules(conn)
        vers = {v["ver"]: v for v in RV.read_versions(conn)}
    except sqlite3.Error:
        return _env("error", funnel=None, paths=[])
    finally:
        conn.close()
    ls = LS.read_all(root)
    labels = [l for l in ls.values() if LS.is_labels(l)]
    all_rules = [r for rs in ru.values() for r in rs]
    funnel = {"candidates": int(n_cand), "hyp": int(n_hyp), "trial": len([k for k in tr if k]), "confirmed": sum(1 for l in labels if l.get("status") == "confirmed"),
              "rule_active": sum(1 for r in all_rules if r.get("status") == "active")}
    out = []
    for c in cands:
        lid = c.get("lesson_id")
        l = ls.get(lid) if lid else None
        ts = (tr.get(lid) or []) if lid else []
        rs = sorted(ru.get(lid) or [], key=lambda r: r.get("seq") or 0) if lid else []
        rule = rs[-1] if rs else None
        ver = None
        if rule is not None and (rule.get("rulebook_ver_in") or rule.get("rulebook_ver_out")):
            vi, vo = vers.get(rule.get("rulebook_ver_in")), vers.get(rule.get("rulebook_ver_out"))
            ver = {"in": _s(rule.get("rulebook_ver_in"), 12), "in_ts": _s((vi or {}).get("ts"), 32), "out": _s(rule.get("rulebook_ver_out"), 12),
                   "out_ts": _s((vo or {}).get("ts"), 32), "out_reason": _s((vo or {}).get("reason_code"), 24)}
        out.append({"candidate": {"id": _int(c.get("id")), "menu_id": _s(c.get("menu_id"), 16), "src": _s(c.get("src"), 12), "status": _s(c.get("status"), 12),
                                  "code": _s(c.get("code"), 24), "p_screen": _r(c.get("p_screen"), 6), "holdout_start": _int(c.get("holdout_start")),
                                  "holdout_end": _int(c.get("holdout_end")), "ts": _int(c.get("ts"))},
                    "lesson": None if l is None else {"lesson_id": _s(lid, 16), "status": _s(l.get("status"), 12), "txt": _s(l.get("txt"), 160)},
                    "trial": _trial_brief(ts[-1] if ts else None), "rule": _rule_brief(rule), "version": ver})
    return _env("ok" if out or n_cand else "no_data", reason=None if (out or n_cand) else "нет данных: кандидатов пока нет", funnel=funnel, paths=out,
                lesson_id=lesson_id)


def read(op, root=None, m=None, now=None):
    """Операция чтения по имени -> ответ (без scrub; его делает сервер)."""
    m = m if isinstance(m, dict) else {}
    if op == "memory_cases":
        return memory_cases(root, now)
    if op == "memory_lessons":
        return memory_lessons(root, now)
    if op == "memory_rules":
        return memory_rules(root, now)
    if op == "memory_path":
        return memory_path(root, now, m.get("lesson_id"))
    raise ValueError(op)


# ---------------------------------------------------------------- действия владельца

def _clean(x, depth=0):
    if depth > 4:
        return None
    if isinstance(x, bool) or x is None or isinstance(x, int):
        return x
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, str):
        return x[:300]
    if isinstance(x, (list, tuple)):
        return [_clean(v, depth + 1) for v in list(x)[:50]]
    if isinstance(x, dict):
        return {str(k)[:32]: _clean(v, depth + 1) for k, v in list(x.items())[:24]}
    return None


class MemoryActions(object):
    """Аварийные действия вкладки «Память»: сессия на соединение (CSRF и лимит частоты), подтверждение рисков, вызов rulebook_cli."""

    def __init__(self, root=None, clock=time.time):
        self.root, self.clock = root, clock
        self.sess = {}

    def open(self, ws):
        """-> поле hello.memory: {"csrf", "actions", "confirm": {действие: [id предупреждений]}}."""
        s = {"csrf": secrets.token_urlsafe(24), "posts": []}
        self.sess[ws] = s
        return {"csrf": s["csrf"], "actions": list(ACTIONS), "confirm": {a: [w["id"] for w in WARNINGS[a]] for a in ACTIONS}}

    def close(self, ws):
        self.sess.pop(ws, None)

    @staticmethod
    def _reply(cid, op, code, msg=None, **extra):
        out = {"id": cid, "op": op, "ok": code in ("OK", "CONFIRM"), "code": code, "msg": msg or MSG.get(code, MSG["E_INTERNAL"])}
        out.update(extra)
        return out

    def _gate(self, s, m, now):
        tok = m.get("csrf") if isinstance(m.get("csrf"), str) else ""
        if not hmac.compare_digest(tok.encode("utf-8", "replace"), s["csrf"].encode("ascii")):
            return "E_CSRF"
        s["posts"] = [t for t in s["posts"] if now - t < 60]
        if len(s["posts"]) >= RATE_PER_MIN:
            return "E_RATE"
        s["posts"].append(now)
        return None

    def handle(self, ws, m, now=None):
        """{"op", "id", "csrf", "kind": "challenge|command", "rule_id"|"lesson_id", "accepted": [...]} -> ответ `memory_action_result`.
        Блокирующие вызовы (запись базы) — через asyncio.to_thread."""
        now = self.clock() if now is None else now
        op = m.get("op") if m.get("op") in ACTIONS else ""
        cid = m.get("id") if isinstance(m.get("id"), str) and RE_CID.match(m.get("id")) else ""
        if not op:
            return self._reply(cid, str(m.get("op"))[:24], "E_ARGS")
        s = self.sess.get(ws)
        if s is None:
            return self._reply(cid, op, "E_AUTH")
        bad = self._gate(s, m, now)
        if bad:
            return self._reply(cid, op, bad)
        arg = None
        if op == "rule_disable":
            arg = m.get("rule_id")
            if not (isinstance(arg, str) and RE_RULE.match(arg)):
                return self._reply(cid, op, "E_ARGS", "неверный идентификатор правила")
        elif op == "lesson_reject":
            arg = m.get("lesson_id")
            if not (isinstance(arg, str) and RE_LESSON.match(arg)):
                return self._reply(cid, op, "E_ARGS", "неверный идентификатор урока")
        warns = WARNINGS[op]
        ids = sorted(w["id"] for w in warns)
        if m.get("kind") == "challenge":
            return self._reply(cid, op, "CONFIRM" if warns else "OK", None if warns else "подтверждение не нужно", warnings=warns, needs_confirm=bool(warns))
        if warns:
            acc = m.get("accepted")
            if not (isinstance(acc, list) and all(isinstance(x, str) for x in acc) and sorted(set(acc)) == ids):
                return self._reply(cid, op, "E_CONFIRM", warnings=warns, needs_confirm=True)
        return self._run(cid, op, arg, now)

    def _run(self, cid, op, arg, now):
        from tb.knowledge import rulebook_cli as RBC
        try:
            if op == "exits_rollback":
                from tb.knowledge import exits_cli as XCLI
                rep = XCLI.owner_rollback(self.root, now=now)
                if not rep.get("ok"):
                    why = XCLI.REASONS_RU.get(str(rep.get("db") or ""))
                    return self._reply(cid, op, "E_DB" if why else "E_INTERNAL",
                                       ("%s: %s" % (MSG["E_DB"], why)) if why else None, report=_clean(rep))
            elif op == "rule_disable":
                rep = RBC.disable_rule(self.root, arg, now=now)
            elif op == "rules_disable_all":
                rep = RBC.disable_all(self.root, now=now)
                if not rep.get("ok"):
                    return self._reply(cid, op, "E_RETRY", report=_clean(rep))
            else:
                from tb.memory import cfg as memcfg
                rep = RBC.reject_lesson(self.root, arg, now=now, mcfg=memcfg.load(self.root))
        except RBC.Unavailable as e:
            return self._reply(cid, op, "E_DB", "%s: %s" % (MSG["E_DB"], str(e)[:200]))
        except ValueError as e:
            return self._reply(cid, op, "E_ARGS", str(e)[:200])
        except OSError:
            return self._reply(cid, op, "E_INTERNAL", "выключатель не записан" if op == "rules_disable_all" else None)
        except Exception:  # noqa: BLE001 - сбой действия не рвёт соединение; подробности — ./tb rulebook | ./tb exits
            return self._reply(cid, op, "E_INTERNAL")
        return self._reply(cid, op, "OK", report=_clean(rep))
