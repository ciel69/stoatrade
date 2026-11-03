"""Уроки как гипотезы: `state/lessons.jsonl` (08 п. 4). Формулировки из шаблонов (cond + claim), без свободного текста.

Файл append-only с хеш-цепочкой (chain.py), каждая строка = полное состояние урока (rev растёт, kind new|update). Статусы:
hyp -> confirmed (n_closed>=30 и >=5 дней и >=2 режимов, либо >=15 с эффектом |diff|>t*se с поправкой Бонферрони на число активных уроков, либо решение
владельца), rejected (новые случаи опровергают или владелец), expired (срок годности без подтверждений), merged (дубликат по условию).
Подтверждённый урок остаётся подсказкой, в guard/TRADING НЕ превращается автоматически. conf затухает: conf_eff = conf * 0.5^(дни без подтверждений / half_life);
ниже hide_below урок скрыт от brief. Противоречащие активные уроки получают contradicts и множитель conf. Случаи с mem=off, exclude_from_stats, inj_suspect и
старой версией правил уроки не поднимают и не подтверждают.

R2: уроки меток (evidence.src="labels", создаёт цикл learnloop через create(..., created_by="code", evidence_src="labels", evidence_extra)) живут отдельно:
refresh их не видит, conf не затухает, expires_ts = null до confirmed, статусы меняет только transition; бюджет propose и лимит max_active считаются по группам.
"""
import math
import time

from tb import paths
from tb.core import expstats
from tb.core.filelock import FileLock
from tb.logic.learn_cond import cmp_val, matches_features  # noqa: F401  (реэкспорт: lessons.matches_features, lessons._cmp_val)
from tb.memory import agg, cases, chain, privacy, similar
from tb.memory import cfg as memcfg

SCHEMA = "tb.lesson/1"
OPS = ("=", ">=", "<=", "in")
STATUSES = ("hyp", "confirmed", "rejected", "expired", "merged")
ACTIVE = ("hyp", "confirmed")
BRAIN_REASONS = ("NEW", "ENOUGH_N", "CONTRADICTED", "EXPIRED", "MERGED", "OWNER", "EVICTED", "REFRESH")
# уроки меток (R2, Requirement 6.7): статусы меняет только transition по результату испытания
LABEL_REJECT_REASONS = ("TRIAL_REJECT", "NO_VERDICT", "UNREACHABLE_N", "LOSS_CAP", "SAMPLE_CHANGED", "CTX_CHANGED", "RESTORED", "BUDGET", "OWNER")
LABEL_REASONS = ("NEW", "TRIAL_PROMOTE") + LABEL_REJECT_REASONS + ("EXPIRED",)
REASONS = BRAIN_REASONS + tuple(r for r in LABEL_REASONS if r not in BRAIN_REASONS)
SOURCES = ("brain", "labels")
# допустимые переходы урока меток -> допустимые reason_code (confirmed -> rejected только OWNER)
LABEL_TRANSITIONS = {("hyp", "confirmed"): ("TRIAL_PROMOTE",), ("hyp", "rejected"): LABEL_REJECT_REASONS, ("confirmed", "expired"): ("EXPIRED",),
                     ("confirmed", "rejected"): ("OWNER",)}
LINE_MAX = 4096                                      # лимит строки lessons.jsonl (chain.append max_bytes)
LABELS_RESERVE = 256                                 # запас строки урока меток при создании на будущие переходы (history, expires_ts, conf)
LABELS_IDS_MAX = 5                                   # до 5 идентификаторов кластеров в evidence урока меток
LABELS_ID_KEYS = ("clusters", "cases")
METRICS = ("mean_r", "win_rate", "stop_hit_share", "err_share")
DIRS = ("lower", "higher")
VS = ("base", "zero")
CREATORS = ("code", "reviewer", "owner")
CAT_FEATURES = {"coin_class": cases.COIN_CLASSES, "setup": cases.SETUPS, "regime": cases.REGIMES, "utc_session": cases.SESSIONS, "side": cases.SIDES,
                "confirm_kind": cases.CONFIRM_KINDS}
BOOL_FEATURES = ("confirmed", "no_confirm_tag", "blackout")
NUM_FEATURES = ("range_5m_pct", "range_15m_pct", "ret_5m_pct", "turn_surge_5m", "chg_24h_pct", "spread_bps", "funding_pct", "stop_pct",
                "stop_dist_vs_range5", "rr_planned", "size_mult", "btc_ret_1h_pct")
FEATURES = tuple(CAT_FEATURES) + BOOL_FEATURES + NUM_FEATURES
FEATURE_RU = {"coin_class": "класс монеты", "setup": "сетап", "regime": "режим", "utc_session": "сессия", "side": "сторона", "confirm_kind": "тип подтверждения",
              "confirmed": "подтверждение", "no_confirm_tag": "метка no-confirm", "blackout": "блэкаут", "range_5m_pct": "диапазон 5м %", "range_15m_pct": "диапазон 15м %",
              "ret_5m_pct": "доходность 5м %", "turn_surge_5m": "всплеск оборота", "chg_24h_pct": "изменение 24ч %", "spread_bps": "спред bps",
              "funding_pct": "funding %", "stop_pct": "стоп %", "stop_dist_vs_range5": "стоп/диапазон5м", "rr_planned": "RR плана", "size_mult": "множитель размера",
              "btc_ret_1h_pct": "BTC 1ч %"}
METRIC_RU = {"mean_r": "средний R", "win_rate": "доля плюсовых", "stop_hit_share": "доля стопов", "err_share": "доля ошибок"}
DIR_RU = {"lower": "ниже", "higher": "выше"}
VS_RU = {"base": "базового", "zero": "нуля"}
STATUS_RU = {"hyp": "гипотеза", "confirmed": "подтверждено", "rejected": "отвергнуто", "expired": "устарело", "merged": "объединено"}
# коды lesson_hint ревьюера -> условие (проверяется по данным; урок создаётся только при статистических предпосылках)
LESSON_CODES = {
    "L-NO_CONFIRM": [{"feature": "confirmed", "op": "=", "value": False}],
    "L-WIDE_STOP": [{"feature": "stop_pct", "op": ">=", "value": 2.0}],
    "L-RANGE_GT_STOP": [{"feature": "stop_dist_vs_range5", "op": "<=", "value": 1.0}],
    "L-SPREAD": [{"feature": "spread_bps", "op": ">=", "value": 5.0}],
    "L-CHASE": [{"feature": "setup", "op": "=", "value": "momentum_chase"}],
    "L-BLACKOUT": [{"feature": "blackout", "op": "=", "value": True}],
    "L-MEME": [{"feature": "coin_class", "op": "=", "value": "meme"}],
    "L-BIG_RANGE": [{"feature": "range_5m_pct", "op": ">=", "value": 1.5}],
}
DAY = 86400.0


def lessons_path(root=None):
    return paths.state("lessons.jsonl", root=root)


def lessons_lock(root=None):
    return paths.state("lessons.lock", root=root)


# ---------------------------------------------------------------- условия

def _fmt(v):
    return str(v).lower() if isinstance(v, bool) else str(v)


def render_cond(cond):
    parts = []
    for c in cond:
        v = c["value"]
        if c["feature"] == "confirmed" and c["op"] == "=":
            parts.append("нет подтверждения" if v is False else "есть подтверждение")
            continue
        parts.append("%s %s %s" % (FEATURE_RU.get(c["feature"], c["feature"]), c["op"], ("{" + ",".join(_fmt(x) for x in v) + "}") if isinstance(v, list) else _fmt(v)))
    return " и ".join(parts)


def render_txt(l):
    cl = l["claim"]
    n = (l.get("evidence") or {}).get("n_closed", 0)
    return ("%s -> %s %s %s (%d случаев, %s)" % (render_cond(l["cond"]), METRIC_RU[cl["metric"]], DIR_RU[cl["dir"]], VS_RU[cl["vs"]], n, STATUS_RU[l["status"]]))[:140]


def validate_cond(cond):
    """-> список ошибок. Конъюнкция <= 3 условий из белого списка признаков."""
    errs = []
    if not isinstance(cond, list) or not (1 <= len(cond) <= 3):
        return ["cond: список из 1-3 условий"]
    seen = set()
    for c in cond:
        if not isinstance(c, dict) or set(c) != {"feature", "op", "value"}:
            errs.append("cond: элемент {feature, op, value}")
            continue
        f, op, v = c["feature"], c["op"], c["value"]
        if f not in FEATURES:
            errs.append("cond: признак %r вне белого списка" % f)
            continue
        if f in seen:
            errs.append("cond: признак %s повторён" % f)
        seen.add(f)
        if op not in OPS:
            errs.append("cond: op %r не из %s" % (op, list(OPS)))
            continue
        vals = v if op == "in" else [v]
        if op == "in" and (not isinstance(v, list) or not v or len(v) > 8):
            errs.append("cond: in требует непустой список (до 8)")
            continue
        for x in vals:
            if f in CAT_FEATURES and x not in CAT_FEATURES[f]:
                errs.append("cond: %s=%r не из словаря" % (f, x))
            elif f in BOOL_FEATURES and not isinstance(x, bool):
                errs.append("cond: %s нужен bool" % f)
            elif f in NUM_FEATURES and (isinstance(x, bool) or not isinstance(x, (int, float))):
                errs.append("cond: %s нужно число" % f)
        if (f in CAT_FEATURES or f in BOOL_FEATURES) and op in (">=", "<="):
            errs.append("cond: для %s допустимы = и in" % f)
    return errs


def validate_claim(cl):
    errs = []
    if not isinstance(cl, dict) or set(cl) - {"metric", "dir", "vs", "effect"}:
        return ["claim: {metric, dir, vs, effect}"]
    if cl.get("metric") not in METRICS:
        errs.append("claim.metric не из %s" % list(METRICS))
    if cl.get("dir") not in DIRS:
        errs.append("claim.dir не из %s" % list(DIRS))
    if cl.get("vs") not in VS:
        errs.append("claim.vs не из %s" % list(VS))
    if cl.get("vs") == "zero" and cl.get("metric") != "mean_r":
        errs.append("claim.vs=zero только для mean_r")
    e = cl.get("effect")
    if isinstance(e, bool) or not isinstance(e, (int, float)) or not (0 <= e <= 10):
        errs.append("claim.effect: число 0..10")
    return errs


_cmp_val = cmp_val                                   # прежнее имя (сравнение перенесено в tb.logic.learn_cond)


def matches(cond, case):
    return matches_features(cond, similar.vector(case))


def _domain(c):
    op, v = c["op"], c["value"]
    if op == "=":
        return ("set", {v})
    if op == "in":
        return ("set", set(v))
    return ("ge", v) if op == ">=" else ("le", v)


def _compat(a, b):
    ka, va = a
    kb, vb = b
    if ka == "set" and kb == "set":
        return bool(va & vb)
    if ka == "set" or kb == "set":
        s, (k, v) = (va, b) if ka == "set" else (vb, a)
        return any(isinstance(x, (int, float)) and not isinstance(x, bool) and ((x >= v) if k == "ge" else (x <= v)) for x in s)
    if ka == kb:
        return True
    lo, hi = (va, vb) if ka == "ge" else (vb, va)
    return lo <= hi


def overlap(cond_a, cond_b):
    """Условия пересекаются: все общие признаки совместимы (признаки только одного урока не мешают)."""
    da = dict((c["feature"], _domain(c)) for c in cond_a)
    for c in cond_b:
        if c["feature"] in da and not _compat(da[c["feature"]], _domain(c)):
            return False
    return True


def _cond_set(cond):
    return set((c["feature"], c["op"], str(c["value"])) for c in cond)


def jaccard(a, b):
    x, y = _cond_set(a), _cond_set(b)
    return len(x & y) / float(len(x | y)) if x | y else 0.0


# ---------------------------------------------------------------- статистика урока

def _val(metric, c):
    r = c["result"]
    if metric == "mean_r":
        return float(r["r"])
    if metric == "win_rate":
        return 1.0 if r["r"] > 0 else 0.0
    if metric == "stop_hit_share":
        return 1.0 if r.get("exit_reason") == "sl" else 0.0
    errs = [e for e in ((c.get("tags") or {}).get("err") or []) if e not in ("good", "good-process-bad-luck")]
    return 1.0 if errs else 0.0


def pool(all_cases, cfg):
    """Закрытые сделки, которым разрешено поднимать и подтверждать уроки (не исключены, mem!=off, версия правил допустима)."""
    ok = similar.allowed_rules(cfg)
    return [c for c in all_cases if agg.closed_r(c) is not None and cases.eligible(c, cfg) and (c.get("src") or {}).get("rules_ver") in ok]


def baseline(metric, vs, pool_cases):
    if vs == "zero":
        return 0.0
    xs = [_val(metric, c) for c in pool_cases]
    return sum(xs) / len(xs) if xs else None


def stats(lesson, all_cases, cfg, created_ts=None):
    """Показатели условия урока на пуле -> evidence (+служебные поля diff, n_new, mean_new, days, regimes)."""
    pl = pool(all_cases, cfg)
    cond, cl = lesson["cond"], lesson["claim"]
    m = [c for c in pl if matches(cond, c)]
    base = baseline(cl["metric"], cl["vs"], pl)
    xs = [_val(cl["metric"], c) for c in m]
    n = len(xs)
    mean = sum(xs) / n if n else None
    sd = agg._sd(xs)
    se = sd / math.sqrt(n) if sd is not None and n >= 3 else None
    rs = [c["result"]["r"] for c in m]
    base_r = baseline("mean_r", "base", pl)
    ts = sorted(c["ts"] for c in m)
    ct = created_ts
    new = [c for c in m if ct is not None and (similar.ts_epoch(c["ts"]) or 0) > ct]
    xn = [_val(cl["metric"], c) for c in new]
    days = set(c["ts"][:10] for c in m)
    regs = set((c.get("situation") or {}).get("regime") for c in m) - {None, "unknown"}
    ids = [c["case_id"] for c in sorted(m, key=lambda c: (c["ts"], c["case_id"]))][-20:]
    return {"cases": ids, "n": n, "n_closed": n, "mean_r": round(sum(rs) / n, 3) if n else None, "se": round(se, 3) if se is not None else None,
            "base_r": round(base_r, 3) if base_r is not None else None, "first_ts": ts[0] if ts else None, "last_ts": ts[-1] if ts else None,
            "_metric": mean, "_base": base, "_diff": (mean - base) if mean is not None and base is not None else None, "_n_new": len(new),
            "_mean_new": (sum(xn) / len(xn)) if xn else None, "_days": len(days), "_regimes": len(regs), "_pool_n": len(pl),
            "_last_new": max([similar.ts_epoch(c["ts"]) or 0.0 for c in new]) if new else None}


def supports(lesson, st):
    d = st["_diff"]
    if d is None or d == 0:
        return False
    return d < 0 if lesson["claim"]["dir"] == "lower" else d > 0


def _public_ev(st):
    ev = dict((k, v) for k, v in st.items() if not k.startswith("_"))
    ev["diff"] = round(st["_diff"], 3) if st["_diff"] is not None else None
    ev["days"] = st["_days"]
    return ev


def conf_for(lesson, st, cfg):
    lc = cfg.get("lessons") or {}
    n = st["n_closed"]
    d, se = st["_diff"], st["se"]
    if n == 0 or d is None:
        return 0.05
    n_eff = n * (abs(d) / (abs(d) + se) if se else 0.5)
    c = 1.0 - math.exp(-n_eff / 10.0)
    cap = lc.get("conf_cap_hyp", 0.9)
    if lesson.get("created_by") == "reviewer" and not (n >= lc.get("min_n_propose", 4) and supports(lesson, st)):
        cap = min(cap, lc.get("conf_cap_reviewer", 0.3))
    if lesson["status"] == "confirmed":
        cap = 0.95
    return round(min(c, cap), 3)


def is_labels(lesson):
    """Урок меток (R2): evidence.src == "labels". Уроки без src и с src="brain" — уроки мозга."""
    return ((lesson or {}).get("evidence") or {}).get("src") == "labels"


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def conf_from_numbers(n, diff, se, cap):
    """Та же формула, что conf_for: n_eff = n * |d| / (|d| + se) (без se — n / 2), conf = 1 - exp(-n_eff / 10), не выше cap."""
    if not n or diff is None:
        return 0.05
    n_eff = n * (abs(diff) / (abs(diff) + se) if se else 0.5)
    return round(min(1.0 - math.exp(-n_eff / 10.0), cap), 3)


def conf_eff(lesson, now, cfg):
    if is_labels(lesson):                                     # уроки меток не затухают: conf фиксирован при создании и переходе (Requirement 6.6)
        return round(lesson.get("conf", 0.0), 3)
    lc = cfg.get("lessons") or {}
    last = similar.ts_epoch((lesson.get("evidence") or {}).get("last_ts")) or similar.ts_epoch(lesson.get("created_ts")) or now
    age = max(0.0, now - last) / DAY
    c = lesson.get("conf", 0.0) * (0.5 ** (age / float(lc.get("half_life_days", 21))))
    if lesson.get("contradicts"):
        c *= lc.get("contradict_mult", 0.6)
    return round(c, 3)


# ---------------------------------------------------------------- хранилище

def read_all(root=None):
    """-> {lesson_id: последнее состояние} (битые строки пропущены)."""
    rows, _ = chain.read(lessons_path(root))
    out = {}
    for r in rows:
        if r.get("schema") == SCHEMA and r.get("lesson_id"):
            out[r["lesson_id"]] = r
    return out


def _id_after(ls):
    n = 0
    for k in ls:
        try:
            n = max(n, int(k.split("-")[1]))
        except (IndexError, ValueError):
            pass
    return "L-%04d" % (n + 1)


def _next_id(root):
    """Следующий идентификатор; уникален только под lessons.lock в одном захвате с _write (create так и делает)."""
    return _id_after(read_all(root))


def _write(root, rec):
    rec["txt"] = render_txt(rec)
    errs = privacy.privacy_scan(rec)
    if errs:
        raise ValueError("; ".join(errs))
    return chain.append(lessons_path(root), lessons_lock(root), rec, max_bytes=LINE_MAX)


def _hist(l, now, status, reason):
    h = list(l.get("history") or [])
    h.append({"ts": cases.iso(now), "status": status, "reason_code": reason})
    return h[-8:]


class LessonRefused(ValueError):
    """Отказ с кодом причины (таблица reason_code в design.md): BUDGET, EVIDENCE_TOO_BIG, BAD_SRC. Подкласс ValueError — прежние обработчики его ловят."""

    def __init__(self, code, msg):
        ValueError.__init__(self, "%s: %s" % (code, msg))
        self.code = code


def _line_bytes(root, rec):
    """Точный размер строки rec в lessons.jsonl (с txt, seq, prev, h); вызывать под lessons.lock, иначе seq может устареть."""
    seq, last_h, _nl = chain.tail(lessons_path(root))
    out = dict(rec, txt=render_txt(rec), seq=seq + 1, prev=last_h, h="0" * 16)
    return len(chain.canon(out).encode("utf-8"))


def _fit_labels(root, l):
    """Строка урока меток в LINE_MAX - LABELS_RESERVE: сначала списки идентификаторов до LABELS_IDS_MAX, затем усечение с конца; иначе EVIDENCE_TOO_BIG."""
    limit = LINE_MAX - LABELS_RESERVE
    ev = l["evidence"]
    for k in LABELS_ID_KEYS:
        if isinstance(ev.get(k), list):
            ev[k] = ev[k][:LABELS_IDS_MAX]
    while _line_bytes(root, l) > limit:
        lists = [k for k in LABELS_ID_KEYS if isinstance(ev.get(k), list) and ev[k]]
        if not lists:
            raise LessonRefused("EVIDENCE_TOO_BIG", "строка урока %d байт > %d" % (_line_bytes(root, l), limit))
        k = max(lists, key=lambda x: (len(ev[x]), x))
        ev[k] = ev[k][:-1]


def _labels_evidence(evidence_extra, lc):
    """-> (evidence, conf) урока меток из готового evidence_extra (числа holdout: n_closed, diff, se)."""
    if not isinstance(evidence_extra, dict):
        raise ValueError("evidence_extra: нужен словарь evidence для evidence_src=labels")
    ev = _json_copy(evidence_extra)
    ev["src"] = "labels"
    n, d, se = ev.get("n_closed"), ev.get("diff"), ev.get("se")
    errs = []
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        errs.append("evidence_extra.n_closed: целое >= 0")
    if not _num(d):
        errs.append("evidence_extra.diff: число")
    if se is not None and (not _num(se) or se < 0):
        errs.append("evidence_extra.se: число >= 0 или null")
    if errs:
        raise ValueError("; ".join(errs))
    return ev, conf_from_numbers(n, d, se, min(lc.get("conf_cap_hyp", 0.9), lc.get("conf_cap_reviewer", 0.3)))


def _json_copy(x):
    """Глубокая копия JSON-значения (evidence_extra вызывающего не меняется при усечении)."""
    if isinstance(x, dict):
        return dict((k, _json_copy(v)) for k, v in x.items())
    if isinstance(x, list):
        return [_json_copy(v) for v in x]
    return x


def create(root, cond, claim, created_by, all_cases, cfg, now, evidence_ids=None, status="hyp", evidence_src="brain", evidence_extra=None):
    """Создаёт урок-гипотезу. ValueError при неверном cond/claim.

    evidence_src="brain" (по умолчанию): evidence считается кодом по случаям мозга (stats), как прежде.
    evidence_src="labels" (R2, Requirement 5.6): только created_by="code" и status="hyp"; stats() не вызывается, evidence = evidence_extra + src="labels",
    conf по числам меток (не выше conf_cap_reviewer), expires_ts = None (срок появится при confirmed); лимит max_active считается по группе labels (BUDGET);
    размер строки проверяется до записи (усечение списков идентификаторов, иначе EVIDENCE_TOO_BIG).
    lesson_id выдаётся под lessons.lock в одном захвате с записью (Requirement 5.7)."""
    errs = validate_cond(cond) + validate_claim(claim)
    if created_by not in CREATORS:
        errs.append("created_by не из %s" % list(CREATORS))
    if evidence_src not in SOURCES:
        errs.append("evidence_src не из %s" % list(SOURCES))
    labels = evidence_src == "labels"
    if labels and created_by != "code":
        errs.append("урок меток создаёт только код (created_by=code)")
    if labels and status != "hyp":
        errs.append("урок меток создаётся только со статусом hyp")
    if errs:
        raise ValueError("; ".join(errs))
    lc = cfg.get("lessons") or {}
    l = {"schema": SCHEMA, "lesson_id": None, "rev": 1, "ts": cases.iso(now), "created_ts": now, "kind": "new", "status": status, "cond": cond, "claim": claim,
         "created_by": created_by, "contradicts": [], "merged_into": None, "rules_ver": cfg.get("rules_ver")}
    if labels:
        l["evidence"], l["conf"] = _labels_evidence(evidence_extra, lc)
        l["expires_ts"] = None
    else:
        st = stats(l, all_cases, cfg, created_ts=now)
        l["evidence"] = _public_ev(st)
        if evidence_ids:
            l["evidence"]["cases"] = [i for i in evidence_ids][:20]
        l["conf"] = conf_for(l, st, cfg)
        l["expires_ts"] = now + (lc.get("ttl_confirmed_days", 90) if status == "confirmed" else lc.get("ttl_hyp_days", 30)) * DAY
    l["history"] = [{"ts": l["ts"], "status": status, "reason_code": "NEW"}]
    with FileLock(lessons_lock(root), wait=chain.LOCK_WAIT_S):       # реентерабельна: chain.append берёт её повторно без ожидания
        ls = read_all(root)
        if labels:
            cap = lc.get("max_active", 200)
            if sum(1 for x in ls.values() if x["status"] in ACTIVE and is_labels(x)) >= cap:
                raise LessonRefused("BUDGET", "лимит активных уроков меток %d" % cap)
        l["lesson_id"] = _id_after(ls)
        if labels:
            _fit_labels(root, l)
        return _write(root, l)


def _update(root, l, now, status=None, reason="REFRESH", **kw):
    n = dict((k, v) for k, v in l.items() if k not in ("seq", "prev", "h"))
    n.update(kw)
    n.update({"rev": l["rev"] + 1, "kind": "update", "ts": cases.iso(now)})
    if status and status != l["status"]:
        n["status"] = status
        n["history"] = _hist(l, now, status, reason)
    return _write(root, n)


def _same(a, b):
    keys = ("status", "conf", "expires_ts", "contradicts", "merged_into", "evidence")
    return all(a.get(k) == b.get(k) for k in keys)


def transition(root, lesson_id, status, reason_code, now, cfg=None, nums=None):
    """Смена статуса урока меток (R2, Requirement 6.1): под lessons.lock перечитывает последнее состояние, проверяет переход и пишет через _update.

    Переходы: hyp -> confirmed (TRIAL_PROMOTE), hyp -> rejected (TRIAL_REJECT | NO_VERDICT | UNREACHABLE_N | LOSS_CAP | SAMPLE_CHANGED | CTX_CHANGED | RESTORED |
    BUDGET | OWNER), confirmed -> expired (EXPIRED), confirmed -> rejected (только OWNER). Недопустимый переход или урок мозга -> ValueError
    (урок мозга: LessonRefused BAD_SRC). Повтор с тем же целевым статусом ничего не пишет и возвращает None.
    confirmed: expires_ts = now + ttl_confirmed_days; nums={n_closed, diff, se} (числа испытания) пересчитывают conf той же формулой, не выше 0.95,
    без nums conf прежний. cfg по умолчанию — config/memory.json."""
    if status not in STATUSES:
        raise ValueError("status не из %s" % list(STATUSES))
    if cfg is None:
        cfg = memcfg.load(root)
    lc = cfg.get("lessons") or {}
    with FileLock(lessons_lock(root), wait=chain.LOCK_WAIT_S):
        l = read_all(root).get(lesson_id)
        if l is None:
            raise ValueError("нет урока %s" % lesson_id)
        if not is_labels(l):
            raise LessonRefused("BAD_SRC", "transition только для уроков меток (%s — урок мозга)" % lesson_id)
        if l["status"] == status:
            return None
        allowed = LABEL_TRANSITIONS.get((l["status"], status))
        if allowed is None:
            raise ValueError("недопустимый переход урока меток %s: %s -> %s" % (lesson_id, l["status"], status))
        if reason_code not in allowed:
            raise ValueError("переход %s -> %s: reason_code %r не из %s" % (l["status"], status, reason_code, list(allowed)))
        kw = {}
        if status == "confirmed":
            kw["expires_ts"] = now + lc.get("ttl_confirmed_days", 90) * DAY
            if nums is not None:
                if not isinstance(nums, dict) or not _num(nums.get("diff")) or isinstance(nums.get("n_closed"), bool) or not isinstance(nums.get("n_closed"), int):
                    raise ValueError("nums: {n_closed: int, diff: число, se: число|null}")
                kw["conf"] = conf_from_numbers(nums["n_closed"], nums["diff"], nums.get("se"), 0.95)
        return _update(root, l, now, status, reason_code, **kw)


def owner_set(root, lesson_id, action, cfg, now, into=None):
    """Решение владельца: confirm | reject | expire | merge (into=L-id). -> новое состояние.

    Уроки меток (R2, Requirement 5.12): допустим только reject (через transition, код OWNER; закрытие испытания и чёрный список — задача 16),
    остальное и merge в урок меток -> LessonRefused BAD_SRC."""
    ls = read_all(root)
    l = ls.get(lesson_id)
    if l is None:
        raise ValueError("нет урока %s" % lesson_id)
    if is_labels(l) or (action == "merge" and is_labels(ls.get(into))):
        if action != "reject" or not is_labels(l):
            raise LessonRefused("BAD_SRC", "команда %s неприменима к уроку меток (допустим только reject)" % action)
        return transition(root, lesson_id, "rejected", "OWNER", now, cfg=cfg) or read_all(root)[lesson_id]
    lc = cfg.get("lessons") or {}
    if action == "confirm":
        if l["status"] not in ACTIVE:
            raise ValueError("подтвердить можно только активный урок (статус %s)" % l["status"])
        return _update(root, l, now, "confirmed", "OWNER", expires_ts=now + lc.get("ttl_confirmed_days", 90) * DAY)
    if action == "reject":
        return _update(root, l, now, "rejected", "OWNER")
    if action == "expire":
        return _update(root, l, now, "expired", "OWNER")
    if action == "merge":
        if into not in ls or into == lesson_id:
            raise ValueError("merge: нужен существующий другой урок")
        return _update(root, l, now, "merged", "MERGED", merged_into=into)
    raise ValueError("action: confirm|reject|expire|merge")


def refresh(root, all_cases, cfg, now, dry_run=False):
    """Пересчёт активных уроков по случаям: evidence, conf, статус, срок, противоречия, слияние, вытеснение. -> список изменений [{lesson_id, change, reason}].
    Уроки меток (evidence.src="labels") не видны целиком: ни пересчёта, ни k_active, ни противоречий, слияния и вытеснения (Requirement 6.2);
    лимит max_active здесь — лимит группы уроков мозга."""
    lc = cfg.get("lessons") or {}
    ls = read_all(root)
    act = [l for l in ls.values() if l["status"] in ACTIVE and not is_labels(l)]
    changes = []
    k_active = max(1, len(act))
    allowed = similar.allowed_rules(cfg)
    new_state = {}
    for l in sorted(act, key=lambda x: x["lesson_id"]):
        st = stats(l, all_cases, cfg, created_ts=l.get("created_ts"))
        ev = _public_ev(st)
        status, reason = l["status"], "REFRESH"
        ttl = (lc.get("ttl_confirmed_days", 90) if l["status"] == "confirmed" else lc.get("ttl_hyp_days", 30)) * DAY
        expires = l.get("expires_ts") or now + ttl
        last = st["_last_new"]
        if last is not None and last + ttl > expires:
            expires = last + ttl                                      # новые подтверждающие случаи продлевают срок
        if l.get("rules_ver") not in allowed and (not last or last <= (similar.ts_epoch((l.get("evidence") or {}).get("last_ts")) or 0)):
            expires = min(expires, now + lc.get("old_rules_expire_days", 14) * DAY)
        n_new, mean_new, base = st["_n_new"], st["_mean_new"], st["_base"]
        lower = l["claim"]["dir"] == "lower"
        if expires <= now:
            status, reason = "expired", "EXPIRED"
        elif n_new >= lc.get("reject_n_new", 10) and base is not None and mean_new is not None and ((mean_new >= base) if lower else (mean_new <= base)):
            status, reason = "rejected", "CONTRADICTED"
        elif st["n_closed"] >= lc.get("reject_n_new", 10) and st["_diff"] is not None and not supports(l, st) and l["status"] == "hyp" and l.get("created_by") == "code":
            status, reason = "rejected", "CONTRADICTED"               # эффект сменил знак на достаточной выборке
        elif l["status"] == "hyp" and supports(l, st):
            ok_days, ok_reg = st["_days"] >= lc.get("confirm_days", 5), st["_regimes"] >= lc.get("confirm_min_regimes", 2)
            by_n = st["n_closed"] >= lc.get("confirm_n", 30)
            by_fast = False
            if st["n_closed"] >= lc.get("confirm_n_fast", 15) and st["se"]:
                tq = expstats.t_quantile(1.0 - expstats.bonferroni(k_active) / 2.0, st["n_closed"] - 1)
                by_fast = abs(st["_diff"]) > max(2.0, tq) * st["se"]
            conflict = any(ls.get(o, {}).get("status") == "confirmed" for o in (l.get("contradicts") or []))
            if (by_n or by_fast) and ok_days and ok_reg and not conflict:
                status, reason = "confirmed", "ENOUGH_N"
                expires = max(expires, now + lc.get("ttl_confirmed_days", 90) * DAY)
        cand = dict(l, status=status, evidence=ev, expires_ts=expires)
        cand["conf"] = conf_for(cand, st, cfg)
        new_state[l["lesson_id"]] = (cand, reason)
    # противоречия между активными (по новому статусу)
    live = dict((i, c) for i, (c, r) in new_state.items() if c["status"] in ACTIVE)
    for i, c in live.items():
        c["contradicts"] = sorted(j for j, d in live.items() if j != i and d["claim"]["metric"] == c["claim"]["metric"] and d["claim"]["dir"] != c["claim"]["dir"]
                                  and overlap(c["cond"], d["cond"]))
    # слияние дубликатов: остаётся урок с большим n (при равенстве старший id)
    ids = sorted(live, key=lambda i: (-live[i]["evidence"]["n_closed"], i))
    for a_i, a in enumerate(ids):
        if a not in live:
            continue
        for b in ids[a_i + 1:]:
            if b in live and live[a]["claim"]["metric"] == live[b]["claim"]["metric"] and live[a]["claim"]["dir"] == live[b]["claim"]["dir"] \
                    and jaccard(live[a]["cond"], live[b]["cond"]) > lc.get("merge_jaccard", 0.8):
                new_state[b] = (dict(live[b], status="merged", merged_into=a, contradicts=[]), "MERGED")
                del live[b]
    # вытеснение сверх лимита
    cap = lc.get("max_active", 200)
    if len(live) > cap:
        worst = sorted(live, key=lambda i: (conf_eff(live[i], now, cfg), i))[:len(live) - cap]
        for i in worst:
            new_state[i] = (dict(live[i], status="expired"), "EVICTED")
    for i, (c, reason) in sorted(new_state.items()):
        old = ls[i]
        if _same(old, c) and c["status"] == old["status"]:
            continue
        what = "status:%s->%s" % (old["status"], c["status"]) if c["status"] != old["status"] else "evidence"
        changes.append({"lesson_id": i, "change": what, "reason": reason})
        if not dry_run:
            _update(root, old, now, c["status"], reason, evidence=c["evidence"], conf=c["conf"], expires_ts=c["expires_ts"], contradicts=c.get("contradicts") or [],
                    merged_into=c.get("merged_into"))
    return changes


# ---------------------------------------------------------------- предложения кода

def _singles(cfg, pl):
    th = (cfg.get("lessons") or {}).get("thresholds") or {}
    le = (cfg.get("lessons") or {}).get("le_thresholds") or {}
    out = []
    for f, vals in CAT_FEATURES.items():
        out += [{"feature": f, "op": "=", "value": v} for v in vals if v not in ("unknown", "none") and any((c.get("situation") or {}).get(f) == v for c in pl)]
    out += [{"feature": "confirmed", "op": "=", "value": False}, {"feature": "blackout", "op": "=", "value": True}]
    for f, vs in th.items():
        out += [{"feature": f, "op": ">=", "value": v} for v in vs]
    for f, vs in le.items():
        out += [{"feature": f, "op": "<=", "value": v} for v in vs]
    return out


def propose(root, all_cases, cfg, now):
    """Кандидаты в уроки по устойчивым отклонениям mean_r от базы (не пишет). -> [{cond, claim, n, diff, share}]; уже имеющиеся условия и лимит в сутки учтены."""
    lc = cfg.get("lessons") or {}
    pl = pool(all_cases, cfg)
    if len(pl) < lc.get("min_n_propose", 4):
        return []
    base = sum(c["result"]["r"] for c in pl) / len(pl)
    existing = read_all(root)
    have = [_cond_set(l["cond"]) for l in existing.values() if l["status"] in ACTIVE]
    day0 = now - (now % DAY)
    made = sum(1 for l in existing.values() if l["created_by"] == "code" and not is_labels(l) and l.get("rev") == 1 and (l.get("created_ts") or 0) >= day0)
    budget = max(0, lc.get("max_new_per_day", 3) - made)
    singles = _singles(cfg, pl)
    sing_cases = dict((i, [c for c in pl if matches([s], c)]) for i, s in enumerate(singles))
    combos = [[s] for s in singles]
    for i, a in enumerate(singles):
        for j in range(i + 1, len(singles)):
            b = singles[j]
            if a["feature"] != b["feature"] and len(sing_cases[i]) >= lc.get("min_n_propose", 4) and len(sing_cases[j]) >= lc.get("min_n_propose", 4):
                combos.append([a, b])
    cands = {}
    for cond in combos:
        m = [c for c in pl if matches(cond, c)]
        if len(m) < lc.get("min_n_propose", 4) or _cond_set(cond) in have:
            continue
        rs = [c["result"]["r"] for c in m]
        diff = sum(rs) / len(rs) - base
        if abs(diff) < lc.get("min_diff_r", 0.5):
            continue
        lower = diff < 0
        share = sum(1 for r in rs if (r < base if lower else r > base)) / float(len(rs))
        if share < lc.get("min_share", 0.75):
            continue
        key = tuple(sorted(c["case_id"] for c in m))
        cur = cands.get(key)
        if cur is None or len(cond) < len(cur["cond"]):
            cands[key] = {"cond": cond, "claim": {"metric": "mean_r", "dir": "lower" if lower else "higher", "vs": "base", "effect": round(abs(diff), 2)},
                          "n": len(m), "diff": round(diff, 3), "share": round(share, 2)}
    ranked = sorted(cands.values(), key=lambda x: (-abs(x["diff"]) * math.sqrt(x["n"]), str(x["cond"])))
    return ranked[:budget]


def from_hint(hint, root, all_cases, cfg, now):
    """lesson_hint ревьюера -> урок, ТОЛЬКО если предпосылки выполнены по данным (n_closed >= min_n_propose и отклонение >= min_diff_r); иначе None."""
    lc = cfg.get("lessons") or {}
    cond = LESSON_CODES.get((hint or {}).get("code"))
    if not cond:
        return None
    pl = pool(all_cases, cfg)
    m = [c for c in pl if matches(cond, c)]
    if len(m) < lc.get("min_n_propose", 4) or not pl:
        return None
    base = sum(c["result"]["r"] for c in pl) / len(pl)
    diff = sum(c["result"]["r"] for c in m) / len(m) - base
    if abs(diff) < lc.get("min_diff_r", 0.5):
        return None
    if _cond_set(cond) in [_cond_set(l["cond"]) for l in read_all(root).values() if l["status"] in ACTIVE]:
        return None
    return create(root, cond, {"metric": "mean_r", "dir": "lower" if diff < 0 else "higher", "vs": "base", "effect": round(abs(diff), 2)}, "reviewer", all_cases, cfg, now)


def applicable(lessons, feats, now, cfg, limit=None):
    """Активные уроки, условия которых выполнены признаками запроса, по убыванию conf_eff (скрытые ниже hide_below не берутся).
    -> [{id, st, n, conf, txt, contradicts}] для brief и инструмента шлюза; у уроков меток дополнительно src="labels" (обе группы в одном списке).
    expires_ts здесь не читается (null = «без срока»): сроки исполняют refresh (мозг) и сверка статусов (метки)."""
    lc = cfg.get("lessons") or {}
    out = []
    for l in lessons:
        if l["status"] not in ACTIVE or not matches_features(l["cond"], feats):
            continue
        ce = conf_eff(l, now, cfg)
        if ce < lc.get("hide_below", 0.15):
            continue
        row = {"id": l["lesson_id"], "st": l["status"], "n": (l.get("evidence") or {}).get("n_closed", 0), "conf": ce, "txt": l.get("txt"),
               "contradicts": list(l.get("contradicts") or [])}
        if is_labels(l):
            row["src"] = "labels"
        out.append(row)
    out.sort(key=lambda x: (-x["conf"], x["id"]))
    return out[:limit or lc.get("brief_max", 3)]
