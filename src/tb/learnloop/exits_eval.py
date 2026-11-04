"""Оценки вариантов выхода `exit_evals` (R4, задача 10; Requirement 4, 10.7, 14.3; design.md «Контрфактическая оценка»).

Шаг `eval_pass` (раз в час) считает чистым симулятором `tb.logic.exitsim` оценки каждого пути на каждом варианте и пишет их в таблицу
`exit_evals` (схема `x1`). Что оценивается:

  * пути прогонов меток (`run_paths`) в состоянии `complete` без флага `gap` — ключ прогона `(key, policy_id, policy_ver)`, цена входа и
    план берутся из строки пути (`plan` уже содержит `entry_px`);
  * пути сделок мозга (`trade_paths`) без флагов `gap` и `adopted` (сделка, принятая `plan adopt`, в оценки не входит — Requirement 1,
    Edge Cases); строки пути начинаются за 30 минут до входа, поэтому перед симулятором они срезаются до `entry_ts`;
  * варианты: действующая версия (`exit_versions`, последняя по `seq`), предыдущая версия (её `parent`, пока она под сторожем), варианты
    открытых испытаний (`exit_trials.state="open"`) и — только для путей прогонов окна поиска — все варианты меню (`config/exits.json`).
    Меню на путях сделок не оценивается: его потребитель — поиск, а поиск идёт по путям прогонов (Requirement 5.2).

Ключи и размерности строки (design.md, «Data Models»)

    * `src ∈ {run, trade}`; ключ оценки пути прогона — `"<key>|<policy_id>|<policy_ver>"` (в `exit_evals` нет отдельных колонок политики,
      а ключ сигнала один на `P2` и `P4`), ключ оценки сделки — `journal_id`;
    * `var_id`/`var_ver` версии политики — её имя `X-NNNN` и номер из имени (нет номера — `seq` записи версии); вариантов меню —
      `id`/`ver` из меню; испытаний — `var_id`/`var_ver` записи испытания;
    * `ord ∈ {S, T}`: `S` пишется всегда, `T` — только для пути, где порядок `S` дал неоднозначную свечу (`amb=1`, Requirement 3.3):
      `T`-строки нужны паре для `d_lo` (Requirement 6.4);
    * `px_mode ∈ {fee, taker}`: `fee` — обычная оценка, `taker` — та же оценка варианта с maker-частями, где maker-части исполнены по цене
      taker (пересчёт `ROBUST_COST`, Requirement 6.2, 10.7; только порядок `S`);
    * `block` — единица независимости `unit.block_h` часов и сторона (Requirement 4.4): `"<t0 // (block_h × 3600)>:<side>"`;
    * `path_mfe_r` — MFE пути на фиксированном горизонте `max_hold_min` от входа (признак хвоста, Requirement 6.3): свойство пути,
      одинаковое для всех вариантов;
    * `cost_h` — 16 hex sha256 входов издержек `exitsim` (комиссии и проскальзывание bybit, `stop_slip_bps`, спред), `sim_ver` —
      `exitsim.SIM_VER`.

Идемпотентность и пересчёт (Requirement 4.5, 4.6; свойства 12, 13)

    Существующие оценки ищутся ровно по ключу строки (включая `cost_h` и `sim_ver`), поэтому повторный проход на тех же путях ничего не
    пишет (`INSERT OR IGNORE` — только страховка), а смена `cost_h` или `sim_ver` делает старые строки невидимыми для поиска и заново
    ставит все пути в очередь: оценки прежних значений остаются в таблице и не смешиваются с новыми.

Лимит и очередь (Requirement 4.7)

    За проход пишется не больше `eval.max_per_pass` строк; остаток переносится на следующий проход. Число оставшихся задач пишется в
    `meta.eval_backlog` (счётчик `eval_backlog` в heartbeat); поиск, вердикт и сторож ждут `eval_backlog == 0` (Requirement 4.5).

Окно поиска (Requirement 4.3, 5.2)

    Меню оценивается только на путях окна `t0 ∈ [b − window_days × сутки, b)`, где `b` — последняя граница часа с
    `cut_ts(b) = b + cluster_close_max_h + observe_s ≤ now` (`config/learnloop.json`, механика `learnloop.clusters`), и только при
    `labeled_ts ≤ cut_ts`. Граница и момент отсечения отдаются в результате шага (`window`) и не зависят от прохода — повторный расчёт на
    том же окне даёт те же оценки (свойство 12).

Ротация (Requirement 1.3)

    Оценки вариантов меню старше `eval.keep_days_menu` суток удаляются, если вариант не участвует в открытом испытании; оценки версий не
    удаляются (их читает сторож).

`pass_active` справочно (Requirement 14.3)

    Варианты оцениваются на ВСЕХ заполненных прогонах, включая отсечённые активным rulebook R2. Отдельный справочный срез даёт
    `pass_active_keys`: ключи прогонов, которые не отсекает ни одно действующее правило (`learnloop.rules.row_hit` по строке
    `cluster_runs`). Числа среза шаг возвращает в `pass_active`/`cut`; отдельной размерности в `exit_evals` нет, срез вычисляется по ключам.

Слои `tb.learnloop`: модуль не импортирует мозг, шлюз, биржу, исполнитель, управление, launcher и CLI вне пакета; файлы конфигов читает
только через загрузчики (`exits_conf`, `learnloop.conf`, `core.exec_cfg`, загрузчик меток), сам ничего в `config/` не пишет. Сеть не
используется. Ошибка одного пути не останавливает проход: путь пропускается, счётчик `errors` растёт.
"""
import hashlib
import json

from tb.core import exec_cfg
from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.labels import capture as label_capture
from tb.labels import paths as LP
from tb.labels import policies as label_policies
from tb.labels import schema as LS
from tb.labels import store as LST
from tb.learnloop import clusters as CL
from tb.learnloop import conf as LC
from tb.learnloop import exits_conf as EC
from tb.learnloop import rules as RL
from tb.logic import exitpol as XP
from tb.logic import exitsim as XS

STEP = "exits_eval"
META_BACKLOG = "eval_backlog"
SRC_RUN, SRC_TRADE = "run", "trade"
ORDER_S, ORDER_T = "S", "T"
PX_FEE, PX_TAKER = "fee", "taker"
RUN_KEY_SEP = "|"
COST_KEYS = ("fee", "maker_fee", "slip", "stop_slip_bps", "spread_bps")

RUN_PATH_COLS = ("key", "policy_id", "policy_ver", "t0", "sym", "type", "side", "horizon", "entry_ts", "entry_px", "stop", "tick", "max_hold_min",
                 "atr5_pct", "plan", "rows", "n_rows", "end_ts", "state", "flags", "labeled_ts")
TRADE_PATH_COLS = ("journal_id", "sym", "side", "mode", "entry_ts", "entry_px", "stop0", "tick", "qty", "qty_step", "min_qty", "horizon",
                   "max_hold_min", "atr5_pct", "sig_type", "rows", "end_ts", "flags", "timeline", "fetched_ts")


# ---------------------------------------------------------------- мелочи

def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


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


def _flag_set(flags):
    return set(x for x in str(flags or "").split(",") if x)


def run_key(key, policy_id, policy_ver):
    """Ключ оценки пути прогона: в `exit_evals` нет колонок политики, а ключ сигнала общий у P2 и P4."""
    return "%s%s%s%s%d" % (key, RUN_KEY_SEP, policy_id, RUN_KEY_SEP, int(policy_ver))


def split_run_key(k):
    """Обратное к `run_key`: -> (key, policy_id, policy_ver) или None."""
    parts = str(k or "").rsplit(RUN_KEY_SEP, 2)
    if len(parts) != 3:
        return None
    try:
        return parts[0], parts[1], int(parts[2])
    except ValueError:
        return None


def version_var(ver, seq=0):
    """Версия политики -> (var_id, var_ver): имя `X-NNNN` и номер из имени (нет номера — seq записи версии)."""
    s = str(ver)
    tail = s.rsplit("-", 1)[-1]
    try:
        return s, int(tail)
    except ValueError:
        return s, int(seq or 0)


def block_id(t0, side, block_h):
    """Блок `unit.block_h` часов и сторона (Requirement 4.4); при block_h = 1 совпадает с кластером R2."""
    return "%d:%s" % (int(t0) // (int(block_h) * CL.HOUR), side)


def costs_hash(costs):
    """16 hex sha256 входов издержек симулятора (Requirement 4.2): меняется вместе с комиссиями и проскальзыванием."""
    c = dict((k, _num((costs or {}).get(k))) for k in COST_KEYS)
    blob = json.dumps(c, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _meta_set(conn, k, v):
    conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?,?)", (str(k), str(int(v))))


def _meta_get(conn, k):
    try:
        row = conn.execute("SELECT v FROM meta WHERE k=?", (str(k),)).fetchone()
    except Exception:
        return None
    return _int(row[0]) if row and row[0] is not None else None


def backlog(conn):
    """Счётчик `eval_backlog` (Requirement 4.7): число оценок, оставшихся на следующие проходы; 0 — очередь пуста."""
    return _meta_get(conn, META_BACKLOG) or 0


# ---------------------------------------------------------------- входы шага: пол, издержки, стоп-мувы

def market_inputs(root=None):
    """Пол защиты, `stop_moves` и издержки симулятора из действующих конфигов исполнителя и профиля риска.

    Пол — `exitpol.floor(exec_limits.auto_protect)`; `stop_moves` — секция `exec_limits.stop_moves` (тот же загрузчик, что у исполнителя);
    издержки — общий вход меток (`labels.policies.sim_inputs`: комиссии bybit, проскальзывание, спред `labels.json`), `stop_slip_bps`
    уточняется из `auto_protect`. -> {"floor", "stop_moves", "costs", "cost_h"}.
    """
    lim = exec_cfg.load(root)
    lim = lim if isinstance(lim, dict) else {}
    ap = lim.get("auto_protect") if isinstance(lim.get("auto_protect"), dict) else {}
    try:
        lab = label_capture.load_cfg(root)
    except (ValueError, TypeError, OSError):
        lab = {}
    costs, _manage = label_policies.sim_inputs(root, lab if isinstance(lab, dict) else {}, None)
    costs = dict(costs or {})
    ss = _num(ap.get("stop_slip_bps"))
    if ss is not None:
        costs["stop_slip_bps"] = ss
    sm = lim.get("stop_moves") if isinstance(lim.get("stop_moves"), dict) else {}
    return {"floor": XP.floor(ap), "stop_moves": sm, "costs": costs, "cost_h": costs_hash(costs)}


def maker_cfg(cfg):
    """Параметры модели maker-исполнения из `config/exits.json` (Requirement 3.5)."""
    m = (cfg or {}).get("maker") if isinstance((cfg or {}).get("maker"), dict) else {}
    return {"through_ticks": m.get("through_ticks"), "through_bps": m.get("through_bps"), "wait_s": m.get("wait_s")}


# ---------------------------------------------------------------- варианты

def _version_rows(conn):
    """Записи `exit_versions` по `seq` -> [{"seq", "ver", "parent", "policy", "policy_h"}] (пусто, если таблицы нет)."""
    try:
        raw = conn.execute("SELECT seq, ver, parent, policy, policy_h FROM exit_versions ORDER BY seq").fetchall()
    except Exception:
        return []
    out = []
    for seq, ver, parent, pol, ph in raw:
        p = _j(pol)
        if not isinstance(p, dict):
            continue
        out.append({"seq": int(seq), "ver": str(ver), "parent": parent if isinstance(parent, str) else None,
                    "policy": p, "policy_h": ph})
    return out


def _variant(policy, var_id, var_ver, role, floor_policy):
    n = XP.normalize(policy, floor_policy)
    return {"var_id": str(var_id), "var_ver": int(var_ver), "policy": n, "policy_h": XP.policy_hash(n), "role": role}


def version_variants(conn, floor_policy):
    """Действующая версия (последняя по `seq`) и предыдущая — её `parent` (Requirement 4.1; предыдущая оценивается, пока под сторожем)."""
    rows = _version_rows(conn)
    if not rows:
        return []
    last = rows[-1]
    out = [_variant(last["policy"], *version_var(last["ver"], last["seq"]), role="active", floor_policy=floor_policy)]
    prev = None
    if last["parent"]:
        prev = next((r for r in rows if r["ver"] == last["parent"]), None)
    if prev is None and len(rows) > 1:
        prev = rows[-2]
    if prev is not None and prev["ver"] != last["ver"]:
        out.append(_variant(prev["policy"], *version_var(prev["ver"], prev["seq"]), role="prev", floor_policy=floor_policy))
    return out


def menu_variants(cfg, floor_policy):
    """Варианты меню `config/exits.json` (нормализованные с полом, как их отдаёт `exits_conf`)."""
    try:
        items = EC.variants(cfg, floor_policy)
    except (ValueError, TypeError, KeyError):
        return []
    return [{"var_id": str(it["id"]), "var_ver": int(it["ver"]), "policy": it["policy"], "policy_h": it["policy_h"], "role": "menu"}
            for it in items]


def _variant_from_table(conn, var_id, var_ver, floor_policy):
    """Политика зарегистрированного варианта из `exit_variants` (если его нет в меню)."""
    try:
        row = conn.execute("SELECT policy FROM exit_variants WHERE id=? AND ver=?", (var_id, int(var_ver))).fetchone()
    except Exception:
        return None
    p = _j(row[0]) if row else None
    return p if isinstance(p, dict) else None


def open_trial_variants(conn, cfg, floor_policy):
    """Варианты открытых испытаний (`exit_trials.state="open"`); политика — из меню или `exit_variants`."""
    try:
        raw = conn.execute("SELECT DISTINCT var_id, var_ver FROM exit_trials WHERE state=? ORDER BY var_id, var_ver", ("open",)).fetchall()
    except Exception:
        return []
    menu = dict(((v["var_id"], v["var_ver"]), v) for v in menu_variants(cfg, floor_policy))
    out = []
    for vid, vver in raw:
        v = menu.get((str(vid), int(vver)))
        if v is not None:
            out.append(dict(v, role="trial"))
            continue
        p = _variant_from_table(conn, str(vid), int(vver), floor_policy)
        if p is not None:
            out.append(_variant(p, vid, int(vver), "trial", floor_policy))
    return out


def variants(conn, cfg, floor_policy):
    """-> (базовые варианты, варианты меню): базовые — действующая и предыдущая версии и открытые испытания, без повторов по (id, ver);
    порядок: активная, предыдущая, испытания, затем меню."""
    base, seen = [], set()
    for v in version_variants(conn, floor_policy) + open_trial_variants(conn, cfg, floor_policy):
        k = (v["var_id"], v["var_ver"])
        if k in seen:
            continue
        seen.add(k)
        base.append(v)
    menu = []
    for v in menu_variants(cfg, floor_policy):
        k = (v["var_id"], v["var_ver"])
        if k in seen:
            continue
        seen.add(k)
        menu.append(v)
    return base, menu


def _has_maker(policy):
    """Есть ли в политике компонент со способом исполнения maker (`partial`, `near_tp`, `decay`, `timeout`)."""
    p = policy if isinstance(policy, dict) else {}
    for comp in ("partial", "near_tp"):
        val = p.get(comp)
        seq = val if isinstance(val, list) else ([val] if isinstance(val, dict) else [])
        for it in seq:
            if isinstance(it, dict) and it.get("exec") == "maker":
                return True
    for comp in ("decay", "timeout"):
        it = p.get(comp)
        if isinstance(it, dict) and it.get("exec") == "maker":
            return True
    return False


# ---------------------------------------------------------------- окно поиска

def window(cfg, root, now):
    """Окно поиска (Requirement 4.3, 5.2): -> {"a", "b", "cut_ts"} или None, если конфиг R2 не читается.
    `b` — последняя граница часа с `cut_ts(b) ≤ now`, `a = b − search.window_days × сутки`."""
    lc, errs = LC.load(root)
    if errs:
        return None
    b = CL.last_b(now, lc)
    a = int(b) - int(cfg["search"]["window_days"]) * CL.DAY
    return {"a": int(a), "b": int(b), "cut_ts": int(CL.cut_ts(b, lc))}


def in_window(win, t0, labeled_ts):
    """Путь принадлежит выборке окна: t0 ∈ [a, b) и labeled_ts ≤ cut_ts (Requirement 4.3)."""
    if not win:
        return False
    t0 = _int(t0)
    lab = _int(labeled_ts)
    if t0 is None or lab is None:
        return False
    return win["a"] <= t0 < win["b"] and lab <= win["cut_ts"]


# ---------------------------------------------------------------- пути

def _iter_run_paths(conn):
    """Заполненные пути прогонов в состоянии complete по возрастанию t0 (без флага gap — фильтр в `_plan`)."""
    sql = "SELECT %s FROM run_paths WHERE state=? ORDER BY t0, key, policy_id, policy_ver" % ",".join(RUN_PATH_COLS)
    for r in conn.execute(sql, (LS.PATH_COMPLETE,)):
        yield dict(zip(RUN_PATH_COLS, r))


def _iter_trade_paths(conn):
    """Пути сделок мозга по возрастанию entry_ts."""
    sql = "SELECT %s FROM trade_paths ORDER BY entry_ts, journal_id" % ",".join(TRADE_PATH_COLS)
    for r in conn.execute(sql):
        yield dict(zip(TRADE_PATH_COLS, r))


def _path_data(src, row, stop_moves):
    """Строка пути -> вход симулятора: план, срезанные минутки, хронология, лот, ctx, t0/block-поля.

    Путь сделки начинается за 30 минут до входа (Requirement 1.2): строки срезаются до `entry_ts` и дальше не отличаются от пути прогона.
    """
    tick = _num(row.get("tick"))
    entry_px = _num(row.get("entry_px"))
    entry_ts = _int(row.get("entry_ts"))
    if tick is None or tick <= 0 or entry_px is None or entry_px <= 0 or entry_ts is None:
        raise ValueError("путь: нет тика, входа или времени входа")
    rows = LP.decode(row.get("rows"), tick, entry_px)
    rows = [r for r in rows if int(r[0]) >= entry_ts]
    if not rows:
        raise ValueError("путь: нет свечей после входа")
    if src == SRC_RUN:
        plan = _j(row.get("plan"))
        if not isinstance(plan, dict):
            raise ValueError("путь прогона: нет плана")
        plan = dict(plan)
        plan["entry_px"] = entry_px
        side = plan.get("side") or row.get("side")
        mh = _num(row.get("max_hold_min"))
        if mh is None:
            mh = _num(plan.get("max_hold_min"))
        timeline, lot = None, None
        sig_type, horizon = row.get("type"), row.get("horizon")
        key = run_key(row.get("key"), row.get("policy_id"), _int(row.get("policy_ver"), 0))
        t0 = _int(row.get("t0"), entry_ts)
        labeled_ts = _int(row.get("labeled_ts"), entry_ts)
    else:
        plan = {"side": row.get("side"), "entry": {"kind": "market"}, "stop": _num(row.get("stop0")), "tps": [], "fracs": [],
                "tick": tick, "max_hold_min": _num(row.get("max_hold_min")), "entry_px": entry_px}
        side = row.get("side")
        mh = _num(row.get("max_hold_min"))
        timeline = _j(row.get("timeline")) or []
        lot = {"qty": _num(row.get("qty")), "qty_step": _num(row.get("qty_step")), "min_qty": _num(row.get("min_qty"))}
        sig_type, horizon = row.get("sig_type"), row.get("horizon")
        key = str(row.get("journal_id"))
        t0 = entry_ts
        labeled_ts = _int(row.get("fetched_ts"), entry_ts)
    if not isinstance(plan.get("entry_px"), (int, float)) or _num(plan.get("entry_px")) is None:
        plan["entry_px"] = entry_px
    ctx = {"max_hold_min": mh, "atr5_pct": _num(row.get("atr5_pct")), "sig_type": sig_type, "horizon": horizon,
           "entry_px": entry_px, "stop_moves": stop_moves}
    stop = _num(plan.get("stop"))
    return {"src": src, "key": key, "t0": int(t0), "side": side, "entry_px": entry_px, "stop": stop, "plan": plan, "rows": rows,
            "ctx": ctx, "timeline": timeline, "lot": lot, "labeled_ts": int(labeled_ts), "mh": mh,
            "path_mfe_r": XS.path_mfe(rows, entry_px, stop, side, mh)}


# ---------------------------------------------------------------- существующие оценки и задачи прохода

def existing(conn, src, key, cost_h, sim_ver=None):
    """Оценки пути при текущих входах издержек и версии симулятора -> {(var_id, var_ver, ord, px_mode): amb}.
    Строки прежних `cost_h`/`sim_ver` не видны: их значения не смешиваются с новыми (Requirement 4.5)."""
    sv = XS.SIM_VER if sim_ver is None else sim_ver
    try:
        raw = conn.execute("SELECT var_id, var_ver, ord, px_mode, amb FROM exit_evals WHERE src=? AND key=? AND cost_h=? AND sim_ver=?",
                           (src, key, cost_h, sv)).fetchall()
    except Exception:
        return {}
    out = {}
    for vid, vver, order, mode, amb in raw:
        out[(str(vid), int(vver), str(order), str(mode))] = int(amb or 0)
    return out


def tasks_for(done, variants_):
    """Недостающие оценки пути: [(variant, ord, px_mode)].

    `S`/`fee` — всегда; `T`/`fee` — если оценка `S` была неоднозначной (`amb=1`), но строки `T` нет; `S`/`taker` — только у варианта с
    maker-частями (пересчёт `ROBUST_COST`)."""
    out = []
    for v in variants_ or []:
        k = (v["var_id"], int(v["var_ver"]))
        if (k + (ORDER_S, PX_FEE)) not in done:
            out.append((v, ORDER_S, PX_FEE))
        elif done.get(k + (ORDER_S, PX_FEE)) and (k + (ORDER_T, PX_FEE)) not in done:
            out.append((v, ORDER_T, PX_FEE))
        if _has_maker(v["policy"]) and (k + (ORDER_S, PX_TAKER)) not in done:
            out.append((v, ORDER_S, PX_TAKER))
    return out


def _plan(conn, cfg, floor_policy, base_variants, menu_variants_, win, cost_h):
    """Обход путей и недостающих оценок: yield (src, строка пути, [задачи], done). Путь без задач не отдаётся."""
    for row in _iter_run_paths(conn):
        if LS.FLAG_GAP in _flag_set(row.get("flags")):
            continue
        vs = list(base_variants)
        if in_window(win, row.get("t0"), row.get("labeled_ts")):
            vs = vs + list(menu_variants_)
        if not vs:
            continue
        key = run_key(row.get("key"), row.get("policy_id"), _int(row.get("policy_ver"), 0))
        done = existing(conn, SRC_RUN, key, cost_h)
        tasks = tasks_for(done, vs)
        if tasks:
            yield SRC_RUN, row, tasks, done
    for row in _iter_trade_paths(conn):
        flags = _flag_set(row.get("flags"))
        if LS.FLAG_GAP in flags or LS.FLAG_ADOPTED in flags:
            continue
        if not base_variants:
            continue
        key = str(row.get("journal_id"))
        done = existing(conn, SRC_TRADE, key, cost_h)
        tasks = tasks_for(done, base_variants)
        if tasks:
            yield SRC_TRADE, row, tasks, done


# ---------------------------------------------------------------- прогон и строка оценки

def simulate(data, variant, order, px_mode, floor_policy, costs, maker):
    """Оценка варианта на пути: путь сделки с хронологией мозга идёт через `replay_brain` (порядок `S`, обычные издержки), остальные
    случаи — через `exitsim.run` с нужным порядком и способом исполнения maker-частей."""
    if data["src"] == SRC_TRADE and order == ORDER_S and px_mode == PX_FEE:
        return XS.replay_brain(data["plan"], data["rows"], variant["policy"], floor_policy, costs, data["timeline"], data["ctx"], data["lot"])
    return XS.run(data["plan"], data["rows"], variant["policy"], floor_policy, costs, order=order, maker=maker,
                  timeline=data["timeline"], ctx=data["ctx"], lot=data["lot"], taker_only=(px_mode == PX_TAKER))


def eval_row(data, variant, order, px_mode, res, cost_h, now, block_h):
    """Строка `exit_evals` (Requirement 4.2). `cut` пары не хранится: он вычисляется по `end_ts` сторон при расчёте разности."""
    return {"src": data["src"], "key": data["key"], "var_id": variant["var_id"], "var_ver": int(variant["var_ver"]),
            "ord": order, "px_mode": px_mode, "policy_h": variant["policy_h"], "cost_h": cost_h, "sim_ver": XS.SIM_VER,
            "t0": int(data["t0"]), "block": block_id(data["t0"], data["side"], block_h), "labeled_ts": int(data["labeled_ts"]),
            "r_net": res.get("r_net"), "r_gross": res.get("r_gross"), "fees_r": res.get("fees_r"), "mfe_r": res.get("mfe_r"),
            "mae_r": res.get("mae_r"), "path_mfe_r": data["path_mfe_r"], "exit_reason": res.get("exit_reason"),
            "hold_min": res.get("hold_min"), "end_ts": res.get("end_ts"), "amb": 1 if res.get("amb") else 0, "created_ts": int(now)}


# ---------------------------------------------------------------- справочный срез pass_active

def active_rules(conn):
    """Действующие правила R2 (последняя версия rulebook); пусто, если таблиц rulebook нет."""
    try:
        return RV.active_from_conn(conn).get("rules") or []
    except Exception:
        return []


def pass_active_keys(conn, active=None, keys=None):
    """Отсечённые активным rulebook R2 ключи прогонов (Requirement 14.3, справочно): ключи `run_paths`, для которых хотя бы одно правило
    даёт хит по признакам строки `cluster_runs` (`learnloop.rules.row_hit`). None — активных правил нет и не отсекается ничего.
    `keys` сужает множество проверяемых ключей (иначе читается весь `cluster_runs`)."""
    acts = active_rules(conn) if active is None else [r for r in (active or []) if isinstance(r, dict)]
    if not acts:
        return None
    want = set(keys) if keys is not None else None
    cut = set()
    try:
        if want is not None:
            items = sorted(want)
            raw = []
            for i in range(0, len(items), 500):
                chunk = items[i:i + 500]
                raw.extend(conn.execute("SELECT key, policy_id, policy_ver, type, feats FROM cluster_runs WHERE key IN (%s)"
                                        % ",".join("?" * len(chunk)), chunk).fetchall())
        else:
            raw = conn.execute("SELECT key, policy_id, policy_ver, type, feats FROM cluster_runs").fetchall()
    except Exception:
        return cut
    for key, policy_id, policy_ver, typ, feats in raw:
        if want is not None and key not in want:
            continue
        row = {"key": key, "policy_id": policy_id, "policy_ver": policy_ver, "type": typ, "feats": feats}
        if any(RL.row_hit(a, row) for a in acts):
            cut.add(key)
    return cut


# ---------------------------------------------------------------- ротация оценок меню

def rotate(conn, cfg, now, menu=None):
    """Удаление оценок вариантов меню старше `eval.keep_days_menu` (Requirement 1.3), кроме вариантов открытых испытаний.
    Оценки версий не удаляются: их читает сторож. -> число удалённых строк."""
    keep_days = _int((cfg.get("eval") or {}).get("keep_days_menu"))
    if keep_days is None:
        return 0
    ids = set((v["var_id"], int(v["var_ver"])) for v in (menu if menu is not None else menu_variants(cfg, {})))
    if not ids:
        return 0
    try:
        open_vars = set((str(r[0]), int(r[1])) for r in conn.execute("SELECT var_id, var_ver FROM exit_trials WHERE state=?", ("open",)))
    except Exception:
        open_vars = set()
    cutoff = int(now) - int(keep_days) * CL.DAY
    try:
        raw = conn.execute("SELECT src, key, var_id, var_ver, ord, px_mode, cost_h, sim_ver FROM exit_evals WHERE created_ts < ?",
                           (cutoff,)).fetchall()
    except Exception:
        return 0
    doomed = []
    for src, key, vid, vver, order, mode, ch, sv in raw:
        k = (str(vid), int(vver))
        if k in ids and k not in open_vars:
            doomed.append((src, key, vid, int(vver), order, mode, ch, sv))
    if not doomed:
        return 0
    with LST.tx(conn):
        conn.executemany("DELETE FROM exit_evals WHERE src=? AND key=? AND var_id=? AND var_ver=? AND ord=? AND px_mode=? AND cost_h=? AND sim_ver=?",
                         doomed)
    return len(doomed)


# ---------------------------------------------------------------- шаг

def eval_pass(conn, root, cfg, now, limit=None):
    """Шаг оценок (Requirement 4.1–4.7): дописать недостающие строки `exit_evals` по путям complete.

    -> {"n" (записано строк), "backlog" (осталось задач), "errors", "rotated", "paths" (путей с работой), "window", "cost_h",
    "sim_ver", "pass_active", "cut"}. `limit` заменяет `eval.max_per_pass` (тесты и ручные прогоны); выключенный конфиг -> нули.
    """
    now = int(now)
    out = {"n": 0, "backlog": 0, "errors": 0, "rotated": 0, "paths": 0, "window": None, "cost_h": None, "sim_ver": XS.SIM_VER,
           "pass_active": 0, "cut": 0}
    if not EC.is_enabled(cfg):
        return out
    inp = market_inputs(root)
    floor_policy, stop_moves, costs, cost_h = inp["floor"], inp["stop_moves"], inp["costs"], inp["cost_h"]
    maker = maker_cfg(cfg)
    block_h = _int((cfg.get("unit") or {}).get("block_h"))
    if block_h is None:
        raise ValueError("config/exits.json: нет unit.block_h")
    base_variants, menu_vars = variants(conn, cfg, floor_policy)
    win = window(cfg, root, now)
    out.update(cost_h=cost_h, window=win)
    try:
        out["rotated"] = rotate(conn, cfg, now, menu_vars)
    except Exception:
        out["rotated"] = 0
    lim = _int((cfg.get("eval") or {}).get("max_per_pass")) if limit is None else _int(limit)
    if lim is None:
        raise ValueError("config/exits.json: нет eval.max_per_pass")
    rows, written, backlog_n, seen_keys = [], 0, 0, set()
    for src, prow, tasks, done in _plan(conn, cfg, floor_policy, base_variants, menu_vars, win, cost_h):
        if src == SRC_RUN:
            seen_keys.add(prow.get("key"))
        if written >= lim:
            backlog_n += len(tasks)
            continue
        try:
            data = _path_data(src, prow, stop_moves)
        except (ValueError, TypeError, KeyError):
            out["errors"] += 1
            continue
        queue = list(tasks)
        while queue:
            task = queue.pop(0)
            if written >= lim:
                backlog_n += 1 + len(queue)
                queue = []
                break
            variant, order, px_mode = task
            try:
                res = simulate(data, variant, order, px_mode, floor_policy, costs, maker)
            except (ValueError, TypeError, KeyError):
                out["errors"] += 1
                continue
            if not isinstance(res, dict) or not res.get("filled"):
                out["errors"] += 1
                continue
            rows.append(eval_row(data, variant, order, px_mode, res, cost_h, now, block_h))
            written += 1
            k = (variant["var_id"], int(variant["var_ver"]))
            if order == ORDER_S and px_mode == PX_FEE and res.get("amb"):
                if k + (ORDER_T, PX_FEE) not in done and not any(t[1] == ORDER_T and t[0] is variant for t in queue):
                    queue.append((variant, ORDER_T, PX_FEE))
        out["paths"] += 1
    if rows:
        with LST.tx(conn):
            out["n"] = store2.insert_rows(conn, "exit_evals", rows, ignore_dup=True)
            _meta_set(conn, META_BACKLOG, backlog_n)
    else:
        with LST.tx(conn):
            _meta_set(conn, META_BACKLOG, backlog_n)
    out["backlog"] = backlog_n
    cut = pass_active_keys(conn, keys=seen_keys) if seen_keys else None
    if cut is None:
        out["pass_active"] = len(seen_keys)
    else:
        out["cut"] = len(set(seen_keys) & cut)
        out["pass_active"] = len(seen_keys) - out["cut"]
    return out
