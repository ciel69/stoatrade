"""Хранилище меток: state/knowledge.sqlite (stdlib sqlite3, WAL). Писатель: open_rw + tx (BEGIN IMMEDIATE); читатели: open_ro (query_only).

R2 (Requirement 2.4, 2.11): при маркере runtime/knowledge.restoring open_ro/open_rw не открывают соединение (KnowledgeRestoring); при маркере
state/knowledge.v2 open_rw не переименовывает повреждённую базу и не создаёт пустую, а поднимает DbCorruptError (код DB_CORRUPT; распознаёт
tb.knowledge.markers.is_corruption). Без маркеров поведение R1 прежнее (_is_corrupt и _quarantine не меняются).
"""
import contextlib
import datetime
import os
import sqlite3
import time

from tb import paths
from tb.knowledge import markers as KM
from tb.labels import schema

JOURNAL_SIZE_LIMIT = 64 * 1024 * 1024
BUSY_TIMEOUT_MS = 5000
BACKUP_TRIES = 3
SYNC_MARKERS = ("onedrive", "dropbox", "icloud", "google drive")

LAST_ERROR = None          # DB_CORRUPT после пересоздания повреждённой базы (читает сервис для heartbeat)


class SchemaMigrationError(Exception):
    code = schema.SCHEMA_MIGRATION


class DbCorruptError(sqlite3.DatabaseError):
    """R2: повреждение базы при маркере state/knowledge.v2 (в записях и статусах - код DB_CORRUPT). Восстанавливает только сервис цикла или владелец."""
    code = schema.DB_CORRUPT


class KnowledgeRestoring(sqlite3.OperationalError):
    """R2: идёт протокол восстановления (runtime/knowledge.restoring), соединение не открыто. Подкласс OperationalError: читатели,
    ловящие sqlite3.Error, обрабатывают его как временную недоступность базы."""
    code = "KNOWLEDGE_RESTORING"


def _check_restoring(root):
    if KM.restoring(root):
        raise KnowledgeRestoring("база знаний восстанавливается (runtime/knowledge.restoring): соединение не открыто")


def db_path(root=None):
    return paths.state(schema.DB_NAME, root=root)


def _pragmas(conn):
    conn.execute("PRAGMA busy_timeout=%d" % BUSY_TIMEOUT_MS)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA journal_size_limit=%d" % JOURNAL_SIZE_LIMIT)


def _version(conn):
    has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
    if not has:
        return 0
    row = conn.execute("SELECT v FROM meta WHERE k=?", (schema.META_VER_KEY,)).fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def migrate(conn, migrations=None):
    """Применяет недостающие миграции по порядку в ОДНОЙ транзакции. Сбой -> ROLLBACK (схема прежняя) и SchemaMigrationError."""
    migrations = schema.MIGRATIONS if migrations is None else migrations
    if _version(conn) >= max([v for v, _ in migrations] or [0]):
        return _version(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as e:
        raise SchemaMigrationError("не удалось начать миграцию: %s" % e)
    try:
        cur = _version(conn)                       # повторная проверка под блокировкой (второй процесс мог успеть)
        for ver, stmts in sorted(migrations, key=lambda m: m[0]):
            if ver <= cur:
                continue
            for s in stmts:
                conn.execute(s)
            conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (schema.META_VER_KEY, str(ver)))
            cur = ver
        conn.execute("COMMIT")
        return cur
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise SchemaMigrationError("миграция схемы не применена: %s: %s" % (e.__class__.__name__, e))


def _is_corrupt(e):
    if isinstance(e, sqlite3.OperationalError):
        m = str(e).lower()
        return "malformed" in m or "not a database" in m
    return isinstance(e, sqlite3.DatabaseError)


def _quarantine(path):
    ts = int(time.time())
    dst = os.path.join(os.path.dirname(path), "knowledge.corrupt-%d.sqlite" % ts)
    n = 0
    while os.path.exists(dst):
        n += 1
        dst = os.path.join(os.path.dirname(path), "knowledge.corrupt-%d-%d.sqlite" % (ts, n))
    os.replace(path, dst)
    for suf in ("-wal", "-shm"):
        try:
            os.remove(path + suf)
        except OSError:
            pass
    return dst


def _connect_rw(path):
    conn = sqlite3.connect(path, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000.0)
    try:
        _pragmas(conn)
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def open_rw(root=None):
    """Соединение писателя; создаёт файл и применяет миграции. Повреждённый файл переименовывается в knowledge.corrupt-<ts>.sqlite, создаётся новая база (LAST_ERROR=DB_CORRUPT)."""
    global LAST_ERROR
    _check_restoring(root)
    if KM.has_v2(root):
        return _open_rw_v2(root)
    path = db_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        return _connect_rw(path)
    except SchemaMigrationError:
        raise
    except sqlite3.DatabaseError as e:
        if not _is_corrupt(e) or not os.path.exists(path):
            raise
    _quarantine(path)
    LAST_ERROR = schema.DB_CORRUPT
    return _connect_rw(path)


def _open_rw_v2(root):
    """Режим v2: нет файла или он пуст -> DbCorruptError (пустая база не создаётся); повреждение по is_corruption -> DbCorruptError; остальное как есть."""
    path = db_path(root)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    if not size:
        raise DbCorruptError("DB_CORRUPT: %s при маркере state/knowledge.v2: пустая база не создаётся, нужен ./tb knowledge restore"
                             % ("файл базы отсутствует" if size is None else "файл базы пуст"))
    try:
        return _connect_rw(path)
    except (SchemaMigrationError, sqlite3.DatabaseError) as e:
        if KM.is_corruption(e):
            raise DbCorruptError("DB_CORRUPT: %s: %s" % (type(e).__name__, e)) from e
        raise


def open_ro(root=None):
    """Соединение читателя (PRAGMA query_only=ON). Нет файла -> None. Маркер восстановления -> KnowledgeRestoring."""
    _check_restoring(root)
    path = db_path(root)
    if not os.path.exists(path):
        return None
    conn = sqlite3.connect(path, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000.0)
    conn.execute("PRAGMA busy_timeout=%d" % BUSY_TIMEOUT_MS)
    conn.execute("PRAGMA query_only=ON")
    return conn


@contextlib.contextmanager
def tx(conn):
    """BEGIN IMMEDIATE / COMMIT; при исключении ROLLBACK и повторный подъём."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    else:
        conn.execute("COMMIT")


def _copy(src_path, dst):
    src = sqlite3.connect(src_path, timeout=BUSY_TIMEOUT_MS / 1000.0)
    try:
        dc = sqlite3.connect(dst)
        try:
            src.backup(dc)
        finally:
            dc.close()
    finally:
        src.close()


def backup(root=None, dst=None, sleep=time.sleep, today=None):
    """Копия базы через Connection.backup (сервис не останавливается): state/backups/knowledge-YYYYMMDD.sqlite. До 3 попыток при PermissionError."""
    src_path = db_path(root)
    if not os.path.exists(src_path):
        raise FileNotFoundError(src_path)
    if dst is None:
        day = today or datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
        dst = paths.state("backups", "knowledge-%s.sqlite" % day, root=root)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp"
    for i in range(BACKUP_TRIES):
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
            _copy(src_path, tmp)
            os.replace(tmp, dst)
            return dst
        except PermissionError:
            if i == BACKUP_TRIES - 1:
                raise
            sleep(0.5 * (i + 1))
    return dst


def sync_dir_warning(root=None):
    """W_LABELS_SYNC_DIR, если путь к state/ в папке облачной синхронизации или сетевой (начинается с \\\\); иначе None."""
    raw = paths.state(root=root)
    for p in (raw, os.path.abspath(raw)):
        if p.startswith("\\\\") or p.startswith("//") or any(m in p.lower() for m in SYNC_MARKERS):
            return schema.W_LABELS_SYNC_DIR
    return None
