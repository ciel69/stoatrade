"""config/learnloop.json: строгая загрузка конфига цикла обучения R2 (задача 5; Requirements 4.2, 15.3).

Строгость: файл проверяется целиком. Нужны все ключи схемы; неизвестный ключ, повтор ключа, неверный тип или значение вне
диапазона, нарушение согласованности и `verify.min_n_menu`, не равный формуле достижимости Requirement 4.2, делают конфиг
битым. Битый или отсутствующий конфиг цикл НЕ включает: `load` возвращает выключенный конфиг `DISABLED`
(`enabled=false`, `promotion.mode="off"`, порогов нет) и список ошибок `E_CONF:<вид>:<путь>`.

Числовых умолчаний в коде нет: пороги, в том числе `verify.alpha`, живут только в config/learnloop.json.
Хеш конфига (`config_hash`) записывается в критерий испытания как версия параметров (отдельной таблицы версий параметров нет).
Модуль читает только свой файл и ничего не пишет.
"""
import copy
import hashlib
import json
import math
import re
from fractions import Fraction

from tb import paths

SCHEMA = "tb.learnloop_cfg/1"
FILE = "learnloop.json"
PROMOTION_MODES = ("off", "auto")

# выключенный конфиг: возвращается при битом или отсутствующем файле
DISABLED = {"schema": SCHEMA, "enabled": False, "allow_size_rules": False, "promotion": {"mode": "off"}}

_RE_POLICY = re.compile(r"^[A-Za-z0-9_]{1,16}$")
_RE_DAILY_AFTER = re.compile(r"^([01]\d|2[0-3]):[0-5]\dZ$")


class ConfError(ValueError):
    """Конфиг битый; `errors` — список кодов `E_CONF:<вид>:<путь>`."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("config/learnloop.json: " + ", ".join(self.errors))


# --- проверки значений ---

def _bool(v):
    return isinstance(v, bool)


def _int(lo, hi):
    def ok(v):
        return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi
    return ok


def _num(lo, hi, lo_open=False):
    def ok(v):
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            return False
        return (lo < v if lo_open else lo <= v) and v <= hi
    return ok


def _share(v):          # доля (0, 1]
    return _num(0.0, 1.0, lo_open=True)(v)


def _alpha(v):          # уровень значимости (0, 0.5]
    return _num(0.0, 0.5, lo_open=True)(v)


def _mode(v):
    return isinstance(v, str) and v in PROMOTION_MODES


def _policy(v):
    return isinstance(v, str) and bool(_RE_POLICY.match(v))


def _daily_after(v):
    return isinstance(v, str) and bool(_RE_DAILY_AFTER.match(v))


def _looks(v):          # точки досмотра сторожа: 1..10 целых, строго по возрастанию
    return (isinstance(v, list) and 1 <= len(v) <= 10 and all(_int(1, 100000)(x) for x in v)
            and all(a < b for a, b in zip(v, v[1:])))


def _schema(v):
    return v == SCHEMA


_DAYS = _int(1, 365)
_SEC = _int(1, 86400)
_N = _int(1, 1000000)

# Схема: вложенный dict повторяет структуру файла, лист — проверка значения. Все ключи обязательны.
SPEC = {
    "schema": _schema, "enabled": _bool, "allow_size_rules": _bool,
    "promotion": {"mode": _mode, "warmup_days": _DAYS, "warmup_window_days": _DAYS, "warmup_stuck_days": _DAYS,
                  "backup_max_age_h": _int(1, 720), "watch_fresh_h": _int(1, 168), "step_error_max": _int(1, 1000),
                  "loss_cap_wait_days": _DAYS},
    "lease": {"lease_h": _int(1, 720)},
    "restore": {"restore_ack_timeout_s": _int(1, 3600)},
    "days": {"day_min_work_h": _int(1, 24), "observe_ok_share": _share},
    "blocklist": {"cooldown_days": _DAYS},
    "apply": {"event_max_age_s": _SEC, "feat_missing_max": _num(0.0, 1.0)},
    "stale_min": _int(1, 10080), "lock_wait_s": _int(1, 600), "events_per_day": _N,
    "verify": {"policy": _policy, "menu_every_days": _DAYS, "discovery_days": _DAYS, "discovery_days_min": _DAYS,
               "min_n_menu": _N, "holdout_days": _DAYS, "min_n_hyp": _int(2, 1000000), "alpha": _alpha,
               "feat_cover_min": _share, "labels_max_new_per_day": _int(1, 100)},
    "clusters": {"grace_min": _int(0, 1440), "cluster_close_max_h": _int(1, 720)},
    "trial": {"n_target": _int(2, 1000000), "max_days": _DAYS, "min_effect_r": _num(0.0, 10.0),
              "max_trade_loss_share": _share, "max_parallel": _int(1, 100), "wait_max_days": _DAYS,
              "reach_factor": _share, "reach_lookback_days": _DAYS, "overlap_share_max": _num(0.0, 1.0)},
    "rulebook": {"max_active_rules": _int(1, 1000)},
    "watch": {"looks": _looks, "weekly_window": _N, "alpha_watch": _alpha, "alpha_watch_weekly": _alpha,
              "rollback_delta_r": _num(0.0, 10.0), "disable_trade_loss_share": _share,
              "disable_min_considered": _N, "starvation_window_days": _DAYS},
    "intervals": {"observe_s": _SEC, "verdict_s": _SEC, "watch_s": _SEC, "reconcile_s": _SEC, "hypref_s": _SEC,
                  "daily_after": _daily_after},
    "hypref": {"window_days": _DAYS},
    "second_iteration": {"prefilter_signals": _N, "prefilter_cases": _N,
                         "port": {"llm_job_ttl_s": _SEC, "claim_ttl_s": _SEC, "llm_wait_s": _SEC},
                         "brain_ref": {"min_n": _N, "min_diff_r": _num(0.0, 10.0), "brain_window_days": _DAYS},
                         "freeze": {"freeze_trade_share": _share, "freeze_signal_share": _share,
                                    "freeze_window_days": _DAYS},
                         "violations": {"brain_hits_min": _N}},
}


# --- формула и согласованность ---

def _exact(x):
    """Точное рациональное значение числа из JSON (0.8 -> 4/5), чтобы ceil не зависел от ошибок округления float."""
    return Fraction(repr(x)) if isinstance(x, float) else Fraction(x)


def min_n_menu_formula(n_target, reach_factor, max_days, discovery_days):
    """Requirement 4.2: ceil(n_target / (reach_factor × max_days) × discovery_days); 150, 0.8, 30, 14 -> 88."""
    return math.ceil(_exact(n_target) / (_exact(reach_factor) * _exact(max_days)) * _exact(discovery_days))


def _cross(cfg):
    errs = []
    v, t, p = cfg["verify"], cfg["trial"], cfg["promotion"]
    if v["discovery_days_min"] > v["discovery_days"]:
        errs.append("E_CONF:cross:verify.discovery_days_min")
    if p["warmup_days"] > p["warmup_window_days"]:
        errs.append("E_CONF:cross:promotion.warmup_days")
    if v["min_n_menu"] != min_n_menu_formula(t["n_target"], t["reach_factor"], t["max_days"], v["discovery_days"]):
        errs.append("E_CONF:formula:verify.min_n_menu")
    return errs


# --- проверка структуры ---

def _walk(spec, raw, prefix, errs):
    for k in raw:
        if k not in spec:
            errs.append("E_CONF:unknown:" + prefix + k)
    for k, chk in spec.items():
        path = prefix + k
        if k not in raw:
            errs.append("E_CONF:missing:" + path)
            continue
        v = raw[k]
        if isinstance(chk, dict):
            if isinstance(v, dict):
                _walk(chk, v, path + ".", errs)
            else:
                errs.append("E_CONF:type:" + path)
        elif not chk(v):
            errs.append("E_CONF:value:" + path)


def check(raw):
    """-> [ошибки] для разобранного JSON; пустой список = конфиг годен."""
    if not isinstance(raw, dict):
        return ["E_CONF:file"]
    errs = []
    _walk(SPEC, raw, "", errs)
    if not errs:
        errs.extend(_cross(raw))
    return errs


def _no_dups(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError("duplicate key %r" % k)
        seen[k] = v
    return seen


def _no_const(name):
    raise ValueError("non-finite number %s" % name)


def parse(text):
    """Разбор JSON без повторов ключей и без NaN/Infinity; ValueError при нарушении."""
    return json.loads(text, object_pairs_hook=_no_dups, parse_constant=_no_const)


def disabled():
    return copy.deepcopy(DISABLED)


def load(root=None):
    """-> (конфиг, [ошибки]). Годный файл -> (его содержимое, []). Нет файла -> (DISABLED, ["E_CONF:nofile"]);
    битый файл -> (DISABLED, [ошибки]). Конфиг с ошибками цикл не включает."""
    try:
        with open(paths.config(FILE, root=root), encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return disabled(), ["E_CONF:nofile"]
    except (OSError, UnicodeDecodeError):
        return disabled(), ["E_CONF:file"]
    try:
        raw = parse(text)
    except ValueError:
        return disabled(), ["E_CONF:file"]
    errs = check(raw)
    if errs:
        return disabled(), errs
    return raw, []


def load_strict(root=None):
    """Как load, но битый или отсутствующий конфиг -> ConfError."""
    cfg, errs = load(root)
    if errs:
        raise ConfError(errs)
    return cfg


def is_enabled(cfg):
    return isinstance(cfg, dict) and cfg.get("enabled") is True


def promotion_mode(cfg):
    """Режим продвижения; всё, кроме явного "auto", считается "off"."""
    p = cfg.get("promotion") if isinstance(cfg, dict) else None
    return "auto" if isinstance(p, dict) and p.get("mode") == "auto" else "off"


def config_hash(cfg):
    """sha256 канонического JSON конфига (ключи по порядку, без пробелов): версия параметров в критерии испытания.
    Не зависит от форматирования файла и порядка ключей."""
    blob = json.dumps(cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
