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

/**
 * Всего токенов группы = in + out. Так считает и сервер (`window_totals` в src/tb/llm/usage.py: tokens += in + out), поэтому сумма по группам равна `total.toks` периода.
 * `in` уже включает кэш (`in_hit` его часть, miss + hit + write), `out` уже включает рассуждение (`reasoning` его часть), поэтому их нельзя прибавлять ещё раз.
 */
export const groupToks = (g: Pick<UsageGroup, 'in' | 'out'>): number => (g.in ?? 0) + (g.out ?? 0)

/** Доля группы от всех токенов списка 0..1; нулевая сумма: 0. */
export function toksShare(g: Pick<UsageGroup, 'in' | 'out'>, groups: ReadonlyArray<Pick<UsageGroup, 'in' | 'out'>>): number {
  const total = groups.reduce((a, x) => a + groupToks(x), 0)
  return total > 0 ? groupToks(g) / total : 0
}

const DASH = '–'
/** Деление с «–» при нулевом или пустом знаменателе и нечисловом результате. */
const per = (a: number | null | undefined, b: number | null | undefined, fmt: (x: number) => string): string => {
  const x = (a ?? 0) / (b ?? 0)
  return b && Number.isFinite(x) ? fmt(x) : DASH
}
/** Доля 0..1 в процентах с одним знаком, если меньше 10%: «4.2%», «35%». */
export const pctText = (x: number | null | undefined): string => (x == null || !Number.isFinite(x) ? DASH : `${Number((x * 100).toFixed(x < 0.1 ? 1 : 0))}%`)
/** Деньги: меньше доллара с четырьмя знаками. */
export const usdText = (x: number | null | undefined): string => (x == null || !Number.isFinite(x) ? DASH : `$${x < 1 ? x.toFixed(4) : x.toFixed(2)}`)
const rate = (x: number): string => (x < 10 ? x.toFixed(1) : String(Math.round(x)))

/** Часть состава токенов для сегментной полосы подсказки. Части не пересекаются и в сумме дают in + out. */
export interface MixPart { key: 'cache' | 'miss' | 'answer' | 'think'; label: string; value: number; text: string; share: number }
/** Значение сетки «скорость и качество»; hot подсвечивает проблему (порог как в строке: от 15% при 20+ вызовов). */
export interface TipStat { key: string; label: string; value: string; note: string; help: string; hot: boolean }
export interface TokTip {
  total: string
  perCall: string
  perCallIn: string
  perCallOut: string
  mix: MixPart[]
  stats: TipStat[]
  free: boolean
  callCost: string
  millionCost: string
}

/**
 * Данные подсказки группы. Семантика (src/tb/llm/types.py): `in` включает кэш (`in_hit` его часть), `out` включает рассуждение (`reasoning` его часть), всего = in + out.
 * Состав: кэш входа, вход мимо кэша, выход-ответ (out минус рассуждение) и рассуждение; части не пересекаются, доли считаются от всего.
 * Скорость вывода = (out / calls) / (p50 в секундах): грубая оценка, p50 включает ожидание и ввод, реальная скорость генерации выше.
 * Цена: при usd > 0 цена вызова и за 1 млн токенов, иначе «бесплатно». Деление на ноль даёт «–».
 */
export function toksTip(g: UsageGroup): TokTip {
  const inn = g.in ?? 0, out = g.out ?? 0, calls = g.calls ?? 0, usd = g.usd ?? 0
  const total = groupToks(g)
  const cache = Math.min(g.in_hit ?? 0, inn)
  const think = Math.min(g.reasoning ?? 0, out)
  const parts: Array<[MixPart['key'], string, number]> = [
    ['cache', 'Вход из кэша', cache],
    ['miss', 'Вход мимо кэша', inn - cache],
    ['answer', 'Выход: ответ', out - think],
    ['think', 'Выход: рассуждение', think],
  ]
  const secs = g.lat_p50_ms != null && g.lat_p50_ms > 0 ? g.lat_p50_ms / 1000 : 0
  const free = !(usd > 0)
  return {
    total: toksText(total),
    perCall: per(total, calls, toksText),
    perCallIn: per(inn, calls, toksText),
    perCallOut: per(out, calls, toksText),
    mix: parts.map(([key, label, value]) => ({ key, label, value, text: toksText(value), share: total > 0 ? value / total : 0 })),
    stats: [
      { key: 'p50', label: 'Типичный ответ', value: msText(g.lat_p50_ms), note: 'p50', help: 'Половина ответов приходит быстрее этого времени.', hot: false },
      { key: 'p95', label: 'Медленный ответ', value: msText(g.lat_p95_ms), note: 'p95', help: 'Медленнее приходит только каждый двадцатый ответ.', hot: false },
      { key: 'speed', label: 'Скорость вывода', value: secs && calls ? rate(out / calls / secs) : DASH, note: 'ток/с, оценка', help: 'Выход на вызов делим на p50. Это нижняя оценка: p50 включает ожидание.', hot: false },
      { key: 'err', label: 'Ошибки', value: pctText(g.err_rate), note: `${intText(g.errors ?? 0)} шт.`, help: 'Вызовы, закончившиеся ошибкой.', hot: isHigh(g.err_rate, g.calls) },
      { key: 'fmt', label: 'Неверный формат', value: pctText(g.fmt_err_rate), note: `${intText(g.fmt_errors ?? 0)} шт.`, help: 'Ответы, не принятые по формату.', hot: isHigh(g.fmt_err_rate, g.calls) },
      { key: 'fb', label: 'На запасную модель', value: intText(g.fallbacks ?? 0), note: 'переключений', help: 'Сколько раз вызов ушёл на запасную модель роли.', hot: false },
    ],
    free,
    callCost: free ? 'бесплатно' : per(usd, calls, usdText),
    millionCost: free ? 'бесплатно' : per(usd * 1_000_000, total, usdText),
  }
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

/** Выбранная строка расхода в адресе страницы: `?usage=ID&days=1|7|30&by=model|provider|role`. Период и разрез в адресе нужны, чтобы панель открылась на тех же данных. */
export interface UsagePanelRef { id: string; days: UsagePeriod; by: UsageBy }
export const usagePanelQuery = (id: string, days: UsagePeriod, by: UsageBy): Record<string, string> => ({ usage: id, days: String(days), by })
/** Разбор адреса: неполные или неверные параметры дают null (панель не открывается). */
export function parseUsagePanel(q: Record<string, unknown>): UsagePanelRef | null {
  const id = typeof q.usage === 'string' ? q.usage : ''
  const days = Number(typeof q.days === 'string' ? q.days : NaN)
  const by = typeof q.by === 'string' ? q.by : ''
  if (!id || !PERIODS.some((p) => p.value === String(days)) || !CUTS.some((c) => c.value === by)) return null
  return { id, days: days as UsagePeriod, by: by as UsageBy }
}
/** Подзаголовок панели: «Сутки · по моделям». */
export function panelSubtitle(days: UsagePeriod | number, by: UsageBy | string): string {
  const p = PERIODS.find((x) => x.value === String(days))?.label ?? `${days} дн.`
  const c = (CUTS.find((x) => x.value === by)?.label ?? String(by)).toLowerCase()
  return `${p} · ${c}`
}
