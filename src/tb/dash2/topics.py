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


def _parts_pnl(open_trades, start):
    """Частичные закрытия открытых сделок (model: t.parts {ts, pnl, fee}) -> (за сегодня с start, всего, число сегодня), за вычетом комиссий частей.
    Как на бирже: каждое частичное закрытие уже реализованный результат и отдельная завершённая сделка."""
    from tb.dash import model
    today = total = 0.0
    n = 0
    for t in open_trades or []:
        for x in t.get("parts") or []:
            v = (_num(x.get("pnl")) or 0.0) - (_num(x.get("fee")) or 0.0)
            total += v
            ts = x.get("ts")
            te = ts if isinstance(ts, (int, float)) else model.parse_ts(ts)
            if te is None or te >= start:
                today += v
                n += 1
    return today, total, n


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
    nxt = [{"id": _txt(e.get("id"), 40), "type": _txt(e.get("type"), 40), "impact": _num(e.get("impact")), "verified": bool(e.get("verified")),
            "at": round((now + (_num(e.get("in_min")) or 0.0) * 60.0) / 60.0) * 60} for e in (cal.get("next") or [])[:6] if isinstance(e, dict)]
    bo = cal.get("blackout") if isinstance(cal.get("blackout"), dict) else None
    blackout = None if bo is None else {"name": _txt(bo.get("name"), 60), "from": _num(bo.get("from")), "to": _num(bo.get("to")), "verified": bo.get("v") is not False}
    rd = j.get("reader") or {}
    st = rd.get("stats") if isinstance(rd.get("stats"), dict) else {}
    lim = rd.get("limits") if isinstance(rd.get("limits"), dict) else {}
    status = rd.get("status") if rd.get("status") in _READER_STATUSES else None
    srcs = [{"id": _txt(s.get("id"), 40), "ok": bool(s.get("ok")), "degraded": bool(s.get("degraded")), "verified": bool(s.get("verified")), "fails": _num(s.get("fails")),
             "at": _moment(now, s.get("age_s"), 60)} for s in (j.get("sources") or [])[:24] if isinstance(s, dict)]
    return {"items": items, "sources": srcs, "degraded": [_txt(d, 40) for d in (j.get("degraded") or [])[:24] if _txt(d, 40)],
            "calendar": {"next": nxt, "blackout": blackout},
            "reader": {"enabled": bool(rd.get("enabled")), "present": bool(rd.get("present")), "status": status, "stale": bool(rd.get("stale")),
                       "profile": _txt(rd.get("profile"), 60), "calls_h": _num(st.get("calls_h")), "max_calls_h": _num(lim.get("calls_h")),
                       "usd_h": _round(_num(st.get("usd_h")), 4), "usd_day": _round(_num(st.get("usd_day")), 4), "max_usd_day": _num(lim.get("usd_day")),
                       "pending": _num(st.get("pending")), "ok_n": _num(st.get("ok_n")), "fail_n": _num(st.get("fail_n")), "susp_n": _num(st.get("susp_n")),
                       "err": _txt(st.get("err"), 80), "pause_until": _num(st.get("pause_until")), "last_ok": _num(st.get("last_ok")), "at": _moment(now, rd.get("age_s"), 60)}}


_READER_STATUSES = ("ok", "idle", "off", "limit", "paused", "llm_down")


def _txt(x, n):
    """Короткий текст: только строка, без управляющих символов, не длиннее n. Любое другое значение -> None."""
    if not isinstance(x, str):
        return None
    s = "".join(c for c in x if c >= " " and c != "\x7f").strip()
    return s[:n] if s else None


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
    paper = bool((s.get("risk_room") or {}).get("paper"))
    warns = [w for w in (s.get("warnings") or []) if isinstance(w, dict)]
    if paper:        # guard._evaluate_paper не применяет сессию, слоты, дневной лимит и серию стопов: «входы запрещены» по ним на бумаге справочные, не замечание
        warns = [w for w in warns if not str(w.get("text") or "").startswith("Сессия: входы запрещены")]
        for n in nodes:
            if n["id"] == "session" and str(n.get("text") or "").startswith("входы запрещены"):
                n["level"] = "ok"
    levels = [n["level"] for n in nodes] + [w.get("level") for w in warns] + (["bad"] if isinstance(kill, dict) and kill.get("active", True) else [])
    overall = max((l for l in levels if l in _LEVEL_RANK), key=lambda x: _LEVEL_RANK[x], default="ok")
    sessions = [{k: r.get(k) for k in ("id", "type", "ts", "stopped", "stop_note")} | {"thesis": _txt(r.get("thesis"), 160)} for r in (s.get("sessions") or [])[:8] if isinstance(r, dict)]
    rr = s.get("risk_room") or {}
    acc = s.get("account") or {}
    return {
        "mode": mode, "level": overall, "kill": {"flatten": bool(kill.get("flatten")), "reason": _txt(kill.get("reason"), 80)} if isinstance(kill, dict) and kill.get("active", True) else None,
        "nodes": nodes, "warnings": [{"level": w.get("level"), "text": _txt(w.get("text"), 200)} for w in warns[:6]],
        "session": ({k: sess.get(k) for k in ("id", "type", "budget_usdt", "budget_used", "trades", "max_trades", "entries_allowed", "block_reasons", "realized", "open_risk", "perf_mult", "perf_n", "start")}
                    | {"thesis": _txt(sess.get("thesis"), 200), "expires_at": int(round((now + sess["left_s"]) / 30.0) * 30) if _num(sess.get("left_s")) is not None else None}) if isinstance(sess, dict) else None,
        "room": {"allowed": rr.get("allowed"), "paper": paper, "reasons": [str(x)[:80] for x in (rr.get("reasons") or [])[:4]],
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
    pf = get("profiles")
    profiles = [{"id": str(p.get("id"))[:40], "provider": str(p.get("provider") or "")[:40], "model": str(p.get("model") or "")[:40], "model_name": str(p.get("model_name") or "")[:80],
                 "provider_enabled": bool(p.get("provider_enabled")), "live_ok": bool(p.get("live_ok")), "builtin": bool(p.get("builtin")), "used_by": [str(x)[:60] for x in (p.get("used_by") or [])[:12]],
                 "params": _profile_params(p.get("params"))}
                for p in (pf.get("profiles") or []) if isinstance(p, dict)]
    try:
        pr = get("presets")
    except Exception:                    # шаблоны не обязательны: без них тема остаётся рабочей
        pr = {}
    presets = [{"id": str(p.get("id"))[:30], "name": str(p.get("name") or p.get("id"))[:60], "adapter": str(p.get("adapter") or "")[:40], "base_host": _host(p.get("base_url")),
                "verified": p.get("verified") is True, "note": _txt(p.get("note"), 200), "models": [str(m)[:30] for m in (p.get("models") or [])[:12]], "added": bool(p.get("added"))}
               for p in (pr.get("presets") or []) if isinstance(p, dict) and p.get("id")]
    day = {g.get("id"): g for g in (get("usage", {"days": ["1"], "by": ["role"]}).get("groups") or []) if isinstance(g, dict)}
    roles = []
    for r in rl.get("roles") or []:
        if not isinstance(r, dict):
            continue
        t, d = r.get("today") or {}, day.get(r.get("role")) or {}
        name = str(r.get("role"))[:40]
        target = ("%s.%s" % (r.get("team"), name)) if r.get("scope") == "team" and r.get("team") else ("shared.%s" % name)       # так роль называется в действиях ctl (brain.role, brain.use); поле не «key»: model.scrub вырезает такие имена
        roles.append({"role": name, "target": target, "scope": r.get("scope"), "team": r.get("team"), "live_risk": bool(r.get("live_risk")), "io": r.get("io"),
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
                          "allow_private": bool(p.get("allow_private")),
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
            "usage_today": ov.get("usage_today"), "errors": _remarks(ov.get("errors"), rl.get("errors"), pv.get("errors"), pf.get("errors")), "roles": roles,
            "provider_list": providers, "exchange_list": exchanges, "swaps": swaps, "revisions": len(rv.get("revisions") or []), "profile_list": profiles, "preset_list": presets}


def _profile_params(p):
    """Параметры профиля для страницы: только три известных поля (max_tokens в теме называется max_out_tok)."""
    p = p if isinstance(p, dict) else {}
    return {"temperature": _num(p.get("temperature")), "max_out_tok": _num(_first(p.get("max_out_tok"), p.get("max_tokens"))), "thinking": p.get("thinking") if isinstance(p.get("thinking"), str) else None}


def _remarks(*groups):
    """Замечания к конфигурации (ошибки чтения конфигов мозга и провайдеров): уникальные строки, не больше 12."""
    out = []
    for g in groups:
        for e in g or []:
            s = str(e)[:160]
            if s and s not in out:
                out.append(s)
    return out[:12]


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


def _plan_orders(v2, mode, now):
    """Лимитные входы планов v2, уже стоящие на бирже (runtime.entry.state == resting): карточка плана даёт цену лимита (trigger.limit_px), стоп, тейк, риск и плечо.
    Срок заявки = срок плана (ttl_left_s), своего ttl у входа нет."""
    out = []
    for c in ((v2 or {}).get("plans") or {}).get("cards") or []:
        if not isinstance(c, dict) or c.get("entry_state") != "resting" or c.get("status") not in (None, "armed"):
            continue
        if mode != "all" and c.get("mode") != mode:
            continue
        px = _num((c.get("trigger") or {}).get("limit_px"))
        cur = _num(c.get("price"))
        tgs = [_num(x.get("price")) for x in c.get("targets") or [] if isinstance(x, dict) and _num(x.get("price")) is not None]
        ttl = _num(c.get("ttl_left_s"))
        out.append({"id": "plan:%s" % c.get("id"), "src": "plan", "plan": c.get("id"), "sym": c.get("sym"), "side": c.get("side"), "mode": c.get("mode"), "price": px, "cur": cur,
                    "stale": bool(c.get("price_stale")), "dist_pct": (100.0 * (px / cur - 1.0)) if px and cur else None, "sl": _num(c.get("stop")), "tp": tgs[0] if tgs else None,
                    "risk": _num(c.get("risk_usdt")), "lev": _num(c.get("leverage")), "ttl_min": None, "expires_at": round(now + ttl) if ttl is not None else None})
    return out


def _pending(rows, now, v2=None, mode="all"):
    """Ждущие лимитные входы: заявки прежнего пути (state/pending_orders.json, src=cli) и входы планов v2 (src=plan); срок жизни отдаём моментом (expires_at), по той же причине, что и eta_at.
    Одна и та же заявка в обоих источниках (монета, сторона, цена, режим) показывается один раз, строкой плана."""
    out = []
    for r in rows or []:
        left = _num(r.get("left_s"))
        out.append({"id": r.get("id"), "src": "cli", "plan": None, "sym": r.get("sym"), "side": r.get("side"), "mode": r.get("mode"), "price": _num(r.get("price")), "cur": _num(r.get("cur")), "stale": bool(r.get("price_stale")),
                    "dist_pct": _num(r.get("dist_pct")), "sl": _num(r.get("sl")), "tp": _num(r.get("tp")), "risk": _num(r.get("risk_usdt")), "lev": _num(r.get("lev")),
                    "ttl_min": _num(r.get("ttl_min")), "expires_at": round(now + left) if left is not None else None})

    def key(o):
        return (o["sym"], o["side"], o["mode"], round(o["price"], 8) if o["price"] is not None else None)
    plans = _plan_orders(v2, mode, now)
    seen = {key(o) for o in plans}
    out = plans + [o for o in out if key(o) not in seen]
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
    parts_today, parts_all, parts_n = _parts_pnl(s.get("open_trades"), start)
    pnl_today = round(sum(_num(t.get("pnl_usdt")) or 0.0 for t in today) + parts_today, 4)      # закрытые сделки + частичные фиксации открытых позиций сегодня
    open_pnl = _num(s.get("open_pnl"))
    open_float = round(open_pnl - parts_all, 4) if open_pnl is not None else None             # только плавающая: зафиксированные части уже в «за день»
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
    if rr.get("paper"):      # на бумаге «входы запрещены» по лимитам сессии справочные (см. system)
        raw = {i.get("id"): i for i in list(strip) + list(status.get("items") or []) if isinstance(i, dict)}
        if str((raw.get("session") or {}).get("text") or "").startswith("входы запрещены"):
            for i in items:
                if i["id"] == "session":
                    i["level"] = "ok"
    return {"mode": mode, "modes": list(MODES),
            "platform": {"state": plat.get("state"), "mode": plat.get("mode"), "trading": plat.get("trading"), "level": status.get("level"), "items": items},
            "money": {"under_risk": _num(daily.get("open_risk")), "day_room": _num(daily.get("room")), "day_limit": _num(daily.get("limit")), "pnl_today": pnl_today, "trades_today": len(today) + parts_n,      # частичное закрытие = отдельная завершённая сделка (как closed PnL биржи)
                       "pnl_parts": round(parts_today, 4) if parts_today else None, "open_pnl": open_float, "equity": _num((s.get("account") or {}).get("equity"))},
            "limits": [{"id": r.get("id"), "label": r.get("label"), "limit": _num(r.get("limit")), "used": _num(r.get("used")), "room": _num(r.get("room")), "unit": r.get("unit"), "level": r.get("level")}
                       for r in (rr.get("rows") or []) if isinstance(r, dict)],
            "limits_paper": bool(rr.get("paper")), "allowed": rr.get("allowed"), "positions": pos[:POSITIONS_OUT], "positions_total": len(pos),
            "curve": _curve(s.get("curve")), "plans": _plans(s.get("v2"), mode, now), "pending": _pending(s.get("pending"), now, s.get("v2"), mode)}


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
            "clear_kill": (st.get("config") or {}).get("allow_clear_kill") is True,       # снятие KILL из дашборда включено флагом control.allow_clear_kill_from_dash (решение владельца)
            "live_start": (st.get("config") or {}).get("allow_live_start_dash") is True,    # запуск дня в live из нового дашборда включён выключателем control.allow_live_start_from_dash
            "checks": [{"id": c.get("id"), "name": str(c.get("name") or "")[:90], "sev": c.get("sev"), "ok": bool(c.get("ok")), "msg": str(c.get("msg") or "")[:160]}
                       for c in (st.get("checks") or []) if isinstance(c, dict)][:16],
            "banner": ({"level": banner.get("level"), "text": str(banner.get("text") or "")[:160]} if banner else None), "day": day,
            "last": [{"action": str(r.get("action") or "")[:32], "code": str(r.get("code") or "")[:32], "status": str(r.get("status") or "")[:16], "msg": str(r.get("msg") or "")[:120]}
                     for r in (st.get("last") or [])[:8] if isinstance(r, dict)]}


def notify(root=None, now=None):
    """Страница «Система · Уведомления»: те же данные, что `/api/notify/status` старого дашборда (tb.lifecycle.notifycfg.view), но по белому списку полей. Только чтение:
    запись идёт действием notify.set. Источник `file` (свои настройки владельца) или `config` (своих ещё нет, действует config/lifecycle.json). Секретов в данных нет."""
    from tb.lifecycle import notifycfg
    now = time.time() if now is None else now
    v = notifycfg.view(root, now)
    s = v.get("settings") if isinstance(v.get("settings"), dict) else {}
    q = s.get("quiet") if isinstance(s.get("quiet"), dict) else {}
    lim = s.get("limits") if isinstance(s.get("limits"), dict) else {}
    cats = s.get("categories") if isinstance(s.get("categories"), dict) else {}
    st = v.get("stats") if isinstance(v.get("stats"), dict) else {}
    last = v.get("sent_last") if isinstance(v.get("sent_last"), dict) else None
    return {"source": "file" if v.get("source") == "file" else "config", "engine": bool(v.get("engine")), "warn": _txt(v.get("warn"), 200),
            "settings": {"master": s.get("master") is True, "categories": {k: cats.get(k) is True for k in notifycfg.KEYS},
                         "quiet": {"enabled": q.get("enabled") is True, "from": str(q.get("from") or "")[:5], "to": str(q.get("to") or "")[:5], "tz": "utc" if q.get("tz") == "utc" else "local",
                                   "bypass": [k for k in (q.get("bypass") or []) if k in notifycfg.KEYS]},
                         "limits": {"per_hour": _num(lim.get("per_hour")), "same_kind_min": _num(lim.get("same_kind_min"))}},
            "categories": [{"id": str(c.get("id"))[:30], "label": str(c.get("label") or "")[:80], "on": bool(c.get("on")), "critical": bool(c.get("critical")), "bypass": bool(c.get("bypass"))}
                           for c in (v.get("categories") or []) if isinstance(c, dict)],
            "quiet_now": bool(v.get("quiet_now")), "held_n": int(_num(v.get("held_n")) or 0),
            "sent_last": {"cat": str(last.get("cat") or "")[:30]} if last and last.get("cat") else None,
            "stats": {k: int(_num(st.get(k)) or 0) for k in ("sent", "merged", "limited", "failed")}, "hint": _txt(v.get("hint"), 300)}


# ---------------------------------------------------------------- day: страница «Торговля · День» (состояние текущего дня, список дней, отчёт по запросу)
DAYS_OUT = 30
DAY_STEPS_OUT = 24
DAY_ROWS_OUT = 40


def _int(x):
    v = _num(x)
    return int(v) if v is not None else 0


def _strs(xs, n, m):
    """Список коротких строк (до n штук, каждая до m символов); не список или не строки: пусто."""
    return [x[:m] for x in xs if isinstance(x, str) and x][:n] if isinstance(xs, (list, tuple)) else []


def _blocking(detail):
    """Что именно не прошло при старте (проверки перед запуском): [{id, msg, fix}]; тексты наши (preflight), но обрезаются."""
    rows = detail.get("blocking") if isinstance(detail, dict) else None
    return [{"id": _txt(c.get("id"), 40), "msg": _txt(c.get("msg"), 160), "fix": _txt(c.get("action"), 160)} for c in rows[:4] if isinstance(c, dict)] if isinstance(rows, list) else []


def _day_abort(a):
    if not isinstance(a, dict):
        return None
    det = a.get("detail")
    why = _txt(det, 200) if isinstance(det, str) else (_txt(det.get("why"), 200) if isinstance(det, dict) else None)
    return {"step": _txt(a.get("step"), 4), "code": _txt(a.get("code"), 24), "why": why, "blocking": _blocking(det)}


def _day_step(s):
    det = s.get("detail")
    d = det if isinstance(det, dict) else {}
    return {"name": _txt(s.get("name"), 4), "phase": s.get("phase") if s.get("phase") in ("start", "stop") else None, "ok": s.get("ok") if isinstance(s.get("ok"), bool) else None,
            "dur_s": _num(s.get("dur_s")), "code": _txt(s.get("code"), 24), "amber": bool(d.get("amber")), "why": _txt(det, 160) if isinstance(det, str) else _txt(d.get("why"), 160),
            "blocking": _blocking(d), "gone": _strs(d.get("gone"), 6, 40), "waiting": _strs(d.get("not_closed") or d.get("waiting"), 6, 60), "llm": _txt(d.get("llm"), 20)}


def _day_brief(b):
    if not isinstance(b, dict):
        return None
    return {"day_id": _txt(b.get("day_id"), 24), "state": _txt(b.get("state"), 12), "mode": _txt(b.get("mode"), 10), "agent": _txt(b.get("agent"), 40), "started": _txt(b.get("started"), 24),
            "stopped": _txt(b.get("stopped"), 24), "stop_source": _txt(b.get("stop_source"), 16), "verdict": _txt(b.get("verdict"), 12), "net": _num(b.get("net")),
            "carried_n": _int(b.get("carried_n")), "questions_n": _int(b.get("questions_n")), "acked": bool(b.get("acked")), "report_ready": bool(b.get("report_ready")),
            "needs_resume": bool(b.get("needs_resume")), "abort": _day_abort(b.get("abort"))}


def _day_carry(c):
    return {"plan": _txt(c.get("plan"), 40), "sym": _txt(c.get("sym"), 20), "side": _txt(c.get("side"), 5), "stop": _num(c.get("stop")), "risk_usdt": _num(c.get("risk_usdt")),
            "why": _txt(c.get("why"), 240), "decided_by": _txt(c.get("decided_by"), 12), "alive": c.get("alive") if isinstance(c.get("alive"), bool) else None}


def _day_current(c, carry_in):
    out = _day_brief(c)
    cl = c.get("closing") if isinstance(c.get("closing"), dict) else {}
    fl = cl.get("failed") if isinstance(cl.get("failed"), dict) else None
    dp = c.get("dayplan") if isinstance(c.get("dayplan"), dict) else None
    sess = dp.get("session") if dp else None
    pf = c.get("preflight") if isinstance(c.get("preflight"), dict) else None
    out.update({
        "steps": [_day_step(s) for s in (c.get("steps") or []) if isinstance(s, dict)][:DAY_STEPS_OUT],
        "questions": [{"id": _txt(q.get("id"), 8), "plan": _txt(q.get("plan"), 40), "text": _txt(q.get("text"), 300), "options": _strs(q.get("options"), 4, 24),
                       "answer": _txt(q.get("answer"), 8), "answered_by": _txt(q.get("answered_by"), 12)} for q in (c.get("questions") or []) if isinstance(q, dict)][:12],
        "deadline_ts": _num(c.get("deadline_ts")),
        "closing": {"source": _txt(cl.get("source"), 16), "auto_reason": _txt(cl.get("auto_reason"), 40), "emergency": bool(cl.get("emergency")), "manual": _strs(cl.get("manual"), 6, 60),
                    "failed": {"items": _strs(fl.get("items"), 10, 60), "manual": _strs(fl.get("manual"), 6, 60), "reason": _txt(fl.get("reason"), 24)} if fl else None},
        "dayplan": {"source": _txt(dp.get("source"), 10), "regime": _txt(dp.get("regime"), 10), "bias": _txt(dp.get("bias"), 10), "max_new_plans": _num(dp.get("max_new_plans")),
                    "session": _txt(sess.get("type") if isinstance(sess, dict) else sess, 12)} if dp else None,
        "preflight": {"red": _strs(pf.get("red"), 8, 40), "amber": _strs(pf.get("amber"), 8, 40)} if pf else None,
        "checklist": [{"item": _txt(r.get("item"), 60), "kind": _txt(r.get("kind"), 8), "plan": _txt(r.get("plan"), 40), "sym": _txt(r.get("sym"), 20), "side": _txt(r.get("side"), 5),
                       "stype": _txt(r.get("stype"), 12), "rule": _txt(r.get("rule"), 4), "decision": _txt(r.get("decision"), 8), "codes": _strs(r.get("codes"), 6, 40)}
                      for r in (c.get("checklist") or []) if isinstance(r, dict)][:DAY_ROWS_OUT],
        "carry": [_day_carry(x) for x in (c.get("carry") or []) if isinstance(x, dict)][:12],
        "carry_in": [_day_carry(x) for x in (carry_in or []) if isinstance(x, dict)][:12]})
    return out


def day(root=None, now=None):
    """Страница «Торговля · День»: те же данные, что `/api/day/status|list` старого дашборда (tb.lifecycle.dashapi), по белому списку полей: без путей, ключей, фактуры дня и сырых деталей.
    Только чтение (кнопки идут действиями day.resume, carry.decide, report.ack). Без «сейчас» и возрастов: тема меняется только вместе с состоянием дня. -> {banner, current, last, days}."""
    from tb.lifecycle import dashapi, days
    st = dashapi.status(root, now)
    cur = st.get("current") if isinstance(st.get("current"), dict) else None
    b = st.get("banner") if isinstance(st.get("banner"), dict) else None
    carry_in = None
    if cur and cur.get("day_id"):
        full = days.load(root, cur["day_id"])
        carry_in = full.get("carry_in") if isinstance(full, dict) else None
    return {"banner": {"level": b.get("level") if b.get("level") in ("info", "warn", "alarm") else "info", "text": _txt(b.get("text"), 200), "day_report": _txt(b.get("day_report"), 24),
                       "day_id": _txt(b.get("day_id"), 24)} if b else None,
            "current": _day_current(cur, carry_in) if cur else None, "last": _day_brief(st.get("last")),
            "days": [x for x in (_day_brief(d) for d in dashapi.listing(root).get("days", [])[:DAYS_OUT]) if x]}


def day_report(root, day_id, now=None):
    """Итог одного дня по запросу клиента (чтение state/days/<id>/report.json и report.md, id проверяется days.valid_id): -> {"id","ok","data"|"error"}.
    Белый список полей отчёта; текст отчёта (markdown) уходит как текст, клиент выводит его без HTML."""
    from tb.dash import model
    from tb.lifecycle import dashapi, days
    did = day_id if isinstance(day_id, str) and days.valid_id(day_id) else None
    if did is None:
        return {"id": day_id[:40] if isinstance(day_id, str) else None, "ok": False, "error": "bad id"}
    r = dashapi.report(root, did)
    if not r:
        return {"id": did, "ok": False, "error": "no report"}
    rep = r.get("report") if isinstance(r.get("report"), dict) else {}
    p = rep.get("pnl") if isinstance(rep.get("pnl"), dict) else {}
    cost = rep.get("cost") if isinstance(rep.get("cost"), dict) else {}
    cf = rep.get("close_failed") if isinstance(rep.get("close_failed"), dict) else None
    data = {"day_id": did, "kind": _txt(rep.get("kind"), 12), "mode": _txt(rep.get("mode"), 10), "agent": _txt(rep.get("agent"), 40), "started": _txt(rep.get("started"), 24),
            "stopped": _txt(rep.get("stopped"), 24), "stop_source": _txt(rep.get("stop_source"), 16), "verdict": _txt(rep.get("verdict"), 12),
            "pnl": {k: _num(p.get(k)) for k in ("net", "fees", "funding", "realized_gross", "r_sum")}, "trades_n": _int(rep.get("trades_n")), "open_trades_n": _int(rep.get("open_trades_n")),
            "cost": {"usd": _num(cost.get("usd")), "calls": _int(cost.get("calls"))},
            "sessions": [{"id": _txt(s.get("id"), 40), "type": _txt(s.get("type"), 12), "pnl": _num(s.get("pnl")), "trades": _int(s.get("trades")), "budget_used": _num(s.get("budget_used"))}
                         for s in (rep.get("session") or []) if isinstance(s, dict)][:6],
            "carried": [_day_carry(x) for x in (rep.get("carried") or []) if isinstance(x, dict)][:12],
            "closed_eod": len(rep.get("closed_eod") or []) if isinstance(rep.get("closed_eod"), list) else 0,
            "open_questions": [{"id": _txt(q.get("id"), 8), "text": _txt(q.get("text"), 300)} for q in (rep.get("open_questions") or []) if isinstance(q, dict)][:12],
            "narrative": _txt(rep.get("narrative"), 600), "close_failed": {"items": _strs(cf.get("items"), 10, 60), "reason": _txt(cf.get("reason"), 24)} if cf else None,
            "md": (r.get("md") or "")[:40000] if isinstance(r.get("md"), str) else ""}
    return {"id": did, "ok": True, "data": model.scrub(data)}


EXP_BY = ("exp", "variant", "hyp", "setup", "stype", "symbol", "side", "reason", "err", "venue")
EXP_ROWS_OUT = 60
EXP_TRADES_OUT = 25
EXP_META_OUT = 40


def _exp_keys(t, by, err):
    """Значения группировки сделки в разрезе by (список: у сделки может быть несколько тегов ошибок). Нет значения -> [] (в группы не попадает)."""
    if by == "variant":
        e, v = t.get("exp"), t.get("var")
        k = ("%s/%s" % (e, v)) if e and v else (v or e)
    elif by == "err":
        return [x for x in (err or []) if isinstance(x, str) and x][:6]
    else:
        k = {"exp": t.get("exp"), "hyp": t.get("hyp"), "setup": t.get("setup"), "stype": t.get("stype"), "symbol": t.get("sym"), "side": t.get("side"),
             "reason": t.get("reason"), "venue": t.get("venue")}[by]
    return [str(k)[:60]] if k not in (None, "") else []


def _exp_confirmed(root):
    """Подтверждённые гипотезы ровно по правилу brief.experiments.confirmed (expstats.hyp_confirmed): семейство, на бумаге n >= 30, средний R > 0, p ниже alpha/K
    (K по семействам, Requirement 19.4). Не вердикт, а повод повторять приоритетно."""
    from tb.core import expstats
    by = {}
    for r in expstats.group_stats(expstats.load_trades("all", root, with_rules=False), by="hyp"):
        by.setdefault(r["key"], {})[r["mode"]] = r
    out = []
    for hyp, m in by.items():
        p = m.get("paper")
        if not expstats.hyp_confirmed(p):                   # общее правило expstats (семейства, K по Requirement 19.4, `other` не подтверждается)
            continue
        row = {"hyp": str(hyp)[:40], "n": p["n"], "avg_r": _num(p["avg_r"]), "win_rate": _num(p["win_rate"]), "p": _num(p["p"])}
        if m.get("live"):
            row["live_n"], row["live_avg_r"] = m["live"]["n"], _num(m["live"]["avg_r"])
        out.append(row)
    out.sort(key=lambda x: -(x["avg_r"] or 0))
    return out[:12]


def exp_detail(root, by, mode, now=None):
    """Эксперименты и разрезы по закрытым сделкам журнала по запросу клиента: те же группы и p-значения, что у `./tb exp stats` (tb.core.expstats), без вердиктов.
    n меньше min_n помечается `small` и без p; K = число групп запроса, порог alpha_adj = alpha / K (Бонферрони); для разреза hyp группы — семейства словаря
    (tb.core.hyps), K по общему правилу expstats.hyp_k (Requirement 19.4). Режимы не смешиваются (группа делится по режиму).
    Белый список полей, строки ограничены по длине, групп не больше EXP_ROWS_OUT, сделок в группе не больше EXP_TRADES_OUT. -> {"by","mode","ok":True,...}."""
    from tb.core import expstats
    from tb.dash import model
    by = by if by in EXP_BY else "exp"
    mode = mode if mode in MODES else default_mode(root)
    base = expstats.load_trades(mode, root, with_rules=False)
    extra = {t["id"]: t for t in model.load_trades(root)[0] if t.get("status") == "closed"}
    view, members, unreviewed = [], {}, 0
    for t in base:
        x = extra.get(str(t["id"])) or {}
        t["venue"] = x.get("venue") or "platform"
        t["ts"] = _num(x.get("ts_close_epoch"))
        keys = _exp_keys(t, by, x.get("err"))
        if by == "err" and not keys:
            unreviewed += 1
        for k in keys:
            v = dict(t)
            v["setup"] = k                                  # group_stats группирует по полю setup: подставляем в него ключ выбранного разреза
            view.append(v)
            members.setdefault((k, t["mode"]), []).append(t)
    if by == "hyp":
        # поле hyp уже семейство (expstats._norm); K и порог по общему правилу (Requirement 19.4), как в досье: по бумажным сделкам всего журнала,
        # а не только выбранного режима (иначе на live K был бы 1 и порог расходился бы с досье)
        rows = expstats.group_stats(view, by="hyp", min_n=expstats.MIN_N) if view else []
        k_total = expstats.hyp_k(expstats.load_trades("all", root, with_rules=False)) if rows else 0
        adj = expstats.bonferroni(k_total)
        for r in rows:
            r["k"], r["alpha_adj"] = k_total, adj
            if r["p"] is not None:
                r["below_alpha"] = r["key"] not in expstats.NO_CONFIRM_HYPS and r["p"] < adj
    else:
        rows = expstats.group_stats(view, by="setup", min_n=expstats.MIN_N) if view else []
        k_total = len(rows)
        adj = expstats.bonferroni(k_total)
    rows_total = len(rows)
    conf = _exp_confirmed(root)
    conf_set = {c["hyp"] for c in conf}
    rows.sort(key=lambda r: (-r["n"], str(r["key"])))
    meta_all = expstats.load_meta(root)
    out, meta = [], {}
    for r in rows[:EXP_ROWS_OUT]:
        ms = sorted(members.get((r["key"], r["mode"]), []), key=lambda t: -(t.get("ts") or 0.0))
        name = str(r["key"])
        if by in ("exp", "variant") and name.split("/")[0] in meta_all and len(meta) < EXP_META_OUT:
            m = meta_all[name.split("/")[0]]
            meta[name.split("/")[0]] = {"hypothesis": _txt(m.get("hypothesis"), 200), "prereg": _txt(m.get("prereg"), 280), "started": _txt(m.get("started"), 12),
                                        "min_n": _num(m.get("min_n"))}
        out.append({"name": name[:60], "mode": r["mode"], "n": r["n"], "wins": r["wins"], "win_rate": _num(r["win_rate"]), "avg_r": _num(r["avg_r"]), "se_r": _num(r["se_r"]),
                    "pf": _num(r["pf"]), "pf_inf": bool(r["pf_inf"]), "pnl": _num(r["pnl"]), "fees": _num(r["fees"]), "p": _num(r["p"]), "small": bool(r["small"]),
                    "below_alpha": bool(r["below_alpha"]), "confirmed": bool(by == "hyp" and r["mode"] == "paper" and name in conf_set), "last_ts": _num(ms[0].get("ts")) if ms else None,
                    "trades": [{"id": _txt(t.get("id"), 40), "sym": _txt(t.get("sym"), 20), "side": _txt(t.get("side"), 5), "mode": _txt(t.get("mode"), 8), "r": _num(t.get("r")),
                                "pnl": _num(t.get("pnl")), "ts": _num(t.get("ts")), "reason": _txt(t.get("reason"), 16)} for t in ms[:EXP_TRADES_OUT]]})
    return model.scrub({"ok": True, "by": by, "mode": mode, "min_n": expstats.MIN_N, "alpha": expstats.ALPHA, "k": k_total, "alpha_adj": adj if k_total else None,
                        "rows": out, "rows_total": rows_total, "total_closed": len(base), "unreviewed": unreviewed, "meta": meta, "confirmed": conf})


def learning_detail(root, mode, now=None):
    """Отчёт «Учится ли мозг» по запросу клиента (R0.2): tb.learn.dashapi.detail (отчёт или {"ok": False, "mode", "error": "failed"}), на выходе model.scrub. Режим проверяется в report.normalize_mode.
    Тема в TOPICS не входит: отчёт собирается только по запросу."""
    from tb.dash import model
    from tb.learn import dashapi
    return model.scrub(dashapi.detail(root, mode, now))


TOPICS = {"brain": brain, "ctl": ctl, "day": day, "events": events, "market": market, "news": news, "notify": notify, "briefs": briefs, "system": system, "settings": settings}
INTERVAL = {"decisions": 3.0, "models": 30.0, "ideas": 10.0, "market": 5.0, "news": 15.0, "notify": 5.0, "day": 5.0, "briefs": 10.0, "system": 5.0, "settings": 15.0}      # тема -> не чаще раза в N секунд (по умолчанию HEAVY_S сервера)
MODE_TOPICS = {"home": home, "trades": trades, "decisions": decisions, "models": models, "plans": plans, "ideas": ideas}        # зависят от режима клиента: строятся для каждого режима, который сейчас смотрит хотя бы один клиент


def build(topic, root=None, now=None, mode=None):
    from tb.dash import model
    if topic in MODE_TOPICS:
        return model.scrub(MODE_TOPICS[topic](root, now, mode))
    return model.scrub(TOPICS[topic](root, now))
