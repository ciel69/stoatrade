"""Хранилище случаев и уроков в базе знаний (R2.9b, Requirement 3.4, 3.6, 3.7, 3.8): бэкенд фасада memory.cases / memory.lessons.

mode(root)        значение store из memory.json (кеш по mtime и размеру файла; читается при каждой записи и каждом чтении); "jsonl" по умолчанию,
                  битый файл -> последнее известное значение
snapshot          строки таблицы case_rows / lesson_rows в формате прежнего JSONL (dict с seq/prev/h) по порядку seq; дочитывает только новые строки.
                  База недоступна -> строки последней проверенной копии state/backups; поверх - строки очереди, которых нет в источнике
append            запись строки под блокировкой таблицы (её держит вызывающий: cases.lock / lessons.lock): seq/prev/h считаются в транзакции,
                  хвост цепочки проверяется. База недоступна (нет файла, нет таблиц v2b, восстановление, повреждение, занята, CHAIN_BROKEN) ->
                  строка уходит в очередь state/knowledge.spool.jsonl (формат строки - колонки таблицы и поле table)
replay            ./tb knowledge replay-spool: очередь в базу идемпотентно по (ключ, rev): та же строка уже есть -> пропуск; update случая с занятым
                  rev -> rev = последний + 1; прочие конфликты остаются в очереди. Писатель сам применяет очередь своей таблицы перед записью
verify_chain      цепочка строк таблицы (cases.verify в режиме sqlite)

JSONL-файлы здесь не открываются. Модуль не импортирует tb.memory.cases / tb.memory.lessons (они импортируют его лениво).
Тестовые хуки: _FORCE (режим вместо memory.json), _AUTOCREATE (при отсутствии базы или таблиц v2b создать их; в работе выключен:
пустая база вместо потерянной не создаётся).
"""
import calendar
import contextlib
import json
import os
import sqlite3
import threading
import time

from tb import paths
from tb.core.filelock import FileLock, atomic_write
from tb.knowledge import schema2 as S2
from tb.knowledge import store2
from tb.labels import store as LST
from tb.memory import chain as CH

LOCK_WAIT_S = CH.LOCK_WAIT_S
GENESIS = CH.GENESIS
_FORCE = None
_AUTOCREATE = False
_MODE = {}
_SNAP = {}
_LOCK = threading.RLock()


class Unavailable(Exception):
    """База для случаев и уроков недоступна (код в args[0])."""

    def __init__(self, code, msg=""):
        Exception.__init__(self, "%s: %s" % (code, msg) if msg else code)
        self.code = code


# ---------------------------------------------------------------- режим

def _cfg_path(root):
    p = paths.config("memory.json", root=root)
    if root and not os.path.isfile(p):
        return paths.config("memory.json", root=paths.DEFAULT_ROOT)
    return p


def mode(root=None):
    """-> "jsonl" | "sqlite". Тот же файл, что берёт memory.cfg.load (memory.json корня, иначе файл проекта).
    Тестовый _FORCE действует только для явно заданного временного корня (не для корня проекта и не для root=None)."""
    if _FORCE and root and os.path.abspath(root) not in (os.path.abspath(paths.DEFAULT_ROOT), paths.root()):
        return _FORCE
    p = os.path.abspath(_cfg_path(root))
    hit = _MODE.get(p)
    try:
        st = os.stat(p)
    except OSError:
        return hit[1] if hit else "jsonl"
    sig = (st.st_mtime_ns, st.st_size)
    if hit and hit[0] == sig:
        return hit[1]
    try:
        with open(p, encoding="utf-8") as f:
            v = json.load(f).get("store", "jsonl")
    except (OSError, ValueError, AttributeError):
        v = None
    if v not in S2.STORES:
        v = hit[1] if hit else "jsonl"
    _MODE[p] = (sig, v)
    return v


def lock_path(table, root=None):
    return paths.state(S2.MEM_LOCK[table], root=root)


def spool_lock(root=None):
    return paths.state(S2.SPOOL_LOCK, root=root)


# ---------------------------------------------------------------- строки

def _epoch(s):
    try:
        return int(calendar.timegm(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S")))
    except (ValueError, TypeError):
        return None


def cols_of(table, d):
    """Строка JSONL (dict с seq/prev/h) -> колонки таблицы v2b."""
    body = CH.canon(dict((k, v) for k, v in d.items() if k not in ("seq", "prev", "h")))
    out = {"seq": d["seq"], "rev": d.get("rev"), "kind": d.get("kind"), "ts": d.get("ts"), "body": body, "prev": d["prev"], "h": d["h"]}
    if table == "case_rows":
        out.update(case_id=d.get("case_id"), t_ts=_epoch(d.get("ts")), type=d.get("type"), mode=(d.get("src") or {}).get("mode"))
    else:
        out.update(lesson_id=d.get("lesson_id"), status=d.get("status"), src=((d.get("evidence") or {}).get("src") or "brain"))
    return out


def _insert(conn, table, cols):
    ks = sorted(cols)
    conn.execute("INSERT INTO %s(%s) VALUES(%s)" % (table, ", ".join(ks), ", ".join("?" * len(ks))), [cols[k] for k in ks])


def _top(conn, table):
    cur = conn.execute("SELECT * FROM %s ORDER BY seq DESC LIMIT 1" % table)
    names = [c[0] for c in cur.description]
    r = cur.fetchone()
    return dict(zip(names, r)) if r else None


def _size_check(out, max_bytes):
    if max_bytes:
        n = len(CH.canon(out).encode("utf-8"))
        if n > max_bytes:
            raise ValueError("строка %d байт > %d" % (n, max_bytes))


def append_line(conn, table, rec, max_bytes=None):
    """rec (без seq/prev/h) в конец цепочки таблицы внутри открытой транзакции. Хвост цепочки битый -> ChainBroken. -> строка JSONL."""
    top = _top(conn, table)
    if top is not None and not store2.line_ok(top, top["prev"], table):
        raise store2.ChainBroken(S2.CHAIN_BROKEN, "%s: последняя строка seq=%s не сходится с хешем" % (table, top["seq"]))
    out = dict((k, v) for k, v in rec.items() if k not in ("seq", "prev", "h"))
    out["seq"], out["prev"] = (top["seq"] if top else 0) + 1, (top["h"] if top else GENESIS)
    out["h"] = CH.make_hash(out["prev"], out)
    _size_check(out, max_bytes)
    _insert(conn, table, cols_of(table, out))
    return out


# ---------------------------------------------------------------- соединения

def _open_rw(root):
    path = LST.db_path(root)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    if not size and not _AUTOCREATE:
        raise Unavailable("NO_DB", "нет файла базы знаний")
    conn = LST.open_rw(root)
    try:
        if not store2.ready_v2b(conn):
            if not _AUTOCREATE:
                raise Unavailable(S2.NOT_READY, "нет таблиц v2b (./tb knowledge migrate)")
            with LST.tx(conn):
                store2.apply_v2b(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _open_ro(root):
    conn = LST.open_ro(root)
    if conn is None:
        raise Unavailable("NO_DB", "нет файла базы знаний")
    return conn


def _backup_rows(root, table):
    """Строки таблицы из последней копии state/backups, где она есть (чтение при недоступной базе). -> (строки, имя копии)."""
    for b in reversed(store2.list_backups(root)):
        try:
            c = sqlite3.connect("file:%s?mode=ro" % b["path"].replace("\\", "/"), uri=True)
        except sqlite3.Error:
            continue
        try:
            if store2._has_table(c, table):
                return [store2.line_of(r) for r in store2.mem_rows(c, table)], os.path.basename(b["path"])
        except (sqlite3.Error, ValueError):
            continue
        finally:
            c.close()
    return [], None


# ---------------------------------------------------------------- очередь

def _spool_lines(root):
    """-> (строки очереди по порядку, число битых)."""
    out, broken = [], 0
    try:
        f = open(store2.spool_path(root), "rb")
    except OSError:
        return out, broken
    with f:
        for ln in f:
            if not ln.strip():
                continue
            try:
                d = json.loads(ln.decode("utf-8"))
            except ValueError:
                broken += 1
                continue
            if isinstance(d, dict) and d.get("table") in S2.MEM_TABLES and isinstance(d.get("body"), str):
                out.append(d)
            else:
                broken += 1
    return out, broken


def spool_rows(root, table):
    rows = []
    for d in _spool_lines(root)[0]:
        if d["table"] == table:
            try:
                rows.append(store2.line_of(d))
            except (ValueError, TypeError, KeyError):
                continue
    return rows


def _rewrite_spool(root, lines):
    p = store2.spool_path(root)
    if not lines:
        try:
            os.remove(p)
        except OSError:
            pass
        return
    atomic_write(p, "".join(CH.canon(d) + "\n" for d in lines))


# ---------------------------------------------------------------- чтение

class _Snap(object):
    def __init__(self):
        self.gen = object()
        self.rows = []
        self.last_seq = 0
        self.last_h = None
        self.last_body = None
        self.fid = None


def _file_id(root):
    try:
        st = os.stat(LST.db_path(root))
        return (st.st_ino, st.st_dev)
    except OSError:
        return None


def _db_snap(conn, key, table, fid):
    sn = _SNAP.get(key)
    if sn is None or sn.fid != fid:                       # файл базы подменён (restore): читать заново
        sn = _SNAP[key] = _Snap()
        sn.fid = fid
    top = conn.execute("SELECT seq, h FROM %s ORDER BY seq DESC LIMIT 1" % table).fetchone()
    if sn.last_seq:
        at = conn.execute("SELECT h, body FROM %s WHERE seq=?" % table, (sn.last_seq,)).fetchone()
        if top is None or top[0] < sn.last_seq or at is None or at[0] != sn.last_h or at[1] != sn.last_body:      # усечена или подменена
            sn = _SNAP[key] = _Snap()
            sn.fid = fid
    if top is not None and top[0] > sn.last_seq:
        raw = store2.mem_rows(conn, table, sn.last_seq)
        if raw:
            sn.rows.extend(store2.line_of(r) for r in raw)
            sn.last_seq, sn.last_h, sn.last_body = raw[-1]["seq"], raw[-1]["h"], raw[-1]["body"]
    return sn


def snapshot(root, table):
    """-> (поколение, строки JSONL по порядку). Пока поколение то же, список только растёт (вызывающий дочитывает хвост)."""
    key = (os.path.abspath(LST.db_path(root)), table)
    with _LOCK:
        spool = spool_rows(root, table) if store2.spool_pending(root) else []
        sn = None
        try:
            conn = _open_ro(root)
            try:
                sn = _db_snap(conn, key, table, _file_id(root))
            finally:
                conn.close()
        except (sqlite3.Error, Unavailable, ValueError):
            _SNAP.pop(key, None)
        if sn is not None and not spool:
            return sn.gen, sn.rows
        base = list(sn.rows) if sn is not None else _backup_rows(root, table)[0]
        have = set((r.get(S2.MEM_KEY[table]), r.get("rev")) for r in base)
        extra = [r for r in spool if (r.get(S2.MEM_KEY[table]), r.get("rev")) not in have]
        return object(), base + extra


def tail(root, table):
    """-> (last_seq, last_h, True) как chain.tail."""
    _gen, rows = snapshot(root, table)
    return (rows[-1]["seq"], rows[-1]["h"], True) if rows else (0, GENESIS, True)


def verify_chain(root, table):
    """-> {"ok","n","bad_seq","broken"} как chain.verify (broken - битые строки очереди)."""
    try:
        conn = _open_ro(root)
    except (sqlite3.Error, Unavailable) as e:
        if _AUTOCREATE:                                   # тестовый режим: базы ещё нет = пустая таблица
            return {"ok": True, "n": 0, "bad_seq": None, "broken": 0}
        return {"ok": False, "n": 0, "bad_seq": None, "broken": 0, "error": str(e)}
    try:
        rep = store2.verify_line_chain(conn, table)
    except sqlite3.Error as e:
        rep = {"ok": False, "n": 0, "bad_seq": None, "error": str(e)}
    finally:
        conn.close()
    rep["broken"] = _spool_lines(root)[1]
    return rep


# ---------------------------------------------------------------- запись

def _to_spool(root, table, rec, max_bytes, reason):
    with FileLock(spool_lock(root), wait=LOCK_WAIT_S):
        _gen, rows = snapshot(root, table)
        out = dict((k, v) for k, v in rec.items() if k not in ("seq", "prev", "h"))
        out["seq"], out["prev"] = (rows[-1]["seq"] if rows else 0) + 1, (rows[-1]["h"] if rows else GENESIS)
        out["h"] = CH.make_hash(out["prev"], out)
        _size_check(out, max_bytes)
        line = dict(cols_of(table, out), table=table, spool_ts=int(time.time()), reason=str(reason)[:80])
        p = store2.spool_path(root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "ab") as f:
            f.write((CH.canon(line) + "\n").encode("utf-8"))
            f.flush()
    return out


def append(root, table, rec, max_bytes=None):
    """Строка в таблицу (вызывающий держит блокировку таблицы). Сначала очередь этой таблицы (если есть), затем запись; база недоступна ->
    очередь. Ошибка размера -> ValueError; нарушение UNIQUE(ключ, rev) -> ValueError. -> записанная строка JSONL."""
    if store2.spool_pending(root):
        try:
            replay(root, tables=(table,), locked=True)
        except (sqlite3.Error, Unavailable, store2.KnowledgeError, OSError, TimeoutError, ValueError):
            pass
    try:
        conn = _open_rw(root)
    except (sqlite3.Error, Unavailable, store2.KnowledgeError, OSError) as e:
        return _to_spool(root, table, rec, max_bytes, getattr(e, "code", None) or type(e).__name__)
    try:
        try:
            with LST.tx(conn):
                return append_line(conn, table, rec, max_bytes)
        except sqlite3.IntegrityError as e:
            raise ValueError("%s: %s" % (table, e))
        except (sqlite3.Error, store2.KnowledgeError) as e:
            reason = getattr(e, "code", None) or type(e).__name__
    finally:
        conn.close()
    return _to_spool(root, table, rec, max_bytes, reason)


def _apply(conn, line):
    """Одна строка очереди -> "applied" | "skipped" | "renumbered" | "conflict"."""
    table = line["table"]
    key = S2.MEM_KEY[table]
    rec = json.loads(line["body"])
    kid, rev = rec.get(key), rec.get("rev")
    ex = conn.execute("SELECT body FROM %s WHERE %s=? AND rev=?" % (table, key), (kid, rev)).fetchone()
    if ex is not None:
        if ex[0] == line["body"]:
            return "skipped"
        if table == "case_rows" and rec.get("kind") == "update":
            rec["rev"] = conn.execute("SELECT MAX(rev) FROM case_rows WHERE case_id=?", (kid,)).fetchone()[0] + 1
            append_line(conn, table, rec)
            return "renumbered"
        return "conflict"
    has = conn.execute("SELECT 1 FROM %s WHERE %s=? LIMIT 1" % (table, key), (kid,)).fetchone() is not None
    if (rec.get("kind") == "new" and has) or (table == "case_rows" and rec.get("kind") == "update" and not has):
        return "conflict"
    append_line(conn, table, rec)
    return "applied"


def replay(root=None, tables=S2.MEM_TABLES, locked=False):
    """Очередь -> база одной транзакцией, по порядку строк. locked=True: вызывающий держит блокировки таблиц (писатель); иначе берутся
    cases.lock, lessons.lock (в этом порядке), затем блокировка очереди. В очереди остаются конфликты и строки других таблиц.
    -> {"applied","skipped","renumbered","conflicts","left","broken"}. База недоступна -> исключение (очередь не меняется)."""
    rep = {"applied": 0, "skipped": 0, "renumbered": 0, "conflicts": 0, "left": 0, "broken": 0}
    with contextlib.ExitStack() as st:
        if not locked:
            for t in S2.MEM_TABLES:
                if t in tables:
                    st.enter_context(FileLock(lock_path(t, root), wait=LOCK_WAIT_S))
        st.enter_context(FileLock(spool_lock(root), wait=LOCK_WAIT_S))
        lines, rep["broken"] = _spool_lines(root)
        mine = [d for d in lines if d["table"] in tables]
        if not mine:
            rep["left"] = len(lines)
            return rep
        conn = _open_rw(root)
        try:
            keep = []
            with LST.tx(conn):
                for d in lines:
                    if d["table"] not in tables:
                        keep.append(d)
                        continue
                    try:
                        res = _apply(conn, d)
                    except (ValueError, TypeError):
                        res = "conflict"
                    if res == "conflict":
                        rep["conflicts"] += 1
                        keep.append(d)
                    else:
                        rep[res] += 1
            _rewrite_spool(root, keep)
            rep["left"] = len(keep)
        finally:
            conn.close()
    return rep


def reset_caches():
    with _LOCK:
        _SNAP.clear()
        _MODE.clear()
