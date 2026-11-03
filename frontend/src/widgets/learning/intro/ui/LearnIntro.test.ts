import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LearnIntro from './LearnIntro.vue'

describe('LearnIntro', () => {
  it('справка объясняет n, интервал и «данных мало», свёрнута по умолчанию', () => {
    const w = mount(LearnIntro)
    const t = w.text()
    expect(w.find('details').attributes('open')).toBeUndefined()
    expect(t).toContain('Интервал')
    expect(t).toContain('Данных мало')
    expect(w.findAll('dt').map((d) => d.text())).toEqual(['n', 'Интервал', 'Данных мало'])
    expect(t).not.toMatch(/\p{Extended_Pictographic}/u)
  })
})
