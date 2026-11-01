import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LiveMessage } from '@/shared/api'

/** Фейковый сокет: запоминает отправленное и подписки. В платформу ничего не уходит. */
const sent: Array<Record<string, unknown>> = []
const handlers = new Map<string, (m: LiveMessage) => void>()
let linkUp = true
vi.mock('@/shared/api', () => ({
  useLive: () => ({
    socket: {
      send: (p: Record<string, unknown>) => { if (!linkUp) return false; sent.push(p); return true },
      subscribe: (t: string, h: (m: LiveMessage) => void) => { handlers.set(t, h); return () => handlers.delete(t) },
    },
  }),
}))

import { useActionStore } from './store'
import { FINAL_TIMEOUT_MS, TIMEOUT_TEXT, UNAVAILABLE_TEXT } from '../lib/flow'
import type { Challenge } from './types'

const msg = (data: unknown): LiveMessage => ({ topic: 't', seq: 0, ts: 0, data })
const FULL = { enabled: true, csrf: 'tok', full: true, modes: ['kill.set', 'risk.set', 'day.start'] }

function setup() {
  const s = useActionStore()
  s.start()
  handlers.get('hello')!(msg({ ctl: FULL }))
  return s
}
const last = () => sent[sent.length - 1]
const answerChallenge = (data: Challenge | undefined, over: Record<string, unknown> = {}) =>
  handlers.get('ctl_result')!(msg({ id: last().id, kind: 'challenge', action: last().action, ok: true, code: 'OK', msg: '', data, ...over }))
const answerCommand = (over: Record<string, unknown> = {}) =>
  handlers.get('ctl_result')!(msg({ id: last().id, kind: 'command', action: last().action, ok: true, code: 'OK', msg: 'принято', ...over }))
const final = (rid: unknown, over: Record<string, unknown> = {}) =>
  handlers.get('ctl_final')!(msg({ id: 'q1', rid, status: 'done', code: 'OK', msg: 'готово', data: null, ...over }))

const KILL = { action: 'kill.set', args: { flatten: false, reason: 'из дашборда' }, title: 'Аварийная остановка' }
const RISKY: Challenge = { danger: true, nonce: 'N', phrases: ['П1'], warnings: [{ id: 'W1', msg: 'осторожно' }], min_delay: 3, expires_in: 90, gates: [{ id: 'G1', name: 'Связь', ok: true }] }

describe('action store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    sent.length = 0
    handlers.clear()
    linkUp = true
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
  })
  afterEach(() => vi.useRealTimers())

  it('безрисковое действие: challenge, затем команда сразу, без confirm; итог уведомлением', () => {
    const s = setup()
    expect(s.run(KILL)).toBe(true)
    expect(s.phase).toBe('checking')
    expect(last()).toMatchObject({ op: 'ctl', kind: 'challenge', action: 'kill.set', csrf: 'tok' })
    expect(String(last().id)).toMatch(/^a\d+$/)
    answerChallenge({ gates: [], warnings: [], pre_amber: [], phrases: [], danger: false, nonce: 'n0' })
    expect(s.phase).toBe('sending')
    expect(last()).toMatchObject({ kind: 'command', args: { flatten: false, reason: 'из дашборда' }, nonce: 'n0', phrases: [] })
    answerCommand()
    expect(s.phase).toBe('waiting')
    final(last().id)
    expect(s.phase).toBe('done')
    expect(s.riskyRun).toBe(false)
    expect(s.toasts).toHaveLength(1)
    expect(s.toasts[0]).toMatchObject({ tone: 'ok', title: 'Аварийная остановка', text: 'готово' })
  })

  it('рискованное: ждёт confirm, после accept отправляет nonce, phrases и accepted автоматически', () => {
    const s = setup()
    const onDone = vi.fn()
    s.run({ action: 'risk.set', args: { level: 5 }, title: 'Риск 5', onDone })
    answerChallenge({ ...RISKY, min_delay: 0 })
    expect(s.phase).toBe('confirm')
    expect(sent).toHaveLength(1)
    expect(s.riskyRun).toBe(true)
    s.accept()
    expect(s.phase).toBe('sending')
    expect(last()).toMatchObject({ kind: 'command', action: 'risk.set', nonce: 'N', phrases: ['П1'], args: { level: 5, accepted: ['W1'] } })
    expect(last().args).not.toHaveProperty('allow_amber')
    answerCommand()
    final(last().id)
    expect(s.phase).toBe('done')
    expect(s.toasts).toHaveLength(0)               // рискованный итог показывает диалог, не уведомление
    expect(onDone).toHaveBeenCalledOnce()
  })

  it('blocked: команда не отправляется, accept ничего не делает', () => {
    const s = setup()
    s.run({ action: 'day.start', args: { mode: 'paper' }, title: 'Старт' })
    answerChallenge({ blocked: true, gates: [{ id: 'G2', name: 'Сверка', ok: false, msg: 'устарела' }] }, { code: 'E_GATE', msg: 'условия не выполнены' })
    expect(s.phase).toBe('blocked')
    s.accept()
    expect(sent).toHaveLength(1)
    s.cancel()
    expect(s.phase).toBe('idle')
  })

  it('отказ E_* на команду: failed с человеческим текстом, код и текст сервера только в подробностях', () => {
    const s = setup()
    s.run(KILL)
    answerChallenge({})
    answerCommand({ ok: false, code: 'E_RATE', msg: 'слишком часто' })
    expect(s.phase).toBe('failed')
    expect(s.error).toBe('Слишком частые попытки. Подождите минуту и повторите.')
    expect(s.error).not.toMatch(/E_RATE/)
    expect(s.errorDetails).toContain('E_RATE')
    expect(s.toasts[0]).toMatchObject({ tone: 'bad', text: 'Слишком частые попытки. Подождите минуту и повторите.' })
  })

  it('отказ на challenge и итог rejected', () => {
    const s = setup()
    s.run(KILL)
    answerChallenge(undefined, { ok: false, code: 'E_ELEVATE', msg: 'режим выключен' })
    expect(s.phase).toBe('failed')
    expect(s.error).toMatch(/расширенное управление/)
    expect(s.error).not.toMatch(/E_ELEVATE/)
    s.run(KILL)
    answerChallenge({})
    answerCommand()
    final(last().id, { status: 'rejected', code: 'E_STATE', msg: 'уже активно' })
    expect(s.phase).toBe('failed')
    expect(s.error).toMatch(/Сейчас так сделать нельзя/)
  })

  it('таймаут ожидания итога: failed, «платформа не ответила»', () => {
    const s = setup()
    s.run(KILL)
    answerChallenge({})
    answerCommand()
    vi.advanceTimersByTime(FINAL_TIMEOUT_MS - 1)
    expect(s.phase).toBe('waiting')
    vi.advanceTimersByTime(2)
    expect(s.phase).toBe('failed')
    expect(s.error).toBe(TIMEOUT_TEXT)
    expect(TIMEOUT_TEXT).toMatch(/Платформа не ответила/)
  })

  it('min_delay: до конца паузы accept не отправляет', () => {
    const s = setup()
    s.run({ action: 'risk.set', args: { level: 4 }, title: 'Риск' })
    answerChallenge(RISKY)
    s.accept()
    expect(s.phase).toBe('confirm')
    vi.advanceTimersByTime(2900)
    s.accept()
    expect(sent).toHaveLength(1)
    vi.advanceTimersByTime(200)
    s.accept()
    expect(last().kind).toBe('command')
  })

  it('expires_in: после срока accept запрашивает условия заново, а не падает', () => {
    const s = setup()
    s.run({ action: 'risk.set', args: { level: 4 }, title: 'Риск' })
    answerChallenge({ ...RISKY, min_delay: 0, expires_in: 30 })
    vi.advanceTimersByTime(31_000)
    s.accept()
    expect(s.phase).toBe('checking')
    expect(last()).toMatchObject({ kind: 'challenge' })
    expect(sent).toHaveLength(2)
    answerChallenge({ ...RISKY, nonce: 'N2', min_delay: 0 })
    s.accept()
    expect(last()).toMatchObject({ kind: 'command', nonce: 'N2' })
  })

  it('недоступное действие: failed с пояснением про auth.dash2_full, в сокет ничего не уходит', () => {
    const s = setup()
    expect(s.run({ action: 'brain.use', args: {}, title: 'Мозг' })).toBe(false)
    expect(s.phase).toBe('failed')
    expect(s.error).toBe(UNAVAILABLE_TEXT)
    expect(sent).toHaveLength(0)
    expect(s.can('kill.set')).toBe(true)
    expect(s.can('brain.use')).toBe(false)
    handlers.get('hello')!(msg({ ctl: { enabled: true, csrf: 'tok', full: false, modes: ['day.start', 'day.stop'] } }))
    expect(s.can('kill.set')).toBe(false)
  })

  it('второй run во время активного игнорируется с сообщением', () => {
    const s = setup()
    s.run(KILL)
    expect(s.run({ action: 'risk.set', args: {}, title: 'Другое' })).toBe(false)
    expect(sent).toHaveLength(1)
    expect(s.phase).toBe('checking')
    expect(s.toasts[0].text).toMatch(/Аварийная остановка/)
  })

  it('чужие id (поток запуска дня) игнорируются; обрыв связи даёт failed', () => {
    const s = setup()
    s.run(KILL)
    handlers.get('ctl_result')!(msg({ id: 'c1', kind: 'challenge', action: 'day.start', ok: true, code: 'OK', msg: '', data: {} }))
    expect(s.phase).toBe('checking')
    answerChallenge({})
    answerCommand()
    final('c9')
    expect(s.phase).toBe('waiting')
    final(last().id)
    expect(s.phase).toBe('done')
    linkUp = false
    s.run(KILL)
    expect(s.phase).toBe('failed')
    expect(s.error).toMatch(/^Нет связи с платформой/)
  })

  it('отмена: поздний ответ сервера не оживляет запуск', () => {
    const s = setup()
    s.run(KILL)
    const id = last().id
    s.cancel()
    handlers.get('ctl_result')!(msg({ id, kind: 'challenge', action: 'kill.set', ok: true, code: 'OK', msg: '', data: RISKY }))
    expect(s.phase).toBe('idle')
  })
})
