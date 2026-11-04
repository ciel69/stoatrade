"""Монеты в фокусе сводного рынка (R1.7, задача 7; Requirements 1.1, 1.2, 1.4, 1.5, 1.6, 1.7, 12.3, 17.6; design.md, «Монеты в фокусе»).

Набор = ядро `focus.core` (BTC, ETH, SOL) + топ-`top_n` по межбиржевому обороту 24 ч (`runtime/heat.json`: `xturn_24h_musd`, иначе
`turn_24h_musd`; пересчёт раз в 300 с) + динамика: монеты открытых позиций и планов (`runtime/exec_snapshot*.json`), монеты сигналов
`runtime/hot_events.jsonl` за `keep_signal_min` минут, монеты запросов структуры (`runtime/structure_req.json`, живут
`struct_req_ttl_min`). Нового файла запросов нет; у мозга, шлюза и MCP пути записи в набор нет — он меняется только по этим источникам
и сжатию по бюджету.

Ярусы: `deep` (не больше `max_deep`) — позиции, планы, ядро, топ-`deep_top_n` по обороту; остальное — `light` (не больше `max_light`,
после сжатия по бюджету — `light_cap`). Вытеснение — обратно приоритету: позиции > планы > ядро > запросы структуры > сигналы
(новее выше) > топ по обороту > удерживаемые; позиции и планы не вытесняются никогда (ярус `deep` расширяется на их число).
Выпавшая из набора монета держится ещё `hold_s` (600 с) до отписки; `since` — момент входа монеты в фокус (признак с окном w — None,
пока монета в фокусе меньше w). Монеты совместимого `xflow.json` (`logic.cmkt.compat_symbols`, до 8: позиции и планы, запросы
структуры, BTC/ETH/SOL, затем топ по обороту из фокуса) всегда в фокусе (Requirement 17.6).

`BUDGET`: бюджет превышен `budget.over_min` минут подряд — ярус `light` уменьшается на `shrink_step` монет за шаг, не ниже `light_min`.
`FOCUS_NO_HEAT`: `heat.json` старше 300 с или нет, а известного списка топа нет — только ядро и динамика.

Результат — `runtime/cmkt/focus.json` (атомарная запись; пишет только сервис сводки): при изменении набора и не реже раза в 10 с.
"""
import datetime
import glob
import json
import os
import re
import time

from tb import paths
from tb.core import filelock
from tb.logic import cmkt as L

SCHEMA = "tb.cmkt_focus/1"
FILE = "focus.json"
TOP_REFRESH_S = 300.0
HEAT_MAX_AGE_S = 300.0
WRITE_EVERY_S = 10.0
COMPUTE_EVERY_S = 2.0
SINCE_TRUST_S = 60.0                     # since из прежнего focus.json верен, только если файл свежий (сбор не прерывался)
TAIL_FIRST_BYTES = 8 * 1024 * 1024
TAIL_MAX_READ = 16 * 1024 * 1024
COMPAT_N = 8
PRIORITY = ("pos", "plan", "core", "struct", "signal", "top", "hold")
_RE_SYM = re.compile(r"^[A-Z0-9]{1,20}$")


def focus_path(root=None):
    return paths.runtime("cmkt", FILE, root=root)


def _sym(s):
    if not isinstance(s, str):
        return None
    s = s.strip().upper()
    if s.endswith("USDT") and len(s) > 4:
        s = s[:-4]
    return s if _RE_SYM.match(s) else None


def _t0(ts):
    """ISO-время строки hot_events.jsonl -> целая секунда UTC (как labels.capture._t0)."""
    if not isinstance(ts, str) or not ts:
        return None
    s = ts.strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- источники (только чтение runtime/)
def read_exec(root):
    """Монеты позиций и планов исполнителей (runtime/exec_snapshot.json и exec_snapshot@<биржа>.json) -> (позиции, планы)."""
    pos, plans = [], []
    files = [paths.exec_snapshot_file(root=root)] + sorted(glob.glob(paths.runtime("exec_snapshot@*.json", root=root)))
    for p in files:
        snap = _read_json(p)
        if not isinstance(snap, dict):
            continue
        for key, out in (("positions", pos), ("plans", plans)):
            for row in snap.get(key) if isinstance(snap.get(key), list) else ():
                s = _sym(row.get("sym")) if isinstance(row, dict) else None
                if s and s not in out:
                    out.append(s)
    return pos, [s for s in plans if s not in pos]


def read_struct(root, now, ttl_s):
    """Запросы структуры мозга и сканера (runtime/structure_req.json: {"req": {SYM: epoch}}), новые первыми, не старше ttl_s."""
    d = _read_json(paths.runtime("structure_req.json", root=root))
    req = d.get("req") if isinstance(d, dict) and isinstance(d.get("req"), dict) else {}
    live = []
    for s, t in req.items():
        sym = _sym(s)
        if sym and isinstance(t, (int, float)) and not isinstance(t, bool) and 0 <= now - t < ttl_s:
            live.append((float(t), sym))
    out = []
    for _, s in sorted(live, reverse=True):
        if s not in out:
            out.append(s)
    return out


def read_heat(root, now, top_n, max_age_s=HEAT_MAX_AGE_S):
    """Топ-top_n монет по межбиржевому обороту из runtime/heat.json (xturn_24h_musd, иначе turn_24h_musd) или None, если файла нет
    или он старше max_age_s."""
    d = _read_json(paths.runtime("heat.json", root=root))
    if not isinstance(d, dict):
        return None
    ts = d.get("ts_epoch")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool) or now - ts > max_age_s:
        return None
    rows = []
    for r in d.get("rows") if isinstance(d.get("rows"), list) else ():
        if not isinstance(r, dict):
            continue
        s = _sym(r.get("sym"))
        turn = L.num(r.get("xturn_24h_musd"))
        if turn is None:
            turn = L.num(r.get("turn_24h_musd"))
        if s and turn is not None:
            rows.append((-turn, s))
    out = []
    for _, s in sorted(rows):
        if s not in out:
            out.append(s)
    return out[:int(top_n)]


class SignalTail(object):
    """Хвост runtime/hot_events.jsonl: последняя отметка сигнала каждой монеты (инкрементально по смещению; при усечении файла —
    сначала). Служебные строки (scan_*) и строки без монеты не учитываются."""

    def __init__(self, root=None, path=None):
        self.path = path or paths.runtime("hot_events.jsonl", root=root)
        self.offset = None
        self.last = {}                                  # sym -> t0

    def poll(self, now, keep_s):
        try:
            size = os.path.getsize(self.path)
        except OSError:
            self._prune(now, keep_s)
            return self.last
        if self.offset is None or size < self.offset:
            self.offset = max(0, size - TAIL_FIRST_BYTES)
            skip_partial = self.offset > 0
        else:
            skip_partial = False
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read(TAIL_MAX_READ)
        except OSError:
            return self.last
        end = data.rfind(b"\n")
        if end < 0:
            return self.last                            # неполная строка не читается
        chunk = data[:end + 1]
        self.offset += end + 1
        lines = chunk.split(b"\n")
        if skip_partial and lines:
            lines = lines[1:]
        for ln in lines:
            if not ln.strip():
                continue
            try:
                r = json.loads(ln.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(r, dict) or str(r.get("type") or "").startswith("scan_"):
                continue
            s, t0 = _sym(r.get("sym")), _t0(r.get("ts"))
            if s and t0 is not None and t0 > self.last.get(s, -1):
                self.last[s] = t0
        self._prune(now, keep_s)
        return self.last

    def _prune(self, now, keep_s):
        for s in [s for s, t in self.last.items() if now - t > keep_s]:
            del self.last[s]

    def recent(self, now, keep_s):
        """Монеты сигналов за keep_s, новее первыми."""
        rows = sorted(((t, s) for s, t in self.last.items() if now - t <= keep_s), reverse=True)
        return [s for _, s in rows]


class FileSources(object):
    """Источники фокуса из файлов runtime/ (только чтение)."""

    def __init__(self, root=None):
        self.root = root
        self.tail = SignalTail(root)

    def exec(self, now):
        return read_exec(self.root)

    def struct(self, now, ttl_s):
        return read_struct(self.root, now, ttl_s)

    def signals(self, now, keep_s):
        self.tail.poll(now, keep_s)
        return self.tail.recent(now, keep_s)

    def heat(self, now, top_n):
        return read_heat(self.root, now, top_n)


# ---------------------------------------------------------------- выбор набора
def _uniq(seq, skip=()):
    out, seen = [], set(skip)
    for s in seq:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def select(pos, plans, core, struct, signals, top, fcfg, light_cap=None, held=()):
    """Ярусы по приоритетам и пределам. -> {"deep": [...], "light": [...], "why": {sym: причина}, "compat": [...]}.
    held — удерживаемые монеты (выпали из набора меньше hold_s назад): самый низкий приоритет, в пределах ярусов."""
    max_deep, max_light = int(fcfg["max_deep"]), int(fcfg["max_light"])
    cap_light = max_light if light_cap is None else min(max_light, int(light_cap))
    why = {}

    def mark(syms, reason):
        for s in syms:
            why.setdefault(s, reason)
    fixed = _uniq(list(pos) + list(plans))
    mark(pos, "pos")
    mark(plans, "plan")
    core_ = _uniq(core, fixed)
    mark(core_, "core")
    top_ = _uniq(top)
    deep_top = _uniq(top_[:int(fcfg["deep_top_n"])], fixed + core_)
    mark(deep_top, "top")
    room = max(0, max_deep - len(fixed))                  # позиции и планы не вытесняются: deep расширяется на их число
    deep_c = core_ + deep_top
    deep = fixed + deep_c[:room]
    rest_core = [s for s in core_ if s not in deep]
    struct_ = _uniq(struct, deep)
    mark(struct_, "struct")
    sig_ = _uniq(signals, deep + struct_)
    mark(sig_, "signal")
    top_rest = _uniq(top_, deep + struct_ + sig_)
    mark(top_rest, "top")
    held_ = _uniq(held, deep + struct_ + sig_ + top_rest + rest_core)
    mark(held_, "hold")
    light_c = _uniq(rest_core + struct_ + sig_ + top_rest + held_, deep)
    light = light_c[:cap_light]
    in_focus = set(deep) | set(light)
    extra = [s for s in top_ if s in in_focus]
    compat = L.compat_symbols(fixed, list(struct), extra, COMPAT_N)
    for s in compat:                                      # монеты совместимого xflow.json всегда в фокусе (Requirement 17.6)
        if s not in in_focus:
            light.append(s)
            in_focus.add(s)
            why.setdefault(s, "compat")
    why = dict((s, why.get(s, "compat")) for s in deep + light)
    return {"deep": deep, "light": light, "why": why, "compat": compat}


class Focus(object):
    """Состояние фокуса сервиса сводки: since, удержание, кэш топа, предел light по бюджету, запись focus.json."""

    def __init__(self, root=None, cfg=None, sources=None, clock=time.time):
        self.root = root
        self.cfg = cfg
        self.f = dict((cfg or {}).get("focus") or {})
        self.bud = dict((cfg or {}).get("budget") or {})
        self.sources = sources or FileSources(root)
        self.clock = clock
        self.since, self.held, self.tier = {}, {}, {}
        self.top, self.top_t = [], None
        self.light_cap = int(self.f["max_light"])
        self.over_since = None
        self.doc = None
        self._computed = self._written = None
        self._load_prev()

    def _load_prev(self):
        d = read(self.root)
        if not isinstance(d, dict):
            return
        now = self.clock()
        if isinstance(d.get("top"), list):
            self.top = [s for s in (_sym(x) for x in d["top"]) if s][:int(self.f["top_n"])]
            self.top_t = d.get("top_ts") if isinstance(d.get("top_ts"), (int, float)) else None
        ts = d.get("ts")
        if isinstance(ts, (int, float)) and 0 <= now - ts <= SINCE_TRUST_S:
            for s, t in (d.get("since") or {}).items():
                if _sym(s) and isinstance(t, (int, float)):
                    self.since[s] = float(t)
            for s, t in (d.get("hold") or {}).items():
                if _sym(s) and isinstance(t, (int, float)):
                    self.held[s] = float(t)
            for k in ("deep", "light"):
                for s in d.get(k) or ():
                    if _sym(s):
                        self.tier[s] = k

    # --- бюджет (Requirement 12.3)
    def budget(self, over, now=None):
        """over — бюджет превышен сейчас. over_min минут подряд -> light_cap − shrink_step (не ниже light_min) и счёт заново."""
        now = self.clock() if now is None else now
        if not over:
            self.over_since = None
            return self.light_cap
        if self.over_since is None:
            self.over_since = now
        elif now - self.over_since >= float(self.bud.get("over_min", 10)) * 60.0:
            self.light_cap = max(int(self.bud.get("light_min", 40)), self.light_cap - int(self.bud.get("shrink_step", 20)))
            self.over_since = now
        return self.light_cap

    # --- такт
    def _top(self, now, warn):
        if self.top_t is None or now - self.top_t >= TOP_REFRESH_S or now < self.top_t or not self.top:
            fresh = self.sources.heat(now, int(self.f["top_n"]))
            if fresh is not None:
                self.top, self.top_t = fresh, now
        if not self.top:
            warn.append("FOCUS_NO_HEAT")
        return self.top

    def compute(self, now=None):
        now = self.clock() if now is None else now
        warn = []
        pos, plans = self.sources.exec(now)
        struct = self.sources.struct(now, float(self.f["struct_req_ttl_min"]) * 60.0)
        sig = self.sources.signals(now, float(self.f["keep_signal_min"]) * 60.0)
        top = self._top(now, warn)
        hold_s = float(self.f["hold_s"])
        sources_now = set(pos) | set(plans) | set(self.f["core"]) | set(struct) | set(sig) | set(top)
        for s in self.tier:                               # выпала из набора источников: удержание hold_s до отписки
            if s not in sources_now and s not in self.held:
                self.held[s] = now
        for s in list(self.held):
            if s in sources_now or not 0 <= now - self.held[s] < hold_s:
                del self.held[s]
        held = [s for s, _ in sorted(self.held.items(), key=lambda kv: (-kv[1], kv[0]))]
        sel = select(pos, plans, self.f["core"], struct, sig, top, self.f, self.light_cap, held)
        members = set(sel["deep"]) | set(sel["light"])
        for s in [s for s in self.held if s not in members]:      # вытеснена пределом яруса: удержание кончилось
            del self.held[s]
        for s in members:
            self.since.setdefault(s, now)
        for s in [s for s in self.since if s not in members]:
            del self.since[s]
        self.tier = dict([(s, "deep") for s in sel["deep"]] + [(s, "light") for s in sel["light"]])
        if self.light_cap < int(self.f["max_light"]):
            warn.append("BUDGET")
        doc = {"schema": SCHEMA, "ts": now, "deep": sel["deep"], "light": sel["light"], "why": sel["why"], "compat": sel["compat"],
               "since": dict((s, self.since[s]) for s in sel["deep"] + sel["light"]),
               "hold": dict((s, t) for s, t in self.held.items() if s in members),
               "top": list(top), "top_ts": self.top_t, "light_cap": self.light_cap, "warn": warn,
               "n": {"deep": len(sel["deep"]), "light": len(sel["light"]), "pos": len(pos), "plan": len(plans), "struct": len(struct),
                     "signal": len(sig), "top": len(top)}}
        self.doc = doc
        self._computed = now
        return doc

    def step(self, now=None, force=False):
        """Пересчёт не чаще раза в 2 с; запись focus.json при изменении набора и не реже раза в 10 с. -> документ."""
        now = self.clock() if now is None else now
        if not force and self.doc is not None and self._computed is not None and 0 <= now - self._computed < COMPUTE_EVERY_S:
            return self.doc
        prev = self.doc
        doc = self.compute(now)
        changed = prev is None or (prev["deep"], prev["light"]) != (doc["deep"], doc["light"])
        if force or changed or self._written is None or not 0 <= now - self._written < WRITE_EVERY_S:
            write(self.root, doc)
            self._written = now
        return doc

    def focus_s(self, sym, now=None):
        """Сколько монета в фокусе, с (None — не в фокусе)."""
        now = self.clock() if now is None else now
        t = self.since.get(sym)
        return None if t is None else max(0.0, now - t)


def write(root, doc):
    filelock.atomic_write(focus_path(root), json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def read(root=None, now=None, max_age_s=None):
    """focus.json или None (нет, битый, старше max_age_s). Не бросает."""
    d = _read_json(focus_path(root))
    if not isinstance(d, dict) or d.get("schema") != SCHEMA:
        return None
    if max_age_s is not None:
        now = time.time() if now is None else now
        if not isinstance(d.get("ts"), (int, float)) or now - d["ts"] > max_age_s:
            return None
    return d


def tier_of(doc, sym):
    """'deep' | 'light' | None по документу focus.json."""
    if not isinstance(doc, dict):
        return None
    if sym in (doc.get("deep") or ()):
        return "deep"
    if sym in (doc.get("light") or ()):
        return "light"
    return None
