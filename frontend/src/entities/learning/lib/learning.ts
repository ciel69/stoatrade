import {
  LEARN_BLOCK_ERROR, LEARN_BLOCK_TITLE, LEARN_COMPUTING, LEARN_COMPUTING_TIMEOUT, LEARN_REPORT_FAILED, LEARN_SMALL, LEARN_STAGE, LEARN_TREND_DOWN, LEARN_VERDICT_ABOUT,
  LEARN_VERDICT_WORD, ageText, fixed, learnReason, plural, signed,
} from '@/shared/lib'
import { BLOCK_STATUSES, isLearningMode, type BlockStatus, type Est, type LearningBlock, type LearningDetail, type LearningMode, type LearningVerdict, type VerdictState } from '../model/types'

export const SCHEMA = 'tb.learning/1'
/** Порог числа сделок, с которого значение считается проверяемым (как min_n сервера). */
export const MIN_N = 30
/** Через сколько секунд блок «Считается…» сдаётся (сервер ждёт готовности до 30 с). */
export const COMPUTING_GIVE_UP_S = 35

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v)
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const VERDICT_STATES: readonly string[] = ['few_data', 'no_difference', 'confirmed']

/** Порядок блоков, если сервер его не прислал. */
export const DEFAULT_ORDER: readonly string[] = ['result', 'vs_policy', 'discipline', 'skips', 'exits', 'economics', 'memory', 'lessons', 'versions', 'hyps']

function failed(mode: LearningMode, error: string): LearningDetail {
  return {
    schema: SCHEMA, ok: false, mode, asof: null, error, min_n: MIN_N, alpha: 0.05,
    verdict: { state: 'few_data', reason: 'n_lt_min', text: '', n: 0, n_recent: null, n_prev: null, diff_r: null, p: null, alpha_adj: 0.05, trend: null, guards: null },
    blocks: {}, order: [], meta: { built_ms: null, cached: false, excluded: {}, warnings: [], sources: {} },
  }
}

function normBlock(id: string, raw: unknown): LearningBlock {
  const b = isObj(raw) ? raw : {}
  const st = b.status
  const known = typeof st === 'string' && (BLOCK_STATUSES as readonly string[]).includes(st)
  const title = typeof b.title === 'string' && b.title ? b.title : (LEARN_BLOCK_TITLE[id] ?? id)
  const note = typeof b.note === 'string' ? b.note : ''
  // Неизвестный статус не ломает страницу: блок становится «не удалось показать».
  if (!known) return { ...b, id, title, status: 'error', note, reason: 'E_BLOCK' }
  return { ...b, id, title, status: st as BlockStatus, note, reason: typeof b.reason === 'string' ? b.reason : null }
}

function normVerdict(raw: unknown): LearningVerdict {
  const v = isObj(raw) ? raw : {}
  const state = (typeof v.state === 'string' && VERDICT_STATES.includes(v.state) ? v.state : 'few_data') as VerdictState
  const trend = v.trend === 'up' || v.trend === 'flat' || v.trend === 'down' ? v.trend : null
  return {
    state, reason: typeof v.reason === 'string' ? v.reason : '', text: typeof v.text === 'string' ? v.text : '', n: num(v.n) ?? 0,
    n_recent: num(v.n_recent), n_prev: num(v.n_prev), diff_r: isObj(v.diff_r) ? (v.diff_r as unknown as Est) : null, p: num(v.p), alpha_adj: num(v.alpha_adj) ?? 0.05, trend,
    guards: isObj(v.guards) ? (v.guards as unknown as LearningVerdict['guards']) : null, params: isObj(v.params) ? (v.params as Record<string, number>) : null,
  }
}

/**
 * Приведение ответа сервера к виду. `fallbackMode` нужен для сбоя без режима.
 * Не объект -> null; `ok:false` или чужая схема -> отчёт в состоянии «не удалось собрать» (без исключений);
 * неизвестный статус блока -> блок со статусом error; порядок блоков дополняется неупомянутыми.
 */
export function normalizeLearning(raw: unknown, fallbackMode: LearningMode = 'paper'): LearningDetail | null {
  if (!isObj(raw)) return null
  const mode = isLearningMode(raw.mode) ? raw.mode : fallbackMode
  if (raw.ok === false) return failed(mode, typeof raw.error === 'string' ? raw.error : 'failed')
  if (raw.schema !== SCHEMA) return failed(mode, 'schema')
  const blocksIn = isObj(raw.blocks) ? raw.blocks : {}
  const blocks: Record<string, LearningBlock> = {}
  for (const id of Object.keys(blocksIn)) blocks[id] = normBlock(id, blocksIn[id])
  const given = Array.isArray(raw.order) ? raw.order.filter((x): x is string => typeof x === 'string') : []
  const order = [...given.filter((id) => id in blocks), ...Object.keys(blocks).filter((id) => !given.includes(id))]
  const meta = isObj(raw.meta) ? raw.meta : {}
  return {
    schema: SCHEMA, ok: true, mode, asof: num(raw.asof), min_n: num(raw.min_n) ?? MIN_N, alpha: num(raw.alpha) ?? 0.05, verdict: normVerdict(raw.verdict), blocks, order,
    meta: {
      built_ms: num(meta.built_ms), cached: meta.cached === true, excluded: isObj(meta.excluded) ? (meta.excluded as Record<string, number>) : {},
      warnings: Array.isArray(meta.warnings) ? meta.warnings.filter((x): x is string => typeof x === 'string') : [], sources: isObj(meta.sources) ? meta.sources : {},
    },
  }
}

export type LearningState = 'link' | 'loading' | 'failed' | 'ok'

/** Что показать вместо страницы: нет связи, скелет, сбой сборки либо данные. Устаревший ответ при оборванной связи остаётся (его возраст виден по asof). */
export function learningState(d: LearningDetail | null, pending: boolean, linkOpen: boolean): LearningState {
  if (d && !pending) return d.ok ? 'ok' : 'failed'
  return linkOpen ? 'loading' : 'link'
}

export const failedText = (): string => LEARN_REPORT_FAILED

/** «обновлено 3 мин назад»; без asof пусто. */
export function updatedText(asof: number | null | undefined, nowSeconds: number): string {
  return typeof asof === 'number' ? `обновлено ${ageText(asof, nowSeconds)}` : ''
}
/** Отчёт устарел: старше 5 минут. */
export const isStale = (asof: number | null | undefined, nowSeconds: number): boolean => typeof asof === 'number' && nowSeconds - asof > 300

export type Tone = 'pos' | 'neg' | ''
export const toneOf = (x: number | null | undefined): Tone => (typeof x !== 'number' || x === 0 ? '' : x > 0 ? 'pos' : 'neg')

/** Мало ли данных у оценки. */
export const isSmall = (e: Pick<Est, 'n'> | null | undefined, minN = MIN_N): boolean => !e || e.n < minN

export interface EstView {
  /** главное число: «+0.12 R», «54%», «–» */
  value: string
  /** интервал: «от −0.51 до +0.21», пусто, если не определён */
  interval: string
  /** «n = 38» */
  n: string
  /** «мало данных (n < 30)» либо пусто */
  small: string
  tone: Tone
}

export type EstKind = 'r' | 'pct' | 'plain'

function fmt(x: number | null | undefined, kind: EstKind, digits: number, sign: boolean): string {
  if (typeof x !== 'number' || !Number.isFinite(x)) return '–'
  if (kind === 'pct') return `${Math.round(x * 100)}%`
  const s = sign ? signed(x, digits) : fixed(x, digits)
  return kind === 'r' ? `${s} R` : s
}

/** Оценка словами: число, интервал, n и пометка «мало данных». Интервал не придумывается: нет границ, нет и строки. */
export function estView(e: Est | null | undefined, kind: EstKind = 'r', digits = 2, minN = MIN_N): EstView {
  if (!e) return { value: '–', interval: '', n: '', small: '', tone: '' }
  const has = typeof e.lo === 'number' && typeof e.hi === 'number'
  const sign = kind === 'r'
  // Единица (R, %) стоит один раз: у значения, а границы интервала даются числами; для процентов знак % нужен у обеих границ.
  const edge = (x: number | null): string => (kind === 'r' ? signed(x, digits) : fmt(x, kind, digits, false))
  return {
    value: fmt(e.v, kind, digits, sign),
    interval: has ? `от ${edge(e.lo)} до ${edge(e.hi)}` : '',
    n: `n = ${e.n}`,
    small: e.n < minN ? LEARN_SMALL(minN) : '',
    tone: kind === 'r' ? toneOf(e.v) : '',
  }
}

/** Расчётная величина без интервала (отношение сумм, токены): число и n. */
export function plainView(x: number | null | undefined, digits = 2): string {
  return fmt(x, 'plain', digits, false)
}

export type BlockKind = 'data' | 'stage' | 'empty' | 'computing' | 'gave_up' | 'error'

export interface BlockView {
  kind: BlockKind
  /** слова вместо данных; для data с few_data пусто, подпись «мало данных» идёт у чисел */
  message: string
  /** несколько данных, но показываем с пометкой */
  few: boolean
}

/** Как показывать блок: данные, заглушка этапа, «данных нет» с причиной словами, «считается», «не дождались», ошибка. */
export function blockView(b: LearningBlock, computingForS = 0): BlockView {
  switch (b.status) {
    case 'ok': return { kind: 'data', message: '', few: false }
    case 'few_data': return { kind: 'data', message: '', few: true }
    case 'stage': return { kind: 'stage', message: LEARN_STAGE(typeof b.stage === 'number' ? b.stage : null), few: false }
    case 'no_data': return { kind: 'empty', message: learnReason(b.reason), few: false }
    case 'computing':
      return computingForS > COMPUTING_GIVE_UP_S ? { kind: 'gave_up', message: LEARN_COMPUTING_TIMEOUT, few: false } : { kind: 'computing', message: LEARN_COMPUTING, few: false }
    default: return { kind: 'error', message: LEARN_BLOCK_ERROR, few: false }
  }
}

export interface VerdictView {
  state: VerdictState
  word: string
  /** пояснение: текст сервера, а если его нет — запасная фраза */
  text: string
  n: string
  /** разность недавних и прежних: «+0.21 R, от −0.10 до +0.52 (95%)», пусто если не считалась */
  diff: string
  /** предупреждение «стало хуже» либо пусто */
  warn: string
  /** причина словами для состояний без вывода */
  reason: string
}

export function verdictView(v: LearningVerdict): VerdictView {
  const e = v.diff_r ? estView(v.diff_r, 'r', 2, 0) : null
  const lvl = v.diff_r && v.diff_r.lvl > 0 && v.diff_r.lvl < 1 ? ` (уровень ${Math.round(v.diff_r.lvl * 100)}%)` : ''
  const diff = e && v.diff_r?.v != null ? `${e.value}${e.interval ? `, ${e.interval}${lvl}` : ''}` : ''
  return {
    state: v.state,
    word: LEARN_VERDICT_WORD[v.state],
    text: v.text || LEARN_VERDICT_ABOUT[v.state],
    n: `${v.n} ${plural(v.n, ['сделка', 'сделки', 'сделок'])}`,
    diff,
    warn: v.trend === 'down' ? LEARN_TREND_DOWN : '',
    reason: v.reason ? learnReason(v.reason) : '',
  }
}
