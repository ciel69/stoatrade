import { HEAT_STALE_S } from '@/entities/market'
import type { StatusLevel } from './types'

/**
 * Пороги «устарело» для данных, у которых сервер не присылает уровень (его считает клиент по моменту `at`).
 * Остальные узлы (исполнитель, биржа, сверка, фид, сканер, контекст, сессия) приходят с готовым уровнем сервера (src/tb/dash/model.py: STALE_FEED_S 30, STALE_HOT_S 180, STALE_CONTEXT_S 600).
 */
export const THRESHOLDS = {
  /** тепловая карта: тот же порог, что в MarketMap */
  heatS: HEAT_STALE_S,
  /** цены монет из фида, когда узла `feed` нет в теме system: PRICE_STALE_S сервера (src/tb/dash/model.py) */
  coinsS: 60,
  /** заголовки: свежий заголовок лент появляется не реже раза в пару часов; тише считаем, что ленты не обновляются (порога на сервере нет) */
  newsQuietS: 7200,
  /** во сколько раз старше порога данные считаются сломанными, а не просто отставшими */
  badFactor: 5,
} as const

/** Свежесть по возрасту: до порога норма, до порога x badFactor внимание, дальше проблема. Нет возраста: нет данных. */
export function freshLevel(ageS: number | null | undefined, staleS: number): StatusLevel {
  if (typeof ageS !== 'number' || !Number.isFinite(ageS)) return 'idle'
  if (ageS <= staleS) return 'ok'
  return ageS <= staleS * THRESHOLDS.badFactor ? 'warn' : 'bad'
}
