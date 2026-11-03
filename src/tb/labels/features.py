"""Признаки на момент сигнала t0 (R1 label factory, Requirement 3). Чистая функция: данные и время приходят аргументами.

Ни один признак не зависит от свечи с закрытием после t0: все ряды сначала режутся `bars.closed_before(rows, t0, tf)`,
книга `CandleBook(now=t0)` заполняется только такими свечами. Нет данных = None и имя в `missing`, а не нулевое значение.
"""
import json
import os
import time

from tb.logic import bars
from tb.logic.candles import CandleBook
from tb.logic.levels import key_levels
from tb.memory import cfg as memcfg
from tb.memory import features as memfeat

ATR_N = 14
VOL_REL_WINDOW = 300          # свечей 5м для медианы ATR
VOL_REL_MIN = 12              # минимум значений ATR для медианы (меньше - vol_rel None)
MAX_LEVELS = 12
CTX_MAX_AGE_S = 900           # context.json моложе 15 мин
CTX_MAX_LAG_S = 120           # сигнал захвачен не позднее чем через 120 с после t0

FEATURE_KEYS = (
    "atr5_pct", "range_15m_pct", "ret_15m_pct", "ret_1h_pct", "ret_4h_pct", "vol_rel",
    "dist_sup_pct", "dist_res_pct", "dist_sup_atr", "dist_res_atr", "n_levels_3atr",
    "btc_ret_1h_pct", "btc_ret_4h_pct", "btc_atr5_pct",
    "utc_hour", "dow", "utc_session", "coin_class", "fng", "dvol", "risk_off",
)

_MEM_CFG = []


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def _mem_cfg():
    if not _MEM_CFG:
        try:
            _MEM_CFG.append(memcfg.load())
        except (OSError, ValueError):
            return {}
    return _MEM_CFG[0]


def read_ctx_snapshot(root, now_s, captured_ts, t0_s):
    """-> {"fng", "dvol", "risk_off"} | None. None, если сигнал захвачен позже t0 + 120 с, файла нет/битый или
    context.json старше 15 мин (ts_epoch относительно now_s). Отдельные поля, которых нет в файле, равны None."""
    c, t0 = _num(captured_ts), _num(t0_s)
    if c is None or t0 is None or c - t0 > CTX_MAX_LAG_S:
        return None
    try:
        with open(os.path.join(root, "runtime", "context.json"), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    ts, now = _num(d.get("ts_epoch")), _num(now_s)
    if ts is None or now is None or now - ts > CTX_MAX_AGE_S:
        return None
    src = d.get("sources") if isinstance(d.get("sources"), dict) else {}

    def val(name):
        s = src.get(name)
        return _num(s.get("value")) if isinstance(s, dict) else None
    ro = d.get("risk_off")
    return {"fng": val("fear_greed"), "dvol": val("dvol_ETH"), "risk_off": ro if isinstance(ro, bool) else None}


def _atr_series(rows, n=ATR_N):
    """Значения ATR (Уайлдер) на каждой свече, где он определён."""
    if len(rows) < n + 1:
        return []
    trs = [max(c[2] - c[3], abs(c[2] - p[4]), abs(c[3] - p[4])) for p, c in zip(rows, rows[1:])]
    a = sum(trs[:n]) / n
    out = [a]
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
        out.append(a)
    return out


def _median(v):
    s = sorted(v)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def _book(r1, r5, r1h, t0_s):
    b = CandleBook()
    for tf, rows in ((1, r1), (5, r5), (60, r1h)):
        b.seed(tf, [(r[0] * 1000, r[1], r[2], r[3], r[4], r[5], r[4] * r[5]) for r in rows], now=t0_s)
    return b


def _level_prices(lv):
    raw = list(lv.get("swing_5m") or []) + list(lv.get("swing_1h") or [])
    for k in ("day", "prev_day", "week"):
        v = lv.get(k)
        if isinstance(v, dict):
            raw += [v.get("hi"), v.get("lo")]
    return sorted(set(float(x) for x in (_num(v) for v in raw) if x is not None and x > 0))


def compute(rows_1m, rows_5m, rows_1h, btc_5m, t0_s, sym, ev, ctx_snapshot, mem_cfg=None):
    """-> (feat: dict, missing: list). Ряды - Row (open_ts_s, o, h, l, c, v), любые, в том числе с хвостом после t0 (он отбрасывается).
    ctx_snapshot - результат read_ctx_snapshot (уже проверенный на свежесть) или None."""
    t0 = int(t0_s)
    r1 = bars.closed_before(rows_1m or [], t0, 60)
    r5 = bars.closed_before(rows_5m or [], t0, 300)
    r1h = bars.closed_before(rows_1h or [], t0, 3600)
    rb = bars.closed_before(btc_5m or [], t0, 300)
    feat = dict((k, None) for k in FEATURE_KEYS)
    feat["levels"] = []

    last = r1[-1] if r1 and (not r5 or r1[-1][0] + 60 >= r5[-1][0] + 300) else (r5[-1] if r5 else (r1[-1] if r1 else None))
    px = last[4] if last else None
    a5 = bars.atr(r5, ATR_N)
    if px and a5 is not None:
        feat["atr5_pct"] = a5 / px * 100
    feat["range_15m_pct"] = bars.range_pct(r5, 15, 5)
    feat["ret_15m_pct"] = bars.ret_pct(r5, 15, 5)
    feat["ret_1h_pct"] = bars.ret_pct(r5, 60, 5)
    feat["ret_4h_pct"] = bars.ret_pct(r5, 240, 5)
    ser = _atr_series(r5[-VOL_REL_WINDOW:])
    if a5 is not None and len(ser) >= VOL_REL_MIN:
        med = _median(ser)
        feat["vol_rel"] = a5 / med if med > 0 else None

    # уровни: книга только из закрытых свечей, now = t0
    if px:
        lv = key_levels(_book(r1, r5, r1h, t0), now_ms=t0 * 1000)
        prices = _level_prices(lv)
        sup = [p for p in prices if p < px]
        res = [p for p in prices if p > px]
        if sup:
            feat["dist_sup_pct"] = (px - max(sup)) / px * 100
        if res:
            feat["dist_res_pct"] = (min(res) - px) / px * 100
        a5_pct = feat["atr5_pct"]
        if a5_pct and a5_pct > 0:
            for k in ("sup", "res"):
                if feat["dist_%s_pct" % k] is not None:
                    feat["dist_%s_atr" % k] = feat["dist_%s_pct" % k] / a5_pct
            feat["n_levels_3atr"] = sum(1 for p in prices if abs(p - px) / px * 100 <= 3 * a5_pct)
        near = sorted(prices, key=lambda p: abs(p - px))[:MAX_LEVELS]
        feat["levels"] = sorted(near)

    feat["btc_ret_1h_pct"] = bars.ret_pct(rb, 60, 5)
    feat["btc_ret_4h_pct"] = bars.ret_pct(rb, 240, 5)
    ba = bars.atr(rb, ATR_N)
    if ba is not None and rb[-1][4] > 0:
        feat["btc_atr5_pct"] = ba / rb[-1][4] * 100

    g = time.gmtime(t0)
    feat["utc_hour"], feat["dow"], feat["utc_session"] = g.tm_hour, g.tm_wday, memfeat.utc_session(g.tm_hour)
    cfg = mem_cfg if mem_cfg is not None else _mem_cfg()
    # оборот 24 ч: поле событий сканера turn_24h_musd (млн USD, marketdata/hotscan.py) -> USD для classify_coin
    tm = _num(ev.get("turn_24h_musd")) if isinstance(ev, dict) else None
    feat["coin_class"] = memfeat.classify_coin(sym, cfg, tm * 1e6 if tm is not None else None)

    if isinstance(ctx_snapshot, dict):
        for k in ("fng", "dvol", "risk_off"):
            feat[k] = ctx_snapshot.get(k)

    missing = [k for k in FEATURE_KEYS if feat[k] is None]
    return feat, missing
