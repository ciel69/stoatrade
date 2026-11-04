"""Блок «Память против базы» (R0.2, задача 10).

Ретро-тест берётся из tb.memory.backtest (closed_trades + walk_forward), формулы не дублируются. Тяжёлый walk-forward идёт в фоне через
tb.learn.cache.Flight (один поток на ключ), ожидание ограничено memory.budget_s; не уложились: status=computing, результат кладётся в кеш
на memory.ttl_s по ключу (корень, режим, подпись хранилища случаев на момент начала: cases.jsonl или case_rows по store), повторный build
отдаёт готовое. Сделок больше memory.max_trades: walk_forward не запускается (too_many_for_request). Файлы не меняются, уроки не читаются.
"""
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeout

from tb.core import expstats
from tb.learn import load, stats
from tb.learn.cache import MEMORY
from tb.memory import backtest, cases as memcases

ID = "memory"
TITLE = "Память против базы"
MIN_EVAL = 30
LVL = 0.95

HISTORY = {"status": "stage", "stage": 3, "roadmap": "R3.4", "note": "история по дням появится на этапе 3"}

_MU = threading.Lock()
_DONE = {}   # key -> (t_monotonic, результат walk_forward)


def _empty():
    return {"id": ID, "title": TITLE, "status": "no_data", "reason": None, "note": "", "n_trades": 0, "n_eval": 0, "covered": None,
            "brier_mem": None, "brier_base": None, "d_brier": None, "acc_mem": None, "acc_base": None, "better": None,
            "history": dict(HISTORY)}


def _sig(root):
    return memcases.signature(root)          # store="jsonl": (размер, mtime_ns) cases.jsonl, как прежде; "sqlite": последняя строка case_rows


def flight_key(root, mode):
    """Ключ Flight/кеша расчёта: (корень, режим, подпись хранилища случаев). По нему report/dashapi ждут готовности блока `computing`."""
    return "mem|%s|%s|%s" % (root, mode, _sig(root))


def _fresh(key, ttl):
    with _MU:
        ent = _DONE.get(key)
    if ent is None:
        return None
    return ent[1] if 0 <= time.monotonic() - ent[0] < ttl else None


def clear():
    """Сбросить кеш готовых результатов (тесты)."""
    with _MU:
        _DONE.clear()
    MEMORY.clear()


def _fill(out, wf):
    """Числа блока из результата walk_forward (значения округлены самим backtest)."""
    n_tr, n_eval = int(wf.get("n_trades") or 0), int(wf.get("n_eval") or 0)
    out["n_trades"], out["n_eval"] = n_tr, n_eval
    out["covered"] = stats.wilson_est(n_eval, n_tr, LVL) if n_tr > 0 else None
    for k in ("brier_mem", "brier_base", "acc_mem", "acc_base"):
        out[k] = wf.get(k) if n_eval else None
    d, se = wf.get("d_brier_mean"), wf.get("d_brier_se")
    if n_eval and d is not None:
        if se is not None and n_eval >= 2:
            h = expstats.t_quantile(1.0 - (1.0 - LVL) / 2.0, n_eval - 1) * se
            out["d_brier"] = stats.est(d, d - h, d + h, n_eval, LVL)
        else:
            out["d_brier"] = stats.est(d, n=n_eval, lvl=LVL)
        lo = out["d_brier"]["lo"]
        out["better"] = bool(lo is not None and lo > 0)
    if n_eval < MIN_EVAL:
        out["status"], out["reason"] = "few_data", "n_eval_lt_min"
        out["note"] = "оценок памяти %d из %d нужных: выводов делать рано" % (n_eval, MIN_EVAL)
    else:
        out["status"], out["reason"] = "ok", None
        out["note"] = "положительная разность Brier: память точнее базовой частоты"
    return out


def build(ctx):
    """-> блок memory. ctx: root, mode, cfg (learn.conf), cases (необязательно: закрытые случаи режима)."""
    out = _empty()
    mcfg = ctx.cfg["memory"]
    memory_cfg = load._mem_cfg(ctx.root)
    allc = getattr(ctx, "cases", None)
    if allc is None:
        allc = load._all_cases(ctx.root)
    trades = backtest.closed_trades(allc, memory_cfg, ctx.mode)      # новый список: снимок на момент вызова
    bc = backtest._bc(memory_cfg)
    out["n_trades"] = len(trades)
    if len(trades) < bc["min_train"]:
        out["reason"], out["note"] = "no_trades", "закрытых сделок в памяти меньше %d: проверять нечего" % bc["min_train"]
        return out
    if len(trades) > mcfg["max_trades"]:
        out["reason"], out["note"] = "too_many_for_request", "считается по расписанию (этап 3)"
        return out

    key = flight_key(ctx.root, ctx.mode)
    wf = _fresh(key, mcfg["ttl_s"])
    if wf is None:
        def job():
            res = backtest.walk_forward(trades, memory_cfg, bc)
            with _MU:
                _DONE[key] = (time.monotonic(), res)
            return res
        fut = MEMORY.start(key, job)
        try:
            wf = fut.result(timeout=mcfg["budget_s"])
        except FutureTimeout:
            out["status"], out["reason"] = "computing", "computing"
            out["note"] = "считается в фоне, результат придёт отдельным сообщением"
            return out
    return _fill(out, wf)
