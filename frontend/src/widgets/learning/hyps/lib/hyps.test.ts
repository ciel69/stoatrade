import { describe, expect, it } from 'vitest'
import type { Est, HypFamily, HypsBlock } from '@/entities/learning'
import { correctionText, familyHint, familyName, familyView, hypsSummary, progressView, refAgeText, refView } from './hyps'

const est = (v: number | null, lo: number | null, hi: number | null, n: number): Est => ({ v, lo, hi, n, lvl: 0.95 })
const fam = (o: Partial<HypFamily> = {}): HypFamily => ({
  family: 'range-fade', n: 96, n_target: 30, small: false, r: est(0.1103, -0.0779, 0.2985, 96), win: 0.5521, p: 0.2476, status: 'not_confirmed', confirmable: true, ref: null, ...o,
})
const REF = { policy: 'P4', policy_ver: 1, kinds: ['liq_cascade'], env_norm: 'mainnet', window_days: 14, n: 42, r: est(0.12, -0.03, 0.27, 42) }

describe('прогресс n из 30', () => {
  it('доля полосы, подпись и имя для читалки', () => {
    expect(progressView(24)).toMatchObject({ ratio: 0.8, text: '24 из 30', done: false, label: 'Набрано 24 сделки из 30 нужных' })
    expect(progressView(30)).toMatchObject({ ratio: 1, text: '30 сделок, порог 30 пройден', done: true })
    expect(progressView(96).ratio).toBe(1)            // полоса не вылезает за край
    expect(progressView(0)).toMatchObject({ ratio: 0, text: '0 из 30' })
    expect(progressView(Number.NaN).text).toBe('0 из 30')
  })
})

describe('строка семейства', () => {
  it('название по-русски, код в подсказке; незнакомое — без дефисов', () => {
    expect(familyName('range-fade')).toBe('От границ диапазона')
    expect(familyName('liq-cascade-reclaim')).toBe('После волны ликвидаций')
    expect(familyName('new-thing_x')).toBe('new thing x')
    expect(familyHint('range-fade').rows).toEqual([{ label: 'Код', value: 'range-fade' }])
  })
  it('статус словами: копит, подтверждена, не подтверждена; прочие не подтверждаются', () => {
    expect(familyView(fam()).status).toBe('не подтверждена')
    expect(familyView(fam({ status: 'confirmed' }))).toMatchObject({ status: 'подтверждена', tone: 'ok' })
    expect(familyView(fam({ status: 'accumulating', n: 7 }))).toMatchObject({ status: 'копит', tone: 'accent' })
    expect(familyView(fam({ family: 'other', confirmable: false, status: 'accumulating' })).status).toBe('не подтверждается')
  })
  it('средний R с интервалом и n, доля в плюс, p; n < 30 помечено', () => {
    const v = familyView(fam())
    expect(v.r).toMatchObject({ value: '+0.11 R', interval: 'от −0.08 до +0.30', n: 'n = 96', small: '' })
    expect(v.win).toBe('в плюс 55%')
    expect(v.p).toBe('p = 0.25')
    expect(familyView(fam({ n: 24, r: est(0.0164, -0.2363, 0.2692, 24) })).r.small).toBe('мало данных (n < 30)')
  })
  it('семейство без сделок (только справка): прочерк, без n и интервала', () => {
    const v = familyView(fam({ n: 0, r: est(null, null, null, 0), win: null, p: null, status: 'accumulating', ref: REF }))
    expect(v.r).toMatchObject({ value: '–', interval: '', n: '' })
    expect(v.win).toBe('')
    expect(v.ref?.n).toBe('n = 42 часа с сигналами')
  })
})

describe('справка меток', () => {
  it('«что говорят тысячи сигналов»: правило, R с интервалом и n', () => {
    const r = refView(REF)!
    expect(r.who).toBe('P4: вход против направления сигнала, сигналы liq cascade, за 14 дней')
    expect(r.r.value).toBe('+0.12 R')
    expect(r.r.interval).toBe('от −0.03 до +0.27')
    expect(r.small).toBe('')
    expect(r.tip.title).toBe('Что говорят тысячи сигналов')
    expect(r.tip.text).toContain('статус гипотезы он не меняет')
    expect(refView({ ...REF, n: 9 })!.small).toBe('мало данных (n < 30)')
    expect(refView(null)).toBeNull()
  })
})

describe('итог и порог', () => {
  const b = { id: 'hyps', title: 'Гипотезы', status: 'ok', note: '', k: 2, alpha_adj: 0.025, n_confirmed: 1, families: [fam(), fam({ family: 'x' })] } as HypsBlock
  it('подтверждено k из n', () => {
    expect(hypsSummary(b)).toEqual({ word: 'подтверждено 1 из 2', tone: 'ok' })
    expect(hypsSummary({ ...b, n_confirmed: 0 }).tone).toBe('neutral')
  })
  it('порог Бонферрони словами; без alpha пусто', () => {
    expect(correctionText(b)).toBe('Порог случайности: p ниже 0.025 — 0.05, поделённое на 2 гипотезы с 30 и больше сделками.')
    expect(correctionText({ ...b, alpha_adj: null })).toBe('')
    expect(refAgeText(null, 0)).toBe('')
    expect(refAgeText(1000, 1000 + 1800)).toContain('Справка по сигналам обновлена')
  })
})
