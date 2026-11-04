"""Ряды сводного рынка по суткам UTC (R1.7, задача 5; Requirements 13.1–13.6; design.md, «Строка ряда», «Хранение»).

Файл суток `state/cmkt/YYYYMMDD.sqlite` (каталог — `store.dir` конфига), WAL, `synchronous=NORMAL`, ключ `(ts, sym_id)` WITHOUT ROWID:
вставка такта (20–160 строк одного `ts`) — одна транзакция и дописывание в конец B-дерева. Значения признаков — один BLOB `f`
(`tb.logic.cmkt.encode_f`), разрез по рынкам — BLOB `by_ex` (`encode_by_ex`); смена набора признаков меняет `meta.cm_ver` и `meta.feats`
(имена и масштабы), а не схему: читатель декодирует строку по `feats` своего файла.

Писатель один (`Store`, процесс сводки): строки с `ts` не больше последней записанной не пишутся (`clock_back`, свойство 3); `light`
пишется только при `ts % light_every_s == 0`, `deep` — при `ts % deep_every_s == 0`; файл выбирается по `ts` строки (переход через
полночь — новый файл, старый закрывается). Свободного места меньше `store.disk_min_gb` — ряды не пишутся (`DISK_LOW`), снимок
продолжается. Повреждённый файл суток (`SQLITE_CORRUPT`, `SQLITE_NOTADB`) переименовывается в `YYYYMMDD.corrupt-<ts>.sqlite`, запись
продолжается в новый файл; ни одна ошибка базы не бросается наружу — сбор не падает. Ротация раз в сутки удаляет файлы старше
`keep_days` суток, никогда — текущие и предыдущие сутки и открытый файл; `PermissionError` (Windows: файл открыт читателем) —
повтор в следующий проход (через час).

Читатели (`read_row`, `read_rows`, `page_stats`) открывают файл только на чтение (`mode=ro`) на один вызов и не бросают: ротация на
Windows удаляет файлы, долгоживущее соединение ей бы мешало. Потребители платформы читают ряды через `tb.core.cmktdoc` (задача 16),
а не через этот модуль. Ряды не входят в резервные копии `knowledge.store2.make_backup` (та копирует только `state/knowledge.sqlite`).
"""
import datetime
import json
import os
import re
import shutil
import sqlite3
import time
from urllib.request import pathname2url

from tb import fsutil, paths
from tb.logic import cmkt as L

SCHEMA_VER = "1"
BY_EX_VER = "1"
TIERS = {"deep": 1, "light": 2}
TIER_NAMES = {v: k for k, v in TIERS.items()}
DISK_CHECK_S = 60.0
ROTATE_EVERY_S = 86400.0
ROTATE_RETRY_S = 3600.0
GIB = 1024 ** 3
_RE_FILE = re.compile(r"^(\d{8})(\.(?:corrupt|cmver)-\d+)?\.sqlite(-wal|-shm|-journal)?$")
CORRUPT_CODES = (11, 26)                       # SQLITE_CORRUPT, SQLITE_NOTADB
CORRUPT_TEXT = ("file is not a database", "database disk image is malformed", "malformed")

DDL = (
    "CREATE TABLE IF NOT EXISTS syms(sym_id INTEGER PRIMARY KEY, sym TEXT UNIQUE NOT NULL)",
    "CREATE TABLE IF NOT EXISTS mkts(mkt_id INTEGER PRIMARY KEY, mkt TEXT UNIQUE NOT NULL)",
    "CREATE TABLE IF NOT EXISTS rows(ts INTEGER NOT NULL, sym_id INTEGER NOT NULL, tier INTEGER NOT NULL, wr_ms INTEGER NOT NULL,"
    " focus_s INTEGER, skew_ms INTEGER, gap_s INTEGER, flags INTEGER NOT NULL, f BLOB NOT NULL, by_ex BLOB,"
    " PRIMARY KEY(ts, sym_id)) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)",
)
INSERT = "INSERT OR IGNORE INTO rows(ts, sym_id, tier, wr_ms, focus_s, skew_ms, gap_s, flags, f, by_ex) VALUES (?,?,?,?,?,?,?,?,?,?)"


def store_cfg(cfg=None):
    d = {"dir": "state/cmkt", "keep_days": 30, "deep_every_s": 1, "light_every_s": 5, "disk_min_gb": 40}
    if isinstance(cfg, dict):
        d.update(cfg.get("store") if isinstance(cfg.get("store"), dict) else {})
    return d


def store_dir(root=None, cfg=None):
    """Каталог рядов: `store.dir` относительно корня проекта (по умолчанию state/cmkt)."""
    parts = store_cfg(cfg)["dir"].split("/")
    return paths.state(*parts[1:], root=root) if parts[0] == "state" else os.path.join(root or paths.root(), *parts)


def day_of(ts):
    """Сутки UTC отметки: 'YYYYMMDD'."""
    return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).strftime("%Y%m%d")


def day_start(day):
    return int(datetime.datetime.strptime(day, "%Y%m%d").replace(tzinfo=datetime.timezone.utc).timestamp())


def day_path(root, day, cfg=None):
    return os.path.join(store_dir(root, cfg), "%s.sqlite" % day)


def is_corrupt(e):
    """Ошибка базы — повреждение файла (SQLITE_CORRUPT, SQLITE_NOTADB)."""
    if not isinstance(e, sqlite3.DatabaseError):
        return False
    code = getattr(e, "sqlite_errorcode", None)
    if isinstance(code, int) and (code & 0xFF) in CORRUPT_CODES:
        return True
    msg = str(e).lower()
    return any(t in msg for t in CORRUPT_TEXT)


class CmVerMismatch(sqlite3.DatabaseError):
    """Файл суток записан другим набором признаков (meta.cm_ver): дописывать в него нельзя."""


def feats_meta():
    """meta.feats: [[имя, масштаб], ...] текущего набора (по ним читается файл)."""
    return json.dumps([list(x) for x in L.FEATURE_SCALES], separators=(",", ":"))


class Store(object):
    """Единственный писатель рядов (процесс сводки). Не бросает исключений из write/rotate: код результата и счётчики в `stats`."""

    def __init__(self, root=None, cfg=None, clock=time.time, disk_free=None):
        self.root = root
        self.cfg = cfg
        self.sc = store_cfg(cfg)
        self.dir = store_dir(root, cfg)
        self.clock = clock
        self.disk_free = disk_free or self._disk_free
        self.day = None
        self.conn = None
        self.syms, self.mkt_ids = {}, {}
        self.last_ts = None
        self._disk_t, self._disk_low = None, False
        self._rot_next = None
        self.stats = {"ticks": 0, "rows": 0, "clock_back": 0, "skip_tier": 0, "disk_low": False, "corrupt": 0, "quarantined": [],
                      "errors": 0, "last_err": None, "rotated": 0, "rotate_fail": 0, "write_ms": None, "file": None}

    # ---------------------------------------------------------------- файл суток
    def _disk_free(self, path):
        p = path
        while p and not os.path.isdir(p):
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
        return shutil.disk_usage(p or ".").free

    def disk_low(self, now=None):
        """Свободно меньше store.disk_min_gb (проверка не чаще раза в 60 с)."""
        now = self.clock() if now is None else now
        if self._disk_t is None or now - self._disk_t >= DISK_CHECK_S or now < self._disk_t:
            self._disk_t = now
            try:
                self._disk_low = self.disk_free(self.dir) < self.sc["disk_min_gb"] * GIB
            except OSError:
                self._disk_low = False
        self.stats["disk_low"] = self._disk_low
        return self._disk_low

    def _open(self, day):
        path = day_path(self.root, day, self.cfg)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, timeout=5.0, isolation_level=None, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            for q in DDL:
                conn.execute(q)
            have = dict(conn.execute("SELECT k, v FROM meta").fetchall())
            if not have:
                conn.execute("BEGIN")
                conn.executemany("INSERT OR IGNORE INTO meta(k, v) VALUES (?, ?)",
                                 [("schema_ver", SCHEMA_VER), ("cm_ver", L.CM_VER), ("by_ex_ver", BY_EX_VER), ("feats", feats_meta()),
                                  ("day", day)])
                conn.executemany("INSERT OR IGNORE INTO mkts(mkt_id, mkt) VALUES (?, ?)", [(i + 1, m) for i, m in enumerate(L.MARKETS)])
                conn.execute("COMMIT")
            elif have.get("cm_ver") != L.CM_VER:
                # файл тех же суток от процесса со старым набором признаков: строки нового набора в нём читались бы неверно
                raise CmVerMismatch("cm_ver mismatch: file %s, code %s" % (have.get("cm_ver"), L.CM_VER))
            self.syms = dict((s, i) for i, s in conn.execute("SELECT sym_id, sym FROM syms"))
            self.mkt_ids = dict((m, i) for i, m in conn.execute("SELECT mkt_id, mkt FROM mkts"))
            mx = conn.execute("SELECT MAX(ts) FROM rows").fetchone()[0]
        except BaseException:
            conn.close()
            raise
        self.conn, self.day = conn, day
        self.stats["file"] = path
        if mx is not None and (self.last_ts is None or mx > self.last_ts):
            self.last_ts = int(mx)
        return conn

    def close(self):
        c, self.conn, self.day = self.conn, None, None
        self.syms, self.mkt_ids = {}, {}
        if c is not None:
            try:
                c.close()
            except sqlite3.Error:
                pass

    def quarantine(self, day, now=None, tag="corrupt"):
        """Повреждённый файл суток -> YYYYMMDD.corrupt-<ts>.sqlite (и его -wal/-shm); запись дальше идёт в новый файл.
        tag="cmver" — файл тех же суток со старым набором признаков (перезапуск после обновления): YYYYMMDD.cmver-<ts>.sqlite."""
        self.close()
        now = int(self.clock() if now is None else now)
        path = day_path(self.root, day, self.cfg)
        dst = path[:-len(".sqlite")] + ".%s-%d.sqlite" % (tag, now)
        n = 0
        while os.path.exists(dst):
            n += 1
            dst = path[:-len(".sqlite")] + ".%s-%d%d.sqlite" % (tag, now, n)
        ok = True
        for suf in ("", "-wal", "-shm"):
            if os.path.exists(path + suf):
                try:
                    fsutil.replace(path + suf, dst + suf, attempts=5, delay=0.05)
                except OSError:
                    ok = False
        if tag == "corrupt":
            self.stats["corrupt"] += 1
        if ok:
            self.stats["quarantined"] = (self.stats["quarantined"] + [os.path.basename(dst)])[-10:]
        return ok

    def _conn_for(self, day):
        if self.conn is not None and self.day == day:
            return self.conn
        self.close()
        try:
            return self._open(day)
        except CmVerMismatch:
            if self.quarantine(day, tag="cmver"):
                return self._open(day)
            raise
        except sqlite3.DatabaseError as e:
            if is_corrupt(e) and self.quarantine(day):
                return self._open(day)
            raise

    # ---------------------------------------------------------------- запись
    def due(self, tier, ts):
        """Строка яруса пишется на этом ts: deep — при ts % deep_every_s == 0, light — при ts % light_every_s == 0."""
        step = self.sc["light_every_s"] if tier == "light" else self.sc["deep_every_s"]
        return int(ts) % int(step) == 0

    def _sym_id(self, conn, sym):
        i = self.syms.get(sym)
        if i is None:
            conn.execute("INSERT OR IGNORE INTO syms(sym) VALUES (?)", (sym,))
            i = conn.execute("SELECT sym_id FROM syms WHERE sym=?", (sym,)).fetchone()[0]
            self.syms[sym] = i
        return i

    def _mkt_ids(self, conn, rows):
        for r in rows or ():
            m = r.get("mkt") if isinstance(r, dict) else None
            if isinstance(m, str) and m not in self.mkt_ids:
                conn.execute("INSERT OR IGNORE INTO mkts(mkt) VALUES (?)", (m,))
                self.mkt_ids[m] = conn.execute("SELECT mkt_id FROM mkts WHERE mkt=?", (m,)).fetchone()[0]
        return self.mkt_ids

    def _records(self, conn, ts, rows, wr_ts):
        wr_ms = int(round((wr_ts - ts) * 1000.0))
        out = []
        for r in rows:
            tier = r.get("tier")
            if tier not in TIERS or not self.due(tier, ts):
                self.stats["skip_tier"] += 1
                continue
            sym = r.get("sym")
            if not isinstance(sym, str) or not sym:
                continue
            flags = r.get("flags")
            bits = int(flags) if isinstance(flags, int) and not isinstance(flags, bool) else L.flag_bits(flags or ())
            by_ex = r.get("by_ex")
            blob_ex = L.encode_by_ex(by_ex, self._mkt_ids(conn, by_ex)) if by_ex else None
            out.append((int(ts), self._sym_id(conn, sym), TIERS[tier], wr_ms, _int_or_none(r.get("focus_s")), _int_or_none(r.get("skew_ms")),
                        _int_or_none(r.get("gap_s")), bits, L.encode_f(r.get("f") or {}), blob_ex))
        return out

    def write(self, ts, rows, wr_ts=None):
        """Строки одного такта ts одной транзакцией. rows: [{"sym", "tier": "deep"|"light", "f": {признак: значение}, "flags": [имена]|биты,
        "by_ex": [строки logic.by_ex_rows], "focus_s", "skew_ms", "gap_s"}]; wr_ts — время записи (по умолчанию часы писателя в момент
        вставки). -> {"n": записано строк, "code": None | "CLOCK_BACK" | "DISK_LOW" | "CORRUPT" | "ERR"}. Не бросает."""
        ts = int(ts)
        self.stats["ticks"] += 1
        if self.last_ts is not None and ts <= self.last_ts:
            self.stats["clock_back"] += 1
            return {"n": 0, "code": "CLOCK_BACK"}
        if self.disk_low():
            return {"n": 0, "code": "DISK_LOW"}
        day = day_of(ts)
        for attempt in (0, 1):
            t_start = time.perf_counter()
            try:
                conn = self._conn_for(day)
                if self.last_ts is not None and ts <= self.last_ts:      # файл суток уже содержит этот ts (перезапуск писателя)
                    self.stats["clock_back"] += 1
                    return {"n": 0, "code": "CLOCK_BACK"}
                conn.execute("BEGIN")
                try:
                    recs = self._records(conn, ts, rows or (), self.clock() if wr_ts is None else wr_ts)
                    if recs:
                        conn.executemany(INSERT, recs)
                    conn.execute("COMMIT")
                except BaseException:
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    raise
                self.last_ts = ts
                self.stats["rows"] += len(recs)
                self.stats["write_ms"] = round((time.perf_counter() - t_start) * 1000.0, 3)
                return {"n": len(recs), "code": None}
            except sqlite3.DatabaseError as e:
                self.stats["last_err"] = "%s: %s" % (type(e).__name__, e)
                if is_corrupt(e) and attempt == 0:
                    self.quarantine(day)
                    continue
                self.stats["errors"] += 1
                self.close()
                return {"n": 0, "code": "CORRUPT" if is_corrupt(e) else "ERR"}
            except (OSError, ValueError, TypeError, OverflowError) as e:
                self.stats["errors"] += 1
                self.stats["last_err"] = "%s: %s" % (type(e).__name__, e)
                self.close()
                return {"n": 0, "code": "ERR"}
        return {"n": 0, "code": "CORRUPT"}

    # ---------------------------------------------------------------- ротация
    def rotate(self, now=None):
        """Удаляет файлы суток старше keep_days (возраст = сегодня − сутки файла > keep_days), кроме текущих и предыдущих суток и открытого
        файла. PermissionError — файл остаётся, повтор в следующий проход. -> {"deleted": [...], "failed": [...]}."""
        now = self.clock() if now is None else now
        today = day_start(day_of(now))
        keep = max(2, int(self.sc["keep_days"]))
        out = {"deleted": [], "failed": []}
        try:
            names = sorted(os.listdir(self.dir))
        except OSError:
            return out
        for n in names:
            m = _RE_FILE.match(n)
            if not m:
                continue
            try:
                d = day_start(m.group(1))
            except ValueError:
                continue
            age = (today - d) // 86400
            if age <= 1 or age <= keep or (m.group(1) == self.day and not m.group(2)):
                continue
            try:
                os.remove(os.path.join(self.dir, n))
                out["deleted"].append(n)
            except FileNotFoundError:
                pass
            except OSError:                                   # PermissionError на Windows: файл открыт читателем
                out["failed"].append(n)
        self.stats["rotated"] += len(out["deleted"])
        self.stats["rotate_fail"] = len(out["failed"])
        return out

    def maybe_rotate(self, now=None):
        """Ротация раз в сутки (после неудачи — через час); первый вызов — сразу."""
        now = self.clock() if now is None else now
        if self._rot_next is not None and now < self._rot_next:
            return None
        r = self.rotate(now)
        self._rot_next = now + (ROTATE_RETRY_S if r["failed"] else ROTATE_EVERY_S)
        return r


def _int_or_none(x):
    if x is None or isinstance(x, bool):
        return None
    try:
        return int(round(float(x)))
    except (TypeError, ValueError, OverflowError):
        return None


# ---------------------------------------------------------------- чтение (только чтение, на один вызов)
def open_ro(path):
    """Соединение только на чтение (mode=ro) или None, если файла нет."""
    if not os.path.isfile(path):
        return None
    return sqlite3.connect("file:%s?mode=ro" % pathname2url(os.path.abspath(path)), uri=True, timeout=1.0)


def _meta(conn):
    meta = dict(conn.execute("SELECT k, v FROM meta").fetchall())
    feats = json.loads(meta.get("feats") or "[]")
    names = tuple(f[0] for f in feats) if feats else L.CM_FEATURES
    scales = dict((f[0], f[1]) for f in feats) if feats else dict(L.SCALES)
    return meta, names, scales


def _row(conn, rec, sym, names, scales, meta, mkt_names):
    ts, tier, wr_ms, focus_s, skew_ms, gap_s, flags, f, by_ex = rec
    return {"ts": ts, "sym": sym, "tier": TIER_NAMES.get(tier, tier), "wr_ts": ts + wr_ms / 1000.0, "focus_s": focus_s, "skew_ms": skew_ms,
            "gap_s": gap_s, "flags": L.flags_of(flags), "f": L.decode_f(f, names, scales),
            "by_ex": L.decode_by_ex(by_ex, mkt_names) if by_ex else [], "cm_ver": meta.get("cm_ver")}


def read_rows(root, sym, ts_from, ts_to, cfg=None, tier=None):
    """Строки монеты с ts_from ≤ ts ≤ ts_to (по файлам суток) -> [dict] по возрастанию ts. Только чтение; сбой — []."""
    out = []
    t = int(ts_from)
    while t <= int(ts_to):
        day = day_of(t)
        end = min(int(ts_to), day_start(day) + 86399)
        conn = None
        try:
            conn = open_ro(day_path(root, day, cfg))
            if conn is not None:
                meta, names, scales = _meta(conn)
                sid = conn.execute("SELECT sym_id FROM syms WHERE sym=?", (sym,)).fetchone()
                if sid is not None:
                    mkt_names = dict(conn.execute("SELECT mkt_id, mkt FROM mkts").fetchall())
                    q = "SELECT ts, tier, wr_ms, focus_s, skew_ms, gap_s, flags, f, by_ex FROM rows WHERE ts BETWEEN ? AND ? AND sym_id=?"
                    args = [t, end, sid[0]]
                    if tier in TIERS:
                        q += " AND tier=?"
                        args.append(TIERS[tier])
                    for rec in conn.execute(q + " ORDER BY ts", args):
                        out.append(_row(conn, rec, sym, names, scales, meta, mkt_names))
        except (sqlite3.Error, OSError, ValueError):
            pass
        finally:
            if conn is not None:
                conn.close()
        t = end + 1
    return out


def read_row(root, sym, ts, cfg=None):
    """Строка (ts, монета) или None. Только чтение, на один вызов; не бросает."""
    r = read_rows(root, sym, ts, ts, cfg)
    return r[0] if r else None


def page_stats(path):
    """Заполнение страниц файла рядов (задача 26, Requirement 13.4): размер страницы, страниц, свободных, строк, байт на строку и доля
    заполнения страниц дерева rows по dbstat (None, если dbstat не собран). Только чтение; сбой — None."""
    conn = None
    try:
        conn = open_ro(path)
        if conn is None:
            return None
        ps = conn.execute("PRAGMA page_size").fetchone()[0]
        pc = conn.execute("PRAGMA page_count").fetchone()[0]
        fl = conn.execute("PRAGMA freelist_count").fetchone()[0]
        n = conn.execute("SELECT COUNT(*) FROM rows").fetchone()[0]
        fill = None
        try:
            r = conn.execute("SELECT SUM(pgsize - unused), SUM(pgsize) FROM dbstat WHERE name='rows'").fetchone()
            if r and r[1]:
                fill = round(r[0] / float(r[1]), 4)
        except sqlite3.Error:
            fill = None
        size = ps * (pc - fl)
        return {"page_size": ps, "page_count": pc, "freelist": fl, "rows": n, "bytes": size,
                "bytes_per_row": round(size / float(n), 1) if n else None, "fill": fill}
    except (sqlite3.Error, OSError):
        return None
    finally:
        if conn is not None:
            conn.close()
