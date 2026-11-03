"""Правило из урока меток, достижимость, пересечение по строкам и чёрный список условий (R2, Requirements 7.9, 7.13, 7.14, 9.1, 9.4, 9.9, 10.5;
design.md, «tb.learnloop.rules / versions / ...», «Чёрный список условий», таблица «Коды причин»).

derive(lesson, policy, ...)    skip_when только из урока меток hyp с claim (mean_r, lower, base) и условием из RULE_FEATURES; чёрный список проверяется
estimate_reach(rows, ...)      дней до n_target по скорости кластеров теста (предельные хиты и база испытания) в переданной выборке окна
overlap_rows(win, a, b)        доля прогонов-хитов правила a в выборке окна, которые отсекает правило b (Requirement 9.9; lessons.overlap не используется)
block / block_policy           чёрный список только через knowledge.store2.blocklist_put/get: коды кода — фиксированные cooldown_days,
                               OWNER_DISABLE и OWNER_REJECT — бессрочно с копией базы после записи; коды без записи по таблице design.md

Выборку окна (строки cluster_runs с t0 в [a, b) и closed_ts <= cut_ts) строит learnloop.clusters.window (задача 7); здесь — чистые функции над
переданными строками. Признаки строки — JSON feats (девять RULE_FEATURES), вид сигнала — колонка type, сторона относительно сигнала — feats.side_rel,
а у прогонов самой политики области (policy_id == scope.policy) — по построению scope.side.
"""
import json
import math
import sqlite3

from tb.knowledge import store2
from tb.logic import learn_cond as LC
from tb.logic import learn_rules as LR
from tb.memory import features as MF

DAY = 86400
SKIP_CLAIM = ("mean_r", "lower", "base")
# чёрный список (Requirement 7.14, таблица кодов): коды кода — на cooldown_days; владельца — бессрочно; остальные коды не пишутся
BLOCK_COOLDOWN = ("NO_OOS_SIGN", "NOT_SIGNIFICANT", "N_TOO_SMALL", "SAMPLE_CHANGED", "TRIAL_REJECT", "NO_VERDICT", "UNREACHABLE_N", "ROLLBACK",
                  "TRADE_STARVATION", "BOUNDS_BLOCKED")
BLOCK_FOREVER = ("OWNER_DISABLE", "OWNER_REJECT")
NO_BLOCK = ("LOSS_CAP", "CTX_CHANGED", "RESTORED", "DUP_ACTIVE", "BUDGET", "EVIDENCE_TOO_BIG", "OWNER")
OVERLAP = "OVERLAP"                               # причина NO_VERDICT при продвижении пересекающегося правила: без записи


# ---------------------------------------------------------------- чёрный список

def block_policy(code, reason=None):
    """-> "cooldown" | "forever" | None (не пишется). NO_VERDICT с причиной OVERLAP не пишется. Неизвестный код -> ValueError."""
    if code == "NO_VERDICT" and reason == OVERLAP:
        return None
    if code in BLOCK_FOREVER:
        return "forever"
    if code in BLOCK_COOLDOWN:
        return "cooldown"
    if code in NO_BLOCK:
        return None
    raise ValueError("код %r не из таблицы чёрного списка" % (code,))


def cooldown_s(cfg):
    return int(cfg["blocklist"]["cooldown_days"]) * DAY


def block_until(code, now, cfg, reason=None):
    """-> (писать ли, until_ts|None). until_ts=None — бессрочно (только записи владельца)."""
    pol = block_policy(code, reason)
    if pol is None:
        return False, None
    if pol == "forever":
        return True, None
    return True, int(now) + cooldown_s(cfg)


def block(conn, cond_h, code, now, cfg, actor=None, reason=None, root=None, backup=True):
    """Запись чёрного списка по коду (через store2.blocklist_put; внутри транзакции вызывающего или в своей). -> строка или None (код не пишется).
    Запись владельца (OWNER_*) — бессрочно, actor=OWNER и копия базы `owner` после фиксации (если вызвано вне транзакции и задан root;
    внутри чужой транзакции копию делает вызывающий после COMMIT)."""
    write, until = block_until(code, now, cfg, reason)
    if not write:
        return None
    owner = code in BLOCK_FOREVER
    actor = actor or ("OWNER" if owner else "code")
    in_tx = conn.in_transaction
    row = store2.blocklist_put(conn, cond_h, code, until, actor, now=now)
    if owner and backup and root is not None and not in_tx:
        row = dict(row, backup=store2.make_backup(root, "owner", now=now))
    return row


def blocked(conn, cond_h, now):
    """Действующая запись чёрного списка условия или None (истекла, снята владельцем, нет)."""
    return store2.blocklist_get(conn, cond_h, now=now)


# ---------------------------------------------------------------- правило из урока

def _claim(lesson):
    cl = (lesson or {}).get("claim") or {}
    return (cl.get("metric"), cl.get("dir"), cl.get("vs"))


def derive_reason(lesson, conn=None, now=None):
    """Почему урок не даёт правила -> None (даёт) | NOT_LABELS | NOT_HYP | NOT_SKIP (claim не (mean_r, lower, base): dir=higher правил не порождает) |
    BAD_COND (условие вне RULE_FEATURES и словаря) | BLOCKED (cond_h в чёрном списке; проверяется, если передан conn)."""
    if not isinstance(lesson, dict) or (lesson.get("evidence") or {}).get("src") != "labels":
        return "NOT_LABELS"
    if lesson.get("status") != "hyp":
        return "NOT_HYP"
    if _claim(lesson) != SKIP_CLAIM:
        return "NOT_SKIP"
    if LC.validate_cond(lesson.get("cond")):
        return "BAD_COND"
    if conn is not None and blocked(conn, LR.cond_h(lesson["cond"], "lower"), now) is not None:
        return "BLOCKED"
    return None


def derive(lesson, policy, conn=None, now=None, env_norm=None):
    """Черновик правила skip_when из урока меток (rule_id выдаёт versions.create_with_trial) или None (см. derive_reason).
    policy — запись политики проверки (config/shadow_policies.json, для P2) -> scope; env_norm — окружение меток урока (по умолчанию из
    evidence.env_norm, иначе mainnet)."""
    if derive_reason(lesson, conn, now) is not None:
        return None
    scope = MF.policy_scope(policy)
    ev = lesson.get("evidence") or {}
    env = env_norm or ev.get("env_norm") or "mainnet"
    rule = {"rule_id": None, "kind": "skip_when", "cond": LR.canon_cond(lesson["cond"]), "param": {}, "scope": scope, "env_norm": env,
            "cond_h": LR.cond_h(lesson["cond"], "lower"), "from_lesson": lesson.get("lesson_id"),
            "policy_ver": (policy or {}).get("ver") if isinstance(policy, dict) else None}
    if LR.validate_rule(dict(rule, rule_id=None)):
        return None
    return rule


# ---------------------------------------------------------------- строки выборки окна

def row_feats(row):
    """Строка cluster_runs (feats — JSON или dict) -> признаки для learn_rules: RULE_FEATURES + ev_kind (колонка type) + side_rel (из feats)."""
    f = row.get("feats")
    if isinstance(f, str):
        try:
            f = json.loads(f)
        except ValueError:
            f = None
    out = dict(f) if isinstance(f, dict) else {}
    if out.get("ev_kind") is None:
        out["ev_kind"] = row.get("type")
    return out


def row_hit(rule, row):
    """Условие правила истинно для прогона в области применимости. Прогоны политики области (policy_id == scope.policy) по построению идут по её
    стороне, поэтому для них сторона относительно сигнала не проверяется; признак None — не хит (как на стороне мозга)."""
    feats = row_feats(row)
    sc = rule.get("scope") or {}
    if feats.get("side_rel") is None and sc.get("policy") is not None and row.get("policy_id") == sc.get("policy"):
        feats["side_rel"] = sc.get("side")
    return LR.evaluate(rule, feats) == "hit"


def _filled(row):
    return bool(row.get("filled"))


def _rows_of(win):
    if isinstance(win, dict):
        return list(win.get("rows") or [])
    return list(win or [])


def _key(row):
    return row.get("key") if row.get("key") is not None else id(row)


def hit_keys(win, rule, filled_only=True):
    """Ключи прогонов-хитов правила в выборке окна (по умолчанию только заполненные: незаполненные в метрики не входят)."""
    return set(_key(r) for r in _rows_of(win) if (_filled(r) or not filled_only) and row_hit(rule, r))


def overlap_keys(keys_a, keys_b):
    """Доля множества keys_a, входящая в keys_b (0.0 при пустом keys_a)."""
    a = set(keys_a)
    return (len(a & set(keys_b)) / float(len(a))) if a else 0.0


def overlap_rows(win, rule_a, rule_b, filled_only=True):
    """Пересечение по строкам: доля прогонов-хитов rule_a в выборке окна, которые отсекает rule_b (хит rule_b). Для условий на разных признаках
    это фактическая доля общих строк, а не «пересекаются ли области признаков» (lessons.overlap для правил не используется)."""
    rows = _rows_of(win)
    a = hit_keys(rows, rule_a, filled_only)
    return overlap_keys(a, hit_keys(rows, rule_b, filled_only))


# ---------------------------------------------------------------- достижимость

def test_cluster_count(win, rule, active=()):
    """Число кластеров теста испытания в выборке: кластеры (колонка cluster), где среди заполненных прогонов, не отсечённых активным набором
    (pass_active), есть предельный хит (hit=1) и база испытания (hit=0)."""
    acts = [r for r in active or () if isinstance(r, dict)]
    by = {}
    for row in _rows_of(win):
        if not _filled(row) or any(row_hit(a, row) for a in acts):
            continue
        st = by.setdefault(row.get("cluster"), [False, False])
        st[0 if row_hit(rule, row) else 1] = True
    return sum(1 for h, b in by.values() if h and b)


def estimate_reach(win, rule, cfg, active=(), lookback_days=None):
    """Дней до n_target кластеров теста по скорости в выборке окна за lookback_days (по умолчанию trial.reach_lookback_days; вызывающий передаёт
    выборку окна этих суток). Нет кластеров теста -> math.inf."""
    days = float(lookback_days if lookback_days is not None else cfg["trial"]["reach_lookback_days"])
    n = test_cluster_count(win, rule, active)
    if n <= 0 or days <= 0:
        return math.inf
    return float(cfg["trial"]["n_target"]) / (n / days)


def reachable(days, cfg):
    """n_target набирается за reach_factor × max_days (иначе UNREACHABLE_N, Requirement 9.4)."""
    return days <= float(cfg["trial"]["reach_factor"]) * float(cfg["trial"]["max_days"])


# ---------------------------------------------------------------- политика проверки

def policy_for(root, policy_id):
    """Запись политики проверки из config/shadow_policies.json (через labels.policies.load) или None."""
    from tb.labels import policies
    try:
        for p in policies.load(root):
            if p.get("id") == policy_id:
                return p
    except (ValueError, OSError, sqlite3.Error):
        return None
    return None
