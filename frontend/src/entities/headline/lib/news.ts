import type { NewsClass, NewsItem } from '../model/types'

export const NEWS_TYPE: Record<string, string> = {
  listing: 'листинг', delisting: 'делистинг', maintenance: 'обслуживание', hack: 'взлом', regulation: 'регулятор', macro: 'макро', etf: 'ETF', unlock: 'разблокировка',
  partnership: 'партнёрство', price_move: 'движение цены', rumor: 'слух', upgrade: 'обновление', outage: 'сбой', other: 'другое', suspicious: 'подозрительно',
}
export const NEWS_DIR: Record<string, string> = { up: 'вверх', down: 'вниз', neutral: 'нейтрально', unknown: 'неясно' }

/** Важность заголовка 0..5: у размеченного ok из разметки, у подозрительного 1, иначе 0 (не размечено). */
export function importance(i: NewsItem): number {
  if (i.susp || i.cls?.st === 'susp') return 1
  return i.cls?.st === 'ok' ? (i.cls.imp ?? 0) : 0
}

export type Weight = 'high' | 'mid' | 'low'
export const weightOf = (imp: number): Weight => (imp >= 4 ? 'high' : imp >= 3 ? 'mid' : 'low')

/** Что показывать вместо разметки, если читатель её не дал. */
export function markState(i: NewsItem): 'ok' | 'struct' | 'waiting' | 'susp' | 'fail' | 'raw' {
  if (i.struct) return 'struct'
  if (!i.cls) return i.susp ? 'raw' : 'waiting'
  if (i.cls.st === 'susp') return 'susp'
  if (i.cls.st === 'fail') return 'fail'
  return 'ok'
}

export type NewsFilter = 'all' | 'important' | 'coin'

/** Фильтр ленты: все, важные (3 и выше), по выбранной монете (в разметке есть эта монета). */
export function filterNews(items: NewsItem[], f: NewsFilter, coin: string | null): NewsItem[] {
  if (f === 'important') return items.filter((i) => importance(i) >= 3)
  if (f === 'coin' && coin) return items.filter((i) => (i.cls?.syms ?? []).some((s) => s.toUpperCase().replace(/USDT$/, '') === coin))
  return items
}

/** Заметный ли сдвиг направления для окраски: вниз красный, вверх зелёный. */
export const dirTone = (c: NewsClass | null): 'up' | 'down' | '' => (c?.st === 'ok' ? (c.dir === 'up' ? 'up' : c.dir === 'down' ? 'down' : '') : '')
