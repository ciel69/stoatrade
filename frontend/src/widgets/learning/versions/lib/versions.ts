import { estView, type EstView, type RulebookPair, type SwapPair, type VersionSide, type VersionVerdict, type VersionsBlock } from '@/entities/learning'
import { reasonWord } from '@/entities/memory'
import { LEARN_SMALL, fixed, learnLevel, plural } from '@/shared/lib'
import type { TipContent } from '@/shared/ui'

/**
 * Слова и числа блока «Версии мозга до/после» (R2.6, Requirement 14.2). Вердикт считает сервер (stages.verdict_of), здесь только слова:
 * «данных мало» при n < 100 в любой стороне, «хуже», если вся разница с интервалом ниже нуля, иначе «не хуже».
 */

const fin = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x)

export const VERSIONS_MIN_N = 100
export const SMALL_N = 30

export type VerdictTone = 'warn' | 'bad' | 'ok'
export const VERSION_VERDICT: Readonly<Record<VersionVerdict, { word: string; tone: VerdictTone; text: (minN: number) => string }>> = {
  few_data: { word: 'данных мало', tone: 'warn', text: (m) => `С одной из сторон меньше ${m} сделок: выводов делать рано.` },
  worse: { word: 'хуже', tone: 'bad', text: () => 'После смены результат ниже, и вся разница с 95% интервалом ниже нуля: это вряд ли случайность.' },
  not_worse: { word: 'не хуже', tone: 'ok', text: () => 'Явного ухудшения нет: разница с 95% интервалом заходит на ноль или выше. Это не значит, что стало лучше.' },
}
const verdictOf = (v: string): (typeof VERSION_VERDICT)[VersionVerdict] => VERSION_VERDICT[v as VersionVerdict] ?? VERSION_VERDICT.few_data

const MONTH = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
/** «3 ноя» по UTC (как недели отчёта). */
export function dayText(ts: number | null | undefined): string {
  if (!fin(ts)) return 'дата неизвестна'
  const d = new Date(ts * 1000)
  return `${d.getUTCDate()} ${MONTH[d.getUTCMonth()]}`
}

export interface SideView {
  /** «120 сделок» */
  n: string
  r: EstView
  viol: EstView
  /** «нарушения по 81 сделке» — сколько сделок с известной отметкой о правилах */
  violBase: string
  /** пометка: мало для любых выводов (n < 30) либо мало для вердикта (n < 100) */
  mark: string
}

export function sideView(s: VersionSide | null | undefined, minN = VERSIONS_MIN_N): SideView {
  const n = s && fin(s.n) ? s.n : 0
  const rf = s && fin(s.n_rf) ? s.n_rf : 0
  return {
    n: `${n} ${plural(n, ['сделка', 'сделки', 'сделок'])}`,
    r: estView(s?.r ?? null, 'r', 2, SMALL_N),
    viol: estView(s?.viol ?? null, 'pct', 0, SMALL_N),
    violBase: rf ? `по ${rf} ${plural(rf, ['сделке', 'сделкам', 'сделкам'])} с отметкой о правилах` : 'отметок о правилах нет',
    mark: n < SMALL_N ? LEARN_SMALL(SMALL_N) : n < minN ? `для вывода нужно ${minN}` : '',
  }
}

export interface PairView {
  key: string
  kind: 'rulebook' | 'model' | 'prompt'
  /** «Правила R-0001 → R-0002», «Смена модели», «Смена промпта» */
  title: string
  /** «3 ноя · добавлено правило · 2 правила» */
  meta: string
  ts: number
  before: SideView
  after: SideView
  diff: EstView
  level: string
  p: string
  verdict: string
  tone: VerdictTone
  verdictTip: TipContent
}

const KIND_TITLE = { model: 'Смена модели', prompt: 'Смена промпта' } as const

function base(p: RulebookPair | SwapPair, minN: number): Omit<PairView, 'key' | 'kind' | 'title' | 'meta'> {
  const v = verdictOf(p.verdict)
  return {
    ts: fin(p.ts) ? p.ts : 0,
    before: sideView(p.before, minN),
    after: sideView(p.after, minN),
    diff: estView(p.diff ?? null, 'r', 2, 0),
    level: learnLevel(p.diff?.lvl),
    p: fin(p.p) ? `p = ${fixed(p.p, p.p < 0.01 ? 3 : 2)}` : '',
    verdict: v.word,
    tone: v.tone,
    verdictTip: { title: `Вывод: ${v.word}`, text: v.text(minN), rows: [{ label: 'Правило вывода', value: `n ≥ ${minN} с обеих сторон, затем верхняя граница разницы` }] },
  }
}

export function rulebookPairView(p: RulebookPair, minN = VERSIONS_MIN_N): PairView {
  const why = reasonWord(p.reason_code)
  const rules = fin(p.rules_n) && p.rules_n > 0 ? `${p.rules_n} ${plural(p.rules_n, ['правило', 'правила', 'правил'])} в наборе` : 'набор без правил'
  return {
    ...base(p, minN),
    key: `rb-${p.from ?? ''}-${p.to ?? ''}`,
    kind: 'rulebook',
    title: `Правила ${p.from ?? '?'} → ${p.to ?? '?'}`,
    meta: [dayText(p.ts), why, rules].filter(Boolean).join(' · '),
  }
}

export function swapPairView(p: SwapPair, minN = VERSIONS_MIN_N): PairView {
  const kind = p.kind === 'prompt' ? 'prompt' : 'model'
  const both = (p.kinds ?? []).length > 1
  const roles = (p.targets ?? []).map((t) => t.replace(/_/g, ' ')).join(', ')
  const to = (p.to ?? []).join(', ')
  return {
    ...base(p, minN),
    key: `sw-${(p.seqs ?? []).join('.')}-${p.ts ?? ''}`,
    kind,
    title: both ? 'Смена модели и промпта' : KIND_TITLE[kind],
    meta: [dayText(p.ts), roles ? `роли: ${roles}` : '', to ? `теперь: ${to}` : ''].filter(Boolean).join(' · '),
  }
}

/** Все смены одной лентой, новые сверху. */
export function pairViews(b: VersionsBlock): PairView[] {
  const minN = fin(b.min_n) ? b.min_n : VERSIONS_MIN_N
  const rows = [...(b.rulebook ?? []).map((p) => rulebookPairView(p, minN)), ...(b.swaps ?? []).map((p) => swapPairView(p, minN))]
  return rows.sort((a, c) => c.ts - a.ts)
}

/** Итог блока в шапке: самое тревожное из вердиктов (хуже > данных мало > не хуже). */
export function versionsSummary(rows: ReadonlyArray<PairView>): { word: string; tone: VerdictTone } | null {
  if (!rows.length) return null
  const worse = rows.filter((r) => r.tone === 'bad').length
  if (worse) return { word: `хуже после ${worse} ${plural(worse, ['смены', 'смен', 'смен'])}`, tone: 'bad' }
  if (rows.every((r) => r.tone === 'warn')) return { word: 'данных мало', tone: 'warn' }
  return { word: 'ухудшений нет', tone: 'ok' }
}

/** Сделки, у которых нет версии правил (до R2), в сравнение версий правил не входят. */
export function preR2Text(n: number | null | undefined): string {
  return fin(n) && n > 0 ? `${n} ${plural(n, ['сделка', 'сделки', 'сделок'])} без версии правил (до включения цикла обучения) в сравнение правил не входят.` : ''
}

export const VERSIONS_INTRO = 'Как менялся результат мозга после смены правил, модели или промпта. Это сравнение «до и после», а не опыт: рынок в эти периоды был разным.'

export const DIFF_TIP: TipContent = { title: 'Разница «после минус до»', text: 'Средний результат сделки после смены минус до, с 95% интервалом. Плюс значит, что после смены сделки в среднем лучше.' }
export const VIOL_TIP: TipContent = { title: 'Нарушения правил', text: 'Доля сделок, где мозг отступил от собственных правил входа (по разбору сделки). Чем ниже, тем дисциплинированнее.' }
export const R_TIP: TipContent = { title: 'Средний R после издержек', text: 'Средний результат сделки в единицах риска с учётом комиссий: +1 R значит заработали столько, сколько готовы были потерять.' }
