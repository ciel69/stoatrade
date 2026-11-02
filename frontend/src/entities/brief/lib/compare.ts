import { regimeWord } from '@/shared/lib'
import type { Brief } from '../model/types'
import { coinStat, hotRows } from './brief'

/**
 * Сравнение досье с предыдущим: что существенно изменилось человеческими словами и какие блоки пометить.
 * Сравниваются только значимые поля (белый список): позиции, планы, лимиты и запас риска, режим и риск-офф рынка, структура, горячие монеты, идеи, события.
 * Возраст, время, цены, проценты и прочий шум не сравниваются. Блок сравнивается, только если он есть в обоих досье (событийное досье неполное, отсутствие блока не «исчезновение»).
 */
export type BlockId = 'acc' | 'pos' | 'pl' | 'mkt' | 'st' | 'lim' | 'hot' | 'id' | 'ev'
export type Mark = 'changed' | 'new'

export interface BriefDiff {
  /** выводы сверху, по важности: позиции, запас риска, рынок, остальное */
  lines: string[]
  marks: Partial<Record<BlockId, Mark>>
  /** ключи элементов, которых не было в прошлом досье (для бейджа «новое» у самой строки) */
  fresh: { pos: Set<string>; pl: Set<string>; hot: Set<string>; id: Set<string> }
}

/** Ключ позиции: план, иначе монета со стороной. */
export const posKey = (p: { plan?: string; sym: string; side: string }): string => p.plan ?? `${p.sym}:${p.side}`

const EPS = 0.01
const differs = (a: number | null | undefined, b: number | null | undefined, eps = EPS): boolean => a != null && b != null && Math.abs(a - b) >= eps
const listSyms = (xs: string[], max = 4): string => (xs.length > max ? `${xs.slice(0, max).join(', ')} и ещё ${xs.length - max}` : xs.join(', '))
const regime = (r: string | null | undefined): string => (r ? (regimeWord(r) !== '–' ? regimeWord(r) : r) : '–')
const usd = (v: number): string => `${(Math.round(v * 100) / 100).toString()} USDT`

function setMark(m: BriefDiff['marks'], id: BlockId, v: Mark): void {
  if (m[id] !== 'changed') m[id] = v
}

export function compareBriefs(prev: Brief | null | undefined, cur: Brief): BriefDiff {
  const out: BriefDiff = { lines: [], marks: {}, fresh: { pos: new Set(), pl: new Set(), hot: new Set(), id: new Set() } }
  if (!prev) return out
  const { lines, marks, fresh } = out

  // позиции
  if (prev.positions && cur.positions) {
    const before = new Map(prev.positions.map((p) => [posKey(p), p]))
    const after = new Map(cur.positions.map((p) => [posKey(p), p]))
    const opened = cur.positions.filter((p) => !before.has(posKey(p)))
    const closed = prev.positions.filter((p) => !after.has(posKey(p)))
    const moved = cur.positions.filter((p) => { const o = before.get(posKey(p)); return o != null && differs(o.sl, p.sl, 1e-9) })
    opened.forEach((p) => fresh.pos.add(posKey(p)))
    if (prev.positions.length !== cur.positions.length) lines.push(`Позиций стало ${cur.positions.length} вместо ${prev.positions.length}.`)
    if (opened.length) lines.push(`Открыта позиция: ${listSyms(opened.map((p) => `${p.sym} ${p.side === 'long' ? 'лонг' : 'шорт'}`))}.`)
    if (closed.length) lines.push(`Позиция закрыта или пропала: ${listSyms(closed.map((p) => p.sym))}.`)
    if (moved.length) lines.push(`Стоп сдвинут: ${listSyms(moved.map((p) => p.sym))}.`)
    if (opened.length || closed.length || moved.length) setMark(marks, 'pos', prev.positions.length === 0 ? 'new' : 'changed')
  }

  // планы
  if (prev.plans && cur.plans) {
    const before = new Map(prev.plans.map((p) => [p.id, p]))
    const after = new Set(cur.plans.map((p) => p.id))
    const added = cur.plans.filter((p) => !before.has(p.id))
    const gone = prev.plans.filter((p) => !after.has(p.id))
    const status = cur.plans.filter((p) => { const o = before.get(p.id); return o != null && o.status != null && p.status != null && o.status !== p.status })
    added.forEach((p) => fresh.pl.add(p.id))
    if (prev.plans.length !== cur.plans.length) lines.push(`Планов стало ${cur.plans.length} вместо ${prev.plans.length}.`)
    if (added.length) lines.push(`Новый план: ${listSyms(added.map((p) => p.sym))}.`)
    if (gone.length) lines.push(`План ушёл из списка: ${listSyms(gone.map((p) => p.sym))}.`)
    if (status.length) lines.push(`Сменился статус плана: ${listSyms(status.map((p) => `${p.sym} (${before.get(p.id)!.status} → ${p.status})`), 3)}.`)
    if (added.length || gone.length || status.length) setMark(marks, 'pl', prev.plans.length === 0 ? 'new' : 'changed')
  }

  // запас риска: можно ли входить, слоты
  if (prev.room && cur.room) {
    if (prev.room.allowed != null && cur.room.allowed != null && prev.room.allowed !== cur.room.allowed) {
      lines.push(cur.room.allowed ? 'Входы снова разрешены.' : `Входы теперь запрещены${cur.room.reasons?.length ? `: ${cur.room.reasons.join('; ')}` : ''}.`)
      setMark(marks, 'acc', 'changed')
    }
    if (prev.room.slots_free != null && cur.room.slots_free != null && prev.room.slots_free !== cur.room.slots_free) {
      lines.push(`Свободных слотов стало ${cur.room.slots_free} вместо ${prev.room.slots_free}.`)
      setMark(marks, 'acc', 'changed')
    }
  }

  // лимиты: запас до дневного лимита, риск, уровень
  const dl = [prev.limits?.daily_left_usdt ?? prev.room?.daily_room, cur.limits?.daily_left_usdt ?? cur.room?.daily_room] as const
  if (differs(dl[0], dl[1])) {
    lines.push(`Запас до дневного лимита ${dl[1]! < dl[0]! ? 'уменьшился' : 'вырос'}: ${usd(dl[1]!)} вместо ${usd(dl[0]!)}.`)
    setMark(marks, cur.limits?.daily_left_usdt != null ? 'lim' : 'acc', 'changed')
  }
  if (prev.limits && cur.limits) {
    const l0 = prev.limits
    const l1 = cur.limits
    if (differs(l0.risk_per_trade_usdt, l1.risk_per_trade_usdt)) { lines.push(`Риск на сделку теперь ${usd(l1.risk_per_trade_usdt!)} вместо ${usd(l0.risk_per_trade_usdt!)}.`); setMark(marks, 'lim', 'changed') }
    if (l0.max_open_positions != null && l1.max_open_positions != null && l0.max_open_positions !== l1.max_open_positions) { lines.push(`Лимит открытых позиций теперь ${l1.max_open_positions} вместо ${l0.max_open_positions}.`); setMark(marks, 'lim', 'changed') }
    if (differs(l0.size_mult, l1.size_mult)) { lines.push(`Множитель размера теперь ×${l1.size_mult} вместо ×${l0.size_mult}.`); setMark(marks, 'lim', 'changed') }
  }
  if (prev.risk_level?.level != null && cur.risk_level?.level != null && prev.risk_level.level !== cur.risk_level.level) {
    lines.push(`Уровень риска изменился: ${cur.risk_level.level} вместо ${prev.risk_level.level}.`)
    setMark(marks, 'lim', 'changed')
  }

  // рынок: режим BTC и ETH, риск-офф, окно новостей
  if (prev.market && cur.market) {
    const s0 = prev.struct?.s ?? {}
    const s1 = cur.struct?.s ?? {}
    const changed: string[] = []
    for (const [name, a, b] of [['BTC', prev.market.btc, cur.market.btc], ['ETH', prev.market.eth, cur.market.eth]] as const) {
      const r0 = coinStat(a, s0[name]).regime
      const r1 = coinStat(b, s1[name]).regime
      if (r0 && r1 && r0 !== r1) changed.push(`${name}: ${regime(r0)} → ${regime(r1)}`)
    }
    if (changed.length) lines.push(`Режим рынка сменился (${changed.join('; ')}).`)
    if (prev.market.risk_off != null && cur.market.risk_off != null && prev.market.risk_off !== cur.market.risk_off) {
      lines.push(cur.market.risk_off ? 'Включился режим риск-офф: рынок уходит от риска.' : 'Режим риск-офф закончился.')
      changed.push('risk_off')
    }
    if (Boolean(prev.market.blackout) !== Boolean(cur.market.blackout) && (prev.market.blackout !== undefined || cur.market.blackout !== undefined)) {
      lines.push(cur.market.blackout ? `Появилось окно новостей: ${cur.market.blackout.event}.` : 'Окно новостей закончилось.')
      changed.push('blackout')
    }
    if (changed.length) setMark(marks, 'mkt', 'changed')
  }

  // структура: режимы остальных монет
  if (prev.struct?.s && cur.struct?.s) {
    const diff = Object.keys(cur.struct.s).filter((k) => {
      const r0 = prev.struct!.s![k]?.reg
      const r1 = cur.struct!.s![k]?.reg
      return r0 && r1 && r0 !== r1
    })
    if (diff.length) {
      const rest = diff.filter((k) => k !== 'BTC' && k !== 'ETH')
      if (rest.length) lines.push(`Режим сменился у монет: ${listSyms(rest)}.`)
      setMark(marks, 'st', 'changed')
    }
  }

  // горячие монеты
  if (prev.hot !== undefined && cur.hot !== undefined) {
    const before = new Set(hotRows(prev).map((h) => h.sym))
    const now = hotRows(cur)
    const added = now.filter((h) => !before.has(h.sym))
    const gone = [...before].filter((s) => !now.some((h) => h.sym === s))
    added.forEach((h) => fresh.hot.add(h.sym))
    if (added.length) lines.push(`Появились новые горячие монеты: ${listSyms(added.map((h) => h.sym))}.`)
    if (gone.length) lines.push(`Из горячих ушли: ${listSyms(gone)}.`)
    if (added.length || gone.length) setMark(marks, 'hot', before.size === 0 ? 'new' : 'changed')
  }

  // идеи
  if (prev.ideas?.active && cur.ideas?.active) {
    const before = new Set(prev.ideas.active.map((i) => i.id))
    const after = new Set(cur.ideas.active.map((i) => i.id))
    const added = cur.ideas.active.filter((i) => !before.has(i.id))
    const gone = prev.ideas.active.filter((i) => !after.has(i.id))
    added.forEach((i) => fresh.id.add(i.id))
    if (added.length) lines.push(`Новые идеи: ${listSyms(added.map((i) => i.sym))}.`)
    if (gone.length) lines.push(`Идей убрано: ${gone.length}.`)
    if (added.length || gone.length) setMark(marks, 'id', before.size === 0 ? 'new' : 'changed')
  }

  // события
  if (prev.recent && cur.recent) {
    const last = Math.max(0, ...prev.recent.map((e) => e.seq))
    const n = cur.recent.filter((e) => e.seq > last).length
    if (n) {
      lines.push(`Новых событий с прошлого досье: ${n}.`)
      setMark(marks, 'ev', 'changed')
    }
  }

  return out
}
