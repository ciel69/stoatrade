import { LIVE_GATE_WORD, gateName, humanize, liveLimitLines, liveWhyText, type LiveGateState, type LiveWhy } from '@/shared/lib'
import type { ChallengeData, CtlHello, CtlSnapshot } from '../model/types'

/**
 * Запуск торгового дня на РЕАЛЬНЫЕ деньги из этого дашборда. Что решает сервер, а не интерфейс: расширенный режим (hello.ctl.full), выключатель владельца
 * (тема ctl, live_start), жёсткие ворота H1–H8, предупреждения W, nonce и фразы. Интерфейс только показывает это словами и ничего не обходит.
 */

export interface LiveAccess {
  ok: boolean
  why: LiveWhy | null
  /** причина словами (null, когда доступно) */
  reason: string | null
}

/** Можно ли выбрать реальные деньги: управление включено, есть расширенный режим, ctl отвечает и владелец включил запуск live из дашборда. */
export function liveAccess(hello: CtlHello | null | undefined, ctl: CtlSnapshot | null | undefined): LiveAccess {
  const no = (why: LiveWhy): LiveAccess => ({ ok: false, why, reason: liveWhyText(why) })
  if (!hello?.enabled || !hello.csrf) return no('link')
  if (hello.full !== true || !(hello.modes ?? []).includes('day.start')) return no('full')
  if (!ctl || !ctl.alive) return no('down')
  if (ctl.live_start !== true) return no('off')
  return { ok: true, why: null, reason: null }
}

export interface LiveGateRow {
  id: string
  state: LiveGateState
  /** что проверяется, словами */
  title: string
  /** слово статуса: готово, не готово, проверится при запуске */
  word: string
  /** что сделать (только для «не готово») */
  todo: string
}

const DEFERRED = /проверится после|будет переключ/
const num = (id: string): number => Number(/\d+/.exec(id)?.[0] ?? 99)

/** Жёсткие проверки H1–H8 словами со статусом. «Проверится при запуске»: исполнитель ещё не на живой бирже, платформа перепроверит их сразу после переключения (провал = откат). */
export function liveGateRows(ch: ChallengeData | null | undefined): LiveGateRow[] {
  return [...(ch?.gates ?? [])].sort((a, b) => num(a.id) - num(b.id)).map((g) => {
    const ok = g.ok === true
    const later = ok && (g.deferred === true || DEFERRED.test(g.msg ?? ''))
    const h = humanize({ kind: 'gate', id: g.id, name: g.name, ok, msg: g.msg ?? '' }, { mode: 'live' })
    const state: LiveGateState = !ok ? 'bad' : later ? 'later' : 'ok'
    return { id: g.id, state, title: later ? (gateName(g.id) ?? h.title) : h.title, word: LIVE_GATE_WORD[state], todo: ok ? '' : h.todo }
  })
}

/** Сколько проверок не готово (непройденных). «Проверится при запуске» не в счёт: их проверит сама платформа. */
export function liveGatesBad(rows: readonly LiveGateRow[]): number {
  return rows.filter((r) => r.state === 'bad').length
}

/** «Начать торговлю» доступна: проверки получены, все пройдены (или проверятся при запуске), платформа не заблокировала запуск. Предупреждения принимаются в общем окне. */
export function canBeginLive(ch: ChallengeData | null | undefined): boolean {
  const g = ch?.gates ?? []
  return !!ch && !ch.blocked && g.length > 0 && g.every((x) => x.ok === true)
}

/** Пределы живой торговли, только из фраз платформы (H5, H8). */
export function liveLimits(ch: ChallengeData | null | undefined): string[] {
  const msg = (id: string): string | null => (ch?.gates ?? []).find((g) => g.id === id)?.msg ?? null
  return liveLimitLines({ H5: msg('H5'), H8: msg('H8') })
}
