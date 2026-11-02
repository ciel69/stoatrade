import { describe, expect, it } from 'vitest'
import { LIVE_CONFIRM_TITLE, LIVE_GATE_WORD, LIVE_GATES_NOTE, LIVE_INTRO, LIVE_RISK, LIVE_TITLE, LIVE_WARN_NOTE, LIVE_WHAT, liveLimitLines, liveRefusal, liveWhyText } from '.'

describe('слова запуска на реальные деньги', () => {
  it('все тексты простым русским: без служебных кодов и имён настроек', () => {
    const all = [LIVE_INTRO, LIVE_RISK, LIVE_WHAT, LIVE_TITLE, LIVE_CONFIRM_TITLE, LIVE_GATES_NOTE, LIVE_WARN_NOTE, ...Object.values(LIVE_GATE_WORD), ...(['link', 'down', 'full', 'off'] as const).map(liveWhyText)]
    for (const t of all) expect(t).not.toMatch(/\bE_[A-Z]|\b[HW]\d\b|allow_|dash2|_/)
  })
  it('заголовок окна подтверждения отличается от обычного и называет реальные деньги', () => {
    expect(LIVE_CONFIRM_TITLE).toMatch(/реальные деньги/)
    expect(LIVE_CONFIRM_TITLE).not.toBe(LIVE_TITLE)
    expect(LIVE_INTRO).toMatch(/потери реальны/)
  })
  it('слова статусов', () => {
    expect(LIVE_GATE_WORD).toEqual({ ok: 'готово', bad: 'не готово', later: 'проверится при запуске' })
  })
  it('отказ платформы: выключатель и расширенный режим говорят прямо, остальное отдаётся общему слою', () => {
    expect(liveRefusal('E_DISABLED')).toBe(liveWhyText('off'))
    expect(liveRefusal('E_ELEVATE')).toBe(liveWhyText('full'))
    expect(liveRefusal('E_GATE')).toBeNull()
    expect(liveRefusal(undefined)).toBeNull()
  })
  it('пределы: цифры только из фраз платформы', () => {
    expect(liveLimitLines({ H5: 'убыток за сутки 0.00 из 3.00 USDT', H8: 'запусков за сутки 0 из 3' })).toEqual(['Дневной лимит убытка: 3.00 USDT, сегодня уже 0.00.', 'Запусков на реальные деньги сегодня: 0 из 3.'])
    expect(liveLimitLines({ H5: 'убыток -1.5 из 3 USDT' })).toEqual([])
    expect(liveLimitLines({ H5: null, H8: undefined })).toEqual([])
    expect(liveLimitLines({ H5: 'что-то другое', H8: 'и это' })).toEqual([])
  })
})
