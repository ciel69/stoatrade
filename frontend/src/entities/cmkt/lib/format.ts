/** Форматирование сводного рынка: млн USD, проценты, возрасты и человеческие слова для кодов (R1.7, задача 29). */
import { duration, plural, price, signed } from '@/shared/lib'

/** Неизвестное значение: длинное тире, как во всей вкладке. */
export const DASH = '—'
/** Снимок старше этого возраста на экране считается устаревшим (сервер отдаёт STALE после 5 с). */
export const SNAP_STALE_S = 5

export type WordTone = 'neutral' | 'ok' | 'warn' | 'bad'

export interface WordInfo {
  word: string
  about: string
  tone: WordTone
}

/** Полосы глубины, которые отдаёт `cmktdoc._part_book`: 0.5 %, 1 %, 2 %. */
export const BAND_LABELS: ReadonlyArray<readonly [string, string]> = [
  ['05', '±0.5%'],
  ['1', '±1%'],
  ['2', '±2%'],
]
export const BAND_KINDS: ReadonlyArray<readonly [string, string]> = [
  ['perp', 'Перп'],
  ['spot', 'Спот'],
  ['all', 'Все'],
]
export const CVD_WINDOWS: ReadonlyArray<readonly [string, string]> = [
  ['1m', '1 мин'],
  ['5m', '5 мин'],
  ['15m', '15 мин'],
]
export const CVD_KINDS: ReadonlyArray<readonly [string, string]> = [
  ['perp', 'перп'],
  ['spot', 'спот'],
  ['all', 'все'],
]

/** Число или `—`; серверные `null` и мусор показываются одинаково. */
const isNum = (x: number | null | undefined): x is number => typeof x === 'number' && Number.isFinite(x)

/** Млн USD: 123.5M, 1.24B; неизвестное — «—». */
export function musd(x: number | null | undefined): string {
  if (!isNum(x)) return DASH
  return Math.abs(x) >= 1000 ? `${(x / 1000).toFixed(2)}B` : `${x.toFixed(1)}M`
}

/** Млн USD со знаком (поток ордеров): +2.3M, −1.1M, 0.0M; неизвестное — «—». */
export function musdSigned(x: number | null | undefined): string {
  if (!isNum(x)) return DASH
  if (x > 0) return `+${musd(x)}`
  if (x < 0) return `−${musd(-x)}`
  return musd(x)
}

/** Процент с одним знаком: 12.3%, 0.4%; неизвестное — «—». */
export function pct(x: number | null | undefined, digits = 1): string {
  return isNum(x) ? `${x.toFixed(digits)}%` : DASH
}

/** Процент со знаком: +12.3%, −4.0%; неизвестное — «—». */
export function pctSigned(x: number | null | undefined, digits = 1): string {
  return isNum(x) ? `${signed(x, digits)}%` : DASH
}

/** Доля 0…1 в процент: 0.94 -> 93.9 %; неизвестное — «—». */
export function ratioPct(x: number | null | undefined, digits = 1): string {
  return isNum(x) ? `${(x * 100).toFixed(digits)}%` : DASH
}

/** Маленькие проценты (финансирование): 0.0012 % не должна выглядеть нулём. */
export function smallPct(x: number | null | undefined, digits = 5): string {
  return isNum(x) ? `${signed(x, digits)}%` : DASH
}

/** Базисные пункты со знаком: +1.4 б.п., −0.6 б.п.; неизвестное — «—». */
export function bps(x: number | null | undefined): string {
  return isNum(x) ? `${signed(x, 1)} б.п.` : DASH
}

/** Человеческий возраст по метке времени (секунды): 3 с, 1 мин; неизвестное — «—». */
export function ageSince(ts: number | null | undefined, now: number): string {
  return isNum(ts) ? duration(Math.max(0, now - ts)) : DASH
}

/** Человеческий возраст по готовым секундам: 3 с, 1 мин; неизвестное — «—». */
export function ageSeconds(seconds: number | null | undefined): string {
  return isNum(seconds) ? duration(Math.max(0, seconds)) : DASH
}

/** Задержка рынка: 120 мс; неизвестное — «—». */
export function lagText(ms: number | null | undefined): string {
  return isNum(ms) ? `${Math.round(ms)} мс` : DASH
}

/** Расхождение часов со знаком: +420 мс, −90 мс; неизвестное — «—». */
export function skewText(ms: number | null | undefined): string {
  return isNum(ms) ? `${signed(ms, 0)} мс` : DASH
}

/** Цена инструмента: значащие цифры, как на карте; неизвестное — «—». */
export function priceText(x: number | null | undefined): string {
  return isNum(x) ? price(x) : DASH
}

/** Число бирж словами: 3 биржи, 5 бирж; неизвестное — «—». */
export function exchangesText(n: number | null | undefined): string {
  if (!isNum(n)) return DASH
  const count = Math.round(n)
  return `${count} ${plural(count, ['биржа', 'биржи', 'бирж'])}`
}

/** Снимок устарел по отметке `ts` (5 с); без метки — тоже устарел. */
export function isAged(ts: number | null | undefined, now: number, maxSeconds = SNAP_STALE_S): boolean {
  return !isNum(ts) || now - ts > maxSeconds
}

const TIER: Record<string, string> = { deep: 'глубокий', light: 'лёгкий' }
/** Ярус фокуса: глубокая монета обновляется раз в секунду, лёгкая — раз в пять. */
export function tierWord(tier: string | null | undefined): string {
  return (tier && TIER[tier]) || 'обзор'
}

const BRANDS: Record<string, string> = { binance: 'Binance', bybit: 'Bybit', okx: 'OKX', bitget: 'Bitget', gate: 'Gate', coinbase: 'Coinbase' }
const KINDS: Record<string, string> = { perp: 'перп', spot: 'спот' }

/** Идентификатор рынка (`binance_perp`) человеческими словами: Binance · перп. */
export function venueName(mkt: string | null | undefined): string {
  if (!mkt) return 'Биржа'
  const cut = mkt.lastIndexOf('_')
  const brand = cut > 0 ? mkt.slice(0, cut) : mkt
  const kind = cut > 0 ? mkt.slice(cut + 1) : ''
  const name = BRANDS[brand] ?? (brand ? brand[0].toUpperCase() + brand.slice(1) : 'Биржа')
  return kind ? `${name} · ${KINDS[kind] ?? kind}` : name
}

const VENUE_CODES: Record<string, WordInfo> = {
  OK: { word: 'норма', about: 'данные приходят вовремя', tone: 'ok' },
  STALE: { word: 'устарела', about: 'последнее обновление давно', tone: 'warn' },
  LAG: { word: 'отстаёт', about: 'задержка выше допустимой', tone: 'warn' },
  SILENT: { word: 'молчит', about: 'нет сообщений дольше предела', tone: 'warn' },
  DOWN: { word: 'недоступна', about: 'соединение с биржей потеряно', tone: 'bad' },
  RESYNC: { word: 'пересинхронизация', about: 'биржа заново собирает стакан', tone: 'warn' },
  OFF: { word: 'выключена', about: 'рынок отключён в настройках', tone: 'neutral' },
  NOT_LISTED: { word: 'не котируется', about: 'у биржи нет этой пары', tone: 'neutral' },
  MISMATCH: { word: 'расхождение', about: 'цены расходятся со сводкой', tone: 'bad' },
}
const UNKNOWN_VENUE: WordInfo = { word: 'состояние неизвестно', about: 'код биржи не распознан', tone: 'neutral' }

/** Код рынка словами и с пояснением; сырой код на экран не попадает. */
export function venueInfo(code: string | null | undefined): WordInfo {
  return (code && VENUE_CODES[code]) || UNKNOWN_VENUE
}

const WARNINGS: Record<string, WordInfo> = {
  STALE: { word: 'снимок устарел', about: 'сводка старше 5 секунд', tone: 'warn' },
  NOT_FOCUS: { word: 'вне фокуса', about: 'монету сейчас не собирают', tone: 'neutral' },
  LOW_COV: { word: 'мало бирж', about: 'бирж меньше нужного или покрытие низкое', tone: 'warn' },
  BUDGET: { word: 'фокус урезан', about: 'лёгких монет меньше из-за нагрузки', tone: 'warn' },
  CLOCK_SKEW: { word: 'часы расходятся', about: 'часы бирж расходятся больше допустимого', tone: 'warn' },
  FOCUS_NONE: { word: 'фокус пуст', about: 'набор монет для сводки не задан', tone: 'warn' },
}
const UNKNOWN_WARNING: WordInfo = { word: 'замечание', about: 'подробностей нет', tone: 'neutral' }

/** Предупреждение сводки словами; неизвестный код не показывается как есть. */
export function warnInfo(code: string): WordInfo {
  return WARNINGS[code] ?? UNKNOWN_WARNING
}

const FLAGS: Record<string, string> = {
  low_cov: 'мало бирж',
  liq_partial: 'ликвидации неполные',
  clock_back: 'часы отстают',
  warmup: 'прогрев',
  mm_unchecked: 'маркет-мейкеры не проверены',
  stale_ex: 'есть отстающие биржи',
}

/** Флаг снимка словами; неизвестный флаг не показывается. */
export function flagWord(flag: string): string | null {
  return FLAGS[flag] ?? null
}

const TRUNC: Record<string, string> = { venues: 'биржи', liq: 'ликвидации', oi: 'открытый интерес', funding: 'финансирование', cvd: 'поток ордеров', book: 'стакан' }

/** Отброшенная при усечении часть ответа. */
export function truncWord(part: string): string {
  return TRUNC[part] ?? 'часть данных'
}

const BAND_KIND_WORD: Record<string, string> = { perp: 'Перп', spot: 'Спот', all: 'Все' }
export function bandKindWord(kind: string): string {
  return BAND_KIND_WORD[kind] ?? kind
}

const CVD_KIND_WORD: Record<string, string> = { perp: 'перп', spot: 'спот', all: 'все' }
export function cvdKindWord(kind: string): string {
  return CVD_KIND_WORD[kind] ?? kind
}

/** Спред лучших цен: 0.04 %; пересечение (бид выше аска) называется словами; неизвестное — «—». */
export function spreadText(x: number | null | undefined): string {
  if (!isNum(x)) return DASH
  return x <= 0 ? 'пересечение' : `${x.toFixed(3)}%`
}

/** Источник уровней лестницы: корзины 25 б.п., полосы или крупные уровни. */
export function ladderSourceWord(source: string | null | undefined): string {
  if (source === 'bk') return 'полосы 0.25%'
  if (source === 'bands') return 'полосы 0.25–2%'
  if (source === 'walls') return 'крупные уровни'
  return 'уровни'
}

/** Рынок стакана словами: перп или спот. */
export function bookKindWord(kind: string | null | undefined): string {
  if (kind === 'perp') return 'Перп'
  if (kind === 'spot') return 'Спот'
  return DASH
}