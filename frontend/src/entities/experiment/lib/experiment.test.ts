import { describe, expect, it } from 'vitest'
import { expHeadline, expState, groupTitle, hiddenRows, rowKey, rowViews, sortRows, splitKey, thresholdText, tradeViews } from './experiment'
import { isExpMock, mockExp } from './mock'
import type { ExpDetail, ExpRow } from '../model/types'

const row = (name: string, o: Partial<ExpRow> = {}): ExpRow => ({
  name, mode: 'paper', n: 40, wins: 20, win_rate: 0.5, avg_r: 0.1, se_r: 0.2, pf: 1.2, pf_inf: false, pnl: 1, fees: 0.1, p: 0.3, small: false, below_alpha: false, confirmed: false, last_ts: 1, trades: [], ...o,
})
const det = (rows: ExpRow[], o: Partial<ExpDetail> = {}): ExpDetail => ({ ok: true, by: 'hyp', mode: 'paper', min_n: 30, alpha: 0.05, k: rows.length, alpha_adj: rows.length ? 0.05 / rows.length : null, rows, rows_total: rows.length, total_closed: 9, confirmed: [], ...o })

describe('порядок строк', () => {
  it('подтверждённые сверху, затем достаточные по среднему R, затем малые по числу сделок', () => {
    const rows = [row('a', { small: true, n: 5, avg_r: 2 }), row('b', { avg_r: -0.3 }), row('c', { avg_r: 0.5 }), row('d', { confirmed: true, avg_r: 0.1 }), row('e', { small: true, n: 12, avg_r: 0 })]
    expect(sortRows(rows).map((r) => r.name)).toEqual(['d', 'c', 'b', 'e', 'a'])
  })
  it('не меняет исходный массив; равные по имени', () => {
    const rows = [row('z'), row('y')]
    sortRows(rows)
    expect(rows.map((r) => r.name)).toEqual(['z', 'y'])
    expect(sortRows(rows).map((r) => r.name)).toEqual(['y', 'z'])
  })
})

describe('ключ строки', () => {
  it('режим и имя; имя с двоеточием не ломается', () => {
    expect(rowKey(row('x:y'))).toBe('paper:x:y')
    expect(splitKey('paper:x:y')).toEqual({ mode: 'paper', name: 'x:y' })
    expect(splitKey('')).toBeNull()
    expect(splitKey('нет')).toBeNull()
  })
})

describe('строки для показа', () => {
  it('человеческие подписи, «мало данных» без числа p, режим только когда их несколько', () => {
    const d = det([row('long', { avg_r: 0.5, win_rate: 0.58, pnl: -2.5, p: 0.004 }), row('short', { small: true, n: 3, p: null, mode: 'live' })], { by: 'side' })
    const v = rowViews(d, 'side')
    expect(v.map((x) => x.title)).toEqual(['покупка', 'продажа'])
    expect(v[0]).toMatchObject({ avgR: '+0.50 R', win: '58%', pnl: '−2.50', pnlTone: 'neg', avgTone: 'pos', mode: 'бумага', nText: '40 сделок' })
    expect(v[0].p.text).toBe('вряд ли случайность')
    expect(v[1]).toMatchObject({ small: true, mode: 'реальные деньги', nText: '3 сделки' })
    expect(v[1].p.text).toBe('мало данных')
    expect(v[1].pHint).toContain('меньше 30')
  })
  it('режим не показывается, если он один; описание эксперимента из его записи', () => {
    const d = det([row('stop-width')], { by: 'exp', meta: { 'stop-width': { hypothesis: 'Широкий стоп лучше', prereg: null, started: null, min_n: 30 } } })
    const v = rowViews(d, 'exp')
    expect(v[0].mode).toBe('')
    expect(v[0].note).toBe('Широкий стоп лучше')
    expect(rowViews(det([row('stop-width/wide')], { meta: d.meta }), 'variant')[0].note).toBe('Широкий стоп лучше')
    expect(rowViews(det([row('stop-width')], { meta: d.meta }), 'hyp')[0].note).toBeNull()
  })
  it('нет данных или сбой: пустой список', () => {
    expect(rowViews(null, 'hyp')).toEqual([])
    expect(rowViews({ ok: false, by: 'hyp', mode: 'paper' }, 'hyp')).toEqual([])
  })
})

describe('главный вывод', () => {
  it('с подтверждёнными: число и пояснение без обещаний', () => {
    const h = expHeadline(det([], { confirmed: [{ hyp: 'a', n: 41, avg_r: 0.3, win_rate: 0.5, p: 0.001 }] }))!
    expect(h.text).toBe('Подтверждённых гипотез: 1')
    expect(h.tone).toBe('ok')
    expect(h.detail).toContain('Это не обещание прибыли')
  })
  it('без подтверждённых: «пока нет», выборка копится; нет сделок: так и говорим', () => {
    expect(expHeadline(det([]))!.text).toBe('Подтверждённых гипотез пока нет')
    expect(expHeadline(det([]))!.detail).toContain('30')
    expect(expHeadline(det([], { total_closed: 0 }))!.detail).toContain('Закрытых сделок пока нет')
    expect(expHeadline(null)).toBeNull()
    expect(expHeadline({ ok: false, by: 'hyp', mode: 'paper' })).toBeNull()
  })
})

describe('порог и число сравнений', () => {
  it('K групп: порог строже обычного, числа в тексте', () => {
    expect(thresholdText(det([row('a'), row('b'), row('c'), row('d'), row('e'), row('f')]))).toContain('0,0083')
    expect(thresholdText(det([row('a')]))).toContain('одна группа')
    expect(thresholdText(det([]))).toBe('')
    expect(thresholdText(null)).toBe('')
  })
  it('скрытые строки: сколько не показано', () => {
    expect(hiddenRows(det([row('a')], { rows_total: 81 }))).toBe(80)
    expect(hiddenRows(det([row('a')]))).toBe(0)
    expect(hiddenRows(null)).toBe(0)
  })
})

describe('состояние страницы', () => {
  it('нет связи, загрузка, сбой, пусто, данные', () => {
    expect(expState(null, true, false)).toBe('link')
    expect(expState(null, true, true)).toBe('loading')
    expect(expState({ ok: false, by: 'hyp', mode: 'paper' }, false, true)).toBe('failed')
    expect(expState(det([], { total_closed: 0 }), false, true)).toBe('none')
    expect(expState(det([], { total_closed: 5 }), false, true)).toBe('cut')
    expect(expState(det([row('a')]), false, true)).toBe('ok')
    expect(expState(det([row('a')]), true, true)).toBe('loading')
  })
})

describe('сделки группы', () => {
  it('подписи словами, пустые поля не ломают', () => {
    const v = tradeViews([{ id: 'T1', sym: 'BTCUSDT', side: 'short', mode: 'paper', r: -1, pnl: -0.2, ts: 1793000000, reason: 'sl' }, { id: null, sym: null, side: null, mode: null, r: null, pnl: null, ts: null, reason: null }])
    expect(v[0]).toMatchObject({ title: 'BTCUSDT, продажа', r: '−1.00 R', rTone: 'neg', reason: 'сработал стоп', pnl: '−0.20 USDT' })
    expect(v[0].when).not.toBe('')
    expect(v[1]).toMatchObject({ title: 'Сделка', r: '–', pnl: '–', when: '', reason: '' })
  })
  it('заголовок панели', () => {
    expect(groupTitle('hyp', 'sweep-fade')).toBe('Гипотеза: sweep-fade')
    expect(groupTitle('side', 'long')).toBe('Сторона: покупка')
  })
})

describe('образцы для отладки', () => {
  it('режимы распознаются; образцы согласованы с контрактом', () => {
    expect(['mock', 'mock-empty', 'mock-confirmed'].every(isExpMock)).toBe(true)
    expect(isExpMock('x')).toBe(false)
    const c = mockExp('mock-confirmed', 'hyp', 'paper')
    expect(c.confirmed!.length).toBe(2)
    expect(c.rows!.filter((r) => r.confirmed).length).toBe(2)
    expect(mockExp('mock', 'hyp', 'paper').confirmed).toEqual([])
    const e = mockExp('mock-empty', 'hyp', 'all')
    expect(expState(e, false, true)).toBe('none')
    for (const by of ['hyp', 'exp', 'variant', 'setup', 'stype', 'symbol', 'side', 'reason', 'err', 'venue']) {
      const m = mockExp('mock', by, 'all')
      expect(m.rows!.length, by).toBeGreaterThan(0)
      for (const r of m.rows!) expect(r.small ? r.p === null : true).toBe(true)
    }
  })
})
