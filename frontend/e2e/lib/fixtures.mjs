/**
 * Данные тем для фейкового сервера. Образцы берутся из моков фронта (entities/.../lib/mock.ts, widgets/system/platform-status/lib/mock.ts): они только с `import type`,
 * поэтому грузятся напрямую (node --experimental-strip-types). Остальные темы (trades, decisions, models, events, briefs, market-карта) заполнены минимально.
 */
import { pathToFileURL } from 'node:url'
import path from 'node:path'
import { SRC_DIR } from './env.mjs'

const imp = (rel) => import(pathToFileURL(path.join(SRC_DIR, rel)).href)

let mocks = null
async function loadMocks() {
  if (mocks) return mocks
  const [status, trading, brain, ideas, headline, notify, day, role, exp] = await Promise.all([
    imp('widgets/system/platform-status/lib/mock.ts'), imp('entities/trading/lib/mock.ts'), imp('entities/brain/lib/mock.ts'), imp('entities/idea/lib/mock.ts'),
    imp('entities/headline/lib/mock.ts'), imp('entities/notify/lib/mock.ts'), imp('entities/day/lib/mock.ts'), imp('entities/role/lib/mock.ts'), imp('entities/experiment/lib/mock.ts'),
  ])
  mocks = { status, trading, brain, ideas, headline, notify, day, role, exp }
  return mocks
}

const heatRow = (sym, o = {}) => ({
  sym, tradable: true, price: 100, chg_24h_pct: 1.2, ret_5m_pct: 0.1, ret_15m_pct: 0.3, range_5m_pct: 0.4, turn_surge_5m: 1.5, oi_chg_15m_pct: 0.2, oi_chg_1h_pct: 0.5, oi_musd: 120,
  funding_pct: 0.01, turn_24h_musd: 800, score: 4, ...o,
})

const trade = (id, sym, side, pnl, r, reason) => ({
  id, sym, side, lev: 5, mode: 'paper', venue: 'bybit', stype: 'day', pnl, pct: pnl * 20, move_pct: pnl, r, entry: 100, exit: 100 + pnl, reason, ts_open: Date.now() / 1000 - 7200, ts_close: Date.now() / 1000 - 3600,
  margin: 5, fees: 0.02, funding: 0, risk: 0.9, sl0: 98, sl: 98, path: null, err: [], rule_followed: null, tags: [], lesson: null, thesis: 'Откат к поддержке', note: null, plan_id: null,
  exp: null, var: null, setup: 'support', hyp: null, adds: 0, parts: 1, realized_parts: null,
})

/** Все темы по умолчанию. ctl: платформа остановлена, уровень риска 3, запуск live и снятие KILL выключены (меняют сценарии). */
export async function defaultTopics() {
  const m = await loadMocks()
  const now = Date.now() / 1000
  const status = m.status.mockStatus('mock', now)
  const ctl = {
    ...status.ctl,
    platform: { state: 'stopped', mode: 'paper', session: null, health: 'green', day_id: null, trading: false, observe: false },
    live_start: false, clear_kill: false, kill: null, day: null, banner: null, step: null, busy: false,
  }
  // карта рынка близка к боевой: много монет с разными оборотами, горячие, отслеживаемые, ликвидации, ордерфлоу
  const SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT', 'XRPUSDT', 'BNBUSDT', 'ADAUSDT', 'AVAXUSDT', 'LINKUSDT', 'SUIUSDT', 'TONUSDT', 'NEARUSDT', 'APTUSDT', 'ARBUSDT', 'OPUSDT', 'INJUSDT',
    'PEPEUSDT', 'WIFUSDT', '1000BONKUSDT', 'FARTCOINUSDT', 'TRUMPUSDT', 'HYPEUSDT', 'ENAUSDT', 'LTCUSDT', 'DOTUSDT', 'ATOMUSDT', 'FILUSDT', 'AAVEUSDT', 'UNIUSDT', 'SEIUSDT']
  const heat = SYMS.map((sym, i) => heatRow(sym, { price: 100 / (i + 1), turn_24h_musd: Math.max(8, 9000 / (i + 1) ** 1.3), chg_24h_pct: ((i * 7) % 13) - 6, ret_15m_pct: ((i * 5) % 9) / 3 - 1.4, oi_musd: 300 / (i + 1), score: (i % 8) + 1, tradable: i % 5 !== 4 }))
  const hotRow = (sym, i) => ({ sym, price: 10 + i, score: 8 - i, reasons: ['всплеск оборота', 'выход из диапазона'], skip: i === 2 ? ['спред шире лимита'] : [], tradable: i !== 2, rank: i + 1, chg_24h_pct: 3.1 - i, ret_5m_pct: 0.4, range_5m_pct: 0.9, turn_24h_musd: 400 - i * 30, turn_surge_5m: 2.4, spread_bps: 3.5, lot_usdt: 6, max_leverage: 10 })
  const market = {
    ...status.market,
    heat: { available: true, at: now - 20, rows: heat },
    hot: { available: true, at: now - 20, warm: true, stats: { scanned: 120, hot: 3 }, top: SYMS.slice(0, 3).map(hotRow), blocked: [hotRow('FARTCOINUSDT', 2)] },
    liq: { available: true, at: now - 10, totals: { long: 120000, short: 80000 }, syms: SYMS.slice(0, 4).map((sym, i) => ({ sym, long_5m: 1000 * (i + 1), short_5m: 500 * (i + 1), long_1h: 30000, short_1h: 20000, n_1h: 14, px: 100, map: [{ px: 98, long: 50000, short: 0 }, { px: 103, long: 0, short: 40000 }] })) },
    flow: { available: true, at: now - 10, xex_ok: true, syms: SYMS.slice(0, 3).map((sym) => ({ sym, px: 100, cvd_5m_pct: 3.2, buy_5m_usd: 800000, sell_5m_usd: 500000, big_5m_n: 4, big_5m_usd: 300000, bid_usd: 2000000, ask_usd: 1500000, imb_pct: 14, spread_bps: 1.2, xex_bps: 2, walls: [{ side: 'bid', px: 99, usd: 900000, dist_pct: 1, stable: true }] })) },
    coins: { ok: true, at: now - 3, rows: SYMS.slice(0, 5).map((sym) => ({ sym, price: 100, regime: 'range', regime_1h: 'trend_up', ret_1m: 0.0004, ret_5m: 0.001, ret_15m: 0.002, rv_5m: 0.003, rv_1h: 0.006, spread_bps: 1.2, imbalance: 0.1, funding_hourly: 0.00001, oi_chg_5m: 0.002, prices_by_src: { bybit: 100, binance: 100.02 } })) },
  }
  // образец мозга показывает один сбой роли; для e2e все последние ответы успешны, иначе шапка вечно жёлтая
  const brain = m.brain.mockSnapshot('idle', now)
  for (const r of brain.roles) if (r.last) r.last = { ...r.last, ok: true, err: null }
  return {
    brain,
    ctl,
    system: status.system,
    market,
    news: m.headline.mockNews('mock', now),
    notify: m.notify.mockNotify('mock'),
    day: m.day.mockDay('mock-empty', now),
    settings: m.role.mockSeed('edit'),
    briefs: { total: 0, items: [] },
    events: { last_seq: 0, items: [] },
    home: m.trading.mockHome(),
    plans: m.trading.mockPlans(),
    ideas: m.ideas.mockIdeas(now),
    trades: { mode: 'paper', total: 2, items: [trade('T-1', 'SOLUSDT', 'long', 0.84, 1.4, 'tp'), trade('T-2', 'ETHUSDT', 'short', -0.45, -0.5, 'sl')], reasons: [{ name: 'tp', n: 1, pnl: 0.84 }, { name: 'sl', n: 1, pnl: -0.45 }], errs: [], unreviewed: 2 },
    decisions: {
      mode: 'paper', total: 1, plans: ['PL-B1'], day: { n: 1, brain: 1, rejects: 0, auto: 0, skips: 0, protect: 0, reject_codes: [] },
      items: [{
        seq: 1, ts: '2026-11-02T10:00:00Z', t: now - 120, actor: 'brain', plan: 'PL-B1', rev: 1, kind: 'brain', what: 'brain_decision', action: 'propose', why: 'Вход в лонг от уровня: откат удержался, стоп за минимум.',
        args: { sym: 'SOL' }, basis: { role: 'entry_scout' }, res: {}, ok: true, code: null, denied: false, noop: false, idle: false, px: 143.2, r: null, brief_id: null, link_id: null, action_id: null, exch_ms: null, h: '', mode: 'paper',
      }],
    },
    models: { mode: 'paper', periods: {} },
  }
}

/** Платформа торгует на бумаге (день идёт): в шапке «Всё в порядке», кнопка старта показывает «Торговля идёт». Правит набор тем на месте. */
export function makeRunning(topics, mode = 'paper') {
  topics.ctl = {
    ...topics.ctl,
    platform: { state: 'running', mode, session: 'day', health: 'green', day_id: 'D-20261102-1', trading: true, observe: false },
    day: { day_id: 'D-20261102-1', state: 'running', mode, steps: ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9'].map((name) => ({ name, phase: 'start', ok: true, code: null })) },
  }
  return topics
}

/** Образец hello.ctl с расширенным управлением (все действия, которые нужны сценариям). */
export const HELLO_FULL = {
  enabled: true, csrf: 'e2e-csrf', full: true,
  modes: ['day.start', 'day.stop', 'risk.set', 'kill.clear', 'kill.set', 'mode.set', 'exchange.set', 'brain.use', 'brain.role', 'brain.profile', 'llm.provider', 'llmkey', 'notify.set', 'notify.test'],
}

/** Образцы для разовых ответов. */
export async function planDetail(id, plans) {
  const p = plans.items.find((x) => x.id === id)
  if (!p) return { id, ok: false, error: 'not found' }
  return {
    id, ok: true,
    data: {
      id, spec: {}, runtime: {}, revs: [], decisions: [],
      card: { ...p, risk_usdt: p.risk, margin: null, rev: p.rev ?? 1, rev_applied: p.rev ?? 1, status: p.status ?? 'armed', targets: (p.targets ?? []).map((price, i) => ({ id: `T${i + 1}`, price, frac: null, state: null })) },
    },
  }
}

export async function expDetail(by, mode) {
  const m = await loadMocks()
  return m.exp.mockExp('mock', by || 'hyp', mode || 'paper')
}

export async function dayReport(id) {
  const m = await loadMocks()
  return m.day.mockReport(id)
}

export async function mockDayDone() {
  const m = await loadMocks()
  return m.day.mockDay('mock-done', Date.now() / 1000)
}
