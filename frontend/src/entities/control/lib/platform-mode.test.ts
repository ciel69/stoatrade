import { describe, expect, it } from 'vitest'
import { modeArgs, modeBlock, modeEffect, modeLine, modeReflects, modeTitle, saferModes } from './platform-mode'

describe('режим платформы', () => {
  it('предлагаются только более безопасные режимы', () => {
    expect(saferModes('live')).toEqual(['observe', 'paper', 'testnet'])
    expect(saferModes('testnet')).toEqual(['observe', 'paper'])
    expect(saferModes('paper')).toEqual(['observe'])
    expect(saferModes('observe')).toEqual([])
    expect(saferModes(null)).toEqual([])
    expect(saferModes('что-то')).toEqual([])
  })
  it('сменить можно только пока день идёт и есть куда снижаться', () => {
    expect(modeBlock('stopped', 'paper')).toMatch(/пока идёт торговый день/)
    expect(modeBlock('starting', 'paper')).not.toBeNull()
    expect(modeBlock('running', 'observe')).toMatch(/самом безопасном/)
    expect(modeBlock('running', 'paper')).toBeNull()
  })
  it('аргументы, «было → станет» и последствия без служебных слов', () => {
    expect(modeArgs('paper')).toEqual({ mode: 'paper' })
    expect(modeLine('testnet', 'paper')).toBe('тестовая биржа → бумажная торговля')
    expect(modeTitle('observe')).toBe('Режим платформы: наблюдение')
    expect(modeEffect('observe')).toMatch(/только наблюдает/)
    expect(modeEffect('paper')).toMatch(/нельзя/)
    expect(modeEffect('paper')).not.toMatch(/mode|observe|SAFER/)
    expect(modeReflects('paper', 'paper')).toBe(true)
    expect(modeReflects('testnet', 'paper')).toBe(false)
  })
})
