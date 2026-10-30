import { describe, expect, it } from 'vitest'
import type { Curve } from '../model/types'
import { buildSeries, nearest, niceTicks, smoothPath } from './curve'

const DAY = 86400
const curve: Curve = {
  base: 5,
  total: 4,
  points: [[1000, 6, 1, 'A'], [1000 + 2 * DAY, 4, -2, 'B'], [1000 + 9 * DAY, 7, 3, 'C'], [1000 + 9 * DAY + 100, 8, 1, 'D']],
}

describe('buildSeries', () => {
  it('нет сделок: нет кривой', () => {
    expect(buildSeries(undefined, 'all', 0)).toBeNull()
    expect(buildSeries({ base: 0, points: [], total: 0 }, 'all', 0)).toBeNull()
  })

  it('«всё»: старт с базы, прирост к последнему', () => {
    const s = buildSeries(curve, 'all', 1000 + 10 * DAY)!
    expect(s.start).toBe(5)
    expect(s.last).toBe(8)
    expect(s.delta).toBe(3)
    expect(s.points).toHaveLength(4)
    expect(s.t0).toBeLessThan(1000)
  })

  it('неделя: старт это накопленный на начало периода, до него сделки не в списке', () => {
    const s = buildSeries(curve, 'week', 1000 + 10 * DAY)!
    expect(s.points.map((p) => p.sym)).toEqual(['C', 'D'])
    expect(s.start).toBe(4)
    expect(s.delta).toBe(4)
  })

  it('период без сделок: плоская линия на последнем уровне', () => {
    const s = buildSeries(curve, 'day', 1000 + 30 * DAY)!
    expect(s.points).toEqual([])
    expect(s.start).toBe(8)
    expect(s.delta).toBe(0)
  })
})

describe('niceTicks', () => {
  it('круглые числа внутри диапазона', () => {
    expect(niceTicks(-1.2, 3.1, 4)).toEqual([-1, 0, 1, 2, 3])
    expect(niceTicks(0, 100, 4)).toEqual([0, 20, 40, 60, 80, 100])
  })
  it('вырожденный диапазон', () => {
    expect(niceTicks(2, 2)).toEqual([2])
  })
})

describe('smoothPath', () => {
  it('проходит через точки и не даёт NaN', () => {
    const d = smoothPath([[0, 10], [10, 0], [20, 5], [30, 5]])
    expect(d.startsWith('M0.0 10.0C')).toBe(true)
    expect(d.endsWith('30.0 5.0')).toBe(true)
    expect(d).not.toContain('NaN')
    expect((d.match(/C/g) ?? []).length).toBe(3)
  })
  it('одинаковое время схлопывается, одна и две точки не ломаются', () => {
    expect((smoothPath([[0, 1], [5, 2], [5, 3], [9, 3]]).match(/C/g) ?? []).length).toBe(2)
    expect(smoothPath([[1, 2]])).toBe('M1.0 2.0')
    expect(smoothPath([])).toBe('')
    expect(smoothPath([[0, 0], [4, 2]])).not.toContain('NaN')
  })
  it('нет выброса выше соседей на плато', () => {
    const d = smoothPath([[0, 0], [10, 10], [20, 10], [30, 0]])
    const ys = [...d.matchAll(/C[\d.]+ ([\d.-]+) [\d.]+ ([\d.-]+)/g)].flatMap((m) => [Number(m[1]), Number(m[2])])
    expect(Math.max(...ys)).toBeLessThanOrEqual(10.0001)
  })
})

describe('nearest', () => {
  it('ближайшая по времени', () => {
    const p = buildSeries(curve, 'all', 5000)!.points
    expect(nearest(p, 1000 + 8 * DAY)).toBe(2)
    expect(nearest([], 1)).toBe(-1)
  })
})
