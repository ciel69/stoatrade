"""config/learning.json: загрузка со строгой проверкой (R0.2, задача 1).

Файл необязателен. Битый файл, неизвестный ключ или значение вне диапазона не роняют загрузку: поле берётся из умолчаний,
предупреждение `E_CONF:<поле>` уходит в `meta.warnings`. `MIN_N` и `ALPHA` не настраиваются (берутся из tb.core.expstats).
Модуль читает только свой файл конфигурации.
"""
import copy
import json
import re

from tb import paths

SCHEMA = "tb.learning_cfg/1"
DEFAULTS = {"schema": SCHEMA,
            "verdict": {"recent_n": 50, "guard_trades_ratio": 0.5},
            "windows": [50, 200], "series_points": 120, "weeks": 8, "policy_days": 14,
            "baseline_policies": ["P5", "P6"],
            "memory": {"max_trades": 600, "budget_s": 3.0, "ttl_s": 900},
            "cache": {"ttl_s": 60, "min_interval_s": 10},
            "pf_bootstrap": {"b": 500, "seed": 20261102, "level": 0.9}}

_RE_POLICY = re.compile(r"^[A-Za-z0-9_]{1,16}$")


def _int(lo, hi):
    def ok(v):
        return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi
    return ok


def _num(lo, hi):
    def ok(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and lo <= v <= hi
    return ok


def _windows(v):
    return isinstance(v, list) and 1 <= len(v) <= 4 and all(_int(30, 1000)(x) for x in v) and len(set(v)) == len(v)


def _policies(v):
    return isinstance(v, list) and 1 <= len(v) <= 20 and all(isinstance(x, str) and _RE_POLICY.match(x) for x in v) and len(set(v)) == len(v)


# путь поля -> проверка значения
CHECKS = {
    "verdict.recent_n": _int(30, 1000), "verdict.guard_trades_ratio": _num(0.0, 1.0),
    "windows": _windows, "series_points": _int(10, 1000), "weeks": _int(2, 52), "policy_days": _int(1, 365),
    "baseline_policies": _policies,
    "memory.max_trades": _int(10, 100000), "memory.budget_s": _num(0.1, 60.0), "memory.ttl_s": _int(1, 86400),
    "cache.ttl_s": _int(0, 3600), "cache.min_interval_s": _int(0, 600),
    "pf_bootstrap.b": _int(100, 5000), "pf_bootstrap.seed": _int(0, 2 ** 31 - 1), "pf_bootstrap.level": _num(0.5, 0.99),
}


def defaults():
    return copy.deepcopy(DEFAULTS)


def _merge(base, raw, prefix, warns):
    """Накладывает raw на base (оба dict), проверяя каждое поле. Неизвестные ключи и плохие значения -> предупреждение."""
    for k, v in raw.items():
        path = prefix + k
        if k == "schema" and not prefix:
            if v != SCHEMA:
                warns.append("E_CONF:schema")
            continue
        if k not in base:
            warns.append("E_CONF:" + path)
            continue
        if isinstance(base[k], dict):
            if isinstance(v, dict):
                _merge(base[k], v, path + ".", warns)
            else:
                warns.append("E_CONF:" + path)
            continue
        chk = CHECKS.get(path)
        if chk is not None and chk(v):
            base[k] = copy.deepcopy(sorted(v)) if path == "windows" else copy.deepcopy(v)
        else:
            warns.append("E_CONF:" + path)


def load(root=None):
    """-> (конфиг со слиянием умолчаний, [предупреждения]). Нет файла = умолчания без предупреждений; битый файл = умолчания + E_CONF:file."""
    cfg, warns = defaults(), []
    try:
        with open(paths.config("learning.json", root=root), encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return cfg, warns
    except (OSError, ValueError):
        return cfg, ["E_CONF:file"]
    if not isinstance(raw, dict):
        return cfg, ["E_CONF:file"]
    _merge(cfg, raw, "", warns)
    return cfg, warns
