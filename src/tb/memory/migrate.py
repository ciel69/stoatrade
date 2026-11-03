"""Писатель случаев и миграция накопленного (08 п. 1.3, 2.2). Читает журналы (trades/ideas/hot_outcomes/decisions), ничего в них не меняет.

plan() строит список новых случаев и update-строк, apply() пишет их в cases.jsonl. Идемпотентность: case_id = детерминированная функция источника,
существующие case_id пропускаются; update (whatif/разбор) добавляется только если поля ещё нет. `./tb memory migrate [--dry-run] [--with-signals]` и
`./tb memory sync` используют один и тот же план (sync дополнительно пересчитывает уроки).
Правила: закрытые сделки -> trade (механические проверки: role=mechanics и exclude_from_stats), идеи -> idea (из заметки числа не парсятся; taken со сделкой
помечается dup_of_trade), решения skip -> skip (если есть sym), hot_outcomes без идеи -> signal только при --with-signals. Свободный текст lesson/note НЕ переносится.
"""
import json
import os
import re

from tb import paths
from tb.core import decisions, ideas
from tb.core.tradectx import ERR_TAGS
from tb.memory import cases, features, privacy


def _fold_trades(path):
    trades, order = {}, []
    for r in ideas.read_rows(path):
        i, ev = r.get("id"), r.get("ev")
        if ev == "open" and i:
            trades[i] = {"open": dict(r), "close": None, "post": None}
            order.append(i)
        elif ev == "close" and i in trades:
            trades[i]["close"] = dict(trades[i]["close"] or {}, **r)
        elif ev == "post" and i in trades:
            trades[i]["post"] = dict(trades[i]["post"] or {}, **dict((k, v) for k, v in r.items() if k in ("err", "rule_followed", "lesson", "ts_post")))
        elif ev == "adjust" and i in trades and trades[i]["close"] is None:
            for k in ("sl", "tp"):
                if r.get(k) is not None:
                    trades[i]["open"].setdefault(k + "0", trades[i]["open"].get(k))
    return [(i, trades[i]) for i in order]


PLAN_BRIEF = "plan_brief"                       # decisions what=plan_brief: связь плана с досье и событием, на которых он подан (пишет шлюз при подаче плана)
RE_BRIEF_ID = re.compile(r"^B-[0-9]{8}-[0-9]{4}-[0-9]{2}$")
RE_PLAN_ID = re.compile(r"^PL-[A-Za-z0-9-]{1,40}$")
RULE_EVAL = "rule_eval"                         # decisions kind=note, what=rule_eval: проверка плана правилами rulebook на подаче (шлюз, R2); basis.rulebook_ver
RE_RULEBOOK_VER = re.compile(r"^R-[0-9]{4,6}$")  # src.rulebook_ver в схеме случая — строка до 8 символов


def read_sources(root=None):
    outs = {}
    for r in ideas.read_rows(ideas.outcomes_path(root)):
        if r.get("id") is not None:
            outs.setdefault((r.get("kind"), str(r["id"])), []).append(r)
    decs, links, rbv = [], {}, {}
    try:
        for d in decisions.read(limit=0, root=root):
            if d.get("kind") == "skip":
                decs.append(d)
            elif d.get("what") == PLAN_BRIEF and isinstance(d.get("plan"), str) and isinstance(d.get("basis"), dict):
                links.setdefault(d["plan"], d["basis"])          # первая запись плана: досье первой подачи
            elif d.get("what") == RULE_EVAL and d.get("kind") == "note" and isinstance(d.get("plan"), str) and isinstance(d.get("basis"), dict):
                v = d["basis"].get("rulebook_ver")
                if isinstance(v, str) and RE_RULEBOOK_VER.match(v):
                    rbv.setdefault(d["plan"], v)                 # версия rulebook, с которой шлюз проверил план на подаче (момент решения)
    except (OSError, ValueError):
        decs, links, rbv = [], {}, {}
    return {"trades": _fold_trades(paths.state("trades.jsonl", root=root)), "ideas": ideas.load_ideas(root=root), "outcomes": outs, "decisions": decs,
            "plan_links": links, "plan_rulebook": rbv}


def _brief(root, brief_id):
    """Сохранённое досье state/briefs/<brief_id>.json (в нём блок snap: fng, dvol, risk_off, blackout, даже в урезанной версии) или None."""
    if not isinstance(brief_id, str) or not RE_BRIEF_ID.match(brief_id):
        return None
    try:
        with open(paths.briefs("%s.json" % brief_id, root=root), encoding="utf-8") as f:
            b = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    return b if isinstance(b, dict) else None


class Ext(object):
    """Ленивые источники признаков НОВОГО случая сделки: связь плана с досье (decisions plan_brief), spec плана, события шины (trigger_fired, scanner_signal).
    Шина читается один раз и только если понадобилась."""

    def __init__(self, root, links, rulebook=None):
        self.root, self.links, self.rulebook = root, links or {}, rulebook or {}
        self._bus = None

    def link(self, plan_id):
        return self.links.get(plan_id) or {}

    def rulebook_ver(self, plan_id):
        """Версия rulebook на момент решения: из первой записи rule_eval плана в журнале решений; нет записи — None (случай «до R2»)."""
        return self.rulebook.get(plan_id) if isinstance(plan_id, str) else None

    def spec(self, plan_id):
        if not isinstance(plan_id, str) or not RE_PLAN_ID.match(plan_id):
            return None
        try:
            with open(paths.plans(plan_id, "spec.json", root=self.root), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError, TypeError):
            return None
        return d if isinstance(d, dict) else None

    def _load_bus(self):
        if self._bus is None:
            sig, fired = {}, {}
            try:
                from tb.core import bus
                for e in bus.read(0, limit=0, root=self.root):
                    d = e.get("d") if isinstance(e.get("d"), dict) else {}
                    if e.get("type") == "scanner_signal" and isinstance(e.get("seq"), int):
                        sig[e["seq"]] = d
                    elif e.get("type") == "trigger_fired" and e.get("plan"):
                        fired.setdefault(e["plan"], []).append(d.get("cond"))
            except (OSError, ValueError, TypeError):
                pass
            self._bus = (sig, fired)
        return self._bus

    def event(self, seq):
        return self._load_bus()[0].get(seq) if isinstance(seq, int) else None

    def fired(self, plan_id):
        return self._load_bus()[1].get(plan_id) or []


def _post_tags(post):
    err = [e for e in ((post or {}).get("err") or []) if e in ERR_TAGS] if isinstance((post or {}).get("err"), list) else []
    rf = (post or {}).get("rule_followed")
    return {"err": err[:5], "rule_followed": rf if rf in cases.RULE_FOLLOWED else None}


def build_trade_case(tid, t, cfg, root=None, ext=None):
    """ext (Ext) передаётся для НОВЫХ случаев: досье по brief_id (журнал или связь плана), confirmed из плана, рыночные поля из события шины по event_seq."""
    op, cl, post = t["open"], t["close"], t["post"]
    tags = features.parse_tags(op.get("tags"))
    sit = features.situation_from_trade(op, post, cfg)
    plan_id = op.get("plan_id") or tags["plan"]
    brief_id, event_seq, spec = op.get("brief_id"), None, None
    if ext is not None and plan_id:
        link, spec = ext.link(plan_id), ext.spec(plan_id)
        brief_id = brief_id or link.get("brief_id")
        seq = link.get("event_seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            seq = ((spec or {}).get("source") or {}).get("event_seq")
        event_seq = seq if isinstance(seq, int) and not isinstance(seq, bool) else None
        sit = features.apply_plan(sit, spec, () if spec is not None else ext.fired(plan_id))
    brief_id = brief_id if isinstance(brief_id, str) and RE_BRIEF_ID.match(brief_id) else None
    sit = features.apply_brief(sit, _brief(root, brief_id))
    if ext is not None and event_seq is not None and any(sit.get(k) is None for k in features.EVENT_FILL):
        sit = features.apply_event(sit, ext.event(event_seq))
    mech = features.is_mechanics(op)
    why, sus = features.why_text(op.get("thesis"))
    role = "mechanics" if mech else ("manual" if tags["manual"] else "chat")
    c = {"case_id": cases.case_id_for(op.get("ts_open"), sit["sym"], tid), "type": "trade", "ts": op.get("ts_open"),
         "refs": {"journal_id": tid, "idea": op.get("idea"), "plan": plan_id, "brief_id": brief_id, "event_seq": event_seq},
         "src": {"actor": "owner" if tags["manual"] else "agent", "agent_id": tags["agent_id"], "model": tags["model"], "role": role, "exp": op.get("exp") or tags["exp"],
                 "var": op.get("var") or tags["var"], "rules_ver": cfg.get("rules_ver"), "mode": op.get("mode") if op.get("mode") in cases.MODES else "live", "mem": tags["mem"]},
         "situation": sit,
         "decision": {"action": "enter", "why": why or None, "conf": None, "stop_dist_vs_range5": features.stop_vs_range5(sit)},
         "result": features.result_from_close(op, cl), "whatif": None, "review": None, "tags": _post_tags(post),
         "flags": (["retro_features"] if sit.get("retro") else []) + (["inj_suspect"] if sus else []),
         "exclude_from_stats": role in (cfg.get("exclude_roles") or [])}
    rbv = ext.rulebook_ver(plan_id) if ext is not None and plan_id else None
    if rbv is not None:
        c["src"]["rulebook_ver"] = rbv                   # R2: версия rulebook на момент решения; без неё случай относится к «до R2»
    return c


IDEA_ACTION = {"taken": "enter", "skipped": "skip"}


def build_idea_case(i, outs, cfg, trade_ids):
    sit = features.situation_from_idea(i, cfg)
    why, sus = features.why_text(i.get("note"))
    status = i.get("status")
    taken = bool(i.get("trade")) and i.get("trade") in trade_ids          # идея привела к сделке (статус мог смениться на invalidated после)
    wi = features.whatif_from_outcomes(outs.get(("idea", str(i.get("id"))), []), cfg.get("whatif_h_min", 15))
    return {"case_id": cases.case_id_for(i.get("ts"), sit["sym"], "idea:%s" % i.get("id")), "type": "idea", "ts": i.get("ts"),
            "refs": {"idea": i.get("id"), "journal_id": i.get("trade") if taken else None, "plan": i.get("plan"), "outcome_id": str(i.get("id")) if wi else None},
            "src": {"actor": "agent" if i.get("src") in (None, "agent") else "owner", "role": "chat", "rules_ver": cfg.get("rules_ver"), "mode": i.get("mode") if i.get("mode") in ("paper", "testnet", "live") else "live"},
            "situation": sit, "decision": {"action": "enter" if taken else IDEA_ACTION.get(status, "wait"), "why": why or None, "skip_code": "OTHER" if status == "skipped" else None},
            "result": {"status": "na"}, "whatif": wi, "review": None, "tags": {"err": [], "rule_followed": None},
            "flags": (["backfill"] if i.get("tag") == "backfill" else []) + (["dup_of_trade"] if taken else []) + (["inj_suspect"] if sus else []),
            "exclude_from_stats": False}


def build_signal_case(oid, rows, cfg):
    r0 = rows[0]
    sit = features.situation_from_outcome(r0, cfg)
    wi = features.whatif_from_outcomes(rows, cfg.get("whatif_h_min", 15))
    return {"case_id": cases.case_id_for(r0.get("ts"), sit["sym"], "sig:%s" % oid), "type": "signal", "ts": r0.get("ts"),
            "refs": {"outcome_id": str(oid)[:48]}, "src": {"actor": "system", "rules_ver": cfg.get("rules_ver"), "mode": "live"}, "situation": sit,
            "decision": {"action": "wait"}, "result": {"status": "na"}, "whatif": wi, "review": None, "tags": {"err": [], "rule_followed": None}, "flags": [],
            "exclude_from_stats": False}


def build_skip_case(d, cfg):
    a = d.get("args") if isinstance(d.get("args"), dict) else {}
    sym = features.base_sym(a.get("sym"))
    if not cases.RE_SYM.match(sym or "") or a.get("side") not in cases.SIDES:
        return None
    sit = features.finalize({"sym": sym, "side": a["side"], "coin_class": features.classify_coin(sym, cfg), "setup": a.get("setup") if a.get("setup") in cases.SETUPS else None,
                             "confirm_kind": "unknown", **features.time_feats(d.get("ts"))})
    why, sus = features.why_text(d.get("why"))
    actor = str(d.get("actor") or "")
    code = a.get("skip_code") if a.get("skip_code") in cases.SKIP_CODES else "OTHER"
    return {"case_id": cases.case_id_for(d.get("ts"), sym, "dec:%s" % d.get("seq")), "type": "skip", "ts": d.get("ts"),
            "refs": {"plan": d.get("plan"), "event_seq": d.get("seq")},
            "src": {"actor": "owner" if actor == "owner" else ("agent" if actor.startswith("brain") else "system"), "agent_id": actor.split(":", 1)[1][:16] if ":" in actor else None,
                    "rules_ver": cfg.get("rules_ver"), "mode": "live"},
            "situation": sit, "decision": {"action": "skip", "why": why or None, "skip_code": code}, "result": {"status": "na"}, "whatif": None, "review": None,
            "tags": {"err": [], "rule_followed": None}, "flags": ["inj_suspect"] if sus else [], "exclude_from_stats": False}


def plan(root=None, cfg=None, with_signals=False, final_only=False):
    """-> {"new": [случай], "updates": [(case_id, patch)], "report": {...}} без записи.
    final_only (периодический писатель, WP15b): идеи в статусах idea/watching ещё не решены и случаем не становятся (decision/situation после записи неизменны);
    случай идеи создаётся, когда она получила итоговый статус (taken/skipped/expired/invalidated), whatif дописывается update-строкой позже."""
    from tb.memory import cfg as memcfg
    cfg = cfg or memcfg.load(root)
    src = read_sources(root)
    existing = dict((c["case_id"], c) for c in cases.load(root))
    rep = {"sources": {"trades_closed": 0, "ideas": len(src["ideas"]), "decisions_skip": len(src["decisions"]), "signals": 0}, "new": {}, "exists": 0,
           "excluded": [], "with_missing": 0, "errors": [], "updates": {"whatif": 0, "tags": 0}}
    built = []
    trade_ids = set()
    ext = Ext(root, src.get("plan_links"), src.get("plan_rulebook"))
    for tid, t in src["trades"]:
        if t["close"] is not None:
            rep["sources"]["trades_closed"] += 1
            trade_ids.add(tid)
            op = t["open"]
            new_case = cases.case_id_for(op.get("ts_open"), features.base_sym(op.get("sym")), tid) not in existing      # дорогие источники (план, шина) только для нового случая
            built.append(("trades", tid, build_trade_case(tid, t, cfg, root, ext if new_case else None), t))
    for i in src["ideas"]:
        if final_only and (i.get("status") or "idea") in ideas.ACTIVE + ("planned",):
            continue
        built.append(("ideas", i.get("id"), build_idea_case(i, src["outcomes"], cfg, trade_ids), None))
    for d in src["decisions"]:
        c = build_skip_case(d, cfg)
        if c:
            built.append(("decisions", d.get("seq"), c, None))
    if with_signals or cfg.get("record_signals"):
        idea_ids = set(str(i.get("id")) for i in src["ideas"])
        sig = dict((k[1], v) for k, v in src["outcomes"].items() if k[0] == "event" and k[1] not in idea_ids)
        rep["sources"]["signals"] = len(sig)
        for oid in sorted(sig):
            built.append(("signals", oid, build_signal_case(oid, sorted(sig[oid], key=lambda r: r.get("h") or 0), cfg), None))
    new, updates = [], []
    for kind, sid, c, t in sorted(built, key=lambda x: (x[2].get("ts") or "", x[2]["case_id"])):
        old = existing.get(c["case_id"])
        if old is not None:
            rep["exists"] += 1
            patch = {}
            if c.get("whatif") and not old.get("whatif"):
                patch["whatif"] = c["whatif"]
                rep["updates"]["whatif"] += 1
            if t is not None and (c["tags"]["err"] or c["tags"]["rule_followed"]) and not (old.get("tags") or {}).get("err") and not (old.get("tags") or {}).get("rule_followed"):
                patch["tags"] = c["tags"]
                rep["updates"]["tags"] += 1
            if patch:
                updates.append((c["case_id"], patch))
            continue
        norm, errs = cases.validate(c)
        if errs:
            rep["errors"].append({"source": kind, "id": str(sid), "errors": errs[:3]})
            continue
        new.append(c)
        rep["new"][c["type"]] = rep["new"].get(c["type"], 0) + 1
        if (c["situation"].get("missing") or []):
            rep["with_missing"] += 1
        if c.get("exclude_from_stats"):
            rep["excluded"].append({"case_id": c["case_id"], "role": c["src"].get("role"), "journal_id": c["refs"].get("journal_id")})
    return {"new": new, "updates": updates, "report": rep}


def apply(pl, root=None):
    """Пишет план -> число записанных. Конкурирующий дубль (кто-то успел записать) тихо пропускается."""
    n = 0
    for c in pl["new"]:
        try:
            cases.append_new(c, root=root)
            n += 1
        except cases.CaseError as e:
            if not str(e).startswith("exists"):
                raise
    for cid, patch in pl["updates"]:
        cases.append_update(cid, patch, root=root)
        n += 1
    return n


def migrate(root=None, dry_run=False, with_signals=False, cfg=None, final_only=False):
    """-> отчёт миграции (сколько случаев из каких источников, с missing, исключённые, ошибки)."""
    pl = plan(root, cfg, with_signals, final_only)
    rep = dict(pl["report"], dry_run=bool(dry_run), would_write=len(pl["new"]) + len(pl["updates"]))
    rep["written"] = 0 if dry_run else apply(pl, root)
    return rep
