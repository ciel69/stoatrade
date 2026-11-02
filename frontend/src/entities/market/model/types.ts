type N = number | null

/** Строка тепловой карты: топ монет по обороту (пишет сканер). Проценты в процентах, оборот и открытый интерес в млн USD. */
export interface HeatRow {
  sym: string
  tradable: boolean
  price: N
  chg_24h_pct: N
  ret_5m_pct: N
  ret_15m_pct: N
  range_5m_pct: N
  turn_surge_5m: N
  oi_chg_15m_pct: N
  oi_chg_1h_pct: N
  oi_musd: N
  funding_pct: N
  turn_24h_musd: N
  score: N
}

export interface LiqLevel {
  px: number
  long: number
  short: number
}

/** Ликвидации монеты за сутки и час (USD): long это выбитые лонги, short выбитые шорты; map по ценовым уровням. */
export interface LiqSym {
  sym: string
  long_5m: number
  short_5m: number
  long_1h: number
  short_1h: number
  n_1h: number
  px: N
  map: LiqLevel[]
}

export interface FlowWall {
  side: 'bid' | 'ask'
  px: number
  usd: number
  dist_pct: number
  stable: boolean
}

/** Ордерфлоу небольшого набора монет с живым потоком: агрессия покупателей и продавцов, перекос стакана, разрыв с Binance. */
export interface FlowSym {
  sym: string
  px: N
  cvd_5m_pct: N
  buy_5m_usd: N
  sell_5m_usd: N
  big_5m_n: N
  big_5m_usd: N
  bid_usd: N
  ask_usd: N
  imb_pct: N
  spread_bps: N
  xex_bps: N
  walls: FlowWall[]
}

export interface HotRow {
  sym: string
  price: N
  score: N
  reasons: string[]
  skip: string[]
  tradable: boolean
  rank: N
  chg_24h_pct: N
  ret_5m_pct: N
  range_5m_pct: N
  turn_24h_musd: N
  turn_surge_5m: N
  spread_bps: N
  lot_usdt: N
  max_leverage: N
}

/** Отслеживаемая монета фида (runtime/market.json): доходности долями, не процентами. */
export interface FeedCoin {
  sym: string
  price: N
  regime: string | null
  regime_1h: string | null
  ret_1m: N
  ret_5m: N
  ret_15m: N
  rv_5m: N
  rv_1h: N
  spread_bps: N
  imbalance: N
  funding_hourly: N
  oi_chg_5m: N
  prices_by_src: Record<string, number>
}

export interface MarketSnapshot {
  heat: { available: boolean; at: number | null; rows: HeatRow[] }
  liq: { available: boolean; at: number | null; totals: { long?: number; short?: number }; syms: LiqSym[] }
  flow: { available: boolean; at: number | null; xex_ok: boolean | null; syms: FlowSym[] }
  hot: { available: boolean; at: number | null; warm: boolean | null; stats: Record<string, number>; top: HotRow[]; blocked: HotRow[] }
  coins: { ok: boolean; at: number | null; rows: FeedCoin[] }
}
