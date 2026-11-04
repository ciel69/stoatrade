"""config/exits.json: строгая загрузка конфига цикла выходов R4 (задача 4; Requirements 16.1, 16.2, 5.1, 1.1, 4.4).

Образец — tb.learnloop.conf: файл проверяется целиком, нужны все ключи схемы; неизвестный ключ, повтор ключа, неверный тип или значение вне
диапазона, нарушение согласованности или битое меню делают конфиг битым. Битый или отсутствующий конфиг цикл выходов НЕ включает: `load`
возвращает выключенный конфиг `DISABLED` (`enabled=false`, `promotion.mode="off"`, порогов нет) и список ошибок `E_CONF:<путь>`
(Requirement 16.1; исполнитель тогда применяет пол, когда истечёт аренда).

Меню (`menu`, design.md «Меню первой итерации»): каждый вариант при загрузке нормализуется с полом (`exitpol.normalize`), проходит
схему (`exitpol.validate`) и проверку «не слабее пола» (`exitpol.weaker_than`); ошибка варианта — `E_CONF:menu.<id>:<код exitpol>`.
Пол — `exitpol.floor(auto_protect)` действующих лимитов исполнителя (`tb.core.exec_cfg.load`) или явный аргумент `floor_policy`.

Согласованность: `paths.hold_mult` (окно пути H, Requirement 1.1) не меньше 1.0 и не меньше наибольшего `timeout.hold_frac` меню;
`search.min_clusters` = ⌈0.75 × `trial.n_target`⌉ (design.md «Мощность и сроки»); `brain.min_n` <= `brain.n`; вариантов не больше
`search.max_k`, пары (id, ver) различны.

Числовых умолчаний в коде нет: значения живут только в config/exits.json (block_h = 24, n_target = 30, min_clusters = 23 — ревизия
design.md 2026-11-04 по замеру задачи 2). Хеш конфига (`config_hash`) записывается в критерий испытания и числа поиска (Requirement 16.2).
Модуль только читает свой файл и лимиты исполнителя; ничего не пишет.
"""
import copy
import hashlib
import json
import math
import re
from fractions import Fraction

from tb import paths
from tb.core import exec_cfg
from tb.learnloop import conf as LC
from tb.logic import exitpol as XP

SCHEMA = "tb.exits_cfg/1"
FILE = "exits.json"
PROMOTION_MODES = ("off", "auto")
BRAIN_MODES = ("paper", "testnet", "live")
BLOCK_H = (1, 4, 24)                    # Requirement 4.4: час, 4 часа или сутки
FAMILIES = ("lock", "be", "partial", "trail_atr", "stall", "decay", "timeout", "near_tp", "combo")
MIN_CLUSTERS_SHARE = Fraction(3, 4)     # design.md: min_clusters поиска = 0.75 × n_target (формула, не порог)

DISABLED = {"schema": SCHEMA, "enabled": False, "promotion": {"mode": "off"}}

_RE_POLICY = re.compile(r"^[A-Za-z0-9_]{1,16}$")
_RE_VAR = re.compile(r"^[A-Z][A-Z0-9]{0,7}$")


class ConfError(ValueError):
    """Конфиг битый; `errors` — список кодов `E_CONF:<путь>`."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("config/exits.json: " + ", ".join(self.errors))


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


def _enum(values):
    def ok(v):
        return isinstance(v, str) and v in values
    return ok


def _alpha(v):          # уровень значимости (0, 0.5]
    return _num(0.0, 0.5, lo_open=True)(v)


def _share(v):          # доля (0, 1]
    return _num(0.0, 1.0, lo_open=True)(v)


def _r(v):              # величина в R [0, 10]
    return _num(0.0, 10.0)(v)


def _policies(v):       # политики меток с путями: 1..8 различных id
    return (isinstance(v, list) and 1 <= len(v) <= 8 and all(isinstance(x, str) and _RE_POLICY.match(x) for x in v)
            and len(set(v)) == len(v))


def _looks(v):          # точки сторожа: 1..10 целых, строго по возрастанию
    return (isinstance(v, list) and 1 <= len(v) <= 10 and all(_int(1, 100000)(x) for x in v)
            and all(a < b for a, b in zip(v, v[1:])))


def _block_h(v):
    return _int(1, 24)(v) and v in BLOCK_H


def _schema(v):
    return v == SCHEMA


def _menu(v):           # структура списка; варианты проверяет _menu_errors
    return isinstance(v, list) and len(v) >= 1


_DAYS = _int(1, 3650)
_SEC = _int(1, 86400)
_N = _int(1, 1000000)

# Схема: вложенный dict повторяет структуру файла, лист — проверка значения. Все ключи обязательны.
SPEC = {
    "schema": _schema, "enabled": _bool,
    "paths": {"policies": _policies, "keep_days": _DAYS, "hold_mult": _num(1.0, 3.0), "reserve_min": _int(0, 1440),
              "post_close_min": _int(0, 1440), "trade_retry_h": _int(1, 720)},
    "unit": {"block_h": _block_h},
    "eval": {"every_s": _int(60, 86400), "max_per_pass": _int(1, 10000000), "keep_days_menu": _DAYS},
    "maker": {"through_ticks": _int(0, 100), "through_bps": _num(0.0, 100.0), "wait_s": _int(1, 3600)},
    "search": {"every_days": _DAYS, "window_days": _DAYS, "max_k": _int(1, 100), "perm_b": _int(100, 1000000), "alpha": _alpha,
               "min_effect_r": _r, "min_clusters": _int(2, 1000000)},
    "trial": {"n_target": _int(2, 1000000), "max_parallel": _int(1, 10), "alpha": _alpha, "min_effect_r": _r, "max_days": _DAYS},
    "tail": {"mfe_r": _num(0.0, 10.0, lo_open=True), "keep": _share, "min_n": _N},
    "robust": {"tol_r": _r},
    "brain": {"n": _N, "min_n": _N, "tol_r": _r, "mode": _enum(BRAIN_MODES)},
    "promotion": {"mode": _enum(PROMOTION_MODES), "wait_days": _DAYS, "watch_fresh_h": _int(1, 168)},
    "watch": {"looks": _looks, "weekly_window": _N, "alpha": _alpha, "alpha_weekly": _alpha, "rollback_delta_r": _r,
              "brain_min_n": _N, "brain_delta_r": _r, "brain_alpha": _alpha, "sim_gap_r": _r, "sim_gap_days": _DAYS, "sim_gap_min_n": _N},
    "lease": {"lease_h": _int(1, 720)},
    "cooldown_days": _DAYS,
    "menu_ver": _N,
    "menu": _menu,
}

VARIANT_KEYS = ("id", "ver", "family", "policy")


# --- проверка структуры ---

def _walk(spec, raw, prefix, errs):
    for k in raw:
        if k not in spec:
            errs.append("E_CONF:" + prefix + str(k))
    for k, chk in spec.items():
        path = prefix + k
        if k not in raw:
            errs.append("E_CONF:" + path)
            continue
        v = raw[k]
        if isinstance(chk, dict):
            if isinstance(v, dict):
                _walk(chk, v, path + ".", errs)
            else:
                errs.append("E_CONF:" + path)
        elif not chk(v):
            errs.append("E_CONF:" + path)


def _family_ok(fam, policy):
    comps = [k for k in policy if k not in ("schema", "scope", "min_profit_r")]
    if fam == "combo":
        return len(comps) >= 2
    return comps == [fam]


def _menu_errors(menu, floor_policy):
    errs, seen = [], set()
    for i, it in enumerate(menu):
        if not isinstance(it, dict):
            errs.append("E_CONF:menu[%d]" % i)
            continue
        bad = [k for k in it if k not in VARIANT_KEYS] + [k for k in VARIANT_KEYS if k not in it]
        vid = it.get("id")
        if bad or not isinstance(vid, str) or not _RE_VAR.match(vid):
            errs.append("E_CONF:menu[%d]" % i)
            continue
        where = "E_CONF:menu.%s" % vid
        if not _int(1, 1000000)(it["ver"]):
            errs.append(where + ".ver")
        elif (vid, it["ver"]) in seen:
            errs.append(where + ".ver")
        seen.add((vid, it["ver"]))
        pol = it["policy"]
        if not isinstance(pol, dict) or not pol:
            errs.append(where + ".policy")
            continue
        if it["family"] not in FAMILIES or not _family_ok(it["family"], pol):
            errs.append(where + ".family")
        n = XP.normalize(pol, floor_policy)
        for code in XP.validate(n) + XP.weaker_than(n, floor_policy):
            errs.append("%s:%s" % (where, code))
    return errs


def max_timeout_frac(menu):
    """Наибольший timeout.hold_frac среди вариантов меню (0, если тайм-аута нет)."""
    out = 0.0
    for it in menu or []:
        to = ((it or {}).get("policy") or {}).get("timeout") if isinstance(it, dict) else None
        v = to.get("hold_frac") if isinstance(to, dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            out = max(out, float(v))
    return out


def min_clusters_formula(n_target):
    """design.md «Мощность и сроки»: min_clusters = ⌈0.75 × n_target⌉; 30 -> 23, 200 -> 150."""
    return math.ceil(MIN_CLUSTERS_SHARE * Fraction(n_target))


def _cross(cfg):
    errs = []
    h = cfg["paths"]["hold_mult"]
    if h < 1.0 or h < max_timeout_frac(cfg["menu"]) - 1e-12:
        errs.append("E_CONF:paths.hold_mult")
    if cfg["search"]["min_clusters"] != min_clusters_formula(cfg["trial"]["n_target"]):
        errs.append("E_CONF:search.min_clusters")
    if cfg["brain"]["min_n"] > cfg["brain"]["n"]:
        errs.append("E_CONF:brain.min_n")
    if len(cfg["menu"]) > cfg["search"]["max_k"]:
        errs.append("E_CONF:menu")
    return errs


def check(raw, floor_policy):
    """-> [ошибки] для разобранного JSON при поле `floor_policy`; пустой список = конфиг годен."""
    if not isinstance(raw, dict):
        return ["E_CONF:file"]
    errs = []
    _walk(SPEC, raw, "", errs)
    if errs:
        return errs
    errs.extend(_menu_errors(raw["menu"], floor_policy))
    if not errs:
        errs.extend(_cross(raw))
    return errs


def disabled():
    return copy.deepcopy(DISABLED)


def current_floor(root=None):
    """Пол защиты из действующих лимитов исполнителя (exec_limits.auto_protect) -> политика или None (лимиты не читаются)."""
    try:
        lim = exec_cfg.load(root)
    except (ValueError, OSError):
        return None
    return XP.floor(lim.get("auto_protect"))


def load(root=None, floor_policy=None):
    """-> (конфиг, [ошибки]). Годный файл -> (его содержимое, []). Нет файла -> (DISABLED, ["E_CONF:nofile"]); битый файл -> (DISABLED,
    [ошибки]); лимиты исполнителя не читаются (пол неизвестен) -> (DISABLED, ["E_CONF:floor"]). Конфиг с ошибками цикл не включает."""
    try:
        with open(paths.config(FILE, root=root), encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return disabled(), ["E_CONF:nofile"]
    except (OSError, UnicodeDecodeError):
        return disabled(), ["E_CONF:file"]
    try:
        raw = LC.parse(text)
    except ValueError:
        return disabled(), ["E_CONF:file"]
    fl = current_floor(root) if floor_policy is None else floor_policy
    if not isinstance(fl, dict):
        return disabled(), ["E_CONF:floor"]
    errs = check(raw, fl)
    if errs:
        return disabled(), errs
    return raw, []


def load_strict(root=None, floor_policy=None):
    """Как load, но битый или отсутствующий конфиг -> ConfError."""
    cfg, errs = load(root, floor_policy)
    if errs:
        raise ConfError(errs)
    return cfg


def is_enabled(cfg):
    return isinstance(cfg, dict) and cfg.get("enabled") is True


def promotion_mode(cfg):
    """Режим продвижения; всё, кроме явного "auto", считается "off"."""
    p = cfg.get("promotion") if isinstance(cfg, dict) else None
    return "auto" if isinstance(p, dict) and p.get("mode") == "auto" else "off"


def variants(cfg, floor_policy):
    """-> [{"id", "ver", "family", "policy" (нормализованная с полом), "policy_h"}] меню годного конфига в порядке файла."""
    out = []
    for it in (cfg or {}).get("menu") or []:
        n = XP.normalize(it["policy"], floor_policy)
        out.append({"id": it["id"], "ver": it["ver"], "family": it["family"], "policy": n, "policy_h": XP.policy_hash(n)})
    return out


def config_hash(cfg):
    """sha256 канонического JSON конфига (ключи по порядку, без пробелов): в критерий испытания и числа поиска (Requirement 16.2).
    Не зависит от форматирования файла и порядка ключей."""
    blob = json.dumps(cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
