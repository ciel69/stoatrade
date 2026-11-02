import { describe, expect, it } from 'vitest'
import { mockStatus } from './mock'
import { buildRows, coarse } from './rows'
import { summarize, worstLevel } from './summary'
import { freshLevel, THRESHOLDS } from './thresholds'
import type { StatusInput, StatusRow } from './types'

const NOW = 1_000_000
const empty = (o: Partial<StatusInput> = {}): StatusInput => ({ now: NOW, link: 'open', home: null, system: null, brain: null, market: null, news: null, ctl: null, ...o })
const r = (o: Partial<StatusRow> & Pick<StatusRow, 'id' | 'level'>): StatusRow => ({ group: 'platform', label: o.id, value: 'v', note: null, brief: null, stops: false, raw: null, human: null, ...o })
const sum = (i: StatusInput) => summarize(buildRows(i), { mode: i.ctl?.platform.mode ?? null })

describe('агрегация', () => {
  it('худший уровень: bad > warn > ok > idle, пусто = idle', () => {
    expect(worstLevel(['ok', 'warn', 'ok'])).toBe('warn')
    expect(worstLevel(['warn', 'bad', 'idle'])).toBe('bad')
    expect(worstLevel(['idle', 'ok'])).toBe('ok')
    expect(worstLevel([])).toBe('idle')
  })
  it('нет данных: только связь в норме -> «ждём данные», а не ложное «всё в порядке»', () => {
    const s = sum(empty())
    expect(s.level).toBe('idle')
    expect(s.text).toBe('Ждём данные платформы')
    expect(s.problems).toBe(0)
  })
  it('нет связи: красный и текст про связь, остальные данные не считаются свежими', () => {
    const s = sum({ ...mockStatus('mock', NOW), link: 'closed' })
    expect(s.level).toBe('bad')
    expect(s.text).toBe('Нет связи с платформой')
    expect(s.problems).toBe(1)
    const others = s.groups.flatMap((g) => g.rows).filter((x) => x.id !== 'link')
    expect(others.every((x) => x.level !== 'bad' && x.level !== 'warn')).toBe(true)
  })
  it('подключение: жёлтый', () => {
    const s = sum({ ...empty(), link: 'connecting' })
    expect(s.level).toBe('warn')
    expect(s.text).toBe('Торговля идёт: подключаемся к платформе…')
  })
  it('образец «в норме»: зелёный и спокойный текст', () => {
    const s = sum(mockStatus('mock', NOW))
    expect(s.level).toBe('ok')
    expect(s.text).toBe('Всё в порядке')
    expect(s.extra).toBe(0)
    expect(s.headline.detail).toContain('без присмотра')
  })
  it('несколько проблем: цвет и текст по самой серьёзной, остальные считаются в extra', () => {
    const s = sum(mockStatus('mock-bad', NOW))
    expect(s.level).toBe('bad')
    expect(s.problems).toBeGreaterThan(1)
    expect(s.extra).toBe(s.problems - 1)
    expect(s.groups[0]?.level).toBe('bad')
    expect(s.groups[0]?.rows[0]?.level).toBe('bad')
  })
  it('одинаковый уровень: названа проблема из группы выше по порядку; одна проблема называется в тексте, несколько дают общую фразу', () => {
    const one = summarize([r({ id: 'm', group: 'market', level: 'warn', brief: 'Рынок отстаёт' })])
    expect(one.text).toBe('Торговля идёт: рынок отстаёт')
    const s = summarize([
      r({ id: 'm', group: 'market', level: 'warn', brief: 'Рынок' }),
      r({ id: 'e1', group: 'exec', level: 'warn', brief: 'Исполнитель 1' }),
      r({ id: 'e2', group: 'exec', level: 'warn', brief: 'Исполнитель 2' }),
    ])
    expect(s.text).toBe('Торговля идёт, есть что проверить')
    expect(s.extra).toBe(2)
    expect(s.headline.detail).toContain('Замечаний: 3')
    expect(s.key.map((x) => x.id)).toContain('e1')
  })
  it('красный перебивает жёлтый независимо от порядка; остановка торговли называется прямо', () => {
    const s = summarize([r({ id: 'a', group: 'platform', level: 'warn', brief: 'A' }), r({ id: 'b', group: 'news', level: 'bad', brief: 'Включена аварийная остановка', stops: true })])
    expect(s.level).toBe('bad')
    expect(s.text).toBe('Торговля остановлена: включена аварийная остановка')
  })
  it('красный без остановки торговли: «Нужно внимание»', () => {
    const s = summarize([r({ id: 'b', group: 'brain', level: 'bad', brief: 'Не отвечает мозг' })])
    expect(s.text).toBe('Нужно внимание: не отвечает мозг')
  })
  it('платформа остановлена без проблем: нейтральный текст', () => {
    const base = mockStatus('mock', NOW)
    const s = sum({ ...base, ctl: { ...base.ctl!, platform: { ...base.ctl!.platform, state: 'stopped' } } })
    expect(s.level).toBe('idle')
    expect(s.text).toBe('Платформа остановлена')
  })
  it('платформа остановлена: «исполнитель не запущен» и «мозг не отвечает» не проблема', () => {
    const base = mockStatus('mock-bad', NOW)
    const rows = buildRows({ ...base, ctl: { ...base.ctl!, platform: { ...base.ctl!.platform, state: 'stopped' } } })
    expect(rows.find((x) => x.id === 'brain')?.level).toBe('idle')
    expect(rows.find((x) => x.id === 'node:net')?.level).toBe('idle')
  })
  it('мозг в режиме «только защита»: жёлтый, торговля идёт', () => {
    const base = mockStatus('mock', NOW)
    const rows = buildRows({ ...base, brain: { ...base.brain!, status: 'protect', protect_reason: 'лимит' } })
    expect(rows.find((x) => x.id === 'brain')?.level).toBe('warn')
  })
  it('строки без данных не выдумываются', () => {
    expect(buildRows(empty()).map((x) => x.id)).toEqual(['link'])
  })
  it('наверху окна от 3 до 6 строк, остальное в группах', () => {
    const s = sum(mockStatus('mock', NOW))
    expect(s.key.length).toBeGreaterThanOrEqual(3)
    expect(s.key.length).toBeLessThanOrEqual(6)
    expect(s.groups.flatMap((g) => g.rows).length).toBeGreaterThan(s.key.length)
  })
})

describe('свежесть', () => {
  it('порог: до порога ok, до 5 порогов warn, дальше bad, без возраста idle', () => {
    expect(freshLevel(120, 120)).toBe('ok')
    expect(freshLevel(121, 120)).toBe('warn')
    expect(freshLevel(120 * THRESHOLDS.badFactor, 120)).toBe('warn')
    expect(freshLevel(120 * THRESHOLDS.badFactor + 1, 120)).toBe('bad')
    expect(freshLevel(null, 120)).toBe('idle')
    expect(freshLevel(Number.NaN, 120)).toBe('idle')
  })
  it('карта рынка: возраст по моменту at и порог из MarketMap', () => {
    const base = mockStatus('mock', NOW)
    const at = (age: number) => buildRows({ ...base, market: { ...base.market!, heat: { ...base.market!.heat, at: NOW - age } } }).find((x) => x.id === 'heat')
    expect(at(THRESHOLDS.heatS)?.level).toBe('ok')
    expect(at(THRESHOLDS.heatS + 1)?.level).toBe('warn')
    expect(at(THRESHOLDS.heatS + 1)?.label).toContain('Карта рынка')
  })
  it('новости: тишина дольше порога даёт жёлтый и текст с возрастом', () => {
    const base = mockStatus('mock', NOW)
    const news = (age: number) => buildRows({ ...base, news: { ...base.news!, items: [{ ...base.news!.items[0]!, ts: NOW - age }] } }).find((x) => x.id === 'news-fresh')
    expect(news(300)?.level).toBe('ok')
    expect(news(THRESHOLDS.newsQuietS + 1)?.level).toBe('warn')
    expect(news(720 + THRESHOLDS.newsQuietS)?.note).toContain('2 ч 12 мин')
  })
  it('ленты новостей: часть упала жёлтый, все упали тоже жёлтый (торговлю не останавливает)', () => {
    const base = mockStatus('mock', NOW)
    const src = (oks: boolean[]) => buildRows({ ...base, news: { ...base.news!, sources: oks.map((ok, i) => ({ id: `s${i}`, ok, fails: 0, verified: true, degraded: false })) } }).find((x) => x.id === 'news-sources')
    expect(src([true, true])?.level).toBe('ok')
    expect(src([true, false])?.level).toBe('warn')
    expect(src([false, false])?.level).toBe('warn')
  })
  it('короткий возраст не мерцает: секунды округляются вниз до 10', () => {
    expect(coarse(3)).toBe('10 с')
    expect(coarse(47)).toBe('40 с')
    expect(coarse(725)).toBe('12 мин')
  })
})
