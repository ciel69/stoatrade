/** Контракты общего движка действий (сервер: ctl в src/tb/dash2/ctlbridge.py; сокет: `ctl_result`, `ctl_final`). */

/** Что нужно выполнить: заголовок и строка «что изменится» показывает диалог, onDone вызывается после успешного итога. */
export interface ActionSpec {
  action: string
  args: Record<string, unknown>
  title: string
  what?: string
  /** Сколько ждать итог принятой команды (мс), если дольше обычных 60 с (запуск дня идёт до минуты и больше). */
  timeoutMs?: number
  /** После успешного итога; final есть, если итог пришёл от платформы (данные отчёта: списки моделей, проверка подключения). */
  onDone?: (final?: ActionFinal) => void
  /** После неудачного итога: text уже словами; final есть, если отказ пришёл от платформы (код и данные: сколько ждать, что не так). */
  onFail?: (text: string, final?: ActionFinal) => void
  /** Итог показывает сам вызывающий (строка статуса у формы): общее уведомление о нём не нужно. Диалог рисков остаётся как был. */
  quiet?: boolean
  /**
   * По какому протоколу идёт действие. 'ctl' (по умолчанию): конвейер управления (`ctl_result`, `ctl_final`, CSRF из hello.ctl).
   * 'memory': аварийные действия вкладки «Память» (op = action, ответ `memory_action_result`, CSRF из hello.memory; src/tb/dash2/memory_ops.py).
   * Диалог «Принимаю риски» у обоих один и тот же.
   */
  channel?: 'ctl' | 'memory'
}

/** hello.memory: CSRF соединения, разрешённые аварийные действия «Памяти» и id предупреждений, которые нужно принять (пустой список: без диалога). */
export interface MemoryHello {
  csrf?: string
  actions?: string[]
  confirm?: Record<string, string[]>
}

/** Ответ `memory_action_result` на challenge или команду аварийного действия «Памяти». */
export interface MemoryActionResult {
  id: string
  op: string
  ok: boolean
  code: string
  msg?: string
  warnings?: Array<{ id: string; text?: string }>
  needs_confirm?: boolean
  report?: Record<string, unknown> | null
}

export type ActionPhase = 'idle' | 'checking' | 'confirm' | 'blocked' | 'sending' | 'waiting' | 'done' | 'failed'

/** hello.ctl: что разрешено этому дашборду (full и modes приходят, когда включено расширенное управление). */
export interface ActionHello {
  enabled: boolean
  csrf?: string
  full?: boolean
  modes?: string[]
}

export interface ChallengeGate {
  id: string
  name?: string
  ok?: boolean
  msg?: string
}

export interface ChallengeWarning {
  id: string
  code?: string
  msg?: string
  numbers?: Record<string, unknown>
}

export interface ChallengePreAmber {
  id?: string
  name?: string
  msg?: string
}

/** Что платформа знает о снимаемой аварийной остановке (только у kill.clear): причина, закрывались ли позиции, режим исполнителя (live, paper, testnet, observe). */
export interface KillInfo {
  active?: boolean
  reason?: string
  flatten?: boolean
  mode?: string
}

/** Данные challenge: условия, предупреждения, фразы и nonce, которые интерфейс сам возвращает в команду. */
export interface Challenge {
  kill?: KillInfo | null
  action?: string
  danger?: boolean
  gates?: ChallengeGate[]
  warnings?: ChallengeWarning[]
  pre_amber?: ChallengePreAmber[]
  blocked?: boolean
  expires_in?: number
  min_delay?: number
  min_type?: number
  phrases?: string[]
  nonce?: string
}

export interface ActionResult {
  id: string
  kind: 'challenge' | 'command' | 'llmkey'
  action: string
  ok: boolean
  code: string
  msg: string
  data?: Challenge
}

export interface ActionFinal {
  id: string
  rid: string
  status: 'done' | 'rejected' | 'failed' | 'expired' | string
  code: string
  msg: string
  data?: Record<string, unknown> | null
}

export interface ActionCommand {
  args: Record<string, unknown>
  nonce?: string
  phrases?: string[]
}

/** Пункт списка последствий в диалоге. */
export interface RiskItem {
  id: string
  level: 'danger' | 'warn' | 'amber'
  text: string
}

export interface ActionToast {
  id: number
  tone: 'ok' | 'bad'
  title: string
  text: string
}
