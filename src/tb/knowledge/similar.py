"""Поиск похожих по решениям мозга и по меткам (R2.9b, вторая итерация; Requirement 3.9-3.12). Только чтение базы; в досье и инструменты
мозга не подключён (это этап 3).

nearest(query, source)   ближайшие записи мерой tb.memory.similar.similarity (feature_sim, веса и шкалы config/memory.json не меняются):
                         source="brain"  - случаи мозга; предотбор: последние prefilter_cases (2000) по ts того же режима и типа, затем
                                           tb.memory.similar.select над предотбором (hits совпадают с прежним select поле в поле);
                         source="labels" - сигналы меток (signals); только при config/memory.json similar.labels=true; предотбор SQL по type,
                                           side (и utc_session, если задан), status=done, режиму данных, затем последние prefilter_signals (5000)
                                           по t0 через индекс (type, side, t0); исход - net_dir_pct (outcomes, горизонт h) и r_net политики;
                         source="both"   - оба источника, общий top-k по (-sim, свежее, источник, id).
                         Ответ компактный, <= max_bytes (2 КБ): лишние хвостовые попадания отбрасываются (trimmed).
brain_select(...)        полный ответ select над предотбором случаев с учётом отброшенных предотбором: при числе подходящих случаев
                         <= prefilter_cases равен similar.select(cases, ...) поле в поле.
label_vector(row)        признаки метки: числа события (ev: range_5m_pct, spread_bps, ...) и признаки на t0 (feat); None, имена из missing
                         и coin_class="unknown" - отсутствующий признак (снижает coverage, а не даёт ноль).
migrate_v2s(conn)        индекс ix_sig_type_side ON signals(type, side, t0) отдельной миграцией с собственным ключом meta.knowledge_similar_ver
                         (не labels_schema_ver, не knowledge_v2_ver); идемпотентна. Без индекса поиск работает (через ix_sig_type), но медленнее.
"""
import heapq
import json
import sqlite3
import time

from tb.labels import store as LST
from tb.memory import cases as CS
from tb.memory import cfg as memcfg
from tb.memory import similar as MS

SCHEMA = "tb.similar/1"
SOURCES = ("brain", "labels", "both")
PREFILTER_SIGNALS = 5000
PREFILTER_CASES = 2000
MAX_BYTES = 2048
DEFAULT_POLICY = "P2"
DEFAULT_H = 60

LABELS_OFF = "LABELS_OFF"            # similar.labels=false: источник меток выключен (по умолчанию до этапа 3)
NO_DB = "NO_DB"                      # базы меток нет или она недоступна
NO_SIG_TYPE = "NO_SIG_TYPE"          # в запросе нет типа сигнала (sig_type / features.ev_kind) - предотбор меток невозможен
PREFILTER_CAP = "prefilter_cap"      # причина в filtered: случай старше последних prefilter_cases

# ---- миграция индекса (свой ключ версии; не меняет labels_schema_ver и knowledge_v2_ver)
SIM_VER = 1
META_VER_KEY = "knowledge_similar_ver"
INDEX_NAME = "ix_sig_type_side"
V2S = ["CREATE INDEX IF NOT EXISTS %s ON signals(type, side, t0)" % INDEX_NAME]

_EV_NUM = ("range_5m_pct", "ret_5m_pct", "turn_surge_5m", "chg_24h_pct", "spread_bps", "funding_pct")   # tb.labels.capture.EV_NUM_FIELDS с весом
_FEAT_SKIP = ("levels",)


def _meta_ver(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
        return 0
    row = conn.execute("SELECT v FROM meta WHERE k=?", (META_VER_KEY,)).fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def has_index(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (INDEX_NAME,)).fetchone() is not None


def migrate_v2s(conn):
    """Индекс (type, side, t0) на signals в одной транзакции + meta.knowledge_similar_ver. Повторный вызов ничего не меняет.
    Нет таблицы signals -> KnowledgeMigrationError. Сбой -> ROLLBACK и KnowledgeMigrationError. -> версия после вызова."""
    from tb.knowledge import store2 as ST2
    if _meta_ver(conn) >= SIM_VER and has_index(conn):
        return SIM_VER
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as e:
        raise ST2.KnowledgeMigrationError(None, "не удалось начать миграцию индекса похожих: %s" % e) from e
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='signals'").fetchone():
            raise sqlite3.OperationalError("нет таблицы signals")
        for s in V2S:
            conn.execute(s)
        conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (META_VER_KEY, str(SIM_VER)))
        conn.execute("COMMIT")
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise ST2.KnowledgeMigrationError(None, "индекс похожих не создан: %s: %s" % (e.__class__.__name__, e)) from e
    return SIM_VER


# ---------------------------------------------------------------- общее

def _cfg_similar(cfg):
    return cfg.get("similar") or {}


def labels_enabled(cfg):
    return _cfg_similar(cfg).get("labels") is True


def _query_feats(query):
    """Как в select: признаки запроса без None; сторона long/short добавляется признаком side."""
    feats = dict((k, v) for k, v in (query.get("features") or {}).items() if v is not None)
    if query.get("side") in ("long", "short"):
        feats.setdefault("side", query["side"])
    return feats


def _thresholds(cfg, k, min_sim, min_coverage):
    sc = _cfg_similar(cfg)
    k = max(1, min(int(k or sc.get("k_default", 8)), int(sc.get("k_max", 20))))
    ms = sc.get("min_sim", 0.55) if min_sim is None else min_sim
    mc = sc.get("min_coverage", 0.6) if min_coverage is None else min_coverage
    return k, ms, mc


def _r(x, n):
    return round(x, n) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


# ---------------------------------------------------------------- случаи мозга

def _brain_opts(query, cfg, scope, types, mode, include_excluded):
    sc = _cfg_similar(cfg)
    return {"types": tuple(types or ("trade", "skip", "idea")), "scope": scope or sc.get("scope", "same_mode"),
            "mode": mode or query.get("mode") or "live", "include_excluded": include_excluded}


def prefilter_cases(cases, query, cfg, scope=None, types=None, mode=None, include_excluded=False, cap=PREFILTER_CASES):
    """-> (пул: последние cap по ts случаев того же режима и типа, {причина: n} отброшенных). Порядок проверок как в similar.prefilter
    (type, flags, excluded, mode), поэтому причины отброшенных совпадают с тем, что насчитал бы select; сверх cap - PREFILTER_CAP."""
    o = _brain_opts(query, cfg, scope, types, mode, include_excluded)
    types_, same, md, inc = o["types"], o["scope"] == "same_mode", o["mode"], o["include_excluded"]
    pool, dropped = [], {}
    for c in cases:
        if c.get("type") not in types_:
            why = "type"
        else:
            fl = c.get("flags") or ()
            if "inj_suspect" in fl or "dup_of_trade" in fl:
                why = "flags"
            elif c.get("exclude_from_stats") and not inc:
                why = "excluded"
            elif same and ((c.get("src") or {}).get("mode") or "live") != md:
                why = "mode"
            else:
                pool.append(c)
                continue
        dropped[why] = dropped.get(why, 0) + 1
    if len(pool) > cap:
        keep = heapq.nlargest(cap, pool, key=lambda c: (str(c.get("ts") or ""), str(c.get("case_id") or "")))
        dropped[PREFILTER_CAP] = len(pool) - len(keep)
        ids = set(id(c) for c in keep)
        pool = [c for c in pool if id(c) in ids]            # прежний порядок (select от него не зависит, но так проще сверять)
    return pool, dropped


def brain_select(cases, query, cfg, now, k=None, min_sim=None, min_coverage=None, scope=None, rules=None, types=None, mode=None,
                 max_age_days=None, as_of=None, include_excluded=False, cap=PREFILTER_CASES):
    """similar.select над предотбором; considered и filtered дополняются отброшенными предотбором (ответ как у select по всем случаям,
    пока подходящих случаев не больше cap)."""
    cases = list(cases)
    pool, dropped = prefilter_cases(cases, query, cfg, scope=scope, types=types, mode=mode, include_excluded=include_excluded, cap=cap)
    sel = MS.select(pool, query, cfg, now, k=k, min_sim=min_sim, min_coverage=min_coverage, scope=scope, rules=rules, types=types, mode=mode,
                    max_age_days=max_age_days, as_of=as_of, include_excluded=include_excluded)
    filt = dict(dropped)
    for kk, v in sel["filtered"].items():
        filt[kk] = filt.get(kk, 0) + v
    sel["filtered"] = filt
    sel["considered"] = len(cases)
    sel["pool"] = len(pool)
    return sel


def _brain_hit(h):
    c = h["case"]
    s, r = c.get("situation") or {}, c.get("result") or {}
    return {"src": "brain", "id": c["case_id"], "sim": h["sim"], "cov": h["coverage"], "n_used": h["n_used"], "ts": c.get("ts"),
            "type": c.get("type"), "side": s.get("side"), "r": _r(r.get("r"), 3)}


# ---------------------------------------------------------------- метки

def label_vector(feat, ev, side, missing=None):
    """Признаки метки -> вектор в именах памяти. feat/ev - словари (или JSON-строки) колонок signals; side - сторона сигнала."""
    if isinstance(feat, str):
        try:
            feat = json.loads(feat)
        except ValueError:
            feat = None
    if isinstance(ev, str):
        try:
            ev = json.loads(ev)
        except ValueError:
            ev = None
    if isinstance(missing, str):
        try:
            missing = json.loads(missing)
        except ValueError:
            missing = None
    miss = set(missing) if isinstance(missing, list) else set()
    v = {}
    if isinstance(ev, dict):
        for k in _EV_NUM:
            x = ev.get(k)
            if isinstance(x, (int, float)) and not isinstance(x, bool):
                v[k] = x
    if isinstance(feat, dict):
        for k, x in feat.items():
            if x is None or k in miss or k in _FEAT_SKIP or isinstance(x, (list, dict)):
                continue
            if k == "coin_class" and x == "unknown":
                continue
            v[k] = x
    if side in ("long", "short"):
        v["side"] = side
    return v


def _env_clause(mode):
    """Режим данных меток: testnet только для signals.env='testnet', иначе mainnet (как learnloop.clusters.env_norm); режимы не смешиваются."""
    if mode == "testnet":
        return "env = 'testnet'", ()
    return "(env IS NULL OR env <> 'testnet')", ()


def _json_cols(keys):
    """Только нужные запросу признаки достаём json_extract в SQL (без разбора всего JSON в Python): (sql-колонки, [(имя, источник)])."""
    cols, names = [], []
    for k in sorted(keys):
        if k == "side":
            continue
        if k in _EV_NUM:
            cols.append("json_extract(ev, '$.%s')" % k)
            names.append((k, "ev"))
        elif k.replace("_", "").isalnum():
            cols.append("json_extract(feat, '$.%s')" % k)
            names.append((k, "feat"))
    return cols, names


def label_candidates(conn, sig_type, side=None, utc_session=None, mode=None, as_of=None, keys=None, cap=PREFILTER_SIGNALS):
    """Предотбор меток SQL: type, side (если long/short), utc_session (если задан), status=done, режим данных, t0 < as_of;
    последние cap по t0. -> [(key, t0, side, вектор)] (вектор только по keys)."""
    where, args = ["type = ?"], [sig_type]
    if side in ("long", "short"):
        where.append("side = ?")
        args.append(side)
    if utc_session:
        where.append("json_extract(feat, '$.utc_session') = ?")
        args.append(utc_session)
    where.append("status = 'done'")
    env_sql, env_args = _env_clause(mode)
    where.append(env_sql)
    args.extend(env_args)
    if as_of is not None:
        where.append("t0 < ?")
        args.append(int(as_of))
    cols, names = _json_cols(keys or ())
    sql = "SELECT key, t0, side%s FROM signals WHERE %s ORDER BY t0 DESC LIMIT ?" % ("".join(", " + c for c in cols), " AND ".join(where))
    args.append(int(cap))
    out = []
    for row in conn.execute(sql, args):
        v = {}
        for (name, _src), x in zip(names, row[3:]):
            if x is None:
                continue
            if name == "coin_class" and x == "unknown":
                continue
            if name in MS.BOOL and isinstance(x, int):
                x = bool(x)
            v[name] = x
        if row[2] in ("long", "short"):
            v["side"] = row[2]
        out.append((row[0], row[1], row[2], v))
    return out


def _outcome(conn, key, side, h, policy_id):
    net = None
    if side in ("long", "short"):
        r = conn.execute("SELECT net_dir_pct FROM outcomes WHERE key=? AND h=? AND side=?", (key, int(h), side)).fetchone()
        net = r[0] if r else None
    p = conn.execute("SELECT r_net, filled FROM policy_runs WHERE key=? AND policy_id=? ORDER BY policy_ver DESC LIMIT 1", (key, policy_id)).fetchone()
    return _r(net, 4), (_r(p[0], 3) if p else None), (bool(p[1]) if p else None)


def labels_select(conn, query, cfg, k=None, min_sim=None, min_coverage=None, utc_session=None, mode=None, as_of=None, h=DEFAULT_H,
                  policy_id=DEFAULT_POLICY, cap=PREFILTER_SIGNALS):
    """-> {"hits": [компактные попадания], "pool", "n_hits", "filtered", "code"}."""
    k, ms, mc = _thresholds(cfg, k, min_sim, min_coverage)
    feats = _query_feats(query)
    sig_type = query.get("sig_type") or (query.get("features") or {}).get("ev_kind")
    if not sig_type:
        return {"hits": [], "pool": 0, "n_hits": 0, "filtered": {}, "code": NO_SIG_TYPE}
    md = mode or query.get("mode") or "live"
    w = cfg.get("weights") or {}
    rows = label_candidates(conn, sig_type, side=query.get("side"), utc_session=utc_session, mode=md, as_of=as_of,
                            keys=[x for x in feats if w.get(x, 0.0) > 0], cap=cap)
    filtered, scored = {}, []
    for key, t0, side, v in rows:
        s, cov, n_used = MS.similarity(feats, v, cfg)
        if n_used == 0 or cov < mc:
            filtered["coverage"] = filtered.get("coverage", 0) + 1
            continue
        if s < ms:
            filtered["min_sim"] = filtered.get("min_sim", 0) + 1
            continue
        scored.append((s, cov, n_used, key, t0, side))
    scored.sort(key=lambda x: (-x[0], -x[4], x[3]))
    hits = []
    for s, cov, n_used, key, t0, side in scored[:k]:
        net, r, filled = _outcome(conn, key, side, h, policy_id)
        hits.append({"src": "labels", "id": key, "sim": s, "cov": cov, "n_used": n_used, "t0": t0, "type": sig_type, "side": side,
                     "net_dir_pct": net, "r": r, "filled": filled})
    return {"hits": hits, "pool": len(rows), "n_hits": len(scored), "filtered": filtered, "code": None}


# ---------------------------------------------------------------- фасад

def _hit_t(hh):
    if hh["src"] == "labels":
        return float(hh.get("t0") or 0)
    return MS.ts_epoch(hh.get("ts")) or 0.0


def _fit(out, max_bytes):
    """Отбрасывает хвостовые попадания, пока JSON ответа длиннее max_bytes; число отброшенных - trimmed."""
    def size():
        return len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    while out["hits"] and size() > max_bytes:
        out["hits"].pop()
        out["trimmed"] = out.get("trimmed", 0) + 1
    return out


def nearest(query, source="brain", k=None, root=None, cfg=None, now=None, cases=None, conn=None, min_sim=None, min_coverage=None,
            scope=None, rules=None, types=None, mode=None, max_age_days=None, as_of=None, include_excluded=False, utc_session=None,
            h=DEFAULT_H, policy_id=DEFAULT_POLICY, max_bytes=None):
    """Ближайшие записи к запросу. query: {"features": {...}, "side": "long|short|watch|None", "mode": "live|paper|testnet",
    "sig_type": тип сигнала меток (или features.ev_kind)}. source: brain | labels | both.

    cases - свёрнутые случаи (по умолчанию tb.memory.cases.load(root), то есть текущее хранилище); conn - соединение с базой меток
    (по умолчанию labels.store.open_ro(root), закрывается здесь). Параметры отбора случаев - как у similar.select; as_of для меток - epoch.
    -> {"schema", "source", "k", "mode", "hits": [...], "n": {источник: размер пула}, "n_hits": {...}, "off"?: {источник: код}, "trimmed"?}
    Попадание: src, id (case_id | ключ сигнала), sim, cov, n_used, время (ts | t0), type, side, исход (r случая | net_dir_pct, r политики, filled)."""
    if source not in SOURCES:
        raise ValueError("source: %s" % "|".join(SOURCES))
    cfg = cfg if cfg is not None else memcfg.load(root)
    now = time.time() if now is None else now
    k_, _ms, _mc = _thresholds(cfg, k, min_sim, min_coverage)
    md = mode or query.get("mode") or "live"
    out = {"schema": SCHEMA, "source": source, "k": k_, "mode": md, "hits": [], "n": {}, "n_hits": {}}
    hits = []
    if source in ("brain", "both"):
        allc = CS.load(root) if cases is None else cases
        sel = brain_select(allc, query, cfg, now, k=k_, min_sim=min_sim, min_coverage=min_coverage, scope=scope, rules=rules, types=types,
                           mode=mode, max_age_days=max_age_days, as_of=as_of, include_excluded=include_excluded)
        hits.extend(_brain_hit(x) for x in sel["hits"])
        out["n"]["brain"] = sel["pool"]
        out["n_hits"]["brain"] = len(sel["hits"])
    if source in ("labels", "both"):
        code = None
        if not labels_enabled(cfg):
            code = LABELS_OFF
        else:
            own = conn is None
            c = None
            try:
                c = LST.open_ro(root) if own else conn
                if c is None:
                    code = NO_DB
                else:
                    lab_as_of = MS.ts_epoch(as_of) if isinstance(as_of, str) else as_of
                    ls = labels_select(c, query, cfg, k=k_, min_sim=min_sim, min_coverage=min_coverage, utc_session=utc_session, mode=md,
                                       as_of=lab_as_of, h=h, policy_id=policy_id)
                    code = ls["code"]
                    hits.extend(ls["hits"])
                    out["n"]["labels"] = ls["pool"]
                    out["n_hits"]["labels"] = ls["n_hits"]
            except sqlite3.Error:
                code = NO_DB
            finally:
                if own and c is not None:
                    c.close()
        if code:
            out["off"] = {"labels": code}
    hits.sort(key=lambda x: (-x["sim"], -_hit_t(x), x["src"], x["id"]))
    out["hits"] = hits[:k_]
    sc = cfg.get("tool") or {}
    return _fit(out, int(max_bytes or sc.get("max_bytes") or MAX_BYTES))
