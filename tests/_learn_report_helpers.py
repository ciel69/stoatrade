"""Оснастка тестов отчёта «Учится ли мозг» (R0.2, задача 12): временный корень, синтетические сделки/случаи/расход/метки с фиксированным seed и
сценарии эталонов `tests/fixtures_learning/payload_*.json`. Сети нет, боевые файлы не читаются."""
import json
import os
import random
import shutil
import tempfile

import _bootstrap  # noqa: F401
import _labels_helpers as H
from _memory_helpers import mk
from tb.labels import store
from tb.learn import report
from tb.memory import cases as memcases

NOW = H.BASE + 12 * 3600                       # 2026-11-02 12:00 UTC, понедельник
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures_learning")
SCENARIOS = ("empty", "few", "nodiff", "confirmed", "down", "mode_unknown", "no_labels", "computing", "full", "r2")
FILES = {"full": "payload_full.json", "few": "payload_few.json"}
COLS = "key, policy_id, policy_ver, geometry_ver, ctx_ver, t0, sym, type, side, plan, code, filled, exit_reason, r_gross, r_net, fees_r, mfe_r, mae_r, hold_min, flags"


def fixture_file(name):
    return os.path.join(FIX, FILES.get(name, "payload_%s.json" % name))


def mk_root():
    root = tempfile.mkdtemp(prefix="tblearnrep_")
    os.makedirs(os.path.join(root, "config"))
    os.makedirs(os.path.join(root, "state"))
    os.makedirs(os.path.join(root, "runtime"))
    for n in H.CFG_FILES:
        shutil.copy(os.path.join(H.PROJECT, "config", n), os.path.join(root, "config", n))
    return root


def write_cfg(root, obj):
    with open(os.path.join(root, "config", "learning.json"), "w", encoding="utf-8") as f:
        json.dump(obj, f)


def _jsonl(root, name, rows, mode="w"):
    with open(os.path.join(root, "state", name), mode, encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def gauss_rs(rng, parts):
    """parts: [(n, mu, sd), ...] -> список R."""
    return [round(rng.gauss(mu, sd), 3) for n, mu, sd in parts for _ in range(n)]


def times(n, step=6 * 3600, end=NOW - 3600):
    return [end - (n - 1 - i) * step for i in range(n)]


def trade_recs(rs, ts, mode="paper", prefix="T", manual_every=0, no_path_every=0):
    """Пары open/close журнала: выигрыш имеет MFE 1.8 R, убыток 0.3 R. manual_every: каждая k-я сделка ручная (без plan_id, тег manual-pick)."""
    out = []
    for i, (r, t) in enumerate(zip(rs, ts)):
        tid = "%s%03d-%s" % (prefix, i, mode)
        o = {"ev": "open", "id": tid, "ts": H.iso(t - 1800), "sym": "SYM%d" % (i % 6), "side": "long" if i % 2 else "short", "mode": mode,
             "risk_usdt": 1.0, "setup": ("level_reclaim", "sweep_reject", "range_fade")[i % 3], "tags": [], "plan_id": "PL-%d" % i}
        if manual_every and i % manual_every == manual_every - 1:
            o["tags"] = ["manual-pick"]
            o.pop("plan_id")
        c = {"ev": "close", "id": tid, "ts_close": H.iso(t), "reason": "tp" if r > 0 else "sl", "pnl_usdt": r, "r": r, "fees_usdt": 0.02}
        if not (no_path_every and i % no_path_every == 0):
            c["path"] = {"mfe_r": round(r * 1.8, 3) if r > 0 else 0.3, "mae_r": -0.4}
        out += [o, c]
    return out


def case_recs(rng, rs, ts, mode="paper", p_no=0.15, start=0):
    out = []
    for i, (r, t) in enumerate(zip(rs, ts)):
        bad = rng.random() < p_no
        err = ["chase"] if bad and rng.random() < 0.5 else (["no-confirm"] if bad else [])
        out.append(mk(start + i, sym="SYM%d" % (i % 6), side="long" if i % 2 else "short", r=r, confirmed=rng.random() < 0.6, mode=mode, ts=H.iso(t),
                      setup=("level_reclaim", "sweep_reject", "range_fade")[i % 3], regime=("range", "trend", "volatile")[i % 3],
                      err=err, rule_followed="no" if bad else "yes", range_5m_pct=round(0.5 + (i % 5) * 0.4, 2), stop_pct=round(1.0 + (i % 4) * 0.5, 2)))
    return out


def skip_recs(rng, n, ts, mode="paper", type="skip", start=1000):
    return [mk(start + i, type=type, mode=mode, ts=H.iso(t), whatif={"net_dir_pct": round(rng.gauss(-0.1, 0.5), 3)}, sym="SYM%d" % (i % 6))
            for i, t in enumerate(ts)]


def put_cases(root, lst):
    for c in lst:
        memcases.append_new(c, root=root)


def usage_recs(rng, end=NOW, days=50):
    out = []
    for d in range(days):
        for k in range(3):
            out.append({"t": end - d * 86400 - k * 3600 - 5, "in": rng.randint(2000, 9000), "out": rng.randint(200, 900), "usd": round(rng.uniform(0.001, 0.02), 5),
                        "kind": "call"})
    out.sort(key=lambda r: r["t"])
    return out


def put_policies(root, rng, since_end=NOW):
    conn = store.open_rw(root)
    try:
        rows, n = [], 0
        for pid, mu in (("P5", -0.05), ("P6", 0.0)):
            for k in range(60):
                n += 1
                r = round(rng.gauss(mu, 0.9), 3)
                rows.append(("k%d" % n, pid, 1, "g1", "aaaa0001", since_end - 3600 - n * 600, "AAA", "range_5m", "long", None, None, 1, "tp" if r > 0 else "sl",
                             r, r, 0.1, None, None, 10.0, None))
        conn.executemany("INSERT INTO policy_runs(%s) VALUES(%s)" % (COLS, ",".join("?" * 20)), rows)
        conn.commit()
    finally:
        conn.close()


R2_HYPS = ("range-fade", "range_fade", "Range-Fade-lower-edge", "range-fade", "level_reclaim", "reclaim-level", "liq-cascade-reclaim", "breakout_retest",
           "momentum_chase", None)          # варианты написания сводятся к семействам при чтении (Requirement 19.3); None — сделка без гипотезы


def _iso(t):
    return H.iso(t)


def put_r2(root, rng, mode="paper"):
    """Сценарий r2 (R2.6, задача 20): таблицы цикла в базе знаний, уроки, версии rulebook, смена модели, справка меток и бумажные сделки с гипотезами.
    240 сделок: первые 20 без версии rulebook («до R2»), 100 под R-0001, 120 под R-0002; смена модели после 80-й сделки."""
    from tb.knowledge import store2
    from tb.memory import lessons as LS
    rs = gauss_rs(rng, [(120, 0.12, 0.8), (120, 0.22, 0.8)])
    ts = times(len(rs), 2 * 3600)
    recs = trade_recs(rs, ts, mode)
    for k, r in enumerate(recs):
        hyp = R2_HYPS[(k // 2) % len(R2_HYPS)]
        if r["ev"] == "open" and hyp:
            r["hyp"] = hyp
    _jsonl(root, "trades.jsonl", recs)
    cs = case_recs(rng, rs, ts, mode)
    for i, c in enumerate(cs):
        c["refs"]["journal_id"] = "T%03d-%s" % (i, mode)
        if i >= 20:
            c["src"]["rulebook_ver"] = "R-0001" if i < 120 else "R-0002"
    put_cases(root, cs)
    conn = store.open_rw(root)
    try:
        store2.migrate_v2a(conn, root, explicit=True, backup=False, now=NOW - 30 * 86400)
        for ver, t, why, rules in (("R-0000", ts[0] - 3600, "INIT", []), ("R-0001", ts[20] - 600, "PROMOTE", ["RL-0001"]), ("R-0002", ts[120] - 600, "PROMOTE", ["RL-0001", "RL-0002"])):
            store2.append_row(conn, "rulebook_versions", {"ver": ver, "parent": None, "reason_code": why, "trial_id": None, "rule_ids": rules, "numbers": None, "ts": _iso(t)})
        for i in range(1, 7):
            store2.insert_rows(conn, "lesson_candidates", [{"id": i, "menu_id": "M-%d" % i, "src": "labels", "cond": [{"feature": "coin_class", "op": "=", "value": "meme"}],
                                                            "claim": {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.2}, "env_norm": "mainnet", "ctx_ver": "c1",
                                                            "k_space": 4, "status": "hyp" if i <= 3 else "new", "ts": NOW - i * 86400,
                                                            "lesson_id": "L-%04d" % i if i <= 3 else None, "evidence": {"x": i}}])
        for tid, rid, st, v in (("TR-0001", "RL-0001", "closed", "promote"), ("TR-0002", "RL-0002", "open", None)):
            store2.insert_rows(conn, "trials", [{"trial_id": tid, "rule_id": rid, "lesson_id": "L-0001", "env_norm": "mainnet", "criterion": {"k": 1}, "criterion_h": "0" * 16,
                                                 "policy_id": "P2", "policy_ver": 1, "ctx_ver": "c1", "start_ts": NOW - 20 * 86400, "deadline_ts": NOW + 86400, "state": st, "verdict": v}])
        store2.append_row(conn, "rule_rows", {"rule_id": "RL-0001", "rev": 1, "kind": "skip_when", "status": "active", "from_lesson": "L-0001", "env_norm": "mainnet",
                                              "body": {"cond": []}, "ts": _iso(NOW - 10 * 86400)})
        conn.commit()
    finally:
        conn.close()
    day = 86400
    for lid, st, src, n, eff, diff, se, created, hist, txt in (
            ("L-0001", "confirmed", "labels", 120, 0.22, -0.22, 0.06, NOW - 26 * day, ((NOW - 9 * day, "confirmed"),), "Входы на мем-монетах в азиатскую сессию в среднем хуже"),
            ("L-0002", "hyp", "labels", 48, 0.35, -0.35, 0.12, NOW - 12 * day, (), "Входы против часового тренда при широком спреде хуже обычного"),
            ("L-0003", "hyp", "brain", 12, 0.6, 0.6, 0.3, NOW - 5 * day, (), "После двух стопов подряд лучше пропустить следующий сигнал"),
            ("L-0004", "rejected", "labels", 40, 0.1, -0.1, 0.09, NOW - 20 * day, ((NOW - 4 * day, "rejected"),), "Входы в пятницу вечером хуже обычного"),
            ("L-0005", "expired", "brain", 6, 0.4, 0.4, 0.4, NOW - 40 * day, (), "Ждать закрытия свечи у уровня")):
        ev = {"n_closed": n, "diff": diff, "se": se}
        if src == "labels":
            ev.update(src="labels", sample={"env_norm": "mainnet"})
        LS._write(root, {"schema": LS.SCHEMA, "lesson_id": lid, "rev": 1, "ts": _iso(created), "created_ts": created, "kind": "new", "status": st, "txt": txt,
                         "cond": [{"feature": "coin_class", "op": "=", "value": "meme"}], "claim": {"metric": "mean_r", "dir": "lower" if diff < 0 else "higher", "vs": "base", "effect": eff},
                         "created_by": "code" if src == "labels" else "brain", "contradicts": [], "merged_into": None, "evidence": ev, "conf": 0.2, "expires_ts": None,
                         "history": [{"ts": _iso(created), "status": "hyp", "reason_code": "NEW"}] + [{"ts": _iso(t), "status": s, "reason_code": "X"} for t, s in hist]})
    os.makedirs(os.path.join(root, "state", "brain"), exist_ok=True)
    with open(os.path.join(root, "state", "brain", "swaps.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"seq": 1, "kind": "use", "ts": ts[80] - 900, "result": "ok", "tier": 0, "by": "owner-cli", "from": "model-a", "to": "model-b",
                            "target": ["entry_scout"]}) + "\n")
    os.makedirs(os.path.join(root, "runtime", "learn"), exist_ok=True)
    with open(os.path.join(root, "runtime", "learn", "hyp_ref.json"), "w", encoding="utf-8") as f:
        json.dump({"schema": "tb.hyp_ref/1", "ts": NOW - 1800, "window_days": 14, "a": NOW - 14 * day, "b": NOW, "families": {
            "liq-cascade-reclaim": {"policy": "P4", "policy_ver": 1, "kinds": ["liq_cascade"], "env": {"mainnet": {"n": 42, "n_runs": 61, "mean": 0.12, "lo": -0.03, "hi": 0.27}}},
            "oi-build-squeeze": {"policy": "P2", "policy_ver": 1, "kinds": ["oi_spike"], "env": {"mainnet": {"n": 9, "n_runs": 9, "mean": -0.1, "lo": -0.6, "hi": 0.4}}}}}, f)


def setup_scenario(name, root):
    """Наполняет корень данными сценария. -> (mode, now). Сценарии фиксированы seed."""
    rng = random.Random(1 if name == "nodiff" else 20261102)
    mode = "paper"
    if name == "empty":
        return mode, NOW
    if name == "few":
        rs = gauss_rs(rng, [(12, 0.1, 1.0)])
        ts = times(12)
        _jsonl(root, "trades.jsonl", trade_recs(rs, ts, mode))
        put_cases(root, case_recs(rng, rs, ts, mode))
    elif name == "nodiff":
        rs = gauss_rs(rng, [(80, 0.1, 1.0)])
        ts = times(80)
        _jsonl(root, "trades.jsonl", trade_recs(rs, ts, mode, manual_every=20))
        put_cases(root, case_recs(rng, rs, ts, mode))
        put_cases(root, skip_recs(rng, 40, times(40, 10 * 3600), mode))
    elif name in ("confirmed", "down", "full"):
        parts = [(60, -0.1, 0.8), (60, 0.7, 0.8)] if name != "down" else [(60, 0.6, 0.8), (60, -0.5, 0.8)]
        rs = gauss_rs(rng, parts)
        ts = times(len(rs))
        _jsonl(root, "trades.jsonl", trade_recs(rs, ts, mode, no_path_every=7))
        put_cases(root, case_recs(rng, rs, ts, mode, p_no=0.1 if name != "down" else 0.1))
        put_cases(root, skip_recs(rng, 60, times(60, 8 * 3600), mode))
        if name == "full":
            _jsonl(root, "llm_calls.jsonl", usage_recs(rng))
            put_policies(root, rng)
            write_cfg(root, {"schema": "tb.learning_cfg/1", "memory": {"budget_s": 60.0}})
    elif name == "mode_unknown":
        rs = gauss_rs(rng, [(40, 0.1, 1.0)])
        ts = times(40)
        _jsonl(root, "trades.jsonl", trade_recs(rs, ts, mode))
        put_cases(root, case_recs(rng, rs, ts, mode))
        put_cases(root, skip_recs(rng, 40, times(40, 10 * 3600), "live"))        # режим пропусков по умолчанию live: чужие цифры не подмешиваются
    elif name == "no_labels":
        rs = gauss_rs(rng, [(35, 0.2, 1.0)])
        ts = times(35)
        _jsonl(root, "trades.jsonl", trade_recs(rs, ts, mode))
        put_cases(root, case_recs(rng, rs, ts, mode))
    elif name == "r2":
        put_r2(root, rng, mode)
    elif name == "computing":
        rs = gauss_rs(rng, [(36, 0.2, 1.0)])
        ts = times(36)
        _jsonl(root, "trades.jsonl", trade_recs(rs, ts, mode))
        put_cases(root, case_recs(rng, rs, ts, mode))
        write_cfg(root, {"schema": "tb.learning_cfg/1", "memory": {"budget_s": 0.1}})
    else:
        raise ValueError(name)
    return mode, NOW


def build_scenario(name, root=None):
    """Строит отчёт сценария (без кеша). -> payload с built_ms = 0."""
    own = root is None
    root = root or mk_root()
    try:
        mode, now = setup_scenario(name, root)
        if name == "computing":
            import threading
            from unittest import mock
            from tb.memory import backtest
            gate, real = threading.Event(), backtest.walk_forward

            def slow(*a, **kw):
                gate.wait(30)
                return real(*a, **kw)
            try:
                with mock.patch.object(backtest, "walk_forward", side_effect=slow):
                    out = report.build(root, mode, now)
            finally:
                gate.set()
                from tb.learn import memory_vs_base
                memory_vs_base.clear()
        else:
            out = report.build(root, mode, now)
        out["meta"]["built_ms"] = 0
        return out
    finally:
        if own:
            shutil.rmtree(root, ignore_errors=True)


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def regenerate():
    os.makedirs(FIX, exist_ok=True)
    for name in SCENARIOS:
        with open(fixture_file(name), "w", encoding="utf-8", newline="\n") as f:
            f.write(dumps(build_scenario(name)))


if __name__ == "__main__":
    regenerate()
    print("fixtures regenerated:", ", ".join(fixture_file(n) for n in SCENARIOS))
