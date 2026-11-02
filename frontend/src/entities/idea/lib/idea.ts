import type { IdeaItem, IdeaOutcome, IdeaStatus, IdeaTab, IdeasSnapshot } from '../model/types'

/** Воронка: идея (цены у уровня ещё нет) → цена у уровня, мозг проверяет → план (его ведёт исполнитель) → сделка. Статусы по вкладкам; авто-история своих статусов не имеет. */
export const TAB_STATUSES: Record<Exclude<IdeaTab, 'auto'>, IdeaStatus[]> = {
  active: ['watching', 'idea'],
  taken: ['planned', 'taken'],
  skipped: ['skipped', 'expired', 'invalidated'],
}

export const TAB_LABEL: Record<IdeaTab, string> = { active: 'Ждут уровня', taken: 'Стали планом', skipped: 'Не сработали', auto: 'Авто-история' }

export const STATUS_RU: Record<IdeaStatus, string> = {
  idea: 'заметка',
  watching: 'ждём уровень',
  planned: 'план ждёт входа',
  taken: 'стала сделкой',
  skipped: 'пропущена',
  expired: 'истекла',
  invalidated: 'тезис сломан',
}

export function tabCount(s: IdeasSnapshot, tab: IdeaTab): number {
  if (tab === 'auto') return s.counts.auto_skipped ?? s.stats.n_events ?? 0
  return TAB_STATUSES[tab].reduce((a, st) => a + (s.counts[st] ?? 0), 0)
}

/** Первая вкладка, где есть идеи; иначе авто-история. */
export function defaultTab(s: IdeasSnapshot): IdeaTab {
  return (['active', 'taken', 'skipped'] as const).find((t) => tabCount(s, t) > 0) ?? 'auto'
}

/** Идеи вкладки; ждущие уровня: ближайшие к уровню первыми, без цены в конце. */
export function ideasOf(s: IdeasSnapshot, tab: Exclude<IdeaTab, 'auto'>): IdeaItem[] {
  const rows = s.items.filter((i) => TAB_STATUSES[tab].includes(i.status))
  if (tab !== 'active') return rows
  const d = (i: IdeaItem) => (i.dist_level_pct == null ? Number.POSITIVE_INFINITY : Math.abs(i.dist_level_pct))
  return rows.slice().sort((a, b) => d(a) - d(b))
}

/** Последний замер с ценой после издержек: самый дальний горизонт. */
export function lastOutcome(o: Record<string, IdeaOutcome> | undefined): { h: number; v: number } | null {
  const hs = Object.keys(o ?? {}).filter((h) => o![h].net_pct != null).map(Number).sort((a, b) => b - a)
  return hs.length ? { h: hs[0], v: o![String(hs[0])].net_pct as number } : null
}

/** Замеры по возрастанию горизонта. */
export function outcomeList(o: Record<string, IdeaOutcome> | undefined): Array<{ h: number } & IdeaOutcome> {
  return Object.keys(o ?? {}).map(Number).sort((a, b) => a - b).map((h) => ({ h, ...o![String(h)] }))
}

/** Геометрия полосы идеи: ось от стопа (0) к тейку (1) вне зависимости от стороны; уровень, цена. null: нет стороны, уровня или стопа, либо стоп по ту же сторону, что и тейк. */
export interface IdeaRoute {
  level: number
  price: number | null
  gap: [number, number] | null
  inZone: boolean
}

export function ideaRoute(i: IdeaItem): IdeaRoute | null {
  if (!i.side || i.level == null || i.stop == null) return null
  const sign = i.side === 'long' ? 1 : -1
  const risk = sign * (i.level - i.stop)
  if (!(risk > 0)) return null
  const far = i.tp != null && sign * (i.tp - i.level) > 0 ? i.tp : i.level + sign * risk
  const span = sign * (far - i.stop)
  const at = (v: number) => Math.max(-0.08, Math.min(1.08, (sign * (v - i.stop!)) / span))
  const level = at(i.level)
  const price = i.now_price == null ? null : at(i.now_price)
  return { level, price, gap: price != null && Math.abs(price - level) > 0.004 ? [Math.min(price, level), Math.max(price, level)] : null, inZone: i.zone }
}

/** Риск, награда (% от уровня) и RR без издержек. */
export function ideaRisk(i: IdeaItem): { risk: number | null; reward: number | null; rr: number | null } {
  const pct = (x: number | null) => (i.level && x != null ? Math.abs((x / i.level - 1) * 100) : null)
  const risk = pct(i.stop)
  const reward = pct(i.tp)
  return { risk, reward, rr: risk && reward ? reward / risk : null }
}

/** Куда идти цене: для карточки «ждёт уровня». */
export function levelWait(i: IdeaItem): { text: string; tone: 'near' | 'close' | 'far' | 'none' } {
  const d = i.dist_level_pct
  if (d == null) return { text: 'цены нет', tone: 'none' }
  if (i.zone) return { text: 'у уровня', tone: 'near' }
  return { text: `${d > 0 ? 'снижение' : 'рост'} на ${Math.abs(d).toFixed(2)}%`, tone: Math.abs(d) < 1 ? 'close' : 'far' }
}
