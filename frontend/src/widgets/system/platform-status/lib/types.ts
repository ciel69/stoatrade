import type { LinkState } from '@/shared/api'
import type { BrainSnapshot } from '@/entities/brain'
import type { CtlSnapshot } from '@/entities/control'
import type { NewsSnapshot } from '@/entities/headline'
import type { MarketSnapshot } from '@/entities/market'
import type { SystemSnapshot } from '@/entities/system'
import type { HomeSnapshot } from '@/entities/trading'

/** ok, warn, bad: уровни проблемы; idle: нет данных или не активно (на цвет индикатора не влияет, пока есть данные в норме). */
export type StatusLevel = 'ok' | 'warn' | 'bad' | 'idle'

export type GroupId = 'link' | 'platform' | 'exec' | 'brain' | 'market' | 'news' | 'gates'

export interface StatusRow {
  id: string
  group: GroupId
  label: string
  level: StatusLevel
  /** значение или возраст одной строкой */
  value: string
  /** при проблеме: что именно не так (показывается в окне) */
  reason: string | null
  /** при проблеме: короткая фраза для индикатора в шапке */
  brief: string | null
}

export interface StatusGroup {
  id: GroupId
  label: string
  level: StatusLevel
  rows: StatusRow[]
}

export interface StatusSummary {
  level: StatusLevel
  /** короткий текст рядом с индикатором */
  text: string
  /** сколько ещё проблемных строк, кроме названной в тексте */
  extra: number
  groups: StatusGroup[]
  problems: number
}

/** Всё, из чего считается состояние: снимки тем и время клиента. Любой снимок может отсутствовать: строки без данных не выдумываются. */
export interface StatusInput {
  now: number
  link: LinkState
  home: HomeSnapshot | null
  system: SystemSnapshot | null
  brain: BrainSnapshot | null
  market: MarketSnapshot | null
  news: NewsSnapshot | null
  ctl: CtlSnapshot | null
}
