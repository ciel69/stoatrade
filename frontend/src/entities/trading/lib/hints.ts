import { duration, fixed, price } from '@/shared/lib'
import type { TipContent } from '@/shared/ui'
import type { NearPlan } from '../model/types'

/**
 * Подсказки карточек плана и позиции. Все тексты в одном месте, написаны для того, кто видит систему впервые:
 * коротко (1-2 предложения), термин либо не используется, либо расшифрован.
 */

export type Side = 'long' | 'short' | null | undefined

/** «лонг» / «шорт» и плечо. */
export function sideHint(side: Side, lev?: number | null): TipContent {
  const leverage = lev ? ` Плечо ×${lev}: позиция в ${lev} раз больше вашей маржи, оно усиливает и прибыль, и убыток.` : ''
  if (side === 'long') return { title: 'Лонг: ставка на рост', text: `Прибыль, если цена вырастет, убыток, если упадёт.${leverage}` }
  if (side === 'short') return { title: 'Шорт: ставка на падение', text: `Прибыль, если цена упадёт, убыток, если вырастет.${leverage}` }
  return { title: 'Направление не задано', text: 'У плана пока нет стороны: лонг (на рост) или шорт (на падение).' }
}

/** Подпись чипа «заявка на бирже» (entry_state === 'resting'). */
export const RESTING_LABEL = 'заявка'        // короче прежнего «лимит стоит»: шапка карточки на 390 px заполнена до края

export const restingHint: TipContent = {
  title: 'Заявка на бирже',
  text: 'Заявка на вход уже выставлена на бирже по цене уровня, сделка откроется сама, когда цена до него дойдёт. Пока позиции нет, и деньги не рискуются.',
}

export const draftHint: TipContent = {
  title: 'Черновик',
  text: 'План написан, но ещё не включён: пока его не включат, вход не ищется.',
}

/** Стоп подтянут: убыток урезан (стоп ещё на стороне убытка) или прибыль зафиксирована (стоп за точкой входа). */
export type StopMove = 'cut' | 'locked'

export function stopMoveKind(side: 'long' | 'short', stop: number, avg: number): StopMove {
  const sign = side === 'long' ? 1 : -1
  return sign * (stop - avg) >= 0 ? 'locked' : 'cut'
}

export function stopMovedHint(kind: StopMove): TipContent {
  return kind === 'locked'
    ? { title: 'Стоп подтянут: прибыль зафиксирована', text: 'Стоп-лосс (автоматический выход при убытке) сдвинут за точку входа. Если он сработает, сделка закроется не в убыток, а с частью прибыли.' }
    : { title: 'Стоп подтянут: убыток урезан', text: 'Стоп-лосс (автоматический выход при убытке) сдвинут ближе к цене. Если он сработает, вы потеряете меньше, чем планировалось вначале.' }
}

/** Процент справа в заголовке плана: расстояние до уровня входа. */
export function distHint(crossed: boolean): TipContent {
  return crossed
    ? { title: 'Цена уже за уровнем', text: 'Процент показывает, на сколько цена зашла за уровень входа. Вход ждёт закрытия свечи: если она закроется за уровнем, сделка откроется.' }
    : { title: 'Расстояние до входа', text: 'На сколько процентов цене ещё нужно дойти до уровня входа. Чем меньше число, тем ближе вход.' }
}

/** Кольцо и время рядом с процентом. */
export function timerHint(tf: string | null | undefined): TipContent {
  const t = tf ? ` ${tf}` : ''
  return { title: 'До закрытия свечи', text: `Условие входа проверяется, когда закрывается свеча${t}. Кольцо показывает, какая часть её времени уже прошла.` }
}

/** Точки условий. */
export function dotsHint(done: number, total: number, missing: readonly string[]): TipContent {
  const rows = missing.length ? [{ label: 'Ещё нужно', value: missing.join('; ') }] : undefined
  return { title: `Условия входа: выполнено ${done} из ${total}`, text: 'Сделка откроется, когда выполнятся все условия.', rows }
}

/** Процент справа в заголовке позиции. movePct: уже отформатированный сдвиг цены от входа, например «+0.4». */
export function roiHint(movePct: string): TipContent {
  return { title: 'Результат к марже', text: `Прибыль или убыток относительно вашей маржи, с плечом и комиссией входа. Сама цена от входа сдвинулась на ${movePct}%.` }
}

export const staleHint: TipContent = {
  title: 'Цена устарела',
  text: 'Свежая цена по этой монете давно не приходила, поэтому прибыль и убыток на экране могут отставать от биржи.',
}

/** Метки на полосе от стопа к цели. */
export const stripHints = {
  stop: 'Стоп: цена, при которой позиция закроется сама, чтобы убыток не вырос.',
  safe: 'Стоп за входом: если сработает, сделка закроется с прибылью, а не с убытком.',
  entry: 'Вход: цена, по которой открыта позиция.',
  level: 'Вход: уровень, до которого должна дойти цена, чтобы сделка открылась.',
  take: 'Тейк: цель, на которой фиксируем прибыль.',
  now: 'Цена: где монета торгуется сейчас.',
} as const

/** Слова под полосой. */
export const captionHints: Record<'stop' | 'take', TipContent> = {
  stop: { title: 'Стоп', text: stripHints.stop },
  take: { title: 'Тейк', text: stripHints.take },
}

/** Всё, что раньше склеивалось в один title строки плана: идея, сетап, условия, риск, RR, срок. */
export function planInfoHint(p: NearPlan, nowS: number): TipContent {
  const rows: Array<{ label: string; value: string }> = []
  const add = (label: string, value: string | null | undefined | false): void => { if (value) rows.push({ label, value }) }
  add('Идея', p.thesis)
  add('Сетап', p.setup)
  add('Вход', p.entry_text)
  add('Ещё нужно', p.missing.length > 1 ? p.missing.slice(1).join('; ') : '')
  add('Цена сейчас', p.price != null ? price(p.price) : '')
  add('Риск', p.risk != null ? `${fixed(p.risk, 2)} USDT: столько потеряете, если сработает стоп` : '')
  add('Выигрыш к риску (RR)', p.net_rr != null ? `${fixed(p.net_rr, 1)} к 1, после издержек` : '')
  add('План истекает через', p.ttl_at != null && p.ttl_at > nowS ? duration(p.ttl_at - nowS) : '')
  return { title: `${p.sym}: о плане`, rows: rows.length ? rows : undefined, text: rows.length ? undefined : 'Подробностей у плана пока нет.' }
}
