import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import type { BusEvent, EventsSnapshot } from './types'

const SEEN_KEY = 'tb.events.seen'

function readSeen(): number {
  try {
    return Number(localStorage.getItem(SEEN_KEY)) || 0
  } catch {
    return 0
  }
}

export const useEventStore = defineStore('events', () => {
  const items = ref<BusEvent[]>([])
  const lastSeq = ref(0)
  const seen = ref(readSeen())
  let started = false

  /** Новых с момента, когда панель открывали в последний раз (хранится в браузере). Если seq шины сбросился (новая шина), считаем всё прочитанным. */
  const unread = computed(() => (lastSeq.value < seen.value ? 0 : items.value.filter((e) => e.seq > seen.value).length))

  function apply(m: LiveMessage): void {
    const d = m.data as EventsSnapshot
    items.value = d.items ?? []
    lastSeq.value = d.last_seq ?? 0
  }

  function markSeen(): void {
    seen.value = lastSeq.value
    try {
      localStorage.setItem(SEEN_KEY, String(seen.value))
    } catch {
      /* без хранилища счётчик сбросится при перезагрузке страницы */
    }
  }

  function start(): void {
    if (started) return
    started = true
    useLive().socket.subscribe('events', apply)
  }

  return { items, lastSeq, unread, apply, markSeen, start }
})
