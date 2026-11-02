import { describe, expect, it } from 'vitest'
import { blackoutView, downSources, eventViews, newsVerdict, readerView, sourceViews } from './status'
import { isNewsMock, mockNews } from './mock'

const NOW = 1_800_000_000

describe('вывод о новостях одной фразой', () => {
  it('всё нормально: источники, читатель и ближайшее событие', () => {
    const v = newsVerdict(mockNews('mock', NOW), NOW)
    expect(v.tone).toBe('ok')
    expect(v.text).toBe('Новости идут нормально: 4 источника работают, читатель размечает; ближайшее событие: CPI через 2 ч')
  })
  it('источник не отвечает: называем его и говорим про остальных', () => {
    const v = newsVerdict(mockNews('mock-source-down', NOW), NOW)
    expect(v.tone).toBe('warn')
    expect(v.text).toBe('Источник theblock не отвечает, остальные работают')
  })
  it('несколько источников и все источники', () => {
    const s = mockNews('mock', NOW)
    const two = { ...s, sources: s.sources.map((x, i) => (i < 2 ? { ...x, ok: false } : x)) }
    expect(newsVerdict(two, NOW).text).toBe('Не отвечают 2 из 4 источников: coindesk, theblock, остальные работают')
    const all = { ...s, sources: s.sources.map((x) => ({ ...x, degraded: true })) }
    expect(newsVerdict(all, NOW).text).toBe('Источники новостей не отвечают')
  })
  it('читатель выключен: справка, не тревога', () => {
    const v = newsVerdict(mockNews('mock-reader-off', NOW), NOW)
    expect(v.tone).toBe('idle')
    expect(v.text).toMatch(/^Читатель выключен: заголовки без разметки/)
    expect(v.text).not.toMatch(/reader|config|off/)
  })
  it('лимит расходов это справка, пауза и недоступная модель это внимание', () => {
    expect(newsVerdict(mockNews('mock-limit', NOW), NOW).tone).toBe('idle')
    const s = mockNews('mock', NOW)
    expect(newsVerdict({ ...s, reader: { ...s.reader, status: 'paused' } }, NOW)).toMatchObject({ tone: 'warn' })
    expect(newsVerdict({ ...s, reader: { ...s.reader, status: 'llm_down' } }, NOW).text).toContain('модель не отвечает')
    expect(newsVerdict({ ...s, reader: { ...s.reader, stale: true } }, NOW).text).toMatch(/^Разметка устарела/)
  })
  it('окно блокировки идёт: новые входы приостановлены до времени, без выдуманного', () => {
    const v = newsVerdict(mockNews('mock-blackout', NOW), NOW)
    expect(v.tone).toBe('warn')
    expect(v.text).toMatch(/^Новые входы приостановлены до \d\d:\d\d: идёт окно вокруг события «FOMC»$/)
  })
  it('окно ещё не началось или уже закончилось', () => {
    const s = mockNews('mock', NOW)
    const soon = { ...s, calendar: { ...s.calendar, blackout: { name: 'CPI', from: NOW + 1800, to: NOW + 5400 } } }
    const b = blackoutView(soon, NOW)!
    expect(b.active).toBe(false)
    expect(b.text).toContain('начнётся через 30 мин')
    expect(newsVerdict(soon, NOW).tone).toBe('ok')
    expect(blackoutView({ ...s, calendar: { ...s.calendar, blackout: { name: 'CPI', from: NOW - 900, to: NOW - 10 } } }, NOW)).toBeNull()
    expect(blackoutView({ ...s, calendar: { ...s.calendar, blackout: {} } }, NOW)?.text).toContain('без названия')
  })
  it('нет ни источников, ни заголовков: служба не запущена', () => {
    const s = mockNews('mock', NOW)
    const v = newsVerdict({ ...s, items: [], sources: [], reader: { enabled: false, status: null, stale: false } }, NOW)
    expect(v).toEqual({ tone: 'idle', text: 'Новостей пока нет: служба новостей не запущена или ещё ничего не собрала' })
  })
  it('нет сведений об источниках, но заголовки есть: счёт источников не выдумывается', () => {
    const s = mockNews('mock', NOW)
    const v = newsVerdict({ ...s, sources: [], calendar: { next: [], blackout: null } }, NOW)
    expect(v.text).toBe('Новости идут нормально: читатель размечает')
  })
})

describe('части состояния', () => {
  it('источники: состояние, проверка, когда отвечал', () => {
    const rows = sourceViews(mockNews('mock-source-down', NOW), NOW)
    expect(rows.map((r) => r.state)).toEqual(['работает', 'не отвечает', 'работает', 'работает'])
    expect(rows[2].verified).toBe('не проверен')
    expect(rows[1].seen).toBe('ответил 1 ч назад')
    expect(downSources(mockNews('mock-source-down', NOW))).toEqual(['theblock'])
  })
  it('календарь: только будущее, влияние и проверка словами', () => {
    const ev = eventViews(mockNews('mock', NOW), NOW)
    expect(ev.map((e) => e.inText)).toEqual(['через 2 ч', 'через 26 ч'])
    expect(ev[0].impact).toBe('сильное влияние')
    expect(ev[1].verified).toBe(false)
    expect(eventViews(mockNews('mock', NOW), NOW + 3 * 3600).map((e) => e.type)).toEqual(['FOMC'])
  })
  it('читатель: расход, очередь и итог разметки; выключенный без строк', () => {
    const r = readerView(mockNews('mock-limit', NOW), NOW)
    expect(r.text).toBe('достиг лимита расходов')
    expect(r.lines.find((l) => l.label === 'Запросов за час')?.value).toBe('12 из 12')
    expect(r.lines.find((l) => l.label === 'Ждут разметки')?.value).toBe('9')
    expect(r.lines.find((l) => l.label === 'Итог разметки')?.value).toBe('размечено 120, не принято 2, подозрительных 3')
    const off = readerView(mockNews('mock-reader-off', NOW), NOW)
    expect(off.lines).toEqual([])
    expect(off.tone).toBe('idle')
  })
  it('образцы: распознаются только известные', () => {
    expect(isNewsMock('mock-blackout')).toBe(true)
    expect(isNewsMock('x')).toBe(false)
    expect(isNewsMock(null)).toBe(false)
  })
})
