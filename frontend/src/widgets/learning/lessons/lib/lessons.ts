import { FUNNEL_HINTS, effectView, lessonStatusHint, lessonStatusTone, lessonStatusWord, metricName, srcHint, srcWord, type EffectView } from '@/entities/memory'
import type { BestLesson, LessonsBlock, LessonsWeek } from '@/entities/learning'
import { LEARN_SMALL, plural } from '@/shared/lib'
import type { TipContent } from '@/shared/ui'

/**
 * Слова и числа блока «Уроки» страницы «Учится ли мозг» (R2.6). Числа приходят с сервера (src/tb/learn/stages.py: lessons_block) как есть,
 * здесь только подписи. Слова статусов и источников общие с вкладкой «Память» (entities/memory), чтобы один урок назывался одинаково.
 */

const fin = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x)
const int = (x: unknown): number => (fin(x) ? x : 0)

export const LESSONS_MIN_N = 30

/** Режим уроков меток словами: paper и live считаются по основной бирже, testnet — по тестовой. */
export function envText(env: string | null | undefined): string {
  const where = env === 'testnet' ? 'тестовой бирже' : 'основной бирже'
  return `Уроки из разметки посчитаны по ${where}, уроки мозга — по всем режимам сразу.`
}

// ---------------------------------------------------------------- воронка

export interface FunnelStep { key: string; label: string; value: number; sub: string; tip: TipContent }

/** Пять шагов «кандидат → урок → испытание → подтверждён → правило». Подсказки — те же, что на вкладке «Память». */
export function funnelSteps(b: LessonsBlock): FunnelStep[] {
  const f = b.funnel
  const closed = int(f?.trials_closed)
  return [
    { key: 'candidates', label: 'Кандидаты', value: int(f?.candidates), sub: '', tip: FUNNEL_HINTS.candidates },
    { key: 'hyp', label: 'Уроки', value: int(f?.hyp), sub: '', tip: FUNNEL_HINTS.hyp },
    { key: 'trials', label: 'Испытания', value: int(f?.trials), sub: closed ? `закончено ${closed}` : '', tip: FUNNEL_HINTS.trial },
    { key: 'confirmed', label: 'Подтверждены', value: int(f?.confirmed), sub: '', tip: FUNNEL_HINTS.confirmed },
    { key: 'rules', label: 'Правила', value: int(f?.rules), sub: '', tip: { title: 'Правила', text: 'Сколько правил по урокам хоть раз включалось в торговлю. Вредное правило сторож откатит сам.' } },
  ]
}

// ---------------------------------------------------------------- по статусам

export interface StatusRow { key: string; word: string; tone: ReturnType<typeof lessonStatusTone>; tip: TipContent; brain: number; labels: number; total: number }

/** Строки статусов; статусы без уроков не показываются (кроме случая, когда уроков нет совсем). */
export function statusRows(b: LessonsBlock): StatusRow[] {
  const rows = (b.by_status ?? []).map((r) => ({
    key: r.status, word: lessonStatusWord(r.status), tone: lessonStatusTone(r.status), tip: lessonStatusHint(r.status), brain: int(r.brain), labels: int(r.labels), total: int(r.total),
  }))
  return rows.filter((r) => r.total > 0)
}

export const SRC_LABELS = { brain: srcWord('brain'), labels: srcWord('labels') } as const
export const srcTip = (s: 'brain' | 'labels'): TipContent => srcHint(s)

// ---------------------------------------------------------------- недели

export function weekLabel(week: string, withYear = false): string {
  const m = /^(\d{4})-W(\d{1,2})$/.exec(week)
  if (!m) return week.replace(/[-_]+/g, ' ')
  return withYear ? `нед. ${Number(m[2])}, ${m[1]}` : `нед. ${Number(m[2])}`
}

export interface WeekRow { key: string; label: string; partial: boolean; created: number; brain: number; labels: number; confirmed: number; rejected: number }

export function weekRows(weeks: ReadonlyArray<LessonsWeek> | undefined): WeekRow[] {
  const ws = weeks ?? []
  const years = new Set(ws.map((w) => w.week.slice(0, 4)))
  return ws.map((w) => ({
    key: w.week, label: weekLabel(w.week, years.size > 1), partial: !!w.partial, brain: int(w.created_brain), labels: int(w.created_labels),
    created: int(w.created_brain) + int(w.created_labels), confirmed: int(w.confirmed), rejected: int(w.rejected),
  }))
}

/** «За 8 недель: новых 4, подтверждено 1, отклонено 1». */
export function weeksSummary(rows: ReadonlyArray<WeekRow>): string {
  if (!rows.length) return ''
  const sum = (k: 'created' | 'confirmed' | 'rejected'): number => rows.reduce((a, r) => a + r[k], 0)
  const n = rows.length
  return `За ${n} ${plural(n, ['неделю', 'недели', 'недель'])}: новых ${sum('created')}, подтверждено ${sum('confirmed')}, отклонено ${sum('rejected')}.`
}

// ---------------------------------------------------------------- лучший урок

export interface BestView {
  id: string
  text: string
  status: string
  statusTone: ReturnType<typeof lessonStatusTone>
  statusTip: TipContent
  src: string
  srcTip: TipContent
  /** разница с 95% интервалом и n */
  diff: EffectView
  /** «мало данных (n < 30)» либо пусто */
  small: string
  tip: TipContent
}

export function bestView(best: BestLesson | null | undefined, minN = LESSONS_MIN_N): BestView | null {
  if (!best) return null
  const d = best.diff
  const diff = effectView(d ? { v: d.v, lo: d.lo, hi: d.hi, n: d.n, small: best.small } : null, best.metric)
  const what = metricName(best.metric)
  return {
    id: best.lesson_id ?? '',
    text: best.text || `Урок ${best.lesson_id ?? ''}`.trim(),
    status: lessonStatusWord(best.status),
    statusTone: lessonStatusTone(best.status),
    statusTip: lessonStatusHint(best.status),
    src: srcWord(best.src),
    srcTip: srcHint(best.src),
    diff,
    small: best.small || int(best.n) < minN ? LEARN_SMALL(minN) : '',
    tip: {
      title: 'Разница урока',
      text: `Насколько ${what} в условии урока отличается от обычного, с 95% интервалом. Интервал по одну сторону от нуля значит, что разница вряд ли случайна.`,
      rows: [{ label: 'Наблюдений', value: `${int(best.n)}` }, ...(best.lesson_id ? [{ label: 'Код', value: best.lesson_id }] : [])],
    },
  }
}

export const BEST_TIP: TipContent = {
  title: 'Самый сильный урок',
  text: `Действующий урок с самой большой разницей среди уроков, за которыми стоит не меньше ${LESSONS_MIN_N} наблюдений. Уроки с меньшим числом сюда не попадают.`,
}

export const bestNone = (minN = LESSONS_MIN_N): string => `Сильного урока пока нет: ни за одним действующим уроком нет ${minN} наблюдений.`

/** Заголовочный счётчик: «5 уроков». */
export const lessonsCount = (n: number | undefined): string => `${int(n)} ${plural(int(n), ['урок', 'урока', 'уроков'])}`
