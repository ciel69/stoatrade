"""Справочные уроки мозга, заморозка продвижения, условие по нарушениям (R2 вторая итерация, задача 31; Requirements 18.1, 18.2, 18.4–18.6;
design.md, «config/learnloop.json», блок second_iteration).

Справочные уроки (шаг brain_ref, раз в сутки):
  memory.lessons.propose по пулу сделок мозга с порогами second_iteration.brain_ref.min_n (15) и min_diff_r (0.5) — в propose передаётся копия
  config/memory.json с подставленными lessons.min_n_propose / min_diff_r (файл не меняется); каждый кандидат -> lessons.create(created_by="code",
  ref=True): evidence.src="brain", evidence.ref=true. Бюджет новых уроков в сутки — lessons.max_new_per_day (его считает propose). Справочные
  уроки правил не порождают (испытания регистрируются только для уроков меток) и мозгу не показываются (lessons.applicable и списки уроков).
  Пул — lessons.pool (фильтр lessons.pool_mode: null | "paper").

Справочная проверка кандидата модели с непроверяемым на метках признаком (Requirement 18.2; признаки lessons.FEATURES вне RULE_FEATURES):
  выборка мозга — закрытые сделки режима paper за последние brain_ref.brain_window_days (30) суток; урок evidence.src="brain", ref=true,
  via="llm" создаётся lessons.create при n >= lessons.min_n_propose и отклонении от базы >= lessons.min_diff_r в направлении claim (conf не
  выше conf_cap_reviewer); иначе кандидат закрывается N_TOO_SMALL. Словарь признаков для модели — ref_dictionary (значения считает код).

Заморозка продвижения (Requirement 18.5; дополнительное условие versions.promotion_ready):
  сделок мозга за последние freeze_window_days (7) суток < freeze_trade_share (0.5) x среднее за такой же срок по предыдущим 2 x 7 суткам при
  захваченных сигналах tradable=1 за те же 7 суток >= freeze_signal_share (0.8) x их среднего -> frozen. История сделок мозга короче 3 x 7 (21)
  суток -> не вычисляется (known=false, freeze_unknown) и продвижение не блокирует. Вердикты испытаний не теряются: правило ждёт с wait=READY.

Условие по нарушениям (Requirement 18.6; критерий испытания, проверяется при вердикте promote):
  записи decisions kind="note", what="rule_eval" в окне испытания [start_ts, b), где правило испытания есть в basis.shadow_hits и план принят
  (basis.plan) -> сделки мозга (memory) по refs.plan или refs.event_seq; доля нарушений (tags.err кроме good и good-process-bad-luck или
  tags.rule_followed="no") у таких сделок не выше, чем у остальных сделок мозга того же окна. Сделок меньше brain_hits_min (30) -> not_evaluable;
  записи с принятыми планами есть, но ни одна не связана со случаем -> not_evaluable_link; оба не блокируют продвижение. Иначе ok | fail.

Модуль не импортирует brain/gateway/llm/venues/executor/control и ничего не пишет в config/.
"""
import copy

from tb.learnloop import menu as MN
from tb.logic import learn_cond as LCD
from tb.memory import agg
from tb.memory import cases as MC
from tb.memory import lessons as LS
from tb.memory import similar

DAY = 86400
STEP = "brain_ref"
REF_OK, N_TOO_SMALL, DUPLICATE = "REF_LESSON", "N_TOO_SMALL", "DUPLICATE"
V_OK, V_FAIL, V_NOT_EVALUABLE, V_NOT_EVALUABLE_LINK = "ok", "fail", "not_evaluable", "not_evaluable_link"
GOOD_ERR = ("good", "good-process-bad-luck")
# признаки словаря уроков, которых нет на метках (Requirement 5.1): проверяются только по выборке мозга
UNVERIFIABLE = tuple(f for f in LS.FEATURES if f not in LCD.RULE_FEATURES)
Q_TOKENS = (("q20", 0.2), ("q80", 0.8))


def _si(cfg):
    return (cfg or {}).get("second_iteration") or {}


def _brain_ref(cfg):
    return _si(cfg).get("brain_ref") or {}


# ---------------------------------------------------------------- сделки мозга

def _ts(c):
    return similar.ts_epoch(c.get("ts"))


def is_brain_trade(c, mcfg):
    """Сделка мозга: случай type=trade, src.actor=agent, участвует в статистике (cases.eligible), режим — lessons.pool_mode (если задан)."""
    src = c.get("src") or {}
    if c.get("type") != "trade" or src.get("actor") != "agent" or not MC.eligible(c, mcfg):
        return False
    md = LS.pool_mode(mcfg)
    return md is None or src.get("mode") == md


def brain_trades(all_cases, mcfg, a=None, b=None, closed=False):
    out = []
    for c in all_cases or ():
        if not is_brain_trade(c, mcfg):
            continue
        t = _ts(c)
        if t is None or (a is not None and t < a) or (b is not None and t >= b):
            continue
        if closed and agg.closed_r(c) is None:
            continue
        out.append(c)
    return out


def _load_cases(root, all_cases):
    if all_cases is not None:
        return all_cases
    return MC.load(root)


def _load_mcfg(root, mcfg):
    if mcfg is not None:
        return mcfg
    from tb.memory import cfg as memcfg
    return memcfg.load(root)


# ---------------------------------------------------------------- справочные уроки (шаг brain_ref)

def ref_cfg(mcfg, cfg):
    """Копия config/memory.json с порогами справочных уроков: lessons.min_n_propose = brain_ref.min_n, lessons.min_diff_r = brain_ref.min_diff_r."""
    br = _brain_ref(cfg)
    out = copy.deepcopy(mcfg or {})
    lc = out.setdefault("lessons", {})
    lc["min_n_propose"] = int(br["min_n"])
    lc["min_diff_r"] = float(br["min_diff_r"])
    return out


def run_brain_ref(root, now, cfg, mcfg=None, all_cases=None, dry_run=False):
    """Суточный шаг справочных уроков. -> {"code": OK | NO_DATA, "made": [lesson_id], "cands": n, "pool": n}."""
    now = int(now)
    mcfg = _load_mcfg(root, mcfg)
    all_cases = _load_cases(root, all_cases)
    rc = ref_cfg(mcfg, cfg)
    pl = LS.pool(all_cases, rc)
    rep = {"code": "OK", "made": [], "cands": 0, "pool": len(pl), "refused": []}
    if len(pl) < rc["lessons"]["min_n_propose"]:
        rep["code"] = "NO_DATA"
        return rep
    cands = LS.propose(root, all_cases, rc, now)
    rep["cands"] = len(cands)
    if dry_run:
        return rep
    for c in cands:
        try:
            l = LS.create(root, c["cond"], c["claim"], "code", all_cases, rc, now, ref=True)
        except ValueError as e:                    # privacy, BUDGET и прочие отказы записи — урок не создаётся, шаг продолжается
            rep["refused"].append(type(e).__name__)
            continue
        rep["made"].append(l["lesson_id"])
    return rep


def step_brain_ref(ctx):
    """Суточный шаг цикла brain_ref (регистрация — в cycle.default_steps)."""
    rep = run_brain_ref(ctx.root, ctx.now, ctx.cfg, mcfg=ctx.mcfg)
    return {"code": rep["code"], "made": len(rep["made"]), "cands": rep["cands"], "pool": rep["pool"]}


# ---------------------------------------------------------------- справочная проверка кандидата модели (Requirement 18.2)

def brain_sample(all_cases, mcfg, cfg, now):
    """Выборка мозга справочной проверки: закрытые сделки режима paper за brain_window_days суток до now. -> (случаи, cfg пула)."""
    days = int(_brain_ref(cfg)["brain_window_days"])
    pcfg = copy.deepcopy(mcfg or {})
    pcfg.setdefault("lessons", {})["pool_mode"] = "paper"
    lo = int(now) - days * DAY
    sel = [c for c in all_cases or () if (_ts(c) or 0) >= lo and (_ts(c) or 0) < int(now)]
    return sel, pcfg


def ref_dictionary(root, now, cfg, mcfg=None, all_cases=None):
    """Словарь непроверяемых на метках признаков для кандидатов модели: категориальные — значения словаря уроков без unknown/none (операторы =, in),
    логические — true/false (=), числовые — пороги-токены q20/q80 по выборке мозга (значения считает код; признак без значений не входит)."""
    mcfg = _load_mcfg(root, mcfg)
    sel, pcfg = brain_sample(_load_cases(root, all_cases), mcfg, cfg, now)
    pl = LS.pool(sel, pcfg)
    out = {}
    for f in UNVERIFIABLE:
        if f in LS.CAT_FEATURES:
            out[f] = {"ops": ["=", "in"], "values": [v for v in LS.CAT_FEATURES[f] if v not in ("unknown", "none")]}
        elif f in LS.BOOL_FEATURES:
            out[f] = {"ops": ["="], "values": [True, False]}
        else:
            xs = [x for x in (similar.vector(c).get(f) for c in pl) if LCD.is_num(x)]
            if len(xs) >= int(((mcfg or {}).get("lessons") or {}).get("min_n_propose", 4)):
                out[f] = {"ops": [">=", "<="], "values": dict((t, MN._qv(MN.quantile(xs, q))) for t, q in Q_TOKENS)}
    return out


def check_candidate(root, cond, claim, now, cfg, mcfg=None, all_cases=None, via="llm", dry_run=False):
    """Справочная проверка кандидата с непроверяемым признаком по выборке мозга. -> {"code": REF_LESSON | N_TOO_SMALL | DUPLICATE, "lesson_id",
    "n", "diff"}. Пороги — lessons.min_n_propose и min_diff_r из config/memory.json."""
    mcfg = _load_mcfg(root, mcfg)
    lc = mcfg.get("lessons") or {}
    sel, pcfg = brain_sample(_load_cases(root, all_cases), mcfg, cfg, now)
    pl = LS.pool(sel, pcfg)
    m = [c for c in pl if LS.matches(cond, c)]
    out = {"code": N_TOO_SMALL, "lesson_id": None, "n": len(m), "diff": None}
    if not m or not pl:
        return out
    base = sum(c["result"]["r"] for c in pl) / len(pl)
    diff = sum(c["result"]["r"] for c in m) / len(m) - base
    out["diff"] = round(diff, 4)
    lower = (claim or {}).get("dir", "lower") == "lower"
    if len(m) < int(lc.get("min_n_propose", 4)) or abs(diff) < float(lc.get("min_diff_r", 0.5)) or (diff >= 0 if lower else diff <= 0):
        return out
    key = LS._cond_set(cond)
    if any(LS._cond_set(l["cond"]) == key for l in LS.read_all(root).values() if l.get("status") in LS.ACTIVE):
        out["code"] = DUPLICATE
        return out
    if dry_run:
        out["code"] = REF_OK
        return out
    cl = {"metric": "mean_r", "dir": "lower" if lower else "higher", "vs": "base", "effect": round(min(abs(diff), 10.0), 2)}
    l = LS.create(root, cond, cl, "code", sel, pcfg, now, ref=True, via=via)
    out.update(code=REF_OK, lesson_id=l["lesson_id"])
    return out


# ---------------------------------------------------------------- заморозка продвижения (Requirement 18.5)

def _signals(conn, a, b):
    row = conn.execute("SELECT COUNT(*) FROM signals WHERE tradable=1 AND t0>=? AND t0<?", (int(a), int(b))).fetchone()
    return int(row[0] or 0)


def freeze(conn, root, cfg, now, all_cases=None, mcfg=None):
    """-> {"known", "frozen", "trades", "trades_avg", "signals", "signals_avg", "why"}. known=false (история короче 3 x freeze_window_days суток,
    нет корня или ошибка чтения) — заморозка не вычислена и продвижение не блокирует."""
    fz = _si(cfg).get("freeze") or {}
    now = int(now)
    out = {"known": False, "frozen": False, "trades": None, "trades_avg": None, "signals": None, "signals_avg": None, "why": None}
    if root is None or not fz:
        out["why"] = "NO_ROOT" if root is None else "NO_CFG"
        return out
    w = int(fz["freeze_window_days"]) * DAY
    try:
        mcfg = _load_mcfg(root, mcfg)
        tr = brain_trades(_load_cases(root, all_cases), mcfg)
    except (OSError, ValueError, KeyError, TypeError) as e:
        out["why"] = "CASES:" + type(e).__name__
        return out
    first = min((_ts(c) for c in tr), default=None)
    if first is None or now - first < 3 * w:
        out["why"] = "SHORT_HISTORY"
        return out
    cur = sum(1 for c in tr if now - w <= _ts(c) < now)
    prev = sum(1 for c in tr if now - 3 * w <= _ts(c) < now - w) / 2.0
    try:
        s_cur = _signals(conn, now - w, now)
        s_prev = _signals(conn, now - 3 * w, now - w) / 2.0
    except Exception as e:  # noqa: BLE001 - нет таблицы signals или база недоступна: заморозка не вычислена
        out["why"] = "SIGNALS:" + type(e).__name__
        return out
    out.update(known=True, trades=cur, trades_avg=prev, signals=s_cur, signals_avg=s_prev)
    out["frozen"] = bool(prev > 0 and cur < float(fz["freeze_trade_share"]) * prev and s_cur >= float(fz["freeze_signal_share"]) * s_prev)
    out["why"] = "FROZEN" if out["frozen"] else "OK"
    return out


# ---------------------------------------------------------------- условие по нарушениям (Requirement 18.6)

def is_violation(c):
    t = c.get("tags") or {}
    errs = [e for e in (t.get("err") or []) if e not in GOOD_ERR]
    return bool(errs) or t.get("rule_followed") == "no"


def shadow_records(root, rule_id, a, b):
    """Записи rule_eval журнала решений с rule_id в basis.shadow_hits и t в [a, b). -> [{"plan", "event_seq", "t"}]."""
    from tb.core import decisions as DJ
    out = []
    for ln in DJ._iter(root):
        if b'"shadow_hits"' not in ln or b'"rule_eval"' not in ln:
            continue
        r = DJ._parse(ln)
        if r is None or r.get("kind") != "note" or r.get("what") != "rule_eval":
            continue
        bs = r.get("basis") if isinstance(r.get("basis"), dict) else {}
        hits = bs.get("shadow_hits")
        t = r.get("t")
        if not isinstance(hits, list) or rule_id not in hits or not isinstance(t, (int, float)) or isinstance(t, bool) or not (a <= t < b):
            continue
        es = bs.get("event_seq")
        out.append({"plan": bs.get("plan") or r.get("plan"), "event_seq": es if isinstance(es, int) and not isinstance(es, bool) else None, "t": t})
    return out


def violations(root, rule_id, a, b, cfg, mcfg=None, all_cases=None, records=None):
    """Условие по нарушениям для правила испытания rule_id в окне [a, b). -> {"code": ok | fail | not_evaluable | not_evaluable_link, "n",
    "n_rec", "n_plan", "share", "share_rest", "n_rest", "min"}."""
    hmin = int((_si(cfg).get("violations") or {}).get("brain_hits_min", 30))
    recs = shadow_records(root, rule_id, a, b) if records is None else records
    mcfg = _load_mcfg(root, mcfg)
    tr = brain_trades(_load_cases(root, all_cases), mcfg, a, b, closed=True)
    by_plan = dict(((c.get("refs") or {}).get("plan"), c) for c in tr if (c.get("refs") or {}).get("plan"))
    by_ev = dict(((c.get("refs") or {}).get("event_seq"), c) for c in tr if (c.get("refs") or {}).get("event_seq") is not None)
    planned = [r for r in recs if r.get("plan")]
    linked = {}
    for r in planned:
        c = by_plan.get(r["plan"]) or (by_ev.get(r["event_seq"]) if r.get("event_seq") is not None else None)
        if c is not None:
            linked[c["case_id"]] = c
    out = {"code": V_NOT_EVALUABLE, "n": len(linked), "n_rec": len(recs), "n_plan": len(planned), "share": None, "share_rest": None, "n_rest": None,
           "min": hmin}
    if planned and not linked:
        out["code"] = V_NOT_EVALUABLE_LINK
        return out
    if len(linked) < hmin:
        return out
    rest = [c for c in tr if c["case_id"] not in linked]
    sh = sum(1 for c in linked.values() if is_violation(c)) / float(len(linked))
    sr = (sum(1 for c in rest if is_violation(c)) / float(len(rest))) if rest else 0.0
    out.update(share=round(sh, 4), share_rest=round(sr, 4), n_rest=len(rest), code=V_OK if sh <= sr else V_FAIL)
    return out
