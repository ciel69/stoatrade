import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LiveMessage } from '@/shared/api'

/** Фейковый сокет: запоминает отправленное и подписки. В платформу ничего не уходит. Ключ ниже выдуманный. */
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
import { KEY_MAX, keyOutcomeText, keyProblem } from '../lib/key'

const FAKE = 'fake-key-123'
const msg = (data: unknown): LiveMessage => ({ topic: 'ctl_result', seq: 0, ts: 0, data })
const FULL = { enabled: true, csrf: 'tok', full: true, modes: ['llm.provider', 'llmkey'] }
const reply = (over: Record<string, unknown> = {}) =>
  handlers.get('ctl_result')!(msg({ id: sent[sent.length - 1].id, kind: 'llmkey', action: 'llm.key.set', ok: true, code: 'OK', msg: 'ключ сохранён', provider: 'deepseek', ...over }))

function setup(hello: unknown = FULL) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const s = useActionStore()
  s.start()
  handlers.get('hello')!({ topic: 'hello', seq: 0, ts: 0, data: { ctl: hello } })
  return { s, pinia }
}
/** Всё видимое состояние (DevTools): все сторы и значения свойств. */
const visible = (s: ReturnType<typeof useActionStore>, pinia: ReturnType<typeof createPinia>) => JSON.stringify([pinia.state.value, s.$state, s.hello, s.spec, s.toasts, s.error, s.resultText])

describe('формат ключа', () => {
  it('годный, с пробелами по краям тоже', () => {
    expect(keyProblem(FAKE)).toBeNull()
    expect(keyProblem(`  ${FAKE}\n`)).toBeNull()
  })
  it('пустой, с пробелом внутри, не ASCII, слишком длинный', () => {
    expect(keyProblem('')).toBe('Введите ключ.')
    expect(keyProblem('abc def')).toMatch(/пробелов/)
    expect(keyProblem('ключ123')).toMatch(/латинские/)
    expect(keyProblem('a'.repeat(KEY_MAX + 1))).toMatch(/длиннее/)
    expect(keyProblem('a'.repeat(KEY_MAX))).toBeNull()
  })
  it('тексты по коду не берут msg, неизвестный код безопасен', () => {
    expect(keyOutcomeText('E_KEY')).toBe('Ключ не принят сервером.')
    expect(keyOutcomeText('E_SOMETHING')).toBe('Ключ не сохранён (E_SOMETHING).')
    expect(keyOutcomeText('произвольный текст с fake-key-123')).toBe('Ключ не сохранён.')
  })
})

describe('отправка ключа на фейковом сокете', () => {
  const spies = (['log', 'info', 'warn', 'error', 'debug'] as const).map((k) => vi.spyOn(console, k).mockImplementation(() => {}))
  beforeEach(() => { sent.length = 0; handlers.clear(); spies.forEach((s) => s.mockClear()); vi.useFakeTimers() })
  afterEach(() => vi.useRealTimers())

  it('ключ уходит ровно один раз, в нужной форме', async () => {
    const { s } = setup()
    const p = s.sendKey('deepseek', `  ${FAKE}\n`)
    expect(sent).toHaveLength(1)
    expect(sent[0]).toMatchObject({ op: 'llmkey', csrf: 'tok', provider: 'deepseek', value: FAKE })
    expect(String(sent[0].id)).toMatch(/^k\d+$/)
    reply()
    await expect(p).resolves.toEqual({ ok: true, code: 'OK', text: 'Ключ сохранён.' })
    expect(sent).toHaveLength(1)
  })

  it('значения нет ни в одном состоянии, ни в консоли, ни в итоге', async () => {
    const { s, pinia } = setup()
    const p = s.sendKey('deepseek', FAKE)
    expect(visible(s, pinia)).not.toContain(FAKE)         // пока ждём ответа
    reply()
    const out = await p
    expect(visible(s, pinia)).not.toContain(FAKE)         // и после
    expect(JSON.stringify(out)).not.toContain(FAKE)
    for (const sp of spies) expect(JSON.stringify(sp.mock.calls)).not.toContain(FAKE)
  })

  it('ответ с ошибкой не содержит эха значения, даже если сервер его прислал в msg', async () => {
    const { s, pinia } = setup()
    const p = s.sendKey('deepseek', FAKE)
    reply({ ok: false, code: 'E_KEY', msg: `ключ ${FAKE} не принят` })
    const out = await p
    expect(out).toEqual({ ok: false, code: 'E_KEY', text: 'Ключ не принят сервером.' })
    expect(JSON.stringify(out)).not.toContain(FAKE)
    expect(visible(s, pinia)).not.toContain(FAKE)
    expect(s.toasts).toHaveLength(0)
  })

  it('негодный ключ не отправляется', async () => {
    const { s } = setup()
    await expect(s.sendKey('deepseek', 'с пробелом внутри')).resolves.toMatchObject({ ok: false, code: 'E_ARGS' })
    expect(sent).toHaveLength(0)
  })

  it('пока идёт отправка, вторая не уходит', async () => {
    const { s } = setup()
    const first = s.sendKey('deepseek', FAKE)
    await expect(s.sendKey('deepseek', FAKE)).resolves.toMatchObject({ ok: false, code: 'E_BUSY' })
    expect(sent).toHaveLength(1)
    reply()
    await first
    const again = s.sendKey('deepseek', FAKE)
    expect(sent).toHaveLength(2)
    reply()
    await again
  })

  it('чужой ответ не закрывает отправку; тишина 15 с даёт «не ответил»', async () => {
    const { s } = setup()
    const p = s.sendKey('deepseek', FAKE)
    reply({ id: 'k999' })
    reply({ kind: 'command' })
    vi.advanceTimersByTime(15_000)
    await expect(p).resolves.toMatchObject({ ok: false, code: 'E_WAIT' })
  })

  it('без hello.ctl.csrf ничего не уходит', async () => {
    const { s } = setup({ enabled: false })
    await expect(s.sendKey('deepseek', FAKE)).resolves.toMatchObject({ ok: false, code: 'E_LINK' })
    expect(sent).toHaveLength(0)
  })

  it('ответ ключа не мешает действию: ctl_result других запросов идёт как раньше', () => {
    const { s } = setup()
    expect(s.can('llmkey')).toBe(true)
    expect(s.can('llm.provider')).toBe(true)
    expect(s.active).toBe(false)
  })
})
