"""Ревизия живой идеи мозгом: keep | update | cancel. Общая логика для инструмента шлюза revise_idea и для es_revise раннера (entry_scout в режиме инструментов).

Модель выбирает действие и (для update) номер кандидата brief.cands; цены (level/stop/tp) код берёт из кандидата, модель их не задаёт.
  keep    снять флаг stale_code и продлить срок (ttl от сейчас); тот же код молчит keep.cooldown_h часов; не больше keep.max_renewals раз подряд (config/idea_stale.json).
  update  level/stop/tp из кандидата (только готовый ok-кандидат, не импульсный; сторона та же, у watch берётся сторона кандидата); id и место в лимитах те же
          (правка на месте, лимиты 10 активных и 2 на монету не затрагиваются); дубль с ДРУГОЙ активной идеей (та же монета и сторона, уровень в 0.3%) отклоняется, с самой собой нет.
  cancel  идея skipped (осознанно снята, причина в note); активна только idea/watching (ideas.mark_if_active).
Результат: {"ok": True, "idea_id", "action", ...} или {"ok": False, "code", "msg"}.
"""
from tb.core import ideas

ACTIONS = ("keep", "update", "cancel")


def _err(code, msg):
    return {"ok": False, "code": code, "msg": msg}


def revise(iid, action, cands=None, candidate=None, sym=None, root=None, now=None, path=None):
    """sym: монета, к которой относятся cands (монета задания); для update идея должна быть этой монеты."""
    cur = {i["id"]: i for i in ideas.load_ideas(root, path)}.get(iid)
    if not cur:
        return _err("NOT_FOUND", "нет идеи %s" % iid)
    if (cur.get("status") or "idea") not in ideas.ACTIVE:
        return _err("NOT_ACTIVE", "идея %s уже %s" % (iid, cur.get("status")))
    if action == "keep":
        rec, code = ideas.keep_idea(iid, root=root, path=path, now=now)
        if rec is None:
            return _err(code, "идея %s продлевалась максимум раз: пересчитай (update) или сними (cancel)" % iid if code == "KEEP_LIMIT" else "идея %s не активна" % iid)
        return {"ok": True, "idea_id": iid, "action": "keep", "cleared": cur.get("stale_code"), "renewals": int(cur.get("renewals") or 0) + 1}
    if action == "cancel":
        why = "ревизия: cancel%s" % (" (%s)" % cur["stale_code"] if cur.get("stale_code") else "")
        if ideas.mark_if_active(iid, "skipped", why, root=root, path=path, now=now) is None:
            return _err("NOT_ACTIVE", "идея %s уже не активна" % iid)
        return {"ok": True, "idea_id": iid, "action": "cancel", "status": "skipped"}
    if action != "update":
        return _err("BAD_ACTION", "действие: keep | update | cancel")
    rows = cands if isinstance(cands, list) else []
    try:
        n = int(candidate)
    except (TypeError, ValueError):
        return _err("RANGE", "для update нужен номер кандидата candidate из brief.cands")
    if not 1 <= n <= len(rows) or not isinstance(rows[n - 1], dict):
        return _err("NO_CANDIDATE", "кандидата %d нет (в досье %d)" % (n, len(rows)))
    if sym and cur.get("sym") != sym:
        return _err("SYM_MISMATCH", "кандидаты относятся к %s, идея %s по %s" % (sym, iid, cur.get("sym")))
    c = rows[n - 1]
    if c.get("fail") or not c.get("ok") or c.get("setup") == "momentum_chase":
        return _err("CANDIDATE_FAILED", "кандидат %d не годится для идеи (%s): выбери ok-кандидат уровня, иначе keep или cancel" % (n, c.get("fail") or "не ok/импульс"))
    side = cur.get("side")
    if side in ("long", "short") and c.get("side") != side:
        return _err("SIDE_MISMATCH", "сторона кандидата %s, идеи %s: смена стороны = cancel и новый вход" % (c.get("side"), side))
    lv, stop, tps = ideas.num(c.get("level")), ideas.num(c.get("stop")), c.get("tp")
    tp = ideas.num(tps[0]) if isinstance(tps, list) and tps else ideas.num(tps)
    if not lv or not stop or not tp:
        return _err("CANDIDATE_FAILED", "в кандидате %d нет level/stop/tp" % n)
    for o in ideas.load_ideas(root, path):
        ol = ideas.num(o.get("level"))
        if o["id"] != iid and (o.get("status") or "idea") in ideas.ACTIVE and o.get("sym") == cur.get("sym") and o.get("side") == c.get("side") and ol \
                and abs(ol - lv) / ol * 100.0 <= ideas.DUP_PCT:
            return _err("DUPLICATE", "у монеты уже есть идея %s с уровнем в %.1f%%: сними эту (cancel) или оставь (keep)" % (o["id"], ideas.DUP_PCT))
    if ideas.edit_idea(iid, lv, stop, tp, side=c.get("side"), root=root, path=path, now=now) is None:
        return _err("NOT_ACTIVE", "идея %s уже не активна" % iid)
    return {"ok": True, "idea_id": iid, "action": "update", "level": lv, "stop": stop, "tp": tp}
