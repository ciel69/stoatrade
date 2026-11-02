type N = number | null

/** Строка ленты досье (тема `briefs`): сводка без содержимого. id вида B-ГГГГММДД-ЧЧММ-NN. */
export interface BriefMeta {
  id: string
  ts: string | null
  scope: string | null
  trunc: boolean
  /** разрешены ли входы по запасу риска на момент досье */
  allowed: boolean | null
  positions: number
  plans: number
}

export interface BriefList {
  total: number
  items: BriefMeta[]
}

export interface BriefTarget {
  id: string
  px: number
  state: string
}

export interface BriefPosition {
  plan?: string
  trade?: string
  sym: string
  side: 'long' | 'short'
  mode?: string
  avg: N
  px: N
  sl: N
  tp?: N
  liq: N
  r: N
  mae_r: N
  mfe_r: N
  age_min: N
  targets?: BriefTarget[]
  rules?: Array<{ id: string; state: string; need?: string }>
}

export interface BriefPlan {
  id: string
  sym: string
  side: 'long' | 'short'
  status?: string
  rev?: number
  exp?: string
  entry?: string
  dist_pct?: N
  ttl_left_min?: N
  own_thesis?: string
}

export interface BriefCoinStat {
  px?: N
  ret_5m_pct?: N
  ret_1h_pct?: N
  ret_24h_pct?: N
  range_5m_pct?: N
  funding_8h_pct?: N
  spread_bps?: N
  regime?: string
  regime_src?: string
}

export interface BriefStruct {
  reg?: string
  a5_pct?: N
  a1h_pct?: N
  r1h?: N
  r24h?: N
  dpos?: N
  sp?: N
  fu?: N
  px?: N
}

export interface BriefLevel {
  px?: N
  atr_5m?: N
  vol_med_5m?: N
  week?: { lo: number; hi: number }
  day?: { lo: number; hi: number }
  prev_day?: { lo: number; hi: number }
  swing_5m?: number[]
}

export interface BriefHot {
  sym: string
  score?: N
  reasons?: string[]
  px?: N
  ret_5m_pct?: N
  spread_bps?: N
  funding_pct?: N
  lot_usdt?: N
  tradable?: boolean
}

/** Досье после очистки сервером (белый список полей). Все блоки необязательны: досье бывает неполным (scope=event). */
export interface Brief {
  brief_id?: string
  ts?: string
  scope?: string
  trunc?: boolean
  system?: { exec?: { alive?: boolean; age_s?: N; env?: string; mode?: string; observe?: boolean; hold?: unknown; ws?: unknown; reconcile_age_s?: N }; kill?: boolean; scanner?: { alive?: boolean; age_s?: N }; feed?: { alive?: boolean }; unacked?: N }
  account?: { equity?: N; free?: N; dd_pct?: N; dd_mult?: N; day_pnl?: N; day_trades?: N; stops_today?: N; stop_streak?: N }
  session?: { id?: string; type?: string; min_left?: N; budget?: N; trades?: N; max_trades?: N; realized?: N; open_risk?: N }
  room?: { allowed?: boolean; reasons?: string[]; next_risk_usdt?: N; limited_by?: string; slots_free?: N; daily_room?: N; session_room?: N; cooldowns?: Array<{ sym: string; min_left: number }>; pause_min_left?: N }
  positions?: BriefPosition[]
  plans?: BriefPlan[]
  market?: { btc?: BriefCoinStat; eth?: BriefCoinStat; utc_session?: string; risk_off?: boolean; news_mode?: string; blackout?: { event: string; in_min: number; mode?: string } | null; context_stale_s?: N }
  struct?: { s?: Record<string, BriefStruct>; stale_s?: N }
  limits?: { risk_per_trade_usdt?: N; daily_loss_usdt?: N; daily_left_usdt?: N; max_open_positions?: N; max_new_plans?: N; size_mult?: N; slots?: N }
  risk_level?: { level?: N; carry?: { policy?: string; max_positions?: N } }
  economics?: { llm_usd_day?: N; trades_day?: N; trades_left?: N; min_trades_day?: N }
  hot?: BriefHot[] | { top?: BriefHot[]; stale_s?: N }
  intel?: { sentiment?: { fng?: { v?: N; cls?: string; prev?: N }; dvol?: Record<string, number>; dom?: { v?: N } }; calendar?: { next?: Array<{ type: string; in_min: number }> }; degraded?: string[]; age_s?: N }
  news_digest?: { events?: unknown[] }
  levels?: Record<string, BriefLevel>
  ideas?: { active?: Array<{ id: string; sym: string; side: string; level?: N; dist_pct?: N; status?: string; age_h?: N }> }
  recent?: Array<{ seq: number; type: string; sym?: string | null; d?: Record<string, unknown> }>
  lessons?: string[]
  checklist?: Array<{ id: string; text: string }>
}

export interface BriefDetailReply {
  id: string | null
  ok: boolean
  data?: Brief
  error?: string
}
