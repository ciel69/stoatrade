"""Темы нового дашборда (WebSocket): сборка данных из тех же источников, что у старого, с белыми списками полей. Только чтение.

brain: состояние мозга (работает/думает/ждёт/только защита/нет связи), активная модель по ролям (основной профиль и кто отвечал последним), очередь, расход за сутки,
последние решения и их пояснения («мысли»: brain_decision и brain_why из журнала решений). Сырое рассуждение модели не хранится и не показывается.
Данные темы не содержат «возрастов» (они меняются каждую секунду и рассылались бы по кругу): только метки времени, возраст считает клиент.
Тексты пояснений уже очищены при записи (decisions._clean_why, validate.redact); на клиент они уходят как текст и вставляются без HTML.
"""
import json
import os
import time

from tb import paths

DECISIONS_TAIL = 400            # сколько последних записей журнала просматриваем
DECISIONS_OUT = 24              # сколько решений отдаём
CALLS_TAIL_BYTES = 128 * 1024
RECENT_CALL_S = 600.0           # «последний ответивший профиль» помнится столько секунд
ROLES_TTL_S = 10.0              # таблица ролей считается дорого (читает расход за сутки): обновляем раз в 10 с
HB_STALE_S = 30.0


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _tail_rows(path, max_bytes=CALLS_TAIL_BYTES):
    try:
        with open(path, "rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except OSError:
        return []
    rows = []
    for ln in data.decode("utf-8", "replace").splitlines()[1 if size > max_bytes else 0:]:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            rows.append(r)
    return rows


_roles_cache = {"at": 0.0, "root": None, "rows": []}


def _roles(root, now):
    c = _roles_cache
    if c["root"] == root and now - c["at"] < ROLES_TTL_S:
        return c["rows"]
    try:
        from tb.brain import profiles
        rows = profiles.role_rows(root, now).get("roles", [])
    except Exception:
        rows = []
    c.update(at=now, root=root, rows=rows)
    return rows


def _model_of(p):
    if not isinstance(p, dict):
        return None
    return {"id": p.get("id"), "provider": p.get("provider"), "model": p.get("model_name") or p.get("model"), "enabled": p.get("provider_enabled"),
            "missing": bool(p.get("missing"))}


def _decision_row(r):
    basis = r.get("basis") if isinstance(r.get("basis"), dict) else {}
    args = r.get("args") if isinstance(r.get("args"), dict) else {}
    res = r.get("res") if isinstance(r.get("res"), dict) else {}
    return {"seq": r.get("seq"), "t": r.get("t"), "ts": r.get("ts"), "what": r.get("what"), "kind": r.get("kind"), "role": basis.get("role"), "profile": basis.get("profile"),
            "plan": r.get("plan"), "sym": args.get("sym"), "decision": args.get("decision"), "tool": args.get("tool"), "reason": args.get("reason"),
            "code": res.get("code"), "ok": res.get("ok"), "text": r.get("why")}


def _recent_decisions(root):
    from tb.core import decisions
    rows = [r for r in decisions.last(DECISIONS_TAIL, root=root) if r.get("what") in ("brain_decision", "brain_why")]
    return [_decision_row(r) for r in reversed(rows[-DECISIONS_OUT:])]


def brain(root=None, now=None):
    now = time.time() if now is None else now
    hb = _read_json(paths.runtime("platform", "brain.hb.json", root=root))
    st = _read_json(paths.runtime("brain", "state.json", root=root))
    hb_age = None if not isinstance(hb.get("ts_epoch"), (int, float)) else max(0.0, now - hb["ts_epoch"])
    alive = hb_age is not None and hb_age < HB_STALE_S
    protect = bool(hb.get("protect_only")) if alive else bool((st.get("protect_only") or {}).get("on"))
    inflight = int(hb.get("inflight") or 0) if alive else 0
    if not alive:
        status = "down"
    elif protect:
        status = "protect"
    elif inflight > 0:
        status = "thinking"
    else:
        status = "idle"
    calls = [r for r in _tail_rows(paths.state("llm_calls.jsonl", root=root)) if r.get("kind") != "fmt" and isinstance(r.get("t"), (int, float))]
    last_by_role = {}
    for r in calls:
        if now - r["t"] <= RECENT_CALL_S and r.get("role"):
            last_by_role[r["role"]] = r
    roles = []
    for row in _roles(root, now):
        last = last_by_role.get(row.get("role"))
        roles.append({"role": row.get("role"), "primary": _model_of(row.get("primary")), "fallback": [_model_of(x) for x in row.get("fallback") or []],
                      "last": None if last is None else {"profile": last.get("profile"), "provider": last.get("provider"), "ok": last.get("ok"), "err": last.get("err"),
                                                          "lat_ms": last.get("lat_ms"), "t": last["t"], "fallback_from": last.get("fallback_from")},
                      "today": row.get("today"), "live_risk": row.get("live_risk")})
    latest = calls[-1] if calls else None
    active = None
    if latest is not None and now - latest["t"] <= RECENT_CALL_S:
        active = {"profile": latest.get("profile"), "provider": latest.get("provider"), "model": latest.get("model"), "role": latest.get("role"), "ok": latest.get("ok"),
                  "t": latest["t"]}
    return {"status": status, "alive": alive, "protect_reason": hb.get("reason") if protect else None,
            "health": hb.get("health"), "rev": hb.get("rev"), "inflight": inflight, "queue": hb.get("queue") or {}, "cooling": hb.get("cooling") or [],
            "budget": hb.get("budget"), "errors": hb.get("errors"), "active": active, "roles": roles, "decisions": _recent_decisions(root)}


TOPICS = {"brain": brain}


def build(topic, root=None, now=None):
    from tb.dash import model
    return model.scrub(TOPICS[topic](root, now))
