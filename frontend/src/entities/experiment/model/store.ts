import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { isExpMock, mockExp, type ExpMock } from '../lib/mock'
import type { ExpDetail } from './types'

export const useExperimentStore = defineStore('experiment', () => {
  const detail = ref<ExpDetail | null>(null)
  const mocked = ref<ExpMock | null>(null)
  /** Чего ждём от сервера: ответ другого разреза или режима (запоздавший) показывать нельзя. */
  const wanted = ref<{ by: string; mode: string } | null>(null)
  let started = false

  const pending = computed(() => !!wanted.value && (!detail.value || detail.value.by !== wanted.value.by || detail.value.mode !== wanted.value.mode))

  function apply(m: LiveMessage): void {
    const d = m.data as ExpDetail
    if (mocked.value || !d || typeof d.by !== 'string') return
    const w = wanted.value
    if (w && d.by === w.by && d.mode === w.mode) detail.value = d
  }

  /** Запросить разрез за режим (ответ придёт сообщением exp_detail). В режиме отладки отвечает образец. false: сокет не открыт (запрос повторяет страница, когда связь появится). */
  function request(by: string, mode: string): boolean {
    wanted.value = { by, mode }
    if (mocked.value) {
      detail.value = mockExp(mocked.value, by, mode)
      return true
    }
    return useLive().socket.send({ op: 'exp', by, mode })
  }

  /** Подписка на ответы; `?exp=mock|mock-empty|mock-confirmed` подставляет образец без сервера. */
  function start(): void {
    if (started) return
    started = true
    const q = new URLSearchParams(location.search).get('exp')
    if (isExpMock(q)) mocked.value = q
    useLive().socket.subscribe('exp_detail', apply)
  }

  return { detail, mocked, wanted, pending, apply, request, start }
})
