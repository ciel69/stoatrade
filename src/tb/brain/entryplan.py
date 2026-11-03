"""План tb.plan/1 из ответа entry/1 (роль entry_scout). Чистый модуль: без файлов и сети.

Размер, плечо и маржу модель не задаёт и здесь не появляются: их считает guard исполнителя по риску плана. Режим плана ставит КОД, не ответ модели:
ctx["mode"] = режим дня платформы (paper по умолчанию); live/testnet возможны только когда у агента есть такой режим в agents.json и владелец включил торговый флаг дня
(оба условия проверяет шлюз: E_MODE_FORBIDDEN, E_TRADING_*). Правила входа одни для всех режимов.
Подтверждение входа: confirm=candle -> условие candle_close 5м за уровнем; confirm=none -> price_in_zone по узкой зоне, risk.confirm_waiver и size_mult <= 0.5 (проверено в validate).
"""
import re

from tb.brain import validate
from tb.brain.validate import day_frame_breaks
from tb.core import hyps
from tb.logic.geometry import MAX_HOLD_MIN  # noqa: F401  (срок удержания по горизонту живёт в logic.geometry; имя прежнее)

ES_TTL = {"scalp": 30, "intraday": 60, "swing": 240, "position": 240}          # ttl_min развёрнутого объекта по горизонту кандидата
ES_FAIL_SKIP = {"stop_wide": "wide_stop", "range_gt_stop": "wide_stop"}       # fail кандидата -> skip_code при es_watch
MAX_TAGS = 2                                                                  # лимит tags схемы entry/1


def merge_tags(*lists):
    """Теги кандидата, затем модели, без повторов, не больше MAX_TAGS (Requirement 9.8)."""
    out = []
    for lst in lists:
        for t in lst or []:
            if t in validate.ENTRY_TAGS and t not in out:
                out.append(t)
    return out[:MAX_TAGS]


def reply_call(reply):
    """Проверенный ответ entry_reply/1 -> (имя вызова es_*, аргументы) для expand: propose -> es_take, skip с watch -> es_watch (skip_code модели), иначе es_skip."""
    a = {"why": reply.get("why"), "conf": reply.get("conf")}
    if reply.get("decision") == "propose":
        a.update({k: reply[k] for k in ("candidate", "setup", "thesis", "size_mult", "tags", "hypothesis") if k in reply})
        return "es_take", a
    w = reply.get("watch")
    if isinstance(w, dict):
        return "es_watch", dict(a, candidate=w.get("candidate"), code=reply.get("skip_code"))
    return "es_skip", dict(a, code=reply.get("skip_code"))


def expand(name, a, cands, sym, event_seq):
    """Вызов es_take|es_skip|es_watch или разобранный ответ entry_reply/1 -> (развёрнутый tb.entry/1, ошибки, кандидат или None). Числа берутся из кандидата cands по номеру;
    модель даёт только выбор, сетап, тезис, why и необязательные conf, size_mult, tags, hypothesis. Прежние коды: RANGE:candidate, NO_CANDIDATE:n_OF:m, CANDIDATE_FAILED:fail, SETUP_REQUIRED."""
    cands = cands or []
    why = validate.redact(str(a.get("why") or "").strip(), 200) or name
    conf = a.get("conf") if isinstance(a.get("conf"), (int, float)) and not isinstance(a.get("conf"), bool) and 0 <= a["conf"] <= 1 else 0.6
    base = {"schema": "tb.entry/1", "event_seq": event_seq or 0, "why": why, "conf": conf}
    if name == "es_skip":
        return dict(base, decision="skip", skip_code=a.get("code")), [], None
    try:
        n = int(a.get("candidate"))
    except (TypeError, ValueError):
        return None, ["RANGE:candidate"], None
    if not 1 <= n <= len(cands):
        return None, ["NO_CANDIDATE:%d_OF:%d" % (n, len(cands))], None
    c = cands[n - 1]
    if name == "es_watch":
        fail = str(c.get("fail") or "")
        code = a.get("code") if a.get("code") in validate.ENTRY_SKIP_CODES else ES_FAIL_SKIP.get(fail.split(":")[0], "rr_low" if fail.startswith("rr_low") else "no_confirm")
        setup = c.get("setup") if c.get("setup") in validate.SETUPS else "none"
        return dict(base, decision="skip", skip_code=code, watch={"side": c["side"], "level": c["level"], "stop": c["stop"], "tp": c["tp"][0], "setup": setup}), [], c
    if c.get("fail"):
        return None, ["CANDIDATE_FAILED:%s" % c["fail"]], c                      # негодный кандидат: взять другой ok или es_watch
    setup = c.get("setup") or a.get("setup")
    if setup not in validate.SETUPS or setup == "none":
        return None, ["SETUP_REQUIRED"], c
    hz = c.get("horizon")
    hyp = re.sub(r"[^a-z0-9._-]+", "-", str(a.get("hypothesis") or "").lower()).strip("-")[:40]
    if len(hyp) < 2:                                                             # по умолчанию семейство сетапа (R2, Requirement 19.2); тег модели нормализует шлюз
        hyp = hyps.family(setup)[0] or hyps.OTHER
    thesis = validate.redact(str(a.get("thesis") or "").strip(), 280)
    if len(thesis) < 10:
        thesis = "%s %s от %s, стоп %s, цель %s" % (setup, c["side"], c["level"], c["stop"], c["tp"][0])
    obj = dict(base, decision="propose", sym=sym, side=c["side"], horizon=hz, level=c["level"], stop=c["stop"], tp=list(c["tp"]), setup=setup,
               hypothesis=hyp, ttl_min=ES_TTL.get(hz, 60), confirm=c.get("confirm") or "candle", thesis=thesis)
    for k in ("zone", "entry_type"):
        if c.get(k) is not None:
            obj[k] = c[k]
    sm = [x for x in (c.get("size_mult"), a.get("size_mult")) if isinstance(x, (int, float)) and not isinstance(x, bool)]
    if sm:
        obj["size_mult"] = min(sm)                                               # потолок дня из кандидата, модель может только уменьшить
    tags = merge_tags(c.get("tags"), a.get("tags"))
    if tags:
        obj["tags"] = tags
    return obj, [], c


def expand_ctx(name, a, ctx):
    """expand по контексту раннера (ctx: cands, sym, event_seq) -> (tb.entry/1, ошибки). ctx["_cand"] = {n, geometry_ver} кандидата входа (для basis решения); сбрасывается при каждом вызове."""
    ctx.pop("_cand", None)
    obj, errs, c = expand(name, a, ctx.get("cands"), ctx.get("sym"), ctx.get("event_seq"))
    if not errs and obj is not None and obj.get("decision") == "propose" and c is not None:
        n = c.get("n") if isinstance(c.get("n"), int) else (ctx.get("cands") or []).index(c) + 1
        ctx["_cand"] = {"n": n, "geometry_ver": c.get("geometry_ver")}
    return obj, errs


def check_reply(text, role, ctx, build=None):
    """Сырой текст ответа модели по reply_schema роли -> (tb.entry/1 | None, коды). Порядок: JSON -> PRICE_KEY (цены в ответе) -> схема ответа и обязательные поля ->
    развёртывание кандидата (build, по умолчанию expand_ctx; коды NO_CANDIDATE:n_OF:m / CANDIDATE_FAILED) -> схема entry/1 роли. Семантику entry/1 добавляет вызывающий.
    Полный tb.entry/1 от модели (поле "schema":"tb.entry/1" или ценовые ключи) отклоняется кодом PRICE_KEY: развёрнутый объект строит только код."""
    from tb.llm import schema as schema_mod
    raw, code = schema_mod.extract_json(text)
    if code:
        return None, [code]
    if not isinstance(raw, dict):
        return None, ["BAD_TYPE:$"]
    keys = validate.price_keys(raw)
    if raw.get("schema") == "tb.entry/1":
        keys = sorted(set(keys) | {"schema"})
    if keys:
        return None, ["PRICE_KEY:%s" % ",".join(keys)]
    errs = schema_mod.validate(role.reply_obj, raw) or validate.semantic(role.reply_schema, raw, ctx)
    if errs:
        return None, errs
    name, args = reply_call(raw)
    obj, errs = (build or expand_ctx)(name, args, ctx)
    if errs:
        return None, errs
    errs = schema_mod.validate(role.schema, obj)
    return (obj, []) if not errs else (None, errs)


MODE = "paper"
MODES = ("paper", "testnet", "live")
EXPERIMENT = "entry-scout-v1"
FLATTEN_TYPES = ("scalp", "intraday")
FULL_SIZE_LEVEL = 4         # уровни риска 4-5: guard берёт полный бюджет риска (score 1.0), а не 0.5+0.5*conf (указание владельца 2026-11-01: на агрессии торговать большим размером)


def _fracs(n):
    base = round(1.0 / n, 6)
    out = [base] * (n - 1)
    out.append(round(1.0 - sum(out), 6))
    return out


def build(obj, ctx):
    """obj: проверенный ответ entry/1 (decision=propose); ctx: контекст раннера (session_type, px, event_seq). -> словарь плана tb.plan/1 (без полей исполнителя).
    Горизонт плана (session_type плана: стоп/RR/плечо у guard, срок удержания, закрытие в конце дня) = obj["horizon"], выбор модели; проверен в validate
    (в сессии-дне любой из risk_profile.session_types, в сессии одного горизонта только её тип)."""
    side, lvl, stop, tps = obj["side"], float(obj["level"]), float(obj["stop"]), [float(x) for x in obj["tp"]]
    ttl = int(obj["ttl_min"])
    stype = obj.get("horizon") or ctx.get("session_type")
    px = ctx.get("px") or lvl
    if obj["confirm"] == "candle":
        when = {"kind": "candle_close", "tf": "5m", "dir": "above" if side == "long" else "below", "level": lvl}
    else:
        z = obj["zone"]
        when = {"kind": "price_in_zone", "low": float(z["low"]), "high": float(z["high"])}
    et = obj.get("entry_type") or ("limit" if stype == "scalp" and obj["confirm"] == "candle" else "market")      # scalp по умолчанию лимитом: комиссия рынка съедает короткую цель
    how = {"type": "limit", "price": lvl, "post_only": True} if et == "limit" else {"type": "market"}      # limit: maker у уровня после подтверждения (0.072% круга вместо 0.24%)
    valid = {"ttl_min": ttl}
    if side == "long":
        valid.update(cancel_below=stop, cancel_above=tps[0])
    else:
        valid.update(cancel_above=stop, cancel_below=tps[0])
    tags = {"experiment": EXPERIMENT, "variant": "A", "hypothesis": obj["hypothesis"]}
    if obj["setup"] != "none":
        tags["setup"] = obj["setup"]
    extra = list(obj.get("tags") or [])
    if day_frame_breaks(obj, ctx):
        extra.append("off-plan")                     # вход вне сценария плана дня (разрешён validate.off_plan_allowed): отдельная выборка для разбора
    if extra:
        tags["extra"] = extra
    fr = _fracs(len(tps))
    score = 1.0 if (ctx.get("risk_level") or 0) >= FULL_SIZE_LEVEL else float(obj["conf"])
    risk = {"score": round(score, 2), "size_mult": float(obj.get("size_mult") or 1.0)}
    if obj["confirm"] == "none":
        risk["confirm_waiver"] = True
    mode = ctx.get("mode") if ctx.get("mode") in MODES else MODE
    plan = {"schema": "tb.plan/1", "symbol": obj["sym"], "side": side, "mode": mode, "session_type": stype,
            "source": {"kind": "scanner", "event_seq": int(ctx.get("event_seq") or 0)},
            "thesis": obj["thesis"], "tags": tags,
            "entry": {"when": when, "how": how, "ref_price": float(px), "valid": valid},
            "stop": {"price": stop, "trigger_by": "last"},
            "targets": [{"id": "t%d" % (i + 1), "price": p, "frac": fr[i], "exec": "resting"} for i, p in enumerate(tps)],
            "risk": risk,
            "lifecycle": {"ttl_min": ttl, "max_hold_min": MAX_HOLD_MIN.get(stype, 240), "flatten_at_session_end": stype in FLATTEN_TYPES}}
    assert plan["mode"] in MODES
    return plan
