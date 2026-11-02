import type { ProviderRow } from '../model/types'
import { LIMIT_FIELDS, limitChanges, limitErrors, limitValue, limitsArgs, type LimitKey, type LimitsDraft } from './provider'
import { RE_MODEL_NAME, addArgs, addressPatch, addressReflects, urlProblem, type AddArgs } from './providerNet'

/**
 * Логика двух форм провайдера (новый и «изменить» это одна и та же форма): черновик, проверка полей словами, аргументы llm.provider add и update, «что изменится», «отразила ли тема».
 * Внутреннее имя (id) человек не вводит: оно строится из названия. Ключа здесь нет: он вводится отдельным путём (только запись).
 */

const TRANSLIT: Record<string, string> = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z', и: 'i', й: 'y', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f',
  х: 'h', ц: 'c', ч: 'ch', ш: 'sh', щ: 'sch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya',
}

/** Внутреннее имя (id) из названия: латиница (русские буквы транслитерируются), цифры и «_», до 28 знаков; занятые имена обходятся числом. */
export function slugId(name: string, taken: readonly string[], fallback = 'item'): string {
  let s = [...name.trim().toLowerCase()].map((c) => TRANSLIT[c] ?? c).join('').replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 26).replace(/_+$/, '')
  if (!s) s = fallback
  if (!taken.includes(s)) return s
  for (let n = 2; n < 100; n += 1) {
    const k = `${s}_${n}`
    if (!taken.includes(k)) return k
  }
  return s
}

export interface ProviderDraft {
  name: string
  adapter: string
  /** новый: обязателен; изменить: пусто значит «адрес не менять» (в данных только хост) */
  baseUrl: string
  allowPrivate: boolean
  /** первая модель нового провайдера: сервер принимает провайдера только с моделью */
  model: string
  limits: LimitsDraft
}

export const emptyProviderDraft = (): ProviderDraft => ({ name: '', adapter: 'openai_compat', baseUrl: '', allowPrivate: false, model: '', limits: {} })

/** Имена типа в данных бывают разные (openai, openai-compatible, openai_compat): сводим к одному из списка формы. */
export const normAdapter = (a: string | null | undefined): string => (!a || a === 'openai' || a === 'openai-compatible' ? 'openai_compat' : a)

export const draftOfProvider = (p: ProviderRow): ProviderDraft => ({ name: p.name, adapter: normAdapter(p.adapter), baseUrl: '', allowPrivate: !!p.allow_private, model: '', limits: {} })

export interface ProviderErrors {
  name?: string
  baseUrl?: string
  model?: string
  limits?: Partial<Record<LimitKey, string>>
}

/** Ошибки ввода словами. row = null: создание (адрес и модель обязательны), иначе правка (пустой адрес значит «не менять»). */
export function providerErrors(d: ProviderDraft, row: ProviderRow | null): ProviderErrors {
  const e: ProviderErrors = {}
  const name = d.name.trim()
  if (!name) e.name = 'Введите название.'
  else if (name.length > 60) e.name = 'Не длиннее 60 знаков.'
  if (!row || d.baseUrl.trim()) {
    const u = urlProblem(d.baseUrl)
    if (u) e.baseUrl = u
  }
  if (!row && !RE_MODEL_NAME.test(d.model.trim())) e.model = 'Имя модели у провайдера: латинские буквы, цифры и «. : / - _», до 80 знаков.'
  const l = limitErrors(d.limits)
  if (Object.keys(l).length) e.limits = l
  return e
}

/** Аргументы llm.provider add для нового провайдера; id строится из названия. null: есть ошибки ввода. */
export function createArgs(d: ProviderDraft, ids: readonly string[]): AddArgs | null {
  if (Object.keys(providerErrors(d, null)).length) return null
  const id = slugId(d.name, ids, 'provider')
  return addArgs({ id, name: d.name.trim(), adapter: d.adapter, baseUrl: d.baseUrl, keyFile: '', model: d.model, allowPrivate: d.allowPrivate }, ids)
}

export type ProviderPatch = Record<string, unknown> & { limits?: Record<string, number> }

/** Патч для llm.provider update: только изменённое (название, адрес, внутренняя сеть, лимиты целиком). null: ошибка ввода или менять нечего. */
export function editPatch(d: ProviderDraft, row: ProviderRow): ProviderPatch | null {
  if (Object.keys(providerErrors(d, row)).length) return null
  const patch: ProviderPatch = {}
  if (d.name.trim() !== row.name) patch.name = d.name.trim()
  Object.assign(patch, addressPatch({ baseUrl: d.baseUrl, allowPrivate: d.allowPrivate }, row))
  const lim = limitsArgs(row.id, row.limits, d.limits)
  if (lim) patch.limits = lim.patch.limits
  return Object.keys(patch).length ? patch : null
}

/** Что изменится, по строкам для диалога и для сводки. */
export function editLines(d: ProviderDraft, row: ProviderRow): string[] {
  const out: string[] = []
  if (d.name.trim() && d.name.trim() !== row.name) out.push(`название: ${row.name} → ${d.name.trim()}`)
  if (d.baseUrl.trim() && !urlProblem(d.baseUrl)) out.push('новый адрес; ключ провайдера будет уходить на него')
  if (d.allowPrivate !== !!row.allow_private) out.push(`внутренняя сеть: ${d.allowPrivate ? 'разрешена' : 'запрещена'}`)
  for (const c of limitChanges(row.limits, d.limits)) out.push(`${c.label.toLowerCase()}: ${c.from} → ${c.to}`)
  return out
}

/** Отразила ли тема правку: название, адрес, внутренняя сеть и лимиты совпали с отправленным. */
export function editReflects(row: ProviderRow | null | undefined, patch: ProviderPatch): boolean {
  if (!row) return false
  if (typeof patch.name === 'string' && row.name !== patch.name) return false
  if (!addressReflects(row, patch)) return false
  if (patch.limits) {
    for (const f of LIMIT_FIELDS) if (limitValue(row.limits[f.key]) !== (patch.limits[f.wire] ?? 0)) return false
  }
  return true
}

/** Отразила ли тема создание. */
export const createReflects = (providers: readonly Pick<ProviderRow, 'id'>[] | undefined, id: string): boolean => (providers ?? []).some((p) => p.id === id)
