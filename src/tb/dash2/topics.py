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
PLANS_PAGE_OUT = 120
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
    return {"counts": counts, "total": len(cards), "near": [_plan_row(c, now) for c in near[:PLANS_OUT]]}


def _plan_row(c, now):
    """Строка плана для карточки: что именно ждёт план (условия, уровень, сторона, таймфрейм проверки), стоп и цели, срок (моментом, как eta_at) и итог закрытого плана."""
    t = c.get("trigger") or {}
    eta = _num(t.get("eta_s"))
    ttl = _num(c.get("ttl_left_s"))
    tgs = [x for x in (c.get("targets") or []) if isinstance(x, dict) and _num(x.get("price")) is not None]
    return {"id": c.get("id"), "sym": c.get("sym"), "side": c.get("side"), "mode": c.get("mode"), "col": c.get("col"), "text": t.get("text") or c.get("entry_text") or "",
            "dist_pct": _num(t.get("dist_pct")), "eta_at": round((now + eta) / 5.0) * 5 if eta is not None else None, "entry_state": c.get("entry_state"),
            "blocked_why": c.get("blocked_why"), "net_rr": _num(c.get("net_rr")), "risk": _num(c.get("risk_usdt")),
            "status": c.get("status"), "setup": c.get("setup"), "thesis": (c.get("thesis") or "")[:160], "entry_text": c.get("entry_text") or "",
            "missing": [str(x)[:90] for x in (t.get("missing") or [])[:4]], "met_n": int(_num(t.get("met_n")) or 0), "leaf_n": int(_num(t.get("leaf_n")) or 0),
            "seq": t.get("seq") if isinstance(t.get("seq"), dict) else None, "level": _num(t.get("level")), "price": _num(c.get("price")), "kind": t.get("kind"),
            "dir": t.get("dir"), "beyond": t.get("beyond"), "last_bar": t.get("last_bar") if isinstance(t.get("last_bar"), dict) else None, "tf": t.get("eta_tf"), "ttl_at": round((now + ttl) / 30.0) * 30 if ttl is not None else None,
            "hold": c.get("hold"), "hold_reason": c.get("hold_reason"), "stop": _num(c.get("stop")), "target": _num(tgs[0]["price"]) if tgs else None, "targets": [_num(x["price"]) for x in tgs[:4]],
            "leverage": _num(c.get("leverage")), "chase_bps": _num(c.get("chase_bps")), "chase_max_bps": _num(c.get("chase_max_bps")),
            "rev": c.get("rev"), "exp": c.get("exp"), "var": c.get("var"), "hyp": c.get("hyp"), "src": c.get("src"), "parent": c.get("parent"), "session_type": c.get("session_type"),
            "created_by": c.get("created_by"), "ts_created": c.get("ts_created"), "ts_armed": c.get("ts_armed"), "ts_entered": c.get("ts_entered"), "ts_closed": c.get("ts_closed"),
            "close_reason": c.get("close_reason"), "close_pnl": _num(c.get("close_pnl")), "warnings": list(c.get("warnings") or [])[:6]}


def plans(root=None, now=None, mode=None):
    """Страница «Планы»: все планы режима (ближайшие к входу первыми, затем в сделке, затем закрытые за сутки) и счётчики колонок."""
    now = time.time() if now is None else now
    mode = mode if mode in MODES else default_mode(root)
    v2 = _state(root, mode, now).get("v2")
    cards = [c for c in ((v2 or {}).get("plans") or {}).get("cards") or [] if isinstance(c, dict) and (mode == "all" or c.get("mode") == mode)]
    counts = {k: 0 for k in ("waiting", "watching", "in_trade", "closed")}
    for c in cards:
        if c.get("col") in counts:
            counts[c["col"]] += 1
    return {"mode": mode, "counts": counts, "total": len(cards), "items": [_plan_row(c, now) for c in cards[:PLANS_PAGE_OUT]]}


def ideas(root=None, now=None, mode=None):
    """Страница «Идеи»: идеи агента (ждут уровня, стали планом, не сработали), авто-история сканера и статистика исходов. Источник тот же, что у старой вкладки (model.ideas_info);
    возраст цены (меняется каждую секунду) не отдаём, срок идеи отдан моментом expires_epoch, поэтому тема молчит, пока не двинулась цена или не пришёл замер."""
    from tb.dash import model
    now = time.time() if now is None else now
    info = model.ideas_info(root, now)
    items = []
    for i in info.get("items") or []:
        r = {k: v for k, v in i.items() if k not in ("now_age_s", "now_src", "ttl_h")}
        r["dist_level_pct"] = _round(r.get("dist_level_pct"), 3)
        r["expires_epoch"] = round(r["expires_epoch"] / 30.0) * 30 if r.get("expires_epoch") is not None else None
        items.append(r)
    return {"mode": mode if mode in MODES else default_mode(root), "available": info.get("available"), "counts": info.get("counts"), "items": items, "stats": info.get("stats"), "auto_recent": info.get("auto_recent"),
            "pending": info.get("pending"), "costs": info.get("costs")}


def _at(now, age):
    """Момент обновления файла по его возрасту (возраст менялся бы каждую секунду, момент нет)."""
    a = _num(age)
    return round(now - a) if a is not None else None


def market(root=None, now=None):
    """Страница «Рынок»: тепловая карта (топ монет по обороту), ликвидации, ордерфлоу, горячие монеты сканера и отслеживаемые монеты фида. Источники те же, что у старой вкладки
    (model.deriv_info, hot_info, market_info). Возрасты отдаём моментами `at`, у стен стакана убран возраст: тема меняется только вместе с данными."""
    from tb.dash import model
    now = time.time() if now is None else now
    d = model.deriv_info(root, now)
    heat, liq, flow = d["heat"], d["liq"], d["flow"]
    hot = model.hot_info(root, now)
    mk = model.market_info(root, now)
    coins = [{k: v for k, v in r.items() if k != "age_s"} for r in model._market_rows(mk, None)]
    return {"heat": {"available": bool(heat.get("available")), "at": _at(now, heat.get("age_s")), "rows": heat.get("rows") or []},
            "liq": {"available": bool(liq.get("available")), "at": _at(now, liq.get("age_s")), "totals": liq.get("totals") or {}, "syms": liq.get("syms") or []},
            "flow": {"available": bool(flow.get("available")), "at": _at(now, flow.get("age_s")), "xex_ok": flow.get("xex_ok"),
                     "syms": [dict({k: v for k, v in s.items() if k not in ("age_s", "walls")}, walls=[{k: v for k, v in w.items() if k != "age_s"} for w in (s.get("walls") or []) if isinstance(w, dict)])
                              for s in flow.get("syms") or []]},
            "hot": {"available": bool(hot.get("available")), "at": _at(now, hot.get("age_s")), "warm": hot.get("warm"), "stats": hot.get("stats") or {}, "top": hot.get("top") or [], "blocked": hot.get("blocked") or []},
            "coins": {"ok": bool(mk.get("ok")), "at": _at(now, mk.get("age_s")), "rows": coins}}


def news(root=None, now=None):
    """Новости для ленты «Рынка»: очищенные заголовки с разметкой карантинного читателя, статус источников и календаря. Источник тот же, что у старой вкладки (intel.dashapi.news).
    Текст третьих лиц уходит как данные и вставляется без HTML. Возрасты не отдаём: метка времени заголовка и момент события календаря (`at`, до минуты) считаются на клиенте."""
    from tb.intel import dashapi
    now = time.time() if now is None else now
    j = dashapi.news(root, {"limit": ["60"]}, now)
    items = [{k: v for k, v in i.items() if k != "age_s"} for i in j.get("items") or []]
    cal = j.get("calendar") or {}
    nxt = [{"id": e.get("id"), "type": e.get("type"), "impact": e.get("impact"), "verified": e.get("verified"),
            "at": round((now + (_num(e.get("in_min")) or 0.0) * 60.0) / 60.0) * 60} for e in cal.get("next") or []]
    rd = j.get("reader") or {}
    return {"items": items, "sources": [{k: v for k, v in s.items() if k != "age_s"} for s in j.get("sources") or []], "degraded": j.get("degraded") or [],
            "calendar": {"next": nxt, "blackout": cal.get("blackout")},
            "reader": {"enabled": bool(rd.get("enabled")), "status": rd.get("status"), "stale": bool(rd.get("stale"))}}


def _round(x, n):
    return round(x, n) if isinstance(x, (int, float)) and not isinstance(x, bool) else x


def _moment(now, age, step=15):
    """Момент события по возрасту, округлённый до step секунд: возраст менялся бы каждую секунду, момент нет."""
    a = _num(age)
    return int(round((now - a) / step) * step) if a is not None else None


_LEVEL_RANK = {"ok": 0, "off": 0, "info": 0, "warn": 1, "bad": 2}


def system(root=None, now=None):
    """Страница «Система · Управление»: общий вывод, узлы платформы, сессия, запас риска, капитал, источники данных, исполнитель и очередь, отказы дня, история сессий.
    Источник тот же, что у старой вкладки «Система» (model.build_state). Тексты узлов с «N с назад» не отдаём: только уровень, статический текст и момент (`at`), подпись строит клиент."""
    now = time.time() if now is None else now
    mode = default_mode(root)
    s = _state(root, mode, now)
    v2 = s.get("v2") or {}
    strip = v2.get("strip") or {}
    ex = strip.get("exec") or {}
    bl = strip.get("backlog") or {}
    health = s.get("health") or {}
    feed, ctx, hot = health.get("feed") or {}, health.get("context") or {}, health.get("hot") or {}
    items = {i.get("id"): i for i in (strip.get("items") or []) + ((s.get("status") or {}).get("items") or []) if isinstance(i, dict)}

    def lv(*ids):
        return max((items[i].get("level") for i in ids if i in items and items[i].get("level") in _LEVEL_RANK), key=lambda x: _LEVEL_RANK[x], default="off")
    sess = s.get("session")
    kill = strip.get("kill")
    nodes = [
        {"id": "exec", "label": "Исполнитель", "level": lv("exec"), "text": " · ".join(str(x) for x in (ex.get("env"), "pid %s" % ex["pid"] if ex.get("pid") else None, ex.get("version")) if x) if ex.get("present") else "не запущен",
         "since": _moment(now, ex.get("uptime_s"), 30) if ex.get("present") else None},
        {"id": "net", "label": "Связь с биржей", "level": "ok" if lv("net") == "warn" and ex.get("env") == "paper" and "private нет" in str((items.get("net") or {}).get("text")) else lv("net"),       # paper: приватного канала нет по построению, это не предупреждение
         "text": (items.get("net") or {}).get("text")},
        {"id": "recon", "label": "Сверка с биржей", "level": lv("recon"), "at": _moment(now, ex.get("reconcile_age_s"))},
        {"id": "events", "label": "Шина событий", "level": lv("events"), "at": _moment(now, bl.get("last_age_s"), 30), "text": "не подтверждено wake: %s" % sum(int((c or {}).get("unacked") or 0) for c in (bl.get("consumers") or {}).values())},
        {"id": "scan", "label": "Сканер монет", "level": lv("scan", "scanner"), "at": _moment(now, hot.get("age_s"))},
        {"id": "feed", "label": "Фид цен", "level": lv("feed"), "at": _moment(now, feed.get("age_s")), "text": None if feed.get("running", True) else "остановлен"},
        {"id": "context", "label": "Контекст рынка", "level": lv("context"), "at": _moment(now, ctx.get("age_s"), 60),
         "text": ("risk_off: " + ", ".join(str(x) for x in (ctx.get("risk_off_reasons") or [])[:3])) if ctx.get("risk_off") else "risk_off нет"},
        {"id": "session", "label": "Сессия", "level": lv("session"), "text": (items.get("session") or {}).get("text")},
    ]
    levels = [n["level"] for n in nodes] + [w.get("level") for w in (s.get("warnings") or []) if isinstance(w, dict)] + (["bad"] if isinstance(kill, dict) and kill.get("active", True) else [])
    overall = max((l for l in levels if l in _LEVEL_RANK), key=lambda x: _LEVEL_RANK[x], default="ok")
    sessions = [{k: r.get(k) for k in ("id", "type", "ts", "stopped", "stop_note")} | {"thesis": _txt(r.get("thesis"), 160)} for r in (s.get("sessions") or [])[:8] if isinstance(r, dict)]
    rr = s.get("risk_room") or {}
    acc = s.get("account") or {}
    return {
        "mode": mode, "level": overall, "kill": {"flatten": bool(kill.get("flatten")), "reason": _txt(kill.get("reason"), 80)} if isinstance(kill, dict) and kill.get("active", True) else None,
        "nodes": nodes, "warnings": [{"level": w.get("level"), "text": _txt(w.get("text"), 200)} for w in (s.get("warnings") or [])[:6] if isinstance(w, dict)],
        "session": ({k: sess.get(k) for k in ("id", "type", "budget_usdt", "budget_used", "trades", "max_trades", "entries_allowed", "block_reasons", "realized", "open_risk", "perf_mult", "perf_n", "start")}
                    | {"thesis": _txt(sess.get("thesis"), 200), "expires_at": int(round((now + sess["left_s"]) / 30.0) * 30) if _num(sess.get("left_s")) is not None else None}) if isinstance(sess, dict) else None,
        "room": {"allowed": rr.get("allowed"), "reasons": [str(x)[:80] for x in (rr.get("reasons") or [])[:4]],
                 "rows": [{k: r.get(k) for k in ("id", "label", "limit", "used", "room", "frac", "level", "unit", "help")} for r in (rr.get("rows") or []) if isinstance(r, dict)][:8]},
        "account": {"equity": _num(acc.get("equity")), "hwm": _num(acc.get("hwm")), "drawdown": _num(acc.get("drawdown")), "ladder_mult": _num(acc.get("ladder_mult")),
                    "venues": {k: {"equity": _num(v.get("equity")), "free": _num(v.get("free"))} for k, v in (acc.get("venues") or {}).items() if isinstance(v, dict)}},
        "sources": {"feed": [{"id": k, "state": v.get("state"), "connected": v.get("connected"), "reconnects": _num(v.get("reconnects")), "errors": _num(v.get("errors")), "error": _txt(v.get("last_error"), 120)}
                             for k, v in (feed.get("sources") or {}).items() if isinstance(v, dict)],
                    "context": {k: bool(v) for k, v in (ctx.get("sources_ok") or {}).items()}, "degraded": [str(x)[:40] for x in (ctx.get("degraded_sources") or [])[:8]]},
        "exec": {"present": bool(ex.get("present")), "plans": ex.get("plans") or {}, "positions": _num(ex.get("positions")), "lag_ms": round((_num(ex.get("loop_lag_ms")) or 0) / 5.0) * 5, "hold": ex.get("hold"),
                 "consumers": {k: {"acked": _num(c.get("acked")), "unacked": _num(c.get("unacked"))} for k, c in (bl.get("consumers") or {}).items() if isinstance(c, dict)}},
        "rejects": {"n": _num((v2.get("decisions_day") or {}).get("n")), "rejects": _num((v2.get("decisions_day") or {}).get("rejects")),
                    "codes": [{"code": str(c.get("code"))[:32], "n": _num(c.get("n"))} for c in ((v2.get("decisions_day") or {}).get("reject_codes") or [])[:8] if isinstance(c, dict)]},
        "sessions": sessions,
    }


def _model(m):
    """Модель роли для страницы: без служебных полей (ключей в данных нет, провайдер и модель только как имена)."""
    if not isinstance(m, dict):
        return None
    p = m.get("params") if isinstance(m.get("params"), dict) else {}
    return {"id": str(m.get("id") or "")[:40], "provider": str(m.get("provider") or "")[:40], "model_name": str(m.get("model_name") or "")[:80], "provider_enabled": bool(m.get("provider_enabled")),
            "live_ok": bool(m.get("live_ok")), "params": {"temperature": _num(p.get("temperature")), "max_out_tok": _num(p.get("max_out_tok")), "thinking": p.get("thinking") if isinstance(p.get("thinking"), str) else None}}


def settings(root=None, now=None):
    """Страница «Система · Настройки», блок «Кто думает»: роли мозга, их основная и запасные модели, параметры, расход за сутки и качество ответов по роли. Только чтение: источник
    llm.dashapi (`/api/settings/*`), запись идёт через ctl-действия и в этом дашборде пока недоступна."""
    from tb.llm import dashapi
    now = time.time() if now is None else now

    def get(path, query=None):
        r = dashapi.api("/api/settings/" + path, query or {}, root, now)
        return r[1] if r and r[0] == 200 and isinstance(r[1], dict) else {}
    ov, rl = get("overview"), get("roles")
    pv, ex, rv = get("providers"), get("exchanges"), get("revisions")
    day = {g.get("id"): g for g in (get("usage", {"days": ["1"], "by": ["role"]}).get("groups") or []) if isinstance(g, dict)}
    roles = []
    for r in rl.get("roles") or []:
        if not isinstance(r, dict):
            continue
        t, d = r.get("today") or {}, day.get(r.get("role")) or {}
        roles.append({"role": str(r.get("role"))[:40], "scope": r.get("scope"), "team": r.get("team"), "live_risk": bool(r.get("live_risk")), "io": r.get("io"),
                      "primary": _model(r.get("primary")), "fallback": [m for m in (_model(x) for x in (r.get("fallback") or [])) if m],
                      "budget_day_usd": _num((r.get("budget") or {}).get("day_usd")),
                      "today": {"usd": _num(t.get("usd")), "calls": _num(t.get("calls")), "errors": _num(t.get("errors")), "budget_left_usd": _num(t.get("budget_left_usd"))},
                      "day": {"calls": _num(d.get("calls")), "err_rate": _num(d.get("err_rate")), "fmt_err_rate": _num(d.get("fmt_err_rate")), "fallbacks": _num(d.get("fallbacks")),
                              "p50_ms": round((_num(d.get("lat_p50_ms")) or 0) / 100.0) * 100 if _num(d.get("lat_p50_ms")) is not None else None, "usd": _num(d.get("usd"))}})
    providers = []
    for p in pv.get("providers") or []:
        if not isinstance(p, dict):
            continue
        cred, lim = p.get("cred") if isinstance(p.get("cred"), dict) else {}, p.get("limits") if isinstance(p.get("limits"), dict) else {}
        providers.append({"id": str(p.get("id"))[:40], "name": str(p.get("name") or p.get("id"))[:60], "adapter": str(p.get("adapter") or "")[:40], "enabled": bool(p.get("enabled")), "local": bool(p.get("local")),
                          "verified": bool(p.get("verified")), "note": _txt(p.get("note"), 200), "error": _txt(p.get("error"), 160), "base_host": _host(p.get("base_url")),
                          "cred": {"needed": bool(cred.get("needed")), "present": bool(cred.get("present")), "perms_ok": cred.get("perms_ok") if isinstance(cred.get("perms_ok"), bool) else None, "env": _txt(cred.get("env"), 40)},       # ни ключа, ни маски: только «есть или нет»
                          "limits": {k: _num(lim.get(k)) for k in ("concurrent", "rpm", "day_usd", "hour_usd", "day_toks")},
                          "models": [{"id": str(m.get("id"))[:30], "name": str(m.get("name"))[:60], "ctx": _num(m.get("ctx")), "tools": bool(m.get("tools")), "tags": [str(t)[:20] for t in (m.get("tags") or [])[:4]],
                                      "price": {k: _num((m.get("price") or {}).get(k)) for k in ("in_miss", "in_hit", "out")} if isinstance(m.get("price"), dict) else None}
                                     for m in (p.get("models") or []) if isinstance(m, dict)][:12],
                          "profiles": [str(x)[:30] for x in (p.get("profiles") or [])[:12]]})
    exchanges = [{"id": str(e.get("id"))[:30], "name": str(e.get("name") or e.get("id"))[:60], "enabled": bool(e.get("enabled")), "mode": e.get("mode"), "implemented": bool(e.get("implemented")), "active": bool(e.get("active")),
                  "allowed": bool(e.get("allowed")), "note": _txt(e.get("note"), 160), "alive": bool((e.get("exec") or {}).get("alive")), "creds": sorted(str(k)[:20] for k in (e.get("cred_files") or {}))[:4],
                  "max_open_positions": _num((e.get("limits") or {}).get("max_open_positions"))} for e in (ex.get("exchanges") or []) if isinstance(e, dict)]
    swaps = [{k: s.get(k) for k in ("seq", "ts", "rev", "prev_rev", "kind", "tier", "by")} | {"target": [str(x)[:40] for x in (s.get("target") or [])[:6]], "from": str(s.get("from"))[:80], "to": str(s.get("to"))[:80]}
             for s in reversed((rv.get("swaps") or [])[-30:]) if isinstance(s, dict)]
    return {"mode": ov.get("mode"), "rev": ov.get("rev"), "live_team": ov.get("live_team"), "exchange": ov.get("exchange"), "providers": ov.get("providers"),
            "usage_today": ov.get("usage_today"), "errors": [str(e)[:120] for e in ((ov.get("errors") or []) + (rl.get("errors") or []))[:6]], "roles": roles,
            "provider_list": providers, "exchange_list": exchanges, "swaps": swaps, "revisions": len(rv.get("revisions") or [])}


def _host(url):
    """Только хост из адреса провайдера (без пути и параметров)."""
    from urllib.parse import urlparse
    try:
        h = urlparse(str(url or "")).hostname
    except ValueError:
        h = None
    return h[:80] if h else None


USAGE_DAYS = (1, 7, 30)
USAGE_BY = ("model", "provider", "role")
_USAGE_CACHE = {}           # (корень, дни, разрез) -> (когда, ответ): расход читает журнал вызовов, держим результат несколько секунд


def usage_detail(root, days, by, now=None):
    """Расход LLM по запросу клиента за days суток в разрезе by (model|provider|role): итог, строки групп, лимиты бюджета. -> {"days","by","ok","data"|"error"}."""
    from tb.llm import dashapi
    now = time.time() if now is None else now
    if days not in USAGE_DAYS or by not in USAGE_BY:
        return {"days": days if isinstance(days, int) else None, "by": by if isinstance(by, str) else None, "ok": False, "error": "bad query"}
    key = (root, days, by)
    hit = _USAGE_CACHE.get(key)
    if hit and now - hit[0] < 10.0:
        return hit[1]
    r = dashapi.api("/api/settings/usage", {"days": [str(days)], "by": [by]}, root, now)
    if not r or r[0] != 200 or not isinstance(r[1], dict):
        return {"days": days, "by": by, "ok": False, "error": "no data"}
    d = r[1]
    keep = ("calls", "in", "in_hit", "out", "reasoning", "usd", "errors", "fmt_errors", "fallbacks", "lat_p50_ms", "lat_p95_ms", "err_rate", "fmt_err_rate", "cache_hit_rate")
    out = {"days": days, "by": by, "ok": True, "data": {
        "total": d.get("total"), "today": d.get("today"),
        "groups": [{"id": str(g.get("id"))[:40]} | {k: _num(g.get(k)) for k in keep} for g in (d.get("groups") or []) if isinstance(g, dict)][:20],
        "budget": [{k: b.get(k) for k in ("state", "scope", "cred", "used", "cap", "until_ts")} for b in ((d.get("budget") or {}).get("items") or []) if isinstance(b, dict)][:8]}}
    if len(_USAGE_CACHE) > 20:
        _USAGE_CACHE.clear()
    _USAGE_CACHE[key] = (now, out)
    return out


BRIEFS_OUT = 60
_BRIEF_META = {}            # (корень, id) -> краткая сводка досье: файл досье после записи не меняется, поэтому читаем один раз


def briefs(root=None, now=None):
    """Лента досье: последние BRIEFS_OUT (id вида B-ГГГГММДД-ЧЧММ-NN) с краткой сводкой (время, вид, обрезано ли, разрешены ли входы, число позиций и планов). Содержимое отдаёт brief_detail."""
    from tb.dash import model, model2
    ids = model2.brief_list(root)
    items = []
    for bid in ids[:BRIEFS_OUT]:
        key = (root, bid)
        m = _BRIEF_META.get(key)
        if m is None:
            b = model.read_json(os.path.join(paths.briefs(root=root), bid + ".json"))
            if not isinstance(b, dict):
                continue                                      # файл ещё пишется: в следующий проход
            room = b.get("room") if isinstance(b.get("room"), dict) else {}
            m = {"id": bid, "ts": b.get("ts") if isinstance(b.get("ts"), str) else None, "scope": b.get("scope") if isinstance(b.get("scope"), str) else None, "trunc": bool(b.get("trunc")),
                 "allowed": room.get("allowed") if isinstance(room.get("allowed"), bool) else None,
                 "positions": len(b["positions"]) if isinstance(b.get("positions"), list) else 0, "plans": len(b["plans"]) if isinstance(b.get("plans"), list) else 0}
            if len(_BRIEF_META) > 400:
                _BRIEF_META.clear()
            _BRIEF_META[key] = m
        items.append(m)
    return {"total": len(ids), "items": items}


def brief_detail(root, bid, now=None):
    """Одно досье по запросу клиента (белый список полей и очистка строк в model2.brief_get): -> {"id","ok","data"|"error"}."""
    from tb.dash import model, model2
    code, data = model2.brief_get(root, bid if isinstance(bid, str) else "")
    if code != 200 or not (data or {}).get("available"):
        return {"id": bid if isinstance(bid, str) else None, "ok": False, "error": str((data or {}).get("error") or ("not found" if (data or {}).get("missing") else "no briefs"))[:60]}
    return {"id": data["id"], "ok": True, "data": model.scrub(data["brief"])}


def plan_detail(root, pid, now=None):
    """Подробности одного плана по запросу клиента (ревизии с различиями, решения, события, spec и runtime): -> {"id","ok","data"|"error"}."""
    from tb.dash import model, model2
    code, data = model2.plan_detail(root, pid, now)
    if code != 200:
        return {"id": pid if isinstance(pid, str) else None, "ok": False, "error": str((data or {}).get("error") or "error")[:60]}
    return {"id": pid, "ok": True, "data": model.scrub(data)}


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
            "checks": [{"id": c.get("id"), "name": str(c.get("name") or "")[:90], "sev": c.get("sev"), "ok": bool(c.get("ok")), "msg": str(c.get("msg") or "")[:160]}
                       for c in (st.get("checks") or []) if isinstance(c, dict)][:16],
            "banner": ({"level": banner.get("level"), "text": str(banner.get("text") or "")[:160]} if banner else None), "day": day,
            "last": [{"action": str(r.get("action") or "")[:32], "code": str(r.get("code") or "")[:32], "status": str(r.get("status") or "")[:16], "msg": str(r.get("msg") or "")[:120]}
                     for r in (st.get("last") or [])[:8] if isinstance(r, dict)]}


TOPICS = {"brain": brain, "ctl": ctl, "events": events, "market": market, "news": news, "briefs": briefs, "system": system, "settings": settings}
INTERVAL = {"decisions": 3.0, "models": 30.0, "ideas": 10.0, "market": 5.0, "news": 15.0, "briefs": 10.0, "system": 5.0, "settings": 15.0}        # тема -> не чаще раза в N секунд (по умолчанию HEAVY_S сервера)
MODE_TOPICS = {"home": home, "trades": trades, "decisions": decisions, "models": models, "plans": plans, "ideas": ideas}        # зависят от режима клиента: строятся для каждого режима, который сейчас смотрит хотя бы один клиент


def build(topic, root=None, now=None, mode=None):
    from tb.dash import model
    if topic in MODE_TOPICS:
        return model.scrub(MODE_TOPICS[topic](root, now, mode))
    return model.scrub(TOPICS[topic](root, now))
