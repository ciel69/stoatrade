"""Чтение для дашборда (EXT_API): `/api/learning` и функции для операции сокета `learning` (R0.2, задача 12). Только чтение.

api(path, query, root, now)  контракт реестра EXT_API (tb.dash.server): None, если путь не наш; иначе (код, объект);
detail(root, mode, now)      ответ операции `learning` (learning_detail): отчёт или {"ok": False, "mode", "error": "failed"}; model.scrub делает вызывающий слой;
is_computing(payload)        есть ли блок `computing`;
follow(root, mode, timeout)  ждёт блок памяти и отдаёт отчёт заново (повторное learning_detail); None, если ждать нечего или не дождались.
Блокирующие функции (detail, follow) вызываются из потока (asyncio.to_thread).
"""
import logging
from concurrent.futures import TimeoutError as FutureTimeout

from tb.learn import cache, memory_vs_base, report

PATH = "/api/learning"
FOLLOW_TIMEOUT_S = 30.0
log = logging.getLogger("tb.learn.dashapi")


def _mode(query):
    """mode из query ({"mode": ["paper"]} или {"mode": "paper"}); чужое значение -> paper."""
    v = (query or {}).get("mode")
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    return report.normalize_mode(v)


def failed(mode):
    return {"ok": False, "mode": report.normalize_mode(mode), "error": "failed"}


def detail(root, mode, now=None):
    """Отчёт для операции `learning`. Исключение сборки -> {"ok": False, "mode", "error": "failed"} (причина только в журнале сервера, без значений)."""
    try:
        return report.get(root, mode, now)
    except Exception as e:  # noqa: BLE001 - отчёт не должен рвать соединение
        log.warning("learning report failed: %s", type(e).__name__)
        return failed(mode)


def is_computing(payload):
    return report.has_computing(payload)


def follow(root, mode, timeout=FOLLOW_TIMEOUT_S, now=None):
    """Дождаться фонового расчёта блока памяти (до timeout секунд) и вернуть новый отчёт; None, если расчёта нет, он не уложился или упал."""
    mode = report.normalize_mode(mode)
    fut = cache.MEMORY.peek(memory_vs_base.flight_key(root, mode))
    if fut is None:
        return None
    try:
        fut.result(timeout=timeout)
    except FutureTimeout:
        return None
    except Exception:  # noqa: BLE001 - упавший расчёт покажет блок error при пересборке
        pass
    cache.REPORT.drop((str(root), mode))
    out = detail(root, mode, now)
    return None if is_computing(out) else out


def api(path, query, root=None, now=None):
    """Контракт реестра EXT_API: None, если путь не наш."""
    if path != PATH:
        return None
    mode = _mode(query)
    out = detail(root, mode, now)
    return (200 if out.get("ok") else 500), out
