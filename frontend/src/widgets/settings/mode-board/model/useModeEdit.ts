import { computed, ref } from 'vue'
import { useActionStore } from '@/entities/action'
import { modeArgs, modeBlock, modeEffect, modeLine, modeReflects, modeTitle, saferModes, useControlStore } from '@/entities/control'
import { useReflect } from '@/shared/lib'

/**
 * Правка режима платформы (mode.set): только на более безопасный режим и только пока идёт день. Идёт через общий движок (диалог «Принимаю риски» решает сервер).
 * Выбор копится черновиком со строкой «было → станет»; после успеха ждём, пока тема ctl отразит изменение. При отладке (?edit=mock) ответы изображает движок.
 */
export function useModeEdit() {
  const control = useControlStore()
  const action = useActionStore()
  const platform = computed(() => control.ctl?.platform ?? null)
  const busy = computed(() => action.active)
  const mode = computed(() => platform.value?.mode ?? null)
  const canMode = computed(() => action.can('mode.set'))
  const modeWhy = computed(() => modeBlock(platform.value?.state, mode.value))
  const safer = computed(() => saferModes(mode.value))
  const modeDraft = ref<string | null>(null)
  const modeTo = computed(() => (modeDraft.value && safer.value.includes(modeDraft.value) ? modeDraft.value : null))
  const modeChange = computed(() => (modeTo.value == null ? null : { line: modeLine(mode.value, modeTo.value), effect: modeEffect(modeTo.value) }))
  const modeWait = useReflect()

  function pickMode(m: string): void {
    if (busy.value || !safer.value.includes(m)) return
    modeDraft.value = modeDraft.value === m ? null : m
  }
  function submitMode(): boolean {
    const to = modeTo.value
    if (busy.value || to == null || modeWhy.value) return false
    return action.run({
      action: 'mode.set', args: modeArgs(to), title: modeTitle(to), what: modeEffect(to),
      onDone: () => {
        modeDraft.value = null
        modeWait.begin(() => modeReflects(control.ctl?.platform.mode, to))
        control.mockApply({ mode: to })
      },
    })
  }

  return { busy, mode, canMode, modeWhy, safer, modeDraft, modeChange, modeWait, pickMode, submitMode, resetMode: () => { modeDraft.value = null } }
}

export type ModeEdit = ReturnType<typeof useModeEdit>
