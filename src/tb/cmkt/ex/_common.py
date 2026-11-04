"""Общие мелочи адаптеров бирж сводного рынка (R1.7, задачи 8–10): REST с запасным хостом, уровни стакана, дедупликация.

Не адаптер (имя модуля с подчёркиванием `tb.cmkt.ex.load` не принимает). Только публичные эндпоинты, без ключей и подписи.
"""
import time
from collections import OrderedDict

from tb.cmkt import adapter as A
from tb.logic import cmkt as L

DOWN_S = 60.0                                   # все хосты отказали — пауза перед следующей попыткой


class HostChain(object):
    """REST-хосты биржи по порядку с запоминанием рабочего: официальный хост может отвечать 451/403 с IP машины
    (exchanges.md, п. 0), тогда берётся следующий из белого списка (`www.binance.com`, `api.bybitglobal.com`). 418/429 и нехватка
    веса не перебираются (пауза бюджета). Все хосты отказали — пауза DOWN_S без запросов."""

    def __init__(self, hosts, clock=time.monotonic):
        self.hosts = tuple(hosts)
        self.good = None
        self.down_until = None
        self.clock = clock
        self.fails = 0

    def order(self):
        return ([self.good] if self.good else []) + [h for h in self.hosts if h != self.good]

    def get_json(self, http, path, weight=1, kind="meta", timeout=None):
        """timeout — свой предел запроса (передаётся в Http, только если задан)."""
        now = self.clock()
        if self.down_until is not None and now < self.down_until:
            raise A.HttpError(0, "hosts down")
        last = None
        for h in self.order():
            try:
                url = "https://%s%s" % (h, path)
                r = http.get_json(url, weight, kind) if timeout is None else http.get_json(url, weight, kind, timeout=timeout)
            except A.BudgetExceeded:
                raise
            except A.HttpError as e:
                if e.status in (418, 429):
                    raise
                last = e
                continue
            except (A.HostDenied, OSError, ValueError) as e:
                last = e
                continue
            self.good, self.down_until = h, None
            return r
        self.fails += 1
        self.down_until = now + DOWN_S
        if isinstance(last, A.HttpError):
            raise last
        raise A.HttpError(0, "no host: %s" % (last,))


def lv(rows):
    """Уровни биржи [[px, size, ...]] -> [(px_str, size_str)] (OKX шлёт 4 поля на уровень)."""
    out = []
    for r in rows or ():
        if isinstance(r, (list, tuple)) and len(r) >= 2:
            out.append((str(r[0]), str(r[1])))
    return out


def ms(x):
    """Миллисекунды биржи (число или строка) -> секунды или None."""
    v = L.num(x)
    return v / 1000.0 if v is not None and v > 0 else None


class Dedup(object):
    """Ограниченное множество ключей (повторы событий биржи учитываются один раз)."""

    def __init__(self, keep=20000):
        self.keep = int(keep)
        self.d = OrderedDict()

    def seen(self, key):
        if key in self.d:
            return True
        self.d[key] = None
        if len(self.d) > self.keep:
            self.d.popitem(last=False)
        return False


class IdMap(object):
    """Номер запроса подписки -> темы (ответ биржи несёт только номер)."""

    def __init__(self, keep=4000):
        self.keep = int(keep)
        self.d = OrderedDict()

    def put(self, rid, topics, op):
        self.d[rid] = (tuple(topics), op)
        if len(self.d) > self.keep:
            self.d.popitem(last=False)

    def pop(self, rid):
        return self.d.pop(rid, None)


def new_stats():
    return {"unknown": 0, "skip": 0, "invalid_topic": 0, "snap_err": 0, "notice": 0, "liq_old": 0, "liq_dup": 0, "oi_err": 0}
