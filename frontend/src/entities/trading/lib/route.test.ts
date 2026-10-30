import { describe, expect, it } from 'vitest'
import type { Position } from '../model/types'
import { route } from './route'

const base: Position = {
  plan: 'PL-1', sym: 'ETH', side: 'short', mode: 'paper', lev: 10, qty: 1, avg: 100, price: 98, stale: false, stop: 104, stop0: 104,
  targets: [{ price: 96, state: 'resting' }, { price: 92, state: 'resting' }], r: 0.5, upnl: 1, risk: 1, pnl_at_sl: -1, held_s: 10, hold_left_s: null, liq: null,
}

describe('route', () => {
  it('шорт читается слева направо: стоп 0, вход, цена, последний тейк 1', () => {
    const r = route(base)!
    expect(r.stop).toBe(0)
    expect(r.entry).toBeCloseTo(4 / 12)             // стоп 104 -> тейк 92: длина 12, вход 100 на расстоянии 4
    expect(r.price).toBeCloseTo(6 / 12)
    expect(r.targets[0]).toBeCloseTo(8 / 12)
    expect(r.targets[1]).toBeCloseTo(1)
    expect(r.inProfit).toBe(true)
  })

  it('лонг и цена против позиции', () => {
    const r = route({ ...base, side: 'long', avg: 100, stop: 96, price: 99, targets: [{ price: 108, state: null }] })!
    expect(r.entry).toBeCloseTo(4 / 12)
    expect(r.price).toBeCloseTo(3 / 12)
    expect(r.inProfit).toBe(false)
  })

  it('цели не с той стороны игнорируются, без целей конец оси на расстоянии риска', () => {
    const r = route({ ...base, targets: [{ price: 110, state: null }] })!
    expect(r.targets).toEqual([])
    expect(r.entry).toBeCloseTo(0.5)
  })

  it('нет стопа, входа или стоп на стороне прибыли: маршрута нет', () => {
    expect(route({ ...base, stop: null })).toBeNull()
    expect(route({ ...base, avg: null })).toBeNull()
    expect(route({ ...base, stop: 90 })).toBeNull()
  })

  it('цена за пределами полосы прижимается к краям с запасом', () => {
    expect(route({ ...base, price: 120 })!.price).toBeCloseTo(-0.08)
    expect(route({ ...base, price: 50 })!.price).toBeCloseTo(1.08)
  })
})
