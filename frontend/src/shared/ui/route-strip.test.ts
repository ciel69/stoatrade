import { describe, expect, it } from 'vitest'
import { axisOf, guideParts, markParts, nearestMark, placeTip, priceAt, tipText, type StripMark } from './route-strip'

const m = (o: Partial<StripMark> & Pick<StripMark, 'key' | 'at'>): StripMark => ({ kind: 'take', name: o.key, price: null, ...o })

describe('tipText', () => {
  it('название, цена, процент от входа и деньги одной строкой', () => {
    expect(tipText({ name: 'Стоп', price: 2.892, pct: 3.01, money: 1.15 })).toBe('Стоп 2.892 · +3.01% · +1.15 USDT')
  })
  it('убыток со знаком минус, единица измерения своя', () => {
    expect(tipText({ name: 'Сейчас', price: 100, pct: -0.5, money: -0.8, unit: 'R' })).toBe('Сейчас 100.00 · −0.50% · −0.80 R')
  })
  it('чего нет, того не пишем', () => {
    expect(tipText({ name: 'Вход', price: 2.807 })).toBe('Вход 2.807')
    expect(tipText({ name: 'Тейк', price: 3, pct: null, money: null })).toBe('Тейк 3.000')
  })
  it('части подсказки несут тон для цвета', () => {
    const p = markParts({ name: 'Стоп', price: 1, pct: -2, money: 0.5 })
    expect(p.pct?.tone).toBe('down')
    expect(p.money?.tone).toBe('up')
    expect(markParts({ name: 'X', price: 1, pct: 0 }).pct?.tone).toBe('')
  })
})

describe('nearestMark', () => {
  const marks = [m({ key: 'stop', kind: 'stop', at: 0 }), m({ key: 'entry', kind: 'entry', at: 0.5 }), m({ key: 'now', kind: 'now', at: 0.51 }), m({ key: 'tp', at: 1 })]
  it('зона 16 px: радиус 8 от черты', () => {
    expect(nearestMark(marks, 102, 200)?.key).toBe('now')
    expect(nearestMark(marks, 8, 200)?.key).toBe('stop')
    expect(nearestMark(marks, 9, 200)).toBeNull()
    expect(nearestMark(marks, 192, 200)?.key).toBe('tp')
    expect(nearestMark(marks, 191, 200)).toBeNull()
  })
  it('при равном расстоянии цена важнее входа, ближайшая важнее приоритета', () => {
    const tie = [m({ key: 'entry', kind: 'entry', at: 0.5 }), m({ key: 'now', kind: 'now', at: 0.5 })]
    expect(nearestMark(tie, 100, 200)?.key).toBe('now')
    expect(nearestMark(marks, 98, 200)?.key).toBe('entry')           // 98: до входа (100) 2 px, до цены (102) 4 px
  })
  it('пустая полоса и нулевая ширина не падают', () => {
    expect(nearestMark([], 5, 100)).toBeNull()
    expect(nearestMark(marks, 0, 0)?.key).toBe('now')           // все черты в одной точке: побеждает приоритет
  })
})

describe('ось и направляющая', () => {
  const long = [m({ key: 'stop', kind: 'stop', at: 0, price: 96 }), m({ key: 'entry', kind: 'entry', at: 1 / 3, price: 100 }), m({ key: 'tp', at: 1, price: 108 })]
  it('лонг: цена растёт с долей, процент от входа в плюс выше входа', () => {
    const a = axisOf(long)!
    expect(priceAt(a, 0.5)).toBeCloseTo(102)
    const g = guideParts(a, 2 / 3)
    expect(g.value).toBe('104.00')
    expect(g.pct?.text).toBe('+4.00%')
  })
  it('шорт: цена падает с долей, но движение вниз это плюс', () => {
    const a = axisOf([m({ key: 'stop', kind: 'stop', at: 0, price: 104 }), m({ key: 'entry', kind: 'entry', at: 1 / 3, price: 100 }), m({ key: 'tp', at: 1, price: 92 })])!
    expect(priceAt(a, 2 / 3)).toBeCloseTo(96)
    expect(guideParts(a, 2 / 3).pct?.text).toBe('+4.00%')
    expect(guideParts(a, 0).pct?.text).toBe('−4.00%')
  })
  it('нет двух цен или все в одной точке: оси нет', () => {
    expect(axisOf([m({ key: 'a', at: 0, price: 1 })])).toBeNull()
    expect(axisOf([m({ key: 'a', at: 0.5, price: 1 }), m({ key: 'b', at: 0.5, price: 2 })])).toBeNull()
  })
  it('без входа процент не считаем', () => {
    const a = axisOf([m({ key: 'a', at: 0, price: 1 }), m({ key: 'b', at: 1, price: 2 })])!
    expect(guideParts(a, 0.5).pct).toBeNull()
  })
})

describe('placeTip', () => {
  it('по центру над якорем, пока помещается', () => {
    expect(placeTip(100, 60, 300)).toBe(70)
  })
  it('прижимается к левому и правому краю с запасом на отступ карточки', () => {
    expect(placeTip(0, 80, 300)).toBe(-8)
    expect(placeTip(300, 80, 300)).toBe(300 + 8 - 80)
    expect(placeTip(300, 80, 300, 0)).toBe(220)
  })
  it('подсказка шире полосы: к левому краю, без прыжков', () => {
    expect(placeTip(50, 400, 200)).toBe(-8)
  })
})
