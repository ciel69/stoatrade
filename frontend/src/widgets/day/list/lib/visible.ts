/** Сколько последних дней показывается в списке, пока он не раскрыт. */
export const DAYS_SHOWN = 8

/**
 * Какие строки показывать: последние DAYS_SHOWN (список идёт от новых к старым) и, если выбранный в адресе день старше, он тоже остаётся на виду сразу после них.
 * Раскрытый список показывается целиком.
 */
export function visibleDays<T extends { id: string }>(rows: T[], selected: string | null, expanded: boolean, limit = DAYS_SHOWN): T[] {
  if (expanded || rows.length <= limit) return rows
  const head = rows.slice(0, limit)
  const pick = selected ? rows.slice(limit).find((r) => r.id === selected) : undefined
  return pick ? [...head, pick] : head
}
