import type { BusEvent } from '../model/types'

const TYPE_TEXT: Record<string, string> = {
  scanner_signal: 'сигнал сканера',
  plan_armed: 'план взведён',
  plan_rejected: 'план отклонён',
  trigger_fired: 'сработал триггер',
  entry_blocked: 'вход заблокирован',
  entry_filled: 'вход исполнен',
  milestone: 'веха',
  rule_fired: 'сработало правило',
  stop_moved: 'стоп сдвинут',
  position_closed: 'позиция закрыта',
  plan_ended: 'план завершён',
  action_rejected: 'действие отклонено',
  brain_wake_request: 'мозг разбужен',
  limit_hit: 'лимит',
  kill: 'аварийная остановка',
  connectivity: 'связь',
  reconcile_alert: 'сверка с биржей',
  session_ending: 'сессия заканчивается',
  error: 'ошибка',
  exec_started: 'исполнитель запущен',
  exec_stopped: 'исполнитель остановлен',
  news_flash: 'быстрая новость',
  macro_blackout_soon: 'скоро макро-событие',
  day_report: 'отчёт дня',
}

const BAD = new Set(['error', 'kill', 'limit_hit', 'plan_rejected', 'action_rejected', 'reconcile_alert', 'exec_stopped', 'entry_blocked'])
const GOOD = new Set(['entry_filled', 'position_closed', 'stop_moved'])

export interface EventText {
  title: string
  detail: string
  tone: 'bad' | 'good' | 'neutral'
}

export interface EventGroup {
  /** самое новое событие группы */
  e: BusEvent
  /** самое раннее событие группы (совпадает с e, если повторов не было) */
  oldest: BusEvent
  text: EventText
  count: number
}

const FIELD_LABEL: Record<string, string> = {
  kind: 'вид', name: 'название', code: 'код', which: 'что', reason: 'причина', why: 'почему', state: 'состояние', action: 'действие', rule: 'правило', ok: 'выполнено',
  dir: 'направление', score: 'скор', r: 'результат, R', tf: 'таймфрейм', flatten: 'закрыть позиции', side: 'сторона', price: 'цена', level: 'уровень', pct: 'процент', n: 'число', sym: 'монета',
  px: 'цена', range_5m_pct: 'диапазон 5 мин, %', ret_5m_pct: 'ход за 5 мин, %', spread_bps: 'спред, б.п.', funding_pct: 'funding, %', oi_chg_15m_pct: 'открытый интерес за 15 мин, %',
  oi_chg_1h_pct: 'открытый интерес за 1 ч, %', limit: 'лимит', used: 'использовано', room: 'запас', mode: 'режим', src: 'источник', title: 'заголовок', source: 'источник', age_s: 'возраст, с',
}
const VALUE_WORD: Record<string, string> = { up: 'вверх', down: 'вниз', true: 'да', false: 'нет', on: 'включено', off: 'выключено', close: 'закрыть', hold: 'держать' }

/** Все поля события для раскрытой строки: подпись по-русски (нет перевода: имя поля как есть) и значение текстом. Поля уже из белого списка сервера; всё выводится как текст. */
export function eventFields(e: BusEvent): Array<[string, string]> {
  return Object.entries(e.d ?? {}).map(([k, v]) => {
    const s = typeof v === 'string' ? v : typeof v === 'number' || typeof v === 'boolean' ? String(v) : JSON.stringify(v)
    return [FIELD_LABEL[k] ?? k, VALUE_WORD[s] ?? s]
  })
}

export const PRIORITY_TEXT: Record<string, string> = { wake: 'будит мозг', info: 'информационное' }

/** Подряд идущие одинаковые события (тот же тип, монета, план и текст) сворачиваются в одну строку с числом повторов: шина может слать их десятками. Вход: новые сверху. */
export function collapse(items: BusEvent[]): EventGroup[] {
  const out: EventGroup[] = []
  let key = ''
  for (const e of items) {
    const text = describe(e)
    const k = [e.type, e.sym, e.plan, text.title, text.detail].join('|')
    if (out.length && k === key) {
      out[out.length - 1].count++
      out[out.length - 1].oldest = e
    } else out.push({ e, oldest: e, text, count: 1 })
    key = k
  }
  return out
}

/** Короткое описание события: тип по-русски и главный параметр (код, веха, вид сигнала). Только поля из белого списка шины, всё выводится как текст. */
export function describe(e: BusEvent): EventText {
  const d = e.d ?? {}
  const pick = (k: string) => (typeof d[k] === 'string' || typeof d[k] === 'number' ? String(d[k]) : '')
  if (e.type === 'rule_fired') {
    const act = [pick('rule'), pick('action')].filter(Boolean).join(' → ')
    return { title: TYPE_TEXT.rule_fired, detail: d.ok === false ? `${act} · отказ ${pick('code')}` : act, tone: d.ok === false ? 'bad' : 'neutral' }
  }
  const detail = [pick('kind'), pick('name'), pick('code'), pick('which'), pick('reason'), pick('why'), pick('state'), pick('action')].filter(Boolean)[0] ?? ''
  return { title: TYPE_TEXT[e.type] ?? e.type, detail, tone: BAD.has(e.type) ? 'bad' : GOOD.has(e.type) ? 'good' : 'neutral' }
}
