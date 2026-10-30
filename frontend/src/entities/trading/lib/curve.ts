import type { Curve } from '../model/types'

export type CurveRange = 'day' | 'week' | 'all'

export interface CurvePoint {
  ts: number
  cum: number
  pnl: number
  sym: string | null
}

export interface Series {
  /** накопленный на левом краю периода */
  start: number
  /** сделки периода по времени закрытия */
  points: CurvePoint[]
  t0: number
  t1: number
  /** накопленный сейчас */
  last: number
  /** прирост за период */
  delta: number
}

const SPAN: Record<Exclude<CurveRange, 'all'>, number> = { day: 86400, week: 7 * 86400 }

/** Кривая за период: левый край это накопленный на момент начала периода (для «всё» база кривой), правый край сейчас. null, если у режима нет сделок вообще. */
export function buildSeries(curve: Curve | undefined, range: CurveRange, nowS: number): Series | null {
  if (!curve || !curve.points.length) return null
  const all: CurvePoint[] = curve.points.map(([ts, cum, pnl, sym]) => ({ ts, cum, pnl, sym }))
  const lastAll = all[all.length - 1]
  const t1 = Math.max(nowS, lastAll.ts)
  if (range === 'all') {
    const span = Math.max(lastAll.ts - all[0].ts, 3600)
    return { start: curve.base, points: all, t0: all[0].ts - span * 0.03, t1, last: lastAll.cum, delta: lastAll.cum - curve.base }
  }
  const t0 = t1 - SPAN[range]
  const inside = all.filter((p) => p.ts >= t0)
  const before = all.filter((p) => p.ts < t0)
  const start = before.length ? before[before.length - 1].cum : curve.base
  return { start, points: inside, t0, t1, last: lastAll.cum, delta: lastAll.cum - start }
}

/** Круглые отметки оси: шаг из 1, 2, 2.5, 5 × 10^k, не больше около count штук, внутри [lo, hi]. */
export function niceTicks(lo: number, hi: number, count = 4): number[] {
  if (!(hi > lo)) return [lo]
  const raw = (hi - lo) / count
  const pow = Math.pow(10, Math.floor(Math.log10(raw)))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw * 0.7) ?? raw
  const out: number[] = []
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.round(v / step) * step + 0)      // + 0: убирает -0
  return out
}

/** Номер ближайшей по времени точки к моменту t (-1, если точек нет). */
export function nearest(points: CurvePoint[], t: number): number {
  let best = -1
  let d = Infinity
  points.forEach((p, i) => {
    const x = Math.abs(p.ts - t)
    if (x < d) { d = x; best = i }
  })
  return best
}
