import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { dayNeeds } from '../lib/day'
import { isDayMock, mockDay, mockReport, type DayMock } from '../lib/mock'
import type { DayReportReply, DaySnapshot } from './types'

export const useDayStore = defineStore('day', () => {
  const snapshot = ref<DaySnapshot | null>(null)
  const mocked = ref<DayMock | null>(null)
  /** Отчёты по идентификатору дня (запрошенные страницей); ответ на неизвестный день лежит под ним же с ok=false. */
  const reports = ref<Record<string, DayReportReply>>({})
  let started = false

  const needs = computed(() => dayNeeds(snapshot.value))

  function apply(m: LiveMessage): void {
    if (!mocked.value) snapshot.value = m.data as DaySnapshot
  }

  function applyReport(m: LiveMessage): void {
    const d = m.data as DayReportReply
    if (d && typeof d.id === 'string') reports.value = { ...reports.value, [d.id]: d }
  }

  /** Запросить отчёт дня (ответ придёт сообщением day_report_detail). В режиме отладки отвечает образец. false: сокет не открыт. */
  function requestReport(id: string): boolean {
    if (mocked.value) {
      reports.value = { ...reports.value, [id]: mockReport(id) }
      return true
    }
    return useLive().socket.send({ op: 'day_report', id })
  }

  /** Подписка на тему; `?daymock=mock|mock-resume|mock-done|mock-abort|mock-empty` подставляет образец без сервера. */
  function start(): void {
    if (started) return
    started = true
    const q = new URLSearchParams(location.search).get('daymock')
    if (isDayMock(q)) {
      mocked.value = q
      snapshot.value = mockDay(q)
    }
    const { socket } = useLive()
    socket.subscribe('day', apply)
    socket.subscribe('day_report_detail', applyReport)
  }

  return { snapshot, mocked, reports, needs, apply, applyReport, requestReport, start }
})
