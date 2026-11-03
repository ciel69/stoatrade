import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
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

import { useLearningStore } from './store'
import { mockLearning } from '../lib/mock'

const msg = (data: unknown): LiveMessage => ({ topic: 'learning_detail', seq: 0, ts: 0, data })
const reply = (mode: 'paper' | 'testnet' | 'live', over: Record<string, unknown> = {}) => ({ ...mockLearning('mock', mode), ...over })
const answer = (data: unknown) => handlers.get('learning_detail')!(msg(data))

beforeEach(() => {
  setActivePinia(createPinia())
  sent.length = 0
  handlers.clear()
  linkUp = true
  history.replaceState(null, '', '/')
})

describe('хранилище «Учится ли мозг»', () => {
  it('request шлёт операцию learning с режимом и ждёт ответ', () => {
    const s = useLearningStore()
    s.start()
    expect(s.request('paper')).toBe(true)
    expect(sent).toEqual([{ op: 'learning', mode: 'paper' }])
    expect(s.pending).toBe(true)
    answer(reply('paper'))
    expect(s.pending).toBe(false)
    expect(s.detail?.mode).toBe('paper')
  })

  it('без связи request возвращает false, а запрос остаётся ожидаемым', () => {
    const s = useLearningStore()
    s.start()
    linkUp = false
    expect(s.request('paper')).toBe(false)
    expect(s.wanted).toBe('paper')
    expect(s.pending).toBe(true)
  })

  it('запоздавший ответ другого режима отбрасывается', () => {
    const s = useLearningStore()
    s.start()
    s.request('paper')
    s.request('live')
    answer(reply('paper'))
    expect(s.detail).toBeNull()
    expect(s.pending).toBe(true)
    answer(reply('live'))
    expect(s.detail?.mode).toBe('live')
    expect(s.pending).toBe(false)
  })

  it('после смены режима прежний ответ считается ожидающим, пока не пришёл нужный', () => {
    const s = useLearningStore()
    s.start()
    s.request('paper')
    answer(reply('paper'))
    s.request('testnet')
    expect(s.pending).toBe(true)
    answer(reply('paper'))
    expect(s.detail?.mode).toBe('paper')
    expect(s.pending).toBe(true)
  })

  it('повторное сообщение того же режима заменяет прежнее (готовый блок памяти)', () => {
    const s = useLearningStore()
    s.start()
    s.request('paper')
    answer(mockLearning('mock-computing', 'paper'))
    expect(s.detail?.blocks.memory.status).toBe('computing')
    answer(mockLearning('mock', 'paper'))
    expect(s.detail?.blocks.memory.status).toBe('ok')
    expect(sent).toHaveLength(1)
  })

  it('ответ без запроса и мусор игнорируются; ok:false даёт отчёт «не удалось собрать» без исключения', () => {
    const s = useLearningStore()
    s.start()
    answer(reply('paper'))
    expect(s.detail).toBeNull()
    s.request('paper')
    answer(null)
    answer('x')
    expect(s.detail).toBeNull()
    answer({ ok: false, mode: 'paper', error: 'failed' })
    expect(s.detail?.ok).toBe(false)
    expect(s.pending).toBe(false)
  })

  it('режим отладки ?learning=mock подставляет образец без сервера', () => {
    history.replaceState(null, '', '/?learning=mock-confirmed')
    const s = useLearningStore()
    s.start()
    expect(s.mocked).toBe('mock-confirmed')
    expect(s.request('paper')).toBe(true)
    expect(sent).toEqual([])
    expect(s.detail?.verdict.state).toBe('confirmed')
    answer(reply('paper'))
    expect(s.detail?.verdict.state).toBe('confirmed')
  })

  it('неизвестное значение ?learning= не включает отладку', () => {
    history.replaceState(null, '', '/?learning=что-то')
    const s = useLearningStore()
    s.start()
    expect(s.mocked).toBeNull()
  })

  it('недопустимый режим в request заменяется на paper', () => {
    const s = useLearningStore()
    s.start()
    s.request('all' as never)
    expect(sent).toEqual([{ op: 'learning', mode: 'paper' }])
  })
})
