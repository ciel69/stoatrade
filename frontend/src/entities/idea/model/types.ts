export type IdeaStatus = 'idea' | 'watching' | 'planned' | 'taken' | 'skipped' | 'expired' | 'invalidated'
export type IdeaTab = 'active' | 'taken' | 'skipped' | 'auto'

/** Что было бы через горизонт (минуты): ход цены, ход по стороне идеи и он же после издержек. */
export interface IdeaOutcome {
  ret_pct: number | null
  dir_pct: number | null
  net_pct: number | null
}

export interface IdeaTrade {
  id: string
  status: 'open' | 'closed'
  pnl: number | null
  r: number | null
  reason: string | null
}

export interface IdeaItem {
  id: string
  sym: string
  side: 'long' | 'short' | null
  status: IdeaStatus
  note: string | null
  tag: string | null
  level: number | null
  stop: number | null
  tp: number | null
  price: number | null
  now_price: number | null
  /** расстояние цены до уровня, %: (цена / уровень − 1) × 100 */
  dist_level_pct: number | null
  shift_pct: number | null
  /** цена в пределах 0,3 % от уровня и идея ещё активна */
  zone: boolean
  ts: string | null
  ts_epoch: number | null
  expires_epoch: number | null
  plan_id: string | null
  trade_id: string | null
  trade: IdeaTrade | null
  mark_notes: string[]
  outcomes: Record<string, IdeaOutcome>
}

/** Ячейка статистики: продолжение (cont) и откат (fade) после горизонта, до и после издержек, доля плюсовых. */
export interface StatCell {
  n: number
  cont_gross?: number
  cont_net?: number
  cont_win?: number
  fade_gross?: number
  fade_net?: number
  fade_win?: number
}

export interface StatGroup {
  dim: string
  name: string
  events: number
  h: Record<string, StatCell>
}

export interface IdeaStats {
  costs: { taker_rt_pct: number }
  horizons: number[]
  groups: StatGroup[]
  idea_groups: StatGroup[]
  n_events: number
  agent: { total: number; taken_share: number | null }
  pending: number
  comparisons: number
}

export interface AutoEvent {
  id: string
  sym: string | null
  type: string | null
  dir: 'up' | 'down' | null
  ts0: number | null
  h: Record<string, IdeaOutcome>
}

export interface IdeasSnapshot {
  available: boolean
  counts: Record<string, number>
  items: IdeaItem[]
  stats: IdeaStats
  auto_recent: AutoEvent[]
  pending: number
}
