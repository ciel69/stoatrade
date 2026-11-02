import { ageText, duration } from '@/shared/lib'
import type { SysLevel, SysNode, SysRoomRow, SystemSnapshot } from '../model/types'

const RANK: Record<SysLevel, number> = { bad: 3, warn: 2, info: 1, ok: 0, off: 0 }

/** Худший из уровней (для сводки). */
export function worst(levels: Array<SysLevel | null | undefined>): SysLevel {
  return levels.reduce<SysLevel>((a, l) => (l && RANK[l] > RANK[a] ? l : a), 'ok')
}

/** Подпись узла одной строкой: статический текст и «обновлено N назад» (по моменту `at`) или «работает N» (по `since`). */
export function nodeLine(n: SysNode, now: number): string {
  const parts: string[] = []
  if (n.text) parts.push(n.text)
  if (n.at != null) parts.push(`обновлено ${ageText(n.at, now)}`)
  if (n.since != null) parts.push(`работает ${duration(now - n.since)}`)
  return parts.join(' · ') || '–'
}

/** Узлы по важности: сначала проблемные, затем внимание, затем нормальные; внутри группы порядок источника. */
export function sortNodes(nodes: SysNode[]): SysNode[] {
  return nodes.map((n, i) => ({ n, i })).sort((a, b) => RANK[b.n.level] - RANK[a.n.level] || a.i - b.i).map((x) => x.n)
}

/** Короткий вывод для шапки страницы: что с платформой и на что смотреть. */
export function verdict(s: SystemSnapshot): { title: string; detail: string } {
  if (s.kill) return { title: 'Аварийная остановка активна', detail: s.kill.reason ? `Причина: ${s.kill.reason}. Снять: на странице «Система · Управление» или ./tb exec resume в терминале.` : 'Снять: на странице «Система · Управление» или ./tb exec resume в терминале.' }
  const bad = s.nodes.filter((n) => n.level === 'bad' || n.level === 'warn')
  const title = s.level === 'ok' ? 'Всё работает' : s.level === 'warn' ? 'Есть замечания' : 'Нужно внимание'
  const detail = bad.length ? `Проверьте: ${bad.map((n) => n.label).join(', ')}.` : s.warnings.find((w) => w.text)?.text ?? 'Узлы отвечают, данные свежие.'
  return { title, detail }
}

/** Строка запаса: доля остатка для шкалы (стопы подряд считаются от использованного). */
export function roomFill(r: SysRoomRow): number {
  if (!r.limit) return 0
  return r.id === 'stops' ? 1 - (r.used ?? 0) / r.limit : (r.room ?? 0) / r.limit
}

/** Строка запаса словами: «осталось 4.00 из 5.00 USDT». */
export function roomText(r: SysRoomRow): string {
  const f = (x: number | null) => (x == null ? '–' : r.unit === 'USDT' ? x.toFixed(2) : String(Math.round(x)))
  return r.id === 'stops' ? `${f(r.used)} из ${f(r.limit)}` : `осталось ${f(r.room == null ? null : Math.max(0, r.room))} из ${f(r.limit)}${r.unit === 'USDT' ? ' USDT' : ''}`
}
