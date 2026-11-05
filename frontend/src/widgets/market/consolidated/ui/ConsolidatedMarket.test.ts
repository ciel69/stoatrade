import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import type { LinkState, LiveMessage } from '@/shared/api'

/** Фейковый сокет: тема приходит сообщением, ответ по монете — тоже. К платформе не подключаемся. */
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

import ConsolidatedMarket from './ConsolidatedMarket.vue'

const msg = (topic: string, data: unknown): LiveMessage => ({ topic, seq: 0, ts: 0, data })
const sendTopic = (topic: string, data: unknown): void => {
  const handler = h.handlers.get(topic)
  if (handler) handler(msg(topic, data) as never)
}

const snapshot = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  ok: true,
  state: 'ok',
  ts: Date.now() / 1000,
  age_s: 0.2,
  n_ex: { perp: 5, spot: 5 },
  n_syms: 2,
  skew_ms: 120,
  warn: [],
  venues: [],
  coins: [
    { sym: 'BTC', tier: 'deep', focus_s: 4000, cov: 0.94, mid: 65000, flags: [], n_ex: 6 },
    { sym: 'SOL', tier: 'light', focus_s: 800, cov: 0.71, mid: 150, flags: ['warmup'], n_ex: 4 },
  ],
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

const ladder = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  sym: 'BTC',
  ts: Date.now() / 1000,
  kinds: {
    perp: {
      mid: 65000,
      best_bid: 64990,
      best_ask: 65010,
      spread_pct: 0.031,
      source: 'bk',
      n_mkts: 4,
      levels: {
        ask: [{ px: 65050, pct: 0.077, musd: 12.5, cum: 12.5, mkts: ['binance_perp', 'okx_perp'] }],
        bid: [{ px: 64947, pct: 0.082, musd: 9.1, cum: 9.1, mkts: ['bybit_perp'] }],
      },
      walls: {
        ask: [
          { px: 65100, pct: 0.154, musd: 3.4, cum: 3.4, mkts: ['binance_perp', 'okx_perp'] },
          { px: 65200, pct: 0.308, musd: 1.2, cum: 4.6, mkts: ['bybit_perp'] },
        ],
        bid: [{ px: 64900, pct: 0.154, musd: 4.2, cum: 4.2, mkts: ['binance_perp'] }],
      },
      markets: { binance_perp: { code: 'OK', lag_ms: 120, mid: 65000 } },
      ...over,
    },
  },
})

const detail = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  ok: true,
  sym: 'BTC',
  ts: Date.now() / 1000,
  age_s: 0.2,
  n_ex: 6,
  cov: 0.94,
  warn: [],
  oi: { musd: 11140, chg_5m_pct: 0.123 },
  funding: { w_pct: 0.00123, prem_ps: 1.4 },
  cvd: { '5m': { all: { pct: 10, musd: 3.4 } } },
  liq: { '5m': [1.2, 0.8] },
  venues: { binance_perp: { code: 'OK', lag_ms: 120 }, bybit_perp: { code: 'LAG', lag_ms: 1450 } },
  ladder: ladder(),
  ...over,
})

let wrapper: VueWrapper | null = null

beforeEach(() => {
  setActivePinia(createPinia())
  h.sent.length = 0
  h.handlers.clear()
  h.states.clear()
  h.link = 'open'
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

const mountWidget = async (selected: string | null = null): Promise<VueWrapper> => {
  wrapper = mount(ConsolidatedMarket, { props: { selected } })
  await nextTick()
  return wrapper
}

describe('Сводный стакан', () => {
  it('свежий снимок: полоса, выбор монеты, стакан с уровнями и факты по монете', async () => {
    const w = await mountWidget()
    sendTopic('cmkt', snapshot())
    await nextTick()

    const text = w.text()
    expect(text).toContain('Сводный стакан')
    expect(text).toContain('5 перп · 5 спот')
    expect(text).toContain('2 монет в фокусе')
    expect(text).toContain('Выберите монету')

    await w.setProps({ selected: 'BTC' })
    await nextTick()
    expect(h.sent).toEqual([{ op: 'cmkt_sym', sym: 'BTC' }])
    sendTopic('cmkt_sym_detail', detail())
    await nextTick()

    const withCoin = w.text()
    expect(withCoin).toContain('Стакан')
    expect(withCoin).toContain('Перп')
    expect(withCoin).toContain('3.4M')
    expect(withCoin).toContain('4.2M')
    expect(withCoin).toContain('2 биржи')
    expect(withCoin).toContain('середина')
    expect(withCoin).toContain('0.031%')
    expect(withCoin).toContain('Открытый интерес')
    expect(withCoin).toContain('11.14B')
    expect(withCoin).toContain('Binance · перп')
    expect(withCoin).toContain('норма')
    expect(withCoin).not.toMatch(/NOT_FOCUS|LOW_COV|binance_perp|undefined|NaN|null/)
    expect(withCoin).not.toMatch(/\p{Extended_Pictographic}/u)
  })

  it('устаревший снимок: причина словами, чисел и стакана нет', async () => {
    const w = await mountWidget()
    sendTopic('cmkt', stale())
    await nextTick()

    const text = w.text()
    expect(text).toContain('Снимок сводного рынка устарел')
    expect(text).not.toContain('5 перп')
    expect(w.find('[aria-label="Сводный стакан"]').exists()).toBe(false)
  })

  it('монета вне фокуса: причина словами, стакана нет', async () => {
    const w = await mountWidget()
    sendTopic('cmkt', snapshot())
    await w.setProps({ selected: 'BTC' })
    sendTopic('cmkt_sym_detail', { ok: false, sym: 'BTC', warn: ['NOT_FOCUS'] })
    await nextTick()

    const text = w.text()
    expect(text).toContain('вне фокуса')
    expect(text).toContain('Монета вне фокуса')
    expect(text).not.toContain('3.4M')
  })

  it('LOW_COV: ok=false, но данные есть — показываем стакан и слово «мало бирж»', async () => {
    const w = await mountWidget()
    sendTopic('cmkt', snapshot())
    await w.setProps({ selected: 'BTC' })
    sendTopic('cmkt_sym_detail', detail({ ok: false, warn: ['LOW_COV'] }))
    await nextTick()

    const text = w.text()
    expect(text).toContain('мало бирж')
    expect(text).toContain('3.4M')
  })

  it('чужая монета не подменяет выбранную', async () => {
    const w = await mountWidget()
    sendTopic('cmkt', snapshot())
    await w.setProps({ selected: 'BTC' })
    sendTopic('cmkt_sym_detail', detail({ sym: 'SOL' }))
    await nextTick()
    expect(w.text()).not.toContain('3.4M')
  })
})
