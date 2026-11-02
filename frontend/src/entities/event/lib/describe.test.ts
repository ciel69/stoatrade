import { describe as suite, expect, it } from 'vitest'
import { describe, plainDetail } from './describe'
import type { BusEvent } from '../model/types'

const ev = (type: string, d: Record<string, unknown> = {}): BusEvent => ({ seq: 1, ts: 't', type, pri: 'info', plan: null, sym: 'SOL', d })

suite('describe', () => {
  it('русское название и главный параметр простыми словами', () => {
    expect(describe(ev('milestone', { name: 'r_plus_1', r: 1 }))).toEqual({ title: 'веха', detail: 'прибыль достигла 1R', tone: 'neutral' })
    expect(describe(ev('scanner_signal', { kind: 'hot_enter', px: 1 })).detail).toBe('монета стала горячей')
  })
  it('тон: отказы и ошибки плохие, исполнение хорошее; неизвестный тип и код не показываются сырыми', () => {
    expect(describe(ev('limit_hit', { which: 'brain_budget' })).tone).toBe('bad')
    expect(describe(ev('entry_filled')).tone).toBe('good')
    expect(describe(ev('something_new')).title).toBe('событие платформы')
    expect(describe(ev('milestone', { name: 'weird_code_x' })).detail).toBe('')
  })
  it('отказ правила: причина по-русски', () => {
    expect(describe(ev('rule_fired', { ok: false, code: 'NO_SLOTS' })).detail).toBe('отказ: нет свободных мест под позиции')
  })
  it('plainDetail: обычные слова проходят, snake_case скрывается', () => {
    expect(plainDetail('вход закрыт')).toBe('вход закрыт')
    expect(plainDetail('some_code')).toBe('')
  })
})
