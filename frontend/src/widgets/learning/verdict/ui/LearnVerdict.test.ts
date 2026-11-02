import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { mockLearning, type Est } from '@/entities/learning'
import { guardRows, rangeGeom } from '../lib/verdict'
import LearnVerdict from './LearnVerdict.vue'

const verdictOf = (kind: Parameters<typeof mockLearning>[0]) => mockLearning(kind, 'paper').verdict
const est = (v: number, lo: number | null, hi: number | null): Est => ({ v, lo, hi, n: 50, lvl: 0.95 })

describe('guardRows', () => {
  it('пройдена, не пройдена, не проверялась; guard_unknown не выдаётся за проверку', () => {
    expect(guardRows({ viol: { ok: true }, trades_day: { ok: false } }).map((g) => g.state)).toEqual(['ok', 'bad'])
    expect(guardRows(null).map((g) => g.state)).toEqual(['none', 'none'])
    const unk = guardRows({ viol: { ok: true, guard_unknown: true }, trades_day: { ok: true } })[0]!
    expect(unk.mark).toBe('не проверялась')
    expect(unk.text).toContain('данных мало')
  })
})

describe('rangeGeom', () => {
  it('нуль в центре; интервал по одну сторону от нуля отмечен', () => {
    const g = rangeGeom(est(0.3, 0.1, 0.5), 100)!
    expect(g.zero).toBe(50)
    expect(g.clear).toBe(true)
    expect(g.lo).toBeLessThan(g.v)
    expect(g.v).toBeLessThan(g.hi)
    expect(rangeGeom(est(0.1, -0.2, 0.4), 100)!.clear).toBe(false)
  })
  it('без границ: точка без ленты; без значения: null', () => {
    const g = rangeGeom(est(0.2, null, null), 100)!
    expect(g.lo).toBe(g.hi)
    expect(g.clear).toBe(false)
    expect(rangeGeom(null, 100)).toBeNull()
    expect(rangeGeom({ v: null, lo: null, hi: null, n: 3, lvl: 0.95 }, 100)).toBeNull()
  })
  it('нулевая разность не даёт NaN', () => {
    const g = rangeGeom(est(0, 0, 0), 100)!
    expect([g.lo, g.hi, g.v].every(Number.isFinite)).toBe(true)
  })
})

describe('LearnVerdict', () => {
  it('три слова вердикта', () => {
    expect(mount(LearnVerdict, { props: { verdict: verdictOf('mock-few') } }).find('.word').text()).toBe('Данных мало')
    expect(mount(LearnVerdict, { props: { verdict: verdictOf('mock') } }).find('.word').text()).toBe('Разницы не видно')
    expect(mount(LearnVerdict, { props: { verdict: verdictOf('mock-confirmed') } }).find('.word').text()).toBe('Улучшение подтверждено')
  })
  it('подтверждено: пояснение, n, интервал разности, защиты словами', () => {
    const w = mount(LearnVerdict, { props: { verdict: verdictOf('mock-confirmed') } })
    expect(w.find('.text').text()).toContain('Недавние 50 сделок лучше прежних')
    expect(w.text()).toContain('121')
    expect(w.text()).toMatch(/от \+0\.\d\d до \+0\.\d\d/)
    expect(w.text()).toContain('Нарушений правил не стало больше')
    expect(w.find('.warn').exists()).toBe(false)
  })
  it('мало данных: разность «пока не считается», защит нет', () => {
    const w = mount(LearnVerdict, { props: { verdict: verdictOf('mock-few') } })
    expect(w.text()).toContain('пока не считается')
    expect(w.find('.guards').exists()).toBe(false)
  })
  it('trend=down даёт заметное предупреждение, состояние остаётся «Разницы не видно»', () => {
    const v = { ...verdictOf('mock'), trend: 'down' as const }
    const w = mount(LearnVerdict, { props: { verdict: v } })
    expect(w.find('.warn').text()).toContain('Стало хуже')
    expect(w.find('.word').text()).toBe('Разницы не видно')
  })
  it('нет сырых кодов и эмодзи', () => {
    const t = mount(LearnVerdict, { props: { verdict: verdictOf('mock') } }).text()
    expect(t).not.toMatch(/not_significant|no_baseline|n_lt_min/)
    expect(t).not.toMatch(/\p{Extended_Pictographic}/u)
  })
})
