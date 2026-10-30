import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import type { HomeSnapshot, TradesSnapshot, TradingMode } from './types'

const MODES: TradingMode[] = ['paper', 'live', 'testnet', 'all']

export const useTradingStore = defineStore('trading', () => {
  const snapshot = ref<HomeSnapshot | null>(null)
  const trades = ref<TradesSnapshot | null>(null)
  /** Выбранный режим. null: пока не выбран вручную, берём режим платформы из hello сервера. */
  const chosen = ref<TradingMode | null>(null)
  const serverMode = ref<TradingMode>('paper')
  let started = false

  const mode = computed<TradingMode>(() => chosen.value ?? serverMode.value)
  const positions = computed(() => snapshot.value?.positions ?? [])
  const limits = computed(() => snapshot.value?.limits ?? [])

  function applyHome(m: LiveMessage): void {
    const data = m.data as HomeSnapshot
    if (data.mode !== mode.value) return          // запоздавший снимок прежнего режима
    snapshot.value = data
  }

  function applyTrades(m: LiveMessage): void {
    const data = m.data as TradesSnapshot
    if (data.mode !== mode.value) return
    trades.value = data
  }

  function applyHello(m: LiveMessage): void {
    const d = m.data as { mode?: TradingMode }
    if (d.mode && MODES.includes(d.mode)) serverMode.value = d.mode
    sendMode()                                     // после переподключения сервер начинает с режима платформы: возвращаем выбор владельца
  }

  function sendMode(): void {
    if (chosen.value && chosen.value !== serverMode.value) useLive().socket.send({ op: 'mode', mode: chosen.value })
  }

  function setMode(next: TradingMode): void {
    if (!MODES.includes(next) || next === mode.value) return
    chosen.value = next
    trades.value = null
    snapshot.value = null                          // чужие цифры другого режима не показываем, пока не пришёл снимок
    useLive().socket.send({ op: 'mode', mode: next })
  }

  function start(): void {
    if (started) return
    started = true
    const { socket } = useLive()
    socket.subscribe('hello', applyHello)
    socket.subscribe('home', applyHome)
    socket.subscribe('trades', applyTrades)
  }

  return { snapshot, trades, mode, positions, limits, setMode, applyHome, applyTrades, applyHello, start }
})
