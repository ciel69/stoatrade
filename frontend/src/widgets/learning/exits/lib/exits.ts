import { estView, type Est, type EstView, type ExitsBlock, type ExitsWindow } from '@/entities/learning'
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
