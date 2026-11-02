/**
 * Слова страницы «Новости»: состояние читателя, источников и календаря человеческим языком. Служебные значения сервера (ok, idle, off, limit, paused, llm_down) наружу не выходят.
 * Смысл взят из src/tb/intel/digest.py (статусы читателя) и src/tb/intel/calendar.py (окно блокировки). Названия событий и источников приходят из конфигурации: это данные, показываются как есть.
 */

export type NewsTone = 'ok' | 'warn' | 'idle'

export interface ReaderWord {
  /** короткое состояние: «размечает», «выключен» */
  text: string
  /** одна-две фразы: что это значит для заголовков и для торговли */
  about: string
  tone: NewsTone
}

const READER: Record<string, ReaderWord> = {
  ok: { text: 'размечает заголовки', about: 'Читатель оценивает новые заголовки: тип события, важность, направление и монеты.', tone: 'ok' },
  idle: { text: 'новых заголовков нет', about: 'Читатель работает и ждёт новых заголовков.', tone: 'ok' },
  limit: { text: 'достиг лимита расходов', about: 'Чтобы не потратить больше заданного, разметка приостановлена до следующего окна лимита. Заголовки приходят, но без оценки.', tone: 'idle' },
  paused: { text: 'пауза после ошибок', about: 'Модель отвечала в неверном формате, читатель отдыхает и вернётся сам. Заголовки пока без оценки.', tone: 'warn' },
  llm_down: { text: 'модель не отвечает', about: 'Заголовки приходят, но не размечаются, пока модель недоступна. Обычно проходит само.', tone: 'warn' },
}

/** Состояние читателя словами. Выключенный читатель это справка, а не поломка: торговля идёт и без него. */
export function readerWord(enabled: boolean, status: string | null | undefined): ReaderWord {
  if (!enabled) return { text: 'выключен', about: 'Заголовки показываются без оценки важности. Торговле это не мешает. Включить читателя может владелец в настройках новостей платформы.', tone: 'idle' }
  if (status && READER[status]) return READER[status]
  return { text: 'ждёт первого цикла', about: 'Читатель включён, но ещё не успел обработать заголовки.', tone: 'idle' }
}

/** Подсказка «как включить» для раздела «Для специалиста». */
export const READER_ENABLE_HINT = 'Читатель включается ключом reader.enabled в config/intel.json; сервис intel подхватывает его при следующем цикле.'

/** Сила влияния события словами (значения сервера 0..5). */
export function impactWord(n: number | null | undefined): string {
  if (typeof n !== 'number' || !Number.isFinite(n)) return 'влияние не указано'
  return n >= 3 ? 'сильное влияние' : n >= 2 ? 'заметное влияние' : 'слабое влияние'
}

/** Сколько осталось до момента: «через 2 ч 5 мин», «через 3 дн», «уже идёт». sec может быть отрицательным. */
export function untilWord(sec: number): string {
  if (sec <= 0) return 'уже идёт'
  if (sec < 60) return 'меньше чем через минуту'
  if (sec >= 2 * 86400) return `через ${Math.round(sec / 86400)} дн`
  const total = Math.round(sec / 60)
  const h = Math.floor(total / 60)
  const m = total % 60
  return h ? `через ${h} ч${m ? ` ${m} мин` : ''}` : `через ${m} мин`
}

/** Время суток по местным часам: «15:30». */
export function clockWord(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
}
