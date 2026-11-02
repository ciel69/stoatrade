import { estView, type Est, type EstView, type SkipsBlock, type SkipsWeek } from '@/entities/learning'
import type { BandPoint } from '@/shared/ui'

/** Пояснение к упущенному ходу (решение владельца Q8): проценты цены, не R. Показывается всегда, не зависит от текста сервера. */
export const MISSED_EXPLAIN = 'Упущенный ход — на сколько процентов цены ушёл рынок в сторону пропущенного входа, после издержек, по неверным пропускам. В R его не перевести: у пропуска не было стопа.'
export const FREEZE_HINT = 'Доля верных растёт, но сделок стало заметно меньше: мозг мог просто реже входить, а не лучше выбирать.'

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

/** «2026-W44» -> «нед. 44»; с годом, если ряд пересекает границу года. */
export function weekLabel(week: string, withYear = false): string {
  const m = /^(\d{4})-W(\d{1,2})$/.exec(week)
  if (!m) return week.replace(/[-_]+/g, ' ')
  return withYear ? `нед. ${Number(m[2])}, ${m[1]}` : `нед. ${Number(m[2])}`
}
const crossesYear = (weeks: ReadonlyArray<{ week: string }>): boolean => new Set(weeks.map((w) => w.week.slice(0, 4))).size > 1

/** Упущенный ход: значение уже в процентах цены (0.35 = 0.35%), поэтому «pct»-вид оценки (доля × 100) не подходит. */
export function missedView(e: Est | null | undefined): EstView {
  const v = estView(e, 'plain', 2, 0)
  const lo = e?.lo
  const hi = e?.hi
  const interval = typeof lo === 'number' && typeof hi === 'number' ? `от ${lo.toFixed(2)}% до ${hi.toFixed(2)}%` : ''
  return { ...v, value: v.value === '–' ? '–' : `${v.value}%`, interval, tone: '' }
}

export interface SkipRow {
  key: string
  label: string
  partial: boolean
  n: number
  small: boolean
  correct: EstView
  missed: EstView
  trades: number
}

export function skipRows(weeks: ReadonlyArray<SkipsWeek>, minN = 30): SkipRow[] {
  const year = crossesYear(weeks)
  return weeks.map((w) => ({
    key: w.week, label: weekLabel(w.week, year), partial: w.partial === true, n: w.n, small: w.n < minN,
    correct: estView(w.correct, 'pct', 0, 0), missed: missedView(w.missed_pct), trades: w.trades,
  }))
}

export function correctPoints(weeks: ReadonlyArray<SkipsWeek>): BandPoint[] {
  const year = crossesYear(weeks)
  const out: BandPoint[] = []
  for (const w of weeks) {
    const e = w.correct
    if (!e || typeof e.v !== 'number') continue
    out.push({ i: weekLabel(w.week, year) + (w.partial === true ? ' (неполная)' : ''), v: e.v, lo: e.lo, hi: e.hi, n: w.n })
  }
  return out
}

export interface TotalView { n: number; correct: EstView; wrong: number | null; missedMean: string; missedSum: string }

/** Итог за период; `total` приходит произвольной формы, поэтому поля читаются осторожно. */
export function totalView(total: SkipsBlock['total']): TotalView | null {
  if (!isObj(total)) return null
  const n = num(total.n)
  if (n == null) return null
  const mean = num(total.missed_pct_mean)
  const sum = num(total.missed_pct_sum)
  const c = isObj(total.correct) ? (total.correct as unknown as Est) : null
  return { n, correct: estView(c, 'pct', 0, 0), wrong: num(total.wrong_n), missedMean: mean == null ? '–' : `${mean.toFixed(2)}%`, missedSum: sum == null ? '–' : `${sum.toFixed(1)}%` }
}

/**
 * Замораживание: доля верных пропусков «улучшается», а сделок за последние 4 недели заметно меньше, чем за предыдущие 4.
 * Нужно 8 недель ряда; порог — на треть меньше.
 */
export function isFreezing(weeks: ReadonlyArray<Pick<SkipsWeek, 'trades'>>, signal: string | null): boolean {
  if (signal !== 'improving' || weeks.length < 8) return false
  const last8 = weeks.slice(-8)
  const sum = (a: ReadonlyArray<Pick<SkipsWeek, 'trades'>>): number => a.reduce((s, w) => s + (w.trades || 0), 0)
  const prev = sum(last8.slice(0, 4))
  const cur = sum(last8.slice(4))
  return prev > 0 && cur < prev * (2 / 3)
}

export function signalChip(signal: string | null): { text: string; tone: 'ok' | 'bad' | 'neutral' } | null {
  if (signal === 'improving') return { text: 'Верных пропусков становится больше', tone: 'ok' }
  if (signal === 'worse') return { text: 'Верных пропусков становится меньше', tone: 'bad' }
  if (signal === 'flat') return { text: 'Доля верных без изменений', tone: 'neutral' }
  return null
}

export const pctText = (x: number): string => `${Math.round(x * 100)}%`
