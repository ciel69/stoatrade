"""Снятие аварийной остановки (KILL) из нового дашборда: предусловия. Решение владельца 2026-11-02. Владелец: плоскость управления.

Чистые проверки по файлам (без сети и биржи). Само снятие делает `exec_cfg.clear_kill` (то же, что `./tb exec resume`); здесь только ответ на вопрос «можно ли снять сейчас».
Правило: любая неопределённость (нет снимка, нет связи, устаревшие данные, переходный режим) = нельзя. Режим определяется по профилю живого исполнителя:
trade -> live (все жёсткие ворота старта live, кроме H1 «KILL чист» (это сам KILL) и H8 «лимит запусков» (снятие KILL день не запускает)), иначе только стоп на бирже у каждой позиции.
"""
from tb.brain import common
from tb.control import gates
from tb.core import exec_cfg

LIVE_GATES = ("h2", "h3", "h4", "h5", "h6", "h7")      # жёсткие ворота старта live без H1 и H8
OK_STATES = ("halted", "stopped")                     # состояние платформы, при котором KILL снимается


def _g(gid, name, ok, msg, **numbers):
    return {"id": gid, "name": name, "ok": bool(ok), "msg": msg, "numbers": numbers}


def clean_reason(r):
    return " ".join(str(r or "").split())[:120]


def mode_of(health):
    """Режим по профилю исполнителя: live | testnet | paper | observe | None (исполнитель не отвечает)."""
    if not (health.get("present") and health.get("alive")):
        return None
    d = health.get("data") or {}
    prof, obs = d.get("profile"), bool(d.get("observe"))
    if prof == "trade":
        return "live"
    if prof == "testnet":
        return "testnet"
    if prof == "paper":
        return "observe" if obs else "paper"
    return None


def evaluate(root, now, cfg, plat_state):
    """-> {"active","kill","mode","gates","ok"}. kill: {"flatten","reason"} или None (KILL нет)."""
    k = exec_cfg.kill_state(root)
    kill = None if k is None else {"flatten": bool(k.get("flatten")), "reason": clean_reason(k.get("reason"))}
    health = common.exec_health(root=root, now=now)
    mode = mode_of(health)
    out = {"active": k is not None, "kill": kill, "mode": mode or "unknown", "gates": [], "ok": False}
    if k is None:
        return out
    gs = [_g("K1", "Исполнитель на связи", mode is not None, "исполнитель жив, режим %s" % mode if mode else "исполнитель не отвечает или в неизвестном профиле")]
    st = (plat_state or {}).get("state")
    gs.append(_g("K2", "День не идёт и не закрывается", st in OK_STATES,
                 "платформа остановлена (%s)" % st if st in OK_STATES else "платформа в состоянии «%s»: дождитесь остановки или закрытия дня" % st, state=st))
    if mode is not None:
        f = gates.collect(root, now, cfg, {"mode": mode})
        snap = f.get("snapshot")
        npos = len((snap or {}).get("positions") or []) if isinstance(snap, dict) else None
        if k.get("flatten"):
            gs.append(_g("K3", "Позиции закрыты (KILL с flatten)", npos == 0, "открытых позиций нет" if npos == 0 else "позиции ещё закрываются или снимок недоступен (%s)" % npos, positions=npos))
        for name in (LIVE_GATES if mode == "live" else ("h2",)):
            g = getattr(gates, name)(f)
            if g.get("deferred"):                      # проверка «после переключения»: для снятия KILL так нельзя, исполнитель не в целевом профиле
                g = dict(g, ok=False, msg="исполнитель в переходном режиме: проверка невозможна")
            gs.append(g)
    out["gates"], out["ok"] = gs, all(g["ok"] for g in gs)
    return out
