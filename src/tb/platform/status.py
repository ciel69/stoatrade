"""Единый статус платформы (`./tb status`, дашборд `/api/platform`, MCP get_status). Владелец: WP11-B.

Контракт для читателей: `build_status(root) -> dict` (чистое чтение файлов, без сети и блокировок) либо файл `runtime/platform/status.json`
(пишет супервизор раз в status_s; у файла только блок сервисов, а живые поля — exec, kill, шина, сканер, непрочитанное — считает build_status).
Цвет: green | yellow | red | off (платформа не запущена). Красный: сервис сдался, исполнитель упал/не отвечает при открытых позициях.
"""
import os
import time

from tb import paths
from tb.core import bus, exec_cfg
from tb.core.filelock import pid_alive
from tb.platform import services as S

STALE_FILE_S = 15.0           # status.json старше этого = супервизор завис/упал (при живом pid жёлтый)
SCAN_STALE_X = 4.0            # возраст hot.json > 4 интервалов скана = сканер не обновляется


def supervisor_info(root=None):
    """-> {"running":bool,"pid":int,"started":float|None,...данные lock}."""
    d = S.read_json(S.lock_file(root))
    if not isinstance(d, dict):
        return {"running": False, "pid": 0}
    pid = int(d.get("pid") or 0)
    out = dict(d)
    out["pid"] = pid
    out["running"] = bool(pid and pid_alive(pid))
    return out


def _exec_block(root, now):
    from tb.brain import common
    h = common.exec_health(root=root, now=now)
    d = h.get("data") or {}
    pos = d.get("positions")
    return {"alive": bool(h.get("alive")), "present": bool(h.get("present")), "age_s": h.get("age_s"), "mode": d.get("mode"), "env": d.get("env"),
            "observe": d.get("observe"), "positions": pos if isinstance(pos, int) and not isinstance(pos, bool) else None, "hold": d.get("hold"),
            "plans": d.get("plans"), "exit_status": d.get("exit_status"), "exit_why": d.get("exit_why")}


def _bus_block(root, now):
    try:
        tail = bus.read_tail(1, root=root)
        last = tail[-1] if tail else None
        ts = bus.parse_iso(last.get("ts")) if last else None
        return {"last_seq": bus.last_seq(root=root), "age_s": None if ts is None else round(max(0.0, now - ts), 1)}
    except Exception:
        return {"last_seq": None, "age_s": None}


def _scan_block(root, now):
    hot = S.read_json(paths.runtime("hot.json", root=root))
    ts = hot.get("ts_epoch") if isinstance(hot, dict) else None
    return {"age_s": round(max(0.0, now - ts), 1) if isinstance(ts, (int, float)) else None}


LABELS_WARN = {"due": 500, "failed_share": 0.05}      # запасные пороги; рабочие - config/labels.json:warn


def labels_reasons(hb, warn=None):
    """Причины жёлтого статуса сервиса labels по его heartbeat: due > warn.due; failed_24h > warn.failed_share от захваченных за 24 ч; DB_CORRUPT и предупреждения."""
    w = dict(LABELS_WARN)
    w.update(warn if isinstance(warn, dict) else {})
    hb = hb if isinstance(hb, dict) else {}
    out = []
    due, failed, cap = hb.get("due"), hb.get("failed_24h"), hb.get("captured_24h")
    if isinstance(due, (int, float)) and not isinstance(due, bool) and due > w["due"]:
        out.append("разметчик labels: очередь созревших меток %d (порог %d)" % (due, w["due"]))
    if isinstance(failed, (int, float)) and not isinstance(failed, bool) and failed > 0:
        cap = cap if isinstance(cap, (int, float)) and not isinstance(cap, bool) else 0
        if failed > w["failed_share"] * max(cap, 1):
            out.append("разметчик labels: не размечено за 24 ч %d из %d захваченных (порог %d%%)" % (failed, cap, round(w["failed_share"] * 100)))
    if hb.get("db_state") == "corrupt":
        out.append("разметчик labels: база знаний повреждена (DB_CORRUPT), записи меток остановлены до восстановления (./tb knowledge verify)")
    elif hb.get("db_state") == "restoring":
        out.append("разметчик labels: идёт восстановление базы знаний, записи меток на паузе")
    elif hb.get("last_error") == "DB_CORRUPT":
        out.append("разметчик labels: база знаний была повреждена и пересоздана (DB_CORRUPT), старая лежит как state/knowledge.corrupt-*.sqlite")
    for x in hb.get("warnings") or []:
        if x == "W_LABELS_SYNC_DIR":
            out.append("разметчик labels: state/ в папке облачной синхронизации (W_LABELS_SYNC_DIR): SQLite WAL там ломается, перенеси проект")
    return out


def _labels_warn(root):
    try:
        from tb.labels import capture
        w = capture.load_cfg(root).get("warn")
        return w if isinstance(w, dict) else None
    except Exception:  # noqa: BLE001 - статус не должен падать из-за конфига меток
        return None


def knowledge_block(root, now):
    """R2 (Requirement 2.3, 2.4, 2.10, 16.4): ./tb knowledge verify (quick_check, цепочки, маркеры) для статуса. Без маркеров v2 и восстановления -> None
    (статус как в R1). -> {"color", "codes", "reasons"} или None."""
    try:
        from tb.knowledge import store2
        rep = store2.status_check(root, now)
    except Exception as e:  # noqa: BLE001 - статус не должен падать из-за проверки базы
        return {"color": "yellow", "codes": ["VERIFY_ERROR"], "reasons": ["база знаний: проверка не выполнена (%s)" % type(e).__name__]}
    if rep is None:
        return None
    return {"color": rep["color"], "codes": list(rep["codes"]), "reasons": list(rep["reasons"]), "store": rep.get("store"), "store_src": rep.get("store_src")}


LEARN_YELLOW = ("RULES_NO_EVENT", "WARMUP_STUCK", "AUTO_RESTORED", "STALE")
LEARN_RED = ("WATCH_STALE", "CHAIN_BROKEN", "DB_CORRUPT", "KNOWLEDGE_MIGRATION")


def learnloop_block(hb, now, running=True):
    """R2 (Requirement 15.5): состояние цикла обучения по heartbeat сервиса learnloop. Нет heartbeat -> None.
    Выключен (learnloop.enabled=false) -> {"state": "off", "color": "off"} без влияния на цвет платформы. Иначе жёлтый: RULES_NO_EVENT, WARMUP_STUCK,
    AUTO_RESTORED, нет успешного прохода дольше stale_min (STALE); красный: WATCH_STALE, CHAIN_BROKEN, DB_CORRUPT, KNOWLEDGE_MIGRATION."""
    if not isinstance(hb, dict) or not hb:
        return None
    if not hb.get("enabled"):
        errs = [e for e in hb.get("conf_errors") or [] if isinstance(e, str) and e != "E_CONF:nofile"]
        items = [{"code": "CONF", "text": "цикл обучения выключен: config/learnloop.json не годен (%s)" % ",".join(errs[:3])}] if errs else []
        return {"state": "off", "color": "yellow" if errs else "off", "codes": [it["code"] for it in items], "reasons": [it["text"] for it in items],
                "items": items, "mode": hb.get("mode"), "conf_errors": errs}
    items = [{"code": it.get("code") if isinstance(it.get("code"), str) else None, "text": it["text"]}
             for it in hb.get("items") or [] if isinstance(it, dict) and isinstance(it.get("text"), str)]
    ok_ts, sm = hb.get("last_ok_ts"), hb.get("stale_min")
    start = hb.get("run_start")
    if running and isinstance(sm, (int, float)) and not isinstance(sm, bool):
        ref = ok_ts if isinstance(ok_ts, (int, float)) else start
        if isinstance(ref, (int, float)) and now - ref > float(sm) * 60:
            items.append({"code": "STALE", "text": "цикл обучения: нет успешного прохода %d мин (порог %d)" % ((now - ref) // 60, sm)})
    codes = [it["code"] for it in items if it["code"]]
    reasons = [it["text"] for it in items]
    color = "red" if any(c in LEARN_RED for c in codes) else ("yellow" if items else "green")
    rd = hb.get("ready") if isinstance(hb.get("ready"), dict) else None
    ne = hb.get("no_event") if isinstance(hb.get("no_event"), dict) else {}
    cov = ne.get("coverage") if isinstance(ne.get("coverage"), dict) else None
    return {"state": hb.get("state"), "color": color, "codes": codes, "reasons": reasons, "items": items, "mode": hb.get("mode"),
            "ready": rd, "stubs": hb.get("stubs") or [], "day": hb.get("day"), "last_ok_ts": ok_ts, "coverage": cov}


def coverage_line(lb):
    """Информационная строка ./tb status (не влияет на цвет): доля решений entry_scout/chat вне области правил за 7 суток по видам события
    (rulebook_view.coverage_txt). Нет данных -> None."""
    cov = (lb or {}).get("coverage")
    if not cov:
        return None
    try:
        from tb.knowledge import rulebook_view as RV
        txt = RV.coverage_txt(cov)
    except Exception:  # noqa: BLE001 - строка справочная
        return None
    return ("покрытие правилами (7 сут): %s; правила проверены только на видах сигналов политики, на остальные входы не распространяются" % txt
            if txt else None)


def learnloop_line(lb):
    """Строка ./tb status про цикл обучения."""
    if lb.get("state") == "off":
        return "цикл обучения: выключен (learnloop.enabled=false)" + (", конфиг не годен" if lb.get("conf_errors") else "")
    rd = lb.get("ready") or {}
    day = lb.get("day") or {}
    ready = "готов" if rd.get("ok") else ("прогрев: " + ",".join(rd.get("failed") or []) if rd else "прогрев: -")
    return "цикл обучения: %s, %s, продвижение %s, %s; сутки %s: работа %s ч%s" % (
        {"green": "в норме", "yellow": "ЖЁЛТЫЙ", "red": "КРАСНЫЙ"}.get(lb.get("color"), lb.get("color")), lb.get("state"), lb.get("mode"), ready,
        day.get("day") or "-", day.get("work_h") if day.get("work_h") is not None else "-",
        ("; заглушки: %s" % ",".join(lb["stubs"])) if lb.get("stubs") else "")


CMKT_SNAP_MAX_AGE_S = 5.0     # R1.7, Requirement 11.4: снимок старше — жёлтый
CMKT_OUT_S = 300.0            # 2 и более бирж вне сводки дольше 300 с — жёлтый
CMKT_OUT_MIN = 2
CMKT_COV_MIN = 0.9            # доля сигналов монет в фокусе с признаками за 24 ч
CMKT_START_S = 15.0           # первые секунды после старта сводки снимка ещё может не быть
CMKT_WARN = ("CLOCK_SKEW", "BUDGET", "FOCUS_NO_HEAT")


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def cmkt_family(name):
    """Сводка `cmkt` или сборщик `cmkt@*`: красного статуса эти сервисы не дают (торговля от них не зависит)."""
    return name == "cmkt" or S.cmkt_market(name) is not None


def cmkt_cov(lhb):
    """Доля сигналов монет в фокусе с признаками ev.cm за 24 ч из heartbeat labels (`cm_cov_24h`: число или {"focus": число}); нет -> None."""
    v = (lhb or {}).get("cm_cov_24h") if isinstance(lhb, dict) else None
    if isinstance(v, dict):
        v = v.get("focus")
    return _num(v)


def cmkt_block(hb, now, snap_mtime=None, cov24=None, uptime_s=None):
    """R1.7 (Requirement 11.4): строка `cmkt` статуса по heartbeat сводки. Нет heartbeat -> None. Выключен -> {"state": "off", "color": "off"}.
    Жёлтый (красного нет): снимок старше 5 с (SNAP_STALE), 2 и более бирж вне сводки дольше 300 с (VENUES_OUT), CLOCK_SKEW, доля сигналов
    монет в фокусе с признаками за 24 ч < 0.9 (CM_COV), DISK_LOW, BUDGET, FOCUS_NO_HEAT; битый config/cmkt.json (CONF)."""
    if not isinstance(hb, dict) or not hb:
        return None
    if hb.get("state") == "OFF":
        return {"state": "off", "color": "off", "codes": [], "reasons": [], "items": []}
    items = []
    if hb.get("state") == "E_CONF":
        errs = [e for e in hb.get("errors") or [] if isinstance(e, str)]
        items.append(("CONF", "сводный рынок: config/cmkt.json не годен (%s), сбор не идёт" % ",".join(errs[:3])))
    else:
        mt = _num(snap_mtime)
        age = max(0.0, now - mt) if mt is not None else None
        if age is None and _num(hb.get("snap_ts")) is not None:
            age = max(0.0, now - _num(hb["snap_ts"]))
        started = uptime_s is None or uptime_s >= CMKT_START_S
        if age is not None and age > CMKT_SNAP_MAX_AGE_S:
            items.append(("SNAP_STALE", "сводный рынок: снимок не обновляется %d с (порог %d)" % (age, CMKT_SNAP_MAX_AGE_S)))
        elif age is None and started:
            items.append(("SNAP_STALE", "сводный рынок: снимка ещё нет"))
        out = [v for v, t in (hb.get("venues_out") or {}).items() if _num(t) is not None and now - float(t) > CMKT_OUT_S]
        if len(out) >= CMKT_OUT_MIN:
            items.append(("VENUES_OUT", "сводный рынок: вне сводки дольше %d мин биржи %s" % (CMKT_OUT_S // 60, ", ".join(sorted(out)))))
        warn = [w for w in hb.get("warn") or [] if isinstance(w, str)]
        store = hb.get("store") if isinstance(hb.get("store"), dict) else {}
        if store.get("disk_low") or hb.get("store_code") == "DISK_LOW":
            items.append(("DISK_LOW", "сводный рынок: мало места на диске (DISK_LOW), ряды не пишутся"))
        texts = {"CLOCK_SKEW": "сводный рынок: сбились локальные часы (CLOCK_SKEW, offset > 500 мс у 4 и более бирж)",
                 "BUDGET": "сводный рынок: превышен бюджет ресурсов (BUDGET), ярус light урезан",
                 "FOCUS_NO_HEAT": "сводный рынок: нет heat.json (FOCUS_NO_HEAT), в фокусе только ядро и динамика"}
        for c in CMKT_WARN:
            if c in warn:
                items.append((c, texts[c]))
        if cov24 is not None and cov24 < CMKT_COV_MIN:
            items.append(("CM_COV", "сводный рынок: признаки есть у %d%% сигналов монет в фокусе за 24 ч (порог %d%%)" % (round(cov24 * 100), round(CMKT_COV_MIN * 100))))
    n_ex = hb.get("n_ex") if isinstance(hb.get("n_ex"), dict) else {}
    return {"state": hb.get("state"), "color": "yellow" if items else "green", "codes": [c for c, _ in items], "reasons": [t for _, t in items],
            "items": [{"code": c, "text": t} for c, t in items], "n_ex": n_ex, "n_syms": hb.get("n_syms"), "cov24": cov24,
            "venues_out": sorted((hb.get("venues_out") or {}).keys()) if isinstance(hb.get("venues_out"), dict) else []}


def cmkt_line(cb):
    """Строка ./tb status про сводный рынок."""
    if cb.get("state") == "off":
        return "сводный рынок: выключен (config/cmkt.json: enabled=false)"
    n = cb.get("n_ex") or {}
    return "сводный рынок: %s, рынков в сводке perp %s / spot %s, монет %s%s%s" % (
        {"green": "в норме", "yellow": "ЖЁЛТЫЙ"}.get(cb.get("color"), cb.get("color")), n.get("perp", "-"), n.get("spot", "-"),
        cb.get("n_syms") if cb.get("n_syms") is not None else "-",
        ("; вне сводки: %s" % ",".join(cb["venues_out"])) if cb.get("venues_out") else "",
        ("; коды: %s" % ",".join(cb["codes"])) if cb.get("codes") else "")


def _cmkt_snap_mtime(root):
    try:
        return os.stat(paths.runtime("cmkt", "snap.json", root=root)).st_mtime
    except OSError:
        return None


def build_status(root=None, now=None):
    now = time.time() if now is None else now
    cfg = S.load_cfg(root)
    sup = supervisor_info(root)
    fresh = S.read_json(S.status_file(root))
    fresh = fresh if isinstance(fresh, dict) else {}
    file_age = None
    if isinstance(fresh.get("ts_epoch"), (int, float)):
        file_age = max(0.0, now - fresh["ts_epoch"])
    running = bool(sup.get("running"))
    svcs = {}
    for n, v in (fresh.get("services") or {}).items():
        if not isinstance(v, dict):
            continue
        v = dict(v)
        if running and v.get("state") == "running" and isinstance(v.get("started_ts"), (int, float)):
            v["uptime_s"] = round(max(0.0, now - v["started_ts"]), 1)
        if not running:
            v["state"] = "stopped" if v.get("state") in ("running", "starting", "backoff") else v.get("state")
            v["pid"] = None
        svcs[n] = v
    ex = _exec_block(root, now)
    kill = exec_cfg.kill_state(root)
    bus_b, scan_b = _bus_block(root, now), _scan_block(root, now)
    unread = S.read_json(S.unread_file(root))
    unread = unread if isinstance(unread, dict) else {}
    profile = sup.get("profile") if running else None
    reasons, color = [], "green"

    def worse(c):
        order = ("green", "yellow", "red")
        return c if order.index(c) > order.index(color) else color

    if not running:
        color = "off"
        reasons.append("платформа не запущена (./tb start)")
    else:
        if file_age is not None and file_age > STALE_FILE_S:
            color = worse("yellow")
            reasons.append("status.json не обновляется %d с: супервизор завис?" % file_age)
        for n, v in svcs.items():
            st = v.get("state")
            if st == "failed":
                color = worse("yellow" if cmkt_family(n) else "red")     # сводный рынок красного не даёт (R1.7, Requirement 11.4)
                reasons.append("сервис %s не поднимается (%s)" % (n, v.get("last_error") or "сбой"))
            elif st in ("backoff", "starting"):
                color = worse("yellow")
                reasons.append("сервис %s перезапускается (подряд сбоев: %s)" % (n, v.get("tries")))
            elif st == "unavailable":
                color = worse("yellow")
                reasons.append("сервис %s недоступен: %s" % (n, v.get("last_error") or "нет модуля"))
            elif st == "running" and v.get("hb_ok") is False:
                color = worse("yellow")
                reasons.append("сервис %s: heartbeat устарел" % n)
        bsv = svcs.get("brain") or {}
        if bsv.get("state") == "running":           # WP11-C2: API-мозг сообщает здоровье в heartbeat (red = «только защита», yellow = запасной профиль/бюджет/ошибки конфигурации)
            _a, bhb = S.read_hb("brain", root, now)
            if bhb.get("protect_only"):
                color = worse("red")
                reasons.append("мозг в режиме «только защита»: %s (новых входов нет, позиции ведёт исполнитель)" % (str(bhb.get("reason") or "-")[:80]))
            elif bhb.get("health") == "yellow":
                color = worse("yellow")
                reasons.append("мозг работает в ограниченном режиме (запасной профиль, бюджет или ошибки конфигурации): ./tb brain status")
        sc = (svcs.get("scan") or {})
        if sc.get("state") == "running" and scan_b["age_s"] is not None:
            lim = SCAN_STALE_X * float((cfg.get("scan") or {}).get("interval") or 30.0)
            if scan_b["age_s"] > max(lim, 120.0):
                color = worse("yellow")
                reasons.append("сканер не обновляет hot.json %d с" % scan_b["age_s"])
        if (svcs.get("labels") or {}).get("state") == "running":      # R1.6: очередь разметки и доля неразмеченных видны здесь, в шину разметчик не пишет
            _a, lhb = S.read_hb("labels", root, now)
            lr = labels_reasons(lhb, _labels_warn(root))
            if lr:
                color = worse("yellow")
                reasons.extend(lr)
    kb = knowledge_block(root, now)
    if kb is not None and kb["codes"]:
        if color != "off":
            color = worse(kb["color"])
        reasons.extend(kb["reasons"])
    lb = None
    if "learnloop" in svcs or (running and "learnloop" in (sup.get("services") or [])):
        _a, llhb = S.read_hb("learnloop", root, now)
        lb = learnloop_block(llhb, now, running=running and (svcs.get("learnloop") or {}).get("state") == "running")
        if lb is not None and running and (svcs.get("learnloop") or {}).get("state") == "running" and lb["color"] in ("yellow", "red"):
            color = worse(lb["color"])
            seen = set((kb or {}).get("codes") or [])          # DB_CORRUPT, CHAIN_BROKEN, AUTO_RESTORED уже показал блок базы знаний
            reasons.extend(it["text"] for it in lb["items"] if it["code"] is None or it["code"] not in seen)
    cb = None
    if "cmkt" in svcs or (running and "cmkt" in (sup.get("services") or [])):
        csv = svcs.get("cmkt") or {}
        _a, chb = S.read_hb("cmkt", root, now)
        _a, lhb2 = S.read_hb("labels", root, now)
        cb = cmkt_block(chb, now, snap_mtime=_cmkt_snap_mtime(root), cov24=cmkt_cov(lhb2), uptime_s=csv.get("uptime_s"))
        if cb is not None and running and csv.get("state") == "running" and cb["color"] == "yellow":
            color = worse("yellow")
            reasons.extend(cb["reasons"])
    exec_expected = "exec" in svcs or (running and "exec" in (sup.get("services") or []))
    if exec_expected and not ex["alive"] and running:
        if (svcs.get("exec") or {}).get("state") == "running" and (svcs.get("exec") or {}).get("uptime_s", 0) < 60:
            pass                                          # исполнитель только стартует
        else:
            color = worse("yellow")
            reasons.append("исполнитель не отвечает (heartbeat)")
    if running and exec_expected and ex.get("exit_status"):
        color = worse("yellow")
        reasons.append("исполнитель ведёт позиции полом защиты: %s" % ex["exit_status"])
    pos = ex.get("positions") or (fresh.get("positions_seen") or 0)
    if exec_expected and not ex["alive"] and pos:
        color = worse("red")
        reasons.append("ИСПОЛНИТЕЛЬ НЕ ЖИВ при открытых позициях (%s): они защищены биржевым стопом, но не ведутся; проверь вручную" % pos)
    if kill:
        color = worse("yellow") if color != "off" else color
        reasons.append("KILL активен (flatten=%s): новых входов нет" % bool(kill.get("flatten")))
    total = 0
    for u in unread.values():
        if isinstance(u, dict) and isinstance(u.get("unread"), int):
            total += u["unread"]
    links = {}
    for n in svcs:
        lk = S.link_of(n, cfg)
        if lk:
            links[n] = lk
    return {"ts_epoch": round(now, 3), "color": color, "reasons": reasons, "running": running,
            "supervisor": {"pid": sup.get("pid") or None, "started": sup.get("started"), "uptime_s": round(now - sup["started"], 1) if running and isinstance(sup.get("started"), (int, float)) else None,
                           "profile": profile, "observe": sup.get("observe") if running else None, "services": sup.get("services") if running else None},
            "services": svcs, "exec": ex, "kill": kill is not None, "kill_info": kill, "bus": bus_b, "scanner": scan_b, **({"knowledge": kb} if kb is not None else {}),
            **({"learnloop": lb} if lb is not None else {}), **({"cmkt": cb} if cb is not None else {}),
            "unread": unread, "unread_total": total, "links": links}


def format_line(st):
    """Короткая строка для статусной строки Claude Code: `TB paper G exec:ok scan:ok chat:3`."""
    mark = {"green": "OK", "yellow": "WARN", "red": "RED", "off": "OFF"}.get(st.get("color"), "?")
    prof = (st.get("supervisor") or {}).get("profile") or "-"
    parts = ["TB", prof, mark]
    cols = [v for n, v in (st.get("services") or {}).items() if S.cmkt_market(n) is not None]
    for n, v in (st.get("services") or {}).items():
        if S.cmkt_market(n) is not None:
            continue                                   # сборщики сводного рынка — одним счётчиком cmkt@:живых/всего
        parts.append("%s:%s" % (n, {"running": "ok", "failed": "DOWN", "backoff": "retry", "starting": "start", "unavailable": "n/a", "stopped": "off"}.get(v.get("state"), "?")))
    if cols:
        parts.append("cmkt@:%d/%d" % (sum(1 for v in cols if v.get("state") == "running"), len(cols)))
    for sid, u in (st.get("unread") or {}).items():
        if isinstance(u, dict) and u.get("unread"):
            parts.append("%s:%d%s" % (sid, u["unread"], "!" if u.get("wake_unread") else ""))
    if st.get("kill"):
        parts.append("KILL")
    return " ".join(parts)


def format_text(st):
    c = {"green": "ЗЕЛЁНЫЙ", "yellow": "ЖЁЛТЫЙ", "red": "КРАСНЫЙ", "off": "ВЫКЛЮЧЕНА"}.get(st["color"], st["color"])
    sup = st["supervisor"]
    out = ["платформа: %s" % c]
    if st["running"]:
        out.append("профиль %s%s, супервизор pid %s, аптайм %s" % (sup.get("profile"), " (observe)" if sup.get("observe") else "", sup.get("pid"), _dur(sup.get("uptime_s"))))
    for r in st["reasons"]:
        out.append("  ! " + r)
    out.append("%-9s %-11s %-7s %-8s %-9s %-4s %s" % ("сервис", "состояние", "pid", "аптайм", "heartbeat", "рст", "последняя ошибка"))
    for n, v in st["services"].items():
        hb = "-" if v.get("hb_age_s") is None else ("%ds%s" % (v["hb_age_s"], "" if v.get("hb_ok") is not False else " !"))
        out.append("%-9s %-11s %-7s %-8s %-9s %-4s %s" % (n, v.get("state"), v.get("pid") or "-", _dur(v.get("uptime_s")), hb, v.get("restarts", 0), v.get("last_error") or ""))
    ex = st["exec"]
    out.append("исполнитель: %s, режим %s, позиций %s, hold %s, KILL %s" % ("жив" if ex["alive"] else "НЕ ЖИВ", ex.get("mode"), ex.get("positions"), ex.get("hold"), "да" if st["kill"] else "нет"))
    out.append("шина: последний seq %s, возраст %s с; сканер: hot.json %s с; непрочитано: %s" % (st["bus"]["last_seq"], st["bus"]["age_s"], st["scanner"]["age_s"], st["unread_total"]))
    kb = st.get("knowledge") or {}
    if kb.get("store"):
        out.append("база знаний: %s; случаи и уроки: store=%s (источник %s)" % (
            {"green": "ок", "yellow": "жёлтый", "red": "КРАСНЫЙ"}.get(kb.get("color"), kb.get("color")), kb["store"], kb.get("store_src") or "-"))
    if st.get("learnloop"):
        out.append(learnloop_line(st["learnloop"]))
        cl = coverage_line(st["learnloop"])
        if cl:
            out.append(cl)
    if st.get("cmkt"):
        out.append(cmkt_line(st["cmkt"]))
    for n, u in st["links"].items():
        out.append("ссылка %s: %s" % (n, u))
    return "\n".join(out)


def _dur(s):
    if not isinstance(s, (int, float)):
        return "-"
    s = int(s)
    if s < 120:
        return "%ds" % s
    if s < 7200:
        return "%dm" % (s // 60)
    return "%dh%02dm" % (s // 3600, s % 3600 // 60)
