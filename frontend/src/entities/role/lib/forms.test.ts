import { describe, expect, it } from 'vitest'
import type { ProviderRow } from '../model/types'
import { createArgs, createReflects, draftOfProvider, editLines, editPatch, editReflects, emptyProviderDraft, providerErrors, slugId } from './forms'

const row = (o: Partial<ProviderRow> = {}): ProviderRow => ({
  id: 'mine', name: 'Мой сервер', adapter: 'openai_compat', enabled: false, local: false, verified: false, note: null, error: null, base_host: 'api.example.com', allow_private: false,
  cred: { needed: true, present: true, perms_ok: true, env: null }, limits: { concurrent: null, rpm: null, day_usd: null, hour_usd: null, day_toks: null },
  models: [], profiles: [], ...o,
})
const filled = () => ({ ...emptyProviderDraft(), name: 'Мой сервер', baseUrl: 'https://api.example.com/v1', model: 'gpt-4o-mini' })

describe('slugId: внутреннее имя из названия', () => {
  it('латиница в нижнем регистре через «_», русские буквы транслитерируются', () => {
    expect(slugId('My LLM', [])).toBe('my_llm')
    expect(slugId('Быстрый чат', [])).toBe('bystryy_chat')
  })
  it('занятое имя обходится числом, пустое даёт запасное', () => {
    expect(slugId('My LLM', ['my_llm'])).toBe('my_llm_2')
    expect(slugId('!!!', [], 'provider')).toBe('provider')
  })
  it('результат всегда подходит под правило id', () => {
    const id = slugId('Очень длинное название провайдера с пробелами и цифрами 12345', [])
    expect(id).toMatch(/^[a-z0-9][a-z0-9_-]{0,29}$/)
  })
})

describe('providerErrors: проверка словами', () => {
  it('создание требует название, адрес и модель', () => {
    const e = providerErrors(emptyProviderDraft(), null)
    expect(Object.keys(e).sort()).toEqual(['baseUrl', 'model', 'name'])
    expect(providerErrors(filled(), null)).toEqual({})
  })
  it('тип из данных сводится к значению списка формы', () => {
    expect(draftOfProvider(row({ adapter: 'openai' })).adapter).toBe('openai_compat')
    expect(draftOfProvider(row({ adapter: 'openai-compatible' })).adapter).toBe('openai_compat')
    expect(draftOfProvider(row({ adapter: null })).adapter).toBe('openai_compat')
    expect(draftOfProvider(row({ adapter: 'anthropic' })).adapter).toBe('anthropic')
  })
  it('правка: пустой адрес значит «не менять», плохой адрес и лимит с ошибкой названы', () => {
    const d = draftOfProvider(row())
    expect(providerErrors(d, row())).toEqual({})
    expect(providerErrors({ ...d, baseUrl: 'http://api.example.com' }, row()).baseUrl).toMatch(/localhost/)
    expect(providerErrors({ ...d, limits: { day_usd: 'abc' } }, row()).limits?.day_usd).toBeTruthy()
    expect(providerErrors({ ...d, name: '  ' }, row()).name).toBe('Введите название.')
  })
})

describe('createArgs: llm.provider add', () => {
  it('id из названия, название в spec, выключен, без ключа в аргументах', () => {
    const a = createArgs({ ...filled(), name: 'My LLM', allowPrivate: true }, [])!
    expect(a.op).toBe('add')
    expect(a.id).toBe('my_llm')
    expect(a.spec).toMatchObject({ name: 'My LLM', adapter: 'openai_compat', base_url: 'https://api.example.com/v1', key_file: 'my_llm', allow_private: true })
    expect(Object.keys(a.spec.models as object)).toEqual(['gpt-4o-mini'])
    expect(JSON.stringify(a)).not.toMatch(/"key"|value/)
  })
  it('занятый id обходится, ошибки ввода дают null', () => {
    expect(createArgs({ ...filled(), name: 'My LLM' }, ['my_llm'])!.id).toBe('my_llm_2')
    expect(createArgs(emptyProviderDraft(), [])).toBeNull()
  })
  it('для Anthropic добавляется имя переменной ключа', () => {
    expect(createArgs({ ...filled(), adapter: 'anthropic', name: 'claude' }, [])!.spec.key_env).toBe('CLAUDE_API_KEY')
  })
})

describe('editPatch: llm.provider update', () => {
  it('ничего не меняли: патча нет', () => {
    expect(editPatch(draftOfProvider(row()), row())).toBeNull()
  })
  it('название, адрес, внутренняя сеть и лимиты уходят одним патчем, лимиты целиком', () => {
    const d = { ...draftOfProvider(row()), name: 'Новый', baseUrl: 'https://new.example.com/v1/', allowPrivate: true, limits: { day_usd: '3', concurrent: '2' } }
    const p = editPatch(d, row())!
    expect(p).toMatchObject({ name: 'Новый', base_url: 'https://new.example.com/v1', allow_private: true })
    expect(p.limits).toEqual({ day_usd: 3, hour_usd: 0, day_tokens: 0, concurrent: 2 })
  })
  it('ошибка ввода блокирует патч', () => {
    expect(editPatch({ ...draftOfProvider(row()), baseUrl: 'ftp://x' }, row())).toBeNull()
  })
  it('строки «что изменится» и отражение в теме', () => {
    const d = { ...draftOfProvider(row()), name: 'Новый', limits: { day_usd: '3' } }
    expect(editLines(d, row())).toEqual(['название: Мой сервер → Новый', 'деньги за день: без потолка → $3'])
    const p = editPatch(d, row())!
    expect(editReflects(row(), p)).toBe(false)
    expect(editReflects(row({ name: 'Новый', limits: { concurrent: null, rpm: null, day_usd: 3, hour_usd: null, day_toks: null } }), p)).toBe(true)
  })
})

describe('createReflects', () => {
  it('провайдер есть в списке', () => {
    expect(createReflects([row()], 'mine')).toBe(true)
    expect(createReflects([], 'mine')).toBe(false)
  })
})
