import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import { useBrainStore } from './store'
import type { BrainSnapshot } from './types'

const snap = (status: BrainSnapshot['status']): BrainSnapshot => ({
  status, alive: true, protect_reason: null, health: 'green', rev: 1, inflight: 0, queue: {}, cooling: [], budget: 'ok', errors: 0, active: null, roles: [], decisions: [],
})

describe('brain store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('без данных мозг считается неработающим', () => {
    const s = useBrainStore()
    expect(s.status).toBe('down')
    expect(s.statusText).toBe('не работает')
    expect(s.decisions).toEqual([])
  })

  it('применяет снимок и изменения', () => {
    const s = useBrainStore()
    s.apply({ topic: 'brain', seq: 1, ts: 1, data: snap('idle') })
    s.apply({ topic: 'brain', seq: 2, ts: 2, data: snap('thinking') })
    expect(s.status).toBe('thinking')
    expect(s.statusText).toBe('думает')
  })

  it('устаревшее сообщение игнорируется, а снимок нового процесса (seq 1) принимается', () => {
    const s = useBrainStore()
    s.apply({ topic: 'brain', seq: 5, ts: 1, data: snap('protect') })
    s.apply({ topic: 'brain', seq: 3, ts: 2, data: snap('idle') })
    expect(s.status).toBe('protect')
    s.apply({ topic: 'brain', seq: 1, ts: 3, data: snap('idle') })
    expect(s.status).toBe('idle')
  })
})
