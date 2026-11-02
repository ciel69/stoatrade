import { stepLabel } from '../human-codes'
import { modeName } from '../human-text'

/**
 * Слова страницы «Торговля · День»: шаги запуска (S1–S9) и закрытия (C1–C8), причины отказа и незавершённого закрытия, правила чек-листа, состояния дня.
 * Смысл шагов взят из src/tb/lifecycle (starter.py, audit.py, report.py); ничего сверх кода не выдумано. Неизвестный код не показывается как есть: подставляется общая фраза.
 */

interface Step { label: string; does: string; amber: string }

/** Шаги закрытия дня (audit.py, STEPS C1–C8). amber: что значит «с замечанием» на шаге. */
const CLOSE: Record<string, Step> = {
  C1: { label: 'Отмена ждущих входов', does: 'Снимает с биржи заявки, которые ещё не сработали, кроме тех, что можно оставить на ночь.', amber: '' },
  C2: { label: 'Снимок позиций и планов', does: 'Фиксирует, какие позиции и планы есть на конец дня, и ждёт, пока сверка с биржей совпадёт.', amber: '' },
  C3: { label: 'Чек-лист закрытия', does: 'По правилам решает для каждой позиции: закрыть её или можно перенести через сутки.', amber: '' },
  C4: { label: 'Совет мозга по переносу', does: 'Мозг-аудитор оценивает спорные позиции и при сомнении задаёт вопрос владельцу.', amber: 'мозг не ответил, решения по спорным позициям приняты по правилам' },
  C5: { label: 'Ответ владельца', does: 'Ждёт вашего решения по вопросам о переносе. Без ответа в срок позиция закрывается.', amber: '' },
  C6: { label: 'Закрытие позиций', does: 'Отправляет исполнителю команды закрыть позиции и ждёт подтверждения по снимку биржи.', amber: '' },
  C7: { label: 'Остановка торговой сессии', does: 'Закрывает сессию дня и записывает, какие позиции перенесены.', amber: '' },
  C8: { label: 'Итоговый отчёт', does: 'Собирает итог дня: результат, комиссии, расход на модели, перенесённое и вопросы.', amber: '' },
}

/** Что делает шаг запуска (starter.py, S1–S9). Название шага берётся из stepLabel. */
const START_DOES: Record<string, string> = {
  S1: 'Проверяет, можно ли начинать: исполнитель жив, аварийной остановки нет, лимиты и данные на месте.',
  S2: 'Собирает свежие новости и календарь событий для мозга.',
  S3: 'Сверяет портфель с биржей и находит позиции, перенесённые со вчера.',
  S4: 'Готовит сводку (досье) по счёту, позициям и рынку.',
  S5: 'Мозг пишет утренний разбор рынка.',
  S6: 'Мозг составляет план дня: режим рынка, уклон и сколько новых планов можно. Если не вышло, берётся осторожный запасной.',
  S7: 'Открывает торговую сессию дня с лимитом убытка.',
  S8: 'Включает торговлю.',
  S9: 'Записывает отчёт о запуске.',
}

export const DAY_STEP_NAMES = [...Object.keys(START_DOES), ...Object.keys(CLOSE)]
export const isCloseStep = (name: string | null | undefined): boolean => !!name && name in CLOSE
export const dayStepLabel = (name: string | null | undefined): string => (name && CLOSE[name] ? CLOSE[name].label : name && START_DOES[name] ? stepLabel(name) : 'Шаг дня')
export const dayStepDoes = (name: string | null | undefined): string => (name ? (CLOSE[name]?.does ?? START_DOES[name] ?? '') : '')
export const dayStepAmber = (name: string | null | undefined): string => (name && CLOSE[name]?.amber) || 'шаг выполнен с замечанием'

/** Почему шаг не прошёл или старт отклонён: по коду ответа платформы. */
const FAIL: Record<string, string> = {
  E_GATE: 'не выполнено обязательное условие безопасности',
  E_AMBER: 'предупреждения запуска не были приняты',
  E_LIMITS: 'сработал лимит или уже идёт другая торговая сессия',
  E_INTERNAL: 'внутренняя ошибка шага',
  day_close_failed: 'исполнитель не подтвердил закрытие позиций',
}
export const failReason = (code: string | null | undefined): string => (code ? (FAIL[code] ?? 'шаг не завершился') : 'шаг не завершился')

/** Почему закрытие дня не подтверждено (closing.failed.reason). */
const CLOSE_FAIL: Record<string, string> = {
  exec_down: 'исполнитель не отвечает',
  not_confirmed: 'исполнитель не подтвердил, что позиции закрыты',
}
export const closeFailReason = (code: string | null | undefined): string => (code ? (CLOSE_FAIL[code] ?? 'закрытие не подтверждено') : 'закрытие не подтверждено')

/** Почему день закрылся сам (closing.auto_reason). Текст сервера свободный: показываем его, только если он простой русский. */
export const autoCloseText = (reason: string | null | undefined): string => {
  const r = (reason ?? '').trim()
  return r && !/[A-Za-z_]/.test(r) ? r : 'по расписанию или условию платформы'
}

/** Кто остановил день (stop_source). */
const STOP: Record<string, string> = { owner: 'по вашей команде', auto: 'платформа закрыла день сама', abort: 'старт был отклонён', emergency: 'аварийная остановка', kill: 'аварийная остановка' }
export const stopSourceText = (s: string | null | undefined): string => (s ? (STOP[s] ?? '') : '')

const STATE: Record<string, string> = { stopped: 'остановлен', starting: 'запускается', running: 'идёт', closing: 'закрывается', halted: 'остановлен аварийно' }
export const dayStateWord = (s: string | null | undefined): string => (s ? (STATE[s] ?? 'состояние неизвестно') : 'состояние неизвестно')

const VERDICT: Record<string, string> = { ok: 'без замечаний', attention: 'есть замечания', alarm: 'нужен разбор' }
export const verdictWord = (v: string | null | undefined): string => (v ? (VERDICT[v] ?? 'оценка неизвестна') : 'оценки нет')

const REGIME: Record<string, string> = { trend: 'тренд', range: 'боковик', volatile: 'резкие движения', unclear: 'картина неясна' }
export const regimeWord = (v: string | null | undefined): string => (v ? (REGIME[v] ?? '–') : '–')
const BIAS: Record<string, string> = { long: 'уклон в покупки', short: 'уклон в продажи', neutral: 'без уклона' }
export const biasWord = (v: string | null | undefined): string => (v ? (BIAS[v] ?? '–') : '–')
export const planSourceWord = (v: string | null | undefined): string => (v === 'agent' ? 'составил мозг' : v === 'fallback' ? 'запасной осторожный план' : '')

/** Режим дня одним словом: «бумага», «реальные деньги». */
export const dayModeWord = (m: string | null | undefined): string => modeName(m)

/** Правила чек-листа закрытия (checklist.py): почему позицию закрыли, перенесли или отменили. */
const RULE: Record<string, string> = {
  A0: 'позиция и план не сходятся', A1: 'ждущий вход, который ещё не сработал', A2: 'короткие сделки на ночь не переносятся', A3: 'правила для внутридневных сделок', A4: 'правила для долгих сделок',
  A6: 'позиция держится дольше допустимого', A7: 'не прошла проверку на перенос', A8: 'торговая сессия закрывается', A9: 'идеи остаются',
}
export const ruleWord = (r: string | null | undefined): string => (r ? (RULE[r] ?? 'правило закрытия') : 'правило закрытия')

/** Коды результата проверки позиции на перенос. K1–K8: что именно не сошлось. */
const CODE: Record<string, string> = {
  K1_STOP: 'нет надёжного стопа на бирже', K2_RISK: 'риск до стопа вырос', K3_TOTAL: 'суммарный риск переносов слишком велик', K4_EVENT: 'скоро важное событие',
  K5_FUNDING: 'плата за удержание (фандинг) слишком велика', K6_LIQ: 'цена ликвидации слишком близко', K7_RECONCILE: 'расхождение с биржей', K8_LINK: 'связь или данные не в порядке',
  A0_ORPHAN: 'у позиции нет плана', A0_DRIFT: 'расхождение плана и позиции', A0_NO_POSITION: 'позиции по плану нет', A2_SCALP: 'короткие сделки на ночь не оставляют',
  A6_MAX_HOLD: 'вышел предельный срок удержания', POLICY_NEVER: 'правила уровня риска запрещают перенос', NO_EXIT_RULES: 'нет правил выхода', A1_KEEP_LONG_TTL: 'вход с длинным сроком оставлен',
  OWNER_CLOSE: 'вы выбрали закрыть', OWNER_NO_ANSWER: 'ответа не было, позиция закрыта', LATE_POSITION: 'позиция появилась уже при закрытии',
}
export const checkCodeWord = (code: string): string => {
  if (code.startsWith('K_FAILED:')) return `перед переносом не сошлось: ${CODE[code.slice(9)] ?? 'проверка'}`
  return CODE[code] ?? 'причина не расшифрована'
}

const DECISION: Record<string, string> = { carry: 'перенести', close: 'закрыть', cancel: 'отменить', keep: 'оставить', stop: 'остановить', ask_owner: 'ждёт вашего решения' }
export const decisionWord = (d: string | null | undefined): string => (d ? (DECISION[d] ?? '–') : '–')
/** Что владелец ответил на вопрос о переносе. */
export const answerWord = (a: string | null | undefined): string => (a === 'carry' ? 'перенести' : a === 'close' ? 'закрыть' : '')
/** Кто решил. */
export const decidedByWord = (b: string | null | undefined): string => (b === 'owner' ? 'вы' : b === 'agent' ? 'мозг' : b === 'timeout' ? 'срок вышел' : b === 'emergency' ? 'аварийная остановка' : b === 'default' ? 'по правилам' : '')
