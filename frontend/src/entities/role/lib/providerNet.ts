import type { ProviderModel, ProviderRow } from '../model/types'
import { RE_ID } from './expert'

/**
 * Свой провайдер LLM и сетевые действия над ним: форма добавления (llm.provider op add со spec), смена адреса (op update), список моделей у провайдера (op models),
 * проверка подключения (op ping), добавление модели (op model_set). Чистая логика: проверка ввода, аргументы, слова вместо кодов ошибок. Ключа здесь нет: он вводится отдельным путём (только запись).
 * Правила адреса повторяют серверные (tb.llm.netpolicy); сервер проверяет всё заново, здесь только быстрая подсказка до отправки.
 */

export const ADAPTERS = [
  { id: 'openai_compat', label: 'OpenAI-совместимый' },
  { id: 'anthropic', label: 'Anthropic' },
  { id: 'deepseek', label: 'DeepSeek' },
] as const

export const RE_KEY_FILE = /^[A-Za-z0-9_.-]{1,60}$/
/** Имя модели у провайдера (как в запросах к нему). */
export const RE_MODEL_NAME = /^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,79}$/
export const MAX_SHOWN_MODELS = 100

export interface AddDraft {
  id: string
  /** название для людей; пусто: берётся id */
  name?: string
  adapter: string
  baseUrl: string
  /** пусто: имя файла ключа берётся по id провайдера */
  keyFile: string
  /** имя первой модели у провайдера: провайдер без моделей сервер не принимает */
  model: string
  allowPrivate: boolean
}

export const emptyAdd = (): AddDraft => ({ id: '', adapter: 'openai_compat', baseUrl: '', keyFile: '', model: '', allowPrivate: false })

const isLoopback = (h: string): boolean => h === 'localhost' || h === '[::1]' || /^127(\.\d{1,3}){3}$/.test(h)

function parse(url: string): URL | null {
  try {
    return new URL(url.trim())
  } catch {
    return null
  }
}

/** Что не так с адресом (слова) или null. Пустой адрес тоже ошибка. */
export function urlProblem(raw: string): string | null {
  const t = raw.trim()
  if (!t) return 'Укажите адрес.'
  if (/\s/.test(t) || t.length > 300) return 'Адрес без пробелов, до 300 знаков.'
  const u = parse(t)
  if (!u || (u.protocol !== 'https:' && u.protocol !== 'http:')) return 'Адрес должен начинаться с https://, например https://api.example.com/v1.'
  if (u.protocol === 'http:' && !isLoopback(u.hostname)) return 'Обычный http допустим только для этого компьютера (localhost). Для остальных нужен https.'
  if (u.username || u.password) return 'В адресе не должно быть логина и пароля.'
  if (u.search || u.hash || /[?#]/.test(t)) return 'В адресе не должно быть параметров (после «?») и фрагмента (после «#»).'
  return null
}

/** Похоже ли на адрес во внутренней сети (нужен флажок «Адрес во внутренней сети»). Только подсказка, решает сервер. */
export function looksPrivate(raw: string): boolean {
  const u = parse(raw)
  if (!u) return false
  const h = u.hostname
  if (isLoopback(h)) return false
  if (/^10(\.\d{1,3}){3}$/.test(h) || /^192\.168(\.\d{1,3}){2}$/.test(h)) return true
  const m = /^172\.(\d{1,3})(\.\d{1,3}){2}$/.exec(h)
  if (m && Number(m[1]) >= 16 && Number(m[1]) <= 31) return true
  return !h.includes('.') || /\.(local|internal|lan|corp|home|intranet)$/.test(h)
}

export interface AddErrors {
  id?: string
  baseUrl?: string
  keyFile?: string
  model?: string
}

export function addErrors(d: AddDraft, existingIds: readonly string[]): AddErrors {
  const e: AddErrors = {}
  if (!RE_ID.test(d.id)) e.id = 'Короткое имя: строчные латинские буквы, цифры, «-» и «_», до 30 знаков.'
  else if (existingIds.includes(d.id)) e.id = 'Провайдер с таким именем уже есть.'
  const u = urlProblem(d.baseUrl)
  if (u) e.baseUrl = u
  if (d.keyFile.trim() && (!RE_KEY_FILE.test(d.keyFile.trim()) || d.keyFile.includes('..'))) e.keyFile = 'Имя файла: латинские буквы, цифры, «.», «-», «_», без пути.'
  if (!RE_MODEL_NAME.test(d.model.trim())) e.model = 'Имя модели у провайдера: латинские буквы, цифры и «. : / - _», до 80 знаков.'
  return e
}

/** Имя переменной окружения для ключа (нужна адаптерам, кроме OpenAI-совместимого): ID_API_KEY, только буквы, цифры и «_». */
export const envName = (id: string): string => `${/^[0-9]/.test(id) ? 'LLM_' : ''}${id.toUpperCase().replace(/[^A-Z0-9]/g, '_')}_API_KEY`

/** Ключ модели в конфигурации (строчные буквы, цифры, «-», «_», до 30 знаков) из её имени у провайдера; занятые ключи обходятся числом. */
export function modelKey(name: string, taken: readonly string[]): string {
  let base = name.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^[-_]+|[-_]+$/g, '').slice(0, 30).replace(/[-_]+$/g, '')
  if (!base) base = 'model'
  if (!taken.includes(base)) return base
  for (let n = 2; n < 100; n += 1) {
    const k = `${base.slice(0, 30 - String(n).length - 1)}-${n}`
    if (!taken.includes(k)) return k
  }
  return base
}

/** Модель без известных цен и возможностей: всё по нулям и выключено, чтобы не считать то, чего не знаем. Цены нужно сверить с тарифом провайдера. */
export const newModelSpec = (name: string): { name: string; tools: false; json_mode: false; price: { in_miss: 0; in_hit: 0; out: 0 } } => ({
  name: name.trim(), tools: false, json_mode: false, price: { in_miss: 0, in_hit: 0, out: 0 },
})

export interface AddArgs {
  op: 'add'
  id: string
  spec: Record<string, unknown>
}

/** Аргументы llm.provider add для своего провайдера. null: есть ошибки ввода. Провайдер добавляется выключенным; ключа в аргументах нет. */
export function addArgs(d: AddDraft, existingIds: readonly string[]): AddArgs | null {
  if (Object.keys(addErrors(d, existingIds)).length) return null
  const spec: Record<string, unknown> = {
    name: d.name?.trim() || d.id, adapter: d.adapter, base_url: d.baseUrl.trim().replace(/\/+$/, ''), key_file: d.keyFile.trim() || d.id,
    models: { [modelKey(d.model, [])]: newModelSpec(d.model) },
  }
  if (d.adapter !== 'openai_compat') spec.key_env = envName(d.id)
  if (d.allowPrivate) spec.allow_private = true
  return { op: 'add', id: d.id, spec }
}

/** Строки «что будет» для подтверждения добавления. */
export function addLines(d: AddDraft): string {
  const a = ADAPTERS.find((x) => x.id === d.adapter)?.label ?? d.adapter
  return `провайдер «${d.name?.trim() || d.id}» (${a}), адрес ${d.baseUrl.trim()}, модель ${d.model.trim()}; добавится выключенным, без ключа${d.allowPrivate ? '; внутренняя сеть разрешена' : ''}.`
}

// ---------------------------------------------------------------- смена адреса

export interface AddressDraft {
  baseUrl: string
  allowPrivate: boolean
}

/** Патч для llm.provider update: только то, что изменилось. Адрес сравнить с текущим нельзя (в данных только хост), поэтому непустой адрес считается сменой. null: менять нечего или ошибка. */
export function addressPatch(d: AddressDraft, was: Pick<ProviderRow, 'allow_private'>): Record<string, unknown> | null {
  const patch: Record<string, unknown> = {}
  if (d.baseUrl.trim()) {
    if (urlProblem(d.baseUrl)) return null
    patch.base_url = d.baseUrl.trim().replace(/\/+$/, '')
  }
  if (d.allowPrivate !== !!was.allow_private) patch.allow_private = d.allowPrivate
  return Object.keys(patch).length ? patch : null
}

export function hostOf(url: string): string | null {
  return parse(url)?.host ?? null
}

/** Отразила ли тема смену адреса: хост совпал (путь в данных не приходит), флаг внутренней сети совпал. */
export function addressReflects(p: Pick<ProviderRow, 'base_host' | 'allow_private'> | null | undefined, patch: Record<string, unknown>): boolean {
  if (!p) return false
  if (typeof patch.base_url === 'string') {
    const h = hostOf(patch.base_url)
    if (h && p.base_host !== h && p.base_host !== h.replace(/:\d+$/, '')) return false
  }
  if (typeof patch.allow_private === 'boolean' && !!p.allow_private !== patch.allow_private) return false
  return true
}

// ---------------------------------------------------------------- итоги сетевых действий словами

/** Итог команды от платформы (то, что приходит в ctl_final). */
export interface NetFinal {
  status: string
  code: string
  msg: string
  data?: Record<string, unknown> | null
}

const NET_ERRORS: Record<string, string> = {
  auth: 'Провайдер не принял ключ. Проверьте, что ключ верный и у него есть доступ к моделям.',
  rate: 'Провайдер просит не спешить: слишком много запросов. Повторите позже.',
  server: 'У провайдера сбой на его стороне. Повторите позже.',
  timeout: 'Провайдер не ответил вовремя.',
  network: 'Не удалось связаться с провайдером. Проверьте адрес и что сервер доступен.',
  redirect: 'Адрес перенаправляет на другой. Платформа за перенаправлениями не ходит, это защита ключа: укажите конечный адрес.',
  bad_body: 'Ответ не похож на ожидаемый. Проверьте адрес и тип провайдера.',
  config: 'Настройки провайдера не позволяют сделать запрос. Проверьте адрес и тип.',
  billing: 'Провайдер отказал по оплате: на счёте нет средств или исчерпан лимит.',
  bad_request: 'Провайдер отклонил запрос. Проверьте имя модели.',
}

const clip = (v: unknown, n: number): string => (typeof v === 'string' ? v.replace(/\s+/g, ' ').trim().slice(0, n) : '')

export function netErrorText(code: unknown, status?: unknown): string {
  const base = NET_ERRORS[String(code)] ?? 'Провайдер ответил ошибкой.'
  return typeof status === 'number' && status > 0 ? `${base} Код ответа: ${status}.` : base
}

export interface NetFail {
  ok: false
  text: string
  /** то, что ответил провайдер (чужой текст, только для показа): короткий, без разметки */
  detail: string
  /** через сколько секунд можно повторить (лимит частоты платформы) */
  retryS: number | null
}

const rejected = (f: NetFinal | undefined, fallback: string): NetFail => {
  const d = f?.data ?? {}
  if (f?.code === 'E_RATE') {
    const s = typeof d.retry_after_s === 'number' ? Math.max(1, Math.ceil(d.retry_after_s)) : 3
    return { ok: false, text: `Слишком часто: платформа пускает один запрос на провайдера раз в несколько секунд. Повторите через ${s} с.`, detail: '', retryS: s }
  }
  if (f?.code === 'E_NO_KEY') return { ok: false, text: 'У провайдера нет ключа. Сначала введите ключ.', detail: '', retryS: null }
  return { ok: false, text: fallback, detail: '', retryS: null }
}

export interface ModelsOk {
  ok: true
  ids: string[]
  count: number
  truncated: boolean
}

/** Итог op models словами. */
export function modelsOutcome(f: NetFinal | undefined, fallback: string): ModelsOk | NetFail {
  const d = f?.data
  if (f?.status !== 'done' || !d) return rejected(f, fallback)
  if (d.ok !== true) return { ok: false, text: netErrorText(d.error, d.status), detail: clip(d.detail, 200), retryS: null }
  const ids = (Array.isArray(d.ids) ? d.ids : []).filter((x): x is string => typeof x === 'string')
  return { ok: true, ids, count: typeof d.count === 'number' ? d.count : ids.length, truncated: d.truncated === true }
}

export interface PingOk {
  ok: true
  ms: number
  model: string
  modelName: string
  tokIn: number | null
  tokOut: number | null
  text: string
}

/** Итог op ping словами. */
export function pingOutcome(f: NetFinal | undefined, fallback: string): PingOk | NetFail {
  const d = f?.data
  if (f?.status !== 'done' || !d) return rejected(f, fallback)
  if (d.ok !== true) return { ok: false, text: netErrorText(d.error, d.status), detail: clip(d.detail, 200), retryS: null }
  const u = (d.usage ?? {}) as Record<string, unknown>
  const n = (v: unknown): number | null => (typeof v === 'number' ? v : null)
  return { ok: true, ms: typeof d.ms === 'number' ? d.ms : 0, model: clip(d.model, 40), modelName: clip(d.model_name, 80), tokIn: n(u.in), tokOut: n(u.out), text: clip(d.text, 80) }
}

/** «Ответ за 0,84 с. Модель X. Токенов: вход 9, выход 2.» */
export function pingLine(p: PingOk): string {
  const sec = (p.ms / 1000).toFixed(2).replace('.', ',')
  const parts = [`Ответ за ${sec} с`, p.modelName ? `Модель ${p.modelName}` : null, p.tokIn != null || p.tokOut != null ? `Токенов: вход ${p.tokIn ?? '–'}, выход ${p.tokOut ?? '–'}` : null]
  return parts.filter(Boolean).join('. ') + '.'
}

/** Фильтр списка моделей по подстроке (без учёта регистра); показывается не больше MAX_SHOWN_MODELS. */
export function filterIds(ids: readonly string[], query: string, limit = MAX_SHOWN_MODELS): { shown: string[]; total: number } {
  const q = query.trim().toLowerCase()
  const all = q ? ids.filter((i) => i.toLowerCase().includes(q)) : [...ids]
  return { shown: all.slice(0, limit), total: all.length }
}

/** Подпись под списком: сколько всего у провайдера, сколько подходит, усечён ли список. */
export function modelsNote(r: ModelsOk, total: number, shown: number, query: string): string {
  const parts = [r.truncated ? `Показаны первые ${r.ids.length} по алфавиту из ${r.count}: остальные платформа не передаёт, введите имя модели вручную.` : `Моделей у провайдера: ${r.count}.`]
  if (query.trim()) parts.push(`Подходит: ${total}.`)
  if (total > shown) parts.push(`Показаны первые ${shown}: уточните поиск.`)
  return parts.join(' ')
}

export const modelAddReflects = (p: Pick<ProviderRow, 'models'> | null | undefined, name: string): boolean => !!p && p.models.some((m: ProviderModel) => m.name === name.trim())

/** Можно ли делать сетевой запрос к провайдеру: ключ задан или не нужен. */
export const netReady = (p: Pick<ProviderRow, 'local' | 'cred'> | null | undefined, keyPresent: boolean): boolean => !!p && (p.local || !p.cred.needed || keyPresent)
