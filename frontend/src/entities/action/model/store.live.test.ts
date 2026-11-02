import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LiveMessage } from '@/shared/api'

/** Фейковый сокет: запоминает отправленное. В платформу ничего не уходит. */
const sent: Array<Record<string, unknown>> = []
const handlers = new Map<string, (m: LiveMessage) => void>()
vi.mock('@/shared/api', () => ({
  useLive: () => ({ socket: { send: (p: Record<string, unknown>) => { sent.push(p); return true }, subscribe: (t: string, h: (m: LiveMessage) => void) => { handlers.set(t, h); return () => handlers.delete(t) } } }),
}))

import { useActionStore } from './store'
import { blockReasons, isLiveStart, risksOf } from '../lib/flow'
import type { Challenge } from './types'

const msg = (data: unknown): LiveMessage => ({ topic: 't', seq: 0, ts: 0, data })
const last = () => sent[sent.length - 1]
const SPEC = { action: 'day.start', args: { mode: 'live', session: 'day' }, title: 'Запуск торговли на реальные деньги', what: 'Начнётся торговый день на реальные деньги.' }
const PHRASE = 'ТОРГУЮ LIVE 2026-11-02 УРОВЕНЬ 3'
const gate = (id: string, ok = true, msg = '') => ({ id, name: id, ok, msg })
const GATES = ['H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'H7', 'H8'].map((id) => gate(id))
const CH: Challenge = { danger: true, nonce: 'N1', phrases: [PHRASE, 'я принимаю риск'], warnings: [{ id: 'W1', code: 'W_PAPER', msg: 'сделок 18 из рекомендованных 30; дней 5 из 7' }, { id: 'W9', msg: 'сделок в журнале 4 из 10' }], pre_amber: [], min_delay: 2, expires_in: 60, gates: GATES }

function setup(over: Record<string, unknown> = {}) {
  const s = useActionStore()
  s.start()
  handlers.get('hello')!(msg({ ctl: { enabled: true, csrf: 'tok', full: true, modes: ['day.start', 'day.stop', 'kill.clear'], ...over } }))
  return s
}
const challenge = (data: Challenge | undefined, over: Record<string, unknown> = {}) =>
  handlers.get('ctl_result')!(msg({ id: last().id, kind: 'challenge', action: 'day.start', ok: true, code: 'OK', msg: '', data, ...over }))

describe('запуск дня на реальные деньги в общем движке', () => {
  beforeEach(() => { setActivePinia(createPinia()); sent.length = 0; handlers.clear(); vi.useFakeTimers(); vi.setSystemTime(1_000_000) })
  afterEach(() => vi.useRealTimers())

  it('распознаётся только режим live у day.start', () => {
    expect(isLiveStart('day.start', { mode: 'live' })).toBe(true)
    expect(isLiveStart('day.start', { mode: 'paper' })).toBe(false)
    expect(isLiveStart('kill.clear', { mode: 'live' })).toBe(false)
    expect(isLiveStart(undefined, undefined)).toBe(false)
  })

  it('без расширенного управления команда не уходит (живой запуск не входит в безопасный набор по режиму)', () => {
    const s = setup({ full: false })
    expect(s.can('day.start')).toBe(true)         // для paper действие доступно; режим live сервер отклонит сам, а окно запуска его и не покажет
    s.run(SPEC)
    expect(last()).toMatchObject({ kind: 'challenge', action: 'day.start', args: { mode: 'live', session: 'day' } })
    challenge(undefined, { ok: false, code: 'E_ELEVATE', msg: 'нужен расширенный режим' })
    expect(s.phase).toBe('failed')
    expect(s.error).not.toMatch(/E_ELEVATE|dash2/)
  })

  it('выключатель владельца выключен: отказ словами, команды нет', () => {
    const s = setup()
    s.run(SPEC)
    challenge(undefined, { ok: false, code: 'E_DISABLED', msg: 'выключено' })
    expect(s.phase).toBe('failed')
    expect(s.error).toMatch(/отключено/)
    expect(sent.filter((p) => p.kind === 'command')).toHaveLength(0)
  })

  it('единое окно: первой строкой реальные деньги, затем предупреждения; фразы и nonce подставляются сами, принятые id = все предупреждения', () => {
    const s = setup()
    s.run(SPEC)
    challenge(CH)
    expect(s.phase).toBe('confirm')
    expect(s.risks.map((r) => r.id)).toEqual(['live', 'W1', 'W9'])
    expect(s.risks[0].level).toBe('danger')
    expect(s.risks[0].text).toMatch(/реальные деньги/i)
    vi.advanceTimersByTime(2100)
    s.accept()
    expect(last()).toMatchObject({ kind: 'command', action: 'day.start', nonce: 'N1', phrases: [PHRASE, 'я принимаю риск'], args: { mode: 'live', session: 'day', accepted: ['W1', 'W9'], allow_amber: false } })
    expect(Object.keys(last().args as object).sort()).toEqual(['accepted', 'allow_amber', 'mode', 'session'])
  })

  it('пауза: до min_delay команда не уходит', () => {
    const s = setup()
    s.run(SPEC)
    challenge(CH)
    const before = sent.length
    s.accept()
    expect(sent).toHaveLength(before)
    expect(s.phase).toBe('confirm')
  })

  it('провал жёсткой проверки: окно блокировки, причины как для реальных денег, команда не отправляется', () => {
    const s = setup()
    s.run(SPEC)
    const bad = GATES.map((g) => (g.id === 'H5' ? gate('H5', false, 'убыток за сутки 3.20 из 3.00 USDT') : g))
    challenge({ ...CH, blocked: true, gates: bad }, { ok: false, code: 'E_GATE', msg: 'условия не выполнены' })
    expect(s.phase).toBe('blocked')
    const why = blockReasons(s.challenge, '', true)
    expect(why).toHaveLength(1)
    expect(why[0]).not.toMatch(/на бумаге|ни на что не влияет/)
    expect(sent.filter((p) => p.kind === 'command')).toHaveLength(0)
  })

  it('отказ после отправки: ворота, не прошедшие при повторной проверке, называются словами', () => {
    const s = setup()
    s.run(SPEC)
    challenge(CH)
    vi.advanceTimersByTime(2100)
    s.accept()
    const id = last().id
    handlers.get('ctl_result')!(msg({ id, kind: 'command', action: 'day.start', ok: true, code: 'OK', msg: '' }))
    handlers.get('ctl_final')!(msg({ id: 'q', rid: id, status: 'rejected', code: 'E_GATE', msg: 'жёсткие условия', data: { phase: 'after_switch', gates: [gate('H3', false, 'приватный WS не подключён')] } }))
    expect(s.phase).toBe('failed')
    expect(s.error).toMatch(/Условия безопасности/)
    expect(s.error).toMatch(/Связь с биржей/)
    expect(s.error).not.toMatch(/H3|E_GATE|after_switch/)
  })

  it('прочие отказы живого запуска идут обычным текстом кода (предупреждения изменились, пауза, лимит)', () => {
    for (const [code, re] of [['E_WARN_CHANGED', /предупреждений изменился/], ['E_COOLDOWN', /пауза/i], ['E_LIMITS', /лимит запусков/i]] as const) {
      setActivePinia(createPinia()); sent.length = 0; handlers.clear()
      const s = setup()
      s.run(SPEC)
      challenge(CH)
      vi.advanceTimersByTime(2100)
      s.accept()
      const id = last().id
      handlers.get('ctl_result')!(msg({ id, kind: 'command', action: 'day.start', ok: true, code: 'OK', msg: '' }))
      handlers.get('ctl_final')!(msg({ id: 'q', rid: id, status: 'rejected', code, msg: '' }))
      expect(s.error).toMatch(re)
      expect(s.error).not.toMatch(/E_[A-Z]/)
    }
  })

  it('risksOf: строка про реальные деньги только для живого запуска', () => {
    expect(risksOf(CH, false, true)[0].id).toBe('live')
    expect(risksOf(CH, false, false).map((r) => r.id)).toEqual(['W1', 'W9'])
  })
})
