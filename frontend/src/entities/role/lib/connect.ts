import { roleLabel } from './role'
import type { ExchangeRow, ModelPrice, ProviderRow, RoleRow, SwapRow } from '../model/types'

/** Чистая логика блока «Что подключено» и журнала смен: фразы вместо терминов конфигурации. Ключей в данных нет, показываем только факт «ключ есть». */

export interface ConnectIssue {
  tone: 'warn' | 'bad'
  text: string
}

const money = (x: number): string => `$${Number.isInteger(x) ? x : Number(x.toFixed(4))}`
const num = (x: number): string => x.toLocaleString('ru-RU')

/** Цена за миллион токенов: «$0.28»; нет данных: «–». */
export const perMillion = (x: number | null | undefined): string => (typeof x === 'number' && Number.isFinite(x) ? money(x) : '–')

/** Цена модели одной строкой: «вход $0.28 (из кэша $0.028), выход $0.42 за 1 млн токенов»; без цены «цена не задана». */
export function priceText(p: ModelPrice | null | undefined): string {
  if (!p) return 'цена не задана'
  const hit = p.in_hit != null ? ` (из кэша ${perMillion(p.in_hit)})` : ''
  return `вход ${perMillion(p.in_miss)}${hit}, выход ${perMillion(p.out)} за 1 млн токенов`
}

/** Размер контекста: 128000 -> «128 тыс. токенов», 1000000 -> «1 млн токенов». */
export function ctxText(n: number | null | undefined): string {
  if (typeof n !== 'number' || !Number.isFinite(n) || n <= 0) return '–'
  if (n >= 1_000_000) return `${Number((n / 1_000_000).toFixed(1))} млн токенов`
  if (n >= 1000) return `${Math.round(n / 1000)} тыс. токенов`
  return `${n} токенов`
}

/** Состояние ключа словами. Локальному провайдеру ключ не нужен; облачному без ключа включиться нельзя. */
export function credText(p: Pick<ProviderRow, 'cred'> & { local?: boolean }): string {
  if (p.local || !p.cred.needed) return 'ключ не нужен'
  return p.cred.present ? 'ключ есть' : 'ключа нет, провайдер без него не включить'
}

/** Предупреждение о правах файла ключа (только когда точно известно, что они слабые). */
export const permsWarning = (p: Pick<ProviderRow, 'cred'>): string | null => (p.cred.present && p.cred.perms_ok === false ? 'файл ключа читается другими пользователями' : null)

/** Лимиты расхода кратко: «до $3 в день, $1 в час, 60 запросов в минуту». Пустые лимиты: «без лимитов». */
export function limitsText(l: ProviderRow['limits'] | null | undefined): string {
  if (!l) return 'без лимитов'
  const parts: string[] = []
  const usd: string[] = []
  // ноль в настройках означает «без потолка», как в бюджете сервера
  if (l.day_usd) usd.push(`${money(l.day_usd)} в день`)
  if (l.hour_usd) usd.push(`${money(l.hour_usd)} в час`)
  if (usd.length) parts.push(`до ${usd.join(', ')}`)
  if (l.day_toks) parts.push(`${num(l.day_toks)} токенов в день`)
  if (l.rpm) parts.push(`${l.rpm} запросов в минуту`)
  if (l.concurrent) parts.push(`одновременно ${l.concurrent}`)
  return parts.length ? parts.join(', ') : 'без лимитов'
}

/** Склонение: 1 модель, 2 модели, 5 моделей. */
export function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10
  const m100 = n % 100
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many
}
export const modelsText = (n: number): string => `${n} ${plural(n, 'модель', 'модели', 'моделей')}`

/** Роли, которые ссылаются на провайдера (основной или запасной моделью). */
export function rolesUsing(providerId: string, roles: RoleRow[]): string[] {
  return roles.filter((r) => r.primary?.provider === providerId || r.fallback.some((f) => f.provider === providerId)).map((r) => r.role)
}

/** Замечания по провайдеру: выключен, а роли на него ссылаются; включён без ключа; ошибка загрузки; слабые права файла ключа. */
export function providerIssues(p: ProviderRow, roles: RoleRow[]): ConnectIssue[] {
  const out: ConnectIssue[] = []
  const users = rolesUsing(p.id, roles)
  if (!p.enabled && users.length) out.push({ tone: 'bad', text: `выключен, а им пользуется ${users.length > 1 ? 'роли' : 'роль'} ${users.map(roleLabel).join(', ')}` })
  if (p.enabled && p.cred.needed && !p.cred.present) out.push({ tone: 'bad', text: 'включён, а ключа нет: обращения будут отклонены' })
  const w = permsWarning(p)
  if (w) out.push({ tone: 'warn', text: w })
  if (p.error) out.push({ tone: 'warn', text: p.error })
  return out
}

/** Статус биржи словами: активная (на ней торгует платформа), разрешена, заготовка (адаптера нет), не разрешена. */
export function exchangeStatus(e: ExchangeRow): string {
  if (!e.implemented) return 'пока не поддерживается'
  if (e.active) return 'активная'
  return e.allowed ? 'разрешена' : 'не разрешена'
}

const CRED_WORDS: Record<string, string> = { read: 'чтение', trade: 'торговля', testnet: 'тестовая сеть' }
/** Какие виды ключей настроены: «чтение, торговля»; нет: «ключей нет». Сами ключи не показываются. */
export function credsText(creds: string[] | null | undefined): string {
  const c = creds ?? []
  return c.length ? c.map((x) => CRED_WORDS[x] ?? x).join(', ') : 'ключей нет'
}

const MODE_WORDS: Record<string, string> = { paper: 'бумага', testnet: 'тестовая сеть', live: 'живые деньги' }
export const modeText = (m: string | null | undefined): string => (m ? (MODE_WORDS[m] ?? m) : '–')

/** Вывод над блоком: сколько провайдеров включено, где работают, сколько бирж и в каком режиме. */
export function connectSummary(providers: ProviderRow[], exchanges: ExchangeRow[]): string {
  const on = providers.filter((p) => p.enabled)
  const cloud = on.filter((p) => !p.local).length
  const local = on.length - cloud
  const where = [cloud ? `${cloud} в облаке` : '', local ? `${local} локально` : ''].filter(Boolean).join(', ')
  const prov = providers.length ? `Включено провайдеров ${on.length} из ${providers.length}${where ? ` (${where})` : ''}` : 'Провайдеры не настроены'
  const act = exchanges.find((e) => e.active)
  const ex = act ? `торгует ${act.name}, режим: ${modeText(act.mode)}` : exchanges.length ? 'активной биржи нет' : 'биржи не настроены'
  return `${prov}; ${ex}.`
}

const BY_WORDS: Record<string, string> = { 'owner-cli': 'владелец, в терминале', ctl: 'через дашборд' }
export const swapBy = (by: string | null | undefined): string => (by ? (BY_WORDS[by] ?? 'другим способом') : 'неизвестно')

/** Что сделано словами: смена модели роли, перечитывание настроек; прочее как есть. */
export function swapText(s: SwapRow): string {
  if (s.kind === 'use') {
    const roles = (s.target ?? []).map(roleLabel).join(', ')
    const head = roles ? `${s.target.length > 1 ? 'роли' : 'роль'} ${roles}` : 'роль'
    return `${head}: модель ${s.from ?? '–'} → ${s.to ?? '–'}`
  }
  if (s.kind === 'reload') return 'перечитаны настройки'
  if (s.kind === 'rollback') return 'откат назначений'
  const tail = [(s.target ?? []).map(roleLabel).join(', '), s.from || s.to ? `${s.from ?? '–'} → ${s.to ?? '–'}` : ''].filter(Boolean).join(': ')
  return tail ? `другое изменение: ${tail}` : 'другое изменение'
}

/** «5 смен»; смен нет: «смен не было». Номер ревизии остаётся только в подробностях панели. */
export function swapsLine(count: number): string {
  if (!count) return 'смен не было'
  return `${count} ${plural(count, 'смена', 'смены', 'смен')}`
}

/** Дата и время по-русски: «31 окт., 14:05». ts в секундах. */
export function swapWhen(tsSeconds: number): string {
  if (!Number.isFinite(tsSeconds)) return '–'
  return new Date(tsSeconds * 1000).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}
