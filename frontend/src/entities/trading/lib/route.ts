import type { Position } from '../model/types'

/**
 * Геометрия «маршрутной полосы»: ось идёт от стопа (0) к последнему тейку (1) независимо от стороны, поэтому у шорта полоса тоже читается слева направо.
 * Возвращает доли оси для входа, цены и целей. Нет стопа или цели: ось строится от стопа до входа плюс такое же расстояние (границы полосы не прыгают).
 */
export interface Route {
  entry: number
  price: number | null
  targets: number[]
  stop: number
  end: number
  /** цена между входом и концом оси (тейком) по знаку прибыли */
  inProfit: boolean | null
}

export function route(p: Position): Route | null {
  if (p.stop == null || p.avg == null) return null
  const sign = p.side === 'long' ? 1 : -1
  const risk = sign * (p.avg - p.stop)
  if (!(risk > 0)) return null
  const targets = p.targets.map((t) => t.price).filter((x) => sign * (x - p.avg!) > 0)
  const far = targets.length ? (sign > 0 ? Math.max(...targets) : Math.min(...targets)) : p.avg + sign * risk
  const span = sign * (far - p.stop)
  const at = (v: number) => Math.max(-0.08, Math.min(1.08, (sign * (v - p.stop!)) / span))
  const entry = at(p.avg)
  return {
    entry,
    price: p.price == null ? null : at(p.price),
    targets: targets.map(at),
    stop: 0,
    end: 1,
    inProfit: p.price == null ? null : sign * (p.price - p.avg) >= 0,
  }
}
