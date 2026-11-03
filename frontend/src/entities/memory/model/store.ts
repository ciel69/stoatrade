import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { normalizeDetail } from '../lib/hints'
import { MEMORY_OPS, type MemoryDetails, type MemoryOp } from './types'

type Details = { [K in MemoryOp]: MemoryDetails[K] | null }

/**
 * Вкладка «Память»: четыре независимых запроса (memory_cases, memory_lessons, memory_rules, memory_path), ответ каждого приходит своей темой `<op>_detail`.
 * Блоки не ждут друг друга: сбой одного ответа показывает причину только в этом блоке. Запросы по заходу, возвращению связи и после аварийного действия; таймеров нет.
 */
export const useMemoryStore = defineStore('memory', () => {
  const details = ref<Details>({ memory_cases: null, memory_lessons: null, memory_rules: null, memory_path: null })
  /** Операции, ответ на которые ещё не пришёл после последнего запроса. */
  const pending = ref<Record<MemoryOp, boolean>>({ memory_cases: false, memory_lessons: false, memory_rules: false, memory_path: false })
  let started = false

  function apply(op: MemoryOp, m: LiveMessage): void {
    pending.value[op] = false
    // запрос с lesson_id (путь одного урока) вкладка не шлёт: такие ответы не подменяют общий путь
    if (op === 'memory_path' && m.data && typeof m.data === 'object' && (m.data as { lesson_id?: unknown }).lesson_id) return
    details.value[op] = normalizeDetail(op, m.data) as never
  }

  /** Запросить блок. false: сокет не открыт (страница повторит, когда связь появится). */
  function request(op: MemoryOp): boolean {
    const ok = useLive().socket.send({ op })
    if (ok) pending.value[op] = true
    return ok
  }

  function requestAll(ops: readonly MemoryOp[] = MEMORY_OPS): void {
    for (const op of ops) request(op)
  }

  function start(): void {
    if (started) return
    started = true
    const { socket } = useLive()
    for (const op of MEMORY_OPS) socket.subscribe(`${op}_detail`, (m) => apply(op, m))
  }

  return { details, pending, request, requestAll, start, apply }
})
