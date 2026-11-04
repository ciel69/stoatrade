"""`./tb exits <команда>` (R4, Requirement 16.3; design.md «tb.learnloop.exits_*»). Вне пакета `tb.learnloop`: команды владельца,
аварийные средства и отчёты.

Реализованы команды `after` (задача 20, Requirement 13: путь после закрытия по путям сделок мозга и прогонов меток) и
`brain` (задача 19, Requirement 12: R4.3 «мозг против алгоритма» — `brain_delta` с интервалом по сделкам, разрезы по типам
вмешательства и кодам пробуждения, стоимость `position_manager`). Оба отчёта только читают базу знаний
(`labels.store.open_ro`), ничего не пишут и не меняют геометрию правил. Остальные подкоманды группы (`status`, `versions`,
`search`, `trials`, `gate`, `apply`, `rollback`, `promotion`) добавит задача 21.

Вывод: JSON одной строкой; `--text` — короткий текст для человека. Коды: 0 — отчёт построен; 1 — база знаний недоступна
(нет базы, идёт восстановление, ошибка чтения); 2 — ошибка использования.
"""
import argparse
import json
import sys
import time

from tb.learnloop import exits_report as ER


def _out(obj, text=False, lines=None):
    if text and lines is not None:
        print("\n".join(lines))
    else:
        print(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str))


def _err(msg):
    print("ошибка: %s" % msg, file=sys.stderr)


def cmd_after(a, root):
    since = int(time.time()) - int(a.days) * 86400 if a.days else None
    rep = ER.run(root, mode=a.mode, since=since)
    if rep.get("db") not in (None, "OK"):
        _err("%s" % rep.get("error"))
        return 1
    _out(rep, a.text, ER.render(rep))
    return 0


def cmd_brain(a, root):
    since = int(time.time()) - int(a.days) * 86400 if a.days else None
    rep = ER.run_brain(root, mode=a.mode, since=since)
    if rep.get("db") not in (None, "OK"):
        _err("%s" % rep.get("error"))
        return 1
    _out(rep, a.text, ER.render_brain(rep))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="tb exits", description="политика выхода (R4): отчёты и аварийные средства владельца")
    sub = p.add_subparsers(dest="cmd")
    af = sub.add_parser("after", help="путь после закрытия: ход после выхода, достижение t1 по причинам (только чтение)")
    af.add_argument("--mode", choices=ER.MODES, help="режим торговли для сделок мозга (по умолчанию brain.mode конфига)")
    af.add_argument("--days", type=int, default=0, help="окно: сделки и прогоны с entry_ts не старше N суток")
    af.add_argument("--text", action="store_true", help="короткий текст вместо JSON")
    br = sub.add_parser("brain", help="R4.3: мозг против алгоритма по сделкам мозга (только чтение)")
    br.add_argument("--mode", choices=ER.MODES, help="режим торговли (по умолчанию brain.mode конфига)")
    br.add_argument("--days", type=int, default=0, help="окно: сделки с entry_ts не старше N суток")
    br.add_argument("--text", action="store_true", help="короткий текст вместо JSON")
    return p


HANDLERS = {"after": cmd_after, "brain": cmd_brain}


def main(argv, root=None):
    p = build_parser()
    try:
        a = p.parse_args(list(argv))
    except SystemExit as e:
        return int(e.code or 0)
    if not a.cmd:
        p.print_usage(sys.stderr)
        return 2
    return HANDLERS[a.cmd](a, root)


if __name__ == "__main__":
    for _st in (sys.stdout, sys.stderr):
        try:
            _st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    sys.exit(main(sys.argv[1:]))
