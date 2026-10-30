import { describe, expect, it } from 'vitest'
import type { DecisionItem } from '../model/types'
import { actorKey, filterDecisions, humanAction, planShort } from './decisions'

const row = (o: Partial<DecisionItem>): DecisionItem => ({
  seq: 1, ts: '', t: 1, actor: 'brain:entry_scout', plan: null, rev: null, kind: 'note', what: 'note', action: 'note', why: '', args: {}, basis: {}, res: {}, ok: null, code: null,
  denied: false, noop: false, idle: false, px: null, r: null, brief_id: null, link_id: null, action_id: null, exch_ms: null, h: '', mode: null, ...o,
})
const f = { actor: 'all', onlyFail: false, plan: '', q: '' } as const

describe('decisions', () => {
  const items = [
    row({ seq: 1, actor: 'brain:entry_scout', action: 'вход 1 @ 100', why: 'пробой уровня', plan: 'PL-1030-SOL-01' }),
    row({ seq: 2, actor: 'exec', action: 'стоп 99 → 100', denied: true, code: 'DENY_STOP', plan: 'PL-1030-SOL-01' }),
    row({ seq: 3, actor: 'rule:be', action: 'trail_step', noop: true, plan: 'PL-1030-ETH-01' }),
  ]
  it('исполнитель без хвоста', () => {
    expect(actorKey('brain:entry_scout')).toBe('brain')
    expect(actorKey(null)).toBe('')
  })
  it('фильтр по исполнителю, отказам и плану', () => {
    expect(filterDecisions(items, { ...f, actor: 'brain' }).map((d) => d.seq)).toEqual([1])
    expect(filterDecisions(items, { ...f, onlyFail: true }).map((d) => d.seq)).toEqual([2, 3])
    expect(filterDecisions(items, { ...f, plan: 'PL-1030-ETH-01' }).map((d) => d.seq)).toEqual([3])
  })
  it('поиск по тексту без регистра: действие, причина, код', () => {
    expect(filterDecisions(items, { ...f, q: 'ПРОБОЙ' }).map((d) => d.seq)).toEqual([1])
    expect(filterDecisions(items, { ...f, q: 'deny_stop' }).map((d) => d.seq)).toEqual([2])
    expect(filterDecisions(items, { ...f, q: 'нет такого' })).toEqual([])
  })
  it('служебные действия читаются по-русски', () => {
    expect(humanAction(row({ what: 'brain_decision', action: 'brain_decision decision=skip sym=SOL', args: { decision: 'skip', sym: 'SOL' } }))).toBe('пропуск SOL')
    expect(humanAction(row({ what: 'brain_why', args: { decision: 'hold', sym: 'ETH' } }))).toBe('объяснил: держит ETH')
    expect(humanAction(row({ what: 'watch_wake', args: { code: 'STALLED' } }))).toBe('сторож разбудил мозг: STALLED')
    expect(humanAction(row({ what: 'move_stop', action: 'стоп 99 → 100' }))).toBe('стоп 99 → 100')
  })
  it('короткое имя плана', () => {
    expect(planShort('PL-1030-SOL-01')).toBe('SOL-01')
    expect(planShort(null)).toBe('–')
  })
})
