import type { UsageBudget, UsageBy, UsageGroup, UsagePeriod } from '../model/types'

/** Чистая логика блока «Сколько стоит»: ключи кэша, доли стоимости, шкалы бюджета, пороги подсветки. */

export const PERIODS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '1', label: 'Сутки' },
  { value: '7', label: '7 дней' },
  { value: '30', label: '30 дней' },
]
export const CUTS: ReadonlyArray<{ value: UsageBy; label: string }> = [
  { value: 'model', label: 'По моделям' },
  { value: 'provider', label: 'По провайдерам' },
  { value: 'role', label: 'По ролям' },
]

/** Ключ кэша ответа: период и разрез. */
export const usageKey = (days: UsagePeriod | number, by: UsageBy | string): string => `${days}:${by}`

/** Группы по убыванию стоимости (при равенстве по числу вызовов), с долей от общей стоимости групп 0..1. Нулевая сумма: все доли 0. */
export function withShares(groups: UsageGroup[]): Array<UsageGroup & { share: number }> {
  const total = groups.reduce((a, g) => a + (g.usd ?? 0), 0)
  return [...groups]
    .sort((a, b) => (b.usd ?? 0) - (a.usd ?? 0) || (b.calls ?? 0) - (a.calls ?? 0))
    .map((g) => ({ ...g, share: total > 0 ? (g.usd ?? 0) / total : 0 }))
}

/** Подсветка: доля ≥ 15% при ≥ 20 вызовах (как в блоке ролей). */
export const isHigh = (rate: number | null | undefined, calls: number | null | undefined): boolean => (rate ?? 0) >= 0.15 && (calls ?? 0) >= 20

/** Оставшаяся доля потолка 0..1. */
export const leftFrac = (b: Pick<UsageBudget, 'used' | 'cap'>): number => (b.cap > 0 ? Math.max(0, Math.min(1, (b.cap - b.used) / b.cap)) : 0)

/** Цвет шкалы по остатку: меньше 15% плохо, меньше 35% внимание. */
export function budgetLevel(left: number): 'ok' | 'warn' | 'bad' {
  return left < 0.15 ? 'bad' : left < 0.35 ? 'warn' : 'ok'
}

const KEY_WORDS: Record<string, { title: string; money: boolean }> = {
  day_usd: { title: 'Деньги за день', money: true },
  hour_usd: { title: 'Деньги за час', money: true },
  day_tokens: { title: 'Токены за день', money: false },
  hour_tokens: { title: 'Токены за час', money: false },
}
const ORDER = ['day_usd', 'hour_usd', 'day_tokens', 'hour_tokens']
export const budgetKind = (b: UsageBudget): string => b.cred ?? b.key ?? ''

/** Потолки для показа: сначала общие (область all), затем по провайдерам; внутри деньги/токены, день/час. */
export function sortBudget(list: UsageBudget[]): UsageBudget[] {
  const rank = (b: UsageBudget) => (b.scope === 'all' ? 0 : 1) * 10 + (ORDER.indexOf(budgetKind(b)) + 1 || 9)
  return [...list].sort((a, b) => rank(a) - rank(b) || a.scope.localeCompare(b.scope))
}

/** Заголовок шкалы: «Деньги за день»; для потолка одного провайдера добавляем его имя. */
export function budgetTitle(b: UsageBudget): string {
  const t = KEY_WORDS[budgetKind(b)]?.title ?? budgetKind(b)
  return b.scope && b.scope !== 'all' ? `${t}, ${b.scope}` : t
}

/** Число потолка в своих единицах: «$1.20» или «45 тыс.» токенов. */
export function budgetValue(b: UsageBudget, x: number): string {
  if (KEY_WORDS[budgetKind(b)]?.money) return `$${x < 1 ? x.toFixed(4) : x.toFixed(2)}`
  return x >= 1_000_000 ? `${Number((x / 1_000_000).toFixed(2))} млн` : x >= 1000 ? `${Number((x / 1000).toFixed(1))} тыс.` : String(Math.round(x))
}

/** «Потрачено $0.10 из $3.00, сбросится через 5 ч 10 мин»; момент сброса абсолютный, остаток считаем от now (секунды). */
export function budgetCaption(b: UsageBudget, nowSeconds: number, durationFn: (s: number) => string): string {
  const left = b.until_ts - nowSeconds
  return `потрачено ${budgetValue(b, b.used)} из ${budgetValue(b, b.cap)}, ${left > 0 ? `сбросится через ${durationFn(left)}` : 'окно сбрасывается'}`
}

/** Миллисекунды: «850 мс», «2.3 с». */
export function msText(x: number | null | undefined): string {
  if (x == null || !Number.isFinite(x)) return '–'
  return x >= 1000 ? `${(x / 1000).toFixed(1)} с` : `${Math.round(x)} мс`
}

/** Число с пробелами для разрядов: 12 345. */
export const intText = (x: number | null | undefined): string => (x == null || !Number.isFinite(x) ? '–' : Math.round(x).toLocaleString('ru-RU'))

/** Токены сокращённо: 950, 12.3 тыс., 1.2 млн. */
export function toksText(x: number | null | undefined): string {
  if (x == null || !Number.isFinite(x)) return '–'
  if (x >= 1_000_000) return `${(x / 1_000_000).toFixed(1)} млн`
  if (x >= 1000) return `${(x / 1000).toFixed(1)} тыс.`
  return String(Math.round(x))
}

/** Пояснение для бесплатной группы: только если это локальный провайдер (id 'local'). */
export const isFreeLocal = (g: Pick<UsageGroup, 'id' | 'usd'>, by: UsageBy | string): boolean => by === 'provider' && g.id === 'local' && !(g.usd ?? 0)

/** Один вывод над блоком: сколько стоило за период и какая группа основная. */
export function costLine(usd: number | null | undefined, calls: number | null | undefined, days: number, top: { id: string; share: number } | null, label: (id: string) => string): string {
  const per = days === 1 ? 'за сутки' : `за ${days} дн.`
  if (!calls) return `${days === 1 ? 'За сутки' : `За ${days} дн.`} вызовов не было`
  const cost = `$${(usd ?? 0) < 1 ? (usd ?? 0).toFixed(4) : (usd ?? 0).toFixed(2)}`
  const main = top && top.share >= 0.5 ? `, основная доля у «${label(top.id)}» (${Math.round(top.share * 100)}%)` : ''
  return `Расход ${per}: ${cost}${main}`
}
