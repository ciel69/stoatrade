import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { mockSnapshot, type BrainMock } from '../lib/mock'
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
  const mocked = ref(false)
  let started = false

  const status = computed<BrainStatus>(() => snapshot.value?.status ?? 'down')
  const statusText = computed(() => STATUS_TEXT[status.value])
  const decisions = computed(() => snapshot.value?.decisions ?? [])
  const roles = computed(() => snapshot.value?.roles ?? [])

  /** Применить сообщение темы. Устаревшее (seq не больше принятого) игнорируется: после переподключения сервер шлёт снимок с seq текущего процесса. */
  function apply(m: LiveMessage): void {
    if (mocked.value) return
    if (m.seq < seq.value && m.seq !== 1) return
    seq.value = m.seq
    snapshot.value = m.data as BrainSnapshot
  }

  function start(): void {
    if (started) return
    started = true
    useLive().socket.subscribe('brain', apply)
  }

  /** Только для отладки без сервера (?brain=mock...): подставляет пример снимка, живые сообщения после этого игнорируются. */
  function mock(kind: BrainMock): void {
    mocked.value = true
    snapshot.value = mockSnapshot(kind, Date.now() / 1000)
  }

  return { snapshot, seq, status, statusText, decisions, roles, apply, start, mock }
})
