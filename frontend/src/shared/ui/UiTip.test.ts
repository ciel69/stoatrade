import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import UiTip from './UiTip.vue'

let w: VueWrapper | null = null
const mk = () => (w = mount(UiTip, { props: { tip: { title: 'Заголовок', text: 'Пояснение', rows: [{ label: 'Риск', value: '1 USDT' }] } }, slots: { default: 'чип' }, attachTo: document.body }))
const pop = () => document.body.querySelector('[role="tooltip"]')

afterEach(() => { w?.unmount(); w = null })

describe('UiTip', () => {
  it('закрыта по умолчанию, у кнопки нет aria-describedby', () => {
    const b = mk().find('button')
    expect(pop()).toBeNull()
    expect(b.attributes('aria-describedby')).toBeUndefined()
  })

  it('касание открывает, второе касание закрывает; роль tooltip связана через aria-describedby', async () => {
    const b = mk().find('button')
    await b.trigger('pointerdown', { pointerType: 'touch' })
    await b.trigger('click')
    await nextTick()
    const p = pop()!
    expect(p).not.toBeNull()
    expect(p.textContent).toContain('Заголовок')
    expect(p.textContent).toContain('Пояснение')
    expect(p.textContent).toContain('Риск')
    expect(b.attributes('aria-describedby')).toBe(p.id)
    await b.trigger('pointerdown', { pointerType: 'touch' })
    await b.trigger('click')
    await nextTick()
    expect(b.attributes('aria-describedby')).toBeUndefined()
  })

  it('Esc закрывает и не пускает событие дальше', async () => {
    const b = mk().find('button')
    await b.trigger('click')
    await nextTick()
    expect(pop()).not.toBeNull()
    let leaked = false
    const spy = () => { leaked = true }
    window.addEventListener('keydown', spy)
    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    window.removeEventListener('keydown', spy)
    await nextTick()
    expect(b.attributes('aria-expanded')).toBe('false')
    expect(leaked).toBe(false)
  })

  it('касание вне кнопки закрывает', async () => {
    const b = mk().find('button')
    await b.trigger('click')
    await nextTick()
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await nextTick()
    expect(b.attributes('aria-expanded')).toBe('false')
  })

  it('клик по подсказке не всплывает к строке списка', async () => {
    let rowClicks = 0
    const host = document.createElement('div')
    host.addEventListener('click', () => { rowClicks++ })
    document.body.appendChild(host)
    const m = mount(UiTip, { props: { tip: { text: 'x' } }, attachTo: host })
    await m.find('button').trigger('click')
    expect(rowClicks).toBe(0)
    m.unmount()
    host.remove()
  })

  it('class и style родителя попадают на кнопку (два корня: кнопка и Teleport)', () => {
    w = mount(UiTip, { props: { tip: { text: 'x' } }, attrs: { class: 'push', style: 'margin-left: auto' }, attachTo: document.body })
    const b = w.find('button')
    expect(b.classes()).toContain('push')
    expect(b.classes()).toContain('tt')
    expect(b.attributes('style')).toContain('margin-left')
  })

  it('aria-label берётся из label', () => {
    w = mount(UiTip, { props: { tip: { text: 'x' }, label: 'Условия входа: 1 из 3' }, attachTo: document.body })
    expect(w.find('button').attributes('aria-label')).toBe('Условия входа: 1 из 3')
  })
})
