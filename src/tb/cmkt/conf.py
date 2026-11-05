"""config/cmkt.json: строгая загрузка конфига сводного рынка (R1.7, задача 4; Requirements 1.6, 2.4, 3.4, 4.4, 12.4, 16.1, 21.3).

Строгость: файл проверяется целиком. Нужны все ключи схемы; неизвестный ключ, повтор ключа, неверный тип или значение вне
диапазона и нарушение согласованности дают ошибку `E_CONF:<поле>` (путь через точку, например `E_CONF:sync.silent_s`,
`E_CONF:focus.max_deep`). Битый файл сервис не запускает (код выхода 2, ошибки в heartbeat). Нет файла — умолчания `DEFAULTS`,
равные поставляемому config/cmkt.json (тест сверяет).

Пределы: `focus.max_deep ≤ 40`, `focus.max_light ≤ 200` (бюджет Requirement 12); `sync.lag_max_ms_venue ≤ 2000`;
`sync.silent_s ≥ sync.ping_s + 5`; `brief.max_tokens` 40–600, `brief.syms` 1–3; `tool.max_bytes` 512–8192;
`limits.oi_reserve_per_min[k] ≤ limits.rest_weight_per_min[k]`; у каждого включённого рынка есть REST-вес (по рынку `<venue>_<kind>`
или по бирже); хосты — белый список по биржам, хостов платных агрегаторов в нём быть не может.
Модуль читает только свой файл и ничего не пишет.
"""
import copy
import hashlib
import json
import math
import re

from tb import paths

SCHEMA = "tb.cmkt_cfg/1"
FILE = "cmkt.json"
KINDS = ("perp", "spot")
BRIEF_MODES = ("compact", "full")
BAND_CHOICES = (0.25, 0.5, 1, 2)
PAID_HOSTS = ("coinglass", "kaiko", "tardis", "coinapi", "cryptocompare", "amberdata", "glassnode")
HOST_DENIED = "HOST_DENIED"

_RE_VENUE = re.compile(r"^[a-z][a-z0-9]{0,10}$")           # cmkt@<venue><p|s> проходит ^cmkt@[a-z][a-z0-9]{1,11}$
_RE_SYM = re.compile(r"^[A-Z0-9]{1,20}$")
_RE_HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_RE_STORE_DIR = re.compile(r"^state/[a-z0-9_]+(/[a-z0-9_]+)*$")

DEFAULTS = {
    "schema": SCHEMA, "enabled": True, "compat_xflow": False,
    "focus": {"core": ["BTC", "ETH", "SOL"], "top_n": 30, "deep_top_n": 10, "max_deep": 20, "max_light": 120, "keep_signal_min": 180,
              "struct_req_ttl_min": 30, "hold_s": 600, "warm_s": 300},
    "venues": {
        "binance": {"enabled": True, "markets": ["perp", "spot"],
                    "hosts": ["fstream.binance.com", "ws-fapi.binance.com", "fapi.binance.com", "www.binance.com",
                              "data-stream.binance.vision", "data-api.binance.vision", "stream.binance.com", "api.binance.com"]},
        "bybit": {"enabled": True, "markets": ["perp", "spot"], "hosts": ["stream.bybit.com", "api.bybit.com", "api.bybitglobal.com"]},
        "okx": {"enabled": True, "markets": ["perp", "spot"], "hosts": ["ws.okx.com", "www.okx.com"]},
        "bitget": {"enabled": True, "markets": ["perp", "spot"], "hosts": ["ws.bitget.com", "api.bitget.com"]},
        "gate": {"enabled": True, "markets": ["perp", "spot"], "hosts": ["fx-ws.gateio.ws", "api.gateio.ws"]},
        "coinbase": {"enabled": True, "markets": ["spot"], "hosts": ["ws-feed.exchange.coinbase.com", "api.exchange.coinbase.com"]},
    },
    "sync": {"tick_s": 1, "lag_max_ms": 1000, "lag_max_ms_venue": {}, "silent_s": 15, "file_stale_s": 1, "accept_s": 3, "ping_s": 10,
             "clock_warn_ms": 500, "time_sync_s": 600, "lead_rtt_max_ms": 100, "lead_offset_max_age_s": 900, "snap_every_s": 2},
    "book": {"bucket_bps": 25, "max_bps": 200, "bands_pct": [0.25, 0.5, 1, 2], "imb_band_first_wave": 0.25, "wall_min_market_usd": 100000,
             "wall_mult": 6, "wall_min_usd": 400000, "wall_group_bps": 5, "wall_persist_s": 30, "touch_bps": 5, "pull_drop_share": 0.7,
             "pull_window_s": 5, "resync_min_s": 10},
    "cov": {"min_cov": 0.6, "min_n_ex": 2, "liq_min_cov": 0.5, "mismatch_pct": 2.0},
    "limits": {"subs_per_conn": {"default": 200, "bitget": 50, "okx": 150, "gate": 100, "coinbase": 100}, "sub_msgs_per_s": 5, "conn_per_s": 1,
               "backoff_s": 1, "backoff_max_s": 60,
               "rest_weight_per_min": {"binance_perp": 400, "binance_spot": 1100, "bybit": 100, "okx": 60, "bitget": 60, "gate": 30,
                                       "coinbase": 30},
               "oi_reserve_per_min": {"binance_perp": 240},
               "oi_poll_s": {"deep": 10, "light": 60}},
    "budget": {"cpu_avg_cores": 1.5, "cpu_p99_cores": 4.0, "rss_mb": 1500, "proc_p99_ms": 300, "net_mbit": 80, "disk_write_gb_day": 30,
               "over_min": 10, "shrink_step": 20, "light_min": 40},
    "store": {"dir": "state/cmkt", "keep_days": 30, "deep_every_s": 1, "light_every_s": 5, "disk_min_gb": 40},
    "brief": {"mode": "compact", "max_tokens": 90, "syms": 1, "experiment": False},
    "tool": {"max_bytes": 2048, "per_hour": 30},
}


class ConfError(ValueError):
    """Конфиг битый; `errors` — список кодов `E_CONF:<поле>`."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("config/cmkt.json: " + ", ".join(self.errors))


# --- проверки значений (лист схемы: значение -> bool)

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


def _enum(*vals):
    def ok(v):
        return not isinstance(v, bool) and v in vals and type(v) in (type(x) for x in vals)
    return ok


def _schema(v):
    return v == SCHEMA


def _core(v):
    return (isinstance(v, list) and 1 <= len(v) <= 10 and all(isinstance(s, str) and _RE_SYM.match(s) for s in v)
            and len(set(v)) == len(v))


def _bands(v):
    return (isinstance(v, list) and 1 <= len(v) <= 4 and all(_num(0, 10)(x) and x in BAND_CHOICES for x in v)
            and all(a < b for a, b in zip(v, v[1:])))


def _store_dir(v):
    return isinstance(v, str) and bool(_RE_STORE_DIR.match(v)) and ".." not in v


def _share(v):
    return _num(0.0, 1.0, lo_open=True)(v)


class Dyn(object):
    """Словарь с ключами-данными (биржи, рынки): fn(значение, путь) -> [ошибки]."""

    def __init__(self, fn):
        self.fn = fn


def _hosts_ok(v):
    if not isinstance(v, list) or not 1 <= len(v) <= 16 or len(set(v)) != len(v):
        return False
    return all(isinstance(h, str) and _RE_HOST.match(h) and not any(p in h for p in PAID_HOSTS) for h in v)


def _markets_ok(v):
    return isinstance(v, list) and 1 <= len(v) <= 2 and len(set(v)) == len(v) and all(isinstance(k, str) and k in KINDS for k in v)


VENUE_SPEC = {"enabled": _bool, "markets": _markets_ok, "hosts": _hosts_ok}


def _venues(v, path):
    if not isinstance(v, dict) or not 1 <= len(v) <= 16:
        return ["E_CONF:" + path]
    errs = []
    for k, ent in v.items():
        if not isinstance(k, str) or not _RE_VENUE.match(k):
            errs.append("E_CONF:%s.%s" % (path, k))
        elif not isinstance(ent, dict):
            errs.append("E_CONF:%s.%s" % (path, k))
        else:
            _walk(VENUE_SPEC, ent, "%s.%s." % (path, k), errs)
    return errs


def _int_map(lo, hi, extra_keys=()):
    """Словарь {биржа | рынок | extra: целое lo..hi}; принадлежность ключей биржам проверяет _cross."""
    def fn(v, path):
        if not isinstance(v, dict) or len(v) > 64:
            return ["E_CONF:" + path]
        errs = []
        for k, x in v.items():
            if not isinstance(k, str) or not (k in extra_keys or _market_key(k)):
                errs.append("E_CONF:%s.%s" % (path, k))
            elif not _int(lo, hi)(x):
                errs.append("E_CONF:%s.%s" % (path, k))
        return errs
    return fn


def _market_key(k):
    """'<venue>' или '<venue>_<kind>'."""
    if _RE_VENUE.match(k):
        return True
    v, _, kind = k.rpartition("_")
    return bool(v) and bool(_RE_VENUE.match(v)) and kind in KINDS


_SEC = _int(1, 86400)
_USD = _num(0, 1e12)

# Схема: вложенный dict повторяет структуру файла, лист — проверка значения или Dyn. Все ключи обязательны.
SPEC = {
    "schema": _schema, "enabled": _bool, "compat_xflow": _bool,
    "focus": {"core": _core, "top_n": _int(0, 200), "deep_top_n": _int(0, 40), "max_deep": _int(1, 40), "max_light": _int(0, 200),
              "keep_signal_min": _int(0, 1440), "struct_req_ttl_min": _int(1, 1440), "hold_s": _int(0, 86400), "warm_s": _int(0, 86400)},
    "venues": Dyn(_venues),
    "sync": {"tick_s": _int(1, 1), "lag_max_ms": _int(50, 2000), "lag_max_ms_venue": Dyn(_int_map(50, 2000)),
             "silent_s": _int(2, 600), "file_stale_s": _int(1, 10), "accept_s": _int(1, 30), "ping_s": _int(1, 300),
             "clock_warn_ms": _int(1, 60000), "time_sync_s": _int(60, 86400), "lead_rtt_max_ms": _int(1, 2000),
             "lead_offset_max_age_s": _int(60, 86400), "snap_every_s": _int(1, 5)},
    "book": {"bucket_bps": _int(1, 200), "max_bps": _int(10, 1000), "bands_pct": _bands, "imb_band_first_wave": _enum(0.25, 0.5),
             "wall_min_market_usd": _USD, "wall_mult": _num(1, 100), "wall_min_usd": _USD, "wall_group_bps": _num(0, 100),
             "wall_persist_s": _int(1, 3600), "touch_bps": _num(0, 100), "pull_drop_share": _share, "pull_window_s": _int(1, 600),
             "resync_min_s": _int(1, 3600)},
    "cov": {"min_cov": _share, "min_n_ex": _int(1, 11), "liq_min_cov": _share, "mismatch_pct": _num(0, 50, lo_open=True)},
    "limits": {"subs_per_conn": Dyn(_int_map(1, 1024, ("default",))), "sub_msgs_per_s": _int(1, 10), "conn_per_s": _int(1, 10),
               "backoff_s": _num(0.1, 60), "backoff_max_s": _num(1, 3600),
               "rest_weight_per_min": Dyn(_int_map(1, 100000)), "oi_reserve_per_min": Dyn(_int_map(0, 100000)),
               "oi_poll_s": {"deep": _int(1, 600), "light": _int(1, 3600)}},
    "budget": {"cpu_avg_cores": _num(0, 64, lo_open=True), "cpu_p99_cores": _num(0, 64, lo_open=True), "rss_mb": _int(1, 65536),
               "proc_p99_ms": _int(1, 60000), "net_mbit": _int(1, 100000), "disk_write_gb_day": _int(1, 100000),
               "over_min": _int(1, 1440), "shrink_step": _int(1, 200), "light_min": _int(0, 200)},
    "store": {"dir": _store_dir, "keep_days": _int(2, 365), "deep_every_s": _int(1, 60), "light_every_s": _int(1, 300),
              "disk_min_gb": _int(1, 100000)},
    "brief": {"mode": _enum(*BRIEF_MODES), "max_tokens": _int(40, 600), "syms": _int(1, 3), "experiment": _bool},
    "tool": {"max_bytes": _int(512, 8192), "per_hour": _int(1, 10000)},
}


# --- проверка структуры

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
        if isinstance(chk, Dyn):
            errs.extend(chk.fn(v, path))
        elif isinstance(chk, dict):
            if isinstance(v, dict):
                _walk(chk, v, path + ".", errs)
            else:
                errs.append("E_CONF:" + path)
        elif not chk(v):
            errs.append("E_CONF:" + path)


def markets(cfg, enabled_only=True):
    """Рынки конфига: [(venue, kind)] в порядке файла; enabled_only — только включённых бирж."""
    out = []
    for v, ent in (cfg.get("venues") or {}).items():
        if isinstance(ent, dict) and (ent.get("enabled") is True or not enabled_only):
            out.extend((v, k) for k in ent.get("markets") or () if k in KINDS)
    return out


def rest_weight(cfg, venue, kind):
    """Общий REST-вес cmkt в минуту рынка: ключ '<venue>_<kind>', иначе '<venue>'; None — не задан."""
    w = cfg["limits"]["rest_weight_per_min"]
    return w.get("%s_%s" % (venue, kind), w.get(venue))


def oi_reserve(cfg, venue, kind):
    r = cfg["limits"]["oi_reserve_per_min"]
    return r.get("%s_%s" % (venue, kind), r.get(venue, 0))


def _cross(cfg):
    errs = []
    f, s, b, lim, bud, st = cfg["focus"], cfg["sync"], cfg["book"], cfg["limits"], cfg["budget"], cfg["store"]
    venues = cfg["venues"]
    if s["silent_s"] < s["ping_s"] + 5:
        errs.append("E_CONF:sync.silent_s")                         # живость: хотя бы один пинг с запасом 5 с
    if s["lead_offset_max_age_s"] < s["time_sync_s"]:
        errs.append("E_CONF:sync.lead_offset_max_age_s")            # оценка offset устаревала бы до следующего замера
    for k in s["lag_max_ms_venue"]:
        if k not in venues:
            errs.append("E_CONF:sync.lag_max_ms_venue." + k)
    if f["deep_top_n"] > f["max_deep"] or f["deep_top_n"] > f["top_n"]:
        errs.append("E_CONF:focus.deep_top_n")
    if len(f["core"]) > f["max_deep"]:
        errs.append("E_CONF:focus.core")
    if b["max_bps"] % b["bucket_bps"]:
        errs.append("E_CONF:book.max_bps")
    if b["imb_band_first_wave"] not in b["bands_pct"]:
        errs.append("E_CONF:book.imb_band_first_wave")
    if b["wall_min_usd"] < b["wall_min_market_usd"]:
        errs.append("E_CONF:book.wall_min_usd")
    if lim["backoff_max_s"] < lim["backoff_s"]:
        errs.append("E_CONF:limits.backoff_max_s")
    if lim["oi_poll_s"]["light"] < lim["oi_poll_s"]["deep"]:
        errs.append("E_CONF:limits.oi_poll_s.light")
    for k in lim["subs_per_conn"]:
        if k != "default" and k not in venues:
            errs.append("E_CONF:limits.subs_per_conn." + k)
    if "default" not in lim["subs_per_conn"]:
        errs.append("E_CONF:limits.subs_per_conn.default")
    for name in ("rest_weight_per_min", "oi_reserve_per_min"):
        for k in lim[name]:
            v, _, kind = k.rpartition("_")
            if not (k in venues or (v in venues and kind in (venues[v].get("markets") or ()))):
                errs.append("E_CONF:limits.%s.%s" % (name, k))
    for k, r in lim["oi_reserve_per_min"].items():
        if k not in lim["rest_weight_per_min"] or r > lim["rest_weight_per_min"][k]:
            errs.append("E_CONF:limits.oi_reserve_per_min." + k)    # резерв OI — часть общего веса рынка
    for v, kind in markets(cfg):
        if rest_weight(cfg, v, kind) is None:
            errs.append("E_CONF:limits.rest_weight_per_min.%s_%s" % (v, kind))
    if bud["cpu_p99_cores"] < bud["cpu_avg_cores"]:
        errs.append("E_CONF:budget.cpu_p99_cores")
    if bud["light_min"] > f["max_light"]:
        errs.append("E_CONF:budget.light_min")
    if st["light_every_s"] < st["deep_every_s"] or st["light_every_s"] % st["deep_every_s"]:
        errs.append("E_CONF:store.light_every_s")
    return errs


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


def defaults():
    return copy.deepcopy(DEFAULTS)


def load(root=None):
    """-> (конфиг, [ошибки]). Годный файл -> (его содержимое, []); нет файла -> (умолчания, []); битый -> (None, [ошибки])."""
    try:
        with open(paths.config(FILE, root=root), encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return defaults(), []
    except (OSError, UnicodeDecodeError):
        return None, ["E_CONF:file"]
    try:
        raw = parse(text)
    except ValueError:
        return None, ["E_CONF:file"]
    errs = check(raw)
    return (None, errs) if errs else (raw, [])


def compat_active(cfg):
    """Совместимый runtime/intel/xflow.json пишет cmkt (задача 22): сводный рынок включён и `compat_xflow = true`."""
    return isinstance(cfg, dict) and cfg.get("enabled") is True and cfg.get("compat_xflow") is True


def compat_xflow_on(root=None):
    """Для сервиса intel (не запускать XStream): годный config/cmkt.json с compat_active. Битый файл -> False: сервис cmkt с ним
    не стартует (код 2), писателем остаётся XStream — у файла всегда один писатель."""
    cfg, errs = load(root)
    return not errs and compat_active(cfg)


def load_strict(root=None):
    """Как load, но битый конфиг -> ConfError (сервис выходит с кодом 2)."""
    cfg, errs = load(root)
    if errs:
        raise ConfError(errs)
    return cfg


def config_hash(cfg):
    """sha256 канонического JSON конфига (для heartbeat): не зависит от форматирования и порядка ключей."""
    blob = json.dumps(cfg, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def host_allowed(cfg, venue, host):
    """Хост из белого списка биржи (сравнение без регистра, без порта)."""
    ent = (cfg.get("venues") or {}).get(venue) if isinstance(cfg, dict) else None
    h = (host or "").strip().lower().rsplit(":", 1)[0] if isinstance(host, str) else ""
    return isinstance(ent, dict) and h in (ent.get("hosts") or ())


def check_host(cfg, venue, host):
    """None — хост разрешён; иначе код HOST_DENIED (соединение отклоняется)."""
    return None if host_allowed(cfg, venue, host) else HOST_DENIED


def venue_enabled(cfg, venue):
    ent = (cfg.get("venues") or {}).get(venue)
    return isinstance(ent, dict) and ent.get("enabled") is True
