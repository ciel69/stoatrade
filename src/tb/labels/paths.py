"""Минутные пути прогонов и сделок мозга (R4, задачи 8 и 9; Requirements 1.1–1.7, 11.6, 18.4).

Кодирование пути (design.md «tb.labels.paths», ревизия 2026-11-04): OHLC разностями в тиках к закрытию ПРЕДЫДУЩЕЙ свечи (первая свеча — к
цене входа), zigzag-varint, zlib уровня 6; объём не хранится (ни один компонент меню первой итерации его не использует). Метки времени
минутных свечей хранятся разностью в минутах. `decode(encode(rows, ...))` повторяет OHLC с точностью до тика (свойство 11).

Запись:
  * `run_path_row(...)` собирает строку `run_paths` (состояние `partial`) для заполненного прогона; `label.label_one` пишет её в ТОЙ ЖЕ
    транзакции, что признаки, исходы и `policy_runs` (сбой записи пути откатывает всю разметку — свойство 11 R1);
  * `extend_step(...)` догружает свечи до конца окна `[entry_ts, entry_ts + H × max_hold_min + reserve_min]` после его закрытия (плюс
    `grace_min`) и переводит строку в `complete`;
  * `backfill(...)` (команда `./tb labels paths-backfill --days N`) дописывает пути уже размеченных прогонов полным окном сразу, не меняя
    `policy_runs`, признаки и исходы;
  * `trade_paths_step(...)` — пути сделок мозга: курсор `CURSOR_BUS_CLOSED` по событиям `position_closed` шины, ожидание окна после
    закрытия, загрузка свечей публичным REST, `qty`/`qty_step`/`min_qty` инструмента, части закрытия и хронология вмешательств;
  * `rotate(...)` удаляет пути старше `paths.keep_days`.

Слои `tb.labels`: модуль не импортирует `tb.core.bus`, `tb.core.journal`, `tb.executor`, `tb.venues` и т. д.; файлы шины, журнала сделок,
журнала решений, плана и кэша инструментов читаются как JSON/JSONL по путям `tb.paths`. Ключи не читаются, сеть — только через переданный
`fetch` (общий ограничитель сервиса меток). Модуль ничего не пишет вне переданного соединения и таблиц `run_paths`/`trade_paths`/`cursor`/`meta`.
"""
import datetime
import json
import math
import os
import zlib

from tb import paths as P
from tb.labels import klines, schema, store
from tb.logic import bars

CODEC_VER = 1
EXITS_FILE = "exits.json"
TRADE_WIN_BEFORE_S = 30 * 60          # окно пути сделки: [entry_ts − 30 мин, close_ts + post_close_min]
EXTEND_BATCH = 5                      # строк путей за один проход догрузки (проход не задерживает разметку)
TRADE_BATCH = 2                       # путей сделок за один проход: разметка меток не ждёт дольше 10% времени прохода
META_TRADE_MISSING = "trade_path_missing"
RUN_PATH_COLS = ("key", "policy_id", "policy_ver", "t0", "sym", "type", "side", "horizon", "entry_ts", "entry_px", "stop", "tick", "max_hold_min",
                 "atr5_pct", "plan", "rows", "n_rows", "end_ts", "state", "flags", "aux", "aux_ver", "labeled_ts", "complete_ts")
TRADE_PATH_COLS = ("journal_id", "plan_id", "sym", "side", "mode", "entry_ts", "entry_px", "stop0", "tick", "qty", "qty_step", "min_qty",
                   "horizon", "max_hold_min", "atr5_pct", "sig_type", "exit_ver", "policy_h", "close_ts", "r", "parts", "timeline", "rows", "end_ts",
                   "flags", "fetched_ts")


# ---------------------------------------------------------------- кодирование пути

def _uvarint(n, out):
    n = int(n)
    if n < 0:
        raise ValueError("uvarint: отрицательное число")
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return


def _zig(n, out):
    """Zigzag-varint знакового числа (стандартная схема для 64-битных целых)."""
    n = int(n)
    _uvarint((n << 1) ^ (n >> 63), out)


def _read_uvarint(buf, i):
    shift = n = 0
    while True:
        if i >= len(buf):
            raise ValueError("путь: обрыв varint")
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n, i
        shift += 7
        if shift > 70:
            raise ValueError("путь: varint слишком длинный")


def _read_zig(buf, i):
    u, i = _read_uvarint(buf, i)
    return (u >> 1) ^ -(u & 1), i


def encode(rows, tick, entry_px):
    """Минутные свечи -> сжатый блоб. rows: [(open_ts_s, o, h, l, c, v?)]; цены — разностями в тиках к закрытию предыдущей свечи.

    Первая свеча кодируется от `entry_px`. Кодировщик считает опорную цену так же, как декодер (round к тику), поэтому ошибка округления
    не накапливается: `decode` повторяет OHLC с точностью до тика. Объём не кодируется.
    """
    tick = float(tick)
    if not (tick > 0):
        raise ValueError("encode: tick должен быть > 0")
    rs = sorted((tuple(r) for r in (rows or [])), key=lambda r: int(r[0]))
    out = bytearray([CODEC_VER])
    if not rs:
        _uvarint(0, out)
        _uvarint(0, out)
        return zlib.compress(bytes(out), 6)
    ts0 = int(rs[0][0])
    _uvarint(ts0, out)
    _uvarint(len(rs), out)
    prev_ts = ts0
    prev_px = float(entry_px or 0.0)
    for r in rs:
        ts = int(r[0])
        _uvarint(max(0, (ts - prev_ts) // 60), out)
        prev_ts = ts
        q = [int(round((float(r[k]) - prev_px) / tick)) for k in (1, 2, 3, 4)]
        for x in q:
            _zig(x, out)
        prev_px = prev_px + q[3] * tick          # та же арифметика, что в decode
    return zlib.compress(bytes(out), 6)


def decode(blob, tick, entry_px):
    """Сжатый блоб -> [(open_ts_s, o, h, l, c)] (объём не хранится). Некорректный блоб -> ValueError."""
    tick = float(tick)
    if not (tick > 0):
        raise ValueError("decode: tick должен быть > 0")
    try:
        raw = zlib.decompress(bytes(blob or b""))
    except zlib.error as e:
        raise ValueError("путь: не распаковался (%s)" % e)
    if not raw or raw[0] != CODEC_VER:
        raise ValueError("путь: неизвестная версия кодека")
    i = 1
    ts, i = _read_uvarint(raw, i)
    n, i = _read_uvarint(raw, i)
    prev_px = float(entry_px or 0.0)
    out = []
    for _ in range(n):
        dm, i = _read_uvarint(raw, i)
        ts += dm * 60
        vals = []
        for _k in range(4):
            q, i = _read_zig(raw, i)
            vals.append(prev_px + q * tick)
        prev_px = vals[3]
        out.append((ts, vals[0], vals[1], vals[2], vals[3]))
    return out


def merge_rows(old, new):
    """Слияние двух наборов свечей по open_ts (новые перекрывают старые), по возрастанию времени."""
    by = {}
    for r in (old or []):
        by[int(r[0])] = tuple(r)
    for r in (new or []):
        by[int(r[0])] = tuple(r)
    return [by[k] for k in sorted(by)]


# ---------------------------------------------------------------- конфиг и вспомогательное

def _jd(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _num(v, default=None):
    if isinstance(v, bool) or v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def load_cfg(root=None):
    """config/exits.json целиком (dict) или None: нет файла / битый JSON / не объект. Значения не проверяются (это делает exits_conf)."""
    try:
        with open(P.config(EXITS_FILE, root=root), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def paths_cfg(cfg):
    """Полный конфиг выходов или его часть `paths` -> словарь путей (None, если путей нет)."""
    if not isinstance(cfg, dict):
        return None
    p = cfg.get("paths") if isinstance(cfg.get("paths"), dict) else (cfg if "policies" in cfg else None)
    return p if isinstance(p, dict) else None


def _policies_ok(v):
    return isinstance(v, list) and bool(v) and all(isinstance(x, str) and x for x in v)


def enabled(cfg):
    """Цикл выходов включён и в paths.policies есть хотя бы одна политика (список непустых строк)."""
    if not isinstance(cfg, dict):
        return False
    p = paths_cfg(cfg)
    return bool(cfg.get("enabled", True)) and bool(p and _policies_ok(p.get("policies")))


def cfg_float(p, key, default=None):
    v = _num((p or {}).get(key))
    return v if v is not None else default


def grace_s(root=None):
    """grace_min разметки меток (config/labels.json, умолчание 10) в секундах."""
    try:
        from tb.labels import capture
        return float(capture.load_cfg(root).get("grace_min", 10)) * 60.0
    except (ValueError, TypeError):
        return 10 * 60.0


def is_gap(rows):
    """Есть ли в путях пропуск минутных свечей длиннее bars.GAP_MAX_MIN."""
    return bool(bars.gaps(rows))


def _flags(*names):
    f = [n for n in names if n]
    return ",".join(f) or None


def window_end(entry_ts, max_hold_min, hold_mult, reserve_min):
    """Конец окна пути: entry_ts + H × max_hold_min + reserve_min (секунды)."""
    return int(entry_ts) + int(round(float(hold_mult) * float(max_hold_min) * 60.0 + float(reserve_min) * 60.0))


# ---------------------------------------------------------------- путь прогона

def run_path_row(run, plan, res, r1, feat, hold_mult, reserve_min, now_s=None):
    """Строка `run_paths` (state=partial) для заполненного прогона или None.

    run: {key, policy_id, policy_ver, t0, sym, type}; plan и res — из `tb.logic.sim.run`; r1 — минутки, загруженные разметкой;
    feat — признаки метки (нужен `atr5_pct`). Окно пути: [entry_ts, entry_ts + H × max_hold_min + reserve_min]; в partial пишутся
    уже загруженные свечи от entry_ts, догрузка — `extend_step`. Пропуск свечей даёт флаг `gap` (путь вне оценок).
    """
    if not isinstance(res, dict) or not res.get("filled") or not isinstance(plan, dict):
        return None
    try:
        entry_ts = int(res.get("entry_ts"))
    except (TypeError, ValueError):
        return None
    entry_px = _num(res.get("entry_px"))
    tick = _num(plan.get("tick"))
    mh = _num(plan.get("max_hold_min"))
    if entry_px is None or entry_px <= 0 or tick is None or tick <= 0 or mh is None or mh <= 0:
        return None
    end_lim = window_end(entry_ts, mh, hold_mult, reserve_min)
    rows = [tuple(r) for r in sorted(r1 or [], key=lambda r: int(r[0])) if entry_ts <= int(r[0]) <= end_lim]
    if not rows:
        return None
    end_ts = int(rows[-1][0]) + 60
    plan_json = dict(plan, entry_px=entry_px)          # exitsim читает фактическую цену заполнения из plan["entry_px"]
    return {"key": run["key"], "policy_id": run["policy_id"], "policy_ver": int(run["policy_ver"]), "t0": int(run["t0"]),
            "sym": run["sym"], "type": run["type"], "side": plan.get("side"), "horizon": plan.get("horizon"),
            "entry_ts": entry_ts, "entry_px": entry_px, "stop": _num(plan.get("stop")), "tick": tick, "max_hold_min": mh,
            "atr5_pct": _num((feat or {}).get("atr5_pct")), "plan": _jd(plan_json), "rows": encode(rows, tick, entry_px), "n_rows": len(rows),
            "end_ts": end_ts, "state": schema.PATH_PARTIAL, "flags": _flags(schema.FLAG_GAP if is_gap(rows) else None), "aux": None, "aux_ver": None,
            "labeled_ts": int(now_s if now_s is not None else entry_ts), "complete_ts": None}


def insert_run_paths(conn, recs, replace=False):
    """Вставка строк run_paths (INSERT OR REPLACE при replace=True). Вызывать внутри транзакции. -> число строк."""
    cols = RUN_PATH_COLS
    sql = "INSERT OR %s INTO run_paths(%s) VALUES(%s)" % ("REPLACE" if replace else "IGNORE", ",".join(cols), ",".join("?" * len(cols)))
    n = 0
    for r in recs or []:
        conn.execute(sql, [r.get(c) for c in cols])
        n += 1
    return n


def insert_trade_path(conn, rec):
    cols = TRADE_PATH_COLS
    conn.execute("INSERT OR REPLACE INTO trade_paths(%s) VALUES(%s)" % (",".join(cols), ",".join("?" * len(cols))), [rec.get(c) for c in cols])


# ---------------------------------------------------------------- догрузка пути прогона

def _due(conn, limit):
    """Строки partial для догрузки по возрастанию end_ts -> list[dict]."""
    cols = RUN_PATH_COLS
    cur = conn.execute("SELECT %s FROM run_paths WHERE state=? ORDER BY end_ts LIMIT ?" % ",".join(cols),
                       (schema.PATH_PARTIAL, int(limit)))
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def extend_step(conn, root, cfg, fetch, now, batch=EXTEND_BATCH):
    """Догрузка partial-путей прогонов до конца окна и state=complete.

    Условие окна: `now >= entry_ts + H × max_hold_min + reserve_min + grace_min`. Свечи грузятся тем же `fetch`, что и разметка
    (общий ограничитель частоты); сбой REST оставляет строку partial (повтор на следующем проходе), 403/429 поднимается вызывающему
    как `klines.RateLimited`. -> {"due", "extended", "errors"}.
    """
    p = paths_cfg(cfg)
    out = {"due": 0, "extended": 0, "errors": 0}
    if not p or not _policies_ok(p.get("policies")):
        return out
    hold_mult = cfg_float(p, "hold_mult", 1.5)
    reserve_min = cfg_float(p, "reserve_min", 30.0)
    grace = grace_s(root)
    for row in _due(conn, batch):
        lim = window_end(row["entry_ts"], row["max_hold_min"], hold_mult, reserve_min)
        if now < lim + grace:
            continue
        out["due"] += 1
        try:
            fresh = fetch(row["sym"], "1", int(row["entry_ts"]), lim)
        except klines.RateLimited:
            raise
        except (klines.FetchError, OSError):
            out["errors"] += 1
            continue
        try:
            old = decode(row["rows"], row["tick"], row["entry_px"])
            rows = [r for r in merge_rows(old, fresh) if int(row["entry_ts"]) <= int(r[0]) <= lim]
            blob = encode(rows, row["tick"], row["entry_px"])
        except ValueError:
            out["errors"] += 1
            continue
        end_ts = int(rows[-1][0]) + 60 if rows else int(row["end_ts"])
        with store.tx(conn):
            conn.execute("UPDATE run_paths SET rows=?, n_rows=?, end_ts=?, state=?, flags=?, complete_ts=? WHERE key=? AND policy_id=? AND policy_ver=?",
                         (blob, len(rows), end_ts, schema.PATH_COMPLETE, _flags(schema.FLAG_GAP if is_gap(rows) else None), int(now),
                          row["key"], row["policy_id"], row["policy_ver"]))
        out["extended"] += 1
    return out


def rotate(conn, cfg, now):
    """Удаление путей старше paths.keep_days (Requirement 1.3). -> {"run", "trade"} число удалённых строк."""
    p = paths_cfg(cfg) or {}
    keep_days = cfg_float(p, "keep_days", 120.0)
    cut = int(now - keep_days * 86400.0)
    out = {"run": 0, "trade": 0}
    with store.tx(conn):
        c = conn.execute("DELETE FROM run_paths WHERE t0 < ?", (cut,))
        out["run"] = c.rowcount if c.rowcount and c.rowcount > 0 else 0
        c = conn.execute("DELETE FROM trade_paths WHERE entry_ts < ?", (cut,))
        out["trade"] = c.rowcount if c.rowcount and c.rowcount > 0 else 0
    return out


# ---------------------------------------------------------------- дописывание путей (paths-backfill)

def backfill(conn, root, days, fetch, now, limit=None, stop=None):
    """Дописать run_paths для уже размеченных заполненных прогонов за `days` суток, не меняя policy_runs/признаки/исходы.

    Свечи перезагружаются тем же `fetch` (ограничитель частоты сервиса; сбой REST оставляет прогон на следующий запуск), окно берётся
    целиком сразу: `[entry_ts, entry_ts + H × max_hold_min + reserve_min]`, state=complete. Уже существующие строки не меняются.
    -> {"scanned", "written", "skipped", "errors"}.
    """
    from tb.labels import label as label_mod
    from tb.labels import policies as pol_mod
    from tb.logic import sim as sim_mod

    p = paths_cfg(load_cfg(root))
    out = {"scanned": 0, "written": 0, "skipped": 0, "errors": 0}
    if not p or not _policies_ok(p.get("policies")):
        return out
    hold_mult = cfg_float(p, "hold_mult", 1.5)
    reserve_min = cfg_float(p, "reserve_min", 30.0)
    cutoff = int(now - float(days) * 86400.0)
    want = [str(x) for x in p["policies"]]
    try:
        from tb.labels import capture
        lab_cfg = capture.load_cfg(root)
    except (ValueError, TypeError):
        lab_cfg = {}
    sql = ("SELECT s.key, s.t0, s.sym, s.type, s.ev, s.feat, pr.policy_id, pr.policy_ver, pr.plan FROM signals s "
           "JOIN policy_runs pr ON pr.key=s.key WHERE s.status=? AND s.t0>=? AND pr.filled=1 AND pr.plan IS NOT NULL "
           "AND pr.policy_id IN (%s) ORDER BY s.t0 LIMIT ?" % ",".join("?" * len(want)))
    args = [schema.DONE, cutoff] + want + [int(limit) if limit is not None else -1]
    todo = conn.execute(sql, args).fetchall()
    for key, t0, sym, typ, evs, feats, pid, pver, plan_js in todo:
        if callable(stop) and stop():
            break
        out["scanned"] += 1
        if conn.execute("SELECT 1 FROM run_paths WHERE key=? AND policy_id=? AND policy_ver=?", (key, pid, int(pver))).fetchone():
            out["skipped"] += 1
            continue
        try:
            plan = json.loads(plan_js)
            ev = json.loads(evs) if evs else {}
            feat = json.loads(feats) if feats else {}
        except (TypeError, ValueError):
            out["errors"] += 1
            continue
        if not isinstance(plan, dict) or not plan.get("tick"):
            out["errors"] += 1
            continue
        mh = _num(plan.get("max_hold_min"))
        if mh is None or mh <= 0:
            out["errors"] += 1
            continue
        ttl = _num((plan.get("entry") or {}).get("ttl_min"), 60.0) or 60.0
        end = int(t0) + int(round((ttl + hold_mult * mh + reserve_min) * 60.0)) + 3600
        try:
            r1 = fetch(sym, "1", int(t0) - label_mod.WIN_BEFORE_S, end)
        except klines.RateLimited:
            raise
        except (klines.FetchError, OSError):
            out["errors"] += 1
            continue
        try:
            costs, manage = pol_mod.sim_inputs(root, lab_cfg, ev if isinstance(ev, dict) else {})
            res = sim_mod.run(plan, r1, int(t0), costs, manage)
        except (ValueError, TypeError):
            out["errors"] += 1
            continue
        run = {"key": key, "policy_id": pid, "policy_ver": int(pver), "t0": int(t0), "sym": sym, "type": typ}
        rec = run_path_row(run, plan, res, r1, feat if isinstance(feat, dict) else {}, hold_mult, reserve_min, now_s=int(t0))
        if rec is None:
            out["errors"] += 1
            continue
        rec["state"] = schema.PATH_COMPLETE
        rec["complete_ts"] = int(now)
        with store.tx(conn):
            insert_run_paths(conn, [rec])
        out["written"] += 1
    return out


# ---------------------------------------------------------------- журналы (читаются как JSON/JSONL: слои tb.labels)

def _iso_ts(s):
    """'2026-11-04T12:00:00Z' -> epoch или None."""
    if not isinstance(s, str) or not s:
        return None
    t = s.strip()
    if t.endswith("Z") or t.endswith("z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


def _jsonl(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    yield r
    except OSError:
        return


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def read_trades(root=None):
    """state/trades.jsonl -> {id: {"open", "qty0", "parts", "adjusts", "close", "remaining"}} (только целые записи).

    qty0 — размер позиции на входе/после доборов (не уменьшается частями); remaining — остаток; parts — фактические закрытия;
    adjusts — переносы стопа/тейка с полем `by`; close — запись закрытия.
    """
    out = {}
    for r in _jsonl(P.state("trades.jsonl", root=root)):
        ev, tid = r.get("ev"), r.get("id")
        if not isinstance(tid, str):
            continue
        if ev == "open":
            entry, notional = _num(r.get("entry")), _num(r.get("notional"))
            q0 = (notional / entry) if (entry and notional) else None
            out[tid] = {"open": dict(r), "qty0": q0, "remaining": q0, "parts": [], "adjusts": [], "close": None}
            continue
        t = out.get(tid)
        if t is None or t.get("close"):
            continue
        if ev == "add":
            q = _num(r.get("qty"))
            if q and q > 0:
                t["qty0"] = (t["qty0"] or 0.0) + q
                t["remaining"] = (t["remaining"] or 0.0) + q
        elif ev == "part":
            q = _num(r.get("qty"))
            if q and q > 0:
                t["parts"].append(dict(r))
                t["remaining"] = max(0.0, (t["remaining"] or 0.0) - q)
        elif ev == "adjust":
            t["adjusts"].append(dict(r))
        elif ev == "close":
            t["close"] = dict(r)
    return out


def _plan_files(root, plan_id):
    """(spec, rt) плана: живые state/plans/<id>/ или архивные _archive/YYYY-MM/<id>/."""
    if not isinstance(plan_id, str) or not plan_id:
        return None, None
    dirs = [P.plans(plan_id, root=root)]
    arch = P.plans("_archive", root=root)
    try:
        for m in sorted(os.listdir(arch)):
            p = os.path.join(arch, m, plan_id)
            if os.path.isdir(p):
                dirs.append(p)
    except OSError:
        pass
    for d in dirs:
        spec = _read_json(os.path.join(d, "spec.json"))
        rt = _read_json(os.path.join(d, "runtime.json"))
        if spec is not None or rt is not None:
            return spec, rt
    return None, None


def plan_files(root, plan_id):
    """(spec, rt) плана по id для читателей базы знаний (R4: цель `t1` в отчёте после закрытия); то же, что `_plan_files`."""
    return _plan_files(root, plan_id)


def _decisions(root, plan_id):
    """Записи журнала решений по плану (активный файл + закрытые сегменты decisions.d)."""
    out = []
    files = [P.decisions_file(root=root)]
    segdir = os.path.join(os.path.dirname(files[0]), "decisions.d")
    try:
        files.extend(os.path.join(segdir, n) for n in sorted(os.listdir(segdir)) if n.startswith("seg-") and n.endswith(".jsonl"))
    except OSError:
        pass
    for p in files:
        for r in _jsonl(p):
            if r.get("plan") == plan_id:
                out.append(r)
    out.sort(key=lambda x: (float(x.get("t") or 0.0), int(x.get("seq") or 0)))
    return out


def _instrument(root, sym):
    """Инструмент из runtime/instruments.json (кэш исполнителя, публичный REST) -> dict или None."""
    d = _read_json(P.runtime("instruments.json", root=root)) or {}
    syms = d.get("symbols") if isinstance(d.get("symbols"), dict) else {}
    if not syms:
        return None
    for cand in (sym, str(sym or "").upper(), "%sUSDT" % str(sym or "").upper()):
        v = syms.get(cand)
        if isinstance(v, dict):
            return v
    return None


def _tick_fallback(px):
    """Шаг цены, если инструмента нет (то же приближение, что tb.labels.policies.tick_for)."""
    try:
        return float("1e%d" % math.floor(math.log10(float(px) * 1e-5)))
    except (ValueError, TypeError):
        return 0.01


def _bus_files(root):
    d = P.bus(root=root)
    try:
        names = sorted(n for n in os.listdir(d) if n.startswith("events-") and n.endswith(".jsonl"))
    except OSError:
        names = []
    cur = "events.jsonl"
    if os.path.isfile(os.path.join(d, cur)):
        names.append(cur)
    return [os.path.join(d, n) for n in names]


def bus_events(root=None, after_seq=0, typ="position_closed"):
    """События шины типа `typ` с seq > after_seq по возрастанию seq (читает ротированные файлы и активный)."""
    got = {}
    for p in _bus_files(root):
        for r in _jsonl(p):
            s = r.get("seq")
            if isinstance(s, int) and not isinstance(s, bool) and s > int(after_seq) and r.get("type") == typ:
                got.setdefault(s, r)
    return [got[k] for k in sorted(got)]


def _cursor_get(conn, src):
    row = conn.execute("SELECT offset FROM cursor WHERE src=?", (src,)).fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def _cursor_set(conn, src, path, seq, now):
    sha = conn.execute("SELECT head_sha FROM cursor WHERE src=?", (src,)).fetchone()
    conn.execute("INSERT OR REPLACE INTO cursor(src, path, offset, head_sha, ts) VALUES(?,?,?,?,?)",
                 (src, path, int(seq), (sha[0] if sha else None), int(now)))


def _bump(conn, key, n=1):
    """Счётчик в meta (trade_path_missing): целое значение, увеличивается на n."""
    row = conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    try:
        cur = int(row[0]) if row and row[0] is not None else 0
    except (TypeError, ValueError):
        cur = 0
    conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?,?)", (key, str(cur + int(n))))
    return cur + int(n)


def trade_missing(conn):
    """Значение счётчика trade_path_missing (0, если не было)."""
    row = conn.execute("SELECT v FROM meta WHERE k=?", (META_TRADE_MISSING,)).fetchone()
    try:
        return int(row[0]) if row and row[0] is not None else 0
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------- путь сделки мозга

def _parts_from_rt(rt, qty0):
    """Части закрытия из rt.realized.parts -> [{ts, px, qty, frac, fee, stop_type}] (снимок плана)."""
    out = []
    real = (rt or {}).get("realized") if isinstance(rt, dict) else None
    for p in ((real or {}).get("parts") or []):
        if not isinstance(p, dict):
            continue
        q = _num(p.get("qty"))
        px = _num(p.get("px"))
        if q is None or px is None:
            continue
        out.append({"ts": _num(p.get("ts")), "px": px, "qty": q, "frac": round(q / qty0, 8) if qty0 else None,
                    "fee": _num(p.get("fee")), "stop_type": p.get("stop_type") if isinstance(p.get("stop_type"), str) else None})
    return out


def _parts_from_journal(trade, qty0):
    """Запасной источник частей: события part журнала сделок."""
    out = []
    for p in trade.get("parts") or []:
        q, px = _num(p.get("qty")), _num(p.get("px"))
        if q is None or px is None:
            continue
        out.append({"ts": _num(_iso_ts(p.get("ts"))), "px": px, "qty": q, "frac": round(q / qty0, 8) if qty0 else None,
                    "fee": _num(p.get("fee")), "stop_type": None})
    return out


def _why(d):
    w = (d or {}).get("why")
    return w if isinstance(w, str) and w else "brain"


def _timeline(trade, decisions, plan_id):
    """Хронология вмешательств мозга в формате `tb.logic.exitsim`: [{"ts", "kind", "stop"|"price"|"frac", "why"}],
    kind ∈ {move_stop, close, partial_close}, по возрастанию `ts`.

    Перенос стопа берётся из события `adjust` с `by="cmd"` (фактическое действие), закрытия — из записи исполнителя
    `kind="rule"`, `why="CMD:..."` (`what` close/partial_close), цена и доля — из фактических частей журнала. Действия
    политики в хронологию не входят: exitsim применяет их сам как правила варианта.
    """
    qty0 = _num(trade.get("qty0")) or 0.0
    out = []
    for a in trade.get("adjusts") or []:
        if a.get("by") != "cmd":
            continue
        ts, st = _iso_ts(a.get("ts")), _num(a.get("sl"))
        if ts is None or st is None:
            continue
        out.append({"ts": ts, "kind": "move_stop", "stop": st, "why": "brain"})
    parts = sorted((p for p in (trade.get("parts") or []) if _iso_ts(p.get("ts")) is not None),
                   key=lambda p: _iso_ts(p.get("ts")))
    fracs, left = [], qty0
    for p in parts:
        q = _num(p.get("qty")) or 0.0
        fracs.append(q / left if left > 0 else None)
        left = max(0.0, left - q)
    used, close_used = set(), False
    close_rec = trade.get("close") or {}
    close_ts, close_px = _iso_ts(close_rec.get("ts_close")), _num(close_rec.get("exit"))
    cmds = [d for d in decisions if d.get("kind") == "rule" and str(d.get("why") or "").startswith("CMD:")
            and d.get("what") in ("close", "partial_close")]
    if not cmds:
        cmds = [d for d in decisions if d.get("kind") == "cmd" and d.get("what") == "close"]
    for d in cmds:
        ts = _num(d.get("t"))
        if ts is None:
            continue
        if not close_used and close_ts is not None and close_px is not None and ts - 1 <= close_ts <= ts + 600:
            out.append({"ts": ts, "kind": "close", "price": close_px, "frac": 1.0, "why": _why(d)})
            close_used = True
            continue
        best = None
        for i, p in enumerate(parts):
            if i in used:
                continue
            pts = _iso_ts(p.get("ts"))
            if ts - 1 <= pts <= ts + 600 and (best is None or pts < best[0]):
                best = (pts, i, p)
        if best is not None:
            _pts, i, p = best
            used.add(i)
            out.append({"ts": ts, "kind": "partial_close", "price": _num(p.get("px")), "frac": fracs[i], "why": _why(d)})
            continue
        q = _num((d.get("args") or {}).get("qty"))
        if q and qty0 > 0:
            out.append({"ts": ts, "kind": "partial_close", "price": None, "frac": q / qty0, "why": _why(d)})
    out.sort(key=lambda x: float(x.get("ts") or 0.0))
    return out


def _adopted(decisions):
    return any(d.get("kind") == "cmd" and d.get("what") == "adopt" for d in decisions)


def trade_path_row(trade, ev, root, cfg, fetch, now):
    """Собрать строку trade_paths по сделке журнала и событию шины. Исключения: FetchError/RateLimited пробрасываются, ValueError — нет данных."""
    p = paths_cfg(cfg) or {}
    post_close = cfg_float(p, "post_close_min", 240.0)
    jid = trade["open"].get("id")
    close = trade.get("close") or {}
    close_ts = _iso_ts(close.get("ts_close"))
    if close_ts is None:
        close_ts = _num(_iso_ts(ev.get("ts")))
    entry_ts = _iso_ts(trade["open"].get("ts_open"))
    entry_px = _num(trade["open"].get("entry"))
    stop0 = _num(trade["open"].get("sl"))
    if close_ts is None or entry_ts is None or not entry_px or stop0 is None:
        raise ValueError("нет полей сделки")
    sym = trade["open"].get("sym") or ev.get("sym")
    if not isinstance(sym, str) or not sym:
        raise ValueError("нет символа")
    inst = _instrument(root, sym)
    tick = _num((inst or {}).get("tick")) or _tick_fallback(entry_px)
    qty0 = _num(trade.get("qty0")) or (entry_px and (_num(trade["open"].get("notional")) or 0.0) / entry_px)
    if not qty0 or qty0 <= 0:
        raise ValueError("нет размера позиции")
    qty_step = _num((inst or {}).get("qty_step"))
    min_qty = _num((inst or {}).get("min_qty"))
    if qty_step is None or qty_step <= 0 or min_qty is None:
        raise ValueError("нет лота инструмента")
    plan_id = trade["open"].get("plan_id") if isinstance(trade["open"].get("plan_id"), str) else (ev.get("plan") or None)
    spec, rt = _plan_files(root, plan_id)
    ex = ((rt or {}).get("pos") or {}).get("exit") if isinstance(rt, dict) else None
    ex = ex if isinstance(ex, dict) else {}
    dec = _decisions(root, plan_id) if plan_id else []
    parts = _parts_from_rt(rt, qty0) or _parts_from_journal(trade, qty0)
    start = int(entry_ts) - TRADE_WIN_BEFORE_S
    end = int(close_ts + post_close * 60.0)
    rows = [tuple(r) for r in sorted(fetch(sym, "1", start, end) or [], key=lambda r: int(r[0])) if start <= int(r[0]) <= end]
    if not rows:
        raise ValueError("нет свечей")
    r_val = _num((ev.get("d") or {}).get("r")) if isinstance(ev.get("d"), dict) else None
    if r_val is None:
        r_val = _num(close.get("r"))
    lifecycle = (spec or {}).get("lifecycle") if isinstance(spec, dict) else None
    source = (spec or {}).get("source") if isinstance(spec, dict) else None
    rec = {"journal_id": jid, "plan_id": plan_id, "sym": sym, "side": trade["open"].get("side"), "mode": trade["open"].get("mode") or "paper",
           "entry_ts": int(entry_ts), "entry_px": entry_px, "stop0": stop0, "tick": tick, "qty": qty0, "qty_step": qty_step, "min_qty": min_qty,
           "horizon": ex.get("horizon") if isinstance(ex.get("horizon"), str) else ((spec or {}).get("session_type") if isinstance(spec, dict) else None),
           "max_hold_min": _num(ex.get("max_hold_min")) if ex else _num((lifecycle or {}).get("max_hold_min")),
           "atr5_pct": _num(ex.get("atr5_pct")),
           "sig_type": ex.get("sig_type") if isinstance(ex.get("sig_type"), str) else ((source or {}).get("kind") if isinstance(source, dict) else None),
           "exit_ver": ex.get("ver") if isinstance(ex.get("ver"), str) else None,
           "policy_h": ex.get("policy_h") if isinstance(ex.get("policy_h"), str) else None,
           "close_ts": int(close_ts), "r": r_val, "parts": _jd(parts), "timeline": _jd(_timeline(trade, dec, plan_id)),
           "rows": encode(rows, tick, entry_px), "end_ts": int(rows[-1][0]) + 60,
           "flags": _flags(schema.FLAG_ADOPTED if _adopted(dec) else None, schema.FLAG_GAP if is_gap(rows) else None),
           "fetched_ts": int(now)}
    return rec


def trade_paths_step(conn, root, cfg, fetch, now):
    """Шаг путей сделок мозга (Requirement 1.2, 1.6, 1.7).

    Курсор `CURSOR_BUS_CLOSED` (таблица cursor, offset = seq обработанного события) идёт по событиям `position_closed`; для каждого
    ждёт окно `close_ts + paths.post_close_min + grace_min`, затем грузит свечи `[entry_ts − 30 мин, close_ts + post_close_min]` тем же
    `fetch`. Повторы после сбоя REST — до `paths.trade_retry_h`, затем строка не пишется и растёт счётчик `trade_path_missing`.
    Первое событие, которое ещё нельзя обработать, останавливает проход (курсор не перескакивает); 403/429 поднимается вызывающему.
    -> {"cursor", "written", "waiting", "missing", "errors"}.
    """
    p = paths_cfg(cfg)
    out = {"cursor": 0, "written": 0, "waiting": 0, "missing": 0, "errors": 0}
    if not p or not _policies_ok(p.get("policies")):
        return out
    post_close = cfg_float(p, "post_close_min", 240.0) * 60.0
    retry_h = cfg_float(p, "trade_retry_h", 24.0) * 3600.0
    grace = grace_s(root)
    cur = _cursor_get(conn, schema.CURSOR_BUS_CLOSED)
    path = "bus"                       # соглашение курсора: offset = seq события шины (тест схемы v2)
    if conn.execute("SELECT 1 FROM cursor WHERE src=?", (schema.CURSOR_BUS_CLOSED,)).fetchone() is None:
        with store.tx(conn):
            _cursor_set(conn, schema.CURSOR_BUS_CLOSED, path, cur, now)
    trades = None
    for ev in bus_events(root, after_seq=cur):
        jid = (ev.get("d") or {}).get("journal_id") if isinstance(ev.get("d"), dict) else None
        if not jid:
            cur = int(ev["seq"])
            with store.tx(conn):
                _cursor_set(conn, schema.CURSOR_BUS_CLOSED, path, cur, now)
            continue
        if conn.execute("SELECT 1 FROM trade_paths WHERE journal_id=?", (jid,)).fetchone():
            cur = int(ev["seq"])
            with store.tx(conn):
                _cursor_set(conn, schema.CURSOR_BUS_CLOSED, path, cur, now)
            continue
        if trades is None:
            trades = read_trades(root)
        trade = trades.get(jid)
        close_ts = _iso_ts((trade or {}).get("close", {}).get("ts_close")) if trade else None
        if close_ts is None:
            close_ts = _num(_iso_ts(ev.get("ts")))
        if close_ts is None:
            cur = int(ev["seq"])
            with store.tx(conn):
                _cursor_set(conn, schema.CURSOR_BUS_CLOSED, path, cur, now)
            continue
        open_at = int(close_ts + post_close + grace)
        if now < open_at:
            out["waiting"] += 1
            break
        if now > open_at + retry_h:
            out["missing"] += 1
            with store.tx(conn):
                _bump(conn, META_TRADE_MISSING)
                cur = int(ev["seq"])
                _cursor_set(conn, schema.CURSOR_BUS_CLOSED, path, cur, now)
            continue
        try:
            rec = trade_path_row(trade, ev, root, cfg, fetch, now)
        except klines.RateLimited:
            raise
        except (klines.FetchError, OSError, ValueError, TypeError, KeyError):
            out["errors"] += 1
            break
        with store.tx(conn):
            insert_trade_path(conn, rec)
            cur = int(ev["seq"])
            _cursor_set(conn, schema.CURSOR_BUS_CLOSED, path, cur, now)
        out["written"] += 1
        if out["written"] >= TRADE_BATCH:
            break
    out["cursor"] = cur
    return out
