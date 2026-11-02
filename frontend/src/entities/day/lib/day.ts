import {
  answerWord, autoCloseText, biasWord, checkCodeWord, closeFailReason, dayLabel, dayModeWord, dayStateWord, dayStepAmber, dayStepDoes, dayStepLabel, decidedByWord, decisionWord, duration, failReason,
  humanize, isCloseStep, isPlain, planSourceWord, price, regimeWord, ruleWord, signed, stepAmber, stopSourceText, usd, verdictWord,
} from '@/shared/lib'
import type { DayAbort, DayBlocking, DayBrief, DayCarry, DayCheckRow, DayCurrent, DayNeeds, DayQuestion, DaySnapshot, DayStep } from '../model/types'

/** Часы и минуты по времени компьютера: «21:05». Время приходит секундами epoch. */
export function clock(ts: number | null | undefined): string {
  if (typeof ts !== 'number' || !Number.isFinite(ts)) return '–'
  return new Date(ts * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
}

/** Идентификатор позиции из чек-листа и закрытия («pos:PL-9» или «plan:PL-9») без служебной приставки. */
export const planOf = (item: string | null | undefined): string => (item ?? '').replace(/^(pos|plan|sess):/, '')

// ---------------------------------------------------------------- что ждёт ответа владельца

const NO_NEEDS: DayNeeds = { questions: [], deadline: null, resume: false, report: null, count: 0 }

/**
 * Что требует вашего участия: вопросы о переносе позиции (пока день не завершён и ответа нет), «Возобновить день» после перезапуска платформы и готовый непрочитанный итог.
 * Итог берётся из баннера платформы: он держится до «Прочитал» (report.ack), поэтому старые непрочитанные дни не шумят.
 */
export function dayNeeds(s: DaySnapshot | null | undefined): DayNeeds {
  const c = s?.current
  if (!s || !c) return NO_NEEDS
  const open = c.state === 'closing' || c.state === 'running'
  const questions = open ? c.questions.filter((q) => q.answer == null && !!q.plan) : []
  const resume = c.needs_resume && c.state === 'running'
  const report = s.banner?.day_report ?? null
  return { questions, deadline: questions.length ? c.deadline_ts : null, resume, report, count: questions.length + (resume ? 1 : 0) + (report ? 1 : 0) }
}

/** Короткая строка «ждёт вашего ответа» для индикатора платформы; null: ждать нечего. */
export function dayWaitText(n: DayNeeds): string | null {
  if (!n.count) return null
  const parts: string[] = []
  if (n.questions.length) parts.push(n.questions.length === 1 ? 'вопрос о переносе позиции' : `вопросы о переносе позиций (${n.questions.length})`)
  if (n.resume) parts.push('возобновление дня')
  if (n.report) parts.push('итог дня готов')
  return parts.join(', ')
}

// ---------------------------------------------------------------- расшифровка проблем

/** Что не прошло при запуске: простая фраза и что делать, по каждой проверке. */
export function blockingText(b: DayBlocking): { title: string; todo: string } {
  const id = b.id ?? ''
  if (/^[HWP]\d+$/.test(id)) {
    const h = humanize({ kind: 'gate', id, ok: false, msg: b.msg ?? '', sev: 'red' })
    return { title: h.title, todo: h.todo }
  }
  return { title: isPlain(b.msg) ? b.msg : 'Одно из условий запуска не выполнено', todo: isPlain(b.fix) ? b.fix : '' }
}

export interface AbortView { title: string; reason: string; todos: string[] }

/** Старт отклонён: на каком шаге, по какой причине и что сделать. Без кодов. */
export function abortView(a: DayAbort): AbortView {
  const step = a.step ? `на шаге «${dayStepLabel(a.step)}»` : ''
  const b = a.blocking.map(blockingText)
  const reason = b.length ? b.map((x) => x.title).join('; ') : isPlain(a.why) ? a.why : failReason(a.code)
  return { title: `Старт отклонён${step ? ' ' + step : ''}`, reason, todos: [...new Set(b.map((x) => x.todo).filter(Boolean))] }
}

/** Закрытие не подтверждено исполнителем: что осталось и почему. */
export function closeFailedView(c: DayCurrent): { title: string; reason: string; plans: string[] } | null {
  const f = c.closing.failed
  if (!f) return null
  const plans = [...new Set(f.items.map(planOf).filter(Boolean))]
  return { title: 'Закрытие дня не подтверждено исполнителем', reason: closeFailReason(f.reason), plans }
}

// ---------------------------------------------------------------- шаги дня

export type StepState = 'done' | 'amber' | 'fail' | 'run'
export interface StepRow {
  key: string
  name: string
  label: string
  does: string
  state: StepState
  status: string
  note: string | null
  dur: string | null
}

const upper = (s: string): string => (s ? s[0].toUpperCase() + s.slice(1) : s)
const stepState = (s: DayStep): StepState => (s.ok === null ? 'run' : s.ok === false ? 'fail' : s.amber ? 'amber' : 'done')
const STATUS: Record<StepState, string> = { done: 'готово', amber: 'готово, есть замечание', fail: 'сбой', run: 'выполняется' }

function stepNote(s: DayStep, state: StepState): string | null {
  if (state === 'amber') {
    const name = s.name ?? ''
    const a = isCloseStep(name) ? dayStepAmber(name) : (stepAmber(name)?.text ?? dayStepAmber(name))
    return upper(a)
  }
  if (state === 'fail') {
    const b = s.blocking.map(blockingText)
    if (b.length) return b.map((x) => x.title).join('; ')
    return upper(isPlain(s.why) ? s.why : failReason(s.code))
  }
  return null
}

/** Шаги нужной фазы в порядке выполнения (старт: S1–S9, закрытие: C1–C8): имя, что делает, состояние, причина замечания или сбоя, длительность. */
export function stepRows(c: DayCurrent | null | undefined, phase: 'start' | 'stop'): StepRow[] {
  return (c?.steps ?? []).filter((s) => s.phase === phase && s.name).map((s) => {
    const state = stepState(s)
    const name = s.name as string
    return { key: `${phase}:${name}`, name, label: dayStepLabel(name), does: dayStepDoes(name), state, status: STATUS[state], note: stepNote(s, state), dur: s.dur_s != null && s.dur_s >= 1 ? duration(s.dur_s) : null }
  })
}

// ---------------------------------------------------------------- строка-итог и факты

export type DayTone = 'ok' | 'warn' | 'bad' | 'idle'
export interface DayHeadline { tone: DayTone; text: string; detail: string }

const sideWord = (s: string | null | undefined): string => (s === 'long' ? 'лонг' : s === 'short' ? 'шорт' : '')

/** Монета и сторона плана: из чек-листа или переноса; нет данных: сам идентификатор плана. */
export function planTitle(plan: string | null | undefined, c: DayCurrent | null | undefined): string {
  const row = c?.checklist.find((r) => r.plan === plan && r.sym) ?? null
  const car = c?.carry.find((r) => r.plan === plan && r.sym) ?? c?.carry_in.find((r) => r.plan === plan && r.sym) ?? null
  const sym = row?.sym ?? car?.sym
  const side = sideWord(row?.side ?? car?.side)
  return sym ? [sym, side].filter(Boolean).join(' ') : (plan ?? 'позиция')
}

function startNote(c: DayCurrent): string {
  const amber = stepRows(c, 'start').filter((r) => r.state === 'amber')
  return amber.length ? `При запуске были замечания: ${amber.map((r) => r.label.toLowerCase()).join(', ')}.` : ''
}

/** Одно предложение, с которого начинается страница: что происходит и нужно ли что-то от вас. */
export function dayHeadline(s: DaySnapshot | null | undefined, now: number): DayHeadline {
  if (!s) return { tone: 'idle', text: 'Ждём данные о торговом дне…', detail: '' }
  const c = s.current
  if (!c) return { tone: 'idle', text: 'Торговый день ещё не запускался', detail: 'Запустить его можно кнопкой «Старт торговли».' }
  const need = dayNeeds(s)
  const label = dayLabel(c.day_id)
  const mode = dayModeWord(c.mode)
  if (need.resume) return { tone: 'warn', text: 'Платформа перезапущена, торговля выключена', detail: `День ${label} (${mode}) продолжается, но входы закрыты, пока вы не нажмёте «Возобновить день».` }
  if (need.questions.length) {
    const late = need.deadline != null && now > need.deadline
    const n = need.questions.length
    return {
      tone: 'warn', text: late ? 'Срок ответа вышел, позиции закрываются' : `Нужен ваш ответ${need.deadline != null ? ' до ' + clock(need.deadline) : ''}`,
      detail: `${n === 1 ? 'Вопрос' : 'Вопросов'} о переносе позиции через сутки${n === 1 ? '' : ': ' + n}. Без ответа позиция будет закрыта.`,
    }
  }
  const failed = closeFailedView(c)
  if (failed) return { tone: 'bad', text: failed.title, detail: `Причина: ${failed.reason}. Позиции остаются под защитой стопов на бирже, нужен ручной разбор.` }
  if (need.report) {
    const b = s.days.find((d) => d.day_id === need.report) ?? (c.day_id === need.report ? c : null)
    return { tone: b?.verdict === 'alarm' ? 'bad' : b?.verdict === 'attention' ? 'warn' : 'ok', text: `Итог дня ${dayLabel(need.report)} готов${b?.net != null ? `: ${signed(b.net)} USDT` : ''}`, detail: 'Откройте отчёт и нажмите «Прочитал», когда ознакомитесь.' }
  }
  switch (c.state) {
    case 'starting': {
      const done = stepRows(c, 'start').filter((r) => r.state === 'done' || r.state === 'amber').length
      return { tone: 'idle', text: `День ${label} (${mode}) запускается`, detail: `Выполнено шагов запуска: ${done} из 9.` }
    }
    case 'running': return { tone: 'ok', text: `Сейчас идёт торговый день ${label} (${mode}): всё в порядке`, detail: startNote(c) }
    case 'closing': return { tone: 'warn', text: `День ${label} закрывается`, detail: 'Платформа закрывает позиции и готовит итог. Это занимает несколько минут.' }
    case 'halted': return { tone: 'bad', text: `День ${label} остановлен аварийно`, detail: 'Торговля выключена. Причина указана в состоянии платформы; открытые позиции защищают стопы на бирже.' }
    case 'stopped': {
      if (c.abort) return { tone: 'bad', text: `Старт дня ${label} отклонён`, detail: abortView(c.abort).reason }
      const net = c.net != null ? ` Итог: ${signed(c.net)} USDT.` : ''
      return { tone: 'idle', text: `Торговый день ${label} завершён`, detail: `${upper(stopSourceText(c.stop_source))}${stopSourceText(c.stop_source) ? '.' : ''}${net}`.trim() }
    }
    default: return { tone: 'idle', text: `День ${label}: ${dayStateWord(c.state)}`, detail: '' }
  }
}

export interface DayFact { label: string; text: string }

/** Два-три факта под итогом: итог дня, план дня, перенесённые со вчера позиции. */
export function dayFacts(c: DayCurrent | null | undefined): DayFact[] {
  if (!c) return []
  const out: DayFact[] = []
  out.push({ label: 'Итог дня', text: c.net != null ? `${signed(c.net)} USDT, оценка: ${verdictWord(c.verdict)}` : c.state === 'stopped' || c.state === 'halted' ? 'отчёта нет' : 'появится после закрытия дня' })
  const p = c.dayplan
  if (p) {
    const n = p.max_new_plans
    const src = planSourceWord(p.source)
    out.push({ label: 'План дня', text: [`${regimeWord(p.regime)}, ${biasWord(p.bias)}`, n != null ? `новых планов до ${n}` : '', src].filter(Boolean).join('; ') })
  } else if (c.state === 'starting') out.push({ label: 'План дня', text: 'готовится' })
  if (c.carry_in.length) {
    const alive = c.carry_in.filter((x) => x.alive !== false).length
    out.push({ label: 'Со вчера', text: `перенесено позиций: ${c.carry_in.length}${alive !== c.carry_in.length ? `, на бирже найдено ${alive}` : ''}` })
  }
  return out
}

// ---------------------------------------------------------------- вопросы, перенос, чек-лист

export interface QuestionView { id: string; plan: string; title: string; text: string }

export function questionViews(c: DayCurrent | null | undefined, qs: DayQuestion[]): QuestionView[] {
  return qs.map((q, i) => ({ id: q.id ?? `q${i}`, plan: q.plan as string, title: planTitle(q.plan, c), text: q.text ?? 'Перенести позицию через сутки?' }))
}

export interface CarryView { key: string; title: string; facts: string; why: string }

export function carryViews(items: DayCarry[], c: DayCurrent | null | undefined): CarryView[] {
  return items.map((x, i) => {
    const who = decidedByWord(x.decided_by)
    return {
      key: x.plan ?? `c${i}`, title: [x.sym, sideWord(x.side)].filter(Boolean).join(' ') || planTitle(x.plan, c),
      facts: [x.stop != null ? `стоп ${price(x.stop)}` : '', x.risk_usdt != null ? `риск ${usd(x.risk_usdt)}` : '', who ? `решил: ${who}` : ''].filter(Boolean).join(' · '), why: x.why ?? '',
    }
  })
}

export interface CheckView { key: string; title: string; decision: string; tone: 'ok' | 'warn' | 'idle'; why: string }

/** Чек-лист закрытия по-человечески: что с каждой позицией или заявкой решено и почему. */
export function checkViews(rows: DayCheckRow[], c: DayCurrent | null | undefined): CheckView[] {
  return rows.map((r, i) => {
    const title = r.kind === 'session' ? 'Торговая сессия' : r.kind === 'ideas' ? 'Идеи' : r.kind === 'entry' ? `Ждущий вход ${planTitle(r.plan, c)}` : planTitle(r.plan, c) === 'позиция' ? `Позиция ${planOf(r.item)}` : planTitle(r.plan, c)
    const codes = r.codes.map(checkCodeWord)
    const why = codes.length ? codes.join('; ') : ruleWord(r.rule)
    const d = r.decision
    return { key: r.item ?? `r${i}`, title, decision: r.kind === 'session' ? 'остановить' : decisionWord(d), tone: d === 'carry' || d === 'keep' ? 'ok' : d === 'ask_owner' ? 'warn' : 'idle', why: upper(why) }
  })
}

export function answeredView(q: DayQuestion): string {
  const a = answerWord(q.answer)
  return a ? `${a}${decidedByWord(q.answered_by) ? ` (${decidedByWord(q.answered_by)})` : ''}` : ''
}

// ---------------------------------------------------------------- список дней

export interface DayRow {
  id: string
  label: string
  state: string
  mode: string
  net: string | null
  netTone: 'pos' | 'neg' | null
  verdict: string | null
  verdictTone: 'ok' | 'warn' | 'bad' | null
  hasReport: boolean
  unread: boolean
}

export function dayRows(days: DayBrief[], unreadId: string | null): DayRow[] {
  return days.filter((d) => d.day_id).map((d) => ({
    id: d.day_id as string, label: dayLabel(d.day_id), state: dayStateWord(d.state), mode: dayModeWord(d.mode),
    net: d.net != null ? `${signed(d.net)} USDT` : null, netTone: d.net != null && d.net !== 0 ? (d.net > 0 ? 'pos' : 'neg') : null,
    verdict: d.verdict ? verdictWord(d.verdict) : null, verdictTone: d.verdict === 'ok' ? 'ok' : d.verdict === 'attention' ? 'warn' : d.verdict === 'alarm' ? 'bad' : null,
    hasReport: d.report_ready, unread: d.day_id === unreadId,
  }))
}

/** Текст «закрылся сам»: если день закрыт платформой по условию, а не по команде. */
export const autoNote = (c: DayCurrent): string => (c.closing.auto_reason ? `День закрылся сам: ${autoCloseText(c.closing.auto_reason)}.` : '')
