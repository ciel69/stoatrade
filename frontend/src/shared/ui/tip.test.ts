import { describe, expect, it } from 'vitest'
import { hasTip, placeFloating } from './tip'

const box = (left: number, top: number, width = 40, height = 20) => ({ left, top, width, height })

describe('placeFloating', () => {
  it('по центру элемента и ниже него', () => {
    const p = placeFloating(box(100, 50), { width: 100, height: 40 }, 390, 800)
    expect(p).toEqual({ x: 70, y: 78, side: 'below' })
  })

  it('не выходит за левый и правый край окна на 390 px', () => {
    expect(placeFloating(box(0, 50), { width: 200, height: 40 }, 390, 800).x).toBe(8)
    expect(placeFloating(box(360, 50), { width: 200, height: 40 }, 390, 800).x).toBe(390 - 200 - 8)
  })

  it('внизу окна уходит выше элемента', () => {
    const p = placeFloating(box(100, 760), { width: 100, height: 60 }, 390, 800)
    expect(p.side).toBe('above')
    expect(p.y).toBe(760 - 8 - 60)
  })

  it('шире окна: прижимается к левому краю', () => {
    expect(placeFloating(box(50, 10), { width: 500, height: 40 }, 390, 800).x).toBe(8)
  })

  it('нигде не помещается: остаётся ниже', () => {
    expect(placeFloating(box(10, 20), { width: 100, height: 900 }, 390, 800).side).toBe('below')
  })
})

describe('hasTip', () => {
  it('пустое содержимое не показывается', () => {
    expect(hasTip(null)).toBe(false)
    expect(hasTip({})).toBe(false)
    expect(hasTip({ rows: [] })).toBe(false)
    expect(hasTip({ text: 'x' })).toBe(true)
  })
})
