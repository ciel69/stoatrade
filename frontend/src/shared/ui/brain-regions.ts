/** Геометрия нарисованного мозга (viewBox 64×64): 24 нейрона, левое полушарие 0..11 и зеркальное правое 12..23, и четыре области коры из нейронов обоих полушарий. */
const LEFT: Array<[number, number]> = [[19, 20], [26, 27], [15, 29], [22, 36], [12, 38], [27, 42], [19, 46], [25, 52], [26, 17], [13, 23], [29, 34], [17, 52]]
export const HALF = LEFT.length
export const BRAIN_NODES: ReadonlyArray<readonly [number, number]> = [...LEFT, ...LEFT.map(([x, y]): [number, number] => [64 - x, y])]

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
