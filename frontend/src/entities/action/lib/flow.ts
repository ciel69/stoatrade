import { humanize, plainify } from '@/shared/lib'
import type { ActionCommand, ActionHello, ActionSpec, Challenge, ChallengeGate, RiskItem } from '../model/types'

/**
 * Какие ключи подтверждения принимает схема действия на сервере. Сервер строго отбрасывает лишние ключи args, а часть действий отвергает их целиком,
 * поэтому accepted и allow_amber добавляются только там, где они есть в схеме: day.start (accepted, allow_amber); risk.set, brain.use, brain.rollback, brain.reload (accepted);
 * brain.role: accepted есть у op 'chain' (смена основной и запасных), у op 'opts' сервер собирает args по именам и лишнее поле отбрасывает, так что оно не вредит.
 */
export const ACCEPT_KEYS: Readonly<Record<string, { accepted: boolean; allowAmber: boolean }>> = {
  'day.start': { accepted: true, allowAmber: true },
  'risk.set': { accepted: true, allowAmber: false },
  'brain.use': { accepted: true, allowAmber: false },
  'brain.rollback': { accepted: true, allowAmber: false },
  'brain.reload': { accepted: true, allowAmber: false },
  'brain.role': { accepted: true, allowAmber: false },
}

export const UNAVAILABLE_TEXT = 'Это действие здесь пока недоступно: владелец должен включить расширенное управление дашборда.'
export const NO_CTL_TEXT = 'Управление из этого дашборда недоступно.'
export const TIMEOUT_TEXT = 'Платформа не ответила за 60 секунд, поэтому неизвестно, выполнено ли действие. Проверьте состояние платформы и только потом повторяйте.'
/** Сколько ждём итог принятой команды. */
export const FINAL_TIMEOUT_MS = 60_000

/** Действие доступно: управление включено, расширенный режим включён и действие есть в списке hello.modes. */
export function unavailableReason(hello: ActionHello, action: string): string | null {
  if (!hello.enabled || !hello.csrf) return NO_CTL_TEXT
  if (hello.full !== true || !(hello.modes ?? []).includes(action)) return UNAVAILABLE_TEXT
  return null
}

const warnings = (ch: Challenge | null | undefined) => ch?.warnings ?? []
const preAmber = (ch: Challenge | null | undefined) => ch?.pre_amber ?? []

/** Рискованно: опасное действие, нужны фразы, есть предупреждения или жёлтые проверки. Иначе подтверждения нет вовсе. */
export function isRisky(ch: Challenge | null | undefined): boolean {
  if (!ch) return false
  return !!ch.danger || (ch.phrases?.length ?? 0) > 0 || warnings(ch).length > 0 || preAmber(ch).length > 0
}

/** Одна проверка или предупреждение простым языком: известные коды (H1–H8, W1–W9, P1–P16) переводит словарь, прочее проходит, только если уже написано простым русским. */
export function plainLine(id: string | undefined, name: string | undefined, msg: string | undefined, ok: boolean, sev?: string): { title: string; todo: string } {
  const h = humanize({ kind: 'gate', id: id ?? '', name, ok, msg, sev })
  const known = !!id && /^[HWP]\d{1,2}$/.test(id) && !/не умеем объяснить/.test(h.title)
  if (known) return { title: h.title, todo: ok ? '' : h.todo }
  const own = plainify([name, msg].filter(Boolean).join(': '))
  if (own) return { title: own, todo: '' }
  return { title: h.title, todo: ok ? '' : h.todo }
}

/** Последствия по-человечески, важное сверху: опасность, предупреждения, жёлтые проверки. */
export function risksOf(ch: Challenge | null | undefined): RiskItem[] {
  if (!ch) return []
  const out: RiskItem[] = []
  if (ch.danger) out.push({ id: 'danger', level: 'danger', text: 'Это действие нельзя откатить одним нажатием: прочтите последствия ниже.' })
  for (const w of warnings(ch)) {
    const known = /^W\d$/.test(w.id) ? humanize({ kind: 'warning', id: w.id, msg: w.msg }) : null
    const own = known ? null : plainify(w.msg)
    out.push({ id: w.id, level: 'warn', text: known ? [known.title, known.what].filter(Boolean).join('. ') : (own ?? humanize({ kind: 'text', level: 'warn', text: w.msg ?? w.code ?? w.id }).title) })
  }
  preAmber(ch).forEach((a, i) => {
    const l = plainLine(a.id, a.name, a.msg, false, 'amber')
    out.push({ id: a.id ?? `amber-${i}`, level: 'amber', text: l.title })
  })
  if (!out.length && (ch.phrases?.length ?? 0) > 0) out.push({ id: 'phrases', level: 'warn', text: 'Платформа просит явно подтвердить это действие.' })
  return out
}

export function failedGates(ch: Challenge | null | undefined): ChallengeGate[] {
  return (ch?.gates ?? []).filter((g) => g.ok === false)
}

export function passedGates(ch: Challenge | null | undefined): ChallengeGate[] {
  return (ch?.gates ?? []).filter((g) => g.ok !== false)
}

/** Причины блокировки: непройденные условия простым языком и что делать; если сервер их не перечислил, понятный текст по коду ответа. */
export function blockReasons(ch: Challenge | null | undefined, fallback: string): string[] {
  const list = failedGates(ch).map((g) => {
    const l = plainLine(g.id, g.name, g.msg, false)
    return l.todo ? `${l.title}. ${l.todo}` : l.title
  })
  return list.length ? list : [plainify(fallback) ?? 'Условия для выполнения пока не выполнены.']
}

/** Команда после подтверждения: nonce и фразы из challenge как есть, accepted и allow_amber только там, где они есть в схеме действия. */
export function buildCommand(spec: Pick<ActionSpec, 'action' | 'args'>, ch: Challenge | null | undefined): ActionCommand {
  const args: Record<string, unknown> = { ...spec.args }
  const keys = ACCEPT_KEYS[spec.action]
  if (keys?.accepted) args.accepted = warnings(ch).map((w) => w.id)
  if (keys?.allowAmber) args.allow_amber = preAmber(ch).length > 0
  const cmd: ActionCommand = { args }
  if (ch?.nonce !== undefined) cmd.nonce = ch.nonce
  if (ch?.phrases !== undefined) cmd.phrases = [...ch.phrases]
  return cmd
}

/** Сколько секунд осталось до разрешённого нажатия (min_delay от получения challenge). */
export function secondsLeft(minDelay: number | undefined, receivedAt: number, now: number): number {
  if (!minDelay || minDelay <= 0) return 0
  return Math.max(0, Math.ceil(minDelay - (now - receivedAt) / 1000))
}

/** Срок challenge истёк (expires_in секунд от получения). Нет срока: не истекает. */
export function isExpired(expiresIn: number | undefined, receivedAt: number, now: number): boolean {
  if (!expiresIn || expiresIn <= 0) return false
  return now - receivedAt >= expiresIn * 1000
}

/** Текст кнопки подтверждения: пока идёт пауза, показывает остаток секунд. */
export function acceptLabel(left: number): string {
  return left > 0 ? `Принимаю риски и применить · ${left} с` : 'Принимаю риски и применить'
}
