import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useRoleStore } from '@/entities/role'
import type { ActionSpec } from '@/entities/action'

const runs: ActionSpec[] = []
const fake = { active: false, can: (_a: string) => true, run: (s: ActionSpec) => { runs.push(s); return true } }
vi.mock('@/entities/action', () => ({ useActionStore: () => fake }))

import { useProviderAdd } from './useProviderAdd'

beforeEach(() => {
  runs.length = 0
  fake.active = false
  fake.can = () => true
  setActivePinia(createPinia())
  useRoleStore().snapshot = { mode: 'paper', rev: 1, live_team: null, exchange: null, providers: null, usage_today: null, errors: [], roles: [], provider_list: [] }
})

describe('useProviderAdd', () => {
  it('пока форма не заполнена, отправки нет; заполненная уходит как llm.provider add со spec', () => {
    const a = useProviderAdd()
    a.openForm()
    expect(a.submit()).toBe(false)
    a.patch({ id: 'mine', baseUrl: 'https://api.example.com/v1', model: 'gpt-4o-mini' })
    expect(a.submit()).toBe(true)
    expect(runs[0].action).toBe('llm.provider')
    expect(runs[0].args).toMatchObject({ op: 'add', id: 'mine', spec: { adapter: 'openai_compat', base_url: 'https://api.example.com/v1', key_file: 'mine' } })
    expect(runs[0].args).not.toHaveProperty('spec.allow_private')
  })
  it('успех закрывает форму и запоминает id; отказ платформы с E_ARGS показывает её слова', () => {
    const a = useProviderAdd()
    a.openForm()
    a.patch({ id: 'mine', baseUrl: 'https://api.example.com/v1', model: 'm1', allowPrivate: true })
    a.submit()
    expect((runs[0].args as { spec: Record<string, unknown> }).spec.allow_private).toBe(true)
    runs[0].onFail?.('Платформа не поняла команду.', { id: 'q', rid: 'a', status: 'failed', code: 'E_ARGS', msg: 'base_url: приватный адрес' })
    expect(a.failure.value).toMatch(/приватный адрес/)
    a.patch({ model: 'm2' })
    expect(a.failure.value).toBe('')
    a.submit()
    runs[1].onDone?.()
    expect(a.open.value).toBe(false)
    expect(a.created.value).toBe('mine')
  })
  it('занятое имя и недоступное действие блокируют отправку; подсказка про внутренний адрес', () => {
    useRoleStore().snapshot!.provider_list = [{ id: 'mine' } as never]
    const a = useProviderAdd()
    a.openForm()
    a.patch({ id: 'mine', baseUrl: 'https://10.0.0.5/v1', model: 'm' })
    expect(a.errors.value.id).toMatch(/уже есть/)
    expect(a.privateHint.value).toBe(true)
    a.patch({ id: 'other' })
    fake.can = () => false
    expect(useProviderAdd().canAdd.value).toBe(false)
    expect(a.submit()).toBe(false)
  })
})
