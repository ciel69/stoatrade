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
}

export interface CalendarEvent {
  id: string
  type: string
  impact: number | null
  verified: boolean
  /** момент события, секунды, с точностью до минуты */
  at: number
}

export interface NewsSnapshot {
  items: NewsItem[]
  sources: NewsSource[]
  degraded: string[]
  calendar: { next: CalendarEvent[]; blackout: { name?: string } | null }
  reader: { enabled: boolean; status: string | null; stale: boolean }
}
