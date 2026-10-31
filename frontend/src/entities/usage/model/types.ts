type N = number | null

export type UsagePeriod = 1 | 7 | 30
export type UsageBy = 'model' | 'provider' | 'role'

export interface UsageTotals {
  usd: N
  toks: N
  calls: N
}

/** Группа расхода: модель, провайдер или роль (зависит от разреза). */
export interface UsageGroup {
  id: string
  calls: N
  in: N
  in_hit: N
  out: N
  reasoning: N
  usd: N
  errors: N
  fmt_errors: N
  fallbacks: N
  lat_p50_ms: N
  lat_p95_ms: N
  err_rate: N
  fmt_err_rate: N
  cache_hit_rate: N
}

/** Потолок расхода: область (all или провайдер), вид (day_usd, hour_usd, day_tokens, hour_tokens), израсходовано, потолок, момент сброса (секунды, абсолютный). */
export interface UsageBudget {
  state: string
  scope: string
  cred?: string
  key?: string
  used: number
  cap: number
  until_ts: number
}

export interface UsageData {
  total: UsageTotals
  today: UsageTotals
  groups: UsageGroup[]
  budget: UsageBudget[]
}

/** Ответ темы `usage_detail` на запрос {op:'usage'}: данные или ошибка. */
export interface UsageReply {
  days: number
  by: string
  ok: boolean
  error?: string
  data?: UsageData
}
