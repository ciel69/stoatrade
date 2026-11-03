import { describe, expect, it } from 'vitest'
import { NAV_GROUPS, isActive, sectionOf } from './nav'

const items = NAV_GROUPS.flatMap((g) => g.items)

describe('nav', () => {
  it('счётчик «ждёт ответа» стоит на пункте «Торговля» и на вкладке «День», больше нигде', () => {
    const withBadge = items.filter((i) => i.badge)
    expect(withBadge.map((i) => i.label)).toEqual(['Торговля'])
    expect(items.flatMap((i) => i.tabs ?? []).filter((t) => t.badge).map((t) => t.to)).toEqual(['/trading/day'])
  })
  it('пути и названия пунктов не повторяются, у каждого есть значок и путь с косой чертой', () => {
    expect(new Set(items.map((i) => i.to)).size).toBe(items.length)
    expect(new Set(items.map((i) => i.label)).size).toBe(items.length)
    for (const i of items) { expect(i.icon.length).toBeGreaterThan(5); expect(i.to.startsWith('/')).toBe(true) }
  })
  it('вкладки: пути не повторяются между пунктами, пункт ведёт на первую вкладку и лежит под своим префиксом', () => {
    const tabs = items.flatMap((i) => i.tabs ?? [])
    expect(new Set(tabs.map((t) => t.to)).size).toBe(tabs.length)
    for (const i of items.filter((x) => x.tabs)) {
      expect(i.tabs![0].to).toBe(i.to)
      for (const t of i.tabs!) expect(isActive(i, t.to)).toBe(true)
    }
  })
  it('активность: по префиксу у пунктов с вкладками, точная у остальных; «/market» не захватывает «/marketing»', () => {
    const market = items.find((i) => i.label === 'Рынок')!
    const home = items.find((i) => i.to === '/')!
    expect(isActive(market, '/market')).toBe(true)
    expect(isActive(market, '/market/news')).toBe(true)
    expect(isActive(market, '/marketing')).toBe(false)
    expect(isActive(home, '/')).toBe(true)
    expect(isActive(home, '/trading/plans')).toBe(false)
  })
  it('полоса вкладок: для пункта с вкладками найдена, для обзора нет', () => {
    expect(sectionOf('/trading/plans')?.label).toBe('Торговля')
    expect(sectionOf('/brain/brief')?.tabs?.map((t) => t.label)).toEqual(['Решения', 'Досье', 'Учится ли мозг'])
    expect(sectionOf('/brain/learning')?.label).toBe('Мозг')
    expect(sectionOf('/system/control')?.label).toBe('Система')
    expect(sectionOf('/system/notify')?.tabs?.map((t) => t.label)).toEqual(['Управление', 'Настройки', 'Уведомления'])
    expect(sectionOf('/trading/day')?.tabs?.map((t) => t.label)).toEqual(['Сделки', 'Планы', 'Идеи', 'День', 'Эксперименты'])
    expect(sectionOf('/')).toBeNull()
    expect(sectionOf('/unknown')).toBeNull()
  })
})
