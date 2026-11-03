import { estView, type EstView, type HypFamily, type HypRef, type HypStatus, type HypsBlock } from '@/entities/learning'
import { LEARN_SMALL, ageText, fixed, plural } from '@/shared/lib'
import type { TipContent } from '@/shared/ui'

/**
 * Слова и числа блока «Гипотезы» (R2, Requirement 19.6; сервер: src/tb/learn/stages.py: hyps_block). Гипотеза — тег плана «почему мозг входит»,
 * сервер сводит варианты написания к семействам. Статус считает сервер (expstats.hyp_confirmed); справка меток — ориентир и статус не меняет.
 */

const fin = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x)

export const HYP_MIN_N = 30

/** Семейства словаря config/hypotheses.json словами. Неизвестное семейство показывается кодом без дефисов. */
const FAMILY: Readonly<Record<string, { name: string; text: string }>> = {
  'reclaim-level': { name: 'Возврат за уровень', text: 'Цена ушла за уровень и вернулась обратно; вход на ставке, что уровень удержится.' },
  'sweep-reject': { name: 'Отбой после прокола', text: 'Цена на миг проколола уровень, выбила чужие стопы и вернулась; вход против прокола.' },
  'range-fade': { name: 'От границ диапазона', text: 'Цена ходит в коридоре; вход от границы в сторону середины.' },
  'breakout-retest': { name: 'Пробой с возвратом', text: 'Цена пробила уровень, вернулась его проверить и удержалась; вход на проверке.' },
  'trend-pullback': { name: 'Откат по тренду', text: 'Цена идёт в одну сторону и ненадолго откатывает; вход по ходу тренда в конце отката.' },
  'oi-flush-reclaim': { name: 'После сброса позиций', text: 'Открытый интерес резко упал (позиции массово закрылись), цена вернулась; вход на возврате.' },
  'oi-build-squeeze': { name: 'Сжатие на наборе позиций', text: 'Открытый интерес быстро растёт; ставка на резкое движение, которое выбьет одну из сторон.' },
  'funding-crowded-reversal': { name: 'Разворот перекоса', text: 'Ставка финансирования показывает, что большинство стоит в одну сторону; ставка на разворот против толпы.' },
  'liq-cascade-reclaim': { name: 'После волны ликвидаций', text: 'Прошла волна принудительных закрытий, цена вернулась; вход на возврате против волны.' },
  'cvd-absorption': { name: 'Поглощение потока', text: 'Агрессивные покупки или продажи не двигают цену: кто-то крупный их забирает; вход в сторону этого игрока.' },
  'wall-defense': { name: 'Защита стены заявок', text: 'Крупная заявка в стакане держит цену; вход от неё.' },
  'news-listing-retest': { name: 'Листинг с возвратом', text: 'После новости о листинге цена вернулась проверить уровень; вход на проверке.' },
  'news-negative-short': { name: 'Шорт на плохой новости', text: 'Ставка на падение после негативной новости.' },
  'macro-wait': { name: 'Ожидание макро', text: 'Осторожность у важных экономических новостей.' },
  'level-reject': { name: 'Отбой от уровня', text: 'Цена подошла к уровню и развернулась; ставка на отскок.' },
  'momentum-chase': { name: 'За импульсом', text: 'Вход вдогонку резкому движению по рыночной цене. Рискованнее остальных схем.' },
  other: { name: 'Прочие', text: 'Теги, которых нет в словаре гипотез. Их сделки считаются отдельно и подтвердиться не могут.' },
}

export const familyName = (f: string): string => FAMILY[f]?.name ?? f.replace(/[-_]+/g, ' ')

export function familyHint(f: string): TipContent {
  const d = FAMILY[f]
  return { title: familyName(f), text: d?.text ?? 'Гипотеза, для которой нет описания.', rows: [{ label: 'Код', value: f }] }
}

export type HypTone = 'ok' | 'accent' | 'neutral'
const STATUS: Readonly<Record<HypStatus, { word: string; tone: HypTone; text: string }>> = {
  confirmed: { word: 'подтверждена', tone: 'ok', text: 'Набрано не меньше 30 сделок, средний результат выше нуля, и это вряд ли случайность (с поправкой на число гипотез). Мозг чуть чаще выбирает такие сделки.' },
  accumulating: { word: 'копит', tone: 'accent', text: 'Сделок меньше 30: гипотеза набирает данные. Делать выводы рано, ничего делать не нужно.' },
  not_confirmed: { word: 'не подтверждена', tone: 'neutral', text: 'Сделок достаточно, но средний результат не выше нуля или разница может быть случайной. Статус пересчитывается сам с каждой сделкой.' },
}
const OTHER_TEXT = 'Прочие теги в подтверждение не идут: это разные идеи, сведённые вместе.'

export interface ProgressView {
  /** доля полосы 0..1 */
  ratio: number
  /** «24 из 30», «48 сделок, порог 30 пройден» */
  text: string
  done: boolean
  label: string
}

export function progressView(n: number, target = HYP_MIN_N): ProgressView {
  const k = fin(n) && n > 0 ? n : 0
  const t = fin(target) && target > 0 ? target : HYP_MIN_N
  const done = k >= t
  return {
    ratio: Math.min(1, k / t),
    text: done ? `${k} ${plural(k, ['сделка', 'сделки', 'сделок'])}, порог ${t} пройден` : `${k} из ${t}`,
    done,
    label: `Набрано ${k} ${plural(k, ['сделка', 'сделки', 'сделок'])} из ${t} нужных`,
  }
}

export interface RefView {
  /** «P4, против направления, по сигналам liq cascade» */
  who: string
  r: EstView
  /** «n = 42 часа с сигналами» */
  n: string
  small: string
  tip: TipContent
}

const POLICY: Readonly<Record<string, string>> = { P2: 'вход по направлению сигнала', P4: 'вход против направления сигнала', P5: 'базовая линия', P6: 'случайная сторона' }

export function refView(ref: HypRef | null | undefined): RefView | null {
  if (!ref) return null
  const n = fin(ref.n) ? ref.n : 0
  const days = fin(ref.window_days) ? ref.window_days : 14
  const kinds = (ref.kinds ?? []).map((k) => k.replace(/_/g, ' ')).join(', ')
  const rule = ref.policy ? POLICY[ref.policy] ?? 'простое правило' : 'простое правило'
  return {
    who: `${ref.policy ?? 'политика'}: ${rule}${kinds ? `, сигналы ${kinds}` : ''}, за ${days} ${plural(days, ['день', 'дня', 'дней'])}`,
    r: estView(ref.r ?? null, 'r', 2, HYP_MIN_N),
    n: `n = ${n} ${plural(n, ['час', 'часа', 'часов'])} с сигналами`,
    small: n < HYP_MIN_N ? LEARN_SMALL(HYP_MIN_N) : '',
    tip: {
      title: 'Что говорят тысячи сигналов',
      text: `Не сделки мозга: код размечает все похожие сигналы рынка и смотрит, что дало бы простое правило на каждом из них. Это ориентир — статус гипотезы он не меняет. Сигналы одного часа и стороны считаются вместе, поэтому n — число таких часов.`,
      rows: [{ label: 'Правило', value: `${ref.policy ?? '?'}${fin(ref.policy_ver) ? `, версия ${ref.policy_ver}` : ''}` }, ...(kinds ? [{ label: 'Сигналы', value: kinds }] : [])],
    },
  }
}

export interface FamilyView {
  key: string
  name: string
  hint: TipContent
  progress: ProgressView
  status: string
  tone: HypTone
  statusTip: TipContent
  r: EstView
  /** «в плюс 55%» либо пусто */
  win: string
  p: string
  ref: RefView | null
}

export function familyView(f: HypFamily): FamilyView {
  const st = STATUS[f.status] ?? STATUS.accumulating
  const other = f.confirmable === false
  const word = other ? 'не подтверждается' : st.word
  return {
    key: f.family,
    name: familyName(f.family),
    hint: familyHint(f.family),
    progress: progressView(f.n, fin(f.n_target) ? f.n_target : HYP_MIN_N),
    status: word,
    tone: other ? 'neutral' : st.tone,
    statusTip: { title: word[0].toUpperCase() + word.slice(1), text: other ? OTHER_TEXT : st.text },
    r: estView(f.n > 0 ? f.r : null, 'r', 2, HYP_MIN_N),
    win: fin(f.win) ? `в плюс ${Math.round(f.win * 100)}%` : '',
    p: fin(f.p) ? `p = ${fixed(f.p, f.p < 0.01 ? 3 : 2)}` : '',
    ref: refView(f.ref),
  }
}

export const familyViews = (b: HypsBlock): FamilyView[] => (b.families ?? []).map(familyView)

/** «подтверждено 1 из 6». */
export function hypsSummary(b: HypsBlock): { word: string; tone: HypTone } {
  const n = (b.families ?? []).length
  const c = fin(b.n_confirmed) ? b.n_confirmed : 0
  return { word: `подтверждено ${c} из ${n}`, tone: c > 0 ? 'ok' : 'neutral' }
}

/** Порог значимости словами: alpha / K. */
export function correctionText(b: HypsBlock): string {
  const k = fin(b.k) ? b.k : 1
  const a = b.alpha_adj
  if (!fin(a)) return ''
  return `Порог случайности: p ниже ${fixed(a, a < 0.01 ? 4 : 3)} — 0.05, поделённое на ${k} ${plural(k, ['гипотезу', 'гипотезы', 'гипотез'])} с 30 и больше сделками.`
}

export function refAgeText(ts: number | null | undefined, nowSeconds: number): string {
  return fin(ts) ? `Справка по сигналам обновлена ${ageText(ts, nowSeconds)}.` : ''
}

export const HYPS_INTRO = 'Каждая сделка на бумаге помечена гипотезой — почему мозг вошёл. Гипотеза подтверждается, когда за ней 30 сделок, средний результат выше нуля и это вряд ли случайность.'
export const HYPS_PAPER = 'Гипотезы проверяются только на бумажной торговле, поэтому блок одинаков при любом режиме в шапке.'
export const HYPS_NONE = 'Бумажных сделок с гипотезой пока нет. Метку ставит сам мозг при каждом плане, ничего делать не нужно.'
export const R_TIP: TipContent = { title: 'Средний R после издержек', text: 'Средний результат сделки в единицах риска с учётом комиссий, с 95% интервалом. +1 R значит заработали столько, сколько готовы были потерять.' }
