import type { DecisionItem } from '../model/types'

export type ActorFilter = 'all' | 'brain' | 'exec' | 'owner' | 'system'

export interface DecisionFilter {
  actor: ActorFilter
  /** только отказы Gate и ошибки (denied) и пропуски правил (noop) */
  onlyFail: boolean
  plan: string
  /** поиск по подстроке без учёта регистра: монета, действие, причина, код, план */
  q: string
}

/** Исполнитель решения без хвоста: «brain:entry_scout» -> «brain». */
export const actorKey = (actor: string | null | undefined): string => (actor ?? '').split(':')[0]

/** Отказ или пропуск: строки, которые «только отказы» оставляет. */
export const isFail = (d: DecisionItem): boolean => d.denied || d.noop

export function filterDecisions(items: readonly DecisionItem[], f: DecisionFilter): DecisionItem[] {
  const q = f.q.trim().toLowerCase()
  return items.filter((d) => {
    if (f.actor !== 'all' && actorKey(d.actor) !== f.actor) return false
    if (f.onlyFail && !isFail(d)) return false
    if (f.plan && d.plan !== f.plan) return false
    if (q) {
      const hay = [d.action, humanAction(d), d.why, d.code, d.plan, d.what, d.actor, String(d.args.sym ?? '')].join(' ').toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  })
}

const DECISION_RU: Record<string, string> = {
  skip: 'пропуск', propose: 'предложил вход', hold: 'держит', hold_plan: 'придержал план', release_plan: 'отпустил план', move_stop: 'подтянул стоп', close: 'закрыл',
  partial_close: 'закрыл часть', cancel: 'отменил вход', amend: 'поправил план', escalate: 'позвал владельца',
}

/** Читаемое действие: служебные «brain_decision decision=skip sym=SOL» превращаются в «Пропуск SOL»; остальные записи остаются как их описал сервер. */
export function humanAction(d: DecisionItem): string {
  const sym = typeof d.args.sym === 'string' && d.args.sym ? ` ${d.args.sym}` : ''
  const dec = typeof d.args.decision === 'string' ? d.args.decision : ''
  if (d.what === 'brain_decision') return `${DECISION_RU[dec] ?? (dec || 'решение')}${sym}`
  if (d.what === 'brain_why') return `объяснил: ${DECISION_RU[dec] ?? (dec || 'решение')}${sym}`
  if (d.what === 'watch_wake') return `сторож разбудил мозг${typeof d.args.code === 'string' ? `: ${d.args.code}` : ''}`
  return d.action || d.what
}

/** Короткое имя плана: «PL-1030-SOL-01» -> «SOL-01». */
export function planShort(plan: string | null | undefined): string {
  if (!plan) return '–'
  const m = /^PL-\d+-(.+)$/.exec(plan)
  return m ? m[1] : plan
}
