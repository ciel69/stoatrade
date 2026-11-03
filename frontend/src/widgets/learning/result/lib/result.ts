import { estView, type LearningBlock, type ResultBlock, type SeriesPoint, type Tone, type WindowStat } from '@/entities/learning'
import {
  LEARN_DERIVED, LEARN_RESULT_PF_INF, LEARN_RESULT_SIGNAL, LEARN_RESULT_STAT, LEARN_RESULT_STAT_HINT, LEARN_SMALL, LEARN_WINDOWS,
} from '@/shared/lib'
import type { BandPoint } from '@/shared/ui'

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)

/** Блок «Результат» из общего блока: null, если в нём нет окон (сервер прислал заглушку или пустоту). */
export function asResult(b: LearningBlock | null | undefined): ResultBlock | null {
  if (!b || !isObj(b.windows)) return null
  return b as ResultBlock
}

/** Какие окна переключателя есть в данных (порядок 50, 200). */
export function windowKeys(r: ResultBlock): string[] {
  return LEARN_WINDOWS.filter((w) => isObj(r.windows[w]))
}

/** Ряд для графика: точки без значения отбрасываются, интервал берётся из оценки (null означает «интервала нет»). */
export function chartPoints(series: SeriesPoint[] | null | undefined): BandPoint[] {
  const out: BandPoint[] = []
  for (const p of series ?? []) {
    const r = p.r
    if (!r || typeof r.v !== 'number' || !Number.isFinite(r.v)) continue
    out.push({ i: p.i, v: r.v, lo: r.lo, hi: r.hi, n: p.n })
  }
  return out
}

export type Where = keyof typeof LEARN_RESULT_SIGNAL
/** Где интервал среднего R относительно нуля; выше нуля подтверждается и флагом `signal` сервера. */
export function whereOfZero(ws: WindowStat | null | undefined, flag: boolean | undefined): Where {
  const r = ws?.r
  const has = !!r && typeof r.lo === 'number' && typeof r.hi === 'number'
  if (!has) return 'none'
  if (flag === true || (r!.lo as number) > 0) return 'above'
  if ((r!.hi as number) < 0) return 'below'
  return 'zero'
}

export interface Stat {
  id: 'r' | 'win' | 'pf' | 'fees'
  label: string
  hint: string
  value: string
  interval: string
  /** «n = 38» */
  n: string
  /** «мало данных (n < 30)», «расчётная величина» либо пусто */
  note: string
  tone: Tone
}

/** Четыре числа окна: средний R, доля прибыльных, PF, доля издержек. У каждого n; у доли издержек интервала нет, вместо него пометка «расчётная величина». */
export function statsOf(ws: WindowStat | null | undefined, minN = 30): Stat[] {
  if (!ws) return []
  const small = ws.small || ws.n < minN
  const r = estView(ws.r, 'r', 2, minN)
  const win = estView(ws.win, 'pct', 0, minN)
  const pf = estView(ws.pf, 'plain', 2, minN)
  const fee = ws.fees_share
  const smallNote = small ? LEARN_SMALL(minN) : ''
  return [
    { id: 'r', label: LEARN_RESULT_STAT.r, hint: LEARN_RESULT_STAT_HINT.r, value: r.value, interval: r.interval, n: r.n || `n = ${ws.n}`, note: smallNote, tone: r.tone },
    { id: 'win', label: LEARN_RESULT_STAT.win, hint: LEARN_RESULT_STAT_HINT.win, value: win.value, interval: win.interval, n: win.n || `n = ${ws.n}`, note: smallNote, tone: '' },
    {
      id: 'pf', label: LEARN_RESULT_STAT.pf, hint: LEARN_RESULT_STAT_HINT.pf, value: ws.pf_inf ? LEARN_RESULT_PF_INF : pf.value, interval: ws.pf_inf ? '' : pf.interval,
      n: pf.n || `n = ${ws.n}`, note: smallNote, tone: '',
    },
    {
      id: 'fees', label: LEARN_RESULT_STAT.fees, hint: LEARN_RESULT_STAT_HINT.fees, value: estView({ v: fee, lo: null, hi: null, n: ws.n, lvl: 0 }, 'pct', 0, minN).value, interval: '',
      n: `n = ${ws.n}`, note: LEARN_DERIVED, tone: '',
    },
  ]
}

export interface TableRow { x: string; r: string; interval: string; n: string }
/** Строки таблицы-альтернативы графику: то же, что слышит читалка. */
export function tableRows(points: BandPoint[]): TableRow[] {
  return points.map((p) => {
    const e = estView({ v: p.v, lo: p.lo ?? null, hi: p.hi ?? null, n: p.n ?? 0, lvl: 0.95 }, 'r', 2, 0)
    return { x: String(p.i), r: e.value, interval: e.interval || 'нет', n: p.n != null ? String(p.n) : '–' }
  })
}
