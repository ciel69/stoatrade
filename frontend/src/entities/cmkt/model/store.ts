import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useLive, type LinkState, type LiveMessage } from '@/shared/api'
import { isAged } from '../lib/format'
import { readDetail, readSnapshot, type CmktDetail, type CmktSnapshot } from './types'

/** Монета из адреса или списка: тикер без пробелов и в верхнем регистре. */
export const normalizeSym = (sym: string | null | undefined): string | null => {
  const s = (sym ?? '').trim().toUpperCase()
  return s || null
}

const sameSym = (a: string | null | undefined, b: string | null | undefined): boolean => normalizeSym(a) === normalizeSym(b)

/**
 * Сводный рынок (R1.7, задача 29): тема `cmkt` приходит сама, подробности выбранной монеты — по запросу `cmkt_sym`.
 * Возраст считается на экране по `ts`, а не по серверному `age_s`. Повторный запрос той же монеты не уходит, пока
 * ответ не пришёл; после обрыва связи запрос повторяется, когда сокет снова открылся.
 */
export const useCmktStore = defineStore('cmkt', () => {
  const snapshot = ref<CmktSnapshot | null>(null)
  const detail = ref<CmktDetail | null>(null)
  const selected = ref<string | null>(null)
  const loading = ref(false)
  const link = ref<LinkState>('closed')
  /** Монета, ответ по которой сейчас ждём: защита от повторной отправки и от чужих ответов. */
  const inflight = ref<string | null>(null)
  let started = false

  /** Свежесть по подтверждению сервера; возраст по часам клиента показывает экран (см. `ageAt`). */
  const fresh = computed(() => !!snapshot.value?.ok && snapshot.value.ts !== null)
  const stale = computed(() => snapshot.value !== null && !fresh.value)

  /** Возраст снимка по `ts`, не по `age_s`. */
  function ageAt(now: number): number | null {
    const ts = snapshot.value?.ts
    return typeof ts === 'number' && Number.isFinite(ts) ? Math.max(0, now - ts) : null
  }

  /** Возраст подробностей выбранной монеты по `ts`. */
  function detailAgeAt(now: number): number | null {
    const ts = detail.value?.ts
    return typeof ts === 'number' && Number.isFinite(ts) ? Math.max(0, now - ts) : null
  }

  const detailNeedsRefresh = (d: CmktDetail | null, want: string): boolean =>
    !d || !sameSym(d.sym, want) || d.warn.includes('STALE') || isAged(d.ts, Date.now() / 1000)

  function applySnapshot(m: LiveMessage): void {
    const s = readSnapshot(m.data)
    if (!s) return
    const wasFresh = snapshot.value?.ok === true && snapshot.value.ts !== null
    snapshot.value = s
    const nowFresh = s.ok && s.ts !== null
    const want = selected.value
    if (!nowFresh || !want) return
    if (!wasFresh || detailNeedsRefresh(detail.value, want)) send(want)
  }

  function applyDetail(m: LiveMessage): void {
    const d = readDetail(m.data)
    if (!d || !sameSym(d.sym, selected.value)) return
    detail.value = d
    inflight.value = null
    loading.value = false
  }

  function send(sym: string): boolean {
    if (sameSym(inflight.value, sym)) return true
    inflight.value = sym
    loading.value = true
    const sent = useLive().socket.send({ op: 'cmkt_sym', sym })
    if (!sent) {
      inflight.value = null
      loading.value = false
    }
    return sent
  }

  /** Выбрать монету и запросить подробности. Повторный выбор той же монеты в полёте не дублирует запрос. */
  function select(raw: string | null | undefined): boolean {
    const sym = normalizeSym(raw)
    if (!sym) {
      selected.value = null
      detail.value = null
      inflight.value = null
      loading.value = false
      return false
    }
    if (!sameSym(selected.value, sym)) {
      selected.value = sym
      detail.value = null
    }
    return send(sym)
  }

  /** Связь: пока сокет закрыт, ответа не ждём; при открытии после обрыва повторяем запрос выбранной монеты. */
  function onLink(state: LinkState): void {
    const prev = link.value
    link.value = state
    if (state !== 'open') {
      inflight.value = null
      loading.value = false
      return
    }
    if (prev !== 'open' && selected.value && !inflight.value) send(selected.value)
  }

  /** Подписка на тему и ответы; вызывается один раз при входе на вкладку. */
  function start(): void {
    if (started) return
    started = true
    const { socket } = useLive()
    socket.subscribe('cmkt', applySnapshot)
    socket.subscribe('cmkt_sym_detail', applyDetail)
    socket.onState(onLink)
  }

  return { snapshot, detail, selected, loading, link, fresh, stale, ageAt, detailAgeAt, applySnapshot, applyDetail, select, start }
})
