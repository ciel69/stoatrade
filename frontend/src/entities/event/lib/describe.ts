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

/** Короткое описание события: тип по-русски и главный параметр (код, веха, вид сигнала). Только поля из белого списка шины, всё выводится как текст. */
export function describe(e: BusEvent): EventText {
  const d = e.d ?? {}
  const pick = (k: string) => (typeof d[k] === 'string' || typeof d[k] === 'number' ? String(d[k]) : '')
  const detail = [pick('kind'), pick('name'), pick('code'), pick('which'), pick('reason'), pick('why'), pick('state'), pick('action')].filter(Boolean)[0] ?? ''
  return { title: TYPE_TEXT[e.type] ?? e.type, detail, tone: BAD.has(e.type) ? 'bad' : GOOD.has(e.type) ? 'good' : 'neutral' }
}
