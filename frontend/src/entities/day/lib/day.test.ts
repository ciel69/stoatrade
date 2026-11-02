import { describe, expect, it } from 'vitest'
import { abortView, checkViews, closeFailedView, dayFacts, dayHeadline, dayNeeds, dayRows, dayWaitText, questionViews, stepRows } from './day'
import { mockDay } from './mock'

const NOW = 1_800_000_000

describe('entities/day', () => {
  it('нужно ваше решение: вопросы пока день не завершён, возобновление, непрочитанный итог', () => {
    expect(dayNeeds(null).count).toBe(0)
    const s = mockDay('mock', NOW)
    const n = dayNeeds(s)
    expect(n.questions.map((q) => q.plan)).toEqual(['PL-20261102-07'])
    expect(n.deadline).toBe(NOW + 38 * 60)
    expect(n.count).toBe(1)
    expect(dayNeeds(mockDay('mock-resume', NOW)).resume).toBe(true)
    const done = dayNeeds(mockDay('mock-done', NOW))
    expect(done.report).toBe('D-20261102-1')
    expect(done.questions).toEqual([])
    expect(dayNeeds(mockDay('mock-empty', NOW)).count).toBe(0)
    const stopped = { ...s, current: { ...s.current!, state: 'stopped' } }
    expect(dayNeeds(stopped).questions).toEqual([])
  })

  it('строка-итог по состояниям', () => {
    expect(dayHeadline(null, NOW).tone).toBe('idle')
    expect(dayHeadline(mockDay('mock-empty', NOW), NOW).text).toMatch(/ещё не запускался/)
    expect(dayHeadline(mockDay('mock-resume', NOW), NOW).text).toBe('Платформа перезапущена, торговля выключена')
    const q = dayHeadline(mockDay('mock', NOW), NOW)
    expect(q.tone).toBe('warn')
    expect(q.text).toMatch(/^Нужен ваш ответ до \d\d:\d\d$/)
    expect(dayHeadline(mockDay('mock', NOW), NOW + 3600).text).toBe('Срок ответа вышел, позиции закрываются')
    expect(dayHeadline(mockDay('mock-done', NOW), NOW).text).toMatch(/^Итог дня .* готов: \+1\.26 USDT$/)
    expect(dayHeadline(mockDay('mock-abort', NOW), NOW).text).toMatch(/Старт дня .* отклонён/)
    const s = mockDay('mock-resume', NOW)
    const run = { ...s, banner: null, current: { ...s.current!, needs_resume: false } }
    expect(dayHeadline(run, NOW).text).toMatch(/^Сейчас идёт торговый день .* \(бумажная торговля\): всё в порядке$/)
    expect(dayHeadline(run, NOW).tone).toBe('ok')
  })

  it('закрытие не подтверждено перекрывает остальное и называет причину без кода', () => {
    const s = mockDay('mock-resume', NOW)
    const c = { ...s.current!, needs_resume: false, state: 'closing', closing: { ...s.current!.closing, failed: { items: ['pos:PL-9'], manual: [], reason: 'exec_down' } } }
    const v = closeFailedView(c)!
    expect(v.plans).toEqual(['PL-9'])
    expect(v.reason).toBe('исполнитель не отвечает')
    expect(dayHeadline({ ...s, banner: null, current: c }, NOW).tone).toBe('bad')
  })

  it('отказ старта: проверка объясняется человеческим языком, без кодов', () => {
    const c = mockDay('mock-abort', NOW).current!
    const v = abortView(c.abort!)
    expect(v.title).toBe('Старт отклонён на шаге «Проверки перед запуском»')
    expect(v.reason).not.toMatch(/E_GATE|\bH\d\b/)
    expect(v.reason.length).toBeGreaterThan(10)
    expect(abortView({ step: null, code: 'E_WTF', why: 'Plan_B failed', blocking: [] }).reason).toBe('шаг не завершился')
  })

  it('шаги: фазы, замечание и длительность', () => {
    const c = mockDay('mock-done', NOW).current!
    const st = stepRows(c, 'start')
    expect(st.map((r) => r.name)).toEqual(['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9'])
    expect(st[1].state).toBe('amber')
    expect(st[1].note).toMatch(/^[А-Я]/)
    expect(st[0].dur).toBe('2 с')
    const stop = stepRows(c, 'stop')
    expect(stop).toHaveLength(8)
    expect(stop[3].state).toBe('amber')
    expect(stop[3].note).toMatch(/мозг не ответил/i)
    for (const r of [...st, ...stop]) expect(`${r.label} ${r.does} ${r.status} ${r.note ?? ''}`).not.toMatch(/\b[SC]\d\b|E_[A-Z]/)
    const run = stepRows(mockDay('mock', NOW).current!, 'stop').at(-1)!
    expect(run.state).toBe('run')
    expect(run.status).toBe('выполняется')
  })

  it('факты, вопросы, чек-лист и список дней', () => {
    const c = mockDay('mock', NOW).current!
    const f = dayFacts(c)
    expect(f.map((x) => x.label)).toEqual(['Итог дня', 'План дня', 'Со вчера'])
    expect(f[1].text).toBe('боковик, без уклона; новых планов до 4; составил мозг')
    const qv = questionViews(c, dayNeeds(mockDay('mock', NOW)).questions)
    expect(qv[0].title).toBe('ETHUSDT лонг')
    const ch = checkViews(c.checklist, c)
    expect(ch[1].why).toMatch(/риск до стопа вырос/i)
    expect(ch[0].decision).toBe('ждёт вашего решения')
    expect(ch[3].title).toBe('Торговая сессия')
    const rows = dayRows(mockDay('mock-done', NOW).days, 'D-20261102-1')
    expect(rows[0].unread).toBe(true)
    expect(rows.find((r) => r.id === 'D-20261101-1')?.hasReport).toBe(false)
    expect(rows.find((r) => r.id === 'D-20261031-1')?.netTone).toBe('neg')
  })

  it('текст «ждёт вашего ответа» для индикатора', () => {
    expect(dayWaitText(dayNeeds(mockDay('mock-empty', NOW)))).toBeNull()
    expect(dayWaitText(dayNeeds(mockDay('mock', NOW)))).toBe('вопрос о переносе позиции')
    expect(dayWaitText(dayNeeds(mockDay('mock-done', NOW)))).toBe('итог дня готов')
    expect(dayWaitText(dayNeeds(mockDay('mock-resume', NOW)))).toBe('возобновление дня')
  })
})
