"""Сборка отчёта `tb.learning/1` страницы «Учится ли мозг» (R0.2, задача 12). Только чтение.

build(root, mode, now)  полный ответ без кеша (до model.scrub, который делает слой dash);
get(root, mode, now)    то же с кешем tb.learn.cache.REPORT (ключ (корень, режим), подпись load.source_signature, ttl и min_interval из config/learning.json);
sanitize(obj)           допустимые символы и длина строк, NaN/inf -> None, округление чисел.

Общие данные (сделки, случаи, пропуски, журнал вызовов) загружаются один раз и раздаются блокам через Ctx. Исключение блока изолируется
(`_safe`): блок получает status=error, reason=E_BLOCK, текст исключения в ответ не попадает. Отчёт с блоком `computing` в кеш не кладётся:
готовый блок памяти досылается повторным сообщением (см. tb.learn.dashapi.follow).
"""
import copy
import json
import logging
import math
import os
import re
import time

from tb.core import expstats
from tb.labels import store as labels_store
from tb.learn import conf, discipline, economics, exits, load, memory_vs_base, result, skips, stages, stats, verdict, vs_policy
from tb.learn.cache import REPORT

SCHEMA = "tb.learning/1"
MODES = load.MODES
DEFAULT_MODE = "paper"
ORDER = ("result", "vs_policy", "discipline", "skips", "exits", "economics", "memory", "lessons", "versions")
TITLES = {"result": "Результат", "vs_policy": "Мозг против теневой политики", "discipline": "Дисциплина", "skips": "Пропуски", "exits": "Выходы",
          "economics": "Экономика", "memory": "Память против базы", "lessons": "Уроки", "versions": "Версии мозга до/после"}
STATUSES = ("ok", "few_data", "no_data", "stage", "computing", "error")
ASOF_STEP = 30
MAX_BYTES = 64 * 1024
STR_MAX = 64                       # данные из файлов (теги, названия, id)
TEXT_KEYS = ("note", "text", "title")    # тексты по шаблонам кода: кириллица и знаки допустимы
TEXT_MAX = 200
BAD_CHARS = re.compile(r"[^A-Za-z0-9_.:/ -]")
CTRL = re.compile(r"[\x00-\x1f\x7f]")
SECRET_WORDS = ("key", "secret", "token", "passw", "signature")
SERIES_BLOCKS = ("result", "exits")

log = logging.getLogger("tb.learn.report")


class Ctx(object):
    """Общие входные данные блоков. Имена полей — то, что читают build(ctx) блоков."""

    def __init__(self, **kw):
        self.root = None
        self.mode = DEFAULT_MODE
        self.now = 0
        self.cfg = {}
        self.trades = []
        self.cases = []
        self.skips = ([], "no_whatif")
        self.usage = []
        self.usage_meta = {}
        self.__dict__.update(kw)


def normalize_mode(mode):
    """Допустимый режим или paper (регистр и пробелы не исправляются: чужое значение заменяется)."""
    return mode if isinstance(mode, str) and mode in MODES else DEFAULT_MODE


# ---------------------------------------------------------------- очистка

def _num(v):
    if v != v or v in (math.inf, -math.inf):
        return None
    if v == 0 or abs(v) >= 0.001:
        return round(v, 4)
    return float("%.3g" % v)


def _text(s, limit):
    return CTRL.sub(" ", s)[:limit]


def sanitize(obj, key=None):
    """Рекурсивно: строки данных — алфавит [A-Za-z0-9_.:/ -] (остальное `?`) и до 64 символов; тексты note/text/title — до 200 символов без управляющих;
    NaN и inf -> None; дробные числа округляются; неизвестные типы -> None. Ключи словарей приводятся к строкам."""
    if isinstance(obj, dict):
        return {str(k): sanitize(v, str(k)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return _num(obj)
    if isinstance(obj, str):
        if key in TEXT_KEYS:
            return _text(obj, TEXT_MAX)
        return BAD_CHARS.sub("?", CTRL.sub(" ", obj))[:STR_MAX]
    return None


def field_names(obj):
    """Все имена полей ответа (рекурсивно)."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            out.extend(field_names(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(field_names(v))
    return out


# ---------------------------------------------------------------- блоки

def _error_block(bid):
    return {"id": bid, "title": TITLES[bid], "status": "error", "reason": "E_BLOCK", "note": "не удалось собрать блок"}


def _safe(bid, fn):
    """Блок или заглушка-ошибка: исключение уходит в журнал без значений (только класс), в ответ — E_BLOCK."""
    try:
        blk = fn()
    except Exception as e:  # noqa: BLE001 - изоляция блока
        log.warning("block %s failed: %s", bid, type(e).__name__)
        return _error_block(bid)
    if not isinstance(blk, dict) or blk.get("status") not in STATUSES:
        log.warning("block %s returned a bad shape", bid)
        return _error_block(bid)
    blk.setdefault("id", bid)
    blk["id"], blk["title"] = bid, TITLES[bid]
    blk.setdefault("note", "")
    blk.setdefault("reason", None)
    if blk["status"] == "stage":
        blk.setdefault("stage", stages.STAGE)
        blk.setdefault("roadmap", stages.ROADMAP)
    return blk


def _blocks(ctx):
    """-> блоки по ORDER и предупреждения блоков."""
    fns = {"result": lambda: result.build(ctx), "vs_policy": lambda: vs_policy.build(ctx), "discipline": lambda: discipline.build(ctx),
           "skips": lambda: skips.build(ctx), "exits": lambda: exits.build(ctx), "economics": lambda: economics.build(ctx),
           "memory": lambda: memory_vs_base.build(ctx), "lessons": lambda: stages.lessons(ctx), "versions": lambda: stages.versions(ctx)}
    out, warns = {}, []
    for bid in ORDER:
        blk = _safe(bid, fns[bid])
        w = blk.pop("warnings", None)
        if isinstance(w, list):
            warns.extend(x for x in w if isinstance(x, str))
        out[bid] = blk
    return out, warns


def _load(name, fn, default, warns):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - один источник не роняет отчёт
        log.warning("load %s failed: %s", name, type(e).__name__)
        warns.append("E_LOAD:" + name)
        return default


def _mixed_rules(trades):
    keys = set(str(t["rules_key"]) for t in trades if t.get("rules_key") is not None)
    return len(keys) > 1


def _fix_verdict(v, n_trades):
    """До n < 60 «недавних/прежних» нет: поля null, а не 0 (по контракту)."""
    if v.get("n_recent") == 0 and v.get("n_prev") == 0:
        v["n_recent"] = v["n_prev"] = None
    return v


# ---------------------------------------------------------------- размер

def _thin(out):
    """Прореживает ряды (каждая вторая точка, последняя сохраняется). -> изменилось ли."""
    changed = False
    for bid in SERIES_BLOCKS:
        ser = (out["blocks"].get(bid) or {}).get("series")
        if not isinstance(ser, dict):
            continue
        for w, pts in ser.items():
            if isinstance(pts, list) and len(pts) > 2:
                keep = pts[::2]
                if keep[-1] is not pts[-1]:
                    keep.append(pts[-1])
                ser[w] = keep
                changed = True
    return changed


def _size(out):
    return len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _fit(out):
    thinned = False
    while _size(out) > MAX_BYTES and _thin(out):
        thinned = True
    if thinned:
        out["meta"]["warnings"].append("series_thinned")
    return out


# ---------------------------------------------------------------- сборка

def build(root=None, mode=DEFAULT_MODE, now=None, clock=time.perf_counter):
    """Полный ответ tb.learning/1 без кеша. Недопустимый режим заменяется на paper; ответ несёт фактический режим."""
    t0 = clock()
    mode = normalize_mode(mode)
    now = time.time() if now is None else now
    now_i = int(now)
    cfg, warns = conf.load(root)
    trades, excluded = _load("trades", lambda: load.brain_trades(root, mode), ([], dict.fromkeys(load.EXCLUDED_KEYS, 0)), warns)
    cases = _load("cases", lambda: load.cases(root, mode), [], warns)
    skip = _load("skips", lambda: load.skip_cases(root, mode), ([], "no_whatif"), warns)
    wr = stats.week_range(now_i, cfg["weeks"])
    usage, umeta = _load("usage", lambda: load.usage_rows(root, wr[0]["start"] if wr else None), ([], {}), warns)
    ctx = Ctx(root=root, mode=mode, now=now_i, cfg=cfg, trades=trades, cases=cases, skips=skip, usage=usage, usage_meta=umeta)
    v = _fix_verdict(verdict.decide(trades, cases, cfg, now_i), len(trades))
    if v["state"] != "few_data" and v["reason"] != "no_baseline" and _mixed_rules(trades):
        warns.append("mixed_rules")
    blocks, bwarns = _blocks(ctx)
    warns.extend(bwarns)
    seen, uniq = set(), []
    for w in warns:
        if w not in seen:
            seen.add(w)
            uniq.append(w)
    out = {"schema": SCHEMA, "ok": True, "mode": mode, "asof": now_i // ASOF_STEP * ASOF_STEP, "min_n": expstats.MIN_N, "alpha": expstats.ALPHA,
           "verdict": v, "order": list(ORDER), "blocks": blocks,
           "meta": {"built_ms": 0, "cached": False, "excluded": dict(excluded), "warnings": uniq,
                    "sources": {"trades": len(trades), "cases": len(cases), "labels_db": os.path.exists(labels_store.db_path(root))}}}
    out = _fit(sanitize(out))
    out["meta"]["built_ms"] = max(0, int(round((clock() - t0) * 1000)))
    return out


def has_computing(payload):
    """Есть ли в ответе блок со статусом computing (его досылает повторное сообщение)."""
    blocks = (payload or {}).get("blocks") if isinstance(payload, dict) else None
    return isinstance(blocks, dict) and any(isinstance(b, dict) and b.get("status") == "computing" for b in blocks.values())


def get(root=None, mode=None, now=None, mono=None):
    """Отчёт с кешем. mode нормализуется; now — время расчёта отчёта (секунды эпохи), mono — монотонное время кеша (тесты). Возвращает копию."""
    mode = normalize_mode(mode)
    cfg, _ = conf.load(root)
    key = (str(root), mode)
    sig = load.source_signature(root)
    value, hit = REPORT.get(key, sig, lambda: build(root, mode, now), cfg["cache"]["ttl_s"], cfg["cache"]["min_interval_s"], now=mono)
    out = copy.deepcopy(value)
    if hit:
        out["meta"]["cached"] = True
    elif has_computing(out):
        REPORT.drop(key)
    return out
