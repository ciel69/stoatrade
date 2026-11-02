"""Периодический писатель случаев (WP15b, 08 п. 1.3 «код-писатель»): закрытые сделки, пропуски и итоги идей -> `state/cases.jsonl`.

Один шаг = `migrate.migrate(final_only=True)` (идемпотентно: case_id = функция источника, повторный запуск ничего не дублирует, whatif/разбор
дописываются update-строкой, один раз) + пересчёт уроков (`lessons.refresh`). Вызывается:
  * сервисом ctl каждый цикл через `tick()` (не чаще `tick.interval_s` из config/memory.json, по умолчанию 60 с; сбой не роняет ctl);
  * раннером мозга перед разбором закрытия (`sync_now`), чтобы случай для ревьюера точно существовал;
  * вручную `./tb memory sync`.
Ничего не торгует, сети нет; пишет только cases.jsonl и lessons.jsonl своего корня. Отдельного сервиса платформы не требуется.
"""
import json
import os
import time

from tb import paths
from tb.core import ideas
from tb.memory import cases, cfg as memcfg, lessons, migrate

_LAST = {}


def state_file(root=None):
    return paths.runtime("memory", "tick_state.json", root=root)


def snapshot(root=None):
    """Размер и mtime файлов-источников migrate (сделки, идеи, исходы, решения и их сегменты, config/memory.json) -> {путь: [size, mtime_ns]}."""
    dec = paths.decisions_file(root)
    files = [paths.state("trades.jsonl", root=root), ideas.ideas_path(root), ideas.outcomes_path(root), dec, paths.config("memory.json", root=root)]
    seg = os.path.join(os.path.dirname(dec), "decisions.d")
    try:
        files += [os.path.join(seg, n) for n in sorted(os.listdir(seg))]
    except OSError:
        pass
    out = {}
    for p in files:
        try:
            st = os.stat(p)
            out[os.path.basename(os.path.dirname(p)) + "/" + os.path.basename(p)] = [st.st_size, st.st_mtime_ns]
        except OSError:
            out[os.path.basename(os.path.dirname(p)) + "/" + os.path.basename(p)] = None
    return out


def _saved_snapshot(root):
    try:
        with open(state_file(root), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _save_snapshot(root, snap):
    try:
        p = state_file(root)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f)
        os.replace(tmp, p)
    except OSError:
        pass


def sync_now(root=None, now=None, cfg=None, lessons_too=True, skip_unchanged=True):
    """Один шаг без ограничения частоты -> отчёт {"written","new","lessons"} (ошибки наружу как исключения).
    skip_unchanged: источники (размер и mtime) не менялись с прошлого успешного прогона без ошибок -> migrate не вызывается, отчёт с "skipped": True."""
    now = time.time() if now is None else now
    cfg = cfg or memcfg.load(root)
    try:
        ideas.resolve_planned(root, now)           # идеи с поданным планом: сделка или закрытие без входа
    except (OSError, ValueError, KeyError, TypeError):
        pass
    snap = snapshot(root)
    if skip_unchanged and _saved_snapshot(root) == snap and os.path.exists(cases.cases_path(root)):
        return {"written": 0, "new": {}, "errors": [], "skipped": True}
    rep = migrate.migrate(root, dry_run=False, cfg=cfg, final_only=True)
    if not rep.get("errors"):
        _save_snapshot(root, snap)
    out = {"written": rep.get("written", 0), "new": rep.get("new", {}), "errors": rep.get("errors", [])[:3]}
    if lessons_too and rep.get("written"):
        try:
            out["lessons"] = len(lessons.refresh(root, cases.load(root), cfg, now))
        except (OSError, ValueError, KeyError, TypeError):
            out["lessons"] = None
    return out


def tick(root=None, now=None, force=False):
    """Шаг с ограничением частоты (config/memory.json: tick.interval_s, tick.enabled). -> отчёт или None, если рано/выключено/сбой.
    Любая ошибка проглатывается: писатель памяти не должен мешать сервису, в котором вызван."""
    now = time.time() if now is None else now
    try:
        cfg = memcfg.load(root)
        tc = cfg.get("tick") or {}
        if not tc.get("enabled", True):
            return None
        key = root or ""
        if not force and now - _LAST.get(key, -1e12) < float(tc.get("interval_s", 60.0)):
            return None
        _LAST[key] = now
        return sync_now(root, now, cfg)
    except Exception:       # noqa: BLE001 - см. docstring
        return None
