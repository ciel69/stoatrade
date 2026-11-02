import { describe, expect, it } from 'vitest'
import { defaultTab, ideaRisk, ideaRoute, ideasOf, lastOutcome, levelWait, tabCount } from './idea'
import type { IdeaItem, IdeasSnapshot } from '../model/types'

const idea = (o: Partial<IdeaItem>): IdeaItem => ({
  id: 'I', sym: 'SOL', side: 'long', status: 'watching', note: null, tag: null, level: 100, stop: 98, tp: 106, price: 101, now_price: 102, dist_level_pct: 2, shift_pct: null, zone: false,
  ts: null, ts_epoch: null, expires_epoch: null, plan_id: null, trade_id: null, trade: null, mark_notes: [], outcomes: {}, ...o,
})
const snap = (items: IdeaItem[], counts: Record<string, number> = {}): IdeasSnapshot => ({
  available: true, counts, items, auto_recent: [], pending: 0,
  stats: { costs: { taker_rt_pct: 0.24 }, horizons: [15, 60], groups: [], idea_groups: [], n_events: 3, agent: { total: items.length, taken_share: null }, pending: 0, comparisons: 0 },
})

describe('идеи', () => {
  it('ждущие уровня: ближайшие к уровню первыми, без цены в конце', () => {
    const s = snap([idea({ id: 'A', dist_level_pct: 3 }), idea({ id: 'B', dist_level_pct: null }), idea({ id: 'C', dist_level_pct: -0.4 })])
    expect(ideasOf(s, 'active').map((i) => i.id)).toEqual(['C', 'A', 'B'])
  })
  it('вкладки считаются по статусам; авто берёт число событий сканера', () => {
    const s = snap([], { watching: 2, idea: 1, planned: 1, taken: 2, skipped: 4, auto_skipped: 9 })
    expect([tabCount(s, 'active'), tabCount(s, 'taken'), tabCount(s, 'skipped'), tabCount(s, 'auto')]).toEqual([3, 3, 4, 9])
    expect(defaultTab(s)).toBe('active')
    expect(defaultTab(snap([], { skipped: 1 }))).toBe('skipped')
    expect(defaultTab(snap([]))).toBe('auto')
  })
  it('последний замер: самый дальний горизонт с ценой после издержек', () => {
    const o = { '15': { ret_pct: 0.1, dir_pct: 0.1, net_pct: -0.1 }, '60': { ret_pct: 0.5, dir_pct: 0.5, net_pct: 0.26 }, '240': { ret_pct: 1, dir_pct: null, net_pct: null } }
    expect(lastOutcome(o)).toEqual({ h: 60, v: 0.26 })
    expect(lastOutcome({})).toBeNull()
  })
  it('полоса лонга: стоп 0, тейк 1, уровень между, цена выше уровня даёт отрезок', () => {
    const r = ideaRoute(idea({}))!
    expect(r.level).toBeCloseTo(0.25, 5)
    expect(r.price).toBeCloseTo(0.5, 5)
    expect(r.gap![0]).toBeCloseTo(0.25, 5)
  })
  it('полоса шорта читается в ту же сторону; без стопа или с перевёрнутым стопом полосы нет', () => {
    const r = ideaRoute(idea({ side: 'short', level: 100, stop: 102, tp: 94, now_price: 98 }))!
    expect(r.level).toBeCloseTo(0.25, 5)
    expect(r.price).toBeCloseTo(0.5, 5)
    expect(ideaRoute(idea({ stop: null }))).toBeNull()
    expect(ideaRoute(idea({ stop: 101 }))).toBeNull()
  })
  it('риск, награда и RR от уровня', () => {
    const r = ideaRisk(idea({}))
    expect(r.risk).toBeCloseTo(2, 5)
    expect(r.reward).toBeCloseTo(6, 5)
    expect(r.rr).toBeCloseTo(3, 5)
  })
  it('фраза ожидания уровня', () => {
    expect(levelWait(idea({ dist_level_pct: 2 })).text).toBe('снижение на 2.00%')
    expect(levelWait(idea({ dist_level_pct: -0.6 }))).toEqual({ text: 'рост на 0.60%', tone: 'close' })
    expect(levelWait(idea({ zone: true, dist_level_pct: 0.1 })).tone).toBe('near')
    expect(levelWait(idea({ dist_level_pct: null })).tone).toBe('none')
  })
})
