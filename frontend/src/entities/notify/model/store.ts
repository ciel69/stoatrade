import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { mockApplied, mockNotify, type NotifyMock } from '../lib/mock'
import type { NotifySettings, NotifySnapshot } from './types'

export const useNotifyStore = defineStore('notify', () => {
  const snapshot = ref<NotifySnapshot | null>(null)
  const mocked = ref<NotifyMock | null>(null)
  let started = false
  let timer: ReturnType<typeof setTimeout> | undefined

  function apply(m: LiveMessage): void {
    if (!mocked.value) snapshot.value = m.data as NotifySnapshot
  }

  /** Подписка на тему; `?notify=mock|mock-quiet|mock-empty` подставляет образец без сервера. */
  function start(): void {
    if (started) return
    started = true
    const q = new URLSearchParams(location.search).get('notify')
    if (q === 'mock' || q === 'mock-quiet' || q === 'mock-empty') {
      mocked.value = q
      snapshot.value = mockNotify(q)
    }
    useLive().socket.subscribe('notify', apply)
  }

  /** Отладка: «сервер» применяет настройки через пару секунд, как обновляется тема. */
  function mockApply(to: NotifySettings, delayMs = 2500): void {
    clearTimeout(timer)
    timer = setTimeout(() => { if (snapshot.value) snapshot.value = mockApplied(snapshot.value, to) }, delayMs)
  }

  return { snapshot, mocked, apply, start, mockApply }
})
