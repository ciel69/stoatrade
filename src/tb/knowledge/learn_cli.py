"""`./tb learn <команда>` (R2, Requirements 10.6, 8.4, 11.10; design.md, «Надзор за надзором и аварийные средства»). Вне пакета tb.learnloop:
только здесь пишется config/learnloop.json:promotion.mode.

promotion            текущий режим продвижения (auto | off)
promotion auto|off   аварийная остановка (off) или возврат (auto) автопродвижения. Запись атомарная (tmp + os.replace), меняется только значение
                     promotion.mode (остальные поля и форматирование файла сохраняются); новый текст проверяется загрузчиком tb.learnloop.conf
                     до записи, после записи файл перечитывается загрузчиком (при расхождении прежний текст возвращается). Битый конфиг не
                     правится (код 1). При изменении — learn_events actor=OWNER (если база доступна). Сервис цикла перечитывает конфиг сам.

gate [--json]        гейт этапа R2 (Requirements 15.8, 15.9; tb.learnloop.gate): ступени A и B, «да/нет» с причиной по каждому условию по
                     накопленной истории базы знаний (только чтение). Вывод по умолчанию — текст, --json — JSON одной строкой. Коды: 0 оценка
                     выполнена (пройден гейт или нет — поле ok); 1 база недоступна (нет базы, нет таблиц цикла, идёт восстановление, ошибка чтения).

menu-wave --check [признаки] [--days N] [--json]
                     R1.7 (Requirement 15.7, 15.8): критерий волны признаков сводного рынка по окну поиска текущего меню (только чтение,
                     вывод чисел): (а) заполнение среди заполненных прогонов P2 за последние N суток (первая волна — 7) ≥ feat_cover_min,
                     (б) q20 < q80, (в) |Спирмен| с признаками меню ≤ 0.7, (г) мощность конвейера медианного условия при −0.3R с новым K
                     на установившемся окне 14 суток ≥ 0.5 и снижение ≤ 0.05 (learnloop.menu.wave_check). Без признаков — первая волна.
                     Коды: 0 расчёт выполнен (прошёл ли признак — поля ok_*); 1 нет данных или база недоступна; 2 ошибка использования.

Вывод promotion: JSON одной строкой (--text: кратко). Коды: 0 успех; 1 конфиг битый или не записан; 2 ошибка использования.
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


def cmd_gate(a, root):
    from tb.learnloop import gate as GT
    rep = GT.run(root)
    if a.json:
        _out(rep)
    else:
        _out(rep, True, GT.render(rep))
    return 1 if rep.get("db") not in (None, "OK") else 0


def menu_wave(root, feats, now=None, days=None, env="mainnet"):
    """Проверка критерия волны признаков меню (R1.7, Requirement 15.7) по базе знаний root: соединение только для чтения на вызов, окно
    поиска текущего меню (learnloop.candidates.discovery_window), чёрный список — как у меню. -> отчёт menu.wave_check с полями db, code."""
    import sqlite3
    from tb.labels import store as LST
    from tb.learnloop import candidates as CA
    from tb.learnloop import clusters as CL
    from tb.learnloop import menu as MN
    now = int(time.time() if now is None else now)
    base = {"feats_req": list(feats), "now": now, "env": env}
    try:
        cfg = LC.load_strict(root)
        policy = CL.load_policy(root, cfg)
    except (OSError, ValueError) as e:
        return dict(base, db="CONFIG", code="CONFIG", why=str(e)[:200])
    try:
        conn = LST.open_ro(root)
    except LST.KnowledgeRestoring:
        return dict(base, db="RESTORING", code="NO_DATA")
    except sqlite3.Error as e:
        return dict(base, db="DB_ERROR", code="NO_DATA", why="%s: %s" % (type(e).__name__, e))
    if conn is None:
        return dict(base, db="NO_DB", code="NO_DATA")
    try:
        win, code = CA.discovery_window(conn, now, cfg, policy, env)
        if win is None:
            return dict(base, db="OK", code=code)
        rep = MN.wave_check(win, cfg, feats, blocked=CA._blocked(conn, now), fill_days=days)
        return dict(base, db="OK", code="OK", policy=policy["id"], days_req=days, **rep)
    except (sqlite3.Error, CL.WindowNotReady) as e:
        return dict(base, db="DB_ERROR", code="NO_DATA", why="%s: %s" % (type(e).__name__, e))
    finally:
        conn.close()


def render_wave(rep):
    """Текст для человека: числа по каждому признаку и мощность (только вывод, решения не принимаются)."""
    yn = lambda v: "—" if v is None else ("да" if v else "нет")  # noqa: E731
    if rep.get("code") != "OK":
        return ["проверка волны: нет данных (%s%s)" % (rep.get("code"), (": " + rep["why"]) if rep.get("why") else "")]
    w = rep["window"]
    lines = ["проверка волны меню: окно поиска %.2f сут, заполнение по последним %.2f сут (%d заполненных прогонов)" % (w["days"], w["fill_days"], w["n_fill"])]
    for f, d in rep["feats"].items():
        lines.append("%s: заполнение %.4f (а %s), q20 %s, q80 %s (б %s), |Спирмен| max %s (в %s)" % (
            f, d["cover"], yn(d["a"]), d["q20"], d["q80"], yn(d["b"]), d["rho_max"], yn(d["c"])))
    pw = rep["power"]
    for name, key in (("без волны", "base"), ("с волной", "wave")):
        x = pw[key]
        lines.append("мощность %s: K %d, условий %d, поток %s/сут, σ_d %s, скрининг %s, holdout %s, испытание %s, конвейер %s" % (
            name, x["k"], x["n_cond"], x["flow"], x["sd_d"], x.get("screen"), x.get("holdout"), x.get("trial"), x.get("pipeline")))
    lines.append("снижение мощности %s (эффект %sR, окно %s), (г) %s" % (pw["drop"], pw["effect_r"], "установившееся" if pw["steady"] else "неполное", yn(rep["ok_d"])))
    lines.append("итог: а %s, б %s, в %s, г %s" % (yn(rep["ok_a"]), yn(rep["ok_b"]), yn(rep["ok_c"]), yn(rep["ok_d"])))
    return lines


def cmd_menu_wave(a, root):
    from tb.logic import cmkt as LCM
    from tb.logic import learn_cond as LCD
    if not a.check:
        _err("нужен --check (команда только проверяет критерий волны)")
        return 2
    feats = list(a.features or LCD.CM_WAVE)
    bad = [f for f in feats if f not in LCM.CM_FEATURES or f == "cm_lead_ex"]          # cm_lead_ex — категориальный, в меню не идёт
    if bad:
        _err("признаки вне CM_FEATURES: %s" % ",".join(bad))
        return 2
    rep = menu_wave(root, feats, now=a.now, days=a.days, env=a.env)
    if a.json:
        _out(rep)
    else:
        _out(rep, True, render_wave(rep))
    return 0 if rep.get("code") == "OK" else 1


def build_parser():
    p = argparse.ArgumentParser(prog="tb learn", description="цикл обучения: аварийный режим продвижения, гейт этапа (R2)")
    sub = p.add_subparsers(dest="cmd")
    pr = sub.add_parser("promotion", help="режим автопродвижения: показать или auto|off")
    pr.add_argument("mode", nargs="?", choices=LC.PROMOTION_MODES)
    pr.add_argument("--text", action="store_true")
    gt = sub.add_parser("gate", help="гейт этапа R2: ступени A и B с причиной по каждому условию (только чтение)")
    gt.add_argument("--json", action="store_true")
    mw = sub.add_parser("menu-wave", help="R1.7: критерий волны признаков меню (заполнение, q20<q80, Спирмен, мощность; только чтение)")
    mw.add_argument("--check", action="store_true", help="проверить критерий (обязателен)")
    mw.add_argument("features", nargs="*", help="признаки cm_* (по умолчанию первая волна)")
    mw.add_argument("--days", type=float, default=None, help="окно заполнения, суток (первая волна — 7; по умолчанию всё окно поиска)")
    mw.add_argument("--env", default="mainnet", choices=("mainnet", "testnet"))
    mw.add_argument("--now", type=float, default=None, help=argparse.SUPPRESS)
    mw.add_argument("--json", action="store_true")
    return p


HANDLERS = {"promotion": cmd_promotion, "gate": cmd_gate, "menu-wave": cmd_menu_wave}


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
