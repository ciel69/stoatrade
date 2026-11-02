import { describe, expect, it } from 'vitest'
import { DAYS_SHOWN, visibleDays } from './visible'

const rows = (n: number) => Array.from({ length: n }, (_, i) => ({ id: `d${i}` }))

describe('visibleDays', () => {
  it('до восьми дней показывает все', () => {
    expect(visibleDays(rows(8), null, false)).toHaveLength(8)
    expect(visibleDays(rows(3), 'd2', false)).toHaveLength(3)
  })
  it('больше восьми: последние восемь', () => {
    expect(visibleDays(rows(30), null, false).map((r) => r.id)).toEqual(rows(DAYS_SHOWN).map((r) => r.id))
  })
  it('выбранный старый день остаётся на виду после последних', () => {
    const v = visibleDays(rows(30), 'd20', false).map((r) => r.id)
    expect(v).toHaveLength(9)
    expect(v[8]).toBe('d20')
  })
  it('выбранный свежий день не дублируется, неизвестный не добавляется', () => {
    expect(visibleDays(rows(30), 'd2', false)).toHaveLength(8)
    expect(visibleDays(rows(30), 'zzz', false)).toHaveLength(8)
  })
  it('раскрытый список целиком', () => {
    expect(visibleDays(rows(30), null, true)).toHaveLength(30)
  })
})
