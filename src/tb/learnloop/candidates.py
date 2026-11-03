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
"""
import datetime
import json

from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.labels import store as LST
from tb.learnloop import clusters as CL
from tb.learnloop import menu as MN
from tb.learnloop import rules as RL
from tb.learnloop import verify as VF
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
    return {"cand": c["id"], "menu": c["menu_id"],
            "sample": {"policy": disc["policy"], "policy_ver": disc["policy_ver"], "ctx_ver": disc["ctx_ver"], "env_norm": disc["env_norm"],
                       "disc": {"a": disc["a"], "b": disc["b"], "cut_ts": disc["cut_ts"], "h": disc["h"]},
                       "hold": {"a": hold_win["a"], "b": hold_win["b"], "cut_ts": hold_win["cut_ts"], "h": hold_win["hash"]}},
            "screen": {"n": scr.get("n"), "diff": scr.get("diff"), "p": scr.get("p"), "k": c["k_space"]},
            "n_closed": nums["n"], "diff": nums["diff"], "se": nums["se"], "p": nums["p"], "k_h": nums["k_h"],
            "excl_share": hold_win["excl_share"], "last_ts": store2.iso(hold_win["b"]), "clusters": list(nums["clusters"][:5])}


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
