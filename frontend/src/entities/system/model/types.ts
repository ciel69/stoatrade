type N = number | null
export type SysLevel = 'ok' | 'warn' | 'bad' | 'off' | 'info'

/** Узел платформы: уровень, статический текст и момент последнего обновления (`at`) или запуска (`since`), секунды. Возрастов в теме нет: подпись строит клиент. */
export interface SysNode {
  id: string
  label: string
  level: SysLevel
  text?: string | null
  at?: N
  since?: N
}

export interface SysRoomRow {
  id: string
  label: string
  limit: N
  used: N
  room: N
  frac: N
  level: SysLevel
  unit: string
  help: string
}

export interface SysSession {
  id: string
  type: string
  budget_usdt: N
  budget_used: N
  trades: N
  max_trades: N
  entries_allowed: boolean
  block_reasons: string[]
  realized: N
  open_risk: N
  perf_mult: N
  perf_n: N
  start: string | null
  thesis: string | null
  /** момент окончания сессии со сроком; у дня без срока null */
  expires_at: N
}

export interface SysSessionRow {
  id: string
  type: string | null
  ts: string | null
  stopped: string | null
  stop_note: string | null
  thesis: string | null
}

/** Тема `system`: состояние платформы для страницы «Система · Управление» (источник: model.build_state, как у старой вкладки «Система»). */
export interface SystemSnapshot {
  mode: string
  level: SysLevel
  kill: { flatten: boolean; reason: string | null } | null
  nodes: SysNode[]
  warnings: Array<{ level: SysLevel | null; text: string | null }>
  session: SysSession | null
  room: { allowed: boolean | null; reasons: string[]; rows: SysRoomRow[] }
  account: { equity: N; hwm: N; drawdown: N; ladder_mult: N; venues: Record<string, { equity: N; free: N }> }
  sources: {
    feed: Array<{ id: string; state: string | null; connected: boolean | null; reconnects: N; errors: N; error: string | null }>
    context: Record<string, boolean>
    degraded: string[]
  }
  exec: { present: boolean; plans: Record<string, number>; positions: N; lag_ms: number; hold: unknown; consumers: Record<string, { acked: N; unacked: N }> }
  rejects: { n: N; rejects: N; codes: Array<{ code: string; n: N }> }
  sessions: SysSessionRow[]
}
