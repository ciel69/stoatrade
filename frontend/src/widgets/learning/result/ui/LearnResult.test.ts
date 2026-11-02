import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { mockLearning, type LearningBlock } from '@/entities/learning'
import { asResult, chartPoints, statsOf, whereOfZero } from '../lib/result'
import LearnResult from './LearnResult.vue'

const block = (kind: Parameters<typeof mockLearning>[0]): LearningBlock => mockLearning(kind, 'paper').blocks.result!

describe('lib/result', () => {
  it('asResult: блок без окон не считается результатом', () => {
    expect(asResult({ id: 'result', title: 'Результат', status: 'ok', note: '' })).toBeNull()
    expect(asResult(block('mock'))).not.toBeNull()
  })
  it('chartPoints отбрасывает точки без значения и несёт интервал и n', () => {
    const pts = chartPoints([
      { i: 30, ts: 1, n: 30, r: { v: 0.1, lo: -0.2, hi: 0.4, n: 30, lvl: 0.95 }, pf: 1 },
      { i: 31, ts: 2, n: 31, r: null, pf: null },
      { i: 32, ts: 3, n: 32, r: { v: 0.2, lo: null, hi: null, n: 32, lvl: 0.95 }, pf: 1 },
    ])
    expect(pts).toEqual([{ i: 30, v: 0.1, lo: -0.2, hi: 0.4, n: 30 }, { i: 32, v: 0.2, lo: null, hi: null, n: 32 }])
    expect(chartPoints(undefined)).toEqual([])
  })
  it('whereOfZero: выше, ниже, нуль внутри, интервала нет', () => {
    const w = (lo: number | null, hi: number | null) => ({ n: 50, r: { v: 0.1, lo, hi, n: 50, lvl: 0.95 }, win: null, pf: null, pf_inf: false, fees_share: null, small: false })
    expect(whereOfZero(w(0.1, 0.5), undefined)).toBe('above')
    expect(whereOfZero(w(-0.5, -0.1), false)).toBe('below')
    expect(whereOfZero(w(-0.1, 0.5), false)).toBe('zero')
    expect(whereOfZero(w(null, null), true)).toBe('none')
    expect(whereOfZero(null, undefined)).toBe('none')
  })
  it('statsOf: у каждого числа есть n; доля издержек помечена расчётной; мало данных подписано', () => {
    const r = asResult(block('mock-few'))!
    const s = statsOf(r.windows['50'])
    expect(s.map((x) => x.id)).toEqual(['r', 'win', 'pf', 'fees'])
    expect(s.every((x) => /^n = \d+$/.test(x.n))).toBe(true)
    expect(s[3]!.note).toBe('расчётная величина')
    expect(s[0]!.note).toContain('мало данных')
    expect(statsOf(null)).toEqual([])
  })
  it('statsOf: PF без убытков словами, а не бесконечностью', () => {
    const r = asResult(block('mock'))!
    const s = statsOf({ ...r.windows['50']!, pf_inf: true })
    expect(s[2]!.value).toBe('без убыточных сделок')
  })
})

describe('LearnResult', () => {
  it('данные: график, четыре числа, переключатель окна 50/200 по умолчанию 50', async () => {
    const w = mount(LearnResult, { props: { block: block('mock') } })
    expect(w.findAll('.stat')).toHaveLength(4)
    expect(w.find('.seg').exists()).toBe(true)
    const on = w.find('.opt.on')
    expect(on.text()).toContain('50')
    await w.findAll('.opt')[1]!.trigger('click')
    expect(w.find('.opt.on').text()).toContain('200')
    expect(w.text()).toContain('Результат')
  })
  it('мало данных: ряда нет, подпись «мало данных (n < 30)» и текст вместо графика', () => {
    const w = mount(LearnResult, { props: { block: block('mock-few') } })
    expect(w.text()).toContain('мало данных (n < 30)')
    expect(w.text()).toContain('Ряд начнётся с 30-й закрытой сделки')
  })
  it('нет данных: причина словами, без сырых кодов и без графика', () => {
    const w = mount(LearnResult, { props: { block: block('mock-empty') } })
    expect(w.text()).toContain('в журнале нет закрытых сделок')
    expect(w.text()).not.toContain('no_trades')
    expect(w.find('.chart').exists()).toBe(false)
  })
  it('таблица-альтернатива содержит строки ряда', () => {
    const w = mount(LearnResult, { props: { block: block('mock') } })
    expect(w.findAll('.tbl tbody tr').length).toBeGreaterThan(0)
  })
})
