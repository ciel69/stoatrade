import { describe, expect, it } from 'vitest'
import { categoryName, changeLines, crossesMidnight, draftErrors, draftOf, effectText, patchOf, quietHint, quietNow, sameSettings, statsText } from './notify'
import { mockApplied, mockNotify } from './mock'

const base = () => mockNotify('mock').settings

describe('notify', () => {
  it('названия категорий человеческие, неизвестная берёт подпись сервера, потом id', () => {
    expect(categoryName('day_report')).toBe('Итог дня готов')
    expect(categoryName('new_one', 'Новая')).toBe('Новая')
    expect(categoryName('new_one')).toBe('new_one')
    for (const c of mockNotify('mock').categories) expect(categoryName(c.id, c.label)).not.toMatch(/[_/]/)
  })
  it('черновик это копия: правка не меняет снимок', () => {
    const b = base()
    const d = draftOf(b)
    d.categories.day_start = !d.categories.day_start
    d.quiet.bypass.push('live_gate')
    expect(b.categories.day_start).not.toBe(d.categories.day_start)
    expect(b.quiet.bypass).not.toContain('live_gate')
    expect(sameSettings(b, draftOf(b))).toBe(true)
    expect(sameSettings(b, d)).toBe(false)
  })
  it('patchOf отправляет только изменённое и молчит, когда менять нечего', () => {
    const b = base()
    expect(patchOf(b, draftOf(b))).toBeNull()
    const d = draftOf(b)
    d.master = false
    d.categories.day_start = false
    d.quiet.to = '08:00'
    d.limits.per_hour = 3
    expect(patchOf(b, d)).toEqual({ master: false, categories: { day_start: false }, quiet: { to: '08:00' }, limits: { per_hour: 3 } })
  })
  it('bypass сравнивается без учёта порядка', () => {
    const b = base()
    const d = draftOf(b)
    d.quiet.bypass = [...d.quiet.bypass].reverse()
    expect(patchOf(b, d)).toBeNull()
    d.quiet.bypass = ['live_gate']
    expect(patchOf(b, d)?.quiet?.bypass).toEqual(['live_gate'])
  })
  it('ошибки ввода: время и лимиты, при ошибке патча нет', () => {
    const d = draftOf(base())
    d.quiet.from = '25:00'
    d.limits.per_hour = 0
    d.limits.same_kind_min = null
    const e = draftErrors(d)
    expect(Object.keys(e).sort()).toEqual(['from', 'per_hour', 'same_kind_min'])
    expect(e.from).toMatch(/время/)
    expect(patchOf(base(), d)).toBeNull()
    d.limits.same_kind_min = 1.5
    expect(draftErrors(d).same_kind_min).toBeTruthy()
  })
  it('«было → станет» человеческими словами, без кодов', () => {
    const b = base()
    const d = draftOf(b)
    d.master = false
    d.categories.position_closed = false
    d.quiet.bypass = ['emergency', 'live_gate']
    d.quiet.tz = 'utc'
    d.limits.same_kind_min = 0
    const lines = changeLines(b, d, mockNotify('mock').categories)
    const text = lines.map((l) => `${l.label}: ${l.from} -> ${l.to}`).join('\n')
    expect(text).toContain('Показывать уведомления: включено -> выключено')
    expect(text).toContain('Позиция закрыта по стопу или тейку: включено -> выключено')
    expect(text).toContain('Запуск на реальные деньги: в тихие часы молчит -> и в тихие часы показывается')
    expect(text).toContain('Сбой платформы или связи: и в тихие часы показывается -> в тихие часы молчит')
    expect(text).toContain('по местному времени -> по UTC')
    expect(text).toContain('без паузы')
    expect(text).not.toMatch(/live_gate|service_down|master|bypass/)
  })
  it('последствия и тихие часы', () => {
    const b = base()
    const d = draftOf(b)
    d.master = false
    expect(effectText(b, d)).toMatch(/перестанут/)
    expect(effectText({ ...b, master: false }, { ...d, master: true })).toMatch(/начнут/)
    expect(crossesMidnight('23:00', '07:00')).toBe(true)
    expect(crossesMidnight('09:00', '17:00')).toBe(false)
    expect(quietHint('23:00', '07:00')).toMatch(/следующего дня/)
    expect(quietHint('10:00', '10:00')).toMatch(/совпадают/)
    expect(quietHint('xx', '10:00')).toMatch(/Укажите/)
    expect(quietNow(false, 5)).toBe('')
    expect(quietNow(true, 3)).toContain('накоплено 3')
    expect(statsText({ sent: 1, merged: 2, limited: 3, failed: 4 })).toHaveLength(4)
  })
  it('образцы: пустой без своих настроек, тихий с накоплением; примененный снимок отражает черновик', () => {
    expect(mockNotify('mock-empty').source).toBe('config')
    expect(mockNotify('mock-quiet')).toMatchObject({ quiet_now: true, held_n: 3 })
    const s = mockNotify('mock-empty')
    const d = draftOf(s.settings)
    d.master = true
    d.categories.day_start = true
    const after = mockApplied(s, d)
    expect(after.source).toBe('file')
    expect(sameSettings(after.settings, d)).toBe(true)
    expect(after.categories.find((c) => c.id === 'day_start')?.on).toBe(true)
  })
})
