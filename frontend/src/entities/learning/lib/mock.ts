import type {
  DisciplineBlock, EconomicsBlock, Est, ExitsBlock, HypFamily, HypsBlock, LearningBlock, LearningDetail, LearningMode, LearningVerdict, MemoryBlock, ResultBlock, SeriesPoint, SkipsBlock, VsPolicyBlock, WindowStat,
} from '../model/types'

/**
 * Образцы для отладки без сервера: ?learning=mock (разницы не видно), mock-few (данных мало), mock-confirmed (улучшение подтверждено),
 * mock-empty (журнал пуст), mock-computing (блок памяти считается). Числа выдуманы для показа и не относятся к реальной торговле.
 */
export type LearningMock = 'mock' | 'mock-few' | 'mock-confirmed' | 'mock-empty' | 'mock-computing'
export const LEARNING_MOCKS: readonly string[] = ['mock', 'mock-few', 'mock-confirmed', 'mock-empty', 'mock-computing']
export const isLearningMock = (v: string | null | undefined): v is LearningMock => !!v && LEARNING_MOCKS.includes(v)

const ASOF = 1793700000      // фиксированный момент, чтобы образец не менялся между показами
const WEEKS = ['2026-W37', '2026-W38', '2026-W39', '2026-W40', '2026-W41', '2026-W42', '2026-W43', '2026-W44']

const est = (v: number | null, half: number | null, n: number, lvl = 0.95): Est => ({ v, lo: v != null && half != null && n >= 2 ? v - half : null, hi: v != null && half != null && n >= 2 ? v + half : null, n, lvl })
const r4 = (x: number): number => Math.round(x * 10000) / 10000

function windowStat(n: number, r: number, pf: number): WindowStat {
  return { n, r: est(r, 2 / Math.sqrt(Math.max(n, 1)), n), win: est(0.5, 0.9 / Math.sqrt(Math.max(n, 1)), n), pf: est(pf, 0.5, n, 0.9), pf_inf: false, fees_share: 0.19, small: n < 30 }
}

function series(n: number, base: number): SeriesPoint[] {
  if (n < 30) return []
  return Array.from({ length: Math.min(n - 29, 60) }, (_, k) => {
    const i = 30 + k * Math.max(1, Math.floor((n - 30) / 59))
    const r = r4(base + Math.sin(k / 6) * 0.12 + k * 0.002)
    return { i, ts: ASOF - (n - i) * 5400, n: Math.min(i, 50), r: est(r, 0.55 - k * 0.003, Math.min(i, 50)), pf: r4(1 + r) }
  })
}

const stage = (id: string, title: string): LearningBlock => ({ id, title, status: 'stage', note: 'появится на этапе 2', stage: 2, roadmap: 'R2.6' })
const noData = (id: string, title: string, reason: string): LearningBlock => ({ id, title, status: 'no_data', note: '', reason })

function verdictFor(kind: LearningMock): LearningVerdict {
  const params = { min_n: 30, alpha: 0.05, k: 1, recent_n: 50, guard_trades_ratio: 0.5 }
  const guards = { viol: { ok: true, guard_unknown: false }, trades_day: { ok: true } }
  if (kind === 'mock-empty') return { state: 'few_data', reason: 'n_lt_min', text: 'Сделок 0: нужно ещё 30, чтобы делать выводы.', n: 0, n_recent: null, n_prev: null, diff_r: null, p: null, alpha_adj: 0.05, trend: null, guards: null, params }
  if (kind === 'mock-few') return { state: 'few_data', reason: 'n_lt_min', text: 'Сделок 12: нужно ещё 18, чтобы делать выводы.', n: 12, n_recent: null, n_prev: null, diff_r: null, p: null, alpha_adj: 0.05, trend: null, guards: null, params }
  if (kind === 'mock-confirmed') {
    return { state: 'confirmed', reason: '', text: 'Недавние 50 сделок лучше прежних 71: разница +0.34 R, случайность маловероятна. Нарушений не больше, сделок не меньше.', n: 121, n_recent: 50, n_prev: 71,
      diff_r: est(0.34, 0.27, 121, 0.95), p: 0.011, alpha_adj: 0.05, trend: 'up', guards, params }
  }
  return { state: 'no_difference', reason: 'not_significant', text: 'Недавние 50 сделок не отличаются от прежних 34 сверх случайного разброса: разница +0.06 R.', n: 84, n_recent: 50, n_prev: 34,
    diff_r: est(0.06, 0.41, 84, 0.95), p: 0.74, alpha_adj: 0.05, trend: 'flat', guards, params }
}

function weeksDiscipline(n: number): DisciplineBlock['weeks'] {
  return WEEKS.map((week, i) => ({
    week, partial: i === WEEKS.length - 1, n_cases: n ? 12 + ((i * 5) % 9) : 0, viol: n ? est(0.32 - i * 0.025, 0.2, 12 + ((i * 5) % 9)) : null, n_rf: n ? 11 : 0,
    err_any: n ? est(0.45 - i * 0.03, 0.22, 12 + ((i * 5) % 9)) : null, err_top: n && i % 2 ? [['late_entry', 3], ['no_stop_move', 1]] : [],
  }))
}

export function mockLearning(kind: LearningMock, mode: LearningMode): LearningDetail {
  const empty = kind === 'mock-empty'
  const few = kind === 'mock-few'
  const n = empty ? 0 : few ? 12 : kind === 'mock-confirmed' ? 121 : 84
  const verdict = verdictFor(kind)
  const base = kind === 'mock-confirmed' ? 0.3 : 0.02
  const okOrFew = few ? 'few_data' : 'ok'
  const blocks: Record<string, LearningBlock> = {}

  blocks.result = empty
    ? noData('result', 'Результат', 'no_trades')
    : ({ id: 'result', title: 'Результат', status: okOrFew, note: '', windows: { '50': windowStat(Math.min(n, 50), base, 1 + base), '200': windowStat(Math.min(n, 200), base, 1 + base) },
        all: windowStat(n, base, 1 + base), series: { '50': series(n, base), '200': series(n, base) }, signal: { '50': kind === 'mock-confirmed', '200': false } } as ResultBlock)

  blocks.vs_policy = empty
    ? noData('vs_policy', 'Мозг против теневой политики', 'labels_off')
    : ({ id: 'vs_policy', title: 'Мозг против теневой политики', status: okOrFew, note: '', since: ASOF - 14 * 86400, caveats: ['unpaired', 'selection', 'costs_model', 'correlated'], k: 2, alpha_adj: 0.025,
        brain: { n: Math.min(n, 38), r: est(base, 0.3, Math.min(n, 38)) },
        rows: [
          { id: 'P6', ver: 1, name: 'random_side_baseline', baseline: true, ctx_changed: false, n_filled: 410, fill_rate: 0.97, r: est(-0.03, 0.08, 410), pf: 0.95, diff: est(base + 0.03, 0.34, 38, 0.975), p: 0.31, verdict: few ? 'few_data' : 'unclear' },
          { id: 'P5', ver: 2, name: 'level_follow', baseline: true, ctx_changed: true, n_filled: 22, fill_rate: 0.8, r: est(0.01, 0.2, 22), pf: 1.02, diff: null, p: null, verdict: 'few_data' },
        ], summary: few ? 'few_data' : 'unclear' } as VsPolicyBlock)

  blocks.discipline = empty
    ? noData('discipline', 'Дисциплина', 'no_cases')
    : ({ id: 'discipline', title: 'Дисциплина', status: okOrFew, note: '', weeks: weeksDiscipline(n), err_total: [{ tag: 'late_entry', share: est(0.21, 0.1, 96), n: 96 }, { tag: 'no_stop_move', share: est(0.08, 0.06, 96), n: 96 }], signal: 'falling' } as DisciplineBlock)

  blocks.skips = empty
    ? noData('skips', 'Пропуски', 'no_whatif')
    : ({ id: 'skips', title: 'Пропуски', status: okOrFew, note: 'Упущенный ход в процентах цены после издержек: перевести в R нельзя, у пропуска нет стопа.',
        weeks: WEEKS.map((week, i) => ({ week, partial: i === WEEKS.length - 1, n: 40 + i * 3, correct: est(0.55 + i * 0.01, 0.14, 40 + i * 3), missed_pct: est(0.35, 0.2, 17), trades: 14 - (i % 4) })),
        total: null, signal: 'flat' } as SkipsBlock)

  blocks.exits = empty
    ? noData('exits', 'Выходы', 'no_path')
    : ({ id: 'exits', title: 'Выходы', status: okOrFew, note: '',
        windows: { '50': { n_path: Math.min(n, 50), path_share: 1, mfe: est(1.1, 0.3, Math.min(n, 50)), capture: est(0.42, 0.12, Math.min(n, 50)) }, '200': { n_path: n, path_share: 1, mfe: est(1.05, 0.2, n), capture: est(0.4, 0.09, n) } },
        series: { '50': [], '200': [] } } as ExitsBlock)

  blocks.economics = empty
    ? noData('economics', 'Экономика', 'no_usage')
    : ({ id: 'economics', title: 'Экономика', status: okOrFew, note: 'Расход считается по всей платформе, результат по выбранному режиму.', scope: 'platform',
        weeks: WEEKS.map((week, i) => ({ week, partial: i === WEEKS.length - 1, calls: 900 + i * 40, tok_in: 3000000 + i * 90000, tok_out: 400000, llm_usd: 0.9 + i * 0.05, n_trades: 10 + (i % 5), r_sum: i % 3 ? 1.2 : -0.8,
          tok_per_trade: 90000, usd_per_trade: 0.08, usd_per_r: i % 3 ? 0.7 : null })), signal: null } as EconomicsBlock)

  const hist = { status: 'stage' as const, stage: 3, roadmap: 'R3.4', note: 'история по дням появится на этапе 3' }
  blocks.memory = empty
    ? noData('memory', 'Память против базы', 'no_trades')
    : kind === 'mock-computing'
      ? { id: 'memory', title: 'Память против базы', status: 'computing', note: '' }
      : ({ id: 'memory', title: 'Память против базы', status: few ? 'few_data' : 'ok', note: '', n_trades: n, n_eval: few ? 4 : 52, covered: est(0.62, 0.12, n), brier_mem: 0.292, brier_base: 0.27,
          d_brier: est(-0.022, 0.06, few ? 4 : 52), acc_mem: 0.52, acc_base: 0.52, better: false, history: hist } as MemoryBlock)

  blocks.lessons = stage('lessons', 'Уроки')
  blocks.versions = stage('versions', 'Версии мозга до/после')
  // гипотезы считаются по бумаге и от таблиц цикла не зависят: в образцах есть всегда (кроме пустого журнала)
  const fam = (family: string, k: number, r: number, status: HypFamily['status'], ref: HypFamily['ref'] = null): HypFamily => ({
    family, n: k, n_target: 30, small: k < 30, r: est(k ? r : null, k >= 2 ? 1.6 / Math.sqrt(k) : null, k), win: k ? 0.5 + r / 3 : null, p: k >= 30 ? (r > 0.3 ? 0.004 : 0.4) : null,
    status, confirmable: family !== 'other', ref,
  })
  const liqRef = { policy: 'P4', policy_ver: 1, kinds: ['liq_cascade'], env_norm: 'mainnet', window_days: 14, n: 42, r: est(0.12, 0.15, 42) }
  blocks.hyps = empty
    ? noData('hyps', 'Гипотезы', 'no_trades')
    : ({ id: 'hyps', title: 'Гипотезы', status: few ? 'few_data' : 'ok', note: '', reason: few ? 'n_lt_min' : null, mode: 'paper', env_norm: 'mainnet', min_n: 30, lvl: 0.95,
        k: few ? 1 : 2, alpha_adj: few ? 0.05 : 0.025, n_trades: n, n_confirmed: kind === 'mock-confirmed' ? 1 : 0, ref_ts: ASOF - 1800,
        families: few
          ? [fam('range-fade', 7, 0.1, 'accumulating'), fam('liq-cascade-reclaim', 3, -0.2, 'accumulating', liqRef), fam('other', 2, 0.4, 'accumulating')]
          : [fam('range-fade', Math.min(n, 52), kind === 'mock-confirmed' ? 0.42 : 0.08, kind === 'mock-confirmed' ? 'confirmed' : 'not_confirmed'),
              fam('reclaim-level', 31, 0.05, 'not_confirmed'), fam('liq-cascade-reclaim', 18, 0.21, 'accumulating', liqRef), fam('other', 6, -0.3, 'accumulating'),
              fam('oi-build-squeeze', 0, 0, 'accumulating', { ...liqRef, policy: 'P2', kinds: ['oi_spike'], n: 9, r: est(-0.1, 0.5, 9) })] } as HypsBlock)

  return {
    schema: 'tb.learning/1', ok: true, mode, asof: ASOF, min_n: 30, alpha: 0.05, verdict, blocks, order: ['result', 'vs_policy', 'discipline', 'skips', 'exits', 'economics', 'memory', 'lessons', 'versions', 'hyps'],
    meta: { built_ms: 140, cached: false, excluded: { shadow: 0, manual: 2, no_plan: 0, no_r: 0 }, warnings: [], sources: { trades: n, cases: empty ? 0 : 120, labels_db: !empty } },
  }
}
