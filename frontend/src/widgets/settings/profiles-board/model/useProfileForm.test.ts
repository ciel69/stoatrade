import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useRoleStore, type ProfileRow, type ProviderRow, type RolesSnapshot } from '@/entities/role'
import type { ActionSpec } from '@/entities/action'

const runs: ActionSpec[] = []
const fake = { active: false, can: (_a: string) => true, run: (s: ActionSpec) => { runs.push(s); return true } }
vi.mock('@/entities/action', () => ({ useActionStore: () => fake }))

import { useProfileForm } from './useProfileForm'

const prov = (id: string, enabled: boolean, models: string[]): ProviderRow => ({
  id, name: id.toUpperCase(), adapter: 'openai_compat', enabled, local: false, verified: true, note: null, error: null, base_host: 'x.example.com',
  cred: { needed: true, present: true, perms_ok: true, env: null }, limits: { concurrent: null, rpm: null, day_usd: null, hour_usd: null, day_toks: null },
  models: models.map((m) => ({ id: m, name: `${m}-name`, ctx: null, tools: true, tags: [], price: null })), profiles: [],
})
const profile: ProfileRow = { id: 'fast', provider: 'b', model: 'm1', model_name: 'm1-name', provider_enabled: true, live_ok: false, params: { temperature: 0.2, max_out_tok: 1500, thinking: 'low' } }
const snap = (): RolesSnapshot => ({
  mode: 'paper', rev: 1, live_team: null, exchange: null, providers: null, usage_today: null, errors: [], roles: [],
  provider_list: [prov('a', false, ['x1']), prov('b', true, ['m1', 'm2'])], profile_list: [profile],
})

function setup(id: string | null) {
  setActivePinia(createPinia())
  useRoleStore().snapshot = snap()
  let closed = 0
  const f = useProfileForm(() => id, () => { closed += 1 })
  return { f, closed: () => closed }
}

beforeEach(() => { runs.length = 0; fake.active = false; fake.can = () => true })

describe('useProfileForm: создание', () => {
  it('по умолчанию выбран первый включённый провайдер и его первая модель; без названия отправлять нельзя', () => {
    const { f } = setup(null)
    expect(f.draft.value.provider).toBe('b')
    expect(f.draft.value.model).toBe('m1')
    expect(f.errors.value.name).toBe('Введите название.')
    expect(f.canSubmit.value).toBe(false)
  })
  it('название даёт служебное имя; отправка brain.profile set с параметрами и закрытием окна', () => {
    const { f, closed } = setup(null)
    f.name.value = 'Быстрый чат'
    f.patch({ temperature: '0.3', maxTokens: '2000', thinking: 'low' })
    expect(f.id.value).toBe('bystryy_chat')
    expect(f.submit()).toBe(true)
    expect(runs[0].action).toBe('brain.profile')
    expect(runs[0].args).toEqual({ op: 'set', id: 'bystryy_chat', provider: 'b', model: 'm1', params: { temperature: 0.3, max_tokens: 2000, thinking: 'low' } })
    runs[0].onDone?.()
    expect(closed()).toBe(1)
  })
  it('смена провайдера подбирает его первую модель, плохие числа названы словами', () => {
    const { f } = setup(null)
    f.name.value = 'x'
    f.setProvider('a')
    expect(f.draft.value.model).toBe('x1')
    f.patch({ temperature: '3' })
    expect(f.errors.value.temperature).toMatch(/от 0 до 2/)
    expect(f.canSubmit.value).toBe(false)
    f.patch({ temperature: '', maxTokens: 'abc' })
    expect(f.errors.value.maxTokens).toBeTruthy()
  })
})

describe('useProfileForm: изменение', () => {
  it('форма заполнена профилем; пока ничего не изменено, сохранять нечего', () => {
    const { f } = setup('fast')
    expect(f.isNew.value).toBe(false)
    expect(f.draft.value).toMatchObject({ provider: 'b', model: 'm1', temperature: '0.2', maxTokens: '1500', thinking: 'low' })
    expect(f.canSubmit.value).toBe(false)
  })
  it('уходит только изменённое', () => {
    const { f } = setup('fast')
    f.patch({ temperature: '0.5' })
    f.submit()
    expect(runs[0].args).toEqual({ op: 'set', id: 'fast', params: { temperature: 0.5 } })
    expect(f.lines.value[0].label).toMatch(/Разброс/)
  })
  it('без расширенного управления не отправляется', () => {
    fake.can = () => false
    const { f } = setup('fast')
    f.patch({ temperature: '0.5' })
    expect(f.submit()).toBe(false)
  })
  it('отказ платформы читается словами', () => {
    const { f } = setup('fast')
    f.patch({ temperature: '0.5' })
    f.submit()
    runs[0].onFail?.('Провайдер выключен.', { id: 'q', rid: 'a', status: 'rejected', code: 'E_PROVIDER_OFF', msg: '' })
    expect(f.failure.value).toBe('Провайдер выключен.')
  })
})
