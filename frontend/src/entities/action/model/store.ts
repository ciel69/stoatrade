import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { FINAL_TIMEOUT_MS, TIMEOUT_TEXT, buildCommand, isExpired, isRisky, risksOf, secondsLeft, unavailableReason } from '../lib/flow'
import type { ActionFinal, ActionHello, ActionPhase, ActionResult, ActionSpec, ActionToast, Challenge } from './types'

type Transport = (payload: Record<string, unknown>) => boolean
export type ActionMock = 'confirm' | 'blocked' | 'done' | 'fail' | 'toast' | 'full'

const ACTIVE: ReadonlySet<ActionPhase> = new Set(['checking', 'confirm', 'blocked', 'sending', 'waiting'])
const MAX_TOASTS = 3

/**
 * Общий движок действий управления. Один запуск за раз: challenge (условия), затем либо сразу команда (если рисков нет), либо диалог «Принимаю риски» и команда с nonce и фразами из challenge.
 * Ключей и токенов нет: команду подписывает и ставит в очередь сервер, здесь только CSRF соединения из hello.ctl. Доступность действия читается из hello.ctl.full и hello.ctl.modes.
 * Запросы идут со своими id (префикс `a`), поток запуска дня (entities/control, id `c`) не пересекается.
 */
export const useActionStore = defineStore('action', () => {
  const hello = ref<ActionHello>({ enabled: false })
  const phase = ref<ActionPhase>('idle')
  const spec = ref<ActionSpec | null>(null)
  const challenge = ref<Challenge | null>(null)
  const receivedAt = ref(0)                      // когда пришёл challenge (мс): от него считаются пауза min_delay и срок expires_in
  const error = ref<string | null>(null)
  const resultText = ref('')
  /** Рискованный ли запуск: null пока challenge не пришёл. От этого зависит поверхность: диалог (true, blocked) или уведомление (false, null). */
  const riskyRun = ref<boolean | null>(null)
  const toasts = ref<ActionToast[]>([])

  let seq = 0
  let toastSeq = 0
  let started = false
  let mocked = false
  let pendingId: string | null = null           // запрос, ответ `ctl_result` на который ждём
  let cmdId: string | null = null               // принятая команда, итог `ctl_final` по которой ждём
  let timer: ReturnType<typeof setTimeout> | undefined
  let transport: Transport = (payload) => useLive().socket.send(payload)

  const active = computed(() => ACTIVE.has(phase.value))
  const risks = computed(() => risksOf(challenge.value))

  function start(): void {
    if (started) return
    started = true
    const { socket } = useLive()
    socket.subscribe('hello', applyHello)
    socket.subscribe('ctl_result', applyResult)
    socket.subscribe('ctl_final', applyFinal)
  }

  function applyHello(m: LiveMessage): void {
    if (mocked) return
    const d = (m.data as { ctl?: ActionHello }).ctl
    hello.value = d && typeof d.enabled === 'boolean' ? d : { enabled: false }
  }

  /** Можно ли запустить действие в этом дашборде (для показа кнопок). */
  function can(action: string): boolean {
    return unavailableReason(hello.value, action) === null
  }

  function pushToast(tone: ActionToast['tone'], title: string, text: string): void {
    toasts.value = [...toasts.value, { id: ++toastSeq, tone, title, text }].slice(-MAX_TOASTS)
  }

  function dismissToast(id: number): void {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }

  function clearTimer(): void {
    clearTimeout(timer)
    timer = undefined
  }

  /** Конец запуска. Безрисковый запуск сообщает итог уведомлением, рискованный остаётся в диалоге (done или failed). */
  function finish(ok: boolean, text: string): void {
    clearTimer()
    pendingId = null
    cmdId = null
    phase.value = ok ? 'done' : 'failed'
    resultText.value = ok ? text : ''
    error.value = ok ? null : text
    const s = spec.value
    if (riskyRun.value !== true) pushToast(ok ? 'ok' : 'bad', s?.title ?? 'Действие', text)
    if (ok) s?.onDone?.()
  }

  function send(kind: 'challenge' | 'command', action: string, args: Record<string, unknown>, extra: Record<string, unknown> = {}): string | null {
    const id = `a${++seq}`
    const ok = !!hello.value.csrf && transport({ op: 'ctl', id, csrf: hello.value.csrf, kind, action, args, ...extra })
    if (!ok) {
      finish(false, 'нет связи с сервером')
      return null
    }
    return id
  }

  /** Запустить действие. false: не запущено (занято или недоступно), причина в уведомлении или в error. */
  function run(next: ActionSpec): boolean {
    if (active.value) {
      pushToast('bad', next.title, `Сейчас выполняется «${spec.value?.title ?? 'другое действие'}»: дождитесь завершения.`)
      return false
    }
    spec.value = next
    challenge.value = null
    error.value = null
    resultText.value = ''
    riskyRun.value = null
    pendingId = cmdId = null
    const why = unavailableReason(hello.value, next.action)
    if (why) {
      finish(false, why)
      return false
    }
    phase.value = 'checking'
    pendingId = send('challenge', next.action, next.args)
    return pendingId !== null
  }

  /** Условия получены: заблокировано, нужно подтверждение рисков или можно отправлять сразу. */
  function onChallenge(r: ActionResult): void {
    const blockedCode = r.code === 'E_GATE' || r.code === 'E_STATE'
    if (!r.ok && !blockedCode) return finish(false, r.msg || r.code)
    if (!r.data) {
      if (blockedCode) { phase.value = 'blocked'; error.value = r.msg; return }
      return finish(false, 'ctl не вернул условия выполнения')
    }
    challenge.value = r.data
    receivedAt.value = Date.now()
    if (blockedCode || r.data.blocked) {
      phase.value = 'blocked'
      error.value = r.msg || null
      return
    }
    riskyRun.value = isRisky(r.data)
    if (riskyRun.value) phase.value = 'confirm'
    else dispatch()
  }

  /** Отправить команду: nonce и фразы берутся из challenge, пользователь ничего не вводит. */
  function dispatch(): void {
    const s = spec.value
    if (!s) return
    const cmd = buildCommand(s, challenge.value)
    phase.value = 'sending'
    const extra: Record<string, unknown> = {}
    if (cmd.nonce !== undefined) extra.nonce = cmd.nonce
    if (cmd.phrases !== undefined) extra.phrases = cmd.phrases
    const id = send('command', s.action, cmd.args, extra)
    if (id === null) return
    pendingId = cmdId = id
    clearTimer()
    timer = setTimeout(() => {
      if (phase.value === 'sending' || phase.value === 'waiting') finish(false, TIMEOUT_TEXT)
    }, FINAL_TIMEOUT_MS)
  }

  function applyResult(m: LiveMessage): void {
    const r = m.data as ActionResult
    if (r.id !== pendingId) return
    pendingId = null
    if (r.kind === 'challenge') return onChallenge(r)
    if (!r.ok) return finish(false, r.msg || r.code)
    if (phase.value === 'sending') phase.value = 'waiting'
  }

  function applyFinal(m: LiveMessage): void {
    const f = m.data as ActionFinal
    if (f.rid !== cmdId || (phase.value !== 'sending' && phase.value !== 'waiting')) return
    if (f.status === 'done') finish(true, f.msg || 'выполнено')
    else finish(false, f.msg || f.code || f.status)
  }

  /** «Принимаю риски»: после паузы min_delay отправляет команду; если срок challenge вышел, сам запрашивает условия заново. */
  function accept(): void {
    if (phase.value !== 'confirm' || !spec.value) return
    const ch = challenge.value
    const now = Date.now()
    if (isExpired(ch?.expires_in, receivedAt.value, now)) return recheck()
    if (secondsLeft(ch?.min_delay, receivedAt.value, now) > 0) return
    dispatch()
  }

  /** Повторный challenge (срок вышел). Диалог остаётся на экране. */
  function recheck(): void {
    const s = spec.value
    if (!s || (phase.value !== 'confirm' && phase.value !== 'blocked')) return
    challenge.value = null
    phase.value = 'checking'
    pendingId = send('challenge', s.action, s.args)
  }

  /** Отмена до отправки команды (проверка, подтверждение, блокировка). Позднее ответ сервера игнорируется. */
  function cancel(): void {
    if (phase.value !== 'checking' && phase.value !== 'confirm' && phase.value !== 'blocked') return
    pendingId = null
    clearTimer()
    phase.value = 'idle'
    challenge.value = null
    riskyRun.value = null
  }

  /** Закрыть итог (done, failed). Идущее выполнение не сбрасывается. */
  function reset(): void {
    if (phase.value !== 'done' && phase.value !== 'failed') return
    phase.value = 'idle'
    error.value = null
    resultText.value = ''
    riskyRun.value = null
  }

  // ---- отладка без сервера (?action=mock-...): реальных отправок нет, ответы сервера изображает mockTransport

  function mockChallenge(kind: ActionMock, args: Record<string, unknown>): Challenge {
    const gates = [
      { id: 'G1', name: 'Исполнитель жив', ok: true }, { id: 'G2', name: 'Сверка свежая', ok: true, msg: '4 с назад' },
      { id: 'G3', name: 'Чистый KILL', ok: true }, { id: 'G4', name: 'Связь с биржей', ok: true },
    ]
    if (kind === 'blocked') {
      return { blocked: true, gates: [{ id: 'G1', name: 'Исполнитель жив', ok: true }, { id: 'G2', name: 'Сверка свежая', ok: false, msg: 'последняя сверка 3 минуты назад' }, { id: 'G3', name: 'Нет открытых позиций', ok: false, msg: 'открыта позиция BTCUSDT, сначала закройте её' }], warnings: [], pre_amber: [], danger: false }
    }
    if (kind === 'full' && args.flatten !== true) return { gates, warnings: [], pre_amber: [], danger: false, phrases: [], blocked: false, nonce: 'mock', min_delay: 0, expires_in: 60 }
    return {
      danger: true, blocked: false, gates, phrases: ['mock'], nonce: 'mock', min_delay: 3, expires_in: 90,
      warnings: [{ id: 'W1', code: 'W_RISK_UP', msg: 'Уровень риска 5: размер позиции до 25% счёта, плечо до 10x.' }, { id: 'W2', code: 'W_OPEN', msg: 'Открытая позиция BTCUSDT будет закрыта по рынку, возможно проскальзывание.' }],
      pre_amber: [{ id: 'P1', name: 'Мало paper-истории', msg: 'рекомендуется 30 сделок и 7 дней' }],
    }
  }

  function mockTransport(kind: ActionMock): Transport {
    return (p) => {
      const base = { id: p.id as string, action: p.action as string }
      setTimeout(() => {
        const live = (data: unknown) => ({ topic: 'x', seq: 0, ts: 0, data })
        if (p.kind === 'challenge') {
          applyResult(live({ ...base, kind: 'challenge', ok: true, code: kind === 'blocked' ? 'E_GATE' : 'OK', msg: kind === 'blocked' ? 'условия не выполнены' : '', data: mockChallenge(kind, p.args as Record<string, unknown>) }))
        } else {
          applyResult(live({ ...base, kind: 'command', ok: true, code: 'OK', msg: 'принято' }))
          setTimeout(() => applyFinal(live({ id: 'q1', rid: base.id, status: 'done', code: 'OK', msg: 'Выполнено (пример: на сервер ничего не отправлялось).' })), 700)
        }
      }, 120)
      return true
    }
  }

  /** Только для экрана-примера: подставляет расширенное управление и показывает нужное состояние. На сервер ничего не уходит. */
  function mock(kind: ActionMock): void {
    mocked = true
    hello.value = { enabled: true, csrf: 'mock', full: true, modes: ['day.start', 'day.stop', 'kill.set', 'risk.set', 'brain.use', 'brain.rollback', 'brain.reload', 'llmkey'] }
    transport = mockTransport(kind)
    const demo: ActionSpec = { action: 'risk.set', args: { level: 5 }, title: 'Сменить уровень риска на 5', what: 'Лимиты размера и плеча станут шире; действует на новые входы, открытые позиции не меняются.' }
    if (kind === 'confirm') run(demo)
    else if (kind === 'blocked') run({ action: 'day.stop', args: { mode: 'soft' }, title: 'Остановить торговый день', what: 'Новые входы прекратятся, день завершится штатно.' })
    else if (kind === 'done') {
      spec.value = demo
      riskyRun.value = true
      phase.value = 'done'
      resultText.value = 'Уровень риска 5 применён.'
    } else if (kind === 'fail') {
      spec.value = demo
      riskyRun.value = true
      phase.value = 'failed'
      error.value = 'E_RATE: слишком частые команды, повторите через несколько секунд.'
    } else if (kind === 'toast') {
      pushToast('ok', 'Аварийная остановка', 'Торговля остановлена, позиции не тронуты.')
      pushToast('bad', 'Остановка дня', 'E_STATE: день уже завершается.')
    }
  }

  return { hello, phase, spec, challenge, receivedAt, error, resultText, riskyRun, toasts, active, risks, start, can, run, accept, recheck, cancel, reset, dismissToast, mock, applyHello, applyResult, applyFinal }
})
