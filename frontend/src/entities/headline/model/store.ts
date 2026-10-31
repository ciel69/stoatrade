import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import type { NewsSnapshot } from './types'

export const useNewsStore = defineStore('news', () => {
  const snapshot = ref<NewsSnapshot | null>(null)
  let started = false

  function apply(m: LiveMessage): void {
    snapshot.value = m.data as NewsSnapshot
  }

  function start(): void {
    if (started) return
    started = true
    useLive().socket.subscribe('news', apply)
  }

  return { snapshot, apply, start }
})
