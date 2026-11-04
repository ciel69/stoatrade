"""Каркас цикла обучения R2 (задача 10; Requirements 2.6, 2.11, 10.6, 15.1, 15.2, 15.4, 15.5, 15.6; design.md, «Автономность и автопродвижение»,
«Надзор за надзором», «Записи rule_eval», «config/learnloop.json»).

Реестр шагов (default_steps) — список Step(name, period, fn): period
  pass      каждый проход                         rule_eval_import (перенос записей decisions kind="note", what="rule_eval" в rule_evals),
                                                  llm_collect (ответы порта заданий модели, задача 27)
  observe   раз в intervals.observe_s (15 мин)     close_clusters (сигнал runtime/knowledge.corrupt проверяет сервис перед шагами observe)
  daily     первый проход после daily_after UTC     integrity, backup, menu, accept, holdouts (уроки hyp), register (испытания, задача 14),
                                                  day_review (задание роли day_reviewer или LLM_FALSE, задача 27)
  hourly    раз в intervals.<interval>              verdicts (14), promote (15), bounds, reconcile (15), watch (16), hypref (36)
Проход выполняется, когда наступил срок хотя бы одного шага observe/daily/hourly. Шаг — fn(ctx) -> dict (code: OK, MENU, NO_MENU, NOT_DUE,
NO_DATA, STUB ...). Исключение шага -> STEP_ERROR (ChainBroken -> CHAIN_BROKEN) в learn_events и следующий шаг; повреждение по
store2.is_corruption -> Corruption (проход прерывается, сервис запускает протокол восстановления). Заглушка шага другой задачи — stub(...)
(stub=True; шаг watch с заглушкой не включает проверку WATCH_STALE); шаги задач 14–16 и 36 настоящие, заглушек в default_steps нет.

Учёт суток (Requirement 15.6): работа сервиса копится по часам суток UTC (промежуток между тиками не больше 2 x observe_s, иначе платформа
была выключена — не засчитывается); при смене суток пишется сводка learn_events step="day" (counted: работа >= day_min_work_h, full: counted,
все суточные шаги выполнены или пропущены с кодом и observe успешен в >= observe_ok_share часов работы). Текущие сутки и состояние шагов —
meta.learnloop_day и meta.learnloop_state (versions.META_*). Условия готовности продвижения — versions.promotion_ready.
Модуль не импортирует brain/gateway/llm/venues/executor/control.
"""
import datetime
import os
import time

from tb import paths
from tb.core import decisions as DJ
from tb.core.filelock import FileLock
from tb.knowledge import rulebook_view as RV
from tb.knowledge import schema2 as S2
from tb.knowledge import store2
from tb.labels import store as LST
from tb.learnloop import candidates as CA
from tb.learnloop import clusters as CL
from tb.learnloop import hypref as HR
from tb.learnloop import promote as PR
from tb.learnloop import reconcile as RC
from tb.learnloop import retro as RT
from tb.learnloop import brainref as BR
from tb.learnloop import trial as TR
from tb.learnloop import versions as V
from tb.learnloop import watch as WA

HOUR = 3600
DAY = 86400
PERIODS = ("pass", "observe", "daily", "hourly")
SKIP_CODES = ("NO_MENU", "NOT_DUE", "NO_DATA")
STUB = "STUB"
ERROR = "ERROR"
STEP_ERROR = S2.STEP_ERROR
CHAIN_BROKEN = S2.CHAIN_BROKEN
DAILY_RETRY_S = 3600                     # упавший суточный шаг повторяется не чаще раза в час
GAP_FACTOR = 2                           # промежуток между тиками до GAP_FACTOR x observe_s считается работой
META_CURSOR = "rule_eval_cursor"
RULE_EVAL_WHAT = "rule_eval"
NO_EVENT_WINDOW_S = 7 * DAY
AUTO_RESTORED_S = S2.AUTO_RESTORED_YELLOW_S
YELLOW = ("RULES_NO_EVENT", "WARMUP_STUCK", "AUTO_RESTORED")
RED = ("WATCH_STALE", "CHAIN_BROKEN", "DB_CORRUPT")


class Corruption(Exception):
    """Повреждение базы, замеченное шагом цикла (store2.is_corruption): проход прерван, нужен протокол восстановления."""

    def __init__(self, where, err):
        self.where, self.err = where, err
        Exception.__init__(self, "DB_CORRUPT в %s: %s: %s" % (where, type(err).__name__, err))


class Step(object):
    def __init__(self, name, period, fn, interval=None, group=None, stub=False, task=None):
        if period not in PERIODS:
            raise ValueError(period)
        self.name, self.period, self.fn = name, period, fn
        self.interval = interval            # ключ intervals.* для hourly
        self.group = group                  # observe | verdict | watch: серии STEP_ERROR (promotion_ready)
        self.stub, self.task = stub, task

    def __repr__(self):
        return "Step(%s, %s%s)" % (self.name, self.period, ", stub" if self.stub else "")


def stub(task):
    """Заглушка шага другой задачи: ничего не делает, код STUB (для учёта суток — выполнен)."""
    def fn(ctx):
        return {"code": STUB, "task": task}
    return fn


class Ctx(object):
    """Контекст шага: root, conn, cfg, now, beat() (heartbeat между долгими частями), policy и mcfg (лениво)."""

    def __init__(self, root, conn, cfg, now, policy=None, mcfg=None, beat=None, cycle=None):
        self.root, self.conn, self.cfg, self.now = root, conn, cfg, int(now)
        self._policy, self._mcfg = policy, mcfg
        self.beat = beat or (lambda: None)
        self.cycle = cycle

    @property
    def policy(self):
        if self._policy is None:
            self._policy = CL.load_policy(self.root, self.cfg)
        return self._policy

    @property
    def mcfg(self):
        if self._mcfg is None:
            from tb.memory import cfg as memcfg
            self._mcfg = memcfg.load(self.root)
        return self._mcfg


# ---------------------------------------------------------------- шаги

def step_rule_eval_import(ctx):
    return import_rule_evals(ctx.conn, ctx.root)


def step_close_clusters(ctx):
    return {"code": "OK", "n": CL.close_clusters(ctx.conn, ctx.now, ctx.cfg, policy=ctx.policy, mcfg=ctx.mcfg, root=ctx.root)}


def step_integrity(ctx):
    """Суточный integrity_check (провал -> DbCorruptError -> протокол), сброс счётчика событий за прошлые сутки, wal_checkpoint(TRUNCATE)."""
    store2.check_integrity(ctx.conn)
    if ctx.cycle is not None:
        ctx.cycle.state["integrity"] = {"ts": ctx.now, "ok": True}
    store2.flush_events(ctx.conn, ctx.now)
    try:
        store2.checkpoint(ctx.conn, "TRUNCATE")
    except Exception:  # noqa: BLE001 - checkpoint вторичен
        pass
    return {"code": "OK"}


def step_backup(ctx):
    """Суточная проверенная копия. Повреждённый источник (SRC_CORRUPT) — повреждение базы."""
    try:
        p = store2.make_backup(ctx.root, "daily", now=ctx.now)
    except store2.BackupError as e:
        if e.code == S2.SRC_CORRUPT:
            raise store2.DbCorruptError("DB_CORRUPT: копия: %s" % e) from e
        raise
    return {"code": "OK", "path": os.path.basename(p)}


def step_menu(ctx):
    rep = CA.run_menu(ctx.conn, ctx.root, ctx.now, ctx.cfg, policy=ctx.policy)
    return {"code": rep["code"], "menu_id": rep["menu_id"], "k": rep["k"], "added": len(rep["added"]), "window": rep["code"] == "MENU" or bool(rep["dropped"])}


def step_accept(ctx):
    return {"code": "OK", "changed": CA.accept(ctx.conn, ctx.root, ctx.now, ctx.cfg, policy=ctx.policy)}


def step_holdouts(ctx):
    return {"code": "OK", "changed": CA.check_holdouts(ctx.conn, ctx.root, ctx.now, ctx.cfg, policy=ctx.policy, mcfg=ctx.mcfg)}


def step_bounds(ctx):
    rep = V.bounds_check(ctx.conn, ctx.now, ctx.cfg, root=ctx.root)
    return {"code": "OK" if rep is None else "BOUNDS_BLOCKED", "ver": (rep or {}).get("ver")}


def default_steps():
    """Реестр шагов в порядке выполнения внутри периода (шаги задач 14–16 и 36 настоящие)."""
    return [
        Step("rule_eval_import", "pass", step_rule_eval_import),
        Step("llm_collect", "pass", CA.step_llm_collect),             # задача 27: ответы порта learnloop -> кандидаты llm; просрочка -> LLM_FALSE
        Step("close_clusters", "observe", step_close_clusters, group="observe"),
        Step("integrity", "daily", step_integrity),
        Step("backup", "daily", step_backup),
        Step("menu", "daily", step_menu),
        Step("accept", "daily", step_accept),
        Step("holdouts", "daily", step_holdouts),
        Step("register", "daily", TR.step_register),                                     # trial.register_pending для новых уроков hyp
        Step("day_review", "daily", CA.step_day_review),             # задача 27: роль day_reviewer через порт; без демона/роли LLM_FALSE (llm=false)
        Step("brain_ref", "daily", BR.step_brain_ref),                # задача 31: справочные уроки мозга (lessons.propose с порогами brain_ref, evidence.ref=true)
        Step("retro", "daily", RT.step_retro),                       # задача 28: раз в неделю UTC роль retro через порт или LLM_FALSE; иначе NOT_DUE
        Step("verdicts", "hourly", TR.step_verdicts, interval="verdict_s", group="verdict"),
        Step("promote", "hourly", PR.step_promote, interval="verdict_s", group="verdict"),        # пересечения, LOSS_CAP, лимит, promotion_ready
        Step("bounds", "hourly", step_bounds, interval="verdict_s"),
        Step("reconcile", "hourly", RC.step_reconcile, interval="reconcile_s"),                   # уроки меток по rule_rows/trials
        Step("watch", "hourly", WA.step_watch, interval="watch_s", group="watch"),        # откат, TRADE_STARVATION, аренда runtime/watch_lease.json
        Step("hypref", "hourly", HR.step_hypref, interval="hypref_s"),                   # справка меток по policy_map -> runtime/learn/hyp_ref.json
    ]


# ---------------------------------------------------------------- перенос rule_eval

def _seg_first(path):
    n = os.path.basename(path)
    try:
        return int(n[len(DJ.SEG_PREFIX):-len(DJ.SEG_SUFFIX)])
    except ValueError:
        return None


def get_cursor(conn):
    try:
        row = conn.execute("SELECT v FROM meta WHERE k=?", (META_CURSOR,)).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except (ValueError, TypeError):
        return 0


def _open_journal(root):
    """Список сегментов и дескриптор активного файла под блокировкой журнала (ротация между ними не теряет записи, как decisions._iter)."""
    act = paths.decisions_file(root)
    if not os.path.exists(act) and not DJ.segments(root):
        return [], None
    def grab():
        segs = DJ.segments(root)
        try:
            f = open(act, "rb")
        except OSError:
            f = None
        return segs, f
    try:
        with FileLock(paths.decisions_lock(root), wait=DJ.LOCK_WAIT_S):
            return grab()
    except (TimeoutError, OSError):
        return grab()


def _last_seq(pieces):
    for ln in reversed(pieces):
        r = DJ._parse(ln)
        if r is not None:
            return r["seq"]
    return 0


def read_rule_evals(root, cursor):
    """Записи rule_eval с seq > cursor: с сегмента, у которого first_seq <= cursor + 1 (последнего такого), затем следующие сегменты и активный
    файл; сегменты, у которых manifest.last_seq <= cursor, пропускаются; оборванная последняя строка активного файла ждёт следующего прохода.
    -> (записи, новый курсор = seq последней целой записи прочитанных файлов, не меньше cursor)."""
    segs, f = _open_journal(root)
    try:
        firsts = [(fs, p) for fs, p in ((_seg_first(p), p) for p in segs) if fs is not None]
        start = 0
        for i, (fs, _p) in enumerate(firsts):
            if fs <= cursor + 1:
                start = i
        man = DJ._manifest(root) if firsts else {}
        chunks = []
        for fs, p in firsts[start:]:
            info = man.get(os.path.basename(p)) or {}
            if isinstance(info.get("last_seq"), int) and info["last_seq"] <= cursor:
                continue
            try:
                with open(p, "rb") as sf:
                    chunks.append(sf.read().split(b"\n"))
            except OSError:
                continue
        if f is not None:
            chunks.append(f.read().split(b"\n")[:-1])      # последний кусок — оборванная строка или пусто после "\n"
    finally:
        if f is not None:
            f.close()
    recs, newc = [], cursor
    for pieces in chunks:
        for ln in pieces:
            if b'"rule_eval"' not in ln:
                continue
            r = DJ._parse(ln)
            if r is None or r["seq"] <= cursor or r.get("kind") != "note" or r.get("what") != RULE_EVAL_WHAT:
                continue
            recs.append(r)
        newc = max(newc, _last_seq(pieces))
    return recs, newc


def _ids(b, k):
    v = b.get(k)
    return sorted(set(str(x) for x in v if isinstance(x, str) and x)) if isinstance(v, list) else []


def _s(v, n):
    return str(v)[:n] if isinstance(v, (str, int)) and not isinstance(v, bool) and v != "" else None


def rule_eval_rows(rec):
    """Строки rule_evals одной записи: строка rule_id="" (всегда: знаменатель доли решений без события) и по строке на каждое правило из
    rule_eval_ids / rule_hits / feat_missing_ids. Поля берутся из basis (rulebook_view.rule_eval_fields), иначе из args."""
    b = rec.get("basis") if isinstance(rec.get("basis"), dict) else (rec.get("args") if isinstance(rec.get("args"), dict) else {})
    hits, evals, miss = set(_ids(b, "rule_hits")), set(_ids(b, "rule_eval_ids")), set(_ids(b, "feat_missing_ids"))
    es = b.get("event_seq")
    es = es if isinstance(es, int) and not isinstance(es, bool) else None
    t = rec.get("t")
    ts = int(t) if isinstance(t, (int, float)) and not isinstance(t, bool) else 0
    common = {"rec_seq": int(rec["seq"]), "ts": ts, "role": _s(b.get("role"), 24), "event_seq": es, "no_event": 1 if es is None else 0,
              "rulebook_ver": _s(b.get("rulebook_ver"), 16), "plan": _s(b.get("plan") or rec.get("plan"), 64)}
    rows = [dict(common, rule_id="", hit=1 if hits else 0, evaluated=1 if evals else 0, missing=1 if miss else 0)]
    for rid in sorted(hits | evals | miss):
        rows.append(dict(common, rule_id=rid[:32], hit=1 if rid in hits else 0, evaluated=1 if rid in evals else 0, missing=1 if rid in miss else 0))
    return rows


def import_rule_evals(conn, root):
    """Шаг rule_eval_import: перенос новых записей в rule_evals (INSERT OR IGNORE по (rec_seq, rule_id)) и курсор meta.rule_eval_cursor одной
    транзакцией. После восстановления базы курсор — из копии, перенос продолжается по seq без дублей."""
    cur = get_cursor(conn)
    recs, newc = read_rule_evals(root, cur)
    rows = [r for rec in recs for r in rule_eval_rows(rec)]
    n = 0
    if rows or newc != cur:
        with LST.tx(conn):
            if rows:
                n = store2.insert_rows(conn, "rule_evals", rows, ignore_dup=True)
            conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (META_CURSOR, str(int(newc))))
    return {"code": "OK", "records": len(recs), "rows": n, "cursor": newc}


def no_event_share(conn, now, window_s=NO_EVENT_WINDOW_S):
    """Доля записей rule_eval без события за окно (строки rule_id="") и доля строк правил с feat_missing. -> (n, share|None, missing_share|None)."""
    t0 = int(now) - int(window_s)
    n, ne = conn.execute("SELECT COUNT(*), SUM(no_event) FROM rule_evals WHERE rule_id='' AND ts>=?", (t0,)).fetchone()
    m, mm = conn.execute("SELECT COUNT(*), SUM(missing) FROM rule_evals WHERE rule_id<>'' AND ts>=?", (t0,)).fetchone()
    return int(n or 0), (float(ne or 0) / n if n else None), (float(mm or 0) / m if m else None)


# ---------------------------------------------------------------- учёт суток

def day_of(ts):
    return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).strftime("%Y-%m-%d")


def day_start(ts):
    return int(ts) // DAY * DAY


def daily_offset(cfg):
    hh, mm = cfg["intervals"]["daily_after"][:5].split(":")
    return int(hh) * HOUR + int(mm) * 60


def new_acc(day):
    return {"day": day, "work_s": 0.0, "hw": {}, "oh": [], "daily": {}}


def summarize_day(acc, cfg, daily_names):
    """Сводка суток (Requirement 15.6, свойство 27): counted — работа >= day_min_work_h; full — counted, каждый суточный шаг выполнен или пропущен
    с кодом (не ERROR и не отсутствует) и observe успешен в >= observe_ok_share часов работы (час работы — не меньше observe_s работы в нём)."""
    obs = int(cfg["intervals"]["observe_s"])
    worked = set(h for h, s in (acc.get("hw") or {}).items() if float(s) >= obs)
    ok = worked & set(str(h) for h in acc.get("oh") or [])
    share = round(len(ok) / float(len(worked)), 4) if worked else 0.0
    work_s = float(acc.get("work_s") or 0)
    counted = work_s >= int(cfg["days"]["day_min_work_h"]) * HOUR
    daily = acc.get("daily") or {}
    missing = [n for n in daily_names if daily.get(n) in (None, ERROR)]
    full = bool(counted and not missing and share >= float(cfg["days"]["observe_ok_share"]))
    return {"day": acc["day"], "work_s": int(work_s), "work_h": round(work_s / HOUR, 2), "worked_h": len(worked), "obs_share": share,
            "daily": dict(daily), "missing": missing, "counted": counted, "full": full}


# ---------------------------------------------------------------- цикл

class Cycle(object):
    """Расписание, выполнение проходов, учёт суток и статус цикла. Соединение и протокол восстановления — у сервиса (learnloop.service)."""

    def __init__(self, root, cfg, steps=None, policy=None, mcfg=None, log=None):
        self.root, self.cfg = root, cfg
        self.steps = list(default_steps() if steps is None else steps)
        self.policy, self.mcfg = policy, mcfg
        self.log = log or (lambda m: None)
        self.last = {}                   # имя -> время последней попытки шага observe/hourly (в памяти: после рестарта шаги идут сразу)
        self.state = None                # meta.learnloop_state
        self.acc = None                  # meta.learnloop_day
        self.pending_days = []           # закрытые сутки, ещё не записанные в learn_events
        self.last_tick = None
        self.run_start = None            # начало непрерывной работы процесса (WATCH_STALE)
        self.ready = None                # последний versions.promotion_ready
        self.codes, self.reasons = [], []
        self.no_event = None
        self.last_pass_ts = self.last_ok_ts = None
        self.last_report = None

    # ---- состояние
    def daily_names(self):
        return [s.name for s in self.steps if s.period == "daily"]

    def watch_real(self):
        return any(s.name == "watch" and not s.stub for s in self.steps)

    def stubs(self):
        return [s.name for s in self.steps if s.stub]

    def load(self, conn, now):
        """Состояние и текущие сутки из meta (один раз: после восстановления базы из копии в памяти более свежие данные)."""
        if self.state is None:
            st = V.state_get(conn)
            st.setdefault("started_ts", int(now))
            st.setdefault("steps", {})
            self.state = st
        if self.acc is None:
            acc = V.day_get(conn)
            self.acc = acc if isinstance(acc, dict) and acc.get("day") else None
            self._ensure_day(day_of(now))

    def _ensure_day(self, day):
        if self.acc is not None and self.acc.get("day") == day:
            return
        if self.acc is not None and (self.acc.get("work_s") or self.acc.get("daily")):
            self.pending_days.append(summarize_day(self.acc, self.cfg, self.daily_names()))
        self.acc = new_acc(day)

    def _step_state(self, s):
        st = self.state["steps"].setdefault(s.name, {})
        st["group"] = s.group
        return st

    def credit(self, now):
        """Учёт работы сервиса между тиками (промежуток > GAP_FACTOR x observe_s — простой, не засчитывается)."""
        now = float(now)
        prev, self.last_tick = self.last_tick, now
        if self.acc is None:                  # сутки ещё не загружены из meta (база не открыта): работа до первого прохода не учитывается
            return
        if prev is None or now <= prev or now - prev > GAP_FACTOR * int(self.cfg["intervals"]["observe_s"]):
            self._ensure_day(day_of(now))
            return
        t = prev
        while t < now:
            self._ensure_day(day_of(t))
            hend = min(now, (int(t) // HOUR + 1) * HOUR)
            h = str(int(t) % DAY // HOUR)
            self.acc["hw"][h] = round(float(self.acc["hw"].get(h, 0)) + (hend - t), 3)
            self.acc["work_s"] = round(float(self.acc["work_s"]) + (hend - t), 3)
            t = hend
        self._ensure_day(day_of(now))

    # ---- расписание
    def due(self, s, now):
        now = int(now)
        if s.period == "pass":
            return True
        if s.period == "observe":
            last = self.last.get(s.name)
            return last is None or now - last >= int(self.cfg["intervals"]["observe_s"])
        if s.period == "hourly":
            last = self.last.get(s.name)
            return last is None or now - last >= int(self.cfg["intervals"][s.interval or "verdict_s"])
        today = day_of(now)
        if now < day_start(now) + daily_offset(self.cfg):
            return False
        st = (self.state or {}).get("steps", {}).get(s.name) or {}
        if st.get("day") == today:
            return False
        if st.get("err_day") == today and now - int(st.get("err_ts") or 0) < DAILY_RETRY_S:
            return False
        return True

    def pass_due(self, now):
        if self.state is None:
            return True
        return any(self.due(s, now) for s in self.steps if s.period != "pass")

    def _ordered(self):
        return [s for p in PERIODS for s in self.steps if s.period == p]

    # ---- проход
    def run_pass(self, conn, now, beat=None):
        """Один проход: шаги со сроком по порядку периодов. -> отчёт {"ts", "ran", "errors"}. Повреждение -> Corruption."""
        now = int(now)
        self.load(conn, now)
        self._ensure_day(day_of(now))
        ctx = Ctx(self.root, conn, self.cfg, now, self.policy, self.mcfg, beat, self)
        rep = {"ts": now, "ran": {}, "errors": {}}
        obs_ran = obs_err = hourly = False
        for s in self._ordered():
            if not self.due(s, now):
                continue
            if s.period in ("observe", "hourly"):
                self.last[s.name] = now
            obs_ran = obs_ran or s.period == "observe"
            hourly = hourly or s.period == "hourly"
            st = self._step_state(s)
            try:
                res = s.fn(ctx) or {}
            except Exception as e:  # noqa: BLE001 - сбой шага не останавливает цикл (Requirement 15.4)
                if store2.is_corruption(e):
                    raise Corruption(s.name, e)
                self._rollback(conn)
                code = CHAIN_BROKEN if isinstance(e, store2.ChainBroken) else STEP_ERROR
                st.update(err_ts=now, err_run=int(st.get("err_run") or 0) + 1, code=code)
                if st["err_run"] >= int(self.cfg["promotion"]["step_error_max"]):
                    st["series_ts"] = now
                if s.period == "daily":
                    st["err_day"] = day_of(now)
                    if self.acc["daily"].get(s.name) in (None, ERROR):
                        self.acc["daily"][s.name] = ERROR
                if s.period == "observe":
                    obs_err = True
                rep["errors"][s.name] = code
                self._log_error(conn, s.name, code, e, now)
                self.log("learnloop: шаг %s: %s (%s)" % (s.name, code, type(e).__name__))
                continue
            code = res.get("code") or "OK"
            st.update(ok_ts=now, err_run=0, code=code)
            if s.period == "daily":
                st["day"] = day_of(now)
                self.acc["daily"][s.name] = code
                if code in SKIP_CODES:
                    self._log(conn, s.name, code, now)
            rep["ran"][s.name] = code
            if beat:
                beat()
        if obs_ran and not obs_err:
            h = str(now % DAY // HOUR)
            if h not in self.acc["oh"]:
                self.acc["oh"].append(h)
        self.last_pass_ts = now
        if not rep["errors"]:
            self.last_ok_ts = now
        self.update_status(conn, now, readiness=hourly or self.ready is None)
        self.save(conn, now)
        try:
            store2.checkpoint(conn, "PASSIVE")
        except Exception as e:  # noqa: BLE001
            if store2.is_corruption(e):
                raise Corruption("checkpoint", e)
        self.last_report = rep
        return rep

    def _rollback(self, conn):
        if conn.in_transaction:
            try:
                conn.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass

    def _log(self, conn, step, code, now, body=None, error=False):
        try:
            store2.log_event(conn, step, code, "code", body=body, error=error, now=now,
                             per_day=int(self.cfg.get("events_per_day") or S2.EVENTS_PER_DAY))
        except Exception as e:  # noqa: BLE001 - запись события вторична
            self._rollback(conn)
            if store2.is_corruption(e):
                raise Corruption("learn_events", e)

    def _log_error(self, conn, step, code, e, now):
        try:
            store2.log_event(conn, step, code, "code", body={"err": type(e).__name__, "msg": str(e)[:160]}, error=True, now=now)
        except Exception as e2:  # noqa: BLE001 - privacy_scan текста ошибки и т.п.: без текста
            self._rollback(conn)
            if store2.is_corruption(e2):
                raise Corruption("learn_events", e2)
            self._log(conn, step, code, now, body={"err": type(e).__name__}, error=True)

    def save(self, conn, now):
        """Закрытые сутки -> learn_events step="day"; состояние и текущие сутки -> meta. Одна транзакция."""
        try:
            with LST.tx(conn):
                for d in self.pending_days:
                    store2.log_event(conn, V.DAY_STEP, "DAY", "code", ref=d["day"], body=d, now=now)
                V.meta_put(conn, V.META_STATE, self.state)
                V.meta_put(conn, V.META_DAY, self.acc)
            self.pending_days = []
        except Exception as e:  # noqa: BLE001
            if store2.is_corruption(e):
                raise Corruption("save", e)
            self.log("learnloop: состояние цикла не записано (%s)" % type(e).__name__)

    # ---- статус (Requirement 15.5)
    def update_status(self, conn, now, readiness=True):
        cfg = self.cfg
        codes, reasons = [], []
        try:
            n, share, miss = no_event_share(conn, now)
            self.no_event = {"n": n, "share": None if share is None else round(share, 4), "missing_share": None if miss is None else round(miss, 4)}
            if share is not None and share > float(cfg["apply"]["feat_missing_max"]):
                codes.append("RULES_NO_EVENT")
                reasons.append("цикл обучения: решений без события %.0f%% за 7 суток (RULES_NO_EVENT): правила к ним не применяются" % (share * 100))
        except Exception:  # noqa: BLE001
            pass
        try:
            rm = conn.execute("SELECT k, v FROM meta WHERE k IN (?, ?, ?)", (S2.META_RESTORE_TS, S2.META_RESTORE_MODE, S2.META_RESTORE_SRC)).fetchall()
            rm = dict(rm)
            rts = float(rm.get(S2.META_RESTORE_TS) or 0)
            if rm.get(S2.META_RESTORE_MODE) == "auto" and 0 <= now - rts < AUTO_RESTORED_S:
                codes.append("AUTO_RESTORED")
                reasons.append("цикл обучения: база знаний автоматически восстановлена из копии %s (AUTO_RESTORED)" % (rm.get(S2.META_RESTORE_SRC) or "-"))
        except Exception:  # noqa: BLE001
            pass
        try:
            ch = store2.verify_chain(conn)
            if not ch["ok"]:
                codes.append("CHAIN_BROKEN")
                reasons.append("цикл обучения: цепочка rulebook_versions разорвана (CHAIN_BROKEN): записи версий отказываются")
        except Exception:  # noqa: BLE001
            pass
        lh = int(cfg["lease"]["lease_h"])
        stale = bool(self.watch_real() and self.run_start is not None and now - self.run_start > lh * HOUR and not RV.lease_fresh(self.root, now, lh))
        if stale:
            codes.append("WATCH_STALE")
            reasons.append("цикл обучения: сторож не отработал дольше %d ч работы цикла (WATCH_STALE): правила не применяются" % lh)
            if not self.state.get("watch_stale"):
                self._log(conn, "watch", "WATCH_STALE", now, body={"run_start": int(self.run_start)}, error=True)
        self.state["watch_stale"] = stale
        if readiness:
            try:
                self.ready = V.promotion_ready(conn, cfg, now, root=self.root, state=self.state)
            except Exception as e:  # noqa: BLE001
                if store2.is_corruption(e):
                    raise Corruption("promotion_ready", e)
                self.ready = {"ok": False, "failed": ["ERROR"], "details": {"err": type(e).__name__}}
            if self.ready["ok"]:
                self.state["last_ready_ts"] = now
        if self.ready is not None and not self.ready["ok"]:
            since = int(self.state.get("last_ready_ts") or self.state.get("started_ts") or now)
            if now - since > int(cfg["promotion"]["warmup_stuck_days"]) * DAY:
                codes.append("WARMUP_STUCK")
                reasons.append("цикл обучения: прогрев не завершён %d суток (WARMUP_STUCK): %s" % ((now - since) // DAY, ",".join(self.ready["failed"])))
        self.codes, self.reasons = codes, reasons
        return codes
