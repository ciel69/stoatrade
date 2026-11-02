import type { ExpConfirmed, ExpDetail, ExpMeta, ExpRow, ExpTrade } from '../model/types'

/** Образцы для отладки без сервера: ?exp=mock (идёт набор выборки), mock-confirmed (есть подтверждённые гипотезы), mock-empty (закрытых сделок нет). */
export type ExpMock = 'mock' | 'mock-empty' | 'mock-confirmed'
export const EXP_MOCKS: readonly string[] = ['mock', 'mock-empty', 'mock-confirmed']
export const isExpMock = (v: string | null | undefined): v is ExpMock => !!v && EXP_MOCKS.includes(v)

const T0 = 1793000000          // фиксированный момент, чтобы образец не менялся между показами

function trades(name: string, n: number, avg: number, mode: string): ExpTrade[] {
  const syms = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT']
  return Array.from({ length: Math.min(n, 25) }, (_, i) => {
    const r = Math.round((avg + (((i * 7 + name.length) % 9) - 4) * 0.35) * 100) / 100
    return { id: `T-${name.length}${i}-${mode[0]}`, sym: syms[(i + name.length) % syms.length], side: i % 3 ? 'long' : 'short', mode, r, pnl: Math.round(r * 0.18 * 1000) / 1000,
      ts: T0 - i * 5400, reason: r > 0 ? (i % 2 ? 'tp' : 'trail') : 'sl' }
  })
}

function row(name: string, mode: string, n: number, avg: number, win: number, p: number | null, o: Partial<ExpRow> = {}): ExpRow {
  const small = n < 30
  return {
    name, mode, n, wins: Math.round(n * win), win_rate: win, avg_r: avg, se_r: 0.2, pf: avg > 0 ? 1.6 : 0.7, pf_inf: false, pnl: Math.round(avg * n * 0.18 * 100) / 100, fees: Math.round(n * 0.011 * 100) / 100,
    p: small ? null : p, small, below_alpha: false, confirmed: false, last_ts: T0, trades: trades(name, n, avg, mode), ...o,
  }
}

const META: Record<string, ExpMeta> = {
  'stop-width': { hypothesis: 'Более широкий стоп реже выбивает по шуму и даёт лучший средний результат.', prereg: null, started: '2026-10-27', min_n: 30 },
}

const CONFIRMED: ExpConfirmed[] = [
  { hyp: 'sweep-fade', n: 41, avg_r: 0.38, win_rate: 0.56, p: 0.0021, live_n: 3, live_avg_r: 0.21 },
  { hyp: 'range-edge', n: 34, avg_r: 0.27, win_rate: 0.53, p: 0.0038 },
]

function rowsFor(by: string, kind: ExpMock, mode: string): ExpRow[] {
  const conf = kind === 'mock-confirmed'
  const paper: Record<string, ExpRow[]> = {
    hyp: [
      row('sweep-fade', 'paper', conf ? 41 : 22, 0.38, 0.56, 0.0021, conf ? { below_alpha: true, confirmed: true } : {}),
      row('range-edge', 'paper', conf ? 34 : 17, 0.27, 0.53, 0.0038, conf ? { below_alpha: true, confirmed: true } : {}),
      row('news-spike', 'paper', 33, -0.12, 0.42, 0.41),
      row('momentum-ride', 'paper', 9, 0.6, 0.67, null),
      row('late-reclaim', 'paper', 2, -1, 0, null),
    ],
    exp: [row('stop-width', 'paper', 62, 0.11, 0.5, 0.18), row('entry-delay', 'paper', 28, -0.05, 0.46, null)],
    variant: [row('stop-width/wide', 'paper', 33, 0.2, 0.52, 0.09), row('stop-width/tight', 'paper', 29, 0.02, 0.45, null), row('entry-delay/late', 'paper', 14, -0.09, 0.43, null)],
    setup: [row('level_reclaim', 'paper', 44, 0.22, 0.52, 0.03), row('sweep_reject', 'paper', 31, 0.15, 0.5, 0.2), row('range_fade', 'paper', 12, -0.2, 0.4, null)],
    stype: [row('intraday', 'paper', 70, 0.12, 0.5, 0.11), row('scalp', 'paper', 31, -0.04, 0.47, 0.72), row('swing', 'paper', 4, 0.8, 0.75, null)],
    symbol: [row('BTCUSDT', 'paper', 38, 0.18, 0.53, 0.07), row('ETHUSDT', 'paper', 29, 0.05, 0.48, null), row('SOLUSDT', 'paper', 21, -0.15, 0.43, null), row('XRPUSDT', 'paper', 8, 0.3, 0.6, null)],
    side: [row('long', 'paper', 64, 0.1, 0.5, 0.2), row('short', 'paper', 32, 0.05, 0.48, 0.66)],
    reason: [row('sl', 'paper', 41, -1.02, 0, 0.0001, { below_alpha: true }), row('tp', 'paper', 38, 1.4, 1, 0.0001, { below_alpha: true }), row('trail', 'paper', 11, 0.4, 1, null), row('manual', 'paper', 6, -0.2, 0.33, null)],
    err: [row('late_entry', 'paper', 12, -0.7, 0.2, null), row('no_stop_move', 'paper', 5, -0.9, 0.2, null)],
    venue: [row('bybit', 'paper', 96, 0.09, 0.5, 0.16)],
  }
  const live: Record<string, ExpRow[]> = {
    hyp: [row('sweep-fade', 'live', 3, 0.21, 0.67, null)],
    symbol: [row('BTCUSDT', 'live', 3, 0.21, 0.67, null)],
    side: [row('long', 'live', 3, 0.21, 0.67, null)],
    reason: [row('tp', 'live', 2, 1.3, 1, null), row('sl', 'live', 1, -1, 0, null)],
    stype: [row('intraday', 'live', 3, 0.21, 0.67, null)],
    venue: [row('bybit', 'live', 3, 0.21, 0.67, null)],
  }
  const p = paper[by] ?? []
  const l = live[by] ?? []
  return mode === 'paper' ? p : mode === 'live' ? l : [...p, ...l]
}

export function mockExp(kind: ExpMock, by: string, mode: string): ExpDetail {
  const empty = kind === 'mock-empty'
  const rows = empty ? [] : rowsFor(by, kind, mode)
  const k = rows.length
  return {
    ok: true, by, mode, min_n: 30, alpha: 0.05, k, alpha_adj: k ? 0.05 / k : null, rows, rows_total: k, total_closed: empty ? 0 : 96, unreviewed: by === 'err' ? 41 : 0,
    meta: by === 'exp' || by === 'variant' ? META : {}, confirmed: kind === 'mock-confirmed' ? CONFIRMED : [],
  }
}
