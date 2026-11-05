import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { LinkState, LiveMessage } from '@/shared/api'

/** Фейковый сокет: запоминает отправленное, подписки и состояние связи. В платформу ничего не уходит. */
const h = vi.hoisted(() => ({
  sent: [] as Array<Record<string, unknown>>,
  handlers: new Map<string, (m: never) => void>(),
  states: new Set<(s: never) => void>(),
  link: 'open' as LinkState,
}))

vi.mock('@/shared/api', () => ({
  useLive: () => ({
    socket: {
      send: (p: Record<string, unknown>) => {
        if (h.link !== 'open') return false
        h.sent.push(p)
        return true
      },
      subscribe: (topic: string, handler: (m: LiveMessage) => void) => {
        h.handlers.set(topic, handler as (m: never) => void)
        return () => h.handlers.delete(topic)
      },
      onState: (handler: (s: LinkState) => void) => {
        h.states.add(handler as (s: never) => void)
        handler(h.link)
        return () => h.states.delete(handler as (s: never) => void)
      },
    },
  }),
}))

import { useCmktStore } from './store'

const msg = (topic: string, data: unknown): LiveMessage => ({ topic, seq: 0, ts: 0, data })
const answer = (topic: string, data: unknown): void => {
  const handler = h.handlers.get(topic)
  if (handler) handler(msg(topic, data) as never)
}
const setLink = (state: LinkState): void => {
  h.link = state
  for (const fn of [...h.states]) fn(state as never)
}

const fresh = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  ok: true,
  state: 'ok',
  ts: Date.now() / 1000,
  age_s: 0.2,
  n_ex: { perp: 5, spot: 5 },
  n_syms: 2,
  skew_ms: 120,
  warn: [],
  venues: [{ mkt: 'binance_perp', code: 'OK', lag_ms: 120, age_s: 0.4 }],
  coins: [{ sym: 'BTC', tier: 'deep', focus_s: 4000, cov: 0.94, mid: 65000, flags: [], n_ex: 6 }],
  ...over,
})

const stale = (): Record<string, unknown> => ({
  ok: false,
  state: 'stale',
  ts: null,
  age_s: null,
  n_ex: { perp: 0, spot: 0 },
  n_syms: 0,
  skew_ms: null,
  warn: ['STALE'],
  venues: [],
  coins: [],
})

const detail = (sym: string, over: Record<string, unknown> = {}): Record<string, unknown> => ({
  ok: true,
  sym,
  ts: Date.now() / 1000,
  age_s: 0.2,
  n_ex: 6,
  cov: 0.94,
  warn: [],
  book: { bands: { all: { '05': { bid: 13.5, ask: 11.1, imb: 9.7 } } }, walls: { bid: [{ musd: 4.2, dist_pct: 0.42, n_ex: 3, age_s: 120 }] } },
  ...over,
})

beforeEach(() => {
  setActivePinia(createPinia())
  h.sent.length = 0
  h.handlers.clear()
  h.states.clear()
  h.link = 'open'
})

describe('хранилище сводного рынка', () => {
  it('start подписывает тему снимка и ответы по монете', () => {
    const s = useCmktStore()
    s.start()
    expect(h.handlers.has('cmkt')).toBe(true)
    expect(h.handlers.has('cmkt_sym_detail')).toBe(true)
    expect(s.link).toBe('open')
  })

  it('select отправляет cmkt_sym, запоминает монету и ждёт ответ', () => {
    const s = useCmktStore()
    s.start()
    expect(s.select(' btc ')).toBe(true)
    expect(h.sent).toEqual([{ op: 'cmkt_sym', sym: 'BTC' }])
    expect(s.selected).toBe('BTC')
    expect(s.loading).toBe(true)
    answer('cmkt_sym_detail', detail('BTC'))
    expect(s.loading).toBe(false)
    expect(s.detail?.sym).toBe('BTC')
  })

  it('повторный select той же монеты в полёте не дублирует запрос', () => {
    const s = useCmktStore()
    s.start()
    s.select('BTC')
    s.select('BTC')
    expect(h.sent).toHaveLength(1)
    s.select('ETH')
    expect(h.sent.map((m) => m.sym)).toEqual(['BTC', 'ETH'])
  })

  it('ответ по чужой монете отбрасывается и не снимает ожидание', () => {
    const s = useCmktStore()
    s.start()
    s.select('BTC')
    answer('cmkt_sym_detail', detail('SOL'))
    expect(s.detail).toBeNull()
    expect(s.loading).toBe(true)
    answer('cmkt_sym_detail', detail('BTC'))
    expect(s.detail?.sym).toBe('BTC')
    expect(s.loading).toBe(false)
  })

  it('возраст считается по ts, а не по серверному age_s', () => {
    const s = useCmktStore()
    s.start()
    const now = Date.now() / 1000
    answer('cmkt', fresh({ ts: now - 20, age_s: 0.1 }))
    expect(s.snapshot?.ok).toBe(true)
    expect(s.ageAt(now)).toBe(20)
    s.select('BTC')
    answer('cmkt_sym_detail', detail('BTC'))
    expect(s.detailAgeAt(now + 2)).not.toBeNull()
  })

  it('устаревший снимок заменяет прежний и не оставляет чисел', () => {
    const s = useCmktStore()
    s.start()
    answer('cmkt', fresh())
    expect(s.stale).toBe(false)
    answer('cmkt', stale())
    expect(s.snapshot?.ok).toBe(false)
    expect(s.snapshot?.coins).toEqual([])
    expect(s.snapshot?.venues).toEqual([])
    expect(s.stale).toBe(true)
    expect(s.ageAt(Date.now() / 1000)).toBeNull()
  })

  it('свежий снимок после устаревшего перезапрашивает устаревшую карточку монеты', () => {
    const s = useCmktStore()
    s.start()
    answer('cmkt', stale())
    s.select('BTC')
    answer('cmkt_sym_detail', detail('BTC', { warn: ['STALE'], book: undefined }))
    expect(h.sent).toHaveLength(1)
    answer('cmkt', fresh())
    expect(h.sent.map((m) => m.sym)).toEqual(['BTC', 'BTC'])
    expect(s.loading).toBe(true)
  })

  it('обрыв связи снимает ожидание, а открытие заново просит выбранную монету', () => {
    const s = useCmktStore()
    s.start()
    s.select('BTC')
    expect(h.sent).toHaveLength(1)
    setLink('closed')
    expect(s.loading).toBe(false)
    setLink('open')
    expect(h.sent.map((m) => m.sym)).toEqual(['BTC', 'BTC'])
    expect(s.loading).toBe(true)
  })

  it('мусор в сообщениях не ломает состояние', () => {
    const s = useCmktStore()
    s.start()
    answer('cmkt', null)
    answer('cmkt', 'x')
    answer('cmkt_sym_detail', 7)
    expect(s.snapshot).toBeNull()
    expect(s.detail).toBeNull()
  })
})
