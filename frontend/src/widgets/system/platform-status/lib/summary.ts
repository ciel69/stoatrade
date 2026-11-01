import type { GroupId, StatusGroup, StatusLevel, StatusRow, StatusSummary } from './types'

const RANK: Record<StatusLevel, number> = { bad: 3, warn: 2, ok: 1, idle: 0 }

export const GROUP_ORDER: GroupId[] = ['link', 'platform', 'exec', 'brain', 'market', 'news', 'gates']
export const GROUP_LABEL: Record<GroupId, string> = {
  link: 'Связь',
  platform: 'Платформа',
  exec: 'Исполнитель и биржа',
  brain: 'Мозг и модели',
  market: 'Данные рынка',
  news: 'Новости',
  gates: 'Проверки запуска (H1–H8)',
}

/** Худший из уровней: bad, затем warn, затем ok; только «нет данных» (idle) если других нет. Пустой список: idle. */
export function worstLevel(levels: StatusLevel[]): StatusLevel {
  return levels.reduce<StatusLevel>((a, l) => (RANK[l] > RANK[a] ? l : a), 'idle')
}

/** Стабильная сортировка: проблемные выше, при равенстве порядок источника. */
function bySeverity(rows: StatusRow[]): StatusRow[] {
  return rows.map((r, i) => ({ r, i })).sort((a, b) => RANK[b.r.level] - RANK[a.r.level] || a.i - b.i).map((x) => x.r)
}

/**
 * Свёртка строк в то, что видно в шапке и в окне. Цвет и текст индикатора: самая серьёзная проблема (bad выше warn; при равенстве первая по порядку источника).
 * Проблем нет: платформа остановлена (idle, текст об этом), иначе «всё в норме»; ни одной строки с данными кроме связи: idle «ждём данные платформы».
 */
export function summarize(rows: StatusRow[]): StatusSummary {
  const groups: StatusGroup[] = GROUP_ORDER
    .map((id, order) => ({ id, order, rows: bySeverity(rows.filter((r) => r.group === id)) }))
    .filter((g) => g.rows.length)
    .map((g) => ({ ...g, level: worstLevel(g.rows.map((r) => r.level)) }))
    .sort((a, b) => RANK[b.level] - RANK[a.level] || a.order - b.order)
    .map((g) => ({ id: g.id, label: GROUP_LABEL[g.id], level: g.level, rows: g.rows }))

  const inOrder = [...rows].sort((a, b) => GROUP_ORDER.indexOf(a.group) - GROUP_ORDER.indexOf(b.group))      // при равной серьёзности названа проблема из более «высокой» группы
  const problems = bySeverity(inOrder).filter((r) => r.level === 'bad' || r.level === 'warn')
  const top = problems[0]
  if (top) return { level: top.level, text: top.brief ?? `${top.label}: ${top.value}`, extra: problems.length - 1, groups, problems: problems.length }

  const stopped = rows.find((r) => r.level === 'idle' && r.id === 'platform' && r.brief)
  if (stopped) return { level: 'idle', text: stopped.brief ?? 'платформа остановлена', extra: 0, groups, problems: 0 }

  const hasData = rows.some((r) => r.group !== 'link' && r.level === 'ok')
  return hasData ? { level: 'ok', text: 'всё в норме', extra: 0, groups, problems: 0 } : { level: 'idle', text: 'ждём данные платформы', extra: 0, groups, problems: 0 }
}
