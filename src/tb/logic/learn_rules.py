"""Правила rulebook цикла обучения (R2, Requirements 7.1, 7.13, 8.1, 8.2, 8.5, 8.6; design.md, «tb.logic.learn_cond / learn_rules»). Чистый модуль: stdlib + tb.logic.

Первая итерация знает один вид правила `skip_when`: правило может только запретить вход (кандидат `ok=false` с кодом `RULE_<id>`, план — `E_RULE_<id>`),
не добавляет вход, не создаёт кандидата и не меняет размер, плечо, стоп и тейки. Разрешённые входы с правилами — подмножество разрешённых без них
(свойство 1). Неизвестный вид отклоняется и на записи (validate_rule), и при чтении (apply пропускает правило и перечисляет его в `blocked`);
результат обработчика вида, который не является чистым запретом, тоже даёт `blocked` (BOUNDS_BLOCKED).

Размерные виды (вторая итерация, задача 29; Requirement 8.2, 8.5, 8.6; решение владельца 3; ROADMAP R7.2):
  - при `allow_size_rules=false` (по умолчанию) `size_cap` и `require_confirm` не создаются (validate_rule — ошибка «выключены»), не применяются
    (apply перечисляет их в `blocked`) и при чтении отклоняются; поведение первой итерации не меняется, ключей размера в ответе apply нет;
  - `size_cap` при `allow_size_rules=true`: param {"size_mult": x}, 0 < x < 1; срабатывание даёт множитель размера x (только снижение), вход не
    снимает и не добавляет; несколько срабатываний — минимальный множитель. Итог для плана — bounded_size_mult: min(size_mult плана, потолок уровня
    риска) × множитель правил, то есть не выше потолка и не выше размера без правил; размер и плечо по-прежнему считает guard (правило стоит до него);
  - `require_confirm` объявлен, но не порождается, пока нет парной политики на тех же сигналах (R1.3/R4): validate_rule отклоняет его и при
    `allow_size_rules=true` (NOT_AVAILABLE), поэтому он не создаётся (BAD_RULE) и не применяется (blocked).
Шлюз и мозг (rulebook_view, brain/cands, gateway) читают правила без allow_size_rules, поэтому размерные правила там не действуют до отдельного
указания владельца.

Правило (как его отдают tb.knowledge.rulebook_view и tb.learnloop.versions):
    {"rule_id": "RL0001", "kind": "skip_when", "cond": [{feature, op, value}], "param": {}, "scope": {"policy", "kinds", "side", "entry"},
     "env_norm": "mainnet"|"testnet", "status": "active", ...}
Признаки решения feats — результат tb.memory.features.rule_features (девять RULE_FEATURES + ev_kind, side_rel).
"""
import hashlib
import json
import re

from tb.logic import learn_cond as LC

RULE_KINDS = ("skip_when",)                     # виды при allow_size_rules=false; при true — kinds(True)
SIZE_KINDS = ("size_cap", "require_confirm")
SIZE_IMPL = ("size_cap",)                       # размерные виды, которые реализованы (require_confirm ждёт парной политики)
NOT_AVAILABLE = "NOT_AVAILABLE"
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
    """Хеш условия и направления claim (идентичность правила, DUP_ACTIVE): sha256(canon(cond) + dir), 16 hex как у memory/chain.py
    (privacy_scan не считает утечкой). Ключ чёрного списка — block_h (семейство условия без порогов числовых признаков)."""
    return hashlib.sha256((_canon_json(canon_cond(cond)) + str(dir)).encode("utf-8")).hexdigest()[:16]


def block_family(cond):
    """Семейство условия (ключ чёрного списка, Requirement 7.14): элемент числового признака -> [feature, op] без порога (порог меню — квантиль
    окна и сдвигается каждую неделю), категориального (CAT_VALUES) -> [feature, op, value] с каноническим значением (`in` — по порядку).
    Элементы отсортированы."""
    out = []
    for c in canon_cond(cond or []):
        f = c.get("feature")
        if f in LC.CAT_VALUES or not LC.is_num(c.get("value")):
            out.append([f, c.get("op"), c.get("value")])
        else:
            out.append([f, c.get("op")])
    return sorted(out, key=_canon_json)


def block_h(cond, dir="lower"):
    """Ключ чёрного списка: sha256("block:" + canon(семейство) + dir), 16 hex. Сдвиг порога числового признака ключ не меняет;
    другое значение категориального признака или другое направление claim — другой ключ."""
    return hashlib.sha256(("block:" + _canon_json(block_family(cond)) + str(dir)).encode("utf-8")).hexdigest()[:16]


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


def kinds(allow_size_rules=False):
    """Виды, годные к записи и применению: skip_when; при allow_size_rules=true ещё реализованные размерные (size_cap)."""
    return RULE_KINDS + (SIZE_IMPL if allow_size_rules else ())


def _num01(x):
    """Число строго в (0, 1) (bool не число)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and 0.0 < float(x) < 1.0


valid_size_mult = _num01                        # параметр size_cap годен (для tb.learnloop.trial.verdict_size)


def validate_rule(rule, allow_size_rules=False):
    """-> список ошибок (пусто — правило годно к записи и применению). Вид из kinds(allow_size_rules), условие из RULE_FEATURES и словаря
    (learn_cond.validate_cond), параметр skip_when пуст, у size_cap {"size_mult": (0,1)}, scope области проверки, env_norm.
    Размерные виды при allow_size_rules=false отклоняются как выключенные; require_confirm отклоняется всегда (NOT_AVAILABLE)."""
    if not isinstance(rule, dict):
        return ["rule: словарь"]
    errs = []
    rid = rule.get("rule_id")
    if rid is not None and not (isinstance(rid, str) and RE_RULE_ID.match(rid)):
        errs.append("rule_id: RL и 4-6 цифр")
    kind = rule.get("kind")
    if kind in SIZE_KINDS and not allow_size_rules:
        errs.append("kind %s: размерные правила выключены (allow_size_rules=false)" % kind)
    elif kind in SIZE_KINDS and kind not in SIZE_IMPL:
        errs.append("kind %s: %s, нет парной политики на тех же сигналах (R1.3/R4)" % (kind, NOT_AVAILABLE))
    elif kind not in kinds(allow_size_rules):
        errs.append("kind %r не из %s" % (kind, list(kinds(allow_size_rules))))
    errs.extend(LC.validate_cond(rule.get("cond")))
    param = rule.get("param", {})
    if kind == "skip_when" and param not in ({}, None):
        errs.append("param: у skip_when параметров нет")
    if kind == "size_cap" and allow_size_rules and not (isinstance(param, dict) and set(param) == {"size_mult"} and _num01(param["size_mult"])):
        errs.append("param: у size_cap {\"size_mult\": x}, 0 < x < 1 (только снижение размера)")
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


def _size_cap(rule, cand, feats):
    return {"size_mult": float(rule["param"]["size_mult"]) if evaluate(rule, feats) == "hit" else 1.0}


# вид -> обработчик (rule, cand, feats). skip_when -> {"skip": bool}; size_cap -> {"size_mult": x}, 0 < x <= 1. Результат другой формы
# (лишние ключи, не-bool, множитель вне (0, 1]) — не чистое ужесточение: BOUNDS_BLOCKED. size_cap вызывается, только если allow_size_rules=true.
HANDLERS = {"skip_when": _skip_when, "size_cap": _size_cap}


def _pure(kind, res):
    """Результат обработчика — чистое ужесточение своего вида (запрет или снижение размера, ничего больше)."""
    if not isinstance(res, dict):
        return False
    if kind == "size_cap":
        m = res.get("size_mult")
        return set(res) == {"size_mult"} and isinstance(m, (int, float)) and not isinstance(m, bool) and m == m and 0.0 < float(m) <= 1.0
    return set(res) == {"skip"} and isinstance(res["skip"], bool)


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
    "allow_size_rules": bool}. В режиме без применения ничего не оценивается и совпадения не пишутся; правила trial и активные правила в этом
    режиме оценивает отдельно knowledge.rulebook_view.shadow_hits (вызывает apply с apply=True; решение не меняется, итог идёт в res.shadow_hits
    записи rule_eval, Requirement 18.7).
    Применяются только правила со статусом active (статус не указан — active), годные по validate_rule и с env_norm решения (если оно задано).
    eval — оценённые в области правила (hit или pass), missing — правила, которые нельзя оценить (нет события, стороны или признака), out — молча.
    ok = исходное ok кандидата (у плана — True) и ни одного hit: правило может только снять разрешение.

    Только при ctx.allow_size_rules=true в ответе есть "size_mult" (минимальный множитель сработавших size_cap, 1.0 без срабатываний, всегда в (0, 1])
    и "size_hits" ([id]); size_cap не меняет ok и fail. Итог размера плана — bounded_size_mult (не выше потолка уровня риска)."""
    ctx = ctx or {}
    lst, view_apply = _rules_of(rules)
    on = ctx.get("apply", True) is not False and view_apply is not False
    allow_size = bool(ctx.get("allow_size_rules"))
    out = {"ok": _base_ok(cand), "fail": None, "hits": [], "eval": [], "missing": [], "blocked": [], "apply": bool(on)}
    if allow_size:
        out.update(size_mult=1.0, size_hits=[])
    if not on:
        return out
    env = ctx.get("env_norm")
    for r in sorted((x for x in lst if isinstance(x, dict)), key=lambda x: str(x.get("rule_id"))):
        if r.get("status", "active") != "active":
            continue
        rid = r.get("rule_id")
        kind = r.get("kind")
        if validate_rule(r, allow_size) or kind not in HANDLERS or (kind in SIZE_KINDS and not allow_size):
            out["blocked"].append(rid)
            continue
        if env is not None and r.get("env_norm") not in (None, env):
            continue
        res = HANDLERS[kind](r, cand, feats if isinstance(feats, dict) else {})
        if not _pure(kind, res):
            out["blocked"].append(rid)                   # не чистое ужесточение (рост размера, плечо, новый вход): правило не применяется
            continue
        state = evaluate(r, feats if isinstance(feats, dict) else {})
        if state == "missing":
            out["missing"].append(rid)
        elif state in ("hit", "pass"):
            out["eval"].append(rid)
            if kind == "size_cap":
                if float(res["size_mult"]) < 1.0:
                    out["size_hits"].append(rid)
                    out["size_mult"] = min(out["size_mult"], float(res["size_mult"]))
            elif res["skip"]:
                out["hits"].append(rid)
    if out["hits"]:
        out["ok"] = False
        out["fail"] = fail_code(out["hits"][0])
    return out


def allowed(rules, cand, feats, ctx=None):
    """Разрешён ли вход с этими правилами (исходное ok и ни одного срабатывания)."""
    return apply(rules, cand, feats, ctx)["ok"]


def _fnum(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and x not in (float("inf"), float("-inf"))


def bounded_size_mult(size_mult, res, ceiling=None):
    """Итоговый size_mult плана под размерными правилами: min(size_mult плана, ceiling) × множитель apply (res или число), с отсечкой сверху тем же
    минимумом. ceiling — потолок уровня риска (risklevels.size_mult_cap: min(уровень, agents.json max_size_mult)); None — только size_mult плана.
    Гарантии: результат в [0, min(size_mult, ceiling)] — не выше потолка уровня риска и не выше размера без правил (только снижение); множитель
    вне (0, 1] или нечисло -> 1.0 (правило не применяется, BOUNDS_BLOCKED уже записан apply). Размер и плечо считает guard (Gate.size: qty × size_mult),
    эта функция только сужает множитель."""
    base = float(size_mult) if _fnum(size_mult) and size_mult > 0 else 0.0
    if _fnum(ceiling):
        base = min(base, max(0.0, float(ceiling)))
    m = res.get("size_mult", 1.0) if isinstance(res, dict) else res
    if not (_fnum(m) and 0.0 < float(m) <= 1.0):
        m = 1.0
    return min(base, base * float(m))


# ---------------------------------------------------------------- ужесточение

def rule_key(rule):
    """Содержательный ключ правила (вид, условие, параметр, область, окружение) — для сравнения наборов."""
    return _canon_json([rule.get("rule_id"), rule.get("kind"), canon_cond(rule.get("cond") or []), rule.get("param") or {}, rule.get("scope") or {},
                        rule.get("env_norm")])


def _active(rules):
    lst, _ = _rules_of(rules)
    return [r for r in lst if isinstance(r, dict) and r.get("status", "active") == "active"]


def is_tightening(before, after, probes=(), allow_size_rules=False):
    """allowed(after) ⊆ allowed(before): каждое правило before есть в after без изменений, все правила after годны и вида skip_when (запрет) или,
    при allow_size_rules=true, size_cap (снижение размера); на пробах [(cand, feats, ctx)] разрешённое after разрешено и before, а множитель размера
    after не выше before. Пустой after при непустом before — ослабление (откат так и проверяется: False)."""
    b, a = _active(before), _active(after)
    if any(validate_rule(r, allow_size_rules) or r.get("kind") not in kinds(allow_size_rules) for r in a):
        return False
    keys_a = set(rule_key(r) for r in a)
    if any(rule_key(r) not in keys_a for r in b):
        return False
    for p in probes:
        cand, feats = p[0], p[1]
        ctx = dict(p[2] if len(p) > 2 and p[2] else {}, allow_size_rules=bool(allow_size_rules))
        ra, rb = apply(a, cand, feats, ctx), apply(b, cand, feats, ctx)
        if ra["ok"] and not rb["ok"]:
            return False
        if ra.get("size_mult", 1.0) > rb.get("size_mult", 1.0) + 1e-12:
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
