"""`./tb knowledge <команда>`: verify | backup | restore [--from FILE] | stats | disable-v2 | migrate-v2a (R2.9a, Requirement 2.4, 2.7, 2.10, 16.4).

Вывод: JSON одной строкой (--text: кратко для человека). Коды: 0 успех / зелёный (verify: и жёлтый); 1 - красный verify, отказ команды, нет базы;
2 - ошибка использования. Сеть и ключи не используются; конфиг цикла только читается (флаг enabled для disable-v2 и migrate-v2a).
"""
import argparse
import json
import os
import sqlite3
import sys

from tb import paths
from tb.knowledge import markers as KM
from tb.knowledge import store2
from tb.labels import store as LST


def _out(obj, text=False, lines=None):
    if text and lines is not None:
        print("\n".join(lines))
    else:
        print(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str))


def _err(msg):
    print("ошибка: %s" % msg, file=sys.stderr)


def learnloop_enabled(root=None):
    """True только при читаемом config/learnloop.json с "enabled": true (битый или отсутствующий конфиг цикл не включает)."""
    try:
        with open(paths.config("learnloop.json", root=root), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and d.get("enabled") is True


def cmd_verify(a, root):
    rep = store2.verify(root, quick=a.quick)
    _out(rep, a.text, ["knowledge verify: %s%s" % (rep["color"], (" (%s)" % ",".join(rep["codes"])) if rep["codes"] else ""),
                       "v2=%s ver=%s ready=%s integrity=%s размер=%s МБ" % (rep["v2"], rep["ver"], rep["ready"], rep["integrity"], rep["size_mb"])]
         + ["  ! %s" % r for r in rep["reasons"]] + ["  %s: %s" % (k, v) for k, v in sorted(rep["tables"].items())])
    return 1 if rep["color"] == "red" else 0


def cmd_stats(a, root):
    rep = store2.stats(root)
    _out(rep, a.text, ["knowledge stats: %s, %s МБ" % (rep["color"], rep["size_mb"])]
         + ["  %s: строк %s%s" % (k, v, (", %d КБ" % ((rep["bytes"] or {}).get(k, 0) // 1024)) if rep.get("bytes") else "") for k, v in sorted(rep["tables"].items())]
         + ["  копия %s" % b["path"] for b in rep["backups"]])
    return 0


def cmd_backup(a, root):
    try:
        dst = store2.make_backup(root, "owner")
    except FileNotFoundError:
        _err("нет базы знаний")
        return 1
    except store2.KnowledgeError as e:
        _err(str(e))
        return 1
    _out({"ok": True, "path": dst, "reason": "owner"}, a.text, ["backup: %s" % dst])
    return 0


def cmd_restore(a, root):
    src = os.path.abspath(a.src) if a.src else None
    try:
        rep = store2.restore(root, src=src, auto=False, ack_timeout_s=a.ack_timeout)
    except store2.RestoreError as e:
        _err(str(e))
        _out({"ok": False, "code": e.code, "msg": str(e)}, a.text, ["restore: ОТКАЗ %s" % e])
        return 1
    _out(rep, a.text, ["restore: база восстановлена из %s (copy_ts=%s, restore_ts=%s); переименовано: %s" % (
        rep["src"], rep["copy_ts"], rep["restore_ts"], ", ".join(os.path.basename(p) for p in rep["moved"]) or "-")])
    return 0


def cmd_disable_v2(a, root):
    try:
        rep = store2.disable_v2(root, enabled=learnloop_enabled(root))
    except FileNotFoundError:
        _err("нет базы знаний")
        return 1
    except store2.KnowledgeError as e:
        _err(str(e))
        return 1
    _out(rep, a.text, ["disable-v2: маркер state/knowledge.v2 снят%s" % ((", копия %s" % rep["backup"]) if rep.get("backup") else "")])
    return 0


def cmd_migrate(a, root):
    if KM.restoring(root):
        _err("идёт восстановление базы (runtime/knowledge.restoring)")
        return 1
    try:
        conn = LST.open_rw(root)
    except sqlite3.Error as e:
        _err("база знаний: %s" % e)
        return 1
    try:
        ver = store2.migrate_v2a(conn, root, enabled=learnloop_enabled(root), explicit=True)
    except store2.KnowledgeMigrationError as e:
        _err(str(e))
        return 1
    finally:
        conn.close()
    _out({"ok": True, "knowledge_v2_ver": ver, "marker": KM.has_v2(root)}, a.text, ["migrate-v2a: knowledge_v2_ver=%s, маркер state/knowledge.v2 есть" % ver])
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="tb knowledge", description="база знаний v2: проверка, копии, восстановление, размеры (R2.9a)")
    sub = p.add_subparsers(dest="cmd")
    vf = sub.add_parser("verify", help="integrity_check, цепочки, индексы и триггеры v2a, маркеры; код 1 при красном")
    vf.add_argument("--quick", action="store_true", help="quick_check вместо integrity_check")
    vf.add_argument("--text", action="store_true")
    bk = sub.add_parser("backup", help="проверенная копия state/backups/knowledge-<время>-owner.sqlite")
    bk.add_argument("--text", action="store_true")
    rs = sub.add_parser("restore", help="подставить проверенную копию (самую свежую или --from FILE) по протоколу с маркером восстановления")
    rs.add_argument("--from", dest="src", default=None)
    rs.add_argument("--ack-timeout", dest="ack_timeout", type=float, default=60.0, help="ожидание подтверждений писателей, с (restore_ack_timeout_s)")
    rs.add_argument("--text", action="store_true")
    st = sub.add_parser("stats", help="строки и размеры таблиц, копии")
    st.add_argument("--text", action="store_true")
    dv = sub.add_parser("disable-v2", help="выйти из режима v2 (только при learnloop.enabled=false): копия, затем снятие маркера")
    dv.add_argument("--text", action="store_true")
    mg = sub.add_parser("migrate-v2a", help="явно создать таблицы цикла v2a (копия premigrate) и маркер state/knowledge.v2")
    mg.add_argument("--text", action="store_true")
    return p


HANDLERS = {"verify": cmd_verify, "backup": cmd_backup, "restore": cmd_restore, "stats": cmd_stats, "disable-v2": cmd_disable_v2, "migrate-v2a": cmd_migrate}


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
