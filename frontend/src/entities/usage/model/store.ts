import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { usageKey } from '../lib/usage'
import type { UsageBy, UsagePeriod, UsageReply } from './types'

/**
 * Расход LLM по периоду и разрезу. Данные параметризованные, поэтому запрашиваются по сокету ({op:'usage'}), ответ приходит разовым сообщением `usage_detail`
 * и хранится по ключу «период:разрез». Пока ответа на ключ нет, он в `pending` («загрузка»); прежний ответ остаётся на экране, пока приходит новый, без дёрганья.
 */
export const useUsageStore = defineStore('usage', () => {
  const replies = ref<Record<string, UsageReply>>({})
  const pending = ref<Record<string, boolean>>({})
  /** Когда (клиентские секунды) получен ответ по ключу: «обновлено N с назад». */
  const receivedAt = ref<Record<string, number>>({})
  let started = false

  function apply(m: LiveMessage): void {
    const d = m.data as UsageReply
    if (!d || d.days == null || typeof d.by !== 'string') return
    const k = usageKey(d.days, d.by)
    replies.value = { ...replies.value, [k]: d }
    receivedAt.value = { ...receivedAt.value, [k]: Date.now() / 1000 }
    pending.value = { ...pending.value, [k]: false }
  }

  /** Запросить расход. false: сокет не открыт (повторим по таймеру страницы или при переподключении). */
  function request(days: UsagePeriod, by: UsageBy): boolean {
    const k = usageKey(days, by)
    const ok = useLive().socket.send({ op: 'usage', days, by })
    if (ok) pending.value = { ...pending.value, [k]: true }
    return ok
  }

  function start(): void {
    if (started) return
    started = true
    useLive().socket.subscribe('usage_detail', apply)
  }

  return { replies, pending, receivedAt, request, apply, start }
})
