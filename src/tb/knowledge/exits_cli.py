"""`./tb exits <команда>` (R4, Requirement 16.3; design.md «tb.learnloop.exits_*», «Снимок и аренда», «Ревизии»). Вне пакета
`tb.learnloop`: команды владельца, аварийные средства и отчёты.

Чтение (соединение на вызов через `labels.store.open_ro`):
    status     действующая версия (снимок, аренда), что видит исполнитель (`exitdoc.current`), выключатель, режим продвижения,
               открытые и закрытые испытания, состояние сторожа (только чтение)
    versions   версии `exit_versions` (ver, причина, родитель, хеши, числа; только чтение)
    search     последние поиски `exit_search` (окно, хеш выборки, код, кандидаты; только чтение)
    trials     испытания `exit_trials` (state, verdict, числа; только чтение)
    brain      R4.3 «мозг против алгоритма» (задача 19)
    after      R4.2 путь после закрытия (задача 20)
    gate       гейт этапа 4 одной формулой Requirement 17.3: каждое условие с числами и итог (задача 22)

Аварийные средства владельца (необязательные; вне пакета `tb.learnloop`; каждое изменяющее действие пишет `learn_events`
с `actor=OWNER`, если база доступна):
    apply on|off        только выключатель `runtime/exits_switch.json` (атомарно, без обращения к базе: работает при
                        недоступной/повреждённой базе; база не создаётся). `apply off` сразу даёт `exitdoc.current -> SWITCH`
    rollback            версия `OWNER_ROLLBACK` с политикой пола защиты: `exits_versions.rollback` пишет версию, снимок
                        `runtime/exit_policy.json` и аренду `runtime/exit_lease.json` одной операцией. Отдельная функция
                        `owner_rollback(root, now)` — тот же обработчик для кнопки дашборда и сервиса управления
    promotion auto|off  `promotion.mode` в `config/exits.json`; `set_mode` — ЕДИНСТВЕННЫЙ писатель этого файла. Запись
                        атомарная (tmp + os.replace), меняется только значение `promotion.mode` (остальной текст и поля
                        сохраняются); новый текст проверяется загрузчиком `tb.learnloop.exits_conf` до записи, после записи
                        файл перечитывается (при расхождении прежний текст возвращается). Битый конфиг не правится (код 1)

Вывод: JSON одной строкой; `--text` — короткий текст для человека. Коды: 0 — команда выполнена (для `status` и отчётов —
отчёт построен, даже если данных нет); 1 — база знаний недоступна (нет базы, нет таблиц цикла, идёт восстановление, ошибка
чтения), битый конфиг, отказ записи; 2 — ошибка использования.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time

from tb import paths
from tb.core import exitdoc as XD
from tb.knowledge import markers as KM
from tb.knowledge import schema2 as S2
from tb.knowledge import store2
from tb.labels import store as LST
from tb.learnloop import conf as LC
from tb.learnloop import exits_conf as EC
from tb.learnloop import exits_trial as ET
from tb.learnloop import exits_versions as XV
from tb.learnloop.exits_codes import DB_ERROR, MODES, NO_DB, RESTORING
from tb.learnloop.exits_codes import REASONS_RU as DB_REASONS_RU
from tb.logic import exitpol as XP

OWNER = S2.OWNER
STEP = "exits"
RE_MODE = re.compile(r'("promotion"\s*:\s*\{[^{}]*?"mode"\s*:\s*)"(?:auto|off)"', re.S)
DB_ERRORS = (sqlite3.Error, store2.KnowledgeError, OSError)
SEARCH_COLS = ("id", "ts", "a", "b", "cut_ts", "sample_h", "cfg_h", "menu_ver", "base_ver", "block_h", "code", "results")
REASONS_RU = {
    NO_DB: DB_REASONS_RU[NO_DB],
    RESTORING: DB_REASONS_RU[RESTORING],
    DB_ERROR: DB_REASONS_RU[DB_ERROR],
    "NOT_READY": "таблиц цикла выходов нет (нужна миграция x1: R4 включён после первого прохода цикла)",
}


def _out(obj, text=False, lines=None):
    if text and lines is not None:
        print("\n".join(lines))
    else:
        print(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str))


def _err(msg):
    print("ошибка: %s" % msg, file=sys.stderr)


def _iso(ts):
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ts)))
    except (TypeError, ValueError, OSError):
        return "-"


def _now(now=None):
    return int(time.time() if now is None else now)


class Unavailable(Exception):
    """База знаний недоступна (нет файла, таблиц x1, идёт восстановление, повреждена, ошибка чтения). `code` — код причины."""

    def __init__(self, code, msg=""):
        self.code = code
        super().__init__("%s: %s" % (code, msg) if msg else code)


def _db_report(code, detail=None, now=None, action=None):
    why = REASONS_RU.get(code, code) + ((": %s" % detail) if detail else "")
    return {"ok": False, "db": code, "error": why, "ts": _now(now), "action": action}


# ---------------------------------------------------------------- соединения

def _has_db(root):
    return os.path.exists(LST.db_path(root))


def _open_ro(root):
    try:
        conn = LST.open_ro(root)
    except LST.KnowledgeRestoring as e:
        raise Unavailable(RESTORING, str(e)) from e
    except DB_ERRORS as e:
        raise Unavailable(DB_ERROR, "%s: %s" % (type(e).__name__, e)) from e
    if conn is None:
        raise Unavailable(NO_DB)
    try:
        ok = store2.ready_x1(conn)
    except DB_ERRORS as e:
        conn.close()
        raise Unavailable(DB_ERROR, "%s: %s" % (type(e).__name__, e)) from e
    if not ok:
        conn.close()
        raise Unavailable("NOT_READY")
    return conn


def _open_rw(root):
    """Соединение писателя. Отсутствующая база НЕ создаётся (аварийные средства не должны заводить пустую базу)."""
    if not _has_db(root):
        raise Unavailable(NO_DB)
    try:
        conn = LST.open_rw(root)
    except LST.KnowledgeRestoring as e:
        raise Unavailable(RESTORING, str(e)) from e
    except DB_ERRORS as e:
        raise Unavailable(DB_ERROR, "%s: %s" % (type(e).__name__, e)) from e
    if conn is None:
        raise Unavailable(NO_DB)
    try:
        ok = store2.ready_x1(conn)
    except DB_ERRORS as e:
        conn.close()
        raise Unavailable(DB_ERROR, "%s: %s" % (type(e).__name__, e)) from e
    if not ok:
        conn.close()
        raise Unavailable("NOT_READY")
    return conn


def _try_owner_log(root, code, ref, body, now, rep):
    """Запись `learn_events` с `actor=OWNER` — если база доступна и таблицы v2 на месте. Сбой не ломает аварийную команду."""
    if not _has_db(root):
        rep["log_error"] = REASONS_RU[NO_DB]
        return None
    try:
        conn = LST.open_rw(root)
    except Exception as e:                               # noqa: BLE001 — журнал вторичен, команда уже выполнена
        rep["log_error"] = "%s: %s" % (type(e).__name__, e)
        return None
    try:
        if not store2.ready(conn):
            rep["log_error"] = "таблиц цикла нет (NOT_READY): запись OWNER не сделана"
            return None
        return store2.log_event(conn, STEP, code, OWNER, ref=ref, body=body, now=now)
    except Exception as e:                               # noqa: BLE001 — журнал вторичен
        rep["log_error"] = "%s: %s" % (type(e).__name__, e)
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------- файлы runtime/

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _switch_view(root):
    """Выключатель: файла нет — применение включено; `{"apply": true}` — включено; битый/чужой — выключено (как `exitdoc`)."""
    path = XD.switch_path(root)
    if not os.path.lexists(path):
        return {"apply": True, "source": "default", "ts": None, "actor": None}
    doc = _read_json(path)
    if not isinstance(doc, dict):
        return {"apply": False, "source": "broken", "ts": None, "actor": None}
    return {"apply": doc.get("apply") is True, "source": "file", "ts": doc.get("ts"), "actor": doc.get("actor")}


def write_switch(root, apply, now=None):
    """Выключатель `runtime/exits_switch.json` (атомарно, `markers.write_json`). Не зависит от базы."""
    now = _now(now)
    return KM.write_json(XD.switch_path(root), {"apply": bool(apply), "ts": now, "actor": OWNER})


def _snapshot_view(root):
    doc = _read_json(XD.snapshot_path(root))
    if not isinstance(doc, dict):
        return None
    return {"ver": doc.get("ver"), "policy_h": doc.get("policy_h"), "floor_h": doc.get("floor_h"), "ts": doc.get("ts")}


def _lease_view(root):
    doc = _read_json(XD.lease_path(root))
    if not isinstance(doc, dict):
        return None
    return {"ts": doc.get("ts"), "ver": doc.get("ver"), "policy_h": doc.get("policy_h"), "by": doc.get("by")}


# ---------------------------------------------------------------- отчёты чтения

def _version_brief(v):
    return {"seq": v.get("seq"), "ver": v.get("ver"), "parent": v.get("parent"), "reason_code": v.get("reason_code"),
            "policy_h": v.get("policy_h"), "floor_h": v.get("floor_h"), "actor": v.get("actor"), "ts": v.get("ts"),
            "numbers": v.get("numbers")}


def _trial_brief(t):
    return {"trial_id": t.get("trial_id"), "var_id": t.get("var_id"), "var_ver": t.get("var_ver"), "base_ver": t.get("base_ver"),
            "state": t.get("state"), "verdict": t.get("verdict"), "start_ts": t.get("start_ts"), "deadline_ts": t.get("deadline_ts"),
            "closed_ts": t.get("closed_ts"), "numbers": t.get("numbers")}


def _watch_brief(doc):
    if doc is None:
        return None
    if not isinstance(doc, dict):
        return {"value": doc}
    st = doc.get("state") if isinstance(doc.get("state"), dict) else {}
    pts = st.get("points") if isinstance(st.get("points"), list) else []
    return {"ts": doc.get("ts"), "ver": doc.get("ver"), "prev": doc.get("prev"), "codes": doc.get("codes") or [],
            "points": len(pts), "last_point": pts[-1] if pts else None}


def _status(root, now):
    cfg, errs = EC.load(root)
    floor = EC.current_floor(root)
    try:
        _policy, info = XD.current(root, now, floor)
    except Exception:                                    # noqa: BLE001 — статус не должен падать
        info = {"ver": None, "policy_h": None, "why": "SNAPSHOT"}
    rep = {"ok": True, "ts": now, "action": "status", "db": "OK", "config": {"errors": list(errs)},
           "promotion": {"mode": EC.promotion_mode(cfg)}, "switch": _switch_view(root),
           "snapshot": _snapshot_view(root), "lease": _lease_view(root),
           "effective": {"ver": info.get("ver"), "policy_h": info.get("policy_h"), "why": info.get("why")},
           "version": None, "versions": 0, "trials": {"open": 0, "closed": 0, "rows": []}, "watch": None}
    conn = _open_ro(root)
    try:
        vers = XV.versions(conn)
        trs = sorted(ET.trials(conn), key=lambda t: (int(t.get("start_ts") or 0), str(t.get("trial_id"))), reverse=True)
        rep["version"] = _version_brief(vers[-1]) if vers else None
        rep["versions"] = len(vers)
        rep["trials"] = {"open": sum(1 for t in trs if t.get("state") == ET.OPEN),
                         "closed": sum(1 for t in trs if t.get("state") == ET.CLOSED),
                         "rows": [_trial_brief(t) for t in trs[:20]]}
        rep["watch"] = _watch_brief(XV.watch_state(conn))
    except DB_ERRORS as e:
        raise Unavailable(DB_ERROR, "%s: %s" % (type(e).__name__, e)) from e
    finally:
        conn.close()
    return rep


def status_view(root=None, now=None):
    """Состояние политики выхода для дашборда и CLI (R4, только чтение): тот же отчёт, что печатает `./tb exits status`, без вывода.
    База недоступна — отчёт с файловыми полями и `ok: False` (код причины в `db`), как у `cmd_status`. Не бросает, базу не создаёт."""
    root = paths.root() if root is None else root
    now = _now(now)
    try:
        return _status(root, now)
    except Unavailable as e:
        rep = _db_report(e.code, str(e), now, "status")
        rep["switch"] = _switch_view(root)
        rep["snapshot"] = _snapshot_view(root)
        rep["lease"] = _lease_view(root)
        cfg, errs = EC.load(root)
        rep["promotion"] = {"mode": EC.promotion_mode(cfg)}
        rep["config"] = {"errors": list(errs)}
        rep["version"] = None
        rep["versions"] = 0
        rep["trials"] = {"open": 0, "closed": 0, "rows": []}
        rep["watch"] = None
        rep["effective"] = None
        return rep


def _status_lines(rep):
    sw = "включено" if rep["switch"]["apply"] else "ВЫКЛЮЧЕНО"
    v = rep.get("version") or {}
    eff = rep.get("effective") or {}
    tr = rep.get("trials") or {}
    lease = rep.get("lease")
    watch = rep.get("watch")
    lines = ["версия: %s%s" % (v.get("ver") or "-", (" (%s, %s)" % (v.get("reason_code"), _iso(v.get("ts")))) if v else ""),
             "исполнитель: %s, %s" % (eff.get("ver") or "-", eff.get("why") or "-"),
             "выключатель: %s" % sw,
             "продвижение: %s" % rep["promotion"]["mode"],
             "аренда: %s" % (("%s, %s (by %s)" % (lease.get("ver"), _iso(lease.get("ts")), lease.get("by"))) if lease else "нет"),
             "испытания: открыто %s, закрыто %s" % (tr.get("open", 0), tr.get("closed", 0)),
             "сторож: %s" % (("%s, точки %s" % (watch.get("ver") or "-", watch.get("points"))) if watch else "записи нет")]
    if rep.get("config", {}).get("errors"):
        lines.append("конфиг: ошибки %s" % ",".join(rep["config"]["errors"][:3]))
    if not rep.get("ok"):
        lines.append("база: %s" % rep.get("error"))
    return lines


def cmd_status(a, root):
    rep = status_view(root)
    _out(rep, a.text, _status_lines(rep))
    return 0 if rep.get("ok") else 1


def cmd_versions(a, root):
    now = _now()
    try:
        conn = _open_ro(root)
    except Unavailable as e:
        _out(_db_report(e.code, str(e), now, "versions"), a.text, ["версии: база недоступна (%s)" % e.code])
        return 1
    try:
        rows = XV.versions(conn)
    finally:
        conn.close()
    rep = {"ok": True, "db": "OK", "ts": now, "action": "versions", "n": len(rows),
           "versions": [_version_brief(v) for v in rows]}
    _out(rep, a.text, ["версии: %d" % len(rows)]
         + ["  %s %s %s parent=%s %s" % (v["ver"], _iso(v["ts"]), v["reason_code"], v["parent"] or "-", (v["policy_h"] or "")[:12])
            for v in rows])
    return 0


def cmd_search(a, root):
    now = _now()
    try:
        conn = _open_ro(root)
    except Unavailable as e:
        _out(_db_report(e.code, str(e), now, "search"), a.text, ["поиски: база недоступна (%s)" % e.code])
        return 1
    try:
        limit = max(1, int(a.limit or 20))
        rows = conn.execute("SELECT %s FROM exit_search ORDER BY ts DESC, id DESC LIMIT ?" % ", ".join(SEARCH_COLS),
                            (limit,)).fetchall()
    except DB_ERRORS as e:
        _out(_db_report(DB_ERROR, "%s: %s" % (type(e).__name__, e), now, "search"), a.text, ["поиски: база не читается"])
        return 1
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(zip(SEARCH_COLS, r))
        try:
            d["results"] = json.loads(d.get("results") or "{}")
        except (TypeError, ValueError):
            d["results"] = None
        out.append(d)
    rep = {"ok": True, "db": "OK", "ts": now, "action": "search", "n": len(out), "rows": out}
    lines = ["поиски: %d" % len(out)]
    for d in out:
        res = d.get("results") or {}
        cands = res.get("candidates") if isinstance(res, dict) else None
        lines.append("  %s %s кандидатов=%s путей=%s блоков=%s выборка=%s" % (
            _iso(d.get("ts")), d.get("code"), len(cands or []), res.get("n_paths") if isinstance(res, dict) else "-",
            res.get("n_blocks") if isinstance(res, dict) else "-", d.get("sample_h") or "-"))
    _out(rep, a.text, lines)
    return 0


def cmd_trials(a, root):
    now = _now()
    try:
        conn = _open_ro(root)
    except Unavailable as e:
        _out(_db_report(e.code, str(e), now, "trials"), a.text, ["испытания: база недоступна (%s)" % e.code])
        return 1
    try:
        rows = sorted(ET.trials(conn, state=getattr(a, "state", None)),
                      key=lambda t: (int(t.get("start_ts") or 0), str(t.get("trial_id"))), reverse=True)
    finally:
        conn.close()
    rep = {"ok": True, "db": "OK", "ts": now, "action": "trials", "n": len(rows),
           "trials": [_trial_brief(t) for t in rows]}
    _out(rep, a.text, ["испытания: %d" % len(rows)]
         + ["  %s %s/%s %s %s с %s" % (t["trial_id"], t["var_id"], t["var_ver"], t["state"], t["verdict"] or "-", _iso(t["start_ts"]))
            for t in rows])
    return 0


def cmd_gate(a, root):
    """Гейт этапа 4 (Requirement 17.3–17.4): каждое условие с числами и итог. Код 1 — база знаний недоступна."""
    from tb.learnloop import exits_report as ER
    rep = ER.run_gate(root, mode=getattr(a, "mode", None))
    if rep.get("db") not in (None, "OK"):
        _err("%s" % rep.get("error"))
        return 1
    _out(rep, getattr(a, "text", False), ER.render_gate(rep))
    return 0


# ---------------------------------------------------------------- apply / rollback

def cmd_apply(a, root):
    now = _now()
    on = a.state == "on"
    write_switch(root, on, now)
    rep = {"ok": True, "action": "apply", "apply": bool(on), "ts": now, "switch": XD.switch_path(root)}
    _try_owner_log(root, "APPLY_ON" if on else "APPLY_OFF", None, {"action": "apply", "apply": bool(on)}, now, rep)
    lines = ["применение политики выходов: %s" % ("включено" if on else "ВЫКЛЮЧЕНО (исполнитель на полу защиты)")]
    if rep.get("log_error"):
        lines.append("запись OWNER не сделана: %s" % rep["log_error"])
    _out(rep, a.text, lines)
    return 0


def owner_rollback(root=None, now=None):
    """Обработчик владельца «Выходы: вернуть границу» (Requirement 16.6) и команда `./tb exits rollback`.

    Отдельная функция для кнопки дашборда и сервиса управления: создаёт версию `OWNER_ROLLBACK` с политикой пола защиты,
    пишет снимок и аренду (`exits_versions.rollback`), затем записывает `learn_events` с `actor=OWNER`.
    -> {"ok", "action", "ver", "from", "snapshot", "lease", "log_error"?} или {"ok": False, "db", "error"}.
    Исключений наружу не бросает.
    """
    now = _now(now)
    rep = {"ok": False, "action": "rollback", "ts": now, "ver": None, "from": None, "snapshot": None, "lease": None}
    try:
        conn = _open_rw(root)
    except Unavailable as e:
        rep.update(db=e.code, error=REASONS_RU.get(e.code, e.code) + ((": %s" % e) if str(e) else ""))
        return rep
    try:
        floor = EC.current_floor(root) or XP.floor({})
        if XV.last_version(conn) is None:
            XV.init(conn, floor, now)
        cur = XV.last_version(conn)
        cfg, errs = EC.load(root)
        ver = XV.rollback(conn, root, "OWNER_ROLLBACK", {"by": OWNER}, now, cfg=None if errs else cfg)
        rep.update(ok=True, db="OK", ver=ver)
        rep["from"] = cur["ver"] if cur else None
    except (XV.PromoteRefused, DB_ERRORS, ValueError, KeyError) as e:
        rep.update(db="ERROR", error="%s: %s" % (type(e).__name__, e))
        return rep
    finally:
        conn.close()
    rep["snapshot"] = _snapshot_view(root)
    rep["lease"] = _lease_view(root)
    _try_owner_log(root, "OWNER_ROLLBACK", rep["ver"],
                   {"action": "rollback", "from": rep["from"], "ver": rep["ver"], "policy_h": (rep["snapshot"] or {}).get("policy_h")},
                   now, rep)
    return rep


def cmd_rollback(a, root):
    rep = owner_rollback(root)
    ok = bool(rep.get("ok"))
    lines = ["откат владельца: версия %s (политика пола защиты)" % rep.get("ver") if ok else
             "откат владельца не выполнен: %s" % (rep.get("error") or rep.get("db"))]
    if rep.get("log_error"):
        lines.append("запись OWNER не сделана: %s" % rep["log_error"])
    _out(rep, a.text, lines)
    return 0 if ok else 1


# ---------------------------------------------------------------- promotion.mode

class ConfigRefused(Exception):
    """Конфиг не записан: не читается, не проходит загрузчик, не удалось проверить запись."""


def cfg_path(root=None):
    from tb import paths
    return paths.config(EC.FILE, root=root)


def _read_cfg(root):
    with open(cfg_path(root), encoding="utf-8") as f:
        return f.read()


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


def render(text, raw, mode, floor):
    """Новый текст конфига с `promotion.mode` = mode: замена одного значения в тексте (форматирование сохраняется), иначе —
    полная сериализация. Результат проходит загрузчик и отличается от прежнего ровно полем `promotion.mode`, иначе ConfigRefused."""
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
        if got == want and not EC.check(got, floor):
            return c
    raise ConfigRefused("новый текст config/exits.json не прошёл проверку загрузчиком")


def get_mode(root=None):
    """-> (mode, ошибки загрузчика)."""
    cfg, errs = EC.load(root)
    return EC.promotion_mode(cfg), errs


def set_mode(root, mode, now=None):
    """Записать `promotion.mode` — единственное место, где пишется `config/exits.json`.

    -> {"ok", "action", "mode", "was", "changed", "log_error"?}. Битый конфиг или отказ проверки -> ConfigRefused.
    Остальные поля и форматирование файла сохраняются; запись атомарная; после записи текст перечитывается загрузчиком.
    """
    if mode not in EC.PROMOTION_MODES:
        raise ValueError("mode: %s" % "|".join(EC.PROMOTION_MODES))
    now = _now(now)
    floor = EC.current_floor(root)
    if not isinstance(floor, dict):
        raise ConfigRefused("пол защиты (exec_limits.auto_protect) не читается: конфиг не проверить")
    try:
        text = _read_cfg(root)
        raw = LC.parse(text)
    except (OSError, ValueError) as e:
        raise ConfigRefused("config/exits.json не читается: %s" % e)
    errs = EC.check(raw, floor)
    if errs:
        raise ConfigRefused("config/exits.json не проходит проверку (%s): исправьте файл" % ",".join(errs[:3]))
    was = EC.promotion_mode(raw)
    rep = {"ok": True, "action": "promotion", "mode": mode, "was": was, "changed": was != mode}
    if not rep["changed"]:
        return rep
    new = render(text, raw, mode, floor)
    path = cfg_path(root)
    _write_atomic(path, new)
    cfg, errs = EC.load(root)
    if errs or EC.promotion_mode(cfg) != mode:
        _write_atomic(path, text)
        raise ConfigRefused("после записи загрузчик не принял конфиг (%s): прежний текст возвращён" % ",".join(errs[:3]))
    _try_owner_log(root, "PROMOTION_" + mode.upper(), None,
                   {"action": "promotion", "mode": mode, "was": was}, now, rep)
    return rep


def cmd_promotion(a, root):
    if a.mode is None:
        mode, errs = get_mode(root)
        rep = {"ok": not errs, "action": "promotion", "mode": mode, "errors": errs}
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


# ---------------------------------------------------------------- after / brain

def cmd_after(a, root):
    from tb.learnloop import exits_report as ER
    since = int(time.time()) - int(a.days) * 86400 if a.days else None
    rep = ER.run(root, mode=a.mode, since=since)
    if rep.get("db") not in (None, "OK"):
        _err("%s" % rep.get("error"))
        return 1
    _out(rep, a.text, ER.render(rep))
    return 0


def cmd_brain(a, root):
    from tb.learnloop import exits_report as ER
    since = int(time.time()) - int(a.days) * 86400 if a.days else None
    rep = ER.run_brain(root, mode=a.mode, since=since)
    if rep.get("db") not in (None, "OK"):
        _err("%s" % rep.get("error"))
        return 1
    _out(rep, a.text, ER.render_brain(rep))
    return 0


# ---------------------------------------------------------------- разбор команд

def build_parser():
    p = argparse.ArgumentParser(prog="tb exits", description="политика выхода (R4): отчёты, версии и аварийные средства владельца")
    sub = p.add_subparsers(dest="cmd")

    st = sub.add_parser("status", help="версия, снимок и аренда, что видит исполнитель, выключатель, испытания, сторож (только чтение)")
    st.add_argument("--text", action="store_true", help="короткий текст вместо JSON")

    vs = sub.add_parser("versions", help="версии политики выхода X-NNNN (только чтение)")
    vs.add_argument("--text", action="store_true", help="короткий текст вместо JSON")

    se = sub.add_parser("search", help="последние поиски вариантов (только чтение)")
    se.add_argument("--limit", type=int, default=20, help="сколько последних поисков показать (по умолчанию 20)")
    se.add_argument("--text", action="store_true", help="короткий текст вместо JSON")

    tr = sub.add_parser("trials", help="испытания вариантов: состояние, вердикт, числа (только чтение)")
    tr.add_argument("state", nargs="?", choices=("open", "closed"), help="только открытые или только закрытые")
    tr.add_argument("--text", action="store_true", help="короткий текст вместо JSON")

    ga = sub.add_parser("gate", help="гейт этапа 4: каждое условие с числами и итог (только чтение)")
    ga.add_argument("--mode", choices=MODES, help="режим торговли (по умолчанию brain.mode конфига, иначе paper)")
    ga.add_argument("--text", action="store_true", help="короткий текст вместо JSON")

    ap = sub.add_parser("apply", help="выключатель применения политики runtime/exits_switch.json (работает без базы)")
    ap.add_argument("state", choices=("on", "off"), help="on — применять политику, off — пол защиты")
    ap.add_argument("--text", action="store_true", help="короткий текст вместо JSON")

    rb = sub.add_parser("rollback", help="вернуть границу: версия OWNER_ROLLBACK с политикой пола защиты")
    rb.add_argument("--text", action="store_true", help="короткий текст вместо JSON")

    pr = sub.add_parser("promotion", help="режим автопродвижения: показать или auto|off")
    pr.add_argument("mode", nargs="?", choices=EC.PROMOTION_MODES)
    pr.add_argument("--text", action="store_true")

    af = sub.add_parser("after", help="путь после закрытия: ход после выхода, достижение t1 по причинам (только чтение)")
    af.add_argument("--mode", choices=MODES, help="режим торговли для сделок мозга (по умолчанию brain.mode конфига)")
    af.add_argument("--days", type=int, default=0, help="окно: сделки и прогоны с entry_ts не старше N суток")
    af.add_argument("--text", action="store_true", help="короткий текст вместо JSON")

    br = sub.add_parser("brain", help="R4.3: мозг против алгоритма по сделкам мозга (только чтение)")
    br.add_argument("--mode", choices=MODES, help="режим торговли (по умолчанию brain.mode конфига)")
    br.add_argument("--days", type=int, default=0, help="окно: сделки с entry_ts не старше N суток")
    br.add_argument("--text", action="store_true", help="короткий текст вместо JSON")
    return p


HANDLERS = {"status": cmd_status, "versions": cmd_versions, "search": cmd_search, "trials": cmd_trials,
            "gate": cmd_gate, "apply": cmd_apply, "rollback": cmd_rollback, "promotion": cmd_promotion,
            "after": cmd_after, "brain": cmd_brain}


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
