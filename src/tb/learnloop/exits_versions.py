"""Версии политики выхода, продвижение, откат, снимок и аренда `tb.learnloop.exits_versions` (R4, задача 13; Requirement 7 целиком,
2.7, 8.3, 8.6, 16; design.md «tb.learnloop.exits_*», «Снимок и аренда», «Коды причин», свойства 10 и 19).

Версии `X-NNNN` живут в `exit_versions` (append-only, хеш-цепочка `prev_h`/`row_h` через `store2.append_row`; `X-0001` — пол защиты,
причина `INIT`). Причины создания версии: `INIT`, `PROMOTE`, `ROLLBACK`, `FLOOR_CHANGED`, `OWNER_ROLLBACK` (код `OWNER_ROLLBACK` —
команда владельца вне пакета `tb.learnloop`, здесь поддержана для единого писателя версий).

`init(conn, floor_policy, now)`      `X-0001` из пола защиты; идемпотентно, возвращает действующую версию, если она уже есть.
`promotion_ready(conn, cfg, now)`    готовность продвижения (Requirement 7.1): свежий сторож, проверенная копия базы не старше 24 часов,
                                     режим `promotion.mode = "auto"` (режим проверяет шаг, здесь только сведения). -> {"ok", "failed", "details"}.
`promote(conn, root, trial_id, now)` продвинуть одно закрытое испытание с вердиктом `promote`: версия `PROMOTE`, снимок
                                     `runtime/exit_policy.json` и аренда `runtime/exit_lease.json` с новыми `ver`/`policy_h` (сначала снимок,
                                     затем аренда — Requirement 7.1, 7.4; ошибка записи не оставляет аренду с новым хешем при старом снимке).
                                     Нет готовности -> вариант ждёт `READY` не дольше `promotion.wait_days`, затем испытание закрывается
                                     `inconclusive` (`STALE`) — Requirement 7.6. Повтор после успеха возвращает ту же версию (идемпотентность).
`run(conn, root, cfg, now)`          проход продвижения: инициализация `X-0001`, версия `FLOOR_CHANGED` при смене пола, не больше одной
                                     версии `PROMOTE` за проход (Requirement 7.3); остальные закрытые `promote` испытания прохода закрываются
                                     `inconclusive` (`BASE_CHANGED`, Requirement 6.8). Событие шины `exit_policy_promoted` (`info`).
`floor_changed(conn, root, now)`     Requirement 2.7: если действующая версия перестала проходить `weaker_than` относительно нового пола,
                                     создаётся версия, равная новому полу (причина `FLOOR_CHANGED`), и открытые испытания закрываются
                                     `inconclusive` (`FLOOR_CHANGED`). Пол не строже версии — новой версии нет, испытания всё равно
                                     закрываются по смене поля (`criterion.floor_h`).
`rollback(conn, root, reason, numbers, now)`
                                     откат: версия с политикой предыдущей версии (`ROLLBACK`) или пола (`OWNER_ROLLBACK`), снимок и аренда
                                     (`by="rollback"`), чёрный список откатанного варианта на `cooldown_days`, событие `exit_policy_rollback`.

Снимок пишется первым, аренда — второй; каждая запись атомарна (временный файл в `runtime/` и `fsutil.replace`). Если запись снимка
не удалась, аренда не трогается; если после удачного снимка не удалась аренда, действующая аренда остаётся старой и `exitdoc` возвращает
пол по `HASH`/`LEASE` (Requirement 8.6) — испорченного нового хеша в аренде нет. Повторный `promote`/`run` дозаписывает снимок и аренду
для уже записанной версии, поэтому сбой публикации исправляется следующим проходом.

«Свежий сторож» читается из `meta.exits_watch` (его пишет задача 15). Формат не зафиксирован спекой; принимаются числовые метки
времени в полях `ts`, `last_ts`, `last_check_ts`, `last_run_ts`, `checked_ts`, `ok_ts`, `last_ok_ts`, `updated_ts`, `watch_ts`, `pass_ts`
(в том числе во вложенных `last`/`state`/`point`/`watch`/`result`/`step`) либо само число/строка-число. Записи нет — сторож не
отработал: готовность не выполнена (`WATCH`, как отсутствие аренды сторожа в R2), продвижение ждёт `READY` и по `wait_days` уходит
в `STALE`. Свежесть — не старше `promotion.watch_fresh_h` часов и не дальше `FUTURE_SLACK_S` в будущее.

Продвижение и откат не трогают `ctx_ver` теневых политик, `rules_ver` и `config/` (Requirement 7.5, 14.1, 18.2): только чтение конфигов
и запись `runtime/` и БД. Слои `tb.learnloop`: без мозга, шлюза, биржи, исполнителя, управления и CLI.
"""
import json
import os
import re
import time

from tb import fsutil
from tb.core import bus
from tb.core import exitdoc as XD
from tb.knowledge import store2
from tb.labels import store as LST
from tb.learnloop import exits_conf as EC
from tb.learnloop import exits_trial as ET
from tb.logic import exitpol as XP

STEP = "exits_promote"
HOUR = 3600
DAY = 86400
FIRST_VER = XD.FLOOR_VER
RE_VER = re.compile(r"^X-(\d{4,})$")
VERSION_REASONS = ("INIT", "PROMOTE", "ROLLBACK", "FLOOR_CHANGED", "OWNER_ROLLBACK")
BACKUP_MAX_AGE_H = 24                       # Requirement 7.1: копия базы не старше 24 часов
WATCH_META = "exits_watch"                  # meta.exits_watch (пишет сторож, задача 15)
WATCH_TS_KEYS = ("ts", "last_ts", "last_check_ts", "last_run_ts", "checked_ts", "ok_ts", "last_ok_ts", "updated_ts", "watch_ts", "pass_ts")
WATCH_NESTED = ("last", "state", "point", "watch", "result", "step")
PROMOTE_EVENT = "exit_policy_promoted"
ROLLBACK_EVENT = "exit_policy_rollback"
OFF = "OFF"
READY = "READY"
VERSION_COLS = ("seq", "ver", "parent", "reason_code", "policy", "policy_h", "floor_h", "numbers", "actor", "ts")


class PromoteRefused(Exception):
    """Отказ продвижения/отката без создания версии: E_PROMOTE_STATE, E_PROMOTE_TRIAL, E_PROMOTE_POLICY, E_ROLLBACK_STATE, E_REASON."""

    def __init__(self, code, msg=""):
        self.code = code
        super().__init__("%s: %s" % (code, msg) if msg else code)


# ---------------------------------------------------------------- мелочи

def _num(v):
    """Число (не bool, конечное) или None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if v == v and abs(v) != float("inf") else None


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


def _iso(now):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(now)))


# ---------------------------------------------------------------- имена версий

def ver_name(n):
    return "X-%04d" % int(n)


def ver_num(ver):
    m = RE_VER.match(str(ver or ""))
    return int(m.group(1)) if m else 0


def _tx(conn):
    """Транзакция вызывающего или своя (BEGIN IMMEDIATE через labels.store.tx)."""
    return LST.tx(conn)


# ---------------------------------------------------------------- чтение версий

def _row(r):
    d = dict(zip(VERSION_COLS, r))
    d["seq"] = _int(d["seq"], 0)
    d["policy"] = _j(d["policy"]) or {}
    d["numbers"] = _j(d["numbers"])
    d["ts"] = _int(d["ts"], 0)
    return d


def versions(conn):
    """Строки `exit_versions` по `seq` (политика и числа разобраны); пусто, если таблицы нет."""
    try:
        rows = conn.execute("SELECT %s FROM exit_versions ORDER BY seq" % ", ".join(VERSION_COLS)).fetchall()
    except Exception:                                    # noqa: BLE001 — таблиц x1 может не быть
        return []
    return [_row(r) for r in rows]


def last_version(conn):
    """Действующая версия (последняя по `seq`) или None."""
    rows = versions(conn)
    return rows[-1] if rows else None


def version_row(conn, ver):
    """Запись версии по имени или None."""
    for r in versions(conn):
        if r["ver"] == str(ver):
            return r
    return None


def active_version(conn):
    """Имя действующей версии или None."""
    r = last_version(conn)
    return r["ver"] if r else None


def _next_ver(conn):
    """Следующее имя `X-NNNN`: на единицу больше наибольшего номера (мусорное имя — по `seq`)."""
    n = 0
    for r in versions(conn):
        n = max(n, ver_num(r["ver"]) or r["seq"])
    return ver_name(n + 1)


# ---------------------------------------------------------------- запись версии

def _append_version(conn, ver, parent, reason, policy, policy_h, floor_h, numbers, now):
    """Строка `exit_versions` (append-only, хеш-цепочка store2.append_row)."""
    rec = {"ver": ver, "parent": parent, "reason_code": reason, "policy": policy, "policy_h": policy_h, "floor_h": floor_h,
           "numbers": numbers, "actor": "code", "ts": int(now)}
    with _tx(conn):
        return store2.append_row(conn, "exit_versions", rec)


def init(conn, floor_policy, now=None):
    """`X-0001` из пола защиты (Requirement 2.2): версия `INIT`, политика = пол, `floor_h` = хеш пола. Идемпотентно:
    если версии уже есть, возвращается действующая. -> имя версии."""
    now = int(time.time() if now is None else now)
    cur = last_version(conn)
    if cur is not None:
        return cur["ver"]
    floor = floor_policy if isinstance(floor_policy, dict) else XP.floor({})
    policy = XP.normalize(floor, floor)
    errs = XP.validate(policy)
    if errs:
        raise PromoteRefused("E_PROMOTE_FLOOR", ",".join(errs))
    _append_version(conn, FIRST_VER, None, "INIT", policy, XP.policy_hash(policy), XP.policy_hash(floor), None, now)
    _log(conn, "INIT", FIRST_VER, {"floor_h": XP.policy_hash(floor)}, now)
    return FIRST_VER


def _log(conn, code, ref, body, now, error=False):
    try:
        store2.log_event(conn, STEP, code, "code", ref=ref, body=body, error=error, now=now)
    except Exception:                                    # noqa: BLE001 — журнал вторичен, версия важнее
        pass


# ---------------------------------------------------------------- готовность продвижения

def watch_state(conn):
    """Разобранное `meta.exits_watch` (dict/число/строка) или None: нет таблицы/ключа, мусор."""
    try:
        row = conn.execute("SELECT v FROM meta WHERE k=?", (WATCH_META,)).fetchone()
    except Exception:                                    # noqa: BLE001 — нет таблицы meta
        return None
    if not row or row[0] is None:
        return None
    raw = row[0]
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None


def _watch_ts(obj, depth=0):
    """Метка времени сторожа из `meta.exits_watch` (число, строка-число или известный ключ, в том числе вложенный)."""
    if isinstance(obj, bool) or obj is None or depth > 3:
        return None
    if isinstance(obj, (int, float)):
        return float(obj)
    if isinstance(obj, str):
        try:
            return float(obj.strip())
        except ValueError:
            return None
    if not isinstance(obj, dict):
        return None
    for k in WATCH_TS_KEYS:
        v = _num(obj.get(k))
        if v is not None:
            return v
    for k in WATCH_NESTED:
        v = obj.get(k)
        if isinstance(v, (dict, int, float, str)):
            ts = _watch_ts(v, depth + 1)
            if ts is not None:
                return ts
    return None


def promotion_ready(conn, cfg, now, root=None, floor_policy=None):
    """Готовность продвижения (Requirement 7.1) -> {"ok", "failed": [коды], "details"}.

    `WATCH`  — `meta.exits_watch` нет, метки времени нет, метка старше `promotion.watch_fresh_h` или дальше `FUTURE_SLACK_S` в будущее
               (записи нет — сторож ещё не отработал: ждём `READY`, затем `STALE`, как отсутствие аренды сторожа в R2);
    `BACKUP` — нет проверенных копий, последняя старше 24 часов или не проходит `store2.check_copy` (`integrity_check` и цепочки).
    Режим `promotion.mode` здесь не проверяется (его проверяют `promote` и `run`).
    """
    now = int(time.time() if now is None else now)
    p = (cfg or {}).get("promotion") if isinstance((cfg or {}).get("promotion"), dict) else {}
    fresh_h = _int(p.get("watch_fresh_h"), 0) or 0
    failed, det = [], {}
    st = watch_state(conn)
    ts = _watch_ts(st)
    if ts is None:
        det["watch"] = {"why": "NO_STATE"}
        failed.append("WATCH")
    else:
        age = now - ts
        det["watch"] = {"ts": int(ts), "age_s": int(age)}
        if fresh_h <= 0 or age > fresh_h * HOUR or age < -XD.FUTURE_SLACK_S:
            det["watch"]["why"] = "WATCH_STALE"
            failed.append("WATCH")
    bks = []
    if root is not None:
        try:
            bks = store2.list_backups(root)
        except OSError:
            bks = []
    last = bks[-1] if bks else None
    if last is None:
        det["backup"] = {"why": "NO_BACKUP"}
        failed.append("BACKUP")
    else:
        age = now - int(last["ts"])
        det["backup"] = {"path": last["path"], "reason": last["reason"], "age_s": age}
        if age < -XD.FUTURE_SLACK_S or age > BACKUP_MAX_AGE_H * HOUR:
            det["backup"]["why"] = "BACKUP_STALE"
            failed.append("BACKUP")
        else:
            try:
                ok, code, detail = store2.check_copy(last["path"])
            except Exception as e:                   # noqa: BLE001 — проверка копии не должна бросать наружу
                ok, code, detail = False, type(e).__name__, str(e)
            det["backup"]["check"] = {"ok": bool(ok), "code": code}
            if not ok:
                det["backup"]["why"] = code
                failed.append("BACKUP")
    if isinstance(floor_policy, dict):
        det["floor_h"] = XP.policy_hash(floor_policy)
    return {"ok": not failed, "failed": failed, "details": det}


# ---------------------------------------------------------------- снимок и аренда

def _write_json(path, obj):
    """Атомарная запись JSON: временный файл рядом и fsutil.replace (Windows — с повторами)."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        fsutil.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def publish(root, ver, policy, policy_h, floor_h, now, by):
    """Снимок действующей версии и аренда с новыми `ver`/`policy_h` (Requirement 7.1, 7.4, 8.3): снимок первым, аренда второй.
    Любая ошибка записи пробрасывается; аренда с новым хешем не появляется, пока не записан снимок."""
    snap = {"schema": XD.SNAPSHOT_SCHEMA, "ver": str(ver), "policy": policy, "policy_h": str(policy_h),
            "floor_h": str(floor_h), "ts": int(now)}
    lease = {"ts": int(now), "ver": str(ver), "policy_h": str(policy_h), "by": str(by)}
    _write_json(XD.snapshot_path(root), snap)
    _write_json(XD.lease_path(root), lease)
    XD.clear_cache()


def _publish_row(root, row, now, by):
    publish(root, row["ver"], row["policy"], row["policy_h"], row["floor_h"], now, by)


def _by_reason(reason):
    """`by` аренды по причине версии (design.md: watch | promote | rollback)."""
    return "rollback" if reason in ("ROLLBACK", "OWNER_ROLLBACK") else "promote"


def _active_published(root, row):
    """Снимок и аренда соответствуют строке версии (по `ver`/`policy_h`/`policy`). Ошибка чтения/мусор -> False."""
    if row is None:
        return True
    try:
        with open(XD.snapshot_path(root), encoding="utf-8") as f:
            snap = json.load(f)
        with open(XD.lease_path(root), encoding="utf-8") as f:
            lease = json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    return (isinstance(snap, dict) and isinstance(lease, dict) and snap.get("schema") == XD.SNAPSHOT_SCHEMA
            and snap.get("ver") == row["ver"] and snap.get("policy_h") == row["policy_h"] and snap.get("policy") == row["policy"]
            and lease.get("ver") == row["ver"] and lease.get("policy_h") == row["policy_h"])


def _repair_active(conn, root, now):
    """Дозапись снимка и аренды для действующей версии, если публикация прошлого прохода не удалась -> имя версии или None."""
    cur = last_version(conn)
    if cur is None or _active_published(root, cur):
        return None
    if cur["reason_code"] == "INIT" and not os.path.exists(XD.snapshot_path(root)):
        return None                                      # X-0001 — пол: исполнитель получает его без снимка (Requirement 8.6)
    _publish_row(root, cur, now, _by_reason(cur["reason_code"]))
    _log(conn, "PUBLISH_REPAIRED", cur["ver"], {"by": _by_reason(cur["reason_code"])}, now)
    return cur["ver"]


# ---------------------------------------------------------------- испытания и ожидание

def _applied(conn, trial_id):
    """Версия, созданная этим испытанием (по numbers.trial в `exit_versions`), или None."""
    for r in versions(conn):
        n = r.get("numbers") if isinstance(r.get("numbers"), dict) else {}
        if n.get("trial") == trial_id or n.get("trial_id") == trial_id:
            return r
    return None


def _pending(conn):
    """Закрытые испытания с вердиктом `promote`, для которых версии ещё нет (по `closed_ts`, `trial_id`)."""
    out = []
    for t in ET.trials(conn, ET.CLOSED):
        if t.get("verdict") != ET.PROMOTE:
            continue
        if _applied(conn, t["trial_id"]) is not None:
            continue
        out.append(t)
    out.sort(key=lambda t: (int(t.get("closed_ts") or 0), str(t["trial_id"])))
    return out


def _set_wait(conn, trial, now):
    """Отметка ожидания `READY` в `numbers` (непрерывное ожидание: та же причина не переписывается). -> wait_since_ts."""
    nums = dict(trial.get("numbers") or {})
    since = _int(nums.get("wait_since_ts"))
    if nums.get("wait") == READY and since is not None:
        return since
    nums["wait"] = READY
    nums["wait_since_ts"] = int(now)
    with _tx(conn):
        store2.update_row(conn, "exit_trials", trial["trial_id"], {"numbers": nums})
    trial["numbers"] = nums
    _log(conn, READY, trial["trial_id"], {"since": int(now)}, now)
    return int(now)


def _close_inconclusive(conn, trial, code, now, reason=None):
    """Закрытое испытание -> `inconclusive` с кодом (STALE, BASE_CHANGED, FLOOR_CHANGED). Идемпотентно: тот же код не переписывается."""
    nums = dict(trial.get("numbers") or {})
    if trial.get("verdict") == ET.INCONCLUSIVE and nums.get("code") == code:
        return None
    nums.pop("wait", None)
    nums.pop("wait_since_ts", None)
    nums["code"] = code
    if reason:
        nums["reason"] = reason
    with _tx(conn):
        store2.update_row(conn, "exit_trials", trial["trial_id"],
                          {"state": ET.CLOSED, "verdict": ET.INCONCLUSIVE, "numbers": nums, "closed_ts": int(now)})
    trial["verdict"], trial["numbers"] = ET.INCONCLUSIVE, nums
    _log(conn, code, trial["trial_id"], {"var": trial.get("var_id"), "ver": trial.get("var_ver"), "reason": reason}, now)
    return code


def _wait_or_stale(conn, trial, cfg, now):
    """Не готово: ждать READY не дольше `promotion.wait_days`, затем `inconclusive` STALE (Requirement 7.6) -> "wait"|"stale"."""
    since = _set_wait(conn, trial, now)
    days = _int(((cfg or {}).get("promotion") or {}).get("wait_days"), 0) or 0
    if days > 0 and now - since > days * DAY:
        _close_inconclusive(conn, trial, "STALE", now, reason="STALE")
        return "stale"
    return "wait"


# ---------------------------------------------------------------- политика испытания

def _floor(root, floor_policy=None):
    """Пол: явная политика, иначе `exitpol.floor(exec_limits.auto_protect)` (Requirement 2.2)."""
    if isinstance(floor_policy, dict):
        return floor_policy
    fl = EC.current_floor(root)
    return fl if isinstance(fl, dict) else None


def _trial_policy(conn, trial):
    """Политика варианта из `exit_variants` (как её записала регистрация испытания) или None."""
    try:
        row = conn.execute("SELECT policy FROM exit_variants WHERE id=? AND ver=?", (trial["var_id"], int(trial["var_ver"]))).fetchone()
    except Exception:                                    # noqa: BLE001
        return None
    p = _j(row[0]) if row else None
    return p if isinstance(p, dict) and p else None


def _promotion_numbers(trial, parent):
    """Компактные числа испытания в версию (Requirement 7.1) — без больших справочных блоков."""
    n = trial.get("numbers") if isinstance(trial.get("numbers"), dict) else {}
    out = {"trial": trial["trial_id"], "var": trial["var_id"], "var_ver": int(trial["var_ver"]), "parent": parent}
    for k in ("n_blocks", "mean_d", "p", "brain_lb", "tail_ratio", "cut_share", "amb_share", "b", "cut_ts"):
        if n.get(k) is not None:
            out[k] = n[k]
    return out


# ---------------------------------------------------------------- продвижение

def promote(conn, root, trial_id, now, cfg=None, floor_policy=None, ready=None):
    """Продвинуть закрытое испытание `promote` (Requirement 7.1, 7.4, 7.6) -> имя версии или None (ждёт READY, режим off,
    испытание закрыто `STALE`/`BASE_CHANGED`/`FLOOR_CHANGED`).

    Готовность: свежий сторож (`meta.exits_watch`), проверенная копия базы не старше 24 часов, `promotion.mode = "auto"`.
    Не готово — ожидание `READY` с меткой в `exit_trials.numbers`; дольше `promotion.wait_days` — `inconclusive` (`STALE`).
    Повтор для уже применённого испытания возвращает записанную версию и дозаписывает снимок/аренду (исправление сбоя публикации).
    """
    now = int(time.time() if now is None else now)
    if cfg is None:
        cfg, errs = EC.load(root)
        if errs:
            return None
    if not EC.is_enabled(cfg) or EC.promotion_mode(cfg) != "auto":
        return None
    trial = ET.get(conn, str(trial_id))
    if trial is None:
        raise PromoteRefused("E_PROMOTE_TRIAL", str(trial_id))
    done = _applied(conn, trial["trial_id"])
    if done is not None:
        cur = last_version(conn)
        if cur is not None and cur["ver"] == done["ver"]:    # дозапись снимка только для действующей версии (иначе откат/смена пола)
            _publish_row(root, done, now, "promote")
        return done["ver"]
    if trial.get("state") != ET.CLOSED or trial.get("verdict") != ET.PROMOTE:
        raise PromoteRefused("E_PROMOTE_STATE", "%s: %s/%s" % (trial["trial_id"], trial.get("state"), trial.get("verdict")))
    if not ET.criterion_ok(trial):
        raise PromoteRefused("E_PROMOTE_CRITERION", trial["trial_id"])
    active = active_version(conn)
    if active != trial.get("base_ver"):
        _close_inconclusive(conn, trial, ET.BASE_CHANGED, now, reason=ET.BASE_CHANGED)
        return None
    floor = _floor(root, floor_policy)
    if not isinstance(floor, dict):
        raise PromoteRefused("E_PROMOTE_FLOOR", "пол не читается")
    floor_h = XP.policy_hash(floor)
    crit = trial.get("criterion") or {}
    if crit.get("floor_h") and crit["floor_h"] != floor_h:
        _close_inconclusive(conn, trial, ET.FLOOR_CHANGED, now, reason=ET.FLOOR_CHANGED)
        return None
    rd = ready if isinstance(ready, dict) else promotion_ready(conn, cfg, now, root=root, floor_policy=floor)
    if not rd.get("ok"):
        _wait_or_stale(conn, trial, cfg, now)
        return None
    policy = _trial_policy(conn, trial)
    if policy is None:                                   # вариант, зарегистрированный без строки exit_variants: ищем в меню
        try:
            policy = next((v["policy"] for v in EC.variants(cfg, floor)
                           if v["id"] == trial["var_id"] and int(v["ver"]) == int(trial["var_ver"])), None)
        except (ValueError, TypeError, KeyError):
            policy = None
    if policy is None:
        raise PromoteRefused("E_PROMOTE_POLICY", trial["trial_id"])
    policy = XP.normalize(policy, floor)
    errs = XP.validate(policy) + XP.weaker_than(policy, floor)
    if errs:
        _close_inconclusive(conn, trial, ET.FLOOR_CHANGED, now, reason=ET.FLOOR_CHANGED)
        return None
    parent = active
    ver = _next_ver(conn)
    policy_h = XP.policy_hash(policy)
    nums = _promotion_numbers(trial, parent)
    _append_version(conn, ver, parent, "PROMOTE", policy, policy_h, floor_h, nums, now)
    try:
        publish(root, ver, policy, policy_h, floor_h, now, "promote")
    except OSError as e:
        _log(conn, "PUBLISH_ERROR", ver, {"stage": "snapshot/lease", "error": type(e).__name__}, now, error=True)
        raise
    try:
        seq = bus.emit(PROMOTE_EVENT, {"ver": ver, "trial": trial["trial_id"], "var": trial["var_id"]}, root=root, now=now)
    except (OSError, ValueError) as e:                   # версия и снимок уже записаны; событие вторично
        seq = None
        _log(conn, "BUS_ERROR", ver, {"error": type(e).__name__}, now, error=True)
    _clear_wait(conn, trial, now)
    _log(conn, "PROMOTE", ver, {"trial": trial["trial_id"], "var": trial["var_id"], "var_ver": int(trial["var_ver"]),
                                "policy_h": policy_h, "floor_h": floor_h, "bus_seq": seq, "by": "promote"}, now)
    return ver


def _clear_wait(conn, trial, now):
    """Снять метку ожидания READY после успешного продвижения (без изменения критерия)."""
    nums = dict(trial.get("numbers") or {})
    if "wait" not in nums and "wait_since_ts" not in nums:
        return
    nums.pop("wait", None)
    nums.pop("wait_since_ts", None)
    with _tx(conn):
        store2.update_row(conn, "exit_trials", trial["trial_id"], {"numbers": nums})
    trial["numbers"] = nums


# ---------------------------------------------------------------- смена пола (Requirement 2.7)

def _close_floor_trials(conn, floor_h, now, cfg=None):
    """Открытые испытания, чей `criterion.floor_h` отличается от текущего, -> `inconclusive` FLOOR_CHANGED. -> число закрытых."""
    closed = 0
    for t in ET.trials(conn, ET.OPEN):
        fh = (t.get("criterion") or {}).get("floor_h")
        if fh and fh != floor_h:
            if _close_inconclusive(conn, t, ET.FLOOR_CHANGED, now, reason=ET.FLOOR_CHANGED) is not None:
                closed += 1
    return closed


def floor_changed(conn, root, now, cfg=None, floor_policy=None):
    """Requirement 2.7: смена пола защиты.

    Открытые испытания с прежним `criterion.floor_h` закрываются `inconclusive` (`FLOOR_CHANGED`) всегда. Если действующая версия
    перестала проходить `weaker_than` относительно нового пола, создаётся версия причины `FLOOR_CHANGED`, равная новому полу
    (снимок и аренда с новыми `ver`/`policy_h`). Пол не строже версии — новой версии нет. -> имя версии или None.
    """
    now = int(time.time() if now is None else now)
    floor = _floor(root, floor_policy)
    if not isinstance(floor, dict):
        return None
    floor_h = XP.policy_hash(floor)
    _close_floor_trials(conn, floor_h, now, cfg)
    cur = last_version(conn)
    if cur is None:
        init(conn, floor, now)
        return None
    policy = XP.normalize(floor, floor)
    errs = XP.validate(policy)
    if errs:
        raise PromoteRefused("E_PROMOTE_FLOOR", ",".join(errs))
    weak = XP.weaker_than(XP.normalize(cur["policy"], floor), floor)
    if cur.get("floor_h") == floor_h or not weak:
        return None
    ver = _next_ver(conn)
    policy_h = XP.policy_hash(policy)
    nums = {"from": cur["ver"], "floor_h": floor_h, "weak": weak}
    _append_version(conn, ver, cur["ver"], "FLOOR_CHANGED", policy, policy_h, floor_h, nums, now)
    try:
        publish(root, ver, policy, policy_h, floor_h, now, "promote")
    except OSError as e:
        _log(conn, "PUBLISH_ERROR", ver, {"stage": "snapshot/lease", "error": type(e).__name__}, now, error=True)
        raise
    _log(conn, "FLOOR_CHANGED", ver, nums, now)
    return ver


# ---------------------------------------------------------------- откат

def _blacklist_rolled_back(conn, ver_row, reason, now, cfg):
    """Чёрный список откатанного варианта на `cooldown_days` (Requirement 8.3). -> (var_id, var_ver) или None."""
    n = ver_row.get("numbers") if isinstance(ver_row.get("numbers"), dict) else {}
    var_id, var_ver = n.get("var"), n.get("var_ver")
    if var_id is None or var_ver is None:
        return None
    days = _int((cfg or {}).get("cooldown_days"), 0) or 0
    try:
        with _tx(conn):
            conn.execute("INSERT INTO exit_blacklist(var_id, var_ver, until_ts, reason, ts) VALUES(?,?,?,?,?)",
                         (str(var_id), int(var_ver), int(now) + days * DAY, str(reason), int(now)))
    except Exception:                                    # noqa: BLE001 — чёрный список вторичен для записи версии
        return None
    return str(var_id), int(var_ver)


def rollback(conn, root, reason="ROLLBACK", numbers=None, now=None, cfg=None):
    """Откат действующей версии (Requirement 8.3, 8.9, 16.3) -> имя новой версии.

    `ROLLBACK` — политика предыдущей версии (родитель действующей либо предыдущая запись), чёрный список откатанного варианта на
    `cooldown_days`; `OWNER_ROLLBACK` — политика текущего пола (команда владельца). Снимок и аренда пишутся с причиной `by="rollback"`,
    событие шины `exit_policy_rollback` (`info`). Открытые позиции откат не трогает: они ведут версию, записанную при входе
    (Requirement 8.9) — это забота исполнителя.
    """
    now = int(time.time() if now is None else now)
    if reason not in ("ROLLBACK", "OWNER_ROLLBACK"):
        raise PromoteRefused("E_REASON", str(reason))
    cur = last_version(conn)
    if cur is None:
        raise PromoteRefused("E_ROLLBACK_STATE", "версий нет")
    floor = _floor(root) or XP.floor({})
    if reason == "OWNER_ROLLBACK":
        policy = XP.normalize(floor, floor)
        src_ver = None
    else:
        prev = version_row(conn, cur.get("parent")) if cur.get("parent") else None
        if prev is None:
            rows = versions(conn)
            prev = rows[-2] if len(rows) > 1 else None
        if prev is None:
            policy = XP.normalize(floor, floor)
            src_ver = None
        else:
            policy = XP.normalize(prev["policy"], floor)
            src_ver = prev["ver"]
    errs = XP.validate(policy)
    if errs:
        raise PromoteRefused("E_ROLLBACK_STATE", ",".join(errs))
    floor_h = XP.policy_hash(floor)
    policy_h = XP.policy_hash(policy)
    ver = _next_ver(conn)
    nums = dict(numbers or {})
    nums["from"] = cur["ver"]
    nums["prev"] = src_ver
    nums["code"] = reason
    _append_version(conn, ver, cur["ver"], reason, policy, policy_h, floor_h, nums, now)
    try:
        publish(root, ver, policy, policy_h, floor_h, now, "rollback")
    except OSError as e:
        _log(conn, "PUBLISH_ERROR", ver, {"stage": "snapshot/lease", "error": type(e).__name__}, now, error=True)
        raise
    if cfg is None:
        cfg, _errs = EC.load(root)
    blocked = _blacklist_rolled_back(conn, cur, reason, now, cfg if isinstance(cfg, dict) else None)
    ev = {"ver": ver, "from": cur["ver"], "code": reason}
    for k in ("n", "mean_r", "p"):
        if nums.get(k) is not None:
            ev[k] = nums[k]
    try:
        seq = bus.emit(ROLLBACK_EVENT, ev, root=root, now=now)
    except (OSError, ValueError):                        # версия и снимок уже записаны
        seq = None
    _log(conn, reason, ver, {"from": cur["ver"], "prev": src_ver, "policy_h": policy_h, "blacklist": blocked, "bus_seq": seq}, now)
    return ver


# ---------------------------------------------------------------- проход

def run(conn, root, cfg, now, ready=None, floor_policy=None):
    """Проход продвижения (Requirement 7.1, 7.3, 7.6, 2.7) -> {"code", "promoted", "waiting", "rejected", "floor_changed", "ready"}.

    Порядок: режим `auto` -> `X-0001` при отсутствии версий -> версия `FLOOR_CHANGED` (если пол стал строже версии; в этом проходе
    `PROMOTE` уже не выполняется) -> продвижение не больше одной версии по закрытым `promote` испытаниям. Остальные закрытые `promote`
    испытания этого прохода после продвижения закрываются `inconclusive` (`BASE_CHANGED`, Requirement 6.8). Режим `off` и битый/выключенный
    конфиг ничего не меняют.
    """
    now = int(now)
    rep = {"code": "OK", "promoted": [], "waiting": [], "rejected": [], "refused": [], "floor_changed": None, "ready": None}
    if not EC.is_enabled(cfg):
        rep["code"] = "DISABLED"
        return rep
    if EC.promotion_mode(cfg) != "auto":
        rep["code"] = OFF
        return rep
    if last_version(conn) is None:
        init(conn, _floor(root, floor_policy) or XP.floor({}), now)
        rep["code"] = "INIT"
        return rep
    _repair_active(conn, root, now)                      # сбой публикации прошлого прохода исправляется здесь
    fc = floor_changed(conn, root, now, cfg=cfg, floor_policy=floor_policy)
    if fc:
        rep["floor_changed"] = fc
        rep["code"] = "FLOOR_CHANGED"
        return rep
    cands = _pending(conn)
    if not cands:
        return rep
    rd = ready if isinstance(ready, dict) else promotion_ready(conn, cfg, now, root=root, floor_policy=_floor(root, floor_policy))
    rep["ready"] = {"ok": bool(rd.get("ok")), "failed": list(rd.get("failed") or [])}
    if not rd.get("ok"):
        for t in cands:
            state = _wait_or_stale(conn, t, cfg, now)
            rep["waiting"].append({"trial": t["trial_id"], "state": state})
        return rep
    for i, t in enumerate(cands):
        try:
            ver = promote(conn, root, t["trial_id"], now, cfg=cfg, floor_policy=floor_policy, ready=rd)
        except PromoteRefused as e:
            _log(conn, e.code, t["trial_id"], {"stage": STEP}, now, error=True)
            rep["refused"].append({"trial": t["trial_id"], "code": e.code})
            continue
        if ver is None:
            cur = ET.get(conn, t["trial_id"]) or t
            state = "closed" if cur.get("verdict") == ET.INCONCLUSIVE else "wait"
            rep["waiting"].append({"trial": t["trial_id"], "state": state})
            continue
        rep["promoted"].append({"trial": t["trial_id"], "ver": ver, "var": t["var_id"]})
        for other in cands[i + 1:]:
            _close_inconclusive(conn, other, ET.BASE_CHANGED, now, reason=ET.BASE_CHANGED)
            rep["rejected"].append({"trial": other["trial_id"], "code": ET.BASE_CHANGED})
        break
    return rep


def step_promote(ctx):
    """Шаг прохода цикла обучения (интеграция в `learnloop.cycle` — задача 15)."""
    cfg, errs = EC.load(ctx.root)
    if errs:
        return {"code": "DISABLED", "promoted": 0}
    rep = run(ctx.conn, ctx.root, cfg, ctx.now)
    return {"code": rep["code"], "promoted": len(rep["promoted"]), "waiting": len(rep["waiting"]),
            "rejected": len(rep["rejected"]), "floor_changed": rep["floor_changed"]}
