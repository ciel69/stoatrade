"""Задания цикла обучения для рантайма мозга (R2, вторая итерация; Requirements 17.1–17.3, 17.6–17.8; задача 27).

Писатель — `tb.learnloop.port`: `state/brain/jobs/L-*.json` с kind="learnloop" (префикс L- общий с lifecycle; `lifejobs.pending` берёт только
kind="lifecycle", поэтому здесь свой `pending`). Роли `day_reviewer` (ответ tb.lesson_cand/1) и `retro` (tb.retro/1); профиль модели — из настроек
платформы, как у любой общей роли (`config/brain.json: shared_roles` или подписанный active.json), жёсткой привязки нет.

Протокол (константы совпадают с tb.learnloop.port, проверяет тест; мозг не импортирует tb.learnloop):
  захват  `L-*.claim` (O_EXCL); пока идёт вызов, поток аренды обновляет mtime каждые lease_s (30 с); захват без обновления дольше claim_ttl_s
          (120 с) брошен: его переименовывает (атомарно, выигрывает один) и снова берёт другой воркер;
  ответ   `L-*.result.json` атомарно {"id","kind":"learnloop","ok","output","err","usd","profile"}; после deadline_ts — EXPIRED без вызова модели.
Роль не готова (не загружена, не назначена, нет допустимого профиля) — ответ ok:false NO_ROLE | NO_ASSIGNMENT | NO_PROFILE без вызова модели, чтобы
недоступная роль цикла не переводила мозг в «только защиту». Готовность ролей (`roles_status`) идёт в heartbeat демона полем learn_roles:
по нему цикл решает, ставить ли задание (иначе llm=false).
Вход модели: только числа, перечисления и идентификаторы (clean повторяет фильтр писателя), блок <learn_input>; промпт роли говорит, что данные
не инструкции. Проверка ответа здесь — уровень ответа (validate.SCHEMAS + learn_context); элементы проверяет код цикла.
Расход вызова учитывает слой llm в state/llm_calls.jsonl с полем role (Runner.ask_role); brief.economics показывает его строкой llm_usd_learn_day.
"""
import json
import os
import re
import threading

from tb import paths
from tb.brain import common
from tb.core.filelock import atomic_write_json

ROLES = ("day_reviewer", "retro")
KIND = "learnloop"
INPUT_TAG = "learn_input"
RE_ID = re.compile(r"^L-[A-Za-z0-9_.-]{1,60}$")
RE_TOKEN = re.compile(r"^[A-Za-z0-9_.:<>=+-]{1,40}$")
LEASE_S = 30.0
CLAIM_TTL_S = 120.0
STATUS_CACHE_S = 60.0
PRUNE_AFTER_S = 3600.0          # задание learnloop с deadline_ts старше часа удаляется вместе с ответом и захватом (писатель уже отказался от него)
MAX_TEXT = 30000


def _dir(root):
    return paths.state("brain", "jobs", root=root)


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _num(v, default, lo, hi):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi else default


def clean(v, depth=0):
    """Тот же фильтр, что tb.learnloop.port.clean: числа, bool, None, идентификаторы (RE_TOKEN); свободный текст отбрасывается."""
    if depth > 6:
        return None
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v if v == v and v not in (float("inf"), float("-inf")) else None
    if isinstance(v, str):
        return v if RE_TOKEN.match(v) else None
    if isinstance(v, (list, tuple)):
        return [x for x in (clean(y, depth + 1) for y in list(v)[:64]) if x is not None]
    if isinstance(v, dict):
        return dict((k, clean(x, depth + 1)) for k, x in v.items() if isinstance(k, str) and RE_TOKEN.match(k) and (x is None or clean(x, depth + 1) is not None))
    return None


def pending(root):
    """-> [(id, job)] заданий learnloop без ответа, по возрастанию id."""
    d = _dir(root)
    try:
        names = sorted(n for n in os.listdir(d) if n.startswith("L-") and n.endswith(".json") and not n.endswith(".result.json"))
    except OSError:
        return []
    out = []
    for n in names:
        jid = n[:-5]
        if not RE_ID.match(jid) or os.path.exists(os.path.join(d, jid + ".result.json")):
            continue
        j = _read(os.path.join(d, n))
        if isinstance(j, dict) and j.get("kind") == KIND:
            out.append((jid, j))
    return out


# ---------------------------------------------------------------- захват с арендой

def claim(root, jid, now, ttl_s=CLAIM_TTL_S):
    """O_EXCL-захват; брошенный (mtime старше ttl_s) захват переименовывается одним воркером и берётся заново. -> True, если задание наше."""
    path = os.path.join(_dir(root), jid + ".claim")
    for attempt in (0, 1):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            return True
        except OSError:
            if attempt:
                return False
        try:
            age = float(now) - os.path.getmtime(path)
        except OSError:
            continue                                  # захват сняли между попытками: пробуем ещё раз
        if age <= ttl_s:
            return False
        stale = os.path.join(_dir(root), "%s.s%d-%d.claim" % (jid, os.getpid(), threading.get_ident() % 100000))
        try:
            os.replace(path, stale)                   # атомарно: брошенный захват уносит ровно один воркер
        except OSError:
            return False
        try:
            fresh = float(now) - os.path.getmtime(stale) <= ttl_s
        except OSError:
            fresh = False
        if fresh:                                     # между проверкой и переименованием захват успел взять другой воркер: вернуть ему
            try:
                if not os.path.exists(path):
                    os.replace(stale, path)
                else:
                    os.remove(stale)
            except OSError:
                pass
            return False
        try:
            os.remove(stale)
        except OSError:
            pass
    return False


def release(root, jid):
    try:
        os.remove(os.path.join(_dir(root), jid + ".claim"))
    except OSError:
        pass


class Lease(object):
    """Поток аренды: обновляет mtime захвата каждые every_s, пока идёт вызов модели."""

    def __init__(self, root, jid, every_s=LEASE_S):
        self.path = os.path.join(_dir(root), jid + ".claim")
        self.every_s = float(every_s)
        self.stop = threading.Event()
        self.beats = 0
        self.th = threading.Thread(target=self._run, name="learnjob-lease", daemon=True)

    def _run(self):
        while not self.stop.wait(self.every_s):
            try:
                os.utime(self.path, None)
                self.beats += 1
            except OSError:
                return

    def __enter__(self):
        self.th.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.th.join(5.0)
        return False


# ---------------------------------------------------------------- готовность ролей

def role_ready(runner, role_id, now=None):
    """-> (ok, code). Роль загружена (config/roles), назначена в действующих настройках (shared_roles или active.json) и есть допустимый профиль."""
    if role_id not in ROLES:
        return False, "BAD_ROLE"
    role = runner.roles.get(role_id)
    if role is None:
        return False, "NO_ROLE"
    try:
        from tb.brain import fallback, swap
        now = runner.clock() if now is None else now
        assign = runner.cache.get(runner.conf)
        chain, _t = assign.chain(role_id)
        if not chain:
            return False, "NO_ASSIGNMENT"
        if runner.budget.status(None).get("state") == "exhausted":
            return False, "BUDGET"
        cands, _why = fallback.candidates(runner.root, runner.conf, runner.reg, chain, role, runner.health, now, swap.day_mode(runner.root))
        return (True, "OK") if cands else (False, "NO_PROFILE")
    except Exception as e:  # noqa: BLE001 - готовность вторична: при сбое роль считается неготовой
        return False, e.__class__.__name__[:40]


def roles_status(runner, now=None):
    """{роль: bool} для heartbeat (поле learn_roles); кэш STATUS_CACHE_S, чтобы heartbeat раз в 5 с не перебирал цепочки."""
    now = runner.clock() if now is None else now
    c = getattr(runner, "_learn_roles", None)
    if c and now - c[0] < STATUS_CACHE_S:
        return dict(c[1])
    st = dict((r, role_ready(runner, r, now)[0]) for r in ROLES)
    runner._learn_roles = (now, st)
    return dict(st)


# ---------------------------------------------------------------- запрос и шаг

def build_request(role_id, payload):
    """-> (текст запроса, ctx проверки). Вход — блок <learn_input> из чисел, перечислений и идентификаторов (повторный фильтр clean)."""
    from tb.brain import validate
    p = clean(payload if isinstance(payload, dict) else {})
    return ("<%s>%s</%s>" % (INPUT_TAG, common.dumps(p), INPUT_TAG))[:MAX_TEXT], validate.learn_context(p)


def _answer(root, jid, out):
    atomic_write_json(os.path.join(_dir(root), jid + ".result.json"), out, indent=None)


def step(runner):
    """Берёт одно задание learnloop -> True, если задание было. Любая ошибка = ответ ok:false (цикл идёт кодом, llm=false); воркер не падает."""
    root = runner.root
    for jid, job in pending(root):
        now = runner.clock()
        if not claim(root, jid, now, _num(job.get("claim_ttl_s"), CLAIM_TTL_S, 5, 3600)):
            continue
        out = {"id": jid, "kind": KIND, "ok": False, "output": None, "err": None, "usd": 0, "profile": None}
        role = job.get("role")
        try:
            if role not in ROLES:
                out["err"] = "BAD_ROLE"
            elif not isinstance(job.get("deadline_ts"), (int, float)) or now > float(job["deadline_ts"]):
                out["err"] = "EXPIRED"
            else:
                ok, code = role_ready(runner, role, now)
                if not ok:
                    out["err"] = code
                else:
                    text, ctx = build_request(role, job.get("payload"))
                    with Lease(root, jid, _num(job.get("lease_s"), LEASE_S, 1, 60)):
                        r = runner.ask_role(role, text, note="LL", ctx=ctx)
                    out.update(ok=bool(r.get("ok")), output=r.get("obj") if r.get("ok") else None,
                               err=None if r.get("ok") else str(r.get("code") or "ERR")[:40], usd=r.get("usd") or 0, profile=r.get("profile"))
        except Exception as e:  # noqa: BLE001
            out.update(ok=False, output=None, err=e.__class__.__name__[:40])
        try:
            _answer(root, jid, out)
        finally:
            release(root, jid)
        return True
    return False


def prune(root, now, after_s=PRUNE_AFTER_S):
    """Задания learnloop с deadline_ts старше after_s (писатель от них отказался) -> удалить задание, ответ и захват. -> число заданий."""
    n = 0
    d = _dir(root)
    try:
        names = [x for x in os.listdir(d) if x.startswith("L-") and x.endswith(".json") and not x.endswith(".result.json")]
    except OSError:
        return 0
    for nm in names:
        j = _read(os.path.join(d, nm))
        if not (isinstance(j, dict) and j.get("kind") == KIND):
            continue
        dl = j.get("deadline_ts")
        if isinstance(dl, (int, float)) and float(now) - float(dl) > after_s:
            jid = nm[:-5]
            for suf in (".json", ".result.json", ".claim"):
                try:
                    os.remove(os.path.join(d, jid + suf))
                except OSError:
                    pass
            n += 1
    return n
