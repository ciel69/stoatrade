"""Гейт этапа R2 (задача 21; Requirements 15.8, 15.9, 15.6, 10.6; design.md, «Автономность и автопродвижение», «Надзор за надзором и аварийные средства»,
свойство 27). Чистый расчёт по накопленной истории базы знаний; база только читается (соединение на вызов, labels.store.open_ro).

Ступень A (оба условия):
  A_DAYS  среди последних WINDOW_DAYS (10) засчитываемых суток не меньше FULL_MIN (7) полных проходов без вмешательства владельца.
          Сутки берутся из сводок learn_events step="day" (cycle.summarize_day: counted — работа >= day_min_work_h, full — полный проход).
          Незасчитываемые сутки (работа < 12 ч) и пропущенные сутки (сводки нет: платформа выключена) в окно не входят и счётчик не
          сбрасывают. Вмешательство — запись learn_events actor=OWNER по объектам цикла с ts в эти сутки UTC: шаг "rulebook" (./tb rulebook
          disable|disable-all|apply|unblock, кнопка rule_disable), "promotion" (./tb learn promotion), "lesson" (отклонение урока меток).
          Действия с уроками мозга (запись "lesson", чей урок в memory.lessons — урок мозга, или body.src="brain") и прочие записи OWNER
          (например ручное восстановление базы knowledge_restore) вмешательством не считаются; автоматические действия цикла (actor=code) — тоже.
  A_HYP   есть урок меток (evidence.src="labels"; создаётся только со статусом hyp) с evidence.n_closed >= HYP_MIN_N (30); учитываются и уроки,
          ушедшие из hyp позже (накопленная история).
Ступень B:
  B_VERDICT хотя бы одно испытание закрыто вердиктом promote | reject | inconclusive кодом цикла (испытание, закрытое владельцем — numbers.code
          "OWNER", — не вердикт цикла).
Этап пройден, когда выполнены обе ступени. Пороги гейта зафиксированы требованием 15.8 и в конфиг не выносятся.
Модуль не импортирует brain/gateway/llm/venues/executor/control.
"""
import json
import sqlite3
import time

from tb.knowledge import markers as KM
from tb.knowledge import rulebook_view as RV
from tb.knowledge import schema2 as S2
from tb.knowledge import store2
from tb.labels import store as LST
from tb.learnloop import trial as TR
from tb.learnloop import versions as V

WINDOW_DAYS = 10
FULL_MIN = 7
HYP_MIN_N = 30
VERDICTS = (TR.PROMOTE, TR.REJECT, TR.INCONCLUSIVE)
CYCLE_OWNER_STEPS = ("rulebook", "promotion")
LESSON_STEP = "lesson"
CONDS = ("A_DAYS", "A_HYP", "B_VERDICT")

# классы записей OWNER
CYCLE, BRAIN_LESSON, OTHER = "cycle", "brain_lesson", "other"

# коды недоступности базы
NO_DB, NOT_READY, RESTORING, DB_ERROR = "NO_DB", S2.NOT_READY, S2.RESTORING, "DB_ERROR"
REASONS_RU = {NO_DB: "базы знаний нет (state/knowledge.sqlite не создана): цикл ещё не запускался",
              NOT_READY: "таблиц цикла обучения нет (NOT_READY): миграция v2a не выполнена",
              RESTORING: "база знаний восстанавливается (runtime/knowledge.restoring): повторите после восстановления",
              DB_ERROR: "база знаний не читается"}


def _j(v):
    if isinstance(v, str) and v[:1] in ("{", "["):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v if isinstance(v, (dict, list)) else None


# ---------------------------------------------------------------- записи OWNER

def owner_events(conn):
    """Записи learn_events actor=OWNER по порядку: [{seq, ts, day, step, code, ref, body}]."""
    rows = conn.execute("SELECT seq, ts, step, code, ref, body FROM learn_events WHERE actor=? ORDER BY seq", (S2.OWNER,)).fetchall()
    out = []
    for seq, ts, step, code, ref, body in rows:
        out.append({"seq": seq, "ts": ts, "day": str(ts or "")[:10], "step": step, "code": code, "ref": ref, "body": _j(body) or {}})
    return out


def classify(ev, lessons=None):
    """Класс записи OWNER: cycle (вмешательство по объектам цикла) | brain_lesson | other."""
    step = ev.get("step")
    if step in CYCLE_OWNER_STEPS:
        return CYCLE
    if step == LESSON_STEP:
        body = ev.get("body") if isinstance(ev.get("body"), dict) else {}
        if body.get("src") == "brain":
            return BRAIN_LESSON
        l = (lessons or {}).get(ev.get("ref"))
        if l is not None and ((l.get("evidence") or {}).get("src") != "labels"):
            return BRAIN_LESSON
        return CYCLE                       # урок меток или неизвестный урок (отклонение пишет только для уроков меток) — осторожно: вмешательство
    return OTHER


# ---------------------------------------------------------------- условия

def stage_days(days, owner, lessons=None, window=WINDOW_DAYS, need=FULL_MIN):
    """A_DAYS по сводкам суток (от новых к старым, versions.day_rows) и записям OWNER. -> {ok, clean, need, window, counted, days, ...}."""
    by_day = {}
    for ev in owner:
        if classify(ev, lessons) == CYCLE:
            by_day.setdefault(ev["day"], []).append(ev.get("code") or ev.get("step"))
    counted = [d for d in days if d.get("counted")][:int(window)]
    rows, clean = [], 0
    for d in counted:
        own = by_day.get(d["day"]) or []
        ok = bool(d.get("full")) and not own
        clean += 1 if ok else 0
        rows.append({"day": d["day"], "full": bool(d.get("full")), "owner": own, "ok": ok, "work_h": d.get("work_h"), "missing": d.get("missing") or [],
                     "obs_share": d.get("obs_share")})
    win_days = set(d["day"] for d in counted)
    return {"ok": clean >= int(need), "clean": clean, "need": int(need), "window": int(window), "counted": len(counted),
            "full": sum(1 for r in rows if r["full"]), "owner_days": sum(1 for r in rows if r["owner"]),
            "recorded": len(days), "not_counted": sum(1 for d in days if not d.get("counted")),
            "owner_outside": sum(len(v) for k, v in by_day.items() if k not in win_days), "days": rows}


def label_lessons(lessons):
    return [l for l in (lessons or {}).values() if (l.get("evidence") or {}).get("src") == "labels"]


def _n(l):
    n = (l.get("evidence") or {}).get("n_closed")
    return n if isinstance(n, int) and not isinstance(n, bool) else None


def _was_hyp(l):
    return l.get("status") == "hyp" or any((h or {}).get("status") == "hyp" for h in l.get("history") or [])


def stage_hyp(lessons, need=HYP_MIN_N, lessons_error=None):
    """A_HYP: урок меток, бывший hyp, с evidence.n_closed >= need. -> {ok, need, n_labels, best, lesson_id, ...}."""
    ls = [l for l in label_lessons(lessons) if _was_hyp(l)]
    ok_ls = sorted((l for l in ls if (_n(l) or 0) >= int(need)), key=lambda l: (-(_n(l) or 0), l.get("lesson_id") or ""))
    best = max(ls, key=lambda l: (_n(l) or 0), default=None)
    rep = {"ok": bool(ok_ls), "need": int(need), "n_labels": len(ls), "n_ok": len(ok_ls), "best_n": _n(best) if best else None,
           "lesson_id": (ok_ls[0] if ok_ls else best or {}).get("lesson_id"), "status": (ok_ls[0] if ok_ls else best or {}).get("status")}
    if lessons_error:
        rep["error"] = lessons_error
    return rep


def stage_verdict(trials):
    """B_VERDICT: закрытые вердиктом цикла испытания. -> {ok, n, by_verdict, open, owner_closed, trial_id}."""
    by, owner, first = {}, 0, None
    for t in trials:
        if t.get("state") != TR.CLOSED or t.get("verdict") not in VERDICTS:
            continue
        nums = t.get("numbers") if isinstance(t.get("numbers"), dict) else {}
        if nums.get("code") == S2.OWNER:
            owner += 1
            continue
        by[t["verdict"]] = by.get(t["verdict"], 0) + 1
        if first is None or (t.get("closed_ts") or 0) < (first.get("closed_ts") or 0):
            first = t
    n = sum(by.values())
    return {"ok": n > 0, "n": n, "by_verdict": by, "open": sum(1 for t in trials if t.get("state") == TR.OPEN), "owner_closed": owner,
            "trial_id": (first or {}).get("trial_id"), "verdict": (first or {}).get("verdict"), "total": len(trials)}


# ---------------------------------------------------------------- причины

def _why_days(a):
    s = "%d из %d нужных полных проходов без вмешательства среди последних %d засчитываемых суток (окно %d)" % (a["clean"], a["need"], a["counted"],
                                                                                                         a["window"])
    extra = []
    if a["recorded"] and a["counted"] < a["window"]:
        extra.append("засчитываемых суток пока %d" % a["counted"])
    if a["full"] > a["clean"]:
        extra.append("суток с вмешательством владельца %d" % a["owner_days"])
    if a["counted"] - a["full"] > 0:
        extra.append("неполных %d" % (a["counted"] - a["full"]))
    if a["not_counted"]:
        extra.append("незасчитываемых (работа < 12 ч) %d — не учитываются" % a["not_counted"])
    if not a["recorded"]:
        extra.append("сводок суток нет: цикл ещё не закрыл ни одних суток")
    return s + ("; " + ", ".join(extra) if extra else "")


def _why_hyp(h):
    if h.get("error"):
        return "уроки не читаются (%s)" % h["error"]
    if h["ok"]:
        return "урок меток %s с n=%d (нужно >= %d), уроков меток с n >= %d: %d" % (h["lesson_id"], h["best_n"], h["need"], h["need"], h["n_ok"])
    if not h["n_labels"]:
        return "уроков меток hyp ещё нет (нужен урок с n >= %d)" % h["need"]
    return "уроков меток hyp %d, наибольшее n=%s %s (нужно >= %d)" % (h["n_labels"], h["best_n"], "(%s)" % h["lesson_id"] if h["lesson_id"] else "",
                                                                     h["need"])


def _why_verdict(b):
    if b["ok"]:
        return "испытаний с вердиктом %d (%s), первое %s" % (b["n"], ", ".join("%s %d" % kv for kv in sorted(b["by_verdict"].items())), b["trial_id"])
    s = "испытаний с вердиктом нет: всего испытаний %d, открытых %d" % (b["total"], b["open"])
    if b["owner_closed"]:
        s += ", закрытых владельцем %d (не вердикт цикла)" % b["owner_closed"]
    return s


# ---------------------------------------------------------------- оценка

def evaluate(conn, now=None, lessons=None, lessons_error=None):
    """Гейт по открытому соединению (только чтение). lessons — {lesson_id: урок} из memory.lessons.read_all. -> отчёт."""
    now = int(time.time() if now is None else now)
    days = V.day_rows(conn)
    owner = owner_events(conn)
    a = stage_days(days, owner, lessons)
    h = stage_hyp(lessons, lessons_error=lessons_error)
    b = stage_verdict(TR.trials(conn))
    kinds = {}
    for ev in owner:
        k = classify(ev, lessons)
        kinds[k] = kinds.get(k, 0) + 1
    conds = {"A_DAYS": dict(a, why=_why_days(a)), "A_HYP": dict(h, why=_why_hyp(h)), "B_VERDICT": dict(b, why=_why_verdict(b))}
    sa, sb = a["ok"] and h["ok"], b["ok"]
    acc = V.day_get(conn) or {}
    return {"ok": sa and sb, "ts": now, "stage_a": sa, "stage_b": sb, "failed": [c for c in CONDS if not conds[c]["ok"]], "conds": conds,
            "owner": {"cycle": kinds.get(CYCLE, 0), "brain_lesson": kinds.get(BRAIN_LESSON, 0), "other": kinds.get(OTHER, 0)},
            "today": {"day": acc.get("day"), "work_h": round(float(acc.get("work_s") or 0) / 3600.0, 2)} if acc.get("day") else None}


def unavailable(code, detail=None, now=None):
    why = REASONS_RU.get(code, code) + ((": %s" % detail) if detail else "")
    conds = dict((c, {"ok": False, "why": why}) for c in CONDS)
    return {"ok": False, "ts": int(time.time() if now is None else now), "stage_a": False, "stage_b": False, "failed": list(CONDS), "conds": conds,
            "db": code, "error": why}


def _load_lessons(root):
    from tb.memory import lessons as LS
    try:
        return LS.read_all(root), None
    except (OSError, ValueError) as e:
        return {}, "%s: %s" % (type(e).__name__, e)


def run(root=None, now=None):
    """Гейт по базе root: соединение только для чтения на вызов. Нет базы / таблиц / идёт восстановление / ошибка чтения -> отчёт с db=<код>."""
    try:
        conn = LST.open_ro(root)
    except LST.KnowledgeRestoring:
        return unavailable(RESTORING, now=now)
    except sqlite3.Error as e:
        return unavailable(DB_ERROR, "%s: %s" % (type(e).__name__, e), now=now)
    if conn is None:
        return unavailable(NO_DB, now=now)
    try:
        if not KM.has_v2(root) or not RV.ready(conn):
            return unavailable(NOT_READY, now=now)
        lessons, lerr = _load_lessons(root)
        rep = evaluate(conn, now=now, lessons=lessons, lessons_error=lerr)
        rep["db"] = "OK"
        return rep
    except (sqlite3.Error, store2.KnowledgeError) as e:
        return unavailable(DB_ERROR, "%s: %s" % (type(e).__name__, e), now=now)
    finally:
        conn.close()


def render(rep):
    """Текст для человека: итог и по строке на каждое условие."""
    yn = lambda v: "да" if v else "нет"  # noqa: E731
    lines = ["гейт этапа R2: %s (ступень A — %s, ступень B — %s)" % ("пройден" if rep["ok"] else "не пройден", yn(rep["stage_a"]), yn(rep["stage_b"]))]
    names = {"A_DAYS": "A  полные проходы без вмешательства", "A_HYP": "A  урок меток hyp с n >= 30", "B_VERDICT": "B  испытание с вердиктом"}
    for c in CONDS:
        d = rep["conds"][c]
        lines.append("%s: %s — %s" % (names[c], yn(d["ok"]), d["why"]))
    if rep.get("owner") and (rep["owner"]["brain_lesson"] or rep["owner"]["other"]):
        lines.append("записи владельца вне объектов цикла (не вмешательство): уроки мозга %d, прочие %d" % (rep["owner"]["brain_lesson"], rep["owner"]["other"]))
    if rep.get("today"):
        lines.append("текущие сутки %s: работа цикла %.1f ч (сводка — после смены суток UTC)" % (rep["today"]["day"], rep["today"]["work_h"]))
    return lines
