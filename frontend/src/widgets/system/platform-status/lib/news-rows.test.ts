import { describe, expect, it } from 'vitest'
import { mockNews } from '@/entities/headline'
import { mockStatus } from './mock'
import { buildRows } from './rows'
import { summarize } from './summary'

const NOW = 1_800_000_000
const rowsOf = (kind: Parameters<typeof mockNews>[0]) => buildRows({ ...mockStatus('mock', NOW), news: mockNews(kind, NOW) }).filter((r) => r.group === 'news')
const byId = (rows: ReturnType<typeof rowsOf>, id: string) => rows.find((r) => r.id === id)

describe('строки «Новости» в индикаторе платформы', () => {
  it('всё работает: читатель и источники в норме, ближайшее событие словами', () => {
    const rows = rowsOf('mock')
    expect(byId(rows, 'news-sources')).toMatchObject({ level: 'ok', value: 'работают 4 из 4' })
    expect(byId(rows, 'reader')).toMatchObject({ level: 'ok', value: 'работает' })
    expect(byId(rows, 'calendar')?.value).toBe('CPI через 2 ч')
  })
  it('источник не отвечает: жёлтая строка с названием, не красная', () => {
    const r = byId(rowsOf('mock-source-down'), 'news-sources')!
    expect(r.level).toBe('warn')
    expect(r.label).toBe('Источник theblock не отвечает')
    expect(r.stops).toBe(false)
  })
  it('читатель выключен: справка, не ошибка; в слова без кодов', () => {
    const r = byId(rowsOf('mock-reader-off'), 'reader')!
    expect(r.level).toBe('idle')
    expect(r.value).toBe('выключен')
    expect(`${r.note} ${r.value} ${r.label}`).not.toMatch(/reader|config|enabled|\.json/)
    const s = summarize(buildRows({ ...mockStatus('mock', NOW), news: mockNews('mock-reader-off', NOW) }))
    expect(s.level).toBe('ok')
  })
  it('лимит расходов читателя: справка', () => {
    const r = byId(rowsOf('mock-limit'), 'reader')!
    expect(r.level).toBe('idle')
    expect(r.value).toBe('достиг лимита расходов')
  })
  it('пауза после ошибок и недоступная модель: жёлтый, торговлю не останавливает', () => {
    const base = mockNews('mock', NOW)
    for (const [status, brief] of [['paused', 'Читатель новостей на паузе после ошибок'], ['llm_down', 'Модель читателя новостей не отвечает']] as const) {
      const rows = buildRows({ ...mockStatus('mock', NOW), news: { ...base, reader: { ...base.reader, status } } }).filter((x) => x.id === 'reader')
      expect(rows[0]).toMatchObject({ level: 'warn', brief, stops: false })
      expect(rows[0].raw).toContain(status)
    }
  })
  it('разметка устарела: жёлтый', () => {
    const base = mockNews('mock', NOW)
    const r = buildRows({ ...mockStatus('mock', NOW), news: { ...base, reader: { ...base.reader, stale: true } } }).find((x) => x.id === 'reader')!
    expect(r.level).toBe('warn')
    expect(r.label).toBe('Разметка новостей устарела')
  })
  it('идёт окно блокировки: справка с временем окончания; будущее окно не пугает', () => {
    const c = byId(rowsOf('mock-blackout'), 'calendar')!
    expect(c.level).toBe('idle')
    expect(c.note).toMatch(/Новые входы приостановлены до \d\d:\d\d/)
    const base = mockNews('mock', NOW)
    const future = buildRows({ ...mockStatus('mock', NOW), news: { ...base, calendar: { ...base.calendar, blackout: { name: 'CPI', from: NOW + 3600, to: NOW + 7200 } } } }).find((x) => x.id === 'calendar')!
    expect(future.level).toBe('ok')
  })
})
