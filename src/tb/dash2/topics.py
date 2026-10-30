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


# ---------------------------------------------------------------- home: деньги, лимиты, позиции, платформа (зависит от режима paper|live|testnet|all)
MODES = ("paper", "live", "testnet", "all")
POSITIONS_OUT = 8


def default_mode(root=None):
    """Режим по умолчанию = режим, в котором сейчас работает платформа (state/control/platform.json); платформа остановлена или режим незнаком -> paper."""
    d = _read_json(paths.control_dir("platform.json", root=root))
    return d.get("mode") if d.get("state") == "running" and d.get("mode") in ("paper", "live", "testnet") else "paper"


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and x == x else None


def _first(*xs):
    """Первое число из списка (None, если чисел нет)."""
    for x in xs:
        n = _num(x)
        if n is not None:
            return n
    return None


def _position(vp, trade):
    tg = [{"price": _num(t.get("price")), "state": t.get("state")} for t in (vp.get("targets") or []) if _num(t.get("price")) is not None]
    calc = (trade or {}).get("calc") or {}
    return {"plan": vp.get("plan"), "sym": vp.get("sym"), "side": vp.get("side"), "mode": vp.get("mode"), "lev": _num(vp.get("lev")), "qty": _num(vp.get("qty")),
            "avg": _num(vp.get("avg")), "price": _num(vp.get("price")), "stale": bool(vp.get("price_stale")), "stop": _num(vp.get("cur_stop")), "stop0": _num(vp.get("initial_stop")),
            "targets": tg, "r": _first(calc.get("r_now"), vp.get("r_now")), "upnl": _first(calc.get("pnl"), vp.get("upnl")), "pnl_pct": _num(calc.get("pnl_pct_margin")),       # результат считает журнал сделок, как на старом дашборде: после комиссии входа, к реальной марже
            "risk": _num((trade or {}).get("risk_usdt")), "pnl_at_sl": _num(calc.get("pnl_at_sl")),
            "held_s": _num(vp.get("held_s")), "hold_left_s": _num(vp.get("hold_left_s")), "liq": _num(vp.get("liq"))}


def _trade_position(t):
    """Позиция из журнала сделок, если исполнитель плана её не показал (live без плана v2)."""
    c = t.get("calc") or {}
    return {"plan": t.get("plan_id"), "sym": t.get("sym"), "side": t.get("side"), "mode": t.get("mode"), "lev": _num(t.get("lev")), "qty": None, "avg": _num(t.get("entry")),
            "price": _num(c.get("price")), "stale": bool(c.get("price_stale")), "stop": _num(t.get("sl")), "stop0": _num(t.get("sl")), "targets": [{"price": _num(t.get("tp")), "state": "resting"}] if _num(t.get("tp")) else [],
            "r": _num(c.get("r_now")), "upnl": _num(c.get("pnl")), "pnl_pct": _num(c.get("pnl_pct_margin")), "risk": _num(t.get("risk_usdt")), "pnl_at_sl": _num(c.get("pnl_at_sl")), "held_s": _num(t.get("held_s")), "hold_left_s": None, "liq": _num(c.get("liq_est"))}


CURVE_POINTS = 300
TRADES_OUT = 150
PLANS_OUT = 5
_STATE_TTL = 0.0                # сервер ставит > 0: темы home и trades делят одну сборку состояния (около секунды работы) вместо двух
_state_cache = {}


def _state(root, mode, now):
    """Полное состояние старого дашборда для режима; при _STATE_TTL > 0 сборка повторно используется несколько секунд."""
    from tb.dash import model
    if _STATE_TTL <= 0:
        return model.build_state(root, mode, "all", now)
    key = (root, mode)
    hit = _state_cache.get(key)
    if hit and time.monotonic() - hit[0] < _STATE_TTL:
        return hit[1]
    s = model.build_state(root, mode, "all", now)
    _state_cache[key] = (time.monotonic(), s)
    return s


def _txt(x, n):
    return x[:n] if isinstance(x, str) and x else None


def _trade_card(t):
    p = t.get("path") if isinstance(t.get("path"), dict) else None
    return {"id": t.get("id"), "sym": t.get("sym"), "side": t.get("side"), "lev": _num(t.get("lev")), "mode": t.get("mode"), "venue": t.get("venue"), "stype": _txt(t.get("stype"), 16),
            "pnl": _num(t.get("pnl_usdt")), "pct": _num(t.get("pnl_pct_margin")), "move_pct": _num(t.get("move_pct")), "r": _num(t.get("r")),
            "entry": _num(t.get("entry")), "exit": _num(t.get("exit")), "reason": _txt(t.get("reason"), 16), "ts_open": _num(t.get("ts_open_epoch")), "ts_close": _num(t.get("ts_close_epoch")),
            "margin": _num(t.get("margin")), "fees": _num(t.get("fees_usdt")), "funding": _num(t.get("funding_usdt")), "risk": _num(t.get("risk_usdt")),
            "sl0": _num(t.get("sl0")), "sl": _num(t.get("sl")),
            "path": {k: _num(p.get(k)) for k in ("mae_pct", "mfe_pct", "mae_r", "mfe_r", "mfe_to_tp", "path_coverage")} if p else None,
            "err": [x[:40] for x in (t.get("err") or []) if isinstance(x, str)][:6], "rule_followed": _txt(t.get("rule_followed"), 12),
            "tags": [x[:30] for x in (t.get("tags") or []) if isinstance(x, str)][:8],
            "lesson": _txt(t.get("lesson"), 600), "thesis": _txt(t.get("thesis"), 600), "note": _txt(t.get("note"), 300),
            "plan_id": _txt(t.get("plan_id"), 40), "exp": _txt(t.get("exp"), 40), "var": _txt(t.get("var"), 40), "setup": _txt(t.get("setup"), 40), "hyp": _txt(t.get("hyp"), 40),
            "adds": len(t.get("adds") or []), "parts": len(t.get("parts") or []), "realized_parts": _num(t.get("realized_parts"))}


def trades(root=None, now=None, mode=None):
    """Закрытые сделки режима (новые первыми), причины выхода и теги ошибок из разбора. Кривая для графиков берётся из темы home."""
    now = time.time() if now is None else now
    mode = mode if mode in MODES else default_mode(root)
    s = _state(root, mode, now)
    bd = s.get("breakdown") or {}
    groups = lambda k: [{"name": g.get("name"), "n": g.get("n"), "pnl": _num(g.get("pnl"))} for g in bd.get(k) or [] if isinstance(g, dict)]
    errs = groups("err")
    return {"mode": mode, "total": s.get("history_total") or 0, "items": [_trade_card(t) for t in (s.get("history") or [])[:TRADES_OUT]],
            "reasons": [g for g in groups("reason") if g["name"] != "-"], "errs": [g for g in errs if g["name"] != "(без разбора)"],
            "unreviewed": sum(g["n"] or 0 for g in errs if g["name"] == "(без разбора)")}


PENDING_OUT = 6
DECISIONS_OUT = 120


def decisions(root=None, now=None, mode=None):
    """Лента решений режима (новые сверху): строки уже безопасные (model2.decision_row: тексты обрезаны, секретные ключи вырезаны). Записи без плана видны в любом режиме."""
    from tb.dash import model2
    mode = mode if mode in MODES else default_mode(root)
    items, _ = model2.read_plans(root)
    pm = {}
    for it in items:
        m = it["spec"].get("mode")
        pm[it["id"]] = m if m in ("live", "paper") else ("paper" if it["rt"].get("paper") else "live")
    want = {"paper": "paper", "live": "live", "testnet": "live"}.get(mode)
    rows = [model2.decision_row(r, pm) for r in model2.read_decisions(root)]
    rows = [d for d in rows if want is None or d["mode"] in (None, want)]
    rows.sort(key=lambda d: -d["seq"])
    return {"mode": mode, "total": len(rows), "items": rows[:DECISIONS_OUT], "plans": sorted({d["plan"] for d in rows if d["plan"]})[-60:], "day": model2.decisions_day(rows, time.time() if now is None else now)}


MODEL_DAYS = (1, 7, 30)


def models(root=None, now=None, mode=None):
    """Результат по моделям (сделки с тегом model.* и расход на вызовы) за 1, 7 и 30 суток: период клиент выбирает сам. Собирается редко (см. INTERVAL)."""
    from tb.llm import dashapi
    now = time.time() if now is None else now
    mode = mode if mode in MODES else default_mode(root)
    return {"mode": mode, "periods": {str(d): dashapi.models_perf(root, now, d, mode) for d in MODEL_DAYS}}


def _plans(v2, mode, now):
    """Планы в работе для режима: счётчики колонок доски и ближайшие к входу. Ожидание свечи отдаём моментом (eta_at, округлён до 5 с), а не остатком: остаток менялся бы каждую секунду и тема не молчала бы."""
    cards = [c for c in ((v2 or {}).get("plans") or {}).get("cards") or [] if isinstance(c, dict) and (mode == "all" or c.get("mode") == mode)]
    counts = {k: 0 for k in ("waiting", "watching", "in_trade", "closed")}
    for c in cards:
        if c.get("col") in counts:
            counts[c["col"]] += 1
    near = [c for c in cards if c.get("col") in ("waiting", "watching")]
    near.sort(key=lambda c: (_num((c.get("trigger") or {}).get("dist_pct")) is None, _num((c.get("trigger") or {}).get("dist_pct")) or 0.0))
    out = []
    for c in near[:PLANS_OUT]:
        t = c.get("trigger") or {}
        eta = _num(t.get("eta_s"))
        ttl = _num(c.get("ttl_left_s"))
        tgs = [x for x in (c.get("targets") or []) if isinstance(x, dict) and _num(x.get("price")) is not None]
        out.append({"id": c.get("id"), "sym": c.get("sym"), "side": c.get("side"), "mode": c.get("mode"), "col": c.get("col"), "text": t.get("text") or c.get("entry_text") or "",
                    "dist_pct": _num(t.get("dist_pct")), "eta_at": round((now + eta) / 5.0) * 5 if eta is not None else None, "entry_state": c.get("entry_state"),
                    "blocked_why": c.get("blocked_why"), "net_rr": _num(c.get("net_rr")), "risk": _num(c.get("risk_usdt")),
                    # что именно ждёт план: условия, что из них выполнено, уровень и сторона, на каком таймфрейме проверка, когда план истечёт (моментом, как eta_at)
                    "status": c.get("status"), "setup": c.get("setup"), "thesis": (c.get("thesis") or "")[:160], "entry_text": c.get("entry_text") or "",
                    "missing": [str(x)[:90] for x in (t.get("missing") or [])[:4]], "met_n": int(_num(t.get("met_n")) or 0), "leaf_n": int(_num(t.get("leaf_n")) or 0),
                    "seq": t.get("seq") if isinstance(t.get("seq"), dict) else None, "level": _num(t.get("level")), "price": _num(c.get("price")), "kind": t.get("kind"),
                    "dir": t.get("dir"), "beyond": t.get("beyond"), "tf": t.get("eta_tf"), "ttl_at": round((now + ttl) / 30.0) * 30 if ttl is not None else None,
                    "hold": c.get("hold"), "hold_reason": c.get("hold_reason"), "stop": _num(c.get("stop")), "target": _num(tgs[0]["price"]) if tgs else None, "targets": [_num(x["price"]) for x in tgs[:4]],
                    "leverage": _num(c.get("leverage")), "chase_bps": _num(c.get("chase_bps")), "chase_max_bps": _num(c.get("chase_max_bps"))})
    return {"counts": counts, "total": len(cards), "near": out}


def _pending(rows, now):
    """Ждущие лимитные входы; срок жизни отдаём моментом (expires_at), по той же причине, что и eta_at."""
    out = []
    for r in rows or []:
        left = _num(r.get("left_s"))
        out.append({"id": r.get("id"), "sym": r.get("sym"), "side": r.get("side"), "mode": r.get("mode"), "price": _num(r.get("price")), "cur": _num(r.get("cur")), "stale": bool(r.get("price_stale")),
                    "dist_pct": _num(r.get("dist_pct")), "sl": _num(r.get("sl")), "tp": _num(r.get("tp")), "risk": _num(r.get("risk_usdt")), "lev": _num(r.get("lev")),
                    "ttl_min": _num(r.get("ttl_min")), "expires_at": round(now + left) if left is not None else None})
    return {"total": len(out), "items": out[:PENDING_OUT]}


def _curve(points):
    """Накопленный результат закрытых сделок режима: [[ts, накопленный, результат сделки, монета]]; считаются все сделки журнала, как на старом дашборде.
    Длинную историю обрезаем с хвоста, базу (сумму отрезанного) отдаём отдельно, чтобы кривая начиналась с правильного уровня."""
    rows = [p for p in points or [] if isinstance(p, dict) and _num(p.get("ts")) is not None]
    cum, out = 0.0, []
    for p in rows:
        cum += _num(p.get("pnl")) or 0.0
        out.append([p["ts"], round(cum, 4), _num(p.get("pnl")) or 0.0, p.get("sym")])
    cut = max(0, len(out) - CURVE_POINTS)
    base = out[cut - 1][1] if cut else 0.0
    return {"base": base, "points": out[cut:], "total": len(out)}


def home(root=None, now=None, mode=None):
    now = time.time() if now is None else now
    mode = mode if mode in MODES else default_mode(root)
    s = _state(root, mode, now)
    plat = _read_json(paths.control_dir("platform.json", root=root))
    rr = s.get("risk_room") or {}
    rows = {r.get("id"): r for r in rr.get("rows") or [] if isinstance(r, dict)}
    daily = rows.get("daily") or {}
    start = ((s.get("summary") or {}).get("today") or {}).get("start") or 0
    today = [t for t in s.get("history") or [] if (t.get("ts_close_epoch") or 0) >= start]
    pnl_today = round(sum(_num(t.get("pnl_usdt")) or 0.0 for t in today), 4)
    trades = {t.get("id"): t for t in s.get("open_trades") or []}
    vps = (s.get("v2") or {}).get("positions") or []
    pos, seen = [], set()
    for vp in vps:
        if mode in ("all", vp.get("mode")):
            pos.append(_position(vp, trades.get(vp.get("journal_id"))))
            seen.add(vp.get("journal_id"))
    for t in s.get("open_trades") or []:
        if t.get("id") not in seen:
            pos.append(_trade_position(t))
    pos.sort(key=lambda p: -(p.get("risk") or 0.0))
    status = s.get("status") or {}
    strip = ((s.get("v2") or {}).get("strip") or {}).get("items") or []
    items = [{"id": i.get("id"), "label": i.get("label"), "level": i.get("level"), "value": i.get("value")}
             for i in list(strip) + list(status.get("items") or []) if i.get("id") in ("exec", "net", "scanner", "feed", "context", "session")]
    return {"mode": mode, "modes": list(MODES),
            "platform": {"state": plat.get("state"), "mode": plat.get("mode"), "trading": plat.get("trading"), "level": status.get("level"), "items": items},
            "money": {"under_risk": _num(daily.get("open_risk")), "day_room": _num(daily.get("room")), "day_limit": _num(daily.get("limit")), "pnl_today": pnl_today, "trades_today": len(today),
                       "open_pnl": _num(s.get("open_pnl")), "equity": _num((s.get("account") or {}).get("equity"))},
            "limits": [{"id": r.get("id"), "label": r.get("label"), "limit": _num(r.get("limit")), "used": _num(r.get("used")), "room": _num(r.get("room")), "unit": r.get("unit"), "level": r.get("level")}
                       for r in (rr.get("rows") or []) if isinstance(r, dict)],
            "limits_paper": bool(rr.get("paper")), "allowed": rr.get("allowed"), "positions": pos[:POSITIONS_OUT], "positions_total": len(pos),
            "curve": _curve(s.get("curve")), "plans": _plans(s.get("v2"), mode, now), "pending": _pending(s.get("pending"), now)}


# ---------------------------------------------------------------- events: лента для плавающего «облака» (шина событий, поля уже из белых списков)
EVENTS_OUT = 40


def events(root=None, now=None):
    from tb.core import bus
    rows = bus.read_tail(EVENTS_OUT, root=root)
    out = [{"seq": e.get("seq"), "ts": e.get("ts"), "type": e.get("type"), "pri": e.get("pri"), "plan": e.get("plan"), "sym": e.get("sym"), "d": e.get("d") or {}} for e in reversed(rows)]
    return {"last_seq": out[0]["seq"] if out else 0, "items": out}


def ctl(root=None, now=None):
    """Состояние управления для кнопки старта дня и модалки: платформа, шаг запуска, проверки H1-H8 (справка), KILL, уровень риска, шаги текущего дня.
    Без времён и возрастов (иначе тема менялась бы каждую секунду): `alive` и `stale` меняются только при смене состояния ctl."""
    from tb.control import auth, client
    now = time.time() if now is None else now
    st = client.read_status(root, now, float(client.load_cfg(root).get("status_stale_s", 15)))
    plat = st.get("platform") if isinstance(st.get("platform"), dict) else {}
    kill = st.get("kill") if isinstance(st.get("kill"), dict) else None
    risk = st.get("risk") if isinstance(st.get("risk"), dict) else {}
    step = st.get("step") if isinstance(st.get("step"), dict) else None
    banner = st.get("banner") if isinstance(st.get("banner"), dict) else None
    day = None
    try:
        from tb.lifecycle import days
        d = days.current(root)
        if d:
            day = {"day_id": d.get("day_id"), "state": d.get("state"), "mode": d.get("mode"),
                   "steps": [{"name": s.get("name"), "phase": s.get("phase"), "ok": s.get("ok"), "code": s.get("code")} for s in (d.get("steps") or []) if isinstance(s, dict)][:24]}
    except Exception:
        day = None
    return {"alive": bool((st.get("ctl") or {}).get("alive")) and not st.get("stale"), "configured": bool(auth.status().get("key")),
            "platform": {k: plat.get(k) for k in ("state", "mode", "session", "health", "day_id", "trading", "observe")}, "step": ({k: step.get(k) for k in ("name", "i", "n")} if step else None),
            "kill": ({"flatten": bool(kill.get("flatten")), "reason": str(kill.get("reason") or "")[:80]} if kill and kill.get("active") else None),
            "risk": {"level": risk.get("level"), "name": risk.get("name")}, "busy": bool(st.get("pending")),
            "checks": [{"id": c.get("id"), "name": str(c.get("name") or "")[:40], "sev": c.get("sev"), "ok": bool(c.get("ok")), "msg": str(c.get("msg") or "")[:160]}
                       for c in (st.get("checks") or []) if isinstance(c, dict)][:16],
            "banner": ({"level": banner.get("level"), "text": str(banner.get("text") or "")[:160]} if banner else None), "day": day}


TOPICS = {"brain": brain, "ctl": ctl, "events": events}
INTERVAL = {"decisions": 3.0, "models": 30.0}        # тема -> не чаще раза в N секунд (по умолчанию HEAVY_S сервера)
MODE_TOPICS = {"home": home, "trades": trades, "decisions": decisions, "models": models}        # зависят от режима клиента: строятся для каждого режима, который сейчас смотрит хотя бы один клиент


def build(topic, root=None, now=None, mode=None):
    from tb.dash import model
    if topic in MODE_TOPICS:
        return model.scrub(MODE_TOPICS[topic](root, now, mode))
    return model.scrub(TOPICS[topic](root, now))
