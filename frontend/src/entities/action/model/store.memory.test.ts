import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LiveMessage } from '@/shared/api'

/** Фейковый сокет: запоминает отправленное и подписки. В платформу ничего не уходит. */
const sent: Array<Record<string, unknown>> = []
const handlers = new Map<string, (m: LiveMessage) => void>()
vi.mock('@/shared/api', () => ({
  useLive: () => ({
    socket: {
      send: (p: Record<string, unknown>) => { sent.push(p); return true },
      subscribe: (t: string, h: (m: LiveMessage) => void) => { handlers.set(t, h); return () => handlers.delete(t) },
    },
  }),
}))

import { useActionStore } from './store'
import { MEMORY_UNAVAILABLE_TEXT, MEMORY_WARN_TEXT, memoryChallenge, memoryFailure } from '../lib/memory'

const msg = (data: unknown): LiveMessage => ({ topic: 't', seq: 0, ts: 0, data })
const MEMORY = {
  csrf: 'mcsrf', actions: ['lesson_reject', 'rule_disable', 'rules_disable_all', 'exits_rollback'],
  confirm: { lesson_reject: [], rule_disable: ['rule_off', 'block_forever'], rules_disable_all: ['switch_off', 'all_off', 'block_forever'], exits_rollback: ['exits_floor', 'exits_open_positions'] },
}
const WARN = [{ id: 'rule_off', text: 'правило перестанет применяться: новая версия rulebook без него' }, { id: 'block_forever', text: 'условие уходит в чёрный список; снять — ./tb rulebook unblock' }]

function setup(memory: unknown = MEMORY) {
  const s = useActionStore()
  s.start()
  handlers.get('hello')!(msg({ ctl: { enabled: false }, memory }))
  return s
}
const last = () => sent[sent.length - 1]
const reply = (over: Record<string, unknown>) => handlers.get('memory_action_result')!(msg({ id: last().id, op: last().op, ok: true, code: 'OK', msg: 'готово', ...over }))
const RULE = { channel: 'memory' as const, action: 'rule_disable', args: { rule_id: 'RL0001' }, title: 'Отключить правило RL0001' }

describe('аварийные действия «Памяти» через общий диалог', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    sent.length = 0
    handlers.clear()
    vi.useFakeTimers()
  })
  afterEach(() => vi.useRealTimers())

  it('отключить правило: challenge без записи, окно «Принимаю риски», команда с accepted = все id', () => {
    const s = setup()
    expect(s.canMemory('rule_disable')).toBe(true)
    expect(s.run(RULE)).toBe(true)
    expect(last()).toMatchObject({ op: 'rule_disable', kind: 'challenge', csrf: 'mcsrf', rule_id: 'RL0001' })
    expect(String(last().id)).toMatch(/^m\d+$/)
    expect(last()).not.toHaveProperty('action')
    reply({ ok: true, code: 'CONFIRM', warnings: WARN, needs_confirm: true })
    expect(s.phase).toBe('confirm')
    expect(s.riskyRun).toBe(true)
    // последствия простыми словами, без команд терминала
    expect(s.risks.map((r) => r.text)).toEqual([MEMORY_WARN_TEXT.rule_off, MEMORY_WARN_TEXT.block_forever])
    s.accept()
    expect(s.phase).toBe('sending')
    expect(last()).toMatchObject({ op: 'rule_disable', kind: 'command', csrf: 'mcsrf', rule_id: 'RL0001', accepted: ['rule_off', 'block_forever'] })
    reply({ ok: true, code: 'OK' })
    expect(s.phase).toBe('done')
    expect(s.resultText).toMatch(/Правило отключено/)
    expect(s.toasts).toEqual([])           // рискованный запуск: итог в окне, не уведомлением
  })

  it('отклонить урок: без challenge и без окна, итог уведомлением', () => {
    const s = setup()
    const done = vi.fn()
    expect(s.run({ channel: 'memory', action: 'lesson_reject', args: { lesson_id: 'L-0003' }, title: 'Отклонить урок', onDone: done })).toBe(true)
    expect(sent).toHaveLength(1)
    expect(last()).toMatchObject({ op: 'lesson_reject', kind: 'command', lesson_id: 'L-0003', csrf: 'mcsrf' })
    expect(last()).not.toHaveProperty('accepted')
    expect(s.riskyRun).toBe(false)
    reply({ ok: true, code: 'OK' })
    expect(s.phase).toBe('done')
    expect(done).toHaveBeenCalled()
    expect(s.toasts.map((t) => t.tone)).toEqual(['ok'])
  })

  it('отключить все: E_RETRY словами — выключатель записан, версия нет', () => {
    const s = setup()
    s.run({ channel: 'memory', action: 'rules_disable_all', args: {}, title: 'Отключить все правила' })
    reply({ ok: true, code: 'CONFIRM', warnings: [{ id: 'switch_off', text: 'x' }, { id: 'all_off', text: 'y' }, { id: 'block_forever', text: 'z' }] })
    expect(s.risks.map((r) => r.text)).toContain(MEMORY_WARN_TEXT['rules_disable_all:block_forever'])
    s.accept()
    expect(last().accepted).toEqual(['switch_off', 'all_off', 'block_forever'])
    reply({ ok: false, code: 'E_RETRY', msg: 'выключатель записан' })
    expect(s.phase).toBe('failed')
    expect(s.error).toMatch(/выключатель записан/)
    expect(s.errorDetails).toMatch(/E_RETRY/)
  })

  it('вернуть границу выходов: challenge, окно «Принимаю риски», команда с accepted', () => {
    const s = setup()
    expect(s.canMemory('exits_rollback')).toBe(true)
    expect(s.run({ channel: 'memory', action: 'exits_rollback', args: {}, title: 'Выходы: вернуть границу' })).toBe(true)
    expect(last()).toMatchObject({ op: 'exits_rollback', kind: 'challenge', csrf: 'mcsrf' })
    reply({ ok: true, code: 'CONFIRM', warnings: [{ id: 'exits_floor', text: 'x' }, { id: 'exits_open_positions', text: 'y' }], needs_confirm: true })
    expect(s.phase).toBe('confirm')
    expect(s.risks.map((r) => r.text)).toEqual([MEMORY_WARN_TEXT.exits_floor, MEMORY_WARN_TEXT.exits_open_positions])
    s.accept()
    expect(last()).toMatchObject({ op: 'exits_rollback', kind: 'command', accepted: ['exits_floor', 'exits_open_positions'] })
    reply({ ok: true, code: 'OK' })
    expect(s.phase).toBe('done')
    expect(s.resultText).toBe('Выходы вернулись к границе защиты. Открытые позиции не изменились.')
    expect(s.toasts).toEqual([])
  })

  it('отмена в окне: команда не уходит; поздний ответ не меняет состояние', () => {
    const s = setup()
    s.run(RULE)
    reply({ ok: true, code: 'CONFIRM', warnings: WARN })
    s.cancel()
    expect(s.phase).toBe('idle')
    expect(sent.filter((p) => p.kind === 'command')).toHaveLength(0)
    reply({ ok: true, code: 'OK' })
    expect(s.phase).toBe('idle')
  })

  it('нет hello.memory: действие недоступно, ничего не отправлено', () => {
    const s = setup({ actions: [] })
    expect(s.canMemory('rule_disable')).toBe(false)
    expect(s.run(RULE)).toBe(false)
    expect(sent).toHaveLength(0)
    expect(s.error).toBe(MEMORY_UNAVAILABLE_TEXT)
  })

  it('отказ сервера на challenge (CSRF, частота): текст словами', () => {
    const s = setup()
    s.run(RULE)
    reply({ ok: false, code: 'E_RATE' })
    expect(s.phase).toBe('failed')
    expect(s.error).toMatch(/Подождите минуту/)
  })

  it('ответ ctl_final и чужой id не трогают действие «Памяти»', () => {
    const s = setup()
    s.run(RULE)
    handlers.get('memory_action_result')!(msg({ id: 'm999', op: 'rule_disable', ok: true, code: 'CONFIRM', warnings: WARN }))
    expect(s.phase).toBe('checking')
    handlers.get('ctl_final')!(msg({ id: 'q', rid: last().id, status: 'done', code: 'OK', msg: '' }))
    expect(s.phase).toBe('checking')
  })

  it('словари: неизвестное предупреждение и коды', () => {
    expect(memoryChallenge({ warnings: [{ id: 'new_one', text: 'что-то с rulebook' }] }).warnings?.[0].msg).toMatch(/явно принять/)
    expect(memoryChallenge({ warnings: [{ id: 'new_one', text: 'простой текст' }] }).warnings?.[0].msg).toBe('простой текст')
    expect(memoryFailure('E_DB')).toMatch(/База знаний/)
    expect(memoryFailure('E_CSRF')).toMatch(/Обновите страницу/)
  })
})
