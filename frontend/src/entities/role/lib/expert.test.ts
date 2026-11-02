import { describe, expect, it } from 'vitest'
import type { PresetRow, ProfileRow, RoleRow } from '../model/types'
import { draftOfProfile, emptyOpts, emptyProfile, optsArgs, optsErrors, optsLines, optsReflects, paramsText, presetArgs, presetNote, presetReflects, presetsToAdd, profileArgs, profileErrors, profileLines, profileReflects, remarkText } from './expert'

const prof = (o: Partial<ProfileRow> = {}): ProfileRow => ({ id: 'ds', provider: 'deepseek', model: 'flash', model_name: 'deepseek-flash', provider_enabled: true, live_ok: false, params: { temperature: 0.2, max_out_tok: 1200, thinking: 'low' }, ...o })
const names = { p: (id: string) => id, m: (_p: string, m: string) => m }
const role = (o: Partial<RoleRow> = {}): RoleRow => ({
  role: 'reviewer', scope: 'shared', team: null, live_risk: false, io: null, primary: { id: 'ds', provider: 'deepseek', model_name: 'm', provider_enabled: true, live_ok: false, params: { temperature: 0.2, max_out_tok: 1200, thinking: null } },
  fallback: [], budget_day_usd: 1, today: { usd: 0, calls: 0, errors: 0, budget_left_usd: 1 }, day: { calls: 0, err_rate: 0, fmt_err_rate: 0, fallbacks: 0, p50_ms: null, usd: 0 }, ...o,
})

describe('профили моделей', () => {
  it('имя нового профиля и обязательные поля', () => {
    const d = { ...emptyProfile(), id: 'Bad Name' }
    expect(Object.keys(profileErrors(d, null, []))).toEqual(['id', 'provider', 'model'])
    expect(profileErrors({ ...d, id: 'ds', provider: 'x', model: 'y' }, null, ['ds']).id).toMatch(/уже есть/)
    expect(profileErrors({ ...d, id: 'new-1', provider: 'x', model: 'y' }, null, ['ds'])).toEqual({})
    expect(profileErrors({ ...draftOfProfile(prof()), id: 'любое' }, prof(), ['ds'])).toEqual({})       // у существующего имя не проверяется
  })
  it('температура и длина ответа проверяются, пусто допустимо', () => {
    const d = { ...draftOfProfile(prof()), temperature: '3', maxTokens: '1.5' }
    expect(Object.keys(profileErrors(d, prof(), []))).toEqual(['temperature', 'maxTokens'])
    expect(profileErrors({ ...d, temperature: '', maxTokens: '' }, prof(), [])).toEqual({})
  })
  it('аргументы для нового профиля: модель и заполненные параметры', () => {
    const d = { ...emptyProfile(), id: 'fast', provider: 'deepseek', model: 'flash', temperature: '0,3', thinking: 'off' }
    expect(profileArgs(d, null, [])).toEqual({ op: 'set', id: 'fast', provider: 'deepseek', model: 'flash', params: { temperature: 0.3, thinking: 'off' } })
  })
  it('аргументы правки: только отличающееся; без изменений и с ошибкой аргументов нет', () => {
    const p = prof()
    const d = draftOfProfile(p)
    expect(profileArgs(d, p, ['ds'])).toBeNull()
    expect(profileArgs({ ...d, maxTokens: '2000' }, p, ['ds'])).toEqual({ op: 'set', id: 'ds', params: { max_tokens: 2000 } })
    expect(profileArgs({ ...d, model: 'pro' }, p, ['ds'])).toEqual({ op: 'set', id: 'ds', provider: 'deepseek', model: 'pro' })
    expect(profileArgs({ ...d, temperature: '9' }, p, ['ds'])).toBeNull()
  })
  it('«было → станет» словами', () => {
    const p = prof()
    const lines = profileLines({ ...draftOfProfile(p), maxTokens: '2000', thinking: 'high' }, p, names.p, names.m)
    expect(lines.map((l) => `${l.label}: ${l.from} -> ${l.to}`)).toEqual(['Длина ответа, токенов: 1200 -> 2000', 'Рассуждение: немного -> много'])
    expect(profileLines({ ...emptyProfile(), id: 'n', provider: 'a', model: 'b' }, null, names.p, names.m)[0]).toEqual({ label: 'Модель', from: 'нет', to: 'b (a)' })
  })
  it('тема отражает профиль', () => {
    const rows = [prof({ params: { temperature: 0.2, max_out_tok: 2000, thinking: 'low' } })]
    expect(profileReflects(rows, { op: 'set', id: 'ds', params: { max_tokens: 2000 } })).toBe(true)
    expect(profileReflects(rows, { op: 'set', id: 'ds', params: { max_tokens: 3000 } })).toBe(false)
    expect(profileReflects(rows, { op: 'set', id: 'zz', provider: 'a', model: 'b' })).toBe(false)
    expect(profileReflects(undefined, { op: 'set', id: 'ds' })).toBe(false)
    expect(paramsText(rows[0].params)).toBe('разброс 0.2 · ответ до 2000 токенов · рассуждение: немного')
    expect(paramsText(null)).toBe('параметры по умолчанию')
  })
})

describe('провайдер из шаблона', () => {
  const pr = (o: Partial<PresetRow> = {}): PresetRow => ({ id: 'openai', name: 'OpenAI', adapter: 'openai', base_host: 'api.openai.com', verified: false, note: null, models: ['gpt'], added: false, ...o })
  it('аргументы add берут шаблон по его имени; уже добавленные не предлагаются', () => {
    expect(presetArgs(pr())).toEqual({ op: 'add', id: 'openai', preset: 'openai' })
    expect(presetsToAdd([pr(), pr({ id: 'x', added: true })]).map((p) => p.id)).toEqual(['openai'])
    expect(presetsToAdd(undefined)).toEqual([])
    expect(presetReflects([{ id: 'openai' }], 'openai')).toBe(true)
    expect(presetReflects([], 'openai')).toBe(false)
  })
  it('описание честно говорит про непроверенный шаблон', () => {
    expect(presetNote(pr())).toContain('не проверен')
    expect(presetNote(pr({ verified: true }))).toContain('шаблон проверен')
  })
})

describe('особые параметры роли', () => {
  it('ввод проверяется', () => {
    expect(optsErrors({ budget: '0', temperature: '5', maxTokens: 'x' })).toEqual({ budget: expect.any(String), temperature: expect.any(String), maxTokens: expect.any(String) })
    expect(optsErrors({ budget: '1.555', temperature: '', maxTokens: '' }).budget).toBeTruthy()
    expect(optsErrors(emptyOpts())).toEqual({})
  })
  it('аргументы: только заполненное и отличающееся от текущего', () => {
    const r = role()
    expect(optsArgs('shared.reviewer', emptyOpts(), r)).toBeNull()
    expect(optsArgs('shared.reviewer', { budget: '1', temperature: '0.2', maxTokens: '' }, r)).toBeNull()
    expect(optsArgs('shared.reviewer', { budget: '2,5', temperature: '0.5', maxTokens: '800' }, r)).toEqual({ op: 'opts', role: 'shared.reviewer', budget: { day_usd: 2.5 }, params: { temperature: 0.5, max_tokens: 800 } })
    expect(optsArgs('shared.reviewer', { budget: '-1', temperature: '', maxTokens: '' }, r)).toBeNull()
  })
  it('строки «было → станет» и отражение в теме', () => {
    const a = optsArgs('shared.reviewer', { budget: '3', temperature: '', maxTokens: '800' }, role())!
    expect(optsLines(a, role()).map((l) => `${l.label}: ${l.from} -> ${l.to}`)).toEqual(['Лимит расходов в день: 1 USD -> 3 USD', 'Длина ответа, токенов: 1200 -> 800'])
    expect(optsReflects(role(), a)).toBe(false)
    expect(optsReflects(role({ budget_day_usd: 3, primary: { ...role().primary!, params: { temperature: 0.2, max_out_tok: 800, thinking: null } } }), a)).toBe(true)
    expect(optsReflects(null, a)).toBe(false)
  })
})

describe('замечания', () => {
  it('аккуратный текст', () => {
    expect(remarkText('  провайдер x:   нет адреса ')).toBe('Провайдер x: нет адреса.')
    expect(remarkText('Готово!')).toBe('Готово!')
    expect(remarkText('  ')).toBe('')
  })
})
