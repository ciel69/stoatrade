import type { NewsItem, NewsSnapshot } from '../model/types'

export const NEWS_MOCKS = ['mock', 'mock-reader-off', 'mock-source-down', 'mock-blackout', 'mock-limit'] as const
export type NewsMock = (typeof NEWS_MOCKS)[number]
export const isNewsMock = (v: unknown): v is NewsMock => typeof v === 'string' && (NEWS_MOCKS as readonly string[]).includes(v)

const item = (ref: string, src: string, ago: number, now: number, title: string, cls: NewsItem['cls']): NewsItem => (
  { ref, src, ts: now - ago, title, snip: '', domain: 'example.org', susp: false, seen: 1, cls, struct: false }
)

/**
 * Образцовые данные новостей без сервера (?newsmock=...). Заголовки выдуманы. mock: всё нормально; mock-reader-off: читатель выключен; mock-source-down: один источник не отвечает;
 * mock-blackout: идёт окно блокировки; mock-limit: читатель упёрся в лимит расходов. Реальных команд в этом режиме нет.
 */
export function mockNews(kind: NewsMock, now: number = Date.now() / 1000): NewsSnapshot {
  const off = kind === 'mock-reader-off'
  const limit = kind === 'mock-limit'
  const cls = (imp: number, type: string, dir: 'up' | 'down', syms: string[]): NewsItem['cls'] => (off ? null : { st: 'ok', type, imp, ir: imp, dir, syms, ttl_min: 120, conf: 0.7 })
  return {
    items: [
      item('N-1', 'coindesk', 600, now, 'Образец: биржа объявила о листинге нового токена', cls(4, 'listing', 'up', ['SOL'])),
      item('N-2', 'theblock', 2400, now, 'Образец: регулятор опубликовал разъяснения по стейблкоинам', cls(3, 'regulation', 'down', [])),
      item('N-3', 'coindesk', 5400, now, 'Образец: обзор недели на рынке деривативов', limit ? null : cls(2, 'other', 'up', ['BTC'])),
    ],
    sources: [
      { id: 'coindesk', ok: true, fails: 0, verified: true, degraded: false, at: now - 180 },
      { id: 'theblock', ok: kind !== 'mock-source-down', fails: kind === 'mock-source-down' ? 5 : 0, verified: true, degraded: false, at: now - (kind === 'mock-source-down' ? 5400 : 240) },
      { id: 'decrypt', ok: true, fails: 0, verified: false, degraded: false, at: now - 300 },
      { id: 'cryptoslate', ok: true, fails: 0, verified: true, degraded: false, at: now - 120 },
    ],
    degraded: [],
    calendar: {
      next: [
        { id: 'cpi', type: 'CPI', impact: 3, verified: true, at: now + 2 * 3600 },
        { id: 'fomc', type: 'FOMC', impact: 3, verified: false, at: now + 26 * 3600 },
      ],
      blackout: kind === 'mock-blackout' ? { name: 'FOMC', from: now - 600, to: now + 3000, verified: false } : null,
    },
    reader: {
      enabled: !off, present: !off, status: off ? 'off' : limit ? 'limit' : 'ok', stale: false, profile: off ? null : 'deepseek.flash',
      calls_h: limit ? 12 : 3, max_calls_h: 12, usd_h: limit ? 0.0198 : 0.0004, usd_day: limit ? 0.05 : 0.002, max_usd_day: 0.05, pending: limit ? 9 : 4, ok_n: 120, fail_n: 2, susp_n: 3,
      err: null, pause_until: null, last_ok: now - (limit ? 3000 : 90), at: now - 60,
    },
  }
}
