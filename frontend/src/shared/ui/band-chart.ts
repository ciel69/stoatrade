/** Чистая геометрия графика UiBandChart: ряд, лента интервала и нулевая линия в координатах SVG. Без DOM, поэтому проверяется тестами напрямую. */

export interface BandPoint {
  /** подпись по оси X (номер сделки, день): показывается в подсказке и таблице */
  i: number | string
  /** значение ряда */
  v: number
  /** границы интервала; null или отсутствие означают «интервала нет» (мало данных) */
  lo?: number | null
  hi?: number | null
  /** размер выборки в этой точке */
  n?: number | null
}

export interface Pad { l: number; r: number; t: number; b: number }

export const PAD: Pad = { l: 38, r: 10, t: 10, b: 8 }

export interface Tick { y: number; text: string; zero: boolean }

export interface ZeroCrossing {
  x: number
  y: number
  up: boolean
}

export interface BandLayout {
  /** точки после очистки от нечисловых значений */
  pts: BandPoint[]
  xs: number[]
  ys: number[]
  /** ломаная ряда; для одной точки пусто (рисуется только точка) */
  line: string
  area: string
  /** закрашенные участки ленты: непрерывные отрезки, где интервал известен у каждой точки */
  bands: string[]
  /** вертикальные усы интервала там, где соседей нет (одиночная точка между пустыми) */
  whiskers: { x: number; y1: number; y2: number }[]
  crossings: ZeroCrossing[]
  /** y нулевой линии или null, если нуль вне видимой области (не бывает: домен всегда включает 0) */
  zeroY: number
  ticks: Tick[]
  min: number
  max: number
}

const fin = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x)

/** точки с конечным значением; границы интервала сохраняются только парой и по порядку lo <= hi */
export function cleanPoints(src: BandPoint[]): BandPoint[] {
  const out: BandPoint[] = []
  for (const p of src) {
    if (!fin(p.v)) continue
    const ok = fin(p.lo) && fin(p.hi)
    const lo = ok ? Math.min(p.lo as number, p.hi as number) : null
    const hi = ok ? Math.max(p.lo as number, p.hi as number) : null
    out.push({ i: p.i, v: p.v, lo, hi, n: fin(p.n) ? p.n : null })
  }
  return out
}

/** знак минус как в остальных подписях панели */
export function fmtNum(v: number, digits = 2): string {
  if (!Number.isFinite(v)) return '—'
  const s = Math.abs(v).toFixed(digits)
  const zero = Number(s) === 0
  return `${v < 0 && !zero ? '−' : ''}${s}`
}

export function fmtSigned(v: number, digits = 2): string {
  if (!Number.isFinite(v)) return '—'
  const s = fmtNum(v, digits)
  return v > 0 && Number(s) !== 0 ? `+${s}` : s
}

/** домен по оси Y: все значения, границы ленты и обязательно нуль; небольшой запас, у плоского ряда расширение вокруг нуля */
export function domainOf(pts: BandPoint[]): { min: number; max: number } {
  let min = 0
  let max = 0
  for (const p of pts) {
    for (const x of [p.v, p.lo, p.hi]) {
      if (!fin(x)) continue
      if (x < min) min = x
      if (x > max) max = x
    }
  }
  if (max - min < 1e-9) return { min: -1, max: 1 }
  const pad = (max - min) * 0.08
  return { min: min === 0 ? 0 : min - pad, max: max === 0 ? 0 : max + pad }
}

const r1 = (n: number): string => (Math.round(n * 10) / 10).toString()

export function layoutOf(src: BandPoint[], width: number, height: number, pad: Pad = PAD, fmt: (v: number) => string = (v) => fmtNum(v)): BandLayout {
  const pts = cleanPoints(src)
  const w = Math.max(60, width)
  const h = Math.max(40, height)
  const iw = Math.max(1, w - pad.l - pad.r)
  const ih = Math.max(1, h - pad.t - pad.b)
  const { min, max } = domainOf(pts)
  const yOf = (v: number): number => pad.t + (1 - (v - min) / (max - min)) * ih
  const n = pts.length
  const xs = pts.map((_, k) => (n === 1 ? pad.l + iw / 2 : pad.l + (k / (n - 1)) * iw))
  const ys = pts.map((p) => yOf(p.v))
  const line = n > 1 ? pts.map((_, k) => `${k ? 'L' : 'M'}${r1(xs[k]!)} ${r1(ys[k]!)}`).join('') : ''

  const bands: string[] = []
  const whiskers: BandLayout['whiskers'] = []
  let k = 0
  while (k < n) {
    if (pts[k]!.lo == null) { k++; continue }
    let e = k
    while (e + 1 < n && pts[e + 1]!.lo != null) e++
    if (e === k) {
      whiskers.push({ x: xs[k]!, y1: yOf(pts[k]!.hi as number), y2: yOf(pts[k]!.lo as number) })
    } else {
      const up = []
      const down = []
      for (let j = k; j <= e; j++) {
        up.push(`${r1(xs[j]!)} ${r1(yOf(pts[j]!.hi as number))}`)
        down.unshift(`${r1(xs[j]!)} ${r1(yOf(pts[j]!.lo as number))}`)
      }
      bands.push(`M${up.join('L')}L${down.join('L')}Z`)
    }
    k = e + 1
  }

  const zeroY = yOf(0)
  const area = n > 1
    ? `M${r1(xs[0]!)} ${r1(zeroY)}${pts.map((_, j) => `L${r1(xs[j]!)} ${r1(ys[j]!)}`).join('')}L${r1(xs[n - 1]!)} ${r1(zeroY)}Z`
    : ''
  const crossings: ZeroCrossing[] = []
  for (let j = 0; j + 1 < n; j++) {
    const a = pts[j]!.v
    const b = pts[j + 1]!.v
    if (a * b >= 0) continue
    const t = Math.abs(a) / (Math.abs(a) + Math.abs(b))
    crossings.push({ x: xs[j]! + (xs[j + 1]! - xs[j]!) * t, y: zeroY, up: b > a })
  }
  const ticks: Tick[] = [{ y: zeroY, text: '0', zero: true }]
  for (const v of [max, min]) {
    const y = yOf(v)
    if (Math.abs(y - zeroY) >= 14) ticks.push({ y, text: fmt(v), zero: false })
  }
  return { pts, xs, ys, line, area, bands, whiskers, crossings, zeroY, ticks, min, max }
}

/** индекс ближайшей точки к координате x (в пикселях SVG) */
export function nearestIndex(xs: number[], x: number): number {
  if (!xs.length) return -1
  let best = 0
  let d = Infinity
  for (let k = 0; k < xs.length; k++) {
    const dd = Math.abs(xs[k]! - x)
    if (dd < d) { d = dd; best = k }
  }
  return best
}

/** шаг с клавиатуры: стрелки, Home, End; возвращает новый индекс или null, если клавиша не наша */
export function stepIndex(cur: number | null, count: number, key: string): number | null {
  if (count <= 0) return null
  const last = count - 1
  if (key === 'Home') return 0
  if (key === 'End') return last
  if (key === 'ArrowLeft') return cur == null ? last : Math.max(0, cur - 1)
  if (key === 'ArrowRight') return cur == null ? last : Math.min(last, cur + 1)
  return null
}

export function pointText(p: BandPoint, fmt: (v: number) => string, unit = ''): string {
  const u = unit ? ` ${unit}` : ''
  const parts = [`${p.i}: ${fmt(p.v)}${u}`]
  if (p.lo != null && p.hi != null) parts.push(`интервал ${fmt(p.lo)} … ${fmt(p.hi)}`)
  else parts.push('интервала нет')
  if (p.n != null) parts.push(`n = ${p.n}`)
  return parts.join(' · ')
}

/** текстовая альтернатива: что за график, последнее значение, n, где нуль относительно интервала */
export function summaryText(label: string, pts: BandPoint[], fmt: (v: number) => string, unit = ''): string {
  if (!pts.length) return `${label}: данных нет`
  const last = pts[pts.length - 1]!
  const u = unit ? ` ${unit}` : ''
  const tail = [`последнее ${fmt(last.v)}${u}`]
  if (last.n != null) tail.push(`n = ${last.n}`)
  if (last.lo != null && last.hi != null) {
    tail.push(`интервал ${fmt(last.lo)} … ${fmt(last.hi)}`)
    tail.push(last.lo > 0 ? 'нуль ниже интервала' : last.hi < 0 ? 'нуль выше интервала' : 'нуль внутри интервала')
  } else tail.push('интервала нет')
  return `${label}: ${pts.length} ${plural(pts.length)}, ${tail.join(', ')}`
}

function plural(n: number): string {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return 'точка'
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return 'точки'
  return 'точек'
}
