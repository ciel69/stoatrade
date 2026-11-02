import type { Human, Tone } from './human-types'

/** Режимы платформы простыми словами. «на …» отвечает на вопрос «как торгует платформа». */
const MODE: Record<string, { name: string; on: string }> = {
  paper: { name: 'бумажная торговля', on: 'на бумаге' },
  testnet: { name: 'тестовая биржа', on: 'на тестовой бирже' },
  observe: { name: 'наблюдение', on: 'в режиме наблюдения' },
  live: { name: 'реальные деньги', on: 'на реальные деньги' },
}

export const modeName = (m: string | null | undefined): string => (m ? (MODE[m]?.name ?? 'другой режим') : 'режим неизвестен')
export const modeOn = (m: string | null | undefined): string => (m ? (MODE[m]?.on ?? 'в другом режиме') : 'в неизвестном режиме')

/** Уровень строки в словах для экранных дикторов и подписей. */
export const TONE_WORD: Record<Tone, string> = { ok: 'в порядке', info: 'справка', warn: 'внимание', bad: 'нужно действие' }

const MONTH = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']

/** Номер дня платформы («D-20261101-2») человеку ничего не говорит: показываем дату и номер запуска. */
export function dayLabel(id: string | null | undefined): string {
  const m = /^D-(\d{4})(\d{2})(\d{2})(?:-(\d+))?/.exec(id ?? '')
  if (!m) return 'торговый день'
  const month = MONTH[Number(m[2]) - 1]
  if (!month) return 'торговый день'
  const n = m[4] ? Number(m[4]) : 1
  return `${Number(m[3])} ${month}${n > 1 ? `, запуск ${n}` : ''}`
}

/** Текст без служебных следов: нет латиницы, подчёркиваний и имён файлов. Такие тексты сервера можно показать как есть; остальные уходят только в подробности. */
export function isPlain(s: string | null | undefined): s is string {
  return typeof s === 'string' && s.trim() !== '' && !/[A-Za-z_]|\.json|\d[A-Z]/.test(s)
}

const JARGON: Array<[RegExp, string]> = [
  [/\bк live\b/gi, 'к реальной торговле'], [/\bна live\b/gi, 'на реальную торговлю'], [/\blive-/gi, 'реальных '], [/\blive\b/gi, 'реальная торговля'],
  [/\bpaper-/gi, 'бумажных '], [/\bpaper\b/gi, 'бумага'], [/\bUSDT\b/g, 'USDT'], [/\beval\b/gi, 'проверка'],
]

/**
 * Пытается превратить фразу сервера, написанную для владельца, но со словами-жаргонизмами (live, paper), в простой русский.
 * Остались латиница, подчёркивания или имена файлов: null (показывать нельзя, текст уйдёт только в подробности).
 */
export function plainify(s: string | null | undefined): string | null {
  if (typeof s !== 'string' || !s.trim()) return null
  let t = s
  for (const [re, to] of JARGON) t = t.replace(re, to)
  t = t.replace(/USDT/g, '\u0000')
  const ok = !/[A-Za-z_]|\.json|\d[A-Z]/.test(t)
  return ok ? t.replace(/\u0000/g, 'USDT') : null
}

export const cap =(s: string): string => (s ? s[0].toUpperCase() + s.slice(1) : s)

/** Сырой текст в подробности для специалиста: код и исходная фраза сервера одной строкой. */
export function detailsOf(...parts: Array<string | null | undefined>): string | null {
  const t = parts.map((x) => (x ?? '').trim()).filter(Boolean).join(' | ')
  return t || null
}

export interface Part {
  title: string
  what: string
  why: string
  todo: string
}

export function make(tone: Tone, p: Part, details: string | null): Human {
  return { title: p.title, what: p.what, why: p.why, todo: p.todo, tone, details }
}

/** Запасной текст для того, что мы не умеем объяснить. Сырой код в основной строке никогда не показывается. */
export function unknownHuman(tone: Tone, details: string | null): Human {
  const stops = tone === 'bad'
  return {
    title: 'Платформа сообщила о событии, которое мы пока не умеем объяснить',
    what: stops ? 'Это может мешать торговле.' : 'Торговлю это не останавливает.',
    why: 'Для этого сообщения пока нет понятного описания.',
    todo: stops ? 'Откройте «Подробности для специалиста» и перешлите их разработчику.' : 'Действий не требуется. Если сообщение повторяется, покажите подробности разработчику.',
    tone: tone === 'ok' ? 'info' : tone,
    details,
  }
}

/** Уровень сервера («info», «warn», «bad», «alarm», «red»…) в тон. */
export function toneOfLevel(level: string | null | undefined, fallback: Tone = 'info'): Tone {
  const l = String(level ?? '').toLowerCase()
  if (l === 'bad' || l === 'alarm' || l === 'red' || l === 'error' || l === 'crit') return 'bad'
  if (l === 'warn' || l === 'amber' || l === 'yellow' || l === 'attention') return 'warn'
  if (l === 'ok' || l === 'green') return 'ok'
  if (l === 'info' || l === 'off') return 'info'
  return fallback
}
