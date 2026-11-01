import { describe, expect, it } from 'vitest'
import { canApplyLevel, directionOf, levelLabel, levelName, riskArgs, riskEffect, riskLine, riskReflects, riskTitle } from './level'

describe('уровень риска', () => {
  it('имя берётся с сервера без скобок, иначе из таблицы', () => {
    expect(levelName(3, 'Базовый (как сейчас)')).toBe('Базовый')
    expect(levelName(5)).toBe('Агрессивный')
    expect(levelName(9)).toBe('уровень 9')
    expect(levelName(null)).toBe('не определён')
    expect(levelLabel(4)).toBe('4 · Активный')
  })
  it('направление и условие «Применить»', () => {
    expect(directionOf(3, 5)).toBe('up')
    expect(directionOf(3, 2)).toBe('down')
    expect(directionOf(3, 3)).toBe('same')
    expect(directionOf(null, 3)).toBe('same')
    expect(canApplyLevel(3, 5)).toBe(true)
    expect(canApplyLevel(3, 3)).toBe(false)
    expect(canApplyLevel(3, null)).toBe(false)
    expect(canApplyLevel(3, 9)).toBe(false)
    expect(canApplyLevel(null, 2)).toBe(true)
  })
  it('аргументы действия содержат только уровень (accepted добавляет движок)', () => {
    expect(riskArgs(5)).toEqual({ level: 5 })
  })
  it('«было → станет» и последствия словами, без кодов', () => {
    expect(riskLine(3, 5, 'Базовый (как сейчас)')).toBe('3 · Базовый → 5 · Агрессивный')
    expect(riskEffect(3, 5)).toMatch(/шире/)
    expect(riskEffect(3, 5)).toMatch(/Открытые позиции не меняются/)
    expect(riskEffect(5, 2)).toMatch(/уже/)
    expect(riskEffect(3, 3)).toBe('Уровень не меняется.')
    expect(riskTitle(4)).toBe('Уровень риска 4: Активный')
  })
  it('данные отражают смену, когда уровень совпал', () => {
    expect(riskReflects(5, 5)).toBe(true)
    expect(riskReflects(3, 5)).toBe(false)
    expect(riskReflects(null, 5)).toBe(false)
  })
})
