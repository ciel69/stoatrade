/**
 * Демо-данные для скриншотов документации (npm run screenshots). Берёт набор тем e2e (fixtures.mjs) и заменяет то, что может напоминать реальный счёт:
 * капитал и деньги — круглые нейтральные значения (счёт 1000 USDT), сделки и решения — вымышленная лента, день — завершённый образец.
 * e2e-сценарии этот модуль не используют, их фикстуры не меняются.
 */
import { pathToFileURL } from 'node:url'
import path from 'node:path'
import { SRC_DIR } from './env.mjs'
import { defaultTopics, makeRunning } from './fixtures.mjs'

/** День идёт (образец фронта mock-resume без баннера «перезапущена»), в списке прошлые дни с итогами. */
async function runningDay() {
  const { mockDay } = await import(pathToFileURL(path.join(SRC_DIR, 'entities/day/lib/mock.ts')).href)
  const d = mockDay('mock-resume', Date.now() / 1000)
  return { ...d, banner: null, current: { ...d.current, needs_resume: false } }
}

const EQUITY = 1000
const HWM = 1042
const K = 10 // множитель денег образцов фронта (они рассчитаны на счёт в десятки USDT)

const scale = (v) => (typeof v === 'number' ? Math.round(v * K * 100) / 100 : v)

function demoTrade(i, sym, side, r, reason, setup, hoursAgo) {
  const risk = 9
  const pnl = Math.round(r * risk * 100) / 100
  const entry = { BTCUSDT: 68400, ETHUSDT: 2262, SOLUSDT: 143.2, SUIUSDT: 2.807, XRPUSDT: 0.523, DOGEUSDT: 0.1812, LINKUSDT: 14.6 }[sym] ?? 100
  const move = (r * 0.6) / 100
  const exit = side === 'long' ? entry * (1 + move) : entry * (1 - move)
  const now = Date.now() / 1000
  return {
    id: `T-${100 + i}`, sym, side, lev: 5, mode: 'paper', venue: 'bybit', stype: i % 3 ? 'intraday' : 'scalp', pnl, pct: Math.round(pnl / 0.9 * 10) / 10, move_pct: Math.round(move * 10000) / 100, r,
    entry, exit: Math.round(exit * 10000) / 10000, reason, ts_open: now - hoursAgo * 3600 - 2400, ts_close: now - hoursAgo * 3600, margin: 90, fees: 0.18, funding: 0, risk,
    sl0: entry * (side === 'long' ? 0.994 : 1.006), sl: entry * (side === 'long' ? 0.994 : 1.006),
    path: { mae_pct: -0.3, mfe_pct: Math.max(0.1, move * 100 + 0.2), mae_r: -0.5, mfe_r: Math.max(0.2, r + 0.4), mfe_to_tp: 0.8, path_coverage: 1 },
    err: r < 0 && i % 4 === 0 ? ['late_entry'] : [], rule_followed: 'yes', tags: ['agent.entry_scout'], lesson: null, thesis: setup === 'breakout' ? 'Пробой диапазона на объёме' : 'Откат к поддержке',
    note: null, plan_id: `PL-D${i}`, exp: null, var: null, setup, hyp: i % 2 ? 'H-retest' : null, adds: 0, parts: 1, realized_parts: null,
  }
}

const TRADES = [
  ['SOLUSDT', 'long', 1.4, 'tp', 'support', 1], ['ETHUSDT', 'short', -1, 'sl', 'breakout', 2], ['BTCUSDT', 'long', 0.6, 'trail', 'support', 3], ['SUIUSDT', 'long', 2.1, 'tp', 'breakout', 5],
  ['XRPUSDT', 'short', -0.4, 'time', 'support', 6], ['DOGEUSDT', 'long', -1, 'sl', 'breakout', 8], ['LINKUSDT', 'long', 1.2, 'tp', 'support', 9], ['ETHUSDT', 'long', 0.3, 'manual', 'support', 20],
  ['SOLUSDT', 'short', -1, 'sl', 'breakout', 22], ['BTCUSDT', 'short', 1.8, 'tp', 'breakout', 24], ['SUIUSDT', 'long', -0.6, 'trail', 'support', 27], ['XRPUSDT', 'long', 0.9, 'tp', 'support', 30],
]

function decision(seq, minAgo, o) {
  const now = Date.now() / 1000
  return {
    seq, ts: new Date((now - minAgo * 60) * 1000).toISOString(), t: now - minAgo * 60, actor: 'brain', plan: null, rev: 1, kind: 'brain', what: 'brain_decision', action: 'hold', why: '',
    args: {}, basis: {}, res: {}, ok: true, code: null, denied: false, noop: false, idle: false, px: null, r: null, brief_id: null, link_id: null, action_id: null, exch_ms: null, h: '', mode: 'paper', ...o,
  }
}

const DECISIONS = [
  decision(9, 2, { plan: 'PL-B1', action: 'propose', why: 'Вход в лонг от уровня 142.5: откат удержался на 1ч, стоп за минимум, RR после издержек 2.8.', args: { sym: 'SOL' }, basis: { role: 'entry_scout' }, px: 143.2 }),
  decision(8, 6, { plan: 'PL-A1', action: 'tighten_stop', why: 'Первая цель взята, стоп подтянут в безубыток плюс комиссия.', args: { sym: 'SUI', stop: 2.892 }, basis: { role: 'position_manager' }, px: 2.931, r: 1.8 }),
  decision(7, 11, { actor: 'executor', kind: 'exec', what: 'plan_reject', action: 'reject', why: 'По DOGE уже есть план: второй не принят.', ok: false, code: 'SYM_BUSY', denied: true, plan: 'PL-B4', args: { sym: 'DOGE' } }),
  decision(6, 18, { plan: 'PL-A2', action: 'hold', why: 'Шорт ETH: тезис жив, цена под уровнем, до стопа 1.5%. Держать.', args: { sym: 'ETH' }, basis: { role: 'position_manager' }, px: 2255, r: 0.25 }),
  decision(5, 25, { action: 'skip', why: 'XRP: всплеск оборота без выхода из диапазона, спред шире нормы. Пропуск.', args: { sym: 'XRP' }, basis: { role: 'entry_scout' }, noop: true }),
  decision(4, 40, { plan: 'PL-B2', action: 'propose', why: 'Шорт BTC от 68400: ложный пробой на 15м, объём на откате падает.', args: { sym: 'BTC' }, basis: { role: 'entry_scout' }, px: 68250 }),
  decision(3, 64, { action: 'review', why: 'Сделка T-101 закрыта по стопу: вход до подтверждения. Урок-гипотеза: ждать закрытия 5м свечи.', basis: { role: 'reviewer' } }),
  decision(2, 90, { action: 'brief', why: 'Утро: боковик, BTC в диапазоне 67.8-68.9k, новых входов не больше 4.', basis: { role: 'morning_analyst' } }),
]

/** Набор тем для скриншотов: день идёт на бумаге, счёт 1000 USDT. */
export async function demoTopics() {
  const t = makeRunning(await defaultTopics())
  t.home = structuredClone(t.home)
  const m = t.home.money
  for (const k of ['under_risk', 'day_room', 'day_limit', 'pnl_today', 'open_pnl']) m[k] = scale(m[k])
  m.equity = EQUITY
  for (const p of t.home.positions) for (const k of ['qty', 'upnl', 'risk', 'pnl_at_sl']) p[k] = scale(p[k])
  t.plans = structuredClone(t.plans)
  for (const p of [...t.plans.items, ...t.home.plans.near]) p.risk = scale(p.risk)
  const base = t.plans.items[0]
  const now = Date.now() / 1000
  const more = [
    { id: 'PL-A1', sym: 'SUI', side: 'long', col: 'in_trade', status: 'entered', level: 2.807, price: 2.931, stop: 2.892, target: 2.982, targets: [2.9, 2.945, 2.982], dist_pct: null, eta_at: null, risk: 9.5, net_rr: 2.6, text: 'в сделке: первая цель взята, стоп подтянут', missing: [], met_n: 1, ts_entered: now - 5400 },
    { id: 'PL-A2', sym: 'ETH', side: 'short', col: 'in_trade', status: 'entered', level: 2262, price: 2255, stop: 2290, target: 2200, targets: [2230, 2200], dist_pct: null, eta_at: null, risk: 14, net_rr: 2.2, text: 'в сделке', missing: [], met_n: 1, leverage: 3, ts_entered: now - 780 },
    { id: 'PL-B5', sym: 'LINK', side: 'long', col: 'watching', level: 14.1, price: 14.62, stop: 13.85, target: 15.2, targets: [14.8, 15.2], dist_pct: 3.6, eta_at: null, risk: 8, net_rr: 2.3, text: 'далеко от уровня: ждём отката к 14.10' },
    { id: 'PL-A0', sym: 'XRP', side: 'long', col: 'closed', status: 'closed', level: 0.518, price: 0.527, stop: 0.512, target: 0.527, targets: [0.527], dist_pct: null, eta_at: null, risk: 9, net_rr: 1.9, text: 'закрыт по тейку', missing: [], close_reason: 'tp', close_pnl: 8.1, ts_closed: now - 30 * 3600 },
  ].map((o) => ({ ...structuredClone(base), ...o }))
  t.plans.items = [...t.plans.items, ...more]
  t.plans.total = t.plans.items.length
  t.plans.counts = { waiting: 3, watching: 1, in_trade: 2, closed: 1 }
  // сессия дня и запас до лимитов (форма как у src/tb/dash2/topics.py: system.session, system.room, home.limits)
  const sessStart = new Date((now - 5 * 3600) * 1000).toISOString()
  t.system = {
    ...t.system,
    account: { equity: EQUITY, hwm: HWM, drawdown: Math.round((1 - EQUITY / HWM) * 1000) / 1000, ladder_mult: 1, venues: { bybit: { equity: EQUITY, free: 812.5 } } },
    session: {
      id: 'S-20261102-1', type: 'day', budget_usdt: 30, budget_used: 9, trades: 3, max_trades: 10, entries_allowed: true, block_reasons: [], realized: 12, open_risk: 23,
      perf_mult: 1, perf_n: 12, start: sessStart, thesis: 'Боковик: работа от уровней диапазона, не больше 4 новых планов, стоп за экстремум отката.', expires_at: null,
    },
    room: {
      allowed: true, paper: true, reasons: [], rows: [
        { id: 'daily', label: 'Дневной лимит убытка', limit: 60, used: 20, room: 40, frac: 0.67, level: 'ok', unit: 'USDT', help: 'сколько ещё можно потерять сегодня' },
        { id: 'session', label: 'бюджет убытка сессии', limit: 30, used: 9, room: 21, frac: 0.7, level: 'ok', unit: 'USDT', help: 'остаток бюджета сессии' },
        { id: 'slots', label: 'слоты позиций', limit: 3, used: 2, room: 1, frac: 0.33, level: 'warn', unit: 'шт', help: 'одновременно открытых позиций' },
        { id: 'stops', label: 'Стопов подряд', limit: 3, used: 1, room: 2, frac: 0.67, level: 'ok', unit: 'шт', help: 'после трёх подряд пауза' },
        { id: 'trades', label: 'Сделок за сессию', limit: 10, used: 3, room: 7, frac: 0.7, level: 'ok', unit: 'шт', help: 'предел сделок сессии' },
      ],
    },
    sessions: [
      { id: 'S-20261102-1', type: 'day', ts: sessStart, stopped: null, stop_note: null, thesis: 'Боковик: работа от уровней диапазона' },
      { id: 'S-20261101-2', type: 'day', ts: new Date((now - 30 * 3600) * 1000).toISOString(), stopped: new Date((now - 20 * 3600) * 1000).toISOString(), stop_note: 'owner', thesis: 'Пробои на объёме' },
    ],
    rejects: { n: 8, rejects: 1, codes: [{ code: 'SYM_BUSY', n: 1 }] },
  }
  t.home.limits = [
    { id: 'daily', label: 'Дневной лимит', limit: 60, used: 20, room: 40, unit: 'USDT', level: 'ok' },
    { id: 'session', label: 'Бюджет сессии', limit: 30, used: 9, room: 21, unit: 'USDT', level: 'ok' },
    { id: 'slots', label: 'Слоты', limit: 3, used: 2, room: 1, unit: 'шт', level: 'warn' },
    { id: 'stops', label: 'Стопы подряд', limit: 3, used: 1, room: 2, unit: 'шт', level: 'ok' },
    { id: 'trades', label: 'Сделки', limit: 10, used: 3, room: 7, unit: 'шт', level: 'ok' },
  ]
  t.home.limits_paper = true
  t.home.pending = {
    total: 2, items: [
      { id: 'LP-1', sym: 'AVAX', side: 'long', mode: 'paper', price: 24.6, cur: 25.02, stale: false, dist_pct: -1.68, sl: 24.1, tp: 25.9, risk: 8, lev: 5, ttl_min: 60, expires_at: now + 41 * 60 },
      { id: 'LP-2', sym: 'NEAR', side: 'short', mode: 'paper', price: 5.48, cur: 5.39, stale: false, dist_pct: 1.67, sl: 5.6, tp: 5.2, risk: 7.5, lev: 4, ttl_min: 30, expires_at: now + 6 * 60 },
    ],
  }
  // карта рынка: правдоподобные цены вместо 100/(i+1) фикстуры e2e
  const PX = { BTCUSDT: 68250, ETHUSDT: 2255, SOLUSDT: 143.2, DOGEUSDT: 0.1812, XRPUSDT: 0.523, BNBUSDT: 585.4, ADAUSDT: 0.352, AVAXUSDT: 25.02, LINKUSDT: 14.62, SUIUSDT: 2.931, TONUSDT: 5.12, NEARUSDT: 5.39, APTUSDT: 8.41, ARBUSDT: 0.587, OPUSDT: 1.62, INJUSDT: 21.3, PEPEUSDT: 0.0000101, WIFUSDT: 1.94, '1000BONKUSDT': 0.0231, FARTCOINUSDT: 0.71, TRUMPUSDT: 9.8, HYPEUSDT: 31.5, ENAUSDT: 0.48, LTCUSDT: 84.1, DOTUSDT: 4.37, ATOMUSDT: 4.92, FILUSDT: 3.61, AAVEUSDT: 172.4, UNIUSDT: 8.07, SEIUSDT: 0.311 }
  t.market = structuredClone(t.market)
  for (const r of t.market.heat.rows) r.price = PX[r.sym] ?? r.price
  for (const r of [...t.market.hot.top, ...t.market.hot.blocked]) r.price = PX[r.sym] ?? r.price
  // результат по моделям (тема models): три периода
  const mrow = (id, n, wins, pnl, usd, calls, o = {}) => ({ id, calls, usd, err_rate: 0.02, fmt_err_rate: 0.04, lat_p50_ms: 1700, n, wins, pnl, avg_r: n ? Math.round((pnl / n / 9) * 100) / 100 : null, net: Math.round((pnl - usd) * 100) / 100, pnl_per_100_calls: calls ? Math.round((pnl / calls) * 10000) / 100 : null, pnl_per_usd: usd ? Math.round((pnl / usd) * 10) / 10 : null, reliable: n >= 30, ...o })
  const period = (days, k) => ({ days, min_n: 30, note: '', rows: [mrow('deepseek-flash', 9 * k, 5 * k, 31.5 * k, 0.21 * k, 180 * k), mrow('gpt-oss-20b', 3 * k, 2 * k, 7.2 * k, 0, 95 * k, { lat_p50_ms: 4200, fmt_err_rate: 0.11 }), mrow('deepseek-pro', 0, 0, 0, 0.04 * k, 12 * k)] })
  t.models = { mode: 'paper', periods: { 1: period(1, 1), 7: period(7, 4), 30: period(30, 11) } }
  // события шины (облако событий справа внизу)
  const ev = (seq, minAgo, type, sym, plan, d, pri = 'info') => ({ seq, ts: new Date((now - minAgo * 60) * 1000).toISOString(), type, pri, plan, sym, d })
  t.events = {
    last_seq: 6, items: [
      ev(6, 1, 'scanner_signal', 'SOLUSDT', null, { score: 8, reasons: ['всплеск оборота'] }, 'wake'), ev(5, 6, 'milestone', 'SUIUSDT', 'PL-A1', { r: 1.5 }, 'wake'),
      ev(4, 13, 'entry_filled', 'ETHUSDT', 'PL-A2', { px: 2262 }), ev(3, 40, 'position_closed', 'XRPUSDT', 'PL-A0', { pnl: 8.1, reason: 'tp' }),
      ev(2, 64, 'plan_armed', 'BTCUSDT', 'PL-B2', {}), ev(1, 90, 'day_started', null, null, { day: 'D-20261102-1' }),
    ],
  }
  // роли мозга: все шесть, со статусом и бюджетом
  t.settings = structuredClone(t.settings)
  const [r0, r1, r2] = t.settings.roles
  const extra = [
    { ...structuredClone(r1), role: 'morning_analyst', target: 'A.morning_analyst', today: { usd: 0.011, calls: 1, errors: 0, budget_left_usd: 0.49 }, budget_day_usd: 0.5 },
    { ...structuredClone(r2), role: 'day_auditor', target: 'shared.day_auditor', today: { usd: 0, calls: 0, errors: 0, budget_left_usd: 0.3 }, budget_day_usd: 0.3 },
    { ...structuredClone(r2), role: 'news_reader', target: 'shared.news_reader', today: { usd: 0, calls: 14, errors: 0, budget_left_usd: null }, budget_day_usd: null },
  ]
  t.settings.roles = [r0, r1, extra[0], r2, extra[1], extra[2]]
  // досье: лента и содержимое (ответ на op brief)
  const bid = (h, n) => `B-20261102-${h}-${n}`
  t.briefs = { total: 3, items: [
    { id: bid('1640', '01'), ts: new Date((now - 4 * 60) * 1000).toISOString(), scope: 'event', trunc: false, allowed: true, positions: 2, plans: 3 },
    { id: bid('1520', '01'), ts: new Date((now - 85 * 60) * 1000).toISOString(), scope: 'event', trunc: false, allowed: true, positions: 1, plans: 3 },
    { id: bid('0602', '01'), ts: new Date((now - 10.6 * 3600) * 1000).toISOString(), scope: 'start', trunc: false, allowed: true, positions: 0, plans: 0 },
  ] }
  // закрытые сделки, накопленный PnL, лента решений, день
  const items = TRADES.map((x, i) => demoTrade(i + 1, ...x))
  const by = (f) => Object.values(items.reduce((a, x) => { const k = f(x); (a[k] ??= { name: k, n: 0, pnl: 0 }); a[k].n += 1; a[k].pnl = Math.round((a[k].pnl + x.pnl) * 100) / 100; return a }, {}))
  let cum = 0
  const curvePts = [...items].sort((a, b) => a.ts_close - b.ts_close).map((x) => [x.ts_close, Math.round((cum += x.pnl) * 100) / 100, x.pnl, x.sym])
  t.home.curve = { base: 0, points: curvePts, total: curvePts.length }
  t.brain = structuredClone(t.brain)
  for (const r of t.brain.roles) if (r.last) r.last.fallback_from = null
  t.trades = { mode: 'paper', total: items.length, items, reasons: by((x) => x.reason), errs: [{ name: 'late_entry', n: 1, pnl: -9 }], unreviewed: 2 }
  t.decisions = { mode: 'paper', total: DECISIONS.length, plans: ['PL-B1', 'PL-A1', 'PL-A2', 'PL-B2', 'PL-B4'], day: { n: DECISIONS.length, brain: 6, rejects: 1, auto: 0, skips: 1, protect: 0, reject_codes: [{ code: 'SYM_BUSY', n: 1 }] }, items: DECISIONS }
  t.day = await runningDay()
  return t
}

/** Содержимое досье по id (форма Brief из frontend/src/entities/brief/model/types.ts). */
export function demoBrief(id) {
  const old = !id.endsWith('1640-01')
  return {
    id, ok: true, data: {
      brief_id: id, ts: new Date().toISOString(), scope: 'event', trunc: false,
      system: { exec: { alive: true, age_s: 2, env: 'paper', mode: 'paper', observe: false, reconcile_age_s: 40 }, kill: false, scanner: { alive: true, age_s: 25 }, feed: { alive: true }, unacked: 0 },
      account: { equity: EQUITY, free: 812.5, dd_pct: 4, dd_mult: 1, day_pnl: old ? 3.6 : 12, day_trades: old ? 2 : 3, stops_today: 1, stop_streak: 0 },
      session: { id: 'S-20261102-1', type: 'day', min_left: null, budget: 30, trades: 3, max_trades: 10, realized: 12, open_risk: 23 },
      room: { allowed: true, reasons: [], next_risk_usdt: 9, limited_by: 'slots', slots_free: 1, daily_room: 40, session_room: 21, cooldowns: [{ sym: 'DOGEUSDT', min_left: 18 }], pause_min_left: null },
      positions: [
        { plan: 'PL-A1', sym: 'SUIUSDT', side: 'long', mode: 'paper', avg: 2.807, px: 2.931, sl: 2.892, tp: 2.982, liq: 2.3, r: 1.8, mae_r: -0.2, mfe_r: 2.1, age_min: 90, targets: [{ id: 'T1', px: 2.9, state: 'filled' }, { id: 'T2', px: 2.945, state: 'resting' }] },
        { plan: 'PL-A2', sym: 'ETHUSDT', side: 'short', mode: 'paper', avg: 2262, px: 2255, sl: 2290, tp: 2200, liq: 2900, r: 0.25, mae_r: -0.3, mfe_r: 0.5, age_min: 13 },
      ],
      plans: [
        { id: 'PL-B1', sym: 'SOLUSDT', side: 'long', status: 'armed', rev: 1, entry: 'закрытие 5м выше 142.5', dist_pct: 0.49, ttl_left_min: 140, own_thesis: 'Откат к поддержке' },
        { id: 'PL-B2', sym: 'BTCUSDT', side: 'short', status: 'armed', rev: 2, entry: 'ложный пробой 68400', dist_pct: 0.22, ttl_left_min: 95 },
        { id: 'PL-B5', sym: 'LINKUSDT', side: 'long', status: 'armed', rev: 1, entry: 'откат к 14.10', dist_pct: 3.6, ttl_left_min: 300 },
      ],
      market: { btc: { px: 68250, ret_5m_pct: -0.08, ret_1h_pct: 0.31, ret_24h_pct: 1.2, range_5m_pct: 0.18, funding_8h_pct: 0.01, spread_bps: 0.1, regime: 'range' }, eth: { px: 2255, ret_5m_pct: -0.12, ret_1h_pct: -0.4, ret_24h_pct: -0.9, range_5m_pct: 0.22, funding_8h_pct: 0.008, spread_bps: 0.3, regime: 'trend_down' }, utc_session: 'eu', risk_off: false, news_mode: 'normal', blackout: null, context_stale_s: 90 },
      limits: { risk_per_trade_usdt: 9, daily_loss_usdt: 60, daily_left_usdt: 40, max_open_positions: 3, max_new_plans: 4, size_mult: 1, slots: 1 },
      risk_level: { level: 3, carry: { policy: 'agent', max_positions: 1 } },
      economics: { llm_usd_day: 0.21, trades_day: 3, trades_left: 7, min_trades_day: 2 },
      hot: { top: [{ sym: 'SOLUSDT', score: 8, reasons: ['всплеск оборота', 'выход из диапазона'], px: 143.2, ret_5m_pct: 0.4, spread_bps: 1.1, funding_pct: 0.01, lot_usdt: 6, tradable: true }, { sym: 'AVAXUSDT', score: 6, reasons: ['всплеск оборота'], px: 25.02, ret_5m_pct: 0.3, spread_bps: 2.4, funding_pct: 0.005, lot_usdt: 5, tradable: true }], stale_s: 25 },
      intel: { sentiment: { fng: { v: 54, cls: 'neutral', prev: 51 }, dom: { v: 57.1 } }, calendar: { next: [{ type: 'CPI', in_min: 180 }] }, degraded: [], age_s: 300 },
      levels: { SOLUSDT: { px: 143.2, atr_5m: 0.42, day: { lo: 139.9, hi: 146.1 }, prev_day: { lo: 137.5, hi: 145.2 }, week: { lo: 131, hi: 152 }, swing_5m: [142.5, 144.8] } },
      ideas: { active: [{ id: 'I1', sym: 'SOLUSDT', side: 'long', level: 142.5, dist_pct: 0.49, status: 'watching', age_h: 3 }] },
      recent: [{ seq: 6, type: 'scanner_signal', sym: 'SOLUSDT', d: { score: 8 } }, { seq: 5, type: 'milestone', sym: 'SUIUSDT', d: { r: 1.5 } }],
      lessons: ['ждать закрытия 5м свечи над уровнем, а не входить на касании', 'не открывать вторую позицию в ту же сторону по коррелированной монете'],
      checklist: [{ id: 'C1', text: 'стоп и тейк ставятся при открытии' }, { id: 'C2', text: 'RR после издержек не ниже 2' }],
    },
  }
}

/** Ответ на op usage (расход LLM): разрез по моделям, провайдерам или ролям. */
export function demoUsage(days, by) {
  const k = days === 1 ? 1 : days === 7 ? 5.5 : 21
  const g = (id, calls, usd, o = {}) => ({ id, calls: Math.round(calls * k), in: Math.round(calls * k * 3200), in_hit: Math.round(calls * k * 1900), out: Math.round(calls * k * 260), reasoning: 0, usd: Math.round(usd * k * 10000) / 10000, errors: 0, fmt_errors: 1, fallbacks: 0, lat_p50_ms: 1700, lat_p95_ms: 4100, err_rate: 0.01, fmt_err_rate: 0.03, cache_hit_rate: 0.59, ...o })
  const groups = by === 'provider' ? [g('deepseek', 190, 0.25), g('local', 110, 0)]
    : by === 'role' ? [g('entry_scout', 120, 0.09), g('position_manager', 60, 0.08), g('reviewer', 40, 0), g('news_reader', 70, 0), g('morning_analyst', 6, 0.05), g('day_auditor', 4, 0.03)]
      : [g('deepseek-flash', 180, 0.21), g('gpt-oss-20b', 110, 0, { lat_p50_ms: 4200 }), g('deepseek-pro', 10, 0.04)]
  const usd = groups.reduce((a, x) => a + x.usd, 0)
  const calls = groups.reduce((a, x) => a + x.calls, 0)
  const toks = groups.reduce((a, x) => a + x.in + x.out, 0)
  const now = Date.now() / 1000
  return {
    days, by, ok: true, data: {
      total: { usd: Math.round(usd * 10000) / 10000, toks, calls }, today: { usd: 0.25, toks: 1000000, calls: 300 }, groups,
      budget: [{ state: 'ok', scope: 'all', key: 'day_usd', used: 0.25, cap: 2, until_ts: now + 7 * 3600 }, { state: 'ok', scope: 'deepseek', key: 'hour_usd', used: 0.04, cap: 1, until_ts: now + 1800 }],
    },
  }
}
