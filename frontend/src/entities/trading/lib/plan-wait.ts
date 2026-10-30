import { countdown, duration, price } from '@/shared/lib'
import type { NearPlan } from '../model/types'

/** Причины блокировки входа от исполнителя -> понятные слова. */
const BLOCKED: Record<string, string> = {
  CHASE: 'цена ушла от уровня дальше допуска, догонять не будем',
  SPREAD: 'спред слишком широкий',
  BLACKOUT: 'идёт окно новостей',
  KILL: 'включена аварийная остановка',
  PAUSE: 'платформа на паузе после серии стопов',
}

const CANDLE = new Set(['candle_close', 'candle_reject', 'candle_body', 'volume_spike'])

export interface PlanWait {
  /** Главное: чего план ждёт сейчас, одной фразой. */
  headline: string
  /** Остальные невыполненные условия. */
  rest: string[]
  /** Когда наступит ближайшая проверка или чем она определяется. */
  when: string
  /** Сколько условий входа уже выполнено. */
  progress: string | null
  /** Главное сообщение остановлено (блокировка, пауза, черновик). */
  tone: 'wait' | 'blocked' | 'paused'
}

/** Объяснение плана: чего он ждёт, что ещё не выполнено и когда будет следующая проверка. nowS: текущее время, с. */
export function planWait(p: NearPlan, nowS: number): PlanWait {
  const lvl = p.level != null ? price(p.level) : null
  const candle = p.kind != null && CANDLE.has(p.kind)
  const eta = p.eta_at != null && p.eta_at > nowS ? p.eta_at - nowS : null
  const miss = p.missing.length ? p.missing : p.text ? [p.text] : []
  const expires = p.ttl_at != null && p.ttl_at > nowS ? `, план истекает через ${duration(p.ttl_at - nowS)}` : ''

  let progress: string | null = null
  if (p.seq && p.seq.total > 1) progress = `шаг ${Math.min(p.seq.done + 1, p.seq.total)} из ${p.seq.total}`
  else if (p.leaf_n > 1) progress = `выполнено ${p.met_n} из ${p.leaf_n}`

  if (p.blocked_why) {
    return { headline: `Вход заблокирован: ${BLOCKED[p.blocked_why] ?? p.blocked_why}`, rest: [], when: `Условия входа выполнены, ждёт снятия блокировки${expires}`, progress, tone: 'blocked' }
  }
  if (p.hold) {
    return { headline: `План на паузе${p.hold_reason ? `: ${p.hold_reason}` : ''}`, rest: miss.slice(0, 1), when: `Возобновится, когда пауза снимется${expires}`, progress, tone: 'paused' }
  }
  if (p.status === 'draft') {
    return { headline: 'Черновик: план ещё не взведён', rest: miss, when: 'Начнёт ждать вход после взведения', progress, tone: 'paused' }
  }
  if (p.entry_state === 'resting') {
    return { headline: `Лимитная заявка уже стоит${lvl ? ` на ${lvl}` : ''}`, rest: [], when: `Исполнится, когда цена дойдёт до заявки${expires}`, progress, tone: 'wait' }
  }
  if (!miss.length) {
    return { headline: 'Условия выполнены, идёт попытка входа', rest: [], when: `Вход по рынку или лимиту в ближайшие секунды${expires}`, progress, tone: 'wait' }
  }

  const first = miss[0]
  let headline: string
  let when: string
  if (candle) {
    headline = `Ждёт: ${first}`
    if (p.beyond && p.dir) headline += `. Цена уже ${p.dir === 'above' ? 'выше' : 'ниже'} уровня, но нужна фиксация закрытием свечи`
    when = eta != null ? `Проверка при закрытии свечи ${p.tf ?? ''} через ${countdown(eta)}`.replace('  ', ' ') : 'Проверка при закрытии ближайшей свечи'
  } else if (p.kind === 'price_in_zone') {
    headline = `Ждёт: ${first}`
    when = 'Проверяется непрерывно по цене, времени нет'
  } else {
    headline = `Ждёт: ${first}`
    when = p.level != null && p.price != null ? `Проверяется непрерывно по цене, сейчас ${price(p.price)}` : 'Проверяется непрерывно по цене'
  }
  return { headline, rest: miss.slice(1), when: when + expires, progress, tone: 'wait' }
}
