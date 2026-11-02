"""Свечи с других бирж через ccxt (публичный REST, без ключей) как запасной источник для структуры монеты, когда REST Bybit недоступен
(VPN, гео-блок, лимит: UAI 2026-11-01 ушёл к мозгу без структуры). Указание владельца: монета без данных обогащается тем же ccxt.

Формат результата как у marketdata/structure._rows: [(start_ms, o, h, l, c, volume, turnover)] по возрастанию. Биржи по очереди (okx, bitget, binanceusdm),
первая ответившая побеждает; объём в монетах = contracts x contractSize, оборот = объём x цена закрытия. Объекты бирж живут между вызовами (рынки грузятся раз).
ccxt необязателен: без библиотеки rows() бросает RuntimeError, вызывающий считает источник недоступным. Слой marketdata intel не импортирует."""
import threading
import time

EXCHANGES = ("okx", "bitget", "binanceusdm")
TF = {5: "5m", 60: "1h", 1: "1m", 15: "15m", 240: "4h"}
MARKETS_TTL_S = 6 * 3600.0
_lock = threading.Lock()
_ex = {}
_loaded = {}


def available():
    try:
        import ccxt  # noqa: F401
        return True
    except ImportError:
        return False


def _exchange(eid, factory, now):
    with _lock:
        ex = _ex.get(eid)
        if ex is None:
            if factory is not None:
                ex = factory(eid)
            else:
                import ccxt
                ex = getattr(ccxt, eid)({"enableRateLimit": True, "timeout": 10000, "options": {"defaultType": "swap"}})
            _ex[eid] = ex
        if now - _loaded.get(eid, -1e18) > MARKETS_TTL_S:
            ex.load_markets()
            _loaded[eid] = now
        return ex


def parse(ohlcv, cs=1.0):
    """ccxt OHLCV [[ts_ms, o, h, l, c, vol], ...] -> строки формата CandleBook.seed (оборот = объём x контракт x close)."""
    out = []
    for r in ohlcv or ():
        try:
            ts, o, h, l, c, v = int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5] or 0.0)
        except (TypeError, ValueError, IndexError):
            continue
        vol = v * cs
        out.append((ts, o, h, l, c, vol, vol * c))
    return sorted(out)


def rows(sym, interval, limit, exchanges=EXCHANGES, factory=None, now=None):
    """-> (строки, биржа). Исключение последней биржи наружу, если не ответила ни одна (RuntimeError без ccxt)."""
    if factory is None and not available():
        raise RuntimeError("ccxt не установлен")
    now = time.time() if now is None else now
    tf = TF.get(int(interval))
    if tf is None:
        raise ValueError("интервал %s не поддерживается" % interval)
    m = "%s/USDT:USDT" % sym
    last = None
    for eid in exchanges:
        try:
            ex = _exchange(eid, factory, now)
            if m not in (getattr(ex, "markets", None) or {}):
                continue
            cs = float(((ex.markets or {}).get(m) or {}).get("contractSize") or 1.0)
            data = ex.fetch_ohlcv(m, tf, None, int(limit))
            got = parse(data, cs)
            if got:
                return got, eid
        except Exception as e:  # noqa: BLE001 - следующая биржа
            last = e
    raise last if last is not None else RuntimeError("нет биржи с рынком %s" % m)
