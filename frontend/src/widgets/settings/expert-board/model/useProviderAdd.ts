import { computed, ref } from 'vue'
import { useActionStore } from '@/entities/action'
import { addArgs, addErrors, addLines, addReflects, emptyAdd, looksPrivate, mockProviderAdd, useRoleStore, type AddDraft } from '@/entities/role'
import { useReflect } from '@/shared/lib'

/**
 * Форма «Свой провайдер»: llm.provider add со spec (адрес, тип, файл ключа, первая модель). Идёт через общий движок: если адрес во внутренней сети, сервер требует
 * подтверждения, и общий диалог «Принимаю риски» показывает предупреждение. Провайдер добавляется выключенным и без ключа; ключ вводится в его панели.
 * Итог показывается строкой статуса у формы, а не уведомлением (quiet).
 */
export function useProviderAdd() {
  const store = useRoleStore()
  const action = useActionStore()
  const open = ref(false)
  const draft = ref<AddDraft>(emptyAdd())
  const failure = ref('')
  /** id только что добавленного провайдера: по нему форма предлагает открыть его панель */
  const created = ref<string | null>(null)
  const wait = useReflect()
  const busy = computed(() => action.active)
  const canAdd = computed(() => action.can('llm.provider'))
  const ids = computed(() => (store.snapshot?.provider_list ?? []).map((p) => p.id))
  const errors = computed(() => addErrors(draft.value, ids.value))
  const args = computed(() => addArgs(draft.value, ids.value))
  /** подсказка: адрес похож на внутренний, а флажок не стоит */
  const privateHint = computed(() => !draft.value.allowPrivate && !errors.value.baseUrl && looksPrivate(draft.value.baseUrl))

  function openForm(): void {
    draft.value = emptyAdd()
    failure.value = ''
    created.value = null
    open.value = true
  }
  function close(): void {
    open.value = false
    failure.value = ''
  }
  function patch(p: Partial<AddDraft>): void {
    draft.value = { ...draft.value, ...p }
    failure.value = ''
  }

  function submit(): boolean {
    const a = args.value
    if (busy.value || !a || !canAdd.value) return false
    failure.value = ''
    const id = a.id
    return action.run({
      action: 'llm.provider', args: { ...a }, title: `Добавить провайдера «${id}»`, what: addLines(draft.value), quiet: true,
      onDone: () => {
        created.value = id
        open.value = false
        wait.begin(() => addReflects(store.snapshot?.provider_list, id))
        if (store.mocked) store.mockExpertApply((s) => mockProviderAdd(s, a))
      },
      onFail: (text, f) => {
        failure.value = f?.code === 'E_ARGS' && f.msg ? `${text} Платформа ответила: ${f.msg.replace(/\s+/g, ' ').slice(0, 200)}` : text
      },
    })
  }

  return { open, draft, errors, args, busy, canAdd, privateHint, failure, created, wait, openForm, close, patch, submit }
}

export type ProviderAdd = ReturnType<typeof useProviderAdd>
