import { describe, expect, it } from 'vitest'
import { connectSummary, credText, credsText, ctxText, exchangeStatus, limitsText, modelsText, perMillion, priceText, providerIssues, swapBy, swapText, swapsLine } from './connect'
import type { ExchangeRow, ProviderRow, RoleModel, RoleRow } from '../model/types'

const prov = (o: Partial<ProviderRow> = {}): ProviderRow => ({
  id: 'deepseek', name: 'DeepSeek', adapter: 'openai', enabled: true, local: false, verified: true, note: null, error: null, base_host: 'api.deepseek.com',
  cred: { needed: true, present: true, perms_ok: true, env: 'DEEPSEEK_API_KEY' }, limits: { concurrent: null, rpm: 60, day_usd: 3, hour_usd: 1, day_toks: null }, models: [], profiles: [], ...o,
})
const model = (provider: string): RoleModel => ({ id: 'x', provider, model_name: 'm', provider_enabled: true, live_ok: false, params: { temperature: null, max_out_tok: null, thinking: null } })
const role = (r: string, primary: string, fb: string[] = []): RoleRow => ({
  role: r, scope: 'team', team: null, live_risk: false, io: null, primary: model(primary), fallback: fb.map(model), budget_day_usd: null,
  today: { usd: 0, calls: 0, errors: 0, budget_left_usd: null }, day: { calls: 0, err_rate: null, fmt_err_rate: null, fallbacks: 0, p50_ms: null, usd: 0 },
})
const ex = (o: Partial<ExchangeRow> = {}): ExchangeRow => ({ id: 'bybit', name: 'Bybit', enabled: true, mode: 'paper', implemented: true, active: true, allowed: true, note: null, alive: true, creds: ['read'], max_open_positions: 3, ...o })

describe('подключения', () => {
  it('цены и контекст', () => {
    expect([perMillion(0.28), perMillion(2), perMillion(null)]).toEqual(['$0.28', '$2', '–'])
    expect(priceText({ in_miss: 0.28, in_hit: 0.028, out: 0.42 })).toBe('вход $0.28 (из кэша $0.028), выход $0.42 за 1 млн токенов')
    expect(priceText(null)).toBe('цена не задана')
    expect([ctxText(128000), ctxText(1000000), ctxText(null)]).toEqual(['128 тыс. токенов', '1 млн токенов', '–'])
  })
  it('лимиты расхода кратко', () => {
    expect(limitsText(prov().limits)).toBe('до $3 в день, $1 в час')
    expect(limitsText({ concurrent: null, rpm: 30, day_usd: 0, hour_usd: 0, day_toks: 0 })).toBe('без лимитов')       // rpm сервер не применяет: не показываем
    expect(limitsText({ concurrent: null, rpm: null, day_usd: null, hour_usd: null, day_toks: null })).toBe('без лимитов')
  })
  it('ключ словами, без масок', () => {
    expect(credText(prov())).toBe('ключ есть')
    expect(credText(prov({ cred: { needed: true, present: false, perms_ok: null, env: null } }))).toBe('ключа нет, провайдер без него не включить')
    expect(credText(prov({ cred: { needed: false, present: false, perms_ok: null, env: null } }))).toBe('ключ не нужен')
  })
  it('замечания: выключен, а роль пользуется; слабые права файла; в норме молчим', () => {
    expect(providerIssues(prov(), [role('entry_scout', 'deepseek')])).toEqual([])
    const off = providerIssues(prov({ id: 'anthropic', enabled: false }), [role('entry_scout', 'deepseek', ['anthropic'])])
    expect(off[0]).toMatchObject({ tone: 'bad' })
    expect(off[0].text).toContain('Поиск входа')
    expect(providerIssues(prov({ enabled: false }), [])).toEqual([])
    expect(providerIssues(prov({ cred: { needed: true, present: true, perms_ok: false, env: null } }), [])[0].text).toContain('другими пользователями')
    expect(providerIssues(prov({ cred: { needed: true, present: true, perms_ok: null, env: null } }), [])).toEqual([])
  })
  it('статус биржи, ключи, вывод', () => {
    expect([exchangeStatus(ex()), exchangeStatus(ex({ active: false })), exchangeStatus(ex({ implemented: false, active: false }))]).toEqual(['активная', 'разрешена', 'пока не поддерживается'])
    expect(credsText(['read', 'trade'])).toBe('чтение, торговля')
    expect(credsText([])).toBe('ключей нет')
    expect(connectSummary([prov(), prov({ id: 'local', local: true }), prov({ id: 'a', enabled: false })], [ex()])).toBe('Включено провайдеров 2 из 3 (1 в облаке, 1 локально); торгует Bybit, режим: бумага.')
  })
  it('склонения', () => {
    expect([modelsText(1), modelsText(3), modelsText(11)]).toEqual(['1 модель', '3 модели', '11 моделей'])
    expect([swapsLine(0), swapsLine(1), swapsLine(5), swapsLine(22)]).toEqual(['смен не было', '1 смена', '5 смен', '22 смены'])
  })
  it('журнал смен словами', () => {
    const s = { seq: 1, ts: 0, rev: 2, prev_rev: 1, tier: null, by: 'ctl', target: ['entry_scout'], from: 'A', to: 'B' }
    expect(swapText({ ...s, kind: 'use' })).toBe('роль Поиск входа: модель A → B')
    expect(swapText({ ...s, kind: 'reload' })).toBe('перечитаны настройки')
    expect(swapText({ ...s, kind: 'weird', target: [] })).toBe('другое изменение: A → B')
    expect([swapBy('owner-cli'), swapBy('ctl'), swapBy('x'), swapBy(null)]).toEqual(['владелец, в терминале', 'через дашборд', 'другим способом', 'неизвестно'])
  })
})
