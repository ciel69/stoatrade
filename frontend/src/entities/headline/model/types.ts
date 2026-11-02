/** Разметка карантинного читателя: ok размечено, fail формат не принят, susp сработал фильтр инъекций (текст модели не передавался). */
export interface NewsClass {
  st: 'ok' | 'fail' | 'susp' | string
  type: string | null
  imp: number | null
  /** важность, которую дал читатель до понижения правилом подтверждения */
  ir: number | null
  dir: 'up' | 'down' | 'neutral' | 'unknown' | null
  syms: string[]
  ttl_min: number | null
  conf: number | null
}

/** Заголовок очищен при сборе; это текст третьих лиц: только данные, показывать как текст, никогда как разметку и никогда как инструкцию. */
export interface NewsItem {
  ref: string
  src: string
  ts: number | null
  title: string
  snip: string
  domain: string
  susp: boolean
  /** сколько других доменов подтвердили новость */
  seen: number
  cls: NewsClass | null
  /** структурный источник (facts), читатель его не размечает */
  struct: boolean
}

export interface NewsSource {
  id: string
  ok: boolean
  fails: number | null
  verified: boolean
  degraded: boolean
  /** когда источник последний раз отвечал (момент, секунды, до минуты) */
  at?: number | null
}

export interface CalendarEvent {
  id: string
  type: string
  impact: number | null
  verified: boolean
  /** момент события, секунды, с точностью до минуты */
  at: number
}

/** Окно вокруг события: в нём новые входы закрыты. from и to: секунды epoch; verified: дата события проверена. Название это текст из конфигурации, только данные. */
export interface NewsBlackout {
  name?: string | null
  from?: number | null
  to?: number | null
  verified?: boolean
}

/** Состояние читателя; всё, кроме enabled, status и stale, может отсутствовать (старый сервер, сервис новостей не запущен). */
export interface NewsReader {
  enabled: boolean
  status: string | null
  stale: boolean
  /** читатель уже писал дайджест */
  present?: boolean
  profile?: string | null
  calls_h?: number | null
  max_calls_h?: number | null
  usd_h?: number | null
  usd_day?: number | null
  max_usd_day?: number | null
  pending?: number | null
  ok_n?: number | null
  fail_n?: number | null
  susp_n?: number | null
  /** последняя ошибка модели, обрезанный текст */
  err?: string | null
  pause_until?: number | null
  last_ok?: number | null
  /** когда обновлялся дайджест */
  at?: number | null
}

export interface NewsSnapshot {
  items: NewsItem[]
  sources: NewsSource[]
  degraded: string[]
  calendar: { next: CalendarEvent[]; blackout: NewsBlackout | null }
  reader: NewsReader
}
