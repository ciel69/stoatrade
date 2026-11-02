/** Уровень риска (1-5): названия, аргументы действия risk.set, «было → станет» и последствия словами. Лимиты уровней задаёт платформа, здесь только слова. */

export const RISK_LEVELS: readonly number[] = [1, 2, 3, 4, 5]

const NAMES: Record<number, string> = { 1: 'Защитный', 2: 'Осторожный', 3: 'Базовый', 4: 'Активный', 5: 'Агрессивный' }

/** Название уровня; серверное имя («Базовый (как сейчас)») укорачивается до слова до скобки. */
export function levelName(level: number | null | undefined, serverName?: string | null): string {
  if (level == null) return 'не определён'
  const own = serverName ? serverName.replace(/\s*\(.*\)\s*$/, '').trim() : ''
  return own || NAMES[level] || `уровень ${level}`
}

/** Короткая подпись: «3 · Базовый». */
export const levelLabel = (level: number | null | undefined, serverName?: string | null): string => (level == null ? 'не определён' : `${level} · ${levelName(level, serverName)}`)

export type Direction = 'up' | 'down' | 'same'

export function directionOf(from: number | null | undefined, to: number): Direction {
  if (from == null || from === to) return 'same'
  return to > from ? 'up' : 'down'
}

export const isLevel = (n: unknown): n is number => typeof n === 'number' && Number.isInteger(n) && RISK_LEVELS.includes(n)

/** Аргументы risk.set: принятые предупреждения (accepted) добавляет движок действий из challenge. */
export function riskArgs(level: number): { level: number } {
  return { level }
}

/** Можно ли применять: уровень выбран, допустим и отличается от текущего. */
export const canApplyLevel = (current: number | null | undefined, draft: number | null): draft is number => draft != null && isLevel(draft) && draft !== current

/** «Было → станет» одной строкой. */
export function riskLine(from: number | null | undefined, to: number, fromName?: string | null): string {
  return `${levelLabel(from, fromName)} → ${levelLabel(to)}`
}

/** Что изменится, словами: куда двигаются лимиты и что не затрагивается. */
export function riskEffect(from: number | null | undefined, to: number): string {
  const d = directionOf(from, to)
  if (d === 'up') return 'Лимиты на новые сделки станут шире: допустимый риск на сделку, дневной убыток и число одновременных позиций вырастут. Открытые позиции не меняются.'
  if (d === 'down') return 'Лимиты на новые сделки станут уже: допустимый риск на сделку, дневной убыток и число одновременных позиций уменьшатся. Открытые позиции не меняются.'
  return 'Уровень не меняется.'
}

export const riskTitle = (to: number): string => `Уровень риска ${to}: ${levelName(to)}`

/** Данные отразили смену: тема ctl уже показывает нужный уровень. */
export const riskReflects = (current: number | null | undefined, to: number): boolean => current === to
