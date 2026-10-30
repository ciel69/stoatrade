import { describe, expect, it } from 'vitest'
import type { NearPlan } from '../model/types'
import { planWait } from './plan-wait'

const plan = (o: Partial<NearPlan> = {}): NearPlan => ({
  id: 'PL-1', sym: 'SOL', side: 'short', mode: 'paper', col: 'waiting', text: '', dist_pct: 0.4, eta_at: null, entry_state: null, blocked_why: null, net_rr: 2, risk: 0.3,
  status: 'armed', setup: 'level_reject', thesis: '', entry_text: '', missing: [], met_n: 0, leaf_n: 1, seq: null, level: 144, price: 143.5, kind: 'price_beyond', dir: 'above',
  beyond: false, tf: null, ttl_at: null, hold: null, hold_reason: null, stop: 146, target: 140, targets: [140], leverage: null, chase_bps: null, chase_max_bps: null, ...o,
})
const NOW = 1000

describe('planWait', () => {
  it('ценовое условие: ждёт цену, проверка непрерывная', () => {
    const w = planWait(plan({ missing: ['цена выше 144.0'] }), NOW)
    expect(w.headline).toBe('Ждёт: цена выше 144.0')
    expect(w.when).toContain('непрерывно')
    expect(w.tone).toBe('wait')
  })
  it('свечное условие: обратный отсчёт до закрытия и пометка, что цена уже за уровнем', () => {
    const w = planWait(plan({ missing: ['5м закрытие выше 144.0'], kind: 'candle_close', tf: '5м', eta_at: NOW + 134, beyond: true }), NOW)
    expect(w.headline).toContain('нужна фиксация закрытием свечи')
    expect(w.when).toContain('2:14')
  })
  it('остальные условия и прогресс по условиям и по порядку', () => {
    const a = planWait(plan({ missing: ['цена выше 144.0', 'объём 5м ≥ x2 медианы'], leaf_n: 3, met_n: 1 }), NOW)
    expect(a.rest).toEqual(['объём 5м ≥ x2 медианы'])
    expect(a.progress).toBe('выполнено 1 из 3')
    expect(planWait(plan({ missing: ['x'], seq: { done: 1, total: 3 } }), NOW).progress).toBe('шаг 2 из 3')
  })
  it('блокировка, пауза, черновик, лимит на бирже и «условия выполнены»', () => {
    expect(planWait(plan({ blocked_why: 'CHASE' }), NOW)).toMatchObject({ tone: 'blocked' })
    expect(planWait(plan({ hold: 'news', hold_reason: 'окно новостей' }), NOW).headline).toBe('План на паузе: окно новостей')
    expect(planWait(plan({ status: 'draft', missing: ['цена выше 1'] }), NOW).headline).toContain('Черновик')
    expect(planWait(plan({ entry_state: 'resting' }), NOW).headline).toBe('Лимитная заявка уже стоит на 144.00')
    expect(planWait(plan(), NOW).headline).toContain('Условия выполнены')
  })
  it('время жизни плана подмешивается в строку проверки', () => {
    expect(planWait(plan({ missing: ['цена выше 144.0'], ttl_at: NOW + 7200 }), NOW).when).toContain('план истекает через 2 ч')
  })
})
