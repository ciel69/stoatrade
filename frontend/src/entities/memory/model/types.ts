/**
 * Контракт вкладки «Память» (src/tb/dash2/memory_ops.py, схемы закреплены в tests/test_dash2_memory.py).
 * Ответ операции приходит темой `<op>_detail` только этому соединению; обёртка {ok, state, reason}, числа {v, n, small}.
 */

export const MEMORY_OPS = ['memory_cases', 'memory_lessons', 'memory_rules', 'memory_path'] as const
export type MemoryOp = (typeof MEMORY_OPS)[number]

/** Состояние ответа сервера: ok, нет таблиц или файлов (no_data), база восстанавливается, ошибка чтения. */
export type MemoryState = 'ok' | 'no_data' | 'restoring' | 'error'

export interface Num {
  v: number | null
  n: number
  small: boolean
}

export interface Effect extends Num {
  lo: number | null
  hi: number | null
}

export interface Envelope {
  ok: boolean
  state: MemoryState
  reason: string | null
  error?: string
}

// ---------------------------------------------------------------- memory_cases

export interface CaseRow {
  case_id: string | null
  ts: string | null
  type: string | null
  mode: string | null
  sym: string | null
  side: string | null
  setup: string | null
  action: string | null
  status: string | null
  r: number | null
  rulebook_ver: string | null
  eligible: boolean
}

export interface ModeRow {
  mode: string
  n: number
  mean_r: Num
  win_share: Num
}

export interface CasesDetail extends Envelope {
  total: number
  by_type: Record<string, number>
  by_status: Record<string, number>
  modes: ModeRow[]
  recent: CaseRow[]
  similar: { state: string; reason: string }
}

// ---------------------------------------------------------------- memory_lessons

export interface TrialBrief {
  trial_id: string | null
  rule_id: string | null
  state: string | null
  verdict: string | null
  start_ts: number | null
  deadline_ts: number | null
  closed_ts: number | null
}

export interface RuleBrief {
  rule_id: string | null
  rev: number | null
  kind: string | null
  status: string | null
  wait: string | null
  reason_code: string | null
  from_lesson: string | null
  ver_in: string | null
  ver_out: string | null
  cond_txt: string | null
  cond_h: string | null
  env_norm: string | null
  ts: string | null
  in_version: boolean
}

export interface LessonRow {
  lesson_id: string | null
  src: 'labels' | 'brain'
  status: string | null
  reason_code: string | null
  txt: string | null
  cond_txt: string | null
  claim: { metric: string | null; dir: string | null; vs: string | null }
  created_by: string | null
  conf: number | null
  n: number
  effect: Effect
  p: number | null
  ts: string | null
  expires_ts: number | null
  trial: TrialBrief | null
  rule: RuleBrief | null
  can_reject: boolean
}

export interface LessonsDetail extends Envelope {
  db: MemoryState
  db_reason: string | null
  total: number
  by_status: Record<string, number>
  by_src: Record<string, number>
  small_n: number
  rows: LessonRow[]
}

// ---------------------------------------------------------------- memory_rules

export interface ApplyMode {
  apply: boolean
  why: string | null
  ver: string | null
}

export interface VersionRow {
  ver: string | null
  parent: string | null
  reason_code: string | null
  rule_ids: Array<string | null>
  trial_id: string | null
  ts: string | null
}

export interface WaitingRow {
  rule_id: string | null
  wait: 'READY' | 'MAX_ACTIVE' | 'LOSS_CAP' | string
  since_ts: number | null
  waited_s: number | null
  deadline_ts: number | null
  left_s: number | null
  from_lesson: string | null
  cond_txt: string | null
}

export interface FeatMissing {
  window_days: number
  all: Num
  by_rule: Array<Num & { rule_id: string | null; missing: number }>
}

export interface NoEvent extends Num {
  window_days: number
  threshold: number
  yellow: boolean
}

export interface RulesDetail extends Envelope {
  mode: ApplyMode
  current?: string | null
  versions?: VersionRow[]
  versions_total?: number
  counts?: Record<string, number>
  active?: RuleBrief[]
  waiting?: WaitingRow[]
  rules?: RuleBrief[]
  rules_total?: number
  feat_missing?: FeatMissing
  no_event?: NoEvent
}

// ---------------------------------------------------------------- memory_path

export interface Funnel {
  candidates: number
  hyp: number
  trial: number
  confirmed: number
  rule_active: number
}

export interface PathRow {
  candidate: {
    id: number | null
    menu_id: string | null
    src: string | null
    status: string | null
    code: string | null
    p_screen: number | null
    holdout_start: number | null
    holdout_end: number | null
    ts: number | null
  }
  lesson: { lesson_id: string | null; status: string | null; txt: string | null } | null
  trial: TrialBrief | null
  rule: RuleBrief | null
  version: { in: string | null; in_ts: string | null; out: string | null; out_ts: string | null; out_reason: string | null } | null
}

export interface PathDetail extends Envelope {
  funnel: Funnel | null
  paths: PathRow[]
  lesson_id?: string | null
}

export interface MemoryDetails {
  memory_cases: CasesDetail
  memory_lessons: LessonsDetail
  memory_rules: RulesDetail
  memory_path: PathDetail
}

/** Что показывает блок: загрузка, нет связи, ответ (с его state) или ответ, который не удалось разобрать. */
export type BlockState = 'loading' | 'link' | MemoryState
