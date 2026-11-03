"""Файловый порт заданий модели для цикла обучения (R2, вторая итерация; Requirements 17.1, 17.2, 17.6, 17.8; задача 27).

Каталог `state/brain/jobs/` общий с lifecycle (префикс `L-`, различие по `kind`; `N-` занят ссылками новостей). Файлы одного задания:
  L-<UTC>-<hash>.json         задание {"schema": JOB_SCHEMA, "id", "kind": "learnloop", "role", "ts", "deadline_ts", "claim_ttl_s", "lease_s",
                              "meta", "payload"}; пишет только этот модуль, атомарно. deadline_ts = ts + llm_job_ttl_s (900 с): после него демон
                              API-мозга отвечает EXPIRED и модель не вызывает.
  L-....claim                 захват демона (brain/learnjobs.py): O_EXCL; mtime обновляется каждые lease_s (30 с) — аренда; захват без обновления
                              дольше claim_ttl_s (120 с) брошен и может быть взят снова. При старте демона lifejobs.reset_claims снимает все
                              захваты без ответа (брошенные захваты learnloop освобождаются — допустимо).
  L-....result.json           ответ демона, атомарно: {"id", "kind": "learnloop", "ok", "output", "err", "usd", "profile"}.
Цикл ждёт ответ не дольше llm_wait_s (600 с) от ts задания; дольше — вызов неудачен (llm=false), файлы задания удаляются (sweep/done), задания
не копятся. Все сроки (claim_ttl_s < llm_wait_s < llm_job_ttl_s) много меньше срока чистки демона lifejobs.prune (keep_jobs_s = 86400 в
config/brain.json, по умолчанию 259200): живое задание чистка демона не удаляет.

Доступность (available): heartbeat сервиса brain свеж (svc.brain.hb_stale_s), мозг не в «только защите», бюджет не исчерпан, роль готова — поле
`learn_roles` heartbeat (его пишет brain/api_daemon.py: роль загружена, назначена в настройках платформы и есть допустимый профиль). Иначе
llm=false с кодом NO_DAEMON | PROTECT | BUDGET | NO_ROLE, цикл идёт кодом.

Вход модели — только числа, перечисления и идентификаторы: clean() отбрасывает строки вне RE_TOKEN (свободного текста в задании нет).
Модуль не импортирует tb.brain, tb.gateway, tb.llm и ничего не пишет в config/.
"""
import hashlib
import json
import os
import re
import time

from tb import paths
from tb.core.filelock import atomic_write_json
from tb.platform import services as S

JOB_SCHEMA = "tb.learnloop.job/1"
KIND = "learnloop"
LEASE_S = 30
RE_ID = re.compile(r"^L-[A-Za-z0-9_.-]{1,60}$")
RE_TOKEN = re.compile(r"^[A-Za-z0-9_.:<>=+-]{1,40}$")
MAX_DEPTH = 6
MAX_LIST = 64
HB_STALE_DEFAULT = 300.0

NO_DAEMON, PROTECT, BUDGET, NO_ROLE = "NO_DAEMON", "PROTECT", "BUDGET", "NO_ROLE"
TIMEOUT = "TIMEOUT"


def jobs_dir(root=None):
    return paths.state("brain", "jobs", root=root)


def port_cfg(cfg):
    """Сроки порта из config/learnloop.json: second_iteration.port {llm_job_ttl_s, claim_ttl_s, llm_wait_s}."""
    p = cfg["second_iteration"]["port"]
    return {"llm_job_ttl_s": int(p["llm_job_ttl_s"]), "claim_ttl_s": int(p["claim_ttl_s"]), "llm_wait_s": int(p["llm_wait_s"])}


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _rm(path):
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def _p(root, jid, suffix):
    return os.path.join(jobs_dir(root), jid + suffix)


# ---------------------------------------------------------------- вход модели без свободного текста

def clean(v, depth=0):
    """Только числа, bool, None, идентификаторы и перечисления (RE_TOKEN), списки и объекты с ключами-идентификаторами; прочее отбрасывается."""
    if depth > MAX_DEPTH:
        return None
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v if v == v and v not in (float("inf"), float("-inf")) else None
    if isinstance(v, str):
        return v if RE_TOKEN.match(v) else None
    if isinstance(v, (list, tuple)):
        out = [clean(x, depth + 1) for x in list(v)[:MAX_LIST]]
        return [x for x in out if x is not None]
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if isinstance(k, str) and RE_TOKEN.match(k):
                y = clean(x, depth + 1)
                if y is not None or x is None:
                    out[k] = y
        return out
    return None


# ---------------------------------------------------------------- доступность

def available(root, now, role):
    """-> (ok, code). Демон API-мозга запущен (свежий heartbeat brain), не в «только защите», бюджет не исчерпан, роль готова (learn_roles)."""
    age, d = S.read_hb("brain", root, now)
    try:
        stale = float(((S.load_cfg(root).get("svc") or {}).get("brain") or {}).get("hb_stale_s") or HB_STALE_DEFAULT)
    except Exception:  # noqa: BLE001 - конфиг платформы вторичен
        stale = HB_STALE_DEFAULT
    if age is None or age > stale:
        return False, NO_DAEMON
    if d.get("protect_only"):
        return False, PROTECT
    if d.get("budget") == "exhausted":
        return False, BUDGET
    roles = d.get("learn_roles")
    if not isinstance(roles, dict) or roles.get(role) is not True:
        return False, NO_ROLE
    return True, "OK"


# ---------------------------------------------------------------- писатель заданий

def new_id(role, now, meta=None):
    h = hashlib.sha1(("%s|%s|%r" % (role, json.dumps(meta or {}, sort_keys=True), now)).encode("utf-8")).hexdigest()[:8]
    return "L-%s-%s" % (time.strftime("%Y%m%dT%H%M%S", time.gmtime(now)), h)


def submit(root, role, payload, now, cfg, meta=None):
    """Кладёт задание kind=learnloop -> jid. payload и meta проходят clean()."""
    pc = port_cfg(cfg)
    meta = clean(meta or {})
    jid = new_id(role, now, meta)
    os.makedirs(jobs_dir(root), exist_ok=True)
    job = {"schema": JOB_SCHEMA, "id": jid, "kind": KIND, "role": role, "ts": round(float(now), 3),
           "deadline_ts": round(float(now) + pc["llm_job_ttl_s"], 3), "claim_ttl_s": pc["claim_ttl_s"], "lease_s": LEASE_S,
           "meta": meta, "payload": clean(payload)}
    atomic_write_json(_p(root, jid, ".json"), job, indent=None)
    return jid


def read_job(root, jid):
    if not isinstance(jid, str) or not RE_ID.match(jid):
        return None
    j = _read(_p(root, jid, ".json"))
    return j if isinstance(j, dict) and j.get("kind") == KIND else None


def result(root, jid):
    """Ответ демона -> {"ok", "output", "err", "usd", "profile"} | None (ещё нет)."""
    if not isinstance(jid, str) or not RE_ID.match(jid):
        return None
    d = _read(_p(root, jid, ".result.json"))
    if not isinstance(d, dict) or d.get("id") != jid:
        return None
    usd = d.get("usd")
    return {"ok": d.get("ok") is True and isinstance(d.get("output"), dict), "output": d.get("output") if isinstance(d.get("output"), dict) else None,
            "err": str(d.get("err") or "")[:40] or None, "usd": usd if isinstance(usd, (int, float)) and not isinstance(usd, bool) else 0,
            "profile": d.get("profile") if isinstance(d.get("profile"), str) else None}


def claim_age(root, jid, now):
    try:
        return max(0.0, float(now) - os.path.getmtime(_p(root, jid, ".claim")))
    except OSError:
        return None


def status(root, jid, now, cfg, job=None):
    """-> "done" (есть ответ) | "claimed" (свежий захват) | "waiting" | "timeout" (дольше llm_wait_s от ts задания) | "missing"."""
    job = job if job is not None else read_job(root, jid)
    if job is None:
        return "missing"
    if result(root, jid) is not None:
        return "done"
    pc = port_cfg(cfg)
    if float(now) - float(job.get("ts") or 0) > pc["llm_wait_s"]:
        return "timeout"
    age = claim_age(root, jid, now)
    return "claimed" if age is not None and age <= pc["claim_ttl_s"] else "waiting"


def own(root):
    """-> [(jid, задание)] заданий kind=learnloop (с файлом задания), по возрастанию id."""
    try:
        names = sorted(n for n in os.listdir(jobs_dir(root)) if n.startswith("L-") and n.endswith(".json") and not n.endswith(".result.json"))
    except OSError:
        return []
    out = []
    for n in names:
        jid = n[:-5]
        j = read_job(root, jid)
        if j is not None:
            out.append((jid, j))
    return out


def done(root, jid):
    """Ответ принят (или вызов признан неудачным): удалить задание, ответ и захват."""
    for suf in (".json", ".result.json", ".claim"):
        _rm(_p(root, jid, suf))


def cancel(root, jid):
    """Отказ от задания: файл задания удаляется (демон его больше не возьмёт), ответ — тоже; захват снимает демон или чистка (sweep)."""
    _rm(_p(root, jid, ".json"))
    _rm(_p(root, jid, ".result.json"))


def wait(root, jid, cfg, clock=time.time, sleep=time.sleep, poll_s=2.0):
    """Блокирующее ожидание ответа не дольше llm_wait_s от ts задания. -> ответ (result) | {"ok": False, "err": "TIMEOUT"} (задание отменяется)."""
    job = read_job(root, jid)
    if job is None:
        return {"ok": False, "output": None, "err": "MISSING", "usd": 0, "profile": None}
    limit = float(job.get("ts") or clock()) + port_cfg(cfg)["llm_wait_s"]
    while True:
        r = result(root, jid)
        if r is not None:
            return r
        now = clock()
        if now >= limit:
            cancel(root, jid)
            return {"ok": False, "output": None, "err": TIMEOUT, "usd": 0, "profile": None}
        sleep(min(float(poll_s), max(0.0, limit - now)))


def sweep(root, now, cfg):
    """Чистка порта (просроченные не копятся): задания learnloop старше llm_wait_s без ответа, ответы learnloop без задания, захваты без задания
    старше claim_ttl_s. -> {"timeout": n, "orphans": n}."""
    pc = port_cfg(cfg)
    rep = {"timeout": 0, "orphans": 0}
    for jid, job in own(root):
        if result(root, jid) is None and float(now) - float(job.get("ts") or 0) > pc["llm_wait_s"]:
            cancel(root, jid)
            rep["timeout"] += 1
    d = jobs_dir(root)
    try:
        names = os.listdir(d)
    except OSError:
        return rep
    for nm in names:
        if not nm.startswith("L-"):
            continue
        if nm.endswith(".result.json"):
            jid = nm[:-len(".result.json")]
            if os.path.exists(_p(root, jid, ".json")):
                continue
            r = _read(os.path.join(d, nm))
            if isinstance(r, dict) and r.get("kind") == KIND and _rm(os.path.join(d, nm)):
                rep["orphans"] += 1
        elif nm.endswith(".claim"):
            jid = nm[:-len(".claim")]
            if os.path.exists(_p(root, jid, ".json")) or os.path.exists(_p(root, jid, ".result.json")):
                continue
            age = claim_age(root, jid, now)
            if age is not None and age > pc["claim_ttl_s"] and _rm(os.path.join(d, nm)):
                rep["orphans"] += 1
    return rep
