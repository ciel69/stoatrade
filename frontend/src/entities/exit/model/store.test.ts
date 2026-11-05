import { createPinia, disposePinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LiveMessage } from '@/shared/api'

type Link = 'open' | 'closed'

/** Фейковый сокет: ссылка управляется тестом, отправленное и подписки запоминаются. В платформу ничего не уходит. */
const h = vi.hoisted(() => ({
  sent: [] as Array<Record<string, unknown>>,
  handlers: new Map<string, (m: LiveMessage) => void>(),
  setLink: (_state: Link) => {},
}))

vi.mock('@/shared/api', async () => {
  const { ref } = await import('vue')
  const link = ref<Link>('closed')
  h.setLink = (state) => { link.value = state }
  const socket = {
    send: (p: Record<string, unknown>) => {
      if (link.value !== 'open') return false
      h.sent.push(p)
      return true
    },
    subscribe: (topic: string, fn: (m: LiveMessage) => void) => {
      h.handlers.set(topic, fn)
      return () => h.handlers.delete(topic)
    },
  }
  return { useLive: () => ({ socket, link }) }
})

import { EXITS_TOPIC, useExitsStore } from './store'

const msg = (data: unknown, topic = EXITS_TOPIC): LiveMessage => ({ topic, seq: 0, ts: 0, data })

const reply = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  ok: true,
  ts: 1,
  db: 'OK',
  version: { seq: 2, ver: 'X-0002', parent: 'X-0001', reason_code: 'PROMOTE', ts: 100 },
  versions: 2,
  effective: { ver: 'X-0002', policy_h: 'p', why: 'OK' },
  snapshot: { ver: 'X-0002', policy_h: 'p', floor_h: 'f', ts: 100 },
  lease: { ts: 100, ver: 'X-0002', policy_h: 'p', by: 'promote' },
  switch: { apply: true, source: 'default', ts: null, actor: null },
  promotion: { mode: 'auto' },
  trials: { open: 1, closed: 0, rows: [] },
  watch: null,
  config: { errors: [] },
  ...over,
})

let pinia: ReturnType<typeof createPinia>

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  h.sent.length = 0
  h.handlers.clear()
  h.setLink('closed')
})

afterEach(() => {
  disposePinia(pinia)
})

describe('хранилище политики выхода', () => {
  it('start подписывается на exits_status_detail и не шлёт запрос без связи', () => {
    const s = useExitsStore()
    s.start()
    expect([...h.handlers.keys()]).toEqual([EXITS_TOPIC])
    expect(h.sent).toEqual([])
  })

  it('при открытии связи отправляет exits_status; повторный start без ответа не дублирует запрос', async () => {
    const s = useExitsStore()
    s.start()
    h.setLink('open')
    await nextTick()
    expect(h.sent).toEqual([{ op: 'exits_status' }])
    s.start()
    expect(h.sent).toHaveLength(1)
  })

  it('если связь уже открыта, start сразу запрашивает состояние', () => {
    h.setLink('open')
    const s = useExitsStore()
    s.start()
    expect(h.sent).toEqual([{ op: 'exits_status' }])
    expect(s.pending).toBe(true)
  })

  it('apply принимает ответ и снимает ожидание', async () => {
    const s = useExitsStore()
    s.start()
    h.setLink('open')
    await nextTick()
    s.apply(msg(reply()))
    expect(s.pending).toBe(false)
    expect(s.policy?.version?.ver).toBe('X-0002')
    expect(s.policy?.effective?.why).toBe('OK')
    expect(s.policy?.trials.open).toBe(1)
  })

  it('чужой topic и мусор не подменяют состояние', () => {
    const s = useExitsStore()
    s.apply(msg(reply(), 'other_topic'))
    expect(s.policy).toBeNull()
    s.apply(msg('garbage'))
    expect(s.policy).toBeNull()
    s.apply(msg(null))
    expect(s.policy).toBeNull()
  })

  it('ok:false сохраняет файловые поля', () => {
    const s = useExitsStore()
    s.apply(msg(reply({
      ok: false,
      db: 'NO_DB',
      version: null,
      effective: null,
      snapshot: null,
      lease: null,
      switch: { apply: false, source: 'broken', ts: null, actor: null },
      promotion: { mode: 'off' },
      trials: { open: 0, closed: 0, rows: [] },
      config: { errors: ['promotion.mode'] },
    })))
    expect(s.policy?.ok).toBe(false)
    expect(s.policy?.switch?.apply).toBe(false)
    expect(s.policy?.promotion?.mode).toBe('off')
    expect(s.policy?.config.errors).toEqual(['promotion.mode'])
  })

  it('request вручную шлёт ту же операцию', () => {
    h.setLink('open')
    const s = useExitsStore()
    s.start()
    h.sent.length = 0
    expect(s.request()).toBe(true)
    expect(h.sent).toEqual([{ op: 'exits_status' }])
  })
})
