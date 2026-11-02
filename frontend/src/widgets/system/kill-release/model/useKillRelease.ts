import { computed, onScopeDispose, ref } from 'vue'
import { useActionStore } from '@/entities/action'
import { RELEASE_CHECKS, RELEASE_CHECKS_LIVE, RELEASE_COMMAND, RELEASE_TITLE, killCleared, positionsLine, reasonLine, releaseNote, releaseWhat, useControlStore } from '@/entities/control'
import { useSystemStore } from '@/entities/system'
import { copyText, useReflect } from '@/shared/lib'

/**
 * Снятие аварийной остановки (kill.clear) на странице «Система · Управление». Данные берём из темы ctl (KILL, режим, флаг кнопки) и system (сколько позиций открыто).
 * Нажатие идёт через общий движок: он спрашивает условия, показывает единое окно «Принимаю риски» (фразы и nonce подставляет сам) и отправляет команду. После успеха ждём,
 * пока тема ctl покажет, что остановка снята. Без расширенного управления или при выключенном флаге остаётся инструкция с командой для терминала.
 */
export function useKillRelease() {
  const control = useControlStore()
  const action = useActionStore()
  const sys = useSystemStore()
  const wait = useReflect()

  /** Отладка (?kill=mock...): число позиций подставляется, а не берётся из темы system. undefined: не отладка. */
  const mockPositions = ref<number | null | undefined>(undefined)
  const kill = computed(() => control.ctl?.kill ?? null)
  const active = computed(() => !!kill.value)
  /** Живые деньги: режим платформы live. Это подсказка до нажатия; окно подтверждения опирается на режим исполнителя, который отдаёт сервер. */
  const live = computed(() => control.ctl?.platform.mode === 'live' || sys.snapshot?.mode === 'live')
  const positions = computed(() => (mockPositions.value !== undefined ? mockPositions.value : (sys.snapshot?.exec.positions ?? null)))
  const reason = computed(() => reasonLine(kill.value?.reason))
  const positionsText = computed(() => positionsLine(!!kill.value?.flatten, positions.value))
  const canClick = computed(() => action.can('kill.clear') && control.ctl?.clear_kill === true)
  const note = computed(() => releaseNote(action.can('kill.clear'), control.ctl?.clear_kill === true))
  const checks = computed(() => (live.value ? [...RELEASE_CHECKS, ...RELEASE_CHECKS_LIVE] : [...RELEASE_CHECKS]))
  const busy = computed(() => action.active)
  const shown = computed(() => !!control.ctl && (active.value || wait.state.value !== 'idle'))

  function release(): boolean {
    if (busy.value || !canClick.value || !kill.value) return false
    return action.run({
      action: 'kill.clear', args: {}, title: RELEASE_TITLE, what: releaseWhat(kill.value.reason, live.value),
      onDone: () => {
        wait.begin(() => killCleared(control.ctl))
        control.mockKillCleared()
      },
    })
  }

  const copied = ref<'idle' | 'ok' | 'fail'>('idle')
  let copyTimer: ReturnType<typeof setTimeout> | undefined
  async function copy(): Promise<void> {
    copied.value = (await copyText(RELEASE_COMMAND)) ? 'ok' : 'fail'
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { copied.value = 'idle' }, 4000)
  }
  onScopeDispose(() => clearTimeout(copyTimer))

  return { kill, active, live, positions, reason, positionsText, canClick, note, checks, busy, shown, wait, mockPositions, release, copy, copied, command: RELEASE_COMMAND }
}

export type KillRelease = ReturnType<typeof useKillRelease>
