"""Аудит шлюза: append-only `state/gateway_audit.jsonl` (WP11-A).

Строка: {"ts","t","agent","tool","args","ok","code","ms","via"}; `args` = первые 16 hex SHA-256 канонического JSON аргументов (сами аргументы,
токены и результаты НЕ пишутся). Запись под FileLock (одинаково на POSIX и Windows). Сбой записи аудита не роняет вызов, но возвращается как False.
"""
import hashlib
import json
import os
import re
import time

from tb import paths
from tb.core.filelock import FileLock

TAIL_BYTES = 256 * 1024
_RE_FIELD = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")


def audit_file(root=None):
    return paths.state("gateway_audit.jsonl", root=root)


def args_hash(args):
    try:
        raw = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    except (TypeError, ValueError):
        raw = repr(type(args))
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _field(v):
    return v if isinstance(v, str) and _RE_FIELD.fullmatch(v) else "?"


def record(agent, tool, args, ok, code, ms, via="inproc", root=None, now=None):
    """Дописывает запись аудита -> True/False (False: не удалось записать)."""
    now = time.time() if now is None else now
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "t": round(now, 3), "agent": _field(agent), "tool": _field(tool),
           "args": args_hash(args), "ok": ok if ok in (True, False, None) else False, "code": _field(code) if code else "OK",
           "ms": int(max(0, ms)), "via": _field(via)}
    path = audit_file(root)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with FileLock(path + ".lock", wait=5.0):
            with open(path, "ab") as f:
                f.write((json.dumps(rec, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii"))
        return True
    except (OSError, TimeoutError):
        return False


def tail(n=50, root=None):
    """Последние n записей (по возрастанию времени); битые строки пропускаются."""
    path = audit_file(root)
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            data = f.read()
    except OSError:
        return []
    rows = []
    for ln in data.decode("utf-8", "replace").splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            rows.append(r)
    return rows[-n:] if n else rows


def count_recent(agent, tool, window_s, root=None, now=None, only_ok=True):
    """Сколько раз агент вызывал инструмент за последние window_s секунд (по аудиту; перезапуск не сбрасывает)."""
    now = time.time() if now is None else now
    return sum(1 for r in tail(0, root) if r.get("agent") == agent and r.get("tool") == tool and isinstance(r.get("t"), (int, float))
               and now - r["t"] <= window_s and (not only_ok or r.get("ok") is not False))


def next_slot(agent, tool, limit, window_s=3600.0, root=None, now=None):
    """Секунд до освобождения слота в скользящем окне; 0, если слот уже есть.
    Слот освобождается, когда самая старая из учтённых записей выходит из окна."""
    now = time.time() if now is None else now
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        return 0.0
    ts = [r["t"] for r in tail(0, root) if r.get("agent") == agent and r.get("tool") == tool and isinstance(r.get("t"), (int, float))
          and now - r["t"] <= window_s and r.get("ok") is not False]
    if len(ts) < limit:
        return 0.0
    ts.sort()
    return max(0.0, float(window_s) - (now - ts[len(ts) - limit]))


def wait_text(seconds):
    """Секунды -> короткая строка для сообщения пользователю."""
    s = int(round(max(0.0, float(seconds or 0.0))))
    return ("%d мин %02d с" % (s // 60, s % 60)) if s >= 60 else ("%d с" % s)
