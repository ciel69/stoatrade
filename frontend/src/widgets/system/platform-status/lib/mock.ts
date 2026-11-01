import type { BrainSnapshot } from '@/entities/brain'
import type { CtlSnapshot } from '@/entities/control'
import type { NewsSnapshot } from '@/entities/headline'
import type { MarketSnapshot } from '@/entities/market'
import type { SysNode, SystemSnapshot } from '@/entities/system'
import type { StatusInput } from './types'

export type StatusMockKind = 'mock' | 'mock-warn' | 'mock-bad'

/** Читает ?status=mock|mock-warn|mock-bad: образцовые состояния для проверки глазами без сервера. */
export function mockKind(search: string): StatusMockKind | null {
  const v = new URLSearchParams(search).get('status')
  return v === 'mock' || v === 'mock-warn' || v === 'mock-bad' ? v : null
}

const node = (o: Partial<SysNode> & Pick<SysNode, 'id' | 'label' | 'level'>): SysNode => ({ ...o })

/** Образцовые данные платформы (вымышлены). mock: всё в норме; mock-warn: новости и карта отстают; mock-bad: потеряна связь с биржей и мозг не отвечает. */
export function mockStatus(kind: StatusMockKind, now: number): StatusInput {
  const warn = kind === 'mock-warn'
  const bad = kind === 'mock-bad'
  const nodes: SysNode[] = [
    node({ id: 'exec', label: 'Исполнитель', level: 'ok', text: 'paper · pid 4120 · v0.9', since: now - 3 * 3600 }),
    node({ id: 'net', label: 'Связь с биржей', level: bad ? 'bad' : 'ok', text: bad ? 'WS Bybit не отвечает' : 'public ok' }),
    node({ id: 'recon', label: 'Сверка с биржей', level: bad ? 'warn' : 'ok', at: now - (bad ? 240 : 40) }),
    node({ id: 'events', label: 'Шина событий', level: 'ok', at: now - 20, text: 'не подтверждено wake: 0' }),
    node({ id: 'scan', label: 'Сканер монет', level: warn ? 'warn' : 'ok', at: now - (warn ? 400 : 25) }),
    node({ id: 'feed', label: 'Фид цен', level: 'ok', at: now - 3 }),
    node({ id: 'context', label: 'Контекст рынка', level: 'ok', at: now - 90, text: 'risk_off нет' }),
    node({ id: 'session', label: 'Сессия', level: 'ok', text: 'day · до 21:00' }),
  ]
  const system: SystemSnapshot = {
    mode: 'paper', level: bad ? 'bad' : warn ? 'warn' : 'ok', kill: null, nodes, warnings: [],
    session: null, room: { allowed: true, reasons: [], rows: [] },
    account: { equity: 39, hwm: 40, drawdown: 0, ladder_mult: 1, venues: {} },
    sources: { feed: [{ id: 'bybit', state: 'ok', connected: true, reconnects: 0, errors: 0, error: null }, { id: 'binance', state: bad ? 'error' : 'ok', connected: !bad, reconnects: 2, errors: bad ? 5 : 0, error: bad ? 'тайм-аут' : null }], context: { fng: true, funding: true }, degraded: [] },
    exec: { present: true, plans: {}, positions: 2, lag_ms: 10, hold: null, consumers: {} }, rejects: { n: 0, rejects: 0, codes: [] }, sessions: [],
  }
  const brain: BrainSnapshot = {
    status: bad ? 'down' : 'idle', alive: !bad, protect_reason: null, health: 'green', rev: 12, inflight: 0, queue: {}, cooling: [], budget: null, errors: 0, active: null, decisions: [],
    roles: [{ role: 'trader', primary: null, fallback: [], last: { profile: 'deepseek.fast', provider: 'deepseek', ok: true, err: null, lat_ms: 1800, t: now - 60, fallback_from: null }, today: { usd: 0.4, calls: 30, errors: 0, budget_left_usd: 2 }, live_risk: false }],
  }
  const market: MarketSnapshot = {
    heat: { available: true, at: now - (warn ? 420 : 20), rows: [] }, liq: { available: true, at: now - 10, totals: {}, syms: [] }, flow: { available: true, at: now - 10, xex_ok: true, syms: [] },
    hot: { available: true, at: now - 20, warm: true, stats: {}, top: [], blocked: [] }, coins: { ok: true, at: now - 3, rows: [] },
  }
  const news: NewsSnapshot = {
    items: [{ ref: 'n1', src: 'coindesk', ts: now - (warn ? 12 * 60 + 7200 : 5 * 60), title: 'Образец', snip: '', domain: 'example.org', susp: false, seen: 1, cls: null, struct: false }],
    sources: [{ id: 'coindesk', ok: true, fails: 0, verified: true, degraded: false }, { id: 'cointelegraph', ok: !warn && !bad, fails: warn || bad ? 4 : 0, verified: true, degraded: warn }],
    degraded: [], calendar: { next: [{ id: 'cpi', type: 'CPI', impact: 3, verified: true, at: now + 3 * 3600 }], blackout: null }, reader: { enabled: true, status: 'ok', stale: false },
  }
  const ctl: CtlSnapshot = {
    alive: true, configured: true, platform: { state: 'running', mode: 'paper', session: 'day', health: 'green', day_id: 'D-mock', trading: true, observe: false }, step: null, kill: null,
    risk: { level: 3, name: 'Базовый' }, busy: false, banner: null, day: null,
    checks: [
      { id: 'H1', name: 'Чистый KILL', sev: 'red', ok: true, msg: '' },
      { id: 'H3', name: 'Связь', sev: 'red', ok: !bad, msg: bad ? 'нет приватного канала биржи' : 'исполнитель жив, сверка свежая' },
      { id: 'H6', name: 'Мозг доступен', sev: 'red', ok: !bad, msg: bad ? 'мозг не отвечает' : '' },
      { id: 'W1', name: 'W_PAPER_HISTORY', sev: 'amber', ok: !warn, msg: 'Мало paper-истории: рекомендуется 30 сделок и 7 дней' },
    ],
  }
  return { now, link: 'open', home: null, system, brain, market, news, ctl }
}
