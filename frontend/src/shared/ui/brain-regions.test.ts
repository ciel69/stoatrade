import { describe, expect, it } from 'vitest'
import { BRAIN_NODES, BRAIN_REGIONS, HALF, nearestNode, regionOfNode, waveOrder } from './brain-regions'

describe('brain-regions', () => {
  it('каждый нейрон входит ровно в одну область, области симметричны по полушариям', () => {
    const all = BRAIN_REGIONS.flat().sort((a, b) => a - b)
    expect(all).toEqual(BRAIN_NODES.map((_, i) => i))
    for (const r of BRAIN_REGIONS) expect(r.filter((n) => n < HALF).length).toBe(r.filter((n) => n >= HALF).length)
    for (const r of BRAIN_REGIONS) for (const n of r) if (n < HALF) expect(r).toContain(n + HALF)
  })
  it('regionOfNode', () => {
    expect(regionOfNode(0)).toBe(0)
    expect(regionOfNode(23)).toBe(3)
    expect(regionOfNode(99)).toBe(-1)
  })
  it('nearestNode: ближайший в пределах радиуса, иначе -1', () => {
    expect(nearestNode(BRAIN_NODES[0][0], BRAIN_NODES[0][1])).toBe(0)
    expect(nearestNode(BRAIN_NODES[HALF][0] + 0.5, BRAIN_NODES[HALF][1])).toBe(HALF)
    expect(nearestNode(32, 2, 4)).toBe(-1)
  })
  it('waveOrder: все нейроны, от ближайшего к дальнему', () => {
    const o = waveOrder(BRAIN_NODES[0][0], BRAIN_NODES[0][1])
    expect(o).toHaveLength(BRAIN_NODES.length)
    expect(o[0]).toBe(0)
    expect(new Set(o).size).toBe(BRAIN_NODES.length)
  })
})
