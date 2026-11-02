/** Тема `notify` (сервер: src/tb/dash2/topics.py:notify; данные те же, что у старого `/api/notify/status`). Секретов в данных нет. */

export interface NotifyQuiet {
  enabled: boolean
  /** время HH:MM */
  from: string
  to: string
  tz: 'local' | 'utc'
  /** категории, которые показываются и в тихие часы */
  bypass: string[]
}

export interface NotifyLimits {
  per_hour: number | null
  same_kind_min: number | null
}

export interface NotifySettings {
  master: boolean
  /** id категории -> включена ли */
  categories: Record<string, boolean>
  quiet: NotifyQuiet
  limits: NotifyLimits
}

export interface NotifyCategory {
  id: string
  /** подпись сервера: техническая, на экран идёт слово из categoryName */
  label: string
  on: boolean
  /** важная: по умолчанию обходит тихие часы */
  critical: boolean
  bypass: boolean
}

export interface NotifyStats {
  sent: number
  merged: number
  limited: number
  failed: number
}

export interface NotifySnapshot {
  /** file: свои настройки владельца; config: своих ещё нет, действует config/lifecycle.json */
  source: 'file' | 'config'
  engine: boolean
  warn: string | null
  settings: NotifySettings
  categories: NotifyCategory[]
  quiet_now: boolean
  held_n: number
  sent_last: { cat: string } | null
  stats: NotifyStats
  hint: string | null
}

/** Черновик правки: то же, что настройки, но числа лимитов могут быть пустыми (пока человек печатает). */
export interface NotifyDraft {
  master: boolean
  categories: Record<string, boolean>
  quiet: NotifyQuiet
  limits: NotifyLimits
}

/** Аргументы notify.set: только изменённое. */
export interface NotifyPatch {
  master?: boolean
  categories?: Record<string, boolean>
  quiet?: Partial<NotifyQuiet>
  limits?: Partial<{ per_hour: number; same_kind_min: number }>
}

export interface NotifyLine {
  label: string
  from: string
  to: string
}
