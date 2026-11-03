import { describe, expect, it } from 'vitest'
import { mockLearning } from '@/entities/learning'
import { stageBlocks } from './stages'

describe('заглушки этапов', () => {
  it('уроки и версии: этап словами и одна строка описания', () => {
    const d = mockLearning('mock', 'paper')
    const s = stageBlocks(d.order.map((id) => d.blocks[id]))
    expect(s.map((x) => x.id)).toEqual(['lessons', 'versions'])
    expect(s[0].stage).toBe('Появится на этапе 2')
    expect(s[1].title).toBe('Версии мозга до/после')
    expect(s.every((x) => x.about.length > 10 && !/R2\.6/.test(x.about))).toBe(true)
  })
  it('блок без номера этапа и неизвестный id получают запасные слова', () => {
    const [s] = stageBlocks([{ id: 'zzz', title: 'Z', status: 'stage', note: '' }])
    expect(s.stage).toBe('Появится позже')
    expect(s.about).toBeTruthy()
  })
})
