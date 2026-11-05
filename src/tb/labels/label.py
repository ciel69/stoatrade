"""Разметка одной созревшей метки (R1, Requirements 2, 3, 5, 8): свечи -> признаки -> исходы 15/60/240 -> прогон политик симулятором.

`due(conn, now_s, limit)` - очередь созревших; `label_one(...)` - разметка с записью ОДНОЙ транзакцией (status=done, feat, исходы, policy_runs):
сначала всё считается в памяти, затем пишется под `store.tx`, сбой посреди записи откатывает всё (свойство 11).
Метка с code=RERUN:<id> (её ставит `./tb labels rerun`) размечается в режиме перепрогона: свечи грузятся заново, но пишутся только строки policy_runs этих политик.
Сеть и время приходят аргументами: `fetch(sym, interval, start_s, end_s) -> [Row]` (может бросить klines.FetchError / RateLimited),
`btc_cache.get(t0_s) -> [Row]`. Планов, ордеров и событий шины модуль не создаёт; ключей не читает.
"""
import json

from tb import paths
from tb.labels import features, klines, paths as path_mod, policies as pol_mod, schema, store
from tb.logic import bars
from tb.logic import costs as costs_mod
from tb.logic import geometry, sim

VENUE = "bybit"
LEVELS_SRC = "key_levels"
WIN_BEFORE_S = 30 * 60               # 1м свечи с t0 - 30 мин
N_5M, N_1H = 300, 200
RATE_DEFER_S = klines.RATE_PAUSE_S
DEFAULT_HORIZONS = (15, 60, 240)
RATE_LIMITED = "rate_limited"        # возврат label_one: метка осталась pending, попытка не потрачена
RERUN_PREFIX = "RERUN:"              # signals.code у pending-метки, поставленной `./tb labels rerun`: "RERUN:P2" или "RERUN:P2,P4" (id политик)
RERUN_FAILED = "RERUN_FAILED"        # signals.code у done-метки, чей перепрогон не удался за max_attempts (прежняя разметка цела)

_COLS = ("key", "t0", "sym", "type", "side", "score", "price", "wake", "tradable", "corr_group", "env", "ev_id", "ev", "status", "code",
         "attempts", "mature_ts", "next_try_ts", "captured_ts")


def due(conn, now_s, limit):
    """Созревшие метки в порядке t0: status=pending, mature_ts <= now, next_try_ts <= now -> list[dict]."""
    cur = conn.execute("SELECT %s FROM signals WHERE status=? AND mature_ts<=? AND next_try_ts<=? ORDER BY t0, key LIMIT ?" % ", ".join(_COLS),
                       (schema.PENDING, int(now_s), int(now_s), int(limit)))
    return [dict(zip(_COLS, r)) for r in cur.fetchall()]


def load_geom(root=None):
    """config/geometry.json -> dict (нет файла или битый -> {}: geometry возьмёт DEFAULT_PROFILES)."""
    try:
        with open(paths.config("geometry.json", root=root), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _jd(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


def _flags(*names):
    f = [n for n in names if n]
    return ",".join(f) or None


# ---------- откладывание и завершение без разметки

def rerun_ids(code):
    """signals.code вида 'RERUN:P2,P4' -> {'P2','P4'}; иное значение -> None (обычная разметка)."""
    if isinstance(code, str) and code.startswith(RERUN_PREFIX):
        ids = {x for x in code[len(RERUN_PREFIX):].split(",") if x}
        return ids or None
    return None


def defer(conn, row, cfg, now_s, code):
    """Отложить метку на retry_min с тратой попытки; после max_attempts - failed/code. -> 'pending' | 'failed'.

    Метка перепрогона (code=RERUN:...) метку-маркер не теряет, а после max_attempts возвращается в done (разметка цела) с code=RERUN_FAILED:<причина>."""
    attempts = int(row.get("attempts") or 0) + 1
    if rerun_ids(row.get("code")):
        if attempts >= int(cfg.get("max_attempts", 5)):
            with store.tx(conn):
                conn.execute("UPDATE signals SET status=?, code=?, attempts=? WHERE key=?", (schema.DONE, "%s:%s" % (RERUN_FAILED, code), attempts, row["key"]))
            return schema.DONE
        with store.tx(conn):
            conn.execute("UPDATE signals SET attempts=?, next_try_ts=? WHERE key=?", (attempts, int(now_s + float(cfg.get("retry_min", 30)) * 60), row["key"]))
        return schema.PENDING
    if attempts >= int(cfg.get("max_attempts", 5)):
        with store.tx(conn):
            conn.execute("UPDATE signals SET status=?, code=?, attempts=?, labeled_ts=? WHERE key=?",
                         (schema.FAILED, code, attempts, int(now_s), row["key"]))
        return schema.FAILED
    with store.tx(conn):
        conn.execute("UPDATE signals SET code=?, attempts=?, next_try_ts=? WHERE key=?",
                     (code, attempts, int(now_s + float(cfg.get("retry_min", 30)) * 60), row["key"]))
    return schema.PENDING


def _rate_defer(conn, row, now_s):
    with store.tx(conn):
        conn.execute("UPDATE signals SET next_try_ts=? WHERE key=?", (int(now_s + RATE_DEFER_S), row["key"]))
    return RATE_LIMITED


def _no_data(conn, row, now_s, cfg):
    if rerun_ids(row.get("code")):            # у размеченной метки пропали свечи: прежняя разметка остаётся, перепрогон откладывается
        return defer(conn, row, cfg, now_s, schema.NO_DATA_CODE)
    with store.tx(conn):
        conn.execute("UPDATE signals SET status=?, code=?, labeled_ts=? WHERE key=?", (schema.NO_DATA, schema.NO_DATA_CODE, int(now_s), row["key"]))
    return schema.NO_DATA


# ---------- расчёт

def _windows(row, cfg, fetch, btc_cache):
    t0 = int(row["t0"])
    end1 = int(row["mature_ts"] - float(cfg.get("grace_min", 10)) * 60)
    r1 = fetch(row["sym"], "1", t0 - WIN_BEFORE_S, end1)
    if not any(r[0] >= t0 for r in r1 or []):
        return None
    e5 = t0 // 300 * 300 - 300
    r5 = fetch(row["sym"], "5", e5 - (N_5M - 1) * 300, e5)
    e1h = t0 // 3600 * 3600 - 3600
    r1h = fetch(row["sym"], "60", e1h - (N_1H - 1) * 3600, e1h)
    return r1, r5, r1h, btc_cache.get(t0)


def _round_trip(root, ev):
    """-> (cost_rt_pct | None, no_spread: bool). 2 x (fee + slip) x 100 (risk_profile.costs.bybit) + spread_bps / 100."""
    try:
        with open(paths.config("risk_profile.json", root=root), encoding="utf-8") as f:
            prof = json.load(f)
    except (OSError, ValueError):
        return None, False
    base = costs_mod.round_trip_pct(prof, VENUE)
    sp = _num(ev.get("spread_bps"))
    if base is None:
        return None, sp is None
    return (base + sp / 100.0, False) if sp is not None else (base, True)


def _gap_min(r1, s0, t0):
    """Самый длинный пропуск минутных свечей после t0, мин (None - пропусков длиннее 3 мин нет)."""
    after = [r for r in r1 if r[0] >= t0]
    g = [m for _a, _b, m in bars.gaps(after)]
    if (s0 - t0) // 60 > bars.GAP_MAX_MIN:
        g.append((s0 - t0) // 60)
    return float(max(g)) if g else None


def _outcomes(row, r1, s0, px0, atr_pct, cost_rt, no_spread, horizons):
    sides = [row["side"]] if row.get("side") in ("long", "short") else ["long", "short"]
    out = []
    for h in horizons:
        for side in sides:
            ps = bars.path_stats(r1, s0, px0, side, h, atr_pct)
            net = None if ps["ret_pct"] is None or cost_rt is None else ps["ret_pct"] - cost_rt
            flags = _flags("gap" if ps["gap"] else None, "halt" if ps["halt"] else None, "no_spread" if no_spread else None)
            out.append((row["key"], int(h), side, ps["ret_pct"], ps["mfe_pct"], ps["mae_pct"], ps["mfe_atr"], ps["mae_atr"],
                        ps["t_mfe_min"], ps["t_mae_min"], cost_rt, net, flags))
    return out


def _policy_rows(root, cfg, row, feat, px0, ev, r1, t0, pols, geom_cfg, gap):
    """Строки policy_runs для включённых подходящих политик; tradable=false -> [].

    -> (rows, extra): extra — заполненные прогоны с планом и результатом симулятора для записи минутного пути прогона (R4).
    Ошибка конфигурации -> ValueError."""
    sig = {"key": row["key"], "type": row["type"], "side": row.get("side"), "tradable": row.get("tradable")}
    todo = [p for p in pols if pol_mod.applicable(p, sig)]
    if not todo:
        return [], []
    featp = dict(feat, px=px0)
    ctx = pol_mod.shadow_ctx(root, cfg, featp, ev)
    cver = ctx["ctx_ver"]
    costs, manage = pol_mod.sim_inputs(root, cfg, ev)
    gver = geom_cfg.get("geometry_ver") or geometry.GEOMETRY_VER
    rows, extra = [], []
    for p in todo:
        plan, code = pol_mod.pick(p, sig, featp, ctx, geom_cfg)
        base = (row["key"], p["id"], int(p["ver"]), gver, cver, t0, row["sym"], row["type"])
        if plan is None:
            rows.append(base + (None, None, code, 0, None, None, None, None, None, None, None, None))
            continue
        res = sim.run(plan, r1, t0, costs, manage)
        code = res.get("error")
        filled = bool(res.get("filled"))
        flags = _flags("gap" if gap is not None else None, "truncated" if res.get("truncated") else None,
                       "no_spread" if ev.get("spread_bps") is None else None)
        v = (lambda k: res.get(k) if filled else None)
        rows.append(base + (plan["side"], _jd(plan), code, 1 if filled else 0, res.get("exit_reason"),
                            v("r_gross"), v("r_net"), v("fees_r"), v("mfe_r"), v("mae_r"), v("hold_min"), flags))
        if filled:
            extra.append({"policy_id": p["id"], "policy_ver": int(p["ver"]), "plan": plan, "res": res})
    return rows, extra


def _path_rows(root, row, extra, r1, feat, now_s):
    """Строки run_paths (state=partial) для политик config/exits.json:paths.policies; пусто, если конфига нет (R4, задача 8)."""
    pcfg = path_mod.load_cfg(root)
    pcfg = path_mod.paths_cfg(pcfg)
    if not pcfg or not pcfg.get("policies"):
        return []
    want = set(pcfg["policies"])
    hold_mult = path_mod.cfg_float(pcfg, "hold_mult", 1.5)
    reserve_min = path_mod.cfg_float(pcfg, "reserve_min", 30.0)
    out = []
    for e in extra:
        if e["policy_id"] not in want:
            continue
        run = {"key": row["key"], "policy_id": e["policy_id"], "policy_ver": e["policy_ver"], "t0": int(row["t0"]),
               "sym": row["sym"], "type": row["type"]}
        rec = path_mod.run_path_row(run, e["plan"], e["res"], r1, feat, hold_mult, reserve_min, now_s=now_s)
        if rec is not None:
            out.append(rec)
    return out


def _rerun_write(conn, root, row, cfg, pols, geom_cfg, feat, px0, ev, r1, t0, gap, now_s):
    """Перепрогон политик (code=RERUN:<id,...>): пересчитываются и перезаписываются ТОЛЬКО строки policy_runs указанных политик (текущих версий,
    INSERT OR REPLACE по (key, policy_id, policy_ver)) и их минутные пути run_paths (R4); признаки, исходы, gap_min, строки остальных политик
    и прежних версий не трогаются."""
    try:
        runs, extra = _policy_rows(root, cfg, row, feat, px0, ev, r1, t0, pols, geom_cfg, gap)
    except ValueError:
        return defer(conn, row, cfg, now_s, schema.CONFIG)
    paths_rows = _path_rows(root, row, extra, r1, feat, now_s)
    with store.tx(conn):
        for r in runs:
            conn.execute("INSERT OR REPLACE INTO policy_runs(key, policy_id, policy_ver, geometry_ver, ctx_ver, t0, sym, type, side, plan, code, filled,"
                         " exit_reason, r_gross, r_net, fees_r, mfe_r, mae_r, hold_min, flags) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
        path_mod.insert_run_paths(conn, paths_rows, replace=True)
        conn.execute("UPDATE signals SET status=?, code=NULL, attempts=0 WHERE key=?", (schema.DONE, row["key"]))
    return schema.DONE


def label_one(conn, root, row, cfg, policies, geom_cfg, fetch, btc_cache, now_s):
    """Разметить созревшую метку. -> 'done' | 'no_data' | 'pending' (отложена на retry_min) | 'failed' | 'rate_limited' (отложена на 60 с, попытка не потрачена).

    Исключения сети (FetchError) и ошибки конфигурации (ValueError политик) обрабатываются здесь; прочие (в том числе sqlite3.Error при записи) уходят вызывающему,
    транзакция при этом уже откатена."""
    t0 = int(row["t0"])
    try:
        ev = json.loads(row["ev"]) if row.get("ev") else {}
    except ValueError:
        ev = {}
    ev = ev if isinstance(ev, dict) else {}
    snap = ev.pop("ctx_snap", None)
    try:
        w = _windows(row, cfg, fetch, btc_cache)
    except klines.RateLimited:
        return _rate_defer(conn, row, now_s)
    except klines.FetchError:
        return defer(conn, row, cfg, now_s, schema.FETCH)
    if w is None:
        return _no_data(conn, row, now_s, cfg)
    r1, r5, r1h, btc = w
    first = min((r for r in r1 if r[0] >= t0), key=lambda r: r[0])
    s0, px0 = first[0], first[1]

    feat, missing = features.compute(r1, r5, r1h, btc, t0, row["sym"], ev, snap if isinstance(snap, dict) else None)
    gap = _gap_min(r1, s0, t0)
    only = rerun_ids(row.get("code"))
    if only:
        return _rerun_write(conn, root, row, cfg, [p for p in policies if p["id"] in only], geom_cfg, feat, px0, ev, r1, t0, gap, now_s)
    cost_rt, no_spread = _round_trip(root, ev)
    horizons = cfg.get("horizons") or DEFAULT_HORIZONS
    outs = _outcomes(row, r1, s0, px0, feat.get("atr5_pct"), cost_rt, no_spread, horizons)
    try:
        runs, extra = _policy_rows(root, cfg, row, feat, px0, ev, r1, t0, policies, geom_cfg, gap)
    except ValueError:
        return defer(conn, row, cfg, now_s, schema.CONFIG)
    paths_rows = _path_rows(root, row, extra, r1, feat, now_s)

    with store.tx(conn):
        conn.execute("DELETE FROM outcomes WHERE key=?", (row["key"],))
        conn.executemany("INSERT INTO outcomes(key, h, side, ret_pct, mfe_pct, mae_pct, mfe_atr, mae_atr, t_mfe_min, t_mae_min, cost_rt_pct, net_dir_pct, flags)"
                         " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", outs)
        for r in runs:
            conn.execute("INSERT OR REPLACE INTO policy_runs(key, policy_id, policy_ver, geometry_ver, ctx_ver, t0, sym, type, side, plan, code, filled,"
                         " exit_reason, r_gross, r_net, fees_r, mfe_r, mae_r, hold_min, flags) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
        path_mod.insert_run_paths(conn, paths_rows, replace=True)
        conn.execute("UPDATE signals SET status=?, code=NULL, feat=?, missing=?, levels_src=?, gap_min=?, labeled_ts=? WHERE key=?",
                     (schema.DONE, _jd(feat), _jd(missing), LEVELS_SRC, gap, int(now_s), row["key"]))
    return schema.DONE
