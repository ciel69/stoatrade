import type { ProfileRow, RoleRow } from '../model/types'

/** Не больше стольких запасных у роли (сервер отвергает длиннее). */
export const MAX_FALLBACK = 4

/** Цепочка роли в идентификаторах профилей: основная и запасные по порядку (порядок важен: пробуются по очереди). */
export interface Chain {
  primary: string | null
  fallback: string[]
}

/** Профиль для показа: общая часть ProfileRow и RoleModel, чтобы показывать и профили из списка, и модели роли, которых в списке нет. */
export type ProfileLike = Pick<ProfileRow, 'id' | 'provider' | 'model_name' | 'provider_enabled' | 'live_ok'>

/** Имя роли для действий: поле target, а у сервера без него «команда.роль» или «shared.роль». */
export const targetOf = (r: Pick<RoleRow, 'role' | 'target' | 'team'>): string => r.target ?? `${r.team ?? 'shared'}.${r.role}`

export const chainOf = (r: RoleRow): Chain => ({ primary: r.primary?.id ?? null, fallback: r.fallback.map((f) => f.id) })

/**
 * Роль live-команды в режиме live: запасные, параметры и профиль такой роли не меняются (ctl вернёт E_LIVE_IN_USE),
 * основная меняется только действием brain.use.
 */
export const liveLocked = (r: Pick<RoleRow, 'live_risk' | 'team'>, mode: string | null | undefined, liveTeam: string | null | undefined): boolean =>
  mode === 'live' && r.live_risk && r.team != null && r.team === liveTeam

const sameList = (a: readonly string[], b: readonly string[]): boolean => a.length === b.length && a.every((x, i) => x === b[i])

/** Что в черновике отличается от текущего. */
export function changed(base: Chain, draft: Chain): { primary: boolean; fallback: boolean; any: boolean } {
  const primary = base.primary !== draft.primary
  const fallback = !sameList(base.fallback, draft.fallback)
  return { primary, fallback, any: primary || fallback }
}

/** Выбрать основную: профиль не может быть одновременно запасным, поэтому из запасных он убирается. */
export function setPrimary(c: Chain, id: string): Chain {
  return { primary: id, fallback: c.fallback.filter((x) => x !== id) }
}

/** Добавить запасную в конец: без дублей, без основной, не больше MAX_FALLBACK. Иначе цепочка та же. */
export function addFallback(c: Chain, id: string): Chain {
  if (id === c.primary || c.fallback.includes(id) || c.fallback.length >= MAX_FALLBACK) return c
  return { ...c, fallback: [...c.fallback, id] }
}

export function removeFallback(c: Chain, id: string): Chain {
  return { ...c, fallback: c.fallback.filter((x) => x !== id) }
}

/** Сдвинуть запасную на место выше (dir -1) или ниже (dir 1); у края ничего не меняется. */
export function moveFallback(c: Chain, id: string, dir: -1 | 1): Chain {
  const i = c.fallback.indexOf(id)
  const j = i + dir
  if (i < 0 || j < 0 || j >= c.fallback.length) return c
  const next = [...c.fallback]
  ;[next[i], next[j]] = [next[j], next[i]]
  return { ...c, fallback: next }
}

/** Профили, которые можно добавить запасными: ещё не в цепочке и не основная. */
export const fallbackCandidates = (profiles: readonly ProfileRow[], c: Chain): ProfileRow[] =>
  profiles.filter((p) => p.id !== c.primary && !c.fallback.includes(p.id))

/** Аргументы brain.role (op chain): только изменившиеся поля. null: менять нечего. */
export function chainArgs(target: string, base: Chain, draft: Chain): Record<string, unknown> | null {
  const ch = changed(base, draft)
  if (!ch.any) return null
  const args: Record<string, unknown> = { op: 'chain', role: target }
  if (ch.primary && draft.primary) args.primary = draft.primary
  if (ch.fallback) args.fallback = [...draft.fallback]
  // основную нельзя «убрать»: если она сброшена в null, а запасные не менялись, команда пустая
  return args.primary !== undefined || args.fallback !== undefined ? args : null
}

/** Аргументы для явного набора значений (возврат прежнего). */
export function chainArgsOf(target: string, to: Partial<Chain>): Record<string, unknown> | null {
  const args: Record<string, unknown> = { op: 'chain', role: target }
  if (to.primary) args.primary = to.primary
  if (to.fallback) args.fallback = [...to.fallback]
  return args.primary !== undefined || args.fallback !== undefined ? args : null
}

/** Аргументы brain.use: смена основной модели live-роли в live. */
export const useArgs = (target: string, profile: string): Record<string, unknown> => ({ role: target, profile })

export interface ChangeLine {
  label: 'Основная' | 'Запасные'
  from: string
  to: string
}

const nameIn = (names: ReadonlyMap<string, ProfileLike>, id: string): string => names.get(id)?.model_name ?? id
export const listText = (ids: readonly string[], names: ReadonlyMap<string, ProfileLike>): string => (ids.length ? ids.map((x) => nameIn(names, x)).join(', ') : 'нет')

/** «Было → станет» по изменившимся полям. */
export function changeLines(base: Chain, draft: Chain, names: ReadonlyMap<string, ProfileLike>): ChangeLine[] {
  const ch = changed(base, draft)
  const out: ChangeLine[] = []
  if (ch.primary) out.push({ label: 'Основная', from: base.primary ? nameIn(names, base.primary) : 'не назначена', to: draft.primary ? nameIn(names, draft.primary) : 'не назначена' })
  if (ch.fallback) out.push({ label: 'Запасные', from: listText(base.fallback, names), to: listText(draft.fallback, names) })
  return out
}

/** Строка «что изменится» для диалога: изменившееся «было → станет», остальное «не меняется». */
export function whatText(lines: readonly ChangeLine[]): string {
  const parts = lines.map((l) => `${l.label}: ${l.from} → ${l.to}.`)
  if (!lines.some((l) => l.label === 'Основная')) parts.push('Основная не меняется.')
  if (!lines.some((l) => l.label === 'Запасные')) parts.push('Запасные не меняются.')
  return parts.join(' ')
}

/** Те же строки в обратную сторону: для возврата прежнего. */
export const reverseLines = (lines: readonly ChangeLine[]): ChangeLine[] => lines.map((l) => ({ ...l, from: l.to, to: l.from }))

/** Снимок отразил ожидаемое: основная и/или запасные совпали с тем, что отправляли. */
export function reflects(r: RoleRow | null | undefined, expect: Partial<Chain>): boolean {
  if (!r) return false
  const c = chainOf(r)
  if (expect.primary !== undefined && c.primary !== expect.primary) return false
  if (expect.fallback !== undefined && !sameList(c.fallback, expect.fallback)) return false
  return true
}

/** Поверх текущей цепочки накладывается ещё не отражённое в данных (после применения, пока снимок не обновился). */
export const overlay = (c: Chain, expect: Partial<Chain>): Chain => ({ primary: expect.primary !== undefined ? expect.primary : c.primary, fallback: expect.fallback !== undefined ? [...expect.fallback] : c.fallback })

/** Почему профиль нельзя выбрать (null: можно). */
export const profileBlock = (p: ProfileLike): string | null => (p.provider_enabled ? null : 'провайдер выключен')
