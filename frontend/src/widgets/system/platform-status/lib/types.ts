import type { LinkState } from '@/shared/api'
import type { Headline, Human } from '@/shared/lib'
import type { BrainSnapshot } from '@/entities/brain'
import type { CtlSnapshot } from '@/entities/control'
import type { DaySnapshot } from '@/entities/day'
import type { NewsSnapshot } from '@/entities/headline'
import type { MarketSnapshot } from '@/entities/market'
import type { SystemSnapshot } from '@/entities/system'
import type { HomeSnapshot } from '@/entities/trading'

/** ok, warn, bad: уровни проблемы; idle: справка или нет данных (на цвет индикатора не влияет). */
export type StatusLevel = 'ok' | 'warn' | 'bad' | 'idle'

export type GroupId = 'link' | 'platform' | 'exec' | 'brain' | 'market' | 'news' | 'live'

export interface StatusRow {
  id: string
  group: GroupId
  /** при проблеме: простая фраза «что происходит»; в норме: название проверки */
  label: string
  level: StatusLevel
  /** в норме: короткое состояние («работает», «5 мин назад») */
  value: string
  /** при проблеме: что это значит и что делать, обычным языком */
  note: string | null
  /** короткая фраза для индикатора в шапке (с большой буквы, без точки) */
  brief: string | null
  /** красный и торговля действительно остановлена (иначе красный значит «нужно внимание») */
  stops: boolean
  /** исходный код и текст сервера: только для «Подробностей для специалиста» */
  raw: string | null
  human: Human | null
  /** страница, где это решается (внутренняя ссылка из окна состояния); без неё строка только сообщает */
  href?: string
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
  headline: Headline
  /** 3–6 самых важных строк */
  key: StatusRow[]
  /** все проверки, сгруппированные простыми словами */
  groups: StatusGroup[]
  /** строки warn и bad */
  problems: number
  /** справочные строки (idle с пояснением) */
  notes: number
  /** технические строки для специалиста */
  details: string[]
  /** ссылка на страницу, где решается главная проблема (если она есть у строки) */
  link: { to: string; label: string } | null
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
  /** торговый день: вопросы о переносе, возобновление, готовый итог (тема day); нет данных: строки нет */
  day?: DaySnapshot | null
}
