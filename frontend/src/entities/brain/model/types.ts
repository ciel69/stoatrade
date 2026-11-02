/** Контракт темы `brain` (src/tb/dash2/topics.py). Время в секундах epoch; возрасты считает клиент. */
export type BrainStatus = 'down' | 'protect' | 'thinking' | 'idle'

export interface ModelRef {
  id: string | null
  provider: string | null
  model: string | null
  enabled: boolean | null
  missing: boolean
}

export interface RoleLast {
  profile: string | null
  provider: string | null
  ok: boolean | null
  err: string | null
  lat_ms: number | null
  t: number
  fallback_from: string | null
}

export interface RoleRow {
  role: string
  primary: ModelRef | null
  fallback: Array<ModelRef | null>
  last: RoleLast | null
  today: { usd: number; calls: number; errors: number; budget_left_usd: number | null } | null
  live_risk: boolean | null
}

export interface DecisionRow {
  seq: number
  t: number
  ts: string
  what: 'brain_decision' | 'brain_why'
  kind: string
  role: string | null
  profile: string | null
  plan: string | null
  sym: string | null
  decision: string | null
  tool: string | null
  reason: string | null
  code: string | null
  ok: boolean | null
  text: string | null
}

export interface ActiveModel {
  profile: string | null
  provider: string | null
  model: string | null
  role: string | null
  ok: boolean | null
  t: number
}

export interface BrainSnapshot {
  status: BrainStatus
  alive: boolean
  protect_reason: string | null
  health: 'green' | 'yellow' | 'red' | null
  rev: number | null
  inflight: number
  queue: Record<string, number>
  cooling: string[]
  budget: string | null
  errors: number | null
  active: ActiveModel | null
  roles: RoleRow[]
  decisions: DecisionRow[]
}
