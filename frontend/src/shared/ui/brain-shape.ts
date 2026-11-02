/**
 * Форма мозга (вид спереди, viewBox 64×64): контур из двух долей, борозды коры и расчёт «внутри ли точка». Всё чистые данные и функции без DOM,
 * поэтому границу можно проверить тестом, а нейроны и связи нельзя выпустить за контур: положения нейронов подгоняются внутрь с запасом (snapInside),
 * а модель сети (brain-net.ts) держит отклонение от места не больше MAX_OFFSET.
 *
 * Контур задан кубическими кривыми левого полушария; правое получается зеркалом. Из тех же кривых строится и SVG-путь (BRAIN_PATH), и многоугольник
 * (BRAIN_POLY) для проверок, так что рисунок и проверка совпадают.
 */
export type P = readonly [number, number]
type Cubic = readonly [P, P, P]          // две контрольные точки и конец; начало — конец предыдущей

const TOP: P = [32, 11]                  // верхняя выемка между долями
const LEFT: ReadonlyArray<Cubic> = [
  [[28.5, 6.2], [21, 4.6], [15.8, 9.2]],
  [[10.2, 11.8], [5.6, 17.2], [7.4, 23.2]],
  [[4.2, 27.6], [4.4, 34.8], [8.4, 38.8]],
  [[7.6, 45], [11.6, 50.4], [17.6, 50.8]],
  [[20.8, 56.2], [28.2, 57.2], [32, 54.2]],
]
const mirror = ([x, y]: P): P => [64 - x, y]
const f = (v: number): string => String(Math.round(v * 100) / 100)
const pt = ([x, y]: P): string => `${f(x)} ${f(y)}`

/** Контур целиком: вниз по левой доле, обратно по правой. */
export const BRAIN_PATH: string = (() => {
  let d = `M${pt(TOP)}`
  for (const [a, b, c] of LEFT) d += `C${pt(a)} ${pt(b)} ${pt(c)}`
  const start = LEFT.map((s) => s[2])
  for (let i = LEFT.length - 1; i >= 0; i--) {
    const [a, b] = LEFT[i]
    const from = i === 0 ? TOP : start[i - 1]
    d += `C${pt(mirror(b))} ${pt(mirror(a))} ${pt(mirror(from))}`
  }
  return `${d}Z`
})()

/** Борозды левого полушария (правое рисуется зеркалом): плавные дуги, не касающиеся контура. */
export const FOLD_PATH =
  'M14.5 17.5C19 16.6 22.8 19 23.2 23.4M23.2 23.4C27 23.6 29 26.8 28.4 31M9.6 29C14 28.4 17.4 30.6 18.2 34.6M18.2 34.6C21.6 36 23.2 39 22.6 42.6M12.6 44.2C16 42.8 19.6 44 21 47.4M24.6 12.8C26 14.8 26.4 16.8 25.6 19.2'

/** Многоугольник контура: каждая кубическая кривая в 20 отрезков. */
export const BRAIN_POLY: ReadonlyArray<P> = (() => {
  const out: P[] = [TOP]
  const half: P[] = [TOP]
  let cur = TOP
  for (const [a, b, c] of LEFT) {
    for (let i = 1; i <= 20; i++) {
      const t = i / 20
      const v = 1 - t
      half.push([v * v * v * cur[0] + 3 * v * v * t * a[0] + 3 * v * t * t * b[0] + t * t * t * c[0], v * v * v * cur[1] + 3 * v * v * t * a[1] + 3 * v * t * t * b[1] + t * t * t * c[1]])
    }
    cur = c
  }
  out.push(...half.slice(1))
  for (let i = half.length - 2; i >= 1; i--) out.push(mirror(half[i]))
  return out
})()

function segDist(px: number, py: number, a: P, b: P): number {
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  const l = dx * dx + dy * dy
  const t = l ? Math.min(1, Math.max(0, ((px - a[0]) * dx + (py - a[1]) * dy) / l)) : 0
  return Math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy))
}

/** Насколько глубоко точка внутри контура: расстояние до границы, плюс внутри, минус снаружи. */
export function depthAt(x: number, y: number, poly: ReadonlyArray<P> = BRAIN_POLY): number {
  let inside = false
  let min = Infinity
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[i]
    const b = poly[j]
    if ((a[1] > y) !== (b[1] > y) && x < ((b[0] - a[0]) * (y - a[1])) / (b[1] - a[1]) + a[0]) inside = !inside
    min = Math.min(min, segDist(x, y, a, b))
  }
  return inside ? min : -min
}

/** Радиус нейрона по глубине z (0 дальний, 1 ближний). Единицы рисунка. */
export const nodeRadius = (z: number): number => 1.25 + 1.0 * z
/** На сколько нейрон может уйти от своего места (блуждание, курсор, толчок клика вместе). */
export const MAX_OFFSET = 2
/** Запас от контура до центра нейрона: радиус, возможное отклонение и тонкий зазор. */
export const nodeInset = (z: number): number => nodeRadius(z) + MAX_OFFSET + 0.5
/** Левое полушарие: нейрон остаётся левее этой линии, чтобы доли не слипались. */
export const HEMI_X = 28.5

/** Подтягивает точку внутрь, пока она не окажется глубже inset и левее HEMI_X (двигается к центру полушария). */
export function snapInside(p: P, inset: number, anchor: P = [20, 31]): P {
  let [x, y] = p
  for (let i = 0; i < 200; i++) {
    x = Math.min(x, HEMI_X)
    if (depthAt(x, y) >= inset) return [x, y]
    const dx = anchor[0] - x
    const dy = anchor[1] - y
    const d = Math.hypot(dx, dy) || 1
    x += (dx / d) * 0.25
    y += (dy / d) * 0.25
  }
  return [x, y]
}
