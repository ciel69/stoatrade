"""Будильник по идеям агента: hotscan на каждом скане сверяет цены с уровнями активных идей. Только чтение тикеров, которые сканер уже получил (лишних запросов нет).

Берутся идеи state/ideas.jsonl со статусом idea/watching и заданным level (без level идея только заметка и игнорируется; дешёвое чтение, перечитывается только при смене mtime/size файла).
События (в stdout, будят агента вне зависимости от --wake-score и без общего лимита max-per-hour; свой антиспам на идею), в runtime/hot_events.jsonl с тем же type:
  idea_near         цена вошла в зону уровня: |цена/level-1| <= zone_pct (0.3%, --idea-zone-pct). Раз на идею до выхода из зоны (выход = дальше 1.5*zone_pct) и не чаще cooldown (1800 с).
  idea_level        цена пересекла level с той стороны, откуда ждали. Сторона подхода определяется по цене записи идеи: цена записи выше level -> ждём откат ВНИЗ (цена <= level),
                    ниже level -> ждём подъём (цена >= level). Нет цены записи (или она равна level): по side (long вниз, short вверх, watch вниз); если цены нет и это watch,
                    берётся первая цена сканера. Один раз на идею (запоминается в runtime/idea_watch.json, переживает рестарт сканера).
  idea_invalidated  цена пересекла stop (сторона stop относительно level): событие и идея сразу помечается invalidated. И ПОСЛЕ касания уровня тоже (идея с hit больше не зомби:
                    пробитый стоп закрывает её; в note разница «без касания»/«после касания»).
  idea_tp           цена достигла tp (сторона tp относительно level), а идея всё ещё не взята: «ушла без нас», идея помечается expired с note.
  idea_stale        ревизия без LLM (config/idea_stale.json, ideas.stale_cfg): по данным runtime/structure.json (рядом с файлом состояния) и цене сканера на идею ставится флаг
                    stale_code в хранилище идей (ev=flag, дедуп идея+код) и уходит событие с полем stale (будит entry_scout; решает он инструментом revise_idea keep|update|cancel).
                    Коды (по порядку приоритета; одновременно один флаг на идею; после keep тот же код молчит keep.cooldown_h часов):
                      regime_flip  режим монеты стал против идеи (storm; trend_down для long, trend_up для short), отличается от режима при первом наблюдении и держится hold_s;
                      btc_drift    дрейф BTC за час (r1h) против стороны идеи на r1h_pct и больше (для монет, кроме BTC);
                      level_gone   уровень идеи не найден среди уровней структуры монеты (res/sup/day/pd/wk, допуск tol_pct), хотя должен быть в списке ближайших; не после касания;
                      rr_changed   RR после издержек падает ниже min_rr_net, потому что стоп уже пола stop_floor_a5*ATR(5м) (ATR вырос), а при записи было не ниже;
                      price_far    цена ушла от уровня на atr_1h ATR(1ч) и min_pct %, и расстояние выросло в grow раз от первого наблюдения; не после касания;
                      hit_idle     уровень задет, а за after_s секунд идея так и не взята.
                    Не чаще max_per_hour новых флагов в час. Касание уровня пишется в хранилище (hit, hit_ts) для досье мозга.
Срок жизни: ttl_h идеи (поле ttl_h записи, `ideas add --ttl`, по умолчанию 24 ч, 0 = без срока) от её ts (после keep/update от продления, ts_ttl); по истечении идея молча (без события) получает expired. Проверяется и у идей без level.
Пометка статуса: ideas.mark_if_active (свежее чтение файла, одна строка одним write; взятую/уже помеченную агентом идею не трогает).
Формат строки stdout: {"type","wake":true,"id":"I7","sym":"DOOD","side","level","price","dist_pct":(цена/level-1)*100 со знаком,"stop","tp","zone_pct","idea_note","ts","env"}.
События НЕ идут в трекер исходов (outcomes.py): статистика пропущенных сканера (kind=event) их не видит; идея сама трекается как kind=idea с момента записи.
"""
import json
import os

from tb.core import ideas

ZONE_PCT = 0.3
COOLDOWN_S = 1800.0
EXIT_K = 1.5                    # гистерезис зоны: выход из неё только при |dist| > EXIT_K*zone
NOTE_MAX = 80


class IdeaWatcher:
    def __init__(self, ideas_path, state_path, zone_pct=ZONE_PCT, cooldown=COOLDOWN_S, default_ttl_h=ideas.TTL_H_DEFAULT, cfg=None, struct_path=None, cost_rt_pct=None):
        self.path, self.state_path = ideas_path, state_path
        self.zone, self.cooldown, self.default_ttl_h = float(zone_pct), float(cooldown), float(default_ttl_h)
        self._stamp, self._ideas = None, []
        self.st = self._load_state()
        self.cfg = cfg if isinstance(cfg, dict) else ideas.stale_cfg()
        self.struct_path = struct_path or os.path.join(os.path.dirname(state_path), "structure.json")
        self._cost = cost_rt_pct
        self._sstamp, self._sdoc = None, None
        self._flag_log = []

    # --- состояние
    def _load_state(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                d = json.load(f)
            return {k: v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".tmp.%d" % os.getpid()
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.st, f)
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    # --- чтение идей (кэш по mtime/size)
    def _active(self):
        try:
            s = os.stat(self.path)
            stamp = (s.st_mtime, s.st_size)
        except OSError:
            self._stamp, self._ideas = None, []
            return self._ideas
        if stamp != self._stamp:
            self._stamp = stamp
            self._ideas = [i for i in ideas.load_ideas(path=self.path) if (i.get("status") or "idea") in ideas.ACTIVE]
        return self._ideas

    # --- проверка
    def _event(self, kind, i, price, dist, level):
        return {"type": kind, "wake": True, "id": i["id"], "sym": i["sym"], "side": i.get("side"), "level": level, "price": price,
                "dist_pct": round(dist, 3), "stop": ideas.num(i.get("stop")), "tp": ideas.num(i.get("tp")), "zone_pct": self.zone,
                "idea_note": (i.get("note") or "")[:NOTE_MAX]}

    # --- ревизия живых идей
    def _struct(self, now):
        """runtime/structure.json (кэш по mtime/size) -> {SYM: строка структуры} или None, если файла нет, схема не та или он старше struct_max_age_s."""
        try:
            s = os.stat(self.struct_path)
            stamp = (s.st_mtime, s.st_size)
        except OSError:
            self._sstamp, self._sdoc = None, None
            return None
        if stamp != self._sstamp:
            self._sstamp = stamp
            d = ideas.read_json(self.struct_path)
            ok = isinstance(d, dict) and d.get("schema") == "tb.structure/1" and isinstance(d.get("symbols"), dict) and ideas.num(d.get("ts_epoch")) is not None
            self._sdoc = d if ok else None
        d = self._sdoc
        if d is None or now - ideas.num(d["ts_epoch"]) > float(self.cfg["struct_max_age_s"]):
            return None
        return d["symbols"]

    def _cost_rt(self):
        if self._cost is None:
            try:
                self._cost = ideas.costs()["taker_rt_pct"]
            except Exception:
                self._cost = 0.24
        return self._cost

    def _stale_code(self, i, st, price, level, dist, now, syms):
        """-> (код, короткая заметка) первой сработавшей причины ревизии или None. Только чтение: состояние наблюдения (reg0, adv_since) обновляется в st."""
        cfg, side, hit = self.cfg, i.get("side"), bool(st.get("hit"))
        row = (syms or {}).get(i.get("sym")) if syms else None
        row = row if isinstance(row, dict) else None
        kept = i.get("kept") or {}
        found = []
        # режим монеты: начальный режим фиксируется при первом наблюдении, против идеи считается только смена режима
        if row is not None:
            reg = row.get("reg") if row.get("reg") not in (None, "unknown") else None
            if reg is not None and "reg0" not in st:
                st["reg0"] = reg
            c = cfg["regime_flip"]
            adverse = reg is not None and reg != st.get("reg0") and (reg == "storm" or (side == "long" and reg == "trend_down") or (side == "short" and reg == "trend_up"))
            if adverse:
                st.setdefault("adv_since", now)
            else:
                st.pop("adv_since", None)
            if c["enabled"] and adverse and now - st["adv_since"] >= float(c["hold_s"]):
                found.append(("regime_flip", "режим %s -> %s" % (st.get("reg0"), reg)))
        c = cfg["btc_drift"]
        btc = (syms or {}).get("BTC") if syms else None
        r1h = ideas.num(btc.get("r1h")) if isinstance(btc, dict) else None
        if c["enabled"] and i.get("sym") != "BTC" and r1h is not None and ((side == "long" and r1h <= -float(c["r1h_pct"])) or (side == "short" and r1h >= float(c["r1h_pct"]))):
            found.append(("btc_drift", "BTC за час %+.1f%%" % r1h))
        c = cfg["level_gone"]
        if c["enabled"] and row is not None and not hit and now - (ideas.num(i.get("ts_epoch")) or now) >= float(c["min_age_s"]) and self._level_gone(row, level, float(c["tol_pct"])):
            found.append(("level_gone", "уровня %g нет в структуре" % level))
        c = cfg["rr_changed"]
        stop, tp, a5 = ideas.num(i.get("stop")), ideas.num(i.get("tp")), ideas.num((row or {}).get("a5_pct"))
        if c["enabled"] and stop and tp and a5 and level > 0:
            cost = self._cost_rt()
            reward = abs(tp - level) / level * 100.0 - cost
            risk0 = abs(level - stop) / level * 100.0
            risk1 = max(risk0, float(c["stop_floor_a5"]) * a5)
            rr0, rr1 = reward / (risk0 + cost), reward / (risk1 + cost)
            if rr0 >= float(c["min_rr_net"]) > rr1:
                found.append(("rr_changed", "RR %.1f -> %.1f (a5 %.2f%%)" % (rr0, rr1, a5)))
        c = cfg["price_far"]
        a1h = ideas.num((row or {}).get("a1h_pct"))
        if c["enabled"] and not hit and a1h and abs(dist) >= float(c["min_pct"]) and abs(dist) / a1h >= float(c["atr_1h"]) and abs(dist) >= float(c["grow"]) * (st.get("d0") or 0.0):
            found.append(("price_far", "цена в %.1f%% (%.1f ATR 1ч) от уровня" % (abs(dist), abs(dist) / a1h)))
        c = cfg["hit_idle"]
        if c["enabled"] and hit and st.get("hit_ts") is not None and now - st["hit_ts"] >= float(c["after_s"]):
            found.append(("hit_idle", "уровень задет %d мин назад, входа нет" % ((now - st["hit_ts"]) // 60)))
        for code, note in found:
            until = ideas.num(kept.get(code))
            if until is not None and now < until:
                continue
            return code, note
        return None

    def _stale(self, i, st, price, level, dist, now, out):
        """Одна идея: если сработала причина ревизии, поставить флаг (дедуп идея+код) и добавить событие idea_stale. -> True, если состояние наблюдения изменилось."""
        before = (st.get("reg0"), st.get("adv_since"))
        if i.get("stale_code"):
            return False                                   # флаг уже стоит: ждём решения мозга (keep/update/cancel) или конца срока
        r = self._stale_code(i, st, price, level, dist, now, self._struct(now))
        changed = before != (st.get("reg0"), st.get("adv_since"))
        if r is None or now - (ideas.num(i.get("ts_epoch")) or now) < float(self.cfg["min_age_s"]):
            return changed
        self._flag_log = [t for t in self._flag_log if now - t < 3600.0]
        if len(self._flag_log) >= int(self.cfg["max_per_hour"]):
            return changed
        code, note = r
        if ideas.flag_idea(i["id"], code, note, path=self.path, now=now) is None:
            return changed
        self._flag_log.append(now)
        ev = self._event("idea_stale", i, price, dist, level)
        ev["stale"] = code
        out.append(ev)
        return changed

    @staticmethod
    def _level_gone(row, level, tol_pct):
        """Уровня идеи нет среди уровней структуры монеты, хотя он лежит в зоне, которую структура перечисляет полностью (ближайшие 2 res/sup). Дальше этой зоны: неизвестно -> False."""
        px = ideas.num(row.get("px"))
        if not px:
            return False
        tol = level * tol_pct / 100.0
        pool = []
        for k in ("res", "sup"):
            pool += [ideas.num(r.get("px")) for r in (row.get(k) or []) if isinstance(r, dict)]
        for k in ("day", "pd", "wk"):
            pool += [ideas.num(v) for v in (row.get(k) or [])[:3]]
        if any(v is not None and abs(v - level) <= tol for v in pool):
            return False
        side_rows = [ideas.num(r.get("px")) for r in (row.get("res" if level > px else "sup") or []) if isinstance(r, dict) and ideas.num(r.get("px")) is not None]
        if len(side_rows) >= 2:
            far = max(side_rows) if level > px else min(side_rows)
            if (level > far + tol) if level > px else (level < far - tol):
                return False                                           # дальше второго уровня структура не перечисляет: судить нельзя
        return bool(pool)

    def check(self, tickers, now):
        """tickers: {SYMUSDT: {"price"}} из ответа сканера. -> список событий (dict без ts/env); побочно ставит статусы expired/invalidated."""
        out, dirty = [], False
        alive = set()
        for i in list(self._active()):
            iid = i["id"]
            exp = ideas.expires_epoch(i, self.default_ttl_h)
            if exp is not None and now >= exp:
                ttl = ideas.num(i.get("ttl_h"))
                ideas.mark_if_active(iid, "expired", "ttl %g ч истёк" % (self.default_ttl_h if ttl is None else ttl), path=self.path, now=now)
                continue
            level = ideas.num(i.get("level"))
            t = tickers.get((i.get("sym") or "") + "USDT")
            price = ideas.num((t or {}).get("price"))
            if not level or level <= 0 or not price:
                continue
            alive.add(iid)
            st = self.st.setdefault(iid, {})
            if st.get("rev", 0) != int(i.get("rev") or 0):            # идею пересчитали (update): уровень новый, наблюдение начинается заново
                st.clear()
                st["rev"] = int(i.get("rev") or 0)
                dirty = True
            if "s0" not in st:
                p0 = ideas.num(i.get("price"))
                ref = p0 if p0 and p0 != level else price
                st["s0"] = (1 if ref > level else -1) if ref != level else (-1 if i.get("side") == "short" else 1)
                dirty = True
            dist = 100.0 * (price / level - 1.0)
            stop, tp = ideas.num(i.get("stop")), ideas.num(i.get("tp"))
            if stop and stop != level and (price - stop) * (1 if stop > level else -1) >= 0:          # и после касания уровня: пробитый стоп закрывает идею (не зомби)
                if ideas.mark_if_active(iid, "invalidated", "цена %g пересекла stop %g %s уровня %g" % (price, stop, "после касания" if st.get("hit") else "без касания", level),
                                        path=self.path, now=now):
                    out.append(self._event("idea_invalidated", i, price, dist, level))
                self.st.pop(iid, None)
                alive.discard(iid)
                dirty = True
                continue
            if tp and tp != level and (price - tp) * (1 if tp > level else -1) >= 0:
                if ideas.mark_if_active(iid, "expired", "tp %g достигнут без входа (цена %g)" % (tp, price), path=self.path, now=now):
                    out.append(self._event("idea_tp", i, price, dist, level))
                self.st.pop(iid, None)
                alive.discard(iid)
                dirty = True
                continue
            if st.get("hit"):
                if not i.get("hit"):                                    # касание уже было (состояние до ревизии или сбой записи): дописать в хранилище
                    ideas.mark_hit(iid, st.get("hit_ts") or now, path=self.path, now=now)
                dirty = self._stale(i, st, price, level, dist, now, out) or dirty
                continue
            if "d0" not in st:
                st["d0"] = abs(dist)
                dirty = True
            if (price - level) * st["s0"] <= 0:
                st["hit"], st["in_zone"], st["hit_ts"] = True, True, now
                ideas.mark_hit(iid, now, path=self.path, now=now)
                out.append(self._event("idea_level", i, price, dist, level))
                dirty = True
                continue
            dirty = self._stale(i, st, price, level, dist, now, out) or dirty
            if abs(dist) <= self.zone:
                if not st.get("in_zone"):
                    st["in_zone"] = True
                    dirty = True
                    if now - st.get("near_ts", -1e18) >= self.cooldown:
                        st["near_ts"] = now
                        out.append(self._event("idea_near", i, price, dist, level))
            elif st.get("in_zone") and abs(dist) > self.zone * EXIT_K:
                st["in_zone"] = False
                dirty = True
        for k in [k for k in self.st if k not in alive]:          # идея ушла из активных (взята, помечена, ttl)
            del self.st[k]
            dirty = True
        if dirty:
            self._save_state()
        return out
