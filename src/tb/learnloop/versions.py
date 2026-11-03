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
rules_ver (config/memory.json) здесь не меняется никогда (Requirement 7.6). Условия готовности продвижения (promotion_ready) — задача 10.
"""
import contextlib
import re

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
    правило урока уже есть -> RuleRefused(DUP_RULE); cond_h в чёрном списке -> BLOCKED; правило не годно -> BAD_RULE. -> {"rule_id", "trial_id"}."""
    lesson_id = rule_draft.get("from_lesson")
    if not lesson_id:
        raise RuleRefused("BAD_RULE", "нет from_lesson")
    with _tx(conn):
        dup = non_terminal_for_lesson(conn, lesson_id)
        if dup:
            raise RuleRefused("DUP_RULE", "у урока %s уже есть правило %s" % (lesson_id, ",".join(dup)))
        ch = rule_draft.get("cond_h") or LR.cond_h(rule_draft["cond"], "lower")
        if RL.blocked(conn, ch, now) is not None:
            raise RuleRefused("BLOCKED", "условие %s в чёрном списке" % ch)
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
    ch = (prev.get("body") or {}).get("cond_h")
    if ch and cfg is not None:
        return RL.block(conn, ch, code, now, cfg, reason=reason, backup=False)
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
