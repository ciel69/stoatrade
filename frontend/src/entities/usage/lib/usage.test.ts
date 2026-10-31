import { describe, expect, it } from 'vitest'
import { budgetCaption, budgetLevel, budgetTitle, costLine, groupToks, isFreeLocal, isHigh, leftFrac, msText, panelSubtitle, parseUsagePanel, pctText, sortBudget, toksShare, toksTip, usageKey, usagePanelQuery, withShares } from './usage'
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
  it('токены группы: in + out, сумма сходится с итогом сервера, рассуждение и кэш не прибавляются', () => {
    const a = { ...g('a', 1), in: 5000, in_hit: 3000, out: 2100, reasoning: 400 }
    const c = { ...g('local', 0), in: 900, in_hit: 0, out: 100, reasoning: 0 }
    expect(groupToks(a)).toBe(7100)
    expect(groupToks({ in: null, out: null })).toBe(0)
    expect(groupToks(a) + groupToks(c)).toBe(8100)
  })
  it('доля токенов: от суммы по группам, нулевая сумма без деления на ноль', () => {
    const a = { ...g('a', 1), in: 750, out: 0 }
    const c = { ...g('local', 0), in: 200, out: 50 }
    expect([toksShare(a, [a, c]), toksShare(c, [a, c])]).toEqual([0.75, 0.25])
    expect(toksShare(g('x', 0), [g('x', 0)])).toBe(0)
  })
  it('подсказка: состав токенов не пересекается и в сумме даёт in + out, средние на вызов, цена, скорость', () => {
    const a = { ...g('a', 2), calls: 100, in: 500_000, in_hit: 200_000, out: 100_000, reasoning: 40_000, errors: 5, err_rate: 0.05, fmt_errors: 1, fmt_err_rate: 0.01, fallbacks: 2, lat_p50_ms: 2000, lat_p95_ms: 9000 }
    const t = toksTip(a)
    expect(t.mix.map((x) => [x.key, x.value])).toEqual([['cache', 200_000], ['miss', 300_000], ['answer', 60_000], ['think', 40_000]])
    expect(t.mix.reduce((s, x) => s + x.value, 0)).toBe(groupToks(a))
    expect(t.mix.map((x) => x.share)).toEqual([200 / 600, 0.5, 0.1, 40 / 600].map((x) => x))
    expect([t.total, t.perCall, t.perCallIn, t.perCallOut]).toEqual(['600.0 тыс.', '6.0 тыс.', '5.0 тыс.', '1.0 тыс.'])
    expect([t.free, t.callCost, t.millionCost]).toEqual([false, '$0.0200', '$3.33'])
    const s = Object.fromEntries(t.stats.map((x) => [x.key, x]))
    expect([s.p50.value, s.p95.value, s.speed.value, s.err.value, s.err.note, s.fmt.value, s.fb.value]).toEqual(['2.0 с', '9.0 с', '500', '5%', '5 шт.', '1%', '2'])
  })
  it('подсказка: подсветка проблем по порогу строки, деления на ноль дают «–», бесплатное помечено', () => {
    const hot = toksTip({ ...g('a', 1, 40), err_rate: 0.2, fmt_err_rate: 0.1 })
    expect(hot.stats.filter((x) => x.hot).map((x) => x.key)).toEqual(['err'])
    expect(toksTip({ ...g('a', 1, 10), err_rate: 0.5 }).stats.some((x) => x.hot)).toBe(false)
    const z = toksTip({ ...g('local', 0, 0), lat_p50_ms: null, lat_p95_ms: null, err_rate: null, fmt_err_rate: null })
    expect([z.perCall, z.free, z.callCost, z.millionCost, z.mix.map((x) => x.share)]).toEqual(['–', true, 'бесплатно', 'бесплатно', [0, 0, 0, 0]])
    expect(z.stats.map((x) => x.value)).toEqual(['–', '–', '–', '–', '–', '0'])
    expect(pctText(0.042)).toBe('4.2%')
    // рассуждение больше выхода не уходит в минус
    expect(toksTip({ ...g('x', 0), in: 10, in_hit: 99, out: 5, reasoning: 9 }).mix.map((x) => x.value)).toEqual([10, 0, 0, 5])
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
  it('адрес панели: сборка и разбор параметров, неверные значения отклоняются', () => {
    expect(usagePanelQuery('gpt-oss', 7, 'model')).toEqual({ usage: 'gpt-oss', days: '7', by: 'model' })
    expect(parseUsagePanel(usagePanelQuery('local', 30, 'provider'))).toEqual({ id: 'local', days: 30, by: 'provider' })
    expect(parseUsagePanel({ usage: 'x', days: '5', by: 'model' })).toBeNull()
    expect(parseUsagePanel({ usage: 'x', days: '1', by: 'zone' })).toBeNull()
    expect(parseUsagePanel({ days: '1', by: 'role' })).toBeNull()
    expect(parseUsagePanel({ usage: ['a'], days: '1', by: 'role' })).toBeNull()
    expect(panelSubtitle(1, 'model')).toBe('Сутки · по моделям')
    expect(panelSubtitle(30, 'role')).toBe('30 дней · по ролям')
  })
})
