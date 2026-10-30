import type { DecisionRow } from '../model/types'

/** Сколько секунд мысль остаётся на виду; за это время она плавно тускнеет и исчезает (в журнале решений остаётся навсегда). */
export const FADE_S = 90
export const MAX_THOUGHTS = 3

/** Коды, которые описывают работу машинерии и детерминированные отказы, а не ход мысли (состояние мозга показывает его индикатор). */
const NOISE = new Set(['BAD_FORMAT', 'PROTECT_ONLY', 'NOT_PAPER', 'IDLE_NO_PLAN', 'DOSSIER', 'STALE', 'REPEAT_DECISION', 'ROLE_BUDGET', 'JOB_BUDGET', 'LLM_DOWN', 'TIMEOUT', 'EMPTY', 'TRUNCATED', 'LOOP', 'MAX_ROUNDS',
  'SYM_BUSY', 'NO_SLOTS', 'ROOM_CLOSED', 'PAUSE_SYM', 'PAUSE_ALL', 'MIN_SIZE_SYM', 'WIDE_RANGE', 'DAY_NO_PLANS'])      // отказы по данным досье до вызова модели: правило, а не мысль
const ACTIONS = new Set(['move_stop', 'close', 'partial_close', 'cancel', 'amend', 'escalate', 'hold_plan', 'release_plan'])

export interface Thought {
  row: DecisionRow
  /** возраст, с */
  age: number
  /** прозрачность 0.1..1 по возрасту: свежая яркая, старая растворяется */
  opacity: number
}

/** Ценность мысли для владельца: действие или объяснение действия важнее разбора пропуска входа, «держу» важнее всего прочего шума. 0: не показывать. */
export function importance(r: DecisionRow): number {
  if (!r.text || !r.text.trim()) return 0
  if (r.code && NOISE.has(r.code)) return 0
  if (r.what === 'brain_why') return 4
  if (r.decision && ACTIONS.has(r.decision)) return 4
  if (r.ok === false) return 2
  if (r.decision === 'hold') return 1
  return 3                     // разбор входа: почему вошёл или пропустил (по нему видно, в ту ли сторону думает мозг)
}

export function opacityAt(age: number, fadeS = FADE_S): number {
  if (age >= fadeS) return 0
  return Math.max(0.1, 1 - Math.pow(Math.max(0, age) / fadeS, 1.5))
}

/** Не больше max мыслей моложе fadeS: сначала по ценности, потом по свежести; на выходе от новой к старой. Одинаковый текст не повторяется. */
export function pickThoughts(rows: DecisionRow[], nowS: number, max = MAX_THOUGHTS, fadeS = FADE_S): Thought[] {
  const seen = new Set<string>()
  const live: Thought[] = []
  for (const r of rows) {
    const age = nowS - r.t
    const imp = importance(r)
    if (!imp || age >= fadeS || age < -5 || seen.has(r.text as string)) continue
    seen.add(r.text as string)
    live.push({ row: r, age: Math.max(0, age), opacity: opacityAt(age, fadeS) })
  }
  live.sort((a, b) => importance(b.row) - importance(a.row) || a.age - b.age)
  return live.slice(0, max).sort((a, b) => a.age - b.age)
}
