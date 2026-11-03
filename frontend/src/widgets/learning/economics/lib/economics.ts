import { MIN_N, type EconomicsBlock, type EconomicsWeek } from '@/entities/learning'
import { LEARN_SIGNAL, plural, signed, usd } from '@/shared/lib'
import type { BandPoint } from '@/shared/ui'

/** Слова блока «Экономика». Расход токенов и USD модели; PnL в USDT и капитал не показываются (решение Q7). */
export const ECON_SCOPE = 'Расход считается по всей платформе и не делится по режимам, а результат относится к режиму, выбранному в шапке.'
export const ECON_DERIVED = 'Расход на сделку и на R — расчётные величины: отношение сумм, интервала у них нет.'
export const ECON_FEW = 'мало'
export const ECON_FEW_NOTE = 'Пометка «мало»: в неделе меньше 30 сделок, отношения ориентировочные.'
export const ECON_NO_SIGNAL = 'Динамика расхода на R не определена: нужно не меньше 30 сделок и положительный R в обеих частях периода.'
export const ECON_CHART = 'Расход на 1 R по неделям, USD'

const WEEKS_SHOWN = 12
const fin = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x)

/** Токены сжато: 850, 12.4 тыс., 3.0 млн. */
export function tokensText(n: number | null | undefined): string {
  if (!fin(n)) return '–'
  const a = Math.abs(n)
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)} млн`
  if (a >= 1e4) return `${(n / 1e3).toFixed(1)} тыс.`
  return String(Math.round(n))
}

/** Доллары модели: нуль (локальная модель) пишется «$0», а не «$0.0000». */
export function spendText(x: number | null | undefined): string {
  if (!fin(x)) return '–'
  return x === 0 ? '$0' : usd(x)
}

/** «2026-W44» -> «нед. 44»; год добавляется, если ряд пересекает границу годов. Нераспознанная подпись остаётся как есть. */
export function weekLabel(week: string, withYear: boolean): string {
  const m = /^(\d{4})-W(\d{1,2})$/.exec(week)
  if (!m) return week
  return withYear ? `нед. ${Number(m[2])}, ${m[1]}` : `нед. ${Number(m[2])}`
}

const callsOf = (w: EconomicsWeek): number | null => (fin(w.calls) ? w.calls : w.calls && typeof w.calls === 'object' && fin(w.calls.n) ? w.calls.n : null)
const rText = (x: number | null): string => (fin(x) ? `${signed(x, 1)} R` : '–')
const callsText = (n: number | null): string => (fin(n) ? `${Math.round(n)} ${plural(Math.round(n), ['вызов', 'вызова', 'вызовов'])}` : '')

export interface EconRow {
  key: string
  label: string
  partial: boolean
  /** «вход 3.0 млн, выход 0.4 млн» */
  tokens: string
  spend: string
  trades: number
  /** «12 сделок» для узкой раскладки */
  tradesText: string
  few: boolean
  r: string
  rTone: 'pos' | 'neg' | ''
  perTrade: string
  perR: string
}

export interface EconTotals {
  spend: string
  tokensIn: string
  tokensOut: string
  calls: string
  trades: number
  r: string
  rTone: 'pos' | 'neg' | ''
  perR: string
  perRNote: string
}

export interface EconomicsView {
  rows: EconRow[]
  totals: EconTotals
  chart: BandPoint[]
  signal: string
  anyFew: boolean
}

const sum = (xs: Array<number | null>): number | null => {
  const v = xs.filter(fin)
  return v.length ? v.reduce((a, b) => a + b, 0) : null
}

export function economicsView(b: EconomicsBlock): EconomicsView {
  const src = (Array.isArray(b.weeks) ? b.weeks : []).slice(-WEEKS_SHOWN)
  const years = new Set(src.map((w) => /^(\d{4})-/.exec(w.week)?.[1]).filter(Boolean))
  const withYear = years.size > 1
  const rows: EconRow[] = src
    .map((w): EconRow => ({
      key: w.week,
      label: weekLabel(w.week, withYear),
      partial: w.partial === true,
      tokens: `вход ${tokensText(w.tok_in)}, выход ${tokensText(w.tok_out)}`,
      spend: spendText(w.llm_usd),
      trades: w.n_trades,
      tradesText: `${w.n_trades} ${plural(w.n_trades, ['сделка', 'сделки', 'сделок'])}`,
      few: w.n_trades > 0 && w.n_trades < MIN_N,
      r: rText(w.r_sum),
      rTone: !fin(w.r_sum) || w.r_sum === 0 ? '' : w.r_sum > 0 ? 'pos' : 'neg',
      perTrade: w.n_trades > 0 ? spendText(w.usd_per_trade) : '–',
      perR: spendText(w.usd_per_r),
    }))
    .reverse()          // сверху новая неделя

  const usdSum = sum(src.map((w) => w.llm_usd))
  const rSum = sum(src.map((w) => w.r_sum))
  const trades = src.reduce((a, w) => a + (fin(w.n_trades) ? w.n_trades : 0), 0)
  const perR = usdSum != null && rSum != null && rSum > 0 ? usdSum / rSum : null
  const totals: EconTotals = {
    spend: spendText(usdSum),
    tokensIn: tokensText(sum(src.map((w) => w.tok_in))),
    tokensOut: tokensText(sum(src.map((w) => w.tok_out))),
    calls: callsText(sum(src.map(callsOf))),
    trades,
    r: rText(rSum),
    rTone: rSum == null || rSum === 0 ? '' : rSum > 0 ? 'pos' : 'neg',
    perR: spendText(perR),
    perRNote: perR == null ? 'нет, пока результат не положителен' : 'расчётная величина',
  }

  const chart: BandPoint[] = src.filter((w) => fin(w.usd_per_r)).map((w) => ({ i: weekLabel(w.week, withYear), v: w.usd_per_r as number, n: w.n_trades }))
  const sig = typeof b.signal === 'string' ? LEARN_SIGNAL[b.signal] : undefined
  return {
    rows, totals, chart,
    signal: sig ? `Расход на 1 R ${sig}: последние 4 недели против предыдущих 4.` : ECON_NO_SIGNAL,
    anyFew: rows.some((r) => r.few),
  }
}
