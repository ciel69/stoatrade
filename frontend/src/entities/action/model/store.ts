import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useLive, type LiveMessage } from '@/shared/api'
import { failureText, plainify } from '@/shared/lib'
import { FINAL_TIMEOUT_MS, TIMEOUT_TEXT, blockReasons, buildCommand, acceptWait, timeoutText, isExpired, isIrreversible, isLiveStart, isRisky, risksOf, unavailableReason } from '../lib/flow'
import { createKeySender } from './key'
import type { KeyOutcome } from '../lib/key'
import { memoryChallenge, memoryDoneText, memoryFailure, memoryNeedsChallenge, memoryUnavailable } from '../lib/memory'
import type { ActionFinal, ActionHello, ActionPhase, ActionResult, ActionSpec, ActionToast, Challenge, ChallengeGate as ActionGateRow, MemoryActionResult, MemoryHello } from './types'

type Transport = (payload: Record<string, unknown>) => boolean
export type ActionMock = 'confirm' | 'blocked' | 'done' | 'fail' | 'toast' | 'full' | 'kill' | 'kill-live' | 'kill-blocked'

/** Образец итога models/ping для отладки без сервера (?edit=mock): local не отвечает по сети, deepseek даёт короткий список, остальные длинный усечённый. */
function mockNetFinal(op: string, id: string, model: unknown): { status: string; code: string; msg: string; data: Record<string, unknown> } {
  const done = (data: Record<string, unknown>) => ({ status: 'done', code: 'OK', msg: '', data })
  if (op === 'models') {
    if (id === 'local') return done({ ok: false, error: 'network', detail: 'connection refused' })
    const n = id === 'deepseek' ? 3 : 500
    const ids = id === 'deepseek' ? ['deepseek-chat', 'deepseek-reasoner', 'deepseek-v4-flash'] : Array.from({ length: n }, (_, i) => `acme/model-${String(i + 1).padStart(3, '0')}${i % 7 === 0 ? ':free' : ''}`)
    return done({ ok: true, ids, count: id === 'deepseek' ? 3 : 640, truncated: id !== 'deepseek', source: 'models', detail: '' })
  }
  if (id === 'local') return done({ ok: false, error: 'timeout', detail: 'timed out', model: String(model ?? 'gpt-oss-20b'), paid: true })
  return done({ ok: true, ms: 840, model: String(model ?? 'flash'), model_name: 'mock-model', paid: true, finish: 'stop', usage: { in: 9, in_hit: 0, in_write: 0, out: 2, reasoning: 0 }, text: 'pong' })
}

const ACTIVE: ReadonlySet<ActionPhase> = new Set(['checking', 'confirm', 'blocked', 'sending', 'waiting'])
const MAX_TOASTS = 3

/**
 * Общий движок действий управления. Один запуск за раз: challenge (условия), затем либо сразу команда (если рисков нет), либо диалог «Принимаю риски» и команда с nonce и фразами из challenge.
 * Ключей и токенов нет: команду подписывает и ставит в очередь сервер, здесь только CSRF соединения из hello.ctl. Доступность действия читается из hello.ctl.full и hello.ctl.modes.
 * Запросы идут со своими id (префикс `a`), поток запуска дня (entities/control, id `c`) не пересекается.
 * Аварийные действия «Памяти» (spec.channel = 'memory') идут тем же окном, но своим протоколом: op = действие, CSRF из hello.memory, id с префиксом `m`,
 * ответ `memory_action_result` на challenge и на команду (отдельного итога нет: ответ на команду и есть итог).
 */
export const useActionStore = defineStore('action', () => {
  const hello = ref<ActionHello>({ enabled: false })
  const memory = ref<MemoryHello>({})
  const phase = ref<ActionPhase>('idle')
  const spec = ref<ActionSpec | null>(null)
  const challenge = ref<Challenge | null>(null)
  const receivedAt = ref(0)                      // когда пришёл challenge (мс): от него считаются пауза min_delay и срок expires_in
  const error = ref<string | null>(null)
  /** Исходный код и текст сервера последнего отказа: только для «Подробностей для специалиста». */
  const errorDetails = ref<string | null>(null)
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
  const risks = computed(() => risksOf(challenge.value, isIrreversible(spec.value?.action, spec.value?.args), isLiveStart(spec.value?.action, spec.value?.args)))
  /** Запись ключа провайдера: напрямую, без challenge. Отправитель не хранит значение, в состоянии стора его нет. */
  const keys = createKeySender({ csrf: () => hello.value.csrf, send: (p) => transport(p) })
  const sendKey = (provider: string, value: string): Promise<KeyOutcome> => keys.send(provider, value)

  function start(): void {
    if (started) return
    started = true
    const { socket } = useLive()
    socket.subscribe('hello', applyHello)
    socket.subscribe('ctl_result', applyResult)
    socket.subscribe('ctl_final', applyFinal)
    socket.subscribe('memory_action_result', applyMemoryResult)
  }

  function applyHello(m: LiveMessage): void {
    const data = (m.data ?? {}) as { ctl?: ActionHello; memory?: MemoryHello }
    const mem = data.memory
    memory.value = mem && typeof mem === 'object' ? mem : {}
    if (mocked) return
    const d = data.ctl
    hello.value = d && typeof d.enabled === 'boolean' ? d : { enabled: false }
  }

  /** Можно ли запустить аварийное действие «Памяти» (для показа кнопок). */
  function canMemory(action: string): boolean {
    return memoryUnavailable(memory.value, action) === null
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
  function finish(ok: boolean, text: string, details: string | null = null, final?: ActionFinal): void {
    clearTimer()
    pendingId = null
    cmdId = null
    phase.value = ok ? 'done' : 'failed'
    resultText.value = ok ? text : ''
    error.value = ok ? null : text
    errorDetails.value = ok ? null : details
    const s = spec.value
    if (riskyRun.value !== true && !s?.quiet) pushToast(ok ? 'ok' : 'bad', s?.title ?? 'Действие', text)
    if (ok) s?.onDone?.(final)
    else s?.onFail?.(text, final)
  }

  function send(kind: 'challenge' | 'command', action: string, args: Record<string, unknown>, extra: Record<string, unknown> = {}): string | null {
    const mem = spec.value?.channel === 'memory'
    const id = `${mem ? 'm' : 'a'}${++seq}`
    const ok = mem
      ? !!memory.value.csrf && transport({ ...args, ...extra, op: action, id, csrf: memory.value.csrf, kind })
      : !!hello.value.csrf && transport({ op: 'ctl', id, csrf: hello.value.csrf, kind, action, args, ...extra })
    if (!ok) {
      finish(false, failureText('E_LINK'), 'E_LINK')
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
    errorDetails.value = null
    resultText.value = ''
    riskyRun.value = null
    pendingId = cmdId = null
    const mem = next.channel === 'memory'
    const why = mem ? memoryUnavailable(memory.value, next.action) : unavailableReason(hello.value, next.action)
    if (why) {
      finish(false, why)
      return false
    }
    if (mem && !memoryNeedsChallenge(memory.value, next.action)) {
      // безрисковое действие «Памяти» (отклонить урок): без диалога и без лишнего вопроса серверу, итог уведомлением
      riskyRun.value = false
      dispatch()
      return phase.value === 'sending'
    }
    phase.value = 'checking'
    pendingId = send('challenge', next.action, next.args)
    return pendingId !== null
  }

  /** Условия получены: заблокировано, нужно подтверждение рисков или можно отправлять сразу. */
  function onChallenge(r: ActionResult): void {
    const blockedCode = r.code === 'E_GATE' || r.code === 'E_STATE'
    const raw = [r.code, r.msg].filter(Boolean).join(' | ') || null
    if (!r.ok && !blockedCode) return finish(false, failureText(r.code, r.msg), raw)
    if (!r.data) {
      if (blockedCode) { phase.value = 'blocked'; error.value = failureText(r.code, r.msg); errorDetails.value = raw; return }
      return finish(false, 'Платформа не прислала условия выполнения. Повторите; если повторяется, сообщите разработчику.', raw)
    }
    challenge.value = r.data
    receivedAt.value = Date.now()
    if (blockedCode || r.data.blocked) {
      phase.value = 'blocked'
      error.value = r.msg || r.code ? failureText(r.code, r.msg) : null
      errorDetails.value = raw
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
    if (s.channel === 'memory') {
      // принятые предупреждения: ровно все id из challenge (сервер сверяет множество)
      const accepted = (challenge.value?.warnings ?? []).map((w) => w.id)
      phase.value = 'sending'
      const id = send('command', s.action, s.args, accepted.length ? { accepted } : {})
      if (id === null) return
      pendingId = cmdId = id
      clearTimer()
      timer = setTimeout(() => {
        if (phase.value === 'sending' || phase.value === 'waiting') finish(false, s.timeoutMs ? timeoutText(s.timeoutMs) : TIMEOUT_TEXT)
      }, s.timeoutMs ?? FINAL_TIMEOUT_MS)
      return
    }
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
      if (phase.value === 'sending' || phase.value === 'waiting') finish(false, s.timeoutMs ? timeoutText(s.timeoutMs) : TIMEOUT_TEXT)
    }, s.timeoutMs ?? FINAL_TIMEOUT_MS)
  }

  function applyResult(m: LiveMessage): void {
    const r = m.data as ActionResult
    if (keys.accept(r)) return
    if (r.id !== pendingId) return
    pendingId = null
    if (r.kind === 'challenge') return onChallenge(r)
    if (!r.ok) return finish(false, failureText(r.code, r.msg), [r.code, r.msg].filter(Boolean).join(' | ') || null)
    if (phase.value === 'sending') phase.value = 'waiting'
  }

  /**
   * Итог-отказ словами. Отказ по жёстким условиям (E_GATE) называет, какие именно не прошли, если платформа их прислала (так бывает при повторной проверке
   * сразу после переключения исполнителя на живую биржу: тогда торговля не включена, исполнитель возвращён обратно).
   */
  function failedText(f: ActionFinal): string {
    const raw: unknown = f.code === 'E_GATE' ? f.data?.gates : null
    const bad = (Array.isArray(raw) ? (raw as ActionGateRow[]) : []).filter((g) => g && g.ok === false)
    if (!bad.length) return failureText(f.code, f.msg)
    const live = isLiveStart(spec.value?.action, spec.value?.args)
    return `${failureText(f.code, f.msg)} ${blockReasons({ gates: bad }, '', live).join(' ')}`
  }

  /** Ответ `memory_action_result`: на challenge (подтверждение или сразу команда) либо на команду (это и есть итог). */
  function applyMemoryResult(m: LiveMessage): void {
    const r = m.data as MemoryActionResult
    if (!r || r.id !== pendingId || spec.value?.channel !== 'memory') return
    pendingId = null
    const raw = [r.code, r.msg].filter(Boolean).join(' | ') || null
    if (phase.value === 'checking') {
      if (r.code === 'CONFIRM' && (r.warnings?.length ?? 0) > 0) {
        challenge.value = memoryChallenge(r)
        receivedAt.value = Date.now()
        riskyRun.value = true
        phase.value = 'confirm'
        return
      }
      if (r.ok) { riskyRun.value = false; return dispatch() }
      return finish(false, memoryFailure(r.code, r.msg), raw)
    }
    if (phase.value !== 'sending' && phase.value !== 'waiting') return
    if (r.ok && r.code === 'OK') return finish(true, memoryDoneText(r.op))
    finish(false, memoryFailure(r.code, r.msg), raw)
  }

  function applyFinal(m: LiveMessage): void {
    const f = m.data as ActionFinal
    if (spec.value?.channel === 'memory') return
    if (f.rid !== cmdId || (phase.value !== 'sending' && phase.value !== 'waiting')) return
    if (f.status === 'done') finish(true, plainify(f.msg) ?? 'Готово.', null, f)
    else finish(false, failedText(f), [f.status, f.code, f.msg].filter(Boolean).join(' | ') || null, f)
  }

  /** «Принимаю риски»: после паузы min_delay отправляет команду; если срок challenge вышел, сам запрашивает условия заново. */
  function accept(): void {
    if (phase.value !== 'confirm' || !spec.value) return
    const ch = challenge.value
    const now = Date.now()
    if (isExpired(ch?.expires_in, receivedAt.value, now)) return recheck()
    if (acceptWait(ch, receivedAt.value, now) > 0) return
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

  function mockChallenge(kind: ActionMock, args: Record<string, unknown>, action = ''): Challenge {
    const gates = [
      { id: 'G1', name: 'Исполнитель жив', ok: true }, { id: 'G2', name: 'Сверка свежая', ok: true, msg: '4 с назад' },
      { id: 'G3', name: 'Чистый KILL', ok: true }, { id: 'G4', name: 'Связь с биржей', ok: true },
    ]
    if (action === 'kill.clear') {
      // снятие KILL: проверки K1–K2 и H2 (в live ещё H3–H7); live требует фразу, блокировка: позиция без стопа
      const live = kind === 'kill-live'
      const kill = { active: true, reason: 'ручная остановка', flatten: false, mode: live ? 'live' : 'paper' }
      const ok = (id: string, name: string, msg: string) => ({ id, name, ok: true, msg })
      const head = [ok('K1', 'Исполнитель на связи', 'исполнитель жив'), ok('K2', 'День не идёт и не закрывается', 'платформа остановлена')]
      if (kind === 'kill-blocked') return { blocked: true, danger: false, warnings: [], pre_amber: [], kill, gates: [...head, { id: 'H2', name: 'Стоп на бирже у каждой позиции', ok: false, msg: 'позиции без стопа на бирже: ETHUSDT' }] }
      const tail = [ok('H2', 'Стоп на бирже у каждой позиции', 'позиций 2, стоп у каждой'), ...(live ? [ok('H3', 'Связь', 'в порядке'), ok('H4', 'Сверка', 'расхождений нет'), ok('H5', 'Лимит убытка', 'убыток 0.00 из 1.00 USDT'), ok('H6', 'Мозг', 'отвечает'), ok('H7', 'Настройки защиты', 'в порядке')] : [])]
      return { danger: true, blocked: false, warnings: [], pre_amber: [], kill, gates: [...head, ...tail], phrases: live ? ['СНИМАЮ KILL LIVE 2026-11-02'] : [], nonce: 'mock', min_delay: 2, expires_in: 60 }
    }
    if (kind === 'blocked') {
      return { blocked: true, gates: [{ id: 'G1', name: 'Исполнитель жив', ok: true }, { id: 'G2', name: 'Сверка свежая', ok: false, msg: 'последняя сверка 3 минуты назад' }, { id: 'G3', name: 'Нет открытых позиций', ok: false, msg: 'открыта позиция BTCUSDT, сначала закройте её' }], warnings: [], pre_amber: [], danger: false }
    }
    if (kind === 'full' && args.flatten !== true) return { gates, warnings: [], pre_amber: [], danger: false, phrases: [], blocked: false, nonce: 'mock', min_delay: 0, expires_in: 60 }
    if (action === 'llm.provider') {
      // пример: включение без рисков, остальное с предупреждением, чтобы видеть оба потока
      const warn: Record<string, string> = {
        disable: 'Роли, которые используют провайдера, перейдут на запасные модели; если запасных нет, их вызовы будут отклонены.',
        update: 'Потолки расхода начнут действовать сразу: при исчерпании вызовы к провайдеру остановятся до конца окна (час или сутки).',
        key_clear: 'Без ключа провайдер перестанет отвечать, пока вы не добавите новый.',
      }
      const blk = (args.spec ?? args.patch ?? {}) as Record<string, unknown>
      if (blk.allow_private === true) {
        return { danger: true, blocked: false, gates, phrases: ['mock'], nonce: 'mock', min_delay: 2, expires_in: 90, pre_amber: [], warnings: [{ id: 'llm.allow_private', code: 'ALLOW_PRIVATE', msg: 'Разрешаются приватные и внутренние адреса: ключ и запросы могут уйти во внутреннюю сеть. Включайте только для своего сервера.' }] }
      }
      if (args.op === 'update' && typeof blk.base_url === 'string') {
        return { danger: false, blocked: false, gates, phrases: ['mock'], nonce: 'mock', min_delay: 2, expires_in: 90, pre_amber: [], warnings: [{ id: 'llm.key_new_url', code: 'KEY_NEW_URL', msg: 'Ключ провайдера уйдёт на новый адрес. Убедитесь, что это нужный сервис.', numbers: { host: 'new.example', changed: ['base_url'] } }] }
      }
      const w = args.op === 'update' ? (blk.limits ? warn.update : undefined) : warn[String(args.op)]
      if (!w) return { gates, warnings: [], pre_amber: [], danger: false, phrases: [], blocked: false, nonce: 'mock', min_delay: 0, expires_in: 60 }
      return { danger: false, blocked: false, gates, phrases: ['mock'], nonce: 'mock', min_delay: 2, expires_in: 90, pre_amber: [], warnings: [{ id: 'W_PROVIDER', code: 'W_PROVIDER', msg: w }] }
    }
    const calm: Challenge = { gates, warnings: [], pre_amber: [], danger: false, phrases: [], blocked: false, nonce: 'mock', min_delay: 0, expires_in: 60 }
    if (action === 'risk.set') {
      // повышение: предупреждения и пауза; понижение: без рисков, безрисковый поток
      if (Number(args.level) <= 3) return calm
      return {
        ...calm, danger: true, phrases: [], min_delay: 3, expires_in: 90,
        warnings: [
          { id: 'W_RISK_UP', code: 'W_RISK_UP', msg: `Уровень риска ${String(args.level)}: допустимый риск на сделку и дневной убыток вырастут.` },
          { id: 'W_POSITIONS_UP', code: 'W_POSITIONS_UP', msg: 'Одновременно можно будет держать больше позиций.' },
        ],
      }
    }
    if (action === 'exchange.set') {
      if (args.op === 'mode' && args.mode === 'live') {
        return { ...calm, danger: true, phrases: ['mock'], min_delay: 3, expires_in: 90, warnings: [{ id: 'W_LIVE', code: 'W_LIVE', msg: 'Биржа начнёт торговать на реальные деньги: потери возможны.' }] }
      }
      return calm
    }
    if (action === 'mode.set') return calm
    if (action === 'day.stop') {
      return { ...calm, min_delay: 0, warnings: [{ id: 'W_STOP_OPEN', code: 'W_STOP_OPEN', msg: 'Открытые позиции останутся под стопами на бирже, пока день завершается.' }] }
    }
    if (action === 'day.start' && args.mode === 'live') {
      // запуск на реальные деньги: danger, фраза с датой, предупреждения принимаются общим окном
      return {
        danger: true, blocked: false, phrases: ['ТОРГУЮ LIVE 2026-11-02 УРОВЕНЬ 3'], nonce: 'mock', min_delay: 2, expires_in: 90, pre_amber: [],
        gates: ['H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'H7', 'H8'].map((id) => ({ id, name: id, ok: true })),
        warnings: [{ id: 'W1', code: 'W_PAPER', msg: 'сделок 18 из рекомендованных 30; дней 5 из 7' }, { id: 'W9', code: 'W_SESSION', msg: 'сделок в журнале 4 из 10' }],
      }
    }
    if (action === 'day.start') {
      return {
        ...calm, phrases: [], min_delay: 2, expires_in: 90,
        warnings: [{ id: 'W1', code: 'W_PAPER_HISTORY', msg: 'Мало paper-истории: рекомендуется 30 сделок и 7 дней' }],
        pre_amber: [{ id: 'P1', name: 'Мозг на запасной модели', msg: 'основная модель сейчас не отвечает' }],
      }
    }
    if (action === 'brain.profile' || (action === 'brain.role' && args.op !== 'chain')) return calm       // профиль и параметры роли: безрисковый поток
    if (action === 'brain.role' || action === 'brain.use') {
      return {
        danger: false, blocked: false, gates, phrases: ['mock'], nonce: 'mock', min_delay: 2, expires_in: 90, pre_amber: [],
        warnings: [
          { id: 'W_SWAP_NOT_LIVE_OK', code: 'W_SWAP_NOT_LIVE_OK', msg: 'Профиль не допущен к live: его ответы не проверялись живой оценкой.' },
          { id: 'W_SWAP_PAPER_HISTORY', code: 'W_SWAP_PAPER_HISTORY', msg: 'Мало paper-сделок с этой моделью: рекомендуется 30 сделок за 7 дней.' },
        ],
      }
    }
    return {
      danger: true, blocked: false, gates, phrases: ['mock'], nonce: 'mock', min_delay: 3, expires_in: 90,
      warnings: [{ id: 'W1', code: 'W_RISK_UP', msg: 'Уровень риска 5: размер позиции до 25% счёта, плечо до 10x.' }, { id: 'W2', code: 'W_OPEN', msg: 'Открытая позиция BTCUSDT будет закрыта по рынку, возможно проскальзывание.' }],
      pre_amber: [{ id: 'P1', name: 'Мало paper-истории', msg: 'рекомендуется 30 сделок и 7 дней' }],
    }
  }

  function mockTransport(kind: ActionMock): Transport {
    return (p) => {
      const live = (data: unknown) => ({ topic: 'x', seq: 0, ts: 0, data })
      if (p.op === 'llmkey') {
        // запись ключа: значение не читаем и не сохраняем; пример отказа: ключ, начинающийся с «reject»
        const bad = typeof p.value === 'string' && p.value.startsWith('reject')
        setTimeout(() => applyResult(live({ id: p.id, kind: 'llmkey', action: 'llm.key.set', ok: !bad, code: bad ? 'E_KEY' : 'OK', msg: '', provider: p.provider })), 400)
        return true
      }
      const base = { id: p.id as string, action: p.action as string }
      setTimeout(() => {
        if (p.kind === 'challenge') {
          applyResult(live({ ...base, kind: 'challenge', ok: true, code: kind === 'blocked' || kind === 'kill-blocked' ? 'E_GATE' : 'OK', msg: kind === 'blocked' || kind === 'kill-blocked' ? 'условия не выполнены' : '', data: mockChallenge(kind, p.args as Record<string, unknown>, p.action as string) }))
        } else {
          applyResult(live({ ...base, kind: 'command', ok: true, code: 'OK', msg: 'принято' }))
          const a = p.args as Record<string, unknown>
          const net = p.action === 'llm.provider' && (a.op === 'models' || a.op === 'ping') ? mockNetFinal(String(a.op), String(a.id), a.model) : null
          setTimeout(() => applyFinal(live({ id: 'q1', rid: base.id, status: net?.status ?? 'done', code: net?.code ?? 'OK', msg: net?.msg ?? 'Выполнено (пример: на сервер ничего не отправлялось).', data: net?.data })), net ? 500 : 700)
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
      error.value = failureText('E_RATE')
    } else if (kind === 'toast') {
      pushToast('ok', 'Аварийная остановка', 'Торговля остановлена, позиции не тронуты.')
      pushToast('bad', 'Остановка дня', failureText('E_STATE'))
    }
  }

  /** Отладка правки моделей и провайдеров (?edit=mock): расширенное управление включено для brain.role, brain.use, llm.provider и записи ключа, ответы изображает mockTransport. calm: без рисков (безрисковый поток), иначе рискованный. */
  function mockEdit(calm = false): void {
    mocked = true
    hello.value = { enabled: true, csrf: 'mock', full: true, modes: ['brain.role', 'brain.use', 'brain.profile', 'llm.provider', 'llmkey', 'risk.set', 'mode.set', 'exchange.set', 'day.start', 'day.stop'] }
    transport = mockTransport(calm ? 'full' : 'confirm')
  }

  /** Отладка снятия аварийной остановки (?kill=mock|mock-live|mock-blocked|mock-off): доступно kill.clear, ответы изображает mockTransport; на сервер ничего не уходит. */
  function mockKill(kind: Extract<ActionMock, 'kill' | 'kill-live' | 'kill-blocked'>): void {
    mocked = true
    hello.value = { enabled: true, csrf: 'mock', full: true, modes: ['kill.clear', 'kill.set', 'day.start', 'day.stop'] }
    transport = mockTransport(kind)
  }

  /** Отладка страницы уведомлений (?notify=mock...): доступны notify.set и notify.test, безрисковый поток; на сервер ничего не уходит. */
  function mockNotify(): void {
    mocked = true
    hello.value = { enabled: true, csrf: 'mock', full: true, modes: ['notify.set', 'notify.test'] }
    transport = mockTransport('full')
  }

  /** Отладка страницы «День» (?daymock=...): доступны day.resume, carry.decide, report.ack, безрисковый поток; на сервер ничего не уходит. */
  function mockDay(): void {
    mocked = true
    hello.value = { enabled: true, csrf: 'mock', full: true, modes: ['day.resume', 'carry.decide', 'report.ack'] }
    transport = mockTransport('full')
  }

  /** Отладка страницы «Новости» (?newsmock=...): доступно intel.refresh, безрисковый поток; на сервер ничего не уходит. */
  function mockNews(): void {
    mocked = true
    hello.value = { enabled: true, csrf: 'mock', full: true, modes: ['intel.refresh'] }
    transport = mockTransport('full')
  }

  return { hello, memory, canMemory, applyMemoryResult, phase, spec, challenge, receivedAt, error, errorDetails, resultText, riskyRun, toasts, active, risks, start, can, run, sendKey, accept, recheck, cancel, reset, dismissToast, mock, mockEdit, mockKill, mockNotify, mockDay, mockNews, applyHello, applyResult, applyFinal }
})
