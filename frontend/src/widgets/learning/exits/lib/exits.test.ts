import { describe, expect, it } from 'vitest'
import type { Est, ExitsBlock } from '@/entities/learning'
import { capturePoints, clampShare, exitRows, pickSeries, windowText } from './exits'

const est = (v: number, n: number, half = 0.1): Est => ({ v, lo: v - half, hi: v + half, n, lvl: 0.95 })

describe('exits lib', () => {
  it('подписи окон без сырых ключей', () => {
    expect(windowText('50')).toBe('Последние 50')
    expect(windowText('all')).toBe('Весь период')
    expect(windowText('90')).toBe('Последние 90')
  })
  it('доля забранного в показе не выше 100%', () => {
    const c = clampShare(est(1.04, 20, 0.1))!
    expect(c.v).toBe(1)
    expect(c.hi).toBe(1)
    expect(c.lo).toBeCloseTo(0.94)
    expect(clampShare(null)).toBeNull()
  })
  it('строки: порядок окон, мало данных, доля сделок с путём', () => {
    const windows: ExitsBlock['windows'] = {
      all: { n_path: 80, path_share: 0.8, mfe: est(1.1, 80), capture: est(0.4, 80) },
      '200': { n_path: 80, path_share: 0.8, mfe: est(1.1, 80), capture: est(0.4, 80) },
      '50': { n_path: 12, path_share: 1, mfe: est(1.2, 12), capture: est(0.42, 12) },
    }
    const rows = exitRows(windows)
    expect(rows.map((r) => r.key)).toEqual(['50', '200', 'all'])
    expect(rows[0]!.small).toBe(true)
    expect(rows[1]!.small).toBe(false)
    expect(rows[0]!.pathShare).toBe('путь известен у 100% сделок')
    expect(rows[1]!.pathShare).toBe('путь известен у 80% сделок')
    expect(rows[0]!.capture.value).toBe('42%')
    expect(rows[0]!.mfe.value).toBe('1.20 R')
  })
  it('нет path_share: подпись пустая', () => {
    const rows = exitRows({ '50': { n_path: 5, path_share: null, mfe: null, capture: null } })
    expect(rows[0]!.pathShare).toBe('')
    expect(rows[0]!.capture.value).toBe('–')
  })
  it('ряд: берётся окно 50, при пустом 200', () => {
    const pt = (i: number) => ({ i, ts: null, capture: est(0.4, 30), mfe: est(1, 30) })
    expect(pickSeries({ '50': [pt(30), pt(31)], '200': [pt(30)] })?.key).toBe('50')
    expect(pickSeries({ '50': [], '200': [pt(30)] })?.key).toBe('200')
    expect(pickSeries({ '50': [], '200': [] })).toBeNull()
    expect(pickSeries({})).toBeNull()
  })
  it('точки без оценки пропускаются', () => {
    expect(capturePoints([{ i: 30, capture: null }, { i: 31, capture: est(0.5, 30) }])).toHaveLength(1)
  })
})
