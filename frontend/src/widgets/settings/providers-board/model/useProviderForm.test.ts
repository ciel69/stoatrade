import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { useRoleStore, type ProviderRow, type RolesSnapshot } from '@/entities/role'
import type { ActionSpec } from '@/entities/action'

// движок действий подменён: тест смотрит, что запускается (в том числе диалог рисков решает сервер, здесь его нет)
const runs: ActionSpec[] = []
const fake = { active: false, can: (_a: string) => true, run: (s: ActionSpec) => { runs.push(s); return true } }
vi.mock('@/entities/action', () => ({ useActionStore: () => fake }))

import { useProviderForm } from './useProviderForm'

const prov = (o: Partial<ProviderRow> = {}): ProviderRow => ({
  id: 'mine', name: 'Мой сервер', adapter: 'openai_compat', enabled: false, local: false, verified: false, note: null, error: null, base_host: 'api.example.com', allow_private: false,
  cred: { needed: true, present: true, perms_ok: true, env: null }, limits: { concurrent: null, rpm: null, day_usd: null, hour_usd: null, day_toks: null },
  models: [], profiles: [], ...o,
})
const snap = (list: ProviderRow[]): RolesSnapshot => ({ mode: 'paper', rev: 1, live_team: null, exchange: null, providers: null, usage_today: null, errors: [], roles: [], provider_list: list })
const done = (data: Record<string, unknown> | null = null) => ({ id: 'q', rid: 'a', status: 'done', code: 'OK', msg: '', data })

function setup(id: string | null, list = [prov()]) {
  setActivePinia(createPinia())
  useRoleStore().snapshot = snap(list)
  return { f: useProviderForm(() => id), store: useRoleStore() }
}

beforeEach(() => { runs.length = 0; fake.active = false; fake.can = () => true })

describe('useProviderForm: создание', () => {
  it('пустая форма не отправляется, ошибка показана только у заполненного поля', () => {
    const { f } = setup(null)
    expect(f.isNew.value).toBe(true)
    expect(f.canSubmit.value).toBe(false)
    expect(f.submit()).toBe(false)
    f.setField({ baseUrl: 'http://api.example.com' })
    expect(f.shown('baseUrl')).toMatch(/localhost/)
    expect(f.shown('name')).toBeUndefined()
  })
  it('add с id из названия; внутренняя сеть уходит флажком, ключа в аргументах нет', () => {
    const { f } = setup(null)
    f.setField({ name: 'My LLM', baseUrl: 'https://10.0.0.5/v1', model: 'my-model', allowPrivate: true })
    expect(f.privateHint.value).toBe(false)
    expect(f.submit()).toBe(true)
    expect(runs[0].action).toBe('llm.provider')
    expect(runs[0].args).toMatchObject({ op: 'add', id: 'my_llm', spec: { name: 'My LLM', base_url: 'https://10.0.0.5/v1', allow_private: true } })
    expect(JSON.stringify(runs[0].args)).not.toMatch(/"key"/)
  })
  it('подсказка о внутреннем адресе, пока флажок не стоит', () => {
    const { f } = setup(null)
    f.setField({ baseUrl: 'https://10.0.0.5/v1' })
    expect(f.privateHint.value).toBe(true)
  })
  it('после успеха форма ждёт, пока провайдер появится в данных', async () => {
    const { f, store } = setup(null)
    f.setField({ name: 'New One', baseUrl: 'https://api.example.com/v1', model: 'm1' })
    f.submit()
    runs[0].onDone?.(done())
    expect(f.created.value).toBe('new_one')
    expect(f.createdReady.value).toBe(false)
    expect(f.canSubmit.value).toBe(false)
    store.snapshot = snap([prov(), prov({ id: 'new_one', name: 'New One' })])
    await nextTick()
    expect(f.createdReady.value).toBe(true)
  })
  it('отказ платформы показан словами с её пояснением', () => {
    const { f } = setup(null)
    f.setField({ name: 'x', baseUrl: 'https://api.example.com/v1', model: 'm1' })
    f.submit()
    runs[0].onFail?.('Не получилось.', { id: 'q', rid: 'a', status: 'rejected', code: 'E_ARGS', msg: 'bad   url' })
    expect(f.failure.value).toBe('Не получилось. Платформа ответила: bad url')
  })
  it('без расширенного управления или во время другого действия не отправляется', () => {
    fake.can = () => false
    const { f } = setup(null)
    f.setField({ name: 'x', baseUrl: 'https://api.example.com/v1', model: 'm1' })
    expect(f.canSubmit.value).toBe(false)
    fake.can = () => true
    fake.active = true
    expect(f.submit()).toBe(false)
  })
})

describe('useProviderForm: изменение', () => {
  it('форма заполнена текущим провайдером, менять нечего: сохранять нечего', () => {
    const { f } = setup('mine')
    expect(f.draft.value.name).toBe('Мой сервер')
    expect(f.canSubmit.value).toBe(false)
  })
  it('название, адрес и лимиты уходят одним update, после отправки сохранение закрыто до отражения', async () => {
    const { f, store } = setup('mine')
    f.setField({ name: 'Новый', baseUrl: 'https://new.example.com/v1' })
    f.setLimit('day_usd', '3')
    expect(f.lines.value).toContain('деньги за день: без потолка → $3')
    expect(f.submit()).toBe(true)
    expect(runs[0].args).toEqual({
      op: 'update', id: 'mine', patch: { name: 'Новый', base_url: 'https://new.example.com/v1', limits: { day_usd: 3, hour_usd: 0, day_tokens: 0, concurrent: 0 } },
    })
    runs[0].onDone?.(done({ send_warning: { msg: 'ключ провайдера теперь уходит на new.example.com' } }))
    expect(f.note.value).toMatch(/new\.example\.com/)
    expect(f.canSubmit.value).toBe(false)
    store.snapshot = snap([prov({ name: 'Новый', base_host: 'new.example.com', limits: { concurrent: null, rpm: null, day_usd: 3, hour_usd: null, day_toks: null } })])
    await nextTick()
    expect(f.pending.value).toBeNull()
    expect(f.draft.value.baseUrl).toBe('')
    expect(f.draft.value.limits).toEqual({})
  })
  it('плохой лимит блокирует сохранение и подписан словами', () => {
    const { f } = setup('mine')
    f.setLimit('day_usd', 'abc')
    expect(f.canSubmit.value).toBe(false)
    expect(f.errors.value.limits?.day_usd).toMatch(/число/i)
  })
  it('флажок внутренней сети меняет патч', () => {
    const { f } = setup('mine')
    f.setField({ allowPrivate: true })
    expect(f.patch.value).toEqual({ allow_private: true })
  })
})
