import { describe, expect, it } from 'vitest'
import { ageText, fixed, usd } from './format'

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
})
