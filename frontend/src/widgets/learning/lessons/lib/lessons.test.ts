import { describe, expect, it } from 'vitest'
import type { BestLesson, LessonsBlock } from '@/entities/learning'
import { bestNone, bestView, envText, funnelSteps, lessonsCount, statusRows, weekLabel, weekRows, weeksSummary } from './lessons'

const block = (o: Partial<LessonsBlock> = {}): LessonsBlock => ({
  id: 'lessons', title: 'Уроки', status: 'ok', note: '', env_norm: 'mainnet', n_lessons: 5, best_min_n: 30,
  funnel: { candidates: 6, hyp: 3, trials: 2, trials_closed: 1, confirmed: 1, rules: 1 },
  by_status: [{ status: 'hyp', brain: 1, labels: 1, total: 2 }, { status: 'confirmed', brain: 0, labels: 1, total: 1 }, { status: 'merged', brain: 0, labels: 0, total: 0 }],
  weeks: [{ week: '2026-W44', partial: false, created_brain: 1, created_labels: 0, confirmed: 0, rejected: 1 }, { week: '2026-W45', partial: true, created_brain: 0, created_labels: 2, confirmed: 1, rejected: 0 }],
  ...o,
})
const BEST: BestLesson = {
  lesson_id: 'L-0002', src: 'labels', status: 'hyp', n: 48, small: false, effect: 0.35, metric: 'mean_r', dir: 'lower', vs: 'base',
  diff: { v: -0.35, lo: -0.5914, hi: -0.1086, n: 48, lvl: 0.95 }, text: 'Входы на мем-монетах хуже обычного',
}

describe('уроки: воронка и статусы', () => {
  it('пять шагов с числами сервера; закрытые испытания подписаны', () => {
    const s = funnelSteps(block())
    expect(s.map((x) => [x.label, x.value])).toEqual([['Кандидаты', 6], ['Уроки', 3], ['Испытания', 2], ['Подтверждены', 1], ['Правила', 1]])
    expect(s[2].sub).toBe('закончено 1')
    expect(funnelSteps(block({ funnel: undefined })).every((x) => x.value === 0)).toBe(true)
  })
  it('статусы словами вкладки «Память», пустые статусы скрыты', () => {
    expect(statusRows(block()).map((r) => [r.word, r.brain, r.labels, r.total])).toEqual([['гипотеза', 1, 1, 2], ['подтверждён', 0, 1, 1]])
  })
  it('режим уроков меток словами и счётчик', () => {
    expect(envText('mainnet')).toContain('основной бирже')
    expect(envText('testnet')).toContain('тестовой бирже')
    expect(lessonsCount(5)).toBe('5 уроков')
    expect(lessonsCount(1)).toBe('1 урок')
  })
})

describe('уроки: лучший урок', () => {
  it('разница с интервалом 95% и n; без пометки при n ≥ 30', () => {
    const v = bestView(BEST)!
    expect(v.diff).toMatchObject({ value: '−0.35 R', interval: 'от −0.59 R до −0.11 R', n: 'n = 48' })
    expect(v.small).toBe('')
    expect(v.status).toBe('гипотеза')
    expect(v.src).toBe('из разметки')
    expect(v.tip.rows).toContainEqual({ label: 'Код', value: 'L-0002' })
  })
  it('пометка «мало данных», если сервер прислал small; нет урока — слова', () => {
    expect(bestView({ ...BEST, n: 12, small: true })!.small).toBe('мало данных (n < 30)')
    expect(bestView(null)).toBeNull()
    expect(bestNone(30)).toContain('30 наблюдений')
  })
})

describe('уроки: недели', () => {
  it('подпись недели, сумма новых, итог словами', () => {
    expect(weekLabel('2026-W44')).toBe('нед. 44')
    const rows = weekRows(block().weeks)
    expect(rows.map((r) => [r.label, r.created, r.partial])).toEqual([['нед. 44', 1, false], ['нед. 45', 2, true]])
    expect(weeksSummary(rows)).toBe('За 2 недели: новых 3, подтверждено 1, отклонено 1.')
    expect(weeksSummary([])).toBe('')
    expect(weekRows([{ week: '2025-W52', partial: false, created_brain: 0, created_labels: 0, confirmed: 0, rejected: 0 }, { week: '2026-W01', partial: true, created_brain: 0, created_labels: 0, confirmed: 0, rejected: 0 }])[0].label).toBe('нед. 52, 2025')
  })
})
