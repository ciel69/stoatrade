import { onScopeDispose, ref, watchEffect } from 'vue'

export type ReflectState = 'idle' | 'wait' | 'seen' | 'late'

/** Сколько ждём, пока тема отразит принятое действие: темы обновляются до 15 с, дольше 25 с не держим «ожидаем». */
export const REFLECT_MS = 25_000
/** Подтверждённое «данные обновились» показываем недолго и убираем. */
const SEEN_MS = 4_000

export function reflectText(s: ReflectState): string {
  return s === 'seen' ? 'Данные обновились.' : s === 'late' ? 'Данные обновятся скоро.' : s === 'wait' ? 'Ожидаем обновления данных…' : ''
}

/**
 * Ожидание «данные отразили принятое действие». begin(проверка) запускает ожидание: проверка читает реактивные данные,
 * как только она вернула true, состояние seen (через несколько секунд уходит в idle); не дождались за ms: late.
 */
export function useReflect(ms = REFLECT_MS, seenMs = SEEN_MS) {
  const state = ref<ReflectState>('idle')
  let check: (() => boolean) | null = null
  let lateTimer: ReturnType<typeof setTimeout> | undefined
  let seenTimer: ReturnType<typeof setTimeout> | undefined

  function clear(): void {
    clearTimeout(lateTimer)
    clearTimeout(seenTimer)
  }
  function seen(): void {
    clear()
    check = null
    state.value = 'seen'
    seenTimer = setTimeout(() => { if (state.value === 'seen') state.value = 'idle' }, seenMs)
  }
  function begin(isReflected: () => boolean): void {
    clear()
    check = isReflected
    state.value = 'wait'
    lateTimer = setTimeout(() => { if (state.value === 'wait') state.value = 'late' }, ms)
  }
  function cancel(): void {
    clear()
    check = null
    state.value = 'idle'
  }

  watchEffect(() => {
    if ((state.value === 'wait' || state.value === 'late') && check && check()) seen()
  })
  onScopeDispose(clear)

  return { state, begin, cancel }
}
