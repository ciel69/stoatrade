import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useRoleStore, type ProviderRow, type RolesSnapshot } from '@/entities/role'
import type { ActionSpec } from '@/entities/action'

// движок действий подменён: тест смотрит, что запускается, и сам отдаёт итог платформы
const runs: ActionSpec[] = []
const fake = { active: false, can: (_a: string) => true, run: (s: ActionSpec) => { runs.push(s); return true } }
vi.mock('@/entities/action', () => ({ useActionStore: () => fake }))

import { useProviderNet } from './useProviderNet'

const prov = (o: Partial<ProviderRow> = {}): ProviderRow => ({
  id: 'mine', name: 'mine', adapter: 'openai_compat', enabled: false, local: false, verified: false, note: null, error: null, base_host: 'api.example.com', allow_private: false,
  cred: { needed: true, present: true, perms_ok: true, env: null }, limits: { concurrent: null, rpm: null, day_usd: null, hour_usd: null, day_toks: null },
  models: [{ id: 'gpt', name: 'gpt-4o', ctx: null, tools: false, tags: [], price: null }], profiles: [], ...o,
})
const snap = (p: ProviderRow): RolesSnapshot => ({ mode: 'paper', rev: 1, live_team: null, exchange: null, providers: null, usage_today: null, errors: [], roles: [], provider_list: [p] })
const done = (data: Record<string, unknown>) => ({ id: 'q', rid: 'a', status: 'done', code: 'OK', msg: '', data })

function setup(key = true, p = prov()) {
  setActivePinia(createPinia())
  useRoleStore().snapshot = snap(p)
  return useProviderNet(() => 'mine', () => key)
}

beforeEach(() => { runs.length = 0; fake.active = false; fake.can = () => true })

describe('useProviderNet: список моделей', () => {
  it('без ключа запрос не уходит; с ключом уходит op models без лишних полей', () => {
    expect(setup(false).fetchModels()).toBe(false)
    expect(runs).toHaveLength(0)
    const n = setup(true)
    expect(n.fetchModels()).toBe(true)
    expect(runs[0].action).toBe('llm.provider')
    expect(runs[0].args).toEqual({ op: 'models', id: 'mine' })
    expect(runs[0].quiet).toBe(true)
  })
  it('успех кладёт список; отказ провайдера даёт слова, не код', () => {
    const n = setup()
    n.fetchModels()
    runs[0].onDone?.(done({ ok: true, ids: ['a', 'b'], count: 2, truncated: false }))
    expect(n.models.value?.ids).toEqual(['a', 'b'])
    expect(n.running.value).toBeNull()
    n.fetchModels()
    runs[1].onDone?.(done({ ok: false, error: 'auth', status: 401 }))
    expect(n.modelsFail.value?.text).toMatch(/не принял ключ/)
    expect(n.modelsFail.value?.text).not.toMatch(/auth/)
  })
  it('отказ по частоте называет секунды', () => {
    const n = setup()
    n.fetchModels()
    runs[0].onFail?.('x', { id: 'q', rid: 'a', status: 'rejected', code: 'E_RATE', msg: '', data: { retry_after_s: 2.4 } })
    expect(n.modelsFail.value?.text).toMatch(/через 3 с/)
  })
  it('пока идёт другое действие или нет расширенного управления, запрос не стартует', () => {
    fake.active = true
    expect(setup().fetchModels()).toBe(false)
    fake.active = false
    fake.can = () => false
    expect(setup().fetchModels()).toBe(false)
  })
})

describe('useProviderNet: проверка подключения', () => {
  it('model передаётся, только если выбрана; итог словами', () => {
    const n = setup()
    n.runPing()
    expect(runs[0].args).toEqual({ op: 'ping', id: 'mine' })
    n.pingModel.value = 'gpt'
    n.runPing()
    expect(runs[1].args).toEqual({ op: 'ping', id: 'mine', model: 'gpt' })
    runs[1].onDone?.(done({ ok: true, ms: 500, model: 'gpt', model_name: 'gpt-4o', usage: { in: 5, out: 1 } }))
    expect(n.ping.value?.ms).toBe(500)
  })
})

describe('useProviderNet: добавление модели', () => {
  it('model_set: ключ из имени, нулевая цена, возможности выключены; повтор имени отвергается', () => {
    const n = setup()
    n.choose('openai/gpt-5:free')
    expect(n.canAddModel.value).toBe(true)
    n.addModel()
    expect(runs[0].args).toEqual({
      op: 'model_set', id: 'mine', model: 'openai-gpt-5-free',
      spec: { name: 'openai/gpt-5:free', tools: false, json_mode: false, price: { in_miss: 0, in_hit: 0, out: 0 } },
    })
    n.choose('gpt-4o')
    expect(n.pickError.value).toMatch(/уже добавлена/)
    expect(n.canAddModel.value).toBe(false)
    n.choose('bad name')
    expect(n.pickError.value).toBeTruthy()
  })
})

describe('useProviderNet: адрес', () => {
  it('патч из нового адреса и флажка; пока ничего не изменено, отправлять нечего', () => {
    const n = setup()
    expect(n.patch.value).toBeNull()
    expect(n.submitAddress()).toBe(false)
    n.setBaseUrl('https://new.example.com/v1')
    n.setAllowPrivate(true)
    expect(n.patch.value).toEqual({ base_url: 'https://new.example.com/v1', allow_private: true })
    n.submitAddress()
    expect(runs[0].args).toEqual({ op: 'update', id: 'mine', patch: { base_url: 'https://new.example.com/v1', allow_private: true } })
    runs[0].onDone?.(done({ send_warning: { code: 'KEY_NEW_URL', host: 'new.example.com', msg: 'ключ провайдера теперь уходит на new.example.com' } }))
    expect(n.addrNote.value).toMatch(/new\.example\.com/)
    expect(n.addr.value.baseUrl).toBe('')
  })
  it('плохой адрес не даёт патча', () => {
    const n = setup()
    n.setBaseUrl('http://api.example.com')
    expect(n.addrUrlError.value).toBeTruthy()
    expect(n.patch.value).toBeNull()
  })
})
