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

/** Доступный профиль модели (тема settings, profile_list): провайдер, модель, включён ли провайдер, допущена ли к live, какие роли используют. Ключей в данных нет. */
export interface ProfileRow {
  id: string
  provider: string
  /** ключ модели у провайдера (для правки профиля); у старого сервера нет */
  model?: string
  model_name: string
  provider_enabled: boolean
  live_ok: boolean
  /** поставляется с платформой (правка ложится отдельным слоем) */
  builtin?: boolean
  used_by?: string[]
  params?: RoleParams
}

/** Шаблон провайдера (тема settings, preset_list): готовый набор для «Добавить провайдера». Ключей и имён файлов ключей в данных нет. */
export interface PresetRow {
  id: string
  name: string
  adapter: string | null
  base_host: string | null
  verified: boolean
  note: string | null
  models: string[]
  /** уже добавлен в список провайдеров */
  added: boolean
}

export interface RoleRow {
  role: string
  /** имя роли для действий управления ('A.entry_scout', 'shared.reviewer'); у старого сервера нет, тогда берётся из команды и роли */
  target?: string
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

/** Цена модели за миллион токенов: вход мимо кэша, вход из кэша, выход. null: цена не задана (локальная). */
export interface ModelPrice {
  in_miss: N
  in_hit: N
  out: N
}

export interface ProviderModel {
  id: string
  name: string
  ctx: N
  tools: boolean | null
  tags: string[]
  price: ModelPrice | null
}

/** Провайдер LLM. Ключа в данных нет, только факт: нужен ли, есть ли, безопасны ли права файла (true/false/null: неизвестно). */
export interface ProviderRow {
  id: string
  name: string
  adapter: string | null
  enabled: boolean
  local: boolean
  verified: boolean | string | null
  note: string | null
  error: string | null
  base_host: string | null
  cred: { needed: boolean; present: boolean; perms_ok: boolean | null; env: string | null }
  limits: { concurrent: N; rpm: N; day_usd: N; hour_usd: N; day_toks: N }
  models: ProviderModel[]
  profiles: string[]
}

/** Биржа: разрешена владельцем, активна ли, реализован ли адаптер, режим, жив ли исполнитель, какие виды ключей настроены (read, trade, testnet). */
export interface ExchangeRow {
  id: string
  name: string
  enabled: boolean
  mode: string | null
  implemented: boolean
  active: boolean
  allowed: boolean
  note: string | null
  alive: boolean | null
  creds: string[]
  max_open_positions: N
}

/** Запись журнала смен назначений (новые первыми). ts в секундах. */
export interface SwapRow {
  seq: number
  ts: number
  rev: N
  prev_rev: N
  kind: string
  tier: string | null
  by: string | null
  target: string[]
  from: string | null
  to: string | null
}

/** Тема `settings`: роли мозга и их модели (источник: llm.dashapi, только чтение). */
export interface RolesSnapshot {
  provider_list?: ProviderRow[]
  profile_list?: ProfileRow[]
  preset_list?: PresetRow[]
  exchange_list?: ExchangeRow[]
  swaps?: SwapRow[]
  revisions?: number | null
  mode: string | null
  rev: number | null
  live_team: string | null
  exchange: { active?: string; allowed?: string[] } | null
  providers: { total?: number; enabled?: number; no_cred?: string[] } | null
  usage_today: { usd: N; toks: N; calls: N } | null
  errors: string[]
  roles: RoleRow[]
}
