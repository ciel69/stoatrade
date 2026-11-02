import type { HomeSnapshot, NearPlan, PlansSnapshot, Position } from '../model/types'

/** Образцовые данные для проверки глазами (?trading=mock): сервер не нужен, всё вымышлено. */
const position = (o: Partial<Position> & Pick<Position, 'sym' | 'side'>): Position => ({
  plan: null, mode: 'paper', lev: 5, qty: 1, avg: null, price: null, stale: false, stop: null, stop0: null, targets: [], r: 0, upnl: 0, pnl_pct: null, risk: null, pnl_at_sl: null, held_s: 600, hold_left_s: null, liq: null, ...o,
})

const plan = (o: Partial<NearPlan> & Pick<NearPlan, 'id' | 'sym'>): NearPlan => ({
  side: 'long', mode: 'paper', col: 'waiting', text: 'ждём закрытия 5м свечи над уровнем', dist_pct: 0.4, eta_at: null, entry_state: null, blocked_why: null, net_rr: 2.1, risk: 0.9, status: 'armed', setup: 'support', thesis: 'Откат к поддержке',
  entry_text: 'закрытие 5м выше уровня', missing: ['закрытие 5м выше уровня'], met_n: 0, leaf_n: 1, seq: null, level: null, price: null, kind: 'price_beyond', dir: null, beyond: null, last_bar: null, tf: null, ttl_at: null,
  hold: null, hold_reason: null, stop: null, target: null, targets: [], leverage: 5, chase_bps: null, chase_max_bps: null, rev: 1, exp: null, var: null, hyp: null, src: null, parent: null, session_type: null, created_by: null,
  ts_created: null, ts_armed: null, ts_entered: null, ts_closed: null, close_reason: null, close_pnl: null, warnings: [], ...o,
})

export function mockHome(): HomeSnapshot {
  const positions: Position[] = [
    position({ plan: 'PL-A1', sym: 'SUI', side: 'long', lev: 5, qty: 14.1, avg: 2.807, price: 2.931, stop: 2.892, stop0: 2.74, targets: [{ price: 2.9, state: 'filled' }, { price: 2.945, state: 'resting' }, { price: 2.982, state: 'resting' }], r: 1.8, upnl: 1.75, pnl_pct: 22, risk: 0.95, pnl_at_sl: 1.15, held_s: 5400 }),
    position({ plan: 'PL-A2', sym: 'ETH', side: 'short', lev: 3, qty: 0.05, avg: 2262, price: 2255, stop: 2290, stop0: 2290, targets: [{ price: 2230, state: 'resting' }, { price: 2200, state: 'resting' }], r: 0.25, upnl: 0.35, pnl_pct: 3, risk: 1.4, pnl_at_sl: -1.4, held_s: 780 }),
  ]
  const plans = mockPlans().items
  return {
    mode: 'paper', modes: ['paper', 'live', 'testnet', 'all'], platform: { state: 'running', mode: 'paper', trading: true, level: 'ok', items: [] },
    money: { under_risk: 2.3, day_room: 4, day_limit: 6, pnl_today: 1.2, trades_today: 3, open_pnl: 2.1, equity: 39 }, limits: [], limits_paper: true, allowed: true,
    positions, positions_total: positions.length, plans: { counts: { waiting: 2, watching: 1, in_trade: 2, closed: 0 }, total: plans.length, near: plans },
  }
}

export function mockPlans(): PlansSnapshot {
  const now = Date.now() / 1000
  const items: NearPlan[] = [
    plan({ id: 'PL-B1', sym: 'SOL', side: 'long', level: 142.5, price: 143.2, stop: 139.8, target: 151, targets: [146, 151], dist_pct: 0.49, risk: 0.9, net_rr: 2.8, tf: '5м', eta_at: now + 140 }),
    plan({ id: 'PL-B2', sym: 'BTC', side: 'short', level: 68400, price: 68250, stop: 68900, target: 67200, targets: [67800, 67200], dist_pct: 0.22, risk: 1.1, net_rr: 2.4 }),
    plan({ id: 'PL-B3', sym: 'DOGE', side: 'long', level: 0.1812, price: 0.1905, stop: 0.1775, target: 0.19, targets: [0.19], dist_pct: 5.1, risk: 0.7, net_rr: 1.9, status: 'draft' }),
  ]
  return { mode: 'paper', counts: { waiting: 3, watching: 0, in_trade: 0, closed: 0 }, total: items.length, items }
}
