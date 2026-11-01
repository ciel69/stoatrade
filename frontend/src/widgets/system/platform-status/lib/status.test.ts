import { describe, expect, it } from 'vitest'
import { mockStatus } from './mock'
import { buildRows, coarse } from './rows'
import { summarize, worstLevel } from './summary'
import { freshLevel, THRESHOLDS } from './thresholds'
import type { StatusInput, StatusRow } from './types'

const NOW = 1_000_000
const empty = (o: Partial<StatusInput> = {}): StatusInput => ({ now: NOW, link: 'open', home: null, system: null, brain: null, market: null, news: null, ctl: null, ...o })
const r = (o: Partial<StatusRow> & Pick<StatusRow, 'id' | 'level'>): StatusRow => ({ group: 'platform', label: o.id, value: 'v', reason: null, brief: null, ...o })
const sum = (i: StatusInput) => summarize(buildRows(i))

describe('агрегация', () => {
  it('худший уровень: bad > warn > ok > idle, пусто = idle', () => {
    expect(worstLevel(['ok', 'warn', 'ok'])).toBe('warn')
    expect(worstLevel(['warn', 'bad', 'idle'])).toBe('bad')
    expect(worstLevel(['idle', 'ok'])).toBe('ok')
    expect(worstLevel([])).toBe('idle')
  })
  it('нет данных: только связь в норме -> idle «ждём данные», а не ложное «всё в норме»', () => {
    const s = sum(empty())
    expect(s.level).toBe('idle')
    expect(s.text).toBe('ждём данные платформы')
    expect(s.problems).toBe(0)
  })
  it('нет связи: bad и текст про связь, остальные данные не считаются свежими', () => {
    const s = sum({ ...mockStatus('mock', NOW), link: 'closed' })
    expect(s.level).toBe('bad')
    expect(s.text).toBe('нет связи с платформой')
    expect(s.problems).toBe(1)
    const others = s.groups.flatMap((g) => g.rows).filter((x) => x.id !== 'link')
    expect(others.every((x) => x.level === 'idle')).toBe(true)
  })
  it('подключение: warn', () => {
    const s = sum({ ...empty(), link: 'connecting' })
    expect(s.level).toBe('warn')
    expect(s.text).toBe('подключение к платформе…')
  })
  it('образец «в норме»: ok и нейтральный текст', () => {
    const s = sum(mockStatus('mock', NOW))
    expect(s.level).toBe('ok')
    expect(s.text).toBe('всё в норме')
    expect(s.extra).toBe(0)
  })
  it('несколько проблем: цвет и текст по самой серьёзной, остальные считаются в extra', () => {
    const s = sum(mockStatus('mock-bad', NOW))
    expect(s.level).toBe('bad')
    expect(s.problems).toBeGreaterThan(1)
    expect(s.extra).toBe(s.problems - 1)
    expect(s.groups[0]?.level).toBe('bad')                      // группа с худшим уровнем первая
    expect(s.groups[0]?.rows[0]?.level).toBe('bad')             // проблемная строка выше остальных в группе
  })
  it('одинаковый уровень: называется проблема из группы выше по порядку, внутри группы первая по порядку источника', () => {
    const s = summarize([
      r({ id: 'm', group: 'market', level: 'warn', brief: 'рынок' }),
      r({ id: 'e1', group: 'exec', level: 'warn', brief: 'исполнитель 1' }),
      r({ id: 'e2', group: 'exec', level: 'warn', brief: 'исполнитель 2' }),
    ])
    expect(s.text).toBe('исполнитель 1')
    expect(s.extra).toBe(2)
  })
  it('bad перебивает warn независимо от порядка', () => {
    const s = summarize([r({ id: 'a', group: 'platform', level: 'warn', brief: 'a' }), r({ id: 'b', group: 'news', level: 'bad', brief: 'b' })])
    expect(s.level).toBe('bad')
    expect(s.text).toBe('b')
  })
  it('платформа остановлена без проблем: idle с текстом об этом', () => {
    const base = mockStatus('mock', NOW)
    const s = sum({ ...base, ctl: { ...base.ctl!, platform: { ...base.ctl!.platform, state: 'stopped' } } })
    expect(s.level).toBe('idle')
    expect(s.text).toBe('платформа остановлена')
  })
  it('платформа остановлена: «исполнитель не запущен» и «мозг не отвечает» не проблема', () => {
    const base = mockStatus('mock-bad', NOW)
    const rows = buildRows({ ...base, ctl: { ...base.ctl!, platform: { ...base.ctl!.platform, state: 'stopped' } } })
    expect(rows.find((x) => x.id === 'brain')?.level).toBe('idle')
    expect(rows.find((x) => x.id === 'node:net')?.level).toBe('idle')
  })
  it('KILL: bad с причиной', () => {
    const base = mockStatus('mock', NOW)
    const s = sum({ ...base, system: { ...base.system!, kill: { flatten: false, reason: 'дневной лимит' } } })
    expect(s.level).toBe('bad')
    expect(s.text).toBe('активна аварийная остановка')
  })
  it('мозг в режиме «только защита»: warn', () => {
    const base = mockStatus('mock', NOW)
    const rows = buildRows({ ...base, brain: { ...base.brain!, status: 'protect', protect_reason: 'лимит' } })
    expect(rows.find((x) => x.id === 'brain')).toMatchObject({ level: 'warn', reason: 'лимит' })
  })
  it('строки без данных не выдумываются', () => {
    const ids = buildRows(empty()).map((x) => x.id)
    expect(ids).toEqual(['link'])
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
    expect(at(THRESHOLDS.heatS + 1)?.brief).toContain('карта рынка не обновляется')
  })
  it('новости: тишина дольше порога даёт warn и текст с возрастом', () => {
    const base = mockStatus('mock', NOW)
    const news = (age: number) => buildRows({ ...base, news: { ...base.news!, items: [{ ...base.news!.items[0]!, ts: NOW - age }] } }).find((x) => x.id === 'news-fresh')
    expect(news(300)?.level).toBe('ok')
    expect(news(THRESHOLDS.newsQuietS + 1)?.level).toBe('warn')
    expect(news(720 + THRESHOLDS.newsQuietS)?.brief).toBe('новости не обновляются 2 ч 12 мин')
  })
  it('ленты новостей: часть упала warn, все упали bad', () => {
    const base = mockStatus('mock', NOW)
    const src = (oks: boolean[]) => buildRows({ ...base, news: { ...base.news!, sources: oks.map((ok, i) => ({ id: `s${i}`, ok, fails: 0, verified: true, degraded: false })) } }).find((x) => x.id === 'news-sources')
    expect(src([true, true])?.level).toBe('ok')
    expect(src([true, false])?.level).toBe('warn')
    expect(src([false, false])?.level).toBe('bad')
  })
  it('короткий возраст не мерцает: секунды округляются вниз до 10', () => {
    expect(coarse(3)).toBe('10 с')
    expect(coarse(47)).toBe('40 с')
    expect(coarse(725)).toBe('12 мин')
  })
})

describe('проверки запуска', () => {
  it('красный гейт не пройден: bad, амбер: warn, пройденные ok', () => {
    const base = mockStatus('mock-warn', NOW)
    const gates = buildRows({ ...base, ctl: { ...base.ctl!, checks: [
      { id: 'H1', name: 'KILL', sev: 'red', ok: false, msg: 'активен' },
      { id: 'W1', name: 'W_X', sev: 'amber', ok: false, msg: 'мало истории' },
      { id: 'H2', name: 'Связь', sev: 'red', ok: true, msg: '' },
    ] } }).filter((x) => x.group === 'gates')
    expect(gates.map((x) => x.level)).toEqual(['bad', 'warn', 'ok'])
  })
})
