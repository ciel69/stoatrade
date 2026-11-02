/** Готовые правила ответов фейкового сервера на команды управления (ctl): уровень риска, запуск и остановка дня, снятие KILL. */
import { calmChallenge } from './fake-dash.mjs'

/** Условия с предупреждениями: откроют единый диалог «Принимаю риски». minDelay: пауза до разрешённого нажатия (с). */
export function riskyChallenge({ warnings = [{ id: 'W_RISK_UP', code: 'W_RISK_UP', msg: 'Допустимый риск на сделку и дневной убыток вырастут.' }], minDelay = 1, danger = true, ...rest } = {}) {
  return calmChallenge({ danger, warnings, min_delay: minDelay, expires_in: 120, nonce: 'n-risky', ...rest })
}

/** risk.set: повышение рискованно (диалог), понижение безрисково. Тема ctl обновляется через applyDelayMs после итога команды. */
export function installRiskRules(dash, { applyDelayMs = 400 } = {}) {
  dash.rules['risk.set'] = {
    challenge: (args) => {
      const cur = dash.state.get('ctl').risk.level
      return { data: Number(args.level) > cur ? riskyChallenge() : calmChallenge() }
    },
    command: (args) => ({
      final: { status: 'done', code: 'OK', msg: `Уровень риска ${args.level} применён.` },
      apply: (d) => d.later(applyDelayMs, () => d.patch('ctl', (c) => { c.risk = { level: Number(args.level), name: null } })),
    }),
  }
}

/** Шаги запуска дня платформой: S1..S9. */
export const DAY_STEPS = ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9']

/**
 * day.start: challenge с предупреждениями (paper) и ход запуска в теме ctl: платформа «starting», шаги по одному, затем «running».
 * opts.warnings: предупреждения; opts.stepMs: пауза между шагами; opts.live: усиленные условия (фраза, danger).
 */
export function installDayStartRules(dash, { warnings = [{ id: 'W1', code: 'W_PAPER_HISTORY', msg: 'Мало paper-истории: рекомендуется 30 сделок и 7 дней' }], stepMs = 250, gates = null } = {}) {
  dash.rules['day.start'] = {
    challenge: (args) => {
      const live = args.mode === 'live'
      const g = gates ?? ['H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'H7', 'H8'].map((id) => ({ id, name: id, ok: true }))
      if (live) return { data: riskyChallenge({ gates: g, warnings, minDelay: 1, danger: true, phrases: ['ТОРГУЮ LIVE 2026-11-02 УРОВЕНЬ 3'], nonce: 'n-live' }) }
      return { data: riskyChallenge({ gates: g, warnings, danger: false, minDelay: 1, nonce: 'n-paper' }) }
    },
    command: (args) => ({
      delayMs: stepMs * (DAY_STEPS.length + 2),
      final: { status: 'done', code: 'OK', msg: 'День запущен.' },
      accept: { ok: true, code: 'OK', msg: 'принято' },
      apply: (d) => {
        // итог: платформа торгует
        d.patch('ctl', (c) => {
          c.platform = { ...c.platform, state: 'running', mode: args.mode, day_id: 'D-20261102-1', trading: args.mode !== 'observe', observe: args.mode === 'observe' }
          c.step = null
          c.day = { day_id: 'D-20261102-1', state: 'running', mode: args.mode, steps: DAY_STEPS.map((name) => ({ name, phase: 'start', ok: true, code: null })) }
        })
      },
      // ход запуска: starting и шаги по одному, пока идёт ожидание итога
      progress: true,
    }),
  }
  // ход запуска запускается перехватом: после принятия команды
  const origCtl = dash._ctl.bind(dash)
  dash._ctl = (ws, m) => {
    origCtl(ws, m)
    if (m.kind === 'command' && m.action === 'day.start') runStartProgress(dash, m.args ?? {}, stepMs)
  }
}

function runStartProgress(dash, args, stepMs) {
  dash.later(80, () => dash.patch('ctl', (c) => {
    c.platform = { ...c.platform, state: 'starting', mode: args.mode }
    c.step = { name: 'day' }
    c.busy = true
    c.day = { day_id: 'D-20261102-1', state: 'starting', mode: args.mode, steps: [] }
  }))
  DAY_STEPS.forEach((name, i) => {
    dash.later(80 + stepMs * (i + 1), () => dash.patch('ctl', (c) => {
      c.day.steps = [...c.day.steps, { name, phase: 'start', ok: true, code: null }]
    }))
  })
}

/** day.stop: безрисковая остановка с одним предупреждением (диалог), затем платформа «closing», потом «stopped». */
export function installDayStopRules(dash) {
  dash.rules['day.stop'] = {
    challenge: () => ({ data: riskyChallenge({ warnings: [{ id: 'W_STOP_OPEN', code: 'W_STOP_OPEN', msg: 'Открытые позиции останутся под стопами на бирже, пока день завершается.' }], danger: false, minDelay: 0 }) }),
    command: (_args, _msg, d) => ({
      delayMs: 900,
      // платформа сначала «завершает день», потом останавливается
      accept: (d.later(100, () => d.patch('ctl', (c) => { c.platform = { ...c.platform, state: 'closing' } })), { ok: true, code: 'OK', msg: 'принято' }),
      final: { status: 'done', code: 'OK', msg: 'День завершён.' },
      apply: (d) => d.patch('ctl', (c) => { c.platform = { ...c.platform, state: 'stopped', trading: false, day_id: null }; c.day = null }),
    }),
  }
}

/** kill.clear: условия K1, K2, H2; команда снимает KILL в теме ctl после итога. */
export function installKillRules(dash, { applyDelayMs = 300 } = {}) {
  dash.rules['kill.clear'] = {
    challenge: () => ({
      data: riskyChallenge({
        danger: true, minDelay: 1, warnings: [], phrases: [],
        kill: { active: true, reason: 'ручная остановка', flatten: false, mode: 'paper' },
        gates: [{ id: 'K1', name: 'Исполнитель на связи', ok: true, msg: 'исполнитель жив' }, { id: 'K2', name: 'День не идёт и не закрывается', ok: true, msg: 'платформа остановлена' }, { id: 'H2', name: 'Стоп на бирже у каждой позиции', ok: true, msg: 'позиций 0' }],
      }),
    }),
    command: () => ({
      final: { status: 'done', code: 'OK', msg: 'Аварийная остановка снята.' },
      apply: (d) => d.later(applyDelayMs, () => d.patch('ctl', (c) => { c.kill = null; c.platform = { ...c.platform, state: 'stopped', health: 'green' } })),
    }),
  }
}
