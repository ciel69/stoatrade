"""Сторож выходов, аренда и расхождение симуляции с фактом `tb.learnloop.exits_watch` (R4, задача 15;
Requirement 8 целиком, 16.7; design.md «Сторож и расхождение с фактом», «Снимок и аренда», «Ревизии» п. 1, 3, 4; свойство 21).

Что делает проверка (`check`/`run`)
    * Точки против предыдущей версии (Requirement 8.1, 8.2). Действующая версия (`exit_versions`, последняя по `seq`) сравнивается
      с предыдущей (её `parent`, иначе предыдущая запись) на путях прогонов: `d_w = r_net(действующая) − r_net(предыдущая)` по строкам
      `exit_evals` (источник `run`, порядок `S`, обычные издержки, текущие `cost_h`/`sim_ver`) с `t0` не раньше продвижения действующей
      версии. Значение блока (`unit.block_h` часов, сторона) — среднее `d_w` его путей; порядок блоков хронологический. Точки — первые
      `watch.looks` блоков, каждая один раз на участок (участок = действующая версия); затем раз в 7 суток по скользящим последним
      `watch.weekly_window` блокам с порогом `watch.alpha_weekly`, пропущенные недели не досчитываются (как `learnloop.watch` R2).
      Точки `watch.looks` пересчитаны под `block_h` = 24 по тем же срокам в сутках, что при `block_h` = 1 (`50/100/200` блоков по часу =
      ≈1/2/4 суток; блок 24 ч — (сутки UTC, сторона), 2 блока в сутки, значит те же сроки — `2/4/8` блоков; `weekly_window` 200 часовых
      блоков ≈ 4 суток — `8` блоков). Числа живут только в `config/exits.json`; умолчаний в коде нет.
    * Откат по путям (Requirement 8.3). В точке среднее `d_w` < −`watch.rollback_delta_r` AND одностороннее p t-теста против нуля в
      сторону «хуже» < порога точки -> `exits_versions.rollback`: версия с политикой предыдущей (`ROLLBACK`), снимок и аренда
      (`by="rollback"`), чёрный список варианта на `cooldown_days`, событие `exit_policy_rollback`.
    * Откат по сделкам мозга (Requirement 8.4). На сделках мозга после продвижения (режим `brain.mode`) считается
      `d_b = r(replay_brain(действующая, хронология)) − r(replay_brain(предыдущая, хронология))`; при `watch.brain_min_n` сделках
      среднее `d_b` < −`watch.brain_delta_r` и одностороннее p < `watch.brain_alpha` — тот же откат. Отдельных точек у этой проверки нет.
    * `EXIT_SIM_GAP` (Requirement 8.7). `gap = r_факт − r(replay_brain(версия позиции + хронология мозга, S))` на сделках мозга за
      `watch.sim_gap_days` суток; при `watch.sim_gap_min_n` сделках и |среднем| > `watch.sim_gap_r` в `meta.exits_watch` ставится
      жёлтый статус `EXIT_SIM_GAP`, а `promotion_paused` останавливает продвижение до снятия статуса (откат при этом работает;
      design.md, «Error Handling»).
    * Аренда (Requirement 8.5). После успешной проверки атомарно пишется `runtime/exit_lease.json {ts, ver, policy_h, by="watch"}`.
      При ошибке шага (исключение наружу) аренда не пишется; сбой самого шага не останавливает шаги R2 (изоляция в `learnloop.cycle`).
      После отката аренда уже записана откатом в той же операции — повторно не перезаписывается.

Окно данных
    Граница `b` — последний час, для которого закрыты кластеры (`clusters.last_b`, конфиг `learnloop.json`), строки берутся с
    `t0 < b` и `labeled_ts ≤ cut_ts(b)` — расчёт не видит будущего (свойство 12). При непустой очереди `eval_backlog` проверка не
    выполняется (код `BACKLOG`, состояние и аренда не обновляются). Выключенный или битый `config/exits.json` — код `DISABLED`,
    ничего не пишется (Requirement 16.1).

Состояние
    `meta.exits_watch` = `{"ts": ..., "state": {...}, "ver", "prev", "codes", "sim_gap"}`: `ts` — время последней успешной проверки
    (его читает `exits_versions.promotion_ready`), `state` — участок (`seg`, `prev`, `a`), пройденные точки (`done`), последняя
    недельная точка (`week`) и числа точек; повтор на тех же данных ничего не пишет (идемпотентность, Requirement 8.8).

Модуль не импортирует мозг, шлюз, MCP, биржу, исполнитель, управление и CLI; в `config/` не пишет (слой `tb.learnloop`).
"""
import json
import math

from tb.core import exitdoc as XD
from tb.knowledge import markers as KM
from tb.knowledge import store2
from tb.labels import paths as LP
from tb.labels import schema as LS
from tb.labels import store as LST
from tb.learnloop import clusters as CL
from tb.learnloop import conf as LC
from tb.learnloop import exits_conf as EC
from tb.learnloop import exits_eval as EE
from tb.learnloop import exits_versions as XV
from tb.learnloop import stat as ST
from tb.logic import exitpol as XP
from tb.logic import exitsim as XS

STEP = "exits_watch"
META = "exits_watch"
HOUR = CL.HOUR
DAY = CL.DAY
WEEK = 7 * DAY
SIDE = "lower"                      # откат — среднее d_w значимо НИЖЕ нуля
ROLLBACK = "ROLLBACK"
SIM_GAP = "EXIT_SIM_GAP"
OK, BACKLOG, DISABLED, NO_VERSION = "OK", "BACKLOG", "DISABLED", "NO_VERSION"
POINTS_KEEP = 20                    # сколько чисел точек хранится в meta.exits_watch справочно (не рост без предела)

EVAL_COLS = ("src", "key", "t0", "block", "labeled_ts", "var_id", "var_ver", "ord", "px_mode", "r_net")
TRADE_COLS = ("journal_id", "plan_id", "sym", "side", "mode", "entry_ts", "entry_px", "stop0", "tick", "qty", "qty_step", "min_qty",
              "horizon", "max_hold_min", "atr5_pct", "sig_type", "exit_ver", "close_ts", "end_ts", "r", "timeline", "rows", "flags")


# ---------------------------------------------------------------- мелочи

def _num(v):
    """Число (не bool, конечное) или None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _int(v, default=None):
    if isinstance(v, bool) or v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _j(v):
    """JSON-строка или уже разобранное значение; None при мусоре."""
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str) and v[:1] in "{[":
        try:
            return json.loads(v)
        except ValueError:
            return None
    return None


def _r(x, nd=6):
    return None if x is None else round(float(x), nd)


# ---------------------------------------------------------------- чистые функции

def decide(xs, thr, delta):
    """Решение в точке: значения блоков xs, порог p, `rollback_delta_r`. -> {"n", "mean", "sd", "p", "thr", "rollback"}.
    Откат — среднее < −delta и одностороннее p (t против нуля в сторону «хуже») < thr."""
    xs = [float(x) for x in xs]
    m = ST.mean(xs)
    p = ST.t1_pvalue(xs, 0.0, SIDE) if len(xs) >= 2 else None
    rb = m is not None and p is not None and m < -float(delta) and p < float(thr)
    return {"n": len(xs), "mean": _r(m), "sd": _r(ST.sd(xs)), "p": _r(p, 10), "thr": _r(thr, 10), "rollback": bool(rb)}


def block_head(block):
    """Голова блока `"<номер>:<сторона>"` -> номер (для порядка и недельных границ); мусор -> None."""
    head, _, _side = str(block or "").partition(":")
    try:
        return int(head)
    except ValueError:
        return None


def block_values(pairs):
    """Пары путей ({"block", "d"}) -> [(голова, блок, среднее d_w)] по (голова, блок). Мусор пропускается."""
    by = {}
    for p in pairs or []:
        d = _num(p.get("d"))
        head = block_head(p.get("block"))
        if d is None or head is None:
            continue
        by.setdefault(str(p["block"]), []).append(d)
    out = [(block_head(b), b, ST.mean(v)) for b, v in by.items()]
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def points_due(blocks, b, st, cfg):
    """Точки, наступившие на границе b, ещё не пройденные на участке. -> [(метка, значения, порог)]: ("L<N>", первые N блоков,
    alpha/len(looks)) ..., затем ("W<k>", последние weekly_window блоков до b200 + k недель, alpha_weekly) для последней наступившей
    недели k > st.week. `blocks` — [(голова, блок, значение)] в хронологическом порядке."""
    w = cfg["watch"]
    looks = [int(x) for x in w["looks"]]
    thr = float(w["alpha"]) / len(looks)
    done = set(int(x) for x in st.get("done") or [])
    out = []
    for n in looks:
        if n not in done and len(blocks) >= n:
            out.append(("L%d" % n, [x[2] for x in blocks[:n]], thr))
    last = looks[-1]
    if len(blocks) < last or last not in done | set(int(lab[1:]) for lab, _x, _t in out):
        return out
    b_last = (blocks[last - 1][0] + 1) * DAY
    k = (int(b) - b_last) // WEEK
    if k >= 1 and k > int(st.get("week") or 0):
        end = b_last + k * WEEK
        xs = [x[2] for x in blocks if x[0] * DAY < end][-int(w["weekly_window"]):]
        out.append(("W%d" % k, xs, float(w["alpha_weekly"])))
    return out


# ---------------------------------------------------------------- состояние

def state_get(conn):
    """Разобранное `meta.exits_watch` (dict) или {}."""
    try:
        row = conn.execute("SELECT v FROM meta WHERE k=?", (META,)).fetchone()
        d = json.loads(row[0]) if row and row[0] else {}
    except Exception:                                     # noqa: BLE001 — нет таблицы/мусор: состояния нет
        d = {}
    return d if isinstance(d, dict) else {}


def _state_put(conn, doc):
    with LST.tx(conn):
        conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (META, json.dumps(doc, sort_keys=True, separators=(",", ":"))))


def promotion_paused(conn):
    """Продвижение на паузе: в состоянии стоит `EXIT_SIM_GAP` (Requirement 8.7)."""
    g = state_get(conn).get("sim_gap")
    return bool(isinstance(g, dict) and g.get("active"))


# ---------------------------------------------------------------- версии и данные

def _versions(conn):
    """(действующая, предыдущая) записи `exit_versions` или (None, None). Предыдущая — `parent`, иначе предыдущая запись."""
    rows = XV.versions(conn)
    if not rows:
        return None, None
    cur = rows[-1]
    prev = XV.version_row(conn, cur["parent"]) if cur.get("parent") else None
    if prev is None and len(rows) > 1:
        prev = rows[-2]
    if prev is not None and prev["ver"] == cur["ver"]:
        prev = None
    return cur, prev


def _boundary(now, root):
    """Граница данных (последний час с закрытыми кластерами) и `cut_ts` этой границы. Конфиг `learnloop.json` не читается —
    граница по текущему часу, `cut_ts` = now."""
    try:
        lc, errs = LC.load(root)
    except (OSError, ValueError, TypeError):
        lc, errs = None, True
    if errs or not isinstance(lc, dict):
        return CL.hour_floor(int(now)), int(now)
    try:
        b = CL.last_b(int(now), lc)
        return b, CL.cut_ts(b, lc)
    except (KeyError, TypeError, ValueError):
        return CL.hour_floor(int(now)), int(now)


def _eval_pairs(conn, cur, prev, src, cost_h, sim_ver, a, b, cut):
    """Пары «действующая — предыдущая» по (src, key): [{"key", "t0", "block", "d"}]. Только строки `S`/`fee` текущих
    `cost_h`/`sim_ver`, `t0 ∈ [a, b)`, `labeled_ts ≤ cut`."""
    sql = ("SELECT %s FROM exit_evals WHERE src=? AND ord=? AND px_mode=? AND cost_h=? AND sim_ver=? AND var_id IN (?, ?) "
           "AND t0>=? AND t0<? AND labeled_ts<=?" % ", ".join(EVAL_COLS))
    try:
        raw = conn.execute(sql, (src, EE.ORDER_S, EE.PX_FEE, cost_h, sim_ver, str(cur["ver"]), str(prev["ver"]),
                                 int(a), int(b), int(cut))).fetchall()
    except Exception:                                                       # noqa: BLE001 — таблиц x1 может не быть
        return []
    by = {}
    for r in raw:
        d = dict(zip(EVAL_COLS, r))
        by.setdefault((str(d["var_id"]), str(d["key"])), d)
    out, keys = [], sorted(set(k for _v, k in by))
    for key in keys:
        va, vb = by.get((str(cur["ver"]), key)), by.get((str(prev["ver"]), key))
        if not va or not vb:
            continue
        ra, rb = _num(va.get("r_net")), _num(vb.get("r_net"))
        if ra is None or rb is None:
            continue
        out.append({"key": key, "t0": int(va.get("t0") or vb.get("t0") or 0), "block": str(va.get("block") or vb.get("block") or ""),
                    "d": ra - rb})
    return out


def _trade_modes(conn, keys):
    """Режим торговли сделки по `trade_paths` -> {journal_id: mode}; нет таблицы/строки — режим неизвестен."""
    out = {}
    items = sorted(str(k) for k in keys or [])
    if not items:
        return out
    try:
        for i in range(0, len(items), 500):
            chunk = items[i:i + 500]
            for jid, mode in conn.execute("SELECT journal_id, mode FROM trade_paths WHERE journal_id IN (%s)"
                                          % ",".join("?" * len(chunk)), chunk).fetchall():
                out[str(jid)] = str(mode)
    except Exception:                                                       # noqa: BLE001
        return {}
    return out


def brain_delta(conn, cur, prev, inp, cfg):
    """Откат по сделкам мозга (Requirement 8.4) -> {"n", "mean", "sd", "p", "threshold", "alpha", "rollback"}.
    `d_b` берётся из строк `exit_evals` источника `trade` (`S`/`fee`, `replay_brain` с хронологией): разность действующей и
    предыдущей версий на одной сделке; режим — `brain.mode` (неизвестный режим не отсекается, как в испытании)."""
    brain = cfg.get("brain") if isinstance(cfg.get("brain"), dict) else {}
    mode = brain.get("mode")
    min_n = int(cfg["watch"]["brain_min_n"])
    delta = float(cfg["watch"]["brain_delta_r"])
    alpha = float(cfg["watch"]["brain_alpha"])
    a = int(cur.get("ts") or 0)
    pairs = _eval_pairs(conn, cur, prev, EE.SRC_TRADE, inp["cost_h"], XS.SIM_VER, a, 10 ** 12, 10 ** 12)
    if mode:
        modes = _trade_modes(conn, [p["key"] for p in pairs])
        pairs = [p for p in pairs if modes.get(p["key"]) in (None, mode)]
    vals = [p["d"] for p in pairs if p["t0"] >= a]
    n = len(vals)
    m = ST.mean(vals)
    p = ST.t1_pvalue(vals, 0.0, SIDE) if n >= 2 else None
    rb = bool(n >= min_n and m is not None and p is not None and m < -delta and p < alpha)
    return {"n": n, "mean": _r(m), "sd": _r(ST.sd(vals)), "p": _r(p, 10), "threshold": delta, "alpha": alpha, "min_n": min_n,
            "rollback": rb}


def _policy_of(conn, ver, floor, cache):
    """Политика версии позиции, нормализованная с полом; неизвестная версия — пол."""
    key = str(ver or "")
    if key in cache:
        return cache[key]
    row = XV.version_row(conn, ver) if ver else None
    pol = (row or {}).get("policy") if isinstance(row, dict) else None
    pol = XP.normalize(pol if isinstance(pol, dict) and pol else floor, floor)
    cache[key] = pol
    return pol


def _one_gap(conn, rec, inp, cache):
    """`gap = r_факт − r(replay_brain(версия позиции + хронология, S))` одной сделки или None (путь непригоден)."""
    tick, entry = _num(rec.get("tick")), _num(rec.get("entry_px"))
    ets, stop = _int(rec.get("entry_ts")), _num(rec.get("stop0"))
    fact = _num(rec.get("r"))
    if tick is None or tick <= 0 or entry is None or entry <= 0 or ets is None or stop is None or fact is None:
        return None
    rows = [r for r in LP.decode(rec.get("rows"), tick, entry) if int(r[0]) >= ets]
    if not rows:
        return None
    mh = _num(rec.get("max_hold_min"))
    plan = {"side": rec.get("side"), "entry": {"kind": "market"}, "stop": stop, "tps": [], "fracs": [], "tick": tick,
            "max_hold_min": mh, "entry_px": entry}
    ctx = {"max_hold_min": mh, "atr5_pct": _num(rec.get("atr5_pct")), "sig_type": rec.get("sig_type"),
           "horizon": rec.get("horizon"), "entry_px": entry, "stop_moves": inp["stop_moves"]}
    lot = {"qty": _num(rec.get("qty")), "qty_step": _num(rec.get("qty_step")), "min_qty": _num(rec.get("min_qty"))}
    timeline = _j(rec.get("timeline"))
    timeline = timeline if isinstance(timeline, list) else []
    pol = _policy_of(conn, rec.get("exit_ver"), inp["floor"], cache)
    res = XS.replay_brain(plan, rows, pol, inp["floor"], inp["costs"], timeline, ctx, lot)
    if not res.get("filled") or _num(res.get("r_net")) is None:
        return None
    return float(fact) - float(res["r_net"])


def sim_gap(conn, inp, cfg, now):
    """`EXIT_SIM_GAP` (Requirement 8.7) -> {"active", "n", "mean", "threshold", "days", "since", "skipped"}.
    Сделки мозга режима `brain.mode` за `watch.sim_gap_days` суток; флаги gap/adopted пропускаются."""
    w = cfg["watch"]
    days = int(w["sim_gap_days"])
    min_n = int(w["sim_gap_min_n"])
    thr = float(w["sim_gap_r"])
    mode = (cfg.get("brain") or {}).get("mode")
    since = int(now) - days * DAY
    out = {"active": False, "n": 0, "mean": None, "threshold": thr, "days": days, "since": since, "skipped": 0}
    try:
        raw = conn.execute("SELECT %s FROM trade_paths WHERE entry_ts>=? AND entry_ts<? ORDER BY entry_ts, journal_id"
                           % ", ".join(TRADE_COLS), (since, int(now))).fetchall()
    except Exception:                                                       # noqa: BLE001 — нет таблицы путей сделок
        out["why"] = "NO_TABLE"
        return out
    cache, gaps, skipped = {}, [], 0
    for row in raw:
        rec = dict(zip(TRADE_COLS, row))
        if mode and rec.get("mode") != mode:
            continue
        flags = set(x for x in str(rec.get("flags") or "").split(",") if x)
        if LS.FLAG_GAP in flags or LS.FLAG_ADOPTED in flags:
            continue
        try:
            gap = _one_gap(conn, rec, inp, cache)
        except (ValueError, TypeError, KeyError):
            gap = None
        if gap is None:
            skipped += 1
            continue
        gaps.append(gap)
    mean = ST.mean(gaps)
    out.update(n=len(gaps), mean=_r(mean, 4), skipped=skipped)
    out["active"] = bool(len(gaps) >= min_n and mean is not None and abs(mean) > thr)
    return out


# ---------------------------------------------------------------- проверка

def _lease(root, now, row):
    """Атомарная запись аренды `runtime/exit_lease.json {ts, ver, policy_h, by="watch"}` (Requirement 8.5)."""
    return KM.write_json(XD.lease_path(root), {"ts": int(now), "ver": str(row["ver"]), "policy_h": str(row["policy_h"]), "by": "watch"})


def _log(conn, code, now, body):
    try:
        store2.log_event(conn, STEP, code, "code", body=body, now=now)
    except Exception:                                                       # noqa: BLE001 — журнал вторичен
        pass


def check(conn, root, cfg, now, lease=True):
    """Одна проверка сторожа (Requirement 8.1–8.8) -> {"code", "ver", "prev", "points", "rollback", "brain", "gap", "lease", "codes"}.
    Исключение шага (в том числе чтения данных) — аренда не пишется; повтор на тех же данных ничего не меняет."""
    now = int(now)
    out = {"code": OK, "ver": None, "prev": None, "points": [], "rollback": None, "brain": None, "gap": None, "lease": None, "codes": []}
    if not EC.is_enabled(cfg):
        out["code"] = DISABLED
        return out
    if EE.backlog(conn):
        out["code"] = BACKLOG
        return out
    cur, prev = _versions(conn)
    if cur is None:
        doc = state_get(conn)
        doc["ts"] = now
        _state_put(conn, doc)
        out["code"] = NO_VERSION
        return out
    out["ver"], out["prev"] = cur["ver"], (prev or {}).get("ver")
    inp = EE.market_inputs(root)
    doc = state_get(conn)
    st = doc.get("state") if isinstance(doc.get("state"), dict) else {}
    if st.get("seg") != cur["ver"]:
        st = {"seg": cur["ver"], "prev": (prev or {}).get("ver"), "a": int(cur.get("ts") or 0), "done": [], "week": 0,
              "points": [], "rollbacks": int(st.get("rollbacks") or 0)}
    else:
        st["prev"] = (prev or {}).get("ver")
        st["points"] = st.get("points") if isinstance(st.get("points"), list) else []
    a = int(st.get("a") or cur.get("ts") or 0)
    b, cut = _boundary(now, root)
    delta = float(cfg["watch"]["rollback_delta_r"])

    # ---- точки против предыдущей версии (Requirement 8.1–8.3)
    if prev is not None and cur["ver"] != XD.FLOOR_VER and int(b) > a:
        pairs = _eval_pairs(conn, cur, prev, EE.SRC_RUN, inp["cost_h"], XS.SIM_VER, a, b, cut)
        blocks = block_values(pairs)
        for label, xs, thr in points_due(blocks, b, st, cfg):
            d = decide(xs, thr, delta)
            rec = {"look": label, "n": d["n"], "mean": d["mean"], "sd": d["sd"], "p": d["p"], "thr": d["thr"],
                   "a": a, "b": int(b), "cut_ts": int(cut)}
            out["points"].append(rec)
            st["points"] = (st["points"] + [rec])[-POINTS_KEEP:]
            if label[0] == "L":
                st["done"] = sorted(set(st.get("done") or []) | {int(label[1:])})
            else:
                st["week"] = int(label[1:])
            if d["rollback"]:
                nums = {"n": d["n"], "mean_r": d["mean"], "sd": d["sd"], "p": d["p"], "thr": d["thr"], "look": label,
                        "seg": st["seg"], "a": a, "b": int(b), "cut_ts": int(cut), "ver": cur["ver"], "prev": (prev or {}).get("ver")}
                ver = XV.rollback(conn, root, ROLLBACK, nums, now, cfg=cfg)
                out["rollback"] = ver
                out["ver"], out["prev"] = ver, cur["ver"]
                st = {"seg": ver, "prev": cur["ver"], "a": now, "done": [], "week": 0, "points": st["points"],
                      "rollbacks": int(st.get("rollbacks") or 0) + 1, "last_rollback": nums}
                _log(conn, ROLLBACK, now, dict(nums, ver=ver, from_ver=cur["ver"]))
                break

    # ---- откат по сделкам мозга (Requirement 8.4)
    if out["rollback"] is None and prev is not None and cur["ver"] != XD.FLOOR_VER:
        br = brain_delta(conn, cur, prev, inp, cfg)
        out["brain"] = br
        if br["rollback"]:
            nums = {"n": br["n"], "mean_r": br["mean"], "sd": br["sd"], "p": br["p"], "look": "BRAIN",
                    "seg": st["seg"], "a": a, "ver": cur["ver"], "prev": (prev or {}).get("ver")}
            ver = XV.rollback(conn, root, ROLLBACK, nums, now, cfg=cfg)
            out["rollback"] = ver
            out["ver"], out["prev"] = ver, cur["ver"]
            st = {"seg": ver, "prev": cur["ver"], "a": now, "done": [], "week": 0, "points": st.get("points") or [],
                  "rollbacks": int(st.get("rollbacks") or 0) + 1, "last_rollback": nums}
            _log(conn, ROLLBACK, now, dict(nums, ver=ver, from_ver=cur["ver"]))

    # ---- расхождение симуляции с фактом (Requirement 8.7)
    out["gap"] = sim_gap(conn, inp, cfg, now)
    if out["gap"].get("active"):
        out["codes"].append(SIM_GAP)
        _log(conn, SIM_GAP, now, {"n": out["gap"]["n"], "mean": out["gap"]["mean"], "threshold": out["gap"]["threshold"]})

    last = XV.last_version(conn)
    if last is not None:
        if out["rollback"] is not None:
            out["lease"] = now                                   # аренда записана откатом в той же операции
        elif lease:
            _lease(root, now, last)
            out["lease"] = now
    if out["rollback"] is not None:
        out["code"] = ROLLBACK
    elif out["codes"]:
        out["code"] = SIM_GAP
    doc = {"ts": now, "state": st, "ver": (last or {}).get("ver") if last else None, "prev": st.get("prev"),
           "codes": out["codes"], "sim_gap": out["gap"]}
    _state_put(conn, doc)
    return out


def run(conn, root, cfg, now, lease=True):
    """Проверка сторожа (design.md: `exits_watch.run(conn, root, cfg, now) -> {"code", "points"}`)."""
    return check(conn, root, cfg, now, lease=lease)


def step_watch(ctx):
    """Шаг цикла `learnloop.cycle`: конфиг выходов, проверка, аренда (Requirement 16.7)."""
    cfg, errs = EC.load(ctx.root)
    if errs:
        return {"code": DISABLED}
    rep = run(ctx.conn, ctx.root, cfg, ctx.now)
    return {"code": rep["code"], "ver": rep["ver"], "points": len(rep["points"]), "rollback": rep["rollback"],
            "gap": bool((rep.get("gap") or {}).get("active")), "lease": rep["lease"]}
