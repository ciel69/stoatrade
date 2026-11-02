/** Контракты общего движка действий (сервер: ctl в src/tb/dash2/ctlbridge.py; сокет: `ctl_result`, `ctl_final`). */

/** Что нужно выполнить: заголовок и строка «что изменится» показывает диалог, onDone вызывается после успешного итога. */
export interface ActionSpec {
  action: string
  args: Record<string, unknown>
  title: string
  what?: string
  /** Сколько ждать итог принятой команды (мс), если дольше обычных 60 с (запуск дня идёт до минуты и больше). */
  timeoutMs?: number
  onDone?: () => void
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
