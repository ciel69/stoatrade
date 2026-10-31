import { describe, expect, it } from 'vitest'
import { squarify } from './treemap'

const box = { x: 0, y: 0, w: 400, h: 200 }
const items = [30, 20, 15, 10, 8, 6, 5, 3, 2, 1].map((w, i) => ({ key: `c${i}`, weight: w }))

describe('squarify', () => {
  it('площадь пропорциональна весу, плитки заполняют рамку без зазоров', () => {
    const m = squarify(items, box)
    const total = items.reduce((a, b) => a + b.weight, 0)
    let area = 0
    for (const it of items) {
      const r = m.get(it.key)!
      expect(r.w * r.h).toBeCloseTo((it.weight / total) * box.w * box.h, 3)
      expect(r.x).toBeGreaterThanOrEqual(-1e-9)
      expect(r.y).toBeGreaterThanOrEqual(-1e-9)
      expect(r.x + r.w).toBeLessThanOrEqual(box.w + 1e-6)
      expect(r.y + r.h).toBeLessThanOrEqual(box.h + 1e-6)
      area += r.w * r.h
    }
    expect(area).toBeCloseTo(box.w * box.h, 3)
  })
  it('плитки не пересекаются', () => {
    const rs = [...squarify(items, box).values()]
    for (let a = 0; a < rs.length; a++) {
      for (let b = a + 1; b < rs.length; b++) {
        const ox = Math.min(rs[a].x + rs[a].w, rs[b].x + rs[b].w) - Math.max(rs[a].x, rs[b].x)
        const oy = Math.min(rs[a].y + rs[a].h, rs[b].y + rs[b].h) - Math.max(rs[a].y, rs[b].y)
        expect(ox > 1e-6 && oy > 1e-6).toBe(false)
      }
    }
  })
  it('нулевые веса и пустая рамка дают пустой результат, одна плитка занимает всё', () => {
    expect(squarify([{ key: 'a', weight: 0 }], box).size).toBe(0)
    expect(squarify(items, { x: 0, y: 0, w: 0, h: 10 }).size).toBe(0)
    expect(squarify([{ key: 'a', weight: 5 }], box).get('a')).toEqual(box)
  })
  it('раскладка не зависит от порядка во входе', () => {
    const a = squarify(items, box)
    const b = squarify(items.slice().reverse(), box)
    expect(b.get('c0')).toEqual(a.get('c0'))
  })
})
