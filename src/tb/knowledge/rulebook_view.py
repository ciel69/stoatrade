"""Читатель действующего rulebook для мозга и шлюза (R2, Requirements 2.6, 7.7, 7.12, 7.15, 7.16; design.md, «tb.knowledge.rulebook_view»,
«Надзор за надзором и аварийные средства»). Только чтение: базу открывает на один вызов через labels.store.open_ro и сразу закрывает.

active(root, now)      действующая версия и её правила + единое правило безопасности: правила применяются, только когда одновременно
                       выключатель runtime/rules_switch.json не выключен, база читается и не восстанавливается (нет runtime/knowledge.restoring,
                       нет сигнала повреждения), аренда сторожа runtime/watch_lease.json не старше lease_h; иначе режим без применения (why).
resolve_event(...)     событие scanner_signal монеты для признаков правил: event_seq (только задание раннера, проверка монеты, вида и возраста)
                       или последнее событие монеты вида из scope не старше event_max_age_s; хвост текущего файла шины и предыдущий
                       ротированный, только если его последняя запись моложе event_max_age_s (ротация core/bus.py:451-470).
resolve_event_why(...) то же с причиной для учёта (EVENT_WHY: ok | no_event | out_of_scope | stale) и видом события; rule_eval_why(conn, ...)
                       — счёт причин по rule_evals (доля RULES_NO_EVENT только по no_event и stale) и покрытие правилами (доля вне области).
rule_eval_fields(...)  поля записи decisions kind="note", what="rule_eval"; shadow_hits (вторая итерация, Requirement 18.7) — правила trial и
                       активные в режиме без применения, которые отсекли бы решение (shadow_rules / shadow_hits; решение не меняют).

Кеш в памяти — только данные версии (правила) по (seq, h) последней версии и seq последней строки rule_rows; кеша правил в файлах нет.
Слой: не импортирует tb.learnloop, tb.venues, tb.executor, tb.control, tb.llm, tb.brain, tb.gateway (его зовут brain/cands.py и gateway/catalog.py).
Чтение базы (read_versions, rules_latest, ...) общее с tb.learnloop.versions и ./tb rulebook.
"""
import json
import os
import sqlite3
import threading
import time

from tb import paths
from tb.core import bus
from tb.knowledge import markers as KM
from tb.labels import store as LST
from tb.logic import learn_rules as LR

SWITCH_FILE = "rules_switch.json"
LEASE_FILE = "watch_lease.json"
LEARNLOOP_FILE = "learnloop.json"
WHY = ("OK", "DB", "RESTORING", "LEASE", "SWITCH")
LEASE_FUTURE_SLACK_S = 300                       # аренда «из будущего» дальше этого допуска не считается свежей
NEVER_KINDS = ("idea_stale", "idea_level")       # события идей без рыночных полей (brain/brief.py:1416-1417) правилам не годятся
TAIL_CHUNK = 65536

_CACHE = {}                                      # абсолютный путь базы -> (подпись, данные версии)
_LOCK = threading.Lock()


# ---------------------------------------------------------------- файлы runtime и конфиг

def switch_path(root=None):
    return paths.runtime(SWITCH_FILE, root=root)


def lease_path(root=None):
    return paths.runtime(LEASE_FILE, root=root)


def read_switch(root=None):
    """Выключатель применения: файла нет -> True (по умолчанию правила применяются); {"apply": false} -> False; битый файл или не bool -> False."""
    p = switch_path(root)
    if not os.path.exists(p):
        return True
    d = KM.read_json(p)
    return isinstance(d, dict) and d.get("apply") is True


def read_lease(root=None):
    d = KM.read_json(lease_path(root))
    return d if isinstance(d, dict) else None


def _learn_cfg(root):
    try:
        with open(paths.config(LEARNLOOP_FILE, root=root), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _pos_num(cfg, a, b):
    v = (cfg or {}).get(a)
    v = v.get(b) if isinstance(v, dict) else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def lease_h(root=None):
    """config/learnloop.json lease.lease_h или None (конфиг не читается: аренда не может быть свежей)."""
    return _pos_num(_learn_cfg(root), "lease", "lease_h")


def event_max_age_s(root=None):
    return _pos_num(_learn_cfg(root), "apply", "event_max_age_s")


def lease_fresh(root=None, now=None, hours=None):
    """Аренда сторожа свежа: ts записан и 0 <= now - ts <= lease_h часов (допуск часов LEASE_FUTURE_SLACK_S)."""
    now = time.time() if now is None else now
    hours = lease_h(root) if hours is None else hours
    d = read_lease(root)
    if not hours or not d:
        return False
    ts = d.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return False
    age = now - ts
    return -LEASE_FUTURE_SLACK_S <= age <= float(hours) * 3600.0


# ---------------------------------------------------------------- чтение базы (общее с learnloop.versions и CLI)

def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def ready(conn):
    return _has_table(conn, "rulebook_versions") and _has_table(conn, "rule_rows")


def _dec(v):
    if isinstance(v, str) and v[:1] in ("{", "["):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _rows(cur):
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _version_row(r):
    r = dict(r)
    r["rule_ids"] = _dec(r.get("rule_ids")) or []
    r["numbers"] = _dec(r.get("numbers"))
    return r


def _rule_row(r):
    r = dict(r)
    r["body"] = _dec(r.get("body"))
    if not isinstance(r["body"], dict):
        r["body"] = {}
    return r


def read_versions(conn):
    """Все версии rulebook по возрастанию seq (rule_ids и numbers разобраны)."""
    if not _has_table(conn, "rulebook_versions"):
        return []
    return [_version_row(r) for r in _rows(conn.execute("SELECT * FROM rulebook_versions ORDER BY seq"))]


def last_version(conn):
    if not _has_table(conn, "rulebook_versions"):
        return None
    rows = _rows(conn.execute("SELECT * FROM rulebook_versions ORDER BY seq DESC LIMIT 1"))
    return _version_row(rows[0]) if rows else None


def get_version(conn, ver):
    if not _has_table(conn, "rulebook_versions"):
        return None
    rows = _rows(conn.execute("SELECT * FROM rulebook_versions WHERE ver=?", (ver,)))
    return _version_row(rows[0]) if rows else None


def rule_revs(conn, rule_id):
    """Все ревизии правила по возрастанию seq."""
    if not _has_table(conn, "rule_rows"):
        return []
    return [_rule_row(r) for r in _rows(conn.execute("SELECT * FROM rule_rows WHERE rule_id=? ORDER BY seq", (rule_id,)))]


def rules_latest(conn):
    """{rule_id: последняя ревизия} (текущее состояние правила — последняя строка append-only таблицы)."""
    if not _has_table(conn, "rule_rows"):
        return {}
    cur = conn.execute("SELECT * FROM rule_rows WHERE seq IN (SELECT MAX(seq) FROM rule_rows GROUP BY rule_id) ORDER BY rule_id")
    return dict((r["rule_id"], _rule_row(r)) for r in _rows(cur))


def row_rule(row):
    """Строка rule_rows -> правило для learn_rules.apply."""
    b = row.get("body") or {}
    return {"rule_id": row.get("rule_id"), "kind": row.get("kind"), "status": row.get("status"), "cond": b.get("cond"), "param": b.get("param") or {},
            "scope": b.get("scope"), "env_norm": row.get("env_norm"), "cond_h": b.get("cond_h"), "from_lesson": row.get("from_lesson"),
            "rev": row.get("rev"), "wait": row.get("wait"), "reason_code": row.get("reason_code")}


def _signature(conn):
    v = conn.execute("SELECT seq, h FROM rulebook_versions ORDER BY seq DESC LIMIT 1").fetchone()
    r = conn.execute("SELECT MAX(seq) FROM rule_rows").fetchone()
    return (v[0] if v else None, v[1] if v else None, r[0] if r else None)


def active_from_conn(conn):
    """Действующая версия на соединении: {"ver", "seq", "rules": [годные active], "invalid": [rule_id]}. Правило версии применяется, только если
    его последняя ревизия active и оно годно по learn_rules.validate_rule (неизвестный вид отклоняется и при чтении)."""
    if not ready(conn):
        return {"ver": None, "seq": None, "rules": [], "invalid": [], "trial": []}
    v = last_version(conn)
    latest = rules_latest(conn)
    trial = sorted((row_rule(r) for r in latest.values() if r.get("status") == "trial"), key=lambda x: str(x["rule_id"]))
    trial = [r for r in trial if not LR.validate_rule(dict(r, status="active"))]
    if v is None:
        return {"ver": None, "seq": None, "rules": [], "invalid": [], "trial": trial}
    rules, invalid = [], []
    for rid in v["rule_ids"] if isinstance(v["rule_ids"], list) else []:
        row = latest.get(rid)
        if row is None or row.get("status") != "active":
            invalid.append(rid)
            continue
        r = row_rule(row)
        if LR.validate_rule(r):
            invalid.append(rid)
            continue
        rules.append(r)
    rules.sort(key=lambda x: x["rule_id"])
    return {"ver": v["ver"], "seq": v["seq"], "rules": rules, "invalid": sorted(invalid), "trial": trial}


def _load(root):
    """Данные версии на один вызов: open_ro, чтение (с кешем по подписи), закрытие. -> данные или None (нет файла базы).
    KnowledgeRestoring и sqlite3.Error пробрасываются."""
    conn = LST.open_ro(root)
    if conn is None:
        return None
    try:
        if not ready(conn):
            return {"ver": None, "seq": None, "rules": [], "invalid": []}
        key = os.path.abspath(LST.db_path(root))
        sig = _signature(conn)
        with _LOCK:
            hit = _CACHE.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
        data = active_from_conn(conn)
        with _LOCK:
            _CACHE[key] = (sig, data)
        return data
    finally:
        conn.close()


def clear_cache():
    with _LOCK:
        _CACHE.clear()


# ---------------------------------------------------------------- единое правило безопасности

def active(root=None, now=None, hours=None):
    """-> {"ver", "rules", "apply": bool, "why": "OK"|"DB"|"RESTORING"|"LEASE"|"SWITCH", "kinds", "invalid"}.

    apply=True только при why="OK". Порядок причин: SWITCH (выключатель, не зависит от базы) > RESTORING > DB (нет файла, нет таблиц цикла,
    ошибка чтения, сигнал повреждения или прерванное восстановление) > LEASE. Правила версии отдаются и в режиме без применения, если база
    читается (для блока досье и страницы); при недоступной базе rules пусты."""
    now = time.time() if now is None else now
    view = {"ver": None, "rules": [], "apply": False, "why": "DB", "kinds": [], "invalid": [], "trial": []}
    switch_on = read_switch(root)
    restoring = KM.restoring(root)
    db_ok = False
    if not restoring:
        try:
            data = _load(root)
        except LST.KnowledgeRestoring:
            restoring = True
            data = None
        except sqlite3.Error:
            data = None
        if data is not None:
            view.update(ver=data["ver"], rules=[dict(r) for r in data["rules"]], invalid=list(data["invalid"]),
                        trial=[dict(r) for r in data.get("trial") or []])
            db_ok = data["ver"] is not None
    if KM.read_corrupt(root) is not None or os.path.exists(KM.restore_failed_path(root)):
        db_ok = False
    view["kinds"] = LR.scope_kinds(view["rules"] + [dict(r, status="active") for r in view["trial"]])
    if not switch_on:
        view["why"] = "SWITCH"
    elif restoring:
        view["why"] = "RESTORING"
    elif not db_ok:
        view["why"] = "DB"
    elif not lease_fresh(root, now, hours):
        view["why"] = "LEASE"
    else:
        view["why"] = "OK"
    view["apply"] = view["why"] == "OK"
    return view


# ---------------------------------------------------------------- событие решения

def _parse_lines(data):
    out = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and isinstance(e.get("seq"), int) and not isinstance(e.get("seq"), bool) and isinstance(e.get("type"), str):
            out.append(e)
    return out


def _ts(e):
    return bus.parse_iso(e.get("ts"))


def _tail_since(path, since, chunk=TAIL_CHUNK):
    """События хвоста файла шины, читая с конца кусками, пока не встретится событие старше since (или начало файла)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    n = chunk
    while True:
        try:
            with open(path, "rb") as f:
                if size > n:
                    f.seek(size - n)
                    f.readline()                          # первая строка куска может быть неполной
                data = f.read()
        except OSError:
            return []
        evs = _parse_lines(data)
        if size <= n:
            return evs
        tss = [t for t in (_ts(e) for e in evs) if t is not None]
        if tss and min(tss) < since:
            return evs
        n *= 4


def _prev_rotated(root):
    d = paths.bus(root=root)
    try:
        names = os.listdir(d)
    except OSError:
        return None
    keyed = sorted((bus._rot_key(n), n) for n in names if bus._rot_key(n))
    return os.path.join(d, keyed[-1][1]) if keyed else None


def recent_events(root, now, max_age_s):
    """События шины с ts >= now - max_age_s: хвост текущего файла и предыдущий ротированный, только если его последняя запись (mtime) моложе
    max_age_s (иначе в нём нет событий моложе порога). Старые файлы не сканируются."""
    since = now - float(max_age_s)
    files = []
    prev = _prev_rotated(root)
    if prev:
        try:
            if os.path.getmtime(prev) >= since:
                files.append(prev)
        except OSError:
            pass
    files.append(paths.bus_events(root=root))
    by_seq = {}
    for p in files:
        for e in _tail_since(p, since):
            t = _ts(e)
            if t is not None and t >= since:
                by_seq.setdefault(e["seq"], e)
    return [by_seq[s] for s in sorted(by_seq)]


def _kind_ok(kind, kinds):
    if not isinstance(kind, str) or kind in NEVER_KINDS:
        return False
    return kind in kinds or "*" in kinds


def _event_ok(e, sym, kinds, now, max_age_s):
    if not isinstance(e, dict) or e.get("type") != "scanner_signal" or e.get("sym") != sym:
        return False
    d = e.get("d")
    if not isinstance(d, dict) or not _kind_ok(d.get("kind"), kinds):
        return False
    t = _ts(e)
    return t is not None and now - t <= float(max_age_s)


def resolve_event(root, sym, event_seq, now, kinds, max_age_s=None):
    """Событие для признаков правил решения (монета sym) -> конверт шины {"seq", "ts", "type", "sym", "d"} или None.

    event_seq (передавать только из задания раннера entry_scout; для chat — None) принимается, только если это scanner_signal той же монеты, вида
    из kinds и не старше event_max_age_s; иначе — последнее такое событие монеты; иначе None (правила к решению не применяются, их идентификаторы
    уходят в feat_missing_ids). События idea_stale и idea_level не берутся никогда. max_age_s по умолчанию — config/learnloop.json
    apply.event_max_age_s (конфиг не читается -> None). Причину None даёт resolve_event_why."""
    return resolve_event_why(root, sym, event_seq, now, kinds, max_age_s)["event"]


# причина события записи rule_eval (basis.event_why, колонка rule_evals.event_why): ok — правила применены к событию; no_event — события нет
# (сбой: нет события монеты, не найдено, чужая монета, idea_*, конфиг); out_of_scope — событие есть, но его вид вне области правил (норма:
# правила на такие входы не распространяются); stale — событие вида из области правил старше event_max_age_s (запаздывание)
EVENT_WHY = ("ok", "no_event", "out_of_scope", "stale")
EVENT_WHY_FAIL = ("no_event", "stale")           # входят в долю RULES_NO_EVENT; out_of_scope — нет
STALE_LOOKBACK_X = 24                            # окно поиска event_seq задания для причины stale: event_max_age_s x 24 (6 ч при 900 с)


def _sig_kind(e, sym):
    """Вид scanner_signal монеты sym (не idea_*) или None."""
    if not isinstance(e, dict) or e.get("type") != "scanner_signal" or e.get("sym") != sym:
        return None
    d = e.get("d")
    k = d.get("kind") if isinstance(d, dict) else None
    return k if isinstance(k, str) and k and k not in NEVER_KINDS else None


def _why_of(e, sym, kinds):
    """Причина для события, не прошедшего _event_ok: вид вне kinds -> out_of_scope, вид в kinds (значит, по возрасту) -> stale, иначе no_event."""
    k = _sig_kind(e, sym)
    if k is None:
        return "no_event", None
    return ("stale" if _kind_ok(k, kinds) else "out_of_scope"), k


def resolve_event_why(root, sym, event_seq, now, kinds, max_age_s=None):
    """resolve_event с причиной -> {"event": конверт|None, "why": EVENT_WHY, "kind": вид события|None}. Решение о событии то же, что у
    resolve_event; причина только для учёта. Событие нашлось -> ok. Иначе при заданном event_seq — по событию задания (ищется и в окне
    event_max_age_s x STALE_LOOKBACK_X: вид вне kinds -> out_of_scope, вид из kinds старше порога -> stale, не найдено / чужая монета / idea_* ->
    no_event); без event_seq — последнее scanner_signal монеты в окне порога: вид вне kinds -> out_of_scope, нет -> no_event."""
    now = time.time() if now is None else now
    max_age_s = event_max_age_s(root) if max_age_s is None else max_age_s
    kinds = [k for k in (kinds or []) if isinstance(k, str)]
    out = {"event": None, "why": "no_event", "kind": None}
    if not max_age_s or not isinstance(sym, str) or not sym or not kinds:
        return out
    evs = recent_events(root, now, max_age_s)
    given = isinstance(event_seq, int) and not isinstance(event_seq, bool)
    seen = None
    if given:
        for e in evs:
            if e["seq"] == event_seq:
                seen = e
                if _event_ok(e, sym, kinds, now, max_age_s):
                    return {"event": e, "why": "ok", "kind": _sig_kind(e, sym)}
                break
    best = None
    for e in evs:
        if _event_ok(e, sym, kinds, now, max_age_s) and (best is None or e["seq"] > best["seq"]):
            best = e
    if best is not None:
        return {"event": best, "why": "ok", "kind": _sig_kind(best, sym)}
    if given:
        if seen is None:
            seen = next((e for e in recent_events(root, now, float(max_age_s) * STALE_LOOKBACK_X) if e["seq"] == event_seq), None)
        if seen is None:
            return out
        why, k = _why_of(seen, sym, kinds)
        return dict(out, why=why, kind=k)
    last = None
    for e in evs:
        if _sig_kind(e, sym) is not None and (last is None or e["seq"] > last["seq"]):
            last = e
    if last is not None and not _kind_ok(_sig_kind(last, sym), kinds):
        return dict(out, why="out_of_scope", kind=_sig_kind(last, sym))
    return out


def rule_eval_why(conn, now, window_s, roles=None):
    """Учёт причин событий записей rule_eval за окно (строки rule_id="" таблицы rule_evals; общий для цикла и вкладки «Память»).
    -> {"n", "fail", "why": {ok, no_event, out_of_scope, stale, legacy}, "by_kind": {вид: n вне области}, "coverage": {...}}.
    fail — no_event + stale (+ прежние строки без event_why с no_event=1): числитель RULES_NO_EVENT. coverage — по строкам с известной
    причиной ролей roles (по умолчанию entry_scout, chat): n, in_scope, out_of_scope, share (доля вне области), by_kind."""
    t0 = int(now) - int(window_s)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(rule_evals)").fetchall()]
    has = "event_why" in cols and "event_kind" in cols
    why = dict((w, 0) for w in EVENT_WHY)
    why["legacy"] = 0
    n = fail = 0
    roles = tuple(roles or ("entry_scout", "chat"))
    cov = {"n": 0, "in_scope": 0, "out_of_scope": 0, "by_kind": {}}
    q = ("SELECT event_why, event_kind, no_event, role, COUNT(*) FROM rule_evals WHERE rule_id='' AND ts>=? GROUP BY event_why, event_kind, no_event, role"
         if has else "SELECT NULL, NULL, no_event, role, COUNT(*) FROM rule_evals WHERE rule_id='' AND ts>=? GROUP BY no_event, role")
    by_kind = {}
    for w, k, ne, role, c in conn.execute(q, (t0,)).fetchall():
        c = int(c or 0)
        n += c
        if w not in EVENT_WHY:
            why["legacy"] += c
            fail += c if ne else 0
            continue
        why[w] += c
        fail += c if w in EVENT_WHY_FAIL else 0
        if w == "out_of_scope":
            kk = k if isinstance(k, str) and k else "?"
            by_kind[kk] = by_kind.get(kk, 0) + c
        if role in roles:
            cov["n"] += c
            if w == "out_of_scope":
                cov["out_of_scope"] += c
                kk = k if isinstance(k, str) and k else "?"
                cov["by_kind"][kk] = cov["by_kind"].get(kk, 0) + c
            else:
                cov["in_scope"] += c
    cov["share"] = round(float(cov["out_of_scope"]) / cov["n"], 4) if cov["n"] else None
    return {"n": n, "fail": fail, "why": why, "by_kind": by_kind, "coverage": cov}


def coverage_txt(cov):
    """Строка покрытия правилами: «вне области правил: 31% (patrol 20, book_wall 5) из 80 решений за 7 суток» или None (нет данных)."""
    if not isinstance(cov, dict) or not cov.get("n") or cov.get("share") is None:
        return None
    kinds = sorted((cov.get("by_kind") or {}).items(), key=lambda kv: (-kv[1], kv[0]))
    det = ", ".join("%s %d" % kv for kv in kinds[:5])
    return "вне области правил: %.0f%%%s из %d решений" % (cov["share"] * 100, (" (%s)" % det) if det else "", cov["n"])


# ---------------------------------------------------------------- запись rule_eval

RULE_EVAL_KEYS = ("role", "cands_ok_n", "event_seq", "rule_hits", "rule_eval_ids", "feat_missing_ids", "rulebook_ver", "plan", "shadow_hits",
                  "event_why", "event_kind")


def shadow_rules(view):
    """Правила совпадений без применения (вторая итерация, Requirement 18.7): правила trial и, в режиме без применения (apply=false), активные
    правила версии. -> список правил в форме для learn_rules.apply (status="active": оцениваются, но решение не меняют)."""
    view = view if isinstance(view, dict) else {}
    out = [dict(r, status="active") for r in view.get("trial") or [] if isinstance(r, dict)]
    if not view.get("apply"):
        out += [dict(r, status="active") for r in view.get("rules") or [] if isinstance(r, dict)]
    return out


def shadow_hits(view, cand, feats, ctx=None):
    """Идентификаторы правил shadow_rules(view), которые отсекли бы решение (cand, feats). Нет таких правил -> []. Решение не меняется."""
    rules = shadow_rules(view)
    if not rules:
        return []
    res = LR.apply(rules, cand, feats, dict(ctx or {}, apply=True))
    return sorted(set(res.get("hits") or []))


def rule_eval_fields(view, result, ctx):
    """Поля basis записи decisions kind="note", what="rule_eval" (Requirement 7.7): role, cands_ok_n, event_seq, rule_hits, rule_eval_ids,
    feat_missing_ids, rulebook_ver, plan, shadow_hits (вторая итерация, Requirement 18.7: result["shadow_hits"], иначе []), event_why
    (EVENT_WHY из ctx["event_why"]; нет -> ok при событии, иначе None) и event_kind (вид события, если известен).
    view — active(root), result — learn_rules.apply (+ shadow_hits), ctx — {"role", "cands_ok_n", "event"|"event_seq", "plan", "event_why", "event_kind"}."""
    ctx = ctx or {}
    result = result or {}
    ev = ctx.get("event")
    seq = ev.get("seq") if isinstance(ev, dict) else ctx.get("event_seq")
    seq = seq if isinstance(seq, int) and not isinstance(seq, bool) else None
    cok = ctx.get("cands_ok_n")
    why = ctx.get("event_why")
    why = why if why in EVENT_WHY else ("ok" if seq is not None else None)
    kind = ctx.get("event_kind")
    if not (isinstance(kind, str) and kind) and isinstance(ev, dict) and isinstance(ev.get("d"), dict):
        kind = ev["d"].get("kind")
    return {"role": ctx.get("role"), "cands_ok_n": cok if isinstance(cok, int) and not isinstance(cok, bool) else None,
            "event_seq": seq,
            "rule_hits": list(result.get("hits") or []), "rule_eval_ids": list(result.get("eval") or []),
            "feat_missing_ids": list(result.get("missing") or []), "rulebook_ver": (view or {}).get("ver"), "plan": ctx.get("plan"),
            "shadow_hits": list(result.get("shadow_hits") or []), "event_why": why,
            "event_kind": kind[:24] if isinstance(kind, str) and kind else None}
