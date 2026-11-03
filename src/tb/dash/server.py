"""Маршрутизатор чтения `/api/*` (бывший HTTP-сервер старого дашборда; сервер и статика удалены 2026-11-02, дашборд теперь `tb.dash2`).

HTTP здесь больше нет: остался только чистый `api_payload(path, query, root, now) -> (код, объект)` и реестр расширений EXT_API.
Его используют тесты `dashapi` пакетов (intel, lifecycle, memory, brain, llm): они проверяют, что их `/api/...` ответы проходят `model.scrub` и не ломаются.
Только чтение: на биржу не ходит, ключей Bybit/LLM не читает, процессов не запускает.

  /api/state?mode=live|paper|testnet|all&venue=all|platform|bybit   всё одним ответом (model.build_state)
  /api/market  /api/hot  /api/ideas  /api/health  /api/trades  /api/ping  /api/platform
  /api/v2/*   слой «система плана» (model2.api_v2)
Расширения (WP13/14/15/11-C): EXT_API, модули `tb.<пакет>.dashapi` с функцией api(path, query, root, now), только чтение.
"""
import time

from tb.dash import model, model2

# ---- реестр расширений: модули `tb.<пакет>.dashapi` с функцией api(path, query, root, now) -> None (не мой путь) | (код, объект); подключаются, если модуль существует ----
EXT_API = ("tb.intel.dashapi", "tb.lifecycle.dashapi", "tb.memory.dashapi", "tb.brain.dashapi", "tb.llm.dashapi", "tb.learn.dashapi")
MODES = ("live", "paper", "testnet", "all")


def ext_api(path, query, root, now):
    """Первый расширяющий модуль, взявшийся за путь. -> (код, объект) | None."""
    import importlib
    import importlib.util
    for mod in EXT_API:
        try:
            if importlib.util.find_spec(mod) is None:
                continue
            fn = getattr(importlib.import_module(mod), "api", None)
        except (ImportError, ValueError, AttributeError):
            continue
        if callable(fn):
            r = fn(path, query, root, now)
            if r is not None:
                return r
    return None


def _clean(value, allowed, default):
    return value if value in allowed else default


def api_payload(path, query, root=None, now=None):
    """-> (статус, объект) для /api/*; чистая функция, удобна для тестов."""
    if path.startswith("/api/v2/"):
        code, obj = model2.api_v2(path, query, root, now)
        return code, model.scrub(obj)
    mode = _clean((query.get("mode") or ["live"])[0], MODES, "live")
    venue = (query.get("venue") or ["all"])[0]
    venue = venue if venue in ("all", "platform", "bybit") else "all"
    if path.startswith("/api/ctl/"):      # плоскость управления идёт через ctlweb/ctlbridge, здесь её нет
        return 404, {"error": "unknown endpoint"}
    if path == "/api/ping":
        return 200, {"ok": True, "now": time.time() if now is None else now}
    if path == "/api/platform":          # статус платформы (WP11-B): тот же словарь, что `./tb status --json`
        from tb.platform import status as platform_status
        return 200, model.scrub(platform_status.build_status(root, now))
    if path == "/api/ideas":
        return 200, model.scrub(model.ideas_info(root, now))
    ext = ext_api(path, query, root, now)
    if ext is not None:
        return ext[0], model.scrub(ext[1])
    st = model.build_state(root, mode, venue, now)
    if path == "/api/state":
        return 200, st
    if path == "/api/market":
        return 200, st["market"]
    if path == "/api/hot":
        return 200, st["hot"]
    if path == "/api/health":
        return 200, {"health": st["health"], "warnings": st["warnings"], "events": st["events"]}
    if path == "/api/trades":
        return 200, {"filter": st["filter"], "open": st["open_trades"], "closed": st["history"],
                     "closed_total": st["history_total"], "metrics": st["metrics"]}
    return 404, {"error": "unknown endpoint"}
