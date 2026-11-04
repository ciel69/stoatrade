"""Поиск вариантов выхода `tb.learnloop.exits_search` (R4, задача 11; Requirement 5, 6.2–6.3; design.md «Поиск: max-T», «Единица наблюдения»).

Шаг `run(conn, root, cfg, now)` раз в `search.every_days` суток UTC и только при пустой очереди `eval_backlog` (`BACKLOG` иначе — Requirement 4.5,
5.2). Что делает:

  * берёт окно поиска `[a, b)` на `search.window_days` суток с `cut_ts = b + cluster_close_max_h + observe_s` (`exits_eval.window`, механика
    `learnloop.clusters`) и читает оценки `exit_evals` только по путям прогонов `src="run"` с `t0 ∈ [a, b)` и `labeled_ts ≤ cut_ts` при текущих
    `cost_h` и `sim_ver` (Requirement 4.3, свойство 17: будущее в расчёт не попадает);
  * считает разность `d = r_net(вариант) − r_net(база)` на каждом пути (`база` — действующая версия: последняя запись `exit_versions`), значение
    блока (`unit.block_h` часов, сторона) — среднее `d` его путей, матрицу «блоки × варианты» меню `config/exits.json` (Requirement 4.4, 5.1);
  * поправка на множественность — max-T по знаковым перестановкам блоков (Westfall–Young): знак один на блок для всех вариантов, t — статистика
    среднего `d` по блокам, скорректированное p — доля перестановок с максимумом t не меньше наблюдаемого; зерно — хеш выборки (Requirement 5.3,
    `design.md` «Поиск: max-T», B = `search.perm_b`);
  * кандидат проходит, только если одновременно: скорректированное p < `search.alpha`; среднее `d` ≥ `search.min_effect_r`; блоков ≥
    `search.min_clusters`; выполнено `ROBUST_COST` (Requirement 6.2: у варианта с maker-частями та же проверка по оценкам `px_mode="taker"`) и
    проверка хвоста Requirement 6.3 (пути с MFE пути на горизонте `max_hold_min` ≥ `tail.mfe_r`: сумма `r_net` варианта ≥ `tail.keep` × суммы
    базы при числе путей хвоста ≥ `tail.min_n`). Кандидаты отдаются по убыванию среднего `d` — не больше `trial.max_parallel` в испытание берёт
    `exits_trial.register` (задача 12);
  * варианты чёрного списка `exit_blacklist` (`until_ts > now`) в поиск не идут (Requirement 5.8); вариант, у которого на всех путях окна
    решения те же, что у базы, — `NOOP` и в поиск не идёт (Requirement 5, Edge Cases).

Таблицы `near_tp`: квантили MFE пути по (вид сигнала, горизонт) считаются по путям СТРОГО РАНЬШЕ окна поиска (`t0 < a`, `labeled_ts ≤ cut_ts`) и
подставляются в `near_tp.table` политики кандидата, чтобы регистрация испытания записала их в параметры варианта (Requirement 5.5, свойство 17).
Уровень — `max(квантиль q, near_tp.min_r)`, не выше верхней границы схемы (20R); нет путей вида сигнала — строки таблицы нет и компонент не
действует (так же `exitpol._near_level`). Оценки `exit_evals` вариантов меню считает `exits_eval` по политике из конфига, то есть без таблицы:
`near_tp`-варианты приходят к поиску с неактивным компонентом и получают `NOOP`. Согласование оценки с таблицей — вопрос спеки (см. отчёт).

Что пишется в базу (Requirement 5.6): строка `exit_search` (`a`, `b`, `cut_ts`, `sample_h`, `cfg_h`, `menu_ver`, `base_ver`, `block_h`, `code`,
`results`) и событие `learn_events step="exits_search"` — при любом исходе поиска (`OK`, `LOW_N`, `NO_CANDIDATE`, `NOOP`). `BACKLOG`, `NOT_DUE`,
`DISABLED`, `NO_WINDOW` — ожидание или выключенный цикл: строки нет, результата поиска не существует. В `results` пишутся варианты меню (число
блоков и путей, среднее, sd, t, скорректированное p, все условия кандидата, числа хвоста, среднее и p по цене taker), доли пар с флагом `cut` и
неоднозначной свечой (`amb`), доля совпадения `X-0001` с `policy_runs` на путях окна (Requirement 14.1, справочно), чёрный список и таблицы
`near_tp`.

Воспроизводимость: выборка окна задаётся окном и `cut_ts`; хеш выборки — 16 hex sha256 канонического JSON отсортированных строк
`(t0, key, labeled_ts)` (Requirement 4.3); перестановки берут зерно из хеша выборки, поэтому повтор на той же выборке даёт те же числа (свойство 12).

Слои `tb.learnloop`: модуль не импортирует мозг, шлюз, биржу, исполнитель, управление, CLI, ключи; конфиги читает только через загрузчики
(`exits_conf`, `exits_eval.market_inputs` → `exec_cfg` и загрузчики меток), сам ничего не пишет вне базы знаний и не использует сеть.
"""
import hashlib
import math
import random
from operator import mul

from tb.knowledge import store2
from tb.labels import store as LST
from tb.learnloop import exits_conf as EC
from tb.learnloop import exits_eval as EV
from tb.memory import chain as CH
from tb.logic import exitpol as XP
from tb.logic import exitsim as XS

STEP = "exits_search"
DAY = 86400

SRC_RUN = EV.SRC_RUN
ORDER_S = EV.ORDER_S
PX_FEE = EV.PX_FEE
PX_TAKER = EV.PX_TAKER

CODE_OK = "OK"
CODE_LOW_N = "LOW_N"
CODE_NO_CANDIDATE = "NO_CANDIDATE"
CODE_NOOP = "NOOP"
CODE_BACKLOG = "BACKLOG"
CODE_NOT_DUE = "NOT_DUE"
CODE_DISABLED = "DISABLED"
CODE_NO_WINDOW = "NO_WINDOW"
CODES = (CODE_OK, CODE_LOW_N, CODE_NO_CANDIDATE, CODE_NOOP, CODE_BACKLOG, CODE_NOT_DUE, CODE_DISABLED, CODE_NO_WINDOW)

NEAR_TP_MAX_R = 20.0                      # верхняя граница near_tp.level схемы tb.exit_policy/1 (exitpol)
CHUNK = 400                               # размер пачки параметров IN (...): предел SQLite — 999

EVAL_COLS = ("key", "var_id", "var_ver", "ord", "px_mode", "t0", "block", "labeled_ts", "r_net", "exit_reason", "amb", "end_ts", "path_mfe_r")


# ---------------------------------------------------------------- мелочи

def _fin(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _int(v, default=None):
    if isinstance(v, bool) or v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _r(x, nd=6):
    """Округление числа для чисел в результатах; нечисло и бесконечность (нулевой разброс d) — None."""
    v = _num(x)
    return None if v is None else round(v, nd)


def _pair(x):
    return (str(x[0]), int(x[1]))


def _block_key(block):
    """Порядок блоков: номер блока, затем сторона (детерминизм матрицы)."""
    head, _, side = str(block).partition(":")
    try:
        return (int(head), side)
    except ValueError:
        return (0, str(block))


# ---------------------------------------------------------------- выборка окна

def window(cfg, root, now):
    """Окно поиска `{"a", "b", "cut_ts"}` (Requirement 4.3, 5.2) или None, если конфиг R2 не читается."""
    return EV.window(cfg, root, now)


def sample_hash(paths):
    """16 hex sha256 канонического JSON отсортированных строк `(t0, key, labeled_ts)` выборки окна (Requirement 4.3)."""
    items = sorted([[int(p["t0"]), str(k), int(p["labeled_ts"])] for k, p in (paths or {}).items()])
    return hashlib.sha256(CH.canon(items).encode("utf-8")).hexdigest()[:16]


def seed_of(sample_h):
    """Зерно перестановок — хеш выборки (Requirement 5.3)."""
    return int(str(sample_h), 16)


def last_search_ts(conn):
    """Время последнего поиска (max `exit_search.ts`); None — поиска ещё не было."""
    try:
        row = conn.execute("SELECT MAX(ts) FROM exit_search").fetchone()
    except Exception:
        return None
    return None if not row or row[0] is None else int(row[0])


def due(conn, cfg, now):
    """Поиск раз в `search.every_days` суток UTC (Requirement 5.2)."""
    last = last_search_ts(conn)
    if last is None:
        return True
    return int(now) // DAY - last // DAY >= int(cfg["search"]["every_days"])


def backlog(conn):
    """Очередь пересчёта оценок `eval_backlog` (Requirement 4.5, 4.7)."""
    return EV.backlog(conn)


# ---------------------------------------------------------------- чёрный список

def blacklist(conn, now):
    """Действующие записи `exit_blacklist` (Requirement 5.8) -> {(var_id, ver): reason}."""
    try:
        raw = conn.execute("SELECT var_id, var_ver, reason FROM exit_blacklist WHERE until_ts > ?", (int(now),)).fetchall()
    except Exception:
        return {}
    return dict((_pair((r[0], r[1])), str(r[2])) for r in raw)


# ---------------------------------------------------------------- оценки окна

def window_evals(conn, win, cost_h, sim_ver=None):
    """Оценки путей прогонов окна при текущих `cost_h`/`sim_ver`: `t0 ∈ [a, b)`, `labeled_ts ≤ cut_ts` (Requirement 4.3)."""
    sv = XS.SIM_VER if sim_ver is None else sim_ver
    sql = ("SELECT %s FROM exit_evals WHERE src=? AND cost_h=? AND sim_ver=? AND ord=? AND t0>=? AND t0<? AND labeled_ts<=?"
           % ",".join(EVAL_COLS))
    args = (SRC_RUN, str(cost_h), sv, ORDER_S, int(win["a"]), int(win["b"]), int(win["cut_ts"]))
    out = []
    for r in conn.execute(sql, args):
        out.append(dict(zip(EVAL_COLS, r)))
    return out


def group_paths(rows, base):
    """Строки оценок -> {ключ пути: {"t0", "labeled_ts", "block", "base", "fee", "taker"}}.

    `base` — (var_id, var_ver) действующей версии; путь без её оценки в выборку не входит (разность `d` не с чем считать).
    """
    bid = _pair((base["var_id"], base["var_ver"]))
    paths = {}
    for r in rows:
        key = str(r["key"])
        p = paths.get(key)
        if p is None:
            p = paths[key] = {"t0": _int(r["t0"], 0), "labeled_ts": _int(r["labeled_ts"], 0), "block": str(r["block"]),
                              "base": None, "fee": {}, "taker": {}}
        vid = _pair((r["var_id"], r["var_ver"]))
        if str(r["px_mode"]) == PX_TAKER:
            p["taker"][vid] = r
            continue
        p["fee"][vid] = r
        if vid == bid:
            p["base"] = r
    return dict((k, p) for k, p in paths.items() if p["base"] is not None and _num(p["base"]["r_net"]) is not None)


# ---------------------------------------------------------------- варианты

def maker_parts(policy):
    """Есть ли компонент со способом исполнения maker (`partial`, `near_tp`, `decay`, `timeout`) — условие `ROBUST_COST` (Requirement 6.2)."""
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


def base_variant(conn, floor_policy):
    """Действующая версия (последняя по `seq`) — база разности `d`; None, если версий нет (Requirement 5.2, 6.1)."""
    for v in EV.version_variants(conn, floor_policy):
        if v["role"] == "active":
            return v
    return None


def menu_variants(cfg, floor_policy):
    """Варианты меню `config/exits.json` в порядке файла (Requirement 5.1): id, версия, семейство, нормализованная политика и её хеш."""
    try:
        items = EC.variants(cfg, floor_policy)
    except (ValueError, TypeError, KeyError):
        return []
    return [{"var_id": str(it["id"]), "var_ver": int(it["ver"]), "family": it["family"], "policy": it["policy"],
             "policy_h": it["policy_h"]} for it in items]


# ---------------------------------------------------------------- матрица блоков

def variant_stats(paths, variants, tail_mfe, px_mode="fee"):
    """По каждому варианту: значения `d` по блокам, числа путей, `cut`, `amb`, хвоста и признак `NOOP`.

    `d` — разность `r_net` варианта и базы на одном пути (Requirement 5.3); для `px_mode="taker"` берётся оценка варианта по цене taker, база
    остаётся обычной (у базы нет maker-частей, Requirement 6.2). Хвост — свойство пути (Requirement 6.3, свойство 27): `path_mfe_r` базы.
    """
    out = {}
    for var in variants:
        vid = _pair((var["var_id"], var["var_ver"]))
        vals, n_paths, n_cut, n_amb, n_tail = {}, 0, 0, 0, 0
        tail_var, tail_base, noop = 0.0, 0.0, True
        for key in sorted(paths):
            p = paths[key]
            b = p["base"]
            v = p["taker"].get(vid) if px_mode == PX_TAKER else p["fee"].get(vid)
            if v is None:
                continue
            rb, rv = _num(b["r_net"]), _num(v["r_net"])
            if rb is None or rv is None:
                continue
            d = rv - rb
            vals.setdefault(str(b["block"]), []).append(d)
            n_paths += 1
            if v["exit_reason"] == "end" or b["exit_reason"] == "end":
                n_cut += 1
            if int(v["amb"] or 0) or int(b["amb"] or 0):
                n_amb += 1
            if abs(d) > 1e-9 or v["exit_reason"] != b["exit_reason"]:
                noop = False
            mfe = _num(b["path_mfe_r"])
            if mfe is not None and mfe >= tail_mfe:
                n_tail += 1
                tail_var += rv
                tail_base += rb
        out[vid] = {"d": vals, "n_paths": n_paths, "n_cut": n_cut, "n_amb": n_amb, "noop": bool(noop) and n_paths > 0,
                    "n_tail": n_tail, "tail_var": tail_var, "tail_base": tail_base}
    return out


def block_means(stats):
    """Значения блоков варианта: `{block: среднее d}`."""
    return dict((b, sum(xs) / len(xs)) for b, xs in stats["d"].items() if xs)


def matrix(stats_by_var, variants):
    """Матрица `D[блок][вариант]` по вариантам с данными; блоки — объединение блоков (None — оценки нет).

    -> `(blocks, cols, D)`, где `cols` — варианты с хотя бы одной оценкой. Порядок блоков и вариантов детерминирован (порядок меню), поэтому
    матрица и перестановки воспроизводимы.
    """
    cols = [v for v in variants if stats_by_var[_pair((v["var_id"], v["var_ver"]))]["d"]]
    means = dict((_pair((v["var_id"], v["var_ver"])), block_means(stats_by_var[_pair((v["var_id"], v["var_ver"]))])) for v in cols)
    blocks = sorted(set(b for m in means.values() for b in m), key=_block_key)
    D = [[means[_pair((v["var_id"], v["var_ver"]))].get(b) for v in cols] for b in blocks]
    return blocks, cols, D


def complete_rows(D):
    """Строки без пропусков (общие блоки всех вариантов) — их видят и t-статистики, и перестановки."""
    if not D:
        return []
    k = len(D[0])
    return [r for r in D if len(r) == k and all(_fin(x) for x in r)]


# ---------------------------------------------------------------- max-T

def _t(s, sq, n):
    """t-статистика среднего по сумме `s`, сумме квадратов `sq` и числу значений `n` (знаки квадратов не меняют)."""
    if n < 2:
        return None
    mean = s / n
    var = (sq - s * s / n) / (n - 1)
    if var <= 0.0:
        return 0.0 if mean == 0.0 else math.copysign(math.inf, mean)
    return mean / math.sqrt(var / n)


def t_values(values):
    """Наблюдаемые t по значениям (без перестановок): `{"n", "mean", "sd", "t"}`; меньше двух значений — t None."""
    xs = [float(x) for x in values if _fin(x)]
    n = len(xs)
    if not n:
        return {"n": 0, "mean": None, "sd": None, "t": None}
    mean = sum(xs) / n
    if n < 2:
        return {"n": n, "mean": mean, "sd": None, "t": None}
    sd = math.sqrt(max(0.0, sum((x - mean) ** 2 for x in xs) / (n - 1)))
    return {"n": n, "mean": mean, "sd": sd, "t": _t(sum(xs), sum(x * x for x in xs), n)}


def maxt(D, B, seed):
    """Скорректированные max-T p-значения по столбцам матрицы `D` (Requirement 5.3; design.md «Поиск: max-T»).

    `D[c][k]` — значение блока `c` варианта `k`. Наблюдаемое `t_k` — t-статистика среднего столбца. Перестановка — знаки блоков
    `ε_c ∈ {±1}`, одни для всех столбцов (сохраняет корреляцию вариантов); `T_b = max_k t_k(ε)`; `p_k = (1 + #{b: T_b ≥ t_k}) / (1 + B)`.
    Строки с пропуском (`None`) в любом столбце отбрасываются целиком: у всех вариантов одна и та же выборка блоков. Меньше двух полных строк
    или пустая матрица -> `[None]`. Генератор — `random.Random(seed)`; сумма квадратов при смене знаков не меняется, поэтому перестановка стоит
    одного скалярного произведения на столбец.

    >>> maxt([[1.0, -0.5], [0.8, 0.1], [1.2, -0.3]], 200, 7)[0] < maxt([[1.0, -0.5], [0.8, 0.1], [1.2, -0.3]], 200, 7)[1]
    True
    """
    k = len(D[0]) if D else 0
    rows = complete_rows(D)
    n = len(rows)
    if k == 0 or n < 2:
        return [None] * k
    cols = [[r[j] for r in rows] for j in range(k)]
    sqs = [sum(x * x for x in c) for c in cols]
    obs = [_t(sum(c), sqs[j], n) for j, c in enumerate(cols)]
    rng = random.Random(int(seed))
    hits = [0] * k
    B = int(B)
    for _ in range(B):
        eps = [1 if rng.getrandbits(1) else -1 for _ in range(n)]
        tmax = None
        for j in range(k):
            t = _t(sum(map(mul, eps, cols[j])), sqs[j], n)
            if t is None:
                continue
            if tmax is None or t > tmax:
                tmax = t
        if tmax is None:
            continue
        for j in range(k):
            if obs[j] is not None and tmax >= obs[j]:
                hits[j] += 1
    return [(1.0 + hits[j]) / (1.0 + B) if obs[j] is not None else None for j in range(k)]


# ---------------------------------------------------------------- near_tp

def quantile(xs, q):
    """Квантиль с линейной интерполяцией по отсортированным значениям (детерминированно, без статистических библиотек)."""
    ys = sorted(float(x) for x in xs if _fin(x))
    n = len(ys)
    if not n:
        return None
    if n == 1:
        return ys[0]
    pos = float(q) * (n - 1)
    i = int(math.floor(pos))
    if i >= n - 1:
        return ys[-1]
    return ys[i] + (ys[i + 1] - ys[i]) * (pos - i)


def path_meta(conn, keys):
    """(вид сигнала, горизонт) пути прогона по ключу оценки `"<key>|<policy_id>|<policy_ver>"` -> {ключ оценки: (type, horizon) | None}."""
    out = {}
    wanted = [str(k) for k in keys]
    for i in range(0, len(wanted), CHUNK):
        chunk = wanted[i:i + CHUNK]
        raw_keys, found = [], {}
        for k in chunk:
            sp = EV.split_run_key(k)
            if sp is None:
                out[k] = None
            else:
                raw_keys.append(sp[0])
        if not raw_keys:
            continue
        for key, pid, pver, typ, hz in conn.execute(
                "SELECT key, policy_id, policy_ver, type, horizon FROM run_paths WHERE key IN (%s)" % ",".join("?" * len(raw_keys)), raw_keys):
            found[EV.run_key(key, pid, int(pver))] = (typ, hz) if typ and hz else None
        for k in chunk:
            if k not in out:
                out[k] = found.get(k)
    return out


def near_tp_history(conn, base, a, cut_ts, cost_h, sim_ver=None):
    """MFE путей СТРОГО раньше окна поиска по виду сигнала и горизонту (Requirement 5.5, свойство 17).

    -> {(type, horizon): [path_mfe_r, ...]}; путь без вида сигнала/горизонта в таблицу не входит.
    """
    sql = ("SELECT key, path_mfe_r FROM exit_evals WHERE src=? AND var_id=? AND var_ver=? AND ord=? AND px_mode=? AND cost_h=? AND sim_ver=? "
           "AND t0<? AND labeled_ts<=?")
    args = (SRC_RUN, str(base["var_id"]), int(base["var_ver"]), ORDER_S, PX_FEE, str(cost_h),
            XS.SIM_VER if sim_ver is None else sim_ver, int(a), int(cut_ts))
    raw = conn.execute(sql, args).fetchall()
    meta = path_meta(conn, set(k for k, _mfe in raw))
    out = {}
    for key, mfe in raw:
        m = meta.get(str(key))
        v = _num(mfe)
        if m is None or v is None:
            continue
        out.setdefault(m, []).append(v)
    return out


def near_tp_tables(hist, variants):
    """Таблицы `near_tp.table` вариантов по истории -> {(var_id, ver): table}; вариант без `near_tp` в ответ не входит.

    Уровень — `max(квантиль q, min_r)` в R, не выше `NEAR_TP_MAX_R`; вида сигнала/горизонта нет в истории — строки таблицы нет (компонент
    не действует, как в `exitpol._near_level`).
    """
    out = {}
    for var in variants:
        nt = (var.get("policy") or {}).get("near_tp")
        if not isinstance(nt, dict) or "table" in nt:
            continue
        q, min_r = _num(nt.get("q")), _num(nt.get("min_r"))
        if q is None or min_r is None:
            continue
        table = {}
        for (typ, hz) in sorted(hist, key=lambda x: (str(x[0]), str(x[1]))):
            lvl = quantile(hist[(typ, hz)], q)
            if lvl is None:
                continue
            lvl = min(max(lvl, min_r), NEAR_TP_MAX_R)
            if lvl <= 0:
                continue
            table.setdefault(str(typ), {})[str(hz)] = round(lvl, 6)
        out[_pair((var["var_id"], var["var_ver"]))] = table
    return out


def apply_near_tp(policy, table):
    """Политика с подставленной таблицей `near_tp.table` (пустая таблица — политика без изменений)."""
    nt = (policy or {}).get("near_tp")
    if not table or not isinstance(nt, dict):
        return policy
    p = dict(policy)
    p["near_tp"] = dict(nt, table=dict((k, dict(v)) for k, v in table.items()))
    return p


# ---------------------------------------------------------------- справочные доли

def base_vs_policy_runs(conn, base, paths):
    """Доля путей окна, где `exitsim(X-0001, S)` совпал с `policy_runs.r_net` до 1e-9 (Requirement 14.1, справочно).

    Знаменатель — пути с `policy_runs.exit_reason ≠ "timeout"` (там `logic/sim.py` закрывает остаток по `max_hold_min`, а база держит до конца
    окна). Нет таблицы `policy_runs` или строк — `{"n": 0, "match": 0, "share": None}`.
    """
    items = sorted(paths)
    by_key = {}
    for i in range(0, len(items), CHUNK):
        chunk = items[i:i + CHUNK]
        raw_keys, parsed = [], []
        for k in chunk:
            sp = EV.split_run_key(k)
            if sp is None:
                continue
            raw_keys.append(sp[0])
            parsed.append((k, sp))
        if not raw_keys:
            continue
        for key, pid, pver, reason, r_net in conn.execute(
                "SELECT key, policy_id, policy_ver, exit_reason, r_net FROM policy_runs WHERE key IN (%s)" % ",".join("?" * len(raw_keys)),
                raw_keys):
            by_key[EV.run_key(key, pid, int(pver))] = (reason, _num(r_net))
    n = match = 0
    for k in items:
        row = by_key.get(k)
        if row is None or row[0] == "timeout" or row[1] is None:
            continue
        b = _num(paths[k]["base"]["r_net"])
        if b is None:
            continue
        n += 1
        if abs(b - row[1]) <= 1e-9:
            match += 1
    return {"n": n, "match": match, "share": round(match / float(n), 4) if n else None}


# ---------------------------------------------------------------- шаг

def _empty(code, results=None):
    return {"code": code, "candidates": [], "results": results or {}, "search_id": None}


def run(conn, root, cfg, now, force=False):
    """Шаг поиска (Requirement 5.1–5.8).

    -> {"code", "candidates": [{"id", "ver", "family", "policy", "policy_h", "menu_ver", "numbers"}], "results", "search_id",
    "sample_h", "cfg_h"}; `sample_h` и `cfg_h` — те же значения, что в колонках строки `exit_search` (в `results` их нет: сканер приватности
    базы пропускает длинные hex только в полях-хешах верхнего уровня). `force=True` — не проверять периодичность `search.every_days`.
    """
    now = int(now)
    if not EC.is_enabled(cfg):
        return _empty(CODE_DISABLED)
    if not force and not due(conn, cfg, now):
        return _empty(CODE_NOT_DUE)
    if backlog(conn):
        return _empty(CODE_BACKLOG)
    win = window(cfg, root, now)
    if win is None:
        return _empty(CODE_NO_WINDOW)
    inp = EV.market_inputs(root)
    floor_policy, cost_h = inp["floor"], inp["cost_h"]
    base = base_variant(conn, floor_policy)
    if base is None:
        out = _empty(CODE_NO_CANDIDATE, {"reason": "no_base", "window": win})
        out["cfg_h"] = EC.config_hash(cfg)
        return out

    block_h = _int((cfg.get("unit") or {}).get("block_h"))
    if block_h is None:
        raise ValueError("config/exits.json: нет unit.block_h")
    tail_mfe = float(cfg["tail"]["mfe_r"])
    rows = window_evals(conn, win, cost_h)
    paths = group_paths(rows, base)
    sample_h = sample_hash(paths)
    seed = seed_of(sample_h)

    blocked = blacklist(conn, now)
    base_h = base["policy_h"]
    menu = [v for v in menu_variants(cfg, floor_policy) if _pair((v["var_id"], v["var_ver"])) not in blocked]
    pool = [v for v in menu if v["policy_h"] != base_h]

    results = {"window": {"a": int(win["a"]), "b": int(win["b"]), "cut_ts": int(win["cut_ts"])},
               "menu_ver": cfg["menu_ver"], "base_ver": base["var_id"], "base_h": base_h, "block_h": block_h,
               "perm_b": int(cfg["search"]["perm_b"]), "alpha": float(cfg["search"]["alpha"]),
               "min_effect_r": float(cfg["search"]["min_effect_r"]), "min_clusters": int(cfg["search"]["min_clusters"]),
               "cost_h": cost_h, "sim_ver": XS.SIM_VER, "n_paths": len(paths),
               "n_blocks": len(set(p["base"]["block"] for p in paths.values())),
               "blacklist": sorted([[k[0], k[1], blocked[k]] for k in blocked]), "near_tp": {}, "variants": []}

    if results["n_blocks"] < int(cfg["search"]["min_clusters"]):
        results["blocks"] = sorted(set(p["base"]["block"] for p in paths.values()), key=_block_key)
        return _persist(conn, cfg, now, win, sample_h, base, block_h, CODE_LOW_N, results)

    # near_tp: только пути строго раньше окна (свойство 17); таблица идёт в политику кандидата (Requirement 5.5)
    hist = near_tp_history(conn, base, win["a"], win["cut_ts"], cost_h)
    tables = near_tp_tables(hist, pool)
    results["near_tp"] = dict((k[0], t) for k, t in tables.items() if t)

    if not pool:
        code = CODE_NOOP if menu else CODE_NO_CANDIDATE
        return _persist(conn, cfg, now, win, sample_h, base, block_h, code, results)

    stats = variant_stats(paths, pool, tail_mfe, PX_FEE)
    evaluated = [v for v in pool if stats[_pair((v["var_id"], v["var_ver"]))]["n_paths"] > 0]
    seen = set(_pair((v["var_id"], v["var_ver"])) for v in evaluated)
    noop = [v for v in evaluated if stats[_pair((v["var_id"], v["var_ver"]))]["noop"]]
    live = [v for v in evaluated if not stats[_pair((v["var_id"], v["var_ver"]))]["noop"]]
    results["noop"] = [v["var_id"] for v in noop]
    results["no_evals"] = [v["var_id"] for v in pool if _pair((v["var_id"], v["var_ver"])) not in seen]
    if not evaluated:
        return _persist(conn, cfg, now, win, sample_h, base, block_h, CODE_NO_CANDIDATE, results)
    if not live:
        return _persist(conn, cfg, now, win, sample_h, base, block_h, CODE_NOOP, results)

    _blocks, cols, D = matrix(stats, live)
    ps = maxt(D, int(cfg["search"]["perm_b"]), seed)
    p_by = dict((_pair((v["var_id"], v["var_ver"])), ps[i] if i < len(ps) else None) for i, v in enumerate(cols))
    t_by = dict((_pair((v["var_id"], v["var_ver"])), t_values([r[i] for r in complete_rows(D)])) for i, v in enumerate(cols))

    maker_vars = [v for v in cols if maker_parts(v["policy"])]
    taker_stats = variant_stats(paths, maker_vars, tail_mfe, PX_TAKER) if maker_vars else {}
    _tb, taker_cols, D_t = matrix(taker_stats, maker_vars) if maker_vars else ([], [], [])
    ps_t = maxt(D_t, int(cfg["search"]["perm_b"]), (seed + 1) % (1 << 64)) if D_t else []
    pt_by = dict((_pair((v["var_id"], v["var_ver"])), ps_t[i] if i < len(ps_t) else None) for i, v in enumerate(taker_cols))

    alpha = float(cfg["search"]["alpha"])
    min_eff = float(cfg["search"]["min_effect_r"])
    min_n = int(cfg["search"]["min_clusters"])
    keep = float(cfg["tail"]["keep"])
    tail_min_n = int(cfg["tail"]["min_n"])
    n_blocks = len(complete_rows(D))

    variants_out, candidates = [], []
    for v in pool:
        vid = _pair((v["var_id"], v["var_ver"]))
        st = stats[vid]
        is_noop = st["noop"]
        t_info = t_by.get(vid) or t_values([])
        p_adj = p_by.get(vid)
        tail_ok = bool(st["n_tail"] >= tail_min_n and (st["tail_var"] >= keep * st["tail_base"] if st["tail_base"] else st["tail_var"] >= 0.0))
        near = tables.get(vid)
        maker = maker_parts(v["policy"])
        taker_mean = taker_t = taker_p = None
        if maker:
            tin = t_values([x for xs in (taker_stats.get(vid) or {}).get("d", {}).values() for x in xs])
            taker_mean, taker_t, taker_p = tin["mean"], tin["t"], pt_by.get(vid)
        cost_ok = True if not maker else bool(taker_mean is not None and taker_p is not None and taker_p < alpha
                                              and taker_mean >= min_eff)
        conds = {"p": bool(p_adj is not None and p_adj < alpha),
                 "effect": bool(t_info["mean"] is not None and t_info["mean"] >= min_eff),
                 "n": bool(n_blocks >= min_n),
                 "tail": bool(tail_ok),
                 "cost": bool(cost_ok)}
        rec = {"id": v["var_id"], "ver": int(v["var_ver"]), "family": v["family"], "policy_h": v["policy_h"],
               "near_tp": len(near or {}), "noop": bool(is_noop), "n_blocks": len(st["d"]), "n_paths": st["n_paths"],
               "mean_d": _r(t_info["mean"]), "sd_d": _r(t_info["sd"]), "t": _r(t_info["t"]), "p": _r(p_adj, 10),
               "taker_mean_d": _r(taker_mean), "taker_t": _r(taker_t), "taker_p": _r(taker_p, 10),
               "tail": {"n": st["n_tail"], "sum_var": _r(st["tail_var"]), "sum_base": _r(st["tail_base"]),
                        "ratio": _r(st["tail_var"] / st["tail_base"]) if st["tail_base"] else None},
               "cut": _r(st["n_cut"] / float(st["n_paths"])) if st["n_paths"] else None,
               "amb": _r(st["n_amb"] / float(st["n_paths"])) if st["n_paths"] else None,
               "conds": conds}
        variants_out.append(rec)
        if is_noop or not all(conds.values()):
            continue
        policy = apply_near_tp(v["policy"], near)
        candidates.append({"id": v["var_id"], "ver": int(v["var_ver"]), "family": v["family"], "policy": policy,
                           "policy_h": XP.policy_hash(XP.normalize(policy, floor_policy)), "menu_ver": cfg["menu_ver"],
                           "numbers": {"n_blocks": n_blocks, "n_paths": st["n_paths"], "mean_d": _r(t_info["mean"]),
                                       "t": _r(t_info["t"]), "p": _r(p_adj, 10), "alpha": alpha, "min_effect_r": min_eff,
                                       "min_clusters": min_n, "tail": rec["tail"], "taker_mean_d": _r(taker_mean),
                                       "taker_p": _r(taker_p, 10), "near_tp": len(near or {})}})

    candidates.sort(key=lambda c: (-(c["numbers"]["mean_d"] or 0.0), c["id"], c["ver"]))
    results["variants"] = variants_out
    results["candidates"] = [[c["id"], c["ver"]] for c in candidates]
    results["n_blocks_matrix"] = n_blocks
    results["cut_share"] = _weighted_share(variants_out, "cut")
    results["amb_share"] = _weighted_share(variants_out, "amb")
    results["x0001_policy_runs"] = base_vs_policy_runs(conn, base, paths)
    code = CODE_OK if candidates else CODE_NO_CANDIDATE
    out = _persist(conn, cfg, now, win, sample_h, base, block_h, code, results)
    out["candidates"] = candidates
    return out


def _weighted_share(variants_out, field):
    """Средневзвешенная доля `field` по путям (число путей — вес)."""
    num = sum((v.get(field) or 0.0) * v["n_paths"] for v in variants_out)
    den = sum(v["n_paths"] for v in variants_out)
    return round(num / float(den), 4) if den else None


def _persist(conn, cfg, now, win, sample_h, base, block_h, code, results):
    """Строка `exit_search` и событие `learn_events step="exits_search"` (Requirement 5.6); -> результат шага."""
    rec = {"ts": int(now), "a": int(win["a"]), "b": int(win["b"]), "cut_ts": int(win["cut_ts"]), "sample_h": sample_h,
           "cfg_h": EC.config_hash(cfg), "menu_ver": cfg["menu_ver"], "base_ver": base["var_id"], "block_h": int(block_h),
           "code": code, "results": results}
    body = {"code": code, "a": rec["a"], "b": rec["b"], "cut_ts": rec["cut_ts"], "sample_h": sample_h,
            "n_paths": results.get("n_paths"), "n_blocks": results.get("n_blocks"), "candidates": results.get("candidates") or [],
            "blacklist": len(results.get("blacklist") or []), "cut": results.get("cut_share"), "amb": results.get("amb_share")}
    with LST.tx(conn):
        store2.insert_rows(conn, "exit_search", [rec])
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        store2.log_event(conn, STEP, code, "code", ref=sample_h, body=body, now=now)
    return {"code": code, "candidates": [], "results": results, "search_id": int(sid) if sid is not None else None,
            "sample_h": sample_h, "cfg_h": rec["cfg_h"]}
