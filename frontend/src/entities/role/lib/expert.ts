import type { ProfileRow, RoleRow } from '../model/types'

/**
 * Профили моделей (brain.profile) и особые параметры роли (brain.role op opts / opts_clear). Чистая логика: проверка ввода, аргументы действий, «было → станет», «отразила ли тема». Пустое поле значит «не менять»:
 * сервер не умеет снять отдельное значение параметра, поэтому вернуть стандартные значения роли можно только целиком (opts_clear).
 */

export const RE_ID = /^[a-z0-9][a-z0-9_-]{0,29}$/
export const THINKING = [{ id: 'off', label: 'выключено' }, { id: 'low', label: 'немного' }, { id: 'medium', label: 'средне' }, { id: 'high', label: 'много' }] as const
export const thinkingWord = (v: string | null | undefined): string => THINKING.find((t) => t.id === v)?.label ?? (v || 'по умолчанию')

const num = (t: string): number | null => (/^\d+(\.\d+)?$/.test(t.trim().replace(',', '.')) ? Number(t.trim().replace(',', '.')) : null)
const int = (t: string): number | null => (/^\d+$/.test(t.trim()) ? Number(t.trim()) : null)
const fmt = (v: number | null | undefined): string => (v == null ? 'по умолчанию' : String(v))

export interface Line {
  label: string
  from: string
  to: string
}

// ---------------------------------------------------------------- профили моделей

export interface ProfileDraft {
  id: string
  provider: string
  model: string
  /** текст; пусто: не менять */
  temperature: string
  maxTokens: string
  thinking: string
}

export const emptyProfile = (): ProfileDraft => ({ id: '', provider: '', model: '', temperature: '', maxTokens: '', thinking: '' })

export const draftOfProfile = (p: ProfileRow): ProfileDraft => ({
  id: p.id, provider: p.provider, model: p.model ?? '', temperature: p.params?.temperature != null ? String(p.params.temperature) : '', maxTokens: p.params?.max_out_tok != null ? String(p.params.max_out_tok) : '',
  thinking: p.params?.thinking ?? '',
})

export interface ProfileErrors {
  id?: string
  provider?: string
  model?: string
  temperature?: string
  maxTokens?: string
}

export function profileErrors(d: ProfileDraft, existing: ProfileRow | null, ids: readonly string[]): ProfileErrors {
  const e: ProfileErrors = {}
  if (!existing) {
    if (!RE_ID.test(d.id)) e.id = 'Короткое имя: строчные латинские буквы, цифры, «-» и «_», до 30 знаков.'
    else if (ids.includes(d.id)) e.id = 'Профиль с таким именем уже есть.'
  }
  if (!d.provider) e.provider = 'Выберите провайдера.'
  if (!d.model) e.model = 'Выберите модель.'
  if (d.temperature.trim()) {
    const t = num(d.temperature)
    if (t == null || t > 2) e.temperature = 'Число от 0 до 2, например 0.3.'
  }
  if (d.maxTokens.trim()) {
    const m = int(d.maxTokens)
    if (m == null || m < 1 || m > 1_000_000) e.maxTokens = 'Целое число токенов, например 1500.'
  }
  return e
}

/** Что изменится: для нового профиля все заполненные поля, для существующего только отличающиеся. */
export function profileLines(d: ProfileDraft, existing: ProfileRow | null, providerName: (id: string) => string, modelName: (provider: string, model: string) => string): Line[] {
  const out: Line[] = []
  const was = existing ? draftOfProfile(existing) : null
  const none = 'нет'
  if (!was || was.provider !== d.provider || was.model !== d.model) {
    out.push({ label: 'Модель', from: was ? `${modelName(was.provider, was.model)} (${providerName(was.provider)})` : none, to: d.model ? `${modelName(d.provider, d.model)} (${providerName(d.provider)})` : none })
  }
  const t = d.temperature.trim() ? num(d.temperature) : null
  if (t != null && String(t) !== was?.temperature) out.push({ label: 'Разброс ответов (температура)', from: fmt(was?.temperature ? Number(was.temperature) : null), to: String(t) })
  const m = d.maxTokens.trim() ? int(d.maxTokens) : null
  if (m != null && String(m) !== was?.maxTokens) out.push({ label: 'Длина ответа, токенов', from: fmt(was?.maxTokens ? Number(was.maxTokens) : null), to: String(m) })
  if (d.thinking && d.thinking !== was?.thinking) out.push({ label: 'Рассуждение', from: thinkingWord(was?.thinking), to: thinkingWord(d.thinking) })
  return out
}

export interface ProfileArgs {
  op: 'set'
  id: string
  provider?: string
  model?: string
  params?: { temperature?: number; max_tokens?: number; thinking?: string }
}

/** Аргументы brain.profile: только отличающееся от существующего профиля. null: ошибка ввода или менять нечего. */
export function profileArgs(d: ProfileDraft, existing: ProfileRow | null, ids: readonly string[]): ProfileArgs | null {
  if (Object.keys(profileErrors(d, existing, ids)).length) return null
  const was = existing ? draftOfProfile(existing) : null
  const out: ProfileArgs = { op: 'set', id: d.id }
  if (!was || was.provider !== d.provider || was.model !== d.model) { out.provider = d.provider; out.model = d.model }
  const params: NonNullable<ProfileArgs['params']> = {}
  const t = d.temperature.trim() ? num(d.temperature) : null
  if (t != null && String(t) !== was?.temperature) params.temperature = t
  const m = d.maxTokens.trim() ? int(d.maxTokens) : null
  if (m != null && String(m) !== was?.maxTokens) params.max_tokens = m
  if (d.thinking && d.thinking !== was?.thinking) params.thinking = d.thinking
  if (Object.keys(params).length) out.params = params
  return out.provider || out.params ? out : null
}

/** Отразила ли тема профиль: он есть, и то, что отправляли, совпадает. */
export function profileReflects(rows: readonly ProfileRow[] | undefined, args: ProfileArgs): boolean {
  const r = (rows ?? []).find((x) => x.id === args.id)
  if (!r) return false
  if (args.provider && (r.provider !== args.provider || r.model !== args.model)) return false
  const p = args.params
  if (p?.temperature != null && r.params?.temperature !== p.temperature) return false
  if (p?.max_tokens != null && r.params?.max_out_tok !== p.max_tokens) return false
  if (p?.thinking != null && r.params?.thinking !== p.thinking) return false
  return true
}

/** Параметры профиля словами: «разброс 0.2 · ответ до 1500 токенов · рассуждение немного». */
export function paramsText(p: ProfileRow['params'] | null | undefined): string {
  const parts = [p?.temperature != null ? `разброс ${p.temperature}` : null, p?.max_out_tok != null ? `ответ до ${p.max_out_tok} токенов` : null, p?.thinking ? `рассуждение: ${thinkingWord(p.thinking)}` : null].filter(Boolean)
  return parts.length ? parts.join(' · ') : 'параметры по умолчанию'
}

// ---------------------------------------------------------------- особые параметры роли

export interface OptsDraft {
  budget: string
  temperature: string
  maxTokens: string
}

export const emptyOpts = (): OptsDraft => ({ budget: '', temperature: '', maxTokens: '' })

export interface OptsErrors {
  budget?: string
  temperature?: string
  maxTokens?: string
}

export function optsErrors(d: OptsDraft): OptsErrors {
  const e: OptsErrors = {}
  if (d.budget.trim()) {
    const b = num(d.budget)
    if (b == null || b <= 0 || !/^\d+([.,]\d{1,2})?$/.test(d.budget.trim())) e.budget = 'Сумма в долларах, например 1.5 (не больше двух знаков после запятой).'
  }
  if (d.temperature.trim()) {
    const t = num(d.temperature)
    if (t == null || t > 2) e.temperature = 'Число от 0 до 2, например 0.3.'
  }
  if (d.maxTokens.trim()) {
    const m = int(d.maxTokens)
    if (m == null || m < 1 || m > 1_000_000) e.maxTokens = 'Целое число токенов, например 1500.'
  }
  return e
}

export interface OptsArgs {
  op: 'opts'
  role: string
  budget?: { day_usd: number }
  params?: { temperature?: number; max_tokens?: number }
}

/** Аргументы brain.role op opts: только заполненные и отличающиеся от текущих значения роли. null: ошибка или менять нечего. */
export function optsArgs(target: string, d: OptsDraft, r: RoleRow | null): OptsArgs | null {
  if (Object.keys(optsErrors(d)).length) return null
  const out: OptsArgs = { op: 'opts', role: target }
  const b = d.budget.trim() ? num(d.budget) : null
  if (b != null && b !== r?.budget_day_usd) out.budget = { day_usd: b }
  const params: NonNullable<OptsArgs['params']> = {}
  const t = d.temperature.trim() ? num(d.temperature) : null
  if (t != null && t !== r?.primary?.params.temperature) params.temperature = t
  const m = d.maxTokens.trim() ? int(d.maxTokens) : null
  if (m != null && m !== r?.primary?.params.max_out_tok) params.max_tokens = m
  if (Object.keys(params).length) out.params = params
  return out.budget || out.params ? out : null
}

export function optsLines(a: OptsArgs, r: RoleRow | null): Line[] {
  const out: Line[] = []
  if (a.budget) out.push({ label: 'Лимит расходов в день', from: r?.budget_day_usd != null ? `${r.budget_day_usd} USD` : 'не задан', to: `${a.budget.day_usd} USD` })
  if (a.params?.temperature != null) out.push({ label: 'Разброс ответов (температура)', from: fmt(r?.primary?.params.temperature), to: String(a.params.temperature) })
  if (a.params?.max_tokens != null) out.push({ label: 'Длина ответа, токенов', from: fmt(r?.primary?.params.max_out_tok), to: String(a.params.max_tokens) })
  return out
}

export function optsReflects(r: RoleRow | null | undefined, a: OptsArgs): boolean {
  if (!r) return false
  if (a.budget && r.budget_day_usd !== a.budget.day_usd) return false
  if (a.params?.temperature != null && r.primary?.params.temperature !== a.params.temperature) return false
  if (a.params?.max_tokens != null && r.primary?.params.max_out_tok !== a.params.max_tokens) return false
  return true
}
