import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import type { LiveMessage } from '@/shared/api'

const sent: Array<Record<string, unknown>> = []
const handlers = new Map<string, (m: LiveMessage) => void>()
vi.mock('@/shared/api', () => ({
  useLive: () => ({ socket: { send: (p: Record<string, unknown>) => { sent.push(p); return true }, subscribe: (t: string, h: (m: LiveMessage) => void) => { handlers.set(t, h); return () => handlers.delete(t) } } }),
}))

import { useActionStore } from '@/entities/action'
import { useControlStore, type CtlSnapshot } from '@/entities/control'
import { useKillRelease } from './useKillRelease'

const msg = (data: unknown): LiveMessage => ({ topic: 't', seq: 0, ts: 0, data })
const CTL = (over: Partial<CtlSnapshot> = {}): CtlSnapshot => ({
  alive: true, configured: true, platform: { state: 'halted', mode: 'paper', session: null, health: 'red', day_id: null, trading: false, observe: false }, step: null,
  kill: { flatten: false, reason: 'ручная остановка' }, clear_kill: true, risk: { level: 3, name: 'Базовый' }, busy: false, checks: [], banner: null, day: null, ...over,
})

function setup(opts: { ctl?: Partial<CtlSnapshot>; hello?: Record<string, unknown> } = {}) {
  const action = useActionStore()
  action.start()
  handlers.get('hello')!(msg({ ctl: { enabled: true, csrf: 'tok', full: true, modes: ['kill.clear'], ...opts.hello } }))
  const control = useControlStore()
  control.ctl = CTL(opts.ctl)
  const scope = effectScope()
  const e = scope.run(() => useKillRelease())!
  return { e, control, action }
}

describe('useKillRelease', () => {
  beforeEach(() => { setActivePinia(createPinia()); sent.length = 0; handlers.clear() })

  it('кнопка есть при расширенном управлении и включённом флаге', () => {
    const { e } = setup()
    expect(e.shown.value).toBe(true)
    expect(e.canClick.value).toBe(true)
    expect(e.note.value).toBeNull()
    expect(e.release()).toBe(true)
    expect(sent[0]).toMatchObject({ kind: 'challenge', action: 'kill.clear', args: {} })
  })

  it('флаг выключен: кнопки нет, есть инструкция с командой, ничего не отправляется', () => {
    const { e } = setup({ ctl: { clear_kill: false } })
    expect(e.canClick.value).toBe(false)
    expect(e.note.value).toMatch(/решает владелец/)
    expect(e.command).toBe('./tb exec resume')
    expect(e.release()).toBe(false)
    expect(sent).toHaveLength(0)
  })

  it('нет расширенного управления: кнопки нет, причина названа', () => {
    const { e } = setup({ hello: { full: false, modes: ['day.start', 'day.stop'] } })
    expect(e.canClick.value).toBe(false)
    expect(e.note.value).toMatch(/Расширенное управление/)
    expect(e.release()).toBe(false)
  })

  it('KILL нет: блока нет; после успешного снятия ждём тему ctl и показываем «снята»', () => {
    const { e, control } = setup({ ctl: { kill: null } })
    expect(e.shown.value).toBe(false)
    control.ctl = CTL()
    expect(e.shown.value).toBe(true)
    e.wait.begin(() => !control.ctl?.kill)
    control.ctl = CTL({ kill: null })
    expect(e.active.value).toBe(false)
    expect(e.shown.value).toBe(true)         // сообщение «снята» остаётся, пока идёт подтверждение данных
  })

  it('live по режиму платформы: предупреждение и расширенный перечень проверок', () => {
    const { e } = setup({ ctl: { platform: { state: 'halted', mode: 'live', session: null, health: 'red', day_id: null, trading: false, observe: false } } })
    expect(e.live.value).toBe(true)
    expect(e.checks.value.length).toBeGreaterThan(3)
  })

  it('позиции: без закрытия сказано про стопы, с закрытием про ожидание', () => {
    const { e } = setup()
    e.mockPositions.value = 2
    expect(e.positionsText.value).toMatch(/2 позиции/)
    const w = setup({ ctl: { kill: { flatten: true, reason: 'x' } } })
    w.e.mockPositions.value = 1
    expect(w.e.positionsText.value).toMatch(/нельзя/)
  })
})
