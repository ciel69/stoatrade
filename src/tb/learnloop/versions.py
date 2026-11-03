"""Версии rulebook и статусы правил (R2, Requirements 7.1, 7.2, 7.3, 7.6, 7.9, 8.6; design.md, «tb.learnloop.rules / versions / ...», «Коды причин»).

Версии R-NNNN (R-0001 — пустой rulebook, INIT) в rulebook_versions (append-only, хеш-цепочка store2.append_row); правила в rule_rows (append-only:
каждая смена статуса или ожидания — новая ревизия rev). Статусы trial | active | disabled | rejected; rejected — правило не было активным
(TRIAL_REJECT, NO_VERDICT, UNREACHABLE_N, LOSS_CAP, SAMPLE_CHANGED, CTX_CHANGED, RESTORED, OWNER), disabled — было активным и выключено
(ROLLBACK, TRADE_STARVATION, OWNER_DISABLE, BOUNDS_BLOCKED). Поле ожидания wait: READY | MAX_ACTIVE | LOSS_CAP (wait_since_ts — начало текущего
непрерывного ожидания с этой причиной).

create_with_trial   правило trial и строка trials одной транзакцией BEGIN IMMEDIATE; не больше одного не-терминального (trial/active) правила
                    на урок — проверка внутри транзакции; условие из чёрного списка отклоняется
write_version       новая версия вместе с изменениями rule_rows (и записями чёрного списка) одной транзакцией, затем проверенная копия базы
promote / disable   продвижение (только ужесточение: learn_rules.is_tightening) и выключение активного правила новой версией
reject_rule         отклонение правила trial без смены версии (чёрный список по коду)
bounds_check        активное правило, не годное по learn_rules.validate_rule (неизвестный или размерный вид), выключается версией BOUNDS_BLOCKED
rules_ver (config/memory.json) здесь не меняется никогда (Requirement 7.6).
promotion_ready     условия готовности продвижения (прогрев, Requirement 10.6; задача 10): засчитываемые сутки, события, сторож, серии STEP_ERROR,
                    копия и integrity_check. Счёт суток и состояние шагов пишет tb.learnloop.cycle (meta learnloop_day / learnloop_state,
                    строки learn_events step="day").
"""
import contextlib
import datetime
import json
import re
import time

from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.labels import store as LST
from tb.learnloop import rules as RL
from tb.logic import learn_rules as LR

VERSION_REASONS = ("INIT", "PROMOTE", "ROLLBACK", "TRADE_STARVATION", "OWNER_DISABLE", "BOUNDS_BLOCKED")
DISABLE_CODES = ("ROLLBACK", "TRADE_STARVATION", "OWNER_DISABLE", "BOUNDS_BLOCKED")
REJECT_CODES = ("TRIAL_REJECT", "NO_VERDICT", "UNREACHABLE_N", "LOSS_CAP", "SAMPLE_CHANGED", "CTX_CHANGED", "RESTORED", "OWNER")
WAITS = ("READY", "MAX_ACTIVE", "LOSS_CAP")
NON_TERMINAL = ("trial", "active")
TRIAL_CODE = "TRIAL"
BACKUP_REASON = {"PROMOTE": "promote", "ROLLBACK": "rollback", "TRADE_STARVATION": "rollback", "BOUNDS_BLOCKED": "rollback", "OWNER_DISABLE": "owner"}
FIRST_VER = "R-0001"
RE_VER = re.compile(r"^R-(\d{4,})$")
TRIAL_STATE_OPEN = "open"


class RuleRefused(Exception):
    """Отказ операции с правилом: DUP_RULE, BLOCKED, BAD_RULE, BAD_STATUS, NOT_TIGHTENING, NO_RULE, BAD_REASON."""

    def __init__(self, code, msg=""):
        self.code = code
        super().__init__("%s: %s" % (code, msg) if msg else code)


# ---------------------------------------------------------------- имена и транзакции

def ver_name(n):
    return "R-%04d" % int(n)


def ver_num(ver):
    m = RE_VER.match(ver or "")
    return int(m.group(1)) if m else 0


@contextlib.contextmanager
def _tx(conn):
    """BEGIN IMMEDIATE, если вызывающий не открыл транзакцию; иначе внутри его транзакции. -> True, если транзакция своя."""
    if conn.in_transaction:
        yield False
        return
    with LST.tx(conn):
        yield True


def _num_id(rid, prefix):
    try:
        return int(str(rid)[len(prefix):]) if str(rid).startswith(prefix) else 0
    except ValueError:
        return 0


def next_rule_id(conn):
    n = max([_num_id(r[0], "RL") for r in conn.execute("SELECT DISTINCT rule_id FROM rule_rows")] or [0])
    return "RL%04d" % (n + 1)


def trial_id_for(rule_id):
    return "TR" + str(rule_id)[2:]


# ---------------------------------------------------------------- чтение

def current(conn):
    """Последняя версия (или None, если R-0001 ещё не записана)."""
    return RV.last_version(conn)


def rule(conn, rule_id):
    """Последняя ревизия правила (body разобран) или None."""
    revs = RV.rule_revs(conn, rule_id)
    return revs[-1] if revs else None


def active_rules(conn):
    """Правила действующей версии (как их применяет rulebook_view; без проверки аренды и выключателя)."""
    return RV.active_from_conn(conn)["rules"]


def non_terminal_for_lesson(conn, lesson_id):
    return sorted(rid for rid, r in RV.rules_latest(conn).items() if r.get("from_lesson") == lesson_id and r.get("status") in NON_TERMINAL)


# ---------------------------------------------------------------- запись ревизий правил

def _append_rev(conn, prev, now, **chg):
    """Новая ревизия правила на основе prev (последней ревизии) с изменёнными колонками."""
    row = {"rule_id": prev["rule_id"], "rev": int(prev["rev"]) + 1, "kind": prev["kind"], "status": prev["status"], "wait": prev.get("wait"),
           "wait_since_ts": prev.get("wait_since_ts"), "from_lesson": prev["from_lesson"], "env_norm": prev["env_norm"],
           "rulebook_ver_in": prev.get("rulebook_ver_in"), "rulebook_ver_out": prev.get("rulebook_ver_out"), "reason_code": prev.get("reason_code"),
           "body": prev.get("body") or {}, "ts": store2.iso(now)}
    row.update(chg)
    return store2.append_row(conn, "rule_rows", row)


def init_rulebook(conn, now):
    """R-0001 (пустой rulebook, INIT), если версий нет. Идемпотентно. -> последняя версия."""
    with _tx(conn):
        v = current(conn)
        if v is not None:
            return v
        store2.append_row(conn, "rulebook_versions", {"ver": FIRST_VER, "parent": None, "reason_code": "INIT", "trial_id": None, "rule_ids": [],
                                                      "numbers": None, "ts": store2.iso(now)})
        return current(conn)


def create_with_trial(conn, rule_draft, trial, now, cfg=None):
    """Правило trial (ревизия 1, reason TRIAL) и испытание одной транзакцией BEGIN IMMEDIATE (Requirement 7.9).

    rule_draft — learnloop.rules.derive (cond, scope, env_norm, cond_h, from_lesson, policy_ver); trial — колонки trials без trial_id/rule_id/lesson_id
    (criterion, criterion_h, policy_id, policy_ver, ctx_ver, start_ts, deadline_ts, env_norm; state по умолчанию open). В транзакции: не-терминальное
    правило урока уже есть -> RuleRefused(DUP_RULE); семейство block_h (или прежний cond_h) в чёрном списке -> BLOCKED; правило не годно -> BAD_RULE. -> {"rule_id", "trial_id"}."""
    lesson_id = rule_draft.get("from_lesson")
    if not lesson_id:
        raise RuleRefused("BAD_RULE", "нет from_lesson")
    with _tx(conn):
        dup = non_terminal_for_lesson(conn, lesson_id)
        if dup:
            raise RuleRefused("DUP_RULE", "у урока %s уже есть правило %s" % (lesson_id, ",".join(dup)))
        ch = rule_draft.get("cond_h") or LR.cond_h(rule_draft["cond"], "lower")
        blk = RL.blocked(conn, rule_draft["cond"], now)
        if blk is not None:
            raise RuleRefused("BLOCKED", "условие %s в чёрном списке (%s)" % (blk["key"], blk["code"]))
        rid = next_rule_id(conn)
        cand = dict(rule_draft, rule_id=rid, status="trial")
        errs = LR.validate_rule(cand, bool((cfg or {}).get("allow_size_rules")))
        if errs:
            raise RuleRefused("BAD_RULE", "; ".join(errs[:3]))
        tid = trial_id_for(rid)
        body = {"cond": LR.canon_cond(cand["cond"]), "param": cand.get("param") or {}, "scope": cand["scope"], "cond_h": ch,
                "policy_ver": cand.get("policy_ver"), "trial_id": tid}
        store2.append_row(conn, "rule_rows", {"rule_id": rid, "rev": 1, "kind": cand["kind"], "status": "trial", "wait": None, "wait_since_ts": None,
                                              "from_lesson": lesson_id, "env_norm": cand.get("env_norm") or "mainnet", "rulebook_ver_in": None,
                                              "rulebook_ver_out": None, "reason_code": TRIAL_CODE, "body": body, "ts": store2.iso(now)})
        t = dict(trial or {})
        t.update(trial_id=tid, rule_id=rid, lesson_id=lesson_id)
        t.setdefault("state", TRIAL_STATE_OPEN)
        t.setdefault("env_norm", cand.get("env_norm") or "mainnet")
        store2.insert_rows(conn, "trials", [t])
    return {"rule_id": rid, "trial_id": tid}


def set_wait(conn, rule_id, wait, now):
    """Ожидание продвижения правила trial (READY | MAX_ACTIVE | LOSS_CAP | None). Та же причина -> ничего не пишет (None).
    wait_since_ts — начало текущего непрерывного ожидания с этой причиной."""
    if wait is not None and wait not in WAITS:
        raise RuleRefused("BAD_REASON", str(wait))
    with _tx(conn):
        prev = rule(conn, rule_id)
        if prev is None:
            raise RuleRefused("NO_RULE", rule_id)
        if prev["status"] != "trial":
            raise RuleRefused("BAD_STATUS", "%s: %s" % (rule_id, prev["status"]))
        if prev.get("wait") == wait:
            return None
        return _append_rev(conn, prev, now, wait=wait, wait_since_ts=None if wait is None else int(now))


def _block_rule(conn, prev, code, now, cfg, reason=None):
    """Запись чёрного списка по семейству условия правила (block_h из body.cond; тела без cond — прежний ключ body.cond_h)."""
    b = prev.get("body") or {}
    key = b.get("cond") if isinstance(b.get("cond"), list) and b.get("cond") else b.get("cond_h")
    if key and cfg is not None:
        return RL.block(conn, key, code, now, cfg, reason=reason, backup=False)
    return None


def reject_rule(conn, rule_id, code, now, cfg, reason=None):
    """Правило trial -> rejected с кодом (без смены версии); запись чёрного списка по таблице кодов (NO_VERDICT/OVERLAP, LOSS_CAP, CTX_CHANGED,
    RESTORED, OWNER — без записи). Уже rejected -> None (идемпотентно)."""
    if code not in REJECT_CODES:
        raise RuleRefused("BAD_REASON", str(code))
    with _tx(conn):
        prev = rule(conn, rule_id)
        if prev is None:
            raise RuleRefused("NO_RULE", rule_id)
        if prev["status"] == "rejected":
            return None
        if prev["status"] != "trial":
            raise RuleRefused("BAD_STATUS", "%s: %s -> rejected" % (rule_id, prev["status"]))
        row = _append_rev(conn, prev, now, status="rejected", reason_code=code, wait=None, wait_since_ts=None)
        _block_rule(conn, prev, code, now, cfg, reason)
        return row


# ---------------------------------------------------------------- версии

def write_version(conn, reason_code, rule_ids, now, trial_id=None, numbers=None, changes=(), cfg=None, root=None, backup=True, block_reason=None):
    """Новая версия rulebook (Requirement 7.3): строка rulebook_versions, ревизии rule_rows из changes [(rule_id, status, code)] и записи чёрного
    списка по кодам выключения — одной транзакцией; затем проверенная копия базы (store2.make_backup с причиной по коду), если транзакция своя и
    задан root. PROMOTE — только ужесточение (learn_rules.is_tightening), коды выключения — только подмножество прежних правил.
    -> {"ver", "parent", "rule_ids", "backup", "backup_error"}."""
    if reason_code not in VERSION_REASONS or reason_code == "INIT":
        raise RuleRefused("BAD_REASON", str(reason_code))
    new_ids = sorted(set(rule_ids))
    with _tx(conn) as own:
        cur = init_rulebook(conn, now)
        old_ids = sorted(cur["rule_ids"] or [])
        before = active_rules(conn)
        latest = RV.rules_latest(conn)
        ch = dict((rid, (st, code)) for rid, st, code in changes)
        if reason_code == "PROMOTE":
            if not set(old_ids) <= set(new_ids):
                raise RuleRefused("NOT_TIGHTENING", "продвижение не может убрать правила")
            after = list(before)
            for rid in new_ids:
                if rid in old_ids:
                    continue
                row = latest.get(rid)
                if row is None or ch.get(rid, (None,))[0] != "active":
                    raise RuleRefused("BAD_STATUS", "%s: нет правила или изменения на active" % rid)
                after.append(dict(RV.row_rule(row), status="active"))
            if not LR.is_tightening(before, after, allow_size_rules=bool((cfg or {}).get("allow_size_rules"))):
                raise RuleRefused("NOT_TIGHTENING", "набор после продвижения не ужесточает прежний")
        elif not set(new_ids) <= set(old_ids):
            raise RuleRefused("BAD_STATUS", "%s не может добавить правила" % reason_code)
        ver = ver_name(ver_num(cur["ver"]) + 1)
        store2.append_row(conn, "rulebook_versions", {"ver": ver, "parent": cur["ver"], "reason_code": reason_code, "trial_id": trial_id,
                                                      "rule_ids": new_ids, "numbers": numbers, "ts": store2.iso(now)})
        for rid, (st, code) in sorted(ch.items()):
            prev = latest.get(rid)
            if prev is None:
                raise RuleRefused("NO_RULE", rid)
            if st == "active":
                if prev["status"] != "trial":
                    raise RuleRefused("BAD_STATUS", "%s: %s -> active" % (rid, prev["status"]))
                _append_rev(conn, prev, now, status="active", reason_code=code, rulebook_ver_in=ver, wait=None, wait_since_ts=None)
            elif st == "disabled":
                if prev["status"] != "active":
                    raise RuleRefused("BAD_STATUS", "%s: %s -> disabled" % (rid, prev["status"]))
                if code not in DISABLE_CODES:
                    raise RuleRefused("BAD_REASON", str(code))
                _append_rev(conn, prev, now, status="disabled", reason_code=code, rulebook_ver_out=ver)
                _block_rule(conn, prev, code, now, cfg, block_reason)
            elif st == "rejected":
                if prev["status"] != "trial" or code not in REJECT_CODES:
                    raise RuleRefused("BAD_STATUS", "%s: %s -> rejected (%s)" % (rid, prev["status"], code))
                _append_rev(conn, prev, now, status="rejected", reason_code=code, wait=None, wait_since_ts=None)
                _block_rule(conn, prev, code, now, cfg, block_reason)
            else:
                raise RuleRefused("BAD_STATUS", "%s: %s" % (rid, st))
    rep = {"ver": ver, "parent": cur["ver"], "rule_ids": new_ids, "backup": None, "backup_error": None, "backup_pending": not own}
    if own and backup and root is not None and reason_code in BACKUP_REASON:
        try:
            rep["backup"] = store2.make_backup(root, BACKUP_REASON[reason_code], now=now)
        except (store2.KnowledgeError, OSError) as e:          # версия уже записана; копия — следующим суточным шагом
            rep["backup_error"] = str(e)
    return rep


def promote(conn, rule_id, now, cfg=None, root=None, trial_id=None, numbers=None, backup=True):
    """Продвинуть правило trial: версия PROMOTE с правилом active (rulebook_ver_in). Условия готовности, LOSS_CAP, лимит и пересечения проверяет
    вызывающий (задача 15)."""
    cur = current(conn)
    ids = list((cur or {}).get("rule_ids") or [])
    if rule_id in ids:
        raise RuleRefused("BAD_STATUS", "%s уже в rulebook" % rule_id)
    prev = rule(conn, rule_id)
    tid = trial_id or ((prev or {}).get("body") or {}).get("trial_id")
    return write_version(conn, "PROMOTE", ids + [rule_id], now, trial_id=tid, numbers=numbers, changes=[(rule_id, "active", "PROMOTE")], cfg=cfg,
                         root=root, backup=backup)


def disable(conn, rule_ids, code, now, cfg, root=None, numbers=None, backup=True):
    """Выключить активные правила новой версией без них (ROLLBACK, TRADE_STARVATION, OWNER_DISABLE, BOUNDS_BLOCKED): статус disabled,
    rulebook_ver_out, чёрный список (OWNER_DISABLE — бессрочно), копия. Правило не в действующей версии -> RuleRefused(BAD_STATUS)."""
    if code not in DISABLE_CODES:
        raise RuleRefused("BAD_REASON", str(code))
    rule_ids = sorted(set([rule_ids] if isinstance(rule_ids, str) else rule_ids))
    cur = current(conn)
    ids = list((cur or {}).get("rule_ids") or [])
    miss = [r for r in rule_ids if r not in ids]
    if miss:
        raise RuleRefused("BAD_STATUS", "не в действующей версии: %s" % ",".join(miss))
    return write_version(conn, code, [r for r in ids if r not in rule_ids], now, numbers=numbers,
                         changes=[(r, "disabled", code) for r in rule_ids], cfg=cfg, root=root, backup=backup)


def bounds_check(conn, now, cfg, root=None):
    """Правила действующей версии, которые не годны по learn_rules.validate_rule (неизвестный или размерный вид, условие вне словаря) или чья
    последняя ревизия не active, не применяются (rulebook_view) и выключаются версией BOUNDS_BLOCKED (Requirement 8.6). -> отчёт версии или None."""
    cur = current(conn)
    if cur is None or not cur.get("rule_ids"):
        return None
    allow = bool((cfg or {}).get("allow_size_rules"))
    latest = RV.rules_latest(conn)
    bad = []
    for rid in cur["rule_ids"]:
        row = latest.get(rid)
        if row is None or row.get("status") != "active" or LR.validate_rule(RV.row_rule(row), allow):
            bad.append(rid)
    if not bad:
        return None
    changes = [(r, "disabled", "BOUNDS_BLOCKED") for r in bad if (latest.get(r) or {}).get("status") == "active"]
    return write_version(conn, "BOUNDS_BLOCKED", [r for r in cur["rule_ids"] if r not in bad], now, numbers={"bad": bad}, changes=changes, cfg=cfg,
                         root=root)


# ---------------------------------------------------------------- готовность продвижения (прогрев, Requirement 10.6, 15.6)

META_STATE = "learnloop_state"           # состояние цикла: started_ts, last_ready_ts, шаги (ok_ts, err_run, series_ts, day), integrity
META_DAY = "learnloop_day"               # текущие сутки учёта: day, work_s, hw {час: секунд}, oh [часы с успешным observe], daily {шаг: код}
DAY_STEP = "day"                         # learn_events: сводка закрытых суток (ref = YYYY-MM-DD, body: counted, full, work_s, ...)
BAD_EVENT_CODES = ("CHAIN_BROKEN", "DB_CORRUPT", "WATCH_STALE")
SERIES_GROUPS = ("observe", "verdict", "watch")
WORK_WINDOW_H = 7 * 24
READY_CONDS = ("DAYS", "BAD_EVENTS", "WATCH", "STEP_ERRORS", "BACKUP", "INTEGRITY")


def _meta_json(conn, key):
    try:
        row = conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    except Exception:  # noqa: BLE001 - нет таблицы meta
        return None
    if not row or row[0] is None:
        return None
    try:
        d = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def state_get(conn):
    """Состояние цикла из meta.learnloop_state (пустое, если нет)."""
    return _meta_json(conn, META_STATE) or {}


def day_get(conn):
    """Текущие сутки учёта из meta.learnloop_day или None."""
    return _meta_json(conn, META_DAY)


def meta_put(conn, key, obj):
    conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (key, json.dumps(obj, sort_keys=True, separators=(",", ":"))))


def day_rows(conn):
    """Сводки закрытых суток (learn_events step="day"), по одной на сутки (последняя запись), от новых к старым."""
    by = {}
    try:
        cur = conn.execute("SELECT ref, body FROM learn_events WHERE step=? ORDER BY seq", (DAY_STEP,))
        rows = cur.fetchall()
    except Exception:  # noqa: BLE001 - таблиц цикла нет
        return []
    for ref, body in rows:
        try:
            b = json.loads(body) if body else {}
        except ValueError:
            continue
        if isinstance(b, dict) and ref:
            by[ref] = dict(b, day=ref)
    return [by[k] for k in sorted(by, reverse=True)]


def _day_start(day):
    return int(datetime.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp())


def work_window_start(conn, now, hours=WORK_WINDOW_H, days=None, acc=None):
    """Начало последних `hours` часов работы сервиса цикла по учёту суток: от текущих суток назад, суммируя work_s.
    Работы меньше — начало самых ранних учтённых суток (окно «вся работа»)."""
    days = day_rows(conn) if days is None else days
    acc = day_get(conn) if acc is None else acc
    need = float(hours) * 3600.0
    spans = []
    if isinstance(acc, dict) and acc.get("day"):
        spans.append((acc["day"], float(acc.get("work_s") or 0), int(now)))
    for d in days:
        if not spans or d["day"] != spans[0][0]:
            spans.append((d["day"], float(d.get("work_s") or 0), _day_start(d["day"]) + 86400))
    start = int(now)
    for day, work, end in spans:
        start = _day_start(day)
        if work >= need:
            return max(start, int(end - need))
        need -= work
    return start


def promotion_ready(conn, cfg, now, root=None, state=None, check_db=True):
    """Условия готовности продвижения (все одновременно, Requirement 10.6):
    DAYS        среди последних warmup_window_days засчитываемых суток не меньше warmup_days полных проходов;
    BAD_EVENTS  за последние 7 x 24 часа работы сервиса цикла нет CHAIN_BROKEN, DB_CORRUPT, WATCH_STALE в learn_events;
    WATCH       аренда сторожа runtime/watch_lease.json не старше watch_fresh_h (аренды нет — не готово: до сторожа задачи 16 всегда так);
    STEP_ERRORS нет серии из step_error_max подряд STEP_ERROR шагов наблюдения, вердикта и сторожа за 24 часа;
    BACKUP      последняя проверенная копия (state/backups/knowledge-*-<причина>.sqlite) не старше backup_max_age_h;
    INTEGRITY   PRAGMA integrity_check без ошибок (последним и только если остальное выполнено; check_db=False — не проверять).
    -> {"ok", "failed": [условия], "details": {...}}. Режим promotion.mode здесь не учитывается (его проверяет шаг продвижения)."""
    now = int(time.time() if now is None else now)
    p = cfg["promotion"]
    st = state_get(conn) if state is None else state
    failed, det = [], {}
    days = day_rows(conn)
    counted = [d for d in days if d.get("counted")][:int(p["warmup_window_days"])]
    full = sum(1 for d in counted if d.get("full"))
    det["days"] = {"counted": len(counted), "full": full, "need": int(p["warmup_days"])}
    if full < int(p["warmup_days"]):
        failed.append("DAYS")
    w0 = work_window_start(conn, now, days=days)
    try:
        q = "SELECT code, COUNT(*) FROM learn_events WHERE ts>=? AND code IN (%s) GROUP BY code" % ",".join("?" * len(BAD_EVENT_CODES))
        bad = dict(conn.execute(q, [store2.iso(w0)] + list(BAD_EVENT_CODES)).fetchall())
    except Exception:  # noqa: BLE001 - таблиц цикла нет
        bad = {"NOT_READY": 1}
    det["bad_events"] = {"since": store2.iso(w0), "codes": bad}
    if bad:
        failed.append("BAD_EVENTS")
    lease = RV.read_lease(root) if root is not None else None
    ts = lease.get("ts") if isinstance(lease, dict) else None
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        det["watch"] = {"why": "NO_LEASE"}
        failed.append("WATCH")
    else:
        age = now - ts
        det["watch"] = {"age_s": int(age)}
        if not (-RV.LEASE_FUTURE_SLACK_S <= age <= int(p["watch_fresh_h"]) * 3600):
            det["watch"]["why"] = "WATCH_STALE"
            failed.append("WATCH")
    series = []
    for name, s in sorted(((st or {}).get("steps") or {}).items()):
        if not isinstance(s, dict) or s.get("group") not in SERIES_GROUPS:
            continue
        if int(s.get("err_run") or 0) >= int(p["step_error_max"]) or (s.get("series_ts") and now - int(s["series_ts"]) < 86400):
            series.append(name)
    det["step_errors"] = series
    if series:
        failed.append("STEP_ERRORS")
    bks = store2.list_backups(root) if root is not None else []
    last = bks[-1]["ts"] if bks else None
    det["backup"] = {"age_s": None if last is None else now - last}
    if last is None or now - last > int(p["backup_max_age_h"]) * 3600:
        failed.append("BACKUP")
    if not check_db:
        det["integrity"] = "skipped"
    elif failed:
        det["integrity"] = "not_checked"
    else:
        try:
            res = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
        except Exception as e:  # noqa: BLE001
            res = ["%s: %s" % (type(e).__name__, e)]
        det["integrity"] = "ok" if res == ["ok"] else res[:3]
        if res != ["ok"]:
            failed.append("INTEGRITY")
    return {"ok": not failed, "failed": failed, "details": det}
