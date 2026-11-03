"""Снимки кластеров и выборки окна (R2, Requirements 5.9, 5.10, 5.13, 5.14, 5.17; design.md, «Единица наблюдения, кластеры, снимки и выборки окна»).

Кластер — (час t0 // 3600, сторона входа) по всему рынку, строка "<hour>:<side>". Кластер закрывается, когда все сигналы политики проверки в нём
размечены (нет status="pending") и прошло grace_min после последнего labeled_ts, но не раньше конца часа; иначе через cluster_close_max_h после конца
часа с тем, что есть (неразмеченные прогоны не входят). При закрытии его прогоны один раз пишутся в insert-only cluster_runs (INSERT OR IGNORE);
признаки — девять RULE_FEATURES функцией memory.features.rule_features (та же, что у мозга), вид события — колонка type, область применимости
политики (memory.features.in_scope) — фильтр при записи. Все расчёты цикла читают только cluster_runs.

Выборка окна: строки с t0 ∈ [a, b) (a, b на границе часа) и closed_ts ≤ cut_ts = b + cluster_close_max_h + observe_s; расчёт раньше cut_ts
не выполняется (WindowNotReady). Хеш — sha256 канонического JSON отсортированных по (t0, key) строк, 16 hex (как memory/chain.py).
Модуль не импортирует brain/gateway/llm/venues/executor/control.
"""
import hashlib
import json

from tb.learnloop import conf as LC
from tb.logic.learn_cond import RULE_FEATURES
from tb.memory import chain as CH
from tb.memory import features as MF

HOUR = 3600
DAY = 86400
COLS = ("cluster", "hour", "side", "key", "t0", "type", "policy_id", "policy_ver", "ctx_ver", "env_norm", "filled", "r_net", "feats", "flags", "closed_ts")
EXCL_FLAGS = ("gap", "halt")
SIDES = ("long", "short")


class WindowNotReady(Exception):
    """Расчёт по выборке окна раньше cut_ts (Requirement 5.17): шаг ждёт следующего прохода."""
    code = "BEFORE_CUT"

    def __init__(self, cut_ts, now):
        self.cut_ts, self.now = int(cut_ts), int(now)
        Exception.__init__(self, "BEFORE_CUT: cut_ts=%d > now=%d" % (self.cut_ts, self.now))


# ---------------------------------------------------------------- время и ключи

def env_norm(env):
    """Окружение рыночных данных: "testnet" только для signals.env == "testnet", иначе "mainnet" (пусто, None, mainnet и прочее)."""
    return "testnet" if env == "testnet" else "mainnet"


def hour_floor(ts):
    return int(ts) // HOUR * HOUR


def hour_ceil(ts):
    t = int(ts)
    return t if t % HOUR == 0 else (t // HOUR + 1) * HOUR


def cluster_id(hour, side):
    return "%d:%s" % (int(hour), side)


def cut_ts(b, cfg):
    """cut_ts = b + cluster_close_max_h + observe_s."""
    return int(b) + int(cfg["clusters"]["cluster_close_max_h"]) * HOUR + int(cfg["intervals"]["observe_s"])


def last_b(now, cfg):
    """Последняя граница часа b, для которой cut_ts(b) ≤ now."""
    return hour_floor(int(now) - int(cfg["clusters"]["cluster_close_max_h"]) * HOUR - int(cfg["intervals"]["observe_s"]))


def _jl(v):
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str) and v:
        try:
            return json.loads(v)
        except ValueError:
            return None
    return None


def _flag_set(flags):
    return set(x for x in str(flags).split(",") if x) if flags else set()


# ---------------------------------------------------------------- политика проверки

def load_policy(root=None, cfg=None):
    """Запись политики проверки verify.policy (P2) из config/shadow_policies.json (включённая, последняя по ver). Нет -> ValueError."""
    from tb.labels import policies as LP
    cfg = cfg or LC.load_strict(root)
    pid = cfg["verify"]["policy"]
    pols = [p for p in LP.load(root) if p["id"] == pid and p.get("enabled")]
    if not pols:
        raise ValueError("политика проверки %s не найдена или выключена" % pid)
    return max(pols, key=lambda p: p["ver"])


def entry_side(policy, sig_side):
    """Сторона входа политики по стороне сигнала: with_dir — та же, against_dir — противоположная, иначе None."""
    if sig_side not in SIDES:
        return None
    s = policy.get("side")
    if s == "with_dir":
        return sig_side
    if s == "against_dir":
        return "short" if sig_side == "long" else "long"
    return None


def _applicable(policy, sig):
    kinds = policy.get("kinds") or []
    if sig.get("tradable") in (0, False):
        return False
    return "*" in kinds or sig.get("type") in kinds


# ---------------------------------------------------------------- закрытие кластеров

_SIG_COLS = ("key", "t0", "sym", "type", "side", "tradable", "env", "status", "labeled_ts")


def _since(conn, policy):
    row = conn.execute("SELECT MAX(t0) FROM cluster_runs WHERE policy_id=? AND policy_ver=?", (policy["id"], int(policy["ver"]))).fetchone()
    return row[0] if row and row[0] is not None else None


def _closed_set(conn, policy, t_from):
    rows = conn.execute("SELECT DISTINCT cluster FROM cluster_runs WHERE policy_id=? AND policy_ver=? AND t0>=?",
                        (policy["id"], int(policy["ver"]), int(t_from))).fetchall()
    return set(r[0] for r in rows)


def closable(members, hour, now, cfg):
    """Кластер (список сигналов с status, labeled_ts) можно закрыть: конец часа наступил и (всё размечено и прошло grace_min после последней
    разметки, или прошло cluster_close_max_h после конца часа)."""
    end = (int(hour) + 1) * HOUR
    if now < end:
        return False
    if now >= end + int(cfg["clusters"]["cluster_close_max_h"]) * HOUR:
        return True
    if any(m["status"] == "pending" for m in members):
        return False
    last = max([int(m["labeled_ts"]) for m in members if m.get("labeled_ts") is not None] or [0])
    return now >= last + int(cfg["clusters"]["grace_min"]) * 60


def cluster_rows(conn, policy, members, now, mcfg):
    """Строки cluster_runs закрываемого кластера: прогоны политики (id, ver) размеченных сигналов (status="done") в области применимости."""
    done = [m for m in members if m["status"] == "done"]
    if not done:
        return []
    by_key = dict((m["key"], m) for m in done)
    q = ("SELECT p.key, p.side, p.ctx_ver, p.filled, p.r_net, p.flags, s.ev FROM policy_runs p JOIN signals s ON s.key = p.key "
         "WHERE p.policy_id=? AND p.policy_ver=? AND p.key IN (%s)" % ",".join("?" * len(by_key)))
    scope = MF.policy_scope(policy)
    out = []
    for key, pside, ctx, filled, r_net, flags, ev in conn.execute(q, [policy["id"], int(policy["ver"])] + sorted(by_key)).fetchall():
        sig = by_key[key]
        side = pside if pside in SIDES else entry_side(policy, sig["side"])
        if side is None:
            continue
        feats = MF.rule_features({"t0": sig["t0"], "sym": sig["sym"], "type": sig["type"], "side": sig["side"], "ev": ev}, {"side": side}, mcfg)
        if not MF.in_scope(feats, scope):
            continue
        hour = int(sig["t0"]) // HOUR
        out.append({"cluster": cluster_id(hour, side), "hour": hour, "side": side, "key": key, "t0": int(sig["t0"]), "type": sig["type"],
                    "policy_id": policy["id"], "policy_ver": int(policy["ver"]), "ctx_ver": ctx, "env_norm": env_norm(sig["env"]),
                    "filled": 1 if filled else 0, "r_net": float(r_net) if filled and r_net is not None else None,
                    "feats": dict((k, feats.get(k)) for k in RULE_FEATURES), "flags": flags, "closed_ts": int(now)})
    return out


def close_clusters(conn, now, cfg, policy=None, mcfg=None, root=None, since=None):
    """Закрыть созревшие кластеры и один раз записать их прогоны в cluster_runs (INSERT OR IGNORE, одна транзакция).

    Просматриваются часы от since (по умолчанию: MAX(t0) уже записанных − cluster_close_max_h − 1 час; первая запись — с первого сигнала) до
    последнего полного часа. policy — запись политики проверки (по умолчанию load_policy), mcfg — config/memory.json (для coin_class).
    -> число закрытых в этом проходе кластеров."""
    from tb.knowledge import store2
    if policy is None:
        policy = load_policy(root, cfg)
    if mcfg is None:
        from tb.memory import cfg as memcfg
        mcfg = memcfg.load(root)
    now = int(now)
    cmax = int(cfg["clusters"]["cluster_close_max_h"]) * HOUR
    if since is None:
        last = _since(conn, policy)
        if last is None:
            row = conn.execute("SELECT MIN(t0) FROM signals").fetchone()
            if not row or row[0] is None:
                return 0
            since = row[0]
        else:
            since = last - cmax - HOUR
    t_from, t_to = hour_floor(since), hour_floor(now)
    if t_from >= t_to:
        return 0
    rows = conn.execute("SELECT %s FROM signals WHERE t0>=? AND t0<? ORDER BY t0, key" % ", ".join(_SIG_COLS), (t_from, t_to)).fetchall()
    groups = {}
    for r in rows:
        s = dict(zip(_SIG_COLS, r))
        if not _applicable(policy, s):
            continue
        side = entry_side(policy, s["side"])
        if side is None:
            continue
        groups.setdefault((int(s["t0"]) // HOUR, side), []).append(s)
    closed = _closed_set(conn, policy, t_from)
    out, n = [], 0
    for (hour, side) in sorted(groups):
        if cluster_id(hour, side) in closed or not closable(groups[(hour, side)], hour, now, cfg):
            continue
        rs = cluster_rows(conn, policy, groups[(hour, side)], now, mcfg)
        if rs:
            out.extend(rs)
            n += 1
    if out:
        store2.insert_rows(conn, "cluster_runs", out, ignore_dup=True)
    return n


# ---------------------------------------------------------------- выборка окна

def _row_out(r):
    d = dict(zip(COLS, r))
    d["feats"] = _jl(d["feats"]) or {}
    return d


def sample_hash(raw_rows):
    """sha256 канонического JSON строк выборки (сырые значения колонок COLS, порядок (t0, key)), 16 hex."""
    rows = sorted((list(r) for r in raw_rows), key=lambda r: (r[4], r[3]))
    return hashlib.sha256(CH.canon(rows).encode("utf-8")).hexdigest()[:16]


def metric_rows(rows):
    """Строки метрик: заполненные (filled=1) с r_net, без gap/halt во flags; порядок (t0, key)."""
    out = [r for r in rows if r["filled"] and r["r_net"] is not None and not (_flag_set(r.get("flags")) & set(EXCL_FLAGS))]
    out.sort(key=lambda r: (r["t0"], r["key"]))
    return out


def window(conn, policy_id, policy_ver, ctx_ver, env_norm, a, b, cfg, now):
    """Выборка окна t0 ∈ [a, b), closed_ts ≤ cut_ts (политика, версия, ctx_ver, env_norm). now < cut_ts -> WindowNotReady.
    -> {"rows", "a", "b", "cut_ts", "hash", "n_rows", "n_filled", "n_excl", "excl_share", "policy_id", "policy_ver", "ctx_ver", "env_norm"}."""
    a, b = int(a), int(b)
    if a % HOUR or b % HOUR or a >= b:
        raise ValueError("окно: a < b на границе часа")
    ct = cut_ts(b, cfg)
    if int(now) < ct:
        raise WindowNotReady(ct, now)
    raw = conn.execute("SELECT %s FROM cluster_runs WHERE policy_id=? AND ctx_ver=? AND env_norm=? AND t0>=? AND t0<? AND policy_ver=? AND closed_ts<=? "
                       "ORDER BY t0, key" % ", ".join(COLS), (policy_id, ctx_ver, env_norm, a, b, int(policy_ver), ct)).fetchall()
    rows = [_row_out(r) for r in raw]
    filled = [r for r in rows if r["filled"] and r["r_net"] is not None]
    excl = [r for r in filled if _flag_set(r.get("flags")) & set(EXCL_FLAGS)]
    return {"rows": rows, "a": a, "b": b, "cut_ts": ct, "hash": sample_hash(raw), "n_rows": len(rows), "n_filled": len(filled), "n_excl": len(excl),
            "excl_share": round(len(excl) / float(len(filled)), 4) if filled else 0.0,
            "policy_id": policy_id, "policy_ver": int(policy_ver), "ctx_ver": ctx_ver, "env_norm": env_norm}


def sample(win):
    """Компактная запись выборки для evidence: политика, версия, ctx_ver, env_norm, a, b, cut_ts, хеш."""
    return {"policy": win["policy_id"], "policy_ver": win["policy_ver"], "ctx_ver": win["ctx_ver"], "env_norm": win["env_norm"],
            "a": win["a"], "b": win["b"], "cut_ts": win["cut_ts"], "h": win["hash"]}


def window_of(conn, smp, cfg, now):
    """Пересчёт выборки по записанной sample(...)."""
    return window(conn, smp["policy"], smp["policy_ver"], smp["ctx_ver"], smp["env_norm"], smp["a"], smp["b"], cfg, now)


def _meta(conn, k):
    try:
        row = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    except Exception:
        return None
    try:
        return float(row[0]) if row and row[0] is not None else None
    except (TypeError, ValueError):
        return None


def restored(conn, cut):
    """Окно с записанным хешем потеряно восстановлением из копии: meta.copy_ts < cut_ts ≤ meta.restore_ts."""
    c, r = _meta(conn, "copy_ts"), _meta(conn, "restore_ts")
    return c is not None and r is not None and c < int(cut) <= r


def check_sample(conn, smp, cfg, now):
    """Целостность записанной выборки: "RESTORED" (copy_ts < cut_ts ≤ restore_ts), "SAMPLE_CHANGED" (хеш другой), иначе "OK"."""
    if restored(conn, smp["cut_ts"]):
        return "RESTORED"
    return "OK" if window_of(conn, smp, cfg, now)["hash"] == smp["h"] else "SAMPLE_CHANGED"


def current_ctx(conn, policy_id, policy_ver, env, b):
    """Текущая версия контекста на границе b: ctx_ver последней строки с t0 < b и начало её непрерывного участка (первый t0 после последней
    строки другой версии). -> (ctx_ver, start_t0) или (None, None)."""
    row = conn.execute("SELECT ctx_ver FROM cluster_runs WHERE policy_id=? AND policy_ver=? AND env_norm=? AND t0<? ORDER BY t0 DESC, key DESC LIMIT 1",
                       (policy_id, int(policy_ver), env, int(b))).fetchone()
    if not row:
        return None, None
    ctx = row[0]
    other = conn.execute("SELECT MAX(t0) FROM cluster_runs WHERE policy_id=? AND policy_ver=? AND env_norm=? AND t0<? AND ctx_ver<>?",
                         (policy_id, int(policy_ver), env, int(b), ctx)).fetchone()[0]
    start = conn.execute("SELECT MIN(t0) FROM cluster_runs WHERE policy_id=? AND policy_ver=? AND env_norm=? AND t0<? AND ctx_ver=? AND t0>?",
                         (policy_id, int(policy_ver), env, int(b), ctx, -1 if other is None else int(other))).fetchone()[0]
    return ctx, start


def ctx_changed_since(conn, policy_id, policy_ver, env, ctx_ver, t_from):
    """Есть строки другой ctx_ver с t0 ≥ t_from (смена версии контекста во время holdout или испытания)."""
    row = conn.execute("SELECT 1 FROM cluster_runs WHERE policy_id=? AND policy_ver=? AND env_norm=? AND t0>=? AND ctx_ver<>? LIMIT 1",
                       (policy_id, int(policy_ver), env, int(t_from), ctx_ver)).fetchone()
    return row is not None
