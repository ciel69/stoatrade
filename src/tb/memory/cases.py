"""Случаи tb.case/1: схема, валидатор, хранилище `state/cases.jsonl` (08 п. 1).

Хранилище по `store` из config/memory.json (R2.9b, Requirement 3.4): "jsonl" (по умолчанию) - файл, как прежде; "sqlite" - таблица case_rows
базы знаний (tb.knowledge.memstore: та же цепочка, очередь при недоступной базе), JSONL не открывается. Режим перечитывается под блокировкой
при каждой записи и при каждом чтении (кеш по mtime файла настроек); имена, параметры и структуры функций прежние.
Append-only, хеш-цепочка (chain.py), запись под блокировкой `state/cases.lock`. Случай не редактируется: дополнение = строка kind="update" того же
case_id с rev+1 (читатель сворачивает по case_id). Update может менять ТОЛЬКО result, whatif, review, tags, flags, exclude_from_stats и дополнять refs;
situation/decision/src фиксируются при создании. Строка <= 3 КБ. privacy_scan проверяется при записи.
"""
import calendar
import copy as copy_mod
import hashlib
import json
import os
import re
import threading
import time

from tb import paths
from tb.core.filelock import FileLock
from tb.core.tradectx import ERR_TAGS
from tb.memory import chain, privacy

SCHEMA = "tb.case/1"
MAX_BYTES = 3072
TYPES = ("trade", "skip", "idea", "signal")
SIDES = ("long", "short", "watch")
COIN_CLASSES = ("meme", "major", "alt_mid", "alt_small", "stock_token", "unknown")
SETUPS = ("level_reclaim", "level_reject", "sweep_reject", "breakout_retest", "range_fade", "trend_pullback", "momentum_chase", "manual", "none")
REGIMES = ("trend", "range", "volatile", "unclear", "unknown")
SESSIONS = ("asia", "europe", "us", "overlap")
CONFIRM_KINDS = ("candle_close", "none", "unknown")
ACTIONS = ("enter", "skip", "wait", "exit", "stop_move")
SKIP_CODES = ("NO_CONFIRM", "WIDE_STOP", "RANGE_GT_STOP", "COOLDOWN", "BLACKOUT", "SPREAD", "SIZE", "NO_EDGE", "CORRELATED", "OTHER")
STATUSES = ("open", "closed", "expired", "na")
EXIT_REASONS = ("sl", "tp", "manual", "timeout", "invalidation", "rule", "kill", "session_end")
ACTORS = ("agent", "owner", "system")
ROLES = ("position_manager", "morning_analyst", "chat", "manual", "mechanics", "reviewer", "migration")
MODES = ("live", "paper", "testnet")
MEM = ("on", "off")
RULE_FOLLOWED = ("yes", "no", "partial", "na")
FLAGS = ("inj_suspect", "dup_of_trade", "backfill", "retro_features", "orphan", "artifact")   # artifact: результат искажён сбоем платформы (не рынком), в статистику и уроки не идёт
RE_SYM = re.compile(r"^[A-Z0-9]{2,20}$")
RE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,48}$")
RE_CASE_ID = re.compile(r"^C-\d{8}-[A-Z0-9]{2,20}-[A-Za-z0-9]{2,12}$")

N, I, B, S, E_ = "num", "int", "bool", "str", "enum"
SITUATION = {
    "sym": (S, 20), "coin_class": (E_, COIN_CLASSES), "side": (E_, SIDES), "setup": (E_, SETUPS), "hyp": (S, 40), "regime": (E_, REGIMES),
    "btc_ret_1h_pct": (N, -50, 50), "range_5m_pct": (N, 0, 100), "range_15m_pct": (N, 0, 100), "ret_5m_pct": (N, -100, 100),
    "turn_surge_5m": (N, 0, 10000), "chg_24h_pct": (N, -100, 10000), "spread_bps": (N, 0, 10000), "funding_pct": (N, -100, 100),
    "stop_pct": (N, 0, 100), "rr_planned": (N, 0, 1000), "size_mult": (N, 0, 10), "utc_hour": (I, 0, 23), "utc_session": (E_, SESSIONS),
    "dow": (I, 0, 6), "confirmed": (B,), "confirm_kind": (E_, CONFIRM_KINDS), "no_confirm_tag": (B,), "blackout": (B,), "fng": (I, 0, 100),
    "dvol": (N, 0, 1000), "risk_off": (B,),
}
NUM_FEATURES = tuple(k for k, v in SITUATION.items() if v[0] in (N, I) and k not in ("utc_hour", "dow"))
DECISION = {"action": (E_, ACTIONS), "why": (S, 200), "conf": (N, 0, 1), "stop_dist_vs_range5": (N, 0, 1000), "skip_code": (E_, SKIP_CODES)}
SRC = {"risk_level": (I, 0, 9), "actor": (E_, ACTORS), "agent_id": (S, 16), "model": (S, 40), "role": (E_, ROLES), "exp": (S, 40), "var": (S, 8),
       "rules_ver": (S, 20), "mode": (E_, MODES), "mem": (E_, MEM), "rulebook_ver": (S, 8)}      # rulebook_ver (R2): версия rulebook R-NNNN, необязательно
RESULT = {"status": (E_, STATUSES), "r": (N, -100, 1000), "pnl_r_net": (N, -100, 1000), "mae_r": (N, -100, 1000), "mfe_r": (N, -100, 1000),
          "hold_min": (N, 0, 10 ** 7), "exit_reason": (E_, EXIT_REASONS), "fees_r": (N, -100, 1000)}
WHATIF = {"h_min": (I, 1, 10 ** 5), "dir_pct": (N, -10 ** 4, 10 ** 4), "ret_pct": (N, -10 ** 4, 10 ** 4), "cost_pct": (N, 0, 100),
          "net_dir_pct": (N, -10 ** 4, 10 ** 4), "src": (S, 20), "outcome_id": (S, 48)}
REFS = {"journal_id": (S, 48), "idea": (S, 48), "plan": (S, 48), "brief_id": (S, 48), "event_seq": (I, 0, 10 ** 12), "outcome_id": (S, 48)}
ROUND = {"spread_bps": 1, "r": 2, "pnl_r_net": 2, "mae_r": 2, "mfe_r": 2, "fees_r": 3, "hold_min": 1, "conf": 2}
UPDATE_KEYS = ("result", "whatif", "review", "tags", "flags", "exclude_from_stats", "refs")
HEAD_KEYS = ("schema", "seq", "case_id", "rev", "kind", "ts", "prev", "h")
NEW_KEYS = HEAD_KEYS + ("type", "refs", "src", "situation", "decision", "result", "whatif", "review", "tags", "flags", "exclude_from_stats")
SITUATION_EXTRA = ("missing", "retro")


def cases_path(root=None):
    return paths.state("cases.jsonl", root=root)


def cases_lock(root=None):
    return paths.state("cases.lock", root=root)


def iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if t is None else t))


def parse_ts(s):
    try:
        return float(calendar.timegm(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S")))
    except (ValueError, TypeError):
        return None


def case_id_for(ts, sym, key):
    """Детерминированный case_id: C-<YYYYMMDD>-<SYM>-<хеш источника[:6]> (повторная миграция не плодит дубли)."""
    day = str(ts or "")[:10].replace("-", "")
    if not re.fullmatch(r"\d{8}", day):
        day = "00000000"
    return "C-%s-%s-%s" % (day, sym, hashlib.sha1(str(key).encode("utf-8")).hexdigest()[:6])


def _field(spec, name, v, errs, where):
    """Проверка одного значения по спецификации; -> нормализованное значение (None допустим всегда)."""
    if v is None:
        return None
    kind = spec[0]
    if kind == E_:
        if v not in spec[1]:
            errs.append("%s.%s: %r не из %s" % (where, name, v, list(spec[1])))
        return v
    if kind == B:
        if not isinstance(v, bool):
            errs.append("%s.%s: нужен bool" % (where, name))
        return v
    if kind == S:
        if not isinstance(v, str):
            errs.append("%s.%s: нужна строка" % (where, name))
            return None
        t, sus = privacy.safe_text(v, spec[1])
        return t
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) == float("inf"):
        errs.append("%s.%s: нужно число" % (where, name))
        return None
    if kind == I:
        if int(v) != v:
            errs.append("%s.%s: нужно целое" % (where, name))
        v = int(v)
    else:
        v = round(float(v), ROUND.get(name, 2))
    if not (spec[1] <= v <= spec[2]):
        errs.append("%s.%s: %r вне [%s, %s]" % (where, name, v, spec[1], spec[2]))
    return v


def _block(d, table, where, errs, extra=(), required=()):
    if d is None:
        return None
    if not isinstance(d, dict):
        errs.append("%s: нужен объект" % where)
        return None
    out = {}
    for k, v in d.items():
        if k in extra:
            continue
        if k not in table:
            errs.append("%s: неизвестный ключ %r" % (where, k))
            continue
        out[k] = _field(table[k], k, v, errs, where)
    for k in required:
        if out.get(k) is None:
            errs.append("%s.%s обязателен" % (where, k))
    return dict((k, v) for k, v in out.items() if v is not None)


def _flags(v, errs):
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > 8 or any(x not in FLAGS for x in v):
        errs.append("flags: список из %s" % list(FLAGS))
        return []
    return sorted(set(v))


def _tags(t, errs):
    if t is None:
        return None
    if not isinstance(t, dict):
        errs.append("tags: нужен объект")
        return None
    bad = [k for k in t if k not in ("err", "rule_followed")]
    if bad:
        errs.append("tags: неизвестные ключи %s" % bad)
    err = t.get("err") or []
    if not isinstance(err, list) or len(err) > 5 or any(x not in ERR_TAGS for x in err):
        errs.append("tags.err: до 5 тегов из ERR_TAGS")
        err = []
    rf = t.get("rule_followed")
    if rf is not None and rf not in RULE_FOLLOWED:
        errs.append("tags.rule_followed: %r не из %s" % (rf, list(RULE_FOLLOWED)))
    return {"err": list(dict.fromkeys(err)), "rule_followed": rf}


def _situation(sit, errs):
    out = _block(sit, SITUATION, "situation", errs, extra=SITUATION_EXTRA)
    if out is None:
        return None
    for name in SITUATION_EXTRA:
        v = sit.get(name)
        if v is None:
            continue
        if not isinstance(v, list) or len(v) > 40 or any(x not in SITUATION for x in v):
            errs.append("situation.%s: список имён признаков" % name)
        else:
            out[name] = sorted(set(v))
    if out.get("sym") is not None and not RE_SYM.match(out["sym"]):
        errs.append("situation.sym: не соответствует ^[A-Z0-9]{2,20}$")
    return out


def validate(rec, update=False):
    """Проверяет случай/update-строку (без seq/prev/h) -> (нормализованная запись, [ошибки]). Неизвестные ключи запрещены."""
    errs = []
    if not isinstance(rec, dict):
        return None, ["случай: нужен объект"]
    allowed = set(NEW_KEYS)
    for k in rec:
        if k not in allowed:
            errs.append("неизвестный ключ %r" % k)
    if update:
        bad = [k for k in rec if k not in UPDATE_KEYS and k not in HEAD_KEYS + ("type",) and k in NEW_KEYS]
        if bad:
            errs.append("update не может менять %s (situation/decision/src фиксируются при создании)" % sorted(bad))
    out = {"schema": SCHEMA}
    cid = rec.get("case_id")
    if not isinstance(cid, str) or not RE_CASE_ID.match(cid):
        errs.append("case_id: C-YYYYMMDD-SYM-xxxx")
    out["case_id"] = cid
    out["kind"] = "update" if update else "new"
    if not update:
        if rec.get("type") not in TYPES:
            errs.append("type: %r не из %s" % (rec.get("type"), list(TYPES)))
        out["type"] = rec.get("type")
        out["ts"] = rec.get("ts") if parse_ts(rec.get("ts")) is not None else None
        if out["ts"] is None:
            errs.append("ts: ISO UTC")
        out["src"] = _block(rec.get("src") or {}, SRC, "src", errs) or {}
        out["situation"] = _situation(rec.get("situation"), errs)
        if not out["situation"] or not out["situation"].get("sym") or out["situation"].get("side") is None:
            errs.append("situation.sym и situation.side обязательны")
        out["decision"] = _block(rec.get("decision") or {}, DECISION, "decision", errs, required=("action",))
    refs = rec.get("refs")
    if refs is not None:
        out["refs"] = _block(refs, REFS, "refs", errs)
        out["refs"] = {k: v for k, v in out["refs"].items() if v is not None} if out["refs"] else {}
        for k, v in out["refs"].items():
            if isinstance(v, str) and not RE_ID.match(v):
                errs.append("refs.%s: недопустимые символы" % k)
    elif not update:
        out["refs"] = {}
    if rec.get("result") is not None or not update:
        out["result"] = _block(rec.get("result") or {"status": "na"}, RESULT, "result", errs, required=("status",))
    if rec.get("whatif") is not None:
        out["whatif"] = _block(rec["whatif"], WHATIF, "whatif", errs)
    elif not update:
        out["whatif"] = None
    if rec.get("review") is not None:
        out["review"] = rec["review"]       # схему проверяет review.validate (структура); тут лишь размер и приватность
        if not isinstance(out["review"], dict):
            errs.append("review: нужен объект")
    elif not update:
        out["review"] = None
    if rec.get("tags") is not None or not update:
        out["tags"] = _tags(rec.get("tags") or {"err": [], "rule_followed": None}, errs)
    if rec.get("flags") is not None or not update:
        out["flags"] = _flags(rec.get("flags"), errs)
    if rec.get("exclude_from_stats") is not None:
        if not isinstance(rec["exclude_from_stats"], bool):
            errs.append("exclude_from_stats: bool")
        out["exclude_from_stats"] = rec["exclude_from_stats"]
    elif not update:
        out["exclude_from_stats"] = False
    if privacy.REDACTED in json.dumps(out, ensure_ascii=False) and "inj_suspect" not in (out.get("flags") or []):
        out["flags"] = sorted(set((out.get("flags") or []) + ["inj_suspect"]))
    errs.extend("privacy: " + x for x in privacy.privacy_scan(out))
    return (None if errs else out), errs


class CaseError(ValueError):
    pass


# ---------------------------------------------------------------- хранилище

HEAD_BYTES = 4096


class _Cache:
    """Состояние чтения одного cases.jsonl: целые строки до offset, свёртка, множество case_id. Дочитывает только хвост (см. _state)."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.gen = None
        self.size = 0
        self.mtime = None
        self.head = b""
        self.offset = 0
        self.rows = []
        self.broken = 0
        self.folded = {}
        self.ids = set()
        self.sorted = None


_CACHES = {}
_CACHE_LOCK = threading.Lock()


def _fold_row(out, r):
    cid = r.get("case_id")
    if r.get("kind") == "new" and cid not in out:
        c = dict(r)
        c["ts_upd"] = c.get("ts")
        out[cid] = c
    elif r.get("kind") == "update" and cid in out:
        c = out[cid]
        for k in UPDATE_KEYS:
            if k in r:
                c[k] = dict(c.get(k) or {}, **r[k]) if k == "refs" else r[k]
        c["rev"] = max(c.get("rev", 1), r.get("rev", 1))
        c["ts_upd"] = r.get("ts") or c.get("ts_upd")


def fold(rows):
    """Строки -> {case_id: случай}: база = строка new, поля из update замещают блоки; rev = последняя; ts_upd = ts последнего update."""
    out = {}
    for r in rows:
        _fold_row(out, r)
    return out


def _state(root=None):
    """Актуальный кеш файла. Размер/первые 4 КБ/mtime не сходятся с сохранёнными -> полное перечитывание; иначе дочитывается хвост от offset.
    Неполная последняя строка (писатель ещё пишет) в кеш не попадает: тогда результат считается полным чтением без сохранения. Вызывать под _CACHE_LOCK.
    store="sqlite": строки таблицы case_rows (дочитывается хвост того же поколения снимка)."""
    if _store(root) == "sqlite":
        return _state_db(root)
    path = cases_path(root)
    key = os.path.abspath(path)
    ca = _CACHES.get(key)
    if ca is None:
        ca = _CACHES[key] = _Cache()
    try:
        st = os.stat(path)
        size, mtime = st.st_size, st.st_mtime_ns
    except OSError:
        ca.reset()
        return ca
    if size < ca.size or (size == ca.size and mtime != ca.mtime):
        ca.reset()
    try:
        with open(path, "rb") as f:
            head = f.read(HEAD_BYTES)
            if not head.startswith(ca.head):
                ca.reset()
            if size == ca.size and mtime == ca.mtime and ca.offset == size:
                ca.head = head
                return ca
            f.seek(ca.offset)
            start = ca.offset
            data = f.read()
    except OSError:
        ca.reset()
        return ca
    end = data.rfind(b"\n") + 1
    rows, broken = [], 0
    for ln in data[:end].split(b"\n"):
        if not ln.strip():
            continue
        r = chain.parse(ln)
        if r is None:
            broken += 1
        else:
            rows.append(r)
    if end != len(data):          # неполная последняя строка: не кешируем её, отдаём временное состояние
        tail = chain.parse(data[end:])
        tmp = _Cache()
        tmp.rows, tmp.broken = ca.rows + rows + ([tail] if tail else []), ca.broken + broken + (0 if tail else 1)
        tmp.folded = fold(tmp.rows)
        tmp.ids = set(cid for cid, c in tmp.folded.items())
        _commit(ca, rows, broken, start + end, head, size, mtime, partial=True)
        return tmp
    _commit(ca, rows, broken, start + end, head, size, mtime)
    return ca


def _store(root):
    from tb.knowledge import memstore
    return memstore.mode(root)


def _state_db(root):
    from tb.knowledge import memstore
    gen, rows = memstore.snapshot(root, "case_rows")
    key = "sqlite:" + os.path.abspath(paths.state("knowledge.sqlite", root=root))
    ca = _CACHES.get(key)
    if ca is None:
        ca = _CACHES[key] = _Cache()
    if ca.gen is not gen:
        ca.reset()
        ca.gen = gen
    new = rows[len(ca.rows):]
    for r in new:
        ca.rows.append(r)
        _fold_row(ca.folded, r)
        if r.get("kind") == "new":
            ca.ids.add(r.get("case_id"))
    if new:
        ca.sorted = None
    return ca


def _append(rec, root, check):
    """Запись строки: режим читается под cases.lock (перенос держит её же), поэтому запись не уйдёт в JSONL после переключения."""
    with FileLock(cases_lock(root), wait=chain.LOCK_WAIT_S):
        if _store(root) != "sqlite":
            return chain.append(cases_path(root), cases_lock(root), rec, check=check, max_bytes=MAX_BYTES)
        from tb.knowledge import memstore
        if check is not None:
            check()
        return memstore.append(root, "case_rows", rec, max_bytes=MAX_BYTES)


def _commit(ca, rows, broken, offset, head, size, mtime, partial=False):
    for r in rows:
        ca.rows.append(r)
        _fold_row(ca.folded, r)
        if r.get("kind") == "new":
            ca.ids.add(r.get("case_id"))
    ca.broken += broken
    ca.offset, ca.head = offset, head
    ca.size, ca.mtime = (offset, None) if partial else (size, mtime)       # mtime=None: следующий вызов не примет кеш за актуальный
    if rows:
        ca.sorted = None


def read_raw(root=None):
    """-> (строки по порядку файла, число битых строк)."""
    with _CACHE_LOCK:
        ca = _state(root)
        return list(ca.rows), ca.broken


def load(root=None, copy=False):
    """-> список свёрнутых случаев (по возрастанию ts, затем case_id). Объекты общие с кешем процесса: изменять нельзя; кто меняет, берёт copy=True."""
    with _CACHE_LOCK:
        ca = _state(root)
        if ca.sorted is None:
            ca.sorted = sorted(ca.folded.values(), key=lambda c: (c.get("ts") or "", c["case_id"]))
        out = list(ca.sorted)
    return copy_mod.deepcopy(out) if copy else out


def get(case_id, root=None, rev=None):
    """Свёрнутый случай (копия: можно менять) или None; rev ограничивает версию."""
    with _CACHE_LOCK:
        ca = _state(root)
        if rev is not None:
            c = fold([r for r in ca.rows if r.get("case_id") == case_id and r.get("rev", 1) <= rev]).get(case_id)
        else:
            c = ca.folded.get(case_id)
    return copy_mod.deepcopy(c) if c is not None else None


def _exists(case_id, root):
    with _CACHE_LOCK:
        return case_id in _state(root).ids


def append_new(rec, root=None, now=None):
    """Валидирует и пишет новый случай -> записанная строка. CaseError при ошибках; дубль case_id -> CaseError('exists')."""
    rec = dict(rec)
    norm, errs = validate(rec)
    if errs:
        raise CaseError("; ".join(errs))
    norm["rev"] = 1

    def check():
        if _exists(norm["case_id"], root):
            raise CaseError("exists: %s" % norm["case_id"])
    try:
        row = _append(norm, root, check)
    except ValueError as e:
        if isinstance(e, CaseError):
            raise
        raise CaseError(str(e))
    _refresh(root)
    return row


def _refresh(root):
    with _CACHE_LOCK:
        _state(root)


def append_update(case_id, patch, root=None, ts=None):
    """Пишет update (rev+1) для существующего case_id. patch: подмножество UPDATE_KEYS. CaseError, если случая нет или поле запрещено."""
    rec = dict(patch, case_id=case_id)
    norm, errs = validate(rec, update=True)
    if errs:
        raise CaseError("; ".join(errs))
    os_rec = dict(norm)
    os_rec["ts"] = ts or iso()

    def check():
        c = get(case_id, root)
        if c is None:
            raise CaseError("нет случая %s" % case_id)
        os_rec["rev"] = c.get("rev", 1) + 1
    try:
        row = _append(os_rec, root, check)
    except ValueError as e:
        if isinstance(e, CaseError):
            raise
        raise CaseError(str(e))
    _refresh(root)
    return row


def verify(root=None):
    """Цепочка хешей + повторная проверка схемы и приватности всех свёрнутых случаев (store="sqlite": цепочка таблицы case_rows)."""
    if _store(root) == "sqlite":
        from tb.knowledge import memstore
        rep = memstore.verify_chain(root, "case_rows")
    else:
        rep = chain.verify(cases_path(root))
    rows, _ = read_raw(root)
    bad = []
    for c in fold(rows).values():
        errs = privacy.privacy_scan(c)
        if errs:
            bad.append({"case_id": c["case_id"], "privacy": errs[:3]})
    rep["privacy_bad"] = bad
    rep["ok"] = bool(rep["ok"] and not bad)
    return rep


def eligible(c, cfg=None, roles_excluded=None):
    """Случай участвует в статистике и уроках: не exclude_from_stats, не mem=off, без inj_suspect."""
    if c.get("exclude_from_stats") or (c.get("src") or {}).get("mem") == "off" or "inj_suspect" in (c.get("flags") or []):
        return False
    role = (c.get("src") or {}).get("role")
    ex = roles_excluded if roles_excluded is not None else ((cfg or {}).get("exclude_roles") or [])
    return role not in ex
