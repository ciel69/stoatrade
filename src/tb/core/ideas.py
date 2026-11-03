"""История идей агента и статистика сигналов (в т. ч. пропущенных). Только локальные файлы, на биржу не ходит, ничего не торгует.

Зачем: агент почти всегда пропускает события сканера горячих монет. Чтобы не писать про каждую монету в чат, интересное он записывает ОДНОЙ
короткой командой, а честная статистика «что было бы, если бы входили» копится сама (hotscan пишет форвард-доходность, см. marketdata/outcomes.py).

  ideas.py add SYM --side long|short|watch --note "тезис/что ждём" [--level X] [--stop X] [--tp X] [--tag t] [--status idea|watching|skipped] [--ttl ЧАСЫ] [--src agent]
  ideas.py list [--status S] [--n 20]
  ideas.py mark ID --status taken|watching|skipped|expired|invalidated|idea [--trade ID_СДЕЛКИ] [--note ".."]
  ideas.py stats [--json]

Файлы (только дописывание, utf-8, по строке JSON):
  state/ideas.jsonl         {"ev":"idea","id":"I7","ts","ts_epoch","sym","side","note","level","stop","tp","tag","status","price","price_src","src"}
                            и {"ev":"mark","id","ts","status","note"}; итоговая запись = idea с применёнными mark по порядку.
                            ревизия живых идей (marketdata/ideawatch.py, инструмент revise_idea): {"ev":"flag","id","hit"|"stale_code"|"kept_code","renew"} и
                            {"ev":"edit","id","level","stop","tp"}; load_ideas добавляет поля hit, hit_ts, stale_code, stale_ts, ts_ttl, renewals, rev. Пороги: config/idea_stale.json.
  state/hot_outcomes.jsonl  форвард-доходность событий сканера и идей агента, пишет hotscan (формат в marketdata/outcomes.py)
Будильник: пока работает `scan --watch`, hotscan сам будит агента по идеям со статусом idea/watching и заданным --level (idea_near / idea_level / idea_invalidated / idea_tp,
см. marketdata/ideawatch.py). Без --level идея только заметка и агента не разбудит. --ttl (часы, по умолчанию 24, 0 = без срока): по истечении hotscan молча ставит expired.
Цена на момент записи: runtime/market.json (если свежая) -> runtime/hot.json -> последнее событие runtime/hot_events.jsonl; нет нигде -> price=null.
Издержки Bybit из config/risk_profile.json:costs.bybit: круг taker = 2*(fee+slip), круг maker = 2*maker_fee (на момент входа и выхода).
"""
import argparse
import json
import os
import sys
import time

from tb import paths

# idea: записана, не решено; watching: наблюдаем/ждём уровень; taken: вошли в сделку (--trade ID из ./tb journal); skipped: осознанно пропущена (причина в note);
# expired/invalidated: устарела/сломан тезис. auto_skipped не пишется в ideas.jsonl: это авто-история сканера (state/hot_outcomes.jsonl).
# planned: по идее подан план (поле plan); hotscan её уже не сторожит, итог проставит resolve_planned: taken (план вошёл в сделку) или expired (закрыт без входа).
STATUSES = ("idea", "watching", "planned", "taken", "skipped", "expired", "invalidated")
SIDES = ("long", "short", "watch")
SMALL_N = 30
ACTIVE = ("idea", "watching")          # статусы, которые ещё ждут решения (их сторожит hotscan)
TTL_H_DEFAULT = 24.0
PRICE_FRESH_S = 120.0
HOT_FRESH_S = 600.0
EVENT_FRESH_S = 1800.0
SCAN_PRICE_FRESH_S = 150.0   # runtime/prices.json (сканер, раз в 30 с)
# корзины для статистики: (имя, нижняя граница включительно, верхняя исключительно)
RANGE_BUCKETS = (("<1.0%", None, 1.0), ("1.0-1.5%", 1.0, 1.5), ("1.5-2.5%", 1.5, 2.5), (">=2.5%", 2.5, None))
SURGE_BUCKETS = (("<2x", None, 2.0), ("2-4x", 2.0, 4.0), (">=4x", 4.0, None))


# ---------- пути, файлы ----------

def ideas_path(root=None):
    return paths.state("ideas.jsonl", root=root)


def outcomes_path(root=None):
    return paths.state("hot_outcomes.jsonl", root=root)


def pending_path(root=None):
    return paths.runtime("hot_pending.json", root=root)


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def num(x):
    try:
        if x is None or x == "" or isinstance(x, bool):
            return None
        v = float(x)
        return v if v == v and abs(v) != float("inf") else None
    except (TypeError, ValueError):
        return None


def append(path, rec):
    """Дописать одну строку JSON одним write (режим a: на POSIX и Windows короткие записи не перемешиваются)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_rows(path):
    """-> список dict; битые и недописанные строки пропускаются."""
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    out.append(r)
    except OSError:
        pass
    return out


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def costs(root=None):
    """-> {"taker_rt_pct", "maker_rt_pct"}: издержки круга (вход+выход) в процентах номинала."""
    prof = read_json(paths.config("risk_profile.json", root=root))
    c = ((prof or {}).get("costs") or {}).get("bybit") or {} if isinstance(prof, dict) else {}
    fee = num(c.get("fee")) if num(c.get("fee")) is not None else 0.001
    slip = num(c.get("slip")) if num(c.get("slip")) is not None else 0.0002
    mk = num(c.get("maker_fee")) if num(c.get("maker_fee")) is not None else 0.00036
    return {"taker_rt_pct": round(2 * (fee + slip) * 100, 4), "maker_rt_pct": round(2 * mk * 100, 4)}


# ---------- журнал идей ----------

def load_ideas(root=None, path=None):
    """-> список идей в порядке записи, mark применены (status, а заметка добавляется в mark_notes). path: явный файл (по умолчанию state/ideas.jsonl)."""
    ideas, order = {}, []
    for r in read_rows(path or ideas_path(root)):
        if r.get("ev") == "idea" and r.get("id"):
            if r["id"] not in ideas:
                order.append(r["id"])
            ideas[r["id"]] = dict(r, mark_notes=[])
        elif r.get("ev") == "mark" and r.get("id") in ideas:
            i = ideas[r["id"]]
            if r.get("trade"):
                i["trade"] = r["trade"]
            if r.get("plan"):
                i["plan"] = r["plan"]
            if r.get("status"):
                i["status"] = r["status"]
                i["ts_status"] = r.get("ts")
            if r.get("note"):
                i["mark_notes"].append(r["note"])
        elif r.get("ev") == "flag" and r.get("id") in ideas:
            _apply_flag(ideas[r["id"]], r)
        elif r.get("ev") == "edit" and r.get("id") in ideas:
            _apply_edit(ideas[r["id"]], r)
    return [ideas[k] for k in order]


def _apply_flag(i, r):
    """ev=flag: hit (уровень задет), stale_code (ревизия: причина или None = снят), kept_code/kept_until (после keep тот же код до срока молчит), renew (срок ttl считается заново)."""
    if "hit" in r:
        i["hit"] = bool(r["hit"])
        if r.get("hit_ts") is not None:
            i["hit_ts"] = r["hit_ts"]
    if "stale_code" in r:
        i["stale_code"] = r["stale_code"] or None
        i["stale_ts"] = r.get("ts_epoch") if r.get("stale_code") else None
        i["stale_note"] = r.get("note") if r.get("stale_code") else None
    if r.get("kept_code"):
        i["kept"] = dict(i.get("kept") or {}, **{r["kept_code"]: r.get("kept_until")})
    if r.get("renew"):
        i["ts_ttl"] = r.get("ts_epoch")
        i["renewals"] = int(i.get("renewals") or 0) + 1


def _apply_edit(i, r):
    """ev=edit: идея пересчитана по кандидату (level/stop/tp/сторона/цена записи), срок и счётчики сброшены, флаги сняты; id тот же (лимиты и дедуп не затрагиваются)."""
    for k in ("level", "stop", "tp", "side"):
        if r.get(k) is not None:
            i[k] = r[k]
    i["price"] = r.get("price")            # None: сторож возьмёт первую цену сканера (подход к новому уровню определяется заново)
    if r.get("note"):
        i["note"] = r["note"]
    i["rev"] = int(i.get("rev") or 0) + 1
    i["ts_ttl"] = r.get("ts_epoch")
    i["stale_code"], i["stale_ts"], i["stale_note"] = None, None, None
    i["hit"], i["kept"], i["renewals"] = False, {}, 0
    i.pop("hit_ts", None)


def next_id(root=None):
    n = 0
    for r in read_rows(ideas_path(root)):
        if r.get("ev") == "idea":
            try:
                n = max(n, int(str(r.get("id", ""))[1:]))
            except ValueError:
                pass
    return "I%d" % (n + 1)


def price_book(root=None, now=None):
    """-> {SYM: (цена, ts_epoch или None, источник)}: market.json (свежая) поверх runtime/prices.json (все монеты скана) поверх hot.json (top+blocked) поверх последних событий сканера."""
    now = time.time() if now is None else now
    book = {}
    for r in reversed(read_rows(paths.runtime("hot_events.jsonl", root=root))[-300:]):
        t = _ts_epoch(r.get("ts"))
        p = num(r.get("price"))
        if r.get("sym") and p and t and now - t <= EVENT_FRESH_S and r["sym"] not in book:
            book[r["sym"]] = (p, t, "hot_events")
    h = read_json(paths.runtime("hot.json", root=root))
    if isinstance(h, dict) and num(h.get("ts_epoch")) and now - num(h["ts_epoch"]) <= HOT_FRESH_S:
        for r in (h.get("top") or []) + (h.get("blocked") or []):
            if isinstance(r, dict) and r.get("sym") and num(r.get("price")):
                book[r["sym"]] = (num(r["price"]), num(h["ts_epoch"]), "hot.json")
    sp = read_json(paths.runtime("prices.json", root=root))
    if isinstance(sp, dict) and isinstance(sp.get("prices"), dict) and num(sp.get("ts_epoch")) and now - num(sp["ts_epoch"]) <= SCAN_PRICE_FRESH_S:
        for s, v in sp["prices"].items():
            if num(v):
                book[s] = (num(v), num(sp["ts_epoch"]), "prices.json")
    m = read_json(paths.runtime("market.json", root=root))
    if isinstance(m, dict) and isinstance(m.get("symbols"), dict) and num(m.get("ts_epoch")) and now - num(m["ts_epoch"]) <= PRICE_FRESH_S:
        for s, v in m["symbols"].items():
            if isinstance(v, dict) and num(v.get("price")):
                book[s] = (num(v["price"]), num(m["ts_epoch"]), "market.json")
    return book


def _ts_epoch(s):
    try:
        import calendar
        return float(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")))
    except (TypeError, ValueError):
        return None


def add_idea(sym, side, note, level=None, stop=None, tp=None, tag=None, status="idea", src="agent", root=None, now=None, ttl_h=TTL_H_DEFAULT, mode=None):
    """Дописывает идею, возвращает запись. Цена берётся из локальных файлов (без сети). mode (paper|testnet|live) нужен памяти: случай идеи берёт режим отсюда."""
    now = time.time() if now is None else now
    sym = sym.upper().replace("USDT", "") if sym.upper().endswith("USDT") and len(sym) > 4 else sym.upper()
    price, src_p = None, None
    hit = price_book(root, now).get(sym)
    if hit:
        price, src_p = hit[0], hit[2]
    rec = {"ev": "idea", "id": next_id(root), "ts": iso(now), "ts_epoch": round(now, 1), "sym": sym, "side": side, "note": note,
           "level": level, "stop": stop, "tp": tp, "tag": tag, "status": status, "price": price, "price_src": src_p, "src": src, "ttl_h": ttl_h}
    if mode:
        rec["mode"] = mode
    append(ideas_path(root), rec)
    return rec


def mark_idea(iid, status, note=None, trade=None, root=None, now=None, plan=None):
    now = time.time() if now is None else now
    if not any(i["id"] == iid for i in load_ideas(root)):
        return None
    rec = {"ev": "mark", "id": iid, "ts": iso(now), "status": status, "note": note, "trade": trade}
    if plan:
        rec["plan"] = plan
    append(ideas_path(root), rec)
    return rec


def link_plan(sym, side, level, plan_id, root=None, now=None, tol_pct=2.0):
    """Идея (idea/watching) по той же монете и стороне с уровнем ближе всех к уровню плана (не дальше tol_pct) -> planned с ссылкой на план. -> id идеи или None."""
    best, bd = None, None
    for i in load_ideas(root):
        lv = i.get("level")
        if i.get("sym") != sym or i.get("side") != side or (i.get("status") or "idea") not in ACTIVE or not lv or not level:
            continue
        d = abs(lv - level) / level * 100.0
        if d <= tol_pct and (bd is None or d < bd):
            best, bd = i, d
    if best is None:
        return None
    mark_idea(best["id"], "planned", note="план %s" % plan_id, root=root, now=now, plan=plan_id)
    return best["id"]


def resolve_planned(root=None, now=None):
    """Идеи planned: план вошёл в сделку (runtime.pos.journal_id) -> taken с id сделки; план завершён без входа -> expired. -> число закрытых идей."""
    now = time.time() if now is None else now
    n = 0
    for i in load_ideas(root):
        if i.get("status") != "planned" or not i.get("plan"):
            continue
        try:
            with open(paths.plans(i["plan"], "runtime.json", root=root), encoding="utf-8") as f:
                rt = json.load(f)
        except (OSError, ValueError):
            continue
        jid = (rt.get("pos") or {}).get("journal_id")
        if jid:
            mark_idea(i["id"], "taken", note="план вошёл в сделку", trade=jid, root=root, now=now)
            n += 1
        elif rt.get("status") in ("closed", "cancelled", "expired", "invalidated", "rejected"):
            mark_idea(i["id"], "expired", note="план %s закрыт без входа" % rt.get("status"), root=root, now=now)
            n += 1
    return n


def mark_if_active(iid, status, note=None, root=None, path=None, now=None):
    """Пометить идею, только если она всё ещё idea/watching (агент мог за это время взять её или сам пометить). Читает свежий файл и дописывает одну строку
    одним write (режим a: целая строка или ничего). -> запись mark или None (нет идеи / уже не активна)."""
    now = time.time() if now is None else now
    cur = {i["id"]: i for i in load_ideas(root, path)}.get(iid)
    if not cur or (cur.get("status") or "idea") not in ACTIVE:
        return None
    rec = {"ev": "mark", "id": iid, "ts": iso(now), "status": status, "note": note, "trade": None}
    append(path or ideas_path(root), rec)
    return rec


# ---------- ревизия живых идей: флаги, продление, пересчёт ----------
MAX_ACTIVE = 10        # активных идей всего (лимит записи идей мозгом; тот же у revise_idea)
MAX_PER_SYM = 2        # активных идей на монету
DUP_PCT = 0.3          # идея той же монеты и стороны с уровнем ближе этого процента считается дублем
STALE_DEFAULTS = {
    "min_age_s": 300.0,                 # моложе идею не ревизуем
    "struct_max_age_s": 600.0,          # структура старше этого не используется (проверки по ней молчат)
    "max_per_hour": 4,                  # не больше стольких новых флагов ревизии в час на весь сторож (экономия токенов)
    "keep": {"cooldown_h": 4.0, "max_renewals": 3},
    "level_gone": {"enabled": True, "tol_pct": 0.35, "min_age_s": 600.0},
    "rr_changed": {"enabled": True, "min_rr_net": 1.5, "stop_floor_a5": 1.5},
    "price_far": {"enabled": True, "atr_1h": 6.0, "min_pct": 3.0, "grow": 1.5},
    "regime_flip": {"enabled": True, "hold_s": 600.0},
    "btc_drift": {"enabled": True, "r1h_pct": 1.5},
    "hit_idle": {"enabled": True, "after_s": 1800.0},
}


def stale_cfg(root=None):
    """Пороги ревизии идей: умолчания STALE_DEFAULTS поверх config/idea_stale.json (раздел по разделу). Нет файла или он битый -> умолчания."""
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in STALE_DEFAULTS.items()}
    d = read_json(paths.config("idea_stale.json", root=root))
    if isinstance(d, dict):
        for k, v in d.items():
            if k in cfg and isinstance(v, dict) and isinstance(cfg[k], dict):
                cfg[k].update({kk: vv for kk, vv in v.items() if kk in cfg[k]})
            elif k in cfg and not isinstance(cfg[k], dict) and num(v) is not None:
                cfg[k] = num(v)
    return cfg


def expires_epoch(i, default_ttl_h=TTL_H_DEFAULT):
    """Когда идея истечёт (epoch): отсчёт ttl_h от ts_ttl (продление keep / пересчёт update), иначе от записи; ttl 0 или нет времени = None."""
    ttl = num(i.get("ttl_h"))
    ttl = default_ttl_h if ttl is None else ttl
    t0 = num(i.get("ts_ttl"))
    t0 = num(i.get("ts_epoch")) if t0 is None else t0
    return t0 + ttl * 3600.0 if ttl > 0 and t0 is not None else None


def _active_idea(iid, root=None, path=None):
    cur = {i["id"]: i for i in load_ideas(root, path)}.get(iid)
    return cur if cur and (cur.get("status") or "idea") in ACTIVE else None


def flag_idea(iid, code, note=None, root=None, path=None, now=None):
    """Поставить на активную идею флаг ревизии stale_code. Тот же код уже стоит (или идея не активна) -> None, строка не пишется (дедуп идея+код)."""
    now = time.time() if now is None else now
    cur = _active_idea(iid, root, path)
    if not cur or cur.get("stale_code") == code:
        return None
    rec = {"ev": "flag", "id": iid, "ts": iso(now), "ts_epoch": round(now, 1), "stale_code": code, "note": (note or "")[:80] or None}
    append(path or ideas_path(root), rec)
    return rec


def mark_hit(iid, hit_ts, root=None, path=None, now=None):
    """Запомнить в хранилище, что цена коснулась уровня идеи (досье мозга читает только хранилище идей). Уже помечена или не активна -> None."""
    now = time.time() if now is None else now
    cur = _active_idea(iid, root, path)
    if not cur or cur.get("hit"):
        return None
    rec = {"ev": "flag", "id": iid, "ts": iso(now), "ts_epoch": round(now, 1), "hit": True, "hit_ts": round(hit_ts, 1)}
    append(path or ideas_path(root), rec)
    return rec


def keep_idea(iid, root=None, path=None, now=None):
    """keep: снять флаг ревизии и продлить срок (ttl считается от сейчас); тот же код молчит keep.cooldown_h часов. -> (запись | None, код отказа | None):
    NOT_ACTIVE; KEEP_LIMIT (идея уже продлевалась keep.max_renewals раз: пересчитать или отменить)."""
    now = time.time() if now is None else now
    cur = _active_idea(iid, root, path)
    if not cur:
        return None, "NOT_ACTIVE"
    k = stale_cfg(root)["keep"]
    if int(cur.get("renewals") or 0) >= int(k["max_renewals"]):
        return None, "KEEP_LIMIT"
    rec = {"ev": "flag", "id": iid, "ts": iso(now), "ts_epoch": round(now, 1), "stale_code": None, "renew": True}
    if cur.get("stale_code"):
        rec["kept_code"], rec["kept_until"] = cur["stale_code"], round(now + float(k["cooldown_h"]) * 3600.0, 1)
    append(path or ideas_path(root), rec)
    return rec, None


def edit_idea(iid, level, stop, tp, side=None, note=None, root=None, path=None, now=None):
    """update: тот же id, новые level/stop/tp (и сторона у watch) из кандидата; цена записи берётся заново, ttl и счётчики с нуля, флаги сняты. -> запись | None (не активна)."""
    now = time.time() if now is None else now
    if not _active_idea(iid, root, path):
        return None
    cur = _active_idea(iid, root, path)
    hit = price_book(root, now).get(cur.get("sym")) if path is None else None
    rec = {"ev": "edit", "id": iid, "ts": iso(now), "ts_epoch": round(now, 1), "level": level, "stop": stop, "tp": tp, "side": side,
           "price": hit[0] if hit else None, "note": (note or "")[:300] or None}
    append(path or ideas_path(root), rec)
    return rec


# ---------- статистика ----------

def idea_outcomes(root=None):
    """-> {id идеи: {horizon_min: {"dir_pct","cost_pct","net_pct","ret_pct"}}}: что было бы по стороне идеи через 15/60 мин (до и после издержек)."""
    out = {}
    for r in read_rows(outcomes_path(root)):
        if r.get("kind") == "idea" and r.get("id") and isinstance(r.get("h"), int):
            d, c = num(r.get("dir_pct")), num(r.get("cost_pct")) or 0.0
            out.setdefault(r["id"], {})[r["h"]] = {"ret_pct": num(r.get("ret_pct")), "dir_pct": d, "cost_pct": c, "net_pct": None if d is None else d - c}
    return out


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _se(xs):
    if len(xs) < 2:
        return None
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1) / len(xs)) ** 0.5


def _bucket(v, buckets):
    if v is None:
        return "нет данных"
    for name, lo, hi in buckets:
        if (lo is None or v >= lo) and (hi is None or v < hi):
            return name
    return "нет данных"


def _cell(rows):
    """rows: исходы одной группы и горизонта -> n, средние до/после издержек и доли плюсовых (продолжение и откат)."""
    d = [(r["dir_pct"], r.get("cost_pct") or 0.0) for r in rows if num(r.get("dir_pct")) is not None]
    if not d:
        return {"n": 0}
    cont_g = [x for x, _ in d]
    cont_n = [x - c for x, c in d]
    fade_n = [-x - c for x, c in d]
    n = len(d)
    return {"n": n, "cont_gross": _mean(cont_g), "cont_net": _mean(cont_n), "cont_se": _se(cont_n), "cont_win_gross": sum(x > 0 for x in cont_g) / n,
            "cont_win": sum(x > 0 for x in cont_n) / n, "fade_gross": -_mean(cont_g), "fade_net": _mean(fade_n), "fade_win": sum(x > 0 for x in fade_n) / n}


def _groups(rows, horizons, keyf):
    by = {}
    for r in rows:
        k = keyf(r)
        if k is not None:
            by.setdefault(k, []).append(r)
    out = []
    for k, rs in by.items():
        out.append({"dim": k[0], "name": k[1], "events": len({r.get("id") for r in rs}),
                    "h": {str(h): _cell([r for r in rs if r.get("h") == h]) for h in horizons}})
    return out


def compute_stats(root=None):
    """Полная статистика для CLI и дашборда."""
    rows = read_rows(outcomes_path(root))
    ev_rows = [r for r in rows if r.get("kind") == "event"]
    id_rows = [r for r in rows if r.get("kind") == "idea"]
    horizons = sorted({r["h"] for r in rows if isinstance(r.get("h"), int)} | {15, 60})
    c = costs(root)
    groups = _groups(ev_rows, horizons, lambda r: ("все", "все события"))
    groups += _groups(ev_rows, horizons, lambda r: ("тип", r.get("type") or "?"))
    groups += _groups(ev_rows, horizons, lambda r: ("диапазон 5м", _bucket(num(r.get("range_5m_pct")), RANGE_BUCKETS)))
    groups += _groups(ev_rows, horizons, lambda r: ("оборот 5м", _bucket(num(r.get("turn_surge_5m")), SURGE_BUCKETS)))
    order = {"все": 0, "тип": 1, "диапазон 5м": 2, "оборот 5м": 3}
    groups.sort(key=lambda g: (order.get(g["dim"], 9), g["name"]))
    idea_groups = _groups(id_rows, horizons, lambda r: ("все", "все идеи")) + _groups(id_rows, horizons, lambda r: ("сторона", r.get("side") or "?") if r.get("dir") else None)
    cells = sum(1 for g in groups + idea_groups for h in g["h"].values() if h["n"])
    ideas = load_ideas(root)
    by_status = {}
    for i in ideas:
        by_status[i.get("status") or "idea"] = by_status.get(i.get("status") or "idea", 0) + 1
    pend = read_json(pending_path(root))
    return {"costs": c, "horizons": horizons, "groups": groups, "idea_groups": idea_groups,
            "n_events": len({r.get("id") for r in ev_rows}), "n_outcomes": len(rows),
            "agent": {"total": len(ideas), "by_status": by_status, "tracked": len({r.get("id") for r in id_rows}),
                      "taken_share": (by_status.get("taken", 0) / len(ideas)) if ideas else None},
            "pending": len((pend or {}).get("items") or {}) if isinstance(pend, dict) else 0,
            "comparisons": cells * 2,
            "warn": ("Выборки малые и сильно зависимые (события одного часа коррелируют): n<%d не вывод. Сравнений показано %d (продолжение и откат считаются отдельно): "
                     "при 5%% уровне ~%.0f случайных «плюсов» ожидается без всякого edge. Вход по цене события = оптимистично (на деле позже и хуже)." % (SMALL_N, cells * 2, cells * 2 * 0.05))}


def _pc(x, d=2):
    return "  –  " if x is None else "%+.*f" % (d, x)


def format_stats(st):
    L = ["Издержки круга Bybit: taker %.3f%% (с проскальзыванием), maker-maker %.3f%%. net = до издержек минус taker. «прод» = по направлению события, «откат» = против." % (st["costs"]["taker_rt_pct"], st["costs"]["maker_rt_pct"]),
         "Пропущенных событий с исходами: %d, строк исходов %d, в очереди %d." % (st["n_events"], st["n_outcomes"], st["pending"])]

    def block(title, groups, fade):
        L.append("")
        L.append(title)
        if not groups:
            L.append("  данных ещё нет (нужен работающий hotscan --watch)")
            return
        for g in groups:
            parts = []
            for h in st["horizons"]:
                c = g["h"][str(h)]
                if not c["n"]:
                    parts.append("%dм: n=0" % h)
                    continue
                s = "%dм: n=%d%s прод %s/%s%% win %.0f%%" % (h, c["n"], "*" if c["n"] < SMALL_N else "", _pc(c["cont_gross"]), _pc(c["cont_net"]), 100 * c["cont_win"])
                if fade:
                    s += " | откат net %s%% win %.0f%%" % (_pc(c["fade_net"]), 100 * c["fade_win"])
                parts.append(s)
            L.append("  %-12s %-18s %s" % (g["dim"], g["name"], "  ||  ".join(parts)))
    block("Пропущенные события сканера (авто, прод = gross/net %):", st["groups"], True)
    block("Идеи агента (по стороне идеи, gross/net %):", st["idea_groups"], False)
    a = st["agent"]
    L.append("")
    L.append("Идеи агента: всего %d, по статусам %s, трекается исходов %d%s." % (
        a["total"], ", ".join("%s %d" % kv for kv in sorted(a["by_status"].items())) or "нет", a["tracked"],
        ", взято %.0f%%" % (100 * a["taken_share"]) if a["taken_share"] is not None else ""))
    L.append("* n<%d. %s" % (SMALL_N, st["warn"]))
    return "\n".join(L)


# ---------- CLI ----------

def _fmt_p(p):
    if p is None:
        return "-"
    return ("%.6g" % p)


def line_of(i, book, now):
    cur = book.get(i["sym"])
    shift = ""
    if cur and num(i.get("price")):
        shift = " -> %s (%+.2f%%)" % (_fmt_p(cur[0]), 100 * (cur[0] / i["price"] - 1))
    lv = " ".join("%s %s" % (k, _fmt_p(i[k2])) for k, k2 in (("lvl", "level"), ("sl", "stop"), ("tp", "tp")) if i.get(k2) is not None)
    if i.get("trade"):
        lv += " trade " + str(i["trade"])
    return "%s %s %s %s [%s] @%s%s%s | %s" % (i["id"], (i.get("ts") or "")[5:16].replace("T", " "), i["sym"], i["side"], i.get("status") or "idea",
                                              _fmt_p(i.get("price")), shift, (" | " + lv.strip()) if lv.strip() else "", i.get("note") or "")


def main(argv):
    ap = argparse.ArgumentParser(prog="ideas")
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("add")
    a.add_argument("sym")
    a.add_argument("--side", required=True, choices=SIDES)
    a.add_argument("--note", required=True)
    a.add_argument("--level", type=float)
    a.add_argument("--stop", type=float)
    a.add_argument("--tp", type=float)
    a.add_argument("--tag")
    a.add_argument("--status", choices=("idea", "watching", "skipped"), default="idea")
    a.add_argument("--ttl", type=float, default=TTL_H_DEFAULT, help="срок жизни идеи, часы (0 = без срока); потом hotscan молча ставит expired")
    a.add_argument("--src", default="agent")
    ls = sp.add_parser("list")
    ls.add_argument("--status", choices=STATUSES)
    ls.add_argument("--n", type=int, default=20)
    m = sp.add_parser("mark")
    m.add_argument("id")
    m.add_argument("--status", required=True, choices=STATUSES)
    m.add_argument("--trade", help="id сделки из ./tb journal (для taken)")
    m.add_argument("--note")
    s = sp.add_parser("stats")
    s.add_argument("--json", action="store_true")
    x = ap.parse_args(argv)
    if x.cmd == "add":
        r = add_idea(x.sym, x.side, x.note, x.level, x.stop, x.tp, x.tag, x.status, x.src, ttl_h=x.ttl)
        print("%s %s %s %s @%s%s" % (r["id"], r["sym"], r["side"], r["status"], _fmt_p(r["price"]), "" if r["price"] else " (цены нет, исход не отследится)"))
        if r["level"] is None:
            print("внимание: без --level hotscan не разбудит агента по этой идее (это просто заметка)", file=sys.stderr)
        elif r["status"] not in ACTIVE:
            print("внимание: статус %s не сторожится (будильник только для idea/watching)" % r["status"], file=sys.stderr)
    elif x.cmd == "list":
        now = time.time()
        rows = [i for i in load_ideas() if not x.status or i.get("status") == x.status][-x.n:]
        book = price_book(now=now)
        for i in reversed(rows):
            print(line_of(i, book, now))
        if not rows:
            print("идей нет")
    elif x.cmd == "mark":
        if not mark_idea(x.id, x.status, x.note, x.trade):
            print("нет идеи %s" % x.id, file=sys.stderr)
            return 1
        print("%s -> %s" % (x.id, x.status))
    elif x.cmd == "stats":
        st = compute_stats()
        print(json.dumps(st, ensure_ascii=False) if x.json else format_stats(st))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
