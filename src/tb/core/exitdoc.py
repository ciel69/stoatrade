"""Читатель действующей политики выхода для исполнителя (R4, задача 14; Requirements 9.1, 8.6, 18.3; design.md «tb.core.exitdoc»,
«Снимок и аренда», свойство 19 «Откат к полу при отказе источника»).

Исполнитель получает политику выхода только здесь: снимок `runtime/exit_policy.json`, аренда `runtime/exit_lease.json`, выключатель
`runtime/exits_switch.json`, срок аренды `config/exits.json: lease.lease_h`. Файлы пишут цикл выходов (продвижение, откат, сторож) и
команда владельца `./tb exits apply`; модуль только читает и ничего не пишет.

`current(root, now, floor_policy)` -> `(policy, info)`. Проверки идут в порядке `SWITCH`, `SNAPSHOT`, `LEASE`, `HASH`; `info["why"]` —
причина возврата пола (`OK` — действует снимок), `info["ver"]` и `info["policy_h"]` — версия и хеш возвращённой политики:

  * `SWITCH`   — выключатель `runtime/exits_switch.json` существует и не `{"apply": true}`; файла нет — применение включено (как у
                 правил R2, `knowledge/rulebook_view.py:read_switch`), битый выключатель — выключен;
  * `SNAPSHOT` — снимка нет, файл не читается, корень не объект, чужой `schema` (не `tb.exit_snapshot/1`), политики нет или она не
                 словарь, `exitpol.validate` вернул ошибки, нет `ver`/`policy_h`; сюда же любая неожиданная ошибка читателя;
  * `LEASE`    — аренды нет, файл битый, нет числового `ts`, аренда старше `lease_h` часов, аренда «из будущего» дальше допуска
                 `FUTURE_SLACK_S`, либо порог `lease.lease_h` не читается (без конфига свежесть аренды не проверить: числовых умолчаний
                 в коде нет, Requirement 16.1);
  * `HASH`     — `policy_h` снимка не совпадает с пересчитанным `exitpol.policy_hash(policy)` или с `policy_h` аренды (Requirement 8.6:
                 хеш снимка и аренды пишутся одной операцией продвижения/отката, Requirement 7.1).

Снимок возвращается как есть: политика нормализована при записи (Requirement 2.4), а нормализация с текущим полом здесь не делается —
смена пола не отменяет действующую версию, новый пол действует композицией со следующего `evaluate_manage` (Requirement 2.7). Поэтому
`floor_h` снимка не сверяется с хешем переданного пола: пол — только запасной вариант при отказе источника.

При любой ошибке возвращается пол (`floor_policy` как есть) и причина; исключений наружу нет (Requirement 8.6). `ver` пола — `X-0001`
(Glossary: версия пола на момент создания базы выходов). Снимок, аренда и конфиг кешируются в памяти по подписи файла (mtime в
наносекундах и размер), выключатель читается каждый раз: владелец ждёт немедленного действия `./tb exits apply off` (Requirement 16.4).

Слои (Requirement 18.3): не импортирует `tb.learnloop`, `tb.knowledge`, `tb.labels`, `tb.venues`, `tb.executor` (прямо и транзитивно);
из `tb` знает только `tb.paths` и `tb.logic.exitpol`.
"""
import copy
import json
import math
import os
import threading
import time

from tb import paths
from tb.logic import exitpol as XP

SNAPSHOT_FILE = "exit_policy.json"
LEASE_FILE = "exit_lease.json"
SWITCH_FILE = "exits_switch.json"
CONFIG_FILE = "exits.json"
SNAPSHOT_SCHEMA = "tb.exit_snapshot/1"
FLOOR_VER = "X-0001"
WHY = ("OK", "LEASE", "SNAPSHOT", "HASH", "SWITCH")
FUTURE_SLACK_S = 300.0           # аренда «из будущего» дальше этого допуска не считается свежей (как rulebook_view.LEASE_FUTURE_SLACK_S)
_EMPTY_FLOOR = {"schema": XP.SCHEMA}

_CACHE = {}                     # (абсолютный путь -> (подпись, разобранный JSON-объект | None)); подпись None не кешируется
_LOCK = threading.Lock()


# ---------------------------------------------------------------- пути файлов (общие с писателями цикла выходов)
def snapshot_path(root=None):
    """`runtime/exit_policy.json`: снимок действующей версии (`{"schema": "tb.exit_snapshot/1", "ver", "policy", "policy_h", "floor_h", "ts"}`)."""
    return paths.runtime(SNAPSHOT_FILE, root=root)


def lease_path(root=None):
    """`runtime/exit_lease.json`: аренда сторожа выходов (`{"ts", "ver", "policy_h", "by"}`)."""
    return paths.runtime(LEASE_FILE, root=root)


def switch_path(root=None):
    """`runtime/exits_switch.json`: выключатель применения политики (`{"apply": bool, "ts", "actor"}`)."""
    return paths.runtime(SWITCH_FILE, root=root)


def clear_cache():
    """Очистить кеш чтения (тесты). Продвижение и откат пишут файлы атомарно, подпись меняется сама — вызывать не нужно."""
    with _LOCK:
        _CACHE.clear()


# ---------------------------------------------------------------- мелочи чтения
def _num(x):
    """Число (не bool, конечное) или None."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    v = float(x)
    return v if math.isfinite(v) else None


def _sig(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _read_obj(path):
    """-> (ok, dict). Файла нет / не читается / не JSON-объект -> (False, None). Кеш по подписи файла: mtime (нс) и размер."""
    sig = _sig(path)
    if sig is None:
        return False, None
    with _LOCK:
        hit = _CACHE.get(path)
    if hit is not None and hit[0] == sig:
        return hit[1] is not None, hit[1]
    doc = None
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        raw = None
    if isinstance(raw, dict):
        doc = raw
    with _LOCK:
        _CACHE[path] = (sig, doc)
    return doc is not None, doc


def _now(now):
    v = _num(now)
    return time.time() if v is None else v


def _switch_on(root=None):
    """Выключатель применения политики. Файла нет -> True; `{"apply": true}` -> True; битый файл, не bool, чужое значение -> False."""
    p = switch_path(root)
    if not os.path.lexists(p):
        return True
    try:
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    return isinstance(doc, dict) and doc.get("apply") is True


def _lease_h(root=None):
    """`lease.lease_h` (часы) из `config/exits.json` или None: файл не читается, раздела нет, значение не положительное число.
    Строгую проверку конфига делает загрузчик цикла (`tb.learnloop.exits_conf`); читателю нужен только срок аренды."""
    ok, cfg = _read_obj(paths.config(CONFIG_FILE, root=root))
    if not ok:
        return None
    lease = cfg.get("lease")
    if not isinstance(lease, dict):
        return None
    h = _num(lease.get("lease_h"))
    return h if h is not None and h > 0 else None


def _snapshot_view(doc):
    """Политика снимка -> {"policy", "ver", "policy_h"} или None (не тот файл, не проходит схему, нет версии или хеша)."""
    if not isinstance(doc, dict) or doc.get("schema") != SNAPSHOT_SCHEMA:
        return None
    pol, ver, h = doc.get("policy"), doc.get("ver"), doc.get("policy_h")
    if not isinstance(pol, dict) or not isinstance(ver, str) or not ver:
        return None
    if not isinstance(h, str) or not h:
        return None
    if XP.validate(pol):
        return None
    return {"policy": pol, "ver": ver, "policy_h": h}


def _fallback(floor, why):
    """Пол и причина. Хеш пола считается, но сбой канонизации (NaN и т. п.) не должен бросать исключение наружу."""
    try:
        h = XP.policy_hash(floor)
    except Exception:                                    # noqa: BLE001 — читатель не бросает (Requirement 8.6)
        h = None
    return copy.deepcopy(floor), {"ver": FLOOR_VER, "policy_h": h, "why": why}


# ---------------------------------------------------------------- действующая политика
def current(root=None, now=None, floor_policy=None, lease_h=None):
    """Действующая политика выхода или пол защиты -> `(policy, info)`; `info = {"ver", "policy_h", "why"}` с `why` из `WHY`
    (`OK` — политика снимка; иначе причина отката к полу, Requirement 8.6). `root` — корень проекта, `now` — время (секунды UTC),
    `floor_policy` — пол из `exitpol.floor(exec_limits.auto_protect)`, `lease_h` — срок аренды в часах (None — из `config/exits.json`).

    Не бросает: любая ошибка чтения, разбора или проверки даёт пол и причину. Политика возвращается копией (кеш не портится вызывающим).
    """
    floor = copy.deepcopy(floor_policy) if isinstance(floor_policy, dict) else copy.deepcopy(_EMPTY_FLOOR)
    try:
        if not _switch_on(root):
            return _fallback(floor, "SWITCH")
        ok, doc = _read_obj(snapshot_path(root))
        view = _snapshot_view(doc) if ok else None
        if view is None:
            return _fallback(floor, "SNAPSHOT")
        ok, lease = _read_obj(lease_path(root))
        ts = _num(lease.get("ts")) if ok else None
        hours = _num(lease_h)
        if hours is None:
            hours = _lease_h(root)
        if ts is None or hours is None or hours <= 0:
            return _fallback(floor, "LEASE")
        age = _now(now) - ts
        if age > hours * 3600.0 or age < -FUTURE_SLACK_S:
            return _fallback(floor, "LEASE")
        h = view["policy_h"]
        if lease.get("policy_h") != h or XP.policy_hash(view["policy"]) != h:
            return _fallback(floor, "HASH")
        return copy.deepcopy(view["policy"]), {"ver": view["ver"], "policy_h": h, "why": "OK"}
    except Exception:                                    # noqa: BLE001 — читатель не бросает (Requirement 8.6)
        return _fallback(floor, "SNAPSHOT")
