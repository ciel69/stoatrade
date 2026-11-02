/**
 * Слова страницы «Торговля · Эксперименты»: разрезы сделок, значения разрезов (сторона, причина выхода, тип сессии, схема входа, площадка) и оценка «насколько это случайно».
 * Названия экспериментов, вариантов, гипотез, тегов ошибок и монет придумывает владелец или мозг: они показываются как есть, это имена, а не служебные поля.
 * Смысл взят из src/tb/core/expstats.py (группы, порог n, поправка Бонферрони) и src/tb/dash2/topics.py (exp_detail).
 */

export type ExpCut = 'hyp' | 'exp' | 'variant' | 'setup' | 'stype' | 'symbol' | 'side' | 'reason' | 'err' | 'venue'

export interface CutInfo {
  value: ExpCut
  label: string
  /** одна фраза: по какому признаку сделки собраны в группы */
  about: string
  /** idea: что проверяем (гипотезы и эксперименты); trade: по какому признаку сделки делятся */
  group: 'idea' | 'trade'
  /** как называется одна группа ("гипотеза", "монета"): для заголовка боковой панели */
  one: string
}

export const EXP_CUTS: ReadonlyArray<CutInfo> = [
  { value: 'hyp', label: 'Гипотезы', about: 'Гипотеза это идея, которую проверяют серией сделок: «после прокола уровня цена возвращается».', group: 'idea', one: 'Гипотеза' },
  { value: 'exp', label: 'Эксперименты', about: 'Эксперимент сравнивает несколько вариантов одной идеи на одинаковых условиях.', group: 'idea', one: 'Эксперимент' },
  { value: 'variant', label: 'Варианты', about: 'Вариант это один из способов в эксперименте: например, стоп поближе или подальше.', group: 'idea', one: 'Вариант' },
  { value: 'setup', label: 'Схема входа', about: 'Схема входа это тип ситуации, по которой открыта сделка: возврат за уровень, откат по тренду.', group: 'trade', one: 'Схема входа' },
  { value: 'stype', label: 'Тип сессии', about: 'На какой срок открыта сделка: скальпинг, внутри дня, свинг, позиция.', group: 'trade', one: 'Тип сессии' },
  { value: 'symbol', label: 'Монета', about: 'Результат по каждой монете.', group: 'trade', one: 'Монета' },
  { value: 'side', label: 'Сторона', about: 'Покупки против продаж.', group: 'trade', one: 'Сторона' },
  { value: 'reason', label: 'Как закрыта', about: 'Чем закончилась сделка: стопом, целью, вручную, по времени.', group: 'trade', one: 'Причина выхода' },
  { value: 'err', label: 'Ошибки', about: 'Теги ошибок, которые поставлены при разборе сделки. Одна сделка может попасть в несколько групп.', group: 'trade', one: 'Ошибка из разбора' },
  { value: 'venue', label: 'Площадка', about: 'На какой бирже или в какой системе совершены сделки.', group: 'trade', one: 'Площадка' },
]

export const EXP_CUT_IDS: string[] = EXP_CUTS.map((c) => c.value)
export const isExpCut = (v: unknown): v is ExpCut => typeof v === 'string' && EXP_CUT_IDS.includes(v)
export const cutInfo = (c: ExpCut): CutInfo => EXP_CUTS.find((x) => x.value === c) ?? EXP_CUTS[0]

const SIDE: Record<string, string> = { long: 'покупка', short: 'продажа' }
const REASON: Record<string, string> = {
  sl: 'сработал стоп', tp: 'достигнута цель', manual: 'закрыта вручную', time: 'вышло время', timeout: 'вышло время', be: 'выход в безубыток', trail: 'сработал подтянутый стоп',
  invalidation: 'тезис отменён', rule: 'закрыта по правилу', liq: 'ликвидация',
}
const STYPE: Record<string, string> = { scalp: 'скальпинг', intraday: 'внутри дня', swing: 'свинг, несколько дней', position: 'позиция на недели', day: 'торговый день' }
const SETUP: Record<string, string> = {
  level_reclaim: 'возврат за уровень', level_reject: 'отбой от уровня', sweep_reject: 'отбой после прокола уровня', breakout_retest: 'пробой с возвратом',
  range_fade: 'игра от границ диапазона', trend_pullback: 'откат по тренду', momentum_chase: 'вход за импульсом', none: 'без схемы',
}
const VENUE: Record<string, string> = { bybit: 'Bybit', platform: 'платформа' }
const MODE_SHORT: Record<string, string> = { paper: 'бумага', live: 'реальные деньги', testnet: 'тестовая биржа' }

/** Режим коротко для таблицы: «бумага», «реальные деньги». */
export const expModeWord = (m: string | null | undefined): string => (m ? (MODE_SHORT[m] ?? 'другой режим') : '')

/** Название группы в разрезе: известные коды словами, свободные имена (эксперимент, гипотеза, монета, тег ошибки) как есть. «Эксперимент/вариант» разбирается на части. */
export function expValueWord(cut: ExpCut, raw: string): string {
  switch (cut) {
    case 'side': return SIDE[raw] ?? raw
    case 'reason': return REASON[raw] ?? raw
    case 'stype': return STYPE[raw] ?? raw
    case 'setup': return SETUP[raw] ?? raw
    case 'venue': return VENUE[raw] ?? raw
    case 'variant': {
      const i = raw.indexOf('/')
      return i > 0 ? `${raw.slice(0, i)}, вариант ${raw.slice(i + 1)}` : raw
    }
    default: return raw
  }
}

export type PTone = 'ok' | 'neutral' | 'warn'
export interface PWord { text: string; tone: PTone }

/**
 * «Насколько это случайно» словами. p это вероятность увидеть такой результат, если на самом деле преимущества нет. Порог с поправкой на число сравнений строже обычного 0,05:
 * чем больше групп сравнивают, тем чаще какая-то из них выглядит удачной просто так.
 */
export function pWord(p: number | null | undefined, small: boolean, alphaAdj: number | null | undefined): PWord {
  if (small) return { text: 'мало данных', tone: 'warn' }
  if (typeof p !== 'number' || !Number.isFinite(p)) return { text: 'оценить нельзя', tone: 'neutral' }
  if (typeof alphaAdj === 'number' && p < alphaAdj) return { text: 'вряд ли случайность', tone: 'ok' }
  if (p < 0.05) return { text: 'похоже на закономерность, но порог строже', tone: 'neutral' }
  if (p < 0.2) return { text: 'может быть случайностью', tone: 'neutral' }
  return { text: 'скорее всего случайность', tone: 'neutral' }
}

/** Число в подсказку к оценке: «p = 0,012; порог 0,0083». */
export function pHint(p: number | null | undefined, alphaAdj: number | null | undefined): string {
  const f = (x: number): string => (x >= 0.001 ? x.toFixed(3) : x.toExponential(1)).replace('.', ',')
  const a = typeof p === 'number' && Number.isFinite(p) ? `p = ${f(p)}` : 'p не посчитано'
  const b = typeof alphaAdj === 'number' && Number.isFinite(alphaAdj) ? `; порог ${f(alphaAdj)}` : ''
  return a + b
}

/** Склонение по числу: plural(2, ['гипотеза', 'гипотезы', 'гипотез']) -> «гипотезы». */
export function plural(n: number, forms: [string, string, string]): string {
  const a = Math.abs(n) % 100
  const b = a % 10
  if (a > 10 && a < 20) return forms[2]
  if (b === 1) return forms[0]
  if (b >= 2 && b <= 4) return forms[1]
  return forms[2]
}
