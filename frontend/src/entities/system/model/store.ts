import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import type { SystemSnapshot } from './types'

export const useSystemStore = defineStore('system', () => {
  const snapshot = ref<SystemSnapshot | null>(null)
  let started = false

  function apply(m: LiveMessage): void {
    snapshot.value = m.data as SystemSnapshot
  }

  function start(): void {
    if (started) return
    started = true
    useLive().socket.subscribe('system', apply)
  }

  return { snapshot, apply, start }
})
