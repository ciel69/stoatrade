"""`./tb labels <команда>`: status | stats | coverage | show KEY | backfill --since D | rerun --policy ID --since D | backup | verify (R1, Requirements 12.5, 13).

Вывод: JSON одной строкой (--text: кратко для человека). Коды: 0 успех; 1 - нет ключа (show), нарушения (verify), доля пустых признаков выше порога (coverage),
нет базы (show, verify, backup); 2 - ошибка использования или реестра политик.
Разметку (признаки, исходы, прогоны политик) делает только сервис `labels`. backfill и rerun лишь ставят сигналы в очередь (вставка pending или перевод в pending)
одной транзакцией BEGIN IMMEDIATE; сервис должен быть запущен (`./tb status`), иначе очередь будет разобрана после его старта. Сеть и ключи не используются.
"""
import argparse
import json
import os
import sqlite3
import sys
import time

from tb import paths
from tb.labels import capture, label, policies, query, schema, store

HB_ALIVE_S = 60.0
CHUNK = 1 << 20
BF_META = "backfill"
VERIFY_EXAMPLES = 20
HORIZONS = (15, 60, 240)


def _out(obj, text=False, lines=None):
    if text and lines is not None:
        print("\n".join(lines))
    else:
        print(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str))


def _err(msg):
    print("ошибка: %s" % msg, file=sys.stderr)


def _jl(s):
    if s is None:
        return None
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return s


def _fmt(v, nd=3):
    return "-" if v is None else ("%.*f" % (nd, v) if isinstance(v, float) else str(v))


# ---------------------------------------------------------------- догрузка (backfill)

def _bf_src(since_t):
    return "backfill:%d" % int(since_t)


def _ro_meta(conn, key):
    row = conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return _jl(row[0]) if row else None


def backfill(conn, root, since_t, cfg=None, now_s=None, restart=False, max_bytes=None):
    """Захват сигналов runtime/hot_events.jsonl с t0 >= since_t в signals (status=pending). Возобновляемо: курсор `backfill:<since>` (offset + head_sha первых 4 КБ) и
    транзакция на кусок 1 МБ; повтор читает с сохранённого смещения, дубли отсекает ключ (INSERT OR IGNORE). fng/dvol/risk_off не заполняются (null): снимка контекста
    задним числом нет. Свечи не загружаются (их берёт сервис с max_rps). -> отчёт (прогресс также в meta[backfill] для `labels status`)."""
    cfg = cfg if cfg is not None else capture.load_cfg(root)
    now_s = time.time() if now_s is None else now_s
    path = paths.runtime("hot_events.jsonl", root=root)
    rep = {"since": int(since_t), "path": path, "offset": 0, "size": 0, "new": 0, "seen": 0, "bad_lines": 0, "done": False, "resumed": False}
    if not os.path.exists(path):
        rep["done"] = True
        rep["note"] = "нет runtime/hot_events.jsonl"
        return rep
    size = os.path.getsize(path)
    src = _bf_src(since_t)
    offset, prev = 0, {}
    cur = conn.execute("SELECT offset, head_sha FROM cursor WHERE src=?", (src,)).fetchone()
    if cur and not restart and 0 < cur[0] <= size and capture._head_sha(path, cur[0]) == cur[1]:
        offset = cur[0]
        rep["resumed"] = True
        p = _ro_meta(conn, BF_META)
        prev = p if isinstance(p, dict) and p.get("since") == int(since_t) else {}
    for k in ("new", "seen", "bad_lines"):
        rep[k] = int(prev.get(k) or 0)
    allowed = set(cfg.get("capture_types") or capture.DEFAULT_CFG["capture_types"])
    specs = capture.policy_specs(root)
    read = 0
    with open(path, "rb") as f:
        while max_bytes is None or read < max_bytes:
            f.seek(offset)
            data = f.read(CHUNK)
            if not data:
                break
            last = data.rfind(b"\n")
            if last < 0:
                if len(data) < CHUNK:                      # неполная последняя строка: ждём её окончания
                    break
                last = len(data) - 1                       # одна строка длиннее куска: пропускаем
                rep["bad_lines"] += 1
            chunk = data[:last + 1]
            rows = []
            for raw in chunk.split(b"\n"):
                if not raw.strip():
                    continue
                sig = capture.parse_event(raw.decode("utf-8", "replace"))
                if sig is None:
                    rep["bad_lines"] += 1
                elif sig["t0"] >= since_t and sig["type"] in allowed:
                    rows.append(sig)
            new_off = offset + len(chunk)
            with store.tx(conn):
                rep["new"] += capture.insert_signals(conn, rows, cfg, specs, now_s)
                rep["seen"] += len(rows)
                conn.execute("INSERT OR REPLACE INTO cursor(src, path, offset, head_sha, ts) VALUES(?,?,?,?,?)",
                             (src, path, new_off, capture._head_sha(path, new_off), int(now_s)))
                _save_progress(conn, rep, new_off, size, now_s, False)
            offset = new_off
            read += len(chunk)
    rep["offset"], rep["size"] = offset, size
    rep["done"] = offset >= size
    with store.tx(conn):
        _save_progress(conn, rep, offset, size, now_s, rep["done"])
    return rep


def _save_progress(conn, rep, offset, size, now_s, done):
    d = {"since": rep["since"], "offset": offset, "size": size, "seen": rep["seen"], "new": rep["new"], "bad_lines": rep["bad_lines"], "done": bool(done), "ts": int(now_s)}
    conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (BF_META, json.dumps(d, separators=(",", ":"))))


# ---------------------------------------------------------------- перепрогон (rerun)

def rerun(conn, root, policy_id, since_t):
    """Ставит размеченные (done) сигналы с t0 >= since_t, к которым применима включённая политика policy_id, обратно в pending с маркером code=RERUN:<id>.
    Сервис (label.label_one) загрузит свечи заново и пересчитает ТОЛЬКО строки policy_runs этой политики; признаки, исходы и строки других политик остаются.
    Сигнал, уже ждущий перепрогона другой политики, получает id в маркер; pending без маркера (ещё не размечен) не трогается: его и так разметит сервис.
    ValueError - политики нет в реестре или она выключена. -> {"queued", "not_applicable", "pending_plain"}."""
    pols = [p for p in policies.load(root) if p["id"] == policy_id]
    if not pols:
        raise ValueError("политика %s не найдена в config/shadow_policies.json" % policy_id)
    on = [p for p in pols if p.get("enabled")]
    if not on:
        raise ValueError("политика %s выключена (enabled=false): перепрогонять нечего" % policy_id)
    res = {"policy": policy_id, "since": int(since_t), "queued": 0, "not_applicable": 0, "pending_plain": 0}
    with store.tx(conn):
        rows = conn.execute("SELECT key, type, tradable, status, code FROM signals WHERE t0>=? AND status IN (?, ?)", (int(since_t), schema.DONE, schema.PENDING)).fetchall()
        todo = []
        for key, typ, trad, st, code in rows:
            ids = set()
            if st == schema.PENDING:
                ids = label.rerun_ids(code)
                if ids is None:
                    res["pending_plain"] += 1
                    continue
            if not any(policies.applicable(p, {"type": typ, "tradable": trad}) for p in on):
                res["not_applicable"] += 1
                continue
            todo.append((label.RERUN_PREFIX + ",".join(sorted(ids | {policy_id})), 0, key))        # next_try_ts=0: метка уже созрела, сервис возьмёт её сразу
        conn.executemany("UPDATE signals SET status=?, code=?, attempts=0, next_try_ts=? WHERE key=?", [(schema.PENDING, c, t, k) for c, t, k in todo])
        res["queued"] = len(todo)
    return res


# ---------------------------------------------------------------- verify

def verify(root):
    """-> {"integrity", "done", "violations", "examples"}. Нарушение: у done-сигнала нет исхода горизонта/стороны или строки включённой подходящей политики текущей версии."""
    conn = store.open_ro(root)
    if conn is None:
        return None
    try:
        integrity = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
        cfg = capture.load_cfg(root)
        hs = [int(h) for h in (cfg.get("horizons") or HORIZONS)]
        try:
            active = policies.active(policies.load(root))
            reg_err = None
        except ValueError as e:
            active, reg_err = [], str(e)
        done = conn.execute("SELECT key, type, side, tradable FROM signals WHERE status=?", (schema.DONE,)).fetchall()
        outs = set(conn.execute("SELECT key, h, side FROM outcomes").fetchall())
        runs = set(conn.execute("SELECT key, policy_id, policy_ver FROM policy_runs").fetchall())
    finally:
        conn.close()
    bad = []
    if reg_err:
        bad.append({"key": None, "what": "реестр политик: %s" % reg_err})
    for key, typ, side, trad in done:
        for h in hs:
            for sd in ([side] if side in ("long", "short") else ["long", "short"]):
                if (key, h, sd) not in outs:
                    bad.append({"key": key, "what": "нет исхода h=%d side=%s" % (h, sd)})
        for p in active:
            if policies.applicable(p, {"type": typ, "tradable": trad}) and (key, p["id"], p["ver"]) not in runs:
                bad.append({"key": key, "what": "нет прогона %s v%s" % (p["id"], p["ver"])})
    ok_int = integrity == ["ok"]
    return {"ok": ok_int and not bad, "integrity": "ok" if ok_int else integrity[:5], "done": len(done), "violations": len(bad), "examples": bad[:VERIFY_EXAMPLES]}


# ---------------------------------------------------------------- команды

def cmd_status(a, root):
    now = time.time()
    from tb.platform import services as S
    age, hb = S.read_hb("labels", root, now)
    alive = age is not None and age <= HB_ALIVE_S
    out = {"service": {"alive": alive, "hb_age_s": None if age is None else round(age, 1)}}
    for k in ("captured_24h", "labeled_24h", "pending", "due", "failed_24h", "no_data_24h", "rate_limited_24h", "req_per_min", "bad_lines", "last_error", "db_mb",
              "missing_share_24h", "warnings"):
        if k in hb:
            out["service"][k] = hb[k]
    out["queue"] = query.throughput(root, now) or None
    conn = store.open_ro(root)
    out["backfill"], out["rerun_pending"] = None, 0
    if conn is not None:
        try:
            bf = _ro_meta(conn, BF_META)
            if isinstance(bf, dict):
                size = bf.get("size") or 0
                bf["progress"] = round(min(1.0, (bf.get("offset") or 0) / size), 4) if size else 1.0
                out["backfill"] = bf
            out["rerun_pending"] = conn.execute("SELECT COUNT(*) FROM signals WHERE status=? AND code LIKE ?", (schema.PENDING, label.RERUN_PREFIX + "%")).fetchone()[0]
        finally:
            conn.close()
    if not alive:
        out["note"] = "сервис labels не запущен: очередь будет разобрана после его старта (./tb up)"
    q, s = out["queue"] or {}, out["service"]
    _out(out, a.text, ["labels: %s (hb %s с)" % ("работает" if alive else "НЕ ЗАПУЩЕН", _fmt(s["hb_age_s"], 1)),
                       "очередь: pending=%s due=%s labeled_24h=%s captured_24h=%s failed_24h=%s no_data_24h=%s db_mb=%s" % (
                           q.get("pending"), q.get("due"), q.get("labeled_24h"), q.get("captured_24h"), q.get("failed_24h"), q.get("no_data_24h"), q.get("db_mb")),
                       "догрузка: %s" % ("нет" if not out["backfill"] else "с %s: %.1f%% (новых %s, готово=%s)" % (
                           time.strftime("%Y-%m-%d", time.gmtime(out["backfill"]["since"])), 100 * out["backfill"]["progress"], out["backfill"]["new"], out["backfill"]["done"])),
                       "перепрогон в очереди: %d" % out["rerun_pending"]] + ([out["note"]] if "note" in out else []))
    return 0


def cmd_stats(a, root):
    since = a.since
    if a.by == "policy":
        rows = query.policy_stats(root, since=since, by=("policy_id", "policy_ver"), kinds=a.kinds.split(",") if a.kinds else None)
        lines = []
        for r in rows:
            ex = " ".join("%s=%s" % (k, _fmt(v, 2)) for k, v in sorted(r["exit_reasons"].items()))
            lines.append("%s v%s n=%s fill=%s win=%s r_net=%s+-%s PF=%s MFE=%s MAE=%s p=%s alpha_adj=%s%s | %s" % (
                r["policy_id"], r["policy_ver"], r["n"], _fmt(r["fill_rate"], 2), _fmt(r["win"], 2), _fmt(r["avg_r_net"]), _fmt(r["se_r"]),
                "inf" if r["pf_inf"] else _fmt(r["pf"], 2), _fmt(r["mfe_r"], 2), _fmt(r["mae_r"], 2), _fmt(r["p"], 4), _fmt(r["alpha_adj"], 4),
                " (n<min)" if r["small"] else "", ex))
        _out({"by": "policy", "since": since, "rows": rows}, a.text, lines or ["нет данных"])
    else:
        rows = query.outcome_stats(root, since=since, by=("type",), horizon=a.horizon)
        _out({"by": "type", "since": since, "horizon": a.horizon, "rows": rows}, a.text,
             ["%s h=%s n=%s ret=%s net_dir=%s net>0=%s" % (r["type"], r["h"], r["n"], _fmt(r["avg_ret_pct"]), _fmt(r["avg_net_dir_pct"]), _fmt(r["net_pos_share"], 2)) for r in rows] or ["нет данных"])
    return 0


def cmd_coverage(a, root):
    cov = query.coverage(root, since=a.since)
    if not cov:
        _out({"ok": True, "db": False, "note": "нет базы знаний"}, a.text, ["нет базы знаний"])
        return 0
    share, limit = cov.get("any_missing_share"), cov["limit"]
    cov["ok"] = share is None or share <= limit
    _out(cov, a.text, ["done=%s доля меток с пустым признаком: %s (порог %s)  %s" % (cov["done"], _fmt(share, 4), limit, "OK" if cov["ok"] else "ВЫШЕ ПОРОГА")]
         + ["  %s: %s" % (k, _fmt(v, 4)) for k, v in cov["fields"].items()])
    return 0 if cov["ok"] else 1


def cmd_show(a, root):
    conn = store.open_ro(root)
    if conn is None:
        _err("нет базы знаний")
        return 1
    try:
        cur = conn.execute("SELECT * FROM signals WHERE key=?", (a.key,))
        cols = [c[0] for c in cur.description]
        row = cur.fetchone()
        if row is None:
            _err("нет сигнала %s" % a.key)
            return 1
        sig = dict(zip(cols, row))
        for k in ("ev", "feat", "missing"):
            sig[k] = _jl(sig.get(k))
        oc = conn.execute("SELECT * FROM outcomes WHERE key=? ORDER BY h, side", (a.key,))
        oc_cols = [c[0] for c in oc.description]
        outs = [dict(zip(oc_cols, r)) for r in oc.fetchall()]
        pr = conn.execute("SELECT * FROM policy_runs WHERE key=? ORDER BY policy_id, policy_ver", (a.key,))
        pr_cols = [c[0] for c in pr.description]
        runs = [dict(zip(pr_cols, r)) for r in pr.fetchall()]
    finally:
        conn.close()
    for r in runs:
        r["plan"] = _jl(r.get("plan"))
    print(json.dumps({"signal": sig, "features": sig["feat"], "outcomes": outs, "policy_runs": runs}, ensure_ascii=False, separators=(",", ":"), default=str))
    return 0


def cmd_backfill(a, root):
    try:
        since_t = query.since_ts(a.since)
    except ValueError:
        _err("--since: ожидается YYYY-MM-DD")
        return 2
    if since_t is None:
        _err("--since обязателен")
        return 2
    conn = store.open_rw(root)
    try:
        rep = backfill(conn, root, since_t, restart=a.restart, max_bytes=int(a.max_mb * (1 << 20)) if a.max_mb else None)
    finally:
        conn.close()
    _out(rep, a.text, ["backfill с %s: новых %s из %s в диапазоне, строк с ошибкой %s, прочитано %s из %s байт, %s" % (
        time.strftime("%Y-%m-%d", time.gmtime(since_t)), rep["new"], rep["seen"], rep["bad_lines"], rep["offset"], rep["size"],
        "готово" if rep["done"] else "не дочитано: повторите команду (продолжится с места остановки)")])
    return 0


def cmd_rerun(a, root):
    try:
        since_t = query.since_ts(a.since)
    except ValueError:
        _err("--since: ожидается YYYY-MM-DD")
        return 2
    if since_t is None:
        _err("--since обязателен")
        return 2
    try:
        conn = store.open_rw(root)
        try:
            res = rerun(conn, root, a.policy, since_t)
        finally:
            conn.close()
    except ValueError as e:
        _err(e)
        return 2
    _out(res, a.text, ["rerun %s с %s: в очередь поставлено %s (политика неприменима: %s, ещё не размечены: %s)" % (
        res["policy"], time.strftime("%Y-%m-%d", time.gmtime(since_t)), res["queued"], res["not_applicable"], res["pending_plain"])])
    return 0


def cmd_backup(a, root):
    try:
        dst = store.backup(root)
    except FileNotFoundError:
        _err("нет базы знаний")
        return 1
    except (OSError, sqlite3.Error) as e:
        _err("%s: %s" % (type(e).__name__, e))
        return 1
    _out({"ok": True, "path": dst}, a.text, ["backup: %s" % dst])
    return 0


def cmd_verify(a, root):
    rep = verify(root)
    if rep is None:
        _err("нет базы знаний")
        return 1
    _out(rep, a.text, ["verify: integrity=%s done=%s нарушений=%s" % (rep["integrity"], rep["done"], rep["violations"])]
         + ["  %s: %s" % (e["key"], e["what"]) for e in rep["examples"]])
    return 0 if rep["ok"] else 1


def build_parser():
    p = argparse.ArgumentParser(prog="tb labels", description="фабрика меток (R1): состояние, статистика политик, догрузка, перепрогон, проверка")
    sub = p.add_subparsers(dest="cmd")
    st = sub.add_parser("status", help="heartbeat сервиса, очередь, прогресс догрузки и перепрогона")
    st.add_argument("--text", action="store_true")
    ss = sub.add_parser("stats", help="статистика теневых политик (по умолчанию) или базовая частота исходов по типам")
    ss.add_argument("--by", choices=("policy", "type"), default="policy")
    ss.add_argument("--since", help="YYYY-MM-DD (UTC)")
    ss.add_argument("--kinds", help="типы сигналов через запятую (только для --by policy)")
    ss.add_argument("--horizon", type=int, default=60, help="горизонт исходов, мин (для --by type)")
    ss.add_argument("--text", action="store_true")
    cv = sub.add_parser("coverage", help="доля done-меток с пустыми coverage_fields; код 1 выше порога 10%%")
    cv.add_argument("--since")
    cv.add_argument("--text", action="store_true")
    sh = sub.add_parser("show", help="JSON сигнала с признаками, исходами и прогонами политик (нет ключа - код 1)")
    sh.add_argument("key")
    bf = sub.add_parser("backfill", help="поставить в очередь сигналы runtime/hot_events.jsonl с даты (возобновляемо)")
    bf.add_argument("--since", required=True, help="YYYY-MM-DD (UTC)")
    bf.add_argument("--restart", action="store_true", help="читать файл с начала (дубли отсекает ключ)")
    bf.add_argument("--max-mb", type=float, default=None, help="прочитать не больше N МБ за запуск (остальное - повторным запуском)")
    bf.add_argument("--text", action="store_true")
    rr = sub.add_parser("rerun", help="поставить размеченные сигналы в очередь на перепрогон ОДНОЙ политики (другие строки не меняются)")
    rr.add_argument("--policy", required=True)
    rr.add_argument("--since", required=True, help="YYYY-MM-DD (UTC)")
    rr.add_argument("--text", action="store_true")
    bk = sub.add_parser("backup", help="копия базы state/backups/knowledge-YYYYMMDD.sqlite без остановки сервиса")
    bk.add_argument("--text", action="store_true")
    vf = sub.add_parser("verify", help="integrity_check и полнота разметки; код 0 только при нуле нарушений")
    vf.add_argument("--text", action="store_true")
    return p


HANDLERS = {"status": cmd_status, "stats": cmd_stats, "coverage": cmd_coverage, "show": cmd_show, "backfill": cmd_backfill, "rerun": cmd_rerun, "backup": cmd_backup,
            "verify": cmd_verify}


def main(argv, root=None):
    p = build_parser()
    try:
        a = p.parse_args(list(argv))
    except SystemExit as e:
        return int(e.code or 0)
    if not a.cmd:
        p.print_usage(sys.stderr)
        return 2
    try:
        return HANDLERS[a.cmd](a, root)
    except ValueError as e:
        _err(e)
        return 2
    except sqlite3.Error as e:
        _err("база знаний: %s: %s" % (type(e).__name__, e))
        return 1


if __name__ == "__main__":
    for _st in (sys.stdout, sys.stderr):
        try:
            _st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    sys.exit(main(sys.argv[1:]))
