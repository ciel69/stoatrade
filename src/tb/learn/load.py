"""Загрузка данных для страницы «Учится ли мозг» (R0.2, задача 2). Только чтение; объекты кеша памяти не изменяются.

brain_trades   сделки мозга режима: expstats.load_trades (учитывает state/exclude_trades.json) минус shadow, ручные (тег manual*), без plan_id, без R;
cases          закрытые сделки-случаи режима (как memory.backtest.closed_trades);
skip_cases     случаи пропусков (skip/idea) с whatif.net_dir_pct, режим которых известен; иначе причина mode_unknown / no_whatif;
usage_rows     журнал вызовов LLM (tb.llm.usage.read_rows), строки kind=fmt отброшены;
source_signature  подпись источников для инвалидации кеша.

Находки по данным (проверено в задаче 2, см. отчёт):
  * случаи `skip` миграция (memory/migrate.build_skip_case) пишет с src.mode = "live" ЖЁСТКО: в decisions.jsonl поля mode нет, режим дня не известен;
  * случаи `idea` берут режим из записи идеи (ideas.add_idea(mode=...)), а при отсутствии поля — тоже "live" по умолчанию;
  * сделка мозга отличается от ручной наличием plan_id (план мозга); ручная имеет тег manual-pick и не имеет plan_id; тегов в нормализованной записи
    expstats нет, поэтому ручные определяются отдельным проходом по открытиям trades.jsonl.
"""
import json
import math
import os

from tb import paths
from tb.core import expstats
from tb.knowledge import markers as KM
from tb.labels import store as labels_store
from tb.learn.stats import iso_epoch
from tb.llm import usage as llm_usage
from tb.memory import backtest, cases as memcases, cfg as memcfg, lessons as memlessons

MODES = ("paper", "testnet", "live")
EXCLUDED_KEYS = ("shadow", "manual", "no_plan", "no_r")
SKIP_TYPES = ("skip", "idea")


def _check_mode(mode):
    if mode not in MODES:
        raise ValueError("mode: %s" % "|".join(MODES))


def _manual_ids(root):
    """id сделок, открытых с тегом manual* (в нормализованной записи expstats тегов нет). Нет файла = пусто."""
    out = set()
    try:
        f = open(paths.state("trades.jsonl", root=root), encoding="utf-8")
    except OSError:
        return out
    with f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict) or r.get("ev") != "open" or not r.get("id"):
                continue
            tags = r.get("tags")
            if isinstance(tags, list) and any(isinstance(t, str) and t.startswith("manual") for t in tags):
                out.add(r["id"])
    return out


def brain_trades(root, mode):
    """-> (сделки мозга по возрастанию (эпоха закрытия, id), excluded {shadow, manual, no_plan, no_r}).
    К записям expstats добавляется поле `t_close` (секунды эпохи; 0, если ts_close не разобрался). Каждая отброшенная сделка считается один раз,
    по первой причине в порядке shadow, manual, no_plan, no_r."""
    _check_mode(mode)
    excluded = dict.fromkeys(EXCLUDED_KEYS, 0)
    raw = expstats.load_trades(mode, root, with_rules=False)
    manual = _manual_ids(root) if raw else set()
    out = []
    for t in raw:
        if t.get("shadow"):
            excluded["shadow"] += 1
        elif t.get("id") in manual:
            excluded["manual"] += 1
        elif not t.get("plan_id"):
            excluded["no_plan"] += 1
        elif t.get("r") is None:
            excluded["no_r"] += 1
        else:
            t = dict(t)
            t["t_close"] = iso_epoch(t.get("ts_close")) or 0
            out.append(t)
    out.sort(key=lambda t: (t["t_close"], str(t.get("id"))))
    return out, excluded


def _mem_cfg(root):
    try:
        return memcfg.load(root)
    except (OSError, ValueError):
        return {}


def _all_cases(root):
    try:
        return memcases.load(root)
    except OSError:
        return []


def cases(root, mode):
    """Закрытые сделки-случаи режима (memory.backtest.closed_trades с конфигом памяти). Объекты общие с кешем памяти: не менять."""
    _check_mode(mode)
    return backtest.closed_trades(_all_cases(root), _mem_cfg(root), mode)


def _num(v):
    return not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)


def skip_cases(root, mode):
    """-> (случаи пропусков режима по возрастанию ts, причина).
    Берутся типы skip и idea с whatif.net_dir_pct, не exclude_from_stats, не inj_suspect, не из ролей exclude_roles, не идеи, ставшие сделкой
    (decision.action == enter или флаг dup_of_trade).
    Режим случая считается известным, если:
      * idea: src.mode == mode (для запроса mode != live случай со значением live НЕ берётся: это умолчание, когда в идее режима нет);
      * skip: src.mode == mode и src.mode != live (миграция пишет live жёстко, режим дня в decisions.jsonl не хранится).
    Если подходящих нет: причина mode_unknown, когда есть случаи с whatif, но режим неизвестен (live по умолчанию), иначе no_whatif.
    Случаи другого режима не подмешиваются никогда."""
    _check_mode(mode)
    ex = tuple(_mem_cfg(root).get("exclude_roles") or ())
    known, ambiguous = [], 0
    for c in _all_cases(root):
        if c.get("type") not in SKIP_TYPES or c.get("exclude_from_stats") or "inj_suspect" in (c.get("flags") or []) or "dup_of_trade" in (c.get("flags") or []):
            continue
        src = c.get("src") or {}
        if src.get("role") in ex or (c.get("decision") or {}).get("action") == "enter":
            continue
        wi = c.get("whatif")
        if not isinstance(wi, dict) or not _num(wi.get("net_dir_pct")):
            continue
        m = src.get("mode", "live")
        if c["type"] == "idea":
            if m == mode:
                known.append(c)
            elif m == "live" and mode != "live":
                ambiguous += 1
        else:
            if m == mode and m != "live":
                known.append(c)
            elif m == "live":
                ambiguous += 1
    known.sort(key=lambda c: (c.get("ts") or "", c.get("case_id") or ""))
    if known:
        return known, None
    return [], ("mode_unknown" if ambiguous else "no_whatif")


def usage_rows(root, since):
    """-> (строки журнала вызовов с t >= since без kind=fmt, {"truncated": bool, "fmt_skipped": int}). Нет файла = ([], ...)."""
    path = llm_usage.calls_file(root)
    try:
        truncated = os.path.getsize(path) > llm_usage.MAX_READ
    except OSError:
        truncated = False
    rows = llm_usage.read_rows(root, since)
    keep = [r for r in rows if r.get("kind") != "fmt"]
    return keep, {"truncated": truncated, "fmt_skipped": len(rows) - len(keep)}


def _stat(path):
    try:
        st = os.stat(path)
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def source_signature(root):
    """(размер, mtime_ns) для trades.jsonl, cases.jsonl, exclude_trades.json, llm_calls.jsonl, config/learning.json и (R2.6, блоки «Уроки» и
    «Версии») lessons.jsonl, brain/swaps.jsonl, knowledge.sqlite с -wal, маркера восстановления базы; нет файла = None."""
    kdb = labels_store.db_path(root)
    return (_stat(paths.state("trades.jsonl", root=root)), _stat(memcases.cases_path(root)), _stat(paths.state("exclude_trades.json", root=root)),
            _stat(llm_usage.calls_file(root)), _stat(paths.config("learning.json", root=root)),
            _stat(memlessons.lessons_path(root)), _stat(paths.state("brain", "swaps.jsonl", root=root)), _stat(kdb), _stat(kdb + "-wal"),
            _stat(KM.restoring_path(root)))
