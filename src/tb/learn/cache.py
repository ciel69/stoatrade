"""Кеш отчёта «Учится ли мозг» и single-flight (R0.2, задача 3).

Cache.get: значение живо, пока now - t < ttl и подпись источников не менялась.
При смене подписи пересчёт не чаще одного раза в min_interval на ключ.
Параллельные вызовы одного ключа ждут один расчёт; исключение загрузчика
в кеш не попадает. Время кеша монотонное (тесты подставляют now).

Flight: один фоновый поток на ключ (тяжёлый блок памяти).
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future


class Cache:
    def __init__(self):
        self._mu = threading.Lock()
        self._data = {}   # key -> (value, sig, t)
        self._locks = {}  # key -> Lock

    def _lock(self, key):
        with self._mu:
            lk = self._locks.get(key)
            if lk is None:
                lk = self._locks[key] = threading.Lock()
            return lk

    def _fresh(self, key, sig, ttl, min_interval, now):
        """(значение, True) если запись годна, иначе None."""
        with self._mu:
            ent = self._data.get(key)
        if ent is None:
            return None
        value, esig, t = ent
        age = now - t
        if age < 0:  # часы ушли назад: запись недоверенная
            return None
        if age >= ttl:
            return None
        if esig != sig and age >= min_interval:
            return None
        return value

    def get(self, key, sig, loader, ttl, min_interval, now=None):
        """Вернуть (значение, из_кеша). loader() вызывается под блокировкой ключа."""
        t_now = time.monotonic() if now is None else now
        hit = self._fresh(key, sig, ttl, min_interval, t_now)
        if hit is not None:
            return hit, True
        with self._lock(key):
            # пока ждали, другой поток мог посчитать
            t_now = time.monotonic() if now is None else now
            hit = self._fresh(key, sig, ttl, min_interval, t_now)
            if hit is not None:
                return hit, True
            value = loader()  # исключение уходит наверх, кеш не трогаем
            t_done = time.monotonic() if now is None else now
            with self._mu:
                self._data[key] = (value, sig, t_done)
            return value, False

    def clear(self):
        with self._mu:
            self._data.clear()

    def drop(self, key):
        """Убрать запись ключа (отчёт с блоком `computing` не держится в кеше)."""
        with self._mu:
            self._data.pop(key, None)


class Flight:
    """Один фоновый расчёт на ключ."""

    def __init__(self):
        self._mu = threading.Lock()
        self._fut = {}

    def start(self, key, fn) -> Future:
        """Запустить fn в потоке, если по ключу нет работающего расчёта; иначе вернуть его."""
        with self._mu:
            cur = self._fut.get(key)
            if cur is not None and not cur.done():
                return cur
            fut = Future()
            self._fut[key] = fut

        def run():
            try:
                fut.set_result(fn())
            except BaseException as e:  # noqa: BLE001 - отдаём ожидающим
                fut.set_exception(e)

        threading.Thread(target=run, name=f"learn-flight-{key}", daemon=True).start()
        return fut

    def peek(self, key):
        """Последний Future по ключу (идущий или готовый) либо None."""
        with self._mu:
            return self._fut.get(key)

    def clear(self):
        with self._mu:
            self._fut.clear()


REPORT = Cache()
MEMORY = Flight()
