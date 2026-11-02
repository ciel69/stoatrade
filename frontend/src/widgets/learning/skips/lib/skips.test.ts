import { describe, expect, it } from 'vitest'
import type { Est, SkipsWeek } from '@/entities/learning'
import { correctPoints, isFreezing, missedView, signalChip, skipRows, totalView } from './skips'

const est = (v: number, n: number, half = 0.1): Est => ({ v, lo: v - half, hi: v + half, n, lvl: 0.95 })
const wk = (i: number, over: Partial<SkipsWeek> = {}): SkipsWeek => ({ week: `2026-W${String(i).padStart(2, '0')}`, n: 40, correct: est(0.55, 40), missed_pct: est(0.35, 17, 0.2), trades: 12, ...over })

describe('skips lib', () => {
  it('упущенный ход в процентах цены, не в R и не доля × 100', () => {
    const v = missedView(est(0.35, 17, 0.2))
    expect(v.value).toBe('0.35%')
    expect(v.interval).toBe('от 0.15% до 0.55%')
    expect(v.value).not.toContain('R')
    expect(missedView(null).value).toBe('–')
    expect(missedView({ v: 0.1, lo: null, hi: null, n: 1, lvl: 0.95 }).interval).toBe('')
  })
  it('строки: неполная неделя и мало данных', () => {
    const rows = skipRows([wk(43, { n: 12 }), wk(44, { partial: true })])
    expect(rows[0]!.small).toBe(true)
    expect(rows[1]!.partial).toBe(true)
    expect(rows[0]!.correct.value).toBe('55%')
    expect(rows[0]!.trades).toBe(12)
  })
  it('точки графика без недель без оценки', () => {
    expect(correctPoints([wk(42, { correct: null }), wk(43)])).toHaveLength(1)
  })
  it('итог: неизвестная форма даёт null, поля читаются осторожно', () => {
    expect(totalView(null)).toBeNull()
    expect(totalView({})).toBeNull()
    const t = totalView({ n: 90, correct: est(0.6, 90), wrong_n: 36, missed_pct_sum: 12.345, missed_pct_mean: 0.3429 })
    expect(t?.wrong).toBe(36)
    expect(t?.missedMean).toBe('0.34%')
    expect(t?.missedSum).toBe('12.3%')
    expect(t?.correct.value).toBe('60%')
  })
  it('замораживание: доля верных растёт, сделок на треть меньше', () => {
    const calm = Array.from({ length: 8 }, (_, i) => wk(i + 1, { trades: i < 4 ? 12 : 11 }))
    const frozen = Array.from({ length: 8 }, (_, i) => wk(i + 1, { trades: i < 4 ? 12 : 5 }))
    expect(isFreezing(calm, 'improving')).toBe(false)
    expect(isFreezing(frozen, 'improving')).toBe(true)
    expect(isFreezing(frozen, 'flat')).toBe(false)
    expect(isFreezing(frozen.slice(0, 5), 'improving')).toBe(false)
  })
  it('сигнал', () => {
    expect(signalChip('improving')?.tone).toBe('ok')
    expect(signalChip('worse')?.tone).toBe('bad')
    expect(signalChip(null)).toBeNull()
  })
})
