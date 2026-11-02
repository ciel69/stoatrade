import { estView, type DisciplineBlock, type DisciplineWeek, type Est, type EstView } from '@/entities/learning'
import type { BandPoint } from '@/shared/ui'

/** Теги ошибок из словаря памяти (tb.brain.validate.ERR_TAGS) простыми словами. Неизвестный тег: дефисы и подчёркивания становятся пробелами, без сырого кода. */
const TAG_TEXT: Readonly<Record<string, string>> = {
  'no-confirm': 'вход без подтверждения',
  chase: 'погоня за ценой',
  'tight-stop': 'слишком тесный стоп',
  'wide-stop': 'слишком широкий стоп',
  'wrong-direction': 'неверное направление',
  'news-miss': 'пропущена новость',
  size: 'ошибка размера',
  'fee-drag': 'комиссии съели результат',
  'rule-break': 'нарушено правило',
  'good-process-bad-luck': 'процесс верный, не повезло',
  good: 'ошибок нет',
  late_entry: 'поздний вход',
  no_stop_move: 'стоп не переносился',
}

export function tagText(tag: string): string {
  const known = TAG_TEXT[tag]
  if (known) return known
  const plain = tag.replace(/[-_]+/g, ' ').trim()
  return plain ? plain.charAt(0).toLowerCase() + plain.slice(1) : 'без названия'
}

/** «2026-W44» -> «нед. 44»; с годом, если ряд пересекает границу года. */
export function weekLabel(week: string, withYear = false): string {
  const m = /^(\d{4})-W(\d{1,2})$/.exec(week)
  if (!m) return week.replace(/[-_]+/g, ' ')
  return withYear ? `нед. ${Number(m[2])}, ${m[1]}` : `нед. ${Number(m[2])}`
}

export const crossesYear = (weeks: ReadonlyArray<{ week: string }>): boolean => new Set(weeks.map((w) => w.week.slice(0, 4))).size > 1

export interface WeekRow {
  key: string
  label: string
  partial: boolean
  cases: number
  viol: EstView
  /** подпись под числом n: «мало данных» либо пусто */
  small: boolean
  err: EstView
  top: string
}

/** Строки таблицы: от новой недели к старой не переворачиваем, порядок как в ряду (слева направо = раньше -> позже). */
export function weekRows(weeks: ReadonlyArray<DisciplineWeek>, minN = 30): WeekRow[] {
  const year = crossesYear(weeks)
  return weeks.map((w) => ({
    key: w.week,
    label: weekLabel(w.week, year),
    partial: w.partial,
    cases: w.n_cases,
    viol: estView(w.viol, 'pct', 0, 0),
    small: w.n_cases < minN,
    err: estView(w.err_any, 'pct', 0, 0),
    top: w.err_top.map(([t, c]) => `${tagText(t)}: ${c}`).join(', '),
  }))
}

/** Точки графика: доля нарушений по неделям; недели без оценки пропускаются. */
export function violPoints(weeks: ReadonlyArray<DisciplineWeek>): BandPoint[] {
  const year = crossesYear(weeks)
  const out: BandPoint[] = []
  for (const w of weeks) {
    const e: Est | null = w.viol
    if (!e || typeof e.v !== 'number') continue
    out.push({ i: weekLabel(w.week, year) + (w.partial ? ' (неполная)' : ''), v: e.v, lo: e.lo, hi: e.hi, n: w.n_rf })
  }
  return out
}

export interface TagRow { tag: string; text: string; value: string; interval: string; n: string; share: number }

export function tagRows(block: Pick<DisciplineBlock, 'err_total'>): TagRow[] {
  return block.err_total.map((t) => {
    const v = estView(t.share, 'pct', 0, 0)
    const raw = t.share?.v
    return { tag: t.tag, text: tagText(t.tag), value: v.value, interval: v.interval, n: `n = ${t.n}`, share: typeof raw === 'number' ? Math.min(1, Math.max(0, raw)) : 0 }
  })
}

export const pctText = (x: number): string => `${Math.round(x * 100)}%`

/** Знак сигнала: chip с текстом, цвет только усиливает. */
export function signalChip(signal: string | null): { text: string; tone: 'ok' | 'bad' | 'neutral' } | null {
  if (signal === 'falling') return { text: 'Нарушений становится меньше', tone: 'ok' }
  if (signal === 'rising') return { text: 'Нарушений становится больше', tone: 'bad' }
  if (signal === 'flat') return { text: 'Нарушения без изменений', tone: 'neutral' }
  return null
}
