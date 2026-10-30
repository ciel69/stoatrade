import { describe, expect, it } from 'vitest'
import { BRAIN_NODES, BRAIN_REGIONS, nearestNode, regionOfNode, waveOrder } from './brain-regions'

describe('brain-regions', () => {
  it('каждый нейрон входит ровно в одну область, области симметричны по полушариям', () => {
    const all = BRAIN_REGIONS.flat().sort((a, b) => a - b)
    expect(all).toEqual(BRAIN_NODES.map((_, i) => i))
    for (const r of BRAIN_REGIONS) expect(r.filter((n) => n < 8).length).toBe(r.filter((n) => n >= 8).length)
  })
  it('regionOfNode', () => {
    expect(regionOfNode(0)).toBe(0)
    expect(regionOfNode(15)).toBe(3)
    expect(regionOfNode(99)).toBe(-1)
  })
  it('nearestNode: ближайший в пределах радиуса, иначе -1', () => {
    expect(nearestNode(19, 20)).toBe(0)
    expect(nearestNode(45.5, 20.5)).toBe(8)
    expect(nearestNode(32, 5, 4)).toBe(-1)
  })
  it('waveOrder: все нейроны, от ближайшего к дальнему', () => {
    const o = waveOrder(19, 20)
    expect(o).toHaveLength(16)
    expect(o[0]).toBe(0)
    expect(new Set(o).size).toBe(16)
  })
})
