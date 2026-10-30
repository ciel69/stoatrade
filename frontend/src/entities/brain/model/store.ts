import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import type { BrainSnapshot, BrainStatus } from './types'

const STATUS_TEXT: Record<BrainStatus, string> = {
  down: 'не работает',
  protect: 'только защита',
  thinking: 'думает',
  idle: 'ждёт событий',
}

export const useBrainStore = defineStore('brain', () => {
  const snapshot = ref<BrainSnapshot | null>(null)
  const seq = ref(0)
  let started = false

  const status = computed<BrainStatus>(() => snapshot.value?.status ?? 'down')
  const statusText = computed(() => STATUS_TEXT[status.value])
  const decisions = computed(() => snapshot.value?.decisions ?? [])
  const roles = computed(() => snapshot.value?.roles ?? [])

  /** Применить сообщение темы. Устаревшее (seq не больше принятого) игнорируется: после переподключения сервер шлёт снимок с seq текущего процесса. */
  function apply(m: LiveMessage): void {
    if (m.seq < seq.value && m.seq !== 1) return
    seq.value = m.seq
    snapshot.value = m.data as BrainSnapshot
  }

  function start(): void {
    if (started) return
    started = true
    useLive().socket.subscribe('brain', apply)
  }

  return { snapshot, seq, status, statusText, decisions, roles, apply, start }
})
