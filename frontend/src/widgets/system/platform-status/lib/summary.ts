import { modeOn } from '@/shared/lib'
import { lowerFirst } from './rows'
import type { GroupId, StatusGroup, StatusLevel, StatusRow, StatusSummary } from './types'

const RANK: Record<StatusLevel, number> = { bad: 3, warn: 2, ok: 1, idle: 0 }

export const GROUP_ORDER: GroupId[] = ['link', 'platform', 'exec', 'brain', 'market', 'news', 'live']
export const GROUP_LABEL: Record<GroupId, string> = {
  link: 'Связь',
  platform: 'Платформа',
  exec: 'Исполнитель и биржа',
  brain: 'Мозг и модели',
  market: 'Рыночные данные',
  news: 'Новости',
  live: 'Что нужно для живой торговли',
}

/** Строки, которые показываются наверху окна, если проблем мало: самое важное в норме. */
const FILL = ['link', 'platform', 'node:exec', 'brain', 'node:feed']
const KEY_MAX = 6
const KEY_MIN = 3

/** Худший из уровней: bad, затем warn, затем ok; только справка (idle) если других нет. Пустой список: idle. */
export function worstLevel(levels: StatusLevel[]): StatusLevel {
  return levels.reduce<StatusLevel>((a, l) => (RANK[l] > RANK[a] ? l : a), 'idle')
}

/** Стабильная сортировка: проблемные выше, при равенстве порядок источника. */
function bySeverity(rows: StatusRow[]): StatusRow[] {
  return rows.map((r, i) => ({ r, i })).sort((a, b) => RANK[b.r.level] - RANK[a.r.level] || a.i - b.i).map((x) => x.r)
}

export interface SummaryMeta {
  mode?: string | null
}

/**
 * Свёртка строк в то, что видно в шапке и в окне. Индикатор отвечает на вопрос «могу ли я сейчас спокойно оставить платформу торговать?»:
 * зелёный «Всё в порядке», жёлтый «Торговля идёт, есть что проверить», красный только когда торговля остановлена или нужно ваше действие. Справка (живая торговля пока недоступна и т.п.) цвет не меняет.
 */
export function summarize(rows: StatusRow[], meta: SummaryMeta = {}): StatusSummary {
  const groups: StatusGroup[] = GROUP_ORDER
    .map((id, order) => ({ id, order, rows: bySeverity(rows.filter((r) => r.group === id)) }))
    .filter((g) => g.rows.length)
    .map((g) => ({ ...g, level: worstLevel(g.rows.map((r) => r.level)) }))
    .sort((a, b) => RANK[b.level] - RANK[a.level] || a.order - b.order)
    .map((g) => ({ id: g.id, label: GROUP_LABEL[g.id], level: g.level, rows: g.rows }))

  const inOrder = [...rows].sort((a, b) => GROUP_ORDER.indexOf(a.group) - GROUP_ORDER.indexOf(b.group))      // при равной серьёзности названа проблема из более «высокой» группы
  const problemRows = bySeverity(inOrder).filter((r) => r.level === 'bad' || r.level === 'warn')
  const readiness = rows.find((r) => r.id === 'live-readiness')
  const notes = rows.filter((r) => r.level === 'idle' && r.note).length
  const details = rows.filter((r) => r.raw && r.level !== 'ok').map((r) => r.raw as string)

  const top = problemRows[0]
  const many = !!top && top.level === 'warn' && problemRows.length > 1
  /** Главная проблема уже названа в заголовке окна с пояснением, поэтому в списке ниже её не повторяем (кроме случая «несколько замечаний»). */
  const carried = top && !many ? [top] : []
  const key = problemRows.filter((r) => !carried.includes(r)).slice(0, KEY_MAX - (readiness ? 1 : 0))
  if (readiness) key.push(readiness)
  for (const id of FILL) {
    if (key.length >= KEY_MIN) break
    const r = rows.find((x) => x.id === id && x.level === 'ok')
    if (r && !key.includes(r)) key.push(r)
  }

  const base = { groups, key, problems: problemRows.length, notes, details }
  if (top) {
    const brief = top.brief ?? top.label
    if (top.level === 'bad') {
      const lost = top.id === 'link'
      const text = lost ? brief : top.stops ? `Торговля остановлена: ${lowerFirst(brief)}` : `Нужно внимание: ${lowerFirst(brief)}`
      return { ...base, level: 'bad', text, extra: problemRows.length - 1, headline: { tone: 'bad', title: text, detail: top.note ?? '' } }
    }
    if (!many) {
      const text = `Торговля идёт: ${lowerFirst(brief)}`
      return { ...base, level: 'warn', text, extra: 0, headline: { tone: 'warn', title: text, detail: top.note ?? '' } }
    }
    return {
      ...base, level: 'warn', text: 'Торговля идёт, есть что проверить', extra: problemRows.length - 1,
      headline: { tone: 'warn', title: 'Торговля идёт, но есть что проверить', detail: `Замечаний: ${problemRows.length}, ничего срочного. Они перечислены ниже.` },
    }
  }

  const stopped = rows.find((r) => r.id === 'platform' && r.level === 'idle')
  if (stopped) return { ...base, level: 'idle', text: 'Платформа остановлена', extra: 0, headline: { tone: 'idle', title: 'Платформа остановлена', detail: 'Торговля не идёт. Запустить день можно в разделе «Управление».' } }

  const hasData = rows.some((r) => r.group !== 'link' && r.level === 'ok')
  if (!hasData) return { ...base, level: 'idle', text: 'Ждём данные платформы', extra: 0, headline: { tone: 'idle', title: 'Ждём данные платформы', detail: 'Они появятся через несколько секунд.' } }
  const how = meta.mode ? `Платформа торгует ${modeOn(meta.mode)}. ` : ''
  return { ...base, level: 'ok', text: 'Всё в порядке', extra: 0, headline: { tone: 'ok', title: 'Всё в порядке', detail: `${how}Оставить её без присмотра можно.` } }
}
