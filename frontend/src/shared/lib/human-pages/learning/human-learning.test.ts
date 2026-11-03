import { describe, expect, it } from 'vitest'
import {
  LEARN_BLOCK_TITLE, LEARN_CAVEAT, LEARN_POLICY_SUMMARY, LEARN_POLICY_VERDICT, LEARN_REASON, LEARN_REASON_FALLBACK, LEARN_SIGNAL, LEARN_SMALL, LEARN_STAGE,
  LEARN_TREND_DOWN, LEARN_VERDICT_ABOUT, LEARN_VERDICT_WORD, learnLevel, learnReason,
} from './index'

const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u
const RAW_CODE = /\b[a-z]+_[a-z_]+\b|\bE_[A-Z]+\b/

function allTexts(): string[] {
  return [
    ...Object.values(LEARN_VERDICT_WORD), ...Object.values(LEARN_VERDICT_ABOUT), LEARN_TREND_DOWN, ...Object.values(LEARN_BLOCK_TITLE), ...Object.values(LEARN_REASON),
    LEARN_REASON_FALLBACK, ...Object.values(LEARN_POLICY_VERDICT), ...Object.values(LEARN_POLICY_SUMMARY), ...Object.values(LEARN_CAVEAT), ...Object.values(LEARN_SIGNAL),
    LEARN_SMALL(), LEARN_STAGE(2),
  ]
}

describe('слова страницы «Учится ли мозг»', () => {
  it('три слова вердикта по требованию', () => {
    expect(LEARN_VERDICT_WORD).toEqual({ few_data: 'Данных мало', no_difference: 'Разницы не видно', confirmed: 'Улучшение подтверждено' })
  })
  it('у каждого кода причины из таблицы спеки есть фраза', () => {
    const codes = ['n_lt_min', 'no_baseline', 'not_significant', 'guard_viol', 'guard_trades', 'no_trades', 'no_cases', 'no_usage', 'no_path', 'no_whatif', 'mode_unknown', 'labels_off', 'too_many_for_request', 'E_BLOCK', 'E_REGISTRY']
    for (const c of codes) expect(LEARN_REASON[c], c).toMatch(/^[А-ЯЁA-Z]/)
  })
  it('неизвестный и пустой код получают общую фразу, а не сырой код', () => {
    expect(learnReason('что_то_новое')).toBe(LEARN_REASON_FALLBACK)
    expect(learnReason(null)).toBe(LEARN_REASON_FALLBACK)
    expect(learnReason('no_baseline')).toContain('нужно 60')
  })
  it('нет эмодзи и сырых кодов', () => {
    for (const t of allTexts()) {
      expect(t, t).not.toMatch(EMOJI)
      expect(t, t).not.toMatch(RAW_CODE)
    }
  })
  it('заголовки блоков в порядке требований', () => {
    expect(Object.values(LEARN_BLOCK_TITLE)).toEqual(['Результат', 'Мозг против теневой политики', 'Дисциплина', 'Пропуски', 'Выходы', 'Экономика', 'Память против базы', 'Уроки', 'Версии мозга до/после', 'Гипотезы'])
  })
  it('этап, мало данных, уровень интервала', () => {
    expect(LEARN_STAGE(2)).toBe('Появится на этапе 2')
    expect(LEARN_STAGE(null)).toBe('Появится позже')
    expect(LEARN_SMALL()).toBe('мало данных (n < 30)')
    expect(learnLevel(0.95)).toBe('95%')
    expect(learnLevel(null)).toBe('')
  })
})
