import { describe, expect, it } from 'vitest'
import { DAY_STEP_NAMES, autoCloseText, checkCodeWord, closeFailReason, dayStateWord, dayStepAmber, dayStepDoes, dayStepLabel, decidedByWord, failReason, isCloseStep, ruleWord } from './index'

describe('слова страницы «День»', () => {
  it('у всех шагов S1–S9 и C1–C8 есть человеческое название и описание без служебных кодов', () => {
    expect(DAY_STEP_NAMES).toEqual(['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9', 'C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'C8'])
    for (const n of DAY_STEP_NAMES) {
      expect(dayStepLabel(n)).not.toMatch(/\b[SC]\d\b|[A-Z]_[A-Z]/)
      expect(dayStepDoes(n).length).toBeGreaterThan(10)
      expect(dayStepDoes(n)).not.toMatch(/\b[SC]\d\b|[A-Z]_[A-Z]/)
    }
    expect(isCloseStep('C6')).toBe(true)
    expect(isCloseStep('S6')).toBe(false)
  })

  it('неизвестный шаг не показывает сырое имя', () => {
    expect(dayStepLabel('X9')).toBe('Шаг дня')
    expect(dayStepDoes('X9')).toBe('')
    expect(dayStepAmber('C1')).toBe('шаг выполнен с замечанием')
    expect(dayStepAmber('C4')).toMatch(/мозг не ответил/)
  })

  it('причины отказа и незавершённого закрытия без кодов', () => {
    expect(failReason('E_GATE')).toMatch(/условие/)
    expect(failReason('E_AMBER')).toMatch(/предупреждения/)
    expect(failReason('E_WTF')).toBe('шаг не завершился')
    expect(failReason(null)).toBe('шаг не завершился')
    expect(closeFailReason('exec_down')).toBe('исполнитель не отвечает')
    expect(closeFailReason('zzz')).toBe('закрытие не подтверждено')
  })

  it('коды проверки на перенос и правила', () => {
    expect(checkCodeWord('K2_RISK')).toMatch(/риск/)
    expect(checkCodeWord('K_FAILED:K1_STOP')).toMatch(/перед переносом не сошлось: нет надёжного стопа/)
    expect(checkCodeWord('NEW_CODE')).toBe('причина не расшифрована')
    expect(ruleWord('A7')).toMatch(/проверку/)
    expect(ruleWord('Z9')).toBe('правило закрытия')
  })

  it('состояние, автозакрытие и «кто решил»', () => {
    expect(dayStateWord('running')).toBe('идёт')
    expect(dayStateWord('???')).toBe('состояние неизвестно')
    expect(autoCloseText('max_day_hours 24')).toBe('по расписанию или условию платформы')
    expect(autoCloseText('закончилась сессия')).toBe('закончилась сессия')
    expect(decidedByWord('owner')).toBe('вы')
    expect(decidedByWord('x')).toBe('')
  })
})
