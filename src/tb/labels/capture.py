"""Захват сигналов: хвост runtime/hot_events.jsonl -> таблица signals (курсор offset+head_sha, неполная строка не читается, INSERT OR IGNORE)."""
import datetime
import hashlib
import json
import os

from tb import paths
from tb.labels import features, schema, store
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

EV_NUM_FIELDS = ("range_5m_pct", "ret_5m_pct", "turn_surge_5m", "chg_24h_pct", "turn_24h_musd", "spread_bps", "funding_pct", "liq_usd", "cvd_pct", "xex_bps")


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
    for raw in chunk.split(b"\n"):
        if not raw.strip():
            continue
        sig = parse_event(raw.decode("utf-8", "replace"))
        if sig is None:
            out["bad_lines"] += 1
        elif sig["type"] not in allowed:
            out["filtered"] += 1
        else:
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
