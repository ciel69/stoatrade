import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LiveSocket, defaultSocketUrl, type LinkState } from './live-socket'

class FakeWS {
  static all: FakeWS[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(public url: string) {
    FakeWS.all.push(this)
  }
  close() {
    this.closed = true
    this.onclose?.()
  }
  open() {
    this.onopen?.()
  }
  push(topic: string, data: unknown, seq = 1) {
    this.onmessage?.({ data: JSON.stringify({ topic, seq, ts: 1, data }) })
  }
}

const make = () =>
  new LiveSocket({ url: 'ws://x/ws', factory: (u) => new FakeWS(u) as unknown as WebSocket, random: () => 0.5, silenceMs: 4000 })

describe('LiveSocket', () => {
  beforeEach(() => {
    FakeWS.all = []
    vi.useFakeTimers()
  })
  afterEach(() => vi.useRealTimers())

  it('доставляет сообщения подписчикам темы и меняет состояние', () => {
    const s = make()
    const states: LinkState[] = []
    s.onState((x) => states.push(x))
    const got: unknown[] = []
    s.subscribe('brain', (m) => got.push(m.data))
    s.start()
    FakeWS.all[0].open()
    FakeWS.all[0].push('brain', { a: 1 })
    FakeWS.all[0].push('other', { b: 2 })
    expect(got).toEqual([{ a: 1 }])
    expect(states).toEqual(['closed', 'connecting', 'open'])
  })

  it('переподключается с нарастающей паузой и обнуляет её после успешного открытия', () => {
    const s = make()
    s.start()
    FakeWS.all[0].close()
    expect(FakeWS.all).toHaveLength(1)
    vi.advanceTimersByTime(999)
    expect(FakeWS.all).toHaveLength(1)
    vi.advanceTimersByTime(2)
    expect(FakeWS.all).toHaveLength(2)
    FakeWS.all[1].close()
    vi.advanceTimersByTime(1999)
    expect(FakeWS.all).toHaveLength(2)
    vi.advanceTimersByTime(2)
    expect(FakeWS.all).toHaveLength(3)
    FakeWS.all[2].open()
    FakeWS.all[2].close()
    vi.advanceTimersByTime(1001)
    expect(FakeWS.all).toHaveLength(4)
  })

  it('тишина дольше порога считается обрывом', () => {
    const s = make()
    s.start()
    FakeWS.all[0].open()
    vi.advanceTimersByTime(5100)
    expect(FakeWS.all[0].closed).toBe(true)
  })

  it('сообщение сбрасывает счётчик тишины, мусор игнорируется', () => {
    const s = make()
    s.start()
    const ws = FakeWS.all[0]
    ws.open()
    vi.advanceTimersByTime(3000)
    ws.push('link', {})
    ws.onmessage?.({ data: 'не json' })
    vi.advanceTimersByTime(3000)
    expect(ws.closed).toBe(false)
  })

  it('stop() не переподключается', () => {
    const s = make()
    s.start()
    s.stop()
    vi.advanceTimersByTime(60000)
    expect(FakeWS.all).toHaveLength(1)
    expect(s.state).toBe('closed')
  })

  it('адрес сокета от хоста страницы', () => {
    expect(defaultSocketUrl({ protocol: 'http:', host: '127.0.0.1:8767' })).toBe('ws://127.0.0.1:8767/ws')
    expect(defaultSocketUrl({ protocol: 'https:', host: 'h' })).toBe('wss://h/ws')
  })
})
