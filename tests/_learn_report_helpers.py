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
SCENARIOS = ("empty", "few", "nodiff", "confirmed", "down", "mode_unknown", "no_labels", "computing", "full")
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
