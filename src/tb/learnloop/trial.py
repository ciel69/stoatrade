"""Испытание правила (R2, задача 14; Requirements 9.1–9.9, 10.1; design.md, «Единица наблюдения, кластеры, снимки и выборки окна», «Окна данных»,
«tb.learnloop.rules / versions / trial ...», «Коды причин»; свойства 7, 11, 20, 24, 25, 26, 29).

Регистрация (register_pending; суточный шаг register и каждый часовой проход verdicts — для уроков, ждущих места):
  урок меток hyp с claim (mean_r, lower, base) без правил в rule_rows -> проверка выборки holdout (RESTORED / SAMPLE_CHANGED), смена ctx_ver
  после начала holdout (CTX_CHANGED), отключённая версия политики (NO_VERDICT) -> лимит max_parallel открытых испытаний (ожидание; дольше
  wait_max_days с создания урока -> BUDGET) -> достижимость по выборке окна последних reach_lookback_days (UNREACHABLE_N: правило rejected без
  испытания, чёрный список) -> versions.create_with_trial (правило trial и испытание с критерием одной транзакцией).
  Отклонение при регистрации переводит урок lessons.transition сразу (испытания нет, сверке задачи 15 нечего сверять).
Критерий (criterion, criterion_h = 16 hex sha256 канонического JSON) записывается при регистрации и не меняется (триггер trials): метрика, источник
  (политика, версия, ctx_ver, env_norm), условие, тест, активный набор на момент старта, n_target, alpha_adj = alpha / max_parallel, min_effect_r,
  max_trade_loss_share, overlap_share_max, scope, max_days, start_ts, deadline_ts, параметры cut_ts, хеш config/learnloop.json (16 hex).
Числа (analyze, verdict — чистые): только строки выборки окна cluster_runs [start_ts, b) записанных политики, версии, ctx_ver, env_norm, в scope;
  прогоны, отсечённые активным набором критерия, исключаются (pass_active); предельный хит — hit=1, база испытания — hit=0 того же кластера;
  незаполненные — счётчик nofill_n; кластер теста — обе группы, значение — разность средних r_net; первые n_target по (t0, кластер).
Вердикт (шаг verdicts, раз в час): минимальная граница часа b, при которой выборка [start_ts, b) с closed_ts <= cut_ts(b) <= now содержит
  n_target кластеров теста; promote, если одностороннее p парного теста < alpha_adj, среднее предельных хитов <= -min_effect_r и доля предельных
  хитов среди прогонов, пропущенных активным набором, <= max_trade_loss_share; иначе reject (TRIAL_REJECT). Без набора в [start_ts, deadline_ts)
  — inconclusive (NO_VERDICT) не раньше cut_ts(deadline_ts). Смена ctx_ver — сразу inconclusive CTX_CHANGED; отключённая записанная версия политики
  — NO_VERDICT; окно с copy_ts < cut_ts <= restore_ts — RESTORED; продвинутое после старта правило, отсекающее больше overlap_share_max
  прогонов-хитов испытания (rules.overlap_rows), — NO_VERDICT с причиной OVERLAP без чёрного списка.
Испытание закрывается вердиктом (state=closed, verdict, numbers, cut_ts, closed_ts) и перестаёт занимать место в max_parallel. promote:
  правило остаётся trial с ожиданием READY (versions.set_wait), продвижение и сверка уроков — задача 15; reject/inconclusive: versions.reject_rule
  (чёрный список по таблице кодов).
Модуль не импортирует brain/gateway/llm/venues/executor/control.
"""
import hashlib
import json

from tb.knowledge import rulebook_view as RV
from tb.knowledge import store2
from tb.labels import store as LST
from tb.learnloop import clusters as CL
from tb.learnloop import conf as LC
from tb.learnloop import rules as RL
from tb.learnloop import stat as ST
from tb.learnloop import verify as VF
from tb.learnloop import versions as V
from tb.logic import learn_rules as LR
from tb.memory import chain as CH
from tb.memory import lessons as LS

HOUR = CL.HOUR
DAY = CL.DAY
CRIT_VER = 1
STEP = "trial"
OPEN, CLOSED = "open", "closed"
PROMOTE, REJECT, INCONCLUSIVE = "promote", "reject", "inconclusive"
SIDE = "lower"
TRIAL_COLS = ("trial_id", "rule_id", "lesson_id", "env_norm", "criterion", "criterion_h", "policy_id", "policy_ver", "ctx_ver", "start_ts", "deadline_ts",
              "state", "verdict", "numbers", "cut_ts", "closed_ts")
# причины inconclusive в numbers.reason (код правила NO_VERDICT)
R_DEADLINE, R_POLICY_OFF, R_OVERLAP = "DEADLINE", "POLICY_OFF", RL.OVERLAP


# ---------------------------------------------------------------- чтение испытаний

def _j(v):
    if isinstance(v, str) and v[:1] in ("{", "["):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _trial(r):
    d = dict(zip(TRIAL_COLS, r))
    d["criterion"] = _j(d["criterion"]) or {}
    d["numbers"] = _j(d["numbers"])
    return d


def trials(conn, state=None):
    """Строки trials (criterion и numbers разобраны) по trial_id; state — open | closed | None (все)."""
    q, a = "SELECT %s FROM trials" % ", ".join(TRIAL_COLS), []
    if state:
        q += " WHERE state=?"
        a.append(state)
    return [_trial(r) for r in conn.execute(q + " ORDER BY trial_id", a).fetchall()]


def get(conn, trial_id):
    r = conn.execute("SELECT %s FROM trials WHERE trial_id=?" % ", ".join(TRIAL_COLS), (trial_id,)).fetchone()
    return _trial(r) if r else None


def by_rule(conn, rule_id):
    r = conn.execute("SELECT %s FROM trials WHERE rule_id=?" % ", ".join(TRIAL_COLS), (rule_id,)).fetchone()
    return _trial(r) if r else None


def open_count(conn):
    """Число открытых (до вердикта) испытаний: только они занимают место в max_parallel (Requirement 9.5, свойство 26)."""
    return int(conn.execute("SELECT COUNT(*) FROM trials WHERE state=?", (OPEN,)).fetchone()[0])


def promoted(conn):
    """Для задачи 15: закрытые испытания с вердиктом promote, чьё правило ещё trial (ждёт продвижения). -> [{trial, rule}] по trial_id."""
    latest = RV.rules_latest(conn)
    out = []
    for t in trials(conn, CLOSED):
        r = latest.get(t["rule_id"])
        if t["verdict"] == PROMOTE and r is not None and r.get("status") == "trial":
            out.append({"trial": t, "rule": RV.row_rule(r)})
    return out


# ---------------------------------------------------------------- критерий

def crit_hash(crit):
    """criterion_h: 16 hex sha256 канонического JSON критерия (как memory/chain.py; полный hex privacy_scan отклоняет)."""
    return hashlib.sha256(CH.canon(crit).encode("utf-8")).hexdigest()[:16]


def criterion_ok(t):
    """Свойство 11: записанный criterion_h совпадает с хешем записанного критерия."""
    return isinstance(t.get("criterion"), dict) and crit_hash(t["criterion"]) == t.get("criterion_h")


def _active_entry(r):
    return {"rule_id": r.get("rule_id"), "kind": r.get("kind"), "cond": LR.canon_cond(r.get("cond") or []), "scope": r.get("scope"),
            "env_norm": r.get("env_norm")}


def criterion(rule, smp, active, cfg, start_ts, lesson_id=None):
    """Критерий испытания (Requirement 9.2). rule — rules.derive; smp — выборка holdout урока ({policy, policy_ver, ctx_ver, env_norm, a, b, cut_ts, h});
    active — правила действующей версии на момент старта; start_ts — граница часа."""
    t, a = cfg["trial"], float(cfg["verify"]["alpha"])
    start_ts = int(start_ts)
    return {"v": CRIT_VER, "metric": "mean_r", "kind": rule["kind"], "test": "paired", "side": SIDE,
            "src": {"policy": smp["policy"], "policy_ver": int(smp["policy_ver"]), "ctx_ver": smp["ctx_ver"], "env_norm": smp["env_norm"]},
            "cond": LR.canon_cond(rule["cond"]), "cond_h": rule.get("cond_h") or LR.cond_h(rule["cond"], SIDE), "scope": rule["scope"],
            "active": sorted((_active_entry(r) for r in active or ()), key=lambda x: str(x["rule_id"])),
            "n_target": int(t["n_target"]), "alpha": a, "max_parallel": int(t["max_parallel"]), "alpha_adj": a / int(t["max_parallel"]),
            "min_effect_r": float(t["min_effect_r"]), "max_trade_loss_share": float(t["max_trade_loss_share"]),
            "overlap_share_max": float(t["overlap_share_max"]), "max_days": int(t["max_days"]),
            "start_ts": start_ts, "deadline_ts": start_ts + int(t["max_days"]) * DAY,
            "cut": {"cluster_close_max_h": int(cfg["clusters"]["cluster_close_max_h"]), "observe_s": int(cfg["intervals"]["observe_s"])},
            "cfg_h": LC.config_hash(cfg)[:16], "lesson": lesson_id,
            "hold": {"a": smp["a"], "b": smp["b"], "cut_ts": smp["cut_ts"], "h": smp["h"]}}


def _ccfg(crit):
    """Параметры cut_ts из критерия в форме конфига (clusters.cut_ts / last_b / window): после старта конфиг на испытание не влияет."""
    c = crit.get("cut") or {}
    return {"clusters": {"cluster_close_max_h": int(c["cluster_close_max_h"])}, "intervals": {"observe_s": int(c["observe_s"])}}


def trial_rule(crit, rule_id=None):
    """Правило испытания из критерия (для rules.row_hit / overlap_rows)."""
    return {"rule_id": rule_id, "kind": crit["kind"], "cond": crit["cond"], "param": {}, "scope": crit["scope"], "env_norm": crit["src"]["env_norm"]}


# ---------------------------------------------------------------- числа (чистые)

def _rows(win):
    return list(win.get("rows") or []) if isinstance(win, dict) else list(win or [])


def _in_scope(row, scope):
    f = RL.row_feats(row)
    if f.get("side_rel") is None and scope.get("policy") is not None and row.get("policy_id") == scope.get("policy"):
        f["side_rel"] = scope.get("side")
    return LR.scope_ok(f, scope) is True


def analyze(crit, win):
    """Строки выборки окна -> {"tc": кластеры теста по (t0, кластер), "metric": заполненные строки метрик, прошедшие активный набор, "hits": из них
    предельные хиты, "nofill_n", "cut_active": строк отсечено активным набором}. Только t0 >= start_ts и scope критерия (свойство 7)."""
    start, scope = int(crit["start_ts"]), crit.get("scope") or {}
    rule = trial_rule(crit)
    acts = [dict(a, status="active") for a in crit.get("active") or ()]
    kept, cut_n = [], 0
    for r in _rows(win):
        if int(r["t0"]) < start or not _in_scope(r, scope):
            continue
        if any(RL.row_hit(a, r) for a in acts):
            cut_n += 1
            continue
        kept.append(r)
    metric = CL.metric_rows(kept)
    nofill = sum(1 for r in kept if not r.get("filled"))
    hits = sum(1 for r in metric if RL.row_hit(rule, r))
    tc = VF.test_clusters(kept, crit["cond"])
    return {"tc": tc, "metric": len(metric), "hits": hits, "nofill_n": nofill, "cut_active": cut_n}


def _r(x, nd=6):
    return None if x is None else round(float(x), nd)


def verdict(t, win):
    """Чистая функция (Requirement 9.7, 10.1): испытание (criterion) и выборка окна [start_ts, b) -> {"verdict": promote|reject|None, "code", "numbers"}.
    None — кластеров теста меньше n_target. Используются первые n_target кластеров теста по (t0, кластер); средние — по значениям кластеров."""
    crit = t["criterion"] if "criterion" in t else t
    an = analyze(crit, win)
    n_t = int(crit["n_target"])
    loss = (an["hits"] / float(an["metric"])) if an["metric"] else 0.0
    nums = {"n_tc": len(an["tc"]), "n_target": n_t, "n_rows": an["metric"], "n_hits": an["hits"], "nofill_n": an["nofill_n"], "cut_active": an["cut_active"],
            "loss_share": _r(loss), "alpha_adj": crit["alpha_adj"]}
    if isinstance(win, dict):
        nums.update(a=win.get("a"), b=win.get("b"), cut_ts=win.get("cut_ts"), h=win.get("hash"))
    if len(an["tc"]) < n_t:
        return {"verdict": None, "code": None, "numbers": nums}
    tc = an["tc"][:n_t]
    st = ST.paired([x["diff"] for x in tc], SIDE)
    in_mean = ST.mean([x["in_r"] for x in tc])
    base_mean = ST.mean([x["out_r"] for x in tc])
    a_ok = st["p"] is not None and st["p"] < float(crit["alpha_adj"]) and in_mean <= -float(crit["min_effect_r"])
    b_ok = loss <= float(crit["max_trade_loss_share"])
    nums.update(n=st["n"], diff=_r(st["mean"]), sd=_r(st["sd"]), se=_r(st["se"]), t=_r(st["t"]), p=_r(st["p"], 10), in_mean=_r(in_mean),
                base_mean=_r(base_mean), a_ok=bool(a_ok), b_ok=bool(b_ok), last=tc[-1]["cluster"], clusters=[x["cluster"] for x in tc[:5]])
    if a_ok and b_ok:
        return {"verdict": PROMOTE, "code": "READY", "numbers": nums}
    return {"verdict": REJECT, "code": "TRIAL_REJECT", "numbers": nums}


# ---------------------------------------------------------------- выборка испытания

def _win(conn, crit, b, now):
    s = crit["src"]
    return CL.window(conn, s["policy"], s["policy_ver"], s["ctx_ver"], s["env_norm"], crit["start_ts"], b, _ccfg(crit), now)


def _tc_n(crit, win):
    return len(analyze(crit, win)["tc"])


def find_window(conn, t, now):
    """Выборка вердикта (Requirement 9.6, 9.7). -> ("ready", окно минимальной b с n_target кластерами теста) | ("deadline", окно [start_ts, deadline_ts),
    наступил cut_ts(deadline_ts), набора нет) | ("wait", None). Число кластеров теста монотонно по b (строки только добавляются), поэтому b — бинарным
    поиском по границам часа."""
    crit = t["criterion"]
    start, deadline, n_t = int(crit["start_ts"]), int(crit["deadline_ts"]), int(crit["n_target"])
    hi = min(CL.last_b(now, _ccfg(crit)), deadline)
    lo = start + HOUR
    if hi < lo:
        return "wait", None
    w_hi = _win(conn, crit, hi, now)
    if _tc_n(crit, w_hi) < n_t:
        return ("deadline", w_hi) if hi == deadline else ("wait", None)
    best = w_hi
    a, b = lo // HOUR, hi // HOUR - 1            # искомая граница в [lo, hi): часы a..b; hi уже годна
    while a <= b:
        m = (a + b) // 2
        w = _win(conn, crit, m * HOUR, now)
        if _tc_n(crit, w) >= n_t:
            best, b = w, m - 1
        else:
            a = m + 1
    return "ready", best


# ---------------------------------------------------------------- закрытие испытания

def _log(conn, code, ref, body, now, error=False):
    store2.log_event(conn, STEP, code, "code", ref=ref, body=body, error=error, now=now)


def close(conn, t, verdict_, code, nums, now, cfg, cut=None, reason=None):
    """Закрыть испытание вердиктом одной транзакцией: trials (state, verdict, numbers, cut_ts, closed_ts), правило (promote — ожидание READY;
    иначе reject_rule с кодом и чёрным списком по таблице), learn_events. Закрытое испытание не трогается (-> None)."""
    nums = dict(nums or {})
    old = t.get("numbers") if isinstance(t.get("numbers"), dict) else {}
    if old.get("overlap"):
        nums.setdefault("overlap", old["overlap"])
    nums["code"] = code
    if reason:
        nums["reason"] = reason
    with LST.tx(conn):
        cur = get(conn, t["trial_id"])
        if cur is None or cur["state"] != OPEN:
            return None
        store2.update_row(conn, "trials", t["trial_id"], {"state": CLOSED, "verdict": verdict_, "numbers": nums,
                                                          "cut_ts": None if cut is None else int(cut), "closed_ts": int(now)})
        if verdict_ == PROMOTE:
            V.set_wait(conn, t["rule_id"], "READY", now)
        else:
            V.reject_rule(conn, t["rule_id"], code, now, cfg, reason=reason)
        _log(conn, code, t["trial_id"], {"rule": t["rule_id"], "verdict": verdict_, "reason": reason, "n_tc": nums.get("n_tc"), "p": nums.get("p"),
                                         "cut_ts": cut}, now)
    return {"trial_id": t["trial_id"], "rule_id": t["rule_id"], "verdict": verdict_, "code": code, "reason": reason}


def policy_live(root, policy_id, ver):
    """Записанная версия политики включена в config/shadow_policies.json. Реестр не читается -> True (испытание не закрывается из-за сбоя чтения)."""
    from tb.labels import policies as LP
    try:
        pols = LP.load(root)
    except (ValueError, OSError):
        return True
    return any(p["id"] == policy_id and int(p["ver"]) == int(ver) and p.get("enabled") for p in pols)


def overlap_share(conn, t, rule, now):
    """Requirement 9.9: доля прогонов-хитов испытания (hit=1 по его условию), которые отсекает правило rule, по выборке окна cluster_runs записанного
    источника [min(start_ts, b − 7 сут), b), b — последняя граница часа с cut_ts <= now (окно испытания и, пока оно короткое, предыдущие 7 суток)."""
    crit = t["criterion"]
    b = CL.last_b(now, _ccfg(crit))
    a = min(int(crit["start_ts"]), b - 7 * DAY)
    if b <= a:
        return 0.0, 0
    s = crit["src"]
    w = CL.window(conn, s["policy"], s["policy_ver"], s["ctx_ver"], s["env_norm"], a, b, _ccfg(crit), now)
    tr = trial_rule(crit, t["rule_id"])
    return RL.overlap_rows(w, tr, dict(rule, status="active")), len(RL.hit_keys(w, tr))


def check_overlaps(conn, cfg, now, rules=None):
    """Пересечение по строкам с правилами, продвинутыми после старта испытаний (активные правила вне записанного набора критерия). Каждое правило
    проверяется для испытания один раз (numbers.overlap); доля выше overlap_share_max — inconclusive NO_VERDICT (OVERLAP) без чёрного списка.
    rules — активные правила (по умолчанию действующая версия); задача 15 может вызвать сразу после продвижения. -> [закрытия]."""
    act = V.active_rules(conn) if rules is None else list(rules)
    out = []
    for t in trials(conn, OPEN):
        crit = t["criterion"]
        if (V.rule(conn, t["rule_id"]) or {}).get("status") != "trial":
            continue
        known = set(a["rule_id"] for a in crit.get("active") or ())
        seen = dict(((t.get("numbers") or {}).get("overlap") or {}))
        new = [r for r in act if r.get("rule_id") not in known and r.get("rule_id") not in seen and r.get("rule_id") != t["rule_id"]]
        if not new:
            continue
        hit = None
        for r in sorted(new, key=lambda x: str(x["rule_id"])):
            share, n = overlap_share(conn, t, r, now)
            seen[r["rule_id"]] = {"share": _r(share, 4), "n": n}
            if share > float(crit["overlap_share_max"]) and hit is None:
                hit = r["rule_id"]
        if hit is not None:
            res = close(conn, dict(t, numbers={"overlap": seen}), INCONCLUSIVE, "NO_VERDICT", {"overlap_rule": hit}, now, cfg, reason=R_OVERLAP)
            if res:
                out.append(res)
        else:
            store2.update_row(conn, "trials", t["trial_id"], {"numbers": dict(t.get("numbers") or {}, overlap=seen)})
    return out


def step_trial(conn, t, cfg, now, live=True):
    """Один проход по открытому испытанию. -> закрытие или None (продолжается)."""
    crit = t["criterion"]
    s = crit["src"]
    r = V.rule(conn, t["rule_id"])
    if r is None or r.get("status") != "trial":
        return None                                    # правило закрыто другим путём (владелец, задача 16)
    if not live:
        return close(conn, t, INCONCLUSIVE, "NO_VERDICT", {}, now, cfg, reason=R_POLICY_OFF)
    if CL.ctx_changed_since(conn, s["policy"], s["policy_ver"], s["env_norm"], s["ctx_ver"], crit["start_ts"]):
        return close(conn, t, INCONCLUSIVE, "CTX_CHANGED", {}, now, cfg)
    st, win = find_window(conn, t, now)
    if st == "wait":
        return None
    if CL.restored(conn, win["cut_ts"]):
        return close(conn, t, INCONCLUSIVE, "RESTORED", {"a": win["a"], "b": win["b"], "cut_ts": win["cut_ts"]}, now, cfg, cut=win["cut_ts"])
    v = verdict(t, win)
    if st == "deadline" or v["verdict"] is None:
        return close(conn, t, INCONCLUSIVE, "NO_VERDICT", v["numbers"], now, cfg, cut=win["cut_ts"], reason=R_DEADLINE)
    return close(conn, t, v["verdict"], v["code"], v["numbers"], now, cfg, cut=win["cut_ts"])


def run_verdicts(conn, cfg, now, live=None):
    """Часовой шаг вердиктов: пересечения с продвинутыми правилами, затем каждое открытое испытание (step_trial). live(policy_id, ver) -> bool.
    -> [закрытия]."""
    now = int(now)
    out = check_overlaps(conn, cfg, now)
    for t in trials(conn, OPEN):
        s = t["criterion"].get("src") or {}
        res = step_trial(conn, t, cfg, now, True if live is None else live(s.get("policy"), s.get("policy_ver")))
        if res:
            out.append(res)
    return out


# ---------------------------------------------------------------- регистрация

def hold_sample(lesson):
    """Выборка holdout урока меток в форме clusters.sample: {policy, policy_ver, ctx_ver, env_norm, a, b, cut_ts, h} или None."""
    s = ((lesson or {}).get("evidence") or {}).get("sample") or {}
    h = s.get("hold") or {}
    if not all(k in s for k in ("policy", "policy_ver", "ctx_ver", "env_norm")) or not all(k in h for k in ("a", "b", "cut_ts", "h")):
        return None
    return {"policy": s["policy"], "policy_ver": int(s["policy_ver"]), "ctx_ver": s["ctx_ver"], "env_norm": s["env_norm"], "a": int(h["a"]), "b": int(h["b"]),
            "cut_ts": int(h["cut_ts"]), "h": h["h"]}


def _lesson_rules(conn):
    return set(r.get("from_lesson") for r in RV.rules_latest(conn).values())


def reach(conn, rule, smp, active, cfg, now):
    """Достижимость (Requirement 9.4): кластеры теста (предельные хиты и база при активном наборе) за последние reach_lookback_days выборки окна
    источника урока. -> (дней до n_target, достижимо)."""
    days = int(cfg["trial"]["reach_lookback_days"])
    b = CL.last_b(now, cfg)
    w = CL.window(conn, smp["policy"], smp["policy_ver"], smp["ctx_ver"], smp["env_norm"], b - days * DAY, b, cfg, now)
    d = RL.estimate_reach(w, rule, cfg, active=active, lookback_days=days)
    return d, RL.reachable(d, cfg)


def _reject_unreachable(conn, rule, days, now, cfg):
    """Правило rejected (UNREACHABLE_N) без испытания и чёрный список одной транзакцией (Requirement 9.4). Правило урока уже есть -> None."""
    with LST.tx(conn):
        if rule["from_lesson"] in _lesson_rules(conn):
            return None
        rid = V.next_rule_id(conn)
        body = {"cond": LR.canon_cond(rule["cond"]), "param": {}, "scope": rule["scope"], "cond_h": rule["cond_h"], "policy_ver": rule.get("policy_ver"),
                "trial_id": None, "reach_days": None if days == float("inf") else _r(days, 2)}
        store2.append_row(conn, "rule_rows", {"rule_id": rid, "rev": 1, "kind": rule["kind"], "status": "rejected", "wait": None, "wait_since_ts": None,
                                              "from_lesson": rule["from_lesson"], "env_norm": rule.get("env_norm") or "mainnet", "rulebook_ver_in": None,
                                              "rulebook_ver_out": None, "reason_code": "UNREACHABLE_N", "body": body, "ts": store2.iso(now)})
        RL.block(conn, rule["cond_h"], "UNREACHABLE_N", now, cfg, backup=False)
        _log(conn, "UNREACHABLE_N", rid, {"lesson": rule["from_lesson"], "reach_days": body["reach_days"]}, now)
    return rid


def _reject_lesson(root, lesson, code, now, mcfg):
    try:
        LS.transition(root, lesson["lesson_id"], "rejected", code, now, cfg=mcfg)
    except ValueError:              # урок уже не hyp (повтор или действие владельца)
        return False
    return True


def register(conn, rule, lesson, cfg, now, active=None, smp=None):
    """Правило trial и испытание одной транзакцией (versions.create_with_trial) с критерием на активном наборе момента старта.
    start_ts — следующая граница часа (в испытание идут только будущие кластеры, окна не пересекаются с holdout). -> {"rule_id", "trial_id", "criterion_h"}.
    RuleRefused (DUP_RULE | BLOCKED | BAD_RULE) пробрасывается."""
    now = int(now)
    smp = smp or hold_sample(lesson)
    active = V.active_rules(conn) if active is None else active
    start = CL.hour_ceil(now)
    crit = criterion(rule, smp, active, cfg, start, lesson.get("lesson_id"))
    ch = crit_hash(crit)
    cols = {"criterion": crit, "criterion_h": ch, "policy_id": smp["policy"], "policy_ver": int(smp["policy_ver"]), "ctx_ver": smp["ctx_ver"],
            "start_ts": crit["start_ts"], "deadline_ts": crit["deadline_ts"], "env_norm": smp["env_norm"], "state": OPEN}
    with LST.tx(conn):
        res = V.create_with_trial(conn, dict(rule, env_norm=smp["env_norm"]), cols, now, cfg)
        _log(conn, "TRIAL", res["trial_id"], {"rule": res["rule_id"], "lesson": lesson.get("lesson_id"), "start_ts": crit["start_ts"],
                                              "deadline_ts": crit["deadline_ts"], "criterion_h": ch, "active": [a["rule_id"] for a in crit["active"]]}, now)
    return dict(res, criterion_h=ch)


def register_pending(conn, root, cfg, now, policy=None, mcfg=None, live=None):
    """Регистрация испытаний для уроков меток hyp без правил (Requirements 9.1, 9.4, 9.5). Уроки по (created_ts, lesson_id).
    live(policy_id, ver) -> bool (по умолчанию policy_live по root). -> {"registered", "waiting", "rejected": [(lesson_id, код)], "skipped"}."""
    now = int(now)
    if mcfg is None:
        from tb.memory import cfg as memcfg
        mcfg = memcfg.load(root)
    live = live or (lambda pid, ver: policy_live(root, pid, ver))
    rep = {"registered": [], "waiting": [], "rejected": [], "skipped": 0}
    has_rule = _lesson_rules(conn)
    ls = [l for l in LS.read_all(root).values() if LS.is_labels(l) and l.get("status") == "hyp" and l["lesson_id"] not in has_rule]
    ls.sort(key=lambda l: (int(l.get("created_ts") or 0), l["lesson_id"]))
    free = int(cfg["trial"]["max_parallel"]) - open_count(conn)
    wait_s = int(cfg["trial"]["wait_max_days"]) * DAY
    active = None
    for l in ls:
        why = RL.derive_reason(l, conn, now)
        if why in ("NOT_LABELS", "NOT_HYP", "NOT_SKIP", "BAD_COND"):
            rep["skipped"] += 1
            continue
        smp = hold_sample(l)
        if smp is None:
            rep["skipped"] += 1
            continue
        code = None
        try:
            chk = CL.check_sample(conn, smp, cfg, now)
        except CL.WindowNotReady:
            rep["waiting"].append(l["lesson_id"])
            continue
        if chk != "OK":
            code = chk
        elif CL.ctx_changed_since(conn, smp["policy"], smp["policy_ver"], smp["env_norm"], smp["ctx_ver"], smp["a"]):
            code = "CTX_CHANGED"
        elif not live(smp["policy"], smp["policy_ver"]):
            code = "NO_VERDICT"
        if code:
            ch = LR.cond_h(l["cond"], SIDE)
            with LST.tx(conn):
                RL.block(conn, ch, code, now, cfg, backup=False)
                _log(conn, code, l["lesson_id"], {"stage": "register"}, now, error=code == "SAMPLE_CHANGED")
            if _reject_lesson(root, l, code, now, mcfg):
                rep["rejected"].append((l["lesson_id"], code))
            continue
        if why == "BLOCKED" or free <= 0:                  # ждёт регистрации; дольше wait_max_days с создания урока — BUDGET
            if now - int(l.get("created_ts") or now) > wait_s:
                with LST.tx(conn):
                    _log(conn, "BUDGET", l["lesson_id"], {"stage": "register", "blocked": why == "BLOCKED"}, now)
                if _reject_lesson(root, l, "BUDGET", now, mcfg):
                    rep["rejected"].append((l["lesson_id"], "BUDGET"))
            else:
                rep["waiting"].append(l["lesson_id"])
            continue
        pol = policy if policy and policy.get("id") == smp["policy"] and int(policy.get("ver") or 0) == smp["policy_ver"] else _policy_of(root, smp)
        rule = RL.derive(l, pol, conn, now, env_norm=smp["env_norm"])
        if rule is None:
            rep["skipped"] += 1
            continue
        if active is None:
            active = V.active_rules(conn)
        days, ok = reach(conn, rule, smp, active, cfg, now)
        if not ok:
            if _reject_unreachable(conn, rule, days, now, cfg) is not None and _reject_lesson(root, l, "UNREACHABLE_N", now, mcfg):
                rep["rejected"].append((l["lesson_id"], "UNREACHABLE_N"))
            continue
        try:
            res = register(conn, rule, l, cfg, now, active=active, smp=smp)
        except V.RuleRefused as e:
            if e.code == "BLOCKED":
                rep["waiting"].append(l["lesson_id"])
            else:
                rep["skipped"] += 1
            continue
        free -= 1
        rep["registered"].append(res["trial_id"])
    return rep


def _policy_of(root, smp):
    """Запись политики источника урока (scope правила) из реестра; нет записи -> политика проверки по конфигу."""
    from tb.labels import policies as LP
    try:
        for p in LP.load(root):
            if p["id"] == smp["policy"] and int(p["ver"]) == int(smp["policy_ver"]):
                return p
    except (ValueError, OSError):
        pass
    try:
        return CL.load_policy(root)
    except (ValueError, OSError, LC.ConfError):
        return None


# ---------------------------------------------------------------- шаги цикла

def step_register(ctx):
    """Суточный шаг register (после holdouts того же прохода)."""
    rep = register_pending(ctx.conn, ctx.root, ctx.cfg, ctx.now, policy=_ctx_policy(ctx), mcfg=ctx.mcfg,
                           live=lambda pid, ver: policy_live(ctx.root, pid, ver))
    return {"code": "OK", "registered": len(rep["registered"]), "waiting": len(rep["waiting"]), "rejected": len(rep["rejected"])}


def step_verdicts(ctx):
    """Часовой шаг verdicts: пересечения, вердикты и закрытия; затем регистрация ждущих уроков на освободившиеся места (проверка в каждом
    часовом проходе, Requirement 9.5)."""
    live = lambda pid, ver: policy_live(ctx.root, pid, ver)  # noqa: E731
    closed = run_verdicts(ctx.conn, ctx.cfg, ctx.now, live=live)
    rep = register_pending(ctx.conn, ctx.root, ctx.cfg, ctx.now, policy=_ctx_policy(ctx), mcfg=ctx.mcfg, live=live)
    return {"code": "OK", "closed": len(closed), "registered": len(rep["registered"]), "waiting": len(rep["waiting"]), "rejected": len(rep["rejected"])}


def _ctx_policy(ctx):
    try:
        return ctx.policy
    except ValueError:
        return None
