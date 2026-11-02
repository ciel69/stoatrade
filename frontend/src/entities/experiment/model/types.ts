/** Контракт ответа `exp_detail` на операцию `exp` (сервер: src/tb/dash2/topics.py, exp_detail). Поля из белого списка, строки обрезаны, групп не больше 60, сделок в группе не больше 25. */

export type ExpMode = 'paper' | 'live' | 'testnet' | 'all'

export interface ExpTrade {
  id: string | null
  sym: string | null
  side: string | null
  mode: string | null
  /** результат в долях риска (R), после издержек */
  r: number | null
  pnl: number | null
  /** закрыта, epoch в секундах */
  ts: number | null
  reason: string | null
}

export interface ExpRow {
  /** имя группы: эксперимент, гипотеза, монета, код причины... (разбор словами: shared/lib/human-pages/exp) */
  name: string
  mode: string
  n: number
  wins: number
  win_rate: number | null
  avg_r: number | null
  se_r: number | null
  pf: number | null
  pf_inf: boolean
  pnl: number | null
  fees: number | null
  /** вероятность случайности; null при n меньше порога */
  p: number | null
  small: boolean
  below_alpha: boolean
  /** гипотеза подтверждена по правилу досье (только в разрезе «Гипотезы» на бумаге) */
  confirmed: boolean
  last_ts: number | null
  trades: ExpTrade[]
}

export interface ExpMeta {
  hypothesis: string | null
  prereg: string | null
  started: string | null
  min_n: number | null
}

export interface ExpConfirmed {
  hyp: string
  n: number
  avg_r: number | null
  win_rate: number | null
  p: number | null
  live_n?: number
  live_avg_r?: number | null
}

export interface ExpDetail {
  ok: boolean
  by: string
  mode: string
  error?: string
  min_n?: number
  alpha?: number
  /** число сравнений (групп) в запросе */
  k?: number
  /** порог значимости с поправкой Бонферрони: alpha / k */
  alpha_adj?: number | null
  rows?: ExpRow[]
  rows_total?: number
  total_closed?: number
  /** сделок без разбора (только в разрезе «Ошибки») */
  unreviewed?: number
  meta?: Record<string, ExpMeta>
  /** подтверждённые гипотезы (на бумаге): не зависит от выбранного разреза */
  confirmed?: ExpConfirmed[]
}
