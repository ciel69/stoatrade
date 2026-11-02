/** Контракты темы `day` и ответа `day_report_detail` (сервер: src/tb/dash2/topics.py, day и day_report). Все поля пришли из белого списка сервера; тексты уже обрезаны. */

export interface DayBlocking {
  id: string | null
  msg: string | null
  fix: string | null
}

export interface DayAbort {
  step: string | null
  code: string | null
  why: string | null
  blocking: DayBlocking[]
}

export interface DayStep {
  name: string | null
  phase: 'start' | 'stop' | null
  /** true: готово, false: сбой, null: выполняется */
  ok: boolean | null
  dur_s: number | null
  code: string | null
  amber: boolean
  why: string | null
  blocking: DayBlocking[]
  gone: string[]
  waiting: string[]
  llm: string | null
}

export interface DayQuestion {
  id: string | null
  plan: string | null
  text: string | null
  options: string[]
  answer: string | null
  answered_by: string | null
}

export interface DayCarry {
  plan: string | null
  sym: string | null
  side: string | null
  stop: number | null
  risk_usdt: number | null
  why: string | null
  decided_by: string | null
  alive: boolean | null
}

export interface DayCheckRow {
  item: string | null
  kind: string | null
  plan: string | null
  sym: string | null
  side: string | null
  stype: string | null
  rule: string | null
  decision: string | null
  codes: string[]
}

export interface DayBrief {
  day_id: string | null
  state: string | null
  mode: string | null
  agent: string | null
  started: string | null
  stopped: string | null
  stop_source: string | null
  verdict: string | null
  net: number | null
  carried_n: number
  questions_n: number
  acked: boolean
  report_ready: boolean
  needs_resume: boolean
  abort: DayAbort | null
}

export interface DayPlan {
  source: string | null
  regime: string | null
  bias: string | null
  max_new_plans: number | null
  session: string | null
}

export interface DayClosing {
  source: string | null
  auto_reason: string | null
  emergency: boolean
  manual: string[]
  failed: { items: string[]; manual: string[]; reason: string | null } | null
}

export interface DayCurrent extends DayBrief {
  steps: DayStep[]
  questions: DayQuestion[]
  deadline_ts: number | null
  closing: DayClosing
  dayplan: DayPlan | null
  preflight: { red: string[]; amber: string[] } | null
  checklist: DayCheckRow[]
  carry: DayCarry[]
  carry_in: DayCarry[]
}

export interface DayBanner {
  level: 'info' | 'warn' | 'alarm'
  text: string | null
  /** id дня, итог которого готов и ещё не прочитан (report.ack снимает) */
  day_report: string | null
  day_id: string | null
}

export interface DaySnapshot {
  banner: DayBanner | null
  current: DayCurrent | null
  last: DayBrief | null
  days: DayBrief[]
}

export interface DayReportData {
  day_id: string
  kind: string | null
  mode: string | null
  agent: string | null
  started: string | null
  stopped: string | null
  stop_source: string | null
  verdict: string | null
  pnl: { net: number | null; fees: number | null; funding: number | null; realized_gross: number | null; r_sum: number | null }
  trades_n: number
  open_trades_n: number
  cost: { usd: number | null; calls: number }
  sessions: Array<{ id: string | null; type: string | null; pnl: number | null; trades: number; budget_used: number | null }>
  carried: DayCarry[]
  closed_eod: number
  open_questions: Array<{ id: string | null; text: string | null }>
  narrative: string | null
  close_failed: { items: string[]; reason: string | null } | null
  /** Текст отчёта (markdown): выводится как простой текст, без разметки и HTML. */
  md: string
}

export interface DayReportReply {
  id: string | null
  ok: boolean
  error?: string
  data?: DayReportData
}

/** Что ждёт ответа владельца. count: сколько дел всего (для счётчика). */
export interface DayNeeds {
  questions: DayQuestion[]
  deadline: number | null
  resume: boolean
  /** id дня с готовым непрочитанным итогом */
  report: string | null
  count: number
}
