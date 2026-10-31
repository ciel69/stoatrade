import { describe, expect, it } from 'vitest'
import { budgetCaption, budgetLevel, budgetTitle, costLine, isFreeLocal, isHigh, leftFrac, msText, sortBudget, usageKey, withShares } from './usage'
import type { UsageBudget, UsageGroup } from '../model/types'

const g = (id: string, usd: number, calls = 10): UsageGroup => ({ id, calls, in: 0, in_hit: 0, out: 0, reasoning: 0, usd, errors: 0, fmt_errors: 0, fallbacks: 0, lat_p50_ms: 800, lat_p95_ms: 2000, err_rate: 0, fmt_err_rate: 0, cache_hit_rate: 0 })
const b = (cred: string, scope = 'all', used = 1, cap = 4): UsageBudget => ({ state: 'ok', scope, cred, used, cap, until_ts: 1000 })

describe('расход LLM', () => {
  it('ключ кэша по периоду и разрезу', () => {
    expect(usageKey(7, 'role')).toBe('7:role')
  })
  it('доли стоимости: сортировка по убыванию, нулевая сумма без деления на ноль', () => {
    const r = withShares([g('a', 1), g('b', 3), g('local', 0)])
    expect(r.map((x) => x.id)).toEqual(['b', 'a', 'local'])
    expect(r.map((x) => x.share)).toEqual([0.75, 0.25, 0])
    expect(withShares([g('local', 0)])[0].share).toBe(0)
  })
  it('подсветка доли только на выборке от 20 вызовов', () => {
    expect([isHigh(0.2, 30), isHigh(0.2, 10), isHigh(0.1, 100), isHigh(null, 100)]).toEqual([true, false, false, false])
  })
  it('бесплатно только для локального провайдера', () => {
    expect([isFreeLocal(g('local', 0), 'provider'), isFreeLocal(g('local', 0), 'model'), isFreeLocal(g('deepseek', 0), 'provider'), isFreeLocal(g('local', 1), 'provider')]).toEqual([true, false, false, false])
  })
  it('шкала бюджета: остаток и цвет', () => {
    expect(leftFrac({ used: 1, cap: 4 })).toBe(0.75)
    expect(leftFrac({ used: 9, cap: 4 })).toBe(0)
    expect(leftFrac({ used: 0, cap: 0 })).toBe(0)
    expect([budgetLevel(0.1), budgetLevel(0.2), budgetLevel(0.5)]).toEqual(['bad', 'warn', 'ok'])
  })
  it('потолки: порядок, заголовки, подпись с абсолютным моментом сброса', () => {
    const l = sortBudget([b('hour_tokens'), b('day_usd', 'deepseek'), b('hour_usd'), b('day_usd')])
    expect(l.map((x) => `${x.scope}:${x.cred}`)).toEqual(['all:day_usd', 'all:hour_usd', 'all:hour_tokens', 'deepseek:day_usd'])
    expect([budgetTitle(b('day_usd')), budgetTitle(b('day_tokens', 'deepseek'))]).toEqual(['Деньги за день', 'Токены за день, deepseek'])
    expect(budgetCaption(b('day_usd'), 400, (s) => `${s} с`)).toBe('потрачено $1.00 из $4.00, сбросится через 600 с')
    expect(budgetCaption(b('day_tokens', 'all', 45000, 100000), 2000, () => '')).toBe('потрачено 45 тыс. из 100 тыс., окно сбрасывается')
  })
  it('фразы: время ответа и вывод над блоком', () => {
    expect([msText(850), msText(2300), msText(null)]).toEqual(['850 мс', '2.3 с', '–'])
    expect(costLine(0, 0, 1, null, (x) => x)).toBe('За сутки вызовов не было')
    expect(costLine(1.5, 10, 7, { id: 'deepseek', share: 0.9 }, (x) => x)).toBe('Расход за 7 дн.: $1.50, основная доля у «deepseek» (90%)')
  })
})
