import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import type { RolesSnapshot } from './types'

export const useRoleStore = defineStore('role', () => {
  const snapshot = ref<RolesSnapshot | null>(null)
  let started = false

  function apply(m: LiveMessage): void {
    snapshot.value = m.data as RolesSnapshot
  }

  function start(): void {
    if (started) return
    started = true
    useLive().socket.subscribe('settings', apply)
  }

  return { snapshot, apply, start }
})
