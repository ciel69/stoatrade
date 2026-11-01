import { describe, expect, it } from 'vitest'
import type { ExchangeRow } from '../model/types'
import { activateArgs, activateBlock, activateEffect, activateLine, canSetMode, exchangeBlock, exchangeModeArgs, exchangeModeEffect, exchangeModeLine, exchangeReflects, keyNote } from './exchange'

const ex = (o: Partial<ExchangeRow> = {}): ExchangeRow => ({ id: 'bybit', name: 'Bybit', enabled: true, mode: 'paper', implemented: true, active: false, allowed: true, note: null, alive: null, creds: ['read'], max_open_positions: null, ...o })

describe('правка биржи', () => {
  it('менять можно только при остановленной платформе', () => {
    expect(exchangeBlock('stopped')).toBeNull()
    expect(exchangeBlock('running')).toMatch(/остановлена/)
    expect(exchangeBlock(null)).toMatch(/неизвестно/)
  })
  it('сделать основной нельзя: уже основная, заготовка, не разрешена', () => {
    expect(activateBlock(ex({ active: true }))).toMatch(/уже основная/)
    expect(activateBlock(ex({ implemented: false }))).toMatch(/адаптер/)
    expect(activateBlock(ex({ allowed: false }))).toMatch(/не разрешена/)
    expect(activateBlock(ex())).toBeNull()
  })
  it('аргументы действия точно по схеме сервера', () => {
    expect(activateArgs(ex({ id: 'okx' }))).toEqual({ op: 'activate', id: 'okx' })
    expect(exchangeModeArgs(ex(), 'testnet')).toEqual({ op: 'mode', id: 'bybit', mode: 'testnet' })
  })
  it('режим выбирается, только если допустим и отличается от текущего', () => {
    expect(canSetMode(ex({ mode: 'paper' }), 'testnet')).toBe(true)
    expect(canSetMode(ex({ mode: 'paper' }), 'paper')).toBe(false)
    expect(canSetMode(ex(), null)).toBe(false)
    expect(canSetMode(ex(), 'observe')).toBe(false)
  })
  it('нет ключа нужного вида: предупреждение (тестнет, live), для бумаги тихо', () => {
    expect(keyNote(ex({ creds: ['read'] }), 'testnet')).toMatch(/тестовой сети/)
    expect(keyNote(ex({ creds: ['read'] }), 'live')).toMatch(/торговли/)
    expect(keyNote(ex({ creds: ['read', 'trade'] }), 'live')).toBeNull()
    expect(keyNote(ex({ creds: [] }), 'paper')).toBeNull()
  })
  it('«было → станет» и последствия словами', () => {
    expect(activateLine(ex({ name: 'Bybit', active: true }), ex({ id: 'okx', name: 'OKX' }))).toBe('Bybit → OKX')
    expect(activateLine(null, ex())).toBe('основной биржи не было → Bybit')
    expect(activateEffect(ex({ name: 'Bybit' }), ex({ name: 'OKX', mode: 'live' }))).toMatch(/усиленным/)
    expect(exchangeModeLine(ex({ mode: 'paper' }), 'live')).toBe('бумага → живые деньги')
    expect(exchangeModeEffect(ex(), 'live')).toMatch(/реальные деньги/)
    expect(exchangeModeEffect(ex(), 'live')).toMatch(/подтверждение/)
    expect(exchangeModeEffect(ex(), 'testnet')).toMatch(/тестовая сеть/)
  })
  it('тема отразила изменение', () => {
    expect(exchangeReflects(ex({ active: true }), { active: true })).toBe(true)
    expect(exchangeReflects(ex({ active: false }), { active: true })).toBe(false)
    expect(exchangeReflects(ex({ mode: 'testnet' }), { mode: 'testnet' })).toBe(true)
    expect(exchangeReflects(ex({ mode: 'paper' }), { mode: 'testnet' })).toBe(false)
    expect(exchangeReflects(null, { mode: 'paper' })).toBe(false)
  })
})
