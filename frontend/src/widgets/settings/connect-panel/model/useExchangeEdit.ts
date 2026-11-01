import { computed, ref } from 'vue'
import { useActionStore } from '@/entities/action'
import { useControlStore } from '@/entities/control'
import {
  activateArgs, activateBlock, activateEffect, activateLine, activateTitle, canSetMode, exchangeBlock, exchangeModeArgs, exchangeModeEffect, exchangeModeLine, exchangeModeTitle, exchangeReflects, keyNote, useRoleStore,
} from '@/entities/role'
import { useReflect } from '@/shared/lib'

/**
 * Правка биржи в панели: «Сделать основной» и режим биржи (бумага, тестнет, живые деньги). Только при остановленной платформе (так требует сервер, причина показывается словами).
 * Выбор копится черновиком со строкой «было → станет»; «Применить» запускает общее действие: переход в живые деньги откроет единый диалог «Принимаю риски».
 * После успеха ждём, пока тема settings отразит изменение.
 */
export function useExchangeEdit(exchangeId: () => string) {
  const store = useRoleStore()
  const control = useControlStore()
  const action = useActionStore()
  const list = computed(() => store.snapshot?.exchange_list ?? [])
  const row = computed(() => list.value.find((x) => x.id === exchangeId()) ?? null)
  const current = computed(() => list.value.find((x) => x.active) ?? null)
  const canEdit = computed(() => action.can('exchange.set'))
  const busy = computed(() => action.active)
  /** почему биржи сейчас не менять (платформа должна стоять), или null */
  const platformWhy = computed(() => exchangeBlock(control.ctl?.platform.state))
  const activateWhy = computed(() => (row.value ? activateBlock(row.value) : null))
  const wait = useReflect()

  // ---- основная биржа
  const activateDraft = ref(false)
  const activateChange = computed(() => (activateDraft.value && row.value && !activateWhy.value ? { line: activateLine(current.value, row.value), effect: activateEffect(current.value, row.value) } : null))
  function toggleActivate(): void {
    if (busy.value || activateWhy.value || platformWhy.value) return
    activateDraft.value = !activateDraft.value
  }
  function submitActivate(): boolean {
    const r = row.value
    if (busy.value || !r || activateWhy.value || platformWhy.value || !activateDraft.value) return false
    const id = r.id
    return action.run({
      action: 'exchange.set', args: activateArgs(r), title: activateTitle(r), what: activateEffect(current.value, r),
      onDone: () => {
        activateDraft.value = false
        wait.begin(() => exchangeReflects(list.value.find((x) => x.id === id), { active: true }))
        store.mockExchangeApply(id, { active: true })
      },
    })
  }

  // ---- режим биржи
  const modeDraft = ref<string | null>(null)
  const modeTo = computed(() => (row.value && canSetMode(row.value, modeDraft.value) ? modeDraft.value : null))
  const modeChange = computed(() => {
    const r = row.value
    const to = modeTo.value
    if (!r || to == null) return null
    return { line: exchangeModeLine(r, to), effect: exchangeModeEffect(r, to), hint: keyNote(r, to) ?? undefined }
  })
  function pickMode(m: string): void {
    if (busy.value || platformWhy.value || !row.value) return
    modeDraft.value = m === row.value.mode || modeDraft.value === m ? null : m
  }
  function submitMode(): boolean {
    const r = row.value
    const to = modeTo.value
    if (busy.value || !r || to == null || platformWhy.value) return false
    return action.run({
      action: 'exchange.set', args: exchangeModeArgs(r, to), title: exchangeModeTitle(r, to), what: exchangeModeEffect(r, to),
      onDone: () => {
        modeDraft.value = null
        wait.begin(() => exchangeReflects(list.value.find((x) => x.id === r.id), { mode: to }))
        store.mockExchangeApply(r.id, { mode: to })
      },
    })
  }

  return {
    row, current, canEdit, busy, platformWhy, activateWhy, wait, activateDraft, activateChange, toggleActivate, submitActivate,
    modeDraft, modeChange, pickMode, submitMode, resetMode: () => { modeDraft.value = null }, resetActivate: () => { activateDraft.value = false },
  }
}

export type ExchangeEdit = ReturnType<typeof useExchangeEdit>
