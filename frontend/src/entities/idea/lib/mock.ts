import type { IdeaItem, IdeasSnapshot } from '../model/types'

/** Образцовый снимок для проверки глазами (?ideas=mock): сервер не нужен, данные вымышленные. */
export function mockIdeas(now: number): IdeasSnapshot {
  const base = { stop: null, tp: null, tag: null, price: null, shift_pct: null, ts: null, plan_id: null, trade_id: null, trade: null, mark_notes: [], outcomes: {}, zone: false, note: null, level: null, now_price: null, dist_level_pct: null }
  const mk = (o: Partial<IdeaItem> & Pick<IdeaItem, 'id' | 'sym'>): IdeaItem => ({ side: 'long', status: 'watching', ts_epoch: now - 3600, expires_epoch: now + 5 * 3600, ...base, ...o })
  const items: IdeaItem[] = [
    mk({ id: 'I1', sym: 'SOL', level: 142.5, stop: 139.8, tp: 151, now_price: 143.2, dist_level_pct: 0.49, tag: 'level_reclaim', note: 'Поддержка 142.5 держится на 1ч, вход только после закрытия 5м свечи над уровнем и всплеска оборота.' }),
    mk({ id: 'I2', sym: 'ETH', side: 'short', level: 2262, stop: 2290, tp: 2180, now_price: 2243, dist_level_pct: -0.84, tag: 'trend_pullback', expires_epoch: now + 16 * 3600 + 19 * 60, note: 'res above price' }),
    mk({ id: 'I3', sym: 'DOGE', level: 0.1812, stop: 0.1775, tp: 0.19, now_price: 0.1905, dist_level_pct: 5.1, tag: 'sweep_reject', note: 'sup below price | px 0.1905, до res 0.1930 1.3%, a5 1.2%' }),
    mk({ id: 'I7', sym: 'SOL', side: 'short', level: 150.4, stop: 152, tp: 146, now_price: 150.3, dist_level_pct: -0.07, zone: true, tag: 'range_fade', expires_epoch: now + 1200, note: 'Сопротивление после слива; шорт от отката к уровню.' }),
    mk({ id: 'I4', sym: 'NIL', side: 'short', note: 'Заметка без уровня.', expires_epoch: null }),
    mk({ id: 'I5', sym: 'BTC', status: 'taken', ts_epoch: now - 7200, plan_id: 'PL-1', trade: { id: 'T1', status: 'closed', pnl: 0.42, r: 1.4, reason: 'tp' }, note: 'Откат к 1ч поддержке.' }),
    mk({ id: 'I6', sym: 'TRX', status: 'expired', ts_epoch: now - 90000, outcomes: { '15': { ret_pct: -0.1, dir_pct: -0.1, net_pct: -0.34 }, '60': { ret_pct: 0.6, dir_pct: 0.6, net_pct: 0.36 } }, note: 'Не дошла до уровня.' }),
  ]
  return {
    available: true, counts: { watching: 4, idea: 1, taken: 1, expired: 1, auto_skipped: 120 }, items, auto_recent: [], pending: 4,
    stats: { costs: { taker_rt_pct: 0.24 }, horizons: [15, 60], groups: [], idea_groups: [], n_events: 120, agent: { total: items.length, taken_share: 1 / 6 }, pending: 4, comparisons: 0 },
  }
}
