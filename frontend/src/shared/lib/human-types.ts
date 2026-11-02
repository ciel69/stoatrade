/**
 * Человеческий слой: общие типы. Один словарь переводит всё, что платформа говорит служебным языком (проверки, предупреждения, шаги запуска, коды ответов, узлы, баннеры),
 * в три ответа для человека, который видит платформу впервые: что происходит, почему так и что делать.
 */

/** ok: всё хорошо; info: справка, ничего делать не нужно; warn: стоит обратить внимание, торговля идёт; bad: торговля не идёт или опасна, нужно действие. */
export type Tone = 'ok' | 'info' | 'warn' | 'bad'

export interface Human {
  /** одна простая фраза: что происходит */
  title: string
  /** одно-два предложения: что это значит для торговли */
  what: string
  /** почему так */
  why: string
  /** что делать (или «действий не требуется») */
  todo: string
  tone: Tone
  /** исходный текст сервера и код: только для «Подробностей для специалиста» */
  details: string | null
}

/** Что известно о платформе в момент объяснения: от режима зависит серьёзность проверок, которые относятся только к живой торговле. */
export interface HumanCtx {
  mode?: string | null
  /** платформа запущена (день идёт) */
  running?: boolean
}

export interface HumanNodeInput {
  id: string
  label?: string
  level: 'ok' | 'warn' | 'bad' | 'off' | 'info'
  text?: string | null
}

export type HumanItem =
  | { kind: 'gate'; id: string; name?: string; ok: boolean; msg?: string; sev?: string }
  | { kind: 'warning'; id: string; code?: string; msg?: string }
  | { kind: 'step'; name: string; ok?: boolean | null; code?: string | null; amber?: boolean }
  | { kind: 'node'; node: HumanNodeInput }
  | { kind: 'code'; code: string; msg?: string }
  | { kind: 'banner'; level?: string | null; text: string }
  | { kind: 'text'; level?: string | null; text: string }

/** Короткий вывод «могу ли я оставить платформу торговать»: заголовок, пояснение и что делать. */
export interface Headline {
  tone: Tone | 'idle'
  title: string
  detail: string
}
