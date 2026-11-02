import type { NotifyCategory, NotifyDraft, NotifyLine, NotifyPatch, NotifySettings } from '../model/types'

/** Человеческие названия категорий (подписи сервера технические). Неизвестная категория показывается подписью сервера. */
const NAMES: Readonly<Record<string, string>> = {
  day_start: 'Торговый день начался',
  day_report: 'Итог дня готов',
  emergency: 'Аварийная остановка',
  service_down: 'Сбой платформы или связи',
  live_gate: 'Запуск на реальные деньги',
  risk_level_changed: 'Изменился уровень риска',
  brain_swap: 'Мозг перешёл на другую модель',
  position_closed: 'Позиция закрыта по стопу или тейку',
  day_close_failed: 'День не удалось закрыть',
  macro_blackout_soon: 'Скоро важное событие на рынке',
}

export const categoryName = (id: string, label?: string): string => NAMES[id] ?? label ?? id

export const MAX_PER_HOUR = 60
export const MAX_SAME_KIND_MIN = 1440

/** Копия настроек как черновик (вложенное не разделяется со снимком темы). */
export function draftOf(s: NotifySettings): NotifyDraft {
  return { master: s.master, categories: { ...s.categories }, quiet: { ...s.quiet, bypass: [...s.quiet.bypass] }, limits: { ...s.limits } }
}

const sorted = (xs: readonly string[]): string[] => [...xs].sort()

/** Совпадают ли настройки (для «изменено ли» и «отразила ли тема»). */
export function sameSettings(a: NotifySettings | NotifyDraft, b: NotifySettings | NotifyDraft): boolean {
  if (a.master !== b.master) return false
  const ids = new Set([...Object.keys(a.categories), ...Object.keys(b.categories)])
  for (const id of ids) if ((a.categories[id] ?? false) !== (b.categories[id] ?? false)) return false
  const qa = a.quiet
  const qb = b.quiet
  if (qa.enabled !== qb.enabled || qa.from !== qb.from || qa.to !== qb.to || qa.tz !== qb.tz) return false
  if (sorted(qa.bypass).join() !== sorted(qb.bypass).join()) return false
  return a.limits.per_hour === b.limits.per_hour && a.limits.same_kind_min === b.limits.same_kind_min
}

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/
export const isTime = (v: string): boolean => TIME.test(v)
const isInt = (v: unknown, lo: number, hi: number): v is number => typeof v === 'number' && Number.isInteger(v) && v >= lo && v <= hi

export interface NotifyErrors {
  from?: string
  to?: string
  per_hour?: string
  same_kind_min?: string
}

/** Ошибки ввода по полям черновика, человеческими словами. Пустой объект: можно применять. */
export function draftErrors(d: NotifyDraft): NotifyErrors {
  const e: NotifyErrors = {}
  if (!isTime(d.quiet.from)) e.from = 'Укажите время, например 23:00.'
  if (!isTime(d.quiet.to)) e.to = 'Укажите время, например 07:00.'
  if (!isInt(d.limits.per_hour, 1, MAX_PER_HOUR)) e.per_hour = `Целое число от 1 до ${MAX_PER_HOUR}.`
  if (!isInt(d.limits.same_kind_min, 0, MAX_SAME_KIND_MIN)) e.same_kind_min = `Целое число от 0 до ${MAX_SAME_KIND_MIN}.`
  return e
}

/** Аргументы notify.set: только то, что отличается от текущего. null: менять нечего или в черновике ошибка ввода. */
export function patchOf(base: NotifySettings, d: NotifyDraft): NotifyPatch | null {
  if (Object.keys(draftErrors(d)).length) return null
  const out: NotifyPatch = {}
  if (d.master !== base.master) out.master = d.master
  const cats: Record<string, boolean> = {}
  for (const [id, on] of Object.entries(d.categories)) if ((base.categories[id] ?? false) !== on) cats[id] = on
  if (Object.keys(cats).length) out.categories = cats
  const q: Partial<NotifySettings['quiet']> = {}
  if (d.quiet.enabled !== base.quiet.enabled) q.enabled = d.quiet.enabled
  if (d.quiet.from !== base.quiet.from) q.from = d.quiet.from
  if (d.quiet.to !== base.quiet.to) q.to = d.quiet.to
  if (d.quiet.tz !== base.quiet.tz) q.tz = d.quiet.tz
  if (sorted(d.quiet.bypass).join() !== sorted(base.quiet.bypass).join()) q.bypass = sorted(d.quiet.bypass)
  if (Object.keys(q).length) out.quiet = q
  const l: NonNullable<NotifyPatch['limits']> = {}
  if (d.limits.per_hour !== base.limits.per_hour) l.per_hour = d.limits.per_hour as number
  if (d.limits.same_kind_min !== base.limits.same_kind_min) l.same_kind_min = d.limits.same_kind_min as number
  if (Object.keys(l).length) out.limits = l
  return Object.keys(out).length ? out : null
}

const onOff = (v: boolean): string => (v ? 'включено' : 'выключено')
const tzWord = (v: string): string => (v === 'utc' ? 'по UTC' : 'по местному времени')
const minWord = (n: number | null): string => (n === 0 ? 'без паузы' : `не чаще раза в ${n} мин`)

/** «Было → станет» по изменённому, человеческими словами. */
export function changeLines(base: NotifySettings, d: NotifyDraft, cats: readonly NotifyCategory[] = []): NotifyLine[] {
  const out: NotifyLine[] = []
  const label = (id: string): string => categoryName(id, cats.find((c) => c.id === id)?.label)
  if (d.master !== base.master) out.push({ label: 'Показывать уведомления', from: onOff(base.master), to: onOff(d.master) })
  for (const [id, on] of Object.entries(d.categories)) if ((base.categories[id] ?? false) !== on) out.push({ label: label(id), from: onOff(base.categories[id] ?? false), to: onOff(on) })
  const q = base.quiet
  if (d.quiet.enabled !== q.enabled) out.push({ label: 'Тихие часы', from: onOff(q.enabled), to: onOff(d.quiet.enabled) })
  if (d.quiet.from !== q.from || d.quiet.to !== q.to) out.push({ label: 'Тихие часы, время', from: `с ${q.from} до ${q.to}`, to: `с ${d.quiet.from} до ${d.quiet.to}` })
  if (d.quiet.tz !== q.tz) out.push({ label: 'Часовой пояс тихих часов', from: tzWord(q.tz), to: tzWord(d.quiet.tz) })
  const added = d.quiet.bypass.filter((x) => !q.bypass.includes(x))
  const removed = q.bypass.filter((x) => !d.quiet.bypass.includes(x))
  for (const id of added) out.push({ label: label(id), from: 'в тихие часы молчит', to: 'и в тихие часы показывается' })
  for (const id of removed) out.push({ label: label(id), from: 'и в тихие часы показывается', to: 'в тихие часы молчит' })
  if (d.limits.per_hour !== base.limits.per_hour) out.push({ label: 'Не больше в час на тему', from: String(base.limits.per_hour ?? '–'), to: String(d.limits.per_hour ?? '–') })
  if (d.limits.same_kind_min !== base.limits.same_kind_min) out.push({ label: 'Один и тот же вид', from: minWord(base.limits.same_kind_min), to: minWord(d.limits.same_kind_min) })
  return out
}

/** Что сказать одной строкой под «Применить»: последствия простыми словами. */
export function effectText(base: NotifySettings, d: NotifyDraft): string {
  if (d.master && !base.master) return 'Уведомления начнут появляться на экране компьютера. Тексты только из шаблонов, без ключей и личных данных.'
  if (!d.master && base.master) return 'Уведомления перестанут появляться. Сохранённые настройки остаются.'
  return 'Изменение вступает в силу сразу. Его всегда можно вернуть.'
}

/** Пересекает ли интервал тихих часов полночь (23:00 – 07:00). */
export const crossesMidnight = (from: string, to: string): boolean => isTime(from) && isTime(to) && from > to

/** Подсказка под временем: что значит выбранный промежуток. */
export function quietHint(from: string, to: string): string {
  if (!isTime(from) || !isTime(to)) return 'Укажите время начала и конца.'
  if (from === to) return 'Начало и конец совпадают: тишины не будет.'
  if (crossesMidnight(from, to)) return `Тишина с ${from} вечером до ${to} утра следующего дня.`
  return `Тишина каждый день с ${from} до ${to}.`
}

/** Заметка о тихих часах прямо сейчас (или пустая строка). */
export function quietNow(on: boolean, held: number): string {
  if (!on) return ''
  return held > 0 ? `Сейчас тихие часы: накоплено ${held}, они придут одним сообщением. Важное показывается сразу.` : 'Сейчас тихие часы: новое копится и придёт одним сообщением. Важное показывается сразу.'
}

/** Своих настроек ещё нет, действует файл конфигурации. */
export function emptyNote(master: boolean): string {
  return `Своих настроек ещё нет: пока действует стандартное значение платформы (сейчас ${master ? 'включено' : 'выключено'}). После первого применения приоритет получат ваши настройки.`
}

/** Статистика словами, по одной фразе на показатель. */
export function statsText(s: { sent: number; merged: number; limited: number; failed: number }): string[] {
  return [`показано ${s.sent}`, `объединено повторов ${s.merged}`, `отсечено лимитом ${s.limited}`, `сбоев показа ${s.failed}`]
}
