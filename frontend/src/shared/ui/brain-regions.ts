import { nodeInset, snapInside, type P } from './brain-shape'

/**
 * Геометрия нарисованного мозга (viewBox 64×64): 24 нейрона, левое полушарие 0..11 и зеркальное правое 12..23, и четыре области коры из нейронов обоих полушарий.
 * Желаемые места левых нейронов подгоняются внутрь контура с запасом на размер нейрона и отклонение (snapInside), поэтому ни один не выходит за форму.
 */
const WISH: Array<P> = [[19, 18], [27, 25], [13, 27], [21, 33], [12, 37], [27, 40], [19, 41], [24, 47], [25, 13], [12, 19], [28, 32], [14, 45]]
/** Глубина нейрона 0..1 (дальний мельче и бледнее), для левых и правых отдельно, чтобы зеркало не выглядело копией. */
const Z_LEFT = [0.8, 1, 0.35, 0.7, 0.5, 0.95, 0.4, 0.65, 0.55, 0.3, 0.85, 0.45]
const Z_RIGHT = Z_LEFT.map((z) => 0.3 + 0.7 * ((z * 3.7 + 0.31) % 1))

export const HALF = WISH.length
const LEFT_NODES: Array<[number, number]> = WISH.map((w, i) => { const p = snapInside(w, nodeInset(Math.max(Z_LEFT[i], Z_RIGHT[i]))); return [p[0], p[1]] })
export const BRAIN_NODES: ReadonlyArray<readonly [number, number]> = [...LEFT_NODES, ...LEFT_NODES.map(([x, y]): [number, number] => [64 - x, y])]
export const BRAIN_DEPTH: ReadonlyArray<number> = [...Z_LEFT, ...Z_RIGHT]

/** Области: лоб, верх, висок, затылок. Нейроны в каждой симметричны по полушариям. */
export const BRAIN_REGIONS: ReadonlyArray<readonly number[]> = [[0, 1, 8, 12, 13, 20], [2, 3, 9, 10, 14, 15, 21, 22], [4, 6, 16, 18], [5, 7, 11, 17, 19, 23]]
export const REGION_COUNT = BRAIN_REGIONS.length

export function regionOfNode(node: number): number {
  return BRAIN_REGIONS.findIndex((r) => r.includes(node))
}

/** Нейрон ближе всех к точке (x, y в координатах рисунка) и не дальше maxDist; иначе -1. */
export function nearestNode(x: number, y: number, maxDist = 9): number {
  let best = -1
  let d = maxDist
  BRAIN_NODES.forEach(([nx, ny], i) => {
    const e = Math.hypot(nx - x, ny - y)
    if (e <= d) { d = e; best = i }
  })
  return best
}

/** Порядок вспышки при импульсе из точки: от ближайших нейронов к дальним (волна расходится от места нажатия). */
export function waveOrder(x: number, y: number): number[] {
  return BRAIN_NODES.map(([nx, ny], i) => ({ i, d: Math.hypot(nx - x, ny - y) })).sort((a, b) => a.d - b.d).map((e) => e.i)
}
