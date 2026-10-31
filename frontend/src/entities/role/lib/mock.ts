import type { ProfileRow, RoleModel, RolesSnapshot } from '../model/types'

/** Профили для отладки без сервера (?edit=mock): одно семейство локальных и облачных, один с выключенным провайдером, один без допуска к live. */
export const MOCK_PROFILES: ProfileRow[] = [
  { id: 'lm_gpt_oss_20b', provider: 'local', model_name: 'gpt-oss-20b', provider_enabled: true, live_ok: false, used_by: ['A.entry_scout', 'shared.reviewer'] },
  { id: 'ds_flash', provider: 'deepseek', model_name: 'deepseek-flash', provider_enabled: true, live_ok: true, used_by: ['A.position_manager'] },
  { id: 'ds_pro', provider: 'deepseek', model_name: 'deepseek-pro', provider_enabled: true, live_ok: false, used_by: [] },
  { id: 'lm_qwen3_30b', provider: 'local', model_name: 'qwen3-30b', provider_enabled: true, live_ok: false, used_by: [] },
  { id: 'claude_haiku', provider: 'anthropic', model_name: 'claude-haiku', provider_enabled: false, live_ok: false, used_by: [] },
]

export type RoleMock = 'edit' | 'live'

const asModel = (p: ProfileRow, old?: RoleModel | null): RoleModel => ({
  id: p.id, provider: p.provider, model_name: p.model_name, provider_enabled: p.provider_enabled, live_ok: p.live_ok,
  params: old?.params ?? { temperature: null, max_out_tok: null, thinking: null },
})

/** Входящий снимок для отладки: без profile_list подставляются примеры, у ролей без target он достраивается, в режиме live режим платформы подменяется. */
export function mockSnapshot(s: RolesSnapshot, kind: RoleMock): RolesSnapshot {
  // без profile_list от сервера: модели самих ролей (чтобы «текущая» совпадала с данными) плюс примеры
  const own = new Map<string, ProfileRow>()
  for (const r of s.roles) for (const m of [r.primary, ...r.fallback]) if (m && !own.has(m.id)) own.set(m.id, { id: m.id, provider: m.provider, model_name: m.model_name, provider_enabled: m.provider_enabled, live_ok: m.live_ok, used_by: [] })
  const profiles = s.profile_list?.length ? s.profile_list : [...own.values(), ...MOCK_PROFILES.filter((p) => !own.has(p.id))]
  return {
    ...s,
    profile_list: profiles,
    mode: kind === 'live' ? 'live' : s.mode,
    roles: s.roles.map((r) => ({ ...r, target: r.target ?? `${r.team ?? 'shared'}.${r.role}` })),
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
