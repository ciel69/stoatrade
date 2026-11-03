"""Кандидаты уроков меток (R2, Requirements 4.3, 4.5, 5.3, 5.6, 5.8, 5.9, 5.11, 5.15; design.md, «Меню, скрининг и holdout», «Коды причин»).

Суточные шаги цикла (их вызывает задача 10):
  run_menu        раз в menu_every_days: окно поиска [b − discovery_days, b) текущего ctx_ver (b — последняя граница часа с cut_ts ≤ now),
                  меню (menu.build_menu, чёрный список), скрининг, запись прошедших в lesson_candidates (status=new, источник code, окно,
                  cut_ts, хеш); прежние непринятые new -> dropped BUDGET; повтор cond_h или семейства block_h живого объекта -> dropped DUP_ACTIVE.
                  Коды прохода: NOT_DUE, NO_DATA, NO_MENU, MENU.
  accept          до labels_max_new_per_day кандидатов в сутки по возрастанию p: status=holdout, holdout [floor_hour(now), + holdout_days).
  check_holdouts  CTX_CHANGED сразу при смене ctx_ver; в cut_ts окна holdout: RESTORED / SAMPLE_CHANGED по окну поиска, один тест holdout,
                  урок hyp через lessons.create(created_by="code", evidence_src="labels") или dropped с кодом (чёрный список — только
                  NO_OOS_SIGN, NOT_SIGNIFICANT, N_TOO_SMALL, SAMPLE_CHANGED на cooldown_days).
Статусы lesson_candidates: new -> holdout -> hyp | dropped; new -> dropped. Все шаги идемпотентны.

Вторая итерация (задача 27; Requirements 12.2–12.5, 16.3, 17.1, 17.2, 17.4–17.6): кандидаты модели (источник llm) через файловый порт (port.py).
  llm_request     вход роли кодом: меню окна поиска с номерами M1..Mk и числами скрининга, словарь (признаки, операторы, значения; пороги
                  числовых признаков — токены q10/q20/q80/q90 со значениями квантилей окна), сводка суток; только числа и идентификаторы.
  submit_review   задание роли (day: day_reviewer за прошедшие сутки UTC; week: retro, шаг — задача 28) или llm=false (LLM_FALSE с кодом
                  port.available), если демона, пробы или роли нет; цикл продолжает кодом.
  accept_llm      приём ответа: уровень ответа (schema, day_id/week_id, неизвестное поле верхнего уровня) отклоняет ответ целиком; уровень элемента —
                  BAD_SCHEMA, NOT_IN_DICT, DUPLICATE, TOO_MANY (сверх 5 у ревью дня и 3 у ретроспективы, по порядку), BLOCKED (чёрный список),
                  TOO_BROAD и коды скрининга — только элемент. Принятый элемент проходит скрининг окна поиска (K = K меню + число своих условий модели),
                  claim с effect считает код; прошедшие — lesson_candidates(status=new, src=llm) и далее тот же путь accept/holdout/испытание.
  collect         шаг каждого прохода: готовые ответы -> accept_llm, просроченные (llm_wait_s) -> LLM_FALSE TIMEOUT, чистка порта.
Задача 31 (Requirement 18.2): словарь входа получает раздел ref — признаки, непроверяемые на метках (brainref.ref_dictionary); элемент с таким
  признаком не отклоняется NOT_IN_DICT, а идёт на справочную проверку по выборке мозга (brainref.check_candidate): урок evidence.src="brain",
  ref=true, via="llm" (rep["ref"]) или отказ N_TOO_SMALL; кандидатом lesson_candidates такой элемент не становится и правил не порождает.
"""
import datetime
import json

from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.labels import store as LST
from tb.learnloop import brainref as BR
from tb.learnloop import clusters as CL
from tb.learnloop import menu as MN
from tb.learnloop import port as PT
from tb.learnloop import rules as RL
from tb.learnloop import verify as VF
from tb.logic import learn_cond as LCD
from tb.logic import learn_rules as LR
from tb.memory import lessons as LS

SRC = "code"
SIDE = "lower"
BLOCK_CODES = ("NO_OOS_SIGN", "NOT_SIGNIFICANT", "N_TOO_SMALL", "SAMPLE_CHANGED")
CAND_COLS = ("id", "menu_id", "src", "cond", "claim", "env_norm", "ctx_ver", "k_space", "p_screen", "status", "code", "holdout_start", "holdout_end",
             "evidence", "lesson_id", "ts")


def _j(v):
    if isinstance(v, str) and v[:1] in ("{", "["):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _cand(r):
    d = dict(zip(CAND_COLS, r))
    for k in ("cond", "claim", "evidence"):
        d[k] = _j(d[k])
    return d


def candidates(conn, status=None, env=None):
    """Строки lesson_candidates (по id)."""
    q, a = "SELECT %s FROM lesson_candidates" % ", ".join(CAND_COLS), []
    w = []
    if status:
        w.append("status IN (%s)" % ",".join("?" * len(status)))
        a.extend(status)
    if env:
        w.append("env_norm=?")
        a.append(env)
    if w:
        q += " WHERE " + " AND ".join(w)
    return [_cand(r) for r in conn.execute(q + " ORDER BY id", a).fetchall()]


def _day(now):
    return int(now) // CL.DAY


def _menu_id(env, b):
    return "M-%s-%s" % (env, store2.iso(b)[:13].replace("-", "").replace(":", ""))


def _blocked(conn, now):
    """Ключ (block_h или прежний cond_h) -> в чёрном списке ли (menu.build_menu проверяет оба ключа условия)."""
    return lambda h: store2.blocklist_get(conn, h, now) is not None


# ---------------------------------------------------------------- живые объекты (DUP_ACTIVE)

def _rule_hashes(conn):
    """Ключи DUP_ACTIVE правил trial/active по последней ревизии (rulebook_view.rules_latest задачи 11): cond_h из тела правила (иначе по cond)
    и семейство block_h по cond."""
    out = set()
    for row in RV.rules_latest(conn).values():
        if row.get("status") not in ("trial", "active"):
            continue
        r = RV.row_rule(row)
        cond = r.get("cond") if isinstance(r.get("cond"), list) and r.get("cond") else None
        if isinstance(r.get("cond_h"), str) and r["cond_h"]:
            out.add(r["cond_h"])
        elif cond:
            out.add(LR.cond_h(cond, SIDE))
        if cond:
            out.add(LR.block_h(cond, SIDE))
    return out


def _dir(c):
    return (c.get("claim") or {}).get("dir") or SIDE


def cand_h(c):
    """cond_h кандидата (learn_rules.cond_h: тот же ключ, что у правил и версий). Чёрный список и DUP_ACTIVE — и по семейству (cand_keys)."""
    return LR.cond_h(c["cond"], _dir(c))


def cand_keys(c):
    """Ключи DUP_ACTIVE кандидата: точный cond_h и семейство block_h (дубль — совпадение любого с ключами живых объектов, active_hashes)."""
    return (cand_h(c), LR.block_h(c["cond"], _dir(c)))


def is_dup(c, live):
    return any(k in live for k in cand_keys(c))


def cand_blocked(conn, c, now):
    """Действующая запись чёрного списка кандидата: семейство block_h или прежняя по точному cond_h (rules.blocked) -> {"code", ...} | None."""
    return RL.blocked(conn, c["cond"], now, _dir(c))


def active_hashes(conn, root, exclude_cand=None):
    """Ключи DUP_ACTIVE живых объектов: точный cond_h активных уроков (hyp, confirmed) обеих групп; семейство block_h активных уроков меток,
    кандидатов в holdout и правил trial/active (сдвиг квантиля меню не даёт повторно испытывать то же семейство); cond_h кандидатов и правил."""
    out = set()
    for l in LS.read_all(root).values():
        if l.get("status") in LS.ACTIVE and isinstance(l.get("cond"), list) and l["cond"]:
            out.add(cand_h(l))
            if LS.is_labels(l):
                out.add(LR.block_h(l["cond"], _dir(l)))
    for c in candidates(conn, status=("holdout",)):
        if c["id"] != exclude_cand and isinstance(c["cond"], list) and c["cond"]:
            out.update(cand_keys(c))
    return out | _rule_hashes(conn)


# ---------------------------------------------------------------- меню

def last_menu_ts(conn, env):
    row = conn.execute("SELECT ts FROM learn_events WHERE step='menu' AND code='MENU' AND ref LIKE ? ORDER BY seq DESC LIMIT 1",
                       ("M-%s-%%" % env,)).fetchone()
    t1 = None
    if row:
        t1 =int(datetime.datetime.strptime(row[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp())
    row = conn.execute("SELECT MAX(ts) FROM lesson_candidates WHERE src=? AND menu_id LIKE ?", (SRC, "M-%s-%%" % env)).fetchone()
    t2 = row[0] if row and row[0] is not None else None
    ts = [t for t in (t1, t2) if t is not None]
    return max(ts) if ts else None


def menu_due(conn, now, cfg, env="mainnet"):
    """Меню раз в menu_every_days суток UTC: True, если меню ещё не строилось или прошло не меньше menu_every_days суток."""
    last = last_menu_ts(conn, env)
    return last is None or _day(now) - _day(last) >= int(cfg["verify"]["menu_every_days"])


def discovery_window(conn, now, cfg, policy, env="mainnet"):
    """Окно поиска: b = last_b(now), a = max(b − discovery_days, начало текущего ctx_ver). -> (окно | None, код NO_DATA | NO_MENU | None)."""
    b = CL.last_b(now, cfg)
    ctx, start = CL.current_ctx(conn, policy["id"], policy["ver"], env, b)
    if ctx is None:
        return None, "NO_DATA"
    a = max(b - int(cfg["verify"]["discovery_days"]) * CL.DAY, CL.hour_floor(start))
    if b - a < int(cfg["verify"]["discovery_days_min"]) * CL.DAY:
        return None, "NO_MENU"
    return CL.window(conn, policy["id"], policy["ver"], ctx, env, a, b, cfg, now), None


def run_menu(conn, root, now, cfg, policy=None, env="mainnet", force=False):
    """Суточный шаг меню. -> {"code": NOT_DUE | NO_DATA | NO_MENU | MENU, "menu_id", "k", "added", "dup", "budget", "screen", "dropped"}."""
    now = int(now)
    policy = policy or CL.load_policy(root, cfg)
    rep = {"code": None, "menu_id": None, "k": 0, "added": [], "dup": [], "budget": 0, "screen": [], "dropped": []}
    if not force and not menu_due(conn, now, cfg, env):
        rep["code"] = "NOT_DUE"
        return rep
    win, code = discovery_window(conn, now, cfg, policy, env)
    if win is None:
        rep["code"] = code
        return rep
    menu = MN.build_menu(win, cfg, blocked=_blocked(conn, now), side=SIDE)
    rep["k"] = menu["k"]
    rep["dropped"] = [dict((k, v) for k, v in d.items() if k != "tc") for d in menu["dropped"]]
    if not menu["k"]:
        rep["code"] = "NO_MENU"
        return rep
    mid = _menu_id(env, win["b"])
    rep["menu_id"] = mid
    smp = CL.sample(win)
    live = active_hashes(conn, root)
    rows = []
    for it, sc in MN.screen_menu(win, menu, cfg, SIDE):
        rep["screen"].append({"cond": it["cond"], "n": sc["n"], "diff": sc["diff"], "p": sc["p"], "code": sc["code"]})
        if not sc["ok"]:
            continue
        dup = it["cond_h"] in live or it["block_h"] in live
        ev = {"disc": smp, "screen": {"n": sc["n"], "diff": sc["diff"], "se": sc["se"], "p": sc["p"], "thr": round(sc["thr"], 8), "k": sc["k"],
                                      "halves": sc["halves"], "share": it["share"]}, "excl_share": win["excl_share"]}
        rows.append({"menu_id": mid, "src": SRC, "cond": it["cond"], "claim": it["claim"], "env_norm": env, "ctx_ver": win["ctx_ver"], "k_space": menu["k"],
                     "p_screen": sc["p"], "status": "dropped" if dup else "new", "code": "DUP_ACTIVE" if dup else None, "evidence": ev, "ts": now})
        (rep["dup"] if dup else rep["added"]).append(it["cond"])
    with LST.tx(conn):
        for c in candidates(conn, status=("new",), env=env):
            if c["menu_id"] != mid:
                store2.update_row(conn, "lesson_candidates", c["id"], {"status": "dropped", "code": "BUDGET"})
                rep["budget"] += 1
        if rows:
            store2.insert_rows(conn, "lesson_candidates", rows, ignore_dup=True)
        store2.log_event(conn, "menu", "MENU", "code", ref=mid, body={"k": menu["k"], "new": len(rep["added"]), "dup": len(rep["dup"]),
                                                                          "a": win["a"], "b": win["b"], "cut_ts": win["cut_ts"], "h": win["hash"]}, now=now)
    rep["code"] = "MENU"
    return rep


# ---------------------------------------------------------------- приём

def _current_ctx(conn, policy, env, now):
    return CL.current_ctx(conn, policy["id"], policy["ver"], env, int(now) + 1)[0]


def accept(conn, root, now, cfg, policy=None, env="mainnet"):
    """Приём до labels_max_new_per_day кандидатов в сутки UTC по возрастанию p: holdout [floor_hour(now), + holdout_days).
    Перед приёмом: DUP_ACTIVE, чёрный список (dropped с кодом записи), смена ctx_ver (CTX_CHANGED). -> [(id, статус, код)]."""
    now = int(now)
    policy = policy or CL.load_policy(root, cfg)
    day0 = _day(now) * CL.DAY
    used = conn.execute("SELECT COUNT(*) FROM lesson_candidates WHERE env_norm=? AND holdout_start IS NOT NULL AND holdout_start>=?", (env, day0)).fetchone()[0]
    slots = int(cfg["verify"]["labels_max_new_per_day"]) - int(used)
    new = sorted(candidates(conn, status=("new",), env=env), key=lambda c: (c["p_screen"] if c["p_screen"] is not None else 1.0, c["id"]))
    if not new or slots <= 0:
        return []
    live = active_hashes(conn, root)
    ctx = _current_ctx(conn, policy, env, now)
    hs = CL.hour_floor(now)
    he = hs + int(cfg["verify"]["holdout_days"]) * CL.DAY
    out = []
    with LST.tx(conn):
        for c in new:
            if slots <= 0:
                break
            blk = cand_blocked(conn, c, now)
            if ctx is not None and ctx != c["ctx_ver"]:
                code = "CTX_CHANGED"
            elif is_dup(c, live):
                code = "DUP_ACTIVE"
            elif blk is not None:
                code = blk["code"]
            else:
                code = None
            if code:
                store2.update_row(conn, "lesson_candidates", c["id"], {"status": "dropped", "code": code})
                out.append((c["id"], "dropped", code))
                continue
            store2.update_row(conn, "lesson_candidates", c["id"], {"status": "holdout", "holdout_start": hs, "holdout_end": he})
            live.update(cand_keys(c))
            slots -= 1
            out.append((c["id"], "holdout", None))
    return out


# ---------------------------------------------------------------- holdout

def _drop(conn, c, code, ev, now, cfg, block):
    with LST.tx(conn):
        store2.update_row(conn, "lesson_candidates", c["id"], {"status": "dropped", "code": code, "evidence": ev})
        if block:
            store2.blocklist_put(conn, LR.block_h(c["cond"], _dir(c)), code, now + int(cfg["blocklist"]["cooldown_days"]) * CL.DAY, "code", now)
        if code == "SAMPLE_CHANGED":
            store2.log_event(conn, "holdout", "SAMPLE_CHANGED", "code", ref="cand:%d" % c["id"], body={"disc": (ev or {}).get("disc")}, error=True, now=now)
    return (c["id"], "dropped", code)


def _existing_lesson(root, cand_id):
    for l in LS.read_all(root).values():
        if LS.is_labels(l) and (l.get("evidence") or {}).get("cand") == cand_id:
            return l
    return None


def lesson_evidence(c, disc, hold_win, nums):
    """evidence_extra урока меток: выборки обоих окон (границы, cut_ts, хеш 16 hex), числа скрининга и holdout, до 5 кластеров."""
    scr = (c["evidence"] or {}).get("screen") or {}
    extra = {"via": c["src"]} if c.get("src") and c["src"] != SRC else {}          # кандидат модели (src=llm): источник виден в уроке
    return dict(extra, **{"cand": c["id"], "menu": c["menu_id"],
            "sample": {"policy": disc["policy"], "policy_ver": disc["policy_ver"], "ctx_ver": disc["ctx_ver"], "env_norm": disc["env_norm"],
                       "disc": {"a": disc["a"], "b": disc["b"], "cut_ts": disc["cut_ts"], "h": disc["h"]},
                       "hold": {"a": hold_win["a"], "b": hold_win["b"], "cut_ts": hold_win["cut_ts"], "h": hold_win["hash"]}},
            "screen": {"n": scr.get("n"), "diff": scr.get("diff"), "p": scr.get("p"), "k": c["k_space"]},
            "n_closed": nums["n"], "diff": nums["diff"], "se": nums["se"], "p": nums["p"], "k_h": nums["k_h"],
            "excl_share": hold_win["excl_share"], "last_ts": store2.iso(hold_win["b"]), "clusters": list(nums["clusters"][:5])})


def check_holdouts(conn, root, now, cfg, policy=None, mcfg=None, env="mainnet"):
    """Кандидаты в holdout: CTX_CHANGED сразу; в cut_ts окна holdout — проверка окна поиска (RESTORED, SAMPLE_CHANGED), тест, урок hyp или dropped.
    -> [(id, статус, код)] изменённых (ожидающие не входят)."""
    now = int(now)
    policy = policy or CL.load_policy(root, cfg)
    if mcfg is None:
        from tb.memory import cfg as memcfg
        mcfg = memcfg.load(root)
    out = []
    for c in candidates(conn, status=("holdout",), env=env):
        ev = dict(c["evidence"] or {})
        disc = ev.get("disc") or {}
        if CL.ctx_changed_since(conn, policy["id"], disc.get("policy_ver", policy["ver"]), env, c["ctx_ver"], c["holdout_start"]):
            out.append(_drop(conn, c, "CTX_CHANGED", ev, now, cfg, False))
            continue
        if now < CL.cut_ts(c["holdout_end"], cfg):
            continue
        chk = CL.check_sample(conn, disc, cfg, now)
        if chk != "OK":
            out.append(_drop(conn, c, chk, ev, now, cfg, chk == "SAMPLE_CHANGED"))
            continue
        hw = CL.window(conn, disc["policy"], disc["policy_ver"], c["ctx_ver"], env, c["holdout_start"], c["holdout_end"], cfg, now)
        st, code, nums = VF.holdout(hw, c["cond"], VF.k_h(cfg), cfg, (c["claim"] or {}).get("dir") or SIDE)
        ev["hold"] = dict(CL.sample(hw), n=nums["n"], diff=nums["diff"], se=nums["se"], p=nums["p"], thr=round(nums["thr"], 8))
        if st != "hyp":
            out.append(_drop(conn, c, code, ev, now, cfg, code in BLOCK_CODES))
            continue
        blk = cand_blocked(conn, c, now)
        if blk is not None:
            out.append(_drop(conn, c, blk["code"], ev, now, cfg, False))
            continue
        l = _existing_lesson(root, c["id"])          # повтор после падения между записью урока и строки кандидата
        if l is None:
            if is_dup(c, active_hashes(conn, root, exclude_cand=c["id"])):
                out.append(_drop(conn, c, "DUP_ACTIVE", ev, now, cfg, False))
                continue
            try:
                l = LS.create(root, c["cond"], c["claim"], "code", [], mcfg, now, evidence_src="labels", evidence_extra=lesson_evidence(c, disc, hw, nums))
            except LS.LessonRefused as e:
                out.append(_drop(conn, c, e.code, ev, now, cfg, False))
                continue
        store2.update_row(conn, "lesson_candidates", c["id"], {"status": "hyp", "lesson_id": l["lesson_id"], "evidence": ev})
        out.append((c["id"], "hyp", l["lesson_id"]))
    return out


# ---------------------------------------------------------------- кандидаты модели (вторая итерация)

LLM_SRC = "llm"
LLM_FALSE, LLM_SUBMIT, LLM_PENDING, LLM_OK, LLM_REJECT = "LLM_FALSE", "LLM_SUBMIT", "LLM_PENDING", "LLM", "LLM_REJECT"
LLM_KINDS = {
    "day": {"role": "day_reviewer", "schema": "tb.lesson_cand/1", "id": "day_id", "limit": 5, "prefix": "LD", "step": "day_review"},
    "week": {"role": "retro", "schema": "tb.retro/1", "id": "week_id", "limit": 3, "prefix": "LW", "step": "retro"},
}
Q_TOKENS = (("q10", 0.1), ("q20", 0.2), ("q80", 0.8), ("q90", 0.9))
LLM_CAT = tuple(LCD.MENU_CAT) + ("side",)         # utc_session — Requirement 18.8 (вторая итерация, после замера мощности)
CLAIM_DICT = {"metric": ("mean_r",), "dir": (SIDE,), "vs": ("base",)}
ITEM_MENU_KEYS = frozenset(("menu", "dir"))
ITEM_COND_KEYS = frozenset(("cond", "claim"))
# коды отказа элемента (Requirement 17.4) и прочие коды элемента
BAD_SCHEMA, NOT_IN_DICT, DUPLICATE, TOO_MANY, BLOCKED, TOO_BROAD = "BAD_SCHEMA", "NOT_IN_DICT", "DUPLICATE", "TOO_MANY", "BLOCKED", "TOO_BROAD"
BAD_ID = "BAD_ID"


class ItemRejected(Exception):
    def __init__(self, code):
        Exception.__init__(self, code)
        self.code = code


def day_id(now):
    """Идентификатор прошедших суток UTC (ревью дня после смены суток): D-YYYYMMDD."""
    d = datetime.datetime.fromtimestamp((int(now) // CL.DAY - 1) * CL.DAY, datetime.timezone.utc)
    return "D-" + d.strftime("%Y%m%d")


def week_id(now):
    """Идентификатор прошедшей недели ISO (ретроспектива после смены недели): W-YYYY-WW."""
    d = datetime.datetime.fromtimestamp(int(now) - 7 * CL.DAY, datetime.timezone.utc).isocalendar()
    return "W-%04d-%02d" % (d[0], d[1])


def llm_dictionary(win, cfg):
    """Словарь для модели по выборке окна поиска: числовые признаки меню с заполнением >= feat_cover_min — операторы >=, <= и пороги-токены
    (значения квантилей окна считает код); coin_class и side — значения словаря learn_cond; claim — только mean_r / lower / base."""
    p = VF.prepared(win)
    n = len(p["m"])
    cover_min = float(cfg["verify"]["feat_cover_min"])
    feats = {}
    for f in LCD.MENU_NUM:
        xs = [x for x in VF.column(p, f) if LCD.is_num(x)]
        if not xs or len(xs) / float(n) < cover_min:
            continue
        feats[f] = {"ops": list(LCD.NUM_OPS), "values": dict((t, MN._qv(MN.quantile(xs, q))) for t, q in Q_TOKENS)}
    for f in LLM_CAT:
        vals = list(LCD.menu_cat_values(f))
        feats[f] = {"ops": ["="] if f == "side" else list(LCD.CAT_OPS), "values": vals}
    return {"features": feats, "claim": dict((k, list(v)) for k, v in CLAIM_DICT.items()), "max_cond": LCD.MAX_COND}


def _day_numbers(conn, env, a, b):
    row = conn.execute("SELECT COUNT(*), SUM(filled), AVG(CASE WHEN filled=1 THEN r_net END), COUNT(DISTINCT cluster) FROM cluster_runs "
                       "WHERE env_norm=? AND t0>=? AND t0<?", (env, int(a), int(b))).fetchone()
    return {"runs": int(row[0] or 0), "filled": int(row[1] or 0), "mean_r": None if row[2] is None else round(float(row[2]), 4), "clusters": int(row[3] or 0)}


def llm_request(conn, root, now, cfg, kind="day", policy=None, env="mainnet", extra=None):
    """Вход роли (только числа, перечисления, идентификаторы). -> (payload | None, код NO_DATA | NO_MENU | None)."""
    spec = LLM_KINDS[kind]
    now = int(now)
    policy = policy or CL.load_policy(root, cfg)
    win, code = discovery_window(conn, now, cfg, policy, env)
    if win is None:
        return None, code
    menu = MN.build_menu(win, cfg, blocked=_blocked(conn, now), side=SIDE)
    items = []
    for i, (it, sc) in enumerate(MN.screen_menu(win, menu, cfg, SIDE)):
        items.append({"id": "M%d" % (i + 1), "cond": it["cond"], "n": sc["n"], "diff": sc["diff"], "p": sc["p"], "share": it["share"], "ok": bool(sc["ok"])})
    pid = day_id(now) if kind == "day" else week_id(now)
    dic = llm_dictionary(win, cfg)
    try:                                                  # вторая итерация (задача 31): непроверяемые на метках признаки — справочная проверка
        dic["ref"] = BR.ref_dictionary(root, now, cfg)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    payload = {"kind": kind, spec["id"]: pid, "env": env, "k": menu["k"], "menu": items, "dict": dic, "limit": spec["limit"]}
    if kind == "day":
        d0 = (now // CL.DAY - 1) * CL.DAY
        payload["day"] = _day_numbers(conn, env, d0, d0 + CL.DAY)
    payload.update(extra or {})
    return PT.clean(payload), None


def _log(conn, step, code, now, ref=None, body=None):
    store2.log_event(conn, step, code, "code", ref=ref, body=body, now=now)


def _pending_same(root, kind, pid):
    for jid, job in PT.own(root):
        m = job.get("meta") or {}
        if m.get("kind") == kind and m.get("id") == pid:
            return jid
    return None


def submit_review(conn, root, now, cfg, kind="day", policy=None, env="mainnet", extra=None):
    """Задание роли через порт или llm=false. -> {"code": LLM_SUBMIT | LLM_PENDING | LLM_FALSE | NO_DATA | NO_MENU, "why", "job", "id"}."""
    spec = LLM_KINDS[kind]
    now = int(now)
    pid = day_id(now) if kind == "day" else week_id(now)
    ok, why = PT.available(root, now, spec["role"])
    if not ok:
        _log(conn, spec["step"], LLM_FALSE, now, ref=pid, body={"llm": False, "why": why})
        return {"code": LLM_FALSE, "why": why, "id": pid, "job": None}
    jid = _pending_same(root, kind, pid)
    if jid:
        return {"code": LLM_PENDING, "why": None, "id": pid, "job": jid}
    payload, code = llm_request(conn, root, now, cfg, kind, policy, env, extra)
    if payload is None:
        return {"code": code, "why": code, "id": pid, "job": None}
    jid = PT.submit(root, spec["role"], payload, now, cfg, meta={"kind": kind, "id": pid, "env": env})
    _log(conn, spec["step"], LLM_SUBMIT, now, ref=pid, body={"job": jid, "k": payload.get("k")})
    return {"code": LLM_SUBMIT, "why": None, "id": pid, "job": jid}


# ---- проверка ответа

def check_answer(obj, kind, expect_id):
    """Уровень ответа (Requirement 17.4): объект, schema, day_id/week_id, только ключи schema, id, items. -> (items | None, код | None)."""
    spec = LLM_KINDS[kind]
    if not isinstance(obj, dict) or obj.get("schema") != spec["schema"] or not isinstance(obj.get("items"), list):
        return None, BAD_SCHEMA
    if set(obj) - {"schema", spec["id"], "items"}:
        return None, BAD_SCHEMA
    if obj.get(spec["id"]) != expect_id:
        return None, BAD_ID
    return obj["items"], None


def _num_value(v, vals):
    """Порог числового признака: токен словаря (q80) -> значение квантиля; число принимается, только если совпадает со значением словаря."""
    if isinstance(v, str) and v in vals:
        return vals[v]
    if LCD.is_num(v) and v in vals.values():
        return float(v)
    raise ItemRejected(NOT_IN_DICT)


def _cond_part(c, feats, ref=None):
    """Часть условия по словарю: признак из features (проверяем на метках) или, если его там нет, из ref (непроверяемый на метках признак,
    справочная проверка по выборке мозга). -> (часть, справочная ли)."""
    if not isinstance(c, dict) or set(c) != {"feature", "op", "value"}:
        raise ItemRejected(BAD_SCHEMA)
    f, op, v = c["feature"], c["op"], c["value"]
    is_ref = False
    if isinstance(f, str) and f not in feats and f in (ref or {}):
        feats, is_ref = ref, True
    if not isinstance(f, str) or f not in feats or op not in feats[f]["ops"]:
        raise ItemRejected(NOT_IN_DICT)
    vals = feats[f]["values"]
    if isinstance(vals, dict):
        return {"feature": f, "op": op, "value": _num_value(v, vals)}, is_ref
    if op == "in":
        if not isinstance(v, list) or not v or len(v) > LCD.MAX_IN or len(set(map(str, v))) != len(v) or any(not isinstance(x, str) or x not in vals for x in v):
            raise ItemRejected(NOT_IN_DICT)
        return {"feature": f, "op": op, "value": sorted(v)}, is_ref
    if isinstance(v, bool) and v in vals:                 # логический признак справочного словаря
        return {"feature": f, "op": op, "value": v}, is_ref
    if not isinstance(v, str) or v not in vals:
        raise ItemRejected(NOT_IN_DICT)
    return {"feature": f, "op": op, "value": v}, is_ref


REF = "REF"                                               # pick справочного кандидата (непроверяемый на метках признак, Requirement 18.2)


def check_item(it, menu, dic):
    """Уровень элемента. -> (cond, id меню | None | REF); ItemRejected(BAD_SCHEMA | NOT_IN_DICT). effect и числа правила модель не задаёт.
    Признак вне features, но из словаря ref (вторая итерация, задача 31) делает кандидата справочным: pick=REF, условие проверяется по словарю
    уроков (memory.lessons.validate_cond), дальше — справочная проверка по выборке мозга (learnloop.brainref.check_candidate)."""
    if not isinstance(it, dict):
        raise ItemRejected(BAD_SCHEMA)
    keys = set(it)
    if "menu" in keys:
        if not keys <= ITEM_MENU_KEYS:
            raise ItemRejected(BAD_SCHEMA)
        if it.get("dir", SIDE) not in CLAIM_DICT["dir"]:
            raise ItemRejected(NOT_IN_DICT)
        mid = it["menu"]
        if not isinstance(mid, str) or mid not in menu:
            raise ItemRejected(NOT_IN_DICT)
        return [dict(x) for x in menu[mid]], mid
    if "cond" not in keys or not keys <= ITEM_COND_KEYS:
        raise ItemRejected(BAD_SCHEMA)
    cl = it.get("claim", {})
    if not isinstance(cl, dict) or not set(cl) <= set(CLAIM_DICT):
        raise ItemRejected(BAD_SCHEMA)
    if any(cl[k] not in CLAIM_DICT[k] for k in cl):
        raise ItemRejected(NOT_IN_DICT)
    raw = it["cond"]
    if not isinstance(raw, list) or not (1 <= len(raw) <= int(dic.get("max_cond") or LCD.MAX_COND)):
        raise ItemRejected(BAD_SCHEMA)
    feats = dic.get("features") or {}
    ref = dic.get("ref") if isinstance(dic.get("ref"), dict) else None
    parts = [_cond_part(c, feats, ref) for c in raw]
    cond = [p for p, _r in parts]
    if len(set(c["feature"] for c in cond)) != len(cond):
        raise ItemRejected(BAD_SCHEMA)
    if any(r for _p, r in parts):
        if LS.validate_cond(cond):
            raise ItemRejected(NOT_IN_DICT)
        return sorted(cond, key=lambda c: c["feature"]), REF
    if LCD.validate_cond(cond):
        raise ItemRejected(NOT_IN_DICT)
    return LR.canon_cond(cond), None


def _new_keys(conn, env):
    out = set()
    for c in candidates(conn, status=("new",), env=env):
        if isinstance(c["cond"], list) and c["cond"]:
            out.update(cand_keys(c))
    return out


def accept_llm(conn, root, now, cfg, kind, job, answer, policy=None, env=None):
    """Приём ответа модели (job — своё задание порта: вход, меню, словарь; answer — output ответа демона).
    -> {"code": LLM | LLM_REJECT | NO_DATA | NO_MENU, "why", "menu_id", "accepted": [(i, id кандидата)], "rejected": [(i, код)]}."""
    spec = LLM_KINDS[kind]
    now = int(now)
    payload = job.get("payload") or {}
    env = env or payload.get("env") or "mainnet"
    pid = payload.get(spec["id"])
    jid = job.get("id")
    rep = {"code": None, "why": None, "menu_id": "%s-%s-%s" % (spec["prefix"], env, pid), "accepted": [], "rejected": []}
    items, code = check_answer(answer, kind, pid)
    if items is None:
        rep.update(code=LLM_REJECT, why=code)
        _log(conn, spec["step"], LLM_REJECT, now, ref=pid, body={"job": jid, "why": code})
        return rep
    policy = policy or CL.load_policy(root, cfg)
    win, wcode = discovery_window(conn, now, cfg, policy, env)
    if win is None:
        rep.update(code=wcode, why=wcode, rejected=[(i, wcode) for i in range(len(items))])
        _log(conn, spec["step"], wcode, now, ref=pid, body={"job": jid, "n": len(items)})
        return rep
    menu = dict((m["id"], m["cond"]) for m in payload.get("menu") or [] if isinstance(m, dict) and isinstance(m.get("id"), str) and isinstance(m.get("cond"), list))
    dic = payload.get("dict") or {}
    live = active_hashes(conn, root) | _new_keys(conn, env)
    seen, valid, refs = set(), [], []
    for i, it in enumerate(items):
        if i >= spec["limit"]:
            rep["rejected"].append((i, TOO_MANY))
            continue
        try:
            cond, pick = check_item(it, menu, dic)
        except ItemRejected as e:
            rep["rejected"].append((i, e.code))
            continue
        if pick == REF:
            key = frozenset(LS._cond_set(cond))
            if key in seen:
                rep["rejected"].append((i, DUPLICATE))
                continue
            seen.add(key)
            refs.append((i, cond))
            continue
        c = {"cond": cond, "claim": {"dir": SIDE}}
        keys = cand_keys(c)
        if any(k in seen for k in keys) or is_dup(c, live):
            rep["rejected"].append((i, DUPLICATE))
            continue
        seen.update(keys)
        if cand_blocked(conn, c, now) is not None:
            rep["rejected"].append((i, BLOCKED))
            continue
        valid.append((i, cond, pick))
    k = int(payload.get("k") or 0) + sum(1 for v in valid if v[2] is None)       # своё условие модели расширяет просмотренное пространство
    k = max(k, 1)
    p = VF.prepared(win)
    n_rows = len(p["m"])
    broad = float(cfg["trial"]["max_trade_loss_share"])
    smp = CL.sample(win)
    rows = []
    for i, cond, pick in valid:
        hits = VF.hit_vector(p, cond)
        share = sum(hits) / float(n_rows) if n_rows else 0.0
        if share > broad:
            rep["rejected"].append((i, TOO_BROAD))
            continue
        tc = VF.tc_from_hits(p, hits)
        sc = VF.screen(win, cond, k, cfg, SIDE, tc=tc)
        if not sc["ok"]:
            rep["rejected"].append((i, sc["code"]))
            continue
        ev = {"disc": smp, "screen": {"n": sc["n"], "diff": sc["diff"], "se": sc["se"], "p": sc["p"], "thr": round(sc["thr"], 8), "k": sc["k"],
                                      "halves": sc["halves"], "share": round(share, 4)}, "excl_share": win["excl_share"],
              "llm": {"job": jid, "role": spec["role"], "i": i, "pick": pick}}
        claim = {"metric": "mean_r", "dir": SIDE, "vs": "base", "effect": VF.effect(tc)}
        rows.append((i, {"menu_id": rep["menu_id"], "src": LLM_SRC, "cond": cond, "claim": claim, "env_norm": env, "ctx_ver": win["ctx_ver"], "k_space": k,
                         "p_screen": sc["p"], "status": "new", "code": None, "evidence": ev, "ts": now}))
    rep["ref"] = []
    for i, cond in refs:                                  # справочная проверка по выборке мозга (Requirement 18.2): урок ref или N_TOO_SMALL
        try:
            res = BR.check_candidate(root, cond, {"dir": SIDE}, now, cfg, via=LLM_SRC)
        except (OSError, ValueError, KeyError, TypeError):
            res = {"code": BR.N_TOO_SMALL, "lesson_id": None}
        if res["code"] == BR.REF_OK:
            rep["ref"].append((i, res["lesson_id"]))
        else:
            rep["rejected"].append((i, res["code"]))
    rep["rejected"].sort()
    with LST.tx(conn):
        if rows:
            store2.insert_rows(conn, "lesson_candidates", [r for _i, r in rows], ignore_dup=True)
        ids = dict((cand_h(c), c["id"]) for c in candidates(conn, env=env) if c["menu_id"] == rep["menu_id"] and c["src"] == LLM_SRC)
        for i, r in rows:
            rep["accepted"].append((i, ids.get(cand_h(r))))
        body = {"job": jid, "k": k, "accepted": [i for i, _ in rep["accepted"]], "rejected": [[i, c] for i, c in rep["rejected"]]}
        if rep["ref"]:
            body["ref"] = [[i, lid] for i, lid in rep["ref"]]
        _log(conn, spec["step"], LLM_OK, now, ref=pid, body=body)
    rep["code"] = LLM_OK
    return rep


# ---- шаги цикла

def collect(conn, root, now, cfg, policy=None):
    """Каждый проход: ответы готовых заданий -> accept_llm; неудачные ответы и просрочка llm_wait_s -> LLM_FALSE (llm=false); чистка порта.
    Задание удаляется после обработки (повтор прохода ничего не добавляет). -> {"code": "OK", "done": [(jid, код)], "sweep": {...}}."""
    now = int(now)
    out = []
    for jid, job in PT.own(root):
        m = job.get("meta") or {}
        kind = m.get("kind")
        if kind not in LLM_KINDS:
            continue
        spec = LLM_KINDS[kind]
        st = PT.status(root, jid, now, cfg, job)
        if st in ("waiting", "claimed"):
            continue
        try:
            if st == "done":
                res = PT.result(root, jid)
                if res["ok"]:
                    out.append((jid, accept_llm(conn, root, now, cfg, kind, job, res["output"], policy=policy)["code"]))
                else:
                    _log(conn, spec["step"], LLM_FALSE, now, ref=m.get("id"), body={"llm": False, "why": res["err"] or "ERR", "job": jid})
                    out.append((jid, LLM_FALSE))
            else:
                _log(conn, spec["step"], LLM_FALSE, now, ref=m.get("id"), body={"llm": False, "why": PT.TIMEOUT, "job": jid})
                out.append((jid, LLM_FALSE))
        finally:
            PT.done(root, jid)
    return {"code": "OK", "done": out, "sweep": PT.sweep(root, now, cfg)}


def step_day_review(ctx):
    """Суточный шаг ревью дня (флаг: роль доступна через port.available, иначе LLM_FALSE — сутки помечены llm=false, цикл идёт кодом)."""
    rep = submit_review(ctx.conn, ctx.root, ctx.now, ctx.cfg, "day", policy=ctx.policy)
    return {"code": rep["code"], "why": rep["why"], "id": rep["id"], "job": rep["job"]}


def step_llm_collect(ctx):
    rep = collect(ctx.conn, ctx.root, ctx.now, ctx.cfg, policy=ctx.policy)
    return {"code": "OK", "done": len(rep["done"])}
