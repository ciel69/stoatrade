import { dayLabel, modeOn, plainify } from './human-text'

/** Тезис сессии, который пишет платформа («день D-20261101-2 (paper): …»): без номера дня и режима латиницей. Не поддающийся разбору текст не показывается. */
export function thesisText(t: string | null | undefined): string {
  const m = /^день (\S+) \((\w+)\): (.*)$/i.exec(t ?? '')
  if (m) {
    const rest = plainify(m[3])
    return `Торговый день ${dayLabel(m[1])}, ${modeOn(m[2])}${rest ? `: ${rest}` : ''}`
  }
  return plainify(t) ?? ''
}

/** Короткие слова для служебных названий, которые встречаются в разделе «Система»: действия, типы сессий, причины паузы, получатели событий, источники данных. */

const ACTION: Record<string, string> = {
  'day.start': 'Запуск дня', 'day.stop': 'Остановка дня', 'day.resume': 'Продолжение дня', 'kill.set': 'Аварийная остановка', 'kill.clear': 'Снятие аварийной остановки', 'stop.emergency': 'Экстренная остановка', 'mode.set': 'Смена режима',
  'risk.set': 'Смена уровня риска', 'carry.decide': 'Решение о переносе позиций', 'report.ack': 'Подтверждение итога дня', 'brain.use': 'Смена модели мозга', 'brain.rollback': 'Откат модели мозга',
  'brain.reload': 'Перезагрузка мозга', 'brain.role': 'Настройка роли мозга', 'intel.refresh': 'Обновление справочных данных', 'notify.test': 'Проверка уведомлений', 'notify.set': 'Настройка уведомлений',
  'platform.shutdown': 'Выключение платформы', 'llm.provider': 'Настройка провайдера моделей', 'llm.key.set': 'Ввод ключа провайдера', challenge: 'Проверка условий',
}
export const actionName = (a: string | null | undefined): string => (a ? (ACTION[a] ?? 'Действие') : 'Действие')

const SESSION: Record<string, string> = { day: 'торговый день', scalp: 'скальпинг', intraday: 'внутридневная', swing: 'свинг', position: 'позиционная' }
export const sessionName = (t: string | null | undefined): string => (t ? (SESSION[t] ?? 'торговая сессия') : 'торговая сессия')

/** На бумаге лимиты сессии, дня и слотов справочные: исполнитель их не применяет (guard, режим paper), брифинг мозга тоже говорит «вход открыт». Одна формулировка на все страницы. */
export const PAPER_LIMITS_NOTE = 'На бумаге лимиты не останавливают сделки, на реальных деньгах остановили бы.'
/** Сервер пускает ограниченное число вкладок дашборда; сверх лимита соединение отклоняется. Показывается вместо «Нет связи с платформой», когда платформа жива. */
export const TABS_FULL_TITLE = 'Открыто слишком много вкладок дашборда'
export const TABS_FULL_TEXT = 'Закройте лишние, страница подключится сама.'
export const PAPER_LIMITS_CHIP = 'на бумаге не останавливает'

const STATUS: Record<string, string> = { done: 'выполнено', rejected: 'отклонено', failed: 'не выполнено', expired: 'устарело', ok: 'выполнено' }
export const commandStatus = (s: string | null | undefined): string => (s ? (STATUS[s] ?? '') : '')

const HOLD: Record<string, string> = { private_ws: 'нет личного канала биржи', blind: 'нет данных рынка', bad_data: 'данные рынка некорректны', equity_mismatch: 'баланс расходится с биржей' }
/** Причина паузы исполнителя (hold) простыми словами. */
export function holdText(hold: unknown): string {
  const list = (Array.isArray(hold) ? hold : [hold]).map((x) => String(x ?? '')).filter(Boolean)
  const words = list.map((h) => HOLD[h] ?? '').filter(Boolean)
  return words.length ? words.join(', ') : list.length ? 'причина указана в подробностях' : ''
}

const CONSUMER: Record<string, string> = { brain: 'мозг', dash: 'дашборд', dash2: 'дашборд', exec: 'исполнитель', notifyd: 'уведомления', ctl: 'служба управления', scan: 'сканер', scanner: 'сканер' }
export const consumerName = (n: string): string => CONSUMER[n] ?? 'другой получатель'

const PLAN: Record<string, string> = { armed: 'ждут входа', waiting: 'ждут входа', pending: 'ждут входа', active: 'в сделке', in_trade: 'в сделке', open: 'в сделке', closed: 'закрыты', done: 'закрыты', watching: 'под наблюдением' }
export const planState = (k: string): string => PLAN[k] ?? 'прочие'

const CTX: Record<string, string> = { fng: 'индекс страха и жадности', funding: 'ставки финансирования', macro: 'макро-календарь', oi: 'открытый интерес', liq: 'ликвидации' }
export const contextName = (k: string): string => CTX[k] ?? 'другой источник'

const FEED_STATE: Record<string, string> = { ok: 'работает', error: 'ошибка соединения', connecting: 'подключается', down: 'не отвечает', stale: 'отстаёт' }
export const feedState = (s: string | null | undefined): string => (s ? (FEED_STATE[s] ?? 'состояние неизвестно') : 'состояние неизвестно')

/** Имя биржи или источника цен: это имена собственные (bybit, binance), их показываем с заглавной буквы. */
export const proper = (id: string): string => (/^[a-z0-9]{2,16}$/i.test(id) ? id[0].toUpperCase() + id.slice(1) : 'Источник')

/** Причина остановки сессии («aborted: S3» и подобное): простой текст или ничего. */
export const stopNote = (n: string | null | undefined): string => {
  if (!n) return ''
  if (/aborted/i.test(n)) return 'прервана при неудачном запуске дня'
  return plainify(n) ?? ''
}

/*
 * Роли мозга человеческими словами: единственный источник названий и описаний. Его берут и блок «Мозг» (entities/brain), и «Система · Настройки» (entities/role),
 * поэтому два описания одной роли разойтись не могут. Тексты написаны по инструкциям ролей (config/roles): что роль может и чего не может.
 */
export interface RoleWords {
  name: string
  what: string
}

const ROLES: Readonly<Record<string, RoleWords>> = {
  entry_scout: { name: 'Поиск входа', what: 'Смотрит сигнал сканера по одной монете и решает: подготовить вход с уровнем и стопом или пропустить.' },
  morning_analyst: { name: 'Утренний обзор', what: 'Оценивает рынок утром и составляет план дня: на что смотреть и сколько новых сделок разрешить.' },
  position_manager: { name: 'Сопровождение позиции', what: 'Ведёт открытую сделку: подтягивает стоп, фиксирует часть прибыли или закрывает. Увеличить позицию и ослабить стоп не может.' },
  day_auditor: { name: 'Аудит дня', what: 'В конце дня решает по каждой открытой позиции: перенести на следующий день или закрыть.' },
  news_reader: { name: 'Читатель новостей', what: 'Читает заголовки и размечает их: о чём, насколько важно, куда может двинуть цену. Сделок не решает, мозгу отдаёт только пометки.' },
  reviewer: { name: 'Разбор сделок', what: 'Разбирает закрытую сделку: следовала ли правилам, что улучшить. На саму сделку уже не влияет.' },
  day_reviewer: { name: 'Разбор дня для обучения', what: 'После дня предлагает условия-кандидаты в уроки из меню, которое посчитал код. Проверяет их и решает код цикла обучения; на сделки роль не влияет.' },
  retro: { name: 'Недельная ретроспектива', what: 'Раз в неделю по итогам недели предлагает до трёх условий для проверки как урок. Проверяет их и решает код цикла обучения; на сделки роль не влияет.' },
}

const UNKNOWN_NAME = 'Другая роль мозга'
const UNKNOWN_WHAT = 'Служебная роль мозга, подробностей пока нет.'

/** Название роли; незнакомая роль не показывается служебным кодом. */
export const roleTitle = (role: string | null | undefined): string => (role ? ROLES[role]?.name ?? UNKNOWN_NAME : 'Мозг')

/** Одна фраза «чем занимается». */
export const roleAbout = (role: string | null | undefined): string => (role ? ROLES[role]?.what ?? UNKNOWN_WHAT : '')

/** Известные роли (для проверок и перечислений). */
export const ROLE_IDS: readonly string[] = Object.keys(ROLES)
