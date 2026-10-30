/** Геометрия нарисованного мозга (viewBox 64×64): 16 нейронов, левое полушарие 0..7 и зеркальное правое 8..15, и четыре области коры из нейронов обоих полушарий. */
const LEFT: Array<[number, number]> = [[19, 20], [26, 27], [15, 29], [22, 36], [12, 38], [27, 42], [19, 46], [25, 52]]
export const BRAIN_NODES: ReadonlyArray<readonly [number, number]> = [...LEFT, ...LEFT.map(([x, y]): [number, number] => [64 - x, y])]

/** Области: лоб, верх, висок, затылок. Нейроны в каждой симметричны по полушариям. */
export const BRAIN_REGIONS: ReadonlyArray<readonly number[]> = [[0, 1, 8, 9], [2, 3, 10, 11], [4, 6, 12, 14], [5, 7, 13, 15]]
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
