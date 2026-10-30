import { describe, expect, it } from 'vitest'
import type { DecisionRow } from '../model/types'
import { FADE_S, importance, pickThoughts } from './thoughts'

let n = 0
const row = (o: Partial<DecisionRow>): DecisionRow => ({
  seq: ++n, t: 1000, ts: '', what: 'brain_decision', kind: 'note', role: 'entry_scout', profile: 'lm-oss', plan: null, sym: 'SOL', decision: null, tool: null, reason: null,
  code: 'SKIP', ok: true, text: 'мысль ' + n, ...o,
})

describe('thoughts', () => {
  it('шум машинерии и пустые тексты не мысли', () => {
    expect(importance(row({ code: 'BAD_FORMAT' }))).toBe(0)
    expect(importance(row({ code: 'PROTECT_ONLY' }))).toBe(0)
    expect(importance(row({ code: 'SYM_BUSY', ok: false }))).toBe(0)        // отказ по правилу досье, не рассуждение модели
    expect(importance(row({ text: '  ' }))).toBe(0)
    expect(importance(row({ text: null }))).toBe(0)
  })

  it('объяснение действия важнее пропуска входа, «держу» слабее всего', () => {
    expect(importance(row({ what: 'brain_why' }))).toBeGreaterThan(importance(row({})))
    expect(importance(row({ decision: 'close' }))).toBeGreaterThan(importance(row({ decision: 'hold' })))
  })

  it('не больше трёх, самые ценные, от новой к старой; старше FADE_S исчезают', () => {
    const now = 1100
    const rows = [
      row({ t: 1095, decision: 'hold', text: 'держу' }),
      row({ t: 1090, what: 'brain_why', text: 'стоп подтянут' }),
      row({ t: 1085, text: 'пропуск SOL' }),
      row({ t: 1080, text: 'пропуск ETH' }),
      row({ t: 1070, decision: 'close', text: 'закрыл по инвалидации' }),
      row({ t: 1000 - FADE_S - 5, what: 'brain_why', text: 'давно' }),
    ]
    const out = pickThoughts(rows, now, 3).map((t) => t.row.text)
    expect(out).toHaveLength(3)
    expect(out).toEqual(['стоп подтянут', 'пропуск SOL', 'закрыл по инвалидации'].sort((a, b) => rows.find((r) => r.text === b)!.t - rows.find((r) => r.text === a)!.t))
    expect(out).not.toContain('давно')
    expect(out).not.toContain('держу')
  })

  it('повтор текста не показывается дважды; через FADE_S список пустеет', () => {
    const rows = [row({ t: 1090, text: 'то же' }), row({ t: 1095, text: 'то же' })]
    expect(pickThoughts(rows, 1100)).toHaveLength(1)
    expect(pickThoughts(rows, 1100 + FADE_S)).toEqual([])
  })
})
