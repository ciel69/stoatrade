"""Перенос случаев и уроков в базу знаний и обратно (R2.9b, Requirement 3.2, 3.3, 3.4, 3.5, 3.8; задача 24).

dry_run       свернуть cases.jsonl и lessons.jsonl тем же fold, что код памяти; отчёт (строки, случаи, уроки, расхождения хешей, битые строки,
              дубли ключей, состояние базы и платформы); ничего не пишет. Отсутствующий lessons.jsonl = пусто
migrate       платформа остановлена (иначе PLATFORM_RUNNING) -> cases.lock, lessons.lock -> копии базы (premigrate) и файлов -> одной транзакцией
              схема v2b и строки с сохранением seq, rev, kind, ts (цепочка пересчитывается; у целого файла совпадает с файловой) -> сверка
              fold(база) == fold(файл) поле в поле, кроме prev и h -> переименование файлов в *.pre-r29 и маркер state/memory.store
              store="sqlite" -> COMMIT. Любой сбой до COMMIT: ROLLBACK, имена файлов и маркер прежние (config/memory.json не пишется). После переключения - копия owner (в ней уже есть case_rows) и проверка,
              что cases.jsonl / lessons.jsonl отсутствуют и не появились за settle_s. Перед копией owner строится индекс поиска похожих
              (similar_index; сбой индекса перенос не откатывает, повтор - ./tb knowledge index-similar)
similar_index индекс ix_sig_type_side (tb.knowledge.similar.migrate_v2s) идемпотентно; отдельной командой - при остановленной платформе
export_jsonl  файлы из таблиц (формат прежний, seq по порядку с 1, цепочка пересчитана) и сверка fold(файл) == fold(база); в state/ - только
              при остановленной платформе и пустой очереди, затем маркер store="jsonl" (возврат допустим только после успешного экспорта);
              в другой каталог (out_dir) - без переключения. Правила, версии rulebook и испытания от store не зависят и здесь не трогаются
platform_running  проверка остановки: supervisor.lock, *.pid, свежие *.hb.json живых процессов, зарегистрированные писатели базы

Восстановление после CHAIN_BROKEN в case_rows/lesson_rows: ./tb knowledge restore (копия) и ./tb knowledge replay-spool; rechain нет.
"""
import contextlib
import datetime
import os
import shutil
import sqlite3
import time

from tb import paths
from tb.core.filelock import FileLock, pid_alive
from tb.knowledge import markers as KM
from tb.knowledge import memstore as MS
from tb.knowledge import schema2 as S2
from tb.knowledge import store2
from tb.labels import store as LST
from tb.memory import cases as MC
from tb.memory import chain as CH
from tb.memory import lessons as ML

GENESIS = CH.GENESIS
HB_FRESH_S = 120.0
SETTLE_S = 2.0
NO_CMP = ("prev", "h")


class MigrateError(store2.KnowledgeError):
    code = "MIGRATE"


# ---------------------------------------------------------------- свёртки

def fold_cases(rows):
    return MC.fold(rows)


def fold_lessons(rows):
    """Та же свёртка, что lessons.read_all: последнее состояние по lesson_id (строки схемы tb.lesson/1)."""
    out = {}
    for r in rows:
        if r.get("schema") == ML.SCHEMA and r.get("lesson_id"):
            out[r["lesson_id"]] = r
    return out


FOLD = {"case_rows": fold_cases, "lesson_rows": fold_lessons}


def _strip(d, drop):
    return dict((k, v) for k, v in d.items() if k not in drop)


def fold_diff(fa, fb, drop=NO_CMP):
    """-> идентификаторы, у которых свёртки различаются (поле в поле, кроме drop)."""
    ids = sorted(set(fa) | set(fb))
    return [i for i in ids if i not in fa or i not in fb or _strip(fa[i], drop) != _strip(fb[i], drop)]


# ---------------------------------------------------------------- файлы

def file_path(table, root=None):
    return paths.state(S2.MEM_FILE[table], root=root)


def _chain_report(rows):
    """Расхождения хешей файла: строки, у которых prev не равен h предыдущей целой строки или h не сходится; seq не возрастает."""
    prev, last, bad, bad_seq, first = GENESIS, 0, 0, 0, None
    for r in rows:
        if r.get("prev") != prev or r.get("h") != CH.make_hash(r.get("prev") or "", r):
            bad += 1
            first = r.get("seq") if first is None else first
        if r["seq"] <= last:
            bad_seq += 1
        prev, last = r.get("h"), r["seq"]
    return {"hash_mismatch": bad, "first_bad_seq": first, "seq_not_increasing": bad_seq}


def read_file(table, root=None):
    """-> отчёт файла и строки, готовые к переносу (без дублей с тем же содержимым)."""
    path = file_path(table, root)
    rows, broken = CH.read(path)
    key = S2.MEM_KEY[table]
    seen, keep, bad_rows, dup_same, dup_conflict = {}, [], [], 0, []
    for r in rows:
        k = (r.get(key), r.get("rev"))
        if not isinstance(k[0], str) or not k[0] or isinstance(k[1], bool) or not isinstance(k[1], int) or r.get("kind") not in ("new", "update"):
            bad_rows.append(r.get("seq"))
            continue
        body = CH.canon(_strip(r, ("seq", "prev", "h")))
        if k in seen:
            if seen[k] == body:
                dup_same += 1
            else:
                dup_conflict.append("%s#%s" % k)
            continue
        seen[k] = body
        keep.append(r)
    folded = FOLD[table](rows)
    rep = {"path": os.path.basename(path), "exists": os.path.exists(path), "rows": len(rows), "broken": broken, "items": len(folded),
           "bad_rows": bad_rows[:20], "n_bad_rows": len(bad_rows), "dup_same": dup_same, "dup_conflict": dup_conflict[:20],
           "n_dup_conflict": len(dup_conflict)}
    rep.update(_chain_report(rows))
    return rep, rows, keep, folded


# ---------------------------------------------------------------- платформа

def platform_running(root=None, now=None, hb_fresh_s=HB_FRESH_S, alive=pid_alive):
    """-> список причин «платформа работает» (пусто - остановлена). Ничего не пишет и не удаляет."""
    now = time.time() if now is None else now
    me = os.getpid()
    out = []
    pdir = paths.runtime("platform", root=root)
    sup = KM.read_json(os.path.join(pdir, "supervisor.lock"))
    if isinstance(sup, dict) and int(sup.get("pid") or 0) not in (0, me) and alive(int(sup.get("pid") or 0)):
        out.append("supervisor pid %s" % sup.get("pid"))
    try:
        names = sorted(os.listdir(pdir))
    except OSError:
        names = []
    for n in names:
        d = KM.read_json(os.path.join(pdir, n)) if n.endswith((".pid", ".hb.json")) else None
        if not isinstance(d, dict):
            continue
        pid = d.get("pid")
        pid = int(pid) if isinstance(pid, int) and not isinstance(pid, bool) else 0
        if n.endswith(".pid") and pid and pid != me and alive(pid):
            out.append("сервис %s pid %d" % (n[:-4], pid))
        elif n.endswith(".hb.json"):
            ts = d.get("ts_epoch")
            fresh = isinstance(ts, (int, float)) and not isinstance(ts, bool) and 0 <= now - ts < hb_fresh_s
            if fresh and pid != me and (not pid or alive(pid)):
                out.append("heartbeat %s (%d с)" % (n[:-8], int(now - ts)))
    for pid in KM._pids(KM.writers_dir(root)):
        if pid != me and alive(pid):
            out.append("писатель базы pid %d" % pid)
    return out


# ---------------------------------------------------------------- dry-run

def _db_state(root):
    st = {"db": os.path.exists(LST.db_path(root)), "v2a": False, "v2b": False, "rows": {}}
    if not st["db"] or KM.restoring(root):
        return st
    try:
        conn = LST.open_ro(root)
    except sqlite3.Error as e:
        st["error"] = str(e)
        return st
    if conn is None:
        return st
    try:
        st["v2a"], st["v2b"] = store2.ready(conn), store2.ready_v2b(conn)
        for t in S2.MEM_TABLES:
            if store2._has_table(conn, t):
                st["rows"][t] = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
    except sqlite3.Error as e:
        st["error"] = str(e)
    finally:
        conn.close()
    return st


def _blocking(files):
    out = []
    for t, (rep, _rows, _keep, _f) in files.items():
        if rep["n_dup_conflict"]:
            out.append("DUP_REV:%s" % t)
        if rep["seq_not_increasing"]:
            out.append("BAD_SEQ:%s" % t)
    return out


def dry_run(root=None, now=None):
    """Отчёт без записи (Requirement 3.2)."""
    files = dict((t, read_file(t, root)) for t in S2.MEM_TABLES)
    rep = {"ok": True, "dry_run": True, "store": MS.mode(root), "tables": dict((t, files[t][0]) for t in files), "db": _db_state(root),
           "platform_running": platform_running(root, now), "spool_pending": store2.spool_pending(root), "similar_index": similar_index_state(root),
           "pre_r29": [os.path.basename(file_path(t, root)) + S2.PRE_SUFFIX for t in S2.MEM_TABLES if os.path.exists(file_path(t, root) + S2.PRE_SUFFIX)]}
    rep["cases"], rep["lessons"] = files["case_rows"][0]["items"], files["lesson_rows"][0]["items"]
    rep["blocking"] = _blocking(files) + (["ALREADY_SQLITE"] if rep["store"] == "sqlite" else []) + (["PRE_EXISTS"] if rep["pre_r29"] else [])
    rep["ok"] = not rep["blocking"]
    return rep


# ---------------------------------------------------------------- перенос

def _backup_files(root, stamp):
    """Копии файлов в state/backups/memory-<время>-premigrate/ (без перезаписи). -> каталог."""
    d = os.path.join(store2.backups_dir(root), "memory-%s-premigrate" % stamp)
    n = 1
    base = d
    while os.path.exists(d):
        n += 1
        d = "%s-%d" % (base, n)
    os.makedirs(d)
    for t in S2.MEM_TABLES:
        src = file_path(t, root)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(d, os.path.basename(src)))
    return d


def _db_lines(conn, table):
    return [store2.line_of(r) for r in store2.mem_rows(conn, table)]


def _insert_rows(conn, table, keep):
    """Строки файла в таблицу с сохранением seq; строки, уже перенесённые раньше (повторный перенос после возврата), должны совпасть по seq и
    содержимому (иначе DB_DIVERGED). -> (вставлено, пропущено как уже перенесённое)."""
    have = dict((r["seq"], r) for r in store2.mem_rows(conn, table))
    top = max(have) if have else 0
    prev = have[top]["h"] if have else GENESIS
    n_new = n_old = 0
    for r in keep:
        body = CH.canon(_strip(r, ("seq", "prev", "h")))
        if r["seq"] <= top:
            old = have.get(r["seq"])
            if old is None or old["body"] != body:
                raise MigrateError("DB_DIVERGED", "%s: строка seq=%s в базе отличается от файла" % (table, r["seq"]))
            n_old += 1
            continue
        out = _strip(r, ("prev", "h"))
        out["prev"] = prev
        out["h"] = CH.make_hash(prev, out)
        MS._insert(conn, table, MS.cols_of(table, out))
        prev = out["h"]
        n_new += 1
    return n_new, n_old


def _rename(src, dst, tries=5, sleep=time.sleep):
    for i in range(tries):
        try:
            os.rename(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            sleep(0.2 * (i + 1))


def jsonl_present(root=None):
    return [os.path.basename(file_path(t, root)) for t in S2.MEM_TABLES if os.path.exists(file_path(t, root))]


@contextlib.contextmanager
def _mem_locks(root):
    with contextlib.ExitStack() as st:
        for t in S2.MEM_TABLES:
            try:
                st.enter_context(FileLock(MS.lock_path(t, root), wait=CH.LOCK_WAIT_S))
            except TimeoutError as e:
                raise MigrateError("LOCK_BUSY", "%s занята писателем: остановите платформу" % S2.MEM_LOCK[t]) from e
        yield


def _stamp(now):
    return datetime.datetime.fromtimestamp(time.time() if now is None else now, datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")


def migrate(root=None, now=None, settle_s=SETTLE_S, sleep=time.sleep, running=None):
    """./tb knowledge migrate (Requirement 3.3, 3.4). running - подмена проверки платформы (тесты). -> отчёт; отказ -> MigrateError(code)."""
    if MS.mode(root) == "sqlite":
        raise MigrateError("ALREADY_SQLITE", "store уже sqlite")
    busy = (running or platform_running)(root, now)
    if busy:
        raise MigrateError(S2.PLATFORM_RUNNING, "платформа работает (%s): сначала ./tb down" % "; ".join(busy[:5]), busy)
    if KM.restoring(root):
        raise MigrateError(S2.RESTORING, "идёт восстановление базы")
    if store2.spool_pending(root):
        raise MigrateError(S2.SPOOL_PENDING, "в очереди есть записи: ./tb knowledge replay-spool")
    pre = [file_path(t, root) + S2.PRE_SUFFIX for t in S2.MEM_TABLES if os.path.exists(file_path(t, root) + S2.PRE_SUFFIX)]
    if pre:
        raise MigrateError("PRE_EXISTS", "уже есть %s: уберите прежние копии переноса" % ", ".join(os.path.basename(p) for p in pre))
    rep = {"ok": False, "backup_db": None, "backup_files": None, "tables": {}, "renamed": [], "store": "jsonl"}
    with _mem_locks(root):
        if MS.mode(root) == "sqlite":
            raise MigrateError("ALREADY_SQLITE", "store уже sqlite")
        files = dict((t, read_file(t, root)) for t in S2.MEM_TABLES)
        block = _blocking(files)
        if block:
            raise MigrateError("BLOCKED", ", ".join(block), dict((t, files[t][0]) for t in files))
        stamp = _stamp(now)
        if os.path.exists(LST.db_path(root)):
            try:
                rep["backup_db"] = store2.make_backup(root, "premigrate", now=now, sleep=sleep)
            except (store2.KnowledgeError, OSError, sqlite3.Error) as e:
                raise MigrateError(S2.KNOWLEDGE_MIGRATION, "копия базы не сделана: %s" % e) from e
        rep["backup_files"] = _backup_files(root, stamp)
        conn = LST.open_rw(root)
        renamed, switched, prev_marker = [], False, None
        try:
            with LST.tx(conn):
                store2.apply_v2b(conn)
                for t in S2.MEM_TABLES:
                    frep, rows, keep, folded = files[t]
                    n_new, n_old = _insert_rows(conn, t, keep)
                    diff = fold_diff(FOLD[t](_db_lines(conn, t)), folded)
                    if diff:
                        raise MigrateError(S2.FOLD_MISMATCH, "%s: свёртка базы не равна свёртке файла (%d: %s)" % (t, len(diff), ", ".join(diff[:5])), diff[:50])
                    ch = store2.verify_line_chain(conn, t)
                    if not ch["ok"]:
                        raise MigrateError(S2.CHAIN_BROKEN, "%s: цепочка в базе разорвана на seq=%s" % (t, ch["bad_seq"]))
                    rep["tables"][t] = dict(frep, inserted=n_new, already=n_old, db_rows=ch["n"])
                for t in S2.MEM_TABLES:
                    src = file_path(t, root)
                    if os.path.exists(src):
                        _rename(src, src + S2.PRE_SUFFIX, sleep=sleep)
                        renamed.append(src)
                prev_marker = MS.read_marker(root)
                MS.write_marker(root, "sqlite", "migrate", now=now)
                switched = True
                if MS.mode(root) != "sqlite":
                    raise MigrateError("STORE_NOT_SET", "store=sqlite не читается после записи маркера")
        except BaseException:
            for src in reversed(renamed):
                try:
                    _rename(src + S2.PRE_SUFFIX, src, sleep=sleep)
                except OSError:
                    pass
            if switched:
                MS.restore_marker(root, prev_marker)
            raise
        finally:
            conn.close()
        rep["renamed"] = [os.path.basename(p) + S2.PRE_SUFFIX for p in renamed]
        rep["store"] = "sqlite"
        rep["jsonl_after_switch"] = jsonl_present(root)
    try:                                                  # индекс поиска похожих (задача 25): перенос уже выполнен, сбой индекса его не откатывает
        rep["similar_index"] = similar_index(root, now=now, check_running=False)
    except MigrateError as e:
        rep["similar_index"] = {"ok": False, "code": e.code, "error": str(e)}
    try:
        rep["backup_after"] = store2.make_backup(root, "owner", now=now, sleep=sleep)
    except (store2.KnowledgeError, OSError, sqlite3.Error) as e:
        rep["backup_after"] = None
        rep["backup_after_error"] = str(e)
    if settle_s:
        sleep(settle_s)
    rep["jsonl_after_settle"] = jsonl_present(root)
    rep["ok"] = not rep["jsonl_after_switch"] and not rep["jsonl_after_settle"]
    if not rep["ok"]:
        rep["code"] = "JSONL_APPEARED"
    return rep


# ---------------------------------------------------------------- индекс поиска похожих

def similar_index(root=None, now=None, running=None, check_running=True):
    """./tb knowledge index-similar и шаг migrate: индекс ix_sig_type_side ON signals(type, side, t0) (tb.knowledge.similar.migrate_v2s,
    свой ключ meta.knowledge_similar_ver), идемпотентно: повтор ничего не меняет. Отдельной командой - только при остановленной платформе
    (построение индекса на большой signals держит запись базы). -> {"ok", "ver", "created", "elapsed_s"}; отказ -> MigrateError."""
    from tb.knowledge import similar as KS
    if check_running:
        busy = (running or platform_running)(root, now)
        if busy:
            raise MigrateError(S2.PLATFORM_RUNNING, "платформа работает (%s): сначала ./tb down" % "; ".join(busy[:5]), busy)
    if KM.restoring(root):
        raise MigrateError(S2.RESTORING, "идёт восстановление базы")
    if not os.path.exists(LST.db_path(root)):
        raise MigrateError("NO_DB", "нет базы знаний")
    t0 = time.monotonic()
    try:
        conn = LST.open_rw(root)
    except (sqlite3.Error, LST.SchemaMigrationError) as e:
        raise MigrateError("INDEX_FAILED", "база знаний не открыта: %s" % e) from e
    try:
        had = KS.has_index(conn) and KS._meta_ver(conn) >= KS.SIM_VER
        ver = KS.migrate_v2s(conn)
        ok = KS.has_index(conn)
    except (store2.KnowledgeError, sqlite3.Error) as e:
        raise MigrateError("INDEX_FAILED", str(e)) from e
    finally:
        conn.close()
    return {"ok": ok, "ver": ver, "index": KS.INDEX_NAME, "created": not had, "elapsed_s": round(time.monotonic() - t0, 3)}


def similar_index_state(root=None):
    """-> True/False (индекс поиска похожих есть) или None (базы нет / не читается). Только чтение."""
    from tb.knowledge import similar as KS
    if not os.path.exists(LST.db_path(root)) or KM.restoring(root):
        return None
    try:
        conn = LST.open_ro(root)
    except sqlite3.Error:
        return None
    if conn is None:
        return None
    try:
        return KS.has_index(conn) and KS._meta_ver(conn) >= KS.SIM_VER
    except sqlite3.Error:
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------- экспорт

def _export_lines(rows):
    """Строки базы -> строки файла: seq с 1 по порядку, цепочка пересчитана."""
    out, prev = [], GENESIS
    for i, r in enumerate(rows, 1):
        d = _strip(r, ("prev", "h"))
        d["seq"], d["prev"] = i, prev
        d["h"] = CH.make_hash(prev, d)
        prev = d["h"]
        out.append(d)
    return out


def export_jsonl(root=None, out_dir=None, now=None, running=None):
    """./tb knowledge export-jsonl [--out DIR] (Requirement 3.5). out_dir=None: в state/ и store="jsonl" (только при остановленной платформе,
    store=sqlite, пустой очереди, без существующих cases.jsonl/lessons.jsonl). -> отчёт; отказ -> MigrateError."""
    to_state = out_dir is None
    if to_state:
        if MS.mode(root) != "sqlite":
            raise MigrateError("NOT_SQLITE", "store не sqlite: возвращать нечего")
        busy = (running or platform_running)(root, now)
        if busy:
            raise MigrateError(S2.PLATFORM_RUNNING, "платформа работает (%s): сначала ./tb down" % "; ".join(busy[:5]), busy)
        if store2.spool_pending(root):
            raise MigrateError(S2.SPOOL_PENDING, "в очереди есть записи: сначала ./tb knowledge replay-spool")
    dst_dir = paths.state(root=root) if to_state else os.path.abspath(out_dir)
    rep = {"ok": False, "out": dst_dir, "tables": {}, "store": MS.mode(root)}
    with (_mem_locks(root) if to_state else contextlib.nullcontext()):
        targets = dict((t, os.path.join(dst_dir, S2.MEM_FILE[t])) for t in S2.MEM_TABLES)
        exist = [os.path.basename(p) for p in targets.values() if os.path.exists(p)]
        if exist:
            raise MigrateError("FILES_EXIST", "уже есть %s в %s" % (", ".join(exist), dst_dir))
        conn = LST.open_ro(root)
        if conn is None:
            raise MigrateError("NO_DB", "нет базы знаний")
        try:
            if not store2.ready_v2b(conn):
                raise MigrateError(S2.NOT_READY, "нет таблиц v2b")
            db = dict((t, _db_lines(conn, t)) for t in S2.MEM_TABLES)
        finally:
            conn.close()
        os.makedirs(dst_dir, exist_ok=True)
        tmps = {}
        try:
            for t in S2.MEM_TABLES:
                lines = _export_lines(db[t])
                tmp = targets[t] + ".export.tmp"
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    for d in lines:
                        f.write(CH.canon(d) + "\n")
                tmps[t] = tmp
                rows, broken = CH.read(tmp)
                diff = fold_diff(FOLD[t](rows), FOLD[t](db[t]), drop=("seq", "prev", "h"))
                ch = CH.verify(tmp)
                if diff or broken or not ch["ok"]:
                    raise MigrateError(S2.FOLD_MISMATCH, "%s: экспорт не сходится с базой (расхождений %d, битых %d, цепочка %s)" % (t, len(diff), broken, ch["ok"]))
                rep["tables"][t] = {"rows": len(rows), "items": len(FOLD[t](rows))}
            for t in S2.MEM_TABLES:
                os.replace(tmps.pop(t), targets[t])
        finally:
            for tmp in tmps.values():
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        if to_state:
            MS.write_marker(root, "jsonl", "export-jsonl", now=now)
            rep["store"] = MS.mode(root)
    rep["ok"] = True
    return rep
