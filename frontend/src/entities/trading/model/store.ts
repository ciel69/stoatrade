import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import type { DecisionsSnapshot, HomeSnapshot, ModelsSnapshot, PlanDetailReply, PlansSnapshot, TradesSnapshot, TradingMode } from './types'

const MODES: TradingMode[] = ['paper', 'live', 'testnet', 'all']

export const useTradingStore = defineStore('trading', () => {
  const snapshot = ref<HomeSnapshot | null>(null)
  const trades = ref<TradesSnapshot | null>(null)
  const decisions = ref<DecisionsSnapshot | null>(null)
  const models = ref<ModelsSnapshot | null>(null)
  const plans = ref<PlansSnapshot | null>(null)
  /** Подробности планов, запрошенные панелью (по идентификатору плана). */
  const planDetail = ref<Record<string, PlanDetailReply>>({})
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

  function applyDecisions(m: LiveMessage): void {
    const data = m.data as DecisionsSnapshot
    if (data.mode === mode.value) decisions.value = data
  }

  function applyModels(m: LiveMessage): void {
    const data = m.data as ModelsSnapshot
    if (data.mode === mode.value) models.value = data
  }

  function applyPlans(m: LiveMessage): void {
    const data = m.data as PlansSnapshot
    if (data.mode === mode.value) plans.value = data
  }

  function applyPlanDetail(m: LiveMessage): void {
    const d = m.data as PlanDetailReply
    if (d && typeof d.id === 'string') planDetail.value = { ...planDetail.value, [d.id]: d }
  }

  /** Запросить подробности плана (ответ придёт сообщением plan_detail). false: сокет не открыт. */
  function requestPlan(id: string): boolean {
    return useLive().socket.send({ op: 'plan', id })
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
    decisions.value = null
    models.value = null
    plans.value = null
    planDetail.value = {}
    snapshot.value = null                        // чужие цифры другого режима не показываем, пока не пришёл снимок
    useLive().socket.send({ op: 'mode', mode: next })
  }

  function start(): void {
    if (started) return
    started = true
    const { socket } = useLive()
    socket.subscribe('hello', applyHello)
    socket.subscribe('home', applyHome)
    socket.subscribe('trades', applyTrades)
    socket.subscribe('decisions', applyDecisions)
    socket.subscribe('models', applyModels)
    socket.subscribe('plans', applyPlans)
    socket.subscribe('plan_detail', applyPlanDetail)
  }

  return { snapshot, trades, decisions, models, plans, planDetail, mode, positions, limits, setMode, requestPlan, applyHome, applyTrades, applyDecisions, applyModels, applyPlans, applyPlanDetail, applyHello, start }
})
