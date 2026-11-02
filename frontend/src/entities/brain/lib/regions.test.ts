import { describe, expect, it } from 'vitest'
import { assignRegions, rolesOfRegion } from './regions'

describe('regions', () => {
  it('известные роли держат свою область независимо от порядка', () => {
    const a = assignRegions(['news_reader', 'entry_scout', 'position_manager'])
    const b = assignRegions(['position_manager', 'entry_scout', 'news_reader'])
    expect(a).toEqual(b)
    expect(a).toEqual({ entry_scout: 0, position_manager: 1, news_reader: 2 })
  })
  it('новые роли занимают свободные области, лишние делят', () => {
    const m = assignRegions(['entry_scout', 'zeta', 'alpha', 'beta'])
    expect(m.entry_scout).toBe(0)
    expect(new Set([m.alpha, m.beta, m.zeta]).size).toBe(3)       // три свободные области: 1, 2, 3
    const many = assignRegions(['a', 'b', 'c', 'd', 'e', 'f'])
    expect(Math.max(...Object.values(many))).toBeLessThan(4)
  })
  it('повтор роли не плодит записей, rolesOfRegion', () => {
    const m = assignRegions(['news_reader', 'news_reader'])
    expect(Object.keys(m)).toEqual(['news_reader'])
    expect(rolesOfRegion(m, 2)).toEqual(['news_reader'])
    expect(rolesOfRegion(m, 0)).toEqual([])
  })
})
