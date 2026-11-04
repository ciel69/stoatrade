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
    расширена только ими (`tb.memory.cases.RESULT`), `rules_ver` не меняется. `write_after(root, ...)` — писатель этих
    полей: считает метрики по готовым `trade_paths` и дописывает `update`-строку закрытого случая (`tb.memory.cases.append_update`),
    сохраняя остальной `result` и `src` (в том числе `rules_ver`); повторный вызов без изменений ничего не пишет
    (идемпотентность), без готового пути случай не трогается.

Отчёт R4.3 «мозг против алгоритма» (Requirement 12, задача 19)
    `brain(conn, ...)` для каждой закрытой сделки мозга с путём считает `brain_delta = r_sim(версия позиции + timeline) −
    r_sim(версия позиции без timeline)` через `exitsim.replay_brain` (порядок `S`, один лот `qty/qty_step/min_qty`, строки
    пути срезаны до `entry_ts`). Версия позиции — `trade_paths.exit_ver` из `exit_versions` (нет записи — пол защиты,
    счётчик `policy_missing`). Разрезы: по типам вмешательства (`move_stop`, `close`, `partial_close`) и по кодам
    пробуждения (`decision kind="event", what="watch_wake"` плана в окне сделки, `args.code` / `WHY=WATCH:<код>`); сделка
    входит в каждый разрез, который у неё есть. Доля сделок, где `qty.close_qty` изменил долю политики, считается
    сравнением частей прогона с лотом и без лота. Стоимость `position_manager` — тот же журнал `tb.llm.usage`
    (`state/llm_calls.jsonl`), из которого собран `brief.economics` (импорт `tb.brain` запрещён слоями `tb.learnloop`).
    Вердикт по Requirement 12.2: n < 30 — «данных мало», иначе по 95% интервалу среднего по сделкам.

Конфиг: `paths.post_close_min` берётся из `config/exits.json` (битый конфиг — окно по границе сохранённого пути и
примечание в отчёте, числовых умолчаний в коде нет, Requirement 16.1). Слои `tb.learnloop` соблюдены: нет мозга, шлюза,
биржи, исполнителя, управления, ключей и CLI; в `config/` ничего не пишется.
"""
import json
import math
import sqlite3
import time

from tb.core import decisions as DEC
from tb.core import expstats as XSTAT
from tb.labels import paths as LP
from tb.labels import policies as LPOL
from tb.labels import schema as LS
from tb.labels import store as LST
from tb.learnloop import exits_conf as EC
from tb.learnloop import exits_eval as EV
from tb.llm import usage as LLM
from tb.logic import exitpol as XP
from tb.logic import exitsim
from tb.memory import cases as MC

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
    """Необязательные поля случая `tb.case/1` из строки отчёта: `result.after_mfe_r`, `result.after_t1` (Requirement 13.4).
    Пишет их `write_after` (или закрывающий случай), сам разбор строки базу не меняет."""
    row = row if isinstance(row, dict) else {}
    out = {}
    mfe = _num(row.get("after_mfe_r"))
    if mfe is not None:
        out["after_mfe_r"] = round(mfe, 2)
    hit = _num(row.get("after_t1"))
    if hit is not None:
        out["after_t1"] = 1.0 if hit >= 0.5 else 0.0
    return out


# ---------------------------------------------------------------- R4.3: мозг против алгоритма (Requirement 12, задача 19)

BRAIN_COLS = ("journal_id", "plan_id", "sym", "side", "mode", "entry_ts", "entry_px", "stop0", "tick", "qty", "qty_step", "min_qty",
              "horizon", "max_hold_min", "atr5_pct", "sig_type", "exit_ver", "close_ts", "rows", "end_ts", "flags", "timeline", "fetched_ts")
INTERVENTION_TYPES = ("move_stop", "close", "partial_close")
REPORT_MIN_N = 30                       # Requirement 12.2: n < 30 — «данных мало»
PM_ROLE = "position_manager"
VERDICT_LOW, VERDICT_FLAT, VERDICT_ADDS, VERDICT_HARMS = "данных мало", "разницы не видно", "мозг добавляет", "мозг вредит"


def _sd(xs):
    xs = list(xs)
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / float(n)
    return math.sqrt(max(0.0, sum((x - m) ** 2 for x in xs) / float(n - 1)))


def mean_ci(xs, level=0.95):
    """Среднее и двусторонний t-интервал по сделкам -> (mean, lo, hi); пусто -> (None, None, None), n = 1 -> (m, m, m)."""
    xs = [float(x) for x in (xs or [])]
    n = len(xs)
    if not n:
        return (None, None, None)
    m = sum(xs) / float(n)
    s = _sd(xs)
    if n < 2 or s is None or s == 0.0:
        return (round(m, 4), round(m, 4), round(m, 4))
    se = s / math.sqrt(float(n))
    try:
        q = XSTAT.t_quantile(1.0 - (1.0 - float(level)) / 2.0, n - 1)
    except (ValueError, TypeError, ZeroDivisionError):
        q = WILSON_Z
    return (round(m, 4), round(m - q * se, 4), round(m + q * se, 4))


def brain_verdict(n, lo, hi):
    """Вердикт Requirement 12.2: n < 30 — «данных мало»; интервал содержит 0 — «разницы не видно»;
    нижняя граница > 0 — «мозг добавляет»; верхняя граница < 0 — «мозг вредит»."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return VERDICT_LOW
    if n < REPORT_MIN_N or lo is None or hi is None:
        return VERDICT_LOW
    if float(lo) > 0.0:
        return VERDICT_ADDS
    if float(hi) < 0.0:
        return VERDICT_HARMS
    return VERDICT_FLAT


def _flag_set(flags):
    return set(x for x in str(flags or "").split(",") if x)


def _canon_kind(kind):
    """Вид вмешательства в терминах `exitsim` -> move_stop | close | partial_close | None."""
    k = str(kind or "").strip().lower()
    if k in ("move_stop", "stop", "adjust"):
        return "move_stop"
    if k == "close":
        return "close"
    if k in ("partial_close", "partial"):
        return "partial_close"
    return None


def _timeline_kinds(timeline):
    """Виды вмешательств хронологии без повторов в порядке первого появления (неизвестные события пропускаются)."""
    out = []
    for e in timeline or []:
        if not isinstance(e, dict):
            continue
        k = _canon_kind(e.get("kind"))
        if k and k not in out:
            out.append(k)
    return out


def _wake_code(rec):
    """Код пробуждения из записи решений: `kind="event", what="watch_wake"`, `args.code` или `why="WATCH:<код>"`."""
    if not isinstance(rec, dict) or rec.get("kind") != "event" or rec.get("what") != "watch_wake":
        return None
    args = rec.get("args") if isinstance(rec.get("args"), dict) else {}
    code = args.get("code")
    if isinstance(code, str) and code.strip():
        return code.strip()
    why = rec.get("why")
    if isinstance(why, str) and why.startswith("WATCH:"):
        code = why[6:].strip()
        return code or None
    return None


def _wakes_by_plan(root, since=None):
    """Коды пробуждений по планам из журнала решений -> {plan: [(ts, code)]} (по возрастанию времени)."""
    try:
        rows = DEC.read(after_seq=0, plan=None, limit=0, root=root)
    except (OSError, ValueError, TypeError):
        return {}
    out = {}
    for r in rows:
        code = _wake_code(r)
        if not code:
            continue
        ts = _num(r.get("t"))
        if ts is None or (since is not None and ts < float(since)):
            continue
        plan = r.get("plan")
        if isinstance(plan, str) and plan:
            out.setdefault(plan, []).append((ts, code))
    for lst in out.values():
        lst.sort(key=lambda x: x[0])
    return out


def _policy_for(conn, ver, floor, cache=None):
    """Политика версии позиции из `exit_versions` -> (policy, missing). Нет записи/таблицы — пол и missing=True
    (только для именованной версии, отличной от X-0001)."""
    cache = cache if isinstance(cache, dict) else {}
    key = ver if isinstance(ver, str) and ver else ""
    if key in cache:
        return cache[key]
    pol, missing = floor, False
    if key and key != "X-0001":
        missing = True
        try:
            row = conn.execute("SELECT policy FROM exit_versions WHERE ver=?", (key,)).fetchone()
        except sqlite3.Error:
            row = None
        if row:
            try:
                raw = json.loads(row[0] or "")
            except (TypeError, ValueError):
                raw = None
            if isinstance(raw, dict):
                pol, missing = XP.normalize(raw, floor), False
    cache[key] = (pol, missing)
    return pol, missing


def _iter_brain_trades(conn, mode, since):
    sql = "SELECT %s FROM trade_paths" % ",".join(BRAIN_COLS)
    where, args = [], []
    if mode:
        where.append("mode=?")
        args.append(mode)
    if since is not None:
        where.append("entry_ts>=?")
        args.append(int(since))
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY entry_ts, journal_id"
    for r in conn.execute(sql, args).fetchall():
        yield dict(zip(BRAIN_COLS, r))


def _parts_sig(res):
    """Подпись частей закрытия: [(why, frac)] — по ней видно, изменил ли `close_qty` долю политики."""
    return [(str(p.get("why")), round(_num(p.get("frac"), 0.0), 8)) for p in (res.get("parts") or []) if isinstance(p, dict)]


def _brain_row(rec, floor, costs, stop_moves, policy):
    """Одна сделка: прогон с хронологией и без неё, доля `close_qty`, виды вмешательств. None — путь непригоден."""
    tick = _num(rec.get("tick"))
    entry_px = _num(rec.get("entry_px"))
    entry_ts = _num(rec.get("entry_ts"))
    stop0 = _num(rec.get("stop0"))
    if tick is None or tick <= 0 or entry_px is None or entry_px <= 0 or entry_ts is None or stop0 is None:
        return None
    entry_ts = int(entry_ts)
    rows = [r for r in LP.decode(rec.get("rows"), tick, entry_px) if int(r[0]) >= entry_ts]
    if not rows:
        return None
    mh = _num(rec.get("max_hold_min"))
    plan = {"side": rec.get("side"), "entry": {"kind": "market"}, "stop": stop0, "tps": [], "fracs": [],
            "tick": tick, "max_hold_min": mh, "entry_px": entry_px}
    ctx = {"max_hold_min": mh, "atr5_pct": _num(rec.get("atr5_pct")), "sig_type": rec.get("sig_type"),
           "horizon": rec.get("horizon"), "entry_px": entry_px, "stop_moves": stop_moves}
    lot = {"qty": _num(rec.get("qty")), "qty_step": _num(rec.get("qty_step")), "min_qty": _num(rec.get("min_qty"))}
    timeline = _json_list(rec.get("timeline"))
    with_tl = exitsim.replay_brain(plan, rows, policy, floor, costs, timeline, ctx, lot)
    without_tl = exitsim.replay_brain(plan, rows, policy, floor, costs, None, ctx, lot)
    if not (with_tl.get("filled") and without_tl.get("filled")):
        return None
    exact = exitsim.run(plan, rows, policy, floor, costs, order="S", timeline=timeline, ctx=ctx)
    changed = bool(exact.get("filled") and _parts_sig(with_tl) != _parts_sig(exact))
    kinds = _timeline_kinds(timeline)
    return {"journal_id": rec.get("journal_id"), "mode": rec.get("mode"), "side": rec.get("side"), "entry_ts": entry_ts,
            "exit_ver": rec.get("exit_ver"), "r_with": round(with_tl["r_net"], 4), "r_without": round(without_tl["r_net"], 4),
            "brain_delta": round(with_tl["r_net"] - without_tl["r_net"], 4), "interventions": kinds,
            "wake_codes": [], "close_qty_changed": changed}


def _group_stats(rows, pred):
    """Группа сделок: n, среднее brain_delta, t-интервал, вердикт (строится по округлённым строкам отчёта)."""
    got = [r for r in rows if pred(r)]
    vals = [float(r["brain_delta"]) for r in got if r.get("brain_delta") is not None]
    m, lo, hi = mean_ci(vals)
    return {"n": len(got), "mean_delta": m, "ci_lo": lo, "ci_hi": hi, "verdict": brain_verdict(len(got), lo, hi)}


def _pm_usage(root, since=None):
    """Расход роли `position_manager` из журнала вызовов (тот же источник, что `brief.economics`, но без импорта tb.brain)."""
    try:
        rows = LLM.read_rows(root, since=since)
    except (OSError, ValueError, TypeError):
        rows = []
    calls = tokens = 0
    usd = 0.0
    for r in rows:
        if r.get("kind") == "fmt" or r.get("role") != PM_ROLE:
            continue
        calls += 1
        tokens += int(r.get("in") or 0) + int(r.get("out") or 0)
        usd += float(r.get("usd") or 0.0)
    return {"role": PM_ROLE, "calls": calls, "tokens": tokens, "usd": round(usd, 6), "source": "state/llm_calls.jsonl (brief.economics)"}


def brain(conn, mode=None, since=None, now=None, root=None):
    """Отчёт R4.3 (Requirement 12.1–12.5) по открытому соединению.

    -> {"ts", "mode", "since", "n", "n_intervened", "share_intervened", "mean_delta", "ci_lo", "ci_hi", "verdict",
    "by_type", "by_wake", "close_qty", "tokens", "policy_missing", "skipped", "rows", "notes"}.
    """
    now = int(time.time() if now is None else now)
    cfg, cerrs = EC.load(root)
    notes = []
    if cerrs:
        notes.append("конфиг выходов не прочитан (%s)" % ",".join(cerrs[:3]))
    mode = _mode_of(cfg, mode)
    if mode not in MODES:
        notes.append("режим не задан и brain.mode конфига не прочитан: считаю paper")
        mode = "paper"
    try:
        inp = EV.market_inputs(root)
    except (ValueError, TypeError, KeyError, OSError) as e:
        notes.append("входы симулятора не прочитаны: %s" % type(e).__name__)
        inp = {"floor": XP.floor({}), "costs": {}, "stop_moves": {}}
    floor, costs, stop_moves = inp["floor"], inp["costs"], inp["stop_moves"]
    wakes = _wakes_by_plan(root, since)
    cache, rows, skipped, policy_missing = {}, [], 0, 0
    for rec in _iter_brain_trades(conn, mode, since):
        flags = _flag_set(rec.get("flags"))
        if LS.FLAG_GAP in flags or LS.FLAG_ADOPTED in flags:
            skipped += 1
            continue
        try:
            policy, missing = _policy_for(conn, rec.get("exit_ver"), floor, cache)
            row = _brain_row(rec, floor, costs, stop_moves, policy)
        except (ValueError, TypeError, KeyError, sqlite3.Error):
            skipped += 1
            continue
        if row is None:
            skipped += 1
            continue
        if missing:
            policy_missing += 1
        lo_ts = int(rec.get("entry_ts") or 0)
        hi_ts = _num(rec.get("close_ts"), _num(rec.get("end_ts"), lo_ts)) + 60.0
        codes = []
        for ts, code in wakes.get(rec.get("plan_id") or "", []):
            if lo_ts <= ts <= hi_ts and code not in codes:
                codes.append(code)
        row["wake_codes"] = codes
        rows.append(row)
    n = len(rows)
    vals = [float(r["brain_delta"]) for r in rows]
    m, lo, hi = mean_ci(vals)
    intervened = sum(1 for r in rows if r.get("interventions"))
    by_type = {}
    for kind in INTERVENTION_TYPES:
        if any(kind in r.get("interventions", []) for r in rows):
            by_type[kind] = _group_stats(rows, lambda r, k=kind: k in r.get("interventions", []))
    by_wake = {}
    for code in sorted(set(c for r in rows for c in r.get("wake_codes", []))):
        by_wake[code] = _group_stats(rows, lambda r, c=code: c in r.get("wake_codes", []))
    changed = sum(1 for r in rows if r.get("close_qty_changed"))
    tokens = _pm_usage(root, since)
    tokens["usd_per_trade"] = round(tokens["usd"] / n, 6) if n else None
    tokens["tokens_per_trade"] = int(round(tokens["tokens"] / float(n))) if n else None
    if policy_missing:
        notes.append("версия позиции не найдена в exit_versions у %d сделок: считался пол защиты" % policy_missing)
    if n and not any(r.get("wake_codes") for r in rows):
        notes.append("кодов пробуждения в журнале решений нет (wake-разрезы пусты)")
    return {"ts": now, "mode": mode, "since": since, "n": n, "n_intervened": intervened,
            "share_intervened": round(intervened / float(n), 4) if n else None,
            "mean_delta": m, "ci_lo": lo, "ci_hi": hi, "verdict": brain_verdict(n, lo, hi),
            "by_type": by_type, "by_wake": by_wake,
            "close_qty": {"n": changed, "share": round(changed / float(n), 4) if n else None},
            "tokens": tokens, "policy_missing": policy_missing, "skipped": skipped, "rows": rows, "notes": notes}


def unavailable_brain(code, detail=None, now=None):
    """Отчёт R4.3 при недоступной базе: пустые секции, `db` = код причины, `ok` = False."""
    why = REASONS_RU.get(code, code) + ((": %s" % detail) if detail else "")
    return {"ok": False, "db": code, "error": why, "ts": int(time.time() if now is None else now), "mode": None, "since": None,
            "n": 0, "n_intervened": 0, "share_intervened": None, "mean_delta": None, "ci_lo": None, "ci_hi": None,
            "verdict": VERDICT_LOW, "by_type": {}, "by_wake": {}, "close_qty": {"n": 0, "share": None},
            "tokens": {"role": PM_ROLE, "calls": 0, "tokens": 0, "usd": 0.0, "usd_per_trade": None, "tokens_per_trade": None,
                       "source": "state/llm_calls.jsonl (brief.economics)"},
            "policy_missing": 0, "skipped": 0, "rows": [], "notes": [why]}


def run_brain(root=None, mode=None, since=None, now=None):
    """Отчёт R4.3 по базе root: соединение только для чтения на вызов (`labels.store.open_ro`)."""
    try:
        conn = LST.open_ro(root)
    except LST.KnowledgeRestoring:
        return unavailable_brain(RESTORING, now=now)
    except sqlite3.Error as e:
        return unavailable_brain(DB_ERROR, "%s: %s" % (type(e).__name__, e), now=now)
    if conn is None:
        return unavailable_brain(NO_DB, now=now)
    try:
        rep = brain(conn, mode=mode, since=since, now=now, root=root)
        rep["ok"] = True
        rep["db"] = "OK"
        return rep
    finally:
        conn.close()


# ---------------------------------------------------------------- R4.2: писатель полей после закрытия (Requirement 13.4)

def _case_index(root):
    """Закрытые случаи-сделки по `refs.journal_id` -> {journal_id: [случай]}."""
    try:
        cases = MC.load(root)
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return {}
    out = {}
    for c in cases:
        if not isinstance(c, dict) or c.get("type") != "trade":
            continue
        res = c.get("result") if isinstance(c.get("result"), dict) else {}
        if res.get("status") != "closed":
            continue
        jid = (c.get("refs") or {}).get("journal_id") if isinstance(c.get("refs"), dict) else None
        if isinstance(jid, str) and jid:
            out.setdefault(jid, []).append(c)
    return out


def write_after(root=None, mode=None, since=None, now=None):
    """Записать `result.after_mfe_r` / `result.after_t1` в закрытые случаи по готовым `trade_paths` (Requirement 13.4).

    Идемпотентно: поля, уже совпадающие с расчётом, не пишутся, `update`-строка не добавляется; `rules_ver` и остальной
    `src` не меняются (обновляется только блок `result`). Без готового пути случай не трогается. `mode=None` — все режимы.
    -> {"ok", "db", "ts", "scanned", "updated", "unchanged", "no_case", "no_fields", "errors", "cases"}.
    """
    now = int(time.time() if now is None else now)
    cfg, _errs = EC.load(root)
    p = (cfg or {}).get("paths") if isinstance(cfg, dict) else None
    post_close = _num((p or {}).get("post_close_min")) if isinstance(p, dict) else None
    try:
        conn = LST.open_ro(root)
    except LST.KnowledgeRestoring:
        return {"ok": False, "db": RESTORING, "error": REASONS_RU[RESTORING], "ts": now}
    except sqlite3.Error as e:
        return {"ok": False, "db": DB_ERROR, "error": "%s: %s" % (type(e).__name__, e), "ts": now}
    if conn is None:
        return {"ok": False, "db": NO_DB, "error": REASONS_RU[NO_DB], "ts": now}
    try:
        rows, _skipped = _trade_rows(conn, root, mode, since, post_close)
    except sqlite3.Error as e:
        return {"ok": False, "db": DB_ERROR, "error": "%s: %s" % (type(e).__name__, e), "ts": now}
    finally:
        conn.close()
    index = _case_index(root)
    out = {"ok": True, "db": "OK", "ts": now, "scanned": len(rows), "updated": 0, "unchanged": 0, "no_case": 0,
           "no_fields": 0, "errors": 0, "cases": sum(len(v) for v in index.values())}
    for row in rows:
        fields = case_fields(row)
        if not fields:
            out["no_fields"] += 1
            continue
        matched = index.get(row.get("key"))
        if not matched:
            out["no_case"] += 1
            continue
        for c in matched:
            cur = dict(c.get("result") or {})
            need = dict((k, v) for k, v in fields.items() if _num(cur.get(k)) != v)
            if not need:
                out["unchanged"] += 1
                continue
            cur.update(need)
            try:
                MC.append_update(c["case_id"], {"result": cur}, root=root, ts=MC.iso(now))
            except (MC.CaseError, OSError, ValueError, TypeError):
                out["errors"] += 1
                continue
            out["updated"] += 1
    return out


def render_brain(rep):
    """Текст отчёта R4.3 для человека."""
    lines = ["мозг против алгоритма: режим %s, %s" % (rep.get("mode") or "—",
                                                      "сделок с путём %d" % (rep.get("n") or 0))]
    ci = "—" if rep.get("ci_lo") is None else "95%%: %+.3f…%+.3fR" % (rep["ci_lo"], rep["ci_hi"])
    mean = "—" if rep.get("mean_delta") is None else "%+.3fR" % rep["mean_delta"]
    share = "—" if rep.get("share_intervened") is None else "%.1f%%" % (rep["share_intervened"] * 100.0)
    lines.append("  вмешательства: %d (%s); среднее brain_delta %s, %s; вердикт: %s"
                 % (rep.get("n_intervened") or 0, share, mean, ci, rep.get("verdict")))
    for kind in INTERVENTION_TYPES:
        d = (rep.get("by_type") or {}).get(kind)
        if d:
            lines.append("  %-13s n=%-4d %+.3fR [%s], %s" % (kind, d["n"], d["mean_delta"],
                                                             "—" if d["ci_lo"] is None else "%+.3f…%+.3f" % (d["ci_lo"], d["ci_hi"]),
                                                             d["verdict"]))
    for code, d in sorted((rep.get("by_wake") or {}).items()):
        lines.append("  wake %-10s n=%-4d %+.3fR [%s], %s" % (code, d["n"], d["mean_delta"],
                                                               "—" if d["ci_lo"] is None else "%+.3f…%+.3f" % (d["ci_lo"], d["ci_hi"]),
                                                               d["verdict"]))
    cq = rep.get("close_qty") or {}
    lines.append("  close_qty изменил долю: %d (%s)" % (cq.get("n") or 0,
                                                        "—" if cq.get("share") is None else "%.1f%%" % (cq["share"] * 100.0)))
    tk = rep.get("tokens") or {}
    lines.append("  position_manager: вызовов %d, токенов %d, $%.4f; на сделку %s токенов / $%s"
                 % (tk.get("calls") or 0, tk.get("tokens") or 0, tk.get("usd") or 0.0, tk.get("tokens_per_trade"),
                    "—" if tk.get("usd_per_trade") is None else "%.4f" % tk["usd_per_trade"]))
    for n in rep.get("notes") or []:
        lines.append("примечание: " + n)
    return lines


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
