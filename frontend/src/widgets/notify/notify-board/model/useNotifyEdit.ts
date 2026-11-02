import { computed, ref, watch } from 'vue'
import { useActionStore } from '@/entities/action'
import {
  changeLines, draftErrors, draftOf, effectText, patchOf, sameSettings, useNotifyStore, type NotifyDraft, type NotifySettings,
} from '@/entities/notify'
import { useReflect } from '@/shared/lib'

/**
 * Правка уведомлений: черновик, «было → станет», одно действие notify.set (безрисковое: без диалога, итог приходит уведомлением) и пробное уведомление (notify.test).
 * Пока идёт обновление темы, «было» берётся из отправленного, поэтому панель изменений не мигает старым значением. Пока человек не менял, черновик следует за темой.
 * Реальных команд при отладке (?notify=mock...) нет: ответы изображает движок действий, тему «обновляет» стор.
 */
export function useNotifyEdit() {
  const store = useNotifyStore()
  const action = useActionStore()
  const busy = computed(() => action.active)
  const snap = computed(() => store.snapshot)
  const server = computed(() => store.snapshot?.settings ?? null)
  /** отправленное, но ещё не отразившееся в теме */
  const applied = ref<NotifySettings | null>(null)
  const base = computed(() => applied.value ?? server.value)
  const draft = ref<NotifyDraft | null>(null)
  const wait = useReflect()

  watch(base, (b, old) => {
    if (!b) return
    if (!draft.value || (old && sameSettings(draft.value, old))) draft.value = draftOf(b)
  }, { immediate: true })

  const canEdit = computed(() => action.can('notify.set'))
  const canTest = computed(() => action.can('notify.test'))
  const errors = computed(() => (draft.value ? draftErrors(draft.value) : {}))
  const hasErrors = computed(() => Object.keys(errors.value).length > 0)
  const patch = computed(() => (base.value && draft.value ? patchOf(base.value, draft.value) : null))
  const lines = computed(() => (base.value && draft.value && !hasErrors.value ? changeLines(base.value, draft.value, snap.value?.categories ?? []) : []))
  const dirty = computed(() => !!base.value && !!draft.value && !sameSettings(base.value, draft.value))
  const effect = computed(() => (base.value && draft.value ? effectText(base.value, draft.value) : ''))

  function submit(): boolean {
    const p = patch.value
    const to = draft.value ? draftOf(draft.value) : null
    if (busy.value || !p || !to || !canEdit.value) return false
    return action.run({
      action: 'notify.set', args: { ...p }, title: 'Настройки уведомлений', what: effect.value,
      onDone: () => {
        applied.value = to
        wait.begin(() => {
          if (server.value && sameSettings(server.value, to)) { applied.value = null; return true }
          return false
        })
        if (store.mocked) store.mockApply(to)
      },
    })
  }

  function reset(): void {
    if (base.value) draft.value = draftOf(base.value)
  }

  /** Пробное уведомление: показывает одно настоящее уведомление на экране компьютера. */
  function test(): boolean {
    if (busy.value || !canTest.value) return false
    return action.run({ action: 'notify.test', args: {}, title: 'Пробное уведомление', what: 'Покажет одно настоящее уведомление на экране компьютера.' })
  }

  return { snap, draft, base, busy, canEdit, canTest, errors, hasErrors, dirty, lines, effect, wait, submit, reset, test }
}

export type NotifyEdit = ReturnType<typeof useNotifyEdit>
