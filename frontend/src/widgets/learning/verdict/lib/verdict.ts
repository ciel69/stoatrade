import type { Est, VerdictGuards } from '@/entities/learning'
import { LEARN_GUARD_MARK, LEARN_GUARD_TRADES, LEARN_GUARD_VIOL } from '@/shared/lib'

export type GuardState = 'ok' | 'bad' | 'none'
export interface GuardRow { id: 'viol' | 'trades'; state: GuardState; mark: string; text: string }

/** Две защиты вердикта словами: пройдена, не пройдена, не проверялась (ok:null или блока нет). Состояние выражено словом, не только цветом. */
export function guardRows(g: VerdictGuards | null | undefined): GuardRow[] {
  const v = g?.viol ?? null
  const t = g?.trades_day ?? null
  const vs: GuardState = v && typeof v.ok === 'boolean' ? (v.ok ? 'ok' : 'bad') : 'none'
  const ts: GuardState = t && typeof t.ok === 'boolean' ? (t.ok ? 'ok' : 'bad') : 'none'
  const vText = vs === 'none' ? LEARN_GUARD_VIOL.none : v?.guard_unknown ? LEARN_GUARD_VIOL.unknown : LEARN_GUARD_VIOL[vs]
  return [
    { id: 'viol', state: vs, mark: v?.guard_unknown && vs === 'ok' ? LEARN_GUARD_MARK.none : LEARN_GUARD_MARK[vs], text: vText },
    { id: 'trades', state: ts, mark: LEARN_GUARD_MARK[ts], text: LEARN_GUARD_TRADES[ts] },
  ]
}

export interface RangeGeom {
  /** координаты в пикселях полосы шириной w */
  zero: number
  lo: number
  hi: number
  v: number
  /** интервал целиком по одну сторону от нуля */
  clear: boolean
}

/**
 * Положение разности и её интервала на оси с нулём посередине. Ось симметрична: нуль всегда в центре, масштаб по самой дальней границе.
 * Без границ (n мало) интервала нет: lo = hi = v. Возвращает null, если значения нет.
 */
export function rangeGeom(e: Est | null | undefined, w: number, pad = 8): RangeGeom | null {
  if (!e || typeof e.v !== 'number' || !Number.isFinite(e.v)) return null
  const has = typeof e.lo === 'number' && typeof e.hi === 'number'
  const lo = has ? Math.min(e.lo as number, e.hi as number) : e.v
  const hi = has ? Math.max(e.lo as number, e.hi as number) : e.v
  const reach = Math.max(Math.abs(lo), Math.abs(hi), Math.abs(e.v), 1e-9) * 1.1
  const half = w / 2 - pad
  const x = (a: number): number => w / 2 + (a / reach) * half
  return { zero: w / 2, lo: x(lo), hi: x(hi), v: x(e.v), clear: has && (lo > 0 || hi < 0) }
}
