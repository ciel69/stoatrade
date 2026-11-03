"""Теневые политики (R1.5, Requirement 8): реестр config/shadow_policies.json, контекст теней, выбор плана для симулятора.

load(root) -> [политика]: строгая проверка (лишние и недостающие поля, неизвестные значения, дубли id+ver -> ValueError).
shadow_ctx(root, cfg, feat, ev) -> ctx вида validate.entry_context (для tb.logic.geometry) + ctx_ver.
pick(policy, sig, feat, ctx, geom_cfg) -> (план | None, код | None): план в формате tb.logic.sim.run.
Пакет не импортирует venues/executor/gateway/llm/brain/control/intel.items; планов, ордеров и событий шины не создаёт.

Коды отказа: NO_DIR (у сигнала нет направления, политика with_dir/against_dir), NO_LEVEL (у метки нет уровней), NO_PX (нет цены),
NO_ATR_PLAN (atr_plan вернул None), GEO_<ПРИЧИНА> (геометрия не дала ok-кандидата стороны: GEO_STOP_WIDE, GEO_RR_LOW, GEO_RANGE_GT_STOP, GEO_NO_CAND).
"""
import hashlib
import json
import math

from tb import paths
from tb.core import expstats
from tb.logic import costs as costs_mod
from tb.logic import geometry

SCHEMA = "tb.shadow_policies/1"
FIELDS = ("id", "ver", "name", "kinds", "side", "entry", "profile", "no_tp", "max_horizon", "entry_ttl_min", "enabled")
SIDES = ("with_dir", "against_dir", "random")
ENTRIES = ("market", "candle_confirm", "candle_confirm_limit")
PROFILES = ("base", "atr")
VENUE = "bybit"
HORIZONS = geometry.SESSION_ORDER
DEFAULT_SPREAD_BPS = 2.0
DEFAULT_LATENCY_S = 60
DEFAULT_STOP_SLIP_BPS = 5.0


def _isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _num(v):
    return float(v) if _isnum(v) else None


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError("не читается %s: %s" % (path, e))


def _check(p, i):
    if not isinstance(p, dict):
        raise ValueError("policies[%d]: не объект" % i)
    extra = sorted(set(p) - set(FIELDS))
    if extra:
        raise ValueError("policies[%d]: лишние поля %s" % (i, extra))
    miss = [k for k in FIELDS if k not in p]
    if miss:
        raise ValueError("policies[%d]: нет полей %s" % (i, miss))
    if not isinstance(p["id"], str) or not p["id"].strip():
        raise ValueError("policies[%d]: id" % i)
    if not isinstance(p["ver"], int) or isinstance(p["ver"], bool) or p["ver"] < 1:
        raise ValueError("%s: ver должен быть целым >= 1" % p["id"])
    if not isinstance(p["name"], str) or not p["name"].strip():
        raise ValueError("%s: name" % p["id"])
    k = p["kinds"]
    if not isinstance(k, list) or not k or not all(isinstance(x, str) and x for x in k) or len(set(k)) != len(k):
        raise ValueError("%s: kinds должен быть непустым списком уникальных строк" % p["id"])
    if p["side"] not in SIDES:
        raise ValueError("%s: side %r" % (p["id"], p["side"]))
    if p["entry"] not in ENTRIES:
        raise ValueError("%s: entry %r" % (p["id"], p["entry"]))
    if p["profile"] not in PROFILES:
        raise ValueError("%s: profile %r" % (p["id"], p["profile"]))
    if p["profile"] == "atr" and p["entry"] != "market":
        raise ValueError("%s: профиль atr входит только по рынку" % p["id"])
    for b in ("no_tp", "enabled"):
        if not isinstance(p[b], bool):
            raise ValueError("%s: %s должен быть bool" % (p["id"], b))
    if p["max_horizon"] not in HORIZONS:
        raise ValueError("%s: max_horizon %r" % (p["id"], p["max_horizon"]))
    if not _isnum(p["entry_ttl_min"]) or p["entry_ttl_min"] <= 0:
        raise ValueError("%s: entry_ttl_min должен быть числом > 0" % p["id"])


def load(root=None):
    """-> все политики реестра (включая выключенные), в порядке файла. Любое нарушение -> ValueError."""
    d = _read_json(paths.config("shadow_policies.json", root=root))
    if not isinstance(d, dict) or d.get("schema") != SCHEMA:
        raise ValueError("shadow_policies: schema должна быть %s" % SCHEMA)
    extra = sorted(set(d) - {"schema", "policies"})
    if extra:
        raise ValueError("shadow_policies: лишние поля %s" % extra)
    pols = d.get("policies")
    if not isinstance(pols, list) or not pols:
        raise ValueError("shadow_policies: policies должен быть непустым списком")
    seen = set()
    for i, p in enumerate(pols):
        _check(p, i)
        key = (p["id"], p["ver"])
        if key in seen:
            raise ValueError("дубль id+ver: %s v%s" % key)
        seen.add(key)
    return [dict(p, kinds=list(p["kinds"])) for p in pols]


def active(pols):
    """Включённые политики."""
    return [p for p in pols if p.get("enabled")]


def applicable(policy, sig):
    """Политика включена, тип сигнала в kinds (или '*') и сигнал торгуем (tradable != 0)."""
    if not policy.get("enabled"):
        return False
    if sig.get("tradable") in (0, False):
        return False
    kinds = policy.get("kinds") or []
    return "*" in kinds or sig.get("type") in kinds


# ---------- контекст теней

def _session_horizons(prof):
    types = prof.get("session_types") if isinstance(prof.get("session_types"), dict) else {}
    out = {}
    for h in HORIZONS:
        c = types.get(h)
        if isinstance(c, dict):
            row = {}
            for k in ("max_stop_pct", "min_rr"):
                v = _num(c.get(k))
                row[k] = None if v is None else round(v, 2)
            out[h] = row
    return out


def _inputs(root, cfg):
    """Входы ctx_ver и ctx: профиль издержек, горизонты, автозащита, задержка, проскальзывание стопа."""
    cfg = cfg if isinstance(cfg, dict) else {}
    prof = _read_json(paths.config("risk_profile.json", root=root))
    try:
        lim = _read_json(paths.config("exec_limits.json", root=root))
    except ValueError:
        lim = {}
    ap = lim.get("auto_protect") if isinstance(lim.get("auto_protect"), dict) else {}
    costs = prof.get("costs") if isinstance(prof.get("costs"), dict) else {}
    ss = _num(lim.get("stop_slip_bps"))
    return {"costs_bybit": costs.get(VENUE) if isinstance(costs.get(VENUE), dict) else None,
            "shadow_ctx": cfg.get("shadow_ctx") if isinstance(cfg.get("shadow_ctx"), dict) else {},
            "horizons": _session_horizons(prof),
            "auto_protect": ap,
            "latency_s": _num(cfg.get("latency_s")) if _num(cfg.get("latency_s")) is not None else DEFAULT_LATENCY_S,
            "stop_slip_bps": ss if ss is not None else DEFAULT_STOP_SLIP_BPS}, prof


def ctx_ver(inputs):
    """Первые 8 символов sha256 канонического JSON входов: costs.bybit, shadow_ctx, горизонты сессии, auto_protect, latency_s, stop_slip_bps."""
    s = json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _first(*vals):
    for v in vals:
        n = _num(v)
        if n is not None:
            return n
    return None


def shadow_ctx(root, cfg, feat, ev):
    """ctx вида validate.entry_context для tb.logic.geometry. Из config/labels.json:shadow_ctx (risk_level, session_type, max_horizon, off_plan_ok,
    day_size_mult, stop_streak, no_confirm_allowed), горизонтов risk_profile.session_types (без поправок уровня риска), издержек круга (tb.logic.costs,
    тот же расчёт, что в досье мозга) и признаков/события: px, a5_pct, spread_bps, range_5m_pct, surge_x, ret_5m. Ключ ctx_ver - версия входов."""
    feat = feat if isinstance(feat, dict) else {}
    ev = ev if isinstance(ev, dict) else {}
    inp, prof = _inputs(root, cfg)
    sc = inp["shadow_ctx"]
    ctx = {"sym": None, "risk_level": sc.get("risk_level"), "session_type": sc.get("session_type"), "max_horizon": sc.get("max_horizon"),
           "off_plan_ok": sc.get("off_plan_ok"), "day_size_mult": sc.get("day_size_mult"), "stop_streak": sc.get("stop_streak"),
           "no_confirm_allowed": sc.get("no_confirm_allowed"), "no_confirm_max": None,
           "horizons": inp["horizons"] or None, "min_rr": None, "max_stop_pct": None,
           "cost_rt_pct": costs_mod.round_trip_pct(prof, VENUE), "cost_rt_maker_pct": costs_mod.round_trip_pct(prof, VENUE, maker=True)}
    dsp = _num((cfg or {}).get("default_spread_bps")) if isinstance(cfg, dict) else None
    ctx["px"] = _first(feat.get("px"), ev.get("price"), ev.get("px"))
    ctx["a5_pct"] = _first(feat.get("a5_pct"), feat.get("atr5_pct"))
    ctx["spread_bps"] = _first(ev.get("spread_bps"), feat.get("spread_bps"), dsp, DEFAULT_SPREAD_BPS)
    ctx["range_5m_pct"] = _first(ev.get("range_5m_pct"), feat.get("range_5m_pct"))
    ctx["surge_x"] = _first(ev.get("turn_surge_5m"), feat.get("surge_x"))
    ctx["ret_5m"] = _first(ev.get("ret_5m_pct"), feat.get("ret_5m"))
    ctx["ctx_ver"] = ctx_ver(inp)
    return ctx


def sim_inputs(root, cfg, ev=None):
    """-> (costs, manage) для tb.logic.sim.run: издержки bybit, спред события (иначе default_spread_bps), latency_s и блок auto_protect."""
    inp, _prof = _inputs(root, cfg)
    cb = inp["costs_bybit"] or {}
    dsp = _num((cfg or {}).get("default_spread_bps")) if isinstance(cfg, dict) else None
    sp = _first((ev or {}).get("spread_bps"), dsp, DEFAULT_SPREAD_BPS)
    costs = {"fee": cb.get("fee"), "maker_fee": cb.get("maker_fee"), "slip": cb.get("slip"), "stop_slip_bps": inp["stop_slip_bps"], "spread_bps": sp}
    return costs, {"latency_s": inp["latency_s"], "auto_protect": inp["auto_protect"]}


# ---------- выбор плана

def tick_for(px, feat=None):
    """Шаг цены: feat['tick'], иначе степень десяти около px * 1e-5 (приближение; точный tickSize в метках не хранится)."""
    t = _num((feat or {}).get("tick"))
    if t and t > 0:
        return t
    if not px or px <= 0:
        return 0.01
    return float("1e%d" % math.floor(math.log10(px * 1e-5)))


def _side(policy, sig):
    """-> (сторона | None, код | None)."""
    mode = policy["side"]
    if mode == "random":
        return expstats.assign_variant(sig.get("key"), ["long", "short"], exp=policy["id"]), None
    d = sig.get("side")
    if d not in ("long", "short"):
        return None, "NO_DIR"
    if mode == "with_dir":
        return d, None
    return ("short" if d == "long" else "long"), None


def _plan(side, kind, level, ttl, stop, tps, policy, horizon, tick):
    tps = [] if policy["no_tp"] else list(tps)
    return {"side": side, "entry": {"kind": kind, "level": level, "ttl_min": policy["entry_ttl_min"]}, "stop": stop, "tps": tps,
            "fracs": [round(1.0 / len(tps), 6)] * len(tps) if tps else [], "max_hold_min": geometry.MAX_HOLD_MIN[horizon],
            "tick": tick, "horizon": horizon}


def _fail_code(cands, side):
    for c in cands or []:
        if isinstance(c, dict) and c.get("side") == side and c.get("setup") != geometry.CHASE_SETUP and c.get("fail"):
            return "GEO_" + str(c["fail"]).split(":")[0].upper()
    return "GEO_NO_CAND"


def pick(policy, sig, feat, ctx, geom_cfg):
    """-> (план, None) или (None, код). base: ближайший к цене ok-кандидат стороны из geometry.candidates на уровнях метки (feat['levels']), горизонт
    не выше max_horizon политики, вход по policy.entry на уровне кандидата; atr: geometry.atr_plan, вход по рынку, горизонт max_horizon.
    no_tp убирает тейки. tradable=false проверяет вызывающий (applicable)."""
    feat = feat if isinstance(feat, dict) else {}
    side, code = _side(policy, sig)
    if code:
        return None, code
    px = _num((ctx or {}).get("px"))
    mh = policy["max_horizon"]
    if policy["profile"] == "atr":
        _b, _c, atr_prof, _cl = geometry._profiles(geom_cfg)
        ap = geometry.atr_plan(side, ctx or {}, atr_prof)
        if ap is None:
            return None, "NO_ATR_PLAN"
        return _plan(side, "market", ap["level"], policy["entry_ttl_min"], ap["stop"], ap["tp"], policy, mh, tick_for(px, feat)), None
    if not px or px <= 0:
        return None, "NO_PX"
    levels = geometry.pool(feat.get("levels"), geometry.DEFAULT_CLUSTER_PCT)
    if not levels:
        return None, "NO_LEVEL"
    gctx = dict(ctx, max_horizon=mh, off_plan_ok=False)            # горизонт кандидата не выше max_horizon политики
    cands = geometry.candidates(feat.get("levels"), gctx, geom_cfg)
    c = geometry.nearest_ok(cands, side, px, include_chase=False)
    if c is None:
        return None, _fail_code(cands, side)
    h = c.get("horizon")
    if h not in HORIZONS or HORIZONS.index(h) > HORIZONS.index(mh):
        return None, "GEO_HORIZON"
    return _plan(side, policy["entry"], c["level"], policy["entry_ttl_min"], c["stop"], c["tp"], policy, h, tick_for(px, feat)), None
