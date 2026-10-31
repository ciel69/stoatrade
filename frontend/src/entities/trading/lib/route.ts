import type { NearPlan, Position } from '../model/types'

/**
 * Геометрия «маршрутной полосы»: ось идёт от стопа (0) к последнему тейку (1) независимо от стороны, поэтому у шорта полоса тоже читается слева направо.
 * Возвращает доли оси для стопа, входа, цены и целей. Нет цели: ось строится от стопа до входа плюс такое же расстояние (границы полосы не прыгают).
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

/**
 * Общая сборка оси: сторона, стоп, вход, цена и цены целей. Стоп на стороне убытка: ось от стопа (0). Стоп подтянут за вход (автозащита, зафиксирована прибыль, locked=true):
 * ось начинается от входа (0), а стоп стоит правее входа (route.stop > route.entry); для плана до входа такой стоп считается ошибкой данных.
 */
function build(side: 'long' | 'short', stop: number, entry: number, price: number | null, targetPrices: number[], locked = false): Route | null {
  const sign = side === 'long' ? 1 : -1
  const risk = sign * (entry - stop)
  if (!(risk > 0) && !locked) return null
  const targets = targetPrices.filter((x) => sign * (x - entry) > 0)
  const reach = Math.abs(entry - stop) || Math.abs(entry) * 0.01
  const far = targets.length ? (sign > 0 ? Math.max(...targets) : Math.min(...targets)) : entry + sign * reach
  const origin = risk > 0 ? stop : entry
  const span = sign * (far - origin)
  const at = (v: number) => Math.max(-0.08, Math.min(1.08, (sign * (v - origin)) / span)) + 0        // + 0: минус ноль (шорт) становится нулём
  return {
    entry: at(entry),
    price: price == null ? null : at(price),
    targets: targets.map(at),
    stop: at(stop),
    end: 1,
    inProfit: price == null ? null : sign * (price - entry) >= 0,
  }
}

/** Полоса по голым числам (для досье и мест, где нет объекта позиции): сторона, стоп, вход, цена, цены целей. */
export function routeFor(side: 'long' | 'short', stop: number | null, entry: number | null, price: number | null, targets: number[]): Route | null {
  return stop == null || entry == null ? null : build(side, stop, entry, price, targets, true)
}

export function route(p: Position): Route | null {
  if (p.stop == null || p.avg == null) return null
  return build(p.side === 'long' ? 'long' : 'short', p.stop, p.avg, p.price, p.targets.map((t) => t.price), true)
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
