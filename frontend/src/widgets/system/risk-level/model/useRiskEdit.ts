import { computed, ref } from 'vue'
import { useActionStore } from '@/entities/action'
import { useControlStore } from '@/entities/control'
import { RISK_LEVELS, canApplyLevel, riskArgs, riskEffect, riskLine, riskReflects, riskTitle } from '@/entities/risk'
import { useReflect } from '@/shared/lib'

/**
 * Правка уровня риска (risk.set) рядом с запуском дня. Идёт через общий движок: повышение рискованно и откроет единый диалог «Принимаю риски», понижение применится сразу.
 * Выбор копится черновиком со строкой «было → станет»; после успеха ждём, пока тема ctl отразит уровень. При отладке (?edit=mock) ответы изображает движок.
 */
export function useRiskEdit() {
  const control = useControlStore()
  const action = useActionStore()
  const busy = computed(() => action.active)
  const level = computed(() => control.ctl?.risk.level ?? null)
  const serverName = computed(() => control.ctl?.risk.name ?? null)
  const canRisk = computed(() => action.can('risk.set'))
  const draft = ref<number | null>(null)
  const to = computed(() => (canApplyLevel(level.value, draft.value) ? draft.value : null))
  /** Выбранный в списке уровень: черновик или текущий. */
  const shown = computed(() => draft.value ?? level.value)
  const change = computed(() => (to.value == null ? null : { line: riskLine(level.value, to.value, serverName.value), effect: riskEffect(level.value, to.value), up: level.value != null && to.value > level.value }))
  const wait = useReflect()

  function pick(n: number): void {
    if (busy.value || !RISK_LEVELS.includes(n)) return
    draft.value = n === level.value ? null : n
  }
  function submit(): boolean {
    const target = to.value
    if (busy.value || target == null) return false
    return action.run({
      action: 'risk.set', args: riskArgs(target), title: riskTitle(target), what: riskEffect(level.value, target),
      onDone: () => {
        draft.value = null
        wait.begin(() => riskReflects(control.ctl?.risk.level, target))
        control.mockApply({ level: target })
      },
    })
  }

  return { busy, level, serverName, canRisk, draft, shown, change, wait, pick, submit, reset: () => { draft.value = null } }
}

export type RiskEdit = ReturnType<typeof useRiskEdit>
