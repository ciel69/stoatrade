"""Таблицы цикла обучения и защита базы знаний (R2.9a; Requirement 2, 16.1, 16.4; design.md, «Защита базы знаний», «Сбой базы и восстановление»).

migrate_v2a      таблицы v2a отдельной миграцией (ключ meta.knowledge_v2_ver), только при learnloop.enabled=true или явной команде; маркер state/knowledge.v2
append_row       запись в таблицы цикла: privacy_scan, лимит размера, цепочка rulebook_versions (prev == h последней строки, иначе CHAIN_BROKEN)
insert_rows      пачка строк insert-only таблиц (cluster_runs, rule_evals) и рабочих таблиц
update_row       изменяемые колонки trials / lesson_candidates
log_event        learn_events: результат шагов, ошибки, действия владельца; лимит events_per_day без ошибок -> одна строка EVENTS_THROTTLED
blocklist_put/get  чёрный список условий (текущее состояние - последняя строка по ключу, истёкшая не действует; колонка cond_h хранит ключ записи:
                   block_h семейства условия от tb.learnloop.rules или прежний точный cond_h)
verify / stats   integrity_check, цепочки, индексы и триггеры, размеры; статус для ./tb status (status_check с кешем)
make_backup      проверенная копия state/backups/knowledge-YYYYMMDDTHHMMSS-<reason>.sqlite без перезаписи, ротация по причинам, SRC_CORRUPT
restore          протокол замены файла (маркер, подтверждения писателей, выбор копии, переименование с -wal/-shm, подстановка, meta.copy_ts/restore_ts)
disable_v2       выход из режима v2 (копия, затем удаление маркера) при learnloop.enabled=false
migrate_v2b / apply_v2b, verify_line_chain  (R2.9b) таблицы case_rows/lesson_rows (ключ meta.knowledge_v2b_ver) и их цепочки; verify и check_copy
                 проверяют их (CHAIN_BROKEN - красный), непустая очередь state/knowledge.spool.jsonl - жёлтый SPOOL_PENDING
migrate_re_why   колонки rule_evals.event_why / event_kind (ключ meta.knowledge_re_why_ver, ALTER TABLE ADD COLUMN, идемпотентно; зовёт rule_eval_import)

Соединение то же, что у сервиса меток (labels.store.open_rw: WAL, busy_timeout). Автовосстановление как сервис - задача 10, она вызывает restore(auto=True).
"""
import contextlib
import datetime
import json
import os
import re
import shutil
import sqlite3
import time

from tb import paths
from tb.core.filelock import pid_alive
from tb.knowledge import markers as KM
from tb.knowledge import schema2 as S2
from tb.labels import store as LST
from tb.memory import chain as CH
from tb.memory.privacy import privacy_scan

DbCorruptError = LST.DbCorruptError
KnowledgeRestoring = LST.KnowledgeRestoring
is_corruption = KM.is_corruption
GENESIS = CH.GENESIS
BACKUP_TRIES = LST.BACKUP_TRIES
RE_BACKUP = re.compile(r"^knowledge-(\d{8}T\d{6})-(%s)(?:-(\d+))?\.sqlite$" % "|".join(S2.BACKUP_REASONS))
STATUS_TTL_S = 60.0
_STATUS_CACHE = {}


class KnowledgeError(Exception):
    code = "KNOWLEDGE"

    def __init__(self, code=None, msg="", detail=None):
        self.code = code or self.code
        self.detail = detail
        super().__init__("%s: %s" % (self.code, msg) if msg else self.code)


class KnowledgeMigrationError(KnowledgeError):
    code = S2.KNOWLEDGE_MIGRATION


class ChainBroken(KnowledgeError):
    code = S2.CHAIN_BROKEN


class NotReady(KnowledgeError):
    code = S2.NOT_READY


class RowRejected(KnowledgeError):
    code = "ROW_REJECTED"


class BackupError(KnowledgeError):
    code = "BACKUP"


class RestoreError(KnowledgeError):
    code = "RESTORE"


# ---------------------------------------------------------------- общее

def iso(now=None):
    return datetime.datetime.fromtimestamp(time.time() if now is None else now, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(now):
    return iso(now)[:10]


def db_path(root=None):
    return LST.db_path(root)


def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _meta_get(conn, k):
    if not _has_table(conn, "meta"):
        return None
    row = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return row[0] if row else None


def _meta_set(conn, k, v):
    conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (k, v if isinstance(v, str) else json.dumps(v, separators=(",", ":"))))


def version(conn):
    try:
        return int(_meta_get(conn, S2.META_VER_KEY) or 0)
    except (TypeError, ValueError):
        return 0


def ready(conn):
    """Таблицы v2a на месте (иначе NOT_READY: цикл не стартует)."""
    return version(conn) >= S2.V2A_VER and all(_has_table(conn, t) for t in S2.TABLES)


@contextlib.contextmanager
def _maybe_tx(conn):
    """Своя транзакция BEGIN IMMEDIATE, если вызывающий её не открыл; иначе пишем внутри его транзакции (правило и испытание - одной)."""
    if conn.in_transaction:
        yield conn
        return
    with LST.tx(conn):
        yield conn


def _columns(conn, table):
    return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table).fetchall()]


def _enc(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (dict, list, tuple)):
        return CH.canon(v)
    return v


def _dec(v):
    if isinstance(v, str) and v[:1] in ("{", "["):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _prepare(conn, table, row):
    if table not in S2.TABLES:
        raise RowRejected("BAD_TABLE", table)
    cols = _columns(conn, table)
    bad = [k for k in row if k not in cols]
    if bad:
        raise RowRejected("BAD_COLUMN", "%s: %s" % (table, ",".join(sorted(bad))))
    vals = dict((k, _enc(v)) for k, v in row.items())
    if "ts" in cols and vals.get("ts") is None and table in ("rule_rows", "rulebook_versions", "blocklist_rows", "learn_events"):
        vals["ts"] = iso()
    return vals


def _check_row(table, vals):
    """privacy_scan (Requirement 16.1) и лимит размера строки (правило <= 2 КБ, версия <= 4 КБ; Requirement 2.8)."""
    issues = privacy_scan(dict((k, _dec(v)) for k, v in vals.items() if k not in S2.HASH_FIELDS))
    if issues:
        raise RowRejected("PRIVACY", "; ".join(issues[:3]))
    size = len(CH.canon(vals).encode("utf-8"))
    lim = S2.ROW_MAX.get(table, S2.ROW_MAX_DEFAULT)
    if size > lim:
        raise RowRejected("ROW_TOO_BIG", "%s: %d байт > %d" % (table, size, lim))


def _require_ready(conn):
    if not ready(conn):
        raise NotReady(S2.NOT_READY, "таблиц цикла нет (./tb knowledge migrate-v2a или learnloop.enabled=true)")


# ---------------------------------------------------------------- миграция

def _write_v2_marker(root, now=None):
    if not KM.has_v2(root):
        KM.write_json(KM.v2_path(root), {"ver": S2.V2A_VER, "ts": int(time.time() if now is None else now)})


def migrate_v2a(conn, root=None, enabled=False, explicit=False, backup=True, now=None, sleep=time.sleep):
    """Создать таблицы v2a (одна транзакция) и маркер state/knowledge.v2. Только при enabled (learnloop.enabled=true) или explicit (команда);
    иначе ничего не меняет. Перед миграцией - копия premigrate. Сбой -> ROLLBACK и KnowledgeMigrationError (SchemaMigrationError R1 не затрагивается).
    -> версия knowledge_v2_ver после вызова."""
    if not (enabled or explicit):
        return version(conn)
    cur = version(conn)
    if cur >= S2.V2A_VER:
        _write_v2_marker(root, now)
        return cur
    if backup and os.path.exists(db_path(root)):
        try:
            make_backup(root, "premigrate", now=now, sleep=sleep)
        except (KnowledgeError, OSError, sqlite3.Error) as e:
            raise KnowledgeMigrationError(S2.KNOWLEDGE_MIGRATION, "копия перед миграцией не сделана: %s" % e) from e
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as e:
        raise KnowledgeMigrationError(S2.KNOWLEDGE_MIGRATION, "не удалось начать миграцию: %s" % e) from e
    try:
        cur = version(conn)
        if cur < S2.V2A_VER:
            for s in S2.V2A:
                conn.execute(s)
            _meta_set(conn, S2.META_VER_KEY, str(S2.V2A_VER))
            cur = S2.V2A_VER
        conn.execute("COMMIT")
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise KnowledgeMigrationError(S2.KNOWLEDGE_MIGRATION, "миграция v2a не применена: %s: %s" % (e.__class__.__name__, e)) from e
    _write_v2_marker(root, now)
    return cur


def version_v2b(conn):
    try:
        return int(_meta_get(conn, S2.META_V2B_KEY) or 0)
    except (TypeError, ValueError):
        return 0


def ready_v2b(conn):
    """Таблицы случаев и уроков (v2b) на месте."""
    return version_v2b(conn) >= S2.V2B_VER and all(_has_table(conn, t) for t in S2.MEM_TABLES)


def apply_v2b(conn):
    """DDL v2b внутри уже открытой вызывающим транзакции (перенос случаев делает схему и строки одной транзакцией). -> версия."""
    if version_v2b(conn) >= S2.V2B_VER:
        return version_v2b(conn)
    if not _has_table(conn, "meta"):
        conn.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
    for s in S2.V2B:
        conn.execute(s)
    _meta_set(conn, S2.META_V2B_KEY, str(S2.V2B_VER))
    return S2.V2B_VER


def migrate_v2b(conn, root=None, backup=True, now=None, sleep=time.sleep):
    """Создать таблицы case_rows / lesson_rows (ключ meta.knowledge_v2b_ver) одной транзакцией; копия premigrate перед изменением.
    Сбой -> ROLLBACK и KnowledgeMigrationError. Строки переносит tb.knowledge.migrate2. -> версия v2b."""
    if version_v2b(conn) >= S2.V2B_VER:
        return version_v2b(conn)
    if backup and os.path.exists(db_path(root)):
        try:
            make_backup(root, "premigrate", now=now, sleep=sleep)
        except (KnowledgeError, OSError, sqlite3.Error) as e:
            raise KnowledgeMigrationError(S2.KNOWLEDGE_MIGRATION, "копия перед миграцией v2b не сделана: %s" % e) from e
    try:
        with LST.tx(conn):
            ver = apply_v2b(conn)
    except Exception as e:
        raise KnowledgeMigrationError(S2.KNOWLEDGE_MIGRATION, "миграция v2b не применена: %s: %s" % (e.__class__.__name__, e)) from e
    return ver


def has_re_why(conn):
    """Колонки event_why и event_kind в rule_evals на месте."""
    cols = _columns(conn, "rule_evals")
    return all(c in cols for c, _t in S2.RE_WHY_COLS)


def migrate_re_why(conn):
    """Колонки rule_evals.event_why / event_kind (ALTER TABLE ADD COLUMN недостающих) и meta.knowledge_re_why_ver одной транзакцией.
    Повторный вызов ничего не меняет; нет таблицы rule_evals -> 0 без изменений. Сбой -> ROLLBACK и KnowledgeMigrationError. -> версия."""
    if not _has_table(conn, "rule_evals"):
        return 0
    try:
        cur = int(_meta_get(conn, S2.META_RE_WHY_KEY) or 0)
    except (TypeError, ValueError):
        cur = 0
    if cur >= S2.RE_WHY_VER and has_re_why(conn):
        return cur
    try:
        with LST.tx(conn):
            cols = _columns(conn, "rule_evals")
            for c, t in S2.RE_WHY_COLS:
                if c not in cols:
                    conn.execute("ALTER TABLE rule_evals ADD COLUMN %s %s" % (c, t))
            _meta_set(conn, S2.META_RE_WHY_KEY, str(S2.RE_WHY_VER))
    except Exception as e:
        raise KnowledgeMigrationError(S2.KNOWLEDGE_MIGRATION, "колонки rule_evals.event_why/event_kind не добавлены: %s: %s" % (e.__class__.__name__, e)) from e
    return S2.RE_WHY_VER


# ---------------------------------------------------------------- цепочки case_rows / lesson_rows (строки JSONL)

def line_of(row):
    """Строка таблицы v2b ({seq, body, prev, h, ...}) -> строка прежнего JSONL (dict с seq/prev/h)."""
    d = json.loads(row["body"])
    d["seq"], d["prev"], d["h"] = row["seq"], row["prev"], row["h"]
    return d


def line_ok(row, prev, table):
    """Строка v2b цела: prev = h предыдущей, h = хеш строки, ключ и rev в body совпадают с колонками."""
    try:
        d = line_of(row)
    except (ValueError, TypeError):
        return False
    key = S2.MEM_KEY[table]
    return row["prev"] == prev and row["h"] == CH.make_hash(prev, d) and d.get(key) == row.get(key) and d.get("rev") == row.get("rev")


def mem_rows(conn, table, after_seq=0):
    cur = conn.execute("SELECT * FROM %s WHERE seq > ? ORDER BY seq" % table, (int(after_seq),))
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def verify_line_chain(conn, table, tail_only=False):
    """-> {"ok", "n", "bad_seq"}: цепочка строк case_rows / lesson_rows (tail_only - только последняя строка, для быстрого статуса)."""
    if not _has_table(conn, table):
        return {"ok": True, "n": 0, "bad_seq": None}
    if tail_only:
        cur = conn.execute("SELECT * FROM %s ORDER BY seq DESC LIMIT 1" % table)
        cols = [c[0] for c in cur.description]
        r = cur.fetchone()
        if r is None:
            return {"ok": True, "n": 0, "bad_seq": None}
        r = dict(zip(cols, r))
        return {"ok": line_ok(r, r["prev"], table), "n": None, "bad_seq": None if line_ok(r, r["prev"], table) else r["seq"]}
    prev, last_seq, n = GENESIS, 0, 0
    for r in mem_rows(conn, table):
        if r["seq"] <= last_seq or not line_ok(r, prev, table):
            return {"ok": False, "n": n, "bad_seq": r["seq"]}
        prev, last_seq, n = r["h"], r["seq"], n + 1
    return {"ok": True, "n": n, "bad_seq": None}


def spool_path(root=None):
    return paths.state(S2.SPOOL_NAME, root=root)


def spool_pending(root=None):
    try:
        return os.path.getsize(spool_path(root)) > 0
    except OSError:
        return False


# ---------------------------------------------------------------- цепочка rulebook_versions

def _chain_rows(conn, table):
    cur = conn.execute("SELECT * FROM %s ORDER BY seq" % table)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _row_hash(rec):
    return CH.make_hash(rec.get("prev") or GENESIS, rec)


def verify_chain_rows(rows):
    """-> {"ok", "n", "bad_seq"}: prev каждой строки = h предыдущей (первой - GENESIS), h = sha256(prev + канон. JSON строки без h)."""
    prev = GENESIS
    for i, r in enumerate(rows):
        if r.get("prev") != prev or r.get("h") != _row_hash(r):
            return {"ok": False, "n": i, "bad_seq": r.get("seq")}
        prev = r["h"]
    return {"ok": True, "n": len(rows), "bad_seq": None}


def verify_chain(conn, table="rulebook_versions"):
    if not _has_table(conn, table):
        return {"ok": True, "n": 0, "bad_seq": None}
    return verify_chain_rows(_chain_rows(conn, table))


# ---------------------------------------------------------------- запись

def append_row(conn, table, row):
    """Одна строка в таблицу цикла (в своей транзакции или внутри открытой вызывающим). Для rulebook_versions: seq, prev, h вычисляются здесь, вся
    цепочка проверяется в той же транзакции; разрыв или prev вызывающего != h последней строки -> ChainBroken (CHAIN_BROKEN), запись не делается.
    -> записанная строка (с seq)."""
    _require_ready(conn)
    vals = _prepare(conn, table, row)
    with _maybe_tx(conn):
        if table in S2.CHAIN_TABLES:
            rows = _chain_rows(conn, table)
            chk = verify_chain_rows(rows)
            if not chk["ok"]:
                raise ChainBroken(S2.CHAIN_BROKEN, "%s: цепочка разорвана на seq=%s" % (table, chk["bad_seq"]))
            last_h = rows[-1]["h"] if rows else GENESIS
            if "prev" in row and row["prev"] != last_h:
                raise ChainBroken(S2.CHAIN_BROKEN, "%s: prev записи не равен h последней строки" % table)
            vals.pop("h", None)
            vals["seq"] = (rows[-1]["seq"] if rows else 0) + 1
            vals["prev"] = last_h
            full = dict((c, vals.get(c)) for c in _columns(conn, table) if c != "h")
            vals = dict(full)
            vals["h"] = _row_hash(full)
        _check_row(table, vals)
        ks = sorted(vals)
        cur = conn.execute("INSERT INTO %s(%s) VALUES(%s)" % (table, ", ".join(ks), ", ".join("?" * len(ks))), [vals[k] for k in ks])
        if "seq" not in vals and "seq" in _columns(conn, table):
            vals["seq"] = cur.lastrowid
    return vals


def insert_rows(conn, table, rows, ignore_dup=False):
    """Пачка строк (cluster_runs, rule_evals, lesson_candidates, trials) одной транзакцией; ignore_dup - INSERT OR IGNORE (идемпотентный повтор шага).
    Таблицы с цепочкой сюда не идут. -> число вставленных."""
    if table in S2.CHAIN_TABLES:
        raise RowRejected("BAD_TABLE", "%s пишется только append_row" % table)
    _require_ready(conn)
    rows = list(rows)
    if not rows:
        return 0
    prepared = []
    for r in rows:
        v = _prepare(conn, table, r)
        _check_row(table, v)
        prepared.append(v)
    n0 = conn.total_changes
    with _maybe_tx(conn):
        by_cols = {}
        for v in prepared:
            by_cols.setdefault(tuple(sorted(v)), []).append(v)
        for ks, vs in by_cols.items():
            conn.executemany("INSERT %sINTO %s(%s) VALUES(%s)" % ("OR IGNORE " if ignore_dup else "", table, ", ".join(ks), ", ".join("?" * len(ks))),
                             [[v[k] for k in ks] for v in vs])
    return conn.total_changes - n0


def update_row(conn, table, key, fields):
    """Изменяемые колонки trials (state, verdict, numbers, cut_ts, closed_ts) и lesson_candidates (status, code, ...). -> число изменённых строк."""
    if table not in S2.MUTABLE:
        raise RowRejected("IMMUTABLE", table)
    _require_ready(conn)
    kcol, allowed = S2.MUTABLE[table]
    bad = [k for k in fields if k not in allowed]
    if bad or not fields:
        raise RowRejected("IMMUTABLE", "%s: %s" % (table, ",".join(sorted(bad)) or "нет полей"))
    vals = dict((k, _enc(v)) for k, v in fields.items())
    _check_row(table, vals)
    ks = sorted(vals)
    with _maybe_tx(conn):
        cur = conn.execute("UPDATE %s SET %s WHERE %s=?" % (table, ", ".join("%s=?" % k for k in ks), kcol), [vals[k] for k in ks] + [key])
    return cur.rowcount


# ---------------------------------------------------------------- learn_events

def _events_day(conn):
    d = _dec(_meta_get(conn, S2.META_EVENTS_DAY))
    return d if isinstance(d, dict) else {}


def _throttled_row(conn, day, skipped, now):
    ks = ("ts", "step", "code", "actor", "ref", "body")
    vals = (iso(now), "events", S2.EVENTS_THROTTLED, "code", day, CH.canon({"day": day, "skipped": int(skipped)}))
    cur = conn.execute("INSERT INTO learn_events(%s) VALUES(?,?,?,?,?,?)" % ", ".join(ks), vals)
    return cur.lastrowid


def flush_events(conn, now=None, force=False):
    """Строка EVENTS_THROTTLED с числом пропущенных за прошлые сутки (или за текущие при force: счётчик пропусков обнуляется). -> seq или None."""
    now = time.time() if now is None else now
    _require_ready(conn)
    with _maybe_tx(conn):
        d = _events_day(conn)
        if not d.get("skipped") or (d.get("day") == _day(now) and not force):
            return None
        seq = _throttled_row(conn, d.get("day"), d["skipped"], now)
        d["skipped"] = 0
        _meta_set(conn, S2.META_EVENTS_DAY, d)
        return seq


def log_event(conn, step, code=None, actor="code", ref=None, body=None, error=False, now=None, per_day=S2.EVENTS_PER_DAY):
    """Строка learn_events (результат шага при изменении состояния, ошибка, действие владельца, сводка суток). Строк без ошибок за сутки UTC не больше
    per_day: лишние не пишутся, их число уходит одной строкой EVENTS_THROTTLED при смене суток (или flush_events). Ошибки и OWNER не ограничиваются.
    -> seq или None (пропущено)."""
    now = time.time() if now is None else now
    if actor not in S2.ACTORS:
        raise RowRejected("BAD_ACTOR", str(actor))
    _require_ready(conn)
    is_err = bool(error) or actor == S2.OWNER or code in S2.ERROR_CODES
    day = _day(now)
    vals = _prepare(conn, "learn_events", {"ts": iso(now), "step": step, "code": code, "actor": actor, "ref": ref, "body": body})
    _check_row("learn_events", vals)
    with _maybe_tx(conn):
        d = _events_day(conn)
        if d.get("day") != day:
            if d.get("skipped"):
                _throttled_row(conn, d.get("day"), d["skipped"], now)
            d = {"day": day, "n": 0, "skipped": 0}
        if not is_err and d["n"] >= int(per_day):
            d["skipped"] = int(d.get("skipped") or 0) + 1
            _meta_set(conn, S2.META_EVENTS_DAY, d)
            return None
        ks = sorted(vals)
        cur = conn.execute("INSERT INTO learn_events(%s) VALUES(%s)" % (", ".join(ks), ", ".join("?" * len(ks))), [vals[k] for k in ks])
        if not is_err:
            d["n"] = int(d.get("n") or 0) + 1
        _meta_set(conn, S2.META_EVENTS_DAY, d)
        return cur.lastrowid


def checkpoint(conn, mode="PASSIVE"):
    """wal_checkpoint после прохода цикла (PASSIVE), раз в сутки TRUNCATE (Requirement 2.9)."""
    if mode not in ("PASSIVE", "TRUNCATE", "FULL", "RESTART"):
        raise ValueError(mode)
    return conn.execute("PRAGMA wal_checkpoint(%s)" % mode).fetchone()


# ---------------------------------------------------------------- чёрный список

def blocklist_put(conn, cond_h, code, until_ts, actor, now=None):
    """Новая строка чёрного списка. until_ts=None - бессрочно (снимает владелец новой строкой UNBLOCK или с until_ts <= now).
    Копию после записи владельца делает вызывающий (make_backup(root, "owner")) после фиксации транзакции. -> строка."""
    now = time.time() if now is None else now
    if not isinstance(cond_h, str) or not cond_h:
        raise RowRejected("BAD_COND_H", repr(cond_h))
    if code not in S2.BLOCK_CODES and code != S2.UNBLOCK:
        raise RowRejected("BAD_CODE", str(code))
    if actor not in S2.ACTORS:
        raise RowRejected("BAD_ACTOR", str(actor))
    if until_ts is not None and (isinstance(until_ts, bool) or not isinstance(until_ts, (int, float))):
        raise RowRejected("BAD_UNTIL", repr(until_ts))
    return append_row(conn, "blocklist_rows", {"cond_h": cond_h, "code": code, "until_ts": None if until_ts is None else int(until_ts),
                                                "actor": actor, "ts": iso(now)})


def blocklist_get(conn, cond_h, now=None):
    """Текущее состояние условия: последняя строка по cond_h. -> {"code", "until_ts"} или None (нет записи, истекла, снята UNBLOCK)."""
    now = time.time() if now is None else now
    if not _has_table(conn, "blocklist_rows"):
        return None
    row = conn.execute("SELECT code, until_ts FROM blocklist_rows WHERE cond_h=? ORDER BY seq DESC LIMIT 1", (cond_h,)).fetchone()
    if row is None or row[0] == S2.UNBLOCK:
        return None
    if row[1] is not None and row[1] <= now:
        return None
    return {"code": row[0], "until_ts": row[1]}


# ---------------------------------------------------------------- проверка и статус

def _integrity(conn, quick=False):
    return [r[0] for r in conn.execute("PRAGMA %s" % ("quick_check" if quick else "integrity_check")).fetchall()]


def check_integrity(conn, quick=False):
    """Суточный integrity_check: провал -> DbCorruptError (Requirement 2.4: провал проверки считается повреждением)."""
    res = _integrity(conn, quick)
    if res != ["ok"]:
        raise DbCorruptError("DB_CORRUPT: integrity_check: %s" % "; ".join(res[:3]))
    return True


def _schema_missing(conn):
    names = set(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('index', 'trigger')"))
    return {"indexes": [i for i in S2.INDEXES if i not in names], "triggers": [t for t in S2.TRIGGERS if t not in names]}


def _red(codes):
    return any(c in (S2.DB_CORRUPT, S2.CHAIN_BROKEN, S2.RESTORING, S2.RESTORE_FAILED, S2.SCHEMA_MISMATCH) for c in codes)


def verify(root=None, quick=False, counts=True, now=None):
    """Проверка базы знаний (./tb knowledge verify, ./tb status): маркеры, integrity_check (quick_check при quick), цепочки, индексы и триггеры v2a,
    размеры таблиц. -> {"ok", "color", "codes", "reasons", ...}. Красный: DB_CORRUPT, CHAIN_BROKEN, RESTORING, RESTORE_FAILED, SCHEMA_MISMATCH;
    жёлтый: NOT_READY (маркер v2 без таблиц; store=sqlite без файла или таблиц v2b), AUTO_RESTORED (7 суток после автовосстановления),
    SPOOL_PENDING, JSONL_APPEARED (store=sqlite, а cases.jsonl / lessons.jsonl есть в state/), STORE_IN_CONFIG (store корня проекта взят из
    config/memory.json без маркера state/memory.store). rep["store"] - активное хранилище случаев и уроков, rep["store_src"] - его источник
    (marker | config-compat | default)."""
    now = time.time() if now is None else now
    path = db_path(root)
    rep = {"ok": True, "color": "green", "codes": [], "reasons": [], "v2": KM.has_v2(root), "db": os.path.exists(path), "restoring": KM.restoring(root),
           "ver": 0, "ready": False, "v2b": False, "integrity": None, "chains": {}, "schema": None, "tables": {}, "size_mb": None, "restore": None}

    def add(code, why):
        if code not in rep["codes"]:
            rep["codes"].append(code)
        rep["reasons"].append(why)

    if rep["db"]:
        rep["size_mb"] = round(os.path.getsize(path) / 1048576.0, 2)
    if spool_pending(root):
        add(S2.SPOOL_PENDING, "база знаний: в очереди state/%s есть записи случаев/уроков: ./tb knowledge replay-spool" % S2.SPOOL_NAME)
    from tb.knowledge import memstore                     # ленивый импорт: memstore импортирует этот модуль
    rep["store"], rep["store_src"] = memstore.mode_info(root)
    if rep["store_src"] == "config-compat":
        add(S2.STORE_IN_CONFIG, "store=%s взят из config/memory.json (STORE_IN_CONFIG, переходное состояние): выполните ./tb knowledge store-marker"
            % rep["store"])
    if rep["store"] == "sqlite":                          # R2.9b: после переключения JSONL не открываются и не должны появляться
        extra = [n for n in (S2.MEM_FILE[t] for t in S2.MEM_TABLES) if os.path.exists(paths.state(n, root=root))]
        if extra:
            add(S2.JSONL_APPEARED, "store=sqlite, но в state/ есть %s (JSONL_APPEARED): кто-то пишет в обход фасада; см. RUNBOOK, раздел R2.9b"
                % ", ".join(extra))
    failed = KM.read_json(KM.restore_failed_path(root))
    if isinstance(failed, dict):
        add(S2.RESTORE_FAILED, "база знаний: восстановление прервано (%s), нужен ./tb knowledge restore" % failed.get("code"))
    sig = KM.read_corrupt(root)
    if sig is not None:
        add(S2.DB_CORRUPT, "база знаний повреждена (DB_CORRUPT, сигнал %s): записи меток и цикла остановлены, правила не применяются" % (sig.get("where") or "-"))
    if rep["restoring"]:
        add(S2.RESTORING, "база знаний восстанавливается (runtime/knowledge.restoring): записи на паузе")
        return _finish(rep)
    if not rep["db"]:
        if rep["v2"]:
            add(S2.DB_CORRUPT, "база знаний: файла нет при маркере state/knowledge.v2 (пустая база не создаётся): ./tb knowledge restore")
        elif rep["store"] == "sqlite":
            add(S2.NOT_READY, "store=sqlite, но файла базы знаний нет (NOT_READY): записи случаев и уроков идут в очередь")
        return _finish(rep)
    try:
        conn = LST.open_ro(root)
    except sqlite3.Error as e:
        if isinstance(e, KnowledgeRestoring):
            add(S2.RESTORING, "база знаний восстанавливается (runtime/knowledge.restoring): записи на паузе")
        else:
            add(S2.DB_CORRUPT if is_corruption(e) else "DB_ERROR", "база знаний не открывается (%s): %s" % ("повреждена, DB_CORRUPT" if is_corruption(e) else "ошибка", e))
        return _finish(rep)
    if conn is None:
        return _finish(rep)
    try:
        try:
            res = _integrity(conn, quick)
            rep["integrity"] = "ok" if res == ["ok"] else res[:5]
            if res != ["ok"]:
                add(S2.DB_CORRUPT, "база знаний повреждена (DB_CORRUPT): %s не пройден: ./tb knowledge restore" % ("quick_check" if quick else "integrity_check"))
            rep["ver"] = version(conn)
            rep["ready"] = ready(conn)
            if rep["ready"]:
                rep["chains"]["rulebook_versions"] = ch = verify_chain(conn)
                if not ch["ok"]:
                    add(S2.CHAIN_BROKEN, "база знаний: цепочка rulebook_versions разорвана на seq=%s (CHAIN_BROKEN): запись версий отказывается" % ch["bad_seq"])
                rep["schema"] = miss = _schema_missing(conn)
                if miss["indexes"] or miss["triggers"]:
                    add(S2.SCHEMA_MISMATCH, "база знаний: нет индексов/триггеров v2a: %s" % ",".join(miss["indexes"] + miss["triggers"]))
            elif rep["v2"]:
                add(S2.NOT_READY, "база знаний: маркер v2 есть, таблиц цикла нет (NOT_READY): цикл не стартует")
            rep["v2b"] = ready_v2b(conn)
            if rep.get("store") == "sqlite" and not rep["v2b"]:
                add(S2.NOT_READY, "store=sqlite, но таблиц case_rows/lesson_rows нет (NOT_READY): записи случаев и уроков идут в очередь")
            if rep["v2b"]:                              # вторая итерация: цепочки случаев и уроков (быстрый статус - только хвост)
                for t in S2.MEM_TABLES:
                    rep["chains"][t] = ch = verify_line_chain(conn, t, tail_only=quick)
                    if not ch["ok"]:
                        add(S2.CHAIN_BROKEN, "база знаний: цепочка %s разорвана на seq=%s (CHAIN_BROKEN): записи идут в очередь, восстановление - "
                                             "./tb knowledge restore и replay-spool" % (t, ch["bad_seq"]))
                names = set(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('index', 'trigger')"))
                miss = [x for x in S2.V2B_INDEXES + S2.V2B_TRIGGERS if x not in names]
                if miss:
                    add(S2.SCHEMA_MISMATCH, "база знаний: нет индексов/триггеров v2b: %s" % ",".join(miss))
            if counts:
                for t in ("signals", "outcomes", "policy_runs") + S2.TABLES + S2.MEM_TABLES:
                    if _has_table(conn, t):
                        rep["tables"][t] = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            rs = {k: _meta_get(conn, k) for k in (S2.META_COPY_TS, S2.META_RESTORE_TS, S2.META_RESTORE_MODE, S2.META_RESTORE_SRC)}
            if rs.get(S2.META_RESTORE_TS):
                rep["restore"] = rs
                try:
                    age = now - float(rs[S2.META_RESTORE_TS])
                except (TypeError, ValueError):
                    age = None
                if rs.get(S2.META_RESTORE_MODE) == "auto" and age is not None and 0 <= age < S2.AUTO_RESTORED_YELLOW_S:
                    add(S2.AUTO_RESTORED, "база знаний автоматически восстановлена из копии %s (AUTO_RESTORED)" % (rs.get(S2.META_RESTORE_SRC) or "-"))
        except sqlite3.DatabaseError as e:
            bad = is_corruption(e)
            add(S2.DB_CORRUPT if bad else "DB_ERROR", "база знаний %s: %s: %s%s" % ("повреждена (DB_CORRUPT)" if bad else "не читается", type(e).__name__, e,
                                                                                  ": ./tb knowledge restore" if bad else ""))
    finally:
        conn.close()
    return _finish(rep)


def _finish(rep):
    rep["color"] = "red" if _red(rep["codes"]) else ("yellow" if rep["codes"] else "green")
    rep["ok"] = not rep["codes"]
    return rep


def status_check(root=None, now=None, ttl_s=STATUS_TTL_S):
    """Для ./tb status и дашборда: без маркеров v2/восстановления -> None (поведение R1); иначе verify(quick, без счётчиков) не чаще ttl_s
    (маркеры и сигналы проверяются при каждом вызове)."""
    now = time.time() if now is None else now
    if not (KM.has_v2(root) or KM.restoring(root) or os.path.exists(KM.restore_failed_path(root))):
        return None
    key = os.path.abspath(paths.state(root=root))
    cheap = (KM.has_v2(root), KM.restoring(root), os.path.exists(KM.corrupt_path(root)), os.path.exists(KM.restore_failed_path(root)), os.path.exists(db_path(root)),
             os.path.exists(paths.state(S2.STORE_MARKER, root=root)))
    hit = _STATUS_CACHE.get(key)
    if hit and hit[1] == cheap and 0 <= now - hit[0] < ttl_s:
        return hit[2]
    rep = verify(root, quick=True, counts=False, now=now)
    _STATUS_CACHE[key] = (now, cheap, rep)
    return rep


def stats(root=None):
    """./tb knowledge stats: проверка, строки таблиц, байты таблиц (dbstat, если собран), копии."""
    rep = verify(root, quick=True, counts=True)
    rep["bytes"] = None
    if rep["db"] and not rep["restoring"]:
        try:
            conn = LST.open_ro(root)
        except sqlite3.Error:
            conn = None
        if conn is not None:
            try:
                rep["bytes"] = dict(conn.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name").fetchall())
            except sqlite3.Error:
                rep["bytes"] = None
            finally:
                conn.close()
    rep["backups"] = [dict(b, path=os.path.basename(b["path"])) for b in list_backups(root)]
    return rep


# ---------------------------------------------------------------- копии

def backups_dir(root=None):
    return paths.state("backups", root=root)


def list_backups(root=None):
    """Проверенные копии v2 (только они получают имя knowledge-<время>-<причина>.sqlite) по возрастанию времени. Копии R1 (knowledge-YYYYMMDD) не входят."""
    d = backups_dir(root)
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for n in names:
        m = RE_BACKUP.match(n)
        if m:
            ts = datetime.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=datetime.timezone.utc).timestamp()
            out.append({"path": os.path.join(d, n), "stamp": m.group(1), "reason": m.group(2), "n": int(m.group(3) or 0), "ts": int(ts)})
    # premigrate снята ДО миграции (без таблиц цикла): при равном времени она старше любой другой копии, иначе restore выбрал бы её
    out.sort(key=lambda b: (b["stamp"], 0 if b["reason"] == "premigrate" else 1, b["n"], b["reason"]))
    return out


def _copy_db(src_path, dst):
    src = sqlite3.connect(src_path, timeout=LST.BUSY_TIMEOUT_MS / 1000.0)
    try:
        dc = sqlite3.connect(dst, isolation_level=None)
        try:
            src.backup(dc)
            dc.execute("PRAGMA journal_mode=DELETE")       # копия без -wal/-shm: её можно подставить целиком
        finally:
            dc.close()
    finally:
        src.close()


def check_copy(path):
    """Проверка файла копии: integrity_check и цепочки. -> (ok, code, detail); code: NO_FILE | DB_CORRUPT | CHAIN_BROKEN."""
    if not os.path.isfile(path):
        return False, "NO_FILE", path
    try:
        c = sqlite3.connect(path, isolation_level=None)
    except sqlite3.Error as e:
        return False, S2.DB_CORRUPT, str(e)
    try:
        c.execute("PRAGMA query_only=ON")
        res = _integrity(c)
        if res != ["ok"]:
            return False, S2.DB_CORRUPT, res[:5]
        ch = verify_chain(c)
        if not ch["ok"]:
            return False, S2.CHAIN_BROKEN, ch
        for t in S2.MEM_TABLES:
            ch = verify_line_chain(c, t)
            if not ch["ok"]:
                return False, S2.CHAIN_BROKEN, dict(ch, table=t)
        return True, None, {"ver": version(c), "labels_ver": _meta_get(c, "labels_schema_ver"), "v2b": version_v2b(c)}
    except sqlite3.DatabaseError as e:
        return False, S2.DB_CORRUPT, "%s: %s" % (type(e).__name__, e)
    finally:
        c.close()


def _rename(src, dst):
    """Переименование без перезаписи (на Windows os.rename отказывает при существующем dst). Отдельная функция - точка подмены в тестах."""
    os.rename(src, dst)


def _replace(src, dst):
    os.replace(src, dst)


def _retry(fn, tries=BACKUP_TRIES, sleep=time.sleep, what="операция"):
    """Повторы с паузой 0.5 x номер при PermissionError (как labels.store.backup); исчерпание -> RestoreError(RETRIES)."""
    for i in range(tries):
        try:
            return fn()
        except PermissionError as e:
            if i == tries - 1:
                raise RestoreError("RETRIES", "%s: повторы исчерпаны (%s)" % (what, e)) from e
            sleep(0.5 * (i + 1))
    return None


def _unique_dst(d, stamp, reason):
    dst = os.path.join(d, "knowledge-%s-%s.sqlite" % (stamp, reason))
    n = 1
    while os.path.exists(dst) or os.path.exists(dst + ".tmp"):
        n += 1
        dst = os.path.join(d, "knowledge-%s-%s-%d.sqlite" % (stamp, reason, n))
    return dst


def rotate(root=None):
    """Хранить 7 последних daily и по 3 последних каждой другой причины. -> удалённые пути."""
    gone = []
    by = {}
    for b in list_backups(root):
        by.setdefault(b["reason"], []).append(b)
    for reason, bs in by.items():
        keep = S2.BACKUP_KEEP.get(reason, 3)
        for b in bs[:-keep] if len(bs) > keep else []:
            for p in (b["path"], b["path"] + "-wal", b["path"] + "-shm"):
                if KM.remove(p) and p == b["path"]:
                    gone.append(p)
    return gone


def make_backup(root=None, reason="owner", now=None, sleep=time.sleep, tries=BACKUP_TRIES):
    """Проверенная копия базы: state/backups/knowledge-YYYYMMDDTHHMMSS-<reason>.sqlite. Копия пишется во временный файл, integrity_check и цепочки - до
    переименования; непроверенная копия удаляется и не вытесняет проверенные; существующий файл не затирается; повреждённый источник -> BackupError(SRC_CORRUPT).
    При маркере восстановления -> BackupError(RESTORING). -> путь копии."""
    if reason not in S2.BACKUP_REASONS:
        raise BackupError("BAD_REASON", str(reason))
    if KM.restoring(root):
        raise BackupError(S2.RESTORING, "идёт восстановление базы (runtime/knowledge.restoring)")
    src = db_path(root)
    if not os.path.exists(src):
        raise FileNotFoundError(src)
    d = backups_dir(root)
    os.makedirs(d, exist_ok=True)
    stamp = datetime.datetime.fromtimestamp(time.time() if now is None else now, datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")
    dst = _unique_dst(d, stamp, reason)
    tmp = dst + ".tmp"
    try:
        for i in range(tries):
            try:
                KM.remove(tmp)
                _copy_db(src, tmp)
                break
            except PermissionError:
                if i == tries - 1:
                    raise
                sleep(0.5 * (i + 1))
    except sqlite3.DatabaseError as e:
        KM.remove(tmp)
        if is_corruption(e):
            raise BackupError(S2.SRC_CORRUPT, "источник повреждён: %s" % e) from e
        raise
    except BaseException:
        KM.remove(tmp)
        raise
    ok, code, detail = check_copy(tmp)
    if not ok:
        for p in (tmp, tmp + "-wal", tmp + "-shm", tmp + "-journal"):
            KM.remove(p)
        raise BackupError(S2.SRC_CORRUPT if code == S2.DB_CORRUPT else code, "копия не прошла проверку: %s" % (detail,), detail)
    if os.path.exists(dst):                               # между выбором имени и переименованием появился файл: не затираем
        dst = _unique_dst(d, stamp, reason)
    try:
        _retry(lambda: _rename(tmp, dst), tries, sleep, "переименование копии")
    except RestoreError as e:
        KM.remove(tmp)
        raise BackupError("RETRIES", str(e)) from e
    rotate(root)
    return dst


# ---------------------------------------------------------------- восстановление

def _open_internal(path):
    """Внутреннее открытие исполнителя протокола в обход проверки маркера восстановления (запись meta.copy_ts / restore_ts)."""
    conn = sqlite3.connect(path, isolation_level=None, timeout=LST.BUSY_TIMEOUT_MS / 1000.0)
    try:
        LST._pragmas(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _copy_time(path):
    m = RE_BACKUP.match(os.path.basename(path))
    if m:
        return int(datetime.datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=datetime.timezone.utc).timestamp())
    return int(os.path.getmtime(path))


def _move_damaged(target, now, tries, sleep):
    """Переименовать повреждённый файл вместе с -wal и -shm (не удаляя) в knowledge.corrupt-<ts>.sqlite[-wal|-shm]. -> список новых путей."""
    d = os.path.dirname(target)
    base = os.path.join(d, "knowledge.corrupt-%d.sqlite" % int(now))
    n = 0
    while any(os.path.exists(base + s) for s in ("", "-wal", "-shm")):
        n += 1
        base = os.path.join(d, "knowledge.corrupt-%d-%d.sqlite" % (int(now), n))
    moved = []
    for suf in ("", "-wal", "-shm"):
        if os.path.exists(target + suf):
            _retry(lambda s=suf: _rename(target + s, base + s), tries, sleep, "переименование %s" % os.path.basename(target + suf))
            moved.append(base + suf)
    return moved


def _wal_left(target):
    return [s for s in ("-wal", "-shm") if os.path.exists(target + s)]


def _abort(root, mode, code, detail, now):
    KM.write_json(KM.restore_failed_path(root), {"code": code, "mode": mode, "ts": int(now), "detail": str(detail)[:300]})
    KM.remove(KM.restoring_path(root))
    KM.clear_acks(root)


def restore(root=None, src=None, auto=False, ack_timeout_s=S2.RESTORE_ACK_TIMEOUT_S, close=None, clock=time.time, sleep=time.sleep, tries=BACKUP_TRIES,
            poll_s=0.2, now=None, pid=None):
    """Протокол замены файла базы (Requirement 2.11; ./tb knowledge restore и автовосстановление задачи 10):
    (1) маркер runtime/knowledge.restoring (прежние подтверждения стираются ДО маркера); close() - вызывающий закрывает своё соединение;
    (2) ожидание подтверждений runtime/knowledge.restoring.ack/<pid> от всех живых зарегистрированных писателей не дольше ack_timeout_s;
    (3) копия: src (проверка integrity_check и цепочек) или самая свежая проверенная из state/backups;
    (4) повреждённый файл вместе с -wal/-shm переименовывается (повторы при PermissionError); -wal/-shm у целевого пути не должно остаться;
    (5) подстановка копии (повторы), meta.copy_ts / restore_ts внутренним открытием, строка learn_events; снятие маркера и сигнала повреждения.
    Таймаут, исчерпание повторов, оставшиеся -wal/-shm, плохая копия -> RestoreError, маркер снимается, runtime/knowledge.restore_failed.json (красный статус).
    -> отчёт."""
    mode = "auto" if auto else "manual"
    pid = os.getpid() if pid is None else int(pid)
    t_start = clock() if now is None else now
    mp = KM.restoring_path(root)
    cur = KM.read_json(mp) if os.path.exists(mp) else None
    if isinstance(cur, dict) and int(cur.get("pid") or 0) not in (0, pid) and pid_alive(int(cur.get("pid") or 0)):
        raise RestoreError("RESTORE_BUSY", "восстановление уже идёт (pid %s)" % cur.get("pid"))
    KM.clear_acks(root)
    KM.write_json(mp, {"pid": pid, "ts": int(t_start), "mode": mode})
    target = db_path(root)
    rep = {"ok": False, "mode": mode, "src": None, "copy_ts": None, "restore_ts": None, "moved": [], "waited": []}
    try:
        if close is not None:
            close()
        t_end = clock() + float(ack_timeout_s)
        while True:
            need = [w for w in KM.writers(root) if w != pid]
            missing = [w for w in need if w not in KM.acks(root)]
            rep["waited"] = need
            if not missing:
                break
            if clock() >= t_end:
                raise RestoreError("ACK_TIMEOUT", "нет подтверждения закрытия соединения от pid %s за %s с" % (",".join(map(str, missing)), ack_timeout_s))
            sleep(poll_s)
        if src:
            ok, code, detail = check_copy(src)
            if not ok:
                prev = [os.path.basename(b["path"]) for b in reversed(list_backups(root)) if os.path.abspath(b["path"]) != os.path.abspath(src)][:1]
                raise RestoreError("COPY_BAD", "копия %s не прошла проверку (%s)%s" % (os.path.basename(src), code,
                                                                                    ("; попробуйте предыдущую: %s" % prev[0]) if prev else ""), detail)
            chosen = src
        else:
            chosen = None
            for b in reversed(list_backups(root)):
                if check_copy(b["path"])[0]:
                    chosen = b["path"]
                    break
            if chosen is None:
                raise RestoreError("NO_COPY", "нет проверенной копии в state/backups")
        rep["src"] = os.path.basename(chosen)
        copy_ts = _copy_time(chosen)
        rep["moved"] = _move_damaged(target, clock(), tries, sleep)
        left = _wal_left(target)
        if left:
            raise RestoreError("WAL_LEFT", "у целевого пути остались %s старой базы: подстановка не выполняется" % ",".join(left))
        tmp = target + ".restore.tmp"
        KM.remove(tmp)
        _retry(lambda: shutil.copyfile(chosen, tmp), tries, sleep, "копирование копии")
        _retry(lambda: _replace(tmp, target), tries, sleep, "подстановка копии")
        left = _wal_left(target)
        if left:
            raise RestoreError("WAL_LEFT", "у целевого пути появились %s: база не открывается" % ",".join(left))
        restore_ts = int(clock())
        c = _open_internal(target)
        try:
            with LST.tx(c):
                _meta_set(c, S2.META_COPY_TS, str(int(copy_ts)))
                _meta_set(c, S2.META_RESTORE_TS, str(restore_ts))
                _meta_set(c, S2.META_RESTORE_MODE, mode)
                _meta_set(c, S2.META_RESTORE_SRC, rep["src"])
                if ready(c):
                    log_event(c, "knowledge_restore", S2.AUTO_RESTORED if auto else S2.OWNER, "code" if auto else S2.OWNER, ref=rep["src"],
                              body={"copy_ts": int(copy_ts), "restore_ts": restore_ts, "mode": mode}, error=True, now=restore_ts)
        finally:
            c.close()
        rep.update(ok=True, copy_ts=int(copy_ts), restore_ts=restore_ts)
    except RestoreError as e:
        _abort(root, mode, e.code, e, clock())
        raise
    except Exception as e:
        _abort(root, mode, "RESTORE_ERROR", "%s: %s" % (type(e).__name__, e), clock())
        raise RestoreError("RESTORE_ERROR", "%s: %s" % (type(e).__name__, e)) from e
    KM.remove(mp)
    KM.remove(KM.corrupt_path(root))
    KM.remove(KM.restore_failed_path(root))
    KM.clear_acks(root)
    _STATUS_CACHE.clear()
    return rep


def recover_stale_marker(root=None, **kw):
    """Старт сервиса цикла (задача 10): оставшийся маркер восстановления мёртвого процесса. База цела и сигнала повреждения нет -> маркер снимается
    (протокол завершён); иначе протокол повторяется restore(auto=True). -> None (маркера нет) | "completed" | отчёт restore."""
    mp = KM.restoring_path(root)
    cur = KM.read_json(mp) if os.path.exists(mp) else None
    if not os.path.exists(mp):
        return None
    owner = int(cur.get("pid") or 0) if isinstance(cur, dict) else 0
    if owner and owner != os.getpid() and pid_alive(owner):
        return None
    target = db_path(root)
    intact = os.path.exists(target) and KM.read_corrupt(root) is None and check_copy(target)[0]
    if intact:
        KM.remove(mp)
        KM.clear_acks(root)
        return "completed"
    return restore(root, auto=True, **kw)


def disable_v2(root=None, enabled=False, now=None, sleep=time.sleep):
    """./tb knowledge disable-v2: только при learnloop.enabled=false; копия owner (если база есть), затем удаление маркера state/knowledge.v2.
    Таблицы не удаляются. -> {"ok", "backup"}."""
    if enabled:
        raise KnowledgeError("LEARNLOOP_ENABLED", "сначала выключите цикл (learnloop.enabled=false)")
    if not KM.has_v2(root):
        return {"ok": True, "backup": None, "note": "маркера v2 нет"}
    dst = make_backup(root, "owner", now=now, sleep=sleep) if os.path.exists(db_path(root)) else None
    KM.remove(KM.v2_path(root))
    _STATUS_CACHE.clear()
    return {"ok": True, "backup": dst}
