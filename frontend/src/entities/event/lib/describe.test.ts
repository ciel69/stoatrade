import { describe as suite, expect, it } from 'vitest'
import { describe } from './describe'
import type { BusEvent } from '../model/types'

const ev = (type: string, d: Record<string, unknown> = {}): BusEvent => ({ seq: 1, ts: 't', type, pri: 'info', plan: null, sym: 'SOL', d })

suite('describe', () => {
  it('русское название и главный параметр', () => {
    expect(describe(ev('milestone', { name: 'r_plus_1', r: 1 }))).toEqual({ title: 'веха', detail: 'r_plus_1', tone: 'neutral' })
    expect(describe(ev('scanner_signal', { kind: 'hot_enter', px: 1 })).detail).toBe('hot_enter')
  })
  it('тон: отказы и ошибки плохие, исполнение хорошее, неизвестный тип как есть', () => {
    expect(describe(ev('limit_hit', { which: 'brain_budget' })).tone).toBe('bad')
    expect(describe(ev('entry_filled')).tone).toBe('good')
    expect(describe(ev('something_new')).title).toBe('something_new')
  })
})
