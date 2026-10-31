import type { BriefCoinStat, BriefHot, BriefLevel, BriefStruct, Brief } from '../model/types'

export const SCOPE_RU: Record<string, string> = { start: 'старт дня', event: 'событие', full: 'полное', close: 'закрытие', plan: 'план', review: 'разбор' }
export const scopeText = (s: string | null | undefined): string => (s ? (SCOPE_RU[s] ?? s) : '?')

/** Время досье: из ISO (UTC) в миллисекунды; не разобралось: null. */
export function briefMs(ts: string | null | undefined): number | null {
  if (typeof ts !== 'string') return null
  const t = Date.parse(ts)
  return Number.isNaN(t) ? null : t
}

const num = (x: unknown): number | null => (typeof x === 'number' && Number.isFinite(x) ? x : null)

/** Ход монеты из двух источников досье: блок market (BTC и ETH) и структура рынка (struct.s) для того, чего в market нет (1ч, 24ч, ATR, funding). */
export interface CoinStat {
  px: number | null
  r5: number | null
  r1h: number | null
  r24h: number | null
  atr5: number | null
  funding: number | null
  spread: number | null
  regime: string | null
}

export function coinStat(c: BriefCoinStat | undefined, s: BriefStruct | undefined): CoinStat {
  const reg = c?.regime && c.regime !== 'unknown' ? c.regime : (s?.reg ?? null)
  return {
    px: num(c?.px) ?? num(s?.px),
    r5: num(c?.ret_5m_pct),
    r1h: num(c?.ret_1h_pct) ?? num(s?.r1h),
    r24h: num(c?.ret_24h_pct) ?? num(s?.r24h),
    atr5: num(c?.range_5m_pct) ?? num(s?.a5_pct),
    funding: num(c?.funding_8h_pct) ?? num(s?.fu),
    spread: num(c?.spread_bps) ?? num(s?.sp),
    regime: reg && reg !== 'unknown' ? reg : null,
  }
}

/** Горячие монеты: массив или объект с top. */
export function hotRows(b: Brief): BriefHot[] {
  return Array.isArray(b.hot) ? b.hot : (b.hot?.top ?? [])
}

export interface LevelBand {
  key: 'week' | 'day' | 'prev_day'
  label: string
  lo: number
  hi: number
}

/** Диапазоны уровней монеты и общая ось цены: границы с запасом 4 %, доля позиции любого значения на оси 0..1. */
export function levelAxis(l: BriefLevel): { bands: LevelBand[]; swings: number[]; px: number | null; at: (v: number) => number } | null {
  const defs: Array<[LevelBand['key'], string]> = [['week', 'неделя'], ['day', 'день'], ['prev_day', 'вчера']]
  const bands: LevelBand[] = []
  for (const [key, label] of defs) {
    const r = l[key]
    if (r && num(r.lo) != null && num(r.hi) != null) bands.push({ key, label, lo: r.lo, hi: r.hi })
  }
  const swings = (l.swing_5m ?? []).filter((v) => num(v) != null)
  const px = num(l.px)
  const vals = [...bands.flatMap((b) => [b.lo, b.hi]), ...swings, ...(px != null ? [px] : [])]
  if (vals.length < 2) return null
  const lo = Math.min(...vals)
  const hi = Math.max(...vals)
  const pad = (hi - lo) * 0.04 || 1
  const a = lo - pad
  const span = hi + pad - a
  return { bands, swings, px, at: (v) => (v - a) / span }
}
