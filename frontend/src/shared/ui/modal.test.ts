import { describe, expect, it } from 'vitest'
import { wrapTarget } from './modal'

describe('wrapTarget: фокус не выходит из окна', () => {
  const els = ['a', 'b', 'c']
  it('Tab с последнего элемента ведёт на первый, Shift+Tab с первого на последний', () => {
    expect(wrapTarget(els, 'c', 'root', false)).toBe('a')
    expect(wrapTarget(els, 'a', 'root', true)).toBe('c')
  })
  it('в середине браузер решает сам', () => {
    expect(wrapTarget(els, 'b', 'root', false)).toBeNull()
    expect(wrapTarget(els, 'b', 'root', true)).toBeNull()
  })
  it('фокус на самой рамке или вне окна возвращается внутрь', () => {
    expect(wrapTarget(els, 'root', 'root', true)).toBe('c')
    expect(wrapTarget(els, 'root', 'root', false)).toBe('a')
    expect(wrapTarget(els, 'outside', 'root', false)).toBe('a')
  })
  it('без доступных элементов ничего не делает', () => {
    expect(wrapTarget([], 'x', 'root', false)).toBeNull()
  })
})
