import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { cleanPoints, domainOf, fmtNum, fmtSigned, layoutOf, nearestIndex, pointText, stepIndex, summaryText, type BandPoint } from './band-chart'
import UiBandChart from './UiBandChart.vue'

const p = (i: number, v: number, lo: number | null = null, hi: number | null = null, n: number | null = null): BandPoint => ({ i, v, lo, hi, n })

describe('форматирование', () => {
  it('минус настоящий, отрицательный нуль без знака', () => {
    expect(fmtNum(-0.5)).toBe('−0.50')
    expect(fmtNum(-0.001)).toBe('0.00')
    expect(fmtSigned(0.2)).toBe('+0.20')
    expect(fmtNum(NaN)).toBe('—')
  })
})

describe('cleanPoints и domainOf', () => {
  it('нечисловые значения отбрасываются, перепутанные границы упорядочиваются, половина интервала равна его отсутствию', () => {
    const r = cleanPoints([p(1, NaN), p(2, 1, 3, 0), p(3, 2, 1, null), { i: 4, v: 0.5 }])
    expect(r.map((x) => x.i)).toEqual([2, 3, 4])
    expect(r[0]).toMatchObject({ lo: 0, hi: 3 })
    expect(r[1]).toMatchObject({ lo: null, hi: null })
  })
  it('домен всегда содержит нуль, плоский ряд расширяется', () => {
    expect(domainOf([p(1, 0.4, 0.2, 0.9)]).min).toBe(0)
    const d = domainOf([p(1, -2, -3, -1)])
    expect(d.max).toBe(0)
    expect(d.min).toBeLessThan(-3)
    expect(domainOf([p(1, 0)])).toEqual({ min: -1, max: 1 })
  })
})

describe('layoutOf', () => {
  it('нет точек: пусто, без ошибок', () => {
    const l = layoutOf([], 390, 120)
    expect(l.pts).toEqual([])
    expect(l.line).toBe('')
    expect(l.bands).toEqual([])
  })
  it('одна точка: по центру, без ломаной, интервал усом', () => {
    const l = layoutOf([p(30, 0.2, -0.1, 0.5)], 390, 120)
    expect(l.xs).toHaveLength(1)
    expect(l.line).toBe('')
    expect(l.bands).toEqual([])
    expect(l.whiskers).toHaveLength(1)
    expect(l.whiskers[0]!.y1).toBeLessThan(l.whiskers[0]!.y2)
  })
  it('интервал null: лента не рисуется, ряд есть', () => {
    const l = layoutOf([p(1, 0.1), p(2, 0.2), p(3, 0.3)], 390, 120)
    expect(l.line.startsWith('M')).toBe(true)
    expect(l.bands).toEqual([])
    expect(l.whiskers).toEqual([])
  })
  it('разрыв интервала режет ленту на два участка', () => {
    const l = layoutOf([p(1, 0, -1, 1), p(2, 0, -1, 1), p(3, 0), p(4, 0, -1, 1), p(5, 0, -1, 1)], 390, 120)
    expect(l.bands).toHaveLength(2)
  })
  it('отрицательные значения лежат ниже нуля, больше значение выше на экране', () => {
    const l = layoutOf([p(1, -1, -2, 0), p(2, 1, 0, 2)], 390, 120)
    expect(l.ys[0]!).toBeGreaterThan(l.zeroY)
    expect(l.ys[1]!).toBeLessThan(l.zeroY)
    expect(l.ticks.some((t) => t.zero)).toBe(true)
  })
  it('ширина 390 px: все x внутри полей', () => {
    const l = layoutOf(Array.from({ length: 120 }, (_, k) => p(k, Math.sin(k / 9), -1, 1)), 390, 120)
    expect(Math.min(...l.xs)).toBeGreaterThanOrEqual(38)
    expect(Math.max(...l.xs)).toBeLessThanOrEqual(380)
    for (const y of l.ys) expect(y).toBeGreaterThanOrEqual(0)
  })
  it('подпись у края не налезает на подпись нуля', () => {
    const l = layoutOf([p(1, 0.001), p(2, 0.002)], 390, 120)
    const zero = l.ticks.find((t) => t.zero)!
    for (const t of l.ticks.filter((x) => !x.zero)) expect(Math.abs(t.y - zero.y)).toBeGreaterThanOrEqual(14)
  })
})

describe('навигация', () => {
  it('ближайшая точка по x', () => {
    expect(nearestIndex([10, 20, 30], 26)).toBe(2)
    expect(nearestIndex([], 5)).toBe(-1)
  })
  it('стрелки идут от последней точки и не выходят за границы', () => {
    expect(stepIndex(null, 5, 'ArrowLeft')).toBe(4)
    expect(stepIndex(0, 5, 'ArrowLeft')).toBe(0)
    expect(stepIndex(4, 5, 'ArrowRight')).toBe(4)
    expect(stepIndex(2, 5, 'Home')).toBe(0)
    expect(stepIndex(2, 5, 'End')).toBe(4)
    expect(stepIndex(2, 5, 'a')).toBeNull()
    expect(stepIndex(null, 0, 'Home')).toBeNull()
  })
})

describe('тексты', () => {
  const f = (v: number) => fmtNum(v)
  it('точка с интервалом и без', () => {
    expect(pointText(p(31, 0.12, -0.1, 0.34, 31), f, 'R')).toBe('31: 0.12 R · интервал −0.10 … 0.34 · n = 31')
    expect(pointText(p(5, 0.1), f)).toBe('5: 0.10 · интервала нет')
  })
  it('сводка: нуль относительно интервала, склонение', () => {
    expect(summaryText('R', [p(1, 0.5, 0.1, 0.9, 40)], f, 'R')).toContain('нуль ниже интервала')
    expect(summaryText('R', [p(1, -0.5, -0.9, -0.1)], f)).toContain('нуль выше интервала')
    expect(summaryText('R', [p(1, 0, -1, 1), p(2, 0, -1, 1)], f)).toContain('2 точки')
    expect(summaryText('R', [], f)).toBe('R: данных нет')
  })
})

describe('UiBandChart', () => {
  const mk = (points: BandPoint[], width = 390) => mount(UiBandChart, { props: { points, label: 'Средний R', unit: 'R', xLabel: 'Сделка', width } })

  it('нет точек: текст вместо графика', () => {
    const w = mk([])
    expect(w.find('svg').exists()).toBe(false)
    expect(w.text()).toContain('Данных пока нет')
    expect(w.find('[role="img"]').attributes('aria-label')).toBe('Средний R: данных нет')
  })
  it('одна точка и интервал null не падают, таблица и aria на месте', () => {
    const w = mk([p(30, 0.2)])
    expect(w.find('svg').attributes('width')).toBe('390')
    expect(w.find('path.line').exists()).toBe(false)
    expect(w.find('path.band').exists()).toBe(false)
    expect(w.findAll('tbody tr')).toHaveLength(1)
    expect(w.find('.chart').attributes('aria-label')).toContain('последнее 0.20 R')
  })
  it('отрицательные значения и ширина 390: ряд, лента, нуль, последняя точка', () => {
    const w = mk([p(1, -0.4, -0.8, 0), p(2, -0.2, -0.5, 0.1), p(3, 0.1, -0.2, 0.4)])
    expect(w.find('path.line').exists()).toBe(true)
    expect(w.findAll('path.band')).toHaveLength(1)
    expect(w.find('line.zero').exists()).toBe(true)
    expect(w.find('circle.last').exists()).toBe(true)
  })
  it('клавиатура: стрелка показывает подсказку, Esc убирает', async () => {
    const w = mk([p(1, 0.1, 0, 0.2, 30), p(2, 0.2, 0.1, 0.3, 31)])
    const c = w.find('.chart')
    await c.trigger('keydown', { key: 'ArrowLeft' })
    expect(w.find('.tip').text()).toContain('2: 0.20 R')
    await c.trigger('keydown', { key: 'ArrowLeft' })
    expect(w.find('.tip').text()).toContain('1: 0.10 R')
    await c.trigger('keydown', { key: 'Escape' })
    expect(w.find('.tip').exists()).toBe(false)
  })
  it('точек стало меньше, чем выбранный индекс: подсказка не ломается', async () => {
    const w = mk([p(1, 0.1), p(2, 0.2), p(3, 0.3)])
    await w.find('.chart').trigger('keydown', { key: 'End' })
    await w.setProps({ points: [p(1, 0.1)] })
    expect(w.find('.tip').exists()).toBe(false)
  })
})
