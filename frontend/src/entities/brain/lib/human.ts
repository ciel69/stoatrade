import { ageText, roleAbout, roleTitle } from '@/shared/lib'
import type { BrainStatus, DecisionRow } from '../model/types'

/*
 * Слова блока «Мозг» для человека, который видит платформу впервые: состояние с пояснением для торговли, имя роли и одна фраза «чем занимается»,
 * формулировки мыслей без служебных кодов. Описания ролей взяты из их инструкций (config/roles): что роль может и чего не может.
 */

/** Склонение: 1 запрос, 2 запроса, 5 запросов. */
export function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10
  const m100 = n % 100
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many
}

// ---------------------------------------------------------------- состояние

export interface StateWords {
  /** одно слово-состояние: «Думает», «Отдыхает» */
  title: string
  /** что это значит для торговли */
  hint: string
}

const STATE: Record<BrainStatus, StateWords> = {
  thinking: { title: 'Думает', hint: 'Разбирает событие на рынке или по позиции. Решение появится ниже, в мыслях.' },
  idle: { title: 'Отдыхает', hint: 'Ждёт нового события. Это нормально: открытые позиции защищены стопами на бирже.' },
  protect: { title: 'Только защита', hint: 'Новые сделки не открывает, только сопровождает открытые.' },
  down: { title: 'Не отвечает', hint: 'Новых планов не будет, пока он молчит. Открытые позиции защищает исполнитель.' },
}

export function stateWords(loaded: boolean, status: BrainStatus): StateWords {
  return loaded ? STATE[status] : { title: 'Подключение', hint: 'Ждём первых данных от платформы.' }
}

/** Пустая лента мыслей: что именно происходит. */
export function emptyNote(loaded: boolean, status: BrainStatus): string {
  if (!loaded) return ''
  if (status === 'down') return 'Мозг не отвечает, поэтому новых мыслей нет.'
  if (status === 'thinking') return 'Мозг разбирает событие. Мысль появится здесь, как только он решит.'
  if (status === 'protect') return 'Мозг только сопровождает позиции. Новых мыслей нет.'
  return 'Мозг отдыхает: новых мыслей пока нет.'
}

// ---------------------------------------------------------------- роли

// Названия и описания ролей общие с «Система · Настройки»: единственный источник в shared/lib/human-roles.ts.

/** Имя роли для человека; незнакомая роль не показывается кодом. */
export const roleName = (role: string | null | undefined): string => roleTitle(role)
/** Одна фраза «чем занимается». */
export const roleWhat = (role: string): string => roleAbout(role)

/** Когда роль отвечала в последний раз, словами: «ответила 2 мин назад», «сбой 5 мин назад», «ещё не отвечала». */
export function roleAnswer(last: { ok: boolean | null; t: number } | null, nowS: number): string {
  if (!last) return 'ещё не отвечала'
  return `${last.ok === false ? 'сбой' : 'ответила'} ${ageText(last.t, nowS)}`
}

/** «сегодня: 12 запросов, $0.03» без нулевых хвостов. */
export function todayText(calls: number, usdText: string): string {
  return `сегодня: ${calls} ${plural(calls, 'запрос', 'запроса', 'запросов')}, ${usdText}`
}

// ---------------------------------------------------------------- мысли

const DECISION: Record<string, string> = {
  skip: 'пропустил', propose: 'предложил вход', hold: 'держит позицию', hold_plan: 'придержал план', release_plan: 'отпустил план', move_stop: 'подтянул стоп', close: 'закрыл позицию',
  partial_close: 'закрыл часть позиции', cancel: 'отменил вход', amend: 'поправил план', escalate: 'позвал владельца',
}

/** Что сделала мысль, без служебных названий: если решение незнакомо, говорим «принял решение». */
export function thoughtKind(r: Pick<DecisionRow, 'what' | 'decision' | 'ok'>): string {
  if (r.what === 'brain_why') return 'объяснил решение'
  if (r.ok === false && r.decision === 'propose') return 'предложение отклонено'
  if (r.decision) return DECISION[r.decision] ?? 'принял решение'
  return r.ok === false ? 'не выполнено' : 'разобрал ситуацию'
}

const SETUP: Record<string, string> = {
  level_reclaim: 'возврат за уровень', level_reject: 'отбой от уровня', sweep_reject: 'отбой после прокола уровня', breakout_retest: 'пробой с возвратом',
  range_fade: 'игра от границ диапазона', trend_pullback: 'откат по тренду', momentum_chase: 'вход за импульсом',
}

/** Текст мысли без служебных следов: названия сетапов словами, остальные snake_case с пробелами, коды капсом и имена файлов убираются. */
export function thoughtText(raw: string | null | undefined): string {
  const t = (raw ?? '')
    .replace(/\b[\w-]+\.(?:json|jsonl|md|py)\b/gi, 'файл настроек')
    .replace(/\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g, (m) => SETUP[m] ?? m.replace(/_/g, ' '))
    .replace(/\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/g, '')
  return t.replace(/\s{2,}/g, ' ').replace(/\s+([,.;:])/g, '$1').trim()
}

/** Подсказка к мысли: кто и когда её записал, и чем закончилось отклонённое предложение. */
export function thoughtHint(r: Pick<DecisionRow, 'role' | 't' | 'ok' | 'code'>, nowS: number): string {
  const result = r.ok === false ? ` Результат: отклонено${r.code ? ` (${r.code})` : ''}.` : ''
  return `Мысль мозга. Записала роль «${roleName(r.role)}», ${ageText(r.t, nowS)}.${result}`
}
