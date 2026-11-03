"""Извлечение признаков ситуации из записей журналов (08 п. 2.1). Чистые функции над dict, файлов не читают.

Правила: недоступное = None (не 0) и имя в situation.missing; признак, восстановленный ПОСЛЕ решения (тег no-confirm из разбора сделки),
помечается в situation.retro и не принимается валидатором ревью «known_at_decision». Текст заметок/тезисов НЕ парсится для числовых признаков:
берутся только числовые поля записи, ctx, теги. why = собственный текст системы, очищенный и обрезанный до 200.
"""
import json
import re
import time

from tb.core import plan_schema
from tb.memory import cases, privacy

CTX_NUM = ("range_5m_pct", "range_15m_pct", "ret_5m_pct", "turn_surge_5m", "chg_24h_pct", "spread_bps", "funding_pct")
NON_FEATURES = ("sym", "side", "hyp", "missing", "retro")
RE_MECH = re.compile(r"(?i)механик|mechanic")


def num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def base_sym(sym):
    s = str(sym or "").upper().strip()
    return s[:-4] if s.endswith("USDT") and len(s) > 4 else s


def utc_session(hour):
    """asia 22-07, europe 07-13, overlap 13-16 (Европа+США), us 16-22."""
    if hour is None:
        return None
    if hour >= 22 or hour < 7:
        return "asia"
    if hour < 13:
        return "europe"
    return "overlap" if hour < 16 else "us"


def time_feats(ts):
    t = cases.parse_ts(ts)
    if t is None:
        return {"utc_hour": None, "dow": None, "utc_session": None}
    g = time.gmtime(t)
    return {"utc_hour": g.tm_hour, "dow": g.tm_wday, "utc_session": utc_session(g.tm_hour)}


def classify_coin(sym, cfg, turnover_usd=None):
    """Справочник из config/memory.json; для неизвестных монет по обороту (если он дан), иначе unknown."""
    cc = cfg.get("coin_class") or {}
    b = base_sym(sym)
    for cls in ("major", "stock_token", "meme", "alt_mid"):
        if b in (cc.get(cls) or []):
            return cls
    t = num(turnover_usd)
    if t is None:
        return "unknown"
    return "alt_mid" if t >= (cc.get("turnover_mid_usd") or 2e8) else ("alt_small" if t >= (cc.get("turnover_small_usd") or 2e7) else "meme")


def stop_pct(entry, sl):
    e, s = num(entry), num(sl)
    return round(abs(e - s) / e * 100.0, 2) if e and s else None


def rr_planned(entry, sl, tp):
    e, s, t = num(entry), num(sl), num(tp)
    return round(abs(t - e) / abs(e - s), 2) if e and s and t and e != s else None


def parse_tags(tags):
    """Список тегов журнала/плана -> {setup, exp, var, hyp, plan, model, agent_id, mem, no_confirm, manual, testnet, flags:[...]}. Разделитель ':' или '.'."""
    out = {"setup": None, "exp": None, "var": None, "hyp": None, "plan": None, "model": None, "agent_id": None, "mem": None,
           "no_confirm": False, "manual": False, "testnet": False, "mechanics": False, "blackout": False}
    keys = {"setup": "setup", "exp": "exp", "var": "var", "hyp": "hyp", "plan": "plan", "model": "model", "agent": "agent_id", "mem": "mem"}
    for tg in tags or []:
        if not isinstance(tg, str):
            continue
        t = tg.strip()
        m = re.match(r"^([a-z_]+)[:.](.+)$", t)
        if m and m.group(1) in keys:
            out[keys[m.group(1)]] = m.group(2)
        elif t == "no-confirm":
            out["no_confirm"] = True
        elif t == "manual-pick":
            out["manual"] = True
        elif t == "testnet":
            out["testnet"] = True
        elif t == "blackout":
            out["blackout"] = True          # вход в макро-окне (paper, blackout_mode=warn), ставит исполнитель
        elif t in ("mechanics", "mechanic"):
            out["mechanics"] = True
    if out["setup"] not in cases.SETUPS:
        out["setup"] = None
    if out["mem"] not in cases.MEM:
        out["mem"] = None
    return out


def finalize(sit, retro=()):
    """Дополняет situation: незаполненные признаки = None, missing = их имена, retro = ретроспективные поля."""
    sit = dict(sit)
    for k in cases.SITUATION:
        sit.setdefault(k, None)
    sit["missing"] = sorted(k for k in cases.SITUATION if sit.get(k) is None and k not in NON_FEATURES)
    sit["retro"] = sorted(set(retro))
    return sit


def _ctx_part(ctx):
    ctx = ctx if isinstance(ctx, dict) else {}
    out = {k: num(ctx.get(k)) for k in CTX_NUM}
    reg = ctx.get("regime")
    out["regime"] = reg if reg in cases.REGIMES else None
    return out


def _setup(tags, op):
    s = tags["setup"] or op.get("setup")
    return s if s in cases.SETUPS else ("manual" if tags["manual"] else None)


def situation_from_trade(op, post, cfg):
    """op = open-запись журнала (со слитыми close/post полями или без), post = поля разбора (err/rule_followed) или None -> situation (finalize)."""
    tags = parse_tags(op.get("tags"))
    ctx = op.get("ctx") if isinstance(op.get("ctx"), dict) and op.get("ctx") else op.get("plan_ctx")
    sit = _ctx_part(ctx)
    reg = op.get("regime")
    if sit.get("regime") is None and reg in cases.REGIMES:
        sit["regime"] = reg
    t = time_feats(op.get("ts_open"))
    sit.update(t)
    sit.update({"sym": base_sym(op.get("sym")), "side": op.get("side") if op.get("side") in cases.SIDES else None,
                "coin_class": classify_coin(op.get("sym"), cfg), "stop_pct": stop_pct(op.get("entry"), op.get("sl")),
                "rr_planned": rr_planned(op.get("entry"), op.get("sl"), op.get("tp")),
                "setup": _setup(tags, op),
                "hyp": (op.get("hyp") or tags["hyp"] or None), "size_mult": num(op.get("size_mult")), "blackout": (True if tags["blackout"] else None)})
    retro = []
    errs = (post or {}).get("err") or []
    if tags["no_confirm"] or "no-confirm" in errs:
        sit.update({"confirmed": False, "confirm_kind": "none", "no_confirm_tag": True})
        if not tags["no_confirm"]:
            retro += ["confirmed", "confirm_kind", "no_confirm_tag"]     # знание получено из разбора после сделки
    else:
        sit.update({"confirmed": None, "confirm_kind": "unknown", "no_confirm_tag": None})
    return finalize(sit, retro)


def stop_vs_range5(sit):
    s, r = sit.get("stop_pct"), sit.get("range_5m_pct")
    return round(s / r, 2) if s and r else None


def situation_from_idea(idea, cfg):
    """Идея -> situation только из числовых полей записи (текст note не парсится)."""
    ref = num(idea.get("level")) or num(idea.get("price"))
    sit = {"sym": base_sym(idea.get("sym")), "side": idea.get("side") if idea.get("side") in cases.SIDES else None,
           "coin_class": classify_coin(idea.get("sym"), cfg), "stop_pct": stop_pct(ref, idea.get("stop")),
           "rr_planned": rr_planned(ref, idea.get("stop"), idea.get("tp")), "confirm_kind": "unknown",
           "setup": idea.get("tag") if idea.get("tag") in cases.SETUPS else None}
    sit.update(time_feats(idea.get("ts")))
    return finalize(sit)


def situation_from_outcome(row, cfg):
    """Строка hot_outcomes (событие сканера) -> situation сигнала: только числовые поля строки."""
    sit = {"sym": base_sym(row.get("sym")), "side": "long" if row.get("dir") == "up" else ("short" if row.get("dir") == "down" else "watch"),
           "coin_class": classify_coin(row.get("sym"), cfg), "range_5m_pct": num(row.get("range_5m_pct")), "turn_surge_5m": num(row.get("turn_surge_5m")),
           "confirm_kind": "unknown", "setup": None}
    sit.update(time_feats(row.get("ts")))
    return finalize(sit)


def apply_brief(sit, brief):
    """Пополняет situation полями досье (если есть): fng, dvol (по символу), blackout, regime. Ничего не затирает."""
    if not isinstance(brief, dict):
        return sit
    sit = dict(sit)
    sent = brief.get("sentiment") if isinstance(brief.get("sentiment"), dict) else {}
    fng = sent.get("fng") if isinstance(sent.get("fng"), dict) else {}
    mk = brief.get("market") if isinstance(brief.get("market"), dict) else {}
    cand = {"fng": num(fng.get("v")), "regime": mk.get("regime") if mk.get("regime") in cases.REGIMES else None,
            "blackout": (bool(mk.get("blackout")) if "blackout" in mk else None),
            "dvol": num((sent.get("dvol") or {}).get(sit.get("sym"))) if isinstance(sent.get("dvol"), dict) else None,
            "btc_ret_1h_pct": num(mk.get("btc_ret_1h_pct"))}
    snap = brief.get("snap") if isinstance(brief.get("snap"), dict) else {}       # brain/brief.save: fng, dvol, risk_off, blackout на момент досье (есть и в урезанном досье)
    for k, v in (("fng", num(snap.get("fng"))), ("dvol", num(snap.get("dvol"))),
                 ("risk_off", snap.get("risk_off") if isinstance(snap.get("risk_off"), bool) else None),
                 ("blackout", snap.get("blackout") if isinstance(snap.get("blackout"), bool) else None)):
        if cand.get(k) is None:
            cand[k] = v
    for k, v in cand.items():
        if sit.get(k) is None and v is not None:
            sit[k] = int(v) if k == "fng" else v
    return finalize({k: v for k, v in sit.items() if k not in ("missing", "retro")}, sit.get("retro") or [])


EVENT_FILL = ("range_5m_pct", "ret_5m_pct", "spread_bps", "funding_pct", "turn_surge_5m", "chg_24h_pct")


def apply_event(sit, d):
    """Пополняет пустые рыночные признаки (range_5m_pct, spread_bps, funding_pct, turn_surge_5m, chg_24h_pct, ret_5m_pct) числами события сканера d (поле d события шины).
    Ничего не затирает: приоритет у ctx входа."""
    if not isinstance(d, dict):
        return sit
    sit = dict(sit)
    for k in EVENT_FILL:
        v = num(d.get(k))
        if sit.get(k) is None and v is not None:
            sit[k] = v
    return finalize({k: v for k, v in sit.items() if k not in ("missing", "retro")}, sit.get("retro") or [])


def apply_plan(sit, spec, fired=()):
    """confirmed/confirm_kind из плана (R1, Requirement 11.2): plan_schema.has_confirm(entry.when) - подтверждение свечой/объёмом есть на всех ветках;
    плана нет - по событиям trigger_fired плана (fired = их cond: 'candle_close', 'volume_spike'...). Уже известное (тег no-confirm, разбор) не затирается."""
    if sit.get("confirmed") is not None:
        return sit
    conf = None
    if isinstance(spec, dict) and isinstance(spec.get("entry"), dict):
        conf = bool(plan_schema.has_confirm(spec["entry"].get("when")))
    elif any(isinstance(c, str) and (c.startswith("candle") or c == "volume_spike") for c in fired or ()):
        conf = True
    if conf is None:
        return sit
    sit = dict(sit, confirmed=conf, confirm_kind="candle_close" if conf else "none")
    return finalize({k: v for k, v in sit.items() if k not in ("missing", "retro")}, sit.get("retro") or [])


# ---------------------------------------------------------------- признаки правил R2 (Requirements 7.10, 7.13)
RULE_EVENT_NUM = ("range_5m_pct", "ret_5m_pct", "turn_surge_5m", "chg_24h_pct", "spread_bps", "funding_pct")
RULE_SCOPE_KEYS = ("ev_kind", "side_rel")       # не признаки условий: вид события и сторона относительно направления сигнала для scope
_DIR_SIDE = {"up": "long", "long": "long", "down": "short", "short": "short"}


def _num8(x):
    """Число как его хранит шина (bus._num: не длиннее 8 значащих цифр), всегда float -> паритет меток и шины. Нечисло/bool -> None."""
    if isinstance(x, bool):
        return None
    v = num(x)
    return None if v is None else float("%.8g" % v)


def rule_event(event):
    """Событие сканера в одном виде -> {"sym", "ts", "kind", "dir", "f"} или None.

    Принимает (а) конверт шины {"type": "scanner_signal", "ts": ISO, "sym", "d": {...}} — сторона мозга (событие из resolve_event);
    (б) сигнал меток {"t0", "sym", "type", "side", "ev": dict|JSON} — строка `signals` при записи `cluster_runs`. Иное -> None."""
    if not isinstance(event, dict):
        return None
    if isinstance(event.get("d"), dict):
        if event.get("type") != "scanner_signal":
            return None
        d = event["d"]
        return {"sym": event.get("sym"), "ts": cases.parse_ts(event.get("ts")), "kind": d.get("kind"), "dir": _DIR_SIDE.get(d.get("dir")), "f": d}
    if "ev" in event:
        ev = event.get("ev")
        if isinstance(ev, str):
            try:
                ev = json.loads(ev)
            except ValueError:
                ev = None
        t0 = num(event.get("t0"))
        return {"sym": event.get("sym"), "ts": t0, "kind": event.get("type"), "dir": _DIR_SIDE.get(event.get("side")),
                "f": ev if isinstance(ev, dict) else {}}
    return None


def _cand_side(cand):
    s = cand.get("side") if isinstance(cand, dict) else cand
    return s if s in ("long", "short") else None


def rule_features(event, cand, cfg):
    """Девять признаков правил (tb.logic.learn_cond.RULE_FEATURES) + RULE_SCOPE_KEYS -> {имя: значение|None}. Одна функция для меток и мозга.

    event — см. rule_event (None/не scanner_signal -> все признаки события None); cand — кандидат, план или прогон ({"side"} или строка стороны);
    cfg — config/memory.json. Числа только из полей события (spread_bps — только d.spread_bps, не struct.sp); coin_class = classify_coin(sym, cfg,
    turn_24h_musd × 1e6), а монета вне справочника без оборота -> None (не unknown); utc_session по времени события; side_rel = with_dir|against_dir|None."""
    side = _cand_side(cand)
    out = {"coin_class": None, "utc_session": None, "side": side}
    out.update((k, None) for k in RULE_EVENT_NUM)
    out.update((k, None) for k in RULE_SCOPE_KEYS)
    e = rule_event(event)
    if e is None:
        return out
    f = e["f"]
    for k in RULE_EVENT_NUM:
        out[k] = _num8(f.get(k))
    sym = e["sym"] if isinstance(e["sym"], str) and e["sym"] else None
    if sym:
        tm = _num8(f.get("turn_24h_musd"))
        cc = classify_coin(sym, cfg if isinstance(cfg, dict) else {}, tm * 1e6 if tm is not None else None)
        out["coin_class"] = None if cc == "unknown" else cc
    if e["ts"] is not None:
        out["utc_session"] = utc_session(time.gmtime(e["ts"]).tm_hour)
    out["ev_kind"] = e["kind"] if isinstance(e["kind"], str) and e["kind"] else None
    if side and e["dir"]:
        out["side_rel"] = "with_dir" if side == e["dir"] else "against_dir"
    return out


def policy_scope(policy):
    """Область применимости по записи политики проверки (config/shadow_policies.json, для P2: kinds, side=with_dir, entry) -> scope правила."""
    p = policy if isinstance(policy, dict) else {}
    return {"policy": p.get("id"), "kinds": list(p.get("kinds") or []), "side": p.get("side"), "entry": p.get("entry")}


def in_scope(feats, scope):
    """Решение в области применимости: вид события из scope.kinds ("*" — любой) и сторона относительно направления сигнала равна scope.side.
    Нет события, вида или направления -> False (правило вне области не срабатывает)."""
    if not isinstance(feats, dict) or not isinstance(scope, dict):
        return False
    kinds = scope.get("kinds") or []
    kind = feats.get("ev_kind")
    if kind is None or ("*" not in kinds and kind not in kinds):
        return False
    want = scope.get("side")
    if want in (None, "any"):
        return True
    return feats.get("side_rel") == want


def why_text(s):
    """-> (очищенный why <= 200, подозрение на инъекцию)."""
    return privacy.safe_text(s, 200)


EXIT_MAP = {"sl": "sl", "tp": "tp", "manual": "manual", "timeout": "timeout", "invalidation": "invalidation", "rule": "rule", "kill": "kill",
            "session_end": "session_end"}


def result_from_close(op, cl):
    """open + close (слитые поля) -> result. r берётся из журнала; без r и без risk -> r=None."""
    r = num(cl.get("r"))
    risk = num(cl.get("risk_usdt")) or num(op.get("risk_usdt"))
    if r is None and num(cl.get("pnl_usdt")) is not None and risk:
        r = num(cl["pnl_usdt"]) / risk
    path = cl.get("path") if isinstance(cl.get("path"), dict) else {}
    t0, t1 = cases.parse_ts(op.get("ts_open")), cases.parse_ts(cl.get("ts_close"))
    fees = num(cl.get("fees_usdt"))
    return {"status": "closed", "r": r, "pnl_r_net": r, "mae_r": num(path.get("mae_r")), "mfe_r": num(path.get("mfe_r")),
            "hold_min": round((t1 - t0) / 60.0, 1) if t0 is not None and t1 is not None and t1 >= t0 else None,
            "exit_reason": EXIT_MAP.get(cl.get("reason")), "fees_r": round(fees / risk, 3) if fees is not None and risk else None}


def whatif_from_outcomes(rows, h_pref=15):
    """Строки hot_outcomes одной идеи/события -> whatif (горизонт h_pref, иначе минимальный) или None."""
    rows = [r for r in rows if isinstance(r, dict) and num(r.get("h"))]
    if not rows:
        return None
    pick = [r for r in rows if int(r["h"]) == int(h_pref)] or [min(rows, key=lambda r: r["h"])]
    r = pick[-1]
    d, c = num(r.get("dir_pct")), num(r.get("cost_pct"))
    return {"h_min": int(r["h"]), "dir_pct": d, "ret_pct": num(r.get("ret_pct")), "cost_pct": c,
            "net_dir_pct": round(d - c, 4) if d is not None and c is not None else None, "src": "hot_outcomes", "outcome_id": str(r.get("id"))[:48]}


def is_mechanics(op):
    tags = parse_tags(op.get("tags"))
    return bool(tags["mechanics"] or RE_MECH.search(str(op.get("thesis") or "")))
