"""Чтение базы меток (R1, Requirement 12.6): только open_ro (query_only), ничего не пишет. Для CLI `./tb labels` и страницы «Учится ли мозг».

policy_stats(root, since, by, kinds)   -> list[dict]  по группам политик: n, заполнения, win, r_net +- se, PF, MFE/MAE в R, доли exit_reason, p, порог Бонферрони
outcome_stats(root, since, by, horizon) -> list[dict]  базовая частота исходов по типам сигналов
coverage(root, since)                  -> dict        статусы и доля пустых признаков среди done
throughput(root, now_s)                -> dict        счётчики 24 ч и очередь
Нет базы -> пустой результат. since: None | эпоха в секундах | 'YYYY-MM-DD' (UTC).
"""
import datetime
import json
import math
import os
import sqlite3

from tb.core import expstats
from tb.labels import capture, policies, schema, store

POLICY_BY = ("policy_id", "policy_ver", "geometry_ver", "ctx_ver", "type", "sym", "side")
OUTCOME_BY = ("type", "sym", "side", "env", "corr_group", "wake")
COVERAGE_LIMIT = 0.10
COVERAGE_FIELDS = ("atr5_pct", "range_15m_pct", "ret_1h_pct", "btc_ret_1h_pct", "dist_sup_pct", "dist_res_pct")


def since_ts(since):
    """None | число | 'YYYY-MM-DD' | ISO -> эпоха (секунды) | None."""
    if since is None or since == "":
        return None
    if isinstance(since, (int, float)) and not isinstance(since, bool):
        return int(since)
    s = str(since).strip()
    try:
        return int(float(s))
    except ValueError:
        pass
    d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return int(d.timestamp())


def _by(by, allowed):
    cols = [by] if isinstance(by, str) else list(by or ())
    bad = [c for c in cols if c not in allowed]
    if bad or not cols:
        raise ValueError("by: допустимо %s" % ", ".join(allowed))
    return cols


def _ro(root):
    return store.open_ro(root)


def _close(conn):
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


def _n_enabled(root, fallback):
    try:
        return max(1, len(policies.active(policies.load(root))))
    except ValueError:
        return max(1, fallback)


def policy_stats(root=None, since=None, by=("policy_id", "policy_ver"), kinds=None, min_n=expstats.MIN_N, alpha=expstats.ALPHA):
    """Статистика теневых политик. Версии не смешиваются (по умолчанию группировка по id и ver). Метрики r считаются по заполненным входам.

    Строка: ключи группировки, n (все прогоны), filled, fill_rate, win (доля r_net > 0 среди заполненных), avg_r_net, se_r, pf (None без убытков), pf_inf,
    mfe_r, mae_r (средние по заполненным), exit_reasons {код: доля от заполненных и ttl/no_fill от всех}, p (при filled >= min_n), k (число включённых политик),
    alpha_adj = alpha / k (expstats.bonferroni), below_alpha."""
    cols = _by(by, POLICY_BY)
    conn = _ro(root)
    if conn is None:
        return []
    try:
        where, args = ["1=1"], []
        t = since_ts(since)
        if t is not None:
            where.append("t0>=?")
            args.append(t)
        if kinds:
            where.append("type IN (%s)" % ",".join("?" * len(kinds)))
            args += list(kinds)
        g = ", ".join(cols)
        sql = ("SELECT %s, COUNT(*), SUM(filled), SUM(CASE WHEN filled=1 AND r_net>0 THEN 1 ELSE 0 END), SUM(CASE WHEN filled=1 THEN r_net END),"
               " SUM(CASE WHEN filled=1 THEN r_net*r_net END), SUM(CASE WHEN filled=1 AND r_net>0 THEN r_net ELSE 0 END),"
               " SUM(CASE WHEN filled=1 AND r_net<=0 THEN -r_net ELSE 0 END), AVG(CASE WHEN filled=1 THEN mfe_r END), AVG(CASE WHEN filled=1 THEN mae_r END)"
               " FROM policy_runs WHERE %s GROUP BY %s ORDER BY %s") % (g, " AND ".join(where), g, g)
        rows = conn.execute(sql, args).fetchall()
        ex = {}
        for r in conn.execute("SELECT %s, exit_reason, COUNT(*) FROM policy_runs WHERE %s GROUP BY %s, exit_reason" % (g, " AND ".join(where), g), args).fetchall():
            ex.setdefault(tuple(r[:len(cols)]), {})[r[len(cols)] or "none"] = r[len(cols) + 1]
    finally:
        _close(conn)
    k = _n_enabled(root, len(rows))
    adj = expstats.bonferroni(k, alpha)
    out = []
    nc = len(cols)
    for r in rows:
        key = tuple(r[:nc])
        n, nf, wins, s1, s2, gw, gl, mfe, mae = r[nc], int(r[nc + 1] or 0), r[nc + 2] or 0, r[nc + 3], r[nc + 4], r[nc + 5] or 0.0, r[nc + 6] or 0.0, r[nc + 7], r[nc + 8]
        mean = s1 / nf if nf else None
        se = None
        if nf > 1:
            var = max(0.0, (s2 - nf * mean * mean) / (nf - 1))
            se = math.sqrt(var) / math.sqrt(nf)
        p = expstats.t_two_sided_p(mean / se, nf - 1) if nf >= min_n and se and se > 0 else None
        reasons = {}
        for code, c in sorted((ex.get(key) or {}).items()):
            reasons[code] = c / n if code in ("ttl", "no_fill", "none") else (c / nf if nf else None)
        row = dict(zip(cols, key))
        row.update(n=n, filled=nf, fill_rate=nf / n if n else None, win=wins / nf if nf else None, avg_r_net=mean, se_r=se,
                   pf=(gw / gl) if gl > 0 else None, pf_inf=bool(gl <= 0 and gw > 0), mfe_r=mfe, mae_r=mae, exit_reasons=reasons,
                   p=p, k=k, alpha_adj=adj, below_alpha=(p < adj) if p is not None else None, small=nf < min_n)
        out.append(row)
    return out


def outcome_stats(root=None, since=None, by=("type",), horizon=60):
    """Базовая частота исходов горизонта horizon мин: по группам n, средние ret_pct/mfe/mae/net_dir_pct, доля net_dir_pct > 0, средние mfe_atr/mae_atr,
    доли флагов gap/halt. Сигнал без направления даёт строки обеих сторон (группировка по side различает их)."""
    cols = _by(by, OUTCOME_BY)
    conn = _ro(root)
    if conn is None:
        return []
    try:
        where, args = ["o.h=?"], [int(horizon)]
        t = since_ts(since)
        if t is not None:
            where.append("s.t0>=?")
            args.append(t)
        sel = ", ".join(("o.side" if c == "side" else "s.%s" % c) for c in cols)
        sql = ("SELECT %s, COUNT(*), AVG(o.ret_pct), AVG(o.mfe_pct), AVG(o.mae_pct), AVG(o.net_dir_pct), AVG(CASE WHEN o.net_dir_pct>0 THEN 1.0 ELSE 0.0 END),"
               " AVG(o.mfe_atr), AVG(o.mae_atr), AVG(CASE WHEN o.flags LIKE '%%gap%%' THEN 1.0 ELSE 0.0 END), AVG(CASE WHEN o.flags LIKE '%%halt%%' THEN 1.0 ELSE 0.0 END)"
               " FROM outcomes o JOIN signals s ON s.key=o.key WHERE %s GROUP BY %s ORDER BY %s") % (sel, " AND ".join(where), sel, sel)
        rows = conn.execute(sql, args).fetchall()
    finally:
        _close(conn)
    nc = len(cols)
    names = ("n", "avg_ret_pct", "avg_mfe_pct", "avg_mae_pct", "avg_net_dir_pct", "net_pos_share", "avg_mfe_atr", "avg_mae_atr", "gap_share", "halt_share")
    out = []
    for r in rows:
        d = dict(zip(cols, r[:nc]))
        d.update(zip(names, r[nc:]))
        d["h"] = int(horizon)
        out.append(d)
    return out


def heartbeat_stats(conn, now_s, coverage_fields=None):
    """Счётчики для heartbeat/throughput по готовому соединению: captured_24h, labeled_24h, failed_24h, no_data_24h, pending, due, missing_share_24h."""
    cut = int(now_s) - 86400

    def one(sql, *a):
        return int(conn.execute(sql, a).fetchone()[0] or 0)
    out = {"captured_24h": one("SELECT COUNT(*) FROM signals WHERE captured_ts>=?", cut),
           "labeled_24h": one("SELECT COUNT(*) FROM signals WHERE status=? AND labeled_ts>=?", schema.DONE, cut),
           "failed_24h": one("SELECT COUNT(*) FROM signals WHERE status=? AND labeled_ts>=?", schema.FAILED, cut),
           "no_data_24h": one("SELECT COUNT(*) FROM signals WHERE status=? AND labeled_ts>=?", schema.NO_DATA, cut),
           "pending": one("SELECT COUNT(*) FROM signals WHERE status=?", schema.PENDING),
           "due": one("SELECT COUNT(*) FROM signals WHERE status=? AND mature_ts<=? AND next_try_ts<=?", schema.PENDING, int(now_s), int(now_s))}
    out["missing_share_24h"] = _missing_share(conn, cut, coverage_fields)
    try:                                   # R4: пути прогонов и сделок мозга (таблицы схемы v2)
        out["paths_partial"] = one("SELECT COUNT(*) FROM run_paths WHERE state=?", schema.PATH_PARTIAL)
        out["paths_complete"] = one("SELECT COUNT(*) FROM run_paths WHERE state=?", schema.PATH_COMPLETE)
        out["trade_paths"] = one("SELECT COUNT(*) FROM trade_paths")
        row = conn.execute("SELECT v FROM meta WHERE k='trade_path_missing'").fetchone()
        out["trade_path_missing"] = int(row[0]) if row and row[0] is not None else 0
    except (sqlite3.Error, ValueError, TypeError):
        pass
    return out


def _missing_share(conn, cut, fields):
    """Доля done (с labeled_ts >= cut) с пустым хотя бы одним полем fields; None - нет done."""
    fields = list(fields or COVERAGE_FIELDS)
    sql = "SELECT missing FROM signals WHERE status=? AND labeled_ts>=?"
    n = bad = 0
    for (m,) in conn.execute(sql, (schema.DONE, int(cut))):
        n += 1
        try:
            miss = set(json.loads(m)) if m else set()
        except ValueError:
            miss = set()
        if miss & set(fields):
            bad += 1
    return bad / n if n else None


def coverage(root=None, since=None):
    """-> {"signals": {status: n}, "done": n, "fields": {поле: доля пустых}, "any_missing_share", "limit"} среди done с t0 >= since; нет базы -> {}."""
    conn = _ro(root)
    if conn is None:
        return {}
    cfg = capture.load_cfg(root)
    fields = list(cfg.get("coverage_fields") or COVERAGE_FIELDS)
    try:
        t = since_ts(since)
        cond, args = ("WHERE t0>=?", [t]) if t is not None else ("", [])
        status = dict(conn.execute("SELECT status, COUNT(*) FROM signals %s GROUP BY status" % cond, args).fetchall())
        cond2 = ("AND t0>=?", [t]) if t is not None else ("", [])
        done = conn.execute("SELECT feat FROM signals WHERE status=? %s" % cond2[0], [schema.DONE] + cond2[1]).fetchall()
    finally:
        _close(conn)
    cnt = dict((f, 0) for f in fields)
    anym = 0
    for (feat,) in done:
        try:
            d = json.loads(feat) if feat else {}
        except ValueError:
            d = {}
        miss = [f for f in fields if d.get(f) is None]
        for f in miss:
            cnt[f] += 1
        anym += 1 if miss else 0
    n = len(done)
    return {"signals": status, "done": n, "fields": dict((f, cnt[f] / n if n else None) for f in fields),
            "any_missing_share": anym / n if n else None, "limit": COVERAGE_LIMIT}


def db_mb(root=None):
    """Размер базы знаний с журналом WAL, МБ (до 3 знаков); нет файла -> None."""
    p = store.db_path(root)
    try:
        total = os.path.getsize(p)
    except OSError:
        return None
    for suf in ("-wal",):
        try:
            total += os.path.getsize(p + suf)
        except OSError:
            pass
    return round(total / 1048576.0, 3)


def throughput(root=None, now_s=None):
    """-> счётчики 24 ч и очередь (heartbeat_stats) + db_mb, cursor_offset, oldest_pending_age_s. Нет базы -> {}."""
    import time
    now_s = time.time() if now_s is None else now_s
    conn = _ro(root)
    if conn is None:
        return {}
    try:
        cfg = capture.load_cfg(root)
        out = heartbeat_stats(conn, now_s, cfg.get("coverage_fields"))
        old = conn.execute("SELECT MIN(t0) FROM signals WHERE status=?", (schema.PENDING,)).fetchone()[0]
        cur = conn.execute("SELECT offset FROM cursor WHERE src=?", (capture.SRC,)).fetchone()
    finally:
        _close(conn)
    out["db_mb"] = db_mb(root)
    out["cursor_offset"] = cur[0] if cur else 0
    out["oldest_pending_age_s"] = int(now_s - old) if old is not None else None
    return out
