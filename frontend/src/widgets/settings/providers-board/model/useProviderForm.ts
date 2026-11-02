import { computed, ref, watch } from 'vue'
import { useActionStore } from '@/entities/action'
import {
  addLines, createArgs, createReflects, draftOfProvider, editLines, editPatch, editReflects, emptyProviderDraft, limitInput, looksPrivate, mockAddress, mockProviderAdd, providerErrors, slugId, useRoleStore,
  type LimitKey, type ProviderDraft, type ProviderErrors, type ProviderPatch,
} from '@/entities/role'
import { useReflect } from '@/shared/lib'

/**
 * Одна форма провайдера на «новый» и «изменить». providerId() = null: создание (llm.provider add: название, тип, адрес, внутренняя сеть, первая модель),
 * иначе правка (llm.provider update одним патчем: название, адрес, внутренняя сеть, ограничения расхода). Включение, ключ, список моделей и проверка это отдельные действия
 * (useProviderEdit, useProviderNet) со своими кнопками. Подтверждение рисков, если оно нужно, показывает общий диалог. Итог читается строкой статуса в форме (quiet).
 * Пока отправленная правка не отразилась в теме settings, «Сохранить» закрыта, чтобы не отправить то же дважды.
 */
export function useProviderForm(providerId: () => string | null) {
  const store = useRoleStore()
  const action = useActionStore()
  const row = computed(() => { const id = providerId(); return id ? (store.snapshot?.provider_list ?? []).find((p) => p.id === id) ?? null : null })
  const isNew = computed(() => providerId() === null)
  const ids = computed(() => (store.snapshot?.provider_list ?? []).map((p) => p.id))
  const draft = ref<ProviderDraft>(row.value ? draftOfProvider(row.value) : emptyProviderDraft())
  const canEdit = computed(() => action.can('llm.provider'))
  const busy = computed(() => action.active)
  const failure = ref('')
  /** предупреждение платформы после смены адреса (ключ теперь уходит на новый адрес) */
  const note = ref('')
  /** id только что созданного провайдера: пока тема его не показала, форма ждёт */
  const created = ref<string | null>(null)
  const createWait = useReflect()
  const saveWait = useReflect()
  const pending = ref<ProviderPatch | null>(null)

  const errors = computed<ProviderErrors>(() => providerErrors(draft.value, row.value))
  const createA = computed(() => (isNew.value ? createArgs(draft.value, ids.value) : null))
  const patch = computed(() => (row.value ? editPatch(draft.value, row.value) : null))
  const lines = computed(() => (row.value ? editLines(draft.value, row.value) : []))
  const createdReady = computed(() => !!created.value && ids.value.includes(created.value))
  const canSubmit = computed(() => !busy.value && canEdit.value && !pending.value && !created.value && (isNew.value ? !!createA.value : !!patch.value))
  /** подсказка: адрес похож на внутренний, а флажок не стоит */
  const privateHint = computed(() => !draft.value.allowPrivate && !errors.value.baseUrl && looksPrivate(draft.value.baseUrl))
  /** ошибка поля, пока оно не пусто (пустые поля при создании не ругаем до ввода) */
  const shown = (k: 'name' | 'baseUrl' | 'model'): string | undefined => {
    const v = k === 'name' ? draft.value.name : k === 'baseUrl' ? draft.value.baseUrl : draft.value.model
    return v.trim() || (k === 'name' && !isNew.value) ? errors.value[k] : undefined
  }
  const limitText = (k: LimitKey): string => draft.value.limits[k] ?? limitInput(row.value?.limits[k])
  const idGuess = computed(() => slugId(draft.value.name, ids.value, 'provider'))

  function setField(p: Partial<ProviderDraft>): void {
    draft.value = { ...draft.value, ...p }
    failure.value = ''
  }
  function setLimit(k: LimitKey, v: string): void {
    draft.value = { ...draft.value, limits: { ...draft.value.limits, [k]: v } }
    failure.value = ''
  }

  /** Форма открыта на другом провайдере (после создания переходит в правку созданного): всё начинается заново. */
  function reset(): void {
    draft.value = row.value ? draftOfProvider(row.value) : emptyProviderDraft()
    failure.value = ''
    note.value = ''
    created.value = null
    pending.value = null
  }
  watch(providerId, reset)

  watch([row, pending], () => {
    const p = pending.value
    if (p && editReflects(row.value, p)) {
      pending.value = null
      draft.value = { ...draft.value, baseUrl: '', limits: {} }
    }
  })
  watch(saveWait.state, (s) => { if (s === 'late') pending.value = null })

  const failText = (t: string, f?: { code?: string; msg?: string } | null): string => (f?.code === 'E_ARGS' && f.msg ? `${t} Платформа ответила: ${f.msg.replace(/\s+/g, ' ').slice(0, 200)}` : t)

  function submit(): boolean {
    if (!canSubmit.value) return false
    failure.value = ''
    note.value = ''
    const a = createA.value
    if (a) {
      const name = draft.value.name.trim()
      return action.run({
        action: 'llm.provider', args: { ...a }, title: `Новый провайдер «${name}»`, quiet: true,
        what: addLines({ id: a.id, name, adapter: draft.value.adapter, baseUrl: draft.value.baseUrl, keyFile: '', model: draft.value.model, allowPrivate: draft.value.allowPrivate }),
        onDone: () => {
          created.value = a.id
          createWait.begin(() => createReflects(store.snapshot?.provider_list, a.id))
          if (store.mocked) store.mockExpertApply((s) => mockProviderAdd(s, a))
        },
        onFail: (t, f) => { failure.value = failText(t, f) },
      })
    }
    const p = patch.value
    const r = row.value
    if (!p || !r) return false
    return action.run({
      action: 'llm.provider', args: { op: 'update', id: r.id, patch: { ...p } }, title: `Провайдер «${r.name}»`, quiet: true,
      what: lines.value.join('; ') + '.',
      onDone: (f) => {
        const w = f?.data?.send_warning as { msg?: unknown } | undefined
        note.value = w && typeof w.msg === 'string' ? w.msg.slice(0, 200) : ''
        pending.value = p
        saveWait.begin(() => editReflects(row.value, p))
        if (store.mocked) store.mockExpertApply((s) => mockAddress(s, r.id, p))
      },
      onFail: (t, f) => { failure.value = failText(t, f) },
    })
  }

  return {
    row, isNew, draft, errors, shown, patch, lines, canEdit, busy, canSubmit, privateHint, failure, note, created, createdReady, createWait, saveWait, pending, idGuess,
    limitText, setField, setLimit, submit,
  }
}

export type ProviderForm = ReturnType<typeof useProviderForm>
