import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { normalizeLearning } from '../lib/learning'
import { isLearningMock, mockLearning, type LearningMock } from '../lib/mock'
import { isLearningMode, type LearningDetail, type LearningMode } from './types'

export const useLearningStore = defineStore('learning', () => {
  const detail = ref<LearningDetail | null>(null)
  const mocked = ref<LearningMock | null>(null)
  /** Какой режим ждём от сервера: ответ другого режима (запоздавший) показывать нельзя. */
  const wanted = ref<LearningMode | null>(null)
  let started = false

  const pending = computed(() => !!wanted.value && (!detail.value || detail.value.mode !== wanted.value))

  /** Ответ `learning_detail`: чужой режим отбрасывается, повторное сообщение того же режима заменяет прежнее (так приходит готовый блок «Считается…»). */
  function apply(m: LiveMessage): void {
    if (mocked.value) return
    const w = wanted.value
    if (!w) return
    const d = normalizeLearning(m.data, w)
    if (!d || d.mode !== w) return
    detail.value = d
  }

  /** Запросить отчёт за режим. false: сокет не открыт (страница повторит запрос, когда связь появится). */
  function request(mode: LearningMode): boolean {
    const m: LearningMode = isLearningMode(mode) ? mode : 'paper'
    wanted.value = m
    if (mocked.value) {
      detail.value = mockLearning(mocked.value, m)
      return true
    }
    return useLive().socket.send({ op: 'learning', mode: m })
  }

  /** Подписка на ответы; `?learning=mock|mock-few|mock-confirmed|mock-empty|mock-computing` подставляет образец без сервера. */
  function start(): void {
    if (started) return
    started = true
    const q = new URLSearchParams(location.search).get('learning')
    if (isLearningMock(q)) mocked.value = q
    useLive().socket.subscribe('learning_detail', apply)
  }

  return { detail, mocked, wanted, pending, apply, request, start }
})
