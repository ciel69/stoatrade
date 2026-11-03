"""`./tb rulebook <команда>`: list | show [R-NNNN|RL0001] | verify (R2, Requirement 7.8). Только чтение: соединение на вызов через labels.store.open_ro.

list    версии rulebook (ver, время, причина, правила) и текущее состояние правил; режим применения (rulebook_view.active: apply, why)
show    версия (по умолчанию действующая) с её правилами или все ревизии правила
verify  цепочка rulebook_versions, согласованность действующей версии (правила версии active и годны, active вне версии нет); код 1 при нарушении

Вывод: JSON одной строкой (--text: кратко для человека). Коды: 0 успех; 1 нарушение verify, нет базы или таблиц, база восстанавливается; 2 ошибка
использования. Аварийные команды disable|disable-all|apply|unblock добавляет задача 16 (вне пакета tb.learnloop).
"""
import argparse
import json
import sqlite3
import sys
import time

from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.labels import store as LST


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


def _rule_brief(r):
    b = r.get("body") or {}
    return {"rule_id": r["rule_id"], "rev": r["rev"], "kind": r["kind"], "status": r["status"], "wait": r.get("wait"), "from_lesson": r["from_lesson"],
            "env_norm": r["env_norm"], "reason_code": r.get("reason_code"), "ver_in": r.get("rulebook_ver_in"), "ver_out": r.get("rulebook_ver_out"),
            "cond": b.get("cond"), "cond_h": b.get("cond_h"), "ts": r.get("ts")}


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
        rules = [_rule_brief(r) for r in RV.rules_latest(conn).values()]
    finally:
        conn.close()
    rep = {"ok": True, "apply": view["apply"], "why": view["why"], "ver": view["ver"], "versions": vers, "rules": rules}
    _out(rep, a.text, ["rulebook: версия %s, применение %s (%s)" % (view["ver"] or "-", "да" if view["apply"] else "нет", view["why"])]
         + ["  %s %s %s [%s]" % (v["ver"], v["ts"], v["reason_code"], ",".join(v["rule_ids"]) or "-") for v in vers]
         + ["  %s %s%s %s: %s" % (r["rule_id"], r["status"], ("/" + r["wait"]) if r["wait"] else "", r["from_lesson"], _cond_txt(r["cond"])) for r in rules])
    return 0


def cmd_show(a, root):
    conn = _open(root)
    if conn is None:
        return 1
    try:
        target = a.target
        if target and target.startswith("RL"):
            revs = [_rule_brief(r) for r in RV.rule_revs(conn, target)]
            if not revs:
                _err("нет правила %s" % target)
                return 1
            _out({"ok": True, "rule_id": target, "revs": revs}, a.text,
                 ["правило %s" % target] + ["  rev %d %s %s %s" % (r["rev"], r["ts"], r["status"], r["reason_code"] or "-") for r in revs])
            return 0
        v = RV.get_version(conn, target) if target else RV.last_version(conn)
        if v is None:
            _err("нет версии %s" % (target or "(rulebook пуст)"))
            return 1
        latest = RV.rules_latest(conn)
        rules = [_rule_brief(latest[r]) for r in v["rule_ids"] if r in latest]
    finally:
        conn.close()
    vb = _ver_brief(v)
    _out({"ok": True, "version": vb, "rules": rules}, a.text,
         ["версия %s (%s, %s, родитель %s)" % (vb["ver"], vb["reason_code"], vb["ts"], vb["parent"] or "-")]
         + ["  %s %s: %s" % (r["rule_id"], r["status"], _cond_txt(r["cond"])) for r in rules])
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


def build_parser():
    p = argparse.ArgumentParser(prog="tb rulebook", description="rulebook цикла обучения: версии, правила, проверка цепочки (R2.3)")
    sub = p.add_subparsers(dest="cmd")
    ls = sub.add_parser("list", help="версии и правила, режим применения")
    ls.add_argument("--text", action="store_true")
    sh = sub.add_parser("show", help="версия (R-NNNN, по умолчанию действующая) или правило (RL0001)")
    sh.add_argument("target", nargs="?", default=None)
    sh.add_argument("--text", action="store_true")
    vf = sub.add_parser("verify", help="цепочка версий и согласованность действующей версии; код 1 при нарушении")
    vf.add_argument("--text", action="store_true")
    return p


HANDLERS = {"list": cmd_list, "show": cmd_show, "verify": cmd_verify}


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
