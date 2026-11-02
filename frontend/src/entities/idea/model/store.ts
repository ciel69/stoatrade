import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { mockIdeas } from '../lib/mock'
import type { IdeasSnapshot } from './types'

export const useIdeaStore = defineStore('ideas', () => {
  const snapshot = ref<IdeasSnapshot | null>(null)
  let started = false

  /** Идеи не зависят от режима торговли: берём любой снимок темы (сервер строит её для каждого режима, данные те же). */
  let mocked = false
  function apply(m: LiveMessage): void {
    if (!mocked) snapshot.value = m.data as IdeasSnapshot
  }

  function start(): void {
    if (started) return
    started = true
    if (new URLSearchParams(location.search).get('ideas') === 'mock') {        // ?ideas=mock: образцовые данные без сервера (для проверки глазами)
      mocked = true
      snapshot.value = mockIdeas(Date.now() / 1000)
    }
    useLive().socket.subscribe('ideas', apply)
  }

  return { snapshot, apply, start }
})
