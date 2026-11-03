import { describe, expect, it } from 'vitest'
import type { NearPlan } from '../model/types'
import { dotsHint, distHint, planInfoHint, restingHint, sideHint, stopMoveKind, stopMovedHint, stripHints, timerHint } from './hints'
import { mockPlans } from './mock'

const plan = (o: Partial<NearPlan> = {}): NearPlan => ({ ...mockPlans().items[0], ...o })

describe('sideHint', () => {
  it('лонг и шорт объясняют направление и плечо', () => {
    expect(sideHint('long', 5).title).toMatch(/рост/)
    expect(sideHint('long', 5).text).toContain('Плечо ×5')
    expect(sideHint('short', 3).title).toMatch(/падение/)
    expect(sideHint('short', null).text).not.toContain('Плечо')
  })
  it('без стороны', () => {
    expect(sideHint(null).title).toMatch(/не задано/)
  })
})

describe('стоп подтянут', () => {
  it('лонг: стоп ниже входа это урезанный убыток, выше или равен входу это зафиксированная прибыль', () => {
    expect(stopMoveKind('long', 2.74, 2.807)).toBe('cut')
    expect(stopMoveKind('long', 2.892, 2.807)).toBe('locked')
    expect(stopMoveKind('long', 2.807, 2.807)).toBe('locked')
  })
  it('шорт зеркально', () => {
    expect(stopMoveKind('short', 2290, 2262)).toBe('cut')
    expect(stopMoveKind('short', 2250, 2262)).toBe('locked')
  })
  it('тексты различаются и объясняют стоп-лосс', () => {
    expect(stopMovedHint('cut').title).toMatch(/убыток урезан/)
    expect(stopMovedHint('locked').title).toMatch(/прибыль зафиксирована/)
    expect(stopMovedHint('cut').text).toMatch(/автоматический выход/)
  })
})

describe('прочие подсказки', () => {
  it('заявка на бирже: позиции нет и деньги не рискуются', () => {
    expect(restingHint.text).toMatch(/Пока позиции нет/)
  })
  it('процент: «за уровнем» и обычное расстояние', () => {
    expect(distHint(true).title).toMatch(/за уровнем/)
    expect(distHint(false).title).toMatch(/Расстояние/)
  })
  it('таймер называет таймфрейм', () => {
    expect(timerHint('5м').text).toContain('5м')
    expect(timerHint(null).text).not.toContain('undefined')
  })
  it('точки: выполнено из всех и список недостающего', () => {
    const h = dotsHint(1, 3, ['a', 'b'])
    expect(h.title).toBe('Условия входа: выполнено 1 из 3')
    expect(h.rows).toEqual([{ label: 'Ещё нужно', value: 'a; b' }])
    expect(dotsHint(3, 3, []).rows).toBeUndefined()
  })
  it('метки полосы: стоп, вход, тейк, цена', () => {
    expect(stripHints.take).toMatch(/фиксируем прибыль/)
    expect(Object.keys(stripHints)).toEqual(expect.arrayContaining(['stop', 'entry', 'take', 'now']))
  })
})

describe('planInfoHint', () => {
  it('сохраняет идею, риск, RR и срок плана с человеческими подписями', () => {
    const now = 1000
    const h = planInfoHint(plan({ ttl_at: now + 600, risk: 0.9, net_rr: 2.8 }), now)
    const labels = h.rows!.map((r) => r.label)
    expect(labels).toEqual(expect.arrayContaining(['Идея', 'Риск', 'Выигрыш к риску (RR)', 'План истекает через']))
    expect(h.rows!.find((r) => r.label === 'Идея')!.value).toBe('Откат к поддержке')
    expect(h.rows!.find((r) => r.label === 'Риск')!.value).toContain('0.90 USDT')
  })
  it('истёкший срок и пустые поля не попадают в строки', () => {
    const h = planInfoHint(plan({ ttl_at: 5, thesis: '', setup: null, risk: null, net_rr: null, entry_text: '', price: null }), 1000)
    expect(h.rows?.map((r) => r.label) ?? []).not.toContain('План истекает через')
    expect(h.rows?.map((r) => r.label) ?? []).not.toContain('Риск')
  })
})
