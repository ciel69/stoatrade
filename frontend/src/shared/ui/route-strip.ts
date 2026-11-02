import { price, signed } from '../lib/format'

/**
 * Чистая логика полосы маршрута (UiRouteStrip): тексты подсказок, ось «доля -> цена», выбор ближайшей черты под курсором и прижатие подсказки к краям.
 * Доля `at` это положение на оси: 0 у начала (стоп), 1 у дальнего тейка; значения чуть за 0..1 допустимы (цена вышла за ось).
 */
export type MarkKind = 'stop' | 'safe' | 'entry' | 'level' | 'take' | 'now'

export interface StripMark {
  key: string
  kind: MarkKind
  /** положение на оси, доля */
  at: number
  /** название в подсказке: «Стоп», «Вход», «Сейчас», «Тейк 2» */
  name: string
  price: number | null
  /** смещение цены от входа в пользу позиции, % (минус: против) */
  pct?: number | null
  /** результат в деньгах или R, если известен */
  money?: number | null
  unit?: string
}

export interface StripBand {
  from: number
  to: number
  kind: 'risk' | 'locked' | 'gain' | 'loss' | 'gap'
}

export type Tone = 'up' | 'down' | ''

export interface Part {
  text: string
  tone: Tone
}

export interface TipParts {
  name: string
  value: string
  pct: Part | null
  money: Part | null
}

const toneOf = (x: number): Tone => (x > 0 ? 'up' : x < 0 ? 'down' : '')
const finite = (x: number | null | undefined): x is number => typeof x === 'number' && Number.isFinite(x)

/** Части подсказки черты: название, цена, процент от входа и деньги (то, чего нет, пропускается). */
export function markParts(m: Pick<StripMark, 'name' | 'price' | 'pct' | 'money' | 'unit'>): TipParts {
  return {
    name: m.name,
    value: price(m.price),
    pct: finite(m.pct) ? { text: `${signed(m.pct)}%`, tone: toneOf(m.pct) } : null,
    money: finite(m.money) ? { text: `${signed(m.money)} ${m.unit ?? 'USDT'}`, tone: toneOf(m.money) } : null,
  }
}

/** Та же подсказка одной строкой: для aria-label и title («Стоп 2.892 · +3.01% · +1.15 USDT»). */
export function tipText(m: Pick<StripMark, 'name' | 'price' | 'pct' | 'money' | 'unit'>): string {
  const p = markParts(m)
  return [`${p.name} ${p.value}`, p.pct?.text, p.money?.text].filter(Boolean).join(' · ')
}

/** Порядок выбора, если две черты одинаково близко к курсору: текущая цена важнее стопа, стоп важнее входа. */
const PRIORITY: MarkKind[] = ['now', 'stop', 'safe', 'entry', 'level', 'take']

/** Ближайшая к курсору черта в пределах radius пикселей (радиус 8 даёт зону 16 px) или null. */
export function nearestMark(marks: readonly StripMark[], x: number, width: number, radius = 8): StripMark | null {
  let best: StripMark | null = null
  let bestD = Infinity
  for (const m of marks) {
    const d = Math.abs(m.at * width - x)
    if (d > radius) continue
    if (d < bestD - 0.5 || (Math.abs(d - bestD) <= 0.5 && best && PRIORITY.indexOf(m.kind) < PRIORITY.indexOf(best.kind))) {
      best = m
      bestD = d
    }
  }
  return best
}

/** Ось «доля -> цена» по двум самым дальним чертам с известной ценой. null: оси не построить. */
export interface Axis {
  a0: number
  p0: number
  slope: number
  entry: number | null
}

export function axisOf(marks: readonly StripMark[]): Axis | null {
  const known = marks.filter((m) => finite(m.price))
  if (known.length < 2) return null
  const lo = known.reduce((a, b) => (b.at < a.at ? b : a))
  const hi = known.reduce((a, b) => (b.at > a.at ? b : a))
  if (hi.at - lo.at < 0.01) return null
  const e = marks.find((m) => (m.kind === 'entry' || m.kind === 'level') && finite(m.price))
  return { a0: lo.at, p0: lo.price as number, slope: ((hi.price as number) - (lo.price as number)) / (hi.at - lo.at), entry: e ? (e.price as number) : null }
}

export const priceAt = (axis: Axis, frac: number): number => axis.p0 + (frac - axis.a0) * axis.slope

/** Подсказка направляющей: цена под курсором и процент от входа в пользу позиции (рост доли это движение к тейку). */
export function guideParts(axis: Axis, frac: number): TipParts {
  const p = priceAt(axis, frac)
  const pct = axis.entry ? ((p - axis.entry) / axis.entry) * 100 * (axis.slope >= 0 ? 1 : -1) : null
  return markParts({ name: 'Цена', price: p, pct })
}

/**
 * Левый край подсказки (px от левого края полосы): центр на anchor, но не дальше bleed за края полосы
 * (внутренний отступ карточки), чтобы подсказка не вылезала из карточки. Шире полосы: прижимается к левому краю.
 */
export function placeTip(anchor: number, tipW: number, boxW: number, bleed = 8): number {
  const min = -bleed
  const max = boxW + bleed - tipW
  if (max < min) return min
  return Math.min(max, Math.max(min, anchor - tipW / 2))
}

export const clamp = (x: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, x))
