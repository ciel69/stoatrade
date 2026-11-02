import { describe, expect, it } from 'vitest'
import { mockDay } from '@/entities/day'
import { mockStatus } from './mock'
import { buildRows } from './rows'
import { summarize } from './summary'

const NOW = 1_800_000_000

describe('индикатор платформы: торговый день ждёт ответа', () => {
  it('без вопросов строки нет', () => {
    const rows = buildRows({ ...mockStatus('mock', NOW), day: mockDay('mock-empty', NOW) })
    expect(rows.find((r) => r.id === 'day-wait')).toBeUndefined()
  })

  it('с вопросом о переносе есть строка со ссылкой на страницу «День», заголовок без «Торговля идёт»', () => {
    const rows = buildRows({ ...mockStatus('mock', NOW), day: mockDay('mock', NOW) })
    const r = rows.find((x) => x.id === 'day-wait')!
    expect(r.level).toBe('warn')
    expect(r.href).toBe('/trading/day')
    expect(r.note).toMatch(/вопрос о переносе/)
    const s = summarize(rows.filter((x) => x.id === 'day-wait'))
    expect(s.text).toBe('Ждёт вашего ответа')
    expect(s.link?.to).toBe('/trading/day')
  })
})
