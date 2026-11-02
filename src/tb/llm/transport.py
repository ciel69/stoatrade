"""HTTP-транспорт на stdlib (http.client). Без зависимостей, одинаково на Mac и Windows.

- таймауты: connect_s (соединение), read_s (чтение сокета), total_s (общий бюджет запроса с повторами);
- повторы ТОЛЬКО по статусам из retry.on (по умолчанию 429/500/503): экспоненциальная пауза base_s*2^n с джиттером, не больше max_s,
  Retry-After учитывается (не больше max_s); 400/401/402/422 и сетевые ошибки/таймауты не повторяются;
- keep-alive: соединение хоста переиспользуется (пул по схеме+хост+порт), оборванное простаивавшее соединение пересоздаётся один раз без счёта повтора;
- заголовки (там ключ) нигде не сохраняются и в исключения не попадают;
- сетевая политика (netpolicy): имя резолвится на КАЖДОЕ новое соединение, все адреса проверяются, соединение идёт на проверенный IP (имя остаётся для Host и SNI) —
  защита от DNS rebinding; приватные адреса только при allow_private, link-local/метаданные никогда; редиректы (3xx) НЕ выполняются, ответ 3xx возвращается как есть.
"""
import http.client
import random
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from tb.llm import netpolicy
from tb.llm.types import LLMError

MAX_BODY = 8 * 1024 * 1024
DEFAULT_RETRY = {"max": 3, "base_s": 1.0, "max_s": 8.0, "on": [429, 500, 503]}
DEFAULT_TIMEOUTS = {"connect_s": 10.0, "read_s": 120.0, "total_s": 300.0}
_STALE = (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError, http.client.CannotSendRequest, http.client.ResponseNotReady,
          http.client.BadStatusLine)


def system_resolver(host, port):
    """-> список IP-строк (getaddrinfo)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        raise LLMError("network", "имя не резолвится")
    out = []
    for info in infos:
        if info[4][0] not in out:
            out.append(info[4][0])
    return out


class Reply(object):
    def __init__(self, status, headers, body, retries):
        self.status, self.headers, self.body, self.retries = status, headers, body, retries


class Transport(object):
    def __init__(self, sleep=time.sleep, rand=random.random, clock=time.monotonic, keepalive=True, resolver=None):
        self.sleep, self.rand, self.clock, self.keepalive = sleep, rand, clock, keepalive
        self.resolver = resolver or system_resolver
        self._pool = {}
        self._lock = threading.Lock()

    def close(self):
        with self._lock:
            conns, self._pool = list(self._pool.values()), {}
        for c in conns:
            try:
                c.close()
            except Exception:
                pass

    # ---- пул ----
    def _take(self, key):
        with self._lock:
            return self._pool.pop(key, None)

    def _give(self, key, conn):
        with self._lock:
            old = self._pool.get(key)
            self._pool[key] = conn
        if old is not None and old is not conn:
            try:
                old.close()
            except Exception:
                pass

    def _new(self, parts, connect_s, allow_private=False):
        port = parts.port
        tr = self

        class _Pinned(object):
            """Соединение само резолвит имя, проверяет ВСЕ адреса политикой и подключается к проверенному IP (Host и SNI остаются по имени)."""

            def connect(self):
                host = self.host
                ips = tr.resolver(host, self.port)
                bad = netpolicy.check_ips(host, parts.scheme, ips, allow_private)
                if bad:
                    raise LLMError("config", bad)
                last = None
                for ip in ips:
                    try:
                        sock = socket.create_connection((ip.split("%", 1)[0], self.port), self.timeout)
                    except OSError as e:
                        last = e
                        continue
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    self.sock = self._wrap(sock, host)
                    return
                raise last or OSError("нет адресов")

        if parts.scheme == "https":
            class _S(_Pinned, http.client.HTTPSConnection):
                def _wrap(self, sock, host):
                    return self._context.wrap_socket(sock, server_hostname=host)
            return _S(parts.hostname, port, timeout=connect_s, context=ssl.create_default_context())

        class _P(_Pinned, http.client.HTTPConnection):
            def _wrap(self, sock, host):
                return sock
        return _P(parts.hostname, port, timeout=connect_s)

    def _once(self, method, url, headers, body, tmo, deadline, allow_private=False, max_body=MAX_BODY):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise LLMError("config", "некорректный base_url")
        path = (parts.path or "/") + ("?" + parts.query if parts.query else "")
        key = (parts.scheme, parts.hostname, parts.port, bool(allow_private))
        for fresh in (False, True):
            conn = None if fresh else (self._take(key) if self.keepalive else None)
            pooled = conn is not None
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise LLMError("timeout", "исчерпан общий таймаут запроса")
            try:
                if conn is None:
                    conn = self._new(parts, min(tmo["connect_s"], remaining), allow_private)
                    conn.connect()
                conn.sock.settimeout(max(0.05, min(tmo["read_s"], remaining)))
                conn.request(method, path, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read(max_body + 1)
                if len(data) > max_body:
                    conn.close()
                    raise LLMError("bad_body", "ответ слишком большой")
                hdrs = {k.lower(): v for k, v in resp.getheaders()}
                if self.keepalive and not resp.will_close:
                    self._give(key, conn)
                else:
                    conn.close()
                return resp.status, hdrs, data
            except LLMError:
                raise
            except socket.timeout:
                self._drop(conn)
                raise LLMError("timeout", "таймаут соединения или чтения")
            except _STALE as e:
                self._drop(conn)
                if pooled:
                    continue          # простаивавшее keep-alive соединение закрыто сервером: один раз заново, не повтор
                raise LLMError("network", "соединение оборвано (%s)" % e.__class__.__name__)
            except (OSError, http.client.HTTPException) as e:
                self._drop(conn)
                if pooled:
                    continue          # битое простаивавшее соединение: один раз заново
                raise LLMError("network", "ошибка соединения (%s)" % e.__class__.__name__)
        raise LLMError("network", "ошибка соединения")

    @staticmethod
    def _drop(conn):
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

    def request(self, method, url, headers, body, timeouts=None, retry=None, total_s=None, allow_private=False, max_body=MAX_BODY):
        """-> Reply (любой статус, 3xx тоже: редиректы не выполняются; ошибки статусов превращает в LLMError вызывающий). Повторы по retry['on'] внутри."""
        tmo = dict(DEFAULT_TIMEOUTS)
        tmo.update(timeouts or {})
        rt = dict(DEFAULT_RETRY)
        rt.update(retry or {})
        deadline = self.clock() + float(total_s if total_s else tmo["total_s"])
        attempt = 0
        while True:
            status, hdrs, data = self._once(method, url, headers, body, tmo, deadline, allow_private, max_body)
            if status in rt["on"] and attempt < int(rt["max"]):
                delay = min(float(rt["max_s"]), float(rt["base_s"]) * (2 ** attempt))
                delay *= 0.5 + 0.5 * self.rand()
                ra = hdrs.get("retry-after")
                if ra and ra.strip().isdigit():
                    delay = max(delay, min(float(ra), float(rt["max_s"])))
                if self.clock() + delay < deadline:
                    attempt += 1
                    self.sleep(delay)
                    continue
            return Reply(status, hdrs, data, attempt)
