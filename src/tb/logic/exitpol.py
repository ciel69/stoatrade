"""Политика выхода `tb.exit_policy/1` (R4.1, Requirement 2; design.md «Формальное „не слабее пола“», «Data Models»). Чистая логика.

Политика — словарь компонентов сопровождения поверх стопа и тейков плана. Пол защиты владельца строится из `exec_limits.auto_protect`
функцией `floor` и не хранится в базе. Компоненты делятся на две группы:

  * замещающие (`REPLACING`: `be`, `lock`, `min_profit_r`) — у пола есть такие же; вариант без них наследует их от пола (`normalize`),
    с ними — обязан быть не слабее пола (`weaker_than`, иначе `E_EXIT_WEAKER:<компонент>`);
  * складывающиеся (`partial`, `trail_atr`, `stall`, `decay`, `timeout`, `near_tp`) — дают дополнительные правила после правил пола;
    стоп схлопывается в самый строгий (`rules.evaluate_manage`), закрытия только уменьшают остаток. В `weaker_than` не проверяются.

`compile` переводит композицию «политика ∘ пол» в правила `manage` того же вида, что `rules._auto_rules` (`auto_be`, `auto_lock`) плюс
`xp_*`. Для политики, равной полу, результат совпадает с `_auto_rules` по составу и параметрам (Correctness Property 5). Правила `xp_*`
используют условия `mfe_r`, `since_peak_min` и режимы трейла `mfe_tiers`, `atr_entry`, `pnl_keep`, которые добавляет задача 5
(`tb.logic.conds`, `rules.trail_candidate`); стоповые правила `xp_*` несут `do.min_profit_r` — пол прибыли композиции.

Время (`stall`, `decay`, `timeout`) задаётся долями `ctx.max_hold_min`, зафиксированного на входе (Requirement 2.6): `compile` переводит
доли в минуты. Нет нужного поля `ctx` (срок, ATR на входе, строка таблицы `near_tp`) — компонент не действует.
"""
import copy
import hashlib
import json
import math

SCHEMA = "tb.exit_policy/1"
COMPONENTS = ("be", "lock", "partial", "trail_atr", "stall", "decay", "timeout", "near_tp", "min_profit_r", "scope")
REPLACING = ("be", "lock", "min_profit_r")
ADDITIVE = ("partial", "trail_atr", "stall", "decay", "timeout", "near_tp")
EXEC = ("maker", "taker")
SCOPES = ("all",)
PROFIT_MIN_R = 0.2           # то же умолчание, что rules.PROFIT_MIN_R: секция auto_protect без min_profit_r даёт пол прибыли 0.2R
_EPS = 1e-12

# диапазоны design.md «Data Models» (включительно)
RANGES = {
    "be.r": (0.3, 1.5),
    "lock.at_r": (0.5, 5.0), "lock.keep": (0.3, 0.95), "lock.n": (1, 4),
    "partial.at_r": (0.3, 3.0), "partial.frac": (0.1, 0.6), "partial.n": (1, 3), "partial.sum": (0.0, 0.6),
    "trail_atr.activate_r": (0.5, 3.0), "trail_atr.mult": (1.0, 5.0),
    "stall.hold_frac": (0.1, 1.0), "stall.min_r": (0.2, 2.0), "stall.keep": (0.5, 0.95),
    "decay.hold_frac": (0.2, 1.0), "decay.max_mfe_r": (0.1, 1.5), "decay.max_pnl_r": (-0.5, 0.5),
    "timeout.hold_frac": (0.5, 1.5),
    "near_tp.q": (0.2, 0.8), "near_tp.frac": (0.1, 0.5), "near_tp.min_r": (0.3, 2.0), "near_tp.level": (0.0, 20.0),
    "min_profit_r": (0.0, 1.0),
}
_KEYS = {
    "be": ("r",), "trail_atr": ("activate_r", "mult"), "stall": ("hold_frac", "min_r", "keep"),
    "decay": ("hold_frac", "max_mfe_r", "max_pnl_r", "exec"), "timeout": ("hold_frac", "exec"),
    "near_tp": ("q", "frac", "min_r", "exec", "table"),
}
_OPTIONAL = {"near_tp": ("table",)}


# ---------------------------------------------------------------- мелочи
def _num(x):
    """Число (не bool, конечное) или None."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    v = float(x)
    return v if math.isfinite(v) else None


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _steps(policy):
    """-> [(at_r, keep)] ступеней lock (по возрастанию at_r, как в политике)."""
    out = []
    for st in (policy or {}).get("lock") or []:
        if isinstance(st, dict) and _f(st.get("at_r")) is not None and _f(st.get("keep")) is not None:
            out.append((float(st["at_r"]), float(st["keep"])))
    return sorted(out)


def _keep(steps, m):
    """Ступенчатая доля удержания при MFE m: наибольший keep ступеней с at_r <= m (0, если ни одна не достигнута)."""
    k = 0.0
    for a, kp in steps:
        if a <= m + _EPS:
            k = max(k, kp)
    return k


# ---------------------------------------------------------------- пол
def floor(auto_protect):
    """Пол защиты из `exec_limits.auto_protect` -> политика `tb.exit_policy/1`. Те же условия, что `rules._auto_rules`:
    `be` при be_r > 0, `lock` одной ступенью при trail_activate_r > 0 и 0 < trail_lock_frac < 1, `min_profit_r` (умолчание 0.2, как
    `rules.profit_floor`). Выключенная или отсутствующая секция -> пустая политика `{"schema": SCHEMA}` (только начальный стоп).

    >>> floor({"enabled": True, "be_r": 0.7, "trail_activate_r": 1.0, "trail_lock_frac": 0.5, "min_profit_r": 0.2})["lock"]
    [{'at_r': 1.0, 'keep': 0.5}]
    >>> floor({"enabled": False, "be_r": 0.7})
    {'schema': 'tb.exit_policy/1'}
    """
    ap = auto_protect
    if not isinstance(ap, dict) or not ap.get("enabled"):
        return {"schema": SCHEMA}
    out = {"schema": SCHEMA, "scope": "all"}
    be = _f(ap.get("be_r"))
    if be is not None and be > 0:
        out["be"] = {"r": be}
    act, frac = _f(ap.get("trail_activate_r")), _f(ap.get("trail_lock_frac"))
    if act is not None and act > 0 and frac is not None and 0 < frac < 1:
        out["lock"] = [{"at_r": act, "keep": frac}]
    mr = _f(ap.get("min_profit_r"))
    out["min_profit_r"] = PROFIT_MIN_R if mr is None else mr
    return out


# ---------------------------------------------------------------- нормализация, схема, проверка пола
def normalize(policy, floor_policy):
    """-> новая политика: отсутствующие `be`, `lock`, `min_profit_r` берутся из пола, `schema` проставляется. Вход не меняется.
    Идемпотентна: normalize(normalize(P, F), F) == normalize(P, F).

    >>> F = floor({"enabled": True, "be_r": 0.7, "trail_activate_r": 1.0, "trail_lock_frac": 0.5, "min_profit_r": 0.2})
    >>> sorted(normalize({"be": {"r": 0.5}}, F))
    ['be', 'lock', 'min_profit_r', 'schema']
    """
    out = copy.deepcopy(policy) if isinstance(policy, dict) else {}
    fl = floor_policy if isinstance(floor_policy, dict) else {}
    out.setdefault("schema", SCHEMA)
    for k in REPLACING:
        if k not in out and k in fl:
            out[k] = copy.deepcopy(fl[k])
    return out


def _rng(errs, path, v, key):
    lo, hi = RANGES[key]
    n = _num(v)
    if n is None or n < lo - _EPS or n > hi + _EPS:
        errs.append("E_EXIT_SCHEMA:" + path)
        return None
    return n


def _exec(errs, path, v):
    if v not in EXEC:
        errs.append("E_EXIT_SCHEMA:" + path)


def _dict_keys(errs, path, d, comp):
    if not isinstance(d, dict):
        errs.append("E_EXIT_SCHEMA:" + path)
        return False
    allowed = _KEYS[comp]
    for k in sorted(d):
        if k not in allowed:
            errs.append("E_EXIT_SCHEMA:%s.%s" % (path, k))
    for k in allowed:
        if k not in d and k not in _OPTIONAL.get(comp, ()):
            errs.append("E_EXIT_SCHEMA:%s.%s" % (path, k))
    return True


def validate(policy):
    """-> [] или ["E_EXIT_SCHEMA:<путь>", ...] (порядок детерминирован). Диапазоны design.md «Data Models», включительно.

    >>> validate({"schema": SCHEMA, "be": {"r": 0.7}})
    []
    >>> validate({"schema": SCHEMA, "be": {"r": 2.0}, "foo": 1})
    ['E_EXIT_SCHEMA:foo', 'E_EXIT_SCHEMA:be.r']
    """
    errs = []
    if not isinstance(policy, dict):
        return ["E_EXIT_SCHEMA:policy"]
    for k in sorted(policy):
        if k != "schema" and k not in COMPONENTS:
            errs.append("E_EXIT_SCHEMA:" + str(k))
    if policy.get("schema") != SCHEMA:
        errs.append("E_EXIT_SCHEMA:schema")
    if "scope" in policy and policy["scope"] not in SCOPES:
        errs.append("E_EXIT_SCHEMA:scope")
    if "be" in policy and _dict_keys(errs, "be", policy["be"], "be"):
        _rng(errs, "be.r", policy["be"].get("r"), "be.r")
    if "lock" in policy:
        lk = policy["lock"]
        lo, hi = RANGES["lock.n"]
        if not isinstance(lk, list) or not lo <= len(lk) <= hi:
            errs.append("E_EXIT_SCHEMA:lock")
        else:
            prev_a = prev_k = None
            for i, st in enumerate(lk):
                p = "lock[%d]" % i
                if not isinstance(st, dict):
                    errs.append("E_EXIT_SCHEMA:" + p)
                    continue
                for k in sorted(st):
                    if k not in ("at_r", "keep"):
                        errs.append("E_EXIT_SCHEMA:%s.%s" % (p, k))
                a = _rng(errs, p + ".at_r", st.get("at_r"), "lock.at_r")
                kp = _rng(errs, p + ".keep", st.get("keep"), "lock.keep")
                if a is not None and prev_a is not None and a <= prev_a + _EPS:
                    errs.append("E_EXIT_SCHEMA:%s.at_r" % p)          # ступени строго по возрастанию at_r
                if kp is not None and prev_k is not None and kp < prev_k - _EPS:
                    errs.append("E_EXIT_SCHEMA:%s.keep" % p)          # доля удержания не убывает
                prev_a = a if a is not None else prev_a
                prev_k = kp if kp is not None else prev_k
    if "partial" in policy:
        pt = policy["partial"]
        lo, hi = RANGES["partial.n"]
        if not isinstance(pt, list) or not lo <= len(pt) <= hi:
            errs.append("E_EXIT_SCHEMA:partial")
        else:
            total, prev_a = 0.0, None
            for i, st in enumerate(pt):
                p = "partial[%d]" % i
                if not isinstance(st, dict):
                    errs.append("E_EXIT_SCHEMA:" + p)
                    continue
                for k in sorted(st):
                    if k not in ("at_r", "frac", "exec"):
                        errs.append("E_EXIT_SCHEMA:%s.%s" % (p, k))
                a = _rng(errs, p + ".at_r", st.get("at_r"), "partial.at_r")
                fr = _rng(errs, p + ".frac", st.get("frac"), "partial.frac")
                _exec(errs, p + ".exec", st.get("exec"))
                if a is not None and prev_a is not None and a <= prev_a + _EPS:
                    errs.append("E_EXIT_SCHEMA:%s.at_r" % p)          # уровни фиксации различны и по возрастанию
                prev_a = a if a is not None else prev_a
                total += fr or 0.0
            if total > RANGES["partial.sum"][1] + 1e-9:
                errs.append("E_EXIT_SCHEMA:partial.sum")
    if "trail_atr" in policy and _dict_keys(errs, "trail_atr", policy["trail_atr"], "trail_atr"):
        d = policy["trail_atr"]
        _rng(errs, "trail_atr.activate_r", d.get("activate_r"), "trail_atr.activate_r")
        _rng(errs, "trail_atr.mult", d.get("mult"), "trail_atr.mult")
    if "stall" in policy and _dict_keys(errs, "stall", policy["stall"], "stall"):
        d = policy["stall"]
        for k in ("hold_frac", "min_r", "keep"):
            _rng(errs, "stall." + k, d.get(k), "stall." + k)
    if "decay" in policy and _dict_keys(errs, "decay", policy["decay"], "decay"):
        d = policy["decay"]
        for k in ("hold_frac", "max_mfe_r", "max_pnl_r"):
            _rng(errs, "decay." + k, d.get(k), "decay." + k)
        _exec(errs, "decay.exec", d.get("exec"))
    if "timeout" in policy and _dict_keys(errs, "timeout", policy["timeout"], "timeout"):
        _rng(errs, "timeout.hold_frac", policy["timeout"].get("hold_frac"), "timeout.hold_frac")
        _exec(errs, "timeout.exec", policy["timeout"].get("exec"))
    if "near_tp" in policy and _dict_keys(errs, "near_tp", policy["near_tp"], "near_tp"):
        d = policy["near_tp"]
        for k in ("q", "frac", "min_r"):
            _rng(errs, "near_tp." + k, d.get(k), "near_tp." + k)
        _exec(errs, "near_tp.exec", d.get("exec"))
        if "table" in d:
            tb = d["table"]
            if not isinstance(tb, dict):
                errs.append("E_EXIT_SCHEMA:near_tp.table")
            else:
                for st in sorted(tb, key=str):
                    row = tb[st]
                    if not isinstance(st, str) or not st or not isinstance(row, dict):
                        errs.append("E_EXIT_SCHEMA:near_tp.table.%s" % st)
                        continue
                    for hz in sorted(row, key=str):
                        p = "near_tp.table.%s.%s" % (st, hz)
                        if not isinstance(hz, str) or not hz:
                            errs.append("E_EXIT_SCHEMA:" + p)
                            continue
                        v = _rng(errs, p, row[hz], "near_tp.level")
                        if v is not None and v <= 0:
                            errs.append("E_EXIT_SCHEMA:" + p)
    if "min_profit_r" in policy:
        _rng(errs, "min_profit_r", policy["min_profit_r"], "min_profit_r")
    return errs


def weaker_than(policy, floor_policy):
    """-> [] или ["E_EXIT_WEAKER:<компонент>", ...] по трём замещающим компонентам (Requirement 2.4). Политику ждёт нормализованной:
    отсутствующий у политики компонент, который есть у пола, — слабее (без normalize он молча перекрылся бы полом).

      * `be`: be.r политики <= be.r пола;
      * `lock`: ступенчатая доля удержания политики >= доли пола при каждом MFE >= первой ступени пола (проверка по всем точкам
        излома обеих ступенчатых функций);
      * `min_profit_r`: не ниже пола.

    >>> F = floor({"enabled": True, "be_r": 0.7, "trail_activate_r": 1.0, "trail_lock_frac": 0.5, "min_profit_r": 0.2})
    >>> weaker_than(normalize({"be": {"r": 0.9}, "lock": [{"at_r": 1.2, "keep": 0.6}]}, F), F)
    ['E_EXIT_WEAKER:be', 'E_EXIT_WEAKER:lock']
    """
    p = policy if isinstance(policy, dict) else {}
    fl = floor_policy if isinstance(floor_policy, dict) else {}
    out = []
    if "be" in fl:
        fr, pr = _f((fl.get("be") or {}).get("r")), _f((p.get("be") or {}).get("r")) if isinstance(p.get("be"), dict) else None
        if fr is not None and (pr is None or pr > fr + _EPS):
            out.append("E_EXIT_WEAKER:be")
    fs = _steps(fl)
    if fs:
        ps = _steps(p)
        start = fs[0][0]
        pts = sorted({a for a, _ in fs + ps if a >= start - _EPS} | {start})
        if not ps or any(_keep(ps, m) < _keep(fs, m) - _EPS for m in pts):
            out.append("E_EXIT_WEAKER:lock")
    if "min_profit_r" in fl:
        fm, pm = _f(fl.get("min_profit_r")), _f(p.get("min_profit_r"))
        if fm is not None and (pm is None or pm < fm - _EPS):
            out.append("E_EXIT_WEAKER:min_profit_r")
    return out


def policy_hash(policy):
    """16 hex sha256 канонического JSON политики (ключи по алфавиту, без пробелов). Хешировать нормализованную политику — забота
    вызывающего (Requirement 2.8: хеш, снимок и оценки несут нормализованную политику).

    >>> policy_hash({"schema": SCHEMA, "be": {"r": 0.7}}) == policy_hash({"be": {"r": 0.7}, "schema": SCHEMA})
    True
    """
    return hashlib.sha256(_canon(policy).encode("ascii")).hexdigest()[:16]


# ---------------------------------------------------------------- композиция
def keep_at(policy, floor_policy, mfe_r):
    """Доля удержания MFE композиции «политика ∘ пол» при MFE `mfe_r` (наибольшая из ступеней обеих; 0 — ни одна не достигнута).

    >>> F = floor({"enabled": True, "be_r": 0.7, "trail_activate_r": 1.0, "trail_lock_frac": 0.5, "min_profit_r": 0.2})
    >>> [keep_at({"lock": [{"at_r": 1.5, "keep": 0.65}]}, F, m) for m in (0.9, 1.2, 1.5)]
    [0.0, 0.5, 0.65]
    """
    m = _f(mfe_r)
    if m is None:
        return 0.0
    return max(_keep(_steps(normalize(policy, floor_policy)), m), _keep(_steps(floor_policy), m))


def _eff_min_profit(p, fl):
    vals = [v for v in (_f(p.get("min_profit_r")), _f(fl.get("min_profit_r"))) if v is not None]
    return max(vals) if vals else None


def _fr(x):
    """Число R для текста досье: до двух знаков, не меньше одного (1.0, 0.65)."""
    t = ("%.2f" % float(x)).rstrip("0")
    return t + "0" if t.endswith(".") else t


def _minutes(ctx, frac):
    mh = _f((ctx or {}).get("max_hold_min"))
    return None if mh is None or mh <= 0 else round(float(frac) * mh, 6)


def _near_level(nt, ctx):
    row = ((nt.get("table") or {}).get((ctx or {}).get("sig_type")) or {})
    lvl = _f(row.get((ctx or {}).get("horizon"))) if isinstance(row, dict) else None
    if lvl is None or lvl <= 0:
        return None
    return max(lvl, float(nt["min_r"]))


def compile(policy, floor_policy, ctx=None):  # noqa: A001  (имя из design.md «Components and Interfaces»)
    """-> [правило manage] композиции «политика ∘ пол» в порядке вычисления (раньше `plan.manage`):

      auto_be      безубыток при min(be.r политики, be.r пола) (замещающий; пол строже версии действует сразу);
      auto_lock    ступень пола (mfe_lock), всегда, если она есть у пола;
      xp_lock      ступени политики (mfe_tiers), если хоть где-то строже пола;
      xp_partial_<i>, xp_near  фиксации долей по уровню R (`do.exec`, `do.at_r` — для maker-заявок исполнителя);
      xp_trail     трейл от лучшей цены на mult × ATR 5м на входе (atr_entry, `do.atr_pct` из ctx.atr5_pct);
      xp_stall     нет нового пика hold_frac срока при прибыли >= min_r -> стоп удерживает keep текущей прибыли (pnl_keep);
      xp_decay     после hold_frac срока при MFE < max_mfe_r и прибыли < max_pnl_r -> закрыть остаток;
      xp_timeout   после hold_frac срока -> закрыть остаток.

    `min_profit_r` композиции — наибольший из политики и пола; правилам пола он добавляется в `do`, только если отличается от пола
    (иначе состав в точности как `_auto_rules`). `ctx`: max_hold_min (на входе), atr5_pct, sig_type, horizon.

    >>> ap = {"enabled": True, "be_r": 0.7, "trail_activate_r": 1.0, "trail_lock_frac": 0.5, "min_profit_r": 0.2}
    >>> [r["id"] for r in compile(floor(ap), floor(ap))]
    ['auto_be', 'auto_lock']
    """
    fl = floor_policy if isinstance(floor_policy, dict) else {}
    p = normalize(policy, fl)
    ctx = ctx or {}
    out = []
    mpr = _eff_min_profit(p, fl)
    fl_mpr = _f(fl.get("min_profit_r"))
    extra = {} if (mpr is None or (fl_mpr is not None and abs(mpr - fl_mpr) <= _EPS)) else {"min_profit_r": mpr}
    stop_extra = {} if mpr is None else {"min_profit_r": mpr}

    bes = [v for v in (_f((p.get("be") or {}).get("r")) if isinstance(p.get("be"), dict) else None,
                       _f((fl.get("be") or {}).get("r")) if isinstance(fl.get("be"), dict) else None) if v is not None and v > 0]
    if bes:
        do = {"action": "move_stop", "to": "breakeven"}
        do.update(extra)
        out.append({"id": "auto_be", "when": {"kind": "pnl_r", "gte": min(bes)}, "do": do, "once": True})
    fs = _steps(fl)
    if fs:
        act, frac = fs[0]
        do = {"action": "trail", "mode": "mfe_lock", "activate_r": act, "lock_frac": frac}
        do.update(extra)
        out.append({"id": "auto_lock", "when": {"kind": "pnl_r", "gte": act}, "do": do})
    ps = _steps(p)
    if ps and any(_keep(ps, a) > _keep(fs, a) + _EPS for a, _ in ps):
        do = {"action": "trail", "mode": "mfe_tiers", "tiers": [[a, kp] for a, kp in ps]}
        do.update(stop_extra)
        out.append({"id": "xp_lock", "when": {"kind": "pnl_r", "gte": ps[0][0]}, "do": do})
    for i, st in enumerate(p.get("partial") or []):
        out.append({"id": "xp_partial_%d" % i, "when": {"kind": "pnl_r", "gte": float(st["at_r"])},
                    "do": {"action": "partial_close", "frac_of_initial": float(st["frac"]), "exec": st["exec"], "at_r": float(st["at_r"])},
                    "once": True})
    nt = p.get("near_tp")
    if isinstance(nt, dict):
        lvl = _near_level(nt, ctx)
        if lvl is not None:
            out.append({"id": "xp_near", "when": {"kind": "pnl_r", "gte": lvl},
                        "do": {"action": "partial_close", "frac_of_initial": float(nt["frac"]), "exec": nt["exec"], "at_r": lvl},
                        "once": True})
    ta = p.get("trail_atr")
    atr = _f(ctx.get("atr5_pct"))
    if isinstance(ta, dict) and atr is not None and atr > 0:
        do = {"action": "trail", "mode": "atr_entry", "mult": float(ta["mult"]), "atr_pct": atr}
        do.update(stop_extra)
        out.append({"id": "xp_trail", "when": {"kind": "pnl_r", "gte": float(ta["activate_r"])}, "do": do})
    sl = p.get("stall")
    if isinstance(sl, dict):
        mins = _minutes(ctx, sl["hold_frac"])
        if mins is not None:
            do = {"action": "trail", "mode": "pnl_keep", "keep": float(sl["keep"])}
            do.update(stop_extra)
            out.append({"id": "xp_stall", "when": {"all": [{"kind": "since_peak_min", "gte": mins}, {"kind": "pnl_r", "gte": float(sl["min_r"])}]},
                        "do": do})
    dc = p.get("decay")
    if isinstance(dc, dict):
        mins = _minutes(ctx, dc["hold_frac"])
        if mins is not None:
            out.append({"id": "xp_decay", "when": {"all": [{"kind": "elapsed", "min": mins}, {"kind": "mfe_r", "lt": float(dc["max_mfe_r"])},
                                                           {"kind": "pnl_r", "lt": float(dc["max_pnl_r"])}]},
                        "do": {"action": "close", "frac": 1.0, "reason": "rule", "exec": dc["exec"]}, "once": True})
    to = p.get("timeout")
    if isinstance(to, dict):
        mins = _minutes(ctx, to["hold_frac"])
        if mins is not None:
            out.append({"id": "xp_timeout", "when": {"kind": "elapsed", "min": mins},
                        "do": {"action": "close", "frac": 1.0, "reason": "rule", "exec": to["exec"]}, "once": True})
    return out


# ---------------------------------------------------------------- состояние позиции
def apply_state(policy, floor_policy, ctx, state):
    """Решения композиции для состояния позиции в R (без цены и рынка; для свойств, досье и сверки с симулятором).

    `state`: mfe_r, pnl_r, elapsed_min, since_peak_min, done (идентификаторы once-правил, уже сработавших). ->
    {"stop_r": уровень стопа в R от входа по правилам стопа или None, "close_frac_initial": сумма долей фиксаций, которые сработали бы
    сейчас (не больше 1), "close_all": bool, "fired": [id]}. Безубыток даёт уровень пола прибыли `min_profit_r` (комиссии круга и
    проскальзывание стопа считает исполнитель). Детерминирована.

    >>> ap = {"enabled": True, "be_r": 0.7, "trail_activate_r": 1.0, "trail_lock_frac": 0.5, "min_profit_r": 0.2}
    >>> apply_state({}, floor(ap), {}, {"mfe_r": 1.6, "pnl_r": 1.2})["stop_r"]
    0.8
    """
    st = state or {}
    mfe, pnl = _f(st.get("mfe_r")) or 0.0, _f(st.get("pnl_r"))
    pnl = mfe if pnl is None else pnl
    el, sp = _f(st.get("elapsed_min")), _f(st.get("since_peak_min"))
    done = set(st.get("done") or ())
    stop, frac, close_all, fired = None, 0.0, False, []

    def cand(rid, v):
        nonlocal stop
        fired.append(rid)
        stop = v if stop is None else max(stop, v)

    for r in compile(policy, floor_policy, ctx):
        rid, do, w = r["id"], r["do"], r["when"]
        if r.get("once") and rid in done:
            continue
        mpr = _f(do.get("min_profit_r"))
        if mpr is None:
            mpr = _eff_min_profit(normalize(policy, floor_policy), floor_policy if isinstance(floor_policy, dict) else {}) or 0.0
        if rid == "auto_be":
            if mfe >= w["gte"] - _EPS:
                cand(rid, mpr)
        elif do.get("mode") == "mfe_lock":
            if mfe >= do["activate_r"] - _EPS and do["lock_frac"] * mfe >= mpr - _EPS:
                cand(rid, do["lock_frac"] * mfe)
        elif do.get("mode") == "mfe_tiers":
            k = _keep([tuple(t) for t in do["tiers"]], mfe)
            if k > 0 and k * mfe >= mpr - _EPS:
                cand(rid, k * mfe)
        elif do.get("mode") == "pnl_keep":
            mins = w["all"][0]["gte"]
            if sp is not None and sp >= mins - _EPS and pnl >= w["all"][1]["gte"] - _EPS and do["keep"] * pnl >= mpr - _EPS:
                cand(rid, do["keep"] * pnl)
        elif do.get("action") == "partial_close":
            if mfe >= w["gte"] - _EPS:
                fired.append(rid)
                frac += do["frac_of_initial"]
        elif rid == "xp_decay":
            mins = w["all"][0]["min"]
            if el is not None and el >= mins - _EPS and mfe < w["all"][1]["lt"] - _EPS and pnl < w["all"][2]["lt"] - _EPS:
                fired.append(rid)
                close_all = True
        elif rid == "xp_timeout":
            if el is not None and el >= w["min"] - _EPS:
                fired.append(rid)
                close_all = True
    return {"stop_r": None if stop is None else round(stop, 12), "close_frac_initial": min(1.0, round(frac, 12)), "close_all": close_all,
            "fired": fired}


def summary(policy, floor_policy, pos_state, ctx=None):
    """Ближайшее действие кода для досье `position_manager` (Requirement 11.4) -> {"keep": доля удержания MFE сейчас,
    "next": {"kind", "at_r", ...} | None, "timeout_min": минуты от входа | None, "text": строка до ~60 токенов}.

    >>> ap = {"enabled": True, "be_r": 0.7, "trail_activate_r": 1.0, "trail_lock_frac": 0.5, "min_profit_r": 0.2}
    >>> summary({"partial": [{"at_r": 1.0, "frac": 0.3, "exec": "maker"}]}, floor(ap), {"mfe_r": 0.8})["text"]
    'фиксация 30% на +1.0R; стоп удержит 0.5 MFE с +1.0R'
    """
    fl = floor_policy if isinstance(floor_policy, dict) else {}
    p = normalize(policy, fl)
    st = pos_state or {}
    mfe = _f(st.get("mfe_r")) or 0.0
    done = set(st.get("done") or ())
    ctx = ctx or {}
    cands = []
    bes = [v for v in (_f((p.get("be") or {}).get("r")) if isinstance(p.get("be"), dict) else None,
                       _f((fl.get("be") or {}).get("r")) if isinstance(fl.get("be"), dict) else None) if v is not None and v > 0]
    if bes and min(bes) > mfe + _EPS and "auto_be" not in done:
        cands.append((min(bes), 1, {"kind": "be", "at_r": min(bes)}, "безубыток на +%sR" % _fr(min(bes))))
    steps = sorted(set(_steps(p)) | set(_steps(fl)))
    for a, _ in steps:
        if a > mfe + _EPS:
            k = keep_at(p, fl, a)
            cands.append((a, 2, {"kind": "lock", "at_r": a, "keep": k}, "стоп удержит %s MFE с +%sR" % (_fr(k), _fr(a))))
            break
    for i, x in enumerate(p.get("partial") or []):
        if float(x["at_r"]) > mfe + _EPS and ("xp_partial_%d" % i) not in done:
            cands.append((float(x["at_r"]), 0, {"kind": "partial", "at_r": float(x["at_r"]), "frac": float(x["frac"])},
                          "фиксация %d%% на +%sR" % (round(float(x["frac"]) * 100), _fr(x["at_r"]))))
    nt = p.get("near_tp")
    if isinstance(nt, dict) and "xp_near" not in done:
        lvl = _near_level(nt, ctx)
        if lvl is not None and lvl > mfe + _EPS:
            cands.append((lvl, 0, {"kind": "near_tp", "at_r": lvl, "frac": float(nt["frac"])},
                          "фиксация %d%% на +%sR" % (round(float(nt["frac"]) * 100), _fr(lvl))))
    ta = p.get("trail_atr")
    if isinstance(ta, dict) and float(ta["activate_r"]) > mfe + _EPS:
        cands.append((float(ta["activate_r"]), 3, {"kind": "trail_atr", "at_r": float(ta["activate_r"]), "mult": float(ta["mult"])},
                      "трейл %s ATR с +%sR" % (_fr(ta["mult"]), _fr(ta["activate_r"]))))
    cands.sort(key=lambda c: (c[0], c[1]))
    to = p.get("timeout")
    tmin = _minutes(ctx, to["hold_frac"]) if isinstance(to, dict) else None
    parts = [c[3] for c in cands[:2]]
    if tmin is not None:
        parts.append("закрытие через %g мин от входа" % round(tmin))
    return {"keep": keep_at(p, fl, mfe), "next": cands[0][2] if cands else None, "timeout_min": tmin,
            "text": "; ".join(parts) if parts else "код: только стоп"}
