"""Испытание варианта выхода `tb.learnloop.exits_trial` (R4, задача 12; Requirement 6, design.md «Испытание», «Статистика», «Ревизии»).

Критерий записывается при регистрации и не меняется (`criterion`, `criterion_h` = 16 hex sha256 канонического JSON; триггер
`tr_exit_trials_crit`): вариант и база (версия, `policy_h`, maker-части), источник путей (политики и их версии, `cost_h`, `sim_ver`),
`unit.block_h`, `trial.n_target`, `brain.n`, `alpha_adj = trial.alpha / trial.max_parallel`, `min_effect_r`, параметры хвоста,
допуск устойчивости, допуск по сделкам мозга, `start_ts`, `deadline_ts = start_ts + trial.max_days`, параметры `cut_ts`, хеш
`config/exits.json` и хеш пола защиты (Requirement 6.1, 16.2). Испытание идёт только на будущих данных: пути прогонов с
`t0 ≥ start_ts`, сделки мозга с `entry_ts ≥ start_ts` (свойство 17).

Выборка (Requirement 4.3, 6.2, 6.6)
    Единица независимости — блок (`unit.block_h` часов, сторона) из строк `exit_evals`; значение блока — среднее `d` его путей.
    Берутся первые `n_target` блоков выборки окна `t0 ∈ [start_ts, b)` с `labeled_ts ≤ cut_ts(b) = b + cluster_close_max_h +
    observe_s`. Вердикт считается не раньше `cut_ts(b)` той границы часа `b`, на которой набраны `n_target` блоков,
    `brain.n` сделок мозга и `tail.min_n` путей хвоста; если к `deadline_ts` чего-то не хватило — по всем имеющимся данным,
    а при нехватке блоков/хвоста или сделок (меньше `brain.min_n`) испытание закрывается `inconclusive` (`NO_VERDICT`) с
    причиной `N` или `BRAIN_N`, не раньше `cut_ts(deadline_ts)` (Requirement 6.3, 6.5, 6.7).

Условия `verdict` (чистая функция, Requirement 6.2–6.5, свойство 16)
    * `MEAN` — одностороннее p парного t-теста среднего `d` по блокам в сторону «вариант лучше» < `alpha_adj` AND среднее `d` ≥
      `min_effect_r`;
    * `ROBUST_COST` — для варианта с maker-частями то же условие при цене taker у maker-частей (`px_mode="taker"`); у варианта
      без maker-частей условие выполнено по построению;
    * `TAIL` — на путях хвоста (`path_mfe_r ≥ tail.mfe_r`, признак пути) Σ `r_net` варианта ≥ `tail.keep` × Σ `r_net` базы,
      путей хвоста не меньше `tail.min_n`;
    * `ROBUST` — среднее по блокам `d_lo = min(d(S), d(T))` ≥ −`robust.tol_r` (обе стороны пары в одном порядке; `T`-строка
      может отсутствовать — подставляется `S`, тогда `d_lo = d`; ревизия 2026-11-04);
    * `BRAIN` — нижняя граница одностороннего 90% интервала среднего `d_b` на первых `brain.n` сделках мозга (или всех к
      дедлайну, но не меньше `brain.min_n`) ≥ −`brain.tol_r`; порог один при любом n (меньше сделок — шире интервал, условие
      строже само).
    `promote` — только когда выполнены все условия; иначе `reject` (`TRIAL_REJECT`, в числах — невыполненные условия; чёрный
    список `exit_blacklist` на `cooldown_days`). Доля забранного MFE (`Σ r_net / Σ mfe_r`), доля издержек
    (`Σ fees_r / |Σ r_gross|`) и доля пар `cut` пишутся в числа справочно и в критерий не входят (Requirement 6.9).

Шаг `verdicts` (раз в час)
    Ждёт пустой очереди `eval_backlog` (Requirement 4.5). Смена `cost_h`, `sim_ver`, пола защиты или действующей версии
    закрывает открытые испытания `inconclusive` кодами `COST_CHANGED`, `SIM_CHANGED`, `FLOOR_CHANGED`, `BASE_CHANGED` без
    чёрного списка (Requirement 6.8). Закрытое испытание не трогается, повторный расчёт на той же выборке даёт тот же вердикт
    (свойство 12, 15).

Ключи строк `exit_evals` — соглашения задачи 10: `exits_eval.run_key/split_run_key` для ключа пути прогона (в таблице уже
хранится готовый ключ) и `var_id/var_ver` для варианта. Пары «вариант — база» собираются по (src, key, ord, px_mode); строка
`S/fee` сделки мозга — это `replay_brain` варианта с хронологией вмешательств (Requirement 6.5).

Слои `tb.learnloop`: модуль не импортирует мозг, шлюз, биржу, исполнитель, управление и CLI; конфиги только читает
(`exits_conf`, `learnloop.conf`, `exits_eval.market_inputs`), в `config/` не пишет. Сеть не используется.
"""
import hashlib
import json
import math

from tb.core import expstats as ES
from tb.knowledge import store2
from tb.labels import store as LST
from tb.learnloop import clusters as CL
from tb.learnloop import conf as LC
from tb.learnloop import exits_conf as EC
from tb.learnloop import exits_eval as EE
from tb.learnloop import stat as ST
from tb.logic import exitpol as XP
from tb.logic import exitsim as XS
from tb.memory import chain as CH

HOUR = CL.HOUR
DAY = CL.DAY
STEP = "exits_trial"
CRIT_VER = 1
OPEN, CLOSED = "open", "closed"
PROMOTE, REJECT, INCONCLUSIVE = "promote", "reject", "inconclusive"
SIDE = "higher"                                    # «вариант лучше базы» — среднее d > 0
R_N, R_BRAIN_N = "N", "BRAIN_N"                    # причины NO_VERDICT (Requirement 6.3, 6.5, 6.7)
COST_CHANGED, SIM_CHANGED, FLOOR_CHANGED, BASE_CHANGED = "COST_CHANGED", "SIM_CHANGED", "FLOOR_CHANGED", "BASE_CHANGED"
CODE_READY, CODE_REJECT, CODE_NO_VERDICT = "READY", "TRIAL_REJECT", "NO_VERDICT"

TRIAL_COLS = ("trial_id", "var_id", "var_ver", "base_ver", "criterion", "criterion_h", "state", "verdict", "numbers", "start_ts", "deadline_ts",
              "cut_ts", "closed_ts")
EVAL_COLS = ("src", "key", "t0", "block", "labeled_ts", "var_id", "var_ver", "ord", "px_mode", "r_net", "r_gross", "fees_r", "mfe_r", "path_mfe_r",
             "exit_reason", "end_ts", "amb")
CONDS = ("MEAN", "ROBUST_COST", "TAIL", "ROBUST", "BRAIN")


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


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _block_key(block):
    """Блок `"<t0 // (block_h × 3600)>:<side>"` -> ключ хронологического порядка (мусор — в конец)."""
    head, _, side = str(block or "").partition(":")
    try:
        return (int(head), side)
    except ValueError:
        return (10 ** 18, str(block))


def _crit(trial):
    """Критерий испытания из строки `exit_trials` или из самого критерия."""
    if isinstance(trial, dict) and isinstance(trial.get("criterion"), dict):
        return trial["criterion"]
    return trial if isinstance(trial, dict) else {}


def crit_hash(crit):
    """`criterion_h`: 16 hex sha256 канонического JSON критерия (Requirement 6.1, свойство 15)."""
    return hashlib.sha256(CH.canon(crit).encode("utf-8")).hexdigest()[:16]


def criterion_ok(trial):
    """Свойство 15: записанный `criterion_h` совпадает с хешем записанного критерия."""
    crit = _crit(trial)
    return bool(crit) and crit_hash(crit) == (trial.get("criterion_h") if isinstance(trial, dict) else None)


def has_maker(policy):
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


# ---------------------------------------------------------------- чтение испытаний

def _row_trial(r):
    d = dict(zip(TRIAL_COLS, r))
    d["criterion"] = _j(d["criterion"]) or {}
    d["numbers"] = _j(d["numbers"])
    d["var_ver"] = _int(d["var_ver"], 0)
    d["start_ts"] = _int(d["start_ts"], 0)
    d["deadline_ts"] = _int(d["deadline_ts"], 0)
    d["cut_ts"] = _int(d["cut_ts"])
    d["closed_ts"] = _int(d["closed_ts"])
    return d


def trials(conn, state=None):
    """Строки `exit_trials` (criterion и numbers разобраны) по trial_id; state — open | closed | None (все)."""
    try:
        q, a = "SELECT %s FROM exit_trials" % ", ".join(TRIAL_COLS), []
        if state:
            q += " WHERE state=?"
            a.append(state)
        return [_row_trial(r) for r in conn.execute(q + " ORDER BY trial_id", a).fetchall()]
    except Exception:
        return []


def get(conn, trial_id):
    try:
        r = conn.execute("SELECT %s FROM exit_trials WHERE trial_id=?" % ", ".join(TRIAL_COLS), (str(trial_id),)).fetchone()
    except Exception:
        return None
    return _row_trial(r) if r else None


def open_count(conn):
    """Число открытых испытаний (занимают место в `trial.max_parallel`)."""
    try:
        return int(conn.execute("SELECT COUNT(*) FROM exit_trials WHERE state=?", (OPEN,)).fetchone()[0])
    except Exception:
        return 0


def active_version(conn):
    """Действующая версия политики (последняя по `seq` в `exit_versions`) или None."""
    try:
        row = conn.execute("SELECT ver FROM exit_versions ORDER BY seq DESC LIMIT 1").fetchone()
    except Exception:
        return None
    return str(row[0]) if row and row[0] is not None else None


def version_row(conn, ver=None):
    """Запись `exit_versions` по имени версии (None — действующая) -> {"seq", "ver", "parent", "policy", "policy_h", "floor_h"} или None."""
    try:
        if ver is None:
            r = conn.execute("SELECT seq, ver, parent, policy, policy_h, floor_h FROM exit_versions ORDER BY seq DESC LIMIT 1").fetchone()
        else:
            r = conn.execute("SELECT seq, ver, parent, policy, policy_h, floor_h FROM exit_versions WHERE ver=?", (str(ver),)).fetchone()
    except Exception:
        return None
    if not r:
        return None
    return {"seq": int(r[0]), "ver": str(r[1]), "parent": r[2], "policy": _j(r[3]) or {}, "policy_h": r[4], "floor_h": r[5]}


# ---------------------------------------------------------------- критерий и регистрация

def _floor(root=None, floor_policy=None):
    """Пол защиты: явная политика, иначе `exitpol.floor(exec_limits.auto_protect)`; не читается — пустой пол."""
    if isinstance(floor_policy, dict):
        return floor_policy
    fl = EC.current_floor(root)
    return fl if isinstance(fl, dict) else XP.floor({})


def _source_policies(conn, cfg, root=None):
    """Политики источника путей и их версии (Requirement 6.1): последняя версия в `run_paths`, иначе — в реестре меток."""
    out = []
    for pid in ((cfg or {}).get("paths") or {}).get("policies") or []:
        ver = None
        try:
            row = conn.execute("SELECT MAX(policy_ver) FROM run_paths WHERE policy_id=?", (str(pid),)).fetchone()
            if row and row[0] is not None:
                ver = int(row[0])
        except Exception:
            ver = None
        if ver is None:
            try:
                from tb.labels import policies as LP
                vers = [int(p["ver"]) for p in LP.load(root) if p.get("id") == pid]
                ver = max(vers) if vers else None
            except (ValueError, OSError, KeyError, TypeError):
                ver = None
        out.append({"id": str(pid), "ver": ver})
    return out


def _criterion(var_id, var_ver, policy_h, maker, base, cfg, start, floor_h, lc, cost_h, sim_ver, src_policies):
    """Критерий испытания (Requirement 6.1); записывается один раз и не меняется."""
    t = (cfg or {}).get("trial") or {}
    b = (cfg or {}).get("brain") or {}
    tl = (cfg or {}).get("tail") or {}
    rb = (cfg or {}).get("robust") or {}
    unit = (cfg or {}).get("unit") or {}
    maxp = int(t.get("max_parallel") or 1)
    alpha = float(t.get("alpha"))
    return {
        "v": CRIT_VER,
        "var": {"id": str(var_id), "ver": int(var_ver), "policy_h": policy_h, "maker": bool(maker)},
        "base": {"ver": str(base["ver"]), "policy_h": base.get("policy_h")},
        "src": {"policies": list(src_policies), "cost_h": cost_h, "sim_ver": sim_ver},
        "block_h": int(unit.get("block_h")),
        "n_target": int(t.get("n_target")),
        "alpha": alpha, "max_parallel": maxp, "alpha_adj": alpha / maxp,
        "min_effect_r": float(t.get("min_effect_r")),
        "tail": {"mfe_r": float(tl.get("mfe_r")), "keep": float(tl.get("keep")), "min_n": int(tl.get("min_n"))},
        "robust": {"tol_r": float(rb.get("tol_r"))},
        "brain": {"n": int(b.get("n")), "min_n": int(b.get("min_n")), "tol_r": float(b.get("tol_r")), "mode": str(b.get("mode"))},
        "start_ts": int(start),
        "deadline_ts": int(start) + int(t.get("max_days")) * DAY,
        "cut": {"cluster_close_max_h": int(lc["clusters"]["cluster_close_max_h"]), "observe_s": int(lc["intervals"]["observe_s"])},
        "cfg_h": EC.config_hash(cfg)[:16],
        "floor_h": floor_h,
    }


def _cand_ids(cand):
    """(var_id, var_ver) кандидата: принимаются ключи `var_id/var_ver` и `id/ver` (форма меню)."""
    vid = cand.get("var_id", cand.get("id")) if isinstance(cand, dict) else None
    vver = cand.get("var_ver", cand.get("ver")) if isinstance(cand, dict) else None
    if vid is None or vver is None:
        raise ValueError("E_TRIAL_CAND: нет id/ver варианта")
    return str(vid), int(vver)


def _next_id(conn):
    """Следующий `trial_id` вида `XT-0001` (строки не удаляются триггером)."""
    n = 0
    try:
        n = int(conn.execute("SELECT COUNT(*) FROM exit_trials").fetchone()[0])
    except Exception:
        n = 0
    while True:
        n += 1
        tid = "XT-%04d" % n
        try:
            if conn.execute("SELECT 1 FROM exit_trials WHERE trial_id=?", (tid,)).fetchone() is None:
                return tid
        except Exception:
            return tid


def register(conn, cand, base_ver, cfg, now, root=None, floor_policy=None):
    """Регистрация испытания варианта с критерием до старта (Requirement 6.1; свойство 15) -> trial_id.

    `cand` — запись варианта (`id`/`ver` или `var_id`/`var_ver`, `policy`, необязательный `policy_h`, `family`); политика
    нормализуется полом (`exitpol.normalize`) и проверяется схемой и «не слабее пола». `base_ver` — база испытания (None —
    действующая версия). `start_ts` — следующая граница часа: в испытание идут только будущие пути. Открытое испытание того
    же варианта повторно не создаётся: возвращается его `trial_id`.
    """
    now = int(now)
    if not EC.is_enabled(cfg):
        raise ValueError("E_CONF:disabled")
    var_id, var_ver = _cand_ids(cand)
    try:
        row = conn.execute("SELECT trial_id FROM exit_trials WHERE var_id=? AND var_ver=? AND state=?", (var_id, var_ver, OPEN)).fetchone()
    except Exception:
        row = None
    if row:
        return str(row[0])
    pol = cand.get("policy") if isinstance(cand, dict) else None
    if not isinstance(pol, dict) or not pol:
        raise ValueError("E_TRIAL_POLICY:%s" % var_id)
    floor = _floor(root, floor_policy)
    npol = XP.normalize(pol, floor)
    errs = XP.validate(npol) + XP.weaker_than(npol, floor)
    if errs:
        raise ValueError(",".join(errs))
    policy_h = cand.get("policy_h") or XP.policy_hash(npol)
    base = version_row(conn, base_ver)
    if base is None:
        raise ValueError("E_TRIAL_BASE:%s" % ("active" if base_ver is None else base_ver))
    lc, lerr = LC.load(root)
    if lerr:
        raise ValueError(",".join(lerr))
    inp = EE.market_inputs(root)
    start = CL.hour_ceil(now)
    crit = _criterion(var_id, var_ver, policy_h, has_maker(npol), base, cfg, start, XP.policy_hash(inp["floor"]), lc,
                      inp["cost_h"], XS.SIM_VER, _source_policies(conn, cfg, root))
    ch = crit_hash(crit)
    tid = _next_id(conn)
    variant = {"id": var_id, "ver": int(var_ver), "family": cand.get("family") or "combo", "policy": npol, "policy_h": policy_h,
               "menu_ver": int((cfg or {}).get("menu_ver") or 0), "status": "trial", "created_ts": now}
    trial = {"trial_id": tid, "var_id": var_id, "var_ver": int(var_ver), "base_ver": base["ver"], "criterion": crit, "criterion_h": ch,
             "state": OPEN, "verdict": None, "numbers": None, "start_ts": int(start), "deadline_ts": int(crit["deadline_ts"]),
             "cut_ts": None, "closed_ts": None}
    with LST.tx(conn):
        store2.insert_rows(conn, "exit_variants", [variant], ignore_dup=True)
        store2.insert_rows(conn, "exit_trials", [trial])
        store2.log_event(conn, STEP, "TRIAL", "code", ref=tid,
                         body={"var": var_id, "ver": int(var_ver), "base": base["ver"], "start_ts": int(start),
                               "deadline_ts": int(crit["deadline_ts"]), "criterion_h": ch, "n_target": crit["n_target"],
                               "block_h": crit["block_h"], "brain_n": crit["brain"]["n"]}, now=now)
    return tid


# ---------------------------------------------------------------- выборка испытания

def _cut_cfg(crit):
    """Параметры `cut_ts` из критерия в форме конфига `clusters` (после старта конфиг на испытание не влияет)."""
    c = crit.get("cut") or {}
    return {"clusters": {"cluster_close_max_h": int(c["cluster_close_max_h"])}, "intervals": {"observe_s": int(c["observe_s"])}}


def _read_evals(conn, crit, src, a, b):
    """Строки `exit_evals` источника `src` в окне t0 ∈ [a, b) при записанных `cost_h`/`sim_ver` критерия."""
    var, base = crit.get("var") or {}, crit.get("base") or {}
    src_cfg = crit.get("src") or {}
    try:
        return conn.execute("SELECT %s FROM exit_evals WHERE src=? AND t0>=? AND t0<? AND cost_h=? AND sim_ver=? AND var_id IN (?,?)"
                            % ", ".join(EVAL_COLS),
                            (src, int(a), int(b), src_cfg.get("cost_h"), src_cfg.get("sim_ver"), str(var.get("id")), str(base.get("ver")))).fetchall()
    except Exception:
        return []


def _index(rows):
    """Строки оценок -> {(var_id, var_ver, src, key): {(ord, px_mode): строка}}."""
    out = {}
    for r in rows:
        d = dict(zip(EVAL_COLS, r))
        k = (str(d["var_id"]), int(d["var_ver"]), str(d["src"]), str(d["key"]))
        out.setdefault(k, {})[(str(d["ord"]), str(d["px_mode"]))] = d
    return out


def _trade_modes(conn, keys):
    """Режим торговли сделки по `trade_paths` (Requirement 6.5: сделки текущего режима). Нет таблицы/строки — режим неизвестен."""
    out = {}
    if not keys:
        return out
    items = sorted(keys)
    try:
        for i in range(0, len(items), 500):
            chunk = items[i:i + 500]
            for jid, mode in conn.execute("SELECT journal_id, mode FROM trade_paths WHERE journal_id IN (%s)" % ",".join("?" * len(chunk)),
                                          chunk).fetchall():
                out[str(jid)] = str(mode)
    except Exception:
        return {}
    return out


def _pair_map(evals, var, base, mode=None, modes=None):
    """Пары «вариант — база» по (src, key): строки `d`, `d_lo`, `d_taker`, `cut` (Requirement 6.2–6.4, ревизия 2026-11-04).

    `d` — разность `S/fee`; `d_lo` — минимум разности в порядках `S` и `T`, обе стороны пары в одном порядке; отсутствующая
    `T`-строка стороны заменяется `S` (тогда вклад порядка `T` равен `d`). `d_taker` — разность `S` при цене taker у maker-частей
    (`px_mode="taker"`; у базы без maker-частей — её обычная строка).
    """
    want_var = (str(var.get("id")), int(var.get("ver") or 0))
    want_base = (str(base.get("ver")), int(base.get("ver_num") or 0))
    keys = set()
    for (vid, vver, src, key) in evals:
        if src == "trade" and modes and mode and modes.get(key) not in (None, mode):
            continue
        keys.add((src, key))
    out = []
    for src, key in sorted(keys):
        v = evals.get((want_var[0], want_var[1], src, key))
        b = evals.get((want_base[0], want_base[1], src, key))
        if not v or not b:
            continue
        vs, bs = v.get(("S", "fee")), b.get(("S", "fee"))
        if not vs or not bs or vs.get("r_net") is None or bs.get("r_net") is None:
            continue
        vt = v.get(("T", "fee")) or vs
        bt = b.get(("T", "fee")) or bs
        if vt.get("r_net") is None or bt.get("r_net") is None:
            continue
        d = float(vs["r_net"]) - float(bs["r_net"])
        d_lo = min(d, float(vt["r_net"]) - float(bt["r_net"]))
        vtak = v.get(("S", "taker"))
        btak = b.get(("S", "taker"))
        dtak = None
        if vtak is not None and vtak.get("r_net") is not None:
            other = btak if btak is not None and btak.get("r_net") is not None else bs
            dtak = float(vtak["r_net"]) - float(other["r_net"])
        t0 = _int(vs.get("t0"), _int(bs.get("t0"), 0))
        lab = max(_int(vs.get("labeled_ts"), t0), _int(bs.get("labeled_ts"), t0))
        mfe = _num(vs.get("path_mfe_r"))
        if mfe is None:
            mfe = _num(bs.get("path_mfe_r")) or 0.0
        rec = {"src": src, "key": str(key), "t0": int(t0), "block": str(vs.get("block") or bs.get("block") or ""),
               "labeled_ts": int(lab), "d": d, "d_lo": float(d_lo), "d_taker": dtak,
               "r_var": float(vs["r_net"]), "r_base": float(bs["r_net"]),
               "gross_var": _num(vs.get("r_gross")), "gross_base": _num(bs.get("r_gross")),
               "fees_var": _num(vs.get("fees_r")) or 0.0, "fees_base": _num(bs.get("fees_r")) or 0.0,
               "mfe_var": _num(vs.get("mfe_r")) or 0.0, "mfe_base": _num(bs.get("mfe_r")) or 0.0,
               "path_mfe_r": float(mfe), "cut": vs.get("exit_reason") == "end" or bs.get("exit_reason") == "end",
               "amb": bool(vs.get("amb") or bs.get("amb"))}
        if src == "trade":
            rec["entry_ts"] = int(t0)
            rec["d_b"] = d
        out.append(rec)
    return out


def _sample(rows, start_ts, n_target):
    """Первые `n_target` блоков выборки: -> (строки выбранных блоков, число блоков). Только `t0 ≥ start_ts` (свойство 17)."""
    rows = [r for r in (rows or []) if isinstance(r, dict) and _num(r.get("d")) is not None and _int(r.get("t0"), -1) >= int(start_ts)]
    blocks = sorted(set(str(r.get("block")) for r in rows), key=_block_key)
    keep = set(blocks[:int(n_target)])
    return [r for r in rows if str(r.get("block")) in keep], len(keep)


def _brain_sample(rows, start_ts, n):
    """Первые `n` сделок мозга с `entry_ts ≥ start_ts` по (entry_ts, key) (Requirement 6.5)."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        ts = _int(r.get("entry_ts", r.get("t0")), -1)
        d = _num(r.get("d_b", r.get("d")))
        if ts >= int(start_ts) and d is not None:
            out.append(dict(r, d_b=d, entry_ts=ts))
    out.sort(key=lambda r: (r["entry_ts"], str(r.get("key"))))
    return out[:int(n)] if n else out


def _tail(rows, mfe_r):
    return [r for r in rows if (_num(r.get("path_mfe_r")) or 0.0) >= float(mfe_r)]


def _block_means(rows, field):
    """Среднее поля по блокам в хронологическом порядке блоков; блоки без значений поля пропускаются."""
    by = {}
    for r in rows:
        v = _num(r.get(field))
        if v is not None:
            by.setdefault(str(r.get("block")), []).append(v)
    return [_mean(by[b]) for b in sorted(by, key=_block_key)]


def _ratio(a, b):
    a, b = _num(a), _num(b)
    return (a / b) if (a is not None and b not in (None, 0.0)) else None


def _lower90(vals):
    """Нижняя граница одностороннего 90% интервала среднего (t-квантиль, df = n − 1)."""
    n = len(vals)
    m = _mean(vals)
    if m is None or n < 2:
        return m
    s = ST.sd(vals)
    if s is None or s == 0.0:
        return m
    return m - ES.t_quantile(0.90, n - 1) * s / math.sqrt(n)


# ---------------------------------------------------------------- чистый вердикт

def verdict(trial, rows, brain_rows=None):
    """Чистый вердикт по критерию и выборке (Requirement 6.2–6.6; свойства 16, 17) -> {"verdict", "code", "conds", "numbers"}.

    `verdict` — `promote` | `reject` | None (данных не хватает: причина в `numbers.reason`). Берутся только пути с
    `t0 ≥ start_ts`; первые `n_target` блоков задают выборку `MEAN`, `ROBUST_COST`, `TAIL`, `ROBUST`; `BRAIN` — первые
    `brain.n` сделок с `entry_ts ≥ start_ts`. Порог `BRAIN` один (−`brain.tol_r`) при любом n от `brain.min_n`.
    """
    crit = _crit(trial)
    start_ts = _int(crit.get("start_ts"), 0)
    n_t = _int(crit.get("n_target"), 0) or 0
    tail_cfg = crit.get("tail") or {}
    rob = crit.get("robust") or {}
    brain = crit.get("brain") or {}
    alpha = _num(crit.get("alpha_adj"))
    min_eff = _num(crit.get("min_effect_r"))
    if n_t <= 0 or alpha is None or min_eff is None or not tail_cfg or not brain:
        raise ValueError("E_TRIAL_CRITERION: нет n_target/alpha_adj/min_effect_r/tail/brain")
    sample, n_blocks = _sample(rows, start_ts, n_t)
    bs = _brain_sample(brain_rows, start_ts, _int(brain.get("n"), 0))
    nums = {"n_blocks": n_blocks, "n_target": n_t, "n_paths": len(sample), "alpha_adj": alpha, "reason": None, "brain_n": len(bs)}
    conds = dict((c, False) for c in CONDS)
    res = {"verdict": None, "code": None, "conds": conds, "numbers": nums}
    if n_blocks < n_t:
        nums["reason"], nums["conds"] = R_N, conds
        return res
    if len(bs) < (_int(brain.get("min_n"), 0) or 0):
        nums["reason"], nums["conds"] = R_BRAIN_N, conds
        return res
    mfe_thr = float(tail_cfg.get("mfe_r"))
    tail = _tail(sample, mfe_thr)
    nums["tail_n"] = len(tail)
    if len(tail) < (_int(tail_cfg.get("min_n"), 0) or 0):
        nums["reason"], nums["conds"] = R_N, conds
        return res
    d_blocks = _block_means(sample, "d")
    lo_blocks = _block_means(sample, "d_lo")
    st = ST.paired(d_blocks, SIDE) if len(d_blocks) >= 2 else {"n": len(d_blocks), "mean": _mean(d_blocks), "sd": None, "se": None,
                                                               "t": None, "p": None}
    mean_d, p = st["mean"], st["p"]
    conds["MEAN"] = bool(p is not None and p < alpha and mean_d is not None and mean_d >= min_eff)
    maker = bool((crit.get("var") or {}).get("maker"))
    if not maker:
        conds["ROBUST_COST"] = True
        mean_taker, p_taker, n_taker = mean_d, p, len(d_blocks)
    else:
        tk = _block_means(sample, "d_taker")
        st2 = ST.paired(tk, SIDE) if len(tk) >= 2 else {"mean": _mean(tk), "p": None}
        mean_taker, p_taker, n_taker = st2["mean"], st2["p"], len(tk)
        conds["ROBUST_COST"] = bool(p_taker is not None and p_taker < alpha and mean_taker is not None and mean_taker >= min_eff)
    sum_var = sum(float(r.get("r_var") or 0.0) for r in tail)
    sum_base = sum(float(r.get("r_base") or 0.0) for r in tail)
    conds["TAIL"] = bool(sum_var >= float(tail_cfg.get("keep")) * sum_base)
    mean_lo = _mean(lo_blocks)
    tol_r = float(rob.get("tol_r"))
    conds["ROBUST"] = bool(mean_lo is not None and mean_lo >= -tol_r)
    db_vals = [r["d_b"] for r in bs]
    brain_mean = _mean(db_vals)
    brain_lb = _lower90(db_vals)
    brain_tol = float(brain.get("tol_r"))
    conds["BRAIN"] = bool(brain_lb is not None and brain_lb >= -brain_tol)
    promote = all(conds.values())
    nums.update(n=st.get("n"), mean_d=_r(mean_d), sd=_r(st.get("sd")), se=_r(st.get("se")), t=_r(st.get("t")), p=_r(p, 10),
                mean_lo=_r(mean_lo), robust_tol_r=tol_r, mean_taker=_r(mean_taker), p_taker=_r(p_taker, 10), n_taker=n_taker,
                tail_var=_r(sum_var), tail_base=_r(sum_base), tail_ratio=_r(_ratio(sum_var, sum_base), 4), tail_keep=float(tail_cfg.get("keep")),
                tail_mfe_r=mfe_thr, brain_n=len(bs), brain_n_target=_int(brain.get("n"), 0), brain_mean=_r(brain_mean), brain_sd=_r(ST.sd(db_vals)),
                brain_lb=_r(brain_lb), brain_tol_r=brain_tol, brain_min_n=_int(brain.get("min_n"), 0),
                cut_share=_r(sum(1 for r in sample if r.get("cut")) / float(len(sample)) if sample else None, 4),
                amb_share=_r(sum(1 for r in sample if r.get("amb")) / float(len(sample)) if sample else None, 4),
                cap_var=_r(_ratio(sum(r["r_var"] for r in sample if r["mfe_var"] > 0),
                                 sum(r["mfe_var"] for r in sample if r["mfe_var"] > 0)), 4),
                cap_base=_r(_ratio(sum(r["r_base"] for r in sample if r["mfe_base"] > 0),
                                  sum(r["mfe_base"] for r in sample if r["mfe_base"] > 0)), 4),
                fees_share_var=_r(_ratio(sum(r["fees_var"] for r in sample), abs(sum(r.get("gross_var") or 0.0 for r in sample))), 4),
                fees_share_base=_r(_ratio(sum(r["fees_base"] for r in sample), abs(sum(r.get("gross_base") or 0.0 for r in sample))), 4),
                a=start_ts, deadline_ts=_int(crit.get("deadline_ts")), conds=conds)
    res["verdict"] = PROMOTE if promote else REJECT
    res["code"] = CODE_READY if promote else CODE_REJECT
    return res


# ---------------------------------------------------------------- окно и шаг

def pairs(conn, crit, a, b):
    """Пары «вариант — база» из `exit_evals` в окне t0 ∈ [a, b) -> (пути прогонов, сделки мозга)."""
    run = _index(_read_evals(conn, crit, "run", a, b))
    trade = _index(_read_evals(conn, crit, "trade", a, b))
    var, base = crit.get("var") or {}, crit.get("base") or {}
    base_ver = str(base.get("ver") or "")
    brow = version_row(conn, base_ver)
    base = dict(base, ver_num=EE.version_var(base_ver, brow["seq"] if brow else 0)[1])
    mode = (crit.get("brain") or {}).get("mode")
    tj = set(k[3] for k in trade)
    modes = _trade_modes(conn, tj) if mode else {}
    return (_pair_map(run, var, base),
            [r for r in _pair_map(trade, var, base, mode=mode, modes=modes)])


def find_window(conn, trial, now):
    """Выборка вердикта (Requirement 6.2, 6.6) -> ("ready" | "deadline" | "wait", окно или None).

    `ready` — минимальная граница часа `b`, где набраны `n_target` блоков, `brain.n` сделок и `tail.min_n` путей хвоста, а
    `cut_ts(b) ≤ now`; `deadline` — наступил `cut_ts(deadline_ts)`, набора нет; окно:
    `{"a", "b", "cut_ts", "rows", "brain"}`. Пути с `labeled_ts > cut_ts(b)` не входят (Requirement 4.3).
    """
    crit = _crit(trial)
    start, deadline = _int(crit.get("start_ts"), 0), _int(crit.get("deadline_ts"), 0)
    ccfg = _cut_cfg(crit)
    hi = min(CL.last_b(now, ccfg), deadline)
    if hi <= start:
        return "wait", None
    run_rows, brain_rows = pairs(conn, crit, start, deadline)
    run_rows.sort(key=lambda r: (r["t0"], r.get("key")))
    brain_rows.sort(key=lambda r: (r["entry_ts"], r.get("key")))
    n_t = _int(crit.get("n_target"), 0) or 0
    need_brain = _int((crit.get("brain") or {}).get("n"), 0) or 0
    need_tail = _int((crit.get("tail") or {}).get("min_n"), 0) or 0
    mfe_thr = float((crit.get("tail") or {}).get("mfe_r") or 0.0)
    for b in range(int(start) + HOUR, int(hi) + 1, HOUR):
        cut = CL.cut_ts(b, ccfg)
        rs = [r for r in run_rows if r["t0"] < b and r["labeled_ts"] <= cut]
        sel, _n = _sample(rs, start, n_t)
        if len(set(r["block"] for r in sel)) < n_t:
            continue
        bs = [r for r in brain_rows if r["entry_ts"] < b]
        if len(bs) < need_brain:
            continue
        if len(_tail(sel, mfe_thr)) < need_tail:
            continue
        return "ready", {"a": int(start), "b": int(b), "cut_ts": int(cut), "rows": rs, "brain": bs}
    if hi >= deadline and CL.cut_ts(deadline, ccfg) <= int(now):
        cut = CL.cut_ts(deadline, ccfg)
        rs = [r for r in run_rows if r["t0"] < deadline and r["labeled_ts"] <= cut]
        bs = [r for r in brain_rows if r["entry_ts"] < deadline]
        return "deadline", {"a": int(start), "b": int(deadline), "cut_ts": int(cut), "rows": rs, "brain": bs}
    return "wait", None


def _change_code(conn, crit, inp):
    """Причина закрытия по смене источника во время испытания (Requirement 6.8) или None."""
    src = crit.get("src") or {}
    if src.get("cost_h") is not None and inp.get("cost_h") is not None and src["cost_h"] != inp["cost_h"]:
        return COST_CHANGED
    if src.get("sim_ver") is not None and XS.SIM_VER != src["sim_ver"]:
        return SIM_CHANGED
    fl = crit.get("floor_h")
    if fl and XP.policy_hash(inp.get("floor") or XP.floor({})) != fl:
        return FLOOR_CHANGED
    base_ver = (crit.get("base") or {}).get("ver")
    if active_version(conn) != base_ver:
        return BASE_CHANGED
    return None


def _block_row(conn, var_id, var_ver, code, now, cfg):
    """Чёрный список отклонённого испытанием варианта на `cooldown_days` (Requirement 5.8)."""
    try:
        days = int((cfg or {}).get("cooldown_days") or 0)
    except (TypeError, ValueError):
        days = 0
    try:
        conn.execute("INSERT INTO exit_blacklist(var_id, var_ver, until_ts, reason, ts) VALUES(?,?,?,?,?)",
                     (str(var_id), int(var_ver), int(now) + days * DAY, str(code), int(now)))
    except Exception:
        pass


def close(conn, trial, verdict_, code, numbers, now, cfg, cut=None, reason=None):
    """Закрыть открытое испытание одной транзакцией -> {"trial_id", "var_id", "var_ver", "verdict", "code", "reason"} или None.

    `state=closed`, `verdict`, `numbers` (с кодом и причиной), `cut_ts`, `closed_ts`; `reject` добавляет вариант в чёрный список
    на `cooldown_days`; событие `learn_events step="exits_trial"`. Закрытое испытание не трогается (идемпотентность).
    """
    nums = dict(numbers or {})
    nums["code"] = code
    if reason:
        nums["reason"] = reason
    nums = dict((k, v) for k, v in nums.items() if v is not None or k in ("reason", "cut_share", "amb_share", "cap_var", "cap_base"))
    with LST.tx(conn):
        cur = get(conn, trial["trial_id"])
        if cur is None or cur["state"] != OPEN:
            return None
        store2.update_row(conn, "exit_trials", cur["trial_id"],
                          {"state": CLOSED, "verdict": verdict_, "numbers": nums,
                           "cut_ts": None if cut is None else int(cut), "closed_ts": int(now)})
        if verdict_ == REJECT:
            _block_row(conn, cur["var_id"], cur["var_ver"], code, now, cfg)
        store2.log_event(conn, STEP, code, "code", ref=cur["trial_id"],
                         body={"var": cur["var_id"], "ver": cur["var_ver"], "verdict": verdict_, "reason": reason,
                               "n_blocks": nums.get("n_blocks"), "p": nums.get("p"), "mean_d": nums.get("mean_d"),
                               "cut_ts": None if cut is None else int(cut)}, now=now)
    return {"trial_id": cur["trial_id"], "var_id": cur["var_id"], "var_ver": cur["var_ver"], "verdict": verdict_, "code": code,
            "reason": reason}


def verdicts(conn, cfg, now, root=None):
    """Часовой шаг вердиктов (Requirement 6.6–6.8) -> [закрытия].

    Ждёт пустой очереди `eval_backlog` (Requirement 4.5) и выключенного конфига не трогает. Сначала закрывает открытые
    испытания по смене источника (`COST_CHANGED`, `SIM_CHANGED`, `FLOOR_CHANGED`, `BASE_CHANGED`), затем считает вердикт не
    раньше `cut_ts` выборки; при нехватке данных к дедлайну — `inconclusive` (`NO_VERDICT`) с причиной `N` или `BRAIN_N`.
    """
    now = int(now)
    out = []
    if not EC.is_enabled(cfg):
        return out
    if EE.backlog(conn) > 0:
        return out
    inp = EE.market_inputs(root)
    for t in trials(conn, OPEN):
        crit = _crit(t)
        code = _change_code(conn, crit, inp)
        if code:
            res = close(conn, t, INCONCLUSIVE, code, {}, now, cfg)
            if res:
                out.append(res)
            continue
        state, win = find_window(conn, t, now)
        if state == "wait" or win is None:
            continue
        v = verdict(t, win["rows"], win["brain"])
        nums = dict(v["numbers"])
        nums.update(b=int(win["b"]), cut_ts=int(win["cut_ts"]))
        if v["verdict"] is None:
            res = close(conn, t, INCONCLUSIVE, CODE_NO_VERDICT, nums, now, cfg, cut=win["cut_ts"],
                        reason=nums.get("reason") or R_N)
        else:
            res = close(conn, t, v["verdict"], v["code"], nums, now, cfg, cut=win["cut_ts"])
        if res:
            out.append(res)
    return out
