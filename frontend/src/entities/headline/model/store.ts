import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { isNewsMock, mockNews } from '../lib/mock'
import type { NewsSnapshot } from './types'

export const useNewsStore = defineStore('news', () => {
  const snapshot = ref<NewsSnapshot | null>(null)
  /** образец вместо сервера (?newsmock=...): сокет не слушаем, чтобы живые данные не затёрли образец */
  const mocked = ref(false)
  let started = false

  function apply(m: LiveMessage): void {
    if (mocked.value) return
    snapshot.value = m.data as NewsSnapshot
  }

  /** Подписка на тему; `?newsmock=mock|mock-reader-off|mock-source-down|mock-blackout|mock-limit` подставляет образец без сервера. */
  function start(): void {
    if (started) return
    started = true
    const q = new URLSearchParams(location.search).get('newsmock')
    if (isNewsMock(q)) {
      mocked.value = true
      snapshot.value = mockNews(q)
    }
    useLive().socket.subscribe('news', apply)
  }

  return { snapshot, mocked, apply, start }
})
