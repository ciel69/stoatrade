import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, ref } from 'vue'
import { reflectText, useReflect } from './index'

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
