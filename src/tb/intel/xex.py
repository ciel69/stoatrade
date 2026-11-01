"""Кросс-биржевые данные через ccxt (только чтение, без ключей): цена, фандинг, оборот 24 ч и открытый интерес бессрочных USDT-контрактов
по символам intel на нескольких биржах ОДНИМ циклом, чтобы цены сравнивались на один момент. Bybit берётся тем же циклом (база сравнения).

ccxt необязателен: без библиотеки источник молчит (кэша нет -> в facts и статусе его нет), остальной intel работает как раньше.
Исполнитель и торговый ключ ccxt не используют: модуль импортируется только сервисом intel.
Кэш: runtime/intel/cache/xex_<биржа>.json того же вида, что у fetch.Fetcher ({"ok","fetched","fails","next_try","last_error","data"}),
data = {"symbols": {SYM: {"px", "fund_pct", "vol_usd", "oi_usd"}}, "vols": {SYM: оборот 24 ч USD по ВСЕМ USDT-контрактам биржи}}.
Сравнение с Bybit считает store.build_facts (cross_rows). Обороты всех монет других бирж -> runtime/xvol.json для тепловой карты сканера (hotscan.build_heat).
"""
import time

from tb import paths
from tb.core import filelock
from tb.intel import fetch as ifetch
from tb.marketdata.xvol import XVOL_STALE_S

DEFAULT_EXCHANGES = ("bybit", "binanceusdm", "okx", "bitget")
INTERVAL_S = 300
MARKETS_TTL_S = 6 * 3600
TIMEOUT_MS = 10000


def available():
    try:
        import ccxt  # noqa: F401
        return True
    except ImportError:
        return False


def xcfg(cfg):
    x = (cfg or {}).get("xex") if isinstance((cfg or {}).get("xex"), dict) else {}
    ex = [e for e in (x.get("exchanges") or DEFAULT_EXCHANGES) if isinstance(e, str) and e.isalnum()]
    return {"enabled": bool(x.get("enabled", True)), "exchanges": ex, "interval_s": float(x.get("interval_s", INTERVAL_S)),
            "oi": bool(x.get("oi", True)), "timeout_ms": int(x.get("timeout_ms", TIMEOUT_MS))}


def source_defs(cfg):
    """Описания источников xex_<биржа> для статуса и facts (kind "ccxt": обычный Fetcher их не загружает)."""
    c = xcfg(cfg)
    if not c["enabled"]:
        return []
    return [{"id": "xex_%s" % e, "base": "xex", "kind": "ccxt", "group": e, "exchange": e, "interval_s": c["interval_s"], "enabled": True,
             "verified": False, "url": "", "domain": e, "fields": ["symbols"]} for e in c["exchanges"]]


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


class Collector(object):
    """Держит объекты бирж ccxt между циклами (рынки грузятся раз в MARKETS_TTL_S)."""

    def __init__(self, root, cfg, clock=time.time, factory=None, log=None):
        self.root, self.cfg, self.clock = root, cfg, clock
        self.factory = factory
        self.log = log or (lambda m: None)
        self._ex, self._mk = {}, {}

    def _exchange(self, eid, now):
        ex = self._ex.get(eid)
        if ex is None:
            if self.factory is not None:
                ex = self.factory(eid)
            else:
                import ccxt
                ex = getattr(ccxt, eid)({"timeout": xcfg(self.cfg)["timeout_ms"], "enableRateLimit": True, "options": {"defaultType": "swap"}})
            self._ex[eid] = ex
        if now - self._mk.get(eid, -1e18) >= MARKETS_TTL_S:
            ex.load_markets()
            self._mk[eid] = now
        return ex

    def due(self, src, now):
        c = ifetch.read_cache(src["id"], self.root)
        if now < float(c.get("next_try") or 0):
            return False
        return now - float(c.get("tried") or c.get("fetched") or 0) >= float(src["interval_s"])

    def collect(self, eid, symbols, now):
        """-> {"symbols": {SYM: {...}}, "vols": {SYM: оборот USD}} по одной бирже: один запрос всех тикеров. Исключения ccxt наружу (их ловит step)."""
        ex = self._exchange(eid, now)
        tick = ex.fetch_tickers() or {}
        vols = {}
        for m, t in tick.items():
            if not isinstance(m, str) or not m.endswith("/USDT:USDT") or not isinstance(t, dict):
                continue
            v, px = _vol(ex, m, t), _f(t.get("last"))
            if v and px:
                vols[m.split("/")[0]] = [round(v, 0), px]              # цена нужна, чтобы не склеить разные токены с одним тикером
        mk = {s: "%s/USDT:USDT" % s for s in symbols if "%s/USDT:USDT" % s in (ex.markets or {})}
        if not mk:
            return {"symbols": {}, "vols": vols}
        try:
            fr = ex.fetch_funding_rates(list(mk.values()))
        except Exception:  # noqa: BLE001 - фандинг не у всех бирж пачкой: без него строка остаётся с ценой и оборотом
            fr = {}
        out = {}
        for sym, m in mk.items():
            t = tick.get(m) or {}
            px = _f(t.get("last"))
            if px is None:
                continue
            vol = _vol(ex, m, t)
            row = {"px": px, "vol_usd": None if vol is None else round(vol, 0)}
            f = _f((fr.get(m) or {}).get("fundingRate"))
            if f is not None:
                row["fund_pct"] = round(f * 100.0, 5)
            if xcfg(self.cfg)["oi"] and ex.has.get("fetchOpenInterest"):
                try:
                    oi = ex.fetch_open_interest(m) or {}
                    v = _f(oi.get("openInterestValue"))
                    if v is None and _f(oi.get("openInterestAmount")) is not None:
                        v = _f(oi.get("openInterestAmount")) * px
                    if v is not None:
                        row["oi_usd"] = round(v, 0)
                except Exception:  # noqa: BLE001
                    pass
            out[sym] = row
        return {"symbols": out, "vols": vols}

    def step(self, symbols, now, force=False):
        """Опрос бирж, у которых подошёл срок. -> список обновлённых id. Не бросает."""
        if not available() and self.factory is None:
            return []
        upd = []
        for src in source_defs(self.cfg):
            if not force and not self.due(src, now):
                continue
            cache = ifetch.read_cache(src["id"], self.root)
            try:
                data = self.collect(src["exchange"], symbols, now)
                if not data["symbols"] and not data["vols"]:
                    raise ValueError("no_symbols")
                doc = {"id": src["id"], "ok": True, "fetched": now, "tried": now, "fails": 0, "next_try": 0, "last_error": None, "data": data}
                upd.append(src["id"])
            except Exception as e:  # noqa: BLE001 - ни одна биржа не роняет intel
                fails = int(cache.get("fails") or 0) + 1
                doc = dict(cache)
                doc.update({"id": src["id"], "ok": False, "tried": now, "fails": fails, "last_error": type(e).__name__[:40],
                            "next_try": now + min(3600.0, 60.0 * 2 ** (fails - 1))})
                self.log("intel: %s недоступен (%s)" % (src["id"], type(e).__name__))
            try:
                ifetch.write_cache(src["id"], doc, self.root)
            except OSError:
                pass
        if any(u != "xex_bybit" for u in upd):
            try:
                write_xvol(self.root, now, self.cfg)
            except (OSError, ValueError):
                pass
        return upd


def _vol(ex, m, t):
    """Оборот 24 ч в USD: quoteVolume, иначе baseVolume x размер контракта x цена (OKX: baseVolume в контрактах, у BTC контракт 0.01 BTC)."""
    v = _f(t.get("quoteVolume"))
    px = _f(t.get("last"))
    if v is None and _f(t.get("baseVolume")) is not None and px:
        v = _f(t.get("baseVolume")) * (_f(((ex.markets or {}).get(m) or {}).get("contractSize")) or 1.0) * px
    return v


def xvol_path(root=None):
    return paths.runtime("xvol.json", root=root)


def write_xvol(root, now, cfg=None):
    """runtime/xvol.json = {"ts_epoch", "ex": {биржа: {"at", "vols": {SYM: USD}}}} из свежих кэшей других бирж (Bybit берёт сам сканер)."""
    ex = {}
    for s in source_defs(cfg or {}):
        if s["exchange"] == "bybit":
            continue
        c = ifetch.read_cache(s["id"], root)
        f = c.get("fetched")
        vols = (c.get("data") or {}).get("vols")
        if isinstance(f, (int, float)) and now - f <= XVOL_STALE_S and isinstance(vols, dict) and vols:
            ex[s["exchange"]] = {"at": f, "vols": vols}
    filelock.atomic_write_json(xvol_path(root), {"ts_epoch": now, "ex": ex}, indent=None)


def cross_rows(by_ex, symbols):
    """{биржа: {SYM: row}} -> {SYM: {"xfund_pct", "basis_bps", "bybit_vol_pct", "bybit_oi_pct", "lead"}} относительно Bybit.
    xfund_pct: средний фандинг других бирж; basis_bps: цена Bybit против медианы других; *_pct: доля Bybit в сумме по биржам; lead: биржа с наибольшим оборотом."""
    bb = by_ex.get("bybit") or {}
    out = {}
    for sym in symbols:
        b = bb.get(sym)
        others = {e: d[sym] for e, d in by_ex.items() if e != "bybit" and isinstance(d, dict) and isinstance(d.get(sym), dict)}
        if not isinstance(b, dict) or not others:
            continue
        row = {"n": len(others)}
        fs = [r["fund_pct"] for r in others.values() if _f(r.get("fund_pct")) is not None]
        if fs:
            row["xfund_pct"] = round(sum(fs) / len(fs), 5)
        px = sorted(r["px"] for r in others.values() if _f(r.get("px")))
        if px and _f(b.get("px")):
            med = px[len(px) // 2] if len(px) % 2 else (px[len(px) // 2 - 1] + px[len(px) // 2]) / 2.0
            row["basis_bps"] = round((b["px"] - med) / med * 1e4, 1)
        for k, name in (("vol_usd", "bybit_vol_pct"), ("oi_usd", "bybit_oi_pct")):
            tot = [r.get(k) for r in list(others.values()) + [b] if _f(r.get(k))]
            if _f(b.get(k)) and len(tot) >= 2:
                row[name] = round(b[k] / sum(tot) * 100.0, 1)
        vols = {e: r.get("vol_usd") for e, r in list(others.items()) + [("bybit", b)] if _f(r.get("vol_usd"))}
        if vols:
            row["lead"] = max(vols, key=vols.get)
        out[sym] = row
    return out
