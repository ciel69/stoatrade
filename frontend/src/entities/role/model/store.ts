import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { mockChain, mockExchange, mockProvider, mockSeed, mockSnapshot, type RoleMock } from '../lib/mock'
import type { RolesSnapshot } from './types'

export const useRoleStore = defineStore('role', () => {
  const snapshot = ref<RolesSnapshot | null>(null)
  const mocked = ref<RoleMock | null>(null)
  let started = false
  let mockTimer: ReturnType<typeof setTimeout> | undefined

  /** Отладка: принятые «сервером» изменения провайдеров переживают обновление темы от настоящего сервера. */
  const provPatches = new Map<string, Parameters<typeof mockProvider>[2]>()
  const exchPatches: Array<{ id: string; to: Parameters<typeof mockExchange>[2] }> = []
  const extraPatches: Array<(s: RolesSnapshot) => RolesSnapshot> = []

  function apply(m: LiveMessage): void {
    const s = m.data as RolesSnapshot
    if (!mocked.value) { snapshot.value = s; return }
    let out = mockSnapshot(s, mocked.value)
    for (const [id, to] of provPatches) out = mockProvider(out, id, to)
    for (const p of exchPatches) out = mockExchange(out, p.id, p.to)
    for (const f of extraPatches) out = f(out)
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
    snapshot.value = snapshot.value ? mockSnapshot(snapshot.value, kind) : mockSeed(kind)
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

  /** Отладка: «сервер» применяет смену основной биржи или режима биржи через несколько секунд. */
  function mockExchangeApply(id: string, to: Parameters<typeof mockExchange>[2], delayMs = 2500): void {
    setTimeout(() => {
      exchPatches.push({ id, to })
      if (snapshot.value) snapshot.value = mockExchange(snapshot.value, id, to)
    }, delayMs)
  }

  /** Отладка: «сервер» применяет правку профиля, шаблона провайдера или параметров роли через несколько секунд. */
  function mockExpertApply(f: (s: RolesSnapshot) => RolesSnapshot, delayMs = 2500): void {
    setTimeout(() => {
      extraPatches.push(f)
      if (snapshot.value) snapshot.value = f(snapshot.value)
    }, delayMs)
  }

  return { snapshot, mocked, apply, start, mock, mockApply, mockProviderApply, mockExchangeApply, mockExpertApply }
})
