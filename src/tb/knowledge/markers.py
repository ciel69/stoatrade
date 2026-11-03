"""Маркеры и сигналы защиты базы знаний (Requirement 2.4, 2.11; design.md, «Сбой базы и восстановление»). Только файлы, без SQLite-соединений.

state/knowledge.v2                    режим v2 (создаёт migrate_v2a): open_rw не уходит в карантин, повреждение -> DbCorruptError
runtime/knowledge.restoring           идёт протокол восстановления: open_ro/open_rw не открывают соединение (KnowledgeRestoring)
runtime/knowledge.writers/<pid>       регистрация долгоживущего писателя (сервис labels, ./tb labels backfill|rerun)
runtime/knowledge.restoring.ack/<pid> подтверждение писателя: соединение закрыто, записи на паузе
runtime/knowledge.corrupt             сигнал повреждения от сервиса labels (pid, время, код) для сервиса цикла
runtime/knowledge.restore_failed.json протокол прерван: красный статус до ручного `./tb knowledge restore`

is_corruption(e) - единственный классификатор повреждения для записей меток и шагов цикла (labels.store._is_corrupt R1 не годится:
для любой не-OperationalError он даёт True, то есть триггер append-only или UNIQUE запустили бы восстановление).
Модуль импортируется из labels.store, поэтому сам не импортирует ни labels, ни store2.
"""
import json
import os
import sqlite3
import time

from tb import paths
from tb.core.filelock import pid_alive

V2_MARKER = "knowledge.v2"
RESTORING = "knowledge.restoring"
ACK_DIR = "knowledge.restoring.ack"
WRITERS_DIR = "knowledge.writers"
CORRUPT = "knowledge.corrupt"
RESTORE_FAILED = "knowledge.restore_failed.json"

SQLITE_CORRUPT = 11
SQLITE_NOTADB = 26


# ---------------------------------------------------------------- пути

def v2_path(root=None):
    return paths.state(V2_MARKER, root=root)


def restoring_path(root=None):
    return paths.runtime(RESTORING, root=root)


def corrupt_path(root=None):
    return paths.runtime(CORRUPT, root=root)


def restore_failed_path(root=None):
    return paths.runtime(RESTORE_FAILED, root=root)


def writers_dir(root=None):
    return paths.runtime(WRITERS_DIR, root=root)


def ack_dir(root=None):
    return paths.runtime(ACK_DIR, root=root)


def has_v2(root=None):
    return os.path.exists(v2_path(root))


def restoring(root=None):
    return os.path.exists(restoring_path(root))


# ---------------------------------------------------------------- файлы

def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, obj):
    """Атомарная запись (tmp + os.replace, повтор при PermissionError на Windows)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    for i in range(20):
        try:
            os.replace(tmp, path)
            return path
        except PermissionError:
            if i == 19:
                raise
            time.sleep(0.05)
    return path


def remove(path):
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def _pids(d):
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for n in names:
        try:
            out.append(int(n))
        except ValueError:
            continue
    return sorted(out)


# ---------------------------------------------------------------- писатели и подтверждения

def register_writer(root=None, role="writer", pid=None, now=None):
    pid = os.getpid() if pid is None else int(pid)
    return write_json(os.path.join(writers_dir(root), str(pid)), {"pid": pid, "role": role, "ts": int(time.time() if now is None else now)})


def unregister_writer(root=None, pid=None):
    pid = os.getpid() if pid is None else int(pid)
    remove(os.path.join(writers_dir(root), str(pid)))
    if not restoring(root):                               # во время протокола подтверждение остаётся (его стирает исполнитель протокола)
        remove(os.path.join(ack_dir(root), str(pid)))


def writers(root=None, alive=None):
    """-> pid живых зарегистрированных писателей; файлы мёртвых процессов удаляются."""
    alive = alive or pid_alive
    out = []
    for pid in _pids(writers_dir(root)):
        if alive(pid):
            out.append(pid)
        else:
            remove(os.path.join(writers_dir(root), str(pid)))
    return out


def ack(root=None, pid=None, now=None):
    pid = os.getpid() if pid is None else int(pid)
    return write_json(os.path.join(ack_dir(root), str(pid)), {"pid": pid, "ts": int(time.time() if now is None else now)})


def has_ack(root=None, pid=None):
    pid = os.getpid() if pid is None else int(pid)
    return os.path.exists(os.path.join(ack_dir(root), str(pid)))


def acks(root=None):
    return set(_pids(ack_dir(root)))


def clear_acks(root=None):
    for pid in _pids(ack_dir(root)):
        remove(os.path.join(ack_dir(root), str(pid)))


# ---------------------------------------------------------------- сигнал повреждения

def write_corrupt(root=None, code="DB_CORRUPT", where=None, pid=None, now=None):
    d = {"pid": os.getpid() if pid is None else int(pid), "ts": int(time.time() if now is None else now), "code": code}
    if where:
        d["where"] = str(where)[:40]
    return write_json(corrupt_path(root), d)


def read_corrupt(root=None):
    if not os.path.exists(corrupt_path(root)):
        return None
    d = read_json(corrupt_path(root))
    return d if isinstance(d, dict) else {"code": "DB_CORRUPT"}


# ---------------------------------------------------------------- классификатор

def _one(e):
    if getattr(e, "code", None) == "DB_CORRUPT" and isinstance(e, sqlite3.DatabaseError):
        return True                                       # уже распознанное labels.store.DbCorruptError
    if not isinstance(e, sqlite3.DatabaseError) or isinstance(e, (sqlite3.IntegrityError, sqlite3.ProgrammingError)):
        return False
    code = getattr(e, "sqlite_errorcode", None)
    if isinstance(code, int):
        return (code & 0xFF) in (SQLITE_CORRUPT, SQLITE_NOTADB)
    m = str(e).lower()
    return "malformed" in m or "not a database" in m


def is_corruption(e):
    """True только для sqlite3.DatabaseError с основным кодом SQLITE_CORRUPT/SQLITE_NOTADB (без sqlite_errorcode - по тексту malformed / not a database),
    в том числе в цепочке __context__/__cause__ (SchemaMigrationError оборачивает исходную ошибку). IntegrityError, ProgrammingError, database is locked - нет."""
    seen, stack = set(), [e]
    while stack:
        x = stack.pop()
        if x is None or id(x) in seen:
            continue
        seen.add(id(x))
        if _one(x):
            return True
        stack.append(getattr(x, "__cause__", None))
        stack.append(getattr(x, "__context__", None))
    return False
