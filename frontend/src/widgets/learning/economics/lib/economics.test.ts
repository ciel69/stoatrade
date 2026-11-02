import { describe, expect, it } from 'vitest'
import { mockLearning, type EconomicsBlock } from '@/entities/learning'
import { economicsView, spendText, tokensText, weekLabel } from './economics'

const mock = (): EconomicsBlock => mockLearning('mock', 'paper').blocks.economics as EconomicsBlock

describe('форматы блока «Экономика»', () => {
  it('токены сжимаются, пустое даёт прочерк', () => {
    expect(tokensText(850)).toBe('850')
    expect(tokensText(12400)).toBe('12.4 тыс.')
    expect(tokensText(3000000)).toBe('3.0 млн')
    expect(tokensText(null)).toBe('–')
  })
  it('нулевой расход локальной модели это «$0»', () => {
    expect(spendText(0)).toBe('$0')
    expect(spendText(0.08)).toBe('$0.0800')
    expect(spendText(null)).toBe('–')
  })
  it('подпись недели; год только по требованию; чужой формат остаётся', () => {
    expect(weekLabel('2026-W04', false)).toBe('нед. 4')
    expect(weekLabel('2026-W44', true)).toBe('нед. 44, 2026')
    expect(weekLabel('x', false)).toBe('x')
  })
})

describe('economicsView', () => {
  it('новая неделя сверху, итоги считаются по неделям, расход на R только при положительном R', () => {
    const v = economicsView(mock())
    expect(v.rows[0]!.label).toBe('нед. 44')
    expect(v.rows[0]!.partial).toBe(true)
    expect(v.rows).toHaveLength(8)
    expect(v.totals.trades).toBe(10 + 11 + 12 + 13 + 14 + 10 + 11 + 12)
    expect(v.totals.perRNote).toBe('расчётная величина')
    expect(v.chart.every((p) => p.v > 0)).toBe(true)
  })
  it('неделя без сделок: отношения прочерком, расход виден', () => {
    const b = mock()
    b.weeks = [{ ...b.weeks[0]!, n_trades: 0, r_sum: null, usd_per_trade: null, usd_per_r: null }]
    const v = economicsView(b)
    expect(v.rows[0]!.perTrade).toBe('–')
    expect(v.rows[0]!.perR).toBe('–')
    expect(v.rows[0]!.spend).not.toBe('–')
    expect(v.totals.perR).toBe('–')
    expect(v.totals.perRNote).toBe('нет, пока результат не положителен')
  })
  it('сигнал словами; без сигнала объяснение; отсутствие weeks не ломает', () => {
    expect(economicsView({ ...mock(), signal: 'falling' }).signal).toContain('снижается')
    expect(economicsView(mock()).signal).toContain('не определена')
    expect(economicsView({ ...mock(), weeks: undefined as never }).rows).toEqual([])
  })
  it('в тексте нет сырых кодов и денег счёта', () => {
    const v = economicsView({ ...mock(), signal: 'weird' })
    expect(v.signal).not.toContain('weird')
  })
})
