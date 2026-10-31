import { KEY_MAX, keyOutcomeText, keyProblem, normalizeKey, type KeyOutcome } from '../lib/key'

export const KEY_WAIT_MS = 15_000

interface KeyIo {
  csrf: () => string | undefined
  send: (payload: Record<string, unknown>) => boolean
}
interface Waiting {
  provider: string
  resolve: (o: KeyOutcome) => void
  timer: ReturnType<typeof setTimeout>
}

/** Ответ сокета на запись ключа (тема ctl_result, kind 'llmkey'). Значения ключа в нём нет. */
export interface KeyReply {
  id: string
  kind: string
  ok: boolean
  code: string
}

/**
 * Отправка ключа провайдера (op 'llmkey'). Не стор: значение живёт только в аргументе вызова и в одном сообщении сокета, нигде не копируется.
 * Одна отправка за раз; ответ приходит темой ctl_result, его передаёт accept(). Тексты по коду, поле msg не используется.
 */
export function createKeySender(io: KeyIo) {
  let seq = 0
  const waiting = new Map<string, Waiting>()

  const fail = (code: string): KeyOutcome => ({ ok: false, code, text: keyOutcomeText(code) })

  function send(provider: string, value: string): Promise<KeyOutcome> {
    if (waiting.size) return Promise.resolve(fail('E_BUSY'))
    const bad = keyProblem(value)
    if (bad) return Promise.resolve({ ok: false, code: 'E_ARGS', text: bad })
    const csrf = io.csrf()
    if (!csrf) return Promise.resolve(fail('E_LINK'))
    const id = `k${++seq}`
    return new Promise<KeyOutcome>((resolve) => {
      const timer = setTimeout(() => {
        waiting.delete(id)
        resolve(fail('E_WAIT'))
      }, KEY_WAIT_MS)
      waiting.set(id, { provider, resolve, timer })
      const ok = io.send({ op: 'llmkey', id, csrf, provider, value: normalizeKey(value).slice(0, KEY_MAX) })
      if (!ok) {
        clearTimeout(timer)
        waiting.delete(id)
        resolve(fail('E_LINK'))
      }
    })
  }

  /** true: сообщение было ответом на ключ (обработано), false: чужой ответ. */
  function accept(r: KeyReply): boolean {
    if (r.kind !== 'llmkey') return false
    const w = waiting.get(r.id)
    if (!w) return false
    clearTimeout(w.timer)
    waiting.delete(r.id)
    const code = typeof r.code === 'string' ? r.code : 'E_INTERNAL'
    w.resolve(r.ok === true && code === 'OK' ? { ok: true, code, text: keyOutcomeText('OK') } : fail(r.ok === true ? 'E_INTERNAL' : code))
    return true
  }

  return { send, accept, busy: () => waiting.size > 0 }
}
