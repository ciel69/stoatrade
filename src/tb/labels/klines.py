"""Загрузка свечей для разметки (R1 label factory): публичный GET /v5/market/kline без ключа.

Только stdlib. Каталог ключей не читается, файлов модуль не открывает. Сетевой слой внедряется параметром http:
http(url) -> (status:int, body:str); ошибка сети - исключение OSError. Ошибки для вызывающего: FetchError (после повторов 2/5/15 с;
метку откладывает вызывающий) и RateLimited (HTTP 403/429; все запросы Limiter стоят 60 с).
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api.bybit.com/v5/market/kline"
PAGE = 1000
RETRY_PAUSES = (2, 5, 15)
RATE_PAUSE_S = 60
BTC_SYMBOL = "BTCUSDT"
BUCKET_S = 300
TIMEOUT_S = 10


class FetchError(Exception):
    """Свечи не получены после повторов (сеть, 5xx, код 10002 или иной отказ Bybit)."""


class RateLimited(FetchError):
    """Биржа ответила HTTP 403/429: все запросы остановлены на 60 с."""


class Limiter:
    """Ограничитель частоты запросов (rps) и общая пауза после 403/429. clock() -> секунды, sleep(секунды)."""

    def __init__(self, rps=5.0, clock=time.monotonic, sleep=time.sleep):
        self.interval = 1.0 / rps if rps and rps > 0 else 0.0
        self.clock = clock
        self.sleep = sleep
        self.next_at = None
        self.blocked_until = 0.0
        self.rate_limited = 0          # число 403/429 для heartbeat (rate_limited_24h считает вызывающий по приращению)
        self.requests = 0

    def wait(self):
        now = self.clock()
        t = max(self.next_at if self.next_at is not None else now, self.blocked_until)
        if t > now:
            self.sleep(t - now)
            now = t
        self.next_at = now + self.interval
        self.requests += 1

    def block(self, seconds=RATE_PAUSE_S):
        """Остановить все запросы на seconds секунд от текущего момента."""
        self.rate_limited += 1
        self.blocked_until = max(self.blocked_until, self.clock() + seconds)


def default_http(url):
    """GET через urllib: (status, text). HTTP-ошибки возвращаются статусом, сетевые - исключением OSError."""
    req = urllib.request.Request(url, headers={"User-Agent": "tb-labels/1"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def _page(url, limiter, http):
    """Один запрос с повторами 2/5/15 с -> result.list. Исключения: RateLimited, FetchError."""
    last = "unknown"
    for attempt in range(len(RETRY_PAUSES) + 1):
        if attempt:
            limiter.sleep(RETRY_PAUSES[attempt - 1])
        limiter.wait()
        try:
            status, body = http(url)
        except (OSError, ValueError) as e:                      # сеть, таймаут
            last = "network: %s" % e
            continue
        if status in (403, 429):
            limiter.block(RATE_PAUSE_S)
            raise RateLimited("HTTP %s" % status)
        if status >= 500:
            last = "HTTP %s" % status
            continue
        if status != 200:
            raise FetchError("HTTP %s" % status)
        try:
            d = json.loads(body)
            code = int(d.get("retCode", -1))
        except (ValueError, TypeError, AttributeError):
            last = "bad json"
            continue
        if code == 10002:
            last = "retCode 10002"
            continue
        if code == 10001 and "symbol" in str(d.get("retMsg") or "").lower():
            return []                                           # "params error: Symbol Is Invalid": контракта нет на бирже (снят) -> свечей нет, не сбой сети
        if code != 0:
            raise FetchError("retCode %s" % code)
        lst = (d.get("result") or {}).get("list")
        return lst if isinstance(lst, list) else []
    raise FetchError(last)


def bybit_symbol(sym):
    """Тикер сканера ('BEAT', '1000PEPE') -> символ линейного контракта Bybit ('BEATUSDT'); полные имена (…USDT, …USDC) не меняются."""
    s = str(sym or "").strip().upper()
    return s if s.endswith(("USDT", "USDC")) else s + "USDT"


def fetch(sym, interval, start_s, end_s, limiter, http=None, now_s=None):
    """Закрытые свечи sym интервала interval ('1','5','60' минут) с открытием в [start_s, end_s] -> Row по возрастанию.

    Пагинация по 1000 назад от end_s (Bybit отдаёт новые первыми); незакрытая свеча отбрасывается (tb.logic.bars.from_bybit).
    sym — тикер сканера ('BEAT') или полный символ ('BEATUSDT'): в запрос уходит символ контракта (bybit_symbol).
    """
    from tb.logic import bars
    sym = bybit_symbol(sym)
    http = http or default_http
    now_s = int(time.time()) if now_s is None else int(now_s)
    tf_s = int(interval) * 60
    out = {}
    end_ms = int(end_s) * 1000
    start_ms = int(start_s) * 1000
    while end_ms >= start_ms:
        q = urllib.parse.urlencode({"category": "linear", "symbol": sym, "interval": interval,
                                    "start": start_ms, "end": end_ms, "limit": PAGE})
        lst = _page(BASE_URL + "?" + q, limiter, http)
        ts = []
        for r in lst:
            try:
                ts.append(int(float(r[0])))
            except (TypeError, ValueError, IndexError):
                continue
        for r in bars.from_bybit(lst, tf_s, now_s):
            out[r[0]] = r
        if len(lst) < PAGE or not ts or min(ts) >= end_ms:
            break
        end_ms = min(ts) - 1
    return [out[k] for k in sorted(out) if int(start_s) <= k <= int(end_s)]


class BtcCache:
    """5м BTCUSDT по корзине t0 // 300: на корзину не больше одного успешного запроса (300 свечей, закрытых не позже корзины)."""

    def __init__(self, limiter, http=None, now_s=None, max_buckets=8):
        self.limiter = limiter
        self.http = http
        self.now_s = now_s
        self.max_buckets = max_buckets
        self._c = {}

    def get(self, t0_s):
        b = int(t0_s) // BUCKET_S
        if b not in self._c:
            end = b * BUCKET_S - 300                  # последняя свеча 5м, закрытая не позже начала корзины
            rows = fetch(BTC_SYMBOL, "5", end - 299 * 300, end, self.limiter, self.http, self.now_s)
            while len(self._c) >= self.max_buckets:
                self._c.pop(next(iter(self._c)))
            self._c[b] = rows
        return list(self._c[b])
