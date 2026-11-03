import { describe, expect, it } from 'vitest'
import type { DisciplineWeek, Est } from '@/entities/learning'
import { crossesYear, signalChip, tagRows, tagText, violPoints, weekLabel, weekRows } from './discipline'

const est = (v: number, n: number, half = 0.1): Est => ({ v, lo: v - half, hi: v + half, n, lvl: 0.95 })
const week = (w: string, over: Partial<DisciplineWeek> = {}): DisciplineWeek => ({ week: w, partial: false, n_cases: 12, viol: est(0.3, 11), n_rf: 11, err_any: est(0.4, 12), err_top: [], ...over })

describe('discipline lib', () => {
  it('теги словами, без сырых кодов', () => {
    expect(tagText('late_entry')).toBe('поздний вход')
    expect(tagText('no-confirm')).toBe('вход без подтверждения')
    expect(tagText('some_new_tag')).toBe('some new tag')
    expect(tagText('')).toBe('без названия')
  })
  it('подпись недели и год на границе', () => {
    expect(weekLabel('2026-W44')).toBe('нед. 44')
    expect(weekLabel('2026-W53', true)).toBe('нед. 53, 2026')
    expect(crossesYear([{ week: '2026-W53' }, { week: '2027-W01' }])).toBe(true)
  })
  it('строки: неполная неделя, мало данных, интервал', () => {
    const rows = weekRows([week('2026-W43'), week('2026-W44', { partial: true, n_cases: 40 })])
    expect(rows[0]!.small).toBe(true)
    expect(rows[1]!.small).toBe(false)
    expect(rows[1]!.partial).toBe(true)
    expect(rows[0]!.viol.value).toBe('30%')
    expect(rows[0]!.viol.interval).toBe('от 20% до 40%')
  })
  it('точки графика пропускают недели без оценки и метят неполную', () => {
    const pts = violPoints([week('2026-W42', { viol: null, n_cases: 0 }), week('2026-W43'), week('2026-W44', { partial: true })])
    expect(pts).toHaveLength(2)
    expect(pts[1]!.i).toBe('нед. 44 (неполная)')
    expect(pts[0]!.lo).toBeCloseTo(0.2)
  })
  it('оценка без интервала не придумывает границ', () => {
    const pts = violPoints([week('2026-W43', { viol: { v: 0, lo: null, hi: null, n: 1, lvl: 0.95 } })])
    expect(pts[0]!.lo).toBeNull()
  })
  it('теги ошибок: доля ограничена [0, 1]', () => {
    const rows = tagRows({ err_total: [{ tag: 'late_entry', share: est(1.4, 10), n: 10 }, { tag: 'chase', share: null, n: 0 }] })
    expect(rows[0]!.share).toBe(1)
    expect(rows[1]!.share).toBe(0)
    expect(rows[1]!.value).toBe('–')
  })
  it('сигнал', () => {
    expect(signalChip('falling')?.tone).toBe('ok')
    expect(signalChip('rising')?.tone).toBe('bad')
    expect(signalChip(null)).toBeNull()
  })
})
