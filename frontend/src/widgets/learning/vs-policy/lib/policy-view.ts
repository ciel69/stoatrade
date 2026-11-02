import { estView, plainView, type EstView, type PolicyRow, type VsPolicyBlock } from '@/entities/learning'
import { LEARN_CAVEAT, LEARN_POLICY_SUMMARY, LEARN_POLICY_VERDICT, fixed, learnLevel, plural } from '@/shared/lib'

/** Мозг против теневой политики: готовые к показу строки. Числа приходят с сервера; здесь только слова и подписи. */
export type PolicyTone = 'neutral' | 'ok' | 'warn' | 'bad'

export interface PolicyRowView {
  key: string
  /** название словами: `random_side_baseline` -> «random side baseline» */
  name: string
  /** «P6, версия 1» */
  tag: string
  baseline: boolean
  /** версия контекста менялась в окне: показана самая свежая */
  ctxChanged: boolean
  /** заполненных входов политики */
  filled: string
  fill: string
  r: EstView
  pf: string
  diff: EstView
  /** «p = 0.31» либо «–» */
  p: string
  /** слово вывода и цвет чипа (цвет не единственный носитель: слово есть всегда) */
  verdict: string
  tone: PolicyTone
}

export interface PolicyTableView {
  rows: PolicyRowView[]
  brain: { n: string; r: EstView } | null
  summary: string
  summaryTone: PolicyTone
  caveats: string[]
  /** «поправка Бонферрони на 4 политики: порог p ниже 0.0125» либо пусто, если политик нет */
  correction: string
  level: string
}

const SUMMARY_TONE: Record<string, PolicyTone> = { above_baseline: 'ok', below_baseline: 'bad', unclear: 'neutral', few_data: 'warn' }
const VERDICT_TONE: Record<string, PolicyTone> = { better: 'ok', worse: 'bad', unclear: 'neutral', few_data: 'warn' }

const words = (s: string): string => s.replace(/_/g, ' ')

export function policyRowView(r: PolicyRow, minN: number): PolicyRowView {
  return {
    key: `${r.id}-${r.ver}`,
    name: words(r.name),
    tag: `${r.id}, версия ${r.ver}`,
    baseline: r.baseline,
    ctxChanged: r.ctx_changed,
    filled: `${r.n_filled}`,
    fill: r.fill_rate == null ? '' : `заполнено ${Math.round(r.fill_rate * 100)}%`,
    r: estView(r.r, 'r', 2, minN),
    pf: r.pf == null ? '–' : plainView(r.pf, 2),
    diff: estView(r.diff, 'r', 2, 0),
    p: r.p == null ? '–' : `p = ${fixed(r.p, r.p < 0.01 ? 3 : 2)}`,
    verdict: LEARN_POLICY_VERDICT[r.verdict] ?? LEARN_POLICY_VERDICT.few_data,
    tone: VERDICT_TONE[r.verdict] ?? 'warn',
  }
}

export function policyTableView(b: VsPolicyBlock, minN = 30): PolicyTableView {
  const rows = (b.rows ?? []).map((r) => policyRowView(r, minN))
  const k = b.k ?? rows.length
  const alpha = b.alpha_adj
  const lvl = b.rows?.find((r) => r.diff)?.diff?.lvl
  return {
    rows,
    brain: b.brain ? { n: `${b.brain.n}`, r: estView(b.brain.r, 'r', 2, minN) } : null,
    summary: LEARN_POLICY_SUMMARY[b.summary] ?? LEARN_POLICY_SUMMARY.few_data,
    summaryTone: SUMMARY_TONE[b.summary] ?? 'warn',
    caveats: (b.caveats ?? []).map((c) => LEARN_CAVEAT[c]).filter((x): x is string => !!x),
    correction: k > 0 && typeof alpha === 'number' ? `Поправка Бонферрони на ${k} ${plural(k, ['политику', 'политики', 'политик'])}: значимой считается разница при p ниже ${fixed(alpha, alpha < 0.01 ? 4 : 3)}.` : '',
    level: learnLevel(lvl),
  }
}
