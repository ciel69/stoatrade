import { describe, expect, it } from 'vitest'
import {
  MAX_FALLBACK, addFallback, chainArgs, chainArgsOf, chainOf, changeLines, changed, fallbackCandidates, liveLocked, moveFallback, overlay, profileBlock, reflects, removeFallback, reverseLines, setPrimary, targetOf, useArgs, whatText,
  type Chain, type ProfileLike,
} from './chain'
import type { ProfileRow, RoleModel, RoleRow } from '../model/types'

const model = (id: string, o: Partial<RoleModel> = {}): RoleModel => ({ id, provider: 'local', model_name: id.toUpperCase(), provider_enabled: true, live_ok: false, params: { temperature: null, max_out_tok: null, thinking: null }, ...o })
const role = (o: Partial<RoleRow> = {}): RoleRow => ({
  role: 'entry_scout', target: 'A.entry_scout', scope: 'team', team: 'A', live_risk: false, io: null, primary: model('p'), fallback: [model('f1'), model('f2')], budget_day_usd: null,
  today: { usd: 0, calls: 0, errors: 0, budget_left_usd: null }, day: { calls: 0, err_rate: 0, fmt_err_rate: 0, fallbacks: 0, p50_ms: 0, usd: 0 }, ...o,
})
const base: Chain = { primary: 'p', fallback: ['f1', 'f2'] }
const names = new Map<string, ProfileLike>([['p', model('p', { model_name: 'gpt-oss-20b' })], ['n', model('n', { model_name: 'deepseek-flash' })], ['f1', model('f1')], ['f2', model('f2')]])

describe('цепочка роли', () => {
  it('chainOf и targetOf: идентификаторы и имя роли для действий, запасное имя из команды', () => {
    expect(chainOf(role())).toEqual(base)
    expect(chainOf(role({ primary: null, fallback: [] }))).toEqual({ primary: null, fallback: [] })
    expect(targetOf(role())).toBe('A.entry_scout')
    expect(targetOf(role({ target: undefined }))).toBe('A.entry_scout')
    expect(targetOf(role({ target: undefined, team: null, role: 'reviewer' }))).toBe('shared.reviewer')
  })

  it('перестановка запасных: вверх, вниз, у края без изменений', () => {
    expect(moveFallback(base, 'f2', -1).fallback).toEqual(['f2', 'f1'])
    expect(moveFallback(base, 'f1', 1).fallback).toEqual(['f2', 'f1'])
    expect(moveFallback(base, 'f1', -1)).toBe(base)
    expect(moveFallback(base, 'f2', 1)).toBe(base)
    expect(moveFallback(base, 'нет', 1)).toBe(base)
    expect(base.fallback).toEqual(['f1', 'f2'])
  })

  it('добавление: в конец, без дублей, без основной, не больше четырёх', () => {
    expect(addFallback(base, 'x').fallback).toEqual(['f1', 'f2', 'x'])
    expect(addFallback(base, 'f1')).toBe(base)
    expect(addFallback(base, 'p')).toBe(base)
    let c = base
    for (const id of ['a', 'b', 'c', 'd']) c = addFallback(c, id)
    expect(c.fallback).toHaveLength(MAX_FALLBACK)
    expect(addFallback(c, 'z')).toBe(c)
  })

  it('удаление запасной', () => {
    expect(removeFallback(base, 'f1').fallback).toEqual(['f2'])
    expect(removeFallback(base, 'нет').fallback).toEqual(['f1', 'f2'])
  })

  it('новая основная убирается из запасных', () => {
    expect(setPrimary(base, 'f2')).toEqual({ primary: 'f2', fallback: ['f1'] })
    expect(setPrimary(base, 'n')).toEqual({ primary: 'n', fallback: ['f1', 'f2'] })
  })

  it('кандидаты в запасные: не основная и не уже в цепочке', () => {
    const list = ['p', 'f1', 'f2', 'x', 'y'].map((id) => ({ id, provider: 'local', model_name: id, provider_enabled: true, live_ok: false }) as ProfileRow)
    expect(fallbackCandidates(list, base).map((p) => p.id)).toEqual(['x', 'y'])
  })

  it('изменения: что именно отличается', () => {
    expect(changed(base, base)).toEqual({ primary: false, fallback: false, any: false })
    expect(changed(base, { ...base, primary: 'n' })).toEqual({ primary: true, fallback: false, any: true })
    expect(changed(base, { ...base, fallback: ['f2', 'f1'] }).fallback).toBe(true)
  })

  it('«было → станет» и текст для диалога', () => {
    const l1 = changeLines(base, { ...base, primary: 'n' }, names)
    expect(l1).toEqual([{ label: 'Основная', from: 'gpt-oss-20b', to: 'deepseek-flash' }])
    expect(whatText(l1)).toBe('Основная: gpt-oss-20b → deepseek-flash. Запасные не меняются.')
    const l2 = changeLines(base, { primary: 'p', fallback: [] }, names)
    expect(l2).toEqual([{ label: 'Запасные', from: 'F1, F2', to: 'нет' }])
    expect(whatText(l2)).toBe('Запасные: F1, F2 → нет. Основная не меняется.')
    expect(whatText(changeLines(base, { primary: 'n', fallback: ['f2'] }, names))).toBe('Основная: gpt-oss-20b → deepseek-flash. Запасные: F1, F2 → F2.')
    expect(changeLines({ primary: null, fallback: [] }, { primary: 'n', fallback: [] }, names)[0].from).toBe('не назначена')
    expect(reverseLines(l1)).toEqual([{ label: 'Основная', from: 'deepseek-flash', to: 'gpt-oss-20b' }])
  })
})

describe('аргументы действий', () => {
  it('chain: только изменившиеся поля, op и role всегда', () => {
    expect(chainArgs('A.entry_scout', base, { ...base, primary: 'n' })).toEqual({ op: 'chain', role: 'A.entry_scout', primary: 'n' })
    expect(chainArgs('A.entry_scout', base, { ...base, fallback: ['f2'] })).toEqual({ op: 'chain', role: 'A.entry_scout', fallback: ['f2'] })
    expect(chainArgs('A.entry_scout', base, { primary: 'n', fallback: [] })).toEqual({ op: 'chain', role: 'A.entry_scout', primary: 'n', fallback: [] })
    expect(chainArgs('A.entry_scout', base, base)).toBeNull()
  })
  it('возврат прежнего и brain.use', () => {
    expect(chainArgsOf('shared.reviewer', { primary: 'p', fallback: ['f1'] })).toEqual({ op: 'chain', role: 'shared.reviewer', primary: 'p', fallback: ['f1'] })
    expect(chainArgsOf('shared.reviewer', {})).toBeNull()
    expect(useArgs('A.position_manager', 'ds_flash')).toEqual({ role: 'A.position_manager', profile: 'ds_flash' })
  })
})

describe('live-роль и снимок', () => {
  it('блокировка: live, влияет на live, роль live-команды', () => {
    const r = role({ live_risk: true })
    expect(liveLocked(r, 'live', 'A')).toBe(true)
    expect(liveLocked(r, 'paper', 'A')).toBe(false)
    expect(liveLocked(r, 'live', 'B')).toBe(false)
    expect(liveLocked(role({ live_risk: false }), 'live', 'A')).toBe(false)
    expect(liveLocked(role({ live_risk: true, team: null }), 'live', null)).toBe(false)
  })
  it('снимок отразил ожидаемое: основная и/или запасные', () => {
    const r = role()
    expect(reflects(r, { primary: 'p' })).toBe(true)
    expect(reflects(r, { primary: 'n' })).toBe(false)
    expect(reflects(r, { fallback: ['f1', 'f2'] })).toBe(true)
    expect(reflects(r, { fallback: ['f2', 'f1'] })).toBe(false)
    expect(reflects(r, { primary: 'p', fallback: ['f1'] })).toBe(false)
    expect(reflects(null, {})).toBe(false)
  })
  it('ожидаемое накладывается поверх текущего, неуказанное остаётся', () => {
    expect(overlay(base, { primary: 'n' })).toEqual({ primary: 'n', fallback: ['f1', 'f2'] })
    expect(overlay(base, { fallback: [] })).toEqual({ primary: 'p', fallback: [] })
  })
  it('профиль с выключенным провайдером не выбирается', () => {
    expect(profileBlock(model('x', { provider_enabled: false }))).toBe('провайдер выключен')
    expect(profileBlock(model('x'))).toBeNull()
  })
})
