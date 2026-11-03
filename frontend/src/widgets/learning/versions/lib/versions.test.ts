import { describe, expect, it } from 'vitest'
import type { Est, RulebookPair, SwapPair, VersionSide, VersionsBlock } from '@/entities/learning'
import { VERSION_VERDICT, dayText, pairViews, preR2Text, rulebookPairView, sideView, swapPairView, versionsSummary } from './versions'

const est = (v: number | null, lo: number | null, hi: number | null, n: number): Est => ({ v, lo, hi, n, lvl: 0.95 })
const side = (n: number, r: number | null, viol: number | null = 0.2, rf = n): VersionSide => ({ n, r: est(r, r == null ? null : r - 0.1, r == null ? null : r + 0.1, n), viol: est(viol, 0.1, 0.3, rf), n_rf: rf, small: n < 30 })
const TS = 1792759800          // 2026-10-23 UTC

function rb(o: Partial<RulebookPair> = {}): RulebookPair {
  return { kind: 'rulebook', from: 'R-0001', to: 'R-0002', ts: TS, reason_code: 'PROMOTE', rules_n: 2, before: side(100, 0.075), after: side(120, 0.18),
    diff: est(0.1066, -0.1024, 0.3156, 220), p: 0.3159, verdict: 'not_worse', ...o }
}
function sw(o: Partial<SwapPair> = {}): SwapPair {
  return { kind: 'model', kinds: ['model'], ts: TS - 86400, seqs: [1], targets: ['entry_scout'], to: ['model-b'], before: side(81, 0), after: side(159, 0.14),
    diff: est(0.14, -0.09, 0.36, 240), p: 0.24, verdict: 'few_data', ...o }
}

describe('вердикт словами', () => {
  it('три слова требования 14.2 и тон', () => {
    expect(rulebookPairView(rb()).verdict).toBe('не хуже')
    expect(rulebookPairView(rb({ verdict: 'worse' })).verdict).toBe('хуже')
    expect(rulebookPairView(rb({ verdict: 'few_data' })).verdict).toBe('данных мало')
    expect(rulebookPairView(rb({ verdict: 'worse' })).tone).toBe('bad')
    expect(rulebookPairView(rb({ verdict: 'zzz' as never })).verdict).toBe('данных мало')   // неизвестное — самое осторожное слово
    expect(VERSION_VERDICT.few_data.text(100)).toContain('100')
  })
})

describe('стороны до/после', () => {
  it('n словами, R и нарушения с интервалом, пометки n < 30 и n < 100', () => {
    const v = sideView(side(120, 0.1816))
    expect(v.n).toBe('120 сделок')
    expect(v.r.value).toBe('+0.18 R')
    expect(v.r.interval).toBe('от +0.08 до +0.28')
    expect(v.viol.value).toBe('20%')
    expect(v.viol.interval).toBe('от 10% до 30%')
    expect(v.mark).toBe('')
    expect(sideView(side(81, 0)).mark).toBe('для вывода нужно 100')
    expect(sideView(side(12, 0.3)).mark).toBe('мало данных (n < 30)')
    expect(sideView(side(1, 0.3)).n).toBe('1 сделка')
  })
  it('пустая сторона: прочерки без мусора', () => {
    const v = sideView({ n: 0, r: est(null, null, null, 0), viol: est(null, null, null, 0), n_rf: 0, small: true })
    expect(v.r.value).toBe('–')
    expect(v.r.interval).toBe('')
    expect(v.violBase).toBe('отметок о правилах нет')
    expect(v.mark).toBe('мало данных (n < 30)')
    expect(sideView(null).n).toBe('0 сделок')
  })
})

describe('строки смен', () => {
  it('rulebook: версии, дата, причина и число правил', () => {
    const v = rulebookPairView(rb())
    expect(v.title).toBe('Правила R-0001 → R-0002')
    expect(v.meta).toBe('23 окт · добавлено правило · 2 правила в наборе')
    expect(v.diff.value).toBe('+0.11 R')
    expect(v.diff.interval).toBe('от −0.10 до +0.32')
    expect(v.level).toBe('95%')
    expect(v.p).toBe('p = 0.32')
  })
  it('модель и промпт: роли и новая модель словами', () => {
    expect(swapPairView(sw()).title).toBe('Смена модели')
    expect(swapPairView(sw()).meta).toBe('22 окт · роли: entry scout · теперь: model-b')
    expect(swapPairView(sw({ kind: 'prompt', kinds: ['prompt'], to: [] })).title).toBe('Смена промпта')
    expect(swapPairView(sw({ kinds: ['model', 'prompt'] })).title).toBe('Смена модели и промпта')
  })
  it('лента: новые сверху; min_n блока', () => {
    const b = { id: 'versions', title: 'В', status: 'ok', note: '', min_n: 100, rulebook: [rb()], swaps: [sw()] } as VersionsBlock
    expect(pairViews(b).map((r) => r.kind)).toEqual(['rulebook', 'model'])
  })
  it('итог: хуже важнее данных мало, иначе ухудшений нет', () => {
    expect(versionsSummary([])).toBeNull()
    expect(versionsSummary([rulebookPairView(rb({ verdict: 'worse' })), swapPairView(sw())])).toEqual({ word: 'хуже после 1 смены', tone: 'bad' })
    expect(versionsSummary([swapPairView(sw())])).toEqual({ word: 'данных мало', tone: 'warn' })
    expect(versionsSummary([rulebookPairView(rb()), swapPairView(sw())])).toEqual({ word: 'ухудшений нет', tone: 'ok' })
  })
  it('дата по UTC и сделки до R2', () => {
    expect(dayText(null)).toBe('дата неизвестна')
    expect(dayText(1793577600)).toBe('2 ноя')
    expect(preR2Text(0)).toBe('')
    expect(preR2Text(20)).toContain('20 сделок без версии правил')
  })
})
