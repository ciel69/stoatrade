import { describe, expect, it } from 'vitest'
import { EXP_CUTS, cutInfo, expValueWord, isExpCut, pHint, pWord, plural } from './index'

describe('слова страницы «Эксперименты»', () => {
  it('у каждого разреза есть человеческое название и пояснение без служебных полей', () => {
    expect(EXP_CUTS.map((c) => c.value)).toEqual(['hyp', 'exp', 'variant', 'setup', 'stype', 'symbol', 'side', 'reason', 'err', 'venue'])
    for (const c of EXP_CUTS) {
      expect(c.label).toMatch(/^[А-ЯЁ]/)
      expect(c.about.length).toBeGreaterThan(15)
      expect(`${c.label} ${c.about}`).not.toMatch(/\b(exp|var|hyp|stype|setup)\b/)
    }
    expect(isExpCut('hyp')).toBe(true)
    expect(isExpCut('../x')).toBe(false)
    expect(cutInfo('side').label).toBe('Сторона')
  })
  it('коды значений словами, свободные имена как есть', () => {
    expect(expValueWord('side', 'long')).toBe('покупка')
    expect(expValueWord('reason', 'sl')).toBe('сработал стоп')
    expect(expValueWord('stype', 'intraday')).toBe('внутри дня')
    expect(expValueWord('setup', 'level_reclaim')).toBe('возврат за уровень')
    expect(expValueWord('venue', 'bybit')).toBe('Bybit')
    expect(expValueWord('symbol', 'BTCUSDT')).toBe('BTCUSDT')
    expect(expValueWord('hyp', 'sweep-fade')).toBe('sweep-fade')
    expect(expValueWord('reason', 'новая_причина')).toBe('новая_причина')
    expect(expValueWord('variant', 'stop-width/wide')).toBe('stop-width, вариант wide')
    expect(expValueWord('variant', 'solo')).toBe('solo')
  })
  it('«насколько случайно»: мало данных, порог с поправкой, обычный порог', () => {
    expect(pWord(null, true, 0.05)).toEqual({ text: 'мало данных', tone: 'warn' })
    expect(pWord(0.001, true, 0.05).text).toBe('мало данных')
    expect(pWord(null, false, 0.05).text).toBe('оценить нельзя')
    expect(pWord(0.004, false, 0.0083)).toEqual({ text: 'вряд ли случайность', tone: 'ok' })
    expect(pWord(0.02, false, 0.0083).text).toBe('похоже на закономерность, но порог строже')
    expect(pWord(0.1, false, 0.0083).text).toBe('может быть случайностью')
    expect(pWord(0.6, false, 0.0083).text).toBe('скорее всего случайность')
  })
  it('число в подсказке с запятой; без числа честно «не посчитано»', () => {
    expect(pHint(0.012, 0.0083)).toBe('p = 0,012; порог 0,008')
    expect(pHint(null, null)).toBe('p не посчитано')
    expect(pHint(0.00004, 0.05)).toBe('p = 4,0e-5; порог 0,050')
  })
  it('склонение', () => {
    expect([1, 2, 5, 11, 21, 24].map((n) => plural(n, ['гипотеза', 'гипотезы', 'гипотез']))).toEqual(['гипотеза', 'гипотезы', 'гипотез', 'гипотез', 'гипотеза', 'гипотезы'])
  })
})
