import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { mockChain, mockSnapshot, type RoleMock } from '../lib/mock'
import type { RolesSnapshot } from './types'

export const useRoleStore = defineStore('role', () => {
  const snapshot = ref<RolesSnapshot | null>(null)
  const mocked = ref<RoleMock | null>(null)
  let started = false
  let mockTimer: ReturnType<typeof setTimeout> | undefined

  function apply(m: LiveMessage): void {
    const s = m.data as RolesSnapshot
    snapshot.value = mocked.value ? mockSnapshot(s, mocked.value) : s
  }

  function start(): void {
    if (started) return
    started = true
    useLive().socket.subscribe('settings', apply)
  }

  /** Только для отладки без сервера (?edit=mock): входящие снимки дополняются примерами профилей. */
  function mock(kind: RoleMock): void {
    mocked.value = kind
    if (snapshot.value) snapshot.value = mockSnapshot(snapshot.value, kind)
  }

  /** Отладка: «сервер» применяет цепочку через несколько секунд, как обновляется тема settings. */
  function mockApply(target: string, to: { primary?: string; fallback?: string[] }, delayMs = 2500): void {
    clearTimeout(mockTimer)
    mockTimer = setTimeout(() => { if (snapshot.value) snapshot.value = mockChain(snapshot.value, target, to) }, delayMs)
  }

  return { snapshot, mocked, apply, start, mock, mockApply }
})
