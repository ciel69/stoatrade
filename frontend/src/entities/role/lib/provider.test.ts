import { describe, expect, it } from 'vitest'
import { enableBlock, limitChanges, limitErrors, limitInput, limitText, limitsArgs, limitsEqual, limitsWhat, parseLimit, providerReflects, resolveLimits } from './provider'
import type { ProviderRow } from '../model/types'

const LIM: ProviderRow['limits'] = { concurrent: 2, rpm: 60, day_usd: 3, hour_usd: 1, day_toks: 8000000 }
const prov = (o: Partial<ProviderRow> = {}): ProviderRow => ({
  id: 'deepseek', name: 'DeepSeek', adapter: 'openai', enabled: true, local: false, verified: true, note: null, error: null, base_host: null,
  cred: { needed: true, present: true, perms_ok: true, env: null }, limits: LIM, models: [], profiles: [], ...o,
})

describe('потолки расхода: ввод', () => {
  it('пусто и 0 это без потолка', () => {
    expect(parseLimit('', 'usd')).toEqual({ ok: true, value: 0 })
    expect(parseLimit('  ', 'int')).toEqual({ ok: true, value: 0 })
    expect(parseLimit('0', 'usd')).toEqual({ ok: true, value: 0 })
  })
  it('деньги: два знака, запятая допустима, промежуточный ввод читается', () => {
    expect(parseLimit('1,5', 'usd')).toEqual({ ok: true, value: 1.5 })
    expect(parseLimit('3.', 'usd')).toEqual({ ok: true, value: 3 })
    expect(parseLimit('.5', 'usd')).toEqual({ ok: true, value: 0.5 })
    expect(parseLimit('0.25', 'usd')).toEqual({ ok: true, value: 0.25 })
    expect(parseLimit('0.255', 'usd').ok).toBe(false)
  })
  it('целые поля не принимают дробь', () => {
    expect(parseLimit('60', 'int')).toEqual({ ok: true, value: 60 })
    expect(parseLimit('2.5', 'int')).toMatchObject({ ok: false, error: 'Нужно целое число.' })
  })
  it('отрицательное, не число и слишком большое отвергаются с понятным текстом', () => {
    expect(parseLimit('-1', 'int')).toMatchObject({ ok: false, error: 'Число не может быть меньше 0.' })
    expect(parseLimit('abc', 'usd')).toMatchObject({ ok: false })
    expect(parseLimit('1e5', 'int').ok).toBe(false)
    expect(parseLimit('99999999999999', 'int')).toMatchObject({ ok: false, error: 'Слишком большое число.' })
  })
  it('ошибки по полям черновика', () => {
    expect(limitErrors({ rpm: '2.5', day_usd: '3', hour_usd: 'x' })).toEqual({ rpm: 'Нужно целое число.', hour_usd: 'Введите число, например 3 или 0.5.' })
    expect(limitErrors({})).toEqual({})
  })
  it('значение в поле: нет или 0 это пустое поле', () => {
    expect([limitInput(3), limitInput(0), limitInput(null), limitInput(0.5)]).toEqual(['3', '', '', '0.5'])
  })
})

describe('потолки расхода: было → станет и аргументы', () => {
  it('строки изменений только по реально изменённым полям', () => {
    const ch = limitChanges(LIM, { day_usd: '5', hour_usd: '1', rpm: '' })
    expect(ch.map((c) => [c.label, c.from, c.to])).toEqual([['Деньги за день', '$3', '$5'], ['Запросов в минуту', '60 запросов', 'без потолка']])
    expect(limitChanges(LIM, { day_usd: '3.00' })).toEqual([])
    expect(limitChanges(LIM, { day_usd: 'oops' })).toEqual([])
  })
  it('значения словами', () => {
    expect([limitText('day_usd', 0), limitText('day_usd', 1.5), limitText('hour_usd', 0.05)]).toEqual(['без потолка', '$1.5', '$0.05'])
    expect(limitText('day_toks', 2000000)).toMatch(/^2\s000\s000 токенов$/)
  })
  it('строка для диалога', () => {
    expect(limitsWhat(limitChanges(LIM, { day_usd: '5' }))).toBe('Потолки расхода: деньги за день $3 → $5.')
  })
  it('аргументы update содержат все пять полей целиком, токены под именем day_tokens', () => {
    const a = limitsArgs('deepseek', LIM, { day_usd: '5', rpm: '' })
    expect(a).toEqual({ op: 'update', id: 'deepseek', patch: { limits: { concurrent: 2, rpm: 0, day_usd: 5, hour_usd: 1, day_tokens: 8000000 } } })
    expect(Object.keys(a!.patch.limits).sort()).toEqual(['concurrent', 'day_tokens', 'day_usd', 'hour_usd', 'rpm'])
  })
  it('пустые текущие значения уходят нулями; без изменений или с ошибкой аргументов нет', () => {
    const a = limitsArgs('local', { concurrent: 1, rpm: null, day_usd: null, hour_usd: null, day_toks: null }, { rpm: '20' })
    expect(a!.patch.limits).toEqual({ concurrent: 1, rpm: 20, day_usd: 0, hour_usd: 0, day_tokens: 0 })
    expect(limitsArgs('x', LIM, {})).toBeNull()
    expect(limitsArgs('x', LIM, { day_usd: '3' })).toBeNull()
    expect(limitsArgs('x', LIM, { day_usd: '-3' })).toBeNull()
    expect(resolveLimits(LIM, { day_usd: 'x' })).toBeNull()
  })
})

describe('включение и ожидание отражения', () => {
  it('без ключа включить нельзя, выключить можно, локальному ключ не нужен', () => {
    const noKey = prov({ cred: { needed: true, present: false, perms_ok: null, env: null } })
    expect(enableBlock(noKey, true)).toBe('Сначала добавьте ключ.')
    expect(enableBlock(noKey, false)).toBeNull()
    expect(enableBlock(prov(), true)).toBeNull()
    expect(enableBlock(prov({ local: true, cred: { needed: false, present: false, perms_ok: null, env: null } }), true)).toBeNull()
  })
  it('тема отражает включение, лимиты и ключ', () => {
    expect(providerReflects(prov({ enabled: true }), { enabled: true })).toBe(true)
    expect(providerReflects(prov({ enabled: false }), { enabled: true })).toBe(false)
    expect(providerReflects(prov(), { limits: { concurrent: 2, rpm: 60, day_usd: 3, hour_usd: 1, day_toks: 8000000 } })).toBe(true)
    expect(providerReflects(prov(), { limits: { concurrent: 2, rpm: 0, day_usd: 3, hour_usd: 1, day_toks: 8000000 } })).toBe(false)
    expect(providerReflects(prov({ cred: { needed: true, present: false, perms_ok: null, env: null } }), { key: true })).toBe(false)
    expect(providerReflects(prov(), { key: true })).toBe(true)
    expect(providerReflects(null, { key: true })).toBe(false)
  })
  it('ноль и пусто в теме равны', () => {
    expect(limitsEqual({ concurrent: null, rpm: 0, day_usd: null, hour_usd: 0, day_toks: null }, { concurrent: 0, rpm: 0, day_usd: 0, hour_usd: 0, day_toks: 0 })).toBe(true)
  })
})
