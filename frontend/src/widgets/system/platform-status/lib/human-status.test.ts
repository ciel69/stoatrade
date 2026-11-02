import { describe, expect, it } from 'vitest'
import { mockStatus } from './mock'
import { buildRows } from './rows'
import { summarize } from './summary'
import type { StatusInput } from './types'

const NOW = 1_000_000
const sum = (i: StatusInput) => summarize(buildRows(i), { mode: i.ctl?.platform.mode ?? null })
const CODE_LIKE = [/\b[HSWP]\d{1,2}\b/, /E_[A-Z_]+/, /\b[a-z]+_[a-z_]+\b/, /\.json/]

describe('образец владельца: бумага работает, проверки живой торговли не пройдены', () => {
  const s = sum(mockStatus('mock-paper', NOW))
  it('индикатор не красный и не жёлтый: «Всё в порядке»', () => {
    expect(s.level).toBe('ok')
    expect(s.text).toBe('Всё в порядке')
    expect(s.problems).toBe(0)
  })
  it('наверху есть справка про живую торговлю простыми словами, не больше 6 строк', () => {
    expect(s.key.length).toBeLessThanOrEqual(6)
    const live = s.key.find((x) => x.id === 'live-readiness')
    expect(live?.level).toBe('idle')
    expect(live?.note).toContain('только на бумаге')
    expect(live?.note).toContain('владелец')
  })
  it('в основном тексте строк нет кодов и служебных слов', () => {
    for (const row of s.groups.flatMap((g) => g.rows)) for (const t of [row.label, row.value, row.note ?? '']) for (const re of CODE_LIKE) expect(t, row.id).not.toMatch(re)
  })
  it('баннер запуска с S1,S5,S6 не красит индикатор и не показывает коды', () => {
    const banner = buildRows(mockStatus('mock-paper', NOW)).find((x) => x.id === 'banner')
    expect(banner?.level).toBe('idle')
    expect(banner?.label).toContain('День запущен')
    expect(banner?.note).not.toMatch(/S\d/)
  })
  it('технические подробности сохранены для специалиста', () => {
    expect(s.details.join('\n')).toContain('agents.json')
  })
})

describe('красный остаётся для реально опасного', () => {
  const base = mockStatus('mock', NOW)
  it('нет связи', () => {
    expect(sum({ ...base, link: 'closed' }).level).toBe('bad')
  })
  it('аварийная остановка: «Торговля остановлена»', () => {
    const s = sum({ ...base, system: { ...base.system!, kill: { flatten: false, reason: 'вручную' } } })
    expect(s.level).toBe('bad')
    expect(s.text).toMatch(/^Торговля остановлена/)
    expect(s.text).not.toMatch(/KILL/)
  })
  it('дневной лимит убытка исчерпан: входы закрыты, красный', () => {
    const s = sum({ ...base, system: { ...base.system!, room: { allowed: false, reasons: ['дневной лимит убытка достигнут (1.95 USDT)'], rows: [] } } })
    expect(s.level).toBe('bad')
    expect(s.text).toContain('Дневной лимит убытка исчерпан'.toLowerCase())
  })
  it('исполнитель не отвечает при торговле: красный и торговля остановлена', () => {
    const nodes = base.system!.nodes.map((n) => (n.id === 'exec' ? { ...n, level: 'bad' as const, text: 'не запущен' } : n))
    const s = sum({ ...base, system: { ...base.system!, nodes } })
    expect(s.level).toBe('bad')
    expect(s.text).toMatch(/^Торговля остановлена: исполнитель/)
  })
  it('гейт «исполнитель не отвечает» в бумажном режиме тоже красный', () => {
    const c = base.ctl!
    const s = sum({ ...base, ctl: { ...c, checks: [{ id: 'H3', name: 'x', sev: 'red', ok: false, msg: 'исполнитель не отвечает (heartbeat нет)' }] } })
    expect(s.level).toBe('bad')
  })
})

describe('проверки живой торговли по режимам', () => {
  const paper = mockStatus('mock-paper', NOW)
  const asLive = (): StatusInput => ({ ...paper, ctl: { ...paper.ctl!, platform: { ...paper.ctl!.platform, mode: 'live' } }, system: { ...paper.system!, mode: 'live' } })
  it('в режиме paper ни одна проверка допуска не даёт красного или жёлтого', () => {
    const rows = buildRows(paper).filter((r) => r.id.startsWith('gate:H'))
    expect(rows.every((r) => r.level === 'ok' || r.level === 'idle')).toBe(true)
  })
  it('в режиме live то же нарушение защиты красное', () => {
    const s = sum(asLive())
    expect(s.level).toBe('bad')
  })
  it('в режиме paper провал дневного лимита по живым сделкам и защиты стопов это справка', () => {
    const c = paper.ctl!
    const checks = [...c.checks, { id: 'H5', name: 'x', sev: 'red', ok: false, msg: 'дневной убыток 2.00 USDT исчерпал лимит 1.95 USDT' }]
    expect(sum({ ...paper, ctl: { ...c, checks: checks.filter((x, i, a) => a.findIndex((y) => y.id === x.id) === i || x.id === 'H5').filter((x) => x.id !== 'H5' || !x.ok) } }).level).not.toBe('bad')
  })
})
