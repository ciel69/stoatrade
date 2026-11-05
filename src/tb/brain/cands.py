"""Кандидаты на вход для entry_scout (блок досье `cands`): код заранее считает уровень, стоп, цели, расстояния, RR после издержек и горизонт,
чтобы модель выбирала сетап, а не считала. Тонкая обёртка над tb.logic.geometry (R1.2): собирает пул уровней и ctx из досье, читает config/geometry.json,
вызывает геометрию и добавляет в каждую строку geometry_ver. Алгоритм, константы и формат строк прежние (см. tb.logic.geometry).

R2 (Requirement 7.7, точка (а)): после геометрии кандидаты размечаются действующим rulebook (tb.knowledge.rulebook_view): событие решения
(resolve_event) -> признаки (memory.features.rule_features) -> tb.logic.learn_rules.apply по стороне кандидата. Кандидат под активным правилом
остаётся в списке на своём месте (номер n прежний) с ok=false и fail="RULE_<id>". Та же функция rule_check вызывается шлюзом в submit_plan
(точка (б)), поэтому одно решение (монета, сторона, событие) даёт одинаковый результат в обеих точках. При пустом rulebook, режиме без
применения (выключатель, аренда, недоступная или восстанавливаемая база) и любой ошибке чтения кандидаты не меняются.
Вторая итерация (Requirement 18.7): правила trial и активные правила в режиме без применения оцениваются без влияния на решение — их
идентификаторы, которые отсекли бы кандидата, идут в res.shadow_hits (запись rule_eval) и в поле кандидата shadow_hits (только при совпадении).
"""
import json
import sqlite3
import time

from tb import paths
from tb.brain import validate as V
from tb.logic import geometry as G

CLUSTER_PCT = G.DEFAULT_CLUSTER_PCT
chase = G.chase
RULE_ERRORS = (sqlite3.Error, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError)


def load_config(root=None):
    """-> (config/geometry.json как словарь, geometry_ver). Нет файла или он битый -> умолчания геометрии (равны файлу поставки)."""
    try:
        with open(paths.config("geometry.json", root=root), encoding="utf-8") as f:
            cfg = json.load(f)
        if isinstance(cfg, dict):
            return cfg, str(cfg.get("geometry_ver") or G.GEOMETRY_VER)
    except (OSError, ValueError):
        pass
    return {}, G.GEOMETRY_VER


def pool(brief, sym):
    """-> отсортированный список цен уровней монеты (validate.level_pool: struct, levels, идеи) без дублей ближе cluster_pct."""
    return G.pool(V.level_pool(brief, sym), load_config()[0].get("cluster_pct", G.DEFAULT_CLUSTER_PCT))


def _horizon(ctx, stop_pct, rr, wide_pct=None, tp1_pct=None):
    return G._horizon(ctx, stop_pct, rr, wide_pct, tp1_pct, G._profiles(load_config()[0])[0])


# ---------------------------------------------------------------- правила rulebook (общая функция мозга и шлюза)

def rule_view(root=None, now=None):
    """Действующий rulebook (rulebook_view.active) или None при любой ошибке (режим без применения)."""
    try:
        from tb.knowledge import rulebook_view as RV
        return RV.active(root, now)
    except RULE_ERRORS:
        return None


def policy_kinds(root=None):
    """Виды сигналов области проверки (config/learnloop.json verify.policy, для P2 — range_5m, turnover_surge, hot_enter, oi_spike, liq_cascade)
    из config/shadow_policies.json (включённая запись, последняя по ver). Не читается -> []."""
    try:
        with open(paths.config("learnloop.json", root=root), encoding="utf-8") as f:
            pid = (json.load(f).get("verify") or {}).get("policy")
        from tb.labels import policies as LP
        from tb.memory import features as MF
        pols = [p for p in LP.load(root) if p.get("id") == pid and p.get("enabled")]
        return list(MF.policy_scope(max(pols, key=lambda p: p["ver"]))["kinds"]) if pols else []
    except RULE_ERRORS:
        return []


def rule_kinds(view, root=None):
    """Виды событий для resolve_event: виды scope действующих правил; при пустом rulebook — виды политики проверки (иначе ложный RULES_NO_EVENT)."""
    kinds = list((view or {}).get("kinds") or [])
    return kinds or policy_kinds(root)


def resolve(root, sym, event_seq, now, view):
    """Событие решения (rulebook_view.resolve_event) или None при ошибке чтения шины/конфига."""
    return resolve_why(root, sym, event_seq, now, view)["event"]


def resolve_why(root, sym, event_seq, now, view):
    """Событие решения с причиной (rulebook_view.resolve_event_why) -> {"event", "why", "kind"}; ошибка чтения шины/конфига -> no_event."""
    try:
        from tb.knowledge import rulebook_view as RV
        return RV.resolve_event_why(root, sym, event_seq, now, rule_kinds(view, root))
    except RULE_ERRORS:
        return {"event": None, "why": "no_event", "kind": None}


def _mem_cfg(root):
    try:
        from tb.memory import cfg as memcfg
        return memcfg.load(root)
    except RULE_ERRORS:
        return {}


def needs_cm(view):
    """В действующих, trial или теневых правилах вида есть условие по признаку сводного рынка (cm_*). Нет — строка признаков не читается
    и решение не ждёт её записи (R1.7: ожидание строки нужно только правилам, которые её используют)."""
    if not isinstance(view, dict):
        return False
    try:
        from tb.logic import learn_cond as LC
        return any(isinstance(r, dict) and LC.cond_uses_cm(r.get("cond")) for r in list(view.get("rules") or []) + list(view.get("trial") or []))
    except RULE_ERRORS:
        return False


def rule_cm(root, sym, event, now=None):
    """Строка признаков сигнала для правил (R1.7, Requirements 15.3, 15.4, 23.3): t0 — поле d.t0 события шины (не время решения и не время
    конверта), монета — символ события; tb.core.cmktdoc.features_at(..., wait=True): при pending ждёт до срока ожидания строки
    (row_ts + accept_s + 1, не дольше accept_s + 2 с реального времени), pending наружу не выходит.
    -> {"cm": dict | None, "cm_missing": код | None}; нет события -> оба None; нет d.t0 -> no_t0; любой сбой -> db. Не бросает."""
    try:
        if not isinstance(event, dict) or not isinstance(event.get("d"), dict):
            return {"cm": None, "cm_missing": None}
        from tb.memory import features as MF
        t0 = MF.num(event["d"].get("t0"))
        if t0 is None:
            return {"cm": None, "cm_missing": "no_t0"}
        from tb.core import cmktdoc
        esym = event.get("sym") if isinstance(event.get("sym"), str) and event.get("sym") else sym
        r = cmktdoc.features_at(root, MF.base_sym(esym), t0, now=now, wait=True)
        if isinstance(r, dict) and isinstance(r.get("cm"), dict):
            return {"cm": r["cm"], "cm_missing": None}
        code = r.get("cm_missing") if isinstance(r, dict) else None
        return {"cm": None, "cm_missing": code if isinstance(code, str) and code else "db"}
    except Exception:          # noqa: BLE001 — чтение рядов не ломает решение: сбой = признаки cm_ None
        return {"cm": None, "cm_missing": "db"}


def rule_check(root, sym, side, event_seq=None, now=None, view=None, event=False, cfg=None, cm=False):
    """Одно решение (монета, сторона, событие) под действующим rulebook -> {"view", "event", "res", "event_why", "event_kind", "cm_missing"}; res —
    learn_rules.apply для {"side", "ok": True}; event_why/event_kind — причина события для учёта (rulebook_view.EVENT_WHY), решение не меняют.

    event_seq — только из задания раннера entry_scout (для chat None). view — готовый rulebook_view.active (иначе читается). event — уже
    разрешённое событие (False — разрешить здесь). cm — готовый ответ rule_cm (False — прочитать здесь, только если правилам вида нужны
    признаки cm_, needs_cm). cm_missing — код отсутствия строки признаков (no_t0, not_focus, no_row, late, db) или None (строка есть или
    не читалась). Любая ошибка -> res в режиме без применения (ok=True, ничего не отсечено)."""
    now = time.time() if now is None else now
    view = rule_view(root, now) if view is None else view
    if event is False:
        rw = resolve_why(root, sym, event_seq, now, view)
    else:
        d = event.get("d") if isinstance(event, dict) else None
        rw = {"event": event, "why": "ok" if event else "no_event", "kind": d.get("kind") if isinstance(d, dict) else None}
    ev = rw["event"]
    res = {"ok": True, "fail": None, "hits": [], "eval": [], "missing": [], "blocked": [], "apply": False}
    applies = bool(view and view.get("apply") and view.get("rules"))
    shadow = _shadow_rules(view)
    cmr = {"cm": None, "cm_missing": None}
    if (applies or shadow) and ev:
        if cm is False:
            cmr = rule_cm(root, sym, ev, now) if needs_cm(view) else cmr
        elif isinstance(cm, dict):
            cmr = cm
    if applies or shadow:
        try:
            from tb.logic import learn_rules as LR
            from tb.memory import features as MF
            feats = MF.rule_features(ev, {"side": side}, _mem_cfg(root) if cfg is None else cfg,
                                     cm=cmr["cm"] if isinstance(cmr.get("cm"), dict) else {})
            if applies:
                res = LR.apply(view, {"side": side, "ok": True}, feats)
            if shadow:                                   # вторая итерация (Requirement 18.7): совпадения без применения, решение не меняется
                res = dict(res, shadow_hits=_shadow_hits(view, {"side": side, "ok": True}, feats))
        except RULE_ERRORS:
            pass
    return {"view": view, "event": ev, "res": res, "event_why": rw.get("why"), "event_kind": rw.get("kind"), "cm_missing": cmr.get("cm_missing")}


def _shadow_rules(view):
    """Правила trial и активные в режиме без применения (rulebook_view.shadow_rules) или [] при ошибке/пустом виде."""
    if not view:
        return []
    try:
        from tb.knowledge import rulebook_view as RV
        return RV.shadow_rules(view)
    except RULE_ERRORS:
        return []


def _shadow_hits(view, cand, feats):
    from tb.knowledge import rulebook_view as RV
    return RV.shadow_hits(view, cand, feats)


def is_rule_fail(c):
    """Кандидат отсечён правилом rulebook (fail RULE_<id>)."""
    return isinstance(c, dict) and isinstance(c.get("fail"), str) and c["fail"].startswith("RULE_")


def ok_before_rules(cands):
    """Число ok-кандидатов до правил: ok и отсечённые правилом (поле cands_ok_n записи rule_eval). Не список -> None."""
    if not isinstance(cands, list):
        return None
    return sum(1 for c in cands if isinstance(c, dict) and (c.get("ok") or is_rule_fail(c)))


def apply_rules(out, sym, root=None, event_seq=None, now=None, view=None):
    """Разметить кандидатов действующими правилами на месте: ok-кандидат стороны, на которой сработало правило, -> ok=false, fail=RULE_<id>.
    Пустой rulebook, режим без применения или корень не задан (root=None: ./tb brief без корня, прежние вызовы) -> ничего (база и шина не читаются)."""
    if not out or root is None:
        return out
    now = time.time() if now is None else now
    view = rule_view(root, now) if view is None else view
    applies = bool(view and view.get("apply") and view.get("rules"))
    if not (applies or _shadow_rules(view)):
        return out
    sides = sorted(set(c.get("side") for c in out if isinstance(c, dict) and c.get("ok") and c.get("side") in ("long", "short")))
    if not sides:
        return out
    ev = resolve(root, sym, event_seq, now, view)
    cfg = _mem_cfg(root)
    cm = rule_cm(root, sym, ev, now) if (ev and needs_cm(view)) else {"cm": None, "cm_missing": None}      # одна строка признаков на обе стороны
    for side in sides:
        res = rule_check(root, sym, side, now=now, view=view, event=ev, cfg=cfg, cm=cm)["res"]
        sh = res.get("shadow_hits") or []
        for c in out:
            if not (isinstance(c, dict) and c.get("ok") and c.get("side") == side):
                continue
            if sh:
                c["shadow_hits"] = list(sh)             # вторая итерация: правила, которые отсекли бы кандидата (поле только при совпадении)
            if res.get("fail"):
                c["ok"] = False
                c["fail"] = res["fail"]
    return out


def candidates(brief, sym, ctx=None, root=None, event_seq=None, now=None, view=None):
    """-> [кандидат] (ok сначала, по убыванию rr_net) или None. ctx: validate.entry_context(brief, {"sym": sym}) (считается сам, если не передан).
    root — корень платформы (конфиг геометрии, rulebook, шина); event_seq — событие задания раннера entry_scout (для chat None); view —
    готовый rulebook_view.active(root). Отсечённые правилом кандидаты остаются на своём месте с ok=false и fail=RULE_<id>."""
    if not isinstance(brief, dict) or not sym:
        return None
    ctx = ctx or V.entry_context(brief, {"sym": sym})
    cfg, ver = load_config(root)
    out = G.candidates(V.level_pool(brief, sym), ctx, cfg)
    for r in out or []:
        r["geometry_ver"] = ver
    return apply_rules(out, sym, root, event_seq, now, view)
