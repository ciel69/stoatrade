import { computed, onUnmounted, ref, watch } from 'vue'
import { useActionStore, type KeyOutcome } from '@/entities/action'
import { REFLECT_WAIT_MS, enableBlock, providerReflects, rolesUsing, useRoleStore, type Expect } from '@/entities/role'

/** Подтверждённое «данные обновились» показываем недолго и убираем: в спокойном состоянии форма молчит. */
const SEEN_MS = 4000

export type WaitKind = 'enabled' | 'key'
export interface Wait {
  id: number
  kind: WaitKind
  exp: Expect
  /** wait: ждём обновления данных; seen: тема отразила; late: 25 с прошло, данные ещё не пришли */
  state: 'wait' | 'seen' | 'late'
}

export const waitText = (w: Wait): string => (w.state === 'seen' ? 'Данные обновились.' : w.state === 'late' ? 'Данные обновятся скоро.' : 'Ожидаем обновления данных…')

/**
 * Действия над существующим провайдером, которые применяются сразу, без кнопки «Сохранить»: включение и ключ. Всё идёт через общий движок действий
 * (нужен ли диалог «Принимаю риски», решает сервер); ключ уходит напрямую (запись без challenge). После успеха ждём, пока тема settings отразит изменение,
 * и пока ждём, показываем отправленное как текущее. Значения ключа здесь нет: saveKey только передаёт его в отправитель и не хранит.
 */
export function useProviderEdit(providerId: () => string) {
  const store = useRoleStore()
  const action = useActionStore()
  const row = computed(() => (store.snapshot?.provider_list ?? []).find((x) => x.id === providerId()) ?? null)
  const users = computed(() => rolesUsing(providerId(), store.snapshot?.roles ?? []))
  const liveMode = computed(() => store.snapshot?.mode === 'live')
  /** роли, влияющие на live, среди тех, кто пользуется провайдером */
  const liveUsers = computed(() => users.value.filter((r) => (store.snapshot?.roles ?? []).find((x) => x.role === r)?.live_risk))
  const canEdit = computed(() => action.can('llm.provider'))
  const canKey = computed(() => action.can('llmkey'))
  /** идёт действие (проверка, подтверждение, отправка, ожидание): второе не запускаем */
  const busy = computed(() => action.active)

  // ---- ожидание отражения в теме
  const waits = ref<Wait[]>([])
  let seq = 0
  const timers = new Map<number, ReturnType<typeof setTimeout>>()
  const stop = (id: number): void => { clearTimeout(timers.get(id)); timers.delete(id) }
  const waitOf = (k: WaitKind): Wait | null => waits.value.find((w) => w.kind === k) ?? null
  const pendingOf = (k: WaitKind): Expect | null => { const w = waitOf(k); return w && w.state !== 'seen' ? w.exp : null }

  function drop(id: number): void {
    stop(id)
    waits.value = waits.value.filter((w) => w.id !== id)
  }
  function markSeen(w: Wait): void {
    stop(w.id)
    w.state = 'seen'
    timers.set(w.id, setTimeout(() => drop(w.id), SEEN_MS))
  }
  function track(kind: WaitKind, exp: Expect): void {
    for (const w of waits.value.filter((x) => x.kind === kind)) stop(w.id)
    const w: Wait = { id: ++seq, kind, exp, state: 'wait' }
    waits.value = [...waits.value.filter((x) => x.kind !== kind), w]
    if (providerReflects(row.value, exp)) { markSeen(waits.value[waits.value.length - 1]); return }
    timers.set(w.id, setTimeout(() => { const x = waits.value.find((y) => y.id === w.id); if (x && x.state === 'wait') x.state = 'late' }, REFLECT_WAIT_MS))
    if (store.mocked) {
      if ('enabled' in exp) store.mockProviderApply(providerId(), { enabled: exp.enabled })
      else if ('key' in exp) store.mockProviderApply(providerId(), { keyPresent: exp.key })
    }
  }
  watch(row, () => {
    for (const w of waits.value) if (w.state !== 'seen' && providerReflects(row.value, w.exp)) markSeen(w)
  })
  onUnmounted(() => { for (const id of [...timers.keys()]) stop(id) })

  // ---- что показывать как текущее (с учётом отправленного)
  const shownEnabled = computed(() => { const e = pendingOf('enabled'); return e && 'enabled' in e ? e.enabled : (row.value?.enabled ?? false) })
  const shownKey = computed(() => { const e = pendingOf('key'); return e && 'key' in e ? e.key : (row.value?.cred.present ?? false) })

  /** Включить нельзя без ключа; причина текстом. */
  const enableReason = computed(() => (row.value ? enableBlock({ local: row.value.local, cred: { ...row.value.cred, present: shownKey.value } }, true) : null))
  const title = computed(() => `Провайдер «${row.value?.name ?? providerId()}»`)

  function setEnabled(want: boolean): boolean {
    const p = row.value
    if (busy.value || !p || want === shownEnabled.value || enableBlock({ local: p.local, cred: { ...p.cred, present: shownKey.value } }, want)) return false
    return action.run({
      action: 'llm.provider', args: { op: want ? 'enable' : 'disable', id: p.id }, title: title.value,
      what: want ? 'Включить провайдера: роли смогут отправлять ему запросы.' : 'Выключить провайдера: роли перестанут отправлять ему запросы и перейдут на запасные модели.',
      onDone: () => track('enabled', { enabled: want }),
    })
  }

  // ---- ключ
  /** Записать ключ (напрямую, без challenge). Значение не сохраняется нигде, результат без эха. */
  async function saveKey(value: string): Promise<KeyOutcome> {
    const r = await action.sendKey(providerId(), value)
    if (r.ok) track('key', { key: true })
    return r
  }
  function clearKey(): boolean {
    const p = row.value
    if (busy.value || !p) return false
    return action.run({
      action: 'llm.provider', args: { op: 'key_clear', id: p.id }, title: title.value,
      what: 'Удалить ключ: провайдер перестанет отвечать, пока не будет добавлен новый.',
      onDone: () => track('key', { key: false }),
    })
  }

  return { row, users, liveUsers, liveMode, canEdit, canKey, busy, waitOf, shownEnabled, shownKey, enableReason, setEnabled, saveKey, clearKey }
}

export type ProviderEdit = ReturnType<typeof useProviderEdit>
