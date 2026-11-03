import type { ExchangeRow, ProfileRow, ProviderRow, RoleModel, RolesSnapshot } from '../model/types'
import type { OptsArgs, ProfileArgs } from './expert'
import type { AddArgs } from './providerNet'

/** Профили для отладки без сервера (?edit=mock): одно семейство локальных и облачных, один с выключенным провайдером, один без допуска к live. */
export const MOCK_PROFILES: ProfileRow[] = [
  { id: 'lm_gpt_oss_20b', provider: 'local', model: 'gpt-oss-20b', model_name: 'gpt-oss-20b', provider_enabled: true, live_ok: false, used_by: ['A.entry_scout', 'shared.reviewer'], params: { temperature: 0.1, max_out_tok: 1500, thinking: 'low' } },
  { id: 'ds_flash', provider: 'deepseek', model: 'flash', model_name: 'deepseek-flash', provider_enabled: true, live_ok: true, builtin: true, used_by: ['A.position_manager'], params: { temperature: 0.2, max_out_tok: 1200, thinking: null } },
  { id: 'ds_pro', provider: 'deepseek', model: 'pro', model_name: 'deepseek-pro', provider_enabled: true, live_ok: false, used_by: [], params: { temperature: null, max_out_tok: null, thinking: 'high' } },
  { id: 'lm_qwen3_30b', provider: 'local', model: 'qwen3-30b', model_name: 'qwen3-30b', provider_enabled: true, live_ok: false, used_by: [], params: { temperature: null, max_out_tok: null, thinking: null } },
  { id: 'claude_haiku', provider: 'anthropic', model: 'haiku', model_name: 'claude-haiku', provider_enabled: false, live_ok: false, used_by: [], params: { temperature: null, max_out_tok: null, thinking: null } },
]

const mockModel = (id: string, name: string, price: ProviderRow['models'][number]['price']): ProviderRow['models'][number] => ({ id, name, ctx: 128000, tools: true, tags: [], price })

/** Провайдеры для отладки, если сервер своих не прислал: облачный без ключа, облачный с ключом, локальный. */
export const MOCK_PROVIDERS: ProviderRow[] = [
  {
    id: 'anthropic', name: 'Anthropic', adapter: 'anthropic', enabled: false, local: false, verified: false, note: null, error: null, base_host: 'api.anthropic.com',
    cred: { needed: true, present: false, perms_ok: null, env: 'ANTHROPIC_API_KEY' }, limits: { concurrent: 2, rpm: 50, day_usd: 3, hour_usd: 1, day_toks: 2000000 },
    models: [mockModel('haiku', 'claude-haiku', { in_miss: 1, in_hit: 0.1, out: 5 })], profiles: ['claude_haiku'],
  },
  {
    id: 'deepseek', name: 'DeepSeek', adapter: 'openai', enabled: true, local: false, verified: true, note: null, error: null, base_host: 'api.deepseek.com',
    cred: { needed: true, present: true, perms_ok: true, env: 'DEEPSEEK_API_KEY' }, limits: { concurrent: 2, rpm: 60, day_usd: 3, hour_usd: 1, day_toks: 8000000 },
    models: [mockModel('flash', 'deepseek-flash', { in_miss: 0.28, in_hit: 0.028, out: 0.42 }), mockModel('pro', 'deepseek-pro', { in_miss: 0.5, in_hit: 0.05, out: 1.1 })], profiles: ['ds_flash', 'ds_pro'],
  },
  {
    id: 'local', name: 'Локальный', adapter: 'openai', enabled: true, local: true, verified: true, note: null, error: null, base_host: 'localhost',
    cred: { needed: false, present: false, perms_ok: null, env: null }, limits: { concurrent: 1, rpm: 20, day_usd: 0, hour_usd: null, day_toks: null },
    models: [mockModel('gpt-oss-20b', 'gpt-oss-20b', null), mockModel('qwen3-30b', 'qwen3-30b', null)], profiles: ['lm_gpt_oss_20b', 'lm_qwen3_30b'],
  },
]

/** Биржи для отладки, если сервер своих не прислал: основная в бумаге, разрешённая с ключом тестовой сети, заготовка. */
export const MOCK_EXCHANGES: ExchangeRow[] = [
  { id: 'bybit', name: 'Bybit', enabled: true, mode: 'paper', implemented: true, active: true, allowed: true, note: null, alive: true, creds: ['read', 'trade', 'testnet'], max_open_positions: 3 },
  { id: 'bitget', name: 'Bitget', enabled: true, mode: 'paper', implemented: true, active: false, allowed: true, note: null, alive: null, creds: ['read'], max_open_positions: null },
  { id: 'okx', name: 'OKX', enabled: false, mode: null, implemented: false, active: false, allowed: false, note: 'заготовка', alive: null, creds: [], max_open_positions: null },
]

export type RoleMock = 'edit' | 'live'

const mockRoleModel = (p: ProfileRow): RoleModel => ({ id: p.id, provider: p.provider, model_name: p.model_name, provider_enabled: p.provider_enabled, live_ok: p.live_ok, params: p.params ?? { temperature: null, max_out_tok: null, thinking: null } })
const mockRole = (role: string, scope: string, team: string | null, primary: ProfileRow, fb: ProfileRow[]): RolesSnapshot['roles'][number] => ({
  role, target: `${team ?? 'shared'}.${role}`, scope, team, live_risk: false, io: null, primary: mockRoleModel(primary), fallback: fb.map(mockRoleModel), budget_day_usd: 1,
  today: { usd: 0.02, calls: 9, errors: 0, budget_left_usd: 0.98 }, day: { calls: 30, err_rate: 0.03, fmt_err_rate: 0.1, fallbacks: 1, p50_ms: 1800, usd: 0.05 },
})

/** Снимок темы settings для отладки без сервера: если сервер ничего не прислал (нет связи), ?edit=mock всё равно показывает образцовые роли, профили, провайдеры и шаблоны. */
export function mockSeed(kind: RoleMock): RolesSnapshot {
  const [lm, flash] = MOCK_PROFILES
  return mockSnapshot({
    mode: 'paper', rev: 1, live_team: 'A', exchange: { active: 'bybit', allowed: ['bybit'] }, providers: null, usage_today: null, errors: [],
    roles: [mockRole('entry_scout', 'team', 'A', flash, [lm]), mockRole('position_manager', 'team', 'A', flash, []), mockRole('reviewer', 'shared', null, lm, [flash])],
  }, kind)
}

const asModel = (p: ProfileRow, old?: RoleModel | null): RoleModel => ({
  id: p.id, provider: p.provider, model_name: p.model_name, provider_enabled: p.provider_enabled, live_ok: p.live_ok,
  params: old?.params ?? { temperature: null, max_out_tok: null, thinking: null },
})

/** Входящий снимок для отладки: без profile_list подставляются примеры, у ролей без target он достраивается, в режиме live режим платформы подменяется. */
export function mockSnapshot(s: RolesSnapshot, kind: RoleMock): RolesSnapshot {
  // без profile_list от сервера: модели самих ролей (чтобы «текущая» совпадала с данными) плюс примеры
  const own = new Map<string, ProfileRow>()
  for (const r of s.roles) for (const m of [r.primary, ...r.fallback]) if (m && !own.has(m.id)) own.set(m.id, MOCK_PROFILES.find((x) => x.id === m.id) ?? { id: m.id, provider: m.provider, model_name: m.model_name, provider_enabled: m.provider_enabled, live_ok: m.live_ok, used_by: [] })
  const profiles = s.profile_list?.length ? s.profile_list : [...own.values(), ...MOCK_PROFILES.filter((p) => !own.has(p.id))]
  return {
    ...s,
    profile_list: profiles,
    provider_list: s.provider_list?.length ? s.provider_list : MOCK_PROVIDERS,
    exchange_list: MOCK_EXCHANGES,        // отладка: набор бирж всегда один и тот же, чтобы сценарии (основная, заготовка, без ключа) повторялись
    mode: kind === 'live' ? 'live' : s.mode,
    roles: s.roles.map((r) => ({ ...r, target: r.target ?? `${r.team ?? 'shared'}.${r.role}` })),
  }
}

/** Что сделал бы сервер после действия над провайдером (изображение для отладки): включение, лимиты, ключ есть или нет. */
export function mockProvider(s: RolesSnapshot, id: string, to: { enabled?: boolean; limits?: ProviderRow['limits']; keyPresent?: boolean }): RolesSnapshot {
  return {
    ...s,
    provider_list: (s.provider_list ?? []).map((p) => {
      if (p.id !== id) return p
      return {
        ...p,
        enabled: to.enabled ?? p.enabled,
        limits: to.limits ?? p.limits,
        cred: to.keyPresent === undefined ? p.cred : { ...p.cred, present: to.keyPresent },
      }
    }),
  }
}

/** Что сделал бы сервер после действия над биржей (отладка): сделать основной (остальные перестают быть основными) или сменить режим. */
export function mockExchange(s: RolesSnapshot, id: string, to: { active?: true; mode?: string }): RolesSnapshot {
  return {
    ...s,
    exchange_list: (s.exchange_list ?? []).map((e) => {
      if (to.active) return { ...e, active: e.id === id }
      return e.id === id && to.mode !== undefined ? { ...e, mode: to.mode } : e
    }),
  }
}

/** Что сделал бы сервер после команды chain: у роли меняются основная и запасные (изображение для отладки). */
export function mockChain(s: RolesSnapshot, target: string, to: { primary?: string; fallback?: string[] }): RolesSnapshot {
  const find = (id: string, old?: RoleModel | null): RoleModel | null => {
    const p = (s.profile_list ?? []).find((x) => x.id === id)
    return p ? asModel(p, old) : null
  }
  return {
    ...s,
    roles: s.roles.map((r) => {
      if ((r.target ?? `${r.team ?? 'shared'}.${r.role}`) !== target) return r
      return {
        ...r,
        primary: to.primary !== undefined ? find(to.primary, r.primary) ?? r.primary : r.primary,
        fallback: to.fallback !== undefined ? to.fallback.map((id) => find(id)).filter((x): x is RoleModel => !!x) : r.fallback,
      }
    }),
  }
}

/** Отладка: что сделал бы сервер после brain.profile set. */
export function mockProfile(s: RolesSnapshot, a: ProfileArgs): RolesSnapshot {
  const list = s.profile_list ?? []
  const old = list.find((p) => p.id === a.id)
  const prov = (s.provider_list ?? []).find((p) => p.id === (a.provider ?? old?.provider))
  const model = a.model ?? old?.model ?? ''
  const row: ProfileRow = {
    id: a.id, provider: a.provider ?? old?.provider ?? '', model, model_name: prov?.models.find((m) => m.id === model)?.name ?? model, provider_enabled: prov?.enabled ?? false, live_ok: old?.live_ok ?? false,
    builtin: old?.builtin ?? false, used_by: old?.used_by ?? [],
    params: { temperature: a.params?.temperature ?? old?.params?.temperature ?? null, max_out_tok: a.params?.max_tokens ?? old?.params?.max_out_tok ?? null, thinking: a.params?.thinking ?? old?.params?.thinking ?? null },
  }
  return { ...s, profile_list: old ? list.map((p) => (p.id === a.id ? row : p)) : [...list, row] }
}

/** Отладка: что сделал бы сервер после llm.provider add со своим spec: выключенный провайдер, ключ ещё не задан. */
export function mockProviderAdd(s: RolesSnapshot, a: AddArgs): RolesSnapshot {
  if ((s.provider_list ?? []).some((p) => p.id === a.id)) return s
  const spec = a.spec as { name?: string; adapter?: string; base_url?: string; allow_private?: boolean; models?: Record<string, { name: string }> }
  let host: string | null = null
  try { host = new URL(String(spec.base_url)).host } catch { host = null }
  const row: ProviderRow = {
    id: a.id, name: spec.name ?? a.id, adapter: spec.adapter ?? null, enabled: false, local: false, verified: false, note: null, error: null, base_host: host, allow_private: spec.allow_private === true,
    cred: { needed: true, present: false, perms_ok: null, env: null }, limits: { concurrent: null, rpm: null, day_usd: null, hour_usd: null, day_toks: null },
    models: Object.entries(spec.models ?? {}).map(([k, m]) => mockModel(k, m.name, { in_miss: 0, in_hit: 0, out: 0 })), profiles: [],
  }
  return { ...s, provider_list: [...(s.provider_list ?? []), row] }
}

/** Отладка: что сделал бы сервер после model_set (новая модель с нулевой ценой) и после update адреса. */
export function mockModelSet(s: RolesSnapshot, pid: string, key: string, name: string): RolesSnapshot {
  return {
    ...s,
    provider_list: (s.provider_list ?? []).map((p) => (p.id !== pid || p.models.some((m) => m.id === key) ? p : { ...p, models: [...p.models, { ...mockModel(key, name, { in_miss: 0, in_hit: 0, out: 0 }), tools: false }] })),
  }
}
export function mockAddress(s: RolesSnapshot, pid: string, patch: { name?: string; base_url?: string; allow_private?: boolean; limits?: { day_usd?: number; hour_usd?: number; day_tokens?: number; concurrent?: number } }): RolesSnapshot {
  let host: string | null = null
  try { host = patch.base_url ? new URL(patch.base_url).host : null } catch { host = null }
  const l = patch.limits
  return {
    ...s,
    provider_list: (s.provider_list ?? []).map((p) => (p.id !== pid ? p : {
      ...p, name: patch.name ?? p.name, base_host: host ?? p.base_host, allow_private: patch.allow_private ?? p.allow_private,
      limits: l ? { ...p.limits, day_usd: l.day_usd ?? p.limits.day_usd, hour_usd: l.hour_usd ?? p.limits.hour_usd, day_toks: l.day_tokens ?? p.limits.day_toks, concurrent: l.concurrent ?? p.limits.concurrent } : p.limits,
    })),
  }
}

/** Отладка: что сделал бы сервер после brain.role opts. */
export function mockOpts(s: RolesSnapshot, a: OptsArgs): RolesSnapshot {
  return {
    ...s,
    roles: s.roles.map((r) => {
      if ((r.target ?? `${r.team ?? 'shared'}.${r.role}`) !== a.role) return r
      const primary = r.primary ? { ...r.primary, params: { ...r.primary.params, temperature: a.params?.temperature ?? r.primary.params.temperature, max_out_tok: a.params?.max_tokens ?? r.primary.params.max_out_tok } } : r.primary
      return { ...r, primary, budget_day_usd: a.budget?.day_usd ?? r.budget_day_usd }
    }),
  }
}
