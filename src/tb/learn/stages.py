"""Блоки «Уроки» и «Версии мозга до/после» страницы «Учится ли мозг» (R2.6, задача 18; Requirement 14; заглушки R0.2 — задача 11).

Источники (только чтение, на диск ничего не пишется):
  * база знаний state/knowledge.sqlite — соединение на один вызов через labels.store.open_ro (идёт восстановление -> KnowledgeRestoring ->
    блок no_data, reason KNOWLEDGE_RESTORING); таблицы цикла lesson_candidates, trials, rule_rows, rulebook_versions (версии читает
    knowledge.rulebook_view.read_versions);
  * уроки state/lessons.jsonl — memory.lessons.read_all (последнее состояние урока; источник evidence.src: labels | brain);
  * смены моделей и промптов state/brain/swaps.jsonl — читается напрямую (tb.brain слою learn недоступен), записи с result ok | signal;
  * сделки и случаи режима — из Ctx отчёта (load.brain_trades, load.cases); версия rulebook сделки — src.rulebook_ver её случая
    (refs.journal_id), без версии сделка относится к «до R2».

Без таблиц цикла (нет файла базы или таблиц v2a) оба блока — прежняя заглушка «появится на этапе 2». Формулы — tb.learn.stats и expstats
(mean_est, wilson_est, welch_summary, t_quantile), новых нет. Режимы не смешиваются: сделки берутся режима отчёта, уроки меток и воронка — по
env_norm режима (testnet -> testnet, paper и live -> mainnet). Уроки мозга режима не имеют (пул по всем режимам) и показываются в любом режиме
с пометкой brain_modes = "all". Кеш — кеш отчёта R0.2 (report.get), подпись источников дополнена базой, уроками и журналом смен (load).

Вердикт «до/после» (Requirement 14.2): few_data, если n < VERSION_MIN_N в любой из сторон; иначе worse, если верхняя граница 95% интервала
разницы (после − до, Уэлч) меньше 0; иначе not_worse. Общий вердикт страницы (verdict.decide) от этих блоков не зависит.
"""
import json
import sqlite3

from tb import paths
from tb.core import expstats
from tb.knowledge import rulebook_view as RV
from tb.labels import store as LST
from tb.learn import stats
from tb.memory import lessons as LS

STAGE = 2
ROADMAP = "R2.6"
NOTE = "появится на этапе 2"
TITLES = {"lessons": "Уроки", "versions": "Версии мозга до/после"}
MODES = ("paper", "testnet", "live")

CYCLE_TABLES = ("lesson_candidates", "trials", "rule_rows", "rulebook_versions")
LVL = 0.95
BEST_MIN_N = expstats.MIN_N                  # лучший урок — среди уроков с n >= 30
VERSION_MIN_N = 100                          # «данных мало» при n < 100 в любой из сторон
VERDICTS = ("few_data", "worse", "not_worse")
RESTORING = "KNOWLEDGE_RESTORING"
E_DB = "E_DB"
SWAP_RESULTS = ("ok", "signal")
SWAP_MERGE_S = 600                           # смены журнала ближе 10 минут друг к другу — одна смена (пакет use по ролям)
MAX_PAIRS = 12                               # последних пар в каждом разрезе
LIST_MAX = 6
NOTES = {
    "lessons": "Уроки по статусам и источникам, воронка цикла (кандидат -> hyp -> испытание -> confirmed -> правило) и недельный ряд; "
               "лучший урок — наибольший модуль effect среди активных уроков с n >= %d." % BEST_MIN_N,
    "lessons_none": "Уроков пока нет.",
    "versions": "Средний R сделок мозга (после издержек), число сделок и доля нарушений до и после смены rulebook, модели или промпта. "
                "Вердикт: данных мало при n < %d с любой стороны; хуже, если верхняя граница 95%% интервала разницы меньше 0; иначе не хуже. "
                "Сравнение до/после, не A/B: рынок между периодами различался." % VERSION_MIN_N,
    "versions_none": "Смен rulebook, моделей и промптов пока нет.",
    RESTORING: "База знаний восстанавливается: блок появится после восстановления.",
    E_DB: "База знаний не читается.",
}


def _stub(bid):
    return {"id": bid, "title": TITLES[bid], "status": "stage", "note": NOTE, "reason": None, "stage": STAGE, "roadmap": ROADMAP}


def env_norm(mode):
    """Как learnloop.clusters.env_norm: testnet -> testnet, остальное -> mainnet."""
    return "testnet" if mode == "testnet" else "mainnet"


def _ctx_ok(ctx):
    """Контекст отчёта (report.Ctx): есть root и допустимый режим. Иначе (None, словарь, чужой объект) — заглушка без обращения к файлам."""
    return ctx is not None and not isinstance(ctx, dict) and hasattr(ctx, "root") and getattr(ctx, "mode", None) in MODES


# ---------------------------------------------------------------- база знаний (open_ro на вызов)

def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _read_db(root, fn):
    """-> ("ok", fn(conn)) | ("stage", None) нет базы или таблиц цикла | ("no_data", reason) восстановление или ошибка чтения."""
    try:
        conn = LST.open_ro(root)
    except LST.KnowledgeRestoring:
        return "no_data", RESTORING
    except sqlite3.Error:
        return "no_data", E_DB
    if conn is None:
        return "stage", None
    try:
        if not all(_has_table(conn, t) for t in CYCLE_TABLES):
            return "stage", None
        return "ok", fn(conn)
    except LST.KnowledgeRestoring:
        return "no_data", RESTORING
    except sqlite3.Error:
        return "no_data", E_DB
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _count(conn, sql, args):
    return int(conn.execute(sql, args).fetchone()[0] or 0)


def funnel(conn, env):
    """Воронка цикла по env_norm: кандидаты -> уроки hyp (кандидат получил урок) -> испытания -> закрытые -> promote (confirmed) -> правила
    (правило хоть раз было active)."""
    return {"candidates": _count(conn, "SELECT COUNT(*) FROM lesson_candidates WHERE env_norm=?", (env,)),
            "hyp": _count(conn, "SELECT COUNT(*) FROM lesson_candidates WHERE env_norm=? AND lesson_id IS NOT NULL", (env,)),
            "trials": _count(conn, "SELECT COUNT(*) FROM trials WHERE env_norm=?", (env,)),
            "trials_closed": _count(conn, "SELECT COUNT(*) FROM trials WHERE env_norm=? AND state='closed'", (env,)),
            "confirmed": _count(conn, "SELECT COUNT(*) FROM trials WHERE env_norm=? AND verdict='promote'", (env,)),
            "rules": _count(conn, "SELECT COUNT(DISTINCT rule_id) FROM rule_rows WHERE env_norm=? AND status='active'", (env,))}


# ---------------------------------------------------------------- уроки

def _src(l):
    return "labels" if LS.is_labels(l) else "brain"


def _lesson_env(l):
    s = ((l.get("evidence") or {}).get("sample") or {})
    return s.get("env_norm") if isinstance(s, dict) else None


def lessons_for(all_lessons, mode):
    """Уроки режима: уроки меток своего env_norm (без env_norm — mainnet) и все уроки мозга."""
    env = env_norm(mode)
    out = []
    for l in all_lessons:
        if not isinstance(l, dict) or l.get("status") not in LS.STATUSES:
            continue
        if _src(l) == "labels" and (_lesson_env(l) or "mainnet") != env:
            continue
        out.append(l)
    return out


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _fnum(v):
    return stats._fin(v)


def effect_est(l):
    """95% t-интервал разности урока: diff ± t(0.975, n-1) * se по evidence (n_closed, diff, se); n < 2 или нет se — без интервала."""
    ev = l.get("evidence") or {}
    n, d, se = _int(ev.get("n_closed")) or 0, _fnum(ev.get("diff")), _fnum(ev.get("se"))
    if d is None or se is None or n < 2:
        return stats.est(d, n=n, lvl=LVL)
    h = stats._tq(1.0 - (1.0 - LVL) / 2.0, n - 1) * se
    return stats.est(d, d - h, d + h, n, LVL)


def best_lesson(ls):
    """Активный урок (hyp | confirmed) с наибольшим |claim.effect| среди уроков с n >= BEST_MIN_N; равенство — меньший lesson_id. -> dict | None."""
    best = None
    for l in ls:
        if l.get("status") not in LS.ACTIVE:
            continue
        n = _int((l.get("evidence") or {}).get("n_closed")) or 0
        e = _fnum((l.get("claim") or {}).get("effect"))
        if n < BEST_MIN_N or e is None:
            continue
        key = (-abs(e), str(l.get("lesson_id")))
        if best is None or key < best[0]:
            best = (key, l)
    if best is None:
        return None
    l = best[1]
    cl = l.get("claim") or {}
    n = _int((l.get("evidence") or {}).get("n_closed")) or 0
    return {"lesson_id": l.get("lesson_id"), "src": _src(l), "status": l.get("status"), "n": n, "small": n < expstats.MIN_N,
            "effect": abs(_fnum(cl.get("effect"))), "metric": cl.get("metric"), "dir": cl.get("dir"), "vs": cl.get("vs"),
            "diff": effect_est(l), "text": str(l.get("txt") or "")}


def _week_idx(wr, epoch):
    if epoch is None:
        return None
    for i, w in enumerate(wr):
        if w["start"] <= epoch < w["end"]:
            return i
    return None


def _epoch(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    return stats.iso_epoch(v)


def weekly(ls, wr):
    """Ряд по неделям UTC: созданные уроки по источникам, переходы в confirmed и rejected (по history урока)."""
    rows = [{"week": w["week"], "partial": w["partial"], "created_brain": 0, "created_labels": 0, "confirmed": 0, "rejected": 0} for w in wr]
    for l in ls:
        i = _week_idx(wr, _epoch(l.get("created_ts")))
        if i is not None:
            rows[i]["created_" + _src(l)] += 1
        for h in l.get("history") or []:
            if not isinstance(h, dict) or h.get("status") not in ("confirmed", "rejected"):
                continue
            j = _week_idx(wr, _epoch(h.get("ts")))
            if j is not None:
                rows[j][h["status"]] += 1
    return rows


def lessons_block(all_lessons, fun, mode, now, weeks):
    ls = lessons_for(all_lessons, mode)
    by_status = []
    for st in LS.STATUSES:
        b = sum(1 for l in ls if l["status"] == st and _src(l) == "brain")
        lb = sum(1 for l in ls if l["status"] == st and _src(l) == "labels")
        by_status.append({"status": st, "brain": b, "labels": lb, "total": b + lb})
    by_source = {"brain": sum(1 for l in ls if _src(l) == "brain"), "labels": sum(1 for l in ls if _src(l) == "labels")}
    out = {"id": "lessons", "title": TITLES["lessons"], "status": "ok", "reason": None, "note": NOTES["lessons"],
           "env_norm": env_norm(mode), "brain_modes": "all", "n_lessons": len(ls), "by_status": by_status, "by_source": by_source,
           "funnel": fun, "weeks": weekly(ls, stats.week_range(now, weeks)), "best": best_lesson(ls), "best_min_n": BEST_MIN_N}
    if not ls and not fun.get("candidates"):
        out.update(status="no_data", reason="no_lessons", note=NOTES["lessons_none"])
    return out


def _all_lessons(root):
    return sorted(LS.read_all(root).values(), key=lambda l: str(l.get("lesson_id")))


def lessons(ctx=None):
    """Блок «Уроки» (Requirement 14.1). Без контекста отчёта или без таблиц цикла — заглушка этапа."""
    if not _ctx_ok(ctx):
        return _stub("lessons")
    env = env_norm(ctx.mode)
    st, val = _read_db(ctx.root, lambda conn: funnel(conn, env))
    if st == "stage":
        return _stub("lessons")
    if st == "no_data":
        return {"id": "lessons", "title": TITLES["lessons"], "status": "no_data", "reason": val, "note": NOTES[val]}
    weeks = ((getattr(ctx, "cfg", None) or {}).get("weeks")) or 8
    return lessons_block(_all_lessons(ctx.root), val, ctx.mode, int(getattr(ctx, "now", 0) or 0), weeks)


# ---------------------------------------------------------------- версии до/после

def read_swaps(root):
    """Записи state/brain/swaps.jsonl (seq int, ts число, result ok|signal) по возрастанию ts. Нет файла — []."""
    out = []
    try:
        f = open(paths.state("brain", "swaps.jsonl", root=root), encoding="utf-8")
    except OSError:
        return out
    with f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict) or _int(r.get("seq")) is None or _fnum(r.get("ts")) is None:
                continue
            if r.get("result") not in SWAP_RESULTS or r.get("kind") not in ("use", "rollback", "reload"):
                continue
            out.append(r)
    out.sort(key=lambda r: (r["ts"], r["seq"]))
    return out


def _swap_kind(r):
    return "prompt" if r.get("kind") == "reload" else "model"


def swap_changes(swaps, merge_s=SWAP_MERGE_S):
    """Смены из журнала: подряд идущие записи ближе merge_s секунд — одна смена (ts первой). -> [{"ts", "kinds", "seqs", "targets", "to"}]."""
    out = []
    for r in swaps:
        if out and r["ts"] - out[-1]["_last"] <= merge_s:
            ch = out[-1]
        else:
            ch = {"ts": int(r["ts"]), "kinds": [], "seqs": [], "targets": [], "to": [], "_last": r["ts"]}
            out.append(ch)
        ch["_last"] = r["ts"]
        ch["seqs"].append(r["seq"])
        k = _swap_kind(r)
        if k not in ch["kinds"]:
            ch["kinds"].append(k)
        for t in r.get("target") or []:
            if isinstance(t, str) and t not in ch["targets"]:
                ch["targets"].append(t)
        to = r.get("to")
        if isinstance(to, str) and to not in ("-", "") and to not in ch["to"]:
            ch["to"].append(to)
    for ch in out:
        ch.pop("_last")
        for k in ("targets", "to"):
            ch[k] = ch[k][:LIST_MAX]
    return out


def _trade_epoch(t):
    return stats.iso_epoch(t.get("ts_open"))


def _case_map(cases):
    """{journal_id: случай} закрытых случаев режима."""
    out = {}
    for c in cases or []:
        if isinstance(c, dict):
            j = (c.get("refs") or {}).get("journal_id")
            if isinstance(j, str) and j not in out:
                out[j] = c
    return out


def _rb_ver(c):
    v = ((c or {}).get("src") or {}).get("rulebook_ver")
    return v if isinstance(v, str) and v else None


def side(trades, cmap):
    """Сторона сравнения: n сделок, средний R (95% t), доля нарушений (rule_followed = no среди известных, Уилсон), сводка для Уэлча."""
    rs = [float(t["r"]) for t in trades if _fnum(t.get("r")) is not None]
    rf = [((cmap.get(t.get("id")) or {}).get("tags") or {}).get("rule_followed") for t in trades]
    known = [x for x in rf if x in ("yes", "no", "partial")]
    m = stats.mean_est(rs, LVL)
    sd = expstats._mean_sd(rs)[1] if len(rs) > 1 else None
    return {"n": len(rs), "r": m, "viol": stats.wilson_est(sum(1 for x in known if x == "no"), len(known), LVL), "n_rf": len(known),
            "small": len(rs) < expstats.MIN_N, "_se": (sd / (len(rs) ** 0.5)) if sd is not None else None}


def verdict_of(before, after, diff):
    """Requirement 14.2: few_data (n < VERSION_MIN_N в любой стороне) / worse (верхняя граница 95% интервала после − до < 0) / not_worse."""
    if before["n"] < VERSION_MIN_N or after["n"] < VERSION_MIN_N:
        return "few_data"
    hi = diff.get("hi") if diff.get("hi") is not None else diff.get("v")
    return "worse" if hi is not None and hi < 0 else "not_worse"


def compare(before_tr, after_tr, cmap):
    b, a = side(before_tr, cmap), side(after_tr, cmap)
    w = stats.welch_summary(a["r"]["v"], a["_se"], a["n"], b["r"]["v"], b["_se"], b["n"], lvl=LVL)
    diff = stats.est(w["diff"], w["lo"], w["hi"], a["n"] + b["n"], LVL)
    v = verdict_of(b, a, diff)
    b.pop("_se")
    a.pop("_se")
    return {"before": b, "after": a, "diff": diff, "p": w["p"], "verdict": v}


def rulebook_pairs(vers, trades, cmap):
    """Пары соседних версий rulebook: до — сделки со src.rulebook_ver = предыдущая версия, после — = следующая."""
    by_ver = {}
    for t in trades:
        by_ver.setdefault(_rb_ver(cmap.get(t.get("id"))), []).append(t)
    out = []
    for prev, cur in zip(vers, vers[1:]):
        row = {"kind": "rulebook", "from": prev.get("ver"), "to": cur.get("ver"), "ts": stats.iso_epoch(cur.get("ts")),
               "reason_code": cur.get("reason_code"), "rules_n": len(cur.get("rule_ids") or []) if isinstance(cur.get("rule_ids"), list) else 0}
        row.update(compare(by_ver.get(prev.get("ver"), []), by_ver.get(cur.get("ver"), []), cmap))
        out.append(row)
    return out[-MAX_PAIRS:], len(by_ver.get(None, []))


def swap_pairs(changes, trades, cmap):
    """Смены моделей и промптов: до — сделки, открытые между предыдущей сменой и этой, после — между этой и следующей (по ts_open)."""
    timed = [(e, t) for e, t in ((_trade_epoch(t), t) for t in trades) if e is not None]
    out = []
    for i, ch in enumerate(changes):
        lo = changes[i - 1]["ts"] if i > 0 else None
        hi = changes[i + 1]["ts"] if i + 1 < len(changes) else None
        before = [t for e, t in timed if e < ch["ts"] and (lo is None or e >= lo)]
        after = [t for e, t in timed if e >= ch["ts"] and (hi is None or e < hi)]
        row = {"kind": "prompt" if ch["kinds"] == ["prompt"] else "model", "kinds": list(ch["kinds"]), "ts": ch["ts"], "seqs": ch["seqs"][:LIST_MAX],
               "targets": ch["targets"], "to": ch["to"]}
        row.update(compare(before, after, cmap))
        out.append(row)
    return out[-MAX_PAIRS:]


def versions_block(vers, swaps, trades, cases, mode):
    trades = list(trades or [])
    cmap = _case_map(cases)
    rb, n_pre = rulebook_pairs(vers, trades, cmap)
    sw = swap_pairs(swap_changes(swaps), trades, cmap)
    rows = rb + sw
    out = {"id": "versions", "title": TITLES["versions"], "note": NOTES["versions"], "mode": mode, "min_n": VERSION_MIN_N, "lvl": LVL,
           "rulebook": rb, "swaps": sw, "n_trades": len(trades), "n_pre_r2": n_pre,
           "rulebook_vers": [v.get("ver") for v in vers][-MAX_PAIRS:]}
    if not rows:
        out.update(status="no_data", reason="no_changes", note=NOTES["versions_none"])
    elif all(r["verdict"] == "few_data" for r in rows):
        out.update(status="few_data", reason="n_lt_min")
    else:
        out.update(status="ok", reason=None)
    return out


def versions(ctx=None):
    """Блок «Версии мозга до/после» (Requirement 14.2). Без контекста отчёта или без таблиц цикла — заглушка этапа."""
    if not _ctx_ok(ctx):
        return _stub("versions")
    st, val = _read_db(ctx.root, RV.read_versions)
    if st == "stage":
        return _stub("versions")
    if st == "no_data":
        return {"id": "versions", "title": TITLES["versions"], "status": "no_data", "reason": val, "note": NOTES[val]}
    return versions_block(val, read_swaps(ctx.root), getattr(ctx, "trades", None), getattr(ctx, "cases", None), ctx.mode)


def build(ctx=None):
    """-> {"lessons": блок, "versions": блок}."""
    return {"lessons": lessons(ctx), "versions": versions(ctx)}
