import type { ProviderRow } from '../model/types'

/**
 * Чистая логика правки провайдера LLM в панели: потолки расхода (проверка ввода, «было → станет», аргументы действия), условие включения, ожидание отражения в теме.
 * Сервер строго проверяет аргументы: patch.limits сливается с текущим объектом (глубокое слияние), ключи пропущенных полей остаются прежними,
 * поэтому отправляем все пять полей целиком. Ноль у сервера означает «без потолка» (бюджет читает только истинные значения).
 */

export type LimitKey = 'day_usd' | 'hour_usd' | 'day_toks' | 'rpm' | 'concurrent'
export type Limits = ProviderRow['limits']

export interface LimitField {
  key: LimitKey
  label: string
  /** подпись единицы рядом с полем */
  unit: string
  kind: 'usd' | 'int'
  /** имя поля в конфигурации сервера (в теме токены называются day_toks, а хранятся как day_tokens) */
  wire: 'day_usd' | 'hour_usd' | 'day_tokens' | 'rpm' | 'concurrent'
}

export const LIMIT_FIELDS: readonly LimitField[] = [
  { key: 'day_usd', label: 'Деньги за день', unit: 'USD', kind: 'usd', wire: 'day_usd' },
  { key: 'hour_usd', label: 'Деньги за час', unit: 'USD', kind: 'usd', wire: 'hour_usd' },
  { key: 'day_toks', label: 'Токены за день', unit: 'токенов', kind: 'int', wire: 'day_tokens' },
  { key: 'rpm', label: 'Запросов в минуту', unit: '', kind: 'int', wire: 'rpm' },
  { key: 'concurrent', label: 'Одновременных запросов', unit: '', kind: 'int', wire: 'concurrent' },
]

const MAX_VALUE = 1_000_000_000_000
const NO_CAP = 'без потолка'

/** Текущее значение как число: нет данных и 0 это «без потолка» (0). */
export const limitValue = (v: number | null | undefined): number => (typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : 0)

/** Значение в поле ввода: «без потолка» это пустое поле. */
export const limitInput = (v: number | null | undefined): string => (limitValue(v) ? String(limitValue(v)) : '')

export type ParsedLimit = { ok: true; value: number } | { ok: false; error: string }

/** Разбор введённого текста. Пусто = без потолка (0). Запятая как десятичный разделитель допустима, «3.» и «.5» читаются как 3 и 0.5 (пока человек печатает). Деньги не больше двух знаков, остальное целое. */
export function parseLimit(text: string, kind: LimitField['kind']): ParsedLimit {
  let t = text.trim().replace(',', '.')
  if (!t) return { ok: true, value: 0 }
  if (/^-?\d+\.$/.test(t)) t = t.slice(0, -1)
  else if (/^-?\.\d+$/.test(t)) t = t.replace('.', '0.')
  if (!/^-?\d+(\.\d+)?$/.test(t)) return { ok: false, error: 'Введите число, например 3 или 0.5.' }
  const n = Number(t)
  if (n < 0) return { ok: false, error: 'Число не может быть меньше 0.' }
  if (n > MAX_VALUE) return { ok: false, error: 'Слишком большое число.' }
  if (kind === 'int' && !Number.isInteger(n)) return { ok: false, error: 'Нужно целое число.' }
  if (kind === 'usd' && !/^\d+(\.\d{1,2})?$/.test(t)) return { ok: false, error: 'Не больше двух знаков после запятой.' }
  return { ok: true, value: n }
}

/** Черновик: только изменённые поля, как их ввёл владелец (текст). */
export type LimitsDraft = Partial<Record<LimitKey, string>>

const fieldOf = (key: LimitKey): LimitField => LIMIT_FIELDS.find((f) => f.key === key)!

/** Ошибки ввода по полям черновика. */
export function limitErrors(draft: LimitsDraft): Partial<Record<LimitKey, string>> {
  const out: Partial<Record<LimitKey, string>> = {}
  for (const f of LIMIT_FIELDS) {
    const t = draft[f.key]
    if (t === undefined) continue
    const r = parseLimit(t, f.kind)
    if (!r.ok) out[f.key] = r.error
  }
  return out
}

/** Итоговые пять значений: текущие плюс правки черновика; null, если в черновике есть ошибка ввода. */
export function resolveLimits(current: Limits | null | undefined, draft: LimitsDraft): Record<LimitKey, number> | null {
  const out = {} as Record<LimitKey, number>
  for (const f of LIMIT_FIELDS) {
    const t = draft[f.key]
    if (t === undefined) { out[f.key] = limitValue(current?.[f.key]); continue }
    const r = parseLimit(t, f.kind)
    if (!r.ok) return null
    out[f.key] = r.value
  }
  return out
}

/** Значение словами: «без потолка», «$3», «60 000 токенов», «30 запросов». */
export function limitText(key: LimitKey, v: number): string {
  if (!v) return NO_CAP
  const f = fieldOf(key)
  if (f.kind === 'usd') return `$${Number.isInteger(v) ? v : v.toFixed(2).replace(/0$/, '')}`
  return `${v.toLocaleString('ru-RU')} ${key === 'day_toks' ? 'токенов' : 'запросов'}`
}

export interface LimitChange {
  key: LimitKey
  label: string
  from: string
  to: string
}

/** «Было → станет» по изменённым полям (без ошибок ввода: поля с ошибкой не показываются). Поле считается изменённым, только если значение действительно другое. */
export function limitChanges(current: Limits | null | undefined, draft: LimitsDraft): LimitChange[] {
  const out: LimitChange[] = []
  for (const f of LIMIT_FIELDS) {
    const t = draft[f.key]
    if (t === undefined) continue
    const r = parseLimit(t, f.kind)
    if (!r.ok) continue
    const was = limitValue(current?.[f.key])
    if (r.value !== was) out.push({ key: f.key, label: f.label, from: limitText(f.key, was), to: limitText(f.key, r.value) })
  }
  return out
}

/** Строка «что изменится» для диалога. */
export const limitsWhat = (lines: readonly LimitChange[]): string => `Потолки расхода: ${lines.map((l) => `${l.label.toLowerCase()} ${l.from} → ${l.to}`).join('; ')}.`

/** Аргументы действия llm.provider op update: все пять полей лимитов целиком (имена как в конфигурации сервера). null: ошибка ввода или нечего менять. */
export function limitsArgs(id: string, current: Limits | null | undefined, draft: LimitsDraft): { op: 'update'; id: string; patch: { limits: Record<LimitField['wire'], number> } } | null {
  const all = resolveLimits(current, draft)
  if (!all || !limitChanges(current, draft).length) return null
  const limits = {} as Record<LimitField['wire'], number>
  for (const f of LIMIT_FIELDS) limits[f.wire] = all[f.key]
  return { op: 'update', id, patch: { limits } }
}

/** Причина, по которой провайдер нельзя включить (нужен ключ, а его нет), иначе null. Выключить можно всегда; решение остаётся за сервером. */
export function enableBlock(p: Pick<ProviderRow, 'cred' | 'local'>, wantEnabled: boolean): string | null {
  if (!wantEnabled) return null
  return !p.local && p.cred.needed && !p.cred.present ? 'Сначала добавьте ключ.' : null
}

/** Что мы ждём увидеть в теме после действия. */
export type Expect = { enabled: boolean } | { limits: Record<LimitKey, number> } | { key: boolean }

export function limitsEqual(a: Limits | null | undefined, b: Record<LimitKey, number>): boolean {
  return LIMIT_FIELDS.every((f) => limitValue(a?.[f.key]) === b[f.key])
}

/** Отразила ли тема ожидаемое. */
export function providerReflects(p: ProviderRow | null | undefined, e: Expect): boolean {
  if (!p) return false
  if ('enabled' in e) return p.enabled === e.enabled
  if ('limits' in e) return limitsEqual(p.limits, e.limits)
  return p.cred.present === e.key
}

/** Столько ждём обновления темы settings (она обновляется до 15 с); дольше «ожидаем» не показываем. */
export const REFLECT_WAIT_MS = 25_000

/** Ожидаемые значения лимитов поверх текущих: так «текущая» картина в панели учитывает отправленное, пока тема не обновилась. */
export const withLimits = (current: Limits, to: Record<LimitKey, number>): Limits => ({ ...current, ...to })
