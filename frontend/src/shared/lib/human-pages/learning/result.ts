/** Слова блока «Результат» (requirements 5, 15.4-15.6, 15.11). */

export const LEARN_RESULT_WINDOW_LABEL = 'Окно'
export const LEARN_RESULT_WINDOW = (w: string): string => `Последние ${w}`
export const LEARN_RESULT_SERIES = (w: string): string => `Средний R, окно ${w} сделок`
export const LEARN_RESULT_X = 'Сделка №'
export const LEARN_RESULT_CHART_EMPTY = 'Ряд начнётся с 30-й закрытой сделки'

export const LEARN_RESULT_STAT = {
  r: 'Средний R',
  win: 'Доля прибыльных',
  pf: 'Профит-фактор',
  fees: 'Доля издержек',
} as const
export const LEARN_RESULT_STAT_HINT = {
  r: 'прибыль на сделку в долях риска',
  win: 'сделки с плюсом',
  pf: 'прибыль к убытку, больше 1 это плюс',
  fees: 'комиссии в валовой прибыли',
} as const
export const LEARN_RESULT_PF_INF = 'без убыточных сделок'

/** Где интервал среднего R относительно нуля. */
export const LEARN_RESULT_SIGNAL = {
  above: 'Интервал целиком выше нуля',
  below: 'Интервал целиком ниже нуля',
  zero: 'Нуль внутри интервала: от случайности не отличить',
  none: 'Интервала пока нет',
} as const

export const LEARN_RESULT_ALL = 'За весь период'
export const LEARN_RESULT_TABLE_OPEN = 'Показать таблицу'
export const LEARN_RESULT_TABLE_CLOSE = 'Скрыть таблицу'
export const LEARN_RESULT_TABLE_HEAD = { x: 'Сделка №', r: 'Средний R', interval: 'Интервал', n: 'n' } as const
