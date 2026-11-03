"""Правила rulebook цикла обучения (R2, Requirements 7.1, 7.13, 8.1, 8.2, 8.5, 8.6; design.md, «tb.logic.learn_cond / learn_rules»). Чистый модуль: stdlib + tb.logic.

Первая итерация знает один вид правила `skip_when`: правило может только запретить вход (кандидат `ok=false` с кодом `RULE_<id>`, план — `E_RULE_<id>`),
не добавляет вход, не создаёт кандидата и не меняет размер, плечо, стоп и тейки. Разрешённые входы с правилами — подмножество разрешённых без них
(свойство 1). Размерные виды (`size_cap`, `require_confirm`) не реализованы: при `allow_size_rules=false` они отклоняются как размерные, при `true` —
как ещё не реализованные; применены не бывают никогда. Неизвестный вид отклоняется и на записи (validate_rule), и при чтении (apply пропускает
правило и перечисляет его в `blocked`); результат обработчика вида, который не является чистым запретом, тоже даёт `blocked` (BOUNDS_BLOCKED).

Правило (как его отдают tb.knowledge.rulebook_view и tb.learnloop.versions):
    {"rule_id": "RL0001", "kind": "skip_when", "cond": [{feature, op, value}], "param": {}, "scope": {"policy", "kinds", "side", "entry"},
     "env_norm": "mainnet"|"testnet", "status": "active", ...}
Признаки решения feats — результат tb.memory.features.rule_features (девять RULE_FEATURES + ev_kind, side_rel).
"""
import hashlib
import json
import re

from tb.logic import learn_cond as LC

RULE_KINDS = ("skip_when",)                     # вторая итерация: + "size_cap", "require_confirm" при allow_size_rules
SIZE_KINDS = ("size_cap", "require_confirm")
STATUSES = ("trial", "active", "disabled", "rejected")
TERMINAL = ("disabled", "rejected")
SCOPE_SIDES = ("with_dir", "against_dir", "any")
ENV_NORMS = ("mainnet", "testnet")
RE_RULE_ID = re.compile(r"^RL\d{4,6}$")
RULE_PREFIX = "RULE_"                           # отказ кандидата в brief.cands
PLAN_PREFIX = "E_RULE_"                         # отказ плана в submit_plan
BOUNDS_BLOCKED = "BOUNDS_BLOCKED"
DIRS = ("lower", "higher")


# ---------------------------------------------------------------- условие и его хеш

def canon_cond(cond):
    """Каноническое условие: элементы по имени признака, значения `in` по порядку. Порядок записи условия не меняет правило и cond_h."""
    out = []
    for c in sorted(cond, key=lambda x: (str(x.get("feature")), str(x.get("op")))):
        v = c.get("value")
        if c.get("op") == "in" and isinstance(v, list):
            v = sorted(v, key=lambda x: (str(type(x)), x))
        out.append({"feature": c.get("feature"), "op": c.get("op"), "value": v})
    return out


def _canon_json(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def cond_h(cond, dir="lower"):
    """Хеш условия и направления claim (ключ чёрного списка): sha256(canon(cond) + dir), 16 hex как у memory/chain.py (privacy_scan не считает утечкой)."""
    return hashlib.sha256((_canon_json(canon_cond(cond)) + str(dir)).encode("utf-8")).hexdigest()[:16]


def fail_code(rule_id):
    return RULE_PREFIX + str(rule_id)


def plan_code(rule_id):
    return PLAN_PREFIX + str(rule_id)


# ---------------------------------------------------------------- проверка правила

def _validate_scope(scope):
    if not isinstance(scope, dict) or set(scope) - {"policy", "kinds", "side", "entry"}:
        return ["scope: {policy, kinds, side, entry}"]
    errs = []
    kinds = scope.get("kinds")
    if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) and k for k in kinds) or len(kinds) > 16:
        errs.append("scope.kinds: непустой список видов сигналов")
    if scope.get("side") not in SCOPE_SIDES:
        errs.append("scope.side не из %s" % list(SCOPE_SIDES))
    for k in ("policy", "entry"):
        if scope.get(k) is not None and not isinstance(scope.get(k), str):
            errs.append("scope.%s: строка" % k)
    return errs


def validate_rule(rule, allow_size_rules=False):
    """-> список ошибок (пусто — правило годно к записи и применению). Вид из RULE_KINDS, условие из RULE_FEATURES и словаря (learn_cond.validate_cond),
    параметр skip_when пуст, scope области проверки, env_norm. Размерные виды отклоняются всегда (при allow_size_rules=false — как размерные)."""
    if not isinstance(rule, dict):
        return ["rule: словарь"]
    errs = []
    rid = rule.get("rule_id")
    if rid is not None and not (isinstance(rid, str) and RE_RULE_ID.match(rid)):
        errs.append("rule_id: RL и 4-6 цифр")
    kind = rule.get("kind")
    if kind in SIZE_KINDS:
        errs.append("kind %s: размерные правила %s" % (kind, "не реализованы" if allow_size_rules else "выключены (allow_size_rules=false)"))
    elif kind not in RULE_KINDS:
        errs.append("kind %r не из %s" % (kind, list(RULE_KINDS)))
    errs.extend(LC.validate_cond(rule.get("cond")))
    param = rule.get("param", {})
    if kind == "skip_when" and param not in ({}, None):
        errs.append("param: у skip_when параметров нет")
    errs.extend(_validate_scope(rule.get("scope")))
    if rule.get("env_norm") is not None and rule.get("env_norm") not in ENV_NORMS:
        errs.append("env_norm не из %s" % list(ENV_NORMS))
    st = rule.get("status")
    if st is not None and st not in STATUSES:
        errs.append("status не из %s" % list(STATUSES))
    return errs


# ---------------------------------------------------------------- область применимости и оценка

def scope_ok(feats, scope):
    """Решение в области применимости правила: True / False / None (не определить: нет события или стороны относительно сигнала).
    Та же логика, что tb.memory.features.in_scope (паритет закреплён тестом), но с различением «вне области» и «нет данных»."""
    if not isinstance(feats, dict) or not isinstance(scope, dict):
        return None
    kind = feats.get("ev_kind")
    if kind is None:
        return None
    kinds = scope.get("kinds") or []
    if "*" not in kinds and kind not in kinds:
        return False
    want = scope.get("side")
    if want in (None, "any"):
        return True
    rel = feats.get("side_rel")
    if rel is None:
        return None
    return rel == want


def evaluate(rule, feats):
    """Одно правило на признаках решения -> "hit" (условие истинно в области), "pass" (ложно), "missing" (нет события, стороны или признака условия),
    "out" (вне области применимости)."""
    sc = scope_ok(feats, rule.get("scope"))
    if sc is False:
        return "out"
    if sc is None:
        return "missing"
    if LC.missing(rule["cond"], feats):
        return "missing"
    return "hit" if LC.matches_features(rule["cond"], feats) else "pass"


def _skip_when(rule, cand, feats):
    return {"skip": evaluate(rule, feats) == "hit"}


# вид -> обработчик (rule, cand, feats) -> {"skip": bool}. Результат с другими ключами или не-bool — не чистое ужесточение: BOUNDS_BLOCKED.
HANDLERS = {"skip_when": _skip_when}


def _base_ok(cand):
    return not (isinstance(cand, dict) and cand.get("ok") is False)


def _rules_of(rules):
    """rules: список правил или вид rulebook_view.active() ({"rules", "apply", ...}) -> (список, apply_из_вида|None)."""
    if isinstance(rules, dict):
        return list(rules.get("rules") or []), rules.get("apply")
    return list(rules or []), None


def apply(rules, cand, feats, ctx=None):
    """Применить активные правила к кандидату или плану. -> {"ok", "fail": "RULE_<id>"|None, "hits": [id], "eval": [id], "missing": [id],
    "blocked": [id], "apply": bool}.

    rules — список правил или вид rulebook_view.active(root) (его "apply"=false — режим без применения); ctx: {"apply": bool, "env_norm": str,
    "allow_size_rules": bool}. В режиме без применения ничего не оценивается (совпадения не пишутся, shadow_hits — вторая итерация).
    Применяются только правила со статусом active (статус не указан — active), годные по validate_rule и с env_norm решения (если оно задано).
    eval — оценённые в области правила (hit или pass), missing — правила, которые нельзя оценить (нет события, стороны или признака), out — молча.
    ok = исходное ok кандидата (у плана — True) и ни одного hit: правило может только снять разрешение."""
    ctx = ctx or {}
    lst, view_apply = _rules_of(rules)
    on = ctx.get("apply", True) is not False and view_apply is not False
    out = {"ok": _base_ok(cand), "fail": None, "hits": [], "eval": [], "missing": [], "blocked": [], "apply": bool(on)}
    if not on:
        return out
    env = ctx.get("env_norm")
    allow_size = bool(ctx.get("allow_size_rules"))
    for r in sorted((x for x in lst if isinstance(x, dict)), key=lambda x: str(x.get("rule_id"))):
        if r.get("status", "active") != "active":
            continue
        rid = r.get("rule_id")
        if validate_rule(r, allow_size) or r.get("kind") not in HANDLERS:
            out["blocked"].append(rid)
            continue
        if env is not None and r.get("env_norm") not in (None, env):
            continue
        res = HANDLERS[r["kind"]](r, cand, feats if isinstance(feats, dict) else {})
        if not isinstance(res, dict) or set(res) != {"skip"} or not isinstance(res["skip"], bool):
            out["blocked"].append(rid)                   # не чистый запрет (размер, плечо, новый вход): правило не применяется
            continue
        state = evaluate(r, feats if isinstance(feats, dict) else {})
        if state == "missing":
            out["missing"].append(rid)
        elif state in ("hit", "pass"):
            out["eval"].append(rid)
            if res["skip"]:
                out["hits"].append(rid)
    if out["hits"]:
        out["ok"] = False
        out["fail"] = fail_code(out["hits"][0])
    return out


def allowed(rules, cand, feats, ctx=None):
    """Разрешён ли вход с этими правилами (исходное ok и ни одного срабатывания)."""
    return apply(rules, cand, feats, ctx)["ok"]


# ---------------------------------------------------------------- ужесточение

def rule_key(rule):
    """Содержательный ключ правила (вид, условие, параметр, область, окружение) — для сравнения наборов."""
    return _canon_json([rule.get("rule_id"), rule.get("kind"), canon_cond(rule.get("cond") or []), rule.get("param") or {}, rule.get("scope") or {},
                        rule.get("env_norm")])


def _active(rules):
    lst, _ = _rules_of(rules)
    return [r for r in lst if isinstance(r, dict) and r.get("status", "active") == "active"]


def is_tightening(before, after, probes=(), allow_size_rules=False):
    """allowed(after) ⊆ allowed(before): каждое правило before есть в after без изменений, все правила after годны и вида skip_when (запрет);
    на пробах [(cand, feats, ctx)] разрешённое after разрешено и before. Пустой after при непустом before — ослабление (откат так и проверяется: False)."""
    b, a = _active(before), _active(after)
    if any(validate_rule(r, allow_size_rules) or r.get("kind") != "skip_when" for r in a):
        return False
    keys_a = set(rule_key(r) for r in a)
    if any(rule_key(r) not in keys_a for r in b):
        return False
    for p in probes:
        cand, feats = p[0], p[1]
        ctx = p[2] if len(p) > 2 else None
        if allowed(a, cand, feats, ctx) and not allowed(b, cand, feats, ctx):
            return False
    return True


def scope_kinds(rules):
    """Объединение видов сигналов областей применимости правил (для rulebook_view.resolve_event)."""
    out = set()
    for r in _active(rules):
        sc = r.get("scope")
        if isinstance(sc, dict):
            out.update(k for k in sc.get("kinds") or [] if isinstance(k, str))
    return sorted(out)
