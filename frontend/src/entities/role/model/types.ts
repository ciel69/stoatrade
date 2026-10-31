type N = number | null

export interface RoleParams {
  temperature: N
  max_out_tok: N
  thinking: string | null
}

/** Модель роли: имя модели и провайдер, включён ли провайдер, допущена ли модель к live. Ключей в данных нет. */
export interface RoleModel {
  id: string
  provider: string
  model_name: string
  provider_enabled: boolean
  live_ok: boolean
  params: RoleParams
}

export interface RoleRow {
  role: string
  scope: 'team' | 'shared' | string
  team: string | null
  /** роль влияет на решения в live */
  live_risk: boolean
  io: string | null
  primary: RoleModel | null
  fallback: RoleModel[]
  budget_day_usd: N
  today: { usd: N; calls: N; errors: N; budget_left_usd: N }
  /** за сутки по журналу вызовов: доля ошибок, доля ответов с неверным форматом, число переключений на запасную, медиана задержки */
  day: { calls: N; err_rate: N; fmt_err_rate: N; fallbacks: N; p50_ms: N; usd: N }
}

/** Тема `settings`: роли мозга и их модели (источник: llm.dashapi, только чтение). */
export interface RolesSnapshot {
  mode: string | null
  rev: number | null
  live_team: string | null
  exchange: { active?: string; allowed?: string[] } | null
  providers: { total?: number; enabled?: number; no_cred?: string[] } | null
  usage_today: { usd: N; toks: N; calls: N } | null
  errors: string[]
  roles: RoleRow[]
}
