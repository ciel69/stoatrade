import { describe, expect, it } from 'vitest'
import { intensity, kfmt, metricById, METRICS, tone } from './metrics'
import type { CoinData } from './metrics'

const coin = (o: Partial<CoinData['heat']> = {}, liq?: CoinData['liq']): CoinData => ({
  heat: { sym: 'SOL', tradable: true, price: 100, chg_24h_pct: 5, ret_5m_pct: 0.3, ret_15m_pct: -1.25, range_5m_pct: 0.5, turn_surge_5m: 1, oi_chg_15m_pct: null, oi_chg_1h_pct: null, oi_musd: 10, funding_pct: 0.04, turn_24h_musd: 100, score: 1, ...o },
  liq,
})

describe('метрики карты', () => {
  it('сумма кратко с знаком', () => {
    expect([kfmt(0), kfmt(850), kfmt(12400), kfmt(3_400_000), kfmt(-2400)]).toEqual(['0', '850', '12K', '3.4M', '−2K'])
  })
  it('сила цвета: линейно до шкалы, дальше насыщение; пусто без значения', () => {
    const m = metricById('chg_24h_pct')
    expect(intensity(5, m)).toBeCloseTo(0.5 ** 0.65, 5)
    expect(intensity(0.5, m)).toBeGreaterThan(0.12)          // малое движение (0.5% при шкале 10%) уже заметно
    expect(intensity(-50, m)).toBe(1)
    expect(intensity(null, m)).toBe(0)
  })
  it('фандинг красится только при заметном перекосе и янтарным', () => {
    const m = metricById('funding_pct')
    expect(tone(0.01, m)).toBe('none')
    expect(tone(-0.04, m)).toBe('warn')
  })
  it('ликвидации: больше выбито шортов даёт плюс, нет данных по монете даёт пусто', () => {
    const m = metricById('liq')
    const c = coin({}, { sym: 'SOL', long_5m: 0, short_5m: 0, long_1h: 1000, short_1h: 9000, n_1h: 3, px: 100, map: [] })
    expect(m.get(c)).toBe(8000)
    expect(tone(8000, m)).toBe('up')                    // слабый, но плюс
    expect(m.get(coin())).toBeNull()
  })
  it('у каждой метрики есть значение для строки с данными и текст', () => {
    for (const m of METRICS) { const v = m.get(coin()); if (v != null) expect(m.text(v).length).toBeGreaterThan(0) }
  })
})
