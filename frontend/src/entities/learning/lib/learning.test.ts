import { describe, expect, it } from 'vitest'
import { COMPUTING_GIVE_UP_S, blockView, estView, isStale, learningState, normalizeLearning, updatedText, verdictView } from './learning'
import { LEARNING_MOCKS, isLearningMock, mockLearning, type LearningMock } from './mock'
import type { Est, LearningBlock } from '../model/types'

const est = (v: number | null, lo: number | null, hi: number | null, n: number): Est => ({ v, lo, hi, n, lvl: 0.95 })
const block = (o: Partial<LearningBlock>): LearningBlock => ({ id: 'x', title: 'X', status: 'ok', note: '', ...o })

describe('приведение ответа', () => {
  it('все образцы проходят приведение и сохраняют порядок блоков', () => {
    for (const k of LEARNING_MOCKS) {
      const d = normalizeLearning(mockLearning(k as LearningMock, 'paper'))
      expect(d?.ok, k).toBe(true)
      expect(d?.order).toEqual(['result', 'vs_policy', 'discipline', 'skips', 'exits', 'economics', 'memory', 'lessons', 'versions', 'hyps'])
      expect(Object.keys(d!.blocks).sort()).toEqual([...d!.order].sort())
    }
  })
  it('не объект даёт null; ok:false и чужая схема дают отчёт «не удалось собрать»', () => {
    expect(normalizeLearning(null)).toBeNull()
    expect(normalizeLearning('x')).toBeNull()
    expect(normalizeLearning([])).toBeNull()
    const f = normalizeLearning({ ok: false, mode: 'live', error: 'failed' })
    expect(f).toMatchObject({ ok: false, mode: 'live', error: 'failed' })
    expect(normalizeLearning({ ok: true, mode: 'paper', schema: 'tb.learning/9' })).toMatchObject({ ok: false, error: 'schema' })
    expect(normalizeLearning({ ok: false }, 'testnet')?.mode).toBe('testnet')
  })
  it('неизвестный статус блока становится ошибкой блока, остальные целы', () => {
    const raw = mockLearning('mock', 'paper') as unknown as Record<string, unknown>
    const blocks = { ...(raw.blocks as Record<string, unknown>), result: { id: 'result', title: 'Результат', status: 'загадка', note: 'x' } }
    const d = normalizeLearning({ ...raw, blocks })!
    expect(d.blocks.result.status).toBe('error')
    expect(blockView(d.blocks.result).message).toBe('Не удалось показать блок')
    expect(d.blocks.skips.status).toBe('ok')
  })
  it('блок без заголовка получает запасной; блок вне order дописывается в конец', () => {
    const raw = { schema: 'tb.learning/1', ok: true, mode: 'paper', verdict: {}, order: ['a'], blocks: { a: { status: 'ok' }, result: { status: 'no_data', reason: 'no_trades' } } }
    const d = normalizeLearning(raw)!
    expect(d.order).toEqual(['a', 'result'])
    expect(d.blocks.result.title).toBe('Результат')
    expect(d.blocks.a.title).toBe('a')
  })
  it('неполный вердикт не ломает страницу', () => {
    const d = normalizeLearning({ schema: 'tb.learning/1', ok: true, mode: 'paper', verdict: { state: 'странное', trend: 7 }, blocks: {} })!
    expect(d.verdict.state).toBe('few_data')
    expect(d.verdict.trend).toBeNull()
    expect(d.verdict.n).toBe(0)
  })
  it('isLearningMock узнаёт только пять образцов', () => {
    expect(['mock', 'mock-few', 'mock-confirmed', 'mock-empty', 'mock-computing'].every(isLearningMock)).toBe(true)
    expect(isLearningMock('mock-x')).toBe(false)
    expect(isLearningMock(null)).toBe(false)
  })
})

describe('оценки словами', () => {
  it('R со знаком, интервал и n', () => {
    const v = estView(est(-0.15, -0.51, 0.21, 38), 'r')
    expect(v).toMatchObject({ value: '−0.15 R', interval: 'от −0.51 до +0.21', n: 'n = 38', small: '', tone: 'neg' })
  })
  it('мало данных: подпись при n < 30, интервала нет — строки нет', () => {
    const v = estView(est(0.5, null, null, 1), 'pct')
    expect(v.value).toBe('50%')
    expect(v.interval).toBe('')
    expect(v.small).toBe('мало данных (n < 30)')
    expect(estView(est(0.1, 0, 0.2, 30), 'r').small).toBe('')
  })
  it('нет оценки: прочерк без пометок', () => {
    expect(estView(null)).toEqual({ value: '–', interval: '', n: '', small: '', tone: '' })
    expect(estView(est(null, null, null, 0)).value).toBe('–')
  })
})

describe('состояние блока', () => {
  it('ok и few_data показывают данные, few помечен', () => {
    expect(blockView(block({ status: 'ok' }))).toMatchObject({ kind: 'data', few: false })
    expect(blockView(block({ status: 'few_data' }))).toMatchObject({ kind: 'data', few: true })
  })
  it('заглушка этапа, пусто с причиной словами, ошибка', () => {
    expect(blockView(block({ status: 'stage', stage: 2 })).message).toBe('Появится на этапе 2')
    expect(blockView(block({ status: 'no_data', reason: 'labels_off' })).message).toContain('сервис labels')
    expect(blockView(block({ status: 'no_data', reason: 'код_которого_нет' })).message).toBe('Данных пока нет')
    expect(blockView(block({ status: 'error', reason: 'E_BLOCK' })).message).toBe('Не удалось показать блок')
  })
  it('«Считается…» через 35 с сдаётся', () => {
    expect(blockView(block({ status: 'computing' }), 10)).toMatchObject({ kind: 'computing', message: 'Считается…' })
    expect(blockView(block({ status: 'computing' }), COMPUTING_GIVE_UP_S + 1)).toMatchObject({ kind: 'gave_up', message: 'Не удалось дождаться, обновите страницу' })
  })
})

describe('вердикт', () => {
  it('три слова', () => {
    const w = (k: LearningMock) => verdictView(mockLearning(k, 'paper').verdict).word
    expect(w('mock-few')).toBe('Данных мало')
    expect(w('mock')).toBe('Разницы не видно')
    expect(w('mock-confirmed')).toBe('Улучшение подтверждено')
  })
  it('число сделок со склонением и разность с интервалом', () => {
    const v = verdictView(mockLearning('mock-confirmed', 'paper').verdict)
    expect(v.n).toBe('121 сделка')
    expect(v.diff).toContain('+0.34 R')
    expect(v.diff).toContain('от +0.07 до +0.61')
    expect(verdictView({ ...mockLearning('mock', 'paper').verdict, n: 2 }).n).toBe('2 сделки')
    expect(verdictView({ ...mockLearning('mock', 'paper').verdict, n: 12 }).n).toBe('12 сделок')
  })
  it('предупреждение «стало хуже» только при trend=down; без diff_r разности нет', () => {
    const base = mockLearning('mock', 'paper').verdict
    expect(verdictView({ ...base, trend: 'down' }).warn).toContain('Стало хуже')
    expect(verdictView({ ...base, trend: 'flat' }).warn).toBe('')
    expect(verdictView({ ...base, diff_r: null }).diff).toBe('')
  })
  it('без текста сервера берётся запасная фраза; причина словами', () => {
    const base = mockLearning('mock', 'paper').verdict
    expect(verdictView({ ...base, text: '' }).text.length).toBeGreaterThan(10)
    expect(verdictView({ ...base, reason: 'guard_viol' }).reason).toContain('нарушений')
  })
})

describe('состояния страницы и возраст', () => {
  it('нет связи, загрузка, сбой, данные; старый ответ остаётся при обрыве связи', () => {
    const ok = normalizeLearning(mockLearning('mock', 'paper'))
    const bad = normalizeLearning({ ok: false, mode: 'paper' })
    expect(learningState(null, true, false)).toBe('link')
    expect(learningState(null, true, true)).toBe('loading')
    expect(learningState(ok, true, true)).toBe('loading')
    expect(learningState(bad, false, true)).toBe('failed')
    expect(learningState(ok, false, false)).toBe('ok')
  })
  it('обновлено N мин назад и устаревание', () => {
    expect(updatedText(1000, 1000 + 180)).toBe('обновлено 3 мин назад')
    expect(updatedText(null, 5)).toBe('')
    expect(isStale(1000, 1000 + 301)).toBe(true)
    expect(isStale(1000, 1000 + 60)).toBe(false)
  })
})
