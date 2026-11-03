import { computed, ref } from 'vue'
import { useActionStore } from '@/entities/action'
import {
  RE_MODEL_NAME, filterIds, mockModelSet, modelAddReflects, modelKey, modelsOutcome, netReady, newModelSpec, pingOutcome, useRoleStore,
  type ModelsOk, type NetFail, type PingOk, type ProviderRow,
} from '@/entities/role'
import { useReflect } from '@/shared/lib'

/**
 * Сетевая часть формы провайдера: список моделей у провайдера (бесплатный запрос), проверка подключения (платный: копейки; всегда первой моделью провайдера) и добавление модели.
 * Всё идёт через общий движок действий llm.provider; итог читается здесь же (quiet: без общего уведомления). Одновременно выполняется одно действие (движок), у платформы лимит:
 * не чаще одного сетевого вызова на провайдера раз в несколько секунд, отказ по лимиту показывается словами с числом секунд. Ключ и его значение сюда не попадают.
 */
export function useProviderNet(providerId: () => string, keyPresent: () => boolean) {
  const store = useRoleStore()
  const action = useActionStore()
  const row = computed<ProviderRow | null>(() => (store.snapshot?.provider_list ?? []).find((x) => x.id === providerId()) ?? null)
  const canEdit = computed(() => action.can('llm.provider'))
  const busy = computed(() => action.active)
  const ready = computed(() => netReady(row.value, keyPresent()))
  const title = computed(() => `Провайдер «${row.value?.name ?? providerId()}»`)
  /** какое действие этой формы выполняется сейчас (для подписи на кнопке) */
  const running = ref<'models' | 'ping' | 'model' | null>(null)

  // ---- список моделей у провайдера
  const models = ref<ModelsOk | null>(null)
  const modelsFail = ref<NetFail | null>(null)
  const query = ref('')
  const filtered = computed(() => filterIds(models.value?.ids ?? [], query.value))
  const failOf = (t: string, r: ModelsOk | PingOk | NetFail): NetFail => (r.ok ? { ok: false, text: t, detail: '', retryS: null } : r)

  function fetchModels(): boolean {
    if (busy.value || !canEdit.value || !ready.value) return false
    running.value = 'models'
    modelsFail.value = null
    const ok = action.run({
      action: 'llm.provider', args: { op: 'models', id: providerId() }, title: title.value, what: 'Запросить у провайдера список моделей. Запрос бесплатный.', quiet: true,
      onDone: (f) => {
        running.value = null
        const r = modelsOutcome(f, 'Платформа не прислала список.')
        if (r.ok) { models.value = r; query.value = '' } else modelsFail.value = r
      },
      onFail: (t, f) => { running.value = null; modelsFail.value = failOf(t, modelsOutcome(f, t)) },
    })
    if (!ok) running.value = null
    return ok
  }

  // ---- проверка подключения (платный запрос первой моделью провайдера)
  const ping = ref<PingOk | null>(null)
  const pingFail = ref<NetFail | null>(null)

  function runPing(): boolean {
    if (busy.value || !canEdit.value || !ready.value || !row.value?.models.length) return false
    running.value = 'ping'
    ping.value = null
    pingFail.value = null
    const ok = action.run({
      action: 'llm.provider', args: { op: 'ping', id: providerId() }, title: title.value, what: 'Платный запрос: короткий вопрос модели, до 16 токенов ответа. Стоит копейки.', quiet: true,
      onDone: (f) => {
        running.value = null
        const r = pingOutcome(f, 'Платформа не прислала результат.')
        if (r.ok) ping.value = r; else pingFail.value = r
      },
      onFail: (t, f) => { running.value = null; pingFail.value = failOf(t, pingOutcome(f, t)) },
    })
    if (!ok) running.value = null
    return ok
  }

  // ---- добавление модели
  const pick = ref('')
  const modelWait = useReflect()
  const modelFail = ref('')
  const pickName = computed(() => pick.value.trim())
  const pickError = computed(() => {
    if (!pickName.value) return null
    if (!RE_MODEL_NAME.test(pickName.value)) return 'Имя модели: латинские буквы, цифры и «. : / - _», до 80 знаков.'
    if (row.value?.models.some((m) => m.name === pickName.value)) return 'Такая модель у провайдера уже добавлена.'
    return null
  })
  const canAddModel = computed(() => canEdit.value && !!row.value && !!pickName.value && !pickError.value)

  function choose(id: string): void {
    pick.value = id
    modelFail.value = ''
  }
  function addModel(): boolean {
    const p = row.value
    if (busy.value || !p || !canAddModel.value) return false
    const name = pickName.value
    const key = modelKey(name, p.models.map((m) => m.id))
    running.value = 'model'
    modelFail.value = ''
    const ok = action.run({
      action: 'llm.provider', args: { op: 'model_set', id: p.id, model: key, spec: newModelSpec(name) }, title: title.value, quiet: true,
      what: `Добавить модель ${name}. Цена по умолчанию нулевая, возможности выключены: цену нужно сверить с тарифом.`,
      onDone: () => {
        running.value = null
        pick.value = ''
        modelWait.begin(() => modelAddReflects(row.value, name))
        if (store.mocked) store.mockExpertApply((s) => mockModelSet(s, p.id, key, name))
      },
      onFail: (t, f) => { running.value = null; modelFail.value = f?.code === 'E_ARGS' && f.msg ? `${t} Платформа ответила: ${f.msg.replace(/\s+/g, ' ').slice(0, 200)}` : t },
    })
    if (!ok) running.value = null
    return ok
  }

  return {
    row, canEdit, busy, ready, running, models, modelsFail, query, filtered, fetchModels, ping, pingFail, runPing,
    pick, pickError, canAddModel, modelWait, modelFail, choose, addModel,
  }
}

export type ProviderNet = ReturnType<typeof useProviderNet>
