import { describe, expect, it } from 'vitest'
import { nodeLine, roomFill, roomText, sortNodes, verdict, worst } from './system'
import type { SysNode, SystemSnapshot } from '../model/types'

const node = (o: Partial<SysNode> & Pick<SysNode, 'id' | 'level'>): SysNode => ({ label: o.id, ...o })
const snap = (o: Partial<SystemSnapshot>): SystemSnapshot => ({
  mode: 'paper', level: 'ok', kill: null, nodes: [], warnings: [], session: null, room: { allowed: true, reasons: [], rows: [] },
  account: { equity: 1, hwm: 1, drawdown: 0, ladder_mult: 1, venues: {} }, sources: { feed: [], context: {}, degraded: [] }, exec: { present: true, plans: {}, positions: 0, lag_ms: 0, hold: null, consumers: {} },
  rejects: { n: 0, rejects: 0, codes: [] }, sessions: [], ...o,
})

describe('система', () => {
  it('худший уровень', () => {
    expect(worst(['ok', 'warn', 'ok'])).toBe('warn')
    expect(worst(['warn', 'bad'])).toBe('bad')
    expect(worst([])).toBe('ok')
    expect(worst([null, 'off'])).toBe('ok')
  })
  it('подпись узла: текст, возраст по моменту, время работы по моменту запуска', () => {
    expect(nodeLine(node({ id: 'a', level: 'ok', text: 'paper', at: 940 }), 1000)).toBe('paper · обновлено 1 мин назад')
    expect(nodeLine(node({ id: 'a', level: 'ok', since: 400 }), 1000)).toBe('работает 10 мин')
    expect(nodeLine(node({ id: 'a', level: 'ok' }), 1000)).toBe('–')
  })
  it('узлы: проблемные первыми, порядок внутри группы сохраняется', () => {
    const r = sortNodes([node({ id: 'a', level: 'ok' }), node({ id: 'b', level: 'warn' }), node({ id: 'c', level: 'bad' }), node({ id: 'd', level: 'ok' })])
    expect(r.map((n) => n.id)).toEqual(['c', 'b', 'a', 'd'])
  })
  it('вывод: авария важнее всего, иначе перечень проблемных узлов', () => {
    expect(verdict(snap({ kill: { flatten: false, reason: 'тест' } })).title).toBe('Аварийная остановка активна')
    const v = verdict(snap({ level: 'warn', nodes: [node({ id: 'feed', label: 'Фид цен', level: 'warn' }), node({ id: 'x', level: 'ok' })] }))
    expect([v.title, v.detail]).toEqual(['Есть замечания', 'Проверьте: Фид цен.'])
    expect(verdict(snap({})).title).toBe('Всё работает')
  })
  it('запас: доля остатка и текст; стопы считаются от использованного', () => {
    const row = { id: 'daily', label: 'Дневной', limit: 5, used: 1, room: 4, frac: 0.8, level: 'ok' as const, unit: 'USDT', help: '' }
    expect(roomFill(row)).toBeCloseTo(0.8)
    expect(roomText(row)).toBe('осталось 4.00 из 5.00 USDT')
    const stops = { ...row, id: 'stops', unit: 'шт.', limit: 3, used: 1, room: 2 }
    expect(roomFill(stops)).toBeCloseTo(2 / 3)
    expect(roomText(stops)).toBe('1 из 3')
  })
})
