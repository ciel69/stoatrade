"""Захват сигналов: хвост runtime/hot_events.jsonl -> таблица signals (курсор offset+head_sha, неполная строка не читается, INSERT OR IGNORE)."""
import datetime
import hashlib
import json
import os

from tb import paths
from tb.core import cmktdoc
from tb.labels import features, schema, store
from tb.logic import cmkt as L_cmkt
from tb.logic.geometry import MAX_HOLD_MIN

SRC = "hot_events"
HEAD_BYTES = 4096
MAX_READ = 16 * 1024 * 1024
MIN_MATURE_MIN = 240

DEFAULT_CFG = {
    "grace_min": 10,
    "capture_types": ["range_5m", "turnover_surge", "hot_enter", "oi_spike", "oi_flush", "funding_extreme", "liq_cascade", "cvd_divergence", "book_wall", "xex_gap"],
}
# запасные значения design.md, пока нет реестра политик: (entry_ttl_min, max_horizon) включённых P2, P4, P5, P6
DEFAULT_POLICY_SPECS = [(30, "intraday"), (30, "intraday"), (2, "scalp"), (2, "scalp")]

EV_NUM_FIELDS = ("range_5m_pct", "ret_5m_pct", "turn_surge_5m", "chg_24h_pct", "turn_24h_musd", "spread_bps", "funding_pct", "liq_usd", "cvd_pct", "xex_bps",
                 "wall_usd", "wall_dist_pct")
EV_ENUM_FIELDS = {"wall_side": ("bid", "ask")}          # R1.7 (Requirement 14.2): поля событий book_wall (core/bus.py), перечисление строкой

# R1.7 (Requirement 14): признаки сводного рынка в ev.cm | ev.cm_missing (tb.core.cmktdoc.features_at)
CM_MISS_FOCUS = ("no_row", "late")                       # коды, при которых монета считается в фокусе для покрытия (длительность неизвестна);
                                                         # db (ряды не пишутся) не в знаменателе: это видно жёлтыми кодами сервиса cmkt, а не CM_COV
                                                         # (иначе первые 24 ч после включения cmkt CM_COV был бы жёлтым от старых сигналов)
WARM_S_DEFAULT = 300


def cm_sym(sym):
    """Символ ряда сводного рынка: монета без суффикса USDT (как tb.cmkt.focus)."""
    s = sym.strip().upper() if isinstance(sym, str) else ""
    return s[:-4] if s.endswith("USDT") and len(s) > 4 else s


def cm_features(root, sig, now_s, wait=False):
    """Строка признаков сигнала через cmktdoc.features_at -> ответ читателя. Не pending: ev.cm или ev.cm_missing записаны в sig["ev"]."""
    r = cmktdoc.features_at(root, cm_sym(sig["sym"]), sig["t0"], now=now_s, wait=wait)
    if r.get("pending"):
        return r
    ev = sig["ev"]
    if isinstance(r.get("cm"), dict):
        ev["cm"] = r["cm"]
        ev.pop("cm_missing", None)
    else:
        ev["cm_missing"] = r.get("cm_missing") or "db"
    return r


def warm_s(root=None):
    """focus.warm_s из config/cmkt.json (умолчание 300 с); конфиг читается здесь, без импорта tb.cmkt."""
    try:
        with open(paths.config("cmkt.json", root=root), encoding="utf-8") as f:
            v = json.load(f)["focus"]["warm_s"]
        return float(v) if _num(v) and v >= 0 else WARM_S_DEFAULT
    except (OSError, ValueError, KeyError, TypeError):
        return WARM_S_DEFAULT


def cm_coverage(conn, since_t=None, warm=WARM_S_DEFAULT, wave=None):
    """Покрытие признаками сводного рынка среди сигналов с t0 >= since_t (Requirement 14.4):
    {"focus": доля с ev.cm среди монет в фокусе >= warm на t0 | None, "all": доля среди всех | None, "n_focus", "n_all", "n_cm",
     "missing": {код: n}, "legacy": n сигналов без ev.cm и ev.cm_missing (до R1.7), "first_wave": {признак: доля не-None среди ev.cm | None}, "warm_s"}.
    В фокусе: ev.cm с focus_s >= warm, либо ev.cm_missing из CM_MISS_FOCUS (строки нет, хотя монета в фокусе или ряды недоступны)."""
    wave = tuple(wave if wave is not None else L_cmkt.FIRST_WAVE)
    cond, args = ("WHERE t0>=?", [int(since_t)]) if since_t is not None else ("", [])
    n_all = n_cm = n_focus = n_focus_cm = legacy = 0
    missing, fw = {}, dict((n, 0) for n in wave)
    for (raw,) in conn.execute("SELECT ev FROM signals %s" % cond, args):
        n_all += 1
        try:
            ev = json.loads(raw) if raw else {}
        except ValueError:
            ev = {}
        cm = ev.get("cm") if isinstance(ev, dict) else None
        if isinstance(cm, dict):
            n_cm += 1
            for n in wave:
                if cm.get(n) is not None:
                    fw[n] += 1
            if _num(cm.get("focus_s")) and cm["focus_s"] >= warm:
                n_focus += 1
                n_focus_cm += 1
            continue
        code = ev.get("cm_missing") if isinstance(ev, dict) else None
        if not isinstance(code, str):
            legacy += 1
            continue
        missing[code] = missing.get(code, 0) + 1
        if code in CM_MISS_FOCUS:
            n_focus += 1

    def share(a, b):
        return round(a / float(b), 4) if b else None
    return {"focus": share(n_focus_cm, n_focus), "all": share(n_cm, n_all), "n_focus": n_focus, "n_all": n_all, "n_cm": n_cm,
            "missing": missing, "legacy": legacy, "first_wave": dict((n, share(fw[n], n_cm)) for n in wave), "warm_s": warm}


def load_cfg(root=None):
    """config/labels.json поверх умолчаний; нет файла или битый -> умолчания."""
    cfg = dict(DEFAULT_CFG)
    try:
        with open(paths.config("labels.json", root=root), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            cfg.update(d)
    except (OSError, ValueError):
        pass
    return cfg


def policy_specs(root=None):
    """[(entry_ttl_min, max_horizon)] включённых политик из config/shadow_policies.json; нет файла/невалидно -> значения по умолчанию."""
    try:
        with open(paths.config("shadow_policies.json", root=root), encoding="utf-8") as f:
            d = json.load(f)
        pols = d["policies"]
        if not isinstance(pols, list):
            return list(DEFAULT_POLICY_SPECS)
        return [(float(p.get("entry_ttl_min", 0)), p["max_horizon"]) for p in pols
                if p.get("enabled", True) and p.get("max_horizon") in MAX_HOLD_MIN]
    except (KeyError, IndexError):
        return list(DEFAULT_POLICY_SPECS)
    except (OSError, ValueError, AttributeError, TypeError):
        return list(DEFAULT_POLICY_SPECS)


def mature_ts(t0_s, cfg, specs):
    """t0 + max(240 мин, max(entry_ttl_min + MAX_HOLD_MIN[max_horizon])) + grace_min, секунды."""
    need = max([MIN_MATURE_MIN] + [ttl + MAX_HOLD_MIN[h] for ttl, h in specs])
    return int(t0_s + (need + float(cfg.get("grace_min", 10))) * 60)


def sig_key(t0_s, sym, typ):
    t = datetime.datetime.fromtimestamp(int(t0_s), datetime.timezone.utc)
    return "S-%s-%s-%s" % (t.strftime("%Y%m%d%H%M%S"), sym, typ)


def _t0(ts):
    if not isinstance(ts, str) or not ts:
        return None
    s = ts.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float("inf"), float("-inf"))


def parse_event(line):
    """Строка JSONL -> словарь сигнала или None (битая, нет sym/ts/type, ts не разбирается)."""
    try:
        ev = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(ev, dict):
        return None
    sym, typ = ev.get("sym"), ev.get("type")
    if not isinstance(sym, str) or not sym or not isinstance(typ, str) or not typ:
        return None
    t0 = _t0(ev.get("ts"))
    if t0 is None:
        return None
    side = {"up": "long", "down": "short"}.get(ev.get("direction"))
    num = {k: ev[k] for k in EV_NUM_FIELDS if _num(ev.get(k))}
    for k, allowed in EV_ENUM_FIELDS.items():
        if ev.get(k) in allowed:
            num[k] = ev[k]
    for k, v in ev.items():
        if k.startswith("oi_") and _num(v):
            num[k] = v
    if "spread_bps" not in num:
        num["no_spread"] = True
    tr = ev.get("tradable")
    return {"key": sig_key(t0, sym, typ), "t0": t0, "sym": sym, "type": typ, "side": side,
            "score": ev.get("score") if _num(ev.get("score")) else None,
            "price": ev.get("price") if _num(ev.get("price")) else None,
            "wake": 1 if ev.get("wake") else 0,
            "tradable": 1 if tr is None else (1 if tr else 0),
            "corr_group": ev.get("corr_group") if isinstance(ev.get("corr_group"), str) else None,
            "env": ev.get("env") if isinstance(ev.get("env"), str) else None,
            "ev_id": ev.get("id") if isinstance(ev.get("id"), str) else None,
            "ev": num}


def _head_sha(path, n):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read(min(HEAD_BYTES, n))).hexdigest()


def poll(conn, root, cfg, now_s, specs=None):
    """Один цикл захвата. -> {"new", "bad_lines", "offset", "filtered"}. Курсор и вставки в одной транзакции."""
    path = paths.runtime("hot_events.jsonl", root=root)
    out = {"new": 0, "bad_lines": 0, "offset": 0, "filtered": 0}
    if not os.path.exists(path):
        return out
    cur = conn.execute("SELECT offset, head_sha FROM cursor WHERE src=?", (SRC,)).fetchone()
    offset = cur[0] if cur else 0
    try:
        size = os.path.getsize(path)
        if offset and (size < offset or _head_sha(path, offset) != cur[1]):
            offset = 0
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read(MAX_READ)
    except OSError:
        out["offset"] = offset
        return out
    last = data.rfind(b"\n")
    if last < 0:
        if len(data) >= MAX_READ:                  # одна строка длиннее лимита: пропускаем кусок
            last = len(data) - 1
            out["bad_lines"] += 1
            data = data[:last + 1]
        else:                                      # неполная строка: ждём следующего цикла
            out["offset"] = offset
            if cur and offset != cur[0]:
                _save_cursor(conn, path, offset, now_s)
            return out
    chunk = data[:last + 1]
    new_off = offset + len(chunk)
    allowed = set(cfg.get("capture_types") or DEFAULT_CFG["capture_types"])
    if specs is None:
        specs = policy_specs(root)
    rows = []
    pos = offset
    for raw in chunk.split(b"\n")[:-1]:            # chunk кончается переводом строки: последний кусок пустой
        line_off, pos = pos, pos + len(raw) + 1
        if not raw.strip():
            continue
        sig = parse_event(raw.decode("utf-8", "replace"))
        if sig is None:
            out["bad_lines"] += 1
        elif sig["type"] not in allowed:
            out["filtered"] += 1
        else:
            r = cm_features(root, sig, now_s)
            if r.get("pending"):                   # Requirement 14.6: строка признаков ещё может появиться — этот сигнал и хвост ждут прохода
                new_off = line_off
                out["cm_pending"] = r.get("until")
                break
            rows.append(sig)
    snap = None                                    # контекст на момент захвата (fng/dvol/risk_off): разметка идёт позже и своего снимка взять не может
    snap_read = False
    for s in rows:
        if now_s - s["t0"] <= features.CTX_MAX_LAG_S:
            if not snap_read:
                snap, snap_read = features.read_ctx_snapshot(root, now_s, now_s, s["t0"]), True
            if snap:
                s["ev"]["ctx_snap"] = snap
    with store.tx(conn):
        out["new"] += insert_signals(conn, rows, cfg, specs, now_s)
        _save_cursor(conn, path, new_off, now_s)
    out["offset"] = new_off
    return out


def insert_signals(conn, rows, cfg, specs, now_s):
    """INSERT OR IGNORE разобранных сигналов (parse_event) со status=pending; вызывать внутри транзакции. -> число новых (дубли по ключу не считаются).
    Общий путь для сервиса (poll) и `./tb labels backfill`."""
    n = 0
    for s in rows:
        m = mature_ts(s["t0"], cfg, specs)
        c = conn.execute(
            "INSERT OR IGNORE INTO signals(key, t0, sym, type, side, score, price, wake, tradable, corr_group, env, ev_id, ev, status, mature_ts, next_try_ts, captured_ts, schema_ver)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (s["key"], s["t0"], s["sym"], s["type"], s["side"], s["score"], s["price"], s["wake"], s["tradable"], s["corr_group"], s["env"], s["ev_id"],
             json.dumps(s["ev"], ensure_ascii=False, separators=(",", ":")), schema.PENDING, m, m, int(now_s), schema.SCHEMA_VER))
        n += c.rowcount
    return n


def _save_cursor(conn, path, offset, now_s):
    sha = _head_sha(path, offset) if offset else None
    conn.execute("INSERT OR REPLACE INTO cursor(src, path, offset, head_sha, ts) VALUES(?,?,?,?,?)", (SRC, path, offset, sha, int(now_s)))
