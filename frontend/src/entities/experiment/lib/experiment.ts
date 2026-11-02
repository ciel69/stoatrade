import { cutInfo, expModeWord, expValueWord, fixed, pHint, pWord, plural, signed, type ExpCut, type PWord } from '@/shared/lib'
import type { ExpDetail, ExpMeta, ExpRow, ExpTrade } from '../model/types'

/** Порог числа сделок, с которого группа считается проверяемой (как min_n сервера). */
export const DEFAULT_MIN_N = 30

/** Ключ строки для адреса и выбора: режим и имя группы (имя может содержать двоеточие, поэтому делим по первому). */
export const rowKey = (r: Pick<ExpRow, 'mode' | 'name'>): string => `${r.mode}:${r.name}`
export function splitKey(k: string | null | undefined): { mode: string; name: string } | null {
  if (!k) return null
  const i = k.indexOf(':')
  return i > 0 ? { mode: k.slice(0, i), name: k.slice(i + 1) } : null
}

/**
 * Порядок строк: подтверждённые сверху; затем группы, где данных достаточно, по среднему результату (лучшие выше); затем группы с малым n, где больше сделок, тем выше.
 * Одинаковое: по имени, чтобы порядок не прыгал между обновлениями.
 */
export function sortRows(rows: ReadonlyArray<ExpRow>): ExpRow[] {
  const rank = (r: ExpRow): number => (r.confirmed ? 0 : r.small ? 2 : 1)
  return [...rows].sort((a, b) => {
    const d = rank(a) - rank(b)
    if (d) return d
    if (rank(a) === 2) return b.n - a.n || a.name.localeCompare(b.name)
    return (b.avg_r ?? -Infinity) - (a.avg_r ?? -Infinity) || a.name.localeCompare(b.name)
  })
}

export type Tone = 'pos' | 'neg' | ''
const toneOf = (x: number | null | undefined): Tone => (typeof x !== 'number' || x === 0 ? '' : x > 0 ? 'pos' : 'neg')

export interface ExpRowView {
  key: string
  title: string
  /** режим словами; пусто, если в таблице один режим */
  mode: string
  n: number
  nText: string
  small: boolean
  confirmed: boolean
  avgR: string
  avgTone: Tone
  win: string
  pnl: string
  pnlTone: Tone
  p: PWord
  pHint: string
  /** гипотеза эксперимента из его описания (если владелец её записал) */
  note: string | null
  row: ExpRow
}

function noteOf(cut: ExpCut, name: string, meta: Record<string, ExpMeta> | undefined): string | null {
  if (cut !== 'exp' && cut !== 'variant') return null
  const m = meta?.[name.split('/')[0]]
  return m?.hypothesis ? m.hypothesis : null
}

/** Сделки словами: «1 сделка», «5 сделок». */
export const tradesText = (n: number): string => `${n} ${plural(n, ['сделка', 'сделки', 'сделок'])}`

export function rowViews(d: ExpDetail | null, cut: ExpCut): ExpRowView[] {
  if (!d?.ok || !d.rows) return []
  const multi = new Set(d.rows.map((r) => r.mode)).size > 1
  return sortRows(d.rows).map((r) => ({
    key: rowKey(r),
    title: expValueWord(cut, r.name),
    mode: multi ? expModeWord(r.mode) : '',
    n: r.n,
    nText: tradesText(r.n),
    small: r.small,
    confirmed: r.confirmed,
    avgR: typeof r.avg_r === 'number' ? `${signed(r.avg_r, 2)} R` : '–',
    avgTone: toneOf(r.avg_r),
    win: typeof r.win_rate === 'number' ? `${Math.round(r.win_rate * 100)}%` : '–',
    pnl: signed(r.pnl, 2),
    pnlTone: toneOf(r.pnl),
    p: pWord(r.p, r.small, d.alpha_adj),
    pHint: r.small ? `Сделок меньше ${d.min_n ?? DEFAULT_MIN_N}: вывод делать рано` : pHint(r.p, d.alpha_adj),
    note: noteOf(cut, r.name, d.meta),
    row: r,
  }))
}

export interface ExpHeadline {
  text: string
  detail: string
  /** ok: есть подтверждённые; idle: нет */
  tone: 'ok' | 'idle'
}

export function expHeadline(d: ExpDetail | null): ExpHeadline | null {
  if (!d?.ok) return null
  const n = d.confirmed?.length ?? 0
  const min = d.min_n ?? DEFAULT_MIN_N
  if (n > 0) {
    return {
      text: `Подтверждённых гипотез: ${n}`,
      detail: `Каждая прошла проверку на бумаге: не меньше ${min} сделок, средний результат выше нуля, и случайность маловероятна даже с поправкой на число сравнений. Такие гипотезы повторяют в первую очередь. Это не обещание прибыли.`,
      tone: 'ok',
    }
  }
  return {
    text: 'Подтверждённых гипотез пока нет',
    detail: d.total_closed
      ? `Чтобы гипотеза считалась подтверждённой, нужно не меньше ${min} сделок на бумаге, средний результат выше нуля и малая вероятность случайности. Пока такой нет, это нормально: выборка копится.`
      : 'Закрытых сделок пока нет, поэтому нечего сравнивать.',
    tone: 'idle',
  }
}

/** Про число сравнений и порог: одной фразой, с числами. */
export function thresholdText(d: ExpDetail | null): string {
  if (!d?.ok || !d.k) return ''
  const a = typeof d.alpha_adj === 'number' ? String(Number(d.alpha_adj.toPrecision(2))).replace('.', ',') : '–'
  if (d.k === 1) return 'В этом разрезе одна группа, поэтому порог обычный: вероятность случайности ниже 0,05.'
  return `В этом разрезе групп: ${d.k}. Чем больше групп сравнивают, тем чаще одна из них выглядит удачной просто так, поэтому порог строже: вероятность случайности должна быть ниже ${a} вместо 0,05.`
}

/** Ограничено ли число строк сервером (показано меньше, чем есть). */
export function hiddenRows(d: ExpDetail | null): number {
  return d?.ok && d.rows && typeof d.rows_total === 'number' ? Math.max(0, d.rows_total - d.rows.length) : 0
}

export type ExpState = 'link' | 'loading' | 'failed' | 'none' | 'cut' | 'ok'

/** Что показать вместо таблицы: нет связи, загрузка, сбой сервера, закрытых сделок нет совсем, в этом разрезе пусто, либо данные. */
export function expState(d: ExpDetail | null, pending: boolean, linkOpen: boolean): ExpState {
  if (pending || !d) return linkOpen ? 'loading' : 'link'
  if (!d.ok) return 'failed'
  if (!d.rows?.length) return d.total_closed ? 'cut' : 'none'
  return 'ok'
}

export interface ExpTradeView {
  id: string | null
  title: string
  r: string
  rTone: Tone
  pnl: string
  when: string
  reason: string
}

const SIDE: Record<string, string> = { long: 'покупка', short: 'продажа' }

export function tradeViews(ts: ReadonlyArray<ExpTrade>): ExpTradeView[] {
  return ts.map((t) => ({
    id: t.id,
    title: [t.sym, t.side ? (SIDE[t.side] ?? t.side) : ''].filter(Boolean).join(', ') || 'Сделка',
    r: typeof t.r === 'number' ? `${signed(t.r, 2)} R` : '–',
    rTone: toneOf(t.r),
    pnl: typeof t.pnl === 'number' ? `${signed(t.pnl, 2)} USDT` : '–',
    when: typeof t.ts === 'number' ? new Date(t.ts * 1000).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '',
    reason: t.reason ? expValueWord('reason', t.reason) : '',
  }))
}

/** Заголовок боковой панели: «Гипотеза: sweep-fade» / «Сторона: покупка». */
export function groupTitle(cut: ExpCut, name: string): string {
  return `${cutInfo(cut).one}: ${expValueWord(cut, name)}`
}

/** Короткие факты группы для панели: сделок, средний результат, доля прибыльных, итог, издержки. */
export function groupFacts(r: ExpRow): Array<{ k: string; v: string; tone: Tone }> {
  return [
    { k: 'Сделок', v: String(r.n), tone: '' },
    { k: 'Средний результат', v: typeof r.avg_r === 'number' ? `${signed(r.avg_r, 2)} R` : '–', tone: toneOf(r.avg_r) },
    { k: 'Доля прибыльных', v: typeof r.win_rate === 'number' ? `${Math.round(r.win_rate * 100)}%` : '–', tone: '' },
    { k: 'Итог', v: `${signed(r.pnl, 2)} USDT`, tone: toneOf(r.pnl) },
    { k: 'Комиссии', v: `${fixed(r.fees, 2)} USDT`, tone: '' },
  ]
}
