import { describe, expect, it } from 'vitest'
import type { Brief, BriefPosition } from '../model/types'
import { compareBriefs, posKey } from './compare'

const pos = (o: Partial<BriefPosition> = {}): BriefPosition => ({ sym: 'SOL', side: 'long', avg: 100, px: 101, sl: 99, liq: 50, r: 0.5, mae_r: 0, mfe_r: 1, age_min: 5, ...o })

describe('сравнение досье', () => {
  it('без предыдущего ничего не показывает', () => {
    const d = compareBriefs(null, { positions: [pos()] })
    expect(d.lines).toEqual([])
    expect(d.marks).toEqual({})
  })

  it('одинаковые досье, отличающиеся только временем и возрастом: изменений нет', () => {
    const a: Brief = { ts: '2026-11-01T10:00:00Z', positions: [pos({ age_min: 5, px: 101, r: 0.5 })], intel: { age_s: 3 }, limits: { daily_left_usdt: 2 } }
    const b: Brief = { ts: '2026-11-01T10:05:00Z', positions: [pos({ age_min: 10, px: 103, r: 1.4 })], intel: { age_s: 90 }, limits: { daily_left_usdt: 2.001 } }
    const d = compareBriefs(a, b)
    expect(d.lines).toEqual([])
    expect(d.marks).toEqual({})
  })

  it('позиции: число, открытие, закрытие, сдвиг стопа', () => {
    const d = compareBriefs({ positions: [pos({ sym: 'BTC' })] }, { positions: [pos({ sym: 'BTC', sl: 100 }), pos({ sym: 'ETH' })] })
    expect(d.lines).toContain('Позиций стало 2 вместо 1.')
    expect(d.lines.some((l) => l.startsWith('Открыта позиция: ETH лонг'))).toBe(true)
    expect(d.lines).toContain('Стоп сдвинут: BTC.')
    expect(d.marks.pos).toBe('changed')
    expect(d.fresh.pos.has(posKey(pos({ sym: 'ETH' })))).toBe(true)
    const c = compareBriefs({ positions: [pos()] }, { positions: [] })
    expect(c.lines).toContain('Позиций стало 0 вместо 1.')
    expect(c.lines.some((l) => l.includes('закрыта'))).toBe(true)
  })

  it('из пустого в непустое помечается как новое', () => {
    const d = compareBriefs({ positions: [], hot: [] }, { positions: [pos()], hot: [{ sym: 'DOGE' }] })
    expect(d.marks.pos).toBe('new')
    expect(d.marks.hot).toBe('new')
    expect(d.fresh.hot.has('DOGE')).toBe(true)
  })

  it('запас до дневного лимита и запрет входов', () => {
    const d = compareBriefs(
      { limits: { daily_left_usdt: 2 }, room: { allowed: true, slots_free: 2 } },
      { limits: { daily_left_usdt: 1.2 }, room: { allowed: false, reasons: ['дневной лимит'], slots_free: 1 } },
    )
    expect(d.lines).toContain('Запас до дневного лимита уменьшился: 1.2 USDT вместо 2 USDT.')
    expect(d.lines).toContain('Входы теперь запрещены: дневной лимит.')
    expect(d.lines).toContain('Свободных слотов стало 1 вместо 2.')
    expect(d.marks.lim).toBe('changed')
    expect(d.marks.acc).toBe('changed')
  })

  it('режим рынка и риск-офф', () => {
    const d = compareBriefs(
      { market: { btc: { regime: 'range' }, risk_off: false } },
      { market: { btc: { regime: 'trend_up' }, risk_off: true } },
    )
    expect(d.lines.some((l) => l.startsWith('Режим рынка сменился (BTC:'))).toBe(true)
    expect(d.lines.some((l) => l.includes('риск-офф'))).toBe(true)
    expect(d.marks.mkt).toBe('changed')
  })

  it('горячие монеты, идеи, события', () => {
    const d = compareBriefs(
      { hot: [{ sym: 'A' }, { sym: 'B' }], ideas: { active: [{ id: 'I1', sym: 'A', side: 'long' }] }, recent: [{ seq: 5, type: 'x' }] },
      { hot: [{ sym: 'A' }, { sym: 'C' }], ideas: { active: [{ id: 'I1', sym: 'A', side: 'long' }, { id: 'I2', sym: 'C', side: 'short' }] }, recent: [{ seq: 5, type: 'x' }, { seq: 6, type: 'y' }, { seq: 7, type: 'z' }] },
    )
    expect(d.lines).toContain('Появились новые горячие монеты: C.')
    expect(d.lines).toContain('Из горячих ушли: B.')
    expect(d.lines).toContain('Новые идеи: C.')
    expect(d.lines).toContain('Новых событий с прошлого досье: 2.')
    expect([d.marks.hot, d.marks.id, d.marks.ev]).toEqual(['changed', 'changed', 'changed'])
  })

  it('блок, которого нет в одном из досье (событийное неполное), не считается изменением', () => {
    const d = compareBriefs({ positions: [pos()], plans: [{ id: 'P1', sym: 'SOL', side: 'long' }] }, { recent: [{ seq: 1, type: 'x' }] })
    expect(d.lines).toEqual([])
    expect(d.marks).toEqual({})
  })
})
