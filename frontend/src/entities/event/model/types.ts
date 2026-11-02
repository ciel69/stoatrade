/** Контракт темы `events` (src/tb/dash2/topics.py): последние события шины, новые сверху. Поля уже из белых списков сервера. */
export interface BusEvent {
  seq: number
  ts: string
  type: string
  pri: 'wake' | 'info' | string
  plan: string | null
  sym: string | null
  d: Record<string, unknown>
}

export interface EventsSnapshot {
  last_seq: number
  items: BusEvent[]
}
