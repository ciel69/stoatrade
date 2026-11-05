import { estView, toneOf } from '@/entities/learning'
import type { Est, EstView, ExitsBlock, ExitsR4, ExitsR4State, ExitsWindow, Tone } from '@/entities/learning'
import { signed } from '@/shared/lib'
import type { BandPoint } from '@/shared/ui'

export const CAPTURE_EXPLAIN = 'Доля забранного — какую часть лучшего хода цены в вашу сторону сделка забрала при выходе, по выигрышным сделкам. 100% — выход точно на самой выгодной точке.'
export const MFE_EXPLAIN = 'Лучший ход — насколько далеко цена уходила в вашу пользу за время сделки, в R (долях риска).'

/** Порядок и подписи окон: 50, 200, затем весь период. */
const WINDOW_TEXT: Readonly<Record<string, string>> = { '50': 'Последние 50', '200': 'Последние 200', all: 'Весь период' }
const windowOrder = (k: string): number => (k === '50' ? 0 : k === '200' ? 1 : k === 'all' ? 3 : 2)
export const windowText = (k: string): string => WINDOW_TEXT[k] ?? (/^\d+$/.test(k) ? `Последние ${k}` : k.replace(/[-_]+/g, ' '))

/** Доля забранного в отображении не выше 100%: переполнение из-за округления MFE показывать как 100% (расчёт на сервере не меняется). */
export function clampShare(e: Est | null | undefined): Est | null {
  if (!e) return null
  const c = (x: number | null): number | null => (typeof x === 'number' ? Math.min(1, x) : x)
  return { ...e, v: c(e.v), lo: c(e.lo), hi: c(e.hi) }
}

export interface ExitRow {
  key: string
  label: string
  capture: EstView
  mfe: EstView
  /** число сделок с известным путём */
  nPath: number
  /** «путь цены известен у 100% сделок» либо пусто */
  pathShare: string
  small: boolean
}

export function exitRows(windows: ExitsBlock['windows'], minN = 30): ExitRow[] {
  return Object.keys(windows ?? {})
    .sort((a, b) => windowOrder(a) - windowOrder(b))
    .map((k) => {
      const w: ExitsWindow = windows[k]!
      const share = typeof w.path_share === 'number' && Number.isFinite(w.path_share) ? `путь известен у ${Math.round(Math.min(1, Math.max(0, w.path_share)) * 100)}% сделок` : ''
      // MFE в R без знака «+»: это размер хода, а не результат
      const mfe = estView(w.mfe, 'plain', 2, 0)
      return {
        key: k, label: windowText(k), capture: estView(clampShare(w.capture), 'pct', 0, 0),
        mfe: { ...mfe, value: mfe.value === '–' ? '–' : `${mfe.value} R`, interval: mfe.interval },
        nPath: w.n_path, pathShare: share, small: w.n_path < minN,
      }
    })
}

/** Какой ряд рисовать: окно 50, а если там пусто, то 200, потом любое непустое. */
export function pickSeries(series: ExitsBlock['series']): { key: string; points: BandPoint[] } | null {
  const s = series ?? {}
  const keys = Object.keys(s).sort((a, b) => windowOrder(a) - windowOrder(b))
  for (const k of keys) {
    const pts = capturePoints(s[k])
    if (pts.length) return { key: k, points: pts }
  }
  return null
}

export function capturePoints(src: ReadonlyArray<{ i: number; capture: Est | null }> | undefined): BandPoint[] {
  const out: BandPoint[] = []
  for (const p of src ?? []) {
    const e = clampShare(p.capture)
    if (!e || typeof e.v !== 'number') continue
    out.push({ i: p.i, v: e.v, lo: e.lo, hi: e.hi, n: e.n })
  }
  return out
}

export const pctText = (x: number): string => `${Math.round(x * 100)}%`

export const R4_EMPTY_NOTE = 'Появится после первого продвижения.'

const DATE = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })
const DATE_TIME = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })
const withoutYearSuffix = (s: string): string => s.replace(/\s*г\.$/, '')

/** Разность в R: «+0.12R», «−0.12R», «0.00R», «–». */
export function rSigned(x: number | null): string {
  return typeof x === 'number' && Number.isFinite(x) ? `${signed(x, 2)}R` : '–'
}

/** Двусторонний интервал: «95%: +0.01…+0.23R»; без любой из границ пусто. */
export function ciText(lo: number | null, hi: number | null): string {
  if (typeof lo !== 'number' || !Number.isFinite(lo) || typeof hi !== 'number' || !Number.isFinite(hi)) return ''
  return `95%: ${signed(lo, 2)}…${signed(hi, 2)}R`
}

/** Доля в процентах: «42%», «–». */
export function shareText(x: number | null): string {
  if (typeof x !== 'number' || !Number.isFinite(x)) return '–'
  return `${Math.round(Math.min(1, Math.max(0, x)) * 100)}%`
}

/** Дата в секундах epoch: «31 октября 2026»; без метки пусто. */
export function dateText(ts: number | null): string {
  if (typeof ts !== 'number' || !Number.isFinite(ts)) return ''
  return withoutYearSuffix(DATE.format(new Date(ts * 1000)))
}

/** Дата и время сторожа в секундах epoch: «31 октября в 15:00». */
export function dateTimeText(ts: number | null): string {
  if (typeof ts !== 'number' || !Number.isFinite(ts)) return ''
  return DATE_TIME.format(new Date(ts * 1000))
}

export interface R4Pair { label: string; value: string }

export interface R4Row {
  key: 'd' | 'd_b' | 'cap' | 'costs'
  label: string
  value: string
  interval: string
  counter: string
  pair: R4Pair[]
  note: string
  tone: Tone
}

export interface R4View {
  empty: boolean
  note: string
  word: string
  tone: 'ok' | 'warn' | 'neutral'
  version: string
  reason: string
  text: string
  rows: R4Row[]
  trials: string
  watch: string
  codes: string
}

const R4_VERDICT: Readonly<Record<ExitsR4State, { word: string; tone: R4View['tone'] }>> = {
  few_data: { word: 'Данных мало', tone: 'warn' },
  no_difference: { word: 'Разницы не видно', tone: 'neutral' },
  confirmed: { word: 'Улучшение подтверждено', tone: 'ok' },
}

/** Готовые строки секции R4: вердикт, версия, четыре метрики, испытания и сторож. */
export function r4View(r4: ExitsR4 | null | undefined): R4View {
  const empty = !r4 || r4.status === 'no_data'
  if (empty) {
    return { empty: true, note: r4?.note?.trim() || R4_EMPTY_NOTE, word: '', tone: 'neutral', version: '', reason: '', text: '', rows: [], trials: '', watch: '', codes: '' }
  }

  const state = r4.verdict?.state
  const verdict = state ? R4_VERDICT[state] : R4_VERDICT.few_data
  const ver = r4.version
  const verDate = dateText(ver?.ts ?? r4.since)
  const version = ver?.ver ? `Версия ${ver.ver}${verDate ? ` от ${verDate}` : ''}` : ''

  const d = r4.d
  const dRow: R4Row = {
    key: 'd',
    label: 'Разность d по путям',
    value: rSigned(d?.mean ?? null),
    interval: ciText(d?.lo ?? null, d?.hi ?? null),
    counter: d ? `блоков: ${d.n_blocks}` : '',
    pair: [],
    note: !d && r4.mode !== 'paper' ? 'на этом режиме пути меток не сравниваются (метки — paper)' : '',
    tone: toneOf(d?.mean),
  }

  const db = r4.d_b
  const dbRow: R4Row = {
    key: 'd_b',
    label: 'Разность d_b по сделкам мозга',
    value: rSigned(db?.mean ?? null),
    interval: ciText(db?.lo ?? null, db?.hi ?? null),
    counter: db ? `сделок: ${db.n}` : '',
    pair: [],
    note: '',
    tone: toneOf(db?.mean),
  }

  const cap = r4.cap
  const capRow: R4Row = {
    key: 'cap',
    label: 'Забрано MFE (cap)',
    value: cap ? '' : '–',
    interval: '',
    counter: cap ? `сделок с MFE > 0: ${cap.n}` : '',
    pair: cap ? [{ label: 'факт', value: shareText(cap.fact) }, { label: 'X-0001', value: shareText(cap.x0001) }] : [],
    note: '',
    tone: '',
  }

  const costs = r4.costs
  const costsRow: R4Row = {
    key: 'costs',
    label: 'Издержки',
    value: costs ? '' : '–',
    interval: '',
    counter: costs ? `сделок: ${costs.n}` : '',
    pair: costs ? [{ label: 'факт', value: shareText(costs.cur) }, { label: 'X-0001', value: shareText(costs.x0001) }] : [],
    note: '',
    tone: '',
  }

  const trials = r4.trials
  const watch = r4.watch
  const watchAt = dateTimeText(watch?.ts ?? null)

  return {
    empty: false,
    note: r4.note?.trim() ?? '',
    word: verdict.word,
    tone: verdict.tone,
    version,
    reason: ver?.reason_code ?? '',
    text: r4.verdict?.text ?? '',
    rows: [dRow, dbRow, capRow, costsRow],
    trials: `Испытания: открыто ${trials?.open ?? 0}, закрыто ${trials?.closed ?? 0}`,
    watch: watch ? (watchAt ? `Сторож: проверка ${watchAt}, точек: ${watch.points}` : `Сторож: точек: ${watch.points}`) : 'Сторож ещё не проверял',
    codes: watch?.codes?.length ? `коды: ${watch.codes?.join(', ')}` : '',
  }
}
