"""`./tb learn <команда>` (R2, Requirements 10.6, 8.4, 11.10; design.md, «Надзор за надзором и аварийные средства»). Вне пакета tb.learnloop:
только здесь пишется config/learnloop.json:promotion.mode.

promotion            текущий режим продвижения (auto | off)
promotion auto|off   аварийная остановка (off) или возврат (auto) автопродвижения. Запись атомарная (tmp + os.replace), меняется только значение
                     promotion.mode (остальные поля и форматирование файла сохраняются); новый текст проверяется загрузчиком tb.learnloop.conf
                     до записи, после записи файл перечитывается загрузчиком (при расхождении прежний текст возвращается). Битый конфиг не
                     правится (код 1). При изменении — learn_events actor=OWNER (если база доступна). Сервис цикла перечитывает конфиг сам.

Вывод: JSON одной строкой (--text: кратко). Коды: 0 успех; 1 конфиг битый или не записан; 2 ошибка использования.
"""
import argparse
import json
import os
import re
import sys
import time

from tb import paths
from tb.knowledge import rulebook_cli as RC
from tb.learnloop import conf as LC

RE_MODE = re.compile(r'("promotion"\s*:\s*\{[^{}]*?"mode"\s*:\s*)"(?:auto|off)"', re.S)


def _out(obj, text=False, lines=None):
    if text and lines is not None:
        print("\n".join(lines))
    else:
        print(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str))


def _err(msg):
    print("ошибка: %s" % msg, file=sys.stderr)


class ConfigRefused(Exception):
    pass


def cfg_path(root=None):
    return paths.config(LC.FILE, root=root)


def _read(root):
    with open(cfg_path(root), encoding="utf-8") as f:
        return f.read()


def render(text, raw, mode):
    """Новый текст конфига с promotion.mode=mode: замена одного значения в тексте (форматирование сохраняется), иначе — полная сериализация.
    Результат проходит загрузчик и отличается от прежнего ровно полем promotion.mode, иначе ConfigRefused."""
    want = json.loads(json.dumps(raw))
    want["promotion"]["mode"] = mode
    cands = []
    new, n = RE_MODE.subn(lambda m: m.group(1) + '"%s"' % mode, text, count=1)
    if n == 1:
        cands.append(new)
    cands.append(json.dumps(want, ensure_ascii=False, indent=2) + "\n")
    for c in cands:
        try:
            got = LC.parse(c)
        except ValueError:
            continue
        if got == want and not LC.check(got):
            return c
    raise ConfigRefused("новый текст конфига не прошёл проверку загрузчиком")


def _write_atomic(path, text):
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    for i in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 19:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            time.sleep(0.05)


def get_mode(root=None):
    """-> (mode, ошибки загрузчика)."""
    cfg, errs = LC.load(root)
    return LC.promotion_mode(cfg), errs


def set_mode(root, mode, now=None):
    """Записать promotion.mode. -> {"ok", "mode", "was", "changed", "log_error"?}. Битый конфиг или отказ проверки -> ConfigRefused."""
    if mode not in LC.PROMOTION_MODES:
        raise ValueError("mode: %s" % "|".join(LC.PROMOTION_MODES))
    now = int(time.time() if now is None else now)
    try:
        text = _read(root)
        raw = LC.parse(text)
    except (OSError, ValueError) as e:
        raise ConfigRefused("config/learnloop.json не читается: %s" % e)
    errs = LC.check(raw)
    if errs:
        raise ConfigRefused("config/learnloop.json не проходит проверку (%s): исправьте файл" % ",".join(errs[:3]))
    was = raw["promotion"]["mode"]
    rep = {"ok": True, "action": "promotion", "mode": mode, "was": was, "changed": was != mode}
    if not rep["changed"]:
        return rep
    new = render(text, raw, mode)
    path = cfg_path(root)
    _write_atomic(path, new)
    cfg, errs = LC.load(root)
    if errs or LC.promotion_mode(cfg) != mode:
        _write_atomic(path, text)
        raise ConfigRefused("после записи загрузчик не принял конфиг (%s): прежний текст возвращён" % ",".join(errs[:3]))
    RC._try_log(root, "promotion", "PROMOTION_" + mode.upper(), None, {"action": "promotion", "mode": mode, "was": was}, now, rep)
    return rep


def cmd_promotion(a, root):
    if a.mode is None:
        mode, errs = get_mode(root)
        rep = {"ok": not errs, "mode": mode, "errors": errs}
        _out(rep, a.text, ["продвижение: %s%s" % (mode, (" (конфиг с ошибками: %s)" % ",".join(errs[:3])) if errs else "")])
        return 0 if not errs else 1
    try:
        rep = set_mode(root, a.mode)
    except (ConfigRefused, OSError) as e:
        _err(str(e))
        return 1
    _out(rep, a.text, ["продвижение: %s%s%s" % (rep["mode"], "" if rep["changed"] else " (без изменений)",
                                              (" (запись OWNER не сделана: %s)" % rep["log_error"]) if rep.get("log_error") else "")])
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="tb learn", description="цикл обучения: аварийный режим продвижения (R2)")
    sub = p.add_subparsers(dest="cmd")
    pr = sub.add_parser("promotion", help="режим автопродвижения: показать или auto|off")
    pr.add_argument("mode", nargs="?", choices=LC.PROMOTION_MODES)
    pr.add_argument("--text", action="store_true")
    return p


HANDLERS = {"promotion": cmd_promotion}


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
