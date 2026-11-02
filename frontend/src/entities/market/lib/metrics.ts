import { fixed, signed } from '@/shared/lib'
import type { FlowSym, HeatRow, LiqSym } from '../model/types'

/** Всё, что известно о монете на карте: строка тепловой карты, ликвидации и ордерфлоу (есть не у всех). */
export interface CoinData {
  heat: HeatRow
  liq?: LiqSym
  flow?: FlowSym
}

export type MetricId = 'chg_24h_pct' | 'ret_15m_pct' | 'ret_5m_pct' | 'oi_chg_15m_pct' | 'funding_pct' | 'liq' | 'flow'

/** signed: цвет по знаку (зелёный плюс, красный минус); funding: янтарный, только когда перекос заметен. */
export interface Metric {
  id: MetricId
  label: string
  /** значение, при котором цвет достигает полной насыщенности */
  scale: number
  kind: 'signed' | 'funding'
  get: (c: CoinData) => number | null
  text: (v: number) => string
  hint: string
}

/** Сумма в USD кратко: 850, 12K, 3.4M. */
export function kfmt(x: number | null | undefined): string {
  if (typeof x !== 'number' || !Number.isFinite(x) || x === 0) return '0'
  const a = Math.abs(x)
  const s = a >= 1e6 ? `${fixed(a / 1e6, 1)}M` : a >= 1e3 ? `${fixed(a / 1e3, 0)}K` : String(Math.round(a))
  return x < 0 ? `−${s}` : s
}

export const METRICS: Metric[] = [
  { id: 'chg_24h_pct', label: '24 ч', scale: 10, kind: 'signed', get: (c) => c.heat.chg_24h_pct, text: (v) => `${signed(v, Math.abs(v) < 10 ? 2 : 1)}%`, hint: 'Изменение цены за сутки' },
  { id: 'ret_15m_pct', label: '15 мин', scale: 2.5, kind: 'signed', get: (c) => c.heat.ret_15m_pct, text: (v) => `${signed(v)}%`, hint: 'Изменение цены за 15 минут' },
  { id: 'ret_5m_pct', label: '5 мин', scale: 1.5, kind: 'signed', get: (c) => c.heat.ret_5m_pct, text: (v) => `${signed(v)}%`, hint: 'Изменение цены за 5 минут' },
  { id: 'oi_chg_15m_pct', label: 'Откр. интерес', scale: 5, kind: 'signed', get: (c) => c.heat.oi_chg_15m_pct, text: (v) => `${signed(v)}%`, hint: 'Изменение открытого интереса за 15 минут: деньги приходят в рынок или уходят' },
  { id: 'funding_pct', label: 'Фандинг', scale: 0.05, kind: 'funding', get: (c) => c.heat.funding_pct, text: (v) => `${signed(v, 3)}%`, hint: 'Ставка фандинга: заметный перекос выделен янтарным' },
  { id: 'liq', label: 'Ликвидации', scale: 2e5, kind: 'signed', get: (c) => (c.liq ? c.liq.short_1h - c.liq.long_1h : null), text: (v) => (v > 0 ? '+' : '') + kfmt(v), hint: 'За час: выбитые шорты минус выбитые лонги, USD (зелёный: больше выбито шортов)' },
  { id: 'flow', label: 'Ордерфлоу', scale: 40, kind: 'signed', get: (c) => c.flow?.cvd_5m_pct ?? null, text: (v) => `${signed(v, 0)}%`, hint: 'Перекос агрессии покупателей и продавцов за 5 минут (есть у монет с живым потоком)' },
]

export const metricById = (id: string): Metric => METRICS.find((m) => m.id === id) ?? METRICS[0]

/** Сила цвета 0..1 по значению и шкале; кривая выпуклая (степень 0.65), чтобы малые движения на тихом рынке не сливались с фоном. */
export function intensity(v: number | null, m: Metric): number {
  if (v == null || !Number.isFinite(v)) return 0
  if (m.kind === 'funding') return Math.abs(v) >= m.scale * 0.6 ? Math.min(Math.abs(v) / m.scale, 1) ** 0.65 : 0
  return Math.min(Math.abs(v) / m.scale, 1) ** 0.65
}

/** Тон значения: up, down, warn (перекос фандинга) или none. */
export function tone(v: number | null, m: Metric): 'up' | 'down' | 'warn' | 'none' {
  if (v == null || intensity(v, m) === 0) return 'none'
  if (m.kind === 'funding') return 'warn'
  return v > 0 ? 'up' : 'down'
}
