"""Схемы ответов ролей API-мозга, смысловая проверка решений и маскирование секретов (WP11-C2; 07b п. 5.2, 5.5).

SCHEMAS: id схемы роли (`output_schema`) -> JSON Schema для `tb.llm.schema.validate` (additionalProperties по умолчанию запрещены).
entry/1 (роль entry_scout, только paper): skip | propose (тезис, сторона, уровень, стоп, цели, сетап, гипотеза); размер/плечо/маржу модель не задаёт, исполняет код раннера.
WP11-C3: pm/1, dayplan/1, audit/1 (с вложенным tb.carry/1), casereview/1 (tb.casereview/1 из 08 п. 1.4) полные; review/1 (старая форма) и reader/1 оставлены.
semantic(schema_id, obj, ctx) -> список кодов: проверки, которые схема выразить не может (набор args по решению, совпадение плана, стоп только строже,
символы из списка досье, уровни рядом с ценой, дубликаты, окно новостей, потолки лимитов, «нельзя ослабить детерминированное решение»).
pm_context(brief, plan) / dayplan_context(brief, facts) / audit_context(inp) строят ctx из досье: ctx ОБЯЗАТЕЛЬНО берётся из данных системы, не из ответа модели.
redact(text) режет строки, похожие на ключи (sk-..., длинные hex/base64), для логов, журнала решений и сообщений об ошибках.
Модуль чистый: без файлов и сети.
"""
import json
import re

# paper + blackout_mode=warn: константы совпадают с tb.core.blackout (проверяет tests/test_blackout_warn.py); модуль остаётся чистым, без импорта ядра
BLACKOUT_MAX_SIZE_MULT = 0.5
BLACKOUT_RISK_CODE = "blackout_warn"

RE_PLAN_ID = r"^PL-\d{4}-[A-Z0-9]{2,20}-\d{2,3}$"
PM_DECISIONS = ("hold", "move_stop", "close", "partial_close", "cancel", "amend", "hold_plan", "release_plan", "escalate")
PM_ARGS = {"hold": (), "close": (), "cancel": (), "hold_plan": (), "release_plan": (), "move_stop": ("to",), "partial_close": ("frac",),
           "amend": ("patch",), "escalate": ("reason_code",)}
ESCALATE_CODES = ("SECRET_REQUEST", "NEED_OWNER", "DATA_GAP")
ERR_TAGS = ("no-confirm", "chase", "tight-stop", "wide-stop", "wrong-direction", "news-miss", "size", "fee-drag", "rule-break", "good-process-bad-luck", "good")
SETUPS = ("level_reclaim", "level_reject", "sweep_reject", "breakout_retest", "range_fade", "trend_pullback", "momentum_chase", "none")      # momentum_chase: вход по рынку по импульсу, только уровни риска 4-5 (risk_levels setup_aggr high)
RE_CODE = r"^[a-z0-9_]{2,30}$"
RE_TAG = r"^[a-z0-9._-]{2,40}$"                    # как plan_schema.RE_TAG (теги гипотезы/сетапа плана)
ENTRY_DECISIONS = ("skip", "propose")
ENTRY_SKIP_CODES = ("no_setup", "no_confirm", "blackout", "pause", "busy", "rr_low", "wide_stop", "no_slots", "no_data", "no_levels", "kill", "other")
ENTRY_TAGS = ("no-confirm", "blackout")
ENTRY_PROPOSE_KEYS = ("sym", "side", "horizon", "level", "stop", "tp", "setup", "hypothesis", "ttl_min", "confirm", "thesis")
ENTRY_OPTIONAL_KEYS = ("zone", "size_mult", "tags", "entry_type")
ENTRY_PRECHECK_CODES = ("WIDE_RANGE", "DAY_NO_PLANS", "NO_SLOTS", "ROOM_CLOSED", "SYM_BUSY", "PAUSE_SYM", "PAUSE_ALL", "MIN_SIZE_SYM", "NO_DATA", "NO_SESSION",
                        "STALE_DATA", "NO_CANDIDATES")   # отказы по данным досье без вызова модели
PRICE_KEYS = ("level", "zone", "stop", "tp", "price", "sl", "horizon", "confirm", "entry_type")      # ответ entry_reply/1 не называет цены и параметры входа: их берёт код из кандидата
MAX_DATA_AGE_S = 120.0                              # цена монеты события у сканера/фида старше этого (brief.data.px_age_s) = STALE_DATA; порог: config/brain.json runtime.max_data_age_s
                                                    # (разбор paper 2026-11-02: XPL, hot.json и prices.json устарели на 90 мин, вход разрешён)
FADE_IMPULSE_ATR = 2.0                              # свеча события от 2 ATR(5м) против входа = импульс (кит, каскад): вход против него без реакции запрещён любому сетапу,
                                                    # кроме sweep_reject и level_reclaim (они по определению после реакции). UAI 2026-11-01: trend_pullback long в свечу -2.85%
REACTION_SETUPS = ("sweep_reject", "level_reclaim")  # сетапы «после реакции цены»: единственные, которым можно входить против импульса и против паники/эйфории
# Геометрия входа (константы и формулы: пол стопа, ширина стопа шлюза, RR, импульс chase, пороги) живёт в tb.logic.geometry (R1.2); здесь импорт под прежними именами.
# Пол стопа: 0.1%, 1.5 ATR(5м), три спреда и две издержки круга (Bybit taker 0.24% -> пол 0.48%, maker-вход 0.18% -> 0.35%; разбор 2026-10-29..11-01: -3.2R комиссиями).
from tb.logic.geometry import (  # noqa: E402,F401
    CHASE_MIN_LEVEL, CHASE_RANGE_ATR, CHASE_SETUP, CHASE_SURGE_X, ENTRY_MAX_LEVEL_DIST_LOOSE_PCT, ENTRY_MAX_LEVEL_DIST_PCT, ENTRY_MAX_STOP_PCT,
    ENTRY_MIN_RR, ENTRY_MIN_STOP_PCT, ENTRY_SIZE_MULT_MAX, ENTRY_STOP_ATR_MULT, ENTRY_STOP_COST_MULT, LOOSE_RISK_LEVEL, SESSION_ORDER,
    _entry_rr, _loose, chase_impulse, exec_stop_pct, stop_floor_pct)

FADE_SETUPS = ("range_fade", "level_reject")        # входы против движения события
FADE_SURGE_X = 3.0                                  # оборот 5м от этого множителя при движении против входа = импульс, фейд без реакции запрещён
ENTRY_MAX_ZONE_PCT = 1.0                            # ширина зоны входа без подтверждения не шире 1% от уровня
LEVEL_POOL_TOL_PCT = 0.3                            # уровень входа обязан совпадать с уровнем из struct/levels/ideas досье (допуск 0.3% или половина ATR 5м):
                                                    # UAI 2026-11-01: структуры по монете не было, локальная модель выдумала уровень, стоп и цели, сделка -2.9R


def level_pool(brief, sym):
    """Все известные уровни монеты из досье: struct.s[sym] res/sup/day/pd/wk, levels[sym] swing_5m/swing_1h/day/prev_day/week, активные идеи по монете.
    -> отсортированный список цен без точных дублей (кластеризация близких уровней в cands). Пусто = уровней код не знает."""
    brief = brief if isinstance(brief, dict) else {}
    raw = []
    st = ((brief.get("struct") or {}).get("s") or {}).get(sym) if isinstance(brief.get("struct"), dict) else None
    if isinstance(st, dict):
        for k in ("res", "sup"):
            raw += [r[0] for r in (st.get(k) or []) if isinstance(r, list) and r]
        for k in ("day", "wk"):
            raw += list(st.get(k) or [])[:2]
        raw += list(st.get("pd") or [])[:2]
    lv = (brief.get("levels") or {}).get(sym) if isinstance(brief.get("levels"), dict) else None
    if isinstance(lv, dict):
        raw += list(lv.get("swing_5m") or []) + list(lv.get("swing_1h") or [])
        for k in ("day", "prev_day", "week"):
            v = lv.get(k)
            if isinstance(v, dict):
                raw += [v.get("hi"), v.get("lo")]
    ideas = (brief.get("ideas") or {}).get("active") if isinstance(brief.get("ideas"), dict) else None
    for i in ideas or []:
        if isinstance(i, dict) and i.get("sym") == sym:
            raw.append(i.get("level"))
    return sorted(set(float(v) for v in raw if _num(v) and v > 0))
RE_SYM = r"^[A-Z0-9]{2,20}$"
OPPOSITE_OK = ("range_fade", "level_reject")        # watch против bias допустим только для этих сетапов (07b 5.3)
CARRY_BASIS = ("trend_intact", "level_held", "volume_supportive", "news_neutral", "event_far", "funding_ok", "stop_at_be", "thesis_valid")
CARRY_AGAINST = ("news_risk", "volume_fading", "level_lost", "event_near", "funding_high", "range_expanding")
SIZE_KEYS = ("size", "qty", "quantity", "leverage", "lev", "margin", "amount", "notional", "risk", "risk_usdt", "risk_pct", "size_mult", "contracts")
RE_ITEM = r"^(pos|plan):[A-Za-z0-9_-]{1,40}$"
CR_VERDICTS = ("good_process", "bad_process", "unlucky", "lucky", "unclear")
CR_RIGHT = ("confirm_waited", "stop_beyond_extreme", "size_ok", "skipped_bad_range", "respected_cooldown", "waited_for_blackout")
CR_IMPROVE = ("no_confirm", "chase", "wide_stop", "tight_stop", "size", "ignored_cooldown", "blackout", "fee_drag", "wrong_direction", "late_exit", "none")
CR_ALT = ("wait_confirm", "skip", "smaller_size", "wider_stop_skip", "other_side", "exit_earlier", "hold_longer", "none")
CR_KNOWN = ("range_5m_gt_stop", "funding_extreme", "no_confirm", "wide_stop", "tight_stop", "spread_wide", "blackout", "chase_move", "low_rr", "big_move_24h", "meme_coin")


def _free(depth):
    """Свободное значение ограниченной глубины (патч плана, карточки): скаляры, массивы и объекты; точную проверку полей делает plan_schema/шлюз."""
    leaf = [{"type": "null"}, {"type": "string", "maxLength": 300}, {"type": "number"}, {"type": "boolean"}]
    if depth <= 0:
        return {"anyOf": leaf}
    sub = _free(depth - 1)
    return {"anyOf": leaf + [{"type": "array", "maxItems": 20, "items": sub}, {"type": "object", "additionalProperties": sub}]}


_FREE = _free(5)


def _s(n, lo=0):
    return {"type": "string", "minLength": lo, "maxLength": n}


# rev плана модель не задаёт (решение владельца 2026-11-03): поле необязательно и игнорируется, ревизию и проверки по текущему плану делают шлюз и исполнитель
PM = {"type": "object", "required": ["schema", "event_seq", "plan", "decision", "args", "why", "conf", "next"], "properties": {
    "schema": {"const": "tb.pm/1"}, "event_seq": {"type": "integer", "minimum": 0}, "plan": {"type": "string", "pattern": RE_PLAN_ID},
    "rev": {}, "decision": {"enum": list(PM_DECISIONS)},      # rev: старые ответы с полем принимаются, значение не используется
    "args": {"type": "object", "properties": {
        "to": {"anyOf": [{"type": "number", "minimum": 0}, {"const": "be"}]}, "frac": {"type": "number", "minimum": 0.05, "maximum": 1},
        "patch": {"type": "object", "additionalProperties": _FREE}, "reason_code": {"enum": list(ESCALATE_CODES)}}},
    "why": _s(200, 1), "conf": {"type": "number", "minimum": 0, "maximum": 1}, "next": {"enum": ["event", "check_5m"]}}}

ENTRY = {"type": "object", "required": ["schema", "event_seq", "decision", "why", "conf"], "properties": {
    "schema": {"const": "tb.entry/1"}, "event_seq": {"type": "integer", "minimum": 0}, "decision": {"enum": list(ENTRY_DECISIONS)},
    "skip_code": {"enum": list(ENTRY_SKIP_CODES)},
    "watch": {"type": "object", "required": ["side", "level", "stop", "tp"], "properties": {
        "side": {"enum": ["long", "short"]}, "level": {"type": "number", "minimum": 1e-12}, "stop": {"type": "number", "minimum": 1e-12},
        "tp": {"type": "number", "minimum": 1e-12}, "setup": {"enum": list(SETUPS)}}},
    "sym": {"type": "string", "pattern": RE_SYM}, "side": {"enum": ["long", "short"]}, "horizon": {"enum": list(SESSION_ORDER)}, "level": {"type": "number", "minimum": 1e-12},
    "zone": {"type": "object", "required": ["low", "high"], "properties": {"low": {"type": "number", "minimum": 1e-12}, "high": {"type": "number", "minimum": 1e-12}}},
    "stop": {"type": "number", "minimum": 1e-12}, "tp": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "number", "minimum": 1e-12}},
    "setup": {"enum": list(SETUPS)}, "hypothesis": {"type": "string", "pattern": RE_TAG}, "ttl_min": {"type": "integer", "minimum": 5, "maximum": 240},
    "confirm": {"enum": ["candle", "none"]}, "entry_type": {"enum": ["market", "limit"]}, "size_mult":{"type": "number", "minimum": 0.1, "maximum": ENTRY_SIZE_MULT_MAX},
    "tags": {"type": "array", "maxItems": 2, "items": {"enum": list(ENTRY_TAGS)}}, "thesis": _s(280, 10),
    "why": _s(200, 1), "conf": {"type": "number", "minimum": 0, "maximum": 1}}}

# entry_reply/1: ответ модели в JSON-пути entry_scout (reply_schema роли). Цен нет: модель выбирает кандидата brief.cands по номеру,
# код разворачивает его в tb.entry/1 (runner._entry_obj). Обязательность полей по decision проверяет semantic (_entry_reply).
ENTRY_REPLY = {"type": "object", "required": ["decision", "why", "conf"], "properties": {
    "decision": {"enum": list(ENTRY_DECISIONS)},
    "candidate": {"type": "integer", "minimum": 1, "maximum": 10}, "setup": {"enum": [s for s in SETUPS if s != "none"]}, "thesis": _s(280, 10),
    "size_mult": {"type": "number", "minimum": 0.1, "maximum": ENTRY_SIZE_MULT_MAX}, "tags": {"type": "array", "maxItems": 2, "items": {"enum": list(ENTRY_TAGS)}},
    "hypothesis": {"type": "string", "pattern": RE_TAG},
    "skip_code": {"enum": list(ENTRY_SKIP_CODES)},
    "watch": {"type": "object", "required": ["candidate"], "properties": {"candidate": {"type": "integer", "minimum": 1, "maximum": 10}}},
    "why": _s(200, 1), "conf": {"type": "number", "minimum": 0, "maximum": 1}}}

REVIEW = {"type": "object", "required": ["schema", "journal_id", "errors", "rule_followed", "lesson"], "properties": {
    "schema": {"const": "tb.review/1"}, "journal_id": {"type": "string", "pattern": r"^[A-Za-z0-9_.-]{1,40}$"},
    "errors": {"type": "array", "maxItems": 6, "items": {"enum": list(ERR_TAGS)}}, "rule_followed": {"enum": ["yes", "no", "partial"]},
    "lesson": _s(200), "idea": {"anyOf": [{"type": "null"}, {"type": "object", "additionalProperties": _FREE}]}}}

DAYPLAN = {"type": "object", "required": ["schema", "regime", "bias", "session", "watch", "allowed_setups", "avoid", "limits", "carry", "carried_review", "risks", "questions", "why"], "properties": {
    "schema": {"const": "tb.dayplan/1"}, "day_id": {"type": "string", "pattern": r"^D-[0-9A-Za-z-]{1,30}$"},
    "regime": {"enum": ["trend", "range", "volatile", "unclear"]}, "bias": {"enum": ["long", "short", "neutral"]},
    "session": {"type": "object", "required": ["type"], "properties": {
        "type": {"enum": list(SESSION_ORDER)}, "minutes": {"type": "integer", "minimum": 15, "maximum": 43200}}},     # minutes устарело: принимается и игнорируется
    "watch": {"type": "array", "maxItems": 6, "items": {"type": "object", "required": ["sym", "side", "level", "setup", "note"], "properties": {
        "sym": {"type": "string", "pattern": RE_SYM}, "side": {"enum": ["long", "short"]}, "level": {"type": "number", "minimum": 1e-12},
        "setup": {"enum": list(SETUPS)}, "note": _s(120)}}},
    "allowed_setups": {"type": "array", "maxItems": 8, "items": {"enum": list(SETUPS)}},
    "avoid": {"type": "array", "maxItems": 10, "items": {"type": "string", "pattern": RE_SYM}},
    "limits": {"type": "object", "required": ["max_new_plans", "max_risk_usdt", "symbols", "size_mult"], "properties": {
        "max_new_plans": {"type": "integer", "minimum": 0, "maximum": 6}, "max_risk_usdt": {"type": "number", "minimum": 0},
        "symbols": {"type": "array", "maxItems": 12, "items": {"type": "string", "pattern": RE_SYM}},
        "size_mult": {"type": "number", "minimum": 0, "maximum": 1}}},
    "carry": {"type": "object", "required": ["allow", "max_positions"], "properties": {"allow": {"type": "boolean"}, "max_positions": {"type": "integer", "minimum": 0, "maximum": 2}}},
    "carried_review": {"type": "array", "maxItems": 5, "items": {"type": "object", "required": ["plan", "decision", "why"], "properties": {
        "plan": {"type": "string", "pattern": RE_PLAN_ID}, "decision": {"enum": ["hold", "tighten", "close"]}, "why": _s(160)}}},
    "risks": {"type": "array", "maxItems": 8, "items": {"type": "string", "pattern": RE_CODE}},
    "questions": {"type": "array", "maxItems": 3, "items": _s(160)}, "why": _s(200, 1)}}

CARRY = {"type": "object", "required": ["schema", "item", "decision", "basis", "against", "view", "why", "conf"], "properties": {
    "schema": {"const": "tb.carry/1"}, "item": {"type": "string", "pattern": RE_ITEM}, "decision": {"enum": ["carry", "close"]},
    "basis": {"type": "array", "maxItems": 8, "items": {"enum": list(CARRY_BASIS)}}, "against": {"type": "array", "maxItems": 6, "items": {"enum": list(CARRY_AGAINST)}},
    "view": {"type": "object", "required": ["risk", "horizon_h", "recheck_min"], "properties": {
        "risk": {"enum": ["low", "mid", "high"]}, "horizon_h": {"type": "number", "minimum": 1, "maximum": 168}, "recheck_min": {"type": "number", "minimum": 5, "maximum": 1440}}},
    "why": _s(200, 1), "conf": {"type": "number", "minimum": 0, "maximum": 1}}}

AUDIT = {"type": "object", "required": ["schema", "day_id", "carry", "changes", "owner_questions", "narrative"], "properties": {
    "schema": {"const": "tb.audit/1"}, "day_id": {"type": "string", "pattern": r"^D-[0-9A-Za-z-]{1,30}$"},
    "carry": {"type": "array", "maxItems": 20, "items": CARRY},
    "changes": {"type": "array", "maxItems": 20, "items": {"type": "object", "required": ["item", "decision", "why"], "properties": {
        "item": {"type": "string", "pattern": RE_ITEM}, "decision": {"enum": ["close", "carry"]}, "why": _s(160)}}},
    "owner_questions": {"type": "array", "maxItems": 5, "items": {"type": "object", "required": ["item", "text", "options"], "properties": {
        "item": {"type": "string", "pattern": RE_ITEM}, "text": _s(160, 1), "options": {"type": "array", "maxItems": 2, "items": {"enum": ["carry", "close"]}}}}},
    "narrative": _s(600)}}

CASEREVIEW = {"type": "object", "required": ["schema", "verdict", "right", "improve", "alternative", "known_at_decision", "lesson_hint", "conf"], "properties": {
    "schema": {"const": "tb.casereview/1"}, "verdict": {"enum": list(CR_VERDICTS)},
    "right": {"type": "array", "maxItems": 5, "items": {"enum": list(CR_RIGHT)}}, "improve": {"type": "array", "minItems": 1, "maxItems": 5, "items": {"enum": list(CR_IMPROVE)}},
    "alternative": {"type": "object", "required": ["action", "detail"], "properties": {"action": {"enum": list(CR_ALT)}, "detail": _s(160)}},
    "known_at_decision": {"type": "array", "maxItems": 8, "items": {"enum": list(CR_KNOWN)}},
    "lesson_hint": {"anyOf": [{"type": "null"}, {"type": "object", "required": ["code", "cond"], "properties": {
        "code": {"type": "string", "pattern": r"^L-[a-z0-9_]{2,30}$"}, "cond": {"type": "string", "pattern": RE_CODE}}}]},
    "conf": {"type": "number", "minimum": 0, "maximum": 1}}}

READER = {"type": "object", "required": ["schema", "items"], "properties": {
    "schema": {"const": "tb.reader/1"}, "items": {"type": "array", "maxItems": 10, "items": {"type": "object", "additionalProperties": _FREE}}}}

SCHEMAS = {"pm/1": PM, "entry/1": ENTRY, "entry_reply/1": ENTRY_REPLY, "review/1": REVIEW, "casereview/1": CASEREVIEW, "dayplan/1": DAYPLAN, "audit/1": AUDIT, "reader/1": READER}


def schema_for(schema_id):
    return SCHEMAS.get(schema_id)


# ------------------------------------------------------------------ контекст проверки (из данных системы, не из ответа модели)
def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and x == x else None


SMALL_LOSS_R = -0.5                 # pm close при убытке между -0.5R и 0 отклоняется (HOLD_SMALL_LOSS): стоп и инвалидация плана работают сами
CLOSE_ANY_R_EVENTS = {"session_ending", "kill", "limit_hit"}       # события, при которых закрыть можно при любом R


def pm_context(brief, plan, event=None):
    """Контекст смысловой проверки pm/1 из досье scope=event и события задания: сторона, цена, стоп, наличие позиции, типы событий, политика переноса, окно новостей.
    Нет данных -> поле None (проверка пропускается, остаётся шлюз). Досье есть, а плана в нём нет -> unknown_plan (любое действие кроме hold/escalate отклоняется)."""
    ctx = {"plan": plan, "has_position": None, "side": None, "px": None, "sl": None, "avg": None, "r": None, "status": None, "unknown_plan": False,
           "event_types": sorted(set(t for t in ((event or {}).get("types") or []) if isinstance(t, str))), "carry_policy": None, "blackout_min": None, "blackout_warn": False}
    if not isinstance(brief, dict):
        return ctx
    rl = brief.get("risk_level")
    if isinstance(rl, dict):
        ctx["carry_policy"] = (rl.get("carry") or {}).get("policy") if isinstance(rl.get("carry"), dict) else None
    bo = (brief.get("market") or {}).get("blackout") if isinstance(brief.get("market"), dict) else None
    if isinstance(bo, dict):
        ctx["blackout_min"] = _num(bo.get("in_min"))
        ctx["blackout_warn"] = bo.get("mode") == "warn"          # режим выставляет досье (paper + blackout_mode=warn); live/testnet = block
    pos = next((x for x in brief.get("positions") or [] if isinstance(x, dict) and x.get("plan") == plan), None)
    pl = next((x for x in brief.get("plans") or [] if isinstance(x, dict) and x.get("id") == plan), None)
    if pos:
        ctx.update(has_position=True, side=pos.get("side"), px=_num(pos.get("px")), sl=_num(pos.get("sl")), avg=_num(pos.get("avg")), r=_num(pos.get("r")))
    elif pl:
        ctx.update(has_position=False, side=pl.get("side"))
    if pl:
        ctx["status"] = pl.get("status")
        if ctx["side"] is None:
            ctx["side"] = pl.get("side")
    elif pos:
        ctx["has_position"] = True
    if not pos and not pl:
        ctx["unknown_plan"] = True
    return ctx


def dayplan_context(brief, facts=None, **over):
    """Контекст dayplan/1 из досье start: допустимые символы, цены, окно новостей, потолки лимитов, тип сессии владельца, политика переноса."""
    brief = brief if isinstance(brief, dict) else {}
    facts = facts if isinstance(facts, dict) else {}
    syms, prices = set(), {}
    for key in ("positions", "plans"):
        for x in brief.get(key) or []:
            if isinstance(x, dict) and x.get("sym"):
                syms.add(x["sym"])
                if _num(x.get("px")):
                    prices[x["sym"]] = _num(x["px"])
    for key, val in (brief.get("levels") or {}).items() if isinstance(brief.get("levels"), dict) else []:
        syms.add(key)
        if isinstance(val, dict) and _num(val.get("px")):
            prices.setdefault(key, _num(val["px"]))
    mk = brief.get("market") if isinstance(brief.get("market"), dict) else {}
    for key, name in (("btc", "BTC"), ("eth", "ETH")):          # интеграция: цены BTC/ETH из блока market (фид), пока в levels нет символа без плана/идеи
        m = mk.get(key)
        if isinstance(m, dict) and _num(m.get("px")):
            syms.add(name)
            prices.setdefault(name, _num(m["px"]))
    for x in (brief.get("watchlist") or brief.get("symbols") or []):
        if isinstance(x, str):
            syms.add(x)
    # монеты блоков struct / hot / ideas: промпт morning_analyst разрешает watch и limits.symbols из них, иначе SOL/KORU из struct отвергались как SYMBOL_UNKNOWN
    st = (brief.get("struct") or {}).get("s") if isinstance(brief.get("struct"), dict) else None
    for key, val in (st.items() if isinstance(st, dict) else []):
        if isinstance(key, str) and re.match(RE_SYM, key):
            syms.add(key)
            if isinstance(val, dict) and _num(val.get("px")):
                prices.setdefault(key, _num(val["px"]))
    hot = (brief.get("hot") or {}).get("top") if isinstance(brief.get("hot"), dict) else None
    ideas = (brief.get("ideas") or {}).get("active") if isinstance(brief.get("ideas"), dict) else None
    for x in (hot if isinstance(hot, list) else []) + (ideas if isinstance(ideas, list) else []):
        if isinstance(x, dict) and isinstance(x.get("sym"), str) and re.match(RE_SYM, x["sym"]):
            syms.add(x["sym"])
            if _num(x.get("px")):
                prices.setdefault(x["sym"], _num(x["px"]))
    bo = (brief.get("market") or {}).get("blackout") if isinstance(brief.get("market"), dict) else None
    bl = bo.get("in_min") if isinstance(bo, dict) else None
    acc = brief.get("account") if isinstance(brief.get("account"), dict) else {}
    ph = brief.get("paper_history") if isinstance(brief.get("paper_history"), dict) else None
    mode = ((brief.get("system") or {}).get("exec") or {}).get("mode") if isinstance(brief.get("system"), dict) else None
    cap = None
    if (_num(acc.get("stop_streak")) or 0) >= 2 or (mode == "live" and ph is not None and (_num(ph.get("closed")) or 0) < 30):
        cap = 0.5                                   # после двух стопов подряд и live без paper-истории: только осторожный размер
    ctx = {"symbols": sorted(syms) if brief else None, "prices": prices, "data_ok": bool(prices) and isinstance(brief.get("market"), dict), "size_cap": cap,
           "blackout_min": _num(bl), "blackout_warn": isinstance(bo, dict) and bo.get("mode") == "warn", "limits": brief.get("limits") if isinstance(brief.get("limits"), dict) else None,
           "session_type": (brief.get("session") or {}).get("type") if isinstance(brief.get("session"), dict) else None,
           "carry_policy": ((brief.get("risk_level") or {}).get("carry") or {}).get("policy") if isinstance(brief.get("risk_level"), dict) else None,
           "max_level_distance_pct": 12.0}
    ctx.update(over)
    return ctx


def entry_context(brief, event=None):
    """Контекст смысловой проверки entry/1 из досье scope=event и события задания: символ события, цена, ATR 5м, спред, диапазон 5м, окно новостей, слоты, занятые символы,
    паузы после стопа, политика входа без подтверждения, тип сессии, режим исполнителя, KILL. Нет данных -> None (проверка пропускается, остаётся Gate исполнителя)."""
    brief = brief if isinstance(brief, dict) else {}
    event = event if isinstance(event, dict) else {}
    sym = event.get("sym") if isinstance(event.get("sym"), str) else None
    ctx = {"sym": sym, "symbols": None, "px": None, "a5_pct": None, "spread_bps": None, "range_5m_pct": None, "blackout_min": None, "blackout_warn": False,
           "slots": None, "room_allowed": None, "busy": [], "pause_sym": None, "pause_all": None, "stop_streak": None, "kill": None, "exec_mode": None,
           "no_confirm_allowed": None, "no_confirm_max": None, "session_type": None, "has_brief": bool(brief), "risk_level": None, "struct_missing": False}
    if not brief:
        return ctx
    rec = [e for e in (brief.get("recent") or []) if isinstance(e, dict) and e.get("type") == "scanner_signal" and isinstance(e.get("sym"), str)]
    syms = sorted(set([e["sym"] for e in rec] + ([sym] if sym else [])))
    ctx["symbols"] = syms or None
    last = next((e for e in reversed(rec) if e["sym"] == sym), None) if sym else None
    d = last.get("d") if isinstance(last, dict) and isinstance(last.get("d"), dict) else {}
    px = _num((((brief.get("levels") or {}).get(sym) or {}) if isinstance(brief.get("levels"), dict) else {}).get("px")) if sym else None
    st = (((brief.get("struct") or {}).get("s") or {}).get(sym)) if sym and isinstance(brief.get("struct"), dict) and isinstance(brief["struct"].get("s"), dict) else None
    st = st if isinstance(st, dict) else {}
    ctx["px"] = px or _num(st.get("px")) or _num(d.get("px"))
    a5 = _num(st.get("a5_pct"))
    lv = ((brief.get("levels") or {}).get(sym)) if sym and isinstance(brief.get("levels"), dict) else None
    if a5 is None and isinstance(lv, dict) and _num(lv.get("atr_5m")) and ctx["px"]:
        a5 = _num(lv["atr_5m"]) / ctx["px"] * 100.0
    ctx["a5_pct"] = a5
    ctx["spread_bps"] = _num(st.get("sp")) if _num(st.get("sp")) is not None else _num(d.get("spread_bps"))
    ctx["range_5m_pct"] = _num(d.get("range_5m_pct"))
    ctx["surge_x"], ctx["ret_5m"] = _num(d.get("turn_surge_5m")), _num(d.get("ret_5m_pct"))
    bo = (brief.get("market") or {}).get("blackout") if isinstance(brief.get("market"), dict) else None
    if isinstance(bo, dict):
        ctx["blackout_min"] = _num(bo.get("in_min"))
        ctx["blackout_warn"] = bo.get("mode") == "warn"
    lim = brief.get("limits") if isinstance(brief.get("limits"), dict) else {}
    ctx["slots"] = _num(lim.get("slots"))
    room = brief.get("room") if isinstance(brief.get("room"), dict) else {}
    ctx["room_allowed"] = room.get("allowed") if isinstance(room.get("allowed"), bool) else None
    ctx["busy"] = sorted(set(x["sym"] for key in ("positions", "plans") for x in (brief.get(key) or []) if isinstance(x, dict) and isinstance(x.get("sym"), str)))
    pz = brief.get("pause") if isinstance(brief.get("pause"), dict) else {}
    ctx["pause_sym"] = _num((pz.get("syms") or {}).get(sym)) if sym and isinstance(pz.get("syms"), dict) else None
    ctx["pause_all"] = _num(pz.get("all"))
    ctx["stop_streak"] = _num(pz.get("streak"))
    ctx["min_size_sym"] = _num((pz.get("min_size") or {}).get(sym)) if sym and isinstance(pz.get("min_size"), dict) else None      # исполнитель недавно отказал по MIN_SIZE
    sysd = brief.get("system") if isinstance(brief.get("system"), dict) else {}
    ctx["kill"] = sysd.get("kill") if isinstance(sysd.get("kill"), bool) else None
    ctx["exec_mode"] = (sysd.get("exec") or {}).get("mode") if isinstance(sysd.get("exec"), dict) else None
    rl = _num((brief.get("risk_level") or {}).get("level")) if isinstance(brief.get("risk_level"), dict) else None
    ctx["risk_level"] = int(rl) if rl is not None else None
    sm_ = brief.get("struct_missing") if isinstance(brief.get("struct_missing"), list) else []
    ctx["struct_missing"] = bool(sym and sym in sm_)      # структуры по монете события ещё нет (свечи подтягиваются): раннер ждёт её, а не зовёт модель без уровней (levels без struct бывает пустым)
    ctx["level_pool"] = level_pool(brief, sym) if sym else []      # известные коду уровни монеты: уровень входа обязан быть одним из них (_entry: NO_LEVELS / LEVEL_NOT_IN_STRUCT)
    cr = brief.get("crowd") if isinstance(brief.get("crowd"), dict) else {}
    ctx["crowd_mood"] = cr.get("mood") if cr.get("mood") in ("panic", "euphoria", "crowded_long", "crowded_short", "calm", "unclear") else None
    ctx["crowd_bias"] = cr.get("bias") if cr.get("bias") in ("long", "short", "none") else None
    dt = brief.get("data") if isinstance(brief.get("data"), dict) else {}
    ctx["data_ready"] = dt.get("ready") if isinstance(dt.get("ready"), bool) else None      # полнота данных монеты события (brief.data): False = входа нет
    ctx["data_px"] = dt.get("px") if isinstance(dt.get("px"), bool) else None              # цена монеты у сканера/фида есть (False = данных нет вовсе)
    ctx["data_age_s"] = _num(dt.get("px_age_s"))                                            # возраст самой свежей цены монеты у сканера/фида, с (brief._px_age)
    nc = (brief.get("risk_level") or {}).get("no_confirm") if isinstance(brief.get("risk_level"), dict) else None
    if isinstance(nc, dict):
        ctx["no_confirm_allowed"] = nc.get("allowed") if isinstance(nc.get("allowed"), bool) else None
        ctx["no_confirm_max"] = _num(nc.get("size_mult_max"))
    ses = brief.get("session") if isinstance(brief.get("session"), dict) else {}
    ctx["session_type"] = ses.get("type")
    # пороги типа сессии и издержки круга из досье (те же, что у guard): без них остаются константы ENTRY_MIN_RR / ENTRY_MAX_STOP_PCT;
    # сессия-день: пороги по горизонтам (session.horizons), горизонт называет модель в ответе
    ctx["min_rr"], ctx["max_stop_pct"], ctx["cost_rt_pct"] = _num(ses.get("min_rr")), _num(ses.get("max_stop_pct")), _num(ses.get("cost_rt_pct"))
    ctx["cost_rt_maker_pct"] = _num(ses.get("cost_rt_maker_pct"))      # круг с maker-входом: пол стопа для лимитного входа (stop_floor_pct)
    hz = ses.get("horizons") if isinstance(ses.get("horizons"), dict) else None
    ctx["horizons"] = {k: {kk: _num(v.get(kk)) for kk in ("max_stop_pct", "min_rr")} for k, v in hz.items() if k in SESSION_ORDER and isinstance(v, dict)} if hz else None
    # рамка плана дня (brief.day из state/control/day_limits.json): допустимые сетапы, монеты избегать, число новых планов, потолок размера
    day = brief.get("day") if isinstance(brief.get("day"), dict) else {}
    ctx["day_setups"] = [x for x in (day.get("setups") or []) if isinstance(x, str)] if isinstance(day.get("setups"), list) else None
    ctx["day_avoid"] = [x for x in (day.get("avoid") or []) if isinstance(x, str)] if isinstance(day.get("avoid"), list) else []
    mnp = day.get("max_new_plans")
    ctx["day_max_new_plans"] = mnp if isinstance(mnp, int) and not isinstance(mnp, bool) else None
    ctx["day_size_mult"] = _num(day.get("size_mult"))
    ctx["max_horizon"] = day.get("max_horizon") if day.get("max_horizon") in SESSION_ORDER else None
    sb = (brief.get("struct") or {}).get("s") if isinstance(brief.get("struct"), dict) else None
    regs = [((sb or {}).get(s) or {}).get("reg") for s in ([sym] if sym else []) + ["BTC"]] if isinstance(sb, dict) else []
    ctx["day_shift"] = regime_shift(day.get("regime"), day.get("bias"), regs)
    ctx["off_plan_ok"] = off_plan_allowed(ctx)
    return ctx


OFF_PLAN_FREE_LEVEL = 4         # с этого уровня риска план дня только сценарий: его рамку (сетапы, avoid, горизонт, число планов) можно нарушать всегда
OFF_PLAN_SHIFT_LEVEL = 2        # уровни 2-3: рамку можно нарушать, когда рынок сменил режим/направление против утреннего сценария; уровень 1: рамка жёсткая
_REG_CLASS = {"trend_up": ("trend", "long"), "trend_down": ("trend", "short"), "range": ("range", None), "storm": ("volatile", None)}


def regime_shift(day_regime, day_bias, regs):
    """Смена рынка против утреннего сценария по struct.reg монеты события и BTC -> reg или None.
    Сдвиг: класс режима другой (trend/range/volatile; из unclear в тренд), или тренд против bias плана (long/short)."""
    if day_regime not in ("trend", "range", "volatile", "unclear"):
        return None
    for r in regs:
        cls = _REG_CLASS.get(r)
        if not cls:
            continue
        if day_regime == "unclear":
            if cls[0] == "trend":
                return r
        elif cls[0] != day_regime or (cls[1] and day_bias in ("long", "short") and cls[1] != day_bias):
            return r
    return None


def off_plan_allowed(ctx):
    """Можно ли нарушить рамку плана дня (план = один из сценариев, а не запрет): уровни 4-5 всегда, уровни 2-3 при смене режима (regime_shift)."""
    lvl = ctx.get("risk_level") or 0
    return lvl >= OFF_PLAN_FREE_LEVEL or (lvl >= OFF_PLAN_SHIFT_LEVEL and bool(ctx.get("day_shift")))


def day_frame_breaks(o, ctx):
    """Коды рамки плана дня, которые нарушает ответ entry/1 (размер дня сюда не входит: он жёсткий всегда)."""
    out = []
    if ctx.get("day_max_new_plans") == 0:
        out.append("DAY_NO_PLANS")                           # план дня: новых планов нет
    if ctx.get("day_setups") and o.get("setup") not in ctx["day_setups"]:
        out.append("SETUP_NOT_ALLOWED")                      # сетап вне списка allowed_setups плана дня
    if o.get("sym") in (ctx.get("day_avoid") or []):
        out.append("SYM_AVOID")                              # монета в списке avoid плана дня
    mh, h = ctx.get("max_horizon"), o.get("horizon")
    if mh and h in SESSION_ORDER and SESSION_ORDER.index(h) > SESSION_ORDER.index(mh):
        out.append("HORIZON_ABOVE_DAY")                      # план дня ограничил горизонт на сегодня
    return out


def data_stale(ctx):
    """Устарели ли данные монеты события: возраст цены сканера/фида (data_age_s) выше порога max_data_age_s (по умолчанию MAX_DATA_AGE_S),
    или цены монеты нет ни в одном источнике (data_px=False). Нет метки времени при наличии цены = не устарели. Нет блока data в досье = проверки нет."""
    ctx = ctx or {}
    if ctx.get("data_px") is False:
        return True
    lim = _num(ctx.get("max_data_age_s"))
    lim = MAX_DATA_AGE_S if lim is None or lim <= 0 else lim
    age = ctx.get("data_age_s")
    return age is not None and age > lim


def entry_precheck(ctx):
    """Отказ entry/1 по данным досье ДО вызова модели (экономия токенов; те же коды, что дала бы смысловая проверка) -> код или None.
    WIDE_RANGE: диапазон 5м события шире максимального стопа типа сессии (для scalp любой range_5m-сигнал: по определению сканера диапазон >= 0.8%);
    DAY_NO_PLANS: план дня запрещает новые планы; NO_SLOTS / ROOM_CLOSED / SYM_BUSY / PAUSE_*: как в _entry;
    STALE_DATA: цена монеты события у сканера/фида старше max_data_age_s (data_stale), проверяется сразу после NO_DATA."""
    ctx = ctx or {}
    if not ctx.get("has_brief"):
        return None
    if not ctx.get("session_type"):
        return "NO_SESSION"                                  # сессии нет (день закрывается или не начат): входа не будет, модель не зовём (BEAM 2026-11-02: BAD_ENUM horizon)
    if ctx.get("struct_missing") or ctx.get("data_ready") is False:
        return "NO_DATA"                                     # нет структуры/уровней по монете события: без полного набора данных модель не зовём (UAI 2026-11-01)
    if data_stale(ctx):
        return "STALE_DATA"                                  # цена/данные сканера монеты события устарели (или их нет вовсе): модель не зовём (XPL 2026-11-02)
    if ctx.get("day_max_new_plans") == 0 and not ctx.get("off_plan_ok"):
        return "DAY_NO_PLANS"
    r5, ms = ctx.get("range_5m_pct"), ctx.get("max_stop_pct")
    if r5 is not None and ms is not None and r5 > ms and not _loose(ctx):
        return "WIDE_RANGE"
    if ctx.get("slots") is not None and ctx["slots"] <= 0:
        return "NO_SLOTS"
    if ctx.get("room_allowed") is False:
        return "ROOM_CLOSED"
    if ctx.get("sym") and ctx["sym"] in (ctx.get("busy") or []):
        return "SYM_BUSY"
    if (ctx.get("pause_sym") or 0) > 0:
        return "PAUSE_SYM"
    if (ctx.get("pause_all") or 0) > 0:
        return "PAUSE_ALL"
    if (ctx.get("min_size_sym") or 0) > 0:
        return "MIN_SIZE_SYM"                                # размер по риску не дотягивает до минимального лота монеты: план исполнитель всё равно отклонит
    cands = ctx.get("cands")
    if isinstance(cands, list) and cands and not any(isinstance(c, dict) and c.get("ok") for c in cands) and not ctx.get("stale_ids"):
        return "NO_CANDIDATES"                               # кандидаты есть, но ни один не годится (fail по числу): выбирать нечего, модель не зовём. Последняя проверка:
                                                             # при занятой монете cands=None (SYM_BUSY сработал раньше); при флаге idea_stale модель ещё может сделать es_revise
    return None


def price_keys(raw):
    """Ключи цен и параметров входа в сыром ответе entry_reply/1 (на любой глубине до 3) -> отсортированный список (пусто = чисто)."""
    out = set()

    def walk(o, d):
        if d > 3:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                if str(k).lower() in PRICE_KEYS:
                    out.add(str(k).lower())
                walk(v, d + 1)
        elif isinstance(o, list):
            for v in o[:20]:
                walk(v, d + 1)
    walk(raw, 0)
    return sorted(out)


def audit_context(inp):
    """Контекст audit/1 из входа day_auditor: {"day_id","items":[item...],"det":{item:"close|carry|cancel|keep"}}."""
    inp = inp if isinstance(inp, dict) else {}
    rows = inp.get("items") or inp.get("rows") or []
    items, det = [], {}
    for r in rows:
        if isinstance(r, dict) and isinstance(r.get("item"), str):
            items.append(r["item"])
            det[r["item"]] = r.get("decision")
    return {"day_id": inp.get("day_id"), "items": items, "det": det, "llm_unavailable": bool(inp.get("llm_unavailable"))}


# ------------------------------------------------------------------ смысловая проверка
def semantic(schema_id, obj, ctx=None):
    """Дополнительные проверки после схемы -> список кодов (пусто = ок). ctx строится из данных системы (pm_context/dayplan_context/audit_context)."""
    ctx = ctx or {}
    if _RE_STRONG.search(json.dumps(obj, ensure_ascii=False)):
        return ["SECRET_IN_ANSWER"]                  # ключ/длинный идентификатор в ответе: отклоняем и просим повторить без него
    if schema_id == "pm/1":
        return _pm(obj, ctx)
    if schema_id == "entry/1":
        return _entry(obj, ctx)
    if schema_id == "entry_reply/1":
        return _entry_reply(obj)
    if schema_id == "dayplan/1":
        return _dayplan(obj, ctx)
    if schema_id == "audit/1":
        return _audit(obj, ctx)
    if schema_id == "casereview/1":
        return _casereview(obj, ctx)
    return []


def _deny_keys(o, depth=0):
    """Ключи размера/плеча/маржи в патче amend (на любой глубине)."""
    if depth > 6:
        return []
    out = []
    if isinstance(o, dict):
        for k, v in o.items():
            if str(k).lower() in SIZE_KEYS:
                out.append(str(k)[:20])
            out += _deny_keys(v, depth + 1)
    elif isinstance(o, list):
        for v in o[:20]:
            out += _deny_keys(v, depth + 1)
    return out


def _entry_reply(o):
    """Обязательные и лишние поля ответа entry_reply/1 по decision (схема проверяет типы и диапазоны)."""
    need, only = {"propose": ("candidate", "setup", "thesis"), "skip": ("skip_code",)}.get(o.get("decision"), ()), {
        "propose": ("candidate", "setup", "thesis", "size_mult", "tags", "hypothesis"), "skip": ("skip_code", "watch")}.get(o.get("decision"), ())
    errs = ["ARGS_MISSING:%s" % k for k in need if k not in o]
    errs += ["ARGS_EXTRA:%s" % k for k in o if k not in ("decision", "why", "conf") and k not in only]
    return errs


def _pm(o, ctx):
    errs = []
    d, a = o.get("decision"), o.get("args") or {}
    need = PM_ARGS.get(d, ())
    for k in need:
        if k not in a:
            errs.append("ARGS_MISSING:%s" % k)
    for k in a:
        if k not in need:
            errs.append("ARGS_EXTRA:%s" % k)
    if d == "move_stop" and "to" in a and a["to"] != "be" and not (isinstance(a["to"], (int, float)) and a["to"] > 0):
        errs.append("RANGE:args.to")
    if d == "amend":
        if not a.get("patch"):
            errs.append("ARGS_MISSING:patch")
        elif _deny_keys(a["patch"]):
            errs.append("AMEND_SIZE_KEY")
    want = ctx.get("plan")
    if want and o.get("plan") != want:
        errs.append("PLAN_MISMATCH")
    hp = ctx.get("has_position")
    if ctx.get("unknown_plan") and d not in ("hold", "escalate"):
        errs.append("PLAN_NOT_IN_BRIEF")
    ets = set(ctx.get("event_types") or [])
    bl = ctx.get("blackout_min")
    if d == "release_plan" and (ets & {"connectivity", "reconcile_alert", "limit_hit", "kill"} or (bl is not None and bl <= 60 and not ctx.get("blackout_warn"))):
        errs.append("RELEASE_UNSAFE")                # пауза снимается только когда причина паузы ушла
    if "session_ending" in ets and ctx.get("carry_policy") == "never" and hp and d in ("hold", "hold_plan", "move_stop", "amend", "release_plan"):
        errs.append("CARRY_NOT_ALLOWED")             # политика never: к концу сессии позицию закрывают
    if hp is False and d in ("close", "partial_close"):
        errs.append("NO_POSITION")
    r = ctx.get("r")
    if d == "close" and hp and r is not None and SMALL_LOSS_R < r < 0 and not (ets & CLOSE_ANY_R_EVENTS):
        errs.append("HOLD_SMALL_LOSS")               # жёсткое правило 3 промпта кодом: убыток меньше 0.5R не повод закрывать (разбор POWR 2026-11-02: закрыто при -0.2R по шуму)
    if hp is True and d == "cancel":
        errs.append("CANCEL_IN_POSITION")
    if hp is True and d == "hold_plan":
        errs.append("HOLD_IN_POSITION")              # 2026-11-03: план в позиции на паузу не ставится (защита работает всегда); «ничего не делать» = hold
    if d == "move_stop" and hp is False and a.get("to") == "be":
        errs.append("NO_POSITION")
    to, side, px, sl, avg = a.get("to"), ctx.get("side"), ctx.get("px"), ctx.get("sl"), ctx.get("avg")
    if d == "move_stop" and isinstance(to, (int, float)) and not isinstance(to, bool) and side in ("long", "short"):
        sign = 1 if side == "long" else -1
        if sl is not None and (to - sl) * sign <= 0:
            errs.append("STOP_NOT_TIGHTER")
        if px is not None and (px - to) * sign <= 0:
            errs.append("STOP_WRONG_SIDE")
    if d == "move_stop" and to == "be" and avg is not None and sl is not None and side in ("long", "short"):
        if ((sl - avg) if side == "long" else (avg - sl)) >= 0:
            errs.append("STOP_NOT_TIGHTER")                 # стоп уже в безубытке или лучше
    return errs[:8]


def chase_errors(o, ctx, sign):
    """momentum_chase: уровень риска 4-5, импульс в сторону входа, вход без подтверждения (confirm none, зона у цены)."""
    errs = []
    if (ctx.get("risk_level") or 0) < CHASE_MIN_LEVEL:
        errs.append("CHASE_LEVEL")                           # на уровнях 1-3 догонять свечу нельзя
    if not chase_impulse(ctx, sign):
        errs.append("CHASE_NO_IMPULSE")                      # нет сильного импульса по направлению входа
    if o.get("confirm") != "none":
        errs.append("CHASE_CONFIRM")                         # догоняют по рынку сейчас, свечу не ждут
    return errs


def _entry(o, ctx):
    errs = []
    top = {k: v for k, v in o.items() if k != "size_mult"}
    if _deny_keys(top):
        errs.append("SIZE_KEY")                              # size/qty/leverage/margin и подобное модель не задаёт
    if ctx.get("kill") is True:
        errs.append("KILL")
    if o.get("decision") == "skip":
        extra = [k for k in ENTRY_PROPOSE_KEYS + ENTRY_OPTIONAL_KEYS if k in o]
        if extra:
            return (errs + ["SKIP_EXTRA:%s" % extra[0]])[:8]
        w = o.get("watch")
        if w is not None:                                    # пропуск с уровнем на будущее: станет идеей, на уровне агента разбудит idea_level
            sign = 1 if w["side"] == "long" else -1
            if not ((w["level"] - w["stop"]) * sign > 0 and (w["tp"] - w["level"]) * sign > 0):
                errs.append("WATCH_SIDE_PRICES")
            px = ctx.get("px")
            if px and abs(w["level"] - px) / px * 100.0 > ENTRY_MAX_LEVEL_DIST_LOOSE_PCT:
                errs.append("WATCH_LEVEL_FAR")
        return errs[:8]
    for k in ENTRY_PROPOSE_KEYS:
        if k not in o:
            errs.append("ARGS_MISSING:%s" % k)
    if "skip_code" in o:
        errs.append("ARGS_EXTRA:skip_code")
    if errs and any(e.startswith("ARGS_MISSING") for e in errs):
        return errs[:8]
    side, sym, lvl, stop, tps = o["side"], o["sym"], o["level"], o["stop"], o["tp"]
    want = ctx.get("sym")
    if want and sym != want:
        errs.append("SYMBOL_NOT_EVENT")                      # вход только по монете события сканера
    elif ctx.get("symbols") and sym not in ctx["symbols"]:
        errs.append("SYMBOL_UNKNOWN")
    if sym in (ctx.get("busy") or []):
        errs.append("SYM_BUSY")                              # по монете уже есть позиция или план: второй не подаём
    if ctx.get("slots") is not None and ctx["slots"] <= 0:
        errs.append("NO_SLOTS")
    if ctx.get("room_allowed") is False:
        errs.append("ROOM_CLOSED")
    if data_stale(ctx):
        errs.append("STALE_DATA")                            # цена сканера/фида по монете старше порога: план по устаревшим данным не подаём
    sign = 1 if side == "long" else -1
    ordered = all((b - a) * sign > 0 for a, b in zip(tps, tps[1:]))
    if not ((lvl - stop) * sign > 0 and all((t - lvl) * sign > 0 for t in tps) and ordered):
        errs.append("SIDE_PRICES")                           # long: stop < level < tp1 < tp2; short: зеркально
        return errs[:8]
    px = ctx.get("px")
    if px is not None:
        if (px - stop) * sign <= 0:
            errs.append("STOP_WRONG_SIDE")                   # цена уже за стопом
        if (tps[0] - px) * sign <= 0:
            errs.append("TP_REACHED")
        lvl_max = ENTRY_MAX_LEVEL_DIST_LOOSE_PCT if _loose(ctx) else ENTRY_MAX_LEVEL_DIST_PCT
        lvl_dist = abs(lvl - px) / px * 100.0
        if lvl_dist > lvl_max:
            errs += ["LEVEL_FAR", "LEVEL_DIST_PCT:%.1f_MAX:%.1f" % (lvl_dist, lvl_max)]      # числа в подсказке повтора: модель видит, что исправлять (только наши числа, не цитата ответа)
    pool = ctx.get("level_pool") or []
    if o.get("setup") != CHASE_SETUP:                        # momentum_chase входит по рынку у текущей цены, уровень ему не нужен
        if ctx.get("struct_missing") and not pool:
            errs.append("NO_LEVELS")                         # структуры и уровней по монете нет: уровень выдуман, вход только skip no_levels (UAI 2026-11-01)
        elif pool:
            near = min(pool, key=lambda p: abs(p - lvl))
            tol = max(LEVEL_POOL_TOL_PCT, 0.5 * (ctx.get("a5_pct") or 0.0))
            if abs(near - lvl) / lvl * 100.0 > tol:
                errs += ["LEVEL_NOT_IN_STRUCT", "LEVEL:%.6g_NEAREST:%.6g" % (lvl, near)]      # уровень не из struct/levels/ideas досье: взять уровень кода, не свой
    rr = _entry_rr(side, lvl, px, stop, tps)
    dist = abs(lvl - stop) / lvl * 100.0
    min_rr, max_stop, cost = ctx.get("min_rr"), ctx.get("max_stop_pct"), ctx.get("cost_rt_pct")
    hz, h = ctx.get("horizons"), o.get("horizon")
    if hz:                                                   # сессия-день: пороги по горизонту, который назвала модель
        geo = hz.get(h) or {}
        min_rr, max_stop = geo.get("min_rr"), geo.get("max_stop_pct")
        if h not in hz:
            errs.append("HORIZON_UNKNOWN")
    elif ctx.get("session_type") in SESSION_ORDER and h != ctx["session_type"]:
        errs.append("HORIZON_MISMATCH")                      # сессия одного горизонта: план только этого горизонта (шлюз исполнителя отклонит иначе)
    if not ctx.get("off_plan_ok"):
        errs += day_frame_breaks(o, ctx)                     # рамка плана дня жёсткая только на уровне 1 и на 2-3 без смены режима (off_plan_allowed)
    if rr is None:
        errs.append("RR_LOW")
    elif min_rr is not None and cost is not None:
        # как guard: RR после издержек круга, выигрыш уменьшается на cost, проигрыш растёт на cost (в % от уровня); порог = min_rr типа сессии
        net = (rr * dist - cost) / (dist + cost)
        if net < min_rr - 1e-9:
            errs += ["RR_LOW", "RR_NET:%.2f_NEED:%.2f" % (net, min_rr)]
    elif rr < ENTRY_MIN_RR - 1e-9:
        errs += ["RR_LOW", "RR_GROSS:%.2f_NEED:%.2f" % (rr, ENTRY_MIN_RR)]
    floor = stop_floor_pct(ctx, o.get("entry_type"))
    if dist < floor:
        errs += ["STOP_TIGHT", "STOP_PCT:%.2f_MIN:%.2f" % (dist, floor)]      # стоп не вплотную: не ближе 1.5 ATR(5м), трёх спредов, двух издержек круга и 0.1%
    surge, ret5 = ctx.get("surge_x"), ctx.get("ret_5m")
    if o.get("setup") == CHASE_SETUP:
        errs += chase_errors(o, ctx, sign)
    if o.get("setup") in FADE_SETUPS and surge is not None and ret5 and surge >= FADE_SURGE_X and ret5 * sign < 0:
        errs += ["FADE_IMPULSE", "SURGE_X:%.1f_MAX:%.1f" % (surge, FADE_SURGE_X)]      # фейд против импульса на всплеске оборота: пропускают или ждут отката/сетапа с реакцией
    elif ret5 and ret5 * sign < 0 and o.get("setup") not in REACTION_SETUPS + (CHASE_SETUP,):
        a5 = ctx.get("a5_pct")
        imp = abs(ret5) / a5 if a5 else None
        if (imp is not None and imp >= FADE_IMPULSE_ATR) or (imp is None and abs(ret5) >= 1.5):
            errs += ["FADE_IMPULSE", "IMPULSE_ATR:%.1f_MAX:%.1f" % (imp if imp is not None else abs(ret5), FADE_IMPULSE_ATR)]      # вход против свечи события от 2 ATR без реакции (UAI 2026-11-01)
    mood = ctx.get("crowd_mood")
    if (mood == "panic" and sign > 0) or (mood == "euphoria" and sign < 0):
        if o.get("setup") not in REACTION_SETUPS:
            errs.append("CROWD_AGAINST")                     # против паники/эйфории толпы только сетапы после реакции цены (sweep_reject, level_reclaim)
        if o.get("confirm") != "candle":
            errs.append("CROWD_NO_CONFIRM")                  # и только со свечным подтверждением
    stop_max = max_stop if max_stop is not None else ENTRY_MAX_STOP_PCT
    wide = exec_stop_pct(lvl, px, stop, o.get("entry_type"))
    if wide > stop_max + 1e-9:
        errs += ["STOP_WIDE", "STOP_PCT:%.2f_MAX:%.2f" % (wide, stop_max)]     # шире допустимого для типа сессии (guard отклонит так же): пропускают, а не сжимают
    dsm = ctx.get("day_size_mult")
    if dsm is not None and dsm < 1.0 and not (isinstance(o.get("size_mult"), (int, float)) and o["size_mult"] <= dsm + 1e-9):
        errs.append("DAY_SIZE")                              # план дня ограничил размер: size_mult обязателен и не выше потолка дня
    if ctx.get("range_5m_pct") is not None and ctx["range_5m_pct"] > dist and not (_loose(ctx) and o.get("confirm") == "candle"):
        errs.append("RANGE5_GT_STOP")                        # диапазон 5-минутной свечи шире стопа: пропускают (на уровнях 4-5 не для входа с подтверждением свечой: он ждёт новую свечу)
    tags = set(o.get("tags") or [])
    sm = o.get("size_mult")
    bl = ctx.get("blackout_min")
    in_window = bl is not None and bl <= 60
    if in_window:
        if not ctx.get("blackout_warn"):
            errs.append("BLACKOUT")
        else:                                               # paper + warn: только размер не выше потолка и тег blackout
            if not (isinstance(sm, (int, float)) and sm <= BLACKOUT_MAX_SIZE_MULT + 1e-9):
                errs.append("BLACKOUT_SIZE")
            if "blackout" not in tags:
                errs.append("BLACKOUT_TAG")
    elif "blackout" in tags:
        errs.append("TAG_MISMATCH:blackout")
    if o.get("entry_type") == "limit" and o.get("confirm") != "candle":
        errs.append("LIMIT_NEEDS_CANDLE")                    # лимит по уровню ставится после закрытия свечи за уровнем (цена на стороне отката, post_only не пересекает рынок)
    if o.get("confirm") == "none":
        cap = ENTRY_SIZE_MULT_MAX if ctx.get("no_confirm_max") is None else min(ENTRY_SIZE_MULT_MAX, ctx["no_confirm_max"])
        if ctx.get("no_confirm_allowed") is False:
            errs.append("NO_CONFIRM_FORBIDDEN")              # уровень риска запрещает вход без подтверждения
        if not (isinstance(sm, (int, float)) and sm <= cap + 1e-9):
            errs.append("NO_CONFIRM_SIZE")
        if "no-confirm" not in tags:
            errs.append("NO_CONFIRM_TAG")
        if (ctx.get("stop_streak") or 0) >= 2:
            errs.append("NO_CONFIRM_AFTER_STOPS")            # после двух стопов подряд только со свечным подтверждением
        z = o.get("zone")
        if not isinstance(z, dict) or not (z["low"] <= lvl <= z["high"]) or (z["high"] - z["low"]) / lvl * 100.0 > ENTRY_MAX_ZONE_PCT:
            errs.append("ZONE_REQUIRED")                     # без подтверждения вход только внутри узкой зоны вокруг уровня
    elif "no-confirm" in tags:
        errs.append("TAG_MISMATCH:no-confirm")
    if (ctx.get("pause_sym") or 0) > 0:
        errs.append("PAUSE_SYM")                             # 30 минут после стопа по монете
    if (ctx.get("pause_all") or 0) > 0:
        errs.append("PAUSE_ALL")                             # час после двух стопов подряд
    if (ctx.get("min_size_sym") or 0) > 0:
        errs.append("MIN_SIZE_SYM")                          # исполнитель недавно отказал по минимальному лоту монеты
    return errs[:8]


def _dayplan(o, ctx):
    errs = []
    lim = o.get("limits") or {}
    if lim.get("max_new_plans", 0) > 0 and o.get("regime") == "unclear":
        errs.append("UNCLEAR_WITH_PLANS")
    syms = set(ctx.get("symbols") or [])
    if ctx.get("symbols") is not None and not syms:
        syms = {"-"}                                     # досье без символов: любой символ в плане неизвестен
    if ctx.get("data_ok") is False and (lim.get("max_new_plans", 0) > 0 or o.get("watch")):
        errs.append("NO_DATA_PLANS")                     # нет рынка/цен в досье: план «не торговать»
    seen, avoid = set(), set(o.get("avoid") or [])
    for w in o.get("watch") or []:
        if syms and w.get("sym") not in syms:
            errs.append("SYMBOL_UNKNOWN:%s" % str(w.get("sym"))[:12])
        key = (w.get("sym"), w.get("side"), w.get("setup"))
        if key in seen:
            errs.append("DUP_WATCH:%s" % str(w.get("sym"))[:12])
        seen.add(key)
        if w.get("sym") in avoid:
            errs.append("AVOID_IN_WATCH:%s" % str(w.get("sym"))[:12])
        px = (ctx.get("prices") or {}).get(w.get("sym"))
        if px and isinstance(w.get("level"), (int, float)):
            dist = abs(w["level"] - px) / px * 100.0
            if dist > ctx.get("max_level_distance_pct", 12.0):
                errs.append("LEVEL_FAR:%s" % str(w.get("sym"))[:12])
        bias = o.get("bias")
        if bias in ("long", "short") and w.get("side") != bias and w.get("setup") not in OPPOSITE_OK:
            errs.append("SIDE_VS_BIAS:%s" % str(w.get("sym"))[:12])
    if syms:
        for k in (lim.get("symbols") or []):
            if k not in syms:
                errs.append("SYMBOL_UNKNOWN:%s" % str(k)[:12])
    bl = ctx.get("blackout_min")
    if bl is not None and bl <= 60 and lim.get("max_new_plans", 0) > 0:
        if not ctx.get("blackout_warn"):
            errs.append("BLACKOUT_PLANS")
        else:                      # paper + warn: планы допустимы только с размером не выше потолка и кодом риска blackout_warn
            if not (isinstance(lim.get("size_mult"), (int, float)) and lim["size_mult"] <= BLACKOUT_MAX_SIZE_MULT + 1e-9):
                errs.append("BLACKOUT_SIZE")
            if BLACKOUT_RISK_CODE not in (o.get("risks") or []):
                errs.append("BLACKOUT_RISK_CODE")
    sc_ = ctx.get("size_cap")
    if sc_ is not None and isinstance(lim.get("size_mult"), (int, float)) and lim["size_mult"] > sc_:
        errs.append("SIZE_ABOVE_CAP")
    cap = ctx.get("limits") or {}
    for k in ("max_new_plans", "max_risk_usdt", "size_mult"):
        if isinstance(cap.get(k), (int, float)) and isinstance(lim.get(k), (int, float)) and lim[k] > cap[k]:
            errs.append("LIMIT_ABOVE:%s" % k)
    st = (o.get("session") or {}).get("type")
    own = ctx.get("session_type")
    if own in SESSION_ORDER and st in SESSION_ORDER and SESSION_ORDER.index(st) > SESSION_ORDER.index(own):
        errs.append("SESSION_ABOVE_OWNER")
    if ctx.get("carry_policy") == "never" and (o.get("carry") or {}).get("allow"):
        errs.append("CARRY_NOT_ALLOWED")
    return errs[:8]


def _audit(o, ctx):
    errs = []
    items, det = set(ctx.get("items") or []), ctx.get("det") or {}
    if ctx.get("llm_unavailable") and (o.get("carry") or o.get("changes")):
        errs.append("LLM_UNAVAILABLE_ACTIONS")           # при недоступном агенте чек-лист не меняется
    if ctx.get("day_id") and o.get("day_id") != ctx["day_id"]:
        errs.append("DAY_MISMATCH")
    seen = set()
    for c in o.get("carry") or []:
        it = c.get("item")
        if items and it not in items:
            errs.append("UNKNOWN_ITEM")
        if it in seen:
            errs.append("DUP_ITEM")
        seen.add(it)
        if c.get("decision") == "carry":
            if not c.get("basis"):
                errs.append("CARRY_NO_BASIS")
            if det.get(it) in ("close", "cancel"):
                errs.append("CARRY_OVERRIDES_CLOSE")           # LLM может только ужесточить детерминированное решение
    for ch in o.get("changes") or []:
        it = ch.get("item")
        if items and it not in items:
            errs.append("UNKNOWN_ITEM")
        if ch.get("decision") == "carry" and det.get(it) in ("close", "cancel"):
            errs.append("CARRY_OVERRIDES_CLOSE")
    for q in o.get("owner_questions") or []:
        if items and q.get("item") not in items:
            errs.append("UNKNOWN_ITEM")
    return sorted(set(errs))[:8]


def _codes(v, allowed, limit):
    out = []
    for x in v if isinstance(v, list) else []:
        if x in allowed and x not in out:
            out.append(x)
    return out[:limit]


def repair_casereview(o, ctx):
    """Вывод ревьюера до проверки схемы: из списков кодов убираются значения вне перечня и признаки, которых по ситуации не было
    (known_at_decision знает код, не модель: 2026-10-31/11-01 BAD_ENUM right[0] и KNOWN_FALSE:no_confirm съедали повторы), «none» в improve
    вместе с другими кодами убирается, пустой improve = ["none"], неизвестное действие альтернативы = "none". -> новый объект."""
    o = dict(o)
    o["right"] = _codes(o.get("right"), CR_RIGHT, 5)
    imp = _codes(o.get("improve"), CR_IMPROVE, 5)
    if len(imp) > 1 and "none" in imp:
        imp.remove("none")
    o["improve"] = imp or ["none"]
    known = (ctx or {}).get("known")
    kn = _codes(o.get("known_at_decision"), CR_KNOWN, 8)
    o["known_at_decision"] = [k for k in kn if known.get(k) is True] if isinstance(known, dict) else kn
    alt = o.get("alternative")
    if isinstance(alt, dict) and alt.get("action") not in CR_ALT:
        o["alternative"] = dict(alt, action="none")
    return o


REPAIRS = {"casereview/1": repair_casereview}


def _casereview(o, ctx):
    errs = []
    known = ctx.get("known")                                     # {признак: True|False|None} из situation (memory.review.known_features)
    for k in o.get("known_at_decision") or []:
        if isinstance(known, dict) and known.get(k) is not True:
            errs.append("KNOWN_FALSE:%s" % k)
    imp = o.get("improve") or []
    if "none" in imp and len(imp) > 1:
        errs.append("IMPROVE_NONE_MIXED")
    sv = ctx.get("suggested_verdict")
    if o.get("verdict") in ("unlucky", "lucky") and ("suggested_verdict" in ctx) and sv != o.get("verdict"):
        errs.append("VERDICT_NOT_SUPPORTED")
    return errs[:8]


# ------------------------------------------------------------------ секреты
_RE_KEYLIKE = re.compile(r"\b(?:sk|pk|key|tok|api)[-_][A-Za-z0-9_\-]{8,}\b", re.I)
_RE_LONG = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
_RE_STRONG = re.compile(r"\b(?:sk|pk)[-_][A-Za-z0-9_\-]{12,}|[A-Za-z0-9+/=]{40,}|\b[A-Fa-f0-9]{32,}\b")        # для ответов ролей: только явные ключи (без ложных срабатываний на коды)


def redact(text, limit=None):
    """Строки, похожие на ключи, заменяются на «[скрыто]»; длинные идентификаторы (hex/base64 от 32 символов) тоже."""
    s = text if isinstance(text, str) else str(text)
    s = _RE_KEYLIKE.sub("[скрыто]", s)
    s = _RE_LONG.sub("[скрыто]", s)
    return s[:limit] if limit else s


def has_secret(text):
    s = text if isinstance(text, str) else str(text)
    return bool(_RE_KEYLIKE.search(s) or _RE_LONG.search(s))
