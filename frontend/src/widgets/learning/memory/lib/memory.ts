import { MIN_N, estView, type EstView, type MemoryBlock } from '@/entities/learning'
import { LEARN_SMALL, LEARN_STAGE, learnLevel, signed } from '@/shared/lib'

/** Слова блока «Память против базы». Brier: средний квадрат ошибки вероятности, чем ниже, тем точнее. */
export const MEM_HINT = 'Brier — средняя ошибка прогноза вероятности, чем она ниже, тем лучше. База — прогноз «как в среднем по прошлым сделкам».'
export const MEM_DIFF_LABEL = 'Разность (база минус память)'
export const MEM_DIFF_HINT = 'Больше нуля, если память точнее базы.'
export const MEM_HISTORY = 'История по дням'
export const MEM_COVER_LABEL = 'Покрытие'
export const MEM_COVER_HINT = 'доля сделок, для которых нашлись похожие случаи'

const fin = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x)
const pct = (x: number | null): string => (fin(x) ? `${Math.round(x * 100)}%` : '–')

export type MemoryVerdict = 'few' | 'none' | 'better' | 'worse' | 'same' | 'no_interval'

export interface MemoryBar { key: 'mem' | 'base'; label: string; text: string; ratio: number }

export interface MemoryView {
  bars: MemoryBar[]
  diff: { value: string; interval: string; n: string; level: string }
  verdict: MemoryVerdict
  verdictText: string
  cover: EstView
  /** «52% и 52% верных прогнозов» */
  accuracy: string
  evaluated: string
  small: string
  history: string
}

export function memoryVerdict(b: Pick<MemoryBlock, 'd_brier' | 'n_eval'>, minN = MIN_N): MemoryVerdict {
  const d = b.d_brier
  if (!fin(b.n_eval) || b.n_eval < minN) return 'few'
  if (!d || !fin(d.v)) return 'none'
  if (!fin(d.lo) || !fin(d.hi)) return 'no_interval'
  if (d.lo > 0) return 'better'
  if (d.hi < 0) return 'worse'
  return 'same'
}

export const MEM_VERDICT_TEXT: Readonly<Record<MemoryVerdict, string>> = {
  few: 'Данных мало: сравнивать память с базой рано.',
  none: 'Разность не посчитана.',
  no_interval: 'Интервал разности не определён: вывода нет.',
  better: 'Память точнее базы: интервал разности выше нуля.',
  worse: 'Память хуже базы: интервал разности ниже нуля.',
  same: 'Преимущества памяти не видно: интервал разности включает ноль.',
}

export function memoryView(b: MemoryBlock): MemoryView {
  const vals = [b.brier_mem, b.brier_base].filter(fin)
  const top = vals.length ? Math.max(...vals) * 1.15 : 1         // полоса от нуля: короткая разница остаётся короткой, как есть
  const bar = (key: 'mem' | 'base', label: string, v: number | null): MemoryBar => ({ key, label, text: fin(v) ? v.toFixed(3) : '–', ratio: fin(v) && top > 0 ? Math.min(1, v / top) : 0 })
  const d = b.d_brier
  const has = !!d && fin(d.lo) && fin(d.hi)
  const verdict = memoryVerdict(b)
  return {
    bars: [bar('mem', 'Память', b.brier_mem), bar('base', 'База', b.brier_base)],
    diff: {
      value: d && fin(d.v) ? signed(d.v, 3) : '–',
      interval: has ? `от ${signed(d!.lo, 3)} до ${signed(d!.hi, 3)}` : '',
      n: d ? `n = ${d.n}` : '',
      level: d && learnLevel(d.lvl) ? `уровень ${learnLevel(d.lvl)}` : '',
    },
    verdict,
    verdictText: MEM_VERDICT_TEXT[verdict],
    cover: estView(b.covered, 'pct'),
    accuracy: fin(b.acc_mem) || fin(b.acc_base) ? `память ${pct(b.acc_mem)}, база ${pct(b.acc_base)}` : '–',
    evaluated: `оценено ${fin(b.n_eval) ? b.n_eval : 0} из ${fin(b.n_trades) ? b.n_trades : 0}`,
    small: fin(b.n_eval) && b.n_eval < MIN_N ? LEARN_SMALL(MIN_N) : '',
    history: LEARN_STAGE(b.history?.stage ?? 3),
  }
}
