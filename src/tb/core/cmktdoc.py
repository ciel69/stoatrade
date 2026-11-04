"""Читатель сводного рынка для потребителей (R1.7, задача 16; Requirements 3.3, 14.1, 15.4, 16.1–16.4, 21.2, 21.3; design.md,
«tb.core.cmktdoc», «Время сигнала и строка признаков»; свойства 1, 15).

Мозг, шлюз, метки, цикл обучения и команды читают сводный рынок только здесь (или файлами runtime/). Модуль не импортирует
`tb.cmkt` (слой core): формат файлов берётся из `tb.logic.cmkt` (кодирование строк, колонки снимка), конфиг `config/cmkt.json`
читается сам (только разделы `sync`, `store`, `cov`, `brief`, `tool`, с умолчаниями `tb.cmkt.conf`; строгая проверка — дело
сервиса). Ни одна функция не бросает исключений.

- `latest(root, now=None, max_age_s=5.0)` — снимок `runtime/cmkt/snap.json` не старше `max_age_s` (по `ts` снимка) или None.
- `features_at(root, sym, t0, now=None, wait=False)` — строка признаков сигнала (Glossary): ярус `deep` (шаг 1 с), затем `light`
  (шаг 5 с), `row_ts = floor(t0 / step) × step − step < t0`; строка берётся только с меткой своего яруса и только если
  `wr_ts ≤ row_ts + sync.accept_s`. Файл суток `state/cmkt/YYYYMMDD.sqlite` открывается `mode=ro` на один вызов (ротация на Windows
  удаляет файлы), строка предыдущих суток — из файла предыдущих суток. Ответ:
      {"cm": {"v": cm_ver файла, "tier", "focus_s", "row_ts", <признаки без None>}}   — строка есть и записана вовремя;
      {"cm_missing": "late" | "no_row" | "not_focus" | "db" | "no_t0"}                 — строки нет (no_t0 — t0 не число);
      {"pending": True, "row_ts": N, "until": срок}                                    — строки ещё нет, срок ожидания
          `row_ts + accept_s + 1` по реальным часам не наступил, а у монеты есть строка предыдущего шага яруса (монета в фокусе).
  При `wait=True` ожидание `pending` (повтор раз в 0.25 с) до срока, затем окончательный ответ (`pending` не возвращается).
- `brief_block(root, sym, now=None, cfg=None)` — блок `cmkt` досье (Requirement 16) в бюджете `brief.max_tokens` (`len(json) / 4`).
- `detail(root, sym, parts=None, now=None, cfg=None)` — ответ инструмента `get_market_detail` (Requirement 21) не длиннее
  `tool.max_bytes`.

`cfg` в `brief_block` и `detail` — словарь config/cmkt.json (результат загрузчика сервиса или его разделы); None — файл читается здесь.
"""
import json
import math
import os
import sqlite3
import time
from datetime import datetime, timezone
from urllib.request import pathname2url

from tb import paths
from tb.logic import cmkt as L

TIER_DEEP, TIER_LIGHT = 1, 2                  # tb.cmkt.store.TIERS
STEPS = ((TIER_DEEP, "deep", 1), (TIER_LIGHT, "light", 5))
SNAP_MAX_AGE_S = 5.0
WAIT_POLL_S = 0.25
CODES_MISSING = ("not_focus", "no_row", "late", "db", "no_t0")
DEFAULTS = {"sync": {"accept_s": 3}, "store": {"dir": "state/cmkt"}, "cov": dict(L.DEFAULTS["cov"]),
            "brief": {"mode": "compact", "max_tokens": 90, "syms": 1, "experiment": False},
            "tool": {"max_bytes": 2048, "per_hour": 30}}
BRIEF_EXTRA = ("BTC", "ETH")                  # brief.syms 2–3: добавляются BTC, затем ETH
PARTS = ("book", "cvd", "oi", "liq", "funding", "venues")
TRUNC_ORDER = ("venues", "liq", "oi", "funding", "cvd", "book")     # Requirement 21.3
BRIEF_FLAGS = ("low_cov", "liq_partial", "stale_ex", "warmup")
STALE_CODES = ("STALE", "LAG", "SILENT", "DOWN", "RESYNC")         # рынок монеты выпал по свежести -> пометка stale_ex

_clock = time.time                            # подменяются в тестах
_sleep = time.sleep
_cfg_cache = {}                               # root -> (sig, cfg)


# ---------------------------------------------------------------- конфиг и пути
def _merge(raw):
    out = {}
    for sec, d in DEFAULTS.items():
        v = dict(d)
        if isinstance(raw, dict) and isinstance(raw.get(sec), dict):
            v.update(raw[sec])
        out[sec] = v
    return out


def _load_cfg(root):
    path = paths.config("cmkt.json", root=root)
    try:
        st = os.stat(path)
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    hit = _cfg_cache.get(path)
    if hit is not None and hit[0] == sig:
        return hit[1]
    raw = None
    if sig is not None:
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = None
    cfg = _merge(raw)
    _cfg_cache[path] = (sig, cfg)
    return cfg


def _cfg(root, cfg=None):
    return _merge(cfg) if isinstance(cfg, dict) else _load_cfg(root)


def _int_in(v, lo, hi, dflt):
    try:
        x = int(v)
    except (TypeError, ValueError, OverflowError):
        return dflt
    return max(lo, min(hi, x))


def store_dir(root=None, cfg=None):
    """Каталог рядов: `store.dir` относительно корня (как tb.cmkt.store.store_dir)."""
    d = str((cfg or {}).get("store", {}).get("dir") or "state/cmkt")
    parts = [p for p in d.replace("\\", "/").split("/") if p]
    if parts and parts[0] == "state":
        return paths.state(*parts[1:], root=root)
    return os.path.join(root or paths.root(), *parts)


def day_of(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y%m%d")


def _open_ro(path):
    if not os.path.isfile(path):
        return None
    return sqlite3.connect("file:%s?mode=ro" % pathname2url(os.path.abspath(path)), uri=True, timeout=1.0)


# ---------------------------------------------------------------- снимок
def _read_snap(root):
    try:
        with open(paths.runtime("cmkt", "snap.json", root=root), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("schema") == L.SNAP_SCHEMA and L.num(d.get("ts")) is not None else None


def latest(root=None, now=None, max_age_s=SNAP_MAX_AGE_S):
    """Снимок runtime/cmkt/snap.json, если он не старше max_age_s по `ts` (None — без проверки возраста), иначе None. Не бросает."""
    try:
        d = _read_snap(root)
        if d is None:
            return None
        if max_age_s is not None:
            now = _clock() if now is None else float(now)
            if now - float(d["ts"]) > float(max_age_s):
                return None
        return d
    except Exception:                                    # noqa: BLE001 — читатель не бросает
        return None


# ---------------------------------------------------------------- строка признаков сигнала
def _file_rows(root, cfg, day, sym, want):
    """want: {(ts, tier)} одного дня -> (файл есть, {(ts, tier): (wr_ms, focus_s, f, meta)}). Ошибки базы пробрасываются (-> db)."""
    conn = _open_ro(os.path.join(store_dir(root, cfg), "%s.sqlite" % day))
    if conn is None:
        return False, {}
    try:
        sid = conn.execute("SELECT sym_id FROM syms WHERE sym=?", (sym,)).fetchone()
        if sid is None:
            return True, {}
        tss = sorted(set(t for t, _ in want))
        q = "SELECT ts, tier, wr_ms, focus_s, f FROM rows WHERE sym_id=? AND ts IN (%s)" % ",".join("?" * len(tss))
        recs = conn.execute(q, [sid[0]] + tss).fetchall()
        out = {}
        meta = None
        for ts, tier, wr_ms, focus_s, f in recs:
            if (ts, tier) not in want:
                continue
            if meta is None:
                meta = dict(conn.execute("SELECT k, v FROM meta").fetchall())
            out[(ts, tier)] = (wr_ms, focus_s, f, meta)
        return True, out
    finally:
        conn.close()


def _decode(f, meta):
    feats = json.loads(meta.get("feats") or "[]")
    if feats:
        names = tuple(x[0] for x in feats)
        scales = dict((x[0], x[1]) for x in feats)
        return L.decode_f(f, names, scales)
    return L.decode_f(f)


def _once(root, sym, t0, now, cfg):
    accept = float(cfg["sync"].get("accept_s", 3))
    plan = []                                            # (tier, имя, шаг, row_ts, ts предыдущего шага)
    for tier, name, step in STEPS:
        rts = L.row_ts(t0, step)
        plan.append((tier, name, step, rts, rts - step))
    by_day = {}
    for tier, _, _, rts, prev in plan:
        for ts in (rts, prev):
            by_day.setdefault(day_of(ts), set()).add((ts, tier))
    got, any_file = {}, False
    for day, want in sorted(by_day.items()):
        have, rows = _file_rows(root, cfg, day, sym, want)
        any_file = any_file or have
        got.update(rows)
    if not any_file:
        return {"cm_missing": "db"}                      # сервис не пишет ряды или файл суток удалён
    prev_any = False
    for tier, name, step, rts, prev in plan:
        rec = got.get((rts, tier))
        if rec is not None:
            wr_ms, focus_s, f, meta = rec
            if not L.row_on_time(rts, rts + wr_ms / 1000.0, accept):
                return {"cm_missing": "late"}
            return {"cm": L.ev_cm(_decode(f, meta), name, focus_s, rts, cm_ver=meta.get("cm_ver") or L.CM_VER)}
        if (prev, tier) in got:
            prev_any = True
            until = L.row_deadline(rts, accept)
            if now < until:
                # строка яруса ещё может появиться: ждём её, не переходя к другому ярусу (иначе ответ зависел бы от момента чтения)
                return {"pending": True, "row_ts": rts, "until": until}
    return {"cm_missing": "no_row" if prev_any else "not_focus"}


def features_at(root, sym, t0, now=None, wait=False):
    """Признаки строки сигнала монеты sym на время сигнала t0 (секунды UTC). Ответ — см. описание модуля. Не бросает."""
    try:
        t0f = L.num(t0)
        if t0f is None:
            return {"cm_missing": "no_t0"}
        if not isinstance(sym, str) or not sym:
            return {"cm_missing": "not_focus"}
        cfg = _load_cfg(root)
        now = _clock() if now is None else float(now)
        r = _once(root, sym, t0f, now, cfg)
        if not wait or not r.get("pending"):
            return r
        start = _clock()
        cap = start + float(cfg["sync"].get("accept_s", 3)) + 2.0          # t0 из будущего не держит вызов дольше срока ожидания
        until = min(float(r["until"]), cap)
        while r.get("pending"):
            t = _clock()
            if t >= until:
                break
            _sleep(min(WAIT_POLL_S, until - t))
            r = _once(root, sym, t0f, _clock(), cfg)
        if r.get("pending"):
            r = _once(root, sym, t0f, max(_clock(), float(r["until"])), cfg)
        return r
    except Exception:                                    # noqa: BLE001 — сбой чтения -> db, исключений нет
        return {"cm_missing": "db"}


# ---------------------------------------------------------------- общие мелочи снимка
def _rnd(x, nd):
    v = L.num(x)
    if v is None:
        return None
    v = round(v, nd)
    return int(v) if nd == 0 or v == int(v) else v


def _musd(x, nd=2):
    v = L.num(x)
    return None if v is None else _rnd(v / 1e6, nd)


def _put(d, k, v):
    if v is not None and v != [] and v != {}:
        d[k] = v


def _jlen(obj):
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False))


def _age(doc, now):
    now = _clock() if now is None else float(now)
    return max(0, int(math.floor(now - float(doc["ts"]))))


def _band_ok(row, cov_cfg):
    return isinstance(row, dict) and L.ok_cov(int(row.get("n_ex") or 0), L.num(row.get("cov")), cov_cfg)


def _cov_min(s):
    """Покрытие одним числом: минимум по сводке монеты, полосе ±0.5 %, CVD 5 мин и ликвидациям (что есть)."""
    vals = [L.num(s.get("cov"))]
    vals.append(L.num((((s.get("bands") or {}).get("all") or {}).get("05") or {}).get("cov")))
    vals.append(L.num((((s.get("cvd") or {}).get("5m") or {}).get("all") or {}).get("cov")))
    vals.append(L.num((s.get("liq") or {}).get("liq_cov")))
    vals = [v for v in vals if v is not None]
    return _rnd(min(vals), 2) if vals else None


def _flags(s):
    fl = set(x for x in s.get("flags") or () if x in BRIEF_FLAGS)
    if any(isinstance(m, dict) and m.get("code") in STALE_CODES for m in (s.get("mk") or {}).values()):
        fl.add("stale_ex")
    return [x for x in BRIEF_FLAGS if x in fl]


# ---------------------------------------------------------------- блок досье (Requirement 16)
def _brief_fields(s, mode, cov_cfg):
    """Поля монеты в порядке вида (усечение — с конца списка). None -> поля нет."""
    f = s.get("f") or {}
    g = f.get
    out = []
    l5l, l5s = L.num(g("cm_liq_5m_long_musd")), L.num(g("cm_liq_5m_short_musd"))
    out.append(("imb_05", _rnd(g("cm_imb_05_pct"), 1)))
    out.append(("cvd_ps5", _rnd(g("cm_cvd_ps_5m_pct"), 1)))
    out.append(("oi5", _rnd(g("cm_oi_chg_5m_pct"), 3)))
    out.append(("liq5_net", _rnd(l5l - l5s, 3) if l5l is not None and l5s is not None else None))
    out.append(("fund_w", _rnd(g("cm_fund_w_pct"), 5)))
    out.append(("prem_ps", _rnd(g("cm_prem_ps_bps"), 1)))
    out.append(("pulled15_net", None))   # в снимке и рядах нет числа снятых стен по сторонам (только сумма) — см. отчёт задачи 16
    out.append(("n_ex", s.get("n_ex") if isinstance(s.get("n_ex"), int) else None))
    out.append(("cov", _cov_min(s)))
    out.append(("lead", s.get("lead") if isinstance(s.get("lead"), str) else None))
    out.append(("flags", _flags(s) or None))
    if mode == "full":
        out.append(("cvd5", _rnd(g("cm_cvd_5m_pct"), 1)))
        out.append(("cvd15", _rnd(g("cm_cvd_15m_pct"), 1)))
        out.append(("oi15", _rnd(g("cm_oi_chg_15m_pct"), 3)))
        out.append(("oi_musd", _rnd(g("cm_oi_musd"), 1)))
        out.append(("liq5", [_rnd(l5l, 3), _rnd(l5s, 3)] if l5l is not None and l5s is not None else None))
        lq = s.get("liq") or {}
        h1 = None
        if g("cm_liq_1h_net_musd") is not None and L.num(lq.get("l1h_long")) is not None and L.num(lq.get("l1h_short")) is not None:
            h1 = [_musd(lq.get("l1h_long"), 3), _musd(lq.get("l1h_short"), 3)]
        out.append(("liq1h", h1))
        out.append(("prem_cb", _rnd(g("cm_prem_cb_bps"), 1)))
        bands = {}
        for lab in ("05", "1", "2"):
            b = ((s.get("bands") or {}).get("all") or {}).get(lab)
            if _band_ok(b, cov_cfg) and L.num(b.get("bid")) is not None and L.num(b.get("ask")) is not None:
                bands[lab] = {"imb": _rnd(b.get("imb"), 1), "musd": _musd(L.num(b["bid"]) + L.num(b["ask"]))}
        out.append(("bands", bands or None))
        walls = {}
        for side, key in (("b", "bid"), ("a", "ask")):
            ws = [w for w in s.get("walls") or () if isinstance(w, dict) and w.get("side") == side][:2]
            rows = [{"usd": _rnd(w.get("usd"), 0), "dist_pct": _rnd(w.get("dist_pct"), 2), "n_ex": w.get("n_ex")} for w in ws]
            rows = [dict((k, v) for k, v in r.items() if v is not None) for r in rows]
            if rows:
                walls[key] = rows
        out.append(("walls", walls or None))
    return [(k, v) for k, v in out if v is not None]


def brief_block(root, sym, now=None, cfg=None):
    """Блок `cmkt` досье: {"age_s", "syms": {SYM: {поля вида}}} не длиннее brief.max_tokens (len(json)/4); снимок старше 5 с —
    {"stale_s": N}; снимка нет — None. Монета вне снимка — {"warn": "NOT_FOCUS"}. Усечение: лишние монеты (с конца), затем поля с
    конца списка вида. Не бросает."""
    try:
        c = _cfg(root, cfg)
        b = c["brief"]
        mode = b.get("mode") if b.get("mode") in ("compact", "full") else "compact"
        max_tokens = _int_in(b.get("max_tokens"), 40, 600, 90)
        n_syms = _int_in(b.get("syms"), 1, 3, 1)
        doc = _read_snap(root)
        if doc is None:
            return None
        age = _age(doc, now)
        if age > SNAP_MAX_AGE_S:
            return {"stale_s": age}
        if not isinstance(sym, str) or not sym:
            return None
        order = [sym]
        for x in BRIEF_EXTRA:
            if len(order) >= n_syms:
                break
            if x not in order:
                order.append(x)
        coins = []
        for x in order[:n_syms]:
            s = L.snap_sym(doc, x)
            coins.append((x, _brief_fields(s, mode, c["cov"]) if s is not None else [("warn", "NOT_FOCUS")]))

        def build():
            return {"age_s": age, "syms": dict((x, dict(fl)) for x, fl in coins)}
        blk = build()
        limit = max_tokens * 4
        while _jlen(blk) > limit:
            if len(coins) > 1:
                coins.pop()
            elif coins and coins[0][1]:
                coins[0][1].pop()
            else:
                break
            blk = build()
        return blk
    except Exception:                                    # noqa: BLE001
        return None


# ---------------------------------------------------------------- инструмент get_market_detail (Requirement 21)
def _part_book(s, cov_cfg):
    bands = {}
    for kind in ("perp", "spot", "all"):
        rows = {}
        for lab in ("05", "1", "2"):
            r = ((s.get("bands") or {}).get(kind) or {}).get(lab)
            if _band_ok(r, cov_cfg):
                rows[lab] = dict((k, v) for k, v in (("bid", _musd(r.get("bid"))), ("ask", _musd(r.get("ask"))),
                                                     ("imb", _rnd(r.get("imb"), 1))) if v is not None)
        _put(bands, kind, rows)
    walls = {}
    for side, key in (("b", "bid"), ("a", "ask")):
        ws = [w for w in s.get("walls") or () if isinstance(w, dict) and w.get("side") == side][:3]
        rows = [dict((k, v) for k, v in (("musd", _musd(w.get("usd"))), ("dist_pct", _rnd(w.get("dist_pct"), 2)),
                                         ("n_ex", w.get("n_ex")), ("age_s", _rnd(w.get("age_s"), 0))) if v is not None) for w in ws]
        _put(walls, key, rows)
    out = {}
    _put(out, "bands", bands)
    _put(out, "walls", walls)
    return out


def _part_cvd(s, cov_cfg):
    out = {}
    fs = L.num(s.get("focus_s")) or 0.0
    for lab, w in L.WINDOWS:
        if fs < w:
            continue
        kinds = {}
        for kind in ("perp", "spot", "all"):
            r = ((s.get("cvd") or {}).get(lab) or {}).get(kind)
            if isinstance(r, dict) and L.ok_cov(int(r.get("n_ex") or 0), L.num(r.get("cov")), cov_cfg):
                v = dict((k, x) for k, x in (("pct", _rnd(r.get("pct"), 1)), ("musd", _musd(r.get("net"), 3))) if x is not None)
                _put(kinds, kind, v)
        _put(out, lab, kinds)
    return out


def _part_oi(s):
    f = s.get("f") or {}
    out = {}
    _put(out, "musd", _rnd(f.get("cm_oi_musd"), 1))
    for lab, _ in L.WINDOWS:
        _put(out, "chg_%s_pct" % lab, _rnd(f.get("cm_oi_chg_%s_pct" % lab), 3))
    by = {}
    for m, r in sorted((s.get("mk") or {}).items()):
        if isinstance(r, dict) and r.get("code") is None:
            _put(by, m, _musd(r.get("oi_usd"), 1))
    _put(out, "by_ex", by)
    return out


def _part_liq(s):
    f = s.get("f") or {}
    lq = s.get("liq") or {}
    out = {}
    if f.get("cm_liq_5m_long_musd") is not None and f.get("cm_liq_5m_short_musd") is not None:
        out["5m"] = [_rnd(f["cm_liq_5m_long_musd"], 3), _rnd(f["cm_liq_5m_short_musd"], 3)]
    if f.get("cm_liq_1h_net_musd") is not None and L.num(lq.get("l1h_long")) is not None and L.num(lq.get("l1h_short")) is not None:
        out["1h"] = [_musd(lq["l1h_long"], 3), _musd(lq["l1h_short"], 3)]
    _put(out, "liq_cov", _rnd(f.get("cm_liq_cov"), 3))
    if lq:
        out["liq_partial"] = 1 if lq.get("partial") else 0
    by = {}
    for m, r in sorted((s.get("mk") or {}).items()):
        if isinstance(r, dict) and r.get("code") is None and (L.num(r.get("liq5_long")) is not None or L.num(r.get("liq5_short")) is not None):
            by[m] = [_musd(r.get("liq5_long"), 3), _musd(r.get("liq5_short"), 3)]
    _put(out, "by_ex_5m", by)
    return out


def _part_funding(s):
    f = s.get("f") or {}
    out = {}
    _put(out, "w_pct", _rnd(f.get("cm_fund_w_pct"), 5))
    _put(out, "spread_pct", _rnd(f.get("cm_fund_spread_pct"), 5))
    by = {}
    for m, r in sorted((s.get("mk") or {}).items()):
        if isinstance(r, dict) and r.get("code") is None:
            _put(by, m, _rnd(r.get("fund8_pct"), 5))
    _put(out, "by_ex_8h_pct", by)
    _put(out, "prem_ps", _rnd(f.get("cm_prem_ps_bps"), 1))
    _put(out, "prem_cb", _rnd(f.get("cm_prem_cb_bps"), 1))
    return out


def _part_venues(s, doc):
    ex = doc.get("ex") or {}
    out = {}
    for m, r in sorted((s.get("mk") or {}).items()):
        if not isinstance(r, dict):
            continue
        e = ex.get(m) if isinstance(ex.get(m), (list, tuple)) else ()
        age = L.num(e[3]) if len(e) > 3 else None
        v = {"code": r.get("code") or "OK"}
        _put(v, "lag_ms", _rnd(r.get("lag_ms"), 0))
        _put(v, "age_s", _rnd(age / 1000.0, 1) if age is not None else None)
        out[m] = v
    return out


def detail(root, sym, parts=None, now=None, cfg=None):
    """Ответ инструмента: {"sym", "ts", "age_s", "n_ex", "cov", "warn": [...], <части>, "trunc": [отброшенные части]} не длиннее
    tool.max_bytes (UTF-8 компактного JSON); снимка нет или он старше 5 с — warn STALE без чисел; монета вне фокуса — NOT_FOCUS без
    чисел. Усечение частями в порядке venues, liq, oi, funding, cvd, book. Не бросает."""
    out = {"sym": sym if isinstance(sym, str) else None}
    try:
        c = _cfg(root, cfg)
        max_bytes = _int_in(c["tool"].get("max_bytes"), 512, 8192, 2048)
        want = [p for p in PARTS if parts is None or p in (parts or ())]
        doc = _read_snap(root)
        if doc is None:
            out["warn"] = ["STALE"]
            return out
        out["ts"] = int(doc["ts"])
        age = _age(doc, now)
        out["age_s"] = age
        if age > SNAP_MAX_AGE_S:
            out["warn"] = ["STALE"]
            return out
        s = L.snap_sym(doc, sym) if isinstance(sym, str) else None
        if s is None:
            out["warn"] = ["NOT_FOCUS"]
            return out
        warn = []
        if "low_cov" in (s.get("flags") or ()):
            warn.append("LOW_COV")
        _put(out, "n_ex", s.get("n_ex") if isinstance(s.get("n_ex"), int) else None)
        _put(out, "cov", _cov_min(s))
        out["warn"] = warn
        builders = {"book": lambda: _part_book(s, c["cov"]), "cvd": lambda: _part_cvd(s, c["cov"]), "oi": lambda: _part_oi(s),
                    "liq": lambda: _part_liq(s), "funding": lambda: _part_funding(s), "venues": lambda: _part_venues(s, doc)}
        for p in want:
            v = builders[p]()
            if v:
                out[p] = v
        dropped = []

        def size():
            return len(json.dumps(out, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        for p in TRUNC_ORDER:
            if size() <= max_bytes:
                break
            if p in out:
                del out[p]
                dropped.append(p)
                out["trunc"] = list(dropped)
        return out
    except Exception:                                    # noqa: BLE001
        return {"sym": out.get("sym"), "warn": ["STALE"]}
