/** Контракты управления днём (сервер: src/tb/dash2/ctlbridge.py и тема `ctl` в topics.py). */
export type DayMode = 'paper' | 'testnet' | 'observe'
export type PlatformState = 'stopped' | 'starting' | 'running' | 'closing' | 'halted' | 'unknown'

export interface CtlCheck {
  id: string
  name: string
  /** red: жёсткое условие, amber: предупреждение */
  sev: 'red' | 'amber' | string
  ok: boolean
  msg: string
}

export interface DayStepRow {
  name: string
  phase: string | null
  ok: boolean | null
  code: string | null
}

/** Тема `ctl`: без времён и возрастов, меняется только при смене состояния. */
export interface CtlSnapshot {
  alive: boolean
  configured: boolean
  platform: { state: PlatformState | null; mode: string | null; session: string | null; health: string | null; day_id: string | null; trading: boolean | null; observe: boolean | null }
  step: { name: string; i?: number; n?: number } | null
  kill: { flatten: boolean; reason: string } | null
  risk: { level: number | null; name: string | null }
  busy: boolean
  checks: CtlCheck[]
  banner: { level: string | null; text: string } | null
  day: { day_id: string | null; state: string | null; mode: string | null; steps: DayStepRow[] } | null
  /** последние команды управления (без времён): что было отдано ctl и чем кончилось */
  last?: Array<{ action: string; code: string; status: string; msg: string }>
}

/** hello.ctl: управление включено только при open_local и ключе подписи; csrf привязан к соединению. */
export interface CtlHello {
  enabled: boolean
  csrf?: string
  reason?: 'no_key' | 'token_mode'
  /** расширенное управление включено (auth.dash2_full); modes: какие действия доступны дашборду, плюс 'llmkey' */
  full?: boolean
  modes?: string[]
}

export interface ChallengeData {
  action?: string
  danger?: boolean
  gates?: Array<{ id: string; name?: string; ok?: boolean; msg?: string }>
  warnings?: Array<{ id: string; code?: string; msg?: string }>
  pre_amber?: Array<{ id?: string; name?: string; msg?: string; code?: string }>
  blocked?: boolean
  expires_in?: number
}

/** Ответ на команду по сокету (`ctl_result`). */
export interface CtlResult {
  id: string
  kind: 'challenge' | 'command'
  action: string
  ok: boolean
  code: string
  msg: string
  data?: ChallengeData
}

/** Итог исполнения ctl (`ctl_final`): rid это id нашего запроса, id: команды в очереди. */
export interface CtlFinal {
  id: string
  rid: string
  status: string
  code: string
  msg: string
  data: Record<string, unknown> | null
}

export type FlowPhase = 'idle' | 'checking' | 'ready' | 'blocked' | 'starting' | 'done' | 'failed' | 'stopping'
