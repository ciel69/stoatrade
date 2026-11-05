import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { normalizeExits } from '../lib/format'
import type { ExitsPolicy } from './types'

export const EXITS_OP = 'exits_status'
export const EXITS_TOPIC = 'exits_status_detail'

/**
 * Состояние политики выхода (R4, только чтение). Ответ приходит разовым сообщением `exits_status_detail` только этому соединению,
 * поэтому запрос повторяется при заходе на страницу и при каждом возвращении связи. Таймеров нет.
 */
export const useExitsStore = defineStore('exits', () => {
  const { socket, link } = useLive()
  const policy = ref<ExitsPolicy | null>(null)
  /** Запрос отправлен, ответ ещё не пришёл. */
  const pending = ref(false)
  let started = false

  // Возвращение связи: запрашиваем состояние заново. Подписка одна, таймеров нет.
  watch(link, (state, prev) => {
    if (state === 'open' && prev !== 'open') request()
  })

  function apply(m: LiveMessage): void {
    if (m.topic !== EXITS_TOPIC) return
    const next = normalizeExits(m.data)
    if (!next) return
    policy.value = next
    pending.value = false
  }

  /** Запросить состояние. false: сокет не открыт; тогда запрос повторит watch при подключении. */
  function request(): boolean {
    const ok = useLive().socket.send({ op: EXITS_OP })
    if (ok) pending.value = true
    return ok
  }

  /** Подписка один раз; запрос — при заходе (если связь уже есть). Повторный заход обновляет состояние. */
  function start(): void {
    if (!started) {
      started = true
      socket.subscribe(EXITS_TOPIC, apply)
    }
    if (link.value === 'open' && !pending.value) request()
  }

  return { policy, pending, request, start, apply }
})
