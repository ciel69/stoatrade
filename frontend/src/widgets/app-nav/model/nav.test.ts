import { describe, expect, it } from 'vitest'
import { NAV_GROUPS } from './nav'

describe('nav', () => {
  it('пути и названия разделов не повторяются', () => {
    const all = NAV_GROUPS.flatMap((g) => g.items)
    expect(new Set(all.map((i) => i.to)).size).toBe(all.length)
    expect(new Set(all.map((i) => i.label)).size).toBe(all.length)
  })
  it('у каждого раздела есть значок и путь с косой чертой', () => {
    for (const g of NAV_GROUPS) for (const i of g.items) {
      expect(i.icon.length).toBeGreaterThan(5)
      expect(i.to.startsWith('/')).toBe(true)
    }
  })
})
