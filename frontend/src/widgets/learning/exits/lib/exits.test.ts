import { describe, expect, it } from 'vitest'
import type { Est, ExitsBlock, ExitsR4 } from '@/entities/learning'
import {
  R4_EMPTY_NOTE, capturePoints, ciText, clampShare, dateText, dateTimeText, exitRows, pickSeries, r4View, rSigned, shareText, windowText,
} from './exits'

const est = (v: number, n: number, half = 0.1): Est => ({ v, lo: v - half, hi: v + half, n, lvl: 0.95 })

const TS = Math.floor(new Date(2026, 9, 31, 15, 0).getTime() / 1000)

const r4 = (over: Partial<ExitsR4> = {}): ExitsR4 => ({
  status: 'ok', reason: null, note: '', mode: 'paper', since: TS,
  version: { ver: 'X-0002', parent: 'X-0001', reason_code: 'PROMOTE', actor: 'code', ts: TS, policy_h: 'ab12' },
  d: { mean: 0.2, lo: 0.19, hi: 0.21, n_blocks: 40, n_paths: 40 },
  d_b: { mean: 0.2, lo: 0.19, hi: 0.21, n: 40 },
  cap: { fact: 0.6, x0001: 0.5, n: 40, mfe_sum: 80, r_fact_sum: 48, r_x0001_sum: 40 },
  costs: { cur: 0.02, x0001: 0.02, n: 40 },
  trials: { open: 1, closed: 2, rows: [] },
  watch: { ts: TS + 3600, ver: 'X-0002', prev: 'X-0001', codes: [], points: 4, last: null, sim_gap: null },
  verdict: { state: 'confirmed', text: 'Нижние границы 95% интервалов выше нуля.' },
  ...over,
})

describe('exits lib', () => {
  it('подписи окон без сырых ключей', () => {
    expect(windowText('50')).toBe('Последние 50')
    expect(windowText('all')).toBe('Весь период')
    expect(windowText('90')).toBe('Последние 90')
  })
  it('доля забранного в показе не выше 100%', () => {
    const c = clampShare(est(1.04, 20, 0.1))!
    expect(c.v).toBe(1)
    expect(c.hi).toBe(1)
    expect(c.lo).toBeCloseTo(0.94)
    expect(clampShare(null)).toBeNull()
  })
  it('строки: порядок окон, мало данных, доля сделок с путём', () => {
    const windows: ExitsBlock['windows'] = {
      all: { n_path: 80, path_share: 0.8, mfe: est(1.1, 80), capture: est(0.4, 80) },
      '200': { n_path: 80, path_share: 0.8, mfe: est(1.1, 80), capture: est(0.4, 80) },
      '50': { n_path: 12, path_share: 1, mfe: est(1.2, 12), capture: est(0.42, 12) },
    }
    const rows = exitRows(windows)
    expect(rows.map((r) => r.key)).toEqual(['50', '200', 'all'])
    expect(rows[0]!.small).toBe(true)
    expect(rows[1]!.small).toBe(false)
    expect(rows[0]!.pathShare).toBe('путь известен у 100% сделок')
    expect(rows[1]!.pathShare).toBe('путь известен у 80% сделок')
    expect(rows[0]!.capture.value).toBe('42%')
    expect(rows[0]!.mfe.value).toBe('1.20 R')
  })
  it('нет path_share: подпись пустая', () => {
    const rows = exitRows({ '50': { n_path: 5, path_share: null, mfe: null, capture: null } })
    expect(rows[0]!.pathShare).toBe('')
    expect(rows[0]!.capture.value).toBe('–')
  })
  it('ряд: берётся окно 50, при пустом 200', () => {
    const pt = (i: number) => ({ i, ts: null, capture: est(0.4, 30), mfe: est(1, 30) })
    expect(pickSeries({ '50': [pt(30), pt(31)], '200': [pt(30)] })?.key).toBe('50')
    expect(pickSeries({ '50': [], '200': [pt(30)] })?.key).toBe('200')
    expect(pickSeries({ '50': [], '200': [] })).toBeNull()
    expect(pickSeries({})).toBeNull()
  })
  it('точки без оценки пропускаются', () => {
    expect(capturePoints([{ i: 30, capture: null }, { i: 31, capture: est(0.5, 30) }])).toHaveLength(1)
  })
})

describe('выходы R4: строки для экрана', () => {
  it('разность в R: знак, два знака, тире без числа', () => {
    expect(rSigned(0.12)).toBe('+0.12R')
    expect(rSigned(-0.12)).toBe('−0.12R')
    expect(rSigned(0)).toBe('0.00R')
    expect(rSigned(null)).toBe('–')
  })

  it('интервал 95%: обе границы или пусто', () => {
    expect(ciText(0.01, 0.23)).toBe('95%: +0.01…+0.23R')
    expect(ciText(-0.21, 0.1)).toBe('95%: −0.21…+0.10R')
    expect(ciText(null, 0.1)).toBe('')
    expect(ciText(0.1, null)).toBe('')
  })

  it('доля: проценты, границы 0..1, тире без числа', () => {
    expect(shareText(0.42)).toBe('42%')
    expect(shareText(0.6)).toBe('60%')
    expect(shareText(1)).toBe('100%')
    expect(shareText(null)).toBe('–')
  })

  it('даты: длинная дата и дата со временем в секундах epoch', () => {
    expect(dateText(TS)).toBe('31 октября 2026')
    expect(dateText(null)).toBe('')
    expect(dateTimeText(TS)).toContain('31 октября')
    expect(dateTimeText(TS)).toContain('15:00')
    expect(dateTimeText(null)).toBe('')
  })

  it('подтверждено: слово, версия, четыре метрики, испытания и сторож', () => {
    const v = r4View(r4())
    expect(v.empty).toBe(false)
    expect(v.word).toBe('Улучшение подтверждено')
    expect(v.tone).toBe('ok')
    expect(v.version).toBe('Версия X-0002 от 31 октября 2026')
    expect(v.reason).toBe('PROMOTE')
    expect(v.text).toBe('Нижние границы 95% интервалов выше нуля.')
    expect(v.rows.map((r) => r.key)).toEqual(['d', 'd_b', 'cap', 'costs'])
    expect(v.rows[0]!.value).toBe('+0.20R')
    expect(v.rows[0]!.interval).toBe('95%: +0.19…+0.21R')
    expect(v.rows[0]!.counter).toBe('блоков: 40')
    expect(v.rows[1]!.counter).toBe('сделок: 40')
    expect(v.rows[2]!.pair.map((p) => p.value)).toEqual(['60%', '50%'])
    expect(v.rows[2]!.counter).toBe('сделок с MFE > 0: 40')
    expect(v.rows[3]!.pair.map((p) => p.value)).toEqual(['2%', '2%'])
    expect(v.trials).toBe('Испытания: открыто 1, закрыто 2')
    expect(v.watch).toContain('точек: 4')
    expect(v.codes).toBe('')
  })

  it('мало данных: слово в предупреждающем тоне', () => {
    const v = r4View(r4({ verdict: { state: 'few_data', text: 'данных мало' } }))
    expect(v.word).toBe('Данных мало')
    expect(v.tone).toBe('warn')
    expect(v.text).toBe('данных мало')
  })

  it('пусто: только note, без строк и вердикта', () => {
    const v = r4View(r4({ status: 'no_data', note: 'Появится после первого продвижения.', version: null, d: null, d_b: null, cap: null, costs: null, watch: null, verdict: { state: 'few_data', text: 'данных мало' } }))
    expect(v.empty).toBe(true)
    expect(v.note).toBe('Появится после первого продвижения.')
    expect(v.rows).toEqual([])
    expect(v.word).toBe('')
    expect(v.version).toBe('')
  })

  it('нет r4 в старом ответе: та же пустая подпись', () => {
    const v = r4View(undefined)
    expect(v.empty).toBe(true)
    expect(v.note).toBe(R4_EMPTY_NOTE)
  })

  it('не paper и нет d: тире и пояснение, в paper пояснения нет', () => {
    const live = r4View(r4({ mode: 'live', d: null }))
    expect(live.rows[0]!.value).toBe('–')
    expect(live.rows[0]!.interval).toBe('')
    expect(live.rows[0]!.note).toBe('на этом режиме пути меток не сравниваются (метки — paper)')
    expect(r4View(r4({ d: null })).rows[0]!.note).toBe('')
  })

  it('сторож: коды и пустое состояние', () => {
    const codes = r4View(r4({ watch: { ts: null, ver: null, prev: null, codes: ['EXIT_SIM_GAP'], points: 3, last: null, sim_gap: null } }))
    expect(codes.codes).toBe('коды: EXIT_SIM_GAP')
    expect(codes.watch).toBe('Сторож: точек: 3')
    expect(r4View(r4({ watch: null })).watch).toBe('Сторож ещё не проверял')
  })
})
