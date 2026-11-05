/**
 * Контракт темы `cmkt` и ответа `cmkt_sym_detail` (R1.7, задача 29; сервер: `src/tb/dash2/topics.py`, `src/tb/core/cmktdoc.py`).
 * Снимок старше 5 с сервер отдаёт как `ok: false, state: 'stale'` без чисел. Части ответа по монете могут отсутствовать:
 * порядок усечения — venues, liq, oi, funding, cvd, book (`trunc` перечисляет отброшенные части).
 */

export type CmktTier = 'deep' | 'light'

export interface CmktVenue {
  mkt: string
  code: string
  lag_ms: number | null
  age_s: number | null
}

export interface CmktCoin {
  sym: string
  tier: CmktTier | string
  focus_s: number | null
  cov: number | null
  mid: number | null
  flags: string[]
  n_ex: number | null
}

export interface CmktSnapshot {
  ok: boolean
  state: 'ok' | 'stale' | string
  /** Отметка снимка; возраст на экране считается по ней, серверный `age_s` не используется. */
  ts: number | null
  n_ex: { perp: number; spot: number }
  n_syms: number
  skew_ms: number | null
  warn: string[]
  venues: CmktVenue[]
  coins: CmktCoin[]
}

/** Полоса стакана: bid и ask в млн USD, imb — перекос стороны в процентах. */
export interface CmktBand {
  bid?: number
  ask?: number
  imb?: number
}

/** Вид рынка (`perp`, `spot`, `all`) -> полоса (`05`, `1`, `2`) -> значения. */
export type CmktBands = Record<string, Record<string, CmktBand>>

export interface CmktWall {
  musd?: number
  dist_pct?: number
  n_ex?: number
  age_s?: number
}

export interface CmktBook {
  bands?: CmktBands
  walls?: { bid?: CmktWall[]; ask?: CmktWall[] }
}

export interface CmktCvdValue {
  pct?: number
  musd?: number
}

/** Окно (`1m`, `5m`, `15m`) -> вид рынка -> доля и млн USD. */
export type CmktCvd = Record<string, Record<string, CmktCvdValue>>

export interface CmktOi {
  musd?: number
  chg_1m_pct?: number
  chg_5m_pct?: number
  chg_15m_pct?: number
  by_ex?: Record<string, number>
}

export interface CmktLiq {
  '5m'?: number[]
  '1h'?: number[]
  liq_cov?: number
  /** 1 — ликвидации пришли не со всех бирж; 0 — полные. */
  liq_partial?: number
  by_ex_5m?: Record<string, number[]>
}

export interface CmktFunding {
  w_pct?: number
  spread_pct?: number
  by_ex_8h_pct?: Record<string, number>
  prem_ps?: number
  prem_cb?: number
}

export interface CmktVenueCode {
  code: string
  lag_ms?: number
  age_s?: number
}

export interface CmktLevel {
  px: number | null
  pct: number | null
  musd: number
  cum: number
  mkts: string[]
}

export interface CmktLadderKind {
  mid: number | null
  best_bid: number | null
  best_ask: number | null
  spread_pct: number | null
  source: string
  n_mkts: number
  levels: { ask: CmktLevel[]; bid: CmktLevel[] }
  walls: { ask: CmktLevel[]; bid: CmktLevel[] }
  markets: Record<string, { code: string; lag_ms: number | null; mid: number | null }>
}

export interface CmktLadder {
  sym: string | null
  ts: number | null
  kinds: Partial<Record<'perp' | 'spot', CmktLadderKind>>
}

export interface CmktDetail {
  ok: boolean
  sym: string | null
  ts: number | null
  /** Как и в снимке, возраст считается по `ts`; поле оставлено в типе, но решением экрана не пользуемся. */
  age_s: number | null
  n_ex: number | null
  cov: number | null
  warn: string[]
  book?: CmktBook
  cvd?: CmktCvd
  oi?: CmktOi
  liq?: CmktLiq
  funding?: CmktFunding
  venues?: Record<string, CmktVenueCode>
  trunc?: string[]
  ladder?: CmktLadder
}

// ------------------------------------------------------------------ приведение сырых сообщений

const record = (v: unknown): Record<string, unknown> | null =>
  v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const text = (v: unknown): string | null => (typeof v === 'string' ? v : null)
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])

function venue(v: unknown): CmktVenue | null {
  const r = record(v)
  const mkt = r ? text(r.mkt) : null
  if (!r || !mkt) return null
  return { mkt, code: text(r.code) ?? 'OK', lag_ms: num(r.lag_ms), age_s: num(r.age_s) }
}

function coin(v: unknown): CmktCoin | null {
  const r = record(v)
  const sym = r ? text(r.sym) : null
  if (!r || !sym) return null
  return {
    sym,
    tier: text(r.tier) ?? 'light',
    focus_s: num(r.focus_s),
    cov: num(r.cov),
    mid: num(r.mid),
    flags: strings(r.flags),
    n_ex: num(r.n_ex),
  }
}

/** Тема `cmkt`: мусор не ломает страницу, неполные поля получают безопасные значения. */
export function readSnapshot(data: unknown): CmktSnapshot | null {
  const d = record(data)
  if (!d) return null
  const ne = record(d.n_ex) ?? {}
  const coins = (Array.isArray(d.coins) ? d.coins : []).map(coin).filter((c): c is CmktCoin => c !== null)
  const ok = d.ok === true
  return {
    ok,
    state: text(d.state) ?? (ok ? 'ok' : 'stale'),
    ts: num(d.ts),
    n_ex: { perp: Math.max(0, num(ne.perp) ?? 0), spot: Math.max(0, num(ne.spot) ?? 0) },
    n_syms: num(d.n_syms) ?? coins.length,
    skew_ms: num(d.skew_ms),
    warn: strings(d.warn),
    venues: (Array.isArray(d.venues) ? d.venues : []).map(venue).filter((v): v is CmktVenue => v !== null),
    coins,
  }
}

function band(v: unknown): CmktBand | null {
  const r = record(v)
  if (!r) return null
  const out: CmktBand = {}
  const bid = num(r.bid)
  const ask = num(r.ask)
  const imb = num(r.imb)
  if (bid !== null) out.bid = bid
  if (ask !== null) out.ask = ask
  if (imb !== null) out.imb = imb
  return Object.keys(out).length ? out : null
}

function bands(v: unknown): CmktBands | undefined {
  const r = record(v)
  if (!r) return undefined
  const out: CmktBands = {}
  for (const kind of ['perp', 'spot', 'all']) {
    const raw = record(r[kind])
    if (!raw) continue
    const rows: Record<string, CmktBand> = {}
    for (const label of ['05', '1', '2']) {
      const b = band(raw[label])
      if (b) rows[label] = b
    }
    if (Object.keys(rows).length) out[kind] = rows
  }
  return Object.keys(out).length ? out : undefined
}

function wall(v: unknown): CmktWall | null {
  const r = record(v)
  if (!r) return null
  const out: CmktWall = {}
  const musd = num(r.musd)
  const dist = num(r.dist_pct)
  const nEx = num(r.n_ex)
  const age = num(r.age_s)
  if (musd !== null) out.musd = musd
  if (dist !== null) out.dist_pct = dist
  if (nEx !== null) out.n_ex = nEx
  if (age !== null) out.age_s = age
  return Object.keys(out).length ? out : null
}

function wallList(v: unknown): CmktWall[] | undefined {
  if (!Array.isArray(v)) return undefined
  const rows = v.map(wall).filter((w): w is CmktWall => w !== null)
  return rows.length ? rows : undefined
}

function book(v: unknown): CmktBook | undefined {
  const r = record(v)
  if (!r) return undefined
  const b = bands(r.bands)
  const w = record(r.walls)
  const bid = w ? wallList(w.bid) : undefined
  const ask = w ? wallList(w.ask) : undefined
  const walls = bid || ask ? { ...(bid ? { bid } : {}), ...(ask ? { ask } : {}) } : undefined
  if (!b && !walls) return undefined
  return { ...(b ? { bands: b } : {}), ...(walls ? { walls } : {}) }
}

function cvd(v: unknown): CmktCvd | undefined {
  const r = record(v)
  if (!r) return undefined
  const out: CmktCvd = {}
  for (const [window, rawKinds] of Object.entries(r)) {
    const kinds = record(rawKinds)
    if (!kinds) continue
    const row: Record<string, CmktCvdValue> = {}
    for (const [kind, raw] of Object.entries(kinds)) {
      const value = record(raw)
      if (!value) continue
      const cell: CmktCvdValue = {}
      const pct = num(value.pct)
      const musd = num(value.musd)
      if (pct !== null) cell.pct = pct
      if (musd !== null) cell.musd = musd
      if (Object.keys(cell).length) row[kind] = cell
    }
    if (Object.keys(row).length) out[window] = row
  }
  return Object.keys(out).length ? out : undefined
}

function numRecord(v: unknown): Record<string, number> | undefined {
  const r = record(v)
  if (!r) return undefined
  const out: Record<string, number> = {}
  for (const [key, raw] of Object.entries(r)) {
    const n = num(raw)
    if (n !== null) out[key] = n
  }
  return Object.keys(out).length ? out : undefined
}

function oi(v: unknown): CmktOi | undefined {
  const r = record(v)
  if (!r) return undefined
  const out: CmktOi = {}
  const musd = num(r.musd)
  const one = num(r.chg_1m_pct)
  const five = num(r.chg_5m_pct)
  const fifteen = num(r.chg_15m_pct)
  const byEx = numRecord(r.by_ex)
  if (musd !== null) out.musd = musd
  if (one !== null) out.chg_1m_pct = one
  if (five !== null) out.chg_5m_pct = five
  if (fifteen !== null) out.chg_15m_pct = fifteen
  if (byEx) out.by_ex = byEx
  return Object.keys(out).length ? out : undefined
}

function pair(v: unknown): number[] | undefined {
  if (!Array.isArray(v) || v.length < 2) return undefined
  const a = num(v[0])
  const b = num(v[1])
  return a !== null && b !== null ? [a, b] : undefined
}

function pairRecord(v: unknown): Record<string, number[]> | undefined {
  const r = record(v)
  if (!r) return undefined
  const out: Record<string, number[]> = {}
  for (const [key, raw] of Object.entries(r)) {
    const p = pair(raw)
    if (p) out[key] = p
  }
  return Object.keys(out).length ? out : undefined
}

function liq(v: unknown): CmktLiq | undefined {
  const r = record(v)
  if (!r) return undefined
  const out: CmktLiq = {}
  const five = pair(r['5m'])
  const one = pair(r['1h'])
  const cov = num(r.liq_cov)
  const partial = num(r.liq_partial)
  const byEx = pairRecord(r.by_ex_5m)
  if (five) out['5m'] = five
  if (one) out['1h'] = one
  if (cov !== null) out.liq_cov = cov
  if (partial !== null) out.liq_partial = partial
  if (byEx) out.by_ex_5m = byEx
  return Object.keys(out).length ? out : undefined
}

function funding(v: unknown): CmktFunding | undefined {
  const r = record(v)
  if (!r) return undefined
  const out: CmktFunding = {}
  const w = num(r.w_pct)
  const spread = num(r.spread_pct)
  const ps = num(r.prem_ps)
  const cb = num(r.prem_cb)
  const byEx = numRecord(r.by_ex_8h_pct)
  if (w !== null) out.w_pct = w
  if (spread !== null) out.spread_pct = spread
  if (ps !== null) out.prem_ps = ps
  if (cb !== null) out.prem_cb = cb
  if (byEx) out.by_ex_8h_pct = byEx
  return Object.keys(out).length ? out : undefined
}

function venueCodes(v: unknown): Record<string, CmktVenueCode> | undefined {
  const r = record(v)
  if (!r) return undefined
  const out: Record<string, CmktVenueCode> = {}
  for (const [mkt, raw] of Object.entries(r)) {
    const value = record(raw)
    if (!value) continue
    const code: CmktVenueCode = { code: text(value.code) ?? 'OK' }
    const lag = num(value.lag_ms)
    const age = num(value.age_s)
    if (lag !== null) code.lag_ms = lag
    if (age !== null) code.age_s = age
    out[mkt] = code
  }
  return Object.keys(out).length ? out : undefined
}

function ladderLevel(v: unknown): CmktLevel | null {
  const r = record(v)
  if (!r) return null
  const musd = num(r.musd)
  const cum = num(r.cum)
  if (musd === null || cum === null) return null
  return { px: num(r.px), pct: num(r.pct), musd, cum, mkts: strings(r.mkts) }
}

function ladderLevels(v: unknown): CmktLevel[] {
  return Array.isArray(v) ? v.map(ladderLevel).filter((x): x is CmktLevel => x !== null) : []
}

function ladderKind(v: unknown): CmktLadderKind | null {
  const r = record(v)
  if (!r) return null
  const levels = record(r.levels) ?? {}
  const walls = record(r.walls) ?? {}
  const markets: CmktLadderKind['markets'] = {}
  for (const [mkt, raw] of Object.entries(record(r.markets) ?? {})) {
    const m = record(raw)
    if (!m) continue
    markets[mkt] = { code: text(m.code) ?? 'OK', lag_ms: num(m.lag_ms), mid: num(m.mid) }
  }
  return {
    mid: num(r.mid),
    best_bid: num(r.best_bid),
    best_ask: num(r.best_ask),
    spread_pct: num(r.spread_pct),
    source: text(r.source) ?? 'bk',
    n_mkts: num(r.n_mkts) ?? 0,
    levels: { ask: ladderLevels(levels.ask), bid: ladderLevels(levels.bid) },
    walls: { ask: ladderLevels(walls.ask), bid: ladderLevels(walls.bid) },
    markets,
  }
}

function ladder(v: unknown): CmktLadder | undefined {
  const r = record(v)
  if (!r) return undefined
  const kindsRaw = record(r.kinds) ?? {}
  const kinds: CmktLadder['kinds'] = {}
  const perp = ladderKind(kindsRaw.perp)
  const spot = ladderKind(kindsRaw.spot)
  if (perp) kinds.perp = perp
  if (spot) kinds.spot = spot
  return Object.keys(kinds).length ? { sym: text(r.sym), ts: num(r.ts), kinds } : undefined
}

/** Ответ `cmkt_sym_detail`: неизвестная форма не роняет страницу; части читаются только при совпадении формы. */
export function readDetail(data: unknown): CmktDetail | null {
  const d = record(data)
  if (!d) return null
  const out: CmktDetail = {
    ok: d.ok === true,
    sym: text(d.sym),
    ts: num(d.ts),
    age_s: num(d.age_s),
    n_ex: num(d.n_ex),
    cov: num(d.cov),
    warn: strings(d.warn),
  }
  const b = book(d.book)
  const c = cvd(d.cvd)
  const o = oi(d.oi)
  const l = liq(d.liq)
  const f = funding(d.funding)
  const v = venueCodes(d.venues)
  const trunc = strings(d.trunc)
  const lad = ladder(d.ladder)
  if (b) out.book = b
  if (c) out.cvd = c
  if (o) out.oi = o
  if (l) out.liq = l
  if (f) out.funding = f
  if (v) out.venues = v
  if (trunc.length) out.trunc = trunc
  if (lad) out.ladder = lad
  return out
}
