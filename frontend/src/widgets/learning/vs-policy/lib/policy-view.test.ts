import { describe, expect, it } from 'vitest'
import { mockLearning, type VsPolicyBlock } from '@/entities/learning'
import { policyTableView } from './policy-view'

const block = (): VsPolicyBlock => mockLearning('mock', 'paper').blocks.vs_policy as VsPolicyBlock

describe('таблица политик', () => {
  it('строки несут n, R с интервалом, разность и слова вывода без сырых кодов', () => {
    const t = policyTableView(block())
    expect(t.rows).toHaveLength(2)
    const p6 = t.rows[0]
    expect(p6.name).toBe('random side baseline')
    expect(p6.tag).toBe('P6, версия 1')
    expect(p6.baseline).toBe(true)
    expect(p6.filled).toBe('410')
    expect(p6.r.interval).toContain('от')
    expect(p6.diff.value).toMatch(/^\+/)
    expect(p6.verdict).toBe('не ясно')
    expect(p6.p).toBe('p = 0.31')
    expect(t.rows.map((r) => r.verdict).join(' ')).not.toMatch(/few_data|unclear|better|worse/)
  })
  it('версия контекста менялась: пометка, у политики без разности прочерк', () => {
    const p5 = policyTableView(block()).rows[1]
    expect(p5.ctxChanged).toBe(true)
    expect(p5.diff.value).toBe('–')
    expect(p5.p).toBe('–')
    expect(p5.verdict).toBe('данных мало')
    expect(p5.tone).toBe('warn')
  })
  it('поправка Бонферрони и оговорки словами', () => {
    const t = policyTableView(block())
    expect(t.correction).toContain('на 2 политики')
    expect(t.correction).toContain('0.025')
    expect(t.caveats.length).toBe(4)
    expect(t.caveats.join(' ')).not.toMatch(/unpaired|costs_model/)
    expect(t.summary).toBe('Разницы с простыми правилами не видно')
  })
  it('мозг: n и R после издержек; без политик поправки нет', () => {
    const b = { ...block(), rows: [], k: 0, alpha_adj: null }
    const t = policyTableView(b)
    expect(t.brain?.n).toBe('38')
    expect(t.correction).toBe('')
    expect(t.rows).toEqual([])
  })
  it('итог: выше, ниже, мало данных', () => {
    expect(policyTableView({ ...block(), summary: 'above_baseline' }).summaryTone).toBe('ok')
    expect(policyTableView({ ...block(), summary: 'below_baseline' }).summaryTone).toBe('bad')
    expect(policyTableView({ ...block(), summary: 'few_data' }).summaryTone).toBe('warn')
  })
})
