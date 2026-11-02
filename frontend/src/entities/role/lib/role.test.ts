import { describe, expect, it } from 'vitest'
import { groups, issuesOf, pct, placeOf, roleLabel, summary } from './role'
import type { RoleModel, RoleRow, RolesSnapshot } from '../model/types'

const model = (o: Partial<RoleModel> = {}): RoleModel => ({ id: 'lm', provider: 'local', model_name: 'gpt-oss', provider_enabled: true, live_ok: false, params: { temperature: 0.1, max_out_tok: 1500, thinking: 'low' }, ...o })
const role = (o: Partial<RoleRow> = {}): RoleRow => ({
  role: 'entry_scout', scope: 'team', team: 'A', live_risk: false, io: null, primary: model(), fallback: [], budget_day_usd: null,
  today: { usd: 0, calls: 5, errors: 0, budget_left_usd: null }, day: { calls: 100, err_rate: 0.02, fmt_err_rate: 0.03, fallbacks: 0, p50_ms: 1000, usd: 0 }, ...o,
})
const snap = (roles: RoleRow[], o: Partial<RolesSnapshot> = {}): RolesSnapshot => ({ mode: 'paper', rev: 1, live_team: 'A', exchange: null, providers: null, usage_today: null, errors: [], roles, ...o })

describe('роли мозга', () => {
  it('названия ролей по-русски, неизвестная без служебного кода', () => {
    expect([roleLabel('entry_scout'), roleLabel('news_reader'), roleLabel('new_role')]).toEqual(['Поиск входа', 'Читатель новостей', 'Другая роль мозга'])
  })
  it('место работы модели', () => {
    expect([placeOf(model()), placeOf(model({ provider: 'deepseek' })), placeOf(null)]).toEqual(['локально', 'облако', '–'])
  })
  it('замечания: выключенный провайдер, неверные форматы, ошибки; на малой выборке и в норме молчим', () => {
    expect(issuesOf(role(), 'paper')).toEqual([])
    expect(issuesOf(role({ fallback: [model({ id: 'fb', provider: 'anthropic', model_name: 'claude', provider_enabled: false })] }), 'paper')[0]).toMatchObject({ tone: 'warn' })
    expect(issuesOf(role({ primary: model({ provider_enabled: false }) }), 'paper')[0]).toMatchObject({ tone: 'bad' })
    expect(issuesOf(role({ day: { calls: 100, err_rate: 0.02, fmt_err_rate: 0.26, fallbacks: 0, p50_ms: 1, usd: 0 } }), 'paper')[0].text).toContain('26%')
    expect(issuesOf(role({ day: { calls: 5, err_rate: 0.9, fmt_err_rate: 0.9, fallbacks: 0, p50_ms: 1, usd: 0 } }), 'paper')).toEqual([])
  })
  it('в live роль для live без запасной модели и без допуска получает замечания, в paper нет', () => {
    const r = role({ live_risk: true })
    expect(issuesOf(r, 'paper')).toEqual([])
    expect(issuesOf(r, 'live').map((i) => i.tone)).toEqual(['warn', 'bad'])
  })
  it('сводка: одна модель на все роли или перечень; число запасных', () => {
    const one = summary(snap([role(), role({ role: 'reviewer', fallback: [model()] })]))
    expect(one.line).toBe('Все 2 роли думают одной моделью: gpt-oss')
    expect(one.detail).toBe('Запасная модель, на которую роль переходит при сбое, есть у 1 из 2 ролей.')
    expect(summary(snap([role(), role({ primary: model({ model_name: 'other' }) })])).line).toContain('моделей: 2')
    expect(summary(snap([])).line).toBe('Роли мозга не загружены')
  })
  it('группы: сначала команда, затем общие; пустых нет', () => {
    const g = groups(snap([role(), role({ role: 'reviewer', scope: 'shared', team: null })]))
    expect(g.map((x) => x.id)).toEqual(['team', 'shared'])
    expect(groups(snap([role()])).map((x) => x.id)).toEqual(['team'])
    expect(g.map((x) => x.title).join(' ')).not.toMatch(/[A-Za-z]/)        // без «Команда A» и других служебных букв
    expect(issuesOf(role({ live_risk: true }), 'live').map((i) => i.text).join(' ')).not.toMatch(/live/i)
  })
  it('проценты', () => {
    expect([pct(0.0763), pct(null)]).toEqual(['8%', '–'])
  })
})
