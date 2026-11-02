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
import { blockReasons } from '../lib/flow'
import type { Challenge } from './types'

const msg = (data: unknown): LiveMessage => ({ topic: 't', seq: 0, ts: 0, data })
const last = () => sent[sent.length - 1]
const SPEC = { action: 'kill.clear', args: {}, title: 'Снять аварийную остановку', what: 'Причина: тест.' }
const KILL = { active: true, reason: 'тест', flatten: false, mode: 'paper' }
const ok = (id: string, name: string) => ({ id, name, ok: true, msg: '' })
const CH: Challenge = { danger: true, nonce: 'N1', phrases: [], warnings: [], pre_amber: [], min_delay: 2, expires_in: 60, kill: KILL, gates: [ok('K1', 'Исполнитель на связи'), ok('H2', 'Стоп на бирже')] }

function setup(modes = ['kill.clear', 'kill.set']) {
  const s = useActionStore()
  s.start()
  handlers.get('hello')!(msg({ ctl: { enabled: true, csrf: 'tok', full: true, modes } }))
  return s
}
const challenge = (data: Challenge | undefined, over: Record<string, unknown> = {}) =>
  handlers.get('ctl_result')!(msg({ id: last().id, kind: 'challenge', action: 'kill.clear', ok: true, code: 'OK', msg: '', data, ...over }))

describe('kill.clear в общем движке', () => {
  beforeEach(() => { setActivePinia(createPinia()); sent.length = 0; handlers.clear(); vi.useFakeTimers(); vi.setSystemTime(1_000_000) })
  afterEach(() => vi.useRealTimers())

  it('без расширенного управления и без действия в списке недоступно, ничего не уходит', () => {
    const s = useActionStore()
    s.start()
    handlers.get('hello')!(msg({ ctl: { enabled: true, csrf: 'tok', full: false, modes: ['day.start', 'day.stop'] } }))
    expect(s.can('kill.clear')).toBe(false)
    expect(s.run(SPEC)).toBe(false)
    expect(sent).toHaveLength(0)
    const t = setup(['day.start'])
    expect(t.can('kill.clear')).toBe(false)
  })

  it('обычный режим: challenge, окно с рисками, команда с nonce и пустыми args (accepted не добавляется)', () => {
    const s = setup()
    expect(s.can('kill.clear')).toBe(true)
    s.run(SPEC)
    expect(last()).toMatchObject({ op: 'ctl', kind: 'challenge', action: 'kill.clear', args: {}, csrf: 'tok' })
    challenge(CH)
    expect(s.phase).toBe('confirm')
    expect(s.risks.map((r) => r.id)).toEqual(['kill-open'])
    vi.advanceTimersByTime(2100)
    s.accept()
    expect(last()).toMatchObject({ kind: 'command', action: 'kill.clear', args: {}, nonce: 'N1', phrases: [] })
    handlers.get('ctl_result')!(msg({ id: last().id, kind: 'command', action: 'kill.clear', ok: true, code: 'OK', msg: '' }))
    const done = vi.fn()
    s.spec!.onDone = done
    handlers.get('ctl_final')!(msg({ id: 'q', rid: last().id, status: 'done', code: 'OK', msg: 'выполнено' }))
    expect(s.phase).toBe('done')
    expect(done).toHaveBeenCalled()
  })

  it('live: фраза из challenge подставляется сама, жёсткий текст стоит первым', () => {
    const s = setup()
    s.run(SPEC)
    challenge({ ...CH, phrases: ['СНИМАЮ KILL LIVE 2026-11-02'], kill: { ...KILL, mode: 'live' } })
    expect(s.risks.map((r) => r.id)).toEqual(['kill-live', 'kill-open'])
    expect(s.risks[0].level).toBe('danger')
    vi.advanceTimersByTime(2100)
    s.accept()
    expect(last()).toMatchObject({ kind: 'command', nonce: 'N1', phrases: ['СНИМАЮ KILL LIVE 2026-11-02'] })
  })

  it('позиция без стопа: окно блокировки с причиной словами, команда не отправляется', () => {
    const s = setup()
    s.run(SPEC)
    const before = sent.length
    challenge({ blocked: true, danger: false, kill: KILL, gates: [ok('K1', 'Исполнитель на связи'), { id: 'H2', name: 'Стоп на бирже', ok: false, msg: 'позиции без стопа на бирже: ETHUSDT' }] }, { ok: false, code: 'E_GATE', msg: 'жёсткие условия безопасности не выполнены' })
    expect(s.phase).toBe('blocked')
    expect(sent).toHaveLength(before)
    expect(s.error).toMatch(/Условия безопасности/)
    const reasons = blockReasons(s.challenge, '')
    expect(reasons).toHaveLength(1)
    expect(reasons[0]).toMatch(/нет защитного стопа/)
    expect(reasons[0]).not.toMatch(/ничего делать не нужно|бумаге/)       // для снятия остановки это не справка
  })

  it('флаг выключен на сервере: честный отказ человеческим языком', () => {
    const s = setup()
    s.run(SPEC)
    challenge(undefined, { ok: false, code: 'E_DISABLED', msg: 'действие отключено настройкой' })
    expect(s.phase).toBe('failed')
    expect(s.error).toMatch(/отключено в настройках/)
    expect(s.error).not.toMatch(/E_DISABLED/)
  })

  it('режимы отладки не отправляют ничего на сервер', () => {
    const s = useActionStore()
    s.mockKill('kill-live')
    expect(s.can('kill.clear')).toBe(true)
    s.run(SPEC)
    vi.advanceTimersByTime(200)
    expect(s.phase).toBe('confirm')
    expect(s.challenge?.phrases?.[0]).toMatch(/^СНИМАЮ KILL LIVE/)
    expect(sent).toHaveLength(0)
  })
})
