import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { buttonKind, startArgs } from '../lib/flow'
import type { ChallengeData, CtlFinal, CtlHello, CtlResult, CtlSnapshot, DayMode, FlowPhase } from './types'

/**
 * Управление днём: тема `ctl` (состояние), hello.ctl (csrf соединения), команды по сокету. Только paper, testnet, observe: остальное сервер отклоняет (E_ELEVATE).
 * Ключей и токенов в браузере нет: команда уходит с CSRF соединения, подписывает и ставит её в очередь сервер.
 */
export const useControlStore = defineStore('control', () => {
  const ctl = ref<CtlSnapshot | null>(null)
  const hello = ref<CtlHello>({ enabled: false })
  /** Окно запуска и остановки дня открыто: одно на приложение, открыть его можно и с кнопки в колонке, и со страницы «Управление». */
  const dialogOpen = ref(false)

  const phase = ref<FlowPhase>('idle')
  const mode = ref<DayMode>('paper')
  const challenge = ref<ChallengeData | null>(null)
  const accepted = ref<Set<string>>(new Set())
  const error = ref<string | null>(null)
  const final = ref<CtlFinal | null>(null)
  const pending = ref<string | null>(null)        // id нашего запроса, ответ на который ждём
  const lastAction = ref<'day.start' | 'day.stop' | null>(null)        // последняя поставленная в очередь команда: от неё зависит текст итога
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
    if (st === 'running' && phase.value === 'starting' && !pending.value && final.value === null) phase.value = 'done'
  }

  function applyHello(m: LiveMessage): void {
    if (mocked) return
    const d = (m.data as { ctl?: CtlHello }).ctl
    hello.value = d && typeof d.enabled === 'boolean' ? d : { enabled: false }
  }

  function applyResult(m: LiveMessage): void {
    const r = m.data as CtlResult
    if (r.id !== pending.value) return
    pending.value = null
    if (r.kind === 'challenge') {
      challenge.value = r.data ?? null
      accepted.value = new Set()
      if (r.code === 'OK') phase.value = 'ready'
      else if (r.code === 'E_GATE' || r.code === 'E_STATE') {
        phase.value = 'blocked'
        error.value = r.msg
      } else {
        phase.value = 'failed'
        error.value = r.msg
      }
      return
    }
    if (r.ok) {
      lastAction.value = r.action === 'day.stop' ? 'day.stop' : 'day.start'
      phase.value = r.action === 'day.stop' ? 'stopping' : 'starting'
      error.value = null
      return
    }
    phase.value = 'failed'
    error.value = r.msg
  }

  function applyFinal(m: LiveMessage): void {
    const f = m.data as CtlFinal
    final.value = f
    if (f.code === 'OK' && f.status === 'done') {
      phase.value = 'done'
      error.value = null
    } else {
      phase.value = 'failed'
      error.value = f.msg || f.code
    }
  }

  function start(): void {
    if (started) return
    started = true
    const { socket } = useLive()
    socket.subscribe('hello', applyHello)
    socket.subscribe('ctl', applyCtl)
    socket.subscribe('ctl_result', applyResult)
    socket.subscribe('ctl_final', applyFinal)
  }

  function send(kind: 'challenge' | 'command', action: string, args: Record<string, unknown>): boolean {
    if (!hello.value.enabled || !hello.value.csrf) {
      error.value = 'управление из этого дашборда недоступно'
      phase.value = 'failed'
      return false
    }
    const id = `c${++seq}`
    const ok = useLive().socket.send({ op: 'ctl', id, csrf: hello.value.csrf, kind, action, args })
    if (!ok) {
      error.value = 'нет связи с сервером'
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
    accepted.value = new Set()
    error.value = null
    final.value = null
    phase.value = 'checking'
    send('challenge', 'day.start', { mode: next, session: 'day' })
  }

  function toggleAccept(id: string): void {
    const next = new Set(accepted.value)
    if (!next.delete(id)) next.add(id)
    accepted.value = next
  }

  function begin(): void {
    if (phase.value !== 'ready') return
    final.value = null
    send('command', 'day.start', startArgs(mode.value, challenge.value))
  }

  function stopDay(): void {
    error.value = null
    final.value = null
    send('command', 'day.stop', { mode: 'soft' })
  }

  function reset(): void {
    phase.value = 'idle'
    challenge.value = null
    accepted.value = new Set()
    error.value = null
    final.value = null
    pending.value = null
  }

  /** Только для экрана-примера (?start=mock): подставляет ход запуска без сервера. */
  function mock(kind: 'launch' | 'pick' | 'done' = 'launch'): void {
    mocked = true
    hello.value = { enabled: true, csrf: 'mock' }
    const launch = kind === 'launch'
    ctl.value = {
      alive: true, configured: true, platform: { state: launch ? 'starting' : kind === 'done' ? 'running' : 'stopped', mode: 'paper', session: 'day', health: 'green', day_id: launch ? null : 'D-mock', trading: kind === 'done', observe: false },
      step: launch ? { name: 'day' } : null, kill: null, risk: { level: 3, name: 'Базовый' }, busy: launch, checks: [], banner: null,
      day: { day_id: 'D-mock', state: launch ? 'starting' : 'running', mode: 'paper', steps: (launch ? ['S1', 'S2', 'S3', 'S4', 'S5'] : kind === 'done' ? ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9'] : []).map((name) => ({ name, phase: 'start', ok: true, code: null })) },
    }
    mode.value = 'paper'
    if (kind === 'pick') {
      challenge.value = {
        gates: [{ id: 'H1', name: 'Чистый KILL', ok: true }, { id: 'H3', name: 'Связь', ok: true, msg: 'исполнитель жив, сверка свежая' }, { id: 'H6', name: 'Мозг доступен', ok: true }],
        warnings: [{ id: 'W1', code: 'W_PAPER_HISTORY', msg: 'Мало paper-истории: рекомендуется 30 сделок и 7 дней' }], blocked: false,
      }
      phase.value = 'ready'
    } else {
      lastAction.value = 'day.start'
      phase.value = launch ? 'starting' : 'done'
    }
  }

  /** Открыть окно: показывает текущее состояние, а не прошлый итог. */
  function openDialog(): void {
    if (phase.value === 'done' || phase.value === 'failed') reset()
    dialogOpen.value = true
  }

  return { ctl, hello, dialogOpen, openDialog, phase, mode, challenge, accepted, error, final, pending, lastAction, button, start, check, toggleAccept, begin, stopDay, reset, mock, applyCtl, applyHello, applyResult, applyFinal }
})
