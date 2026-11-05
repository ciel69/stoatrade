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
  /** PF бесконечен (нет убыточных сделок): тогда pf пуст */
  pf_inf?: boolean
  n_runs?: number
  alpha_adj?: number | null
  k?: number
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

/** R4: версия политики выхода и её сравнение с X-0001 (Requirement 17.1–17.2, 17.6; сервер: src/tb/learnloop/exits_report.py, блок src/tb/learn/exits.py). */
export interface ExitsVersion { ver: string; parent: string | null; reason_code: string | null; actor: string | null; ts: number | null; policy_h: string | null }
export interface ExitsDiff { mean: number | null; lo: number | null; hi: number | null }
export interface ExitsR4D extends ExitsDiff { n_blocks: number; n_paths: number }
export interface ExitsR4Db extends ExitsDiff { n: number }
export interface ExitsR4Cap { fact: number | null; x0001: number | null; n: number; mfe_sum: number | null; r_fact_sum: number | null; r_x0001_sum: number | null }
export interface ExitsR4Costs { cur: number | null; x0001: number | null; n: number }
export interface ExitsR4Trial { trial_id: string | null; var_id: string | null; var_ver: number | null; base_ver: string | null; state: string | null; verdict: string | null; start_ts: number | null; deadline_ts: number | null; numbers: Record<string, unknown> | null }
export interface ExitsR4Watch { ts: number | null; ver: string | null; prev: string | null; codes: string[]; points: number; last: Record<string, unknown> | null; sim_gap: Record<string, unknown> | null }
export type ExitsR4State = 'few_data' | 'no_difference' | 'confirmed'
export interface ExitsR4 {
  status: 'ok' | 'few_data' | 'no_data'
  reason: string | null
  note: string
  mode: string | null
  since: number | null
  version: ExitsVersion | null
  d: ExitsR4D | null
  d_b: ExitsR4Db | null
  cap: ExitsR4Cap | null
  costs: ExitsR4Costs | null
  trials: { open: number; closed: number; rows: ExitsR4Trial[] }
  watch: ExitsR4Watch | null
  verdict: { state: ExitsR4State; text: string }
}

export interface ExitsBlock extends LearningBlock {
  windows: Record<string, ExitsWindow>
  series: Record<string, Array<{ i: number; ts: number | null; capture: Est | null; mfe: Est | null }>>
  all?: ExitsWindow
  r4?: ExitsR4
}

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

/** Блок «Уроки» (R2.6, src/tb/learn/stages.py: lessons_block). Уроки меток — по env_norm режима, уроки мозга — по всем режимам (brain_modes = all). */
export interface LessonsByStatus { status: string; brain: number; labels: number; total: number }
export interface LessonsFunnel { candidates: number; hyp: number; trials: number; trials_closed: number; confirmed: number; rules: number }
export interface LessonsWeek { week: string; partial: boolean; created_brain: number; created_labels: number; confirmed: number; rejected: number }
export interface BestLesson {
  lesson_id: string | null; src: 'labels' | 'brain' | string; status: string | null; n: number; small: boolean
  /** модуль claim.effect */
  effect: number | null; metric: string | null; dir: string | null; vs: string | null
  /** разность урока с 95% t-интервалом */
  diff: Est | null; text: string
}
export interface LessonsBlock extends LearningBlock {
  env_norm?: string; brain_modes?: string; n_lessons?: number; best_min_n?: number
  by_status?: LessonsByStatus[]; by_source?: { brain: number; labels: number }; funnel?: LessonsFunnel; weeks?: LessonsWeek[]; best?: BestLesson | null
}

/** Блок «Версии мозга до/после» (R2.6, stages.versions_block). Сторона сравнения: сделки, средний R, доля нарушений. */
export interface VersionSide { n: number; r: Est | null; viol: Est | null; n_rf: number; small: boolean }
export type VersionVerdict = 'few_data' | 'worse' | 'not_worse'
export interface VersionPairBase { ts: number | null; before: VersionSide; after: VersionSide; diff: Est | null; p: number | null; verdict: VersionVerdict }
export interface RulebookPair extends VersionPairBase { kind: 'rulebook'; from: string | null; to: string | null; reason_code: string | null; rules_n: number }
export interface SwapPair extends VersionPairBase { kind: 'model' | 'prompt'; kinds: string[]; seqs: number[]; targets: string[]; to: string[] }
export interface VersionsBlock extends LearningBlock {
  mode?: string; min_n?: number; lvl?: number; n_trades?: number; n_pre_r2?: number; rulebook_vers?: string[]; rulebook?: RulebookPair[]; swaps?: SwapPair[]
}

/** Блок «Гипотезы» (R2, Requirement 19.6; stages.hyps_block): бумажные сделки по семействам, справка меток — ориентир, статус не меняет. */
export type HypStatus = 'confirmed' | 'accumulating' | 'not_confirmed'
export interface HypRef { policy: string | null; policy_ver: number | null; kinds: string[]; env_norm: string; window_days: number | null; n: number; r: Est | null }
export interface HypFamily {
  family: string; n: number; n_target: number; small: boolean; r: Est | null; win: number | null; p: number | null; status: HypStatus; confirmable: boolean; ref: HypRef | null
}
export interface HypsBlock extends LearningBlock {
  mode?: string; env_norm?: string; min_n?: number; lvl?: number; k?: number; alpha_adj?: number | null; n_trades?: number; n_confirmed?: number; ref_ts?: number | null
  families?: HypFamily[]
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
