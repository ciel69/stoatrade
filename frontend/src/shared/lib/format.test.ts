import { describe, expect, it } from 'vitest'
import { ageText, duration, fixed, price, signed, usd } from './format'

describe('format', () => {
  it('возраст по шкале', () => {
    const now = 1000
    expect(ageText(999, now)).toBe('только что')
    expect(ageText(988, now)).toBe('12 с назад')
    expect(ageText(1000 - 180, now)).toBe('3 мин назад')
    expect(ageText(1000 - 7200, now)).toBe('2 ч назад')
    expect(ageText(1000 - 3 * 86400, now)).toBe('3 дн назад')
    expect(ageText(null, now)).toBe('–')
    expect(ageText(2000, now)).toBe('только что')
  })
  it('числа и доллары', () => {
    expect(fixed(1.234)).toBe('1.23')
    expect(fixed(undefined)).toBe('–')
    expect(usd(0.012345)).toBe('$0.0123')
    expect(usd(12.3456)).toBe('$12.35')
    expect(usd(NaN)).toBe('–')
  })
  it('знак: настоящий минус, ноль без знака', () => {
    expect(signed(1.2)).toBe('+1.20')
    expect(signed(-0.354)).toBe('−0.35')
    expect(signed(0)).toBe('0.00')
    expect(signed(-0.001)).toBe('0.00')
    expect(signed(null)).toBe('–')
  })
  it('цена по значащим цифрам', () => {
    expect(price(2224.36)).toBe('2224.4')
    expect(price(25.8238)).toBe('25.824')
    expect(price(0.004604079)).toBe('0.004604')
    expect(price(0)).toBe('0')
  })
  it('длительность', () => {
    expect(duration(45)).toBe('45 с')
    expect(duration(720)).toBe('12 мин')
    expect(duration(7800)).toBe('2 ч 10 мин')
    expect(duration(7200)).toBe('2 ч')
    expect(duration(3 * 3600 + 59 * 60 + 40)).toBe('4 ч')        // минуты переносятся в часы, а не «3 ч 60 мин»
  })
})
