import { describe, expect, it } from 'vitest'
import { mockLearning, type Est, type MemoryBlock } from '@/entities/learning'
import { memoryVerdict, memoryView } from './memory'

const est = (v: number, lo: number | null, hi: number | null, n = 52): Est => ({ v, lo, hi, n, lvl: 0.95 })
const mock = (kind: 'mock' | 'mock-few' = 'mock'): MemoryBlock => mockLearning(kind, 'paper').blocks.memory as MemoryBlock

describe('вывод по памяти', () => {
  it('мало оценок -> few, интервал выше нуля -> better, ниже -> worse, через нуль -> same', () => {
    expect(memoryVerdict({ n_eval: 10, d_brier: est(0.1, 0.05, 0.2) })).toBe('few')
    expect(memoryVerdict({ n_eval: 52, d_brier: est(0.05, 0.01, 0.09) })).toBe('better')
    expect(memoryVerdict({ n_eval: 52, d_brier: est(-0.05, -0.09, -0.01) })).toBe('worse')
    expect(memoryVerdict({ n_eval: 52, d_brier: est(-0.02, -0.06, 0.02) })).toBe('same')
    expect(memoryVerdict({ n_eval: 52, d_brier: est(0.02, null, null) })).toBe('no_interval')
    expect(memoryVerdict({ n_eval: 52, d_brier: null })).toBe('none')
  })
})

describe('memoryView', () => {
  it('образец: числа, интервал разности, покрытие, заглушка истории', () => {
    const v = memoryView(mock())
    expect(v.bars.map((b) => b.text)).toEqual(['0.292', '0.270'])
    expect(v.bars[0]!.ratio).toBeGreaterThan(v.bars[1]!.ratio)
    expect(v.diff.value).toBe('−0.022')
    expect(v.diff.interval).toBe('от −0.082 до +0.038')
    expect(v.diff.level).toBe('уровень 95%')
    expect(v.cover.value).toBe('62%')
    expect(v.history).toBe('Появится на этапе 3')
    expect(v.small).toBe('')
    expect(v.verdict).toBe('same')
  })
  it('мало данных: пометка и вывод «рано»', () => {
    const v = memoryView(mock('mock-few'))
    expect(v.small).toBe('мало данных (n < 30)')
    expect(v.verdict).toBe('few')
  })
  it('пустые числа не ломают отображение', () => {
    const b = { ...mock(), brier_mem: null, brier_base: null, d_brier: null, covered: null, acc_mem: null, acc_base: null, history: null } as MemoryBlock
    const v = memoryView(b)
    expect(v.bars.map((x) => x.text)).toEqual(['–', '–'])
    expect(v.diff.value).toBe('–')
    expect(v.cover.value).toBe('–')
    expect(v.accuracy).toBe('–')
    expect(v.history).toBe('Появится на этапе 3')
  })
})
