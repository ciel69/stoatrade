"""`./tb rulebook <команда>` (R2, Requirements 7.8, 11.8–11.10, 5.12; design.md, «Надзор за надзором и аварийные средства»).

Чтение (соединение на вызов через labels.store.open_ro):
list    версии rulebook (ver, время, причина, правила) и текущее состояние правил; режим применения (rulebook_view.active: apply, why)
show    версия (по умолчанию действующая) с её правилами или все ревизии правила
verify  цепочка rulebook_versions, согласованность действующей версии (правила версии active и годны, active вне версии нет); код 1 при нарушении

Аварийные действия владельца (необязательные; вне пакета tb.learnloop; каждое пишет learn_events с actor=OWNER; соединение на вызов через
labels.store.open_rw, только при маркере state/knowledge.v2 — иначе таблиц цикла нет):
disable RULE      активное правило — версия OWNER_DISABLE без него (disabled, чёрный список бессрочно, копия owner); правило trial — открытое
                  испытание закрывается (inconclusive, OWNER) или правило отклоняется (rejected, OWNER), условие в чёрный список OWNER_DISABLE
                  бессрочно, копия owner; уже выключенное — только бессрочная запись чёрного списка, если её нет
disable-all       СНАЧАЛА runtime/rules_switch.json apply=false (действует всегда, в том числе при повреждённой базе), затем правила trial
                  закрываются как выше, версия OWNER_DISABLE без правил (если в действующей версии есть правила), условия всех правил active и
                  trial — в чёрный список бессрочно, копия owner. База недоступна — сообщение «выключатель записан, запись версии не сделана,
                  повторите после восстановления базы», код 1; отложенных файлов нет. Применение остаётся выключенным до `apply on`.
apply on|off      только выключатель применения (без изменения версий); запись OWNER в learn_events — если база доступна
unblock BLOCK_H   снятие записи чёрного списка (строка UNBLOCK владельца), копия owner; условие не в списке — ничего не пишется.
                  Ключ чёрного списка — block_h семейства условия (tb.logic.learn_rules.block_h: числовые признаки без порога, категориальные
                  со значением); list/show показывают block_h правила и действующий запрет; прежние записи по точному cond_h снимаются по их ключу
Отклонение урока меток владельцем (reject_lesson; вызывается `./tb memory lesson reject` и дашбордом, Requirement 5.12): урок -> rejected (OWNER),
открытое испытание его правила -> inconclusive (OWNER), не активное правило -> rejected (OWNER), активное правило не трогается (Requirement 6.4),
условие -> чёрный список бессрочно OWNER_REJECT, копия owner. Повтор безопасен (идемпотентен).

Вывод: JSON одной строкой (--text: кратко для человека). Коды: 0 успех; 1 нарушение verify, нет базы или таблиц, база восстанавливается или
недоступна; 2 ошибка использования (нет правила и т. п.).
"""
import argparse
import json
import os
import sqlite3
import sys
import time

from tb.knowledge import markers as KM
from tb.knowledge import rulebook_view as RV
from tb.knowledge import schema2 as S2
from tb.knowledge import store2
from tb.labels import store as LST
from tb.logic import learn_rules as LR

OWNER = S2.OWNER
FOREVER_CODES = ("OWNER_DISABLE", "OWNER_REJECT")
DB_ERRORS = (sqlite3.Error, store2.KnowledgeError, OSError)


class Unavailable(Exception):
    """База знаний недоступна для записи владельца (нет файла, нет маркера v2 / таблиц, восстанавливается, повреждена)."""


def _out(obj, text=False, lines=None):
    if text and lines is not None:
        print("\n".join(lines))
    else:
        print(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str))


def _err(msg):
    print("ошибка: %s" % msg, file=sys.stderr)


def _open(root):
    """-> соединение или None (сообщение уже выведено)."""
    try:
        conn = LST.open_ro(root)
    except LST.KnowledgeRestoring:
        _err("база знаний восстанавливается (runtime/knowledge.restoring)")
        return None
    if conn is None:
        _err("нет базы знаний")
        return None
    if not RV.ready(conn):
        conn.close()
        _err("таблиц цикла нет (NOT_READY)")
        return None
    return conn


def _rule_block_h(b):
    """Ключ чёрного списка правила (семейство условия, Requirement 7.14) — его принимает `./tb rulebook unblock`; тело без cond — прежний cond_h."""
    c = b.get("cond")
    return LR.block_h(c, "lower") if isinstance(c, list) and c else b.get("cond_h")


def _block_state(conn, b, now):
    """Действующая запись чёрного списка семейства (или прежняя по точному cond_h) -> {"code", "until_ts", "key"} | None."""
    for k in (_rule_block_h(b), b.get("cond_h")):
        if k:
            cur = store2.blocklist_get(conn, k, now=now)
            if cur is not None:
                return dict(cur, key=k)
    return None


def _rule_brief(r, conn=None, now=None):
    b = r.get("body") or {}
    out = {"rule_id": r["rule_id"], "rev": r["rev"], "kind": r["kind"], "status": r["status"], "wait": r.get("wait"), "from_lesson": r["from_lesson"],
           "env_norm": r["env_norm"], "reason_code": r.get("reason_code"), "ver_in": r.get("rulebook_ver_in"), "ver_out": r.get("rulebook_ver_out"),
           "cond": b.get("cond"), "cond_h": b.get("cond_h"), "block_h": _rule_block_h(b), "ts": r.get("ts")}
    if conn is not None:
        out["blocked"] = _block_state(conn, b, time.time() if now is None else now)
    return out


def _blk_txt(r):
    blk = r.get("blocked")
    return (" [запрет %s, снять: ./tb rulebook unblock %s]" % (blk["code"], blk["key"])) if blk else ""


def _ver_brief(v):
    return {"ver": v["ver"], "parent": v.get("parent"), "reason_code": v["reason_code"], "rule_ids": v["rule_ids"], "trial_id": v.get("trial_id"),
            "numbers": v.get("numbers"), "ts": v["ts"]}


def _cond_txt(cond):
    return " и ".join("%s %s %s" % (c.get("feature"), c.get("op"), c.get("value")) for c in cond or [])


def cmd_list(a, root):
    view = RV.active(root, time.time())
    conn = _open(root)
    if conn is None:
        return 1
    try:
        vers = [_ver_brief(v) for v in RV.read_versions(conn)]
        rules = [_rule_brief(r, conn) for r in RV.rules_latest(conn).values()]
    finally:
        conn.close()
    rep = {"ok": True, "apply": view["apply"], "why": view["why"], "ver": view["ver"], "versions": vers, "rules": rules}
    _out(rep, a.text, ["rulebook: версия %s, применение %s (%s)" % (view["ver"] or "-", "да" if view["apply"] else "нет", view["why"])]
         + ["  %s %s %s [%s]" % (v["ver"], v["ts"], v["reason_code"], ",".join(v["rule_ids"]) or "-") for v in vers]
         + ["  %s %s%s %s: %s (block_h %s)%s" % (r["rule_id"], r["status"], ("/" + r["wait"]) if r["wait"] else "", r["from_lesson"], _cond_txt(r["cond"]),
                                                r["block_h"] or "-", _blk_txt(r)) for r in rules])
    return 0


def cmd_show(a, root):
    conn = _open(root)
    if conn is None:
        return 1
    try:
        target = a.target
        if target and target.startswith("RL"):
            raw = RV.rule_revs(conn, target)
            revs = [_rule_brief(r) for r in raw]
            if not revs:
                _err("нет правила %s" % target)
                return 1
            blk = _block_state(conn, raw[-1].get("body") or {}, time.time())
            _out({"ok": True, "rule_id": target, "block_h": revs[-1]["block_h"], "blocked": blk, "revs": revs}, a.text,
                 ["правило %s, block_h %s%s" % (target, revs[-1]["block_h"] or "-", _blk_txt({"blocked": blk}))]
                 + ["  rev %d %s %s %s" % (r["rev"], r["ts"], r["status"], r["reason_code"] or "-") for r in revs])
            return 0
        v = RV.get_version(conn, target) if target else RV.last_version(conn)
        if v is None:
            _err("нет версии %s" % (target or "(rulebook пуст)"))
            return 1
        latest = RV.rules_latest(conn)
        rules = [_rule_brief(latest[r], conn) for r in v["rule_ids"] if r in latest]
    finally:
        conn.close()
    vb = _ver_brief(v)
    _out({"ok": True, "version": vb, "rules": rules}, a.text,
         ["версия %s (%s, %s, родитель %s)" % (vb["ver"], vb["reason_code"], vb["ts"], vb["parent"] or "-")]
         + ["  %s %s: %s (block_h %s)%s" % (r["rule_id"], r["status"], _cond_txt(r["cond"]), r["block_h"] or "-", _blk_txt(r)) for r in rules])
    return 0


def verify(conn):
    """-> {"ok", "chain", "issues"}: цепочка версий; правила действующей версии active и годны; active вне действующей версии нет."""
    chain = store2.verify_chain(conn)
    issues = []
    if not chain["ok"]:
        issues.append("CHAIN_BROKEN: цепочка rulebook_versions разорвана на seq=%s" % chain["bad_seq"])
    cur = RV.last_version(conn)
    act = RV.active_from_conn(conn)
    for rid in act["invalid"]:
        issues.append("INVALID: правило %s версии %s не active или не годно (BOUNDS_BLOCKED при проверке циклом)" % (rid, act["ver"]))
    ids = set((cur or {}).get("rule_ids") or [])
    for rid, r in sorted(RV.rules_latest(conn).items()):
        if r["status"] == "active" and rid not in ids:
            issues.append("ORPHAN: правило %s active, но не в действующей версии %s" % (rid, (cur or {}).get("ver")))
    vers = RV.read_versions(conn)
    if vers and vers[0]["ver"] != "R-0001":
        issues.append("FIRST: первая версия %s, а не R-0001" % vers[0]["ver"])
    return {"ok": not issues, "chain": chain, "ver": (cur or {}).get("ver"), "n_versions": len(vers), "issues": issues}


def cmd_verify(a, root):
    conn = _open(root)
    if conn is None:
        return 1
    try:
        rep = verify(conn)
    finally:
        conn.close()
    _out(rep, a.text, ["rulebook verify: %s, версия %s, версий %d" % ("ok" if rep["ok"] else "НАРУШЕНИЯ", rep["ver"] or "-", rep["n_versions"])]
         + ["  ! %s" % x for x in rep["issues"]])
    return 0 if rep["ok"] else 1


# ---------------------------------------------------------------- аварийные действия владельца (Requirement 11.8–11.10, 5.12)

def _deps():
    """Модули цикла (импорт по месту: команды чтения их не требуют)."""
    from tb.learnloop import conf as LC
    from tb.learnloop import rules as RL
    from tb.learnloop import trial as TR
    from tb.learnloop import versions as V
    return LC, RL, TR, V


def _loop_cfg(root):
    """config/learnloop.json; битый — DISABLED (записям владельца нужны только бессрочные коды и allow_size_rules)."""
    LC = _deps()[0]
    return LC.load(root)[0]


def open_owner(root):
    """Соединение записи владельца на один вызов. Нет маркера v2 или файла, маркер восстановления, повреждение, нет таблиц -> Unavailable."""
    if not KM.has_v2(root) or not os.path.exists(LST.db_path(root)):
        raise Unavailable("нет базы знаний v2 (таблиц цикла нет)")
    if KM.restoring(root):
        raise Unavailable("база знаний восстанавливается (runtime/knowledge.restoring)")
    try:
        conn = LST.open_rw(root)
    except DB_ERRORS as e:
        raise Unavailable("%s: %s" % (type(e).__name__, e)) from e
    try:
        ok = store2.ready(conn)
    except DB_ERRORS as e:
        conn.close()
        raise Unavailable("%s: %s" % (type(e).__name__, e)) from e
    if not ok:
        conn.close()
        raise Unavailable("таблиц цикла нет (NOT_READY)")
    return conn


def log_owner(conn, step, code, ref, body, now):
    """Запись learn_events с actor=OWNER (не ограничивается events_per_day)."""
    return store2.log_event(conn, step, code, OWNER, ref=ref, body=body, now=now)


def _backup(root, now, rep):
    try:
        rep["backup"] = os.path.basename(store2.make_backup(root, "owner", now=now))
    except DB_ERRORS as e:                               # запись сделана; копия — следующим суточным шагом
        rep["backup_error"] = "%s: %s" % (type(e).__name__, e)


def _block_forever(conn, cond, code, now, cfg, dir="lower"):
    """Бессрочная запись владельца по семейству условия (ключ block_h), если действующей бессрочной записи семейства нет. cond — условие
    (список); строка — готовый ключ (тело правила без cond). -> True, если записана."""
    RL = _deps()[1]
    if not cond:
        return False
    key = RL.block_key(cond, dir)
    cur = RL.blocked(conn, key, now)
    if cur is not None and cur.get("code") in FOREVER_CODES and cur.get("until_ts") is None:
        return False
    RL.block(conn, key, code, now, cfg, actor=OWNER, backup=False)
    return True


def _rule_cond(row):
    """Условие правила для записи чёрного списка: body.cond, иначе прежний ключ body.cond_h."""
    b = (row or {}).get("body") or {}
    return b.get("cond") if isinstance(b.get("cond"), list) and b.get("cond") else b.get("cond_h")


def _close_trial_rule(conn, rid, now, cfg, reason):
    """Правило trial: открытое испытание -> inconclusive (OWNER) с отклонением правила; иначе (ждёт продвижения) правило -> rejected (OWNER).
    Правило стало active параллельно (продвинуто циклом) -> "active" (не трогается). -> "closed" | "rejected" | "active" | None."""
    _LC, _RL, TR, V = _deps()
    t = TR.by_rule(conn, rid)
    if t is not None and t.get("state") == TR.OPEN:
        if TR.close(conn, t, TR.INCONCLUSIVE, "OWNER", {"owner": reason}, now, cfg, reason=reason) is not None:
            return "closed"
    try:
        return "rejected" if V.reject_rule(conn, rid, "OWNER", now, cfg) is not None else None
    except V.RuleRefused as e:
        if e.code == "BAD_STATUS" and (V.rule(conn, rid) or {}).get("status") == "active":
            return "active"
        raise


def disable_rule(root, rule_id, now=None):
    """`./tb rulebook disable RULE` (Requirement 11.8). -> отчёт; нет правила -> ValueError; база недоступна -> Unavailable."""
    now = int(time.time() if now is None else now)
    _LC, _RL, _TR, V = _deps()
    cfg = _loop_cfg(root)
    conn = open_owner(root)
    rep = {"ok": True, "action": "disable", "rule_id": rule_id, "ver": None, "done": None, "blocked": False, "backup": None}
    try:
        row = V.rule(conn, rule_id)
        if row is None:
            raise ValueError("нет правила %s" % rule_id)
        ch = _rule_cond(row)
        ids = (V.current(conn) or {}).get("rule_ids") or []
        if row["status"] == "trial":
            rep["done"] = _close_trial_rule(conn, rule_id, now, cfg, "OWNER_DISABLE")
            row = V.rule(conn, rule_id)
        if row["status"] == "active" and rule_id in ids:
            res = V.disable(conn, [rule_id], "OWNER_DISABLE", now, cfg, root=root, numbers={"by": OWNER}, backup=False)
            rep.update(ver=res["ver"], done="disabled")
        with V._tx(conn):
            rep["blocked"] = _block_forever(conn, ch, "OWNER_DISABLE", now, cfg)
            log_owner(conn, "rulebook", "OWNER_DISABLE", rule_id, {"action": "disable", "ver": rep["ver"], "done": rep["done"],
                                                                     "status": row["status"]}, now)
    finally:
        conn.close()
    _backup(root, now, rep)
    return rep


def write_switch(root, apply, now=None):
    """Выключатель применения runtime/rules_switch.json (атомарно). Не зависит от базы."""
    now = int(time.time() if now is None else now)
    return KM.write_json(RV.switch_path(root), {"apply": bool(apply), "ts": now, "actor": OWNER})


def disable_all(root, now=None):
    """`./tb rulebook disable-all` (Requirement 11.9). Выключатель пишется первым; база недоступна -> отчёт с ok=False, retry=True."""
    now = int(time.time() if now is None else now)
    write_switch(root, False, now)
    rep = {"ok": True, "action": "disable-all", "switch": "apply=false", "ver": None, "disabled": [], "rejected": [], "blocked": 0, "backup": None,
           "retry": False}
    try:
        _LC, _RL, _TR, V = _deps()
        cfg = _loop_cfg(root)
        conn = open_owner(root)
    except Unavailable as e:
        rep.update(ok=False, retry=True, error=str(e))
        return rep
    try:
        latest = RV.rules_latest(conn)
        hs = []
        for rid, r in sorted(latest.items()):
            if r["status"] == "trial":
                if _close_trial_rule(conn, rid, now, cfg, "OWNER_DISABLE") in ("closed", "rejected"):
                    rep["rejected"].append(rid)
        latest = RV.rules_latest(conn)
        cur = V.current(conn) or {}
        ids = list(cur.get("rule_ids") or [])
        act = [r for r in ids if (latest.get(r) or {}).get("status") == "active"]
        for rid in sorted(set(act) | set(rep["rejected"])):
            hs.append(_rule_cond(latest.get(rid)))
        if ids:
            res = V.write_version(conn, "OWNER_DISABLE", [], now, numbers={"by": OWNER, "all": True}, changes=[(r, "disabled", "OWNER_DISABLE") for r in act],
                                  cfg=cfg, root=root, backup=False)
            rep.update(ver=res["ver"], disabled=act)
        with V._tx(conn):
            rep["blocked"] = sum(1 for h in hs if _block_forever(conn, h, "OWNER_DISABLE", now, cfg))
            log_owner(conn, "rulebook", "DISABLE_ALL", None, {"action": "disable-all", "ver": rep["ver"], "disabled": act, "rejected": rep["rejected"]}, now)
    except Exception as e:  # noqa: BLE001 - аварийная команда: выключатель уже записан, любой сбой записи версии — сообщение о повторе
        rep.update(ok=False, retry=True, error="%s: %s" % (type(e).__name__, e))
        return rep
    finally:
        conn.close()
    _backup(root, now, rep)
    return rep


def _try_log(root, step, code, ref, body, now, rep):
    try:
        conn = open_owner(root)
    except Unavailable as e:
        rep["log_error"] = str(e)
        return
    try:
        log_owner(conn, step, code, ref, body, now)
    except DB_ERRORS as e:
        rep["log_error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        conn.close()


def set_apply(root, on, now=None):
    """`./tb rulebook apply on|off` (Requirement 11.10): только выключатель; запись OWNER — если база доступна."""
    now = int(time.time() if now is None else now)
    write_switch(root, on, now)
    rep = {"ok": True, "action": "apply", "apply": bool(on)}
    _try_log(root, "rulebook", "APPLY_ON" if on else "APPLY_OFF", None, {"action": "apply", "apply": bool(on)}, now, rep)
    return rep


def unblock(root, cond_h, now=None):
    """`./tb rulebook unblock BLOCK_H` (Requirement 7.14, 11.10): ключ записи — block_h семейства условия (показывают `list`/`show`, вкладка
    «Память»); прежняя запись по точному cond_h снимается тем же способом по её ключу. Поле отчёта cond_h сохранено (значение — снятый ключ)."""
    now = int(time.time() if now is None else now)
    conn = open_owner(root)
    rep = {"ok": True, "action": "unblock", "cond_h": cond_h, "block_h": cond_h, "changed": False, "was": None, "backup": None}
    try:
        cur = store2.blocklist_get(conn, cond_h, now=now)
        rep["was"] = cur
        if cur is not None:
            with LST.tx(conn):
                store2.blocklist_put(conn, cond_h, S2.UNBLOCK, None, OWNER, now=now)
                log_owner(conn, "rulebook", "UNBLOCK", cond_h, {"action": "unblock", "was": cur.get("code")}, now)
            rep["changed"] = True
    finally:
        conn.close()
    if rep["changed"]:
        _backup(root, now, rep)
    return rep


def reject_lesson(root, lesson_id, now=None, mcfg=None):
    """Отклонение урока меток владельцем (Requirement 5.12; свойство 19). -> отчёт.
    Не урок меток -> ValueError (вызывающий ведёт урок мозга прежним путём); недопустимый переход урока -> ValueError;
    база недоступна после перехода урока -> Unavailable (повтор команды допишет базу; сверка закроет испытание кодом OWNER)."""
    from tb.logic import learn_rules as LR
    from tb.memory import lessons as LS
    now = int(time.time() if now is None else now)
    l = LS.read_all(root).get(lesson_id)
    if l is None:
        raise ValueError("нет урока %s" % lesson_id)
    if not LS.is_labels(l):
        raise ValueError("урок %s — урок мозга" % lesson_id)
    res = LS.transition(root, lesson_id, "rejected", "OWNER", now, cfg=mcfg)
    rep = {"ok": True, "action": "lesson_reject", "lesson_id": lesson_id, "status": "rejected", "lesson_changed": res is not None, "rules": [],
           "blocked": False, "backup": None}
    _LC, _RL, _TR, V = _deps()
    cfg = _loop_cfg(root)
    if not KM.has_v2(root):                              # таблиц цикла нет: ни испытаний, ни правил, ни чёрного списка — закрывать нечего
        rep["db"] = "NOT_READY"
        return rep
    conn = open_owner(root)
    try:
        claim = l.get("claim") or {}
        ldir = claim.get("dir") or "lower"
        hs = {}                                          # ключ block_h -> условие (или прежний ключ) с направлением; дубли семейства — одна запись
        if l.get("cond"):
            hs[LR.block_h(l["cond"], ldir)] = (l["cond"], ldir)
        for rid, r in sorted(RV.rules_latest(conn).items()):
            if r.get("from_lesson") != lesson_id:
                continue
            c = _rule_cond(r)
            if c:
                hs.setdefault(c if isinstance(c, str) else LR.block_h(c, "lower"), (c, "lower"))
            done = _close_trial_rule(conn, rid, now, cfg, "OWNER_REJECT") if r["status"] == "trial" else None
            rep["rules"].append({"rule_id": rid, "status": (V.rule(conn, rid) or {}).get("status"), "done": done})
        with V._tx(conn):
            rep["blocked"] = any([_block_forever(conn, c, "OWNER_REJECT", now, cfg, d) for c, d in hs.values()])
            log_owner(conn, "lesson", "OWNER_REJECT", lesson_id, {"action": "lesson_reject", "rules": rep["rules"]}, now)
    finally:
        conn.close()
    _backup(root, now, rep)
    return rep


def cmd_disable(a, root):
    try:
        rep = disable_rule(root, a.rule_id)
    except Unavailable as e:
        _err("база знаний недоступна: %s" % e)
        return 1
    except ValueError as e:
        _err(str(e))
        return 2
    _out(rep, a.text, ["правило %s: %s%s, чёрный список %s" % (rep["rule_id"], rep["done"] or "без изменений статуса",
                                                              (", версия %s" % rep["ver"]) if rep["ver"] else "", "записан" if rep["blocked"] else "уже был")])
    return 0


def cmd_disable_all(a, root):
    try:
        rep = disable_all(root)
    except OSError as e:
        _err("выключатель не записан: %s" % e)
        return 1
    if not rep["ok"]:
        _out(rep, a.text, ["выключатель записан (apply=false): правила не применяются.",
                           "запись версии НЕ сделана (%s): повторите ./tb rulebook disable-all после восстановления базы." % rep.get("error")])
        _err("выключатель записан, запись версии не сделана: повторите после восстановления базы")
        return 1
    _out(rep, a.text, ["выключатель записан (apply=false); версия %s; выключены %s; отклонены %s; включить применение: ./tb rulebook apply on" % (
        rep["ver"] or "не нужна (правил нет)", ",".join(rep["disabled"]) or "-", ",".join(rep["rejected"]) or "-")])
    return 0


def cmd_apply(a, root):
    try:
        rep = set_apply(root, a.state == "on")
    except OSError as e:
        _err("выключатель не записан: %s" % e)
        return 1
    _out(rep, a.text, ["применение правил: %s%s" % ("включено" if rep["apply"] else "выключено",
                                                    (" (запись OWNER не сделана: %s)" % rep["log_error"]) if rep.get("log_error") else "")])
    return 0


def cmd_unblock(a, root):
    try:
        rep = unblock(root, a.cond_h)
    except Unavailable as e:
        _err("база знаний недоступна: %s" % e)
        return 1
    _out(rep, a.text, ["условие %s: %s" % (a.cond_h, ("снято (было %s)" % rep["was"]["code"]) if rep["changed"] else "не в чёрном списке")])
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="tb rulebook", description="rulebook цикла обучения: версии, правила, проверка цепочки, аварийные действия (R2)")
    sub = p.add_subparsers(dest="cmd")
    ls = sub.add_parser("list", help="версии и правила, режим применения")
    ls.add_argument("--text", action="store_true")
    sh = sub.add_parser("show", help="версия (R-NNNN, по умолчанию действующая) или правило (RL0001)")
    sh.add_argument("target", nargs="?", default=None)
    sh.add_argument("--text", action="store_true")
    vf = sub.add_parser("verify", help="цепочка версий и согласованность действующей версии; код 1 при нарушении")
    vf.add_argument("--text", action="store_true")
    ds = sub.add_parser("disable", help="отключить одно правило (OWNER_DISABLE, чёрный список бессрочно)")
    ds.add_argument("rule_id")
    ds.add_argument("--text", action="store_true")
    da = sub.add_parser("disable-all", help="выключатель apply=false, затем версия OWNER_DISABLE без правил")
    da.add_argument("--text", action="store_true")
    ap = sub.add_parser("apply", help="выключатель применения правил")
    ap.add_argument("state", choices=("on", "off"))
    ap.add_argument("--text", action="store_true")
    ub = sub.add_parser("unblock", help="снять запись чёрного списка условия (ключ block_h из list/show)")
    ub.add_argument("cond_h", metavar="BLOCK_H")
    ub.add_argument("--text", action="store_true")
    return p


HANDLERS = {"list": cmd_list, "show": cmd_show, "verify": cmd_verify, "disable": cmd_disable, "disable-all": cmd_disable_all, "apply": cmd_apply,
            "unblock": cmd_unblock}


def main(argv, root=None):
    p = build_parser()
    try:
        a = p.parse_args(list(argv))
    except SystemExit as e:
        return int(e.code or 0)
    if not a.cmd:
        p.print_usage(sys.stderr)
        return 2
    try:
        return HANDLERS[a.cmd](a, root)
    except sqlite3.Error as e:
        _err("база знаний: %s: %s" % (type(e).__name__, e))
        return 1


if __name__ == "__main__":
    for _st in (sys.stdout, sys.stderr):
        try:
            _st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    sys.exit(main(sys.argv[1:]))
