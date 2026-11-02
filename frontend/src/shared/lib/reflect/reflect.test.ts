import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, ref } from 'vue'
import { reflectText, useReflect } from './index'
import { useRefocus } from '../hooks/refocus'

describe('useReflect', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('ждёт, пока данные совпадут с ожидаемым, затем гаснет сама', async () => {
    const scope = effectScope()
    const level = ref(3)
    const r = scope.run(() => useReflect(1000, 500))!
    r.begin(() => level.value === 5)
    expect(r.state.value).toBe('wait')
    level.value = 5
    await nextTick()
    expect(r.state.value).toBe('seen')
    vi.advanceTimersByTime(600)
    expect(r.state.value).toBe('idle')
    scope.stop()
  })

  it('не дождались: late, а позднее совпадение всё равно отмечается', async () => {
    const scope = effectScope()
    const level = ref(3)
    const r = scope.run(() => useReflect(1000, 500))!
    r.begin(() => level.value === 5)
    vi.advanceTimersByTime(1100)
    expect(r.state.value).toBe('late')
    level.value = 5
    await nextTick()
    expect(r.state.value).toBe('seen')
    scope.stop()
  })

  it('уже совпавшее сразу считается отражённым; cancel сбрасывает', async () => {
    const scope = effectScope()
    const r = scope.run(() => useReflect(1000, 500))!
    r.begin(() => true)
    await nextTick()
    expect(r.state.value).toBe('seen')
    r.begin(() => false)
    r.cancel()
    expect(r.state.value).toBe('idle')
    scope.stop()
  })

  it('тексты состояний человеческие', () => {
    expect(reflectText('wait')).toMatch(/Ожидаем/)
    expect(reflectText('seen')).toMatch(/обновились/)
    expect(reflectText('late')).toMatch(/скоро/)
    expect(reflectText('idle')).toBe('')
  })
})

afterEach(() => { document.body.innerHTML = '' })

function el(tag: string, attrs: Record<string, string> = {}): HTMLElement {
  const e = document.createElement(tag)
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v)
  document.body.appendChild(e)
  return e
}

describe('useRefocus', () => {
  it('после ухода панели с кнопкой фокус уходит на заголовок результата, а не пропадает', async () => {
    const bar = el('div')
    const apply = document.createElement('button')
    bar.appendChild(apply)
    const heading = el('b', { tabindex: '-1' })
    const change = el('button')
    const rf = useRefocus({ blocked: () => false, target: () => heading ?? change, fallback: () => change, lostIn: () => bar })
    apply.focus()
    rf.mark()
    bar.remove()                        // панель изменений исчезла вместе с нажатой кнопкой
    expect(document.activeElement).toBe(document.body)
    rf.arm()
    await nextTick()
    await nextTick()
    expect(document.activeElement).toBe(heading)
  })

  it('пока открыто окно подтверждения, фокус не трогает; после закрытия возвращает на запасной элемент', async () => {
    const blocked = ref(true)
    const change = el('button')
    const rf = useRefocus({ blocked: () => blocked.value, target: () => null, fallback: () => change })
    rf.arm()
    await nextTick()
    expect(document.activeElement).toBe(document.body)
    blocked.value = false
    await new Promise((r) => setTimeout(r, 400))        // окно уходит плавно, фокус возвращается позже
    expect(document.activeElement).toBe(change)
  })

  it('не отнимает фокус у того, куда владелец успел перейти сам', async () => {
    const other = el('input')
    const heading = el('b', { tabindex: '-1' })
    const rf = useRefocus({ blocked: () => false, target: () => heading })
    other.focus()
    rf.arm()
    await nextTick()
    await nextTick()
    expect(document.activeElement).toBe(other)
  })
})
