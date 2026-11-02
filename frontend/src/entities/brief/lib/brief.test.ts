import { describe, expect, it } from 'vitest'
import { briefMs, coinStat, hotRows, levelAxis, scopeText } from './brief'

describe('досье', () => {
  it('вид досье по-русски, неизвестный как есть', () => {
    expect([scopeText('start'), scopeText('event'), scopeText('x1'), scopeText(null)]).toEqual(['старт дня', 'событие', 'x1', '?'])
  })
  it('время досье из ISO', () => {
    expect(briefMs('2026-10-31T10:00:00Z')).toBe(Date.parse('2026-10-31T10:00:00Z'))
    expect(briefMs('мусор')).toBeNull()
    expect(briefMs(null)).toBeNull()
  })
  it('ход монеты: market главнее, недостающее берётся из структуры, режим unknown отбрасывается', () => {
    const s = coinStat({ px: 100, ret_5m_pct: 0.2, regime: 'unknown' }, { r1h: 1.5, r24h: -3, a5_pct: 0.4, fu: 0.01, sp: 2, reg: 'trend_up', px: 99 })
    expect(s).toEqual({ px: 100, r5: 0.2, r1h: 1.5, r24h: -3, atr5: 0.4, funding: 0.01, spread: 2, regime: 'trend_up' })
    expect(coinStat(undefined, undefined).px).toBeNull()
  })
  it('горячие: массив или объект с top, нет данных: пусто', () => {
    expect(hotRows({ hot: [{ sym: 'A' }] }).length).toBe(1)
    expect(hotRows({ hot: { top: [{ sym: 'A' }, { sym: 'B' }] } }).length).toBe(2)
    expect(hotRows({})).toEqual([])
  })
  it('ось уровней: все значения внутри 0..1, цена между диапазонами; одного значения мало', () => {
    const a = levelAxis({ px: 100, week: { lo: 90, hi: 120 }, day: { lo: 98, hi: 104 }, swing_5m: [101] })!
    expect(a.bands.map((b) => b.label)).toEqual(['неделя', 'день'])
    for (const v of [90, 120, 98, 104, 101, 100]) { expect(a.at(v)).toBeGreaterThan(0); expect(a.at(v)).toBeLessThan(1) }
    expect(a.at(100)).toBeGreaterThan(a.at(90))
    expect(levelAxis({ px: 5 })).toBeNull()
  })
})
