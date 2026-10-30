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
  /** результат к марже, %: после комиссии входа, как в журнале сделок */
  pnl_pct: number | null
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

/** Закрытая сделка из журнала (тема `trades`, src/tb/dash2/topics.py). pnl: USDT после комиссий, pct: к марже, r: в рисках. */
export interface TradeCard {
  id: string
  sym: string | null
  side: 'long' | 'short' | null
  lev: number | null
  mode: string | null
  venue: string | null
  stype: string | null
  pnl: number | null
  pct: number | null
  move_pct: number | null
  r: number | null
  entry: number | null
  exit: number | null
  reason: string | null
  ts_open: number | null
  ts_close: number | null
  margin: number | null
  fees: number | null
  funding: number | null
  risk: number | null
  sl0: number | null
  sl: number | null
  path: { mae_pct: number | null; mfe_pct: number | null; mae_r: number | null; mfe_r: number | null; mfe_to_tp: number | null; path_coverage: number | null } | null
  err: string[]
  rule_followed: string | null
  tags: string[]
  lesson: string | null
  thesis: string | null
  note: string | null
  plan_id: string | null
  exp: string | null
  var: string | null
  setup: string | null
  hyp: string | null
  adds: number
  parts: number
  realized_parts: number | null
}

export interface TradeGroup {
  name: string
  n: number
  pnl: number | null
}

export interface TradesSnapshot {
  mode: TradingMode
  /** всего закрытых сделок в журнале режима (в items самые свежие) */
  total: number
  items: TradeCard[]
  reasons: TradeGroup[]
  errs: TradeGroup[]
  unreviewed: number
}

/** Запись ленты решений (тема `decisions`, model2.decision_row). Тексты уже обрезаны и очищены сервером; args, basis и res плоские: ключ -> число, строка, булево. */
export interface DecisionItem {
  seq: number
  ts: string
  t: number | null
  actor: string
  plan: string | null
  rev: number | null
  kind: string
  what: string
  action: string
  why: string
  args: Record<string, unknown>
  basis: Record<string, unknown>
  res: Record<string, unknown>
  ok: boolean | null
  code: string | null
  denied: boolean
  noop: boolean
  idle: boolean
  px: number | null
  r: number | null
  brief_id: string | null
  link_id: string | null
  action_id: string | null
  exch_ms: number | null
  h: string
  mode: string | null
}

export interface DecisionDay {
  n: number
  brain: number
  rejects: number
  auto: number
  skips: number
  protect: number
  reject_codes: Array<{ code: string; n: number }>
}

export interface DecisionsSnapshot {
  mode: TradingMode
  total: number
  items: DecisionItem[]
  plans: string[]
  day: DecisionDay | null
}

export interface ModelRow {
  id: string
  calls: number
  usd: number
  err_rate: number | null
  fmt_err_rate: number | null
  lat_p50_ms: number | null
  n: number
  wins: number
  pnl: number
  avg_r: number | null
  net: number
  pnl_per_100_calls: number | null
  pnl_per_usd: number | null
  reliable: boolean
}

export interface ModelsPeriod {
  days: number
  min_n: number
  rows: ModelRow[]
  note: string
}

export interface ModelsSnapshot {
  mode: TradingMode
  periods: Record<string, ModelsPeriod>
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
