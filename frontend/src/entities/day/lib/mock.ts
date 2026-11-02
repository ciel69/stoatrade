import type { DayBrief, DayCarry, DayCheckRow, DayCurrent, DayReportReply, DayQuestion, DaySnapshot, DayStep } from '../model/types'

export type DayMock = 'mock' | 'mock-resume' | 'mock-done' | 'mock-abort' | 'mock-empty'
export const DAY_MOCKS: readonly string[] = ['mock', 'mock-resume', 'mock-done', 'mock-abort', 'mock-empty']
export const isDayMock = (v: string | null | undefined): v is DayMock => !!v && DAY_MOCKS.includes(v)

const step = (name: string, phase: 'start' | 'stop', o: Partial<DayStep> = {}): DayStep => ({
  name, phase, ok: true, dur_s: 0.6, code: null, amber: false, why: null, blocking: [], gone: [], waiting: [], llm: null, ...o,
})

const START_OK: DayStep[] = [
  step('S1', 'start', { dur_s: 2.4 }), step('S2', 'start', { dur_s: 3.1, amber: true }), step('S3', 'start'), step('S4', 'start', { dur_s: 1.8 }), step('S5', 'start', { dur_s: 14.2 }),
  step('S6', 'start', { dur_s: 9.7 }), step('S7', 'start'), step('S8', 'start', { dur_s: 0.2 }), step('S9', 'start', { dur_s: 0.4 }),
]

const brief = (o: Partial<DayBrief>): DayBrief => ({
  day_id: 'D-20261102-1', state: 'running', mode: 'paper', agent: 'brain', started: '2026-11-02T06:02:00Z', stopped: null, stop_source: null, verdict: null, net: null, carried_n: 0, questions_n: 0,
  acked: false, report_ready: false, needs_resume: false, abort: null, ...o,
})

const QUESTIONS: DayQuestion[] = [
  { id: 'Q1', plan: 'PL-20261102-07', text: 'Позиция по ETHUSDT в плюсе, стоп подтянут. До важного события 6 часов. Перенести через сутки?', options: ['carry', 'close'], answer: null, answered_by: null },
  { id: 'Q2', plan: 'PL-20261102-09', text: 'SOLUSDT: риск до стопа вырос. Оставить на ночь?', options: ['carry', 'close'], answer: 'close', answered_by: 'owner' },
]

const CHECK: DayCheckRow[] = [
  { item: 'pos:PL-20261102-07', kind: 'pos', plan: 'PL-20261102-07', sym: 'ETHUSDT', side: 'long', stype: 'intraday', rule: 'A3', decision: 'ask_owner', codes: [] },
  { item: 'pos:PL-20261102-09', kind: 'pos', plan: 'PL-20261102-09', sym: 'SOLUSDT', side: 'short', stype: 'intraday', rule: 'A7', decision: 'close', codes: ['K2_RISK'] },
  { item: 'plan:PL-20261102-11', kind: 'entry', plan: 'PL-20261102-11', sym: 'BTCUSDT', side: 'long', stype: 'swing', rule: 'A1', decision: 'cancel', codes: [] },
  { item: 'sess:S-1', kind: 'session', plan: null, sym: null, side: null, stype: 'day', rule: 'A8', decision: 'stop', codes: [] },
]

const CARRY_IN: DayCarry[] = [{ plan: 'PL-20261101-03', sym: 'BTCUSDT', side: 'long', stop: 68120, risk_usdt: 0.42, why: 'Тезис жив, стоп выше входа.', decided_by: 'owner', alive: true }]

function current(o: Partial<DayCurrent>): DayCurrent {
  return {
    ...brief({}), steps: START_OK, questions: [], deadline_ts: null,
    closing: { source: null, auto_reason: null, emergency: false, manual: [], failed: null },
    dayplan: { source: 'agent', regime: 'range', bias: 'neutral', max_new_plans: 4, session: 'position' }, preflight: { red: [], amber: ['H4'] },
    checklist: [], carry: [], carry_in: CARRY_IN, ...o,
  }
}

const list = (now: number): DayBrief[] => [
  brief({ day_id: 'D-20261102-1', state: 'closing' }),
  brief({ day_id: 'D-20261101-2', state: 'stopped', stopped: '2026-11-01T21:00:00Z', stop_source: 'owner', verdict: 'ok', net: 0.84, carried_n: 1, acked: true, report_ready: true }),
  brief({ day_id: 'D-20261101-1', state: 'stopped', stopped: '2026-11-01T05:40:00Z', stop_source: 'abort', abort: { step: 'S1', code: 'E_GATE', why: null, blocking: [{ id: 'H1', msg: 'KILL активен', fix: null }] } }),
  brief({ day_id: 'D-20261031-1', state: 'stopped', stopped: '2026-10-31T21:00:00Z', stop_source: 'auto', verdict: 'attention', net: -0.37, acked: true, report_ready: true }),
].map((d) => ({ ...d, started: d.started ?? new Date(now * 1000).toISOString() }))

/** Образцовые данные без сервера (?daymock=...). Выдуманы. Реальных команд страница в этом режиме не отправляет. */
export function mockDay(kind: DayMock, nowS = Date.now() / 1000): DaySnapshot {
  if (kind === 'mock-empty') return { banner: null, current: null, last: null, days: [] }
  if (kind === 'mock-resume') {
    const c = current({ needs_resume: true })
    return { banner: { level: 'warn', text: 'Платформа перезапущена', day_report: null, day_id: c.day_id }, current: c, last: null, days: list(nowS) }
  }
  if (kind === 'mock-done') {
    const closeSteps = ['C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'C8'].map((n) => step(n, 'stop', n === 'C4' ? { amber: true, llm: 'timeout', dur_s: 45 } : n === 'C5' ? { dur_s: 600 } : {}))
    const c = current({
      state: 'stopped', stopped: '2026-11-02T21:00:00Z', stop_source: 'owner', verdict: 'attention', net: 1.26, carried_n: 1, questions_n: 1, report_ready: true, steps: [...START_OK, ...closeSteps],
      checklist: CHECK, questions: QUESTIONS.map((q) => ({ ...q, answer: q.answer ?? 'carry', answered_by: 'owner' })),
      carry: [{ plan: 'PL-20261102-07', sym: 'ETHUSDT', side: 'long', stop: 2441.5, risk_usdt: 0.38, why: 'Тезис жив, стоп подтянут выше входа.', decided_by: 'owner', alive: null }],
    })
    return { banner: { level: 'info', text: 'Итог дня готов', day_report: c.day_id, day_id: c.day_id }, current: c, last: brief({ ...c }), days: [brief({ ...c }), ...list(nowS).slice(1)] }
  }
  if (kind === 'mock-abort') {
    const abort = { step: 'S1', code: 'E_GATE', why: null, blocking: [{ id: 'H1', msg: 'KILL активен (причина: ручная остановка)', fix: null }, { id: 'H3', msg: 'исполнитель не отвечает', fix: null }] }
    const c = current({
      state: 'stopped', stop_source: 'abort', abort, stopped: '2026-11-02T06:02:04Z', carry_in: [], dayplan: null, preflight: { red: ['H1', 'H3'], amber: [] },
      steps: [step('S1', 'start', { ok: false, code: 'E_GATE', dur_s: 1.8, blocking: abort.blocking })],
    })
    return { banner: { level: 'alarm', text: 'Старт дня отклонён', day_report: null, day_id: c.day_id }, current: c, last: brief({ ...c }), days: [brief({ ...c }), ...list(nowS).slice(1)] }
  }
  // mock: день идёт, закрывается и ждёт ответа по переносу (вопрос о позиции с ограниченным сроком)
  const closeSteps = [step('C1', 'stop'), step('C2', 'stop', { dur_s: 5.2 }), step('C3', 'stop'), step('C4', 'stop', { dur_s: 38 }), step('C5', 'stop', { ok: null, dur_s: null })]
  const c = current({ state: 'closing', stop_source: 'owner', questions: QUESTIONS, deadline_ts: nowS + 38 * 60, questions_n: 1, steps: [...START_OK, ...closeSteps], checklist: CHECK, closing: { source: 'owner', auto_reason: null, emergency: false, manual: [], failed: null } })
  return { banner: { level: 'warn', text: 'Жду решения по переносу', day_report: null, day_id: c.day_id }, current: c, last: null, days: list(nowS) }
}

const MD = `# Итог дня D-20261102-1

Режим: paper. Оценка: attention. Старт 06:02, остановка 21:00 UTC (owner).

## Результат
- PnL чистый: +1.2600 USDT (комиссии 0.0840, funding -0.0120)
- Сделок закрыто: 5, открытых осталось: 0

## Перенесено через сутки
- ETHUSDT long: стоп 2441.5, риск 0.38 USDT, решил: owner

## Вопросы
- Q1: Позиция по ETHUSDT в плюсе. Перенести через сутки?
`

/** Образец отчёта (?daymock=mock-done) для боковой панели. */
export function mockReport(id: string): DayReportReply {
  if (id !== 'D-20261102-1' && id !== 'D-20261101-2') return { id, ok: false, error: 'no report' }
  return {
    id, ok: true,
    data: {
      day_id: id, kind: 'stop', mode: 'paper', agent: 'brain', started: '2026-11-02T06:02:00Z', stopped: '2026-11-02T21:00:00Z', stop_source: 'owner', verdict: 'attention',
      pnl: { net: 1.26, fees: 0.084, funding: -0.012, realized_gross: 1.356, r_sum: 2.4 }, trades_n: 5, open_trades_n: 0, cost: { usd: 0.0731, calls: 214 },
      sessions: [{ id: 'S-1', type: 'day', pnl: 1.26, trades: 5, budget_used: 0.12 }],
      carried: [{ plan: 'PL-20261102-07', sym: 'ETHUSDT', side: 'long', stop: 2441.5, risk_usdt: 0.38, why: 'Тезис жив, стоп подтянут выше входа.', decided_by: 'owner', alive: null }],
      closed_eod: 1, open_questions: [{ id: 'A1', text: 'Позиция по ETHUSDT в плюсе. Перенести через сутки?' }], narrative: 'Рынок был в боковике, четыре из пяти сделок закрыты по тейку.',
      close_failed: null, md: MD,
    },
  }
}
