import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import type { BriefDetailReply, BriefList } from './types'

export const useBriefStore = defineStore('brief', () => {
  const list = ref<BriefList | null>(null)
  /** Досье по идентификатору (запрошенные страницей); ответ на неизвестный идентификатор лежит под ним же с ok=false. */
  const detail = ref<Record<string, BriefDetailReply>>({})
  let started = false

  function applyList(m: LiveMessage): void {
    list.value = m.data as BriefList
  }

  function applyDetail(m: LiveMessage): void {
    const d = m.data as BriefDetailReply
    if (d && typeof d.id === 'string') detail.value = { ...detail.value, [d.id]: d }
  }

  /** Запросить досье (ответ придёт сообщением brief_detail). false: сокет не открыт. */
  function request(id: string): boolean {
    return useLive().socket.send({ op: 'brief', id })
  }

  function start(): void {
    if (started) return
    started = true
    const { socket } = useLive()
    socket.subscribe('briefs', applyList)
    socket.subscribe('brief_detail', applyDetail)
  }

  return { list, detail, request, applyList, applyDetail, start }
})
