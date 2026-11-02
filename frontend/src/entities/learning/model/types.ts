/** Контракт ответа `learning_detail` на операцию `learning` (схема `tb.learning/1`; сервер: src/tb/learn/report.py, src/tb/dash2/topics.py). Эталоны: tests/fixtures_learning/*.json. */

export type LearningMode = 'paper' | 'testnet' | 'live'
export const LEARNING_MODES: readonly LearningMode[] = ['paper', 'testnet', 'live']
export const isLearningMode = (v: unknown): v is LearningMode => typeof v === 'string' && (LEARNING_MODES as readonly string[]).includes(v)

export type BlockStatus = 'ok' | 'few_data' | 'no_data' | 'stage' | 'computing' | 'error'
export const BLOCK_STATUSES: readonly BlockStatus[] = ['ok', 'few_data', 'no_data', 'stage', 'computing', 'error']

/** Оценка: значение с интервалом. Интервал null, если n < 2 или разброс нулевой; n остаётся. */
export interface Est { v: number | null; lo: number | null; hi: number | null; n: number; lvl: number }

export type VerdictState = 'few_data' | 'no_difference' | 'confirmed'
export type Trend = 'up' | 'flat' | 'down'

export interface VerdictGuards {
  viol: { ok: boolean | null; guard_unknown?: boolean; [k: string]: unknown } | null
  trades_day: { ok: boolean | null; [k: string]: unknown } | null
}

export interface LearningVerdict {
  state: VerdictState
  reason: string
  text: string
  n: number
  n_recent: number | null
  n_prev: number | null
  diff_r: Est | null
  p: number | null
  alpha_adj: number
  trend: Trend | null
  guards: VerdictGuards | null
  params?: Record<string, number> | null
}

/** Общие поля любого блока; данные блока лежат рядом и разбираются виджетом по типам ниже. */
export interface LearningBlock {
  id: string
  title: string
  status: BlockStatus
  note: string
  reason?: string | null
  stage?: number | null
  roadmap?: string | null
  [k: string]: unknown
}

export interface WindowStat {
  n: number
  r: Est | null
  win: Est | null
  pf: Est | null
  pf_inf: boolean
  fees_share: number | null
  small: boolean
}
export interface SeriesPoint { i: number; ts: number | null; n: number; r: Est | null; pf: number | null }
export interface ResultBlock extends LearningBlock {
  windows: Record<string, WindowStat>
  all: WindowStat | null
  series: Record<string, SeriesPoint[]>
  signal: Record<string, boolean>
}

export interface PolicyRow {
  id: string
  ver: number
  name: string
  baseline: boolean
  ctx_changed: boolean
  n_filled: number
  fill_rate: number | null
  r: Est | null
  pf: number | null
  diff: Est | null
  p: number | null
  verdict: 'better' | 'worse' | 'unclear' | 'few_data'
}
export interface VsPolicyBlock extends LearningBlock {
  since: number | null
  caveats: string[]
  k: number
  alpha_adj: number | null
  brain: { n: number; r: Est | null } | null
  rows: PolicyRow[]
  summary: 'above_baseline' | 'below_baseline' | 'unclear' | 'few_data'
}

export interface DisciplineWeek { week: string; partial: boolean; n_cases: number; viol: Est | null; n_rf: number; err_any: Est | null; err_top: Array<[string, number]> }
export interface DisciplineBlock extends LearningBlock { weeks: DisciplineWeek[]; err_total: Array<{ tag: string; share: Est | null; n: number }>; signal: string | null }

export interface SkipsWeek { week: string; partial?: boolean; n: number; correct: Est | null; missed_pct: Est | null; trades: number }
export interface SkipsBlock extends LearningBlock { weeks: SkipsWeek[]; total: Record<string, unknown> | null; signal: string | null }

export interface ExitsWindow { n_path: number; path_share: number | null; mfe: Est | null; capture: Est | null; small?: boolean }
export interface ExitsBlock extends LearningBlock { windows: Record<string, ExitsWindow>; series: Record<string, Array<{ i: number; ts: number | null; capture: Est | null; mfe: Est | null }>> }

export interface EconomicsWeek {
  week: string; partial?: boolean; calls: Est | number | null; tok_in: number | null; tok_out: number | null; llm_usd: number | null
  n_trades: number; r_sum: number | null; tok_per_trade: number | null; usd_per_trade: number | null; usd_per_r: number | null
}
export interface EconomicsBlock extends LearningBlock { scope: string; weeks: EconomicsWeek[]; signal: string | null }

export interface MemoryBlock extends LearningBlock {
  n_trades: number; n_eval: number; covered: Est | null; brier_mem: number | null; brier_base: number | null; d_brier: Est | null
  acc_mem: number | null; acc_base: number | null; better: boolean | null
  history: { status: BlockStatus; stage?: number; roadmap?: string; note?: string } | null
}

export interface LearningMeta {
  built_ms: number | null
  cached: boolean
  excluded: Record<string, number>
  warnings: string[]
  sources: Record<string, unknown>
}

export interface LearningDetail {
  schema: 'tb.learning/1'
  ok: boolean
  mode: LearningMode
  asof: number | null
  error?: string
  min_n: number
  alpha: number
  verdict: LearningVerdict
  blocks: Record<string, LearningBlock>
  order: string[]
  meta: LearningMeta
}
