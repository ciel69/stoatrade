"""Диагноз R2.0 «почему уроков 0»: воронка кодового `lessons.propose` по случаям и причины по данным (Requirement 1).

Только чтение: cases.jsonl, lessons.jsonl, config/memory.json (и alpha из config/learnloop.json, если файл есть). Ничего не пишет в state/;
единственная допустимая запись — отчёт по --out внутри runtime/ (write_out).

Воронка повторяет отбор `lessons.propose`: пул -> одиночные условия и пары -> n >= min_n_propose -> не дубль активного урока -> |diff| >= min_diff_r ->
share >= min_share -> одно условие на набор случаев -> бюджет max_new_per_day. Коды отсева: POOL_TOO_SMALL, BELOW_N, DUP_ACTIVE, BELOW_DIFF,
BELOW_SHARE, BUDGET. Причины: DATA_VOLUME, DATA_QUALITY, MULTIPLICITY, NONE (несколько сразу допустимо). WIRING (никакой периодический процесс не вызывает
propose) командой не вычисляется: это установленный факт диагноза в design.md, раздел «Диагноз R2.0».
"""
import json
import os

from tb import paths
from tb.memory import agg, cases, chain, lessons

DROP_CODES = ("POOL_TOO_SMALL", "BELOW_N", "DUP_ACTIVE", "BELOW_DIFF", "BELOW_SHARE", "BUDGET")
REASONS = ("DATA_VOLUME", "DATA_QUALITY", "MULTIPLICITY", "NONE")
QUALITY_FEATURES = ("regime", "setup", "stop_pct", "rr_planned")
QUALITY_MIN = 0.9
CAND_N_MIN = 15                    # «у кандидатов n < 15» (Requirement 1.3)
ALPHA_DEFAULT = 0.05
UNKNOWN = (None, "unknown")
WIRING_NOTE = "WIRING не вычисляется: установленный факт (design.md, «Диагноз R2.0»)"
DAY = 86400.0


def _alpha(root):
    """alpha из config/learnloop.json (verify.alpha), иначе 0.05. Битый или отсутствующий файл -> умолчание."""
    try:
        with open(paths.config("learnloop.json", root=root), encoding="utf-8") as f:
            a = (json.load(f).get("verify") or {}).get("alpha")
        if isinstance(a, (int, float)) and not isinstance(a, bool) and 0 < a < 1:
            return float(a)
    except (OSError, ValueError, AttributeError):
        pass
    return ALPHA_DEFAULT


def _singles(cfg, pl):
    """Одиночные условия так же, как их строит lessons.propose (категории, встреченные в пуле, confirmed=false, blackout=true, пороги конфига)."""
    lc = cfg.get("lessons") or {}
    out = []
    for f, vals in lessons.CAT_FEATURES.items():
        out += [{"feature": f, "op": "=", "value": v} for v in vals if v not in ("unknown", "none") and any((c.get("situation") or {}).get(f) == v for c in pl)]
    out += [{"feature": "confirmed", "op": "=", "value": False}, {"feature": "blackout", "op": "=", "value": True}]
    for f, vs in (lc.get("thresholds") or {}).items():
        out += [{"feature": f, "op": ">=", "value": v} for v in vs]
    for f, vs in (lc.get("le_thresholds") or {}).items():
        out += [{"feature": f, "op": "<=", "value": v} for v in vs]
    return out


def _cond_key(cond):
    return frozenset((c["feature"], c["op"], json.dumps(c["value"], sort_keys=True)) for c in cond)


def _known(c, f):
    return (c.get("situation") or {}).get(f) not in UNKNOWN


def _lessons_state(root):
    path = lessons.lessons_path(root)
    if not os.path.isfile(path):
        return {"file": False, "n": 0, "broken": 0, "by_status": dict((s, 0) for s in lessons.STATUSES), "line": "lessons: 0 (файла нет)"}, {}
    rows, broken = chain.read(path)
    last = {}
    for r in rows:
        if r.get("schema") == lessons.SCHEMA and r.get("lesson_id"):
            last[r["lesson_id"]] = r
    by = dict((s, 0) for s in lessons.STATUSES)
    for l in last.values():
        by[l.get("status")] = by.get(l.get("status"), 0) + 1
    line = "lessons: %d (%s)" % (len(last), ", ".join("%s %d" % (s, by[s]) for s in lessons.STATUSES))
    return {"file": True, "n": len(last), "broken": broken, "by_status": by, "line": line}, last


def diagnose(root, cfg, now, alpha=None):
    """-> отчёт {funnel, drops, quality, k, alpha, reasons, lessons, notes}. Детерминирован при тех же данных и now; ничего не пишет."""
    lc = cfg.get("lessons") or {}
    min_n, min_diff, min_share = lc.get("min_n_propose", 4), lc.get("min_diff_r", 0.5), lc.get("min_share", 0.75)
    confirm_n, cap_day = lc.get("confirm_n", 30), lc.get("max_new_per_day", 3)
    alpha = _alpha(root) if alpha is None else float(alpha)
    _, broken = cases.read_raw(root)
    all_cases = cases.load(root)
    closed = [c for c in all_cases if agg.closed_r(c) is not None]
    pl = lessons.pool(all_cases, cfg)
    les, last = _lessons_state(root)

    quality = {}
    for f in QUALITY_FEATURES:
        k = sum(1 for c in pl if _known(c, f))
        quality[f] = {"n": k, "share": round(k / float(len(pl)), 3) if pl else None}
    drops = dict((c, 0) for c in DROP_CODES)
    funnel = {"cases": len(all_cases), "closed": len(closed), "pool": len(pl), "regime_known": quality["regime"]["n"], "conditions": 0,
              "pass_n": 0, "pass_diff": 0, "pass_share": 0, "unique": 0, "would_create": 0}
    day0 = now - (now % DAY)
    made = sum(1 for l in last.values() if l.get("created_by") == "code" and l.get("rev") == 1 and (l.get("created_ts") or 0) >= day0)
    funnel["created_today"] = made
    funnel["lessons"] = les["by_status"]
    k_tested, cand_ns = 0, []
    if len(pl) < min_n:
        drops["POOL_TOO_SMALL"] = 1
    else:
        base = sum(c["result"]["r"] for c in pl) / len(pl)
        have = set(_cond_key(l["cond"]) for l in last.values() if l.get("status") in lessons.ACTIVE and isinstance(l.get("cond"), list))
        singles = _singles(cfg, pl)
        sing_n = [sum(1 for c in pl if lessons.matches([s], c)) for s in singles]
        combos = [[s] for s in singles]
        for i, a in enumerate(singles):
            for j in range(i + 1, len(singles)):
                if a["feature"] != singles[j]["feature"] and sing_n[i] >= min_n and sing_n[j] >= min_n:
                    combos.append([a, singles[j]])
        funnel["conditions"] = len(combos)
        uniq = {}
        for cond in combos:
            m = [c for c in pl if lessons.matches(cond, c)]
            if len(m) < min_n:
                drops["BELOW_N"] += 1
                continue
            funnel["pass_n"] += 1
            if _cond_key(cond) in have:
                drops["DUP_ACTIVE"] += 1
                continue
            k_tested += 1
            rs = [c["result"]["r"] for c in m]
            diff = sum(rs) / len(rs) - base
            if abs(diff) < min_diff:
                drops["BELOW_DIFF"] += 1
                continue
            funnel["pass_diff"] += 1
            lower = diff < 0
            share = sum(1 for r in rs if (r < base if lower else r > base)) / float(len(rs))
            if share < min_share:
                drops["BELOW_SHARE"] += 1
                continue
            funnel["pass_share"] += 1
            key = tuple(sorted(c["case_id"] for c in m))
            cur = uniq.get(key)
            if cur is None or len(cond) < len(cur):
                uniq[key] = cond
        funnel["unique"] = len(uniq)
        cand_ns = sorted(len(k) for k in uniq)
        budget = max(0, cap_day - made)
        funnel["would_create"] = min(budget, len(uniq))
        drops["BUDGET"] = len(uniq) - funnel["would_create"]

    reasons = []
    if len(pl) < confirm_n or (cand_ns and max(cand_ns) < CAND_N_MIN):
        reasons.append("DATA_VOLUME")
    if pl and any(q["share"] < QUALITY_MIN for q in quality.values()):
        reasons.append("DATA_QUALITY")
    if k_tested * alpha >= 1:
        reasons.append("MULTIPLICITY")
    if not reasons:
        reasons.append("NONE")
    notes = [WIRING_NOTE]
    if broken:
        notes.append("cases.jsonl: битых строк пропущено %d" % broken)
    if les.get("broken"):
        notes.append("lessons.jsonl: битых строк пропущено %d" % les["broken"])
    return {"schema": "tb.lesson_diagnose/1", "ts": cases.iso(now), "funnel": funnel, "drops": drops, "quality": quality, "quality_min": QUALITY_MIN,
            "k": k_tested, "alpha": alpha, "k_alpha": round(k_tested * alpha, 3), "cand_n": cand_ns, "confirm_n": confirm_n, "cases_broken": broken,
            "reasons": reasons, "lessons": les, "notes": notes}


def render(rep):
    """Отчёт -> строки для человека (порядок воронки как в Requirement 1.1)."""
    f, d, q = rep["funnel"], rep["drops"], rep["quality"]
    pct = lambda x: "-" if x is None else "%.0f%%" % (100 * x)
    L = ["случаев: %d%s" % (f["cases"], (" (битых строк cases.jsonl пропущено %d)" % rep["cases_broken"]) if rep["cases_broken"] else ""),
         "закрытых сделок: %d" % f["closed"],
         "в пуле уроков: %d" % f["pool"],
         "с известным режимом: %d" % f["regime_known"],
         "заполнение в пуле: " + ", ".join("%s %d (%s)" % (k, v["n"], pct(v["share"])) for k, v in q.items())]
    if d["POOL_TOO_SMALL"]:
        L.append("POOL_TOO_SMALL: пул меньше min_n_propose, воронка остановлена")
    else:
        L += ["условий-кандидатов: %d" % f["conditions"],
              "прошли min_n_propose: %d (BELOW_N %d, DUP_ACTIVE %d)" % (f["pass_n"], d["BELOW_N"], d["DUP_ACTIVE"]),
              "прошли min_diff_r: %d (BELOW_DIFF %d)" % (f["pass_diff"], d["BELOW_DIFF"]),
              "прошли min_share: %d (BELOW_SHARE %d); различных по набору случаев: %d, их n: %s" % (f["pass_share"], d["BELOW_SHARE"], f["unique"], rep["cand_n"] or "-"),
              "создано бы сейчас: %d (BUDGET %d)" % (f["would_create"], d["BUDGET"])]
    L.append("создано кодом за сутки: %d" % f["created_today"])
    L.append(rep["lessons"]["line"])
    L.append("K=%d alpha=%s K*alpha=%s" % (rep["k"], rep["alpha"], rep["k_alpha"]))
    L.append("причины: %s" % ", ".join(rep["reasons"]))
    L += rep["notes"]
    return L


def out_path(path, root=None):
    """Путь отчёта --out: только внутри runtime/ корня; относительный путь считается от корня проекта. ValueError иначе."""
    base = os.path.abspath(root or paths.root())
    rt = os.path.realpath(paths.runtime(root=base))
    p = os.path.realpath(path if os.path.isabs(path) else os.path.join(base, path))
    try:
        inside = os.path.commonpath([os.path.normcase(rt), os.path.normcase(p)]) == os.path.normcase(rt) and os.path.normcase(p) != os.path.normcase(rt)
    except ValueError:                                     # разные диски Windows
        inside = False
    if not inside:
        raise ValueError("--out: только файл внутри runtime/ (получено %s)" % path)
    return p


def write_out(rep, path, root=None):
    p = out_path(path, root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, p)
    return p
