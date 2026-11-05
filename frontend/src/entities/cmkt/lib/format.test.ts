import { describe, expect, it } from 'vitest'
import {
  ageSeconds, ageSince, bps, exchangesText, flagWord, isAged, lagText, musd, musdSigned, pct, pctSigned, priceText, ratioPct,
  skewText, smallPct, tierWord, truncWord, venueInfo, venueName, warnInfo,
} from './format'

describe('формат сводного рынка', () => {
  it('млн USD: M до тысячи, B дальше, неизвестное — длинное тире', () => {
    expect(musd(123.5)).toBe('123.5M')
    expect(musd(1240)).toBe('1.24B')
    expect(musd(0)).toBe('0.0M')
    expect(musd(null)).toBe('—')
    expect(musd(Number.NaN)).toBe('—')
    expect(musdSigned(2.3)).toBe('+2.3M')
    expect(musdSigned(-1.1)).toBe('−1.1M')
    expect(musdSigned(0)).toBe('0.0M')
  })

  it('проценты с одним знаком; доля покрытия переводится в проценты', () => {
    expect(pct(12.34)).toBe('12.3%')
    expect(pctSigned(12.34)).toBe('+12.3%')
    expect(pctSigned(-4)).toBe('−4.0%')
    expect(ratioPct(0.94)).toBe('94.0%')
    expect(smallPct(0.00123)).toBe('+0.00123%')
    expect(bps(1.44)).toBe('+1.4 б.п.')
    expect(pct(null)).toBe('—')
  })

  it('возрасты по часам клиента: секунды и минуты, неизвестное — тире', () => {
    expect(ageSince(100, 103)).toBe('3 с')
    expect(ageSince(100, 160)).toBe('1 мин')
    expect(ageSince(null, 160)).toBe('—')
    expect(ageSeconds(3)).toBe('3 с')
    expect(ageSeconds(60)).toBe('1 мин')
    expect(isAged(100, 104)).toBe(false)
    expect(isAged(100, 106)).toBe(true)
    expect(isAged(null, 106)).toBe(true)
  })

  it('задержки, часы и цены', () => {
    expect(lagText(120.4)).toBe('120 мс')
    expect(lagText(null)).toBe('—')
    expect(skewText(420)).toBe('+420 мс')
    expect(skewText(-90)).toBe('−90 мс')
    expect(priceText(65000)).toBe('65000.0')
    expect(priceText(null)).toBe('—')
  })

  it('биржи, ярусы и отброшенные части названы словами', () => {
    expect(exchangesText(1)).toBe('1 биржа')
    expect(exchangesText(3)).toBe('3 биржи')
    expect(exchangesText(5)).toBe('5 бирж')
    expect(exchangesText(null)).toBe('—')
    expect(tierWord('deep')).toBe('глубокий')
    expect(tierWord('light')).toBe('лёгкий')
    expect(truncWord('venues')).toBe('биржи')
    expect(truncWord('book')).toBe('стакан')
    expect(truncWord('unknown')).toBe('часть данных')
  })

  it('коды бирж и предупреждения объяснены по-русски, без сырых кодов', () => {
    const lag = venueInfo('LAG')
    expect(lag.word).toBe('отстаёт')
    expect(lag.about).toContain('задержка')
    expect(venueInfo('OK').word).toBe('норма')
    expect(venueInfo('DOWN').word).toBe('недоступна')
    expect(venueInfo('ЧУЖОЙ').word).toBe('состояние неизвестно')
    expect(warnInfo('NOT_FOCUS').word).toBe('вне фокуса')
    expect(warnInfo('LOW_COV').about).toContain('покрытие')
    expect(warnInfo('ЧУЖОЕ').word).toBe('замечание')
    expect(flagWord('warmup')).toBe('прогрев')
    expect(flagWord('stale_ex')).toBe('есть отстающие биржи')
    expect(flagWord('чужой')).toBeNull()
  })

  it('название рынка: биржа и вид человеческими словами', () => {
    expect(venueName('binance_perp')).toBe('Binance · перп')
    expect(venueName('coinbase_spot')).toBe('Coinbase · спот')
    expect(venueName('okx_perp')).toBe('OKX · перп')
    expect(venueName('новая_биржа')).toBe('Новая · биржа')
  })

  it('ни в одной подписи нет сырых кодов', () => {
    const text = [venueInfo('LAG').word, venueInfo('SILENT').word, warnInfo('STALE').word, warnInfo('LOW_COV').word].join(' ')
    expect(text).not.toMatch(/LAG|SILENT|STALE|LOW_COV|NOT_FOCUS/)
    expect(text).not.toMatch(/\p{Extended_Pictographic}/u)
  })
})
