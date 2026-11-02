import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { failureText } from '@/shared/lib'
import { buttonKind, dayArgs } from '../lib/flow'
import type { ChallengeData, CtlHello, CtlResult, CtlSnapshot, DayMode, FlowPhase } from './types'

/** Пример для экранов отладки: id дня в формате платформы, чтобы подпись читалась как «2 ноября», а не «торговый день». */
const MOCK_DAY = 'D-20261102-1'

/**
 * Состояние управления днём: тема `ctl`, hello.ctl (csrf соединения) и запрос условий запуска для выбранного режима (проверки окна запуска).
 * Сами команды (запуск и остановка дня) идут через общий движок действий entities/action, здесь их нет. Только paper, testnet, observe: остальное сервер отклоняет (E_ELEVATE).
 */
export const useControlStore = defineStore('control', () => {
  const ctl = ref<CtlSnapshot | null>(null)
  const hello = ref<CtlHello>({ enabled: false })
  /** Окно запуска и остановки дня открыто: одно на приложение, открыть его можно и с кнопки в колонке, и со страницы «Управление». */
  const dialogOpen = ref(false)

  const phase = ref<FlowPhase>('idle')
  const mode = ref<DayMode>('paper')
  const challenge = ref<ChallengeData | null>(null)
  const error = ref<string | null>(null)
  const pending = ref<string | null>(null)        // id нашего запроса условий, ответ на который ждём
  let seq = 0
  let started = false

  const button = computed(() => buttonKind(ctl.value, hello.value.enabled))

  let mocked = false

  function applyCtl(m: LiveMessage): void {
    if (mocked) return
    ctl.value = m.data as CtlSnapshot
    // платформа уже запущена не нами (или после перезагрузки страницы): подстраиваем фазу под реальное состояние
    const st = ctl.value.platform.state
    if (st === 'starting' && phase.value !== 'starting') phase.value = 'starting'
    else if (st !== 'starting' && phase.value === 'starting') phase.value = 'idle'        // запуск кончился (успехом или нет): само окно покажет по состоянию платформы
  }

  function applyHello(m: LiveMessage): void {
    if (mocked) return
    const d = (m.data as { ctl?: CtlHello }).ctl
    hello.value = d && typeof d.enabled === 'boolean' ? d : { enabled: false }
  }

  /** Здесь только ответ на запрос условий запуска (challenge). Сами команды и их итоги ведёт общий движок entities/action. */
  function applyResult(m: LiveMessage): void {
    const r = m.data as CtlResult
    if (r.id !== pending.value) return
    pending.value = null
    challenge.value = r.data ?? null
    if (r.code === 'OK') phase.value = 'ready'
    else if (r.code === 'E_GATE' || r.code === 'E_STATE') {
      phase.value = 'blocked'
      error.value = failureText(r.code, r.msg)
    } else {
      phase.value = 'failed'
      error.value = failureText(r.code, r.msg)
    }
  }

  function start(): void {
    if (started) return
    started = true
    const { socket } = useLive()
    socket.subscribe('hello', applyHello)
    socket.subscribe('ctl', applyCtl)
    socket.subscribe('ctl_result', applyResult)
  }

  function sendChallenge(action: string, args: Record<string, unknown>): boolean {
    if (!hello.value.enabled || !hello.value.csrf) {
      error.value = 'Управление из этого дашборда недоступно.'
      phase.value = 'failed'
      return false
    }
    const id = `c${++seq}`
    const ok = useLive().socket.send({ op: 'ctl', id, csrf: hello.value.csrf, kind: 'challenge', action, args })
    if (!ok) {
      error.value = failureText('E_LINK')
      phase.value = 'failed'
      return false
    }
    pending.value = id
    return true
  }

  /** Выбран режим: спрашиваем у ctl условия старта (проверки, предупреждения). Ничего не запускает. */
  function check(next: DayMode): void {
    if (mocked) return
    mode.value = next
    challenge.value = null
    error.value = null
    phase.value = 'checking'
    sendChallenge('day.start', dayArgs(next))
  }

  function reset(): void {
    phase.value = 'idle'
    challenge.value = null
    error.value = null
    pending.value = null
  }

  /** Только для экрана-примера (?start=mock): подставляет ход запуска без сервера. */
  function mock(kind: 'launch' | 'pick' | 'done' = 'launch'): void {
    mocked = true
    hello.value = { enabled: true, csrf: 'mock' }
    const launch = kind === 'launch'
    ctl.value = {
      alive: true, configured: true, platform: { state: launch ? 'starting' : kind === 'done' ? 'running' : 'stopped', mode: 'paper', session: 'day', health: 'green', day_id: launch ? null : MOCK_DAY, trading: kind === 'done', observe: false },
      step: launch ? { name: 'day' } : null, kill: null, risk: { level: 3, name: 'Базовый' }, busy: launch, checks: [], banner: null,
      day: { day_id: MOCK_DAY, state: launch ? 'starting' : 'running', mode: 'paper', steps: (launch ? ['S1', 'S2', 'S3', 'S4', 'S5'] : kind === 'done' ? ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9'] : []).map((name) => ({ name, phase: 'start', ok: true, code: null })) },
    }
    mode.value = 'paper'
    if (kind === 'pick') {
      challenge.value = {
        gates: [{ id: 'H1', name: 'Чистый KILL', ok: true }, { id: 'H3', name: 'Связь', ok: true, msg: 'исполнитель жив, сверка свежая' }, { id: 'H6', name: 'Мозг доступен', ok: true }],
        warnings: [{ id: 'W1', code: 'W_PAPER_HISTORY', msg: 'Мало paper-истории: рекомендуется 30 сделок и 7 дней' }], blocked: false,
      }
      phase.value = 'ready'
    } else {
      phase.value = launch ? 'starting' : 'done'
    }
  }

  /** Отладка настроек (?edit=mock): платформа остановлена (running=false, режим paper) или идёт (режим testnet), уровень риска 3. Только в mocked-режиме. */
  let platformMock: boolean | null = null
  function mockPlatform(running: boolean): void {
    if (platformMock === running) return        // повторный вызов (блок и панель) не сбрасывает уже «применённые» значения
    platformMock = running
    mock(running ? 'done' : 'pick')
    if (ctl.value) ctl.value = { ...ctl.value, platform: { ...ctl.value.platform, mode: running ? 'testnet' : 'paper' } }
  }

  /** Отладка: «сервер» применяет смену уровня или режима через несколько секунд, как обновляется тема ctl. */
  function mockApply(p: { level?: number; mode?: string }, delayMs = 2500): void {
    if (!mocked) return
    setTimeout(() => {
      if (!ctl.value) return
      ctl.value = {
        ...ctl.value,
        risk: p.level !== undefined ? { level: p.level, name: null } : ctl.value.risk,
        platform: p.mode !== undefined ? { ...ctl.value.platform, mode: p.mode } : ctl.value.platform,
      }
    }, delayMs)
  }

  /** Открыть окно: показывает текущее состояние, а не прошлый итог. */
  function openDialog(): void {
    if (phase.value === 'done' || phase.value === 'failed') reset()
    dialogOpen.value = true
  }

  return { ctl, hello, dialogOpen, openDialog, phase, mode, challenge, error, pending, button, start, check, reset, mock, mockPlatform, mockApply, applyCtl, applyHello, applyResult }
})
