import { describe, expect, it } from 'vitest'
import { THEME_KEY, applyTheme, parseTheme, readTheme, saveTheme } from './theme'

function memory(initial: Record<string, string> = {}) {
  const m = new Map(Object.entries(initial))
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k), m }
}
const broken = { getItem: () => { throw new Error('denied') }, setItem: () => { throw new Error('denied') }, removeItem: () => { throw new Error('denied') } }

describe('тема оформления', () => {
  it('разбирает только известные значения', () => {
    expect(parseTheme('light')).toBe('light')
    expect(parseTheme('dark')).toBe('dark')
    expect(parseTheme('system')).toBe('system')
    expect(parseTheme('purple')).toBe('system')
    expect(parseTheme(null)).toBe('system')
  })

  it('читает сохранённое и переживает отказ хранилища', () => {
    expect(readTheme(memory({ [THEME_KEY]: 'dark' }))).toBe('dark')
    expect(readTheme(memory())).toBe('system')
    expect(readTheme(null)).toBe('system')
    expect(readTheme(broken)).toBe('system')
  })

  it('сохраняет светлую и тёмную, «как в системе» стирает запись', () => {
    const s = memory()
    expect(saveTheme('light', s)).toBe(true)
    expect(s.m.get(THEME_KEY)).toBe('light')
    expect(saveTheme('system', s)).toBe(true)
    expect(s.m.has(THEME_KEY)).toBe(false)
  })

  it('без хранилища не падает', () => {
    expect(saveTheme('dark', null)).toBe(false)
    expect(saveTheme('dark', broken)).toBe(false)
  })

  it('ставит и снимает data-theme на корне', () => {
    const root = document.createElement('html')
    applyTheme('dark', root)
    expect(root.getAttribute('data-theme')).toBe('dark')
    applyTheme('light', root)
    expect(root.getAttribute('data-theme')).toBe('light')
    applyTheme('system', root)
    expect(root.hasAttribute('data-theme')).toBe(false)
  })
})
