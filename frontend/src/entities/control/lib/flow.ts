import { humanize, plainify, stepLabel } from '@/shared/lib'
import type { ChallengeData, CtlSnapshot, DayStepRow } from '../model/types'

/** Шаги запуска дня в ctl (lifecycle): S1..S9. Имена шагов служебные, человеку показываются только подписи из общего словаря. */
export const START_STEPS: ReadonlyArray<{ name: string; label: string; region: number }> = [
  { name: 'S1', label: stepLabel('S1'), region: 0 },
  { name: 'S2', label: stepLabel('S2'), region: 0 },
  { name: 'S3', label: stepLabel('S3'), region: 1 },
  { name: 'S4', label: stepLabel('S4'), region: 1 },
  { name: 'S5', label: stepLabel('S5'), region: 2 },
  { name: 'S6', label: stepLabel('S6'), region: 2 },
  { name: 'S7', label: stepLabel('S7'), region: 3 },
  { name: 'S8', label: stepLabel('S8'), region: 3 },
  { name: 'S9', label: stepLabel('S9'), region: 3 },
]

/** Ожидаемая длительность шагов, с (S5 и S6 идут через LLM и занимают основное время): вес шага на шкале и постоянная времени его плавного хода. */
const STEP_SECONDS: Record<string, number> = { S1: 2, S2: 4, S3: 3, S4: 3, S5: 25, S6: 18, S7: 2, S8: 2, S9: 4 }

/**
 * Доля хода запуска 0..1: готовые шаги целиком, текущий растёт по затухающей кривой от времени в нём (никогда не доходит до конца, пока ctl не отметит шаг),
 * поэтому шкала идёт ровно и не стоит на долгих LLM-шагах, а потом не прыгает.
 */
export function launchFill(rows: readonly StepRow[], secondsInStep: number): number {
  const w = (n: string): number => STEP_SECONDS[n] ?? 3
  const total = rows.reduce((a, r) => a + w(r.name), 0)
  if (!total) return 0
  let done = rows.reduce((a, r) => a + (r.state === 'ok' ? w(r.name) : 0), 0)
  const cur = rows.find((r) => r.state === 'run')
  if (cur) done += w(cur.name) * 0.9 * (1 - Math.exp(-Math.max(0, secondsInStep) / w(cur.name)))
  return Math.min(1, done / total)
}

/** Названия областей коры в модалке запуска (индексы как у BRAIN_REGIONS). */
export const REGION_LABELS = ['проверки', 'сверка и досье', 'план дня', 'торговля']

export const CTL_STEP_LABEL: Record<string, string> = { switch: 'переключение режима', recheck: 'повторная проверка условий', day: 'запуск дня', stop: 'остановка дня' }

export type StepState = 'todo' | 'run' | 'ok' | 'bad'

export interface StepRow {
  name: string
  label: string
  region: number
  state: StepState
  code: string | null
}

/** Шаги запуска с состоянием: записанные ctl берутся как есть, первый ещё не записанный идёт сейчас (только пока запуск действительно идёт). */
export function stepRows(steps: readonly DayStepRow[] | null | undefined, starting: boolean): StepRow[] {
  const by = new Map<string, DayStepRow>()
  for (const s of steps ?? []) if (s.phase === 'start' || s.phase == null) by.set(s.name, s)
  let running = false
  return START_STEPS.map((d) => {
    const s = by.get(d.name)
    let state: StepState = 'todo'
    if (s) {
      state = s.ok === true ? 'ok' : s.ok === false ? 'bad' : 'run'
      if (state === 'run') running = true
    } else if (starting && !running && by.size > 0) {
      state = 'run'
      running = true
    } else if (starting && !running && by.size === 0 && d.name === 'S1') {
      state = 'run'
      running = true
    }
    return { name: d.name, label: d.label, region: d.region, state, code: s?.code ?? null }
  })
}

/** Сколько шагов области готово (0..1) и идёт ли в ней сейчас шаг. */
export function regionProgress(rows: readonly StepRow[], regions = REGION_LABELS.length): Array<{ done: number; running: boolean }> {
  return Array.from({ length: regions }, (_, r) => {
    const mine = rows.filter((x) => x.region === r)
    const ok = mine.filter((x) => x.state === 'ok').length
    return { done: mine.length ? ok / mine.length : 0, running: mine.some((x) => x.state === 'run') }
  })
}

/** Какие нейроны подсвечены: в каждой области доля нейронов равна доле готовых шагов (нейроны зажигаются по мере прохождения проверок). */
export function litNodes(progress: ReadonlyArray<{ done: number }>, regions: ReadonlyArray<readonly number[]>): number[] {
  const out: number[] = []
  progress.forEach((p, r) => {
    const nodes = regions[r] ?? []
    out.push(...nodes.slice(0, Math.round(p.done * nodes.length)))
  })
  return out
}

/** Что владелец должен принять перед стартом: мягкие предупреждения ctl (warnings) и жёлтые предстартовые проверки (pre_amber). */
export function acceptList(ch: ChallengeData | null | undefined): Array<{ id: string; msg: string; kind: 'warning' | 'amber' }> {
  const out: Array<{ id: string; msg: string; kind: 'warning' | 'amber' }> = []
  for (const w of ch?.warnings ?? []) {
    const h = humanize({ kind: 'warning', id: w.id, code: w.code, msg: w.msg })
    out.push({ id: w.id, msg: /не умеем объяснить/.test(h.title) ? (plainify(w.msg) ?? h.title) : [h.title, h.what].filter(Boolean).join('. '), kind: 'warning' })
  }
  ;(ch?.pre_amber ?? []).forEach((a, i) => {
    const h = humanize({ kind: 'gate', id: a.id ?? '', name: a.name, ok: false, msg: a.msg, sev: 'amber' })
    out.push({ id: a.id ?? `amber-${i}`, msg: /не умеем объяснить/.test(h.title) ? (plainify([a.name, a.msg].filter(Boolean).join(': ')) ?? 'Есть замечание к запуску') : h.title, kind: 'amber' })
  })
  return out
}

/** Можно ли нажимать «Начать»: условия не блокируют, всё принято. */
export function canStart(ch: ChallengeData | null | undefined, accepted: ReadonlySet<string>): boolean {
  if (!ch || ch.blocked || ch.danger) return false
  return acceptList(ch).every((a) => accepted.has(a.id))
}

/** Аргументы команды старта: принятые предупреждения и признак принятия жёлтых проверок (как у старого дашборда). */
export function startArgs(mode: string, ch: ChallengeData | null | undefined): { mode: string; session: string; accepted: string[]; allow_amber: boolean } {
  return {
    mode,
    session: 'day',
    accepted: (ch?.warnings ?? []).map((w) => w.id),
    allow_amber: (ch?.pre_amber ?? []).length > 0,
  }
}

/** Состояние кнопки старта по теме ctl и hello. */
export type ButtonKind = 'start' | 'starting' | 'running' | 'closing' | 'blocked' | 'unavailable'

export function buttonKind(ctl: CtlSnapshot | null, enabled: boolean): { kind: ButtonKind; why: string | null } {
  if (!ctl) return { kind: 'unavailable', why: 'Платформа пока не прислала данные.' }
  if (!enabled) return { kind: 'unavailable', why: 'Запуск и остановка из этого дашборда выключены. Включает их владелец в настройках дашборда.' }
  if (!ctl.alive) return { kind: 'unavailable', why: 'Служба управления не отвечает. Платформа, возможно, не запущена: запустите её в терминале.' }
  if (ctl.kill) return { kind: 'blocked', why: 'Включена аварийная остановка. Снять её можно только в терминале платформы.' }
  const st = ctl.platform.state
  if (st === 'starting') return { kind: 'starting', why: null }
  if (st === 'running') return { kind: 'running', why: null }
  if (st === 'closing') return { kind: 'closing', why: null }
  if (st === 'halted') return { kind: 'blocked', why: 'Платформа остановилась аварийно. Разберитесь с причиной и запустите её заново.' }
  if (st === 'stopped') return { kind: 'start', why: null }
  return { kind: 'unavailable', why: 'Состояние платформы пока неизвестно.' }
}
