import { describe, expect, it } from 'vitest'
import { canBeginLive, liveAccess, liveGateRows, liveGatesBad, liveLimits } from './live'
import type { ChallengeData, CtlHello, CtlSnapshot } from '../model/types'

const hello = (over: Partial<CtlHello> = {}): CtlHello => ({ enabled: true, csrf: 'x', full: true, modes: ['day.start', 'day.stop'], ...over })
const ctl = (over: Partial<CtlSnapshot> = {}): CtlSnapshot => ({
  alive: true, configured: true, platform: { state: 'stopped', mode: 'observe', session: null, health: 'green', day_id: null, trading: false, observe: true },
  step: null, kill: null, live_start: true, risk: { level: 3, name: 'Базовый' }, busy: false, checks: [], banner: null, day: null, ...over,
})
const gate = (id: string, ok = true, msg = '', extra: Record<string, unknown> = {}) => ({ id, name: id, ok, msg, ...extra })
const ALL = (): ChallengeData['gates'] => [
  gate('H1', true, 'state/KILL нет'), gate('H2', true, 'проверится после переключения исполнителя на live', { deferred: true }), gate('H3', true, 'исполнитель жив, режим будет переключён на live'),
  gate('H4', true, 'проверится после переключения исполнителя на live', { deferred: true }), gate('H5', true, 'убыток за сутки 0.40 из 3.00 USDT'), gate('H6', true, 'мозг отвечает (hb 2 с)'),
  gate('H7', true, 'конфигурации в порядке, уровень риска 3'), gate('H8', true, 'запусков за сутки 1 из 3'),
]

describe('реальные деньги: доступность', () => {
  it('доступно только при расширенном режиме, живой службе управления и включённом выключателе владельца', () => {
    expect(liveAccess(hello(), ctl())).toEqual({ ok: true, why: null, reason: null })
    expect(liveAccess(hello({ enabled: false }), ctl()).why).toBe('link')
    expect(liveAccess(hello({ csrf: undefined }), ctl()).why).toBe('link')
    expect(liveAccess(hello({ full: false }), ctl()).why).toBe('full')
    expect(liveAccess(hello({ full: undefined }), ctl()).why).toBe('full')
    expect(liveAccess(hello({ modes: [] }), ctl()).why).toBe('full')
    expect(liveAccess(hello(), null).why).toBe('down')
    expect(liveAccess(hello(), ctl({ alive: false })).why).toBe('down')
    expect(liveAccess(hello(), ctl({ live_start: false })).why).toBe('off')
    expect(liveAccess(hello(), ctl({ live_start: undefined })).why).toBe('off')
  })
  it('причина написана словами, без кодов и служебных имён', () => {
    for (const h of [hello({ enabled: false }), hello({ full: false }), hello()]) {
      for (const c of [null, ctl({ alive: false }), ctl({ live_start: false })]) {
        const a = liveAccess(h, c)
        if (a.ok) continue
        expect(a.reason).toBeTruthy()
        expect(a.reason).not.toMatch(/E_[A-Z]|dash2|allow_|live_start|\.json/)
      }
    }
  })
})

describe('реальные деньги: проверки допуска словами', () => {
  it('все восемь по порядку, у каждой слово статуса, без служебных кодов', () => {
    const rows = liveGateRows({ gates: [...ALL()!].reverse() })
    expect(rows.map((r) => r.id)).toEqual(['H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'H7', 'H8'])
    for (const r of rows) {
      expect(['готово', 'проверится при запуске']).toContain(r.word)
      expect(r.title).not.toMatch(/\bH\d\b|[A-Za-z_]{3,}/)
      expect(r.todo).toBe('')
    }
    expect(rows.find((r) => r.id === 'H2')!.state).toBe('later')
    expect(rows.find((r) => r.id === 'H3')!.state).toBe('later')       // сообщение «будет переключён…» тоже отложенная проверка
    expect(rows.find((r) => r.id === 'H1')!.state).toBe('ok')
    expect(liveGatesBad(rows)).toBe(0)
  })
  it('не пройденная проверка: «не готово», что делать, серьёзность как для реальных денег', () => {
    const g = ALL()!
    g[4] = gate('H5', false, 'убыток за сутки 3.20 из 3.00 USDT')
    g[5] = gate('H6', false, 'нет heartbeat сервиса brain')
    const rows = liveGateRows({ gates: g, blocked: true })
    const bad = rows.filter((r) => r.state === 'bad')
    expect(bad.map((r) => r.id)).toEqual(['H5', 'H6'])
    for (const r of bad) {
      expect(r.word).toBe('не готово')
      expect(r.todo.length).toBeGreaterThan(10)
      expect(r.title + r.todo).not.toMatch(/на бумаге|ни на что не влияет/)       // для реальных денег это не справка
    }
    expect(liveGatesBad(rows)).toBe(2)
  })
  it('нет данных: пустой список', () => {
    expect(liveGateRows(null)).toEqual([])
    expect(liveGateRows({})).toEqual([])
  })
})

describe('реальные деньги: можно ли начинать и откуда цифры', () => {
  it('начать можно, когда проверки получены и все пройдены; предупреждения и danger не мешают (их принимает общее окно)', () => {
    expect(canBeginLive({ gates: ALL(), danger: true, warnings: [{ id: 'W1' }] })).toBe(true)
    expect(canBeginLive(null)).toBe(false)
    expect(canBeginLive({ gates: [] })).toBe(false)
    expect(canBeginLive({ blocked: true, gates: ALL() })).toBe(false)
    const g = ALL()!
    g[0] = gate('H1', false, 'state/KILL есть')
    expect(canBeginLive({ gates: g })).toBe(false)
    expect(canBeginLive({ gates: [{ id: 'H1' }] })).toBe(false)         // статус не пришёл: не считаем пройденным
  })
  it('пределы только из фраз платформы; нет фразы: нет строки, ничего не выдумывается', () => {
    expect(liveLimits({ gates: ALL() })).toEqual(['Дневной лимит убытка: 3.00 USDT, сегодня уже 0.40.', 'Запусков на реальные деньги сегодня: 1 из 3.'])
    expect(liveLimits({ gates: [gate('H5', false, 'просадка капитала 25% достигла порога 20%')] })).toEqual([])
    expect(liveLimits(null)).toEqual([])
  })
})
