"""Сторож позиции: дешёвая проверка кодом (без токенов), которая будит мозг (position_manager), когда с монетой в позиции что-то происходит.

Указание владельца 2026-11-01: монета в сделке под постоянным надзором; любое изменение объёмов, структуры, стакана, настроения толпы или новостей
мозг узнаёт немедленно (событие brain_wake_request, класс доставки 1, отстой 0.5 с). Сам сторож позицию не закрывает и стоп не двигает: решает мозг.

Триггеры (код пробуждения, в порядке важности):
  NEWS           свежая новость по монете (intel/flash): любая, не старше news_max_age_s;
  FAST_MOVE      ход за минуту от fast_move_atr ATR(5м) в любую сторону (кит, каскад);
  STRUCT_BREAK   цена за ближайшим уровнем структуры против позиции (long: ниже поддержки, short: выше сопротивления) не меньше struct_break_s подряд;
  CROWD_TURN     настроение толпы (brain/crowd.py) стало против позиции: паника для лонга, эйфория для шорта, или сторона давления против;
  BOOK_WALL      устойчивая стена в стакане против позиции ближе wall_atr ATR(5м);
  VOL_SPIKE      оборот часа к норме (tx) от vol_spike_x и заметно выше, чем при входе;
  REGIME_CHANGE  режим 1ч структуры сменился относительно входа;
  FLOW_TURN      дельта 1м и перекос стакана против позиции не меньше flow_turn_s секунд подряд (только при прибыли >= stalled_min_r);
  STALLED        прибыль stalled_min минут не обновляла максимум (только при прибыли >= stalled_min_r).
Защита от лишних трат: не больше max_wakes_per_hour пробуждений в час на позицию и пауза между повторами одного кода (REPEAT_S). Состояние в rt["counters"]["watch"].

>>> cfg = {"stalled_min": 15, "stalled_min_r": 0.5, "flow_turn_s": 30, "max_wakes_per_hour": 3}
>>> st = {}
>>> evaluate(st, 1, 0.8, None, 0.0, cfg)
>>> evaluate(st, 1, 0.8, None, 15 * 60 + 1, cfg)
'STALLED'
>>> evaluate(st, 1, 0.8, None, 15 * 60 + 2, cfg)
"""

PEAK_EPS_R = 0.05          # новый максимум = выше прежнего не меньше чем на 0.05R (шум не сбрасывает таймер боковика)
REPEAT_S = {"NEWS": 0.0, "FAST_MOVE": 5 * 60.0, "STRUCT_BREAK": 10 * 60.0, "CROWD_TURN": 15 * 60.0, "BOOK_WALL": 10 * 60.0, "VOL_SPIKE": 10 * 60.0,
            "REGIME_CHANGE": 30 * 60.0, "FLOW_TURN": 10 * 60.0, "STALLED": 15 * 60.0}
ORDER = ("NEWS", "FAST_MOVE", "STRUCT_BREAK", "CROWD_TURN", "BOOK_WALL", "VOL_SPIKE", "REGIME_CHANGE", "FLOW_TURN", "STALLED")
CODES = ORDER
DEFAULTS = {"fast_move_atr": 2.0, "fast_move_s": 60.0, "struct_break_s": 30.0, "struct_break_buf_pct": 0.1, "wall_atr": 1.5, "vol_spike_x": 3.0,
            "news_max_age_s": 180.0}
PX_HIST_S = 120.0          # сколько секунд цен держать для хода за минуту


def _f(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def against(side_sign, flow):
    """True, если дельта 1м и перекос стакана оба против позиции (long: продажи и перевес asks)."""
    if not isinstance(flow, dict):
        return False
    cvd, imb = flow.get("cvd_1m_usd"), flow.get("imb_pct")
    if not isinstance(cvd, (int, float)) or not isinstance(imb, (int, float)):
        return False
    return side_sign * cvd < 0 and side_sign * imb < 0


def _cfg(cfg, k):
    v = _f((cfg or {}).get(k))
    return DEFAULTS[k] if v is None else v


def _market_codes(st, side_sign, flow, now, cfg, ctx):
    """Коды надзора по рынку (ctx: last, a5_pct, reg, sup, res, tx, news, crowd, walls) -> set кодов, условия которых выполнены сейчас."""
    out = set()
    ctx = ctx or {}
    last, a5 = _f(ctx.get("last")), _f(ctx.get("a5_pct"))
    # NEWS: новые id новостей по монете (не старше news_max_age_s), каждая будит один раз
    seen = st.setdefault("news_seen", [])
    fresh = [n for n in (ctx.get("news") or []) if isinstance(n, dict) and isinstance(n.get("id"), str) and n["id"] not in seen
             and _f(n.get("age_s")) is not None and n["age_s"] <= _cfg(cfg, "news_max_age_s")]
    if fresh:
        seen.extend(n["id"] for n in fresh)
        del seen[:-50]
        out.add("NEWS")
    # FAST_MOVE: ход за fast_move_s от fast_move_atr ATR(5м)
    if last:
        hist = [(t, p) for t, p in st.get("px_hist", []) if now - t <= PX_HIST_S]
        hist.append((now, last))
        st["px_hist"] = hist
        win = _cfg(cfg, "fast_move_s")
        base = [p for t, p in hist if now - t >= win * 0.8]
        if base and a5:
            ref = base[-1]
            if ref and abs(last / ref - 1.0) * 100.0 >= _cfg(cfg, "fast_move_atr") * a5:
                out.add("FAST_MOVE")
    # STRUCT_BREAK: за ближайшим уровнем против позиции не меньше struct_break_s
    lv = ctx.get("sup") if side_sign > 0 else ctx.get("res")
    lvl = max((p for p in (lv or []) if _f(p)), default=None) if side_sign > 0 else min((p for p in (lv or []) if _f(p)), default=None)
    buf = _cfg(cfg, "struct_break_buf_pct") / 100.0
    if last and lvl and side_sign * (last - lvl * (1 - side_sign * buf)) < 0:
        st.setdefault("break_since", now)
        if now - st["break_since"] >= _cfg(cfg, "struct_break_s"):
            out.add("STRUCT_BREAK")
    else:
        st.pop("break_since", None)
    # CROWD_TURN: настроение или сторона давления против позиции (меняется относительно прошлого состояния)
    cr = ctx.get("crowd") if isinstance(ctx.get("crowd"), dict) else {}
    mood, bias = cr.get("mood"), cr.get("bias")
    bad = (side_sign > 0 and (mood == "panic" or bias == "short")) or (side_sign < 0 and (mood == "euphoria" or bias == "long"))
    key = "%s/%s" % (mood, bias)
    if bad and st.get("crowd_key") != key:
        out.add("CROWD_TURN")
    st["crowd_key"] = key
    # BOOK_WALL: устойчивая стена против позиции ближе wall_atr ATR
    if a5:
        for w in ctx.get("walls") or []:
            if not isinstance(w, dict):
                continue
            side_ok = (w.get("side") == "ask" and side_sign > 0) or (w.get("side") == "bid" and side_sign < 0)
            d = _f(w.get("dist_pct"))
            if side_ok and d is not None and abs(d) <= _cfg(cfg, "wall_atr") * a5:
                out.add("BOOK_WALL")
    # VOL_SPIKE: оборот часа к норме от vol_spike_x и в полтора раза выше, чем при входе
    tx = _f(ctx.get("tx"))
    if tx is not None:
        if st.get("tx0") is None:
            st["tx0"] = tx
        if tx >= _cfg(cfg, "vol_spike_x") and tx >= 1.5 * max(st["tx0"], 0.1):
            out.add("VOL_SPIKE")
    # REGIME_CHANGE: режим 1ч сменился относительно входа
    reg = ctx.get("reg")
    if isinstance(reg, str) and reg != "unknown":
        if st.get("reg0") is None:
            st["reg0"] = reg
        elif reg != st["reg0"]:
            out.add("REGIME_CHANGE")
    return out


def evaluate(st, side_sign, r, flow, now, cfg, ctx=None):
    """-> код пробуждения или None; st (dict состояния) меняется на месте. ctx (необязательно): данные рынка для кодов надзора (_market_codes)."""
    if r is None and ctx is None:
        return None
    if r is not None and (st.get("peak_r") is None or r > st["peak_r"] + PEAK_EPS_R):
        st["peak_r"], st["peak_t"] = r, now
    if against(side_sign, flow):
        st.setdefault("against_since", now)
    else:
        st.pop("against_since", None)
    ready = _market_codes(st, side_sign, flow, now, cfg, ctx) if ctx is not None else set()
    if r is not None and r >= float(cfg["stalled_min_r"]):
        if st.get("against_since") is not None and now - st["against_since"] >= float(cfg["flow_turn_s"]):
            ready.add("FLOW_TURN")
        if st.get("peak_t") is not None and now - st["peak_t"] >= float(cfg["stalled_min"]) * 60.0:
            ready.add("STALLED")
    if not ready:
        return None
    wakes = [t for t in st.get("wakes", []) if now - t < 3600.0]
    st["wakes"] = wakes
    if len(wakes) >= int(cfg["max_wakes_per_hour"]):
        return None
    last = st.setdefault("last", {})
    for code in ORDER:
        if code in ready and now - last.get(code, -1e18) >= REPEAT_S[code]:
            last[code] = now
            wakes.append(now)
            return code
    return None
