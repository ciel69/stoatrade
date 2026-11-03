import { failureText, plainify } from '@/shared/lib'
import type { Challenge, MemoryActionResult, MemoryHello } from '../model/types'

/**
 * Протокол аварийных действий вкладки «Память» (src/tb/dash2/memory_ops.py) поверх общего диалога «Принимаю риски».
 * Сервер шлёт предупреждения своими словами (с командами терминала); владельцу показываем их простым русским по id, исходный текст уходит в запас.
 */

export const MEMORY_ACTIONS = ['lesson_reject', 'rule_disable', 'rules_disable_all'] as const
export type MemoryAction = (typeof MEMORY_ACTIONS)[number]
export const isMemoryAction = (a: string | undefined): a is MemoryAction => !!a && (MEMORY_ACTIONS as readonly string[]).includes(a)

export const MEMORY_UNAVAILABLE_TEXT = 'Аварийные действия «Памяти» сейчас недоступны: соединение с платформой не готово. Обновите страницу.'

/** Последствия по id предупреждения сервера, простыми словами. */
export const MEMORY_WARN_TEXT: Readonly<Record<string, string>> = {
  rule_off: 'Правило перестанет действовать сразу: появится новая версия набора правил без него. Если правило ещё на испытании, испытание закроется.',
  block_forever: 'Платформа больше никогда не предложит это правило сама. Вернуть его сможет только разработчик командой в терминале.',
  switch_off: 'Применение всех правил выключится сразу. Включить обратно можно только командой в терминале платформы.',
  all_off: 'Все действующие правила снимутся, испытания закроются, появится новая версия набора правил без единого правила.',
  /** то же предупреждение у «Отключить все»: правил несколько */
  'rules_disable_all:block_forever': 'Платформа больше никогда не предложит эти правила сама. Вернуть их сможет только разработчик командой в терминале.',
}

/** Коды «Памяти», которых нет в общем словаре ответов. */
const MEMORY_CODES: Readonly<Record<string, string>> = {
  E_CONFIRM: 'Платформа ждёт подтверждения рисков. Нажмите кнопку ещё раз и примите риски в окне.',
  E_DB: 'База знаний сейчас недоступна, действие не выполнено. Подождите и повторите.',
  E_RETRY: 'Правила уже не применяются: выключатель записан. Но новая версия набора правил не записана, потому что база знаний недоступна. Повторите, когда база восстановится.',
  E_AUTH: 'Соединение с платформой не готово. Обновите страницу и повторите.',
}

/** Можно ли запустить аварийное действие: есть CSRF соединения и действие в списке hello.memory.actions. */
export function memoryUnavailable(h: MemoryHello, action: string): string | null {
  if (!h.csrf || !(h.actions ?? []).includes(action)) return MEMORY_UNAVAILABLE_TEXT
  return null
}

/** Нужен ли диалог: в hello.memory.confirm для действия есть предупреждения. Неизвестно (старый сервер): спрашиваем сервер challenge-ом. */
export function memoryNeedsChallenge(h: MemoryHello, action: string): boolean {
  const ids = h.confirm?.[action]
  return !Array.isArray(ids) || ids.length > 0
}

/** Ответ challenge «Памяти» -> данные общего окна: предупреждения простыми словами, опасное действие, без пауз и фраз. */
export function memoryChallenge(r: Pick<MemoryActionResult, 'warnings'> & { op?: string }): Challenge {
  const warnings = (r.warnings ?? []).filter((w) => w && typeof w.id === 'string').map((w) => ({
    id: w.id,
    code: w.id,
    msg: MEMORY_WARN_TEXT[`${r.op}:${w.id}`] ?? MEMORY_WARN_TEXT[w.id] ?? plainify(w.text) ?? 'У действия есть последствия, которые платформа просит явно принять.',
  }))
  return { danger: true, blocked: false, gates: [], warnings, pre_amber: [], phrases: [], min_delay: 0 }
}

/** Отказ словами: свои коды «Памяти», затем общий словарь ответов. */
export function memoryFailure(code: string | null | undefined, msg?: string | null): string {
  if (code && MEMORY_CODES[code]) return MEMORY_CODES[code]
  return failureText(code, msg)
}

/** Итог успешного действия словами (для окна и уведомления). */
export function memoryDoneText(op: string): string {
  if (op === 'lesson_reject') return 'Урок отклонён. Действующие правила не тронуты.'
  if (op === 'rule_disable') return 'Правило отключено. Платформа не вернёт его сама.'
  if (op === 'rules_disable_all') return 'Все правила отключены. Мозг торгует без правил.'
  return 'Готово.'
}
