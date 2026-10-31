import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { mockChain, mockProvider, mockSnapshot, type RoleMock } from '../lib/mock'
import type { RolesSnapshot } from './types'

export const useRoleStore = defineStore('role', () => {
  const snapshot = ref<RolesSnapshot | null>(null)
  const mocked = ref<RoleMock | null>(null)
  let started = false
  let mockTimer: ReturnType<typeof setTimeout> | undefined

  /** Отладка: принятые «сервером» изменения провайдеров переживают обновление темы от настоящего сервера. */
  const provPatches = new Map<string, Parameters<typeof mockProvider>[2]>()

  function apply(m: LiveMessage): void {
    const s = m.data as RolesSnapshot
    if (!mocked.value) { snapshot.value = s; return }
    let out = mockSnapshot(s, mocked.value)
    for (const [id, to] of provPatches) out = mockProvider(out, id, to)
    snapshot.value = out
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

  /** Отладка: «сервер» применяет изменение провайдера через несколько секунд, как обновляется тема settings. */
  function mockProviderApply(id: string, to: Parameters<typeof mockProvider>[2], delayMs = 2500): void {
    setTimeout(() => {
      provPatches.set(id, { ...provPatches.get(id), ...to })
      if (snapshot.value) snapshot.value = mockProvider(snapshot.value, id, to)
    }, delayMs)
  }

  return { snapshot, mocked, apply, start, mock, mockApply, mockProviderApply }
})
