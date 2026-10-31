"""Импульсы и «катание на свече» на 5м свечах (исследование 2026-10-31, не торговый код; итоги в docs/RESEARCH.md).

Сетапы: impulse_continuation, capitulation_reversal, sweep_reject (механические формы сетапов мозга) и momentum_chase как в мозге
(мягкий порог и экстремальный: оборот 5м >= 10x медианы И свеча >= 4 ATR). Данные: `./tb bybit read klines --top 60 --interval 5 --days 60 --no-stocks --out <папка>`
плюс BTC, ETH, SOL отдельно (`--symbols BTCUSDT,ETHUSDT,SOLUSDT`).

Без заглядывания вперёд: сигнал по закрытой свече i, вход не раньше свечи i+1. Консервативно: стоп и цель в одной свече -> стоп;
лимитная заявка и стоп в свече исполнения -> стоп. Издержки круга COST_PCT вычитаются в R (cost / risk%). Одна сделка на монету и сетап одновременно.
Запуск: py -3 src/tb/research/research_extreme.py <папка с k5_*.csv>
"""
import csv
import glob
import math
import os
import statistics
import sys

COST_PCT = float(sys.argv[2]) if len(sys.argv) > 2 else 0.28
TARGET_R = 2.0
TIMEOUT = 48                 # свечей 5м = 4 часа
MAJORS = {"BTC", "ETH", "SOL", "XRP", "BNB", "DOGE"}


def load(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    t = [r["ts_iso"] for r in rows]
    o, h, l, c = ([float(r[k]) for r in rows] for k in "ohlc")
    v = [float(r.get("turnover") or 0.0) for r in rows]
    return t, o, h, l, c, v


def atr(h, l, c, n=14):
    out = [None] * len(c)
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    s = 0.0
    for i, x in enumerate(tr):
        s += x
        if i >= n:
            s -= tr[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def med_prior(v, i, n):
    w = v[max(0, i - n):i]
    return statistics.median(w) if len(w) >= n // 2 else None


def simulate(side, entry_i, entry_px, stop, h, l, c, t):
    """-> (r_gross, exit_i, why). Вход в свече entry_i по entry_px (если в ней же задет стоп: стоп)."""
    risk = (entry_px - stop) if side == "long" else (stop - entry_px)
    if risk <= 0:
        return None
    tgt = entry_px + TARGET_R * risk if side == "long" else entry_px - TARGET_R * risk
    for j in range(entry_i, min(len(c), entry_i + TIMEOUT)):
        hit_stop = l[j] <= stop if side == "long" else h[j] >= stop
        hit_tgt = h[j] >= tgt if side == "long" else l[j] <= tgt
        if hit_stop:
            return -1.0, j, "stop"
        if hit_tgt and j > entry_i:
            return TARGET_R, j, "target"
    j = min(len(c), entry_i + TIMEOUT) - 1
    r = ((c[j] - entry_px) / risk) if side == "long" else ((entry_px - c[j]) / risk)
    return r, j, "timeout"


def net(r, entry_px, stop):
    risk_pct = abs(entry_px - stop) / entry_px * 100.0
    return r, risk_pct          # валовый R; издержки считаются в отчёте (разные варианты)


def impulse(sym, t, o, h, l, c, v, a):
    out, busy = [], -1
    for i in range(60, len(c) - 2):
        if i <= busy or a[i - 1] is None:
            continue
        m = med_prior(v, i, 48)
        if not m or v[i] < 4 * m or (h[i] - l[i]) < 3 * a[i - 1]:
            continue
        side = "long" if c[i] > o[i] else "short"
        if side == "long" and c[i] <= max(h[i - 48:i]):
            continue
        if side == "short" and c[i] >= min(l[i - 48:i]):
            continue
        lvl = c[i] - 0.5 * (c[i] - o[i])
        stop = l[i] if side == "long" else h[i]
        for k in range(i + 1, min(len(c), i + 7)):
            if (side == "long" and l[k] <= lvl) or (side == "short" and h[k] >= lvl):
                res = simulate(side, k, lvl, stop, h, l, c, t)
                if res:
                    r, ex, why = res
                    rn, rp = net(r, lvl, stop)
                    out.append((sym, t[i], side, rn, rp, why))
                    busy = ex
                break
    return out


def capitulation(sym, t, o, h, l, c, v, a):
    out, busy = [], -1
    for i in range(70, len(c) - 2):
        if i <= busy or a[i - 13] is None:
            continue
        hi, lo = max(h[i - 12:i + 1]), min(l[i - 12:i + 1])
        m = med_prior(v, i - 12, 48)
        if not m or max(v[i - 12:i + 1]) < 5 * m:
            continue
        a0 = a[i - 13]
        drop, pump = hi - lo, hi - lo
        # лонг после обвала: минимум окна в последних 3 свечах, закрытие выше максимума предыдущей свечи
        if (hi - lo) >= 8 * a0 and min(l[i - 2:i + 1]) == lo and h.index(hi, i - 12, i + 1) < l.index(lo, i - 12, i + 1) and c[i] > h[i - 1] and c[i] > o[i]:
            side, stop = "long", lo - 0.1 * a0
        elif (hi - lo) >= 8 * a0 and max(h[i - 2:i + 1]) == hi and l.index(lo, i - 12, i + 1) < h.index(hi, i - 12, i + 1) and c[i] < l[i - 1] and c[i] < o[i]:
            side, stop = "short", hi + 0.1 * a0
        else:
            continue
        res = simulate(side, i + 1, o[i + 1], stop, h, l, c, t)
        if res:
            r, ex, why = res
            rn, rp = net(r, o[i + 1], stop)
            out.append((sym, t[i], side, rn, rp, why))
            busy = ex
    return out


def sweep(sym, t, o, h, l, c, v, a):
    out, busy = [], -1
    for i in range(300, len(c) - 2):
        if i <= busy or a[i - 1] is None:
            continue
        H, L = max(h[i - 288:i - 3]), min(l[i - 288:i - 3])
        rng = h[i] - l[i]
        if rng <= 0:
            continue
        if h[i] > H and c[i] < H and (h[i] - max(o[i], c[i])) >= 0.5 * rng:
            side, stop = "short", h[i] + 0.1 * a[i - 1]
        elif l[i] < L and c[i] > L and (min(o[i], c[i]) - l[i]) >= 0.5 * rng:
            side, stop = "long", l[i] - 0.1 * a[i - 1]
        else:
            continue
        res = simulate(side, i + 1, o[i + 1], stop, h, l, c, t)
        if res:
            r, ex, why = res
            rn, rp = net(r, o[i + 1], stop)
            out.append((sym, t[i], side, rn, rp, why))
            busy = ex
    return out


def chase_gen(surge_min, range_atr, tp_r=2.5):
    """momentum_chase как в мозге: импульс (оборот >= surge_min x медианы ИЛИ диапазон >= range_atr ATR), вход по рынку на открытии i+1 по направлению свечи,
    стоп за свечой (диапазон + 0.25 ATR от входа), цель tp_r R, таймаут TIMEOUT."""
    def run(sym, t, o, h, l, c, v, a):
        global TARGET_R
        out, busy = [], -1
        keep = TARGET_R
        TARGET_R = tp_r
        try:
            for i in range(60, len(c) - 2):
                if i <= busy or a[i - 1] is None or c[i] == o[i]:
                    continue
                m = med_prior(v, i, 48)
                rng = h[i] - l[i]
                strong = (m and v[i] >= surge_min * m) and rng >= range_atr * a[i - 1] if surge_min >= 10 else ((m and v[i] >= surge_min * m) or rng >= range_atr * a[i - 1])
                if not strong:
                    continue
                side = "long" if c[i] > o[i] else "short"
                e = o[i + 1]
                dist = rng + 0.25 * a[i - 1]
                stop = e - dist if side == "long" else e + dist
                res = simulate(side, i + 1, e, stop, h, l, c, t)
                if res:
                    r, ex, why = res
                    rn, rp = net(r, e, stop)
                    out.append((sym, t[i], side, rn, rp, why))
                    busy = ex
        finally:
            TARGET_R = keep
        return out
    return run


def stats(trades, cost, min_stop=0.0):
    tr = [x for x in trades if x[4] >= min_stop]
    if not tr:
        return "n=0"
    rs = [x[3] - cost / x[4] for x in tr]
    wins = [r for r in rs if r > 0]
    gl = -sum(r for r in rs if r <= 0)
    se = statistics.pstdev(rs) / math.sqrt(len(rs)) if len(rs) > 1 else float("nan")
    tr_s = sorted(range(len(tr)), key=lambda k: tr[k][1])
    h1 = [rs[k] for k in tr_s[:len(tr_s) // 2]]
    h2 = [rs[k] for k in tr_s[len(tr_s) // 2:]]
    return "n=%4d win=%4.1f%% avgR=%+.3f (±%.3f) PF=%.2f  пол.1 %+.3f пол.2 %+.3f" % (len(rs), 100.0 * len(wins) / len(rs), statistics.mean(rs), se,
                                                                                  sum(wins) / gl if gl else float("inf"), statistics.mean(h1), statistics.mean(h2))


def report(name, trades):
    print("== %s  (медиана стопа %.2f%%, stop/tgt/time %d/%d/%d)" % (name, statistics.median(x[4] for x in trades) if trades else 0, sum(1 for x in trades if x[5] == "stop"),
                                                                   sum(1 for x in trades if x[5] == "target"), sum(1 for x in trades if x[5] == "timeout")))
    print("   валовый (без издержек):       ", stats(trades, 0.0))
    print("   рынок 0.28%, все стопы:       ", stats(trades, 0.28))
    print("   рынок 0.28%, стоп >= 0.8%:    ", stats(trades, 0.28, 0.8))
    print("   лимит 0.12%, стоп >= 0.8%:    ", stats(trades, 0.12, 0.8))
    for side in ("long", "short"):
        print("   %-5s валовый:                " % side, stats([x for x in trades if x[2] == side], 0.0))
    print("   мажоры валовый:               ", stats([x for x in trades if x[0] in MAJORS], 0.0))


def main():
    files = sorted(glob.glob(os.path.join(sys.argv[1], "k5_*.csv")))
    res = {"impulse_continuation": [], "capitulation_reversal": [], "sweep_reject": [], "chase_как_в_мозге": [], "chase_экстрим": []}
    ch1, ch2 = chase_gen(3.0, 2.0), chase_gen(10.0, 4.0)
    span = None
    for f in files:
        sym = os.path.basename(f)[3:-8]
        t, o, h, l, c, v = load(f)
        if len(c) < 2000:
            continue
        span = (t[0], t[-1])
        a = atr(h, l, c)
        res["impulse_continuation"] += impulse(sym, t, o, h, l, c, v, a)
        res["capitulation_reversal"] += capitulation(sym, t, o, h, l, c, v, a)
        res["sweep_reject"] += sweep(sym, t, o, h, l, c, v, a)
        res["chase_как_в_мозге"] += ch1(sym, t, o, h, l, c, v, a)
        res["chase_экстрим"] += ch2(sym, t, o, h, l, c, v, a)
    print("монет %d, период %s .. %s, издержки круга %.2f%%, цель %gR, таймаут %d мин" % (len(files), span and span[0], span and span[1], COST_PCT, TARGET_R, TIMEOUT * 5))
    for k, v in res.items():
        report(k, v)
    btc = [(k, x) for k in res for x in res[k] if x[0] == "BTC" and "2026-10-29T19" <= x[1] <= "2026-10-30T04"]
    print("BTC в обвал 29-30.10:", [(k[:7], x[1][5:16], x[2], round(x[3], 2), x[5]) for k, x in btc])


if __name__ == "__main__":
    main()
