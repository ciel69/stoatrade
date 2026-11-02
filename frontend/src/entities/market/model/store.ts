import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import type { FlowSym, HeatRow, HotRow, LiqSym, MarketSnapshot } from './types'

/** Монета в разных потоках: «BTCUSDT» и «BTC» это одна монета. */
export const coinKey = (sym: string): string => (sym.toUpperCase().endsWith('USDT') && sym.length > 4 ? sym.slice(0, -4) : sym.toUpperCase())

export const useMarketStore = defineStore('market', () => {
  const snapshot = ref<MarketSnapshot | null>(null)
  let started = false

  const liqBy = computed(() => new Map<string, LiqSym>((snapshot.value?.liq.syms ?? []).map((s) => [coinKey(s.sym), s])))
  const flowBy = computed(() => new Map<string, FlowSym>((snapshot.value?.flow.syms ?? []).map((s) => [coinKey(s.sym), s])))
  const hotBy = computed(() => new Map<string, HotRow>([...(snapshot.value?.hot.blocked ?? []), ...(snapshot.value?.hot.top ?? [])].map((s) => [coinKey(s.sym), s])))
  const heatBy = computed(() => new Map<string, HeatRow>((snapshot.value?.heat.rows ?? []).map((s) => [coinKey(s.sym), s])))

  function apply(m: LiveMessage): void {
    snapshot.value = m.data as MarketSnapshot
  }

  function start(): void {
    if (started) return
    started = true
    useLive().socket.subscribe('market', apply)
  }

  return { snapshot, liqBy, flowBy, hotBy, heatBy, apply, start }
})
