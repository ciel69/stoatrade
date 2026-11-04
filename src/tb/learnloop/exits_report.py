"""Путь после закрытия R4.2 (задача 20; Requirement 13, design.md «tb.learnloop.exits_*», «Ревизии»).

Справочный отчёт: что было с ценой ПОСЛЕ выхода. Геометрию (начальный стоп, тейки плана, правила входа) блок не меняет
(Requirement 13.5) и ничего не пишет: `after` работает на соединении читателя (`labels.store.open_ro`).

Источники (Requirement 13.1–13.2)
    * `trade_paths` — сделки мозга: окно `[entry_ts − 30 мин, close_ts + paths.post_close_min]` уже загружено шагом
      путей сделок (задача 9). Цена и время выхода — последняя фактическая часть закрытия (`parts`, по времени), причина
      выхода — её `stop_type` (`StopLoss`/`TakeProfit`/`xp_*`/пусто), иначе последнее закрытие мозга из `timeline`;
      цель `t1` — из `targets` спеки плана (`state/plans/<id>/spec.json`, архив `_archive/`), цель известна не всегда.
    * `run_paths` — прогоны теневых политик: база `X-0001` (пол защиты) проигрывается на пути тем же `exitsim.run`, что и
      оценки (порядок `S`); метрики считаются по остатку окна разметки после выхода базы (Requirement 13.2). Пути `gap`
      и не `complete` не берутся (Requirement 1.5).

Числа (Requirement 13.1)
    R-единица — `|entry_px − stop0|` пути; `after_mfe_r` — наибольший благоприятный ход после выхода ОТ ЦЕНЫ ВЫХОДА,
    `after_mfe_from_entry_r` — тот же экстремум ОТ ВХОДА (обе величины Requirement 13.1), `after_mae_r` — наибольший
    неблагоприятный ход после выхода от цены выхода. `after_t1` — 1/0, достигнута ли цель плана `t1` после выхода;
    `after_t1_min` — минуты от выхода до первой свечи, задевшей `t1`. Окно после выхода начинается со свечи, ОТКРЫВШЕЙСЯ
    не раньше времени выхода (свеча выхода целиком не берётся: неизвестно, какая её часть была после выхода) — оценка
    консервативная.

Отчёт (Requirement 13.3)
    `by_reason` по причинам `sl`, `tp`, `rule`, `manual`, `timeout` (+ `other`, если причину определить не удалось):
    `n` — число путей с известной целью `t1` (знаменатель доли), `n_rows` — всего путей причины, `after_t1_n`, `share`,
    95% интервал Уилсона (`wilson_lo`, `wilson_hi`), средние `after_mfe_r`/`after_mae_r`/`after_t1_min`, `unknown_t1`.

Память (Requirement 13.4)
    `case_fields(row)` — необязательные поля случая `tb.case/1` (`result.after_mfe_r`, `result.after_t1`); схема случая
    расширена только ими (`tb.memory.cases.RESULT`), `rules_ver` не меняется. Пишет их тот, кто закрывает случай (этот
    отчёт только читает базу).

Конфиг: `paths.post_close_min` берётся из `config/exits.json` (битый конфиг — окно по границе сохранённого пути и
примечание в отчёте, числовых умолчаний в коде нет, Requirement 16.1). Слои `tb.learnloop` соблюдены: нет мозга, шлюза,
биржи, исполнителя, управления, ключей и CLI; в `config/` ничего не пишется.
"""
import json
import math
import sqlite3
import time

from tb.labels import paths as LP
from tb.labels import policies as LPOL
from tb.labels import schema as LS
from tb.labels import store as LST
from tb.logic import exitsim
from tb.learnloop import exits_conf as EC

WILSON_Z = 1.96
REASONS = ("sl", "tp", "rule", "manual", "timeout")
OTHER = "other"
R_EPS = 1e-12
MODES = ("paper", "testnet", "live")
_SL_KINDS = ("sl", "stop", "stoploss", "partialstoploss", "trailingstop", "be", "trail")
_TP_KINDS = ("tp", "tp1", "tp2", "tp3", "target", "takeprofit", "partialtakeprofit")
NO_DB, RESTORING, DB_ERROR = "NO_DB", "RESTORING", "DB_ERROR"
REASONS_RU = {NO_DB: "базы знаний нет (state/knowledge.sqlite не создана)",
              RESTORING: "база знаний восстанавливается (runtime/knowledge.restoring): повторите после восстановления",
              DB_ERROR: "база знаний не читается"}


def _num(v, default=None):
    if isinstance(v, bool) or v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _mean(vals):
    return round(sum(vals) / len(vals), 4) if vals else None


def wilson(k, n, z=WILSON_Z):
    """95% интервал Уилсона для доли k/n -> (lo, hi); n <= 0 -> (None, None). Границы в [0, 1], округление 4 знака."""
    try:
        n = int(n)
        k = int(k)
    except (TypeError, ValueError):
        return (None, None)
    if n <= 0:
        return (None, None)
    k = min(max(k, 0), n)
    p = k / float(n)
    den = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / den
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / den
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def reason_group(raw):
    """Причина выхода -> группа отчёта (`sl`, `tp`, `rule`, `manual`, `timeout`) или None (неизвестна)."""
    if not isinstance(raw, str):
        return None
    r = raw.strip().lower()
    if not r:
        return None
    if r in _SL_KINDS:
        return "sl"
    if r in _TP_KINDS:
        return "tp"
    if r == "timeout":
        return "timeout"
    if r == "manual":
        return "manual"
    if r == "rule" or r.startswith(("xp_", "auto_")):
        return "rule"
    return None


def target_t1(plan):
    """Цель `t1` плана: `targets[id="t1"]`, иначе первый `targets` с ценой, иначе `tps[0]` плана прогона, иначе `plan["t1"]`."""
    if not isinstance(plan, dict):
        return None
    tg = plan.get("targets")
    if isinstance(tg, list):
        first = None
        for t in tg:
            if not isinstance(t, dict):
                continue
            px = _num(t.get("price"))
            if px is None or px <= 0:
                continue
            if t.get("id") == "t1":
                return px
            if first is None:
                first = px
        if first is not None:
            return first
    tps = plan.get("tps")
    if isinstance(tps, list):
        for x in tps:
            px = _num(x)
            if px is not None and px > 0:
                return px
    px = _num(plan.get("t1"))
    return px if px is not None and px > 0 else None


def _json_list(raw):
    try:
        v = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return v if isinstance(v, list) else []


def _plan_of(rec):
    try:
        d = json.loads(rec.get("plan") or "")
    except (TypeError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _plan_spec(root, plan_id):
    """Спека плана сделки: читается тем же помощником `tb.labels.paths`, что и путь сделки (живые планы и архив)."""
    if not isinstance(plan_id, str) or not plan_id:
        return None
    try:
        spec, _rt = LP.plan_files(root, plan_id)
    except (OSError, ValueError, TypeError):
        return None
    return spec if isinstance(spec, dict) else None


def after_metrics(rows, side, entry_px, stop0, exit_px, exit_ts, t1=None, cutoff=None, ru=None):
    """Метрики окна после выхода в R (Requirement 13.1).

    rows: минутные свечи пути `(ts, o, h, l, c)`; окно — свечи с `ts >= exit_ts` (и `ts < cutoff`, если задан).
    `ru` — R-единица (иначе `|entry_px − stop0|`). -> {"after_n", "after_mfe_r", "after_mfe_from_entry_r",
    "after_mae_r", "after_t1", "after_t1_min"}; `after_t1` = None, если цель `t1` неизвестна; нет окна, стороны или
    R-единицы — все метрики None.
    """
    s = 1 if side == "long" else (-1 if side == "short" else None)
    epx, xpx, xts = _num(entry_px), _num(exit_px), _num(exit_ts)
    unit = _num(ru)
    if unit is None:
        st = _num(stop0)
        unit = abs(epx - st) if epx is not None and st is not None else None
    out = {"after_n": 0, "after_mfe_r": None, "after_mfe_from_entry_r": None, "after_mae_r": None, "after_t1": None, "after_t1_min": None}
    if s is None or epx is None or epx <= 0 or xpx is None or xpx <= 0 or xts is None or not unit or unit <= 0:
        return out
    win = [tuple(r) for r in (rows or []) if len(r) >= 5 and _num(r[0]) is not None
           and _num(r[0]) >= xts and (cutoff is None or _num(r[0]) < cutoff)]
    out["after_n"] = len(win)
    if not win:
        return out
    best = max(r[2] for r in win) if s == 1 else min(r[3] for r in win)
    worst = min(r[3] for r in win) if s == 1 else max(r[2] for r in win)
    out["after_mfe_r"] = round(max(0.0, s * (best - xpx) / unit), 4)
    out["after_mfe_from_entry_r"] = round(max(0.0, s * (best - epx) / unit), 4)
    out["after_mae_r"] = round(max(0.0, -s * (worst - xpx) / unit), 4)
    if t1 is not None:
        hit = None
        for r in win:
            if (s == 1 and r[2] >= t1 - R_EPS) or (s == -1 and r[3] <= t1 + R_EPS):
                hit = _num(r[0])
                break
        out["after_t1"] = 1.0 if hit is not None else 0.0
        if hit is not None:
            out["after_t1_min"] = round(max(0.0, (hit - xts) / 60.0), 1)
    return out


def _exit_of(parts, close_ts):
    """(цена, время) последней фактической части закрытия; частей нет -> (None, close_ts)."""
    ts0 = _num(close_ts)
    got = [(_num(p.get("ts"), ts0), p) for p in (parts or []) if isinstance(p, dict) and _num(p.get("px")) is not None]
    got = [g for g in got if g[0] is not None]
    if not got:
        return (None, ts0)
    ts, p = max(got, key=lambda g: g[0])
    return (_num(p.get("px")), ts)


def _trade_reason(parts, timeline):
    """Причина выхода сделки: `stop_type` последней части, иначе последнее закрытие мозга из хронологии."""
    got = [(_num(p.get("ts"), 0.0), p) for p in (parts or []) if isinstance(p, dict)]
    got = [g for g in got if g[0] is not None]
    if got:
        _ts, last = max(got, key=lambda g: g[0])
        g = reason_group(last.get("stop_type"))
        if g:
            return g
    tl = [(_num(e.get("ts"), 0.0), e) for e in (timeline or []) if isinstance(e, dict)]
    tl = [x for x in tl if x[0] is not None]
    for _ts, e in reversed(sorted(tl, key=lambda x: x[0])):
        if e.get("kind") in ("close", "partial_close", "partial"):
            return "manual"
    return None


def _trade_rows(conn, root, mode, since, post_close):
    sql = "SELECT journal_id, plan_id, side, mode, entry_ts, entry_px, stop0, tick, close_ts, parts, timeline, rows FROM trade_paths"
    where, args = [], []
    if mode:
        where.append("mode=?")
        args.append(mode)
    if since is not None:
        where.append("entry_ts>=?")
        args.append(int(since))
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY entry_ts"
    cols = ("journal_id", "plan_id", "side", "mode", "entry_ts", "entry_px", "stop0", "tick", "close_ts", "parts", "timeline", "rows")
    out, skipped = [], 0
    for r in conn.execute(sql, args).fetchall():
        rec = dict(zip(cols, r))
        entry_px, stop0 = _num(rec["entry_px"]), _num(rec["stop0"])
        if entry_px is None or stop0 is None or abs(entry_px - stop0) <= R_EPS:
            skipped += 1
            continue
        try:
            path = LP.decode(rec["rows"], rec["tick"], entry_px)
        except (ValueError, TypeError):
            skipped += 1
            continue
        parts, timeline = _json_list(rec["parts"]), _json_list(rec["timeline"])
        exit_px, exit_ts = _exit_of(parts, rec["close_ts"])
        if exit_px is None or exit_ts is None:
            skipped += 1
            continue
        cutoff = _num(exit_ts) + post_close * 60.0 if post_close else None
        t1 = target_t1(_plan_spec(root, rec["plan_id"]))
        m = after_metrics(path, rec["side"], entry_px, stop0, exit_px, exit_ts, t1=t1, cutoff=cutoff)
        out.append(dict({"src": "trade", "key": rec["journal_id"], "mode": rec["mode"], "side": rec["side"], "entry_ts": rec["entry_ts"],
                         "exit_ts": exit_ts, "exit_px": exit_px, "exit_reason": _trade_reason(parts, timeline), "t1": t1}, **m))
    return out, skipped


def _run_rows(conn, since, floor, costs):
    sql = ("SELECT key, policy_id, policy_ver, t0, side, entry_ts, entry_px, stop, tick, max_hold_min, atr5_pct, plan, rows "
           "FROM run_paths WHERE state=? AND (flags IS NULL OR instr(',' || flags || ',', ',' || ? || ',')=0)")
    args = [LS.PATH_COMPLETE, LS.FLAG_GAP]
    if since is not None:
        sql += " AND t0>=?"
        args.append(int(since))
    sql += " ORDER BY entry_ts"
    cols = ("key", "policy_id", "policy_ver", "t0", "side", "entry_ts", "entry_px", "stop", "tick", "max_hold_min", "atr5_pct", "plan", "rows")
    out, skipped = [], 0
    for r in conn.execute(sql, args).fetchall():
        rec = dict(zip(cols, r))
        plan = _plan_of(rec)
        entry_px, stop0 = _num(rec["entry_px"]), _num(rec["stop"])
        if plan is None or entry_px is None or stop0 is None or abs(entry_px - stop0) <= R_EPS:
            skipped += 1
            continue
        try:
            path = LP.decode(rec["rows"], rec["tick"], entry_px)
            base = exitsim.run(plan, path, floor, floor, costs=costs, order="S",
                               ctx={"max_hold_min": _num(rec["max_hold_min"]), "atr5_pct": _num(rec["atr5_pct"])})
        except (ValueError, TypeError, KeyError):
            skipped += 1
            continue
        if not base.get("filled") or base.get("exit_reason") in (None, "end", "no_fill"):
            skipped += 1
            continue
        exit_px, exit_ts = _exit_of(base.get("parts"), base.get("end_ts"))
        if exit_px is None or exit_ts is None:
            skipped += 1
            continue
        t1 = target_t1(plan)
        m = after_metrics(path, rec["side"], entry_px, stop0, exit_px, exit_ts, t1=t1)
        out.append(dict({"src": "run", "key": "%s@%s-%s" % (rec["key"], rec["policy_id"], rec["policy_ver"]), "mode": "paper",
                         "side": rec["side"], "entry_ts": rec["entry_ts"], "exit_ts": exit_ts, "exit_px": exit_px,
                         "exit_reason": reason_group(base.get("exit_reason")), "t1": t1}, **m))
    return out, skipped


def aggregate(rows):
    """Строки отчёта -> {"n", "by_reason", "rows"} (Requirement 13.3: доля «после t1», её n и интервал Уилсона по причинам)."""
    by = {}
    for r in rows or []:
        key = r.get("exit_reason") if r.get("exit_reason") in REASONS else OTHER
        d = by.setdefault(key, {"n_rows": 0, "t1_n": 0, "after_t1_n": 0, "unknown_t1": 0, "mfe": [], "mae": [], "t1_min": []})
        d["n_rows"] += 1
        if r.get("after_t1") is None:
            d["unknown_t1"] += 1
        else:
            d["t1_n"] += 1
            d["after_t1_n"] += int(round(float(r["after_t1"])))
        for src, dst in (("after_mfe_r", "mfe"), ("after_mae_r", "mae"), ("after_t1_min", "t1_min")):
            v = r.get(src)
            if v is not None:
                d[dst].append(float(v))
    out = {}
    for key in by:
        d = by[key]
        lo, hi = wilson(d["after_t1_n"], d["t1_n"])
        out[key] = {"n": d["t1_n"], "n_rows": d["n_rows"], "after_t1_n": d["after_t1_n"], "unknown_t1": d["unknown_t1"],
                    "share": round(d["after_t1_n"] / float(d["t1_n"]), 4) if d["t1_n"] else None,
                    "wilson_lo": lo, "wilson_hi": hi, "mean_after_mfe_r": _mean(d["mfe"]),
                    "mean_after_mae_r": _mean(d["mae"]), "mean_after_t1_min": _mean(d["t1_min"])}
    return {"n": len(rows or []), "by_reason": out, "rows": list(rows or [])}


def _mode_of(cfg, mode):
    if mode in MODES:
        return mode
    b = (cfg or {}).get("brain") if isinstance(cfg, dict) else None
    m = b.get("mode") if isinstance(b, dict) else None
    return m if m in MODES else None


def after(conn, mode=None, since=None, now=None, root=None):
    """Отчёт «путь после закрытия» по открытому соединению (только чтение).

    `mode` — режим торговли для сделок мозга (по умолчанию `config/exits.json: brain.mode`); прогоны меток — всегда paper.
    `since` — нижняя граница `entry_ts`/`t0` (секунды). -> {"ts", "mode", "since", "window_min", "trade", "run", "notes"}.
    """
    now = int(time.time() if now is None else now)
    cfg, cerrs = EC.load(root)
    notes = []
    p = (cfg or {}).get("paths") if isinstance(cfg, dict) else None
    post_close = _num((p or {}).get("post_close_min")) if isinstance(p, dict) else None
    if cerrs:
        notes.append("конфиг выходов не прочитан (%s): окно после закрытия — по границе сохранённого пути" % ",".join(cerrs[:3]))
    mode = _mode_of(cfg, mode)
    trade, t_skip = [], 0
    try:
        trade, t_skip = _trade_rows(conn, root, mode, since, post_close)
    except sqlite3.Error as e:
        notes.append("trade_paths не читаются: %s" % type(e).__name__)
    run, r_skip = [], 0
    if mode in (None, "paper"):
        try:
            costs, _manage = LPOL.sim_inputs(root, {})
        except (ValueError, OSError, TypeError):
            costs = {}
        try:
            run, r_skip = _run_rows(conn, since, EC.current_floor(root), costs)
        except sqlite3.Error as e:
            notes.append("run_paths не читаются: %s" % type(e).__name__)
    else:
        notes.append("прогоны меток — paper: при mode=%s не показываются" % mode)
    return {"ts": now, "mode": mode, "since": since, "window_min": post_close,
            "trade": dict(aggregate(trade), skipped=t_skip), "run": dict(aggregate(run), skipped=r_skip), "notes": notes}


def case_fields(row):
    """Необязательные поля случая `tb.case/1` из строки отчёта: `result.after_mfe_r`, `result.after_t1` (Requirement 13.4)."""
    row = row if isinstance(row, dict) else {}
    out = {}
    mfe = _num(row.get("after_mfe_r"))
    if mfe is not None:
        out["after_mfe_r"] = round(mfe, 2)
    hit = _num(row.get("after_t1"))
    if hit is not None:
        out["after_t1"] = 1.0 if hit >= 0.5 else 0.0
    return out


def unavailable(code, detail=None, now=None):
    """Отчёт при недоступной базе: пустые секции, `db` = код причины, `ok` = False."""
    why = REASONS_RU.get(code, code) + ((": %s" % detail) if detail else "")
    free = {"n": 0, "by_reason": {}, "rows": [], "skipped": 0}
    return {"ok": False, "db": code, "error": why, "ts": int(time.time() if now is None else now), "mode": None, "since": None,
            "window_min": None, "trade": dict(free), "run": dict(free), "notes": [why]}


def run(root=None, mode=None, since=None, now=None):
    """Отчёт по базе root: соединение только для чтения на вызов (`labels.store.open_ro`). Нет базы/восстановление/ошибка -> unavailable."""
    try:
        conn = LST.open_ro(root)
    except LST.KnowledgeRestoring:
        return unavailable(RESTORING, now=now)
    except sqlite3.Error as e:
        return unavailable(DB_ERROR, "%s: %s" % (type(e).__name__, e), now=now)
    if conn is None:
        return unavailable(NO_DB, now=now)
    try:
        rep = after(conn, mode=mode, since=since, now=now, root=root)
        rep["ok"] = True
        rep["db"] = "OK"
        return rep
    finally:
        conn.close()


def _reason_lines(title, sec):
    lines = ["%s: путей %d%s" % (title, sec.get("n") or 0, (" (пропущено %d)" % sec["skipped"]) if sec.get("skipped") else "")]
    if not sec.get("n"):
        lines.append("  данных нет")
        return lines
    for key in REASONS + (OTHER,):
        d = (sec.get("by_reason") or {}).get(key)
        if not d:
            continue
        share = "—" if d["share"] is None else "%.1f%%" % (d["share"] * 100.0)
        ci = "—" if d["wilson_lo"] is None else "95%% Уилсон: %.1f–%.1f%%" % (d["wilson_lo"] * 100.0, d["wilson_hi"] * 100.0)
        mfe = "—" if d["mean_after_mfe_r"] is None else "%.2fR" % d["mean_after_mfe_r"]
        mae = "—" if d["mean_after_mae_r"] is None else "%.2fR" % d["mean_after_mae_r"]
        mins = "—" if d["mean_after_t1_min"] is None else "%.1f мин" % d["mean_after_t1_min"]
        lines.append("  %-7s n=%-4d после t1: %d (%s, %s); ход %s, худший %s, до t1 %s%s"
                     % (key, d["n"], d["after_t1_n"], share, ci, mfe, mae, mins,
                        "; t1 неизвестна у %d" % d["unknown_t1"] if d["unknown_t1"] else ""))
    return lines


def render(rep):
    """Текст для человека: сводка по сделкам мозга и прогонам меток."""
    head = "путь после закрытия: режим %s, окно после выхода %s мин" % (
        rep.get("mode") or "все", "—" if rep.get("window_min") is None else "%g" % rep["window_min"])
    lines = [head]
    lines.extend(_reason_lines("сделки мозга", rep.get("trade") or {}))
    lines.extend(_reason_lines("прогоны меток (база X-0001)", rep.get("run") or {}))
    lines.append("отчёт справочный: стопы и цели правил не меняются")
    for n in rep.get("notes") or []:
        lines.append("примечание: " + n)
    return lines
