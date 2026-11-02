import { onUnmounted, ref, watch, type Ref } from 'vue'
import type { BrainStatus } from '../model/types'
import { pickQuip, quietDelay, quipDelay } from './mood'

/**
 * Облако мыслей: текущая реплика настроения или null (облака нет). Состояние приходит из WebSocket-данных, сама смена идёт локальными таймерами.
 * Пока paused (курсор или фокус на мозге и облаке) реплика не меняется. У «отдыхает» облако появляется изредка и прячется, у «не отвечает» его нет.
 * Пока вкладка скрыта, замена откладывается, а при возвращении идёт обычным темпом.
 */
export function useQuip(status: Ref<BrainStatus>, enabled: Ref<boolean>, paused: Ref<boolean>) {
  const quip = ref<string | null>(null)
  let timer: ReturnType<typeof setTimeout> | undefined
  const later = (fn: () => void, ms: number): void => { clearTimeout(timer); timer = setTimeout(fn, ms) }

  function tick(): void {
    if (!enabled.value || status.value === 'down') { quip.value = null; return }
    if (paused.value || document.hidden) { later(tick, 800); return }
    if (status.value === 'idle') {
      if (quip.value) { quip.value = null; later(tick, quietDelay()) } else { quip.value = pickQuip('idle', null); later(tick, quipDelay('idle')) }
      return
    }
    quip.value = pickQuip(status.value, quip.value)
    later(tick, quipDelay(status.value))
  }
  watch([status, enabled], () => {
    clearTimeout(timer)
    if (!enabled.value || status.value === 'down') { quip.value = null; return }
    if (status.value === 'idle') { quip.value = null; later(tick, 2500); return }
    quip.value = pickQuip(status.value, quip.value)
    later(tick, quipDelay(status.value))
  }, { immediate: true })
  onUnmounted(() => clearTimeout(timer))
  return quip
}
