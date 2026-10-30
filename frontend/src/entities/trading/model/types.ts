/** Контракт темы `home` (src/tb/dash2/topics.py): деньги, лимиты, позиции и платформа для выбранного режима. */
export type TradingMode = 'paper' | 'live' | 'testnet' | 'all'
export type Level = 'ok' | 'warn' | 'bad' | 'off' | null

export interface PlatformItem {
  id: string
  label: string
  level: Level
  value: string | null
}

export interface Money {
  under_risk: number | null
  day_room: number | null
  day_limit: number | null
  pnl_today: number
  trades_today: number
  open_pnl: number | null
  equity: number | null
  excluded: number
}

export interface LimitRow {
  id: string
  label: string
  limit: number | null
  used: number | null
  room: number | null
  unit: string | null
  level: 'ok' | 'warn' | 'bad'
}

export interface Target {
  price: number
  state: string | null
}

export interface Position {
  plan: string | null
  sym: string
  side: 'long' | 'short'
  mode: string | null
  lev: number | null
  qty: number | null
  avg: number | null
  price: number | null
  stale: boolean
  stop: number | null
  stop0: number | null
  targets: Target[]
  r: number | null
  upnl: number | null
  risk: number | null
  pnl_at_sl: number | null
  held_s: number | null
  hold_left_s: number | null
  liq: number | null
}

/** Накопленный результат закрытых сделок: [время закрытия (с), накопленный, результат сделки, монета]; base: накопленный до первой отданной точки (длинная история обрезана). */
export interface Curve {
  base: number
  points: Array<[number, number, number, string | null]>
  total: number
}

/** План, ожидающий входа (ближайшие по расстоянию до триггера). eta_at: момент закрытия свечи, которой ждёт условие (с). */
export interface NearPlan {
  id: string
  sym: string
  side: 'long' | 'short' | null
  mode: string | null
  col: 'waiting' | 'watching'
  text: string
  dist_pct: number | null
  eta_at: number | null
  entry_state: string | null
  blocked_why: string | null
  net_rr: number | null
  risk: number | null
}

export interface PlansBlock {
  counts: { waiting: number; watching: number; in_trade: number; closed: number }
  total: number
  near: NearPlan[]
}

/** Ждущий лимитный вход. dist_pct: на сколько процентов лимит отстоит от текущей цены (минус: лимит ниже цены). expires_at: когда заявка истечёт (с). */
export interface PendingOrder {
  id: string
  sym: string
  side: 'long' | 'short'
  mode: string | null
  price: number | null
  cur: number | null
  stale: boolean
  dist_pct: number | null
  sl: number | null
  tp: number | null
  risk: number | null
  lev: number | null
  ttl_min: number | null
  expires_at: number | null
}

export interface PendingBlock {
  total: number
  items: PendingOrder[]
}

export interface HomeSnapshot {
  mode: TradingMode
  modes: TradingMode[]
  platform: { state: string | null; mode: string | null; trading: boolean | null; level: Level; items: PlatformItem[] }
  money: Money
  limits: LimitRow[]
  limits_paper: boolean
  allowed: boolean | null
  positions: Position[]
  positions_total: number
  curve?: Curve
  plans?: PlansBlock
  pending?: PendingBlock
}
