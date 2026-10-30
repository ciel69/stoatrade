import type { NearPlan, Position } from '../model/types'

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

/** Общая сборка оси: сторона, стоп, вход, цена и цены целей. */
function build(side: 'long' | 'short', stop: number, entry: number, price: number | null, targetPrices: number[]): Route | null {
  const sign = side === 'long' ? 1 : -1
  const risk = sign * (entry - stop)
  if (!(risk > 0)) return null
  const targets = targetPrices.filter((x) => sign * (x - entry) > 0)
  const far = targets.length ? (sign > 0 ? Math.max(...targets) : Math.min(...targets)) : entry + sign * risk
  const span = sign * (far - stop)
  const at = (v: number) => Math.max(-0.08, Math.min(1.08, (sign * (v - stop)) / span))
  return {
    entry: at(entry),
    price: price == null ? null : at(price),
    targets: targets.map(at),
    stop: 0,
    end: 1,
    inProfit: price == null ? null : sign * (price - entry) >= 0,
  }
}

export function route(p: Position): Route | null {
  if (p.stop == null || p.avg == null) return null
  return build(p.side === 'long' ? 'long' : 'short', p.stop, p.avg, p.price, p.targets.map((t) => t.price))
}

/** Полоса плана до входа: вход это уровень условия (если его нет, цена), поэтому между ценой и входом остаётся отрезок «осталось дойти». */
export function planRoute(p: NearPlan): (Route & { gap: [number, number] | null }) | null {
  if (!p.side || p.stop == null) return null
  const entry = p.level ?? p.price
  if (entry == null) return null
  const r = build(p.side, p.stop, entry, p.price, p.targets?.length ? p.targets : p.target != null ? [p.target] : [])
  if (!r) return null
  return { ...r, gap: r.price != null && Math.abs(r.price - r.entry) > 0.004 ? [Math.min(r.price, r.entry), Math.max(r.price, r.entry)] : null }
}
