"""Сводный рынок (R1.7–R1.9, задача 3): чистая математика снимка «все биржи как один». Только stdlib и tb.logic: без os, сети, часов, open.

Время, файлы и конфиг сюда приходят аргументами. Параметры по умолчанию (`DEFAULTS`) равны разделам `sync`, `book`, `cov` файла
config/cmkt.json (тест сверяет с загрузчиком tb.cmkt.conf); вызывающий передаёт свой конфиг теми же разделами.

Единицы (Requirement 5.7): монеты = количество × contract_size × coin_mult; цена за монету = цена инструмента / coin_mult;
USD = монеты × цена за монету (1 USDT = 1 USD; у Coinbase цены в USD).

Состояние рынка для сводки (`features`): словарь, который сводка собирает из `runtime/cmkt/ex/<market>.json` (поля — то, что
публикует сборщик; отсутствующее поле = нет данных):

    {"kind": "perp" | "spot", "quote": "USDT" | "USD", "code": None | "<код исключения>", "w": оборот 24 ч USD (вес покрытия),
     "has_book": bool (умолчание True; у Coinbase False), "mid": цена за монету (по t_rx),
     "bands": {"025"|"05"|"1"|"2": [bid_usd, ask_usd] | None},            # из bands(): None — глубина выдачи не покрывает полосу
     "walls": [{"side", "px", "usd", "age_s"}],                             # устойчивые стены рынка (WallTracker.stable)
     "pulled": {"bid": [n, usd], "ask": [n, usd]},                          # снятые до касания за 15 мин (WallTracker.ends)
     "tape": {"1m"|"5m"|"15m": [buy_usd, sell_usd, cov]},                   # cov — доля окна с непрерывной лентой (0..1)
     "oi": {"now": монеты, "1m"|"5m"|"15m": монеты | None}, "mark": цена за монету, "oi_usd": USD (для исключённого — последнее ≤ 1 ч),
     "fund": ставка периода (доля), "fund_h": интервал, ч,
     "liq": {"has": bool, "partial": bool, "5m": [long_usd, short_usd], "1h": [long_usd, short_usd], "5m_coins": [long, short]}}

`code is None` — рынок включён в снимок (`market_code`); исключённые рынки участвуют только в знаменателях покрытия.
Покрытие `cov` — доля веса включённых рынков среди всех рынков монеты своего вида (книга — только рынки со стаканом; OI, funding и
ликвидации — OI в USD рынков perp). Признак — None, если `n_ex < min_n_ex` или `cov < min_cov` (Requirement 11.2), и пока монета в
фокусе меньше окна признака (Requirement 1.4).
"""
import hashlib
import json
import math
import statistics

# ---------------------------------------------------------------- параметры (равны config/cmkt.json: sync, book, cov)
DEFAULTS = {
    "sync": {"tick_s": 1, "lag_max_ms": 1000, "lag_max_ms_venue": {}, "silent_s": 15, "file_stale_s": 1, "accept_s": 3, "ping_s": 10,
             "clock_warn_ms": 500, "time_sync_s": 600, "lead_rtt_max_ms": 100, "lead_offset_max_age_s": 900, "snap_every_s": 2},
    "book": {"bucket_bps": 25, "max_bps": 200, "bands_pct": [0.25, 0.5, 1, 2], "imb_band_first_wave": 0.25, "wall_min_market_usd": 100000,
             "wall_mult": 6, "wall_min_usd": 400000, "wall_group_bps": 5, "wall_persist_s": 30, "touch_bps": 5, "pull_drop_share": 0.7,
             "pull_window_s": 5, "resync_min_s": 10},
    "cov": {"min_cov": 0.6, "min_n_ex": 2, "liq_min_cov": 0.5, "mismatch_pct": 2.0},
}

# ---------------------------------------------------------------- рынки, коды, окна
MARKETS = ("binance_perp", "binance_spot", "bybit_perp", "bybit_spot", "okx_perp", "okx_spot", "bitget_perp", "bitget_spot",
           "gate_perp", "gate_spot", "coinbase_spot")
MARKET_INDEX = {m: i for i, m in enumerate(MARKETS)}           # код cm_lead_ex (0–10)
KINDS = ("perp", "spot")
CODES = ("OFF", "DOWN", "SILENT", "LAG", "RESYNC", "STALE", "NOT_LISTED", "MISMATCH")
BANDS = (("025", 0.25), ("05", 0.5), ("1", 1.0), ("2", 2.0))
WINDOWS = (("1m", 60), ("5m", 300), ("15m", 900))
TAPE_FULL = 0.99                     # лента окна считается непрерывной при cov ≥ 0.99 (Requirement 8.3)
WALL_BAND_PCT = 1.0                  # медиана уровней стороны для порога стены — в пределах ±1 %
LEAD_WINDOW_S = 300
LEAD_LAGS = (1, 2, 3)
LEAD_MIN_SCORE = 0.1
LEAD_MIN_NONZERO = 60
PULL_WINDOW_S = 900                  # снятые стены за 15 мин
FLAG_BITS = ("liq_partial", "low_cov", "clock_back", "warmup", "mm_unchecked")
EV_CM_MAX_BYTES = 1000

# ---------------------------------------------------------------- признаки (design.md, «CM_FEATURES»): имя -> масштаб в ряду
FEATURE_SCALES = (
    ("cm_n_perp", 1), ("cm_n_spot", 1), ("cm_cov", 1000),
    ("cm_imb_025_pct", 10), ("cm_imb_05_pct", 10), ("cm_imb_1_pct", 10), ("cm_imb_2_pct", 10),
    ("cm_imb_05_perp_pct", 10), ("cm_imb_05_spot_pct", 10),
    ("cm_depth_025_musd", 100), ("cm_depth_05_musd", 100), ("cm_depth_1_musd", 100),
    ("cm_wall_bid_musd", 100), ("cm_wall_ask_musd", 100), ("cm_wall_bid_dist_pct", 100), ("cm_wall_ask_dist_pct", 100),
    ("cm_wall_pull_15m_pct", 10), ("cm_pulled_15m_n", 1),
    ("cm_cvd_1m_pct", 10), ("cm_cvd_5m_pct", 10), ("cm_cvd_15m_pct", 10), ("cm_cvd_5m_perp_pct", 10), ("cm_cvd_5m_spot_pct", 10),
    ("cm_cvd_ps_5m_pct", 10), ("cm_cvd_5m_musd", 100),
    ("cm_oi_musd", 10), ("cm_oi_chg_1m_pct", 1000), ("cm_oi_chg_5m_pct", 1000), ("cm_oi_chg_15m_pct", 1000), ("cm_oi_liq_5m_pct", 1000),
    ("cm_liq_5m_long_musd", 1000), ("cm_liq_5m_short_musd", 1000), ("cm_liq_1h_net_musd", 1000), ("cm_liq_cov", 1000),
    ("cm_fund_w_pct", 100000), ("cm_fund_spread_pct", 100000), ("cm_prem_ps_bps", 10), ("cm_prem_cb_bps", 10),
    ("cm_lead_ex", 1), ("cm_lead_lag_s", 1),
)
CM_FEATURES = tuple(n for n, _ in FEATURE_SCALES)
SCALES = dict(FEATURE_SCALES)
INT_FEATURES = frozenset(n for n, s in FEATURE_SCALES if s == 1)
# Первая волна меню (Requirement 15.1; design.md, «Волны меню»; уточнено по задаче 2, 2026-11-04): полоса ±0.25 % вместо ±0.5 %
# (Requirement 6.3); cm_cvd_ps_5m_pct (оценка заполнения 0.76) и cm_prem_ps_bps (0.78) ниже порога 0.8 и заменены досрочно по
# прежнему FILL_ORDER — cm_oi_chg_5m_pct (0.88) и cm_cvd_5m_perp_pct (0.90); cm_imb_1_pct пропущен (±1 % у BTC/ETH не покрыт ни одним
# рынком, у частичных стаканов light — 1–2 рынка). Заменённые признаки собираются и пишутся в метки; они стоят первыми в FILL_ORDER
# и возвращаются на проверке через 7 суток (задача 34), если заполнение по замеру ≥ 0.8.
# первая волна: по одному признаку от семейства (стакан, OI с ликвидациями, поток, стены) — дубли семейств (cm_oi_chg_5m_pct при
# cm_oi_liq_5m_pct, cm_cvd_5m_pct при cm_cvd_5m_perp_pct) почти повторяют друг друга и только ужесточают порог Бонферрони (2026-11-04)
FIRST_WAVE = ("cm_imb_025_pct", "cm_oi_liq_5m_pct", "cm_cvd_5m_perp_pct", "cm_wall_pull_15m_pct")
FILL_ORDER = ("cm_cvd_ps_5m_pct", "cm_prem_ps_bps", "cm_oi_chg_5m_pct", "cm_cvd_5m_pct", "cm_imb_1_pct")   # кандидаты следующих волн (Requirement 15.7–15.8)
MENU_K_BASE = 17                                                                # learnloop/menu.py: K ≤ 17 до волны


def first_wave(imb_band=0.25):
    """Первая волна; полоса ±0.25 % (Requirement 6.3, book.imb_band_first_wave = 0.25) — по умолчанию; при 0.5 — cm_imb_05_pct
    вместо cm_imb_025_pct."""
    if imb_band == 0.5:
        return tuple("cm_imb_05_pct" if f == "cm_imb_025_pct" else f for f in FIRST_WAVE)
    return FIRST_WAVE


def wave_k_max(wave=FIRST_WAVE, base=MENU_K_BASE):
    """Верхняя граница K меню с волной: base + 2 × признаков (>= q80 и <= q20); первая волна (4 признака) -> 25 (Requirement 15.1)."""
    return base + 2 * len(wave)


def _cm_ver():
    blob = json.dumps([list(x) for x in FEATURE_SCALES], separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:8]


CM_VER = _cm_ver()                    # первые 8 hex sha256 списка имён и масштабов


# ---------------------------------------------------------------- общие мелочи
def num(x):
    """Конечное число или None (строки биржи разбираются)."""
    if isinstance(x, bool) or x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _cfg(cfg, sec):
    d = dict(DEFAULTS[sec])
    if isinstance(cfg, dict) and isinstance(cfg.get(sec), dict):
        d.update(cfg[sec])
    return d


def _w(s):
    return max(0.0, num(s.get("w")) or 0.0)


def share(part, total, n_part=None, n_total=None):
    """Доля веса; при нулевом общем весе — доля по числу рынков (вес неизвестен), иначе None."""
    if total > 0:
        return min(1.0, max(0.0, part / total))
    if n_total:
        return (n_part or 0) / float(n_total)
    return None


def ok_cov(n, cov, c):
    """Requirement 11.2: n_ex ≥ min_n_ex и cov ≥ min_cov."""
    return n >= c["min_n_ex"] and cov is not None and cov >= c["min_cov"]


# ---------------------------------------------------------------- приведение единиц (Requirement 5.7)
def coin_px(px, coin_mult=1.0):
    """Цена за монету: цена инструмента / coin_mult (1000PEPE: цена за 1000 монет / 1000)."""
    p, m = num(px), num(coin_mult)
    return None if p is None or not m or m <= 0 else p / m


def coins(qty, contract_size=1.0, coin_mult=1.0):
    """Монеты: количество × contract_size × coin_mult."""
    q, cs, m = num(qty), num(contract_size), num(coin_mult)
    return None if q is None or not cs or not m else q * cs * m


def norm_levels(levels, contract_size=1.0, coin_mult=1.0):
    """Уровни биржи [(px, size)] (строки или числа) -> [(цена за монету, монеты)], только положительные."""
    out = []
    for r in levels or ():
        if not isinstance(r, (list, tuple)) or len(r) < 2:
            continue
        p, a = coin_px(r[0], coin_mult), coins(r[1], contract_size, coin_mult)
        if p and a and p > 0 and a > 0:
            out.append((p, a))
    return out


# ---------------------------------------------------------------- стакан рынка (сборщик)
def book_mid(bids, asks):
    """Середина по лучшим уровням [(px, coins)]; пустая сторона или перекрёст -> None."""
    if not bids or not asks:
        return None
    bb, ba = max(p for p, _ in bids), min(p for p, _ in asks)
    return None if bb >= ba else (bb + ba) / 2.0


def depth_pct(bids, asks, mid, bound_bid=None, bound_ask=None):
    """Глубина выдачи, %: меньшая из сторон от середины до самого дальнего уровня (или до границы REST-снимка)."""
    if not mid or not bids or not asks:
        return None
    lo = bound_bid if bound_bid is not None else min(p for p, _ in bids)
    hi = bound_ask if bound_ask is not None else max(p for p, _ in asks)
    return max(0.0, min((mid - lo) / mid, (hi - mid) / mid) * 100.0)


def bands(bids, asks, mid, depth, bands_pct=None):
    """Полосы ±pct от своей середины: {label: [bid_usd, ask_usd] | None}; полоса считается, только если глубина выдачи ≥ 0.98 × полосы."""
    want = tuple(float(x) for x in (bands_pct or [b for _, b in BANDS]))
    out = {}
    for label, pct in BANDS:
        if pct not in want:
            continue
        if not mid or depth is None or depth < pct * 0.98:
            out[label] = None
            continue
        b = sum(p * a for p, a in bids if (mid - p) / mid * 100.0 <= pct + 1e-12)
        a_ = sum(p * a for p, a in asks if (p - mid) / mid * 100.0 <= pct + 1e-12)
        out[label] = [b, a_]
    return out


def buckets(bids, asks, mid, bucket_bps=25, max_bps=200):
    """Корзины расстояния от середины шириной bucket_bps до ±max_bps (только атрибуция и показ): {"bid": [usd]*n, "ask": [usd]*n}."""
    n = int(max_bps // bucket_bps)
    out = {"bid": [0.0] * n, "ask": [0.0] * n}
    if not mid or n <= 0:
        return out
    for side, rows in (("bid", bids), ("ask", asks)):
        for p, a in rows:
            d = abs(p - mid) / mid * 1e4
            if d > max_bps + 1e-9:
                continue
            out[side][min(n - 1, int(d // bucket_bps))] += p * a
    return out


def imbalance(bid, ask):
    """(bid − ask) / (bid + ask) × 100 в [−100, 100]; нулевая сумма -> None."""
    b, a = num(bid), num(ask)
    if b is None or a is None or b < 0 or a < 0 or b + a <= 0:
        return None
    return max(-100.0, min(100.0, (b - a) / (b + a) * 100.0))


def wall_candidates(bids, asks, mid, min_market_usd=100000, mult=6):
    """Уровни-стены рынка по абсолютной цене: USD ≥ max(min_market_usd, mult × медиана уровней стороны в ±1 %) -> {(side, px): usd}."""
    out = {}
    if not mid:
        return out
    for side, rows in (("bid", bids), ("ask", asks)):
        near = [p * a for p, a in rows if abs(p - mid) / mid * 100.0 <= WALL_BAND_PCT]
        if not near:
            continue
        thr = max(float(min_market_usd), float(mult) * statistics.median(near))
        for p, a in rows:
            if p * a >= thr:
                out[(side, p)] = p * a
    return out


class WallTracker(object):
    """Стены одного рынка одной монеты по абсолютной цене уровня: устойчивость, касание сделками, конец (touched / pulled / faded).

    Время приходит аргументом. Конец устойчивой стены классифицируется ровно одним кодом (свойство 11):
      touched — цена сделки этого рынка подходила к уровню ближе touch_bps, пока стена стояла;
      pulled  — не касалась, и за ≤ pull_window_s размер упал на ≥ pull_drop_share от максимума стены;
      faded   — иначе.
    """

    def __init__(self, persist_s=30, touch_bps=5, drop_share=0.7, window_s=5, keep_s=PULL_WINDOW_S):
        self.persist_s, self.touch_bps, self.drop_share = float(persist_s), float(touch_bps), float(drop_share)
        self.window_s, self.keep_s = float(window_s), float(keep_s)
        self.live = {}          # (side, px) -> {"first", "max", "last", "touched", "hist": [(t, usd)]}
        self.log = []           # [(t_end, side, px, max_usd, code)]

    def trade(self, t, px):
        """Сделка рынка: отмечает касание стен ближе touch_bps."""
        p = num(px)
        if not p:
            return
        for (side, lv), w in self.live.items():
            if abs(p - lv) / lv * 1e4 <= self.touch_bps:
                w["touched"] = True

    def update(self, t, cands, lookup=None):
        """cands: {(side, px): usd} — уровни, проходящие порог стены сейчас; lookup(side, px) -> текущий USD уровня (для истории
        падения). Стена, которой нет в cands, заканчивается. -> список концов [(t, side, px, max_usd, code)] устойчивых стен."""
        ends = []
        for key in list(self.live):
            w = self.live[key]
            if key in cands:
                continue
            usd = 0.0
            if lookup is not None:
                usd = num(lookup(key[0], key[1])) or 0.0
            w["hist"].append((t, usd))
            code = self._close(t, w, usd)
            del self.live[key]
            if code is not None:
                ends.append((t, key[0], key[1], w["max"], code))
        for key, usd in cands.items():
            w = self.live.get(key)
            if w is None:
                w = self.live[key] = {"first": t, "seen": t, "max": usd, "last": usd, "touched": False, "hist": []}
            w["max"] = max(w["max"], usd)
            w["last"], w["seen"] = usd, t
            w["hist"].append((t, usd))
            while w["hist"] and t - w["hist"][0][0] > self.window_s:
                w["hist"].pop(0)
        self.log.extend(ends)
        while self.log and t - self.log[0][0] > self.keep_s:
            self.log.pop(0)
        return ends

    def _close(self, t, w, usd_end):
        if w["seen"] - w["first"] < self.persist_s:
            return None                                             # не была устойчивой: конца нет
        if w["touched"]:
            return "touched"
        recent = [u for ts, u in w["hist"] if t - ts <= self.window_s]
        hi = max(recent) if recent else w["last"]
        return "pulled" if hi - usd_end >= self.drop_share * w["max"] else "faded"

    def stable(self, t, top=3):
        """Устойчивые стены (возраст ≥ persist_s), до top на сторону по USD: [{"side","px","usd","age_s"}]."""
        out = []
        for side in ("bid", "ask"):
            rows = [(w["last"], px, w) for (s, px), w in self.live.items() if s == side and t - w["first"] >= self.persist_s]
            for usd, px, w in sorted(rows, key=lambda r: (-r[0], r[1]))[:top]:
                out.append({"side": side, "px": px, "usd": usd, "age_s": int(t - w["first"])})
        return out

    def ends(self, t, window_s=PULL_WINDOW_S, code="pulled"):
        """Концы с кодом за окно по сторонам: {"bid": [n, usd], "ask": [n, usd]}."""
        out = {"bid": [0, 0.0], "ask": [0, 0.0]}
        for te, side, _, mx, c in self.log:
            if c == code and t - te <= window_s:
                out[side][0] += 1
                out[side][1] += mx
        return out


# ---------------------------------------------------------------- включение рынка в снимок (Requirement 4.4)
def lag_max_ms(sync, venue=None):
    s = _cfg({"sync": sync} if sync else None, "sync")
    ov = (s.get("lag_max_ms_venue") or {}).get(venue) if venue else None
    return min(2000, int(ov)) if isinstance(ov, (int, float)) and not isinstance(ov, bool) else int(s["lag_max_ms"])


def market_code(T, t_pub, alive_age_s, lag_ms, book_status="ok", sync=None, venue=None, light=False, light_every_s=5,
                enabled=True, listed=True, connected=True):
    """Код исключения рынка на такте T или None (рынок включён). Порядок проверок: OFF, NOT_LISTED, DOWN, SILENT, STALE, RESYNC, LAG.
    STALE — T − T_pub > file_stale_s (для монеты light — от последней отметки, кратной light_every_s) или публикация позже T
    (без заглядывания вперёд); SILENT — нет ответа на пинг и сообщений дольше silent_s; LAG — задержка последнего сообщения больше
    lag_max_ms (переопределение биржи до 2000 мс); RESYNC — стакан восстанавливается (book_part допускается)."""
    s = _cfg({"sync": sync} if sync else None, "sync")
    if not enabled:
        return "OFF"
    if not listed:
        return "NOT_LISTED"
    if not connected:
        return "DOWN"
    if alive_age_s is None or alive_age_s > s["silent_s"]:
        return "SILENT"
    ref = T - (T % light_every_s) if light else T
    if t_pub is None or t_pub > T or ref - t_pub > s["file_stale_s"]:
        return "STALE"
    if book_status == "resync":
        return "RESYNC"
    if lag_ms is None or lag_ms > lag_max_ms(s, venue):
        return "LAG"
    return None


def skew_ms(included):
    """included: [(T − T_pub, lag_ms)] включённых рынков -> max((T − T_pub) × 1000 + lag_ms) или None."""
    v = [int(round(d * 1000 + lg)) for d, lg in included]
    return max(v) if v else None


def mismatch(prices, ref_px=None, pct=2.0):
    """Сопоставление по цене за монету (Requirement 5.8). prices: {mkt: цена за монету} (Coinbase — уже в USDT).
    ≥ 3 рынков: исключается рынок с |px / медиана − 1| > pct %, медиана — по всем рынкам монеты (у 3 рынков «медиана остальных» двух
    была бы их средним, и один рынок с ценой ×1000 исключил бы все три); меньше 3 — сравнение с ref_px (Bybit из prices.json не старше
    150 с), без неё рынки помечаются mm_unchecked. -> (множество исключённых рынков, mm_unchecked)."""
    px = {m: p for m, p in prices.items() if num(p) and p > 0}
    lim = pct / 100.0
    if len(px) >= 3:
        med = statistics.median(px.values())
        return {m for m, p in px.items() if abs(p / med - 1.0) > lim}, False
    ref = num(ref_px)
    if not ref or ref <= 0:
        return set(), bool(px)
    return {m for m, p in px.items() if abs(p / ref - 1.0) > lim}, False


def consensus_mid(mids):
    """Сводная середина: медиана при ≥ 3, среднее при 2, значение при 1, None при 0 (Requirement 10.2)."""
    v = [x for x in (num(m) for m in mids) if x is not None and x > 0]
    if not v:
        return None
    if len(v) == 1:
        return v[0]
    if len(v) == 2:
        return (v[0] + v[1]) / 2.0
    return statistics.median(v)


# ---------------------------------------------------------------- сводный стакан (Requirement 6)
def _inc(mk):
    return {m: s for m, s in mk.items() if isinstance(s, dict) and s.get("code") is None}


def _of(mk, kind):
    return {m: s for m, s in mk.items() if isinstance(s, dict) and (kind == "all" or s.get("kind") == kind)}


def agg_bands(mk):
    """Сводные полосы {kind: {label: {"bid","ask","imb","n_ex","cov","by": {mkt: [bid, ask]}} | None}} по рынкам со стаканом;
    в сумму входят только включённые рынки, покрывающие полосу; cov — доля их веса среди всех рынков вида со стаканом."""
    out = {}
    for kind in ("perp", "spot", "all"):
        elig = {m: s for m, s in _of(mk, kind).items() if s.get("has_book", True)}
        tot = sum(_w(s) for s in elig.values())
        row = {}
        for label, _ in BANDS:
            by = {}
            for m, s in elig.items():
                b = (s.get("bands") or {}).get(label) if s.get("code") is None else None
                if isinstance(b, (list, tuple)) and len(b) == 2 and num(b[0]) is not None and num(b[1]) is not None:
                    by[m] = [float(b[0]), float(b[1])]
            if not by:
                row[label] = None
                continue
            bid, ask = sum(v[0] for v in by.values()), sum(v[1] for v in by.values())
            row[label] = {"bid": bid, "ask": ask, "imb": imbalance(bid, ask), "n_ex": len(by),
                          "cov": share(sum(_w(elig[m]) for m in by), tot, len(by), len(elig)), "by": by}
        out[kind] = row
    return out


def agg_buckets(mk):
    """Корзины 25 б.п. включённых рынков по видам с атрибуцией: {kind: {side: [{"usd", "by": {mkt: usd}}]}} (только у кого есть корзины)."""
    out = {}
    for kind in ("perp", "spot", "all"):
        row = {}
        for m, s in _of(_inc(mk), kind).items():
            bk = s.get("buckets")
            if not isinstance(bk, dict):
                continue
            for side in ("bid", "ask"):
                for i, usd in enumerate(bk.get(side) or ()):
                    lst = row.setdefault(side, [])
                    while len(lst) <= i:
                        lst.append({"usd": 0.0, "by": {}})
                    u = num(usd) or 0.0
                    if u > 0:
                        lst[i]["usd"] += u
                        lst[i]["by"][m] = lst[i]["by"].get(m, 0.0) + u
        out[kind] = row
    return out


def agg_walls(walls_by_mkt, mid, group_bps=5, min_usd=400000, top=3):
    """Сводные стены: стены рынков (цена за монету) в пределах group_bps от самой крупной непривязанной стены группы, сумма ≥ min_usd;
    до top на сторону по USD: [{"side","px","usd","dist_pct","n_ex","mkts","age_s"}]; px — цена самой крупной стены группы, age_s — наибольший
    возраст в группе."""
    out = []
    for side in ("bid", "ask"):
        rows = []
        for m, ws in (walls_by_mkt or {}).items():
            for w in ws or ():
                if isinstance(w, dict) and w.get("side") == side and num(w.get("px")) and num(w.get("usd")):
                    rows.append((float(w["usd"]), float(w["px"]), m, int(w.get("age_s") or 0)))
        rows.sort(key=lambda r: (-r[0], r[1], r[2]))
        used, groups = set(), []
        for i, (usd, px, m, age) in enumerate(rows):
            if i in used:
                continue
            members = [j for j, r in enumerate(rows) if j not in used and abs(r[1] / px - 1.0) * 1e4 <= group_bps + 1e-9]
            used.update(members)
            tot = sum(rows[j][0] for j in members)
            if tot >= min_usd:
                mk = sorted(set(rows[j][2] for j in members))
                groups.append({"side": side, "px": px, "usd": tot,
                               "dist_pct": abs(px - mid) / mid * 100.0 if mid else None,
                               "n_ex": len(mk), "mkts": mk, "age_s": max(rows[j][3] for j in members)})
        groups.sort(key=lambda g: (-g["usd"], g["px"]))
        out.extend(groups[:top])
    return out


# ---------------------------------------------------------------- CVD (Requirement 8)
def agg_cvd(mk, label):
    """CVD окна по видам: {kind: {"buy","sell","net","pct","n_ex","cov","by": {mkt: net}}}; в сумму входят только включённые рынки с
    непрерывной лентой окна (cov ≥ TAPE_FULL); покрытие — доля их оборота среди всех рынков вида (разрыв снижает покрытие, а не даёт ноль)."""
    out = {}
    for kind in ("perp", "spot", "all"):
        elig = _of(mk, kind)
        tot = sum(_w(s) for s in elig.values())
        buy = sell = 0.0
        by = {}
        for m, s in elig.items():
            t = (s.get("tape") or {}).get(label) if s.get("code") is None else None
            if not isinstance(t, (list, tuple)) or len(t) < 3:
                continue
            b, sl, cv = num(t[0]), num(t[1]), num(t[2])
            if b is None or sl is None or cv is None or cv < TAPE_FULL:
                continue
            buy += b
            sell += sl
            by[m] = b - sl
        out[kind] = {"buy": buy, "sell": sell, "net": buy - sell,
                     "pct": (buy - sell) / (buy + sell) * 100.0 if by and buy + sell > 0 else None,
                     "n_ex": len(by), "cov": share(sum(_w(elig[m]) for m in by), tot, len(by), len(elig)) if elig else None, "by": by}
    return out


# ---------------------------------------------------------------- OI, ликвидации, funding (Requirements 7, 9, 10.1)
def oi_usd(s):
    """OI рынка в USD: монеты × марк-цена за монету; для исключённого — последнее известное значение (поле oi_usd)."""
    o = (s.get("oi") or {}).get("now")
    v = num(o) * num(s.get("mark")) if num(o) is not None and num(s.get("mark")) else None
    return v if v is not None else num(s.get("oi_usd"))


def _perp(mk):
    return {m: s for m, s in mk.items() if isinstance(s, dict) and s.get("kind") == "perp"}


def _oi_base(mk):
    allp = _perp(mk)
    w_all = {m: oi_usd(s) or 0.0 for m, s in allp.items()}
    inc = {m: s for m, s in allp.items() if s.get("code") is None and num((s.get("oi") or {}).get("now")) is not None
           and num(s.get("mark"))}
    return allp, w_all, inc


def oi_change(mk, label, c=None):
    """Изменение суммарного OI за окно, %, в монетах по набору рынков, у которых есть значение и сейчас, и на T − w (Requirement 7.3).
    -> (pct | None, n_ex, cov)."""
    c = c or DEFAULTS["cov"]
    allp, w_all, inc = _oi_base(mk)
    tot = sum(w_all.values())
    sel = {m: s for m, s in inc.items() if num((s.get("oi") or {}).get(label)) is not None}
    cov = share(sum(w_all[m] for m in sel), tot, len(sel), len(allp))
    if not ok_cov(len(sel), cov, c):
        return None, len(sel), cov
    now = sum(float(s["oi"]["now"]) for s in sel.values())
    then = sum(float(s["oi"][label]) for s in sel.values())
    return ((now / then - 1.0) * 100.0 if then > 0 else None), len(sel), cov


def oi_liq(mk, c=None):
    """cm_oi_liq_5m_pct (Requirement 7.4): (ΔOI_5м + ликвидации_5м) / OI × 100 в монетах по одному набору рынков perp с потоком
    ликвидаций и обоими значениями OI; None, если OI набора < liq_min_cov от OI включённых рынков или n_ex < min_n_ex."""
    c = c or DEFAULTS["cov"]
    allp, w_all, inc = _oi_base(mk)
    inc_usd = sum(w_all[m] for m in inc)
    sel = {m: s for m, s in inc.items() if (s.get("liq") or {}).get("has") and num(s["oi"].get("5m")) is not None
           and isinstance((s.get("liq") or {}).get("5m_coins"), (list, tuple))}
    sel_usd = sum(w_all[m] for m in sel)
    if len(sel) < c["min_n_ex"] or inc_usd <= 0 or sel_usd < c["liq_min_cov"] * inc_usd:
        return None
    now = sum(float(s["oi"]["now"]) for s in sel.values())
    d = sum(float(s["oi"]["now"]) - float(s["oi"]["5m"]) for s in sel.values())
    lq = sum(sum(num(x) or 0.0 for x in s["liq"]["5m_coins"][:2]) for s in sel.values())
    return (d + lq) / now * 100.0 if now > 0 else None


def agg_liq(mk, c=None):
    """Ликвидации включённых рынков perp с потоком: {"5m": [long, short], "1h": [long, short], "liq_cov", "n_ex", "partial", "ok"}.
    liq_cov — доля OI в USD рынков с потоком среди включённых perp; ok=False -> признаки ликвидаций None, а не 0 (Requirement 9.2)."""
    c = c or DEFAULTS["cov"]
    allp, w_all, inc = _oi_base(mk)
    inc_all = {m: s for m, s in allp.items() if s.get("code") is None}
    den = sum(w_all[m] for m in inc_all)
    sel = {m: s for m, s in inc_all.items() if (s.get("liq") or {}).get("has")}
    out = {"5m": [0.0, 0.0], "1h": [0.0, 0.0], "n_ex": len(sel), "partial": any((s.get("liq") or {}).get("partial") for s in sel.values()),
           "liq_cov": share(sum(w_all[m] for m in sel), den) if den > 0 else None, "by": {}}
    for m, s in sel.items():
        lq = s["liq"]
        for k in ("5m", "1h"):
            v = lq.get(k) if isinstance(lq.get(k), (list, tuple)) else (0.0, 0.0)
            out[k][0] += num(v[0]) or 0.0
            out[k][1] += num(v[1]) or 0.0
        v5 = lq.get("5m") if isinstance(lq.get("5m"), (list, tuple)) else (None, None)
        out["by"][m] = [num(v5[0]), num(v5[1])]
    out["ok"] = out["liq_cov"] is not None and out["liq_cov"] >= c["liq_min_cov"] and len(sel) >= c["min_n_ex"]
    return out


def fund_8h(f, interval_h):
    """Ставка к 8 ч: f × 8 / interval_h."""
    f, h = num(f), num(interval_h)
    return None if f is None or not h or h <= 0 else f * 8.0 / h


def fund_weights(mk):
    """Веса funding: OI в USD включённых рынков perp со ставкой; неотрицательны, в сумме 1. -> {mkt: вес}."""
    allp, w_all, inc = _oi_base(mk)
    sel = {m: w_all[m] for m, s in inc.items() if fund_8h(s.get("fund"), s.get("fund_h")) is not None and w_all[m] > 0}
    tot = sum(sel.values())
    return {m: v / tot for m, v in sel.items()} if tot > 0 else {}


def agg_funding(mk, c=None):
    """-> (cm_fund_w_pct, cm_fund_spread_pct, n_ex, cov): среднее к 8 ч, взвешенное по OI в USD, и разброс (наибольшая − наименьшая), %."""
    c = c or DEFAULTS["cov"]
    allp, w_all, inc = _oi_base(mk)
    wts = fund_weights(mk)
    cov = share(sum(w_all[m] for m in wts), sum(w_all.values()), len(wts), len(allp))
    if not ok_cov(len(wts), cov, c):
        return None, None, len(wts), cov
    f8 = {m: fund_8h(mk[m].get("fund"), mk[m].get("fund_h")) for m in wts}
    return (sum(f8[m] * w for m, w in wts.items()) * 100.0, (max(f8.values()) - min(f8.values())) * 100.0, len(wts), cov)


# ---------------------------------------------------------------- середины и премии (Requirements 10.2, 10.3)
def kind_mid(mk, kind):
    """Сводная середина вида по включённым рынкам к USDT."""
    return consensus_mid([s.get("mid") for s in _of(_inc(mk), kind).values() if s.get("quote", "USDT") == "USDT"])


def prem_ps(mk):
    """(середина perp / середина spot − 1) × 10 000; None без включённого рынка любого вида."""
    p, s = kind_mid(mk, "perp"), kind_mid(mk, "spot")
    return None if not p or not s else (p / s - 1.0) * 1e4


def prem_cb(mk, usdt_usd):
    """(середина Coinbase BASE-USD / (середина spot к USDT × курс USDT/USD) − 1) × 10 000; курс — Coinbase USDT-USD не старше 10 с
    (свежесть проверяет вызывающий: устаревший курс -> None)."""
    cb = consensus_mid([s.get("mid") for s in _inc(mk).values() if s.get("quote") == "USD"])
    sp, r = kind_mid(mk, "spot"), num(usdt_usd)
    return None if not cb or not sp or not r or r <= 0 else (cb / (sp * r) - 1.0) * 1e4


# ---------------------------------------------------------------- лидерство (Requirement 10.4)
def _corr(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return None if sxx <= 0 or syy <= 0 else sxy / math.sqrt(sxx * syy)


def _rets(series, T, window):
    out = {}
    for t in range(int(T) - window + 1, int(T) + 1):
        a, b = num(series.get(t)), num(series.get(t - 1))
        if a and b and a > 0 and b > 0:
            out[t] = a / b - 1.0
    return out


def offset_ok(rtt_ms, age_s, sync=None):
    """Качество оценки offset для лидерства: RTT лучшего запроса ≤ lead_rtt_max_ms и оценка не старше lead_offset_max_age_s."""
    s = _cfg({"sync": sync} if sync else None, "sync")
    r, a = num(rtt_ms), num(age_s)
    return r is not None and a is not None and r <= s["lead_rtt_max_ms"] and 0 <= a <= s["lead_offset_max_age_s"]


def lead_market(series, T, quality, sync=None, window=LEAD_WINDOW_S):
    """Рынок-лидер: series {mkt: {секунда (t_ex − offset): середина}}, quality {mkt: (rtt_ms, age_s)} оценки offset.
    Для рынка m: доходности за window с; сводная доходность остальных — консенсус их доходностей на секунде; оценка — наибольшая корреляция
    r_m[t] с остальными на t + k (k = 1..3) минус корреляция на обратном лаге k; лидер — наибольшая оценка ≥ 0.1 при ≥ 60 ненулевых
    доходностях m. None, если у любого сравниваемого рынка offset без качества (свойство 19) или рынков меньше 2. -> {"mkt","lag_s","score"}."""
    mk = sorted(m for m in series if series.get(m))
    if len(mk) < 2:
        return None
    if any(not offset_ok(*(quality.get(m) or (None, None)), sync=sync) for m in mk):
        return None
    rets = {m: _rets(series[m], T, window) for m in mk}
    best = None
    for m in mk:
        rm = rets[m]
        if sum(1 for v in rm.values() if v != 0) < LEAD_MIN_NONZERO:
            continue
        oth = {}
        for t in set().union(*[rets[o].keys() for o in mk if o != m]):
            v = consensus_mid_signed([rets[o][t] for o in mk if o != m and t in rets[o]])
            if v is not None:
                oth[t] = v
        cand = None
        for k in LEAD_LAGS:
            fx = [(rm[t], oth[t + k]) for t in rm if t + k in oth]
            bx = [(oth[t], rm[t + k]) for t in oth if t + k in rm]
            cf = _corr([a for a, _ in fx], [b for _, b in fx]) if len(fx) >= 30 else None
            cb = _corr([a for a, _ in bx], [b for _, b in bx]) if len(bx) >= 30 else None
            if cf is None:
                continue
            if cand is None or cf > cand[0]:
                cand = (cf, k, cf - (cb or 0.0))
        if cand and cand[2] >= LEAD_MIN_SCORE and (best is None or cand[2] > best["score"]):
            best = {"mkt": m, "lag_s": cand[1], "score": cand[2]}
    return best


def consensus_mid_signed(vals):
    """Консенсус чисел любого знака (медиана при ≥ 3, среднее при 2, значение при 1) — для доходностей."""
    v = [x for x in (num(a) for a in vals) if x is not None]
    if not v:
        return None
    if len(v) <= 2:
        return sum(v) / len(v)
    return statistics.median(v)


# ---------------------------------------------------------------- признаки снимка (CM_FEATURES)
def _r(x):
    return None if x is None else float(x)


def features(mk, focus_s, cfg=None, usdt_usd=None, lead=None, mm_unchecked=False):
    """Признаки монеты на такте по состояниям рынков mk (включённые и исключённые).
    -> {"f": {имя: значение | None} по CM_FEATURES, "flags": [имена FLAG_BITS], "bands", "buckets", "walls", "cvd", "liq", "by_ex",
        "n_ex", "cov"}. focus_s — сколько монета в фокусе (окно w даёт None, пока focus_s < w); lead — результат lead_market."""
    c, b = _cfg(cfg, "cov"), _cfg(cfg, "book")
    fs = num(focus_s) or 0.0
    inc = _inc(mk)
    f = dict.fromkeys(CM_FEATURES)
    f["cm_n_perp"] = sum(1 for s in inc.values() if s.get("kind") == "perp")
    f["cm_n_spot"] = sum(1 for s in inc.values() if s.get("kind") == "spot")
    cov_all = share(sum(_w(s) for s in inc.values()), sum(_w(s) for s in mk.values() if isinstance(s, dict)), len(inc), len(mk))
    f["cm_cov"] = cov_all

    # стакан
    bd = agg_bands(mk)

    def band_ok(kind, label):
        r = bd[kind].get(label)
        return r if r is not None and ok_cov(r["n_ex"], r["cov"], c) else None
    for label, name in (("025", "cm_imb_025_pct"), ("05", "cm_imb_05_pct"), ("1", "cm_imb_1_pct"), ("2", "cm_imb_2_pct")):
        r = band_ok("all", label)
        f[name] = r["imb"] if r else None
    for kind in ("perp", "spot"):
        r = band_ok(kind, "05")
        f["cm_imb_05_%s_pct" % kind] = r["imb"] if r else None
    for label, name in (("025", "cm_depth_025_musd"), ("05", "cm_depth_05_musd"), ("1", "cm_depth_1_musd")):
        r = band_ok("all", label)
        f[name] = (r["bid"] + r["ask"]) / 1e6 if r else None

    # стены
    book_all = {m: s for m, s in mk.items() if isinstance(s, dict) and s.get("has_book", True)}
    book_inc = {m: s for m, s in book_all.items() if s.get("code") is None and num(s.get("mid"))}
    book_cov = share(sum(_w(s) for s in book_inc.values()), sum(_w(s) for s in book_all.values()), len(book_inc), len(book_all))
    mid_all = consensus_mid([s.get("mid") for s in book_inc.values() if s.get("quote", "USDT") == "USDT"])
    walls = agg_walls({m: s.get("walls") for m, s in book_inc.items()}, mid_all, b["wall_group_bps"], b["wall_min_usd"])
    if ok_cov(len(book_inc), book_cov, c):
        for side in ("bid", "ask"):
            top = next((w for w in walls if w["side"] == side), None)
            f["cm_wall_%s_musd" % side] = top["usd"] / 1e6 if top else 0.0
            f["cm_wall_%s_dist_pct" % side] = top["dist_pct"] if top else None
        if fs >= PULL_WINDOW_S:
            pb = pa = 0.0
            n = 0
            for s in book_inc.values():
                p = s.get("pulled") or {}
                for side in ("bid", "ask"):
                    v = p.get(side) if isinstance(p.get(side), (list, tuple)) else (0, 0.0)
                    n += int(num(v[0]) or 0)
                    if side == "bid":
                        pb += num(v[1]) or 0.0
                    else:
                        pa += num(v[1]) or 0.0
            f["cm_pulled_15m_n"] = n
            den = f["cm_depth_025_musd"] if b.get("imb_band_first_wave") == 0.25 else f["cm_depth_05_musd"]
            f["cm_wall_pull_15m_pct"] = (pb - pa) / (den * 1e6) * 100.0 if den else None

    # CVD
    cvd = {}
    for label, w in WINDOWS:
        cvd[label] = agg_cvd(mk, label)
    for label, w in WINDOWS:
        a = cvd[label]["all"]
        if fs >= w and ok_cov(a["n_ex"], a["cov"], c):
            f["cm_cvd_%s_pct" % label] = a["pct"]
            if label == "5m":
                f["cm_cvd_5m_musd"] = a["net"] / 1e6
    if fs >= 300:
        for kind in ("perp", "spot"):
            k = cvd["5m"][kind]
            if ok_cov(k["n_ex"], k["cov"], c):
                f["cm_cvd_5m_%s_pct" % kind] = k["pct"]
        if f["cm_cvd_5m_perp_pct"] is not None and f["cm_cvd_5m_spot_pct"] is not None:
            f["cm_cvd_ps_5m_pct"] = f["cm_cvd_5m_perp_pct"] - f["cm_cvd_5m_spot_pct"]

    # OI
    allp, w_all, oi_inc = _oi_base(mk)
    oi_cov = share(sum(w_all[m] for m in oi_inc), sum(w_all.values()), len(oi_inc), len(allp))
    if ok_cov(len(oi_inc), oi_cov, c):
        f["cm_oi_musd"] = sum(w_all[m] for m in oi_inc) / 1e6
    for label, w in WINDOWS:
        if fs >= w:
            f["cm_oi_chg_%s_pct" % label] = oi_change(mk, label, c)[0]
    if fs >= 300:
        f["cm_oi_liq_5m_pct"] = oi_liq(mk, c)

    # ликвидации
    lq = agg_liq(mk, c)
    f["cm_liq_cov"] = lq["liq_cov"]
    if lq["ok"]:
        if fs >= 300:
            f["cm_liq_5m_long_musd"] = lq["5m"][0] / 1e6
            f["cm_liq_5m_short_musd"] = lq["5m"][1] / 1e6
        if fs >= 3600:
            f["cm_liq_1h_net_musd"] = (lq["1h"][0] - lq["1h"][1]) / 1e6

    # funding, премии
    fw, fsp, _, _ = agg_funding(mk, c)
    f["cm_fund_w_pct"], f["cm_fund_spread_pct"] = fw, fsp
    f["cm_prem_ps_bps"] = prem_ps(mk)
    f["cm_prem_cb_bps"] = prem_cb(mk, usdt_usd)

    # лидерство
    if lead and fs >= LEAD_WINDOW_S and lead.get("mkt") in MARKET_INDEX:
        f["cm_lead_ex"] = MARKET_INDEX[lead["mkt"]]
        f["cm_lead_lag_s"] = int(lead["lag_s"])

    f = {k: (None if v is None or (isinstance(v, float) and not math.isfinite(v)) else v) for k, v in f.items()}
    if not inc:
        f = dict.fromkeys(CM_FEATURES)                              # все рынки исключены: все признаки None (Requirement 11.1)
    flags = []
    if lq["partial"]:
        flags.append("liq_partial")
    if cov_all is None or cov_all < c["min_cov"]:
        flags.append("low_cov")
    if fs < PULL_WINDOW_S:
        flags.append("warmup")
    if mm_unchecked:
        flags.append("mm_unchecked")
    return {"f": f, "flags": flags, "bands": bd, "buckets": agg_buckets(mk), "walls": walls, "cvd": cvd, "liq": lq,
            "by_ex": by_ex_rows(mk), "n_ex": len(inc), "cov": cov_all, "mid": mid_all}


def by_ex_rows(mk):
    """Разрез по включённым рынкам для ряда (Requirement 13.2): [{"mkt","cvd5","doi5","liq5_long","liq5_short","pulled15"}]."""
    out = []
    for m in sorted(_inc(mk)):
        s = mk[m]
        t = (s.get("tape") or {}).get("5m")
        cvd5 = (num(t[0]) - num(t[1])) if isinstance(t, (list, tuple)) and len(t) >= 3 and num(t[0]) is not None \
            and num(t[1]) is not None and (num(t[2]) or 0) >= TAPE_FULL else None
        oi = s.get("oi") or {}
        doi5 = (num(oi.get("now")) - num(oi.get("5m"))) if num(oi.get("now")) is not None and num(oi.get("5m")) is not None else None
        lq = s.get("liq") or {}
        l5 = lq.get("5m") if lq.get("has") and isinstance(lq.get("5m"), (list, tuple)) else (None, None)
        p = s.get("pulled")
        pulled = None
        if isinstance(p, dict):
            pulled = sum(int(num((p.get(sd) or (0, 0))[0]) or 0) for sd in ("bid", "ask"))
        out.append({"mkt": m, "cvd5": cvd5, "doi5": doi5, "liq5_long": num(l5[0]), "liq5_short": num(l5[1]), "pulled15": pulled})
    return out


def flag_bits(flags):
    return sum(1 << i for i, n in enumerate(FLAG_BITS) if n in (flags or ()))


def flags_of(bits):
    return [n for i, n in enumerate(FLAG_BITS) if int(bits or 0) >> i & 1]


# ---------------------------------------------------------------- кодирование строки ряда (zigzag varint)
def _zz(n):
    return 2 * n if n >= 0 else -2 * n - 1


def _unzz(z):
    return z // 2 if z % 2 == 0 else -(z + 1) // 2


def _put(buf, u):
    while True:
        b = u & 0x7F
        u >>= 7
        if u:
            buf.append(b | 0x80)
        else:
            buf.append(b)
            return


def _get(data, i):
    u = shift = 0
    while True:
        if i >= len(data):
            raise ValueError("varint: обрыв")
        b = data[i]
        i += 1
        u |= (b & 0x7F) << shift
        if not b & 0x80:
            return u, i
        shift += 7


def _put_opt(buf, v):
    """None -> 0, иначе zigzag(v) + 1."""
    _put(buf, 0 if v is None else _zz(int(v)) + 1)


def _get_opt(data, i):
    u, i = _get(data, i)
    return (None if u == 0 else _unzz(u - 1)), i


def quantize(name, v):
    """Значение признака -> масштабированное целое (или None)."""
    x = num(v)
    return None if x is None else int(round(x * SCALES[name]))


def dequantize(name, q):
    if q is None:
        return None
    sc = SCALES[name]
    return int(q) if sc == 1 else round(q / float(sc), 6)


def quantized(values):
    """Признаки после округления до масштаба ряда (то, что увидят метки и мозг)."""
    return {n: dequantize(n, quantize(n, (values or {}).get(n))) for n in CM_FEATURES}


def encode_f(values):
    """40 признаков по CM_FEATURES -> BLOB: zigzag varint, 0 — None, иначе значение + 1."""
    buf = bytearray()
    for n in CM_FEATURES:
        _put_opt(buf, quantize(n, (values or {}).get(n)))
    return bytes(buf)


def decode_f(blob, names=CM_FEATURES, scales=None):
    """BLOB -> {имя: значение | None}; names — список признаков cm_ver файла (по умолчанию текущий), scales — его масштабы
    {имя: масштаб} (файл со старым cm_ver; по умолчанию текущие SCALES)."""
    data, i, out = bytes(blob or b""), 0, {}
    sc_map = SCALES if scales is None else scales
    for n in names:
        q, i = _get_opt(data, i)
        sc = sc_map.get(n)
        if q is None or sc is None:
            out[n] = q
        else:
            out[n] = int(q) if sc == 1 else round(q / float(sc), 6)
    if i != len(data):
        raise ValueError("f: лишние байты")
    return out


BY_EX_FIELDS = ("cvd5", "doi5", "liq5_long", "liq5_short", "pulled15")


def _doi_k(rows):
    mx = max([abs(r["doi5"]) for r in rows if num(r.get("doi5")) is not None] or [0.0])
    if mx <= 0:
        return 0
    return max(-6, min(6, 3 - int(math.floor(math.log10(mx)))))      # около 4 значащих цифр у наибольшего ΔOI


def encode_by_ex(rows, mkt_ids):
    """Разрез по рынкам -> BLOB: zigzag k (ΔOI в монетах × 10^k), затем на рынок: mkt_id, cvd5 (тыс. USD), doi5, liq5 long/short (тыс. USD),
    pulled15 (шт.); None — 0, иначе значение + 1. mkt_ids: {mkt: id} из таблицы mkts."""
    rows = [r for r in rows or () if r.get("mkt") in mkt_ids]
    k = _doi_k(rows)
    buf = bytearray()
    _put(buf, _zz(k))
    for r in rows:
        _put(buf, int(mkt_ids[r["mkt"]]))
        for fld in BY_EX_FIELDS:
            v = num(r.get(fld))
            if v is None:
                q = None
            elif fld == "doi5":
                q = int(round(v * 10 ** k))
            elif fld == "pulled15":
                q = int(round(v))
            else:
                q = int(round(v / 1000.0))
            _put_opt(buf, q)
    return bytes(buf)


def decode_by_ex(blob, mkt_names):
    """BLOB -> [{"mkt", "cvd5" (USD), "doi5" (монеты), "liq5_long", "liq5_short" (USD), "pulled15"}]; mkt_names: {id: mkt}."""
    data = bytes(blob or b"")
    if not data:
        return []
    z, i = _get(data, 0)
    k = _unzz(z)
    out = []
    while i < len(data):
        mid, i = _get(data, i)
        r = {"mkt": mkt_names.get(mid, mid)}
        for fld in BY_EX_FIELDS:
            q, i = _get_opt(data, i)
            if q is None:
                r[fld] = None
            elif fld == "doi5":
                r[fld] = q / float(10 ** k) if k >= 0 else float(q * 10 ** (-k))
            elif fld == "pulled15":
                r[fld] = q
            else:
                r[fld] = q * 1000.0
        out.append(r)
    return out


# ---------------------------------------------------------------- строка признаков сигнала и ev.cm (Requirements 14, 15.4)
def row_ts(t0, step):
    """Последняя закрытая строка яруса строго раньше t0: floor(t0 / step) × step − step."""
    return int(math.floor(t0 / step)) * int(step) - int(step)


def row_deadline(rts, accept_s=3):
    """Срок ожидания строки: row_ts + accept_s + 1."""
    return rts + accept_s + 1


def row_on_time(rts, wr_ts, accept_s=3):
    """Строка годна, если записана не позже row_ts + accept_s."""
    return wr_ts is not None and wr_ts <= rts + accept_s


def ev_cm(values, tier, focus_s, rts, cm_ver=CM_VER, max_bytes=EV_CM_MAX_BYTES, wave=FIRST_WAVE):
    """Объект signals.ev.cm: {"v", "tier", "focus_s", "row_ts", <признаки без None>} не длиннее max_bytes (компактный JSON);
    при превышении отбрасываются признаки с конца CM_FEATURES, кроме первой волны."""
    out = {"v": cm_ver, "tier": tier, "focus_s": int(focus_s or 0), "row_ts": int(rts)}
    for n in CM_FEATURES:
        v = (values or {}).get(n)
        if v is not None and (not isinstance(v, float) or math.isfinite(v)):
            out[n] = v
    drop = [n for n in reversed(CM_FEATURES) if n not in wave]
    while len(json.dumps(out, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) > max_bytes and drop:
        out.pop(drop.pop(0), None)
    return out


# ---------------------------------------------------------------- совместимый xflow.json (Requirement 17.6; копии без импорта)
COMPAT_BANDS = (("05", 0.5), ("1", 1.0), ("2", 2.0))
COMPAT_CORE = ("BTC", "ETH", "SOL")


def compat_symbols(positions, requested, extra, n):
    """Копия xstream.symbol_set: позиции и планы первыми, затем запросы структуры, затем BTC/ETH/SOL, затем extra
    (у cmkt — топ по обороту из фокуса вместо горячих монет intel); не больше n. Списки позиций и запросов читает вызывающий."""
    out = []
    for s in list(positions or []) + list(requested or []) + list(COMPAT_CORE) + [x for x in extra or [] if isinstance(x, str)]:
        if s not in out:
            out.append(s)
    return out[:int(n)]


def compat_book_stats(ob, cs, buckets_out=None, bucket_pct=0.1):
    """Копия xflow.book_stats: стакан ccxt -> {"mid","depth_pct","bands": {"05": [bid_usd, ask_usd] | None}}; корзины для стен кладутся в
    buckets_out[(side, k)] -> [usd, max_usd, px, set]."""
    bids = [(num(r[0]), num(r[1])) for r in (ob or {}).get("bids") or [] if isinstance(r, (list, tuple)) and len(r) >= 2]
    asks = [(num(r[0]), num(r[1])) for r in (ob or {}).get("asks") or [] if isinstance(r, (list, tuple)) and len(r) >= 2]
    bids = [(p, a) for p, a in bids if p and a and p > 0 and a > 0]
    asks = [(p, a) for p, a in asks if p and a and p > 0 and a > 0]
    if not bids or not asks:
        return None
    bb, ba = max(p for p, _ in bids), min(p for p, _ in asks)
    if bb >= ba:
        return None
    mid = (bb + ba) / 2.0
    depth = min((mid - min(p for p, _ in bids)) / mid, (max(p for p, _ in asks) - mid) / mid) * 100.0
    bands_ = {}
    for name, pct in COMPAT_BANDS:
        if depth < pct * 0.98:
            bands_[name] = None
            continue
        bands_[name] = [round(sum(p * a * cs for p, a in bids if (mid - p) / mid * 100 <= pct), 0),
                        round(sum(p * a * cs for p, a in asks if (p - mid) / mid * 100 <= pct), 0)]
    if buckets_out is not None:
        for side, rows in (("bid", bids), ("ask", asks)):
            for p, a in rows:
                dist = abs(p - mid) / mid * 100.0
                if dist > COMPAT_BANDS[-1][1]:
                    continue
                usd = p * a * cs
                b = buckets_out.setdefault((side, int(dist // bucket_pct)), [0.0, 0.0, p, set()])
                b[0] += usd
                if usd > b[1]:
                    b[1], b[2] = usd, p
                b[3].add(id(buckets_out))
    return {"mid": mid, "depth_pct": round(depth, 3), "bands": bands_}


def compat_walls(buckets_, mid, mult, min_usd, bucket_pct):
    """Копия xflow.walls: корзины всех бирж -> до 3 стен на сторону: {"side","px","usd","x","dist_pct","n_ex"}."""
    out = []
    for side in ("bid", "ask"):
        lv = {k: v for (s, k), v in buckets_.items() if s == side}
        if len(lv) < 8:
            continue
        med = statistics.median(v[0] for v in lv.values())
        cand = [(v[0], k, v) for k, v in lv.items() if med > 0 and v[0] >= max(min_usd, mult * med)]
        for usd, k, v in sorted(cand, key=lambda c: c[0], reverse=True)[:3]:
            out.append({"side": side, "px": v[2], "usd": round(usd, 0), "x": round(usd / med, 1),
                        "dist_pct": round(abs(v[2] - mid) / mid * 100.0, 2), "n_ex": len(v[3])})
    return out


def compat_merge(books):
    """Как XStream.document: books {биржа: {"mid", "buckets"}} -> (середина: верхняя медиана середин, сводные корзины с множеством бирж)."""
    mids = sorted(b["mid"] for b in books.values())
    mid = mids[len(mids) // 2] if mids else None
    agg = {}
    for e, b in books.items():
        for k, v in b["buckets"].items():
            a = agg.setdefault(k, [0.0, 0.0, v[2], set()])
            a[0] += v[0]
            if v[1] > a[1]:
                a[1], a[2] = v[1], v[2]
            a[3].add(e)
    return mid, agg
