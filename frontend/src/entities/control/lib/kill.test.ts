import { describe, expect, it } from 'vitest'
import { RELEASE_CHECKS, RELEASE_CHECKS_LIVE, RELEASE_COMMAND, killCleared, positionsLine, reasonLine, releaseNote, releaseWhat } from './kill'
import type { CtlSnapshot } from '../model/types'

describe('снятие аварийной остановки: тексты', () => {
  it('причина: пустая не выдумывается, точка в конце не двоится', () => {
    expect(reasonLine('ручная остановка')).toBe('Причина: ручная остановка.')
    expect(reasonLine('сбой.')).toBe('Причина: сбой.')
    expect(reasonLine('  ')).toMatch(/не указана/)
    expect(reasonLine(null)).toMatch(/не указана/)
  })
  it('позиции: без закрытия они под стопами, с закрытием ждём, пока уйдут', () => {
    expect(positionsLine(false, 2)).toBe('Позиции не закрывались: открыто 2 позиции, их защищают стопы на бирже.')
    expect(positionsLine(false, 5)).toMatch(/5 позиций/)
    expect(positionsLine(false, 1)).toMatch(/1 позиция/)
    expect(positionsLine(false, 0)).toMatch(/открытых сейчас нет/)
    expect(positionsLine(false, null)).toMatch(/неизвестно/)
    expect(positionsLine(true, 0)).toMatch(/закрыты по рынку/)
    expect(positionsLine(true, 3)).toMatch(/снять остановку нельзя/)
    expect(positionsLine(true, null)).toMatch(/неизвестно/)
  })
  it('нет кнопки: причина зависит от расширенного управления и флага, оба раза ведёт к команде', () => {
    expect(releaseNote(true, true)).toBeNull()
    expect(releaseNote(false, true)).toMatch(/Расширенное управление/)
    expect(releaseNote(true, false)).toMatch(/решает владелец/)
    expect(RELEASE_COMMAND).toBe('./tb exec resume')
  })
  it('перечень проверок словами, без служебных кодов', () => {
    for (const t of [...RELEASE_CHECKS, ...RELEASE_CHECKS_LIVE]) expect(t).not.toMatch(/\b[HKWP]\d\b|[A-Za-z_]{3,}/)
    expect(RELEASE_CHECKS.join(' ')).toMatch(/стоп на бирже/)
  })
  it('строка «что изменится» для live предупреждает про реальные деньги', () => {
    expect(releaseWhat('тест', false)).not.toMatch(/реальные деньги/)
    expect(releaseWhat('тест', true)).toMatch(/реальные деньги/)
  })
  it('снятие отражено, когда KILL в теме ctl исчез', () => {
    const base = { kill: { flatten: false, reason: 'x' } } as CtlSnapshot
    expect(killCleared(base)).toBe(false)
    expect(killCleared({ ...base, kill: null })).toBe(true)
    expect(killCleared(null)).toBe(false)
  })
})
