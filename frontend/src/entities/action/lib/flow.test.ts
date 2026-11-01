import { describe, expect, it } from 'vitest'
import type { Challenge } from '../model/types'
import { ACCEPT_KEYS, acceptLabel, blockReasons, buildCommand, dialogUp, failedGates, timeoutText, isExpired, isRisky, passedGates, risksOf, secondsLeft, unavailableReason, UNAVAILABLE_TEXT } from './flow'

const calm: Challenge = { gates: [{ id: 'G1', name: 'Связь', ok: true }], warnings: [], pre_amber: [], phrases: [], danger: false, blocked: false, nonce: 'n1' }
const risky: Challenge = {
  danger: true, nonce: 'n2', phrases: ['ФРАЗА 1', 'ФРАЗА 2'],
  gates: [{ id: 'G1', name: 'Связь', ok: true }, { id: 'G2', name: 'Сверка', ok: false, msg: 'устарела' }],
  warnings: [{ id: 'W1', code: 'W_A', msg: 'мало истории' }, { id: 'W2', msg: 'есть позиция' }],
  pre_amber: [{ id: 'P1', name: 'Жёлтая', msg: 'почти предел' }],
}

describe('isRisky', () => {
  it('без опасности, фраз, предупреждений и жёлтых проверок подтверждения нет', () => {
    expect(isRisky(calm)).toBe(false)
    expect(isRisky({})).toBe(false)
    expect(isRisky(null)).toBe(false)
  })
  it('любой из четырёх признаков делает действие рискованным', () => {
    expect(isRisky({ danger: true })).toBe(true)
    expect(isRisky({ phrases: ['x'] })).toBe(true)
    expect(isRisky({ warnings: [{ id: 'W' }] })).toBe(true)
    expect(isRisky({ pre_amber: [{ msg: 'a' }] })).toBe(true)
  })
})

describe('risksOf', () => {
  it('опасность сверху, затем предупреждения, затем жёлтые проверки', () => {
    expect(risksOf(risky).map((r) => [r.level, r.id])).toEqual([['danger', 'danger'], ['warn', 'W1'], ['warn', 'W2'], ['amber', 'P1']])
    expect(risksOf(risky)[1].text).toMatch(/^На бумаге пока мало опыта/)
    expect(risksOf(risky)[1].text).not.toMatch(/\bW\d\b/)
    expect(risksOf(risky)[3].text).toBe('Платформа не готова к запуску дня')
    expect(risksOf({ pre_amber: [{ id: 'Z9', name: 'Жёлтая', msg: 'почти предел' }] })[0].text).toBe('Жёлтая: почти предел')
  })
  it('только фразы: один общий пункт, чтобы список не был пустым', () => {
    expect(risksOf({ phrases: ['x'] })).toHaveLength(1)
    expect(risksOf(calm)).toEqual([])
  })
})

describe('условия', () => {
  it('failedGates и passedGates делят список', () => {
    expect(failedGates(risky).map((g) => g.id)).toEqual(['G2'])
    expect(passedGates(risky).map((g) => g.id)).toEqual(['G1'])
  })
  it('причины блокировки: непройденные условия, иначе текст ответа', () => {
    expect(blockReasons(risky, 'x')).toEqual(['Сверка: устарела'])
    expect(blockReasons({ gates: [{ id: 'H1', ok: false, msg: 'KILL активен' }] }, '')[0]).toMatch(/^Включена аварийная остановка\./)
    expect(blockReasons({ gates: [] }, 'сервер отказал')).toEqual(['сервер отказал'])
    expect(blockReasons({ gates: [] }, 'E_GATE x_y')).toEqual(['Условия для выполнения пока не выполнены.'])
    expect(blockReasons(null, '')).toEqual(['Условия для выполнения пока не выполнены.'])
  })
})

describe('buildCommand', () => {
  it('day.start: accepted из предупреждений, allow_amber по жёлтым, nonce и фразы как есть', () => {
    const c = buildCommand({ action: 'day.start', args: { mode: 'paper', session: 'day' } }, risky)
    expect(c).toEqual({ args: { mode: 'paper', session: 'day', accepted: ['W1', 'W2'], allow_amber: true }, nonce: 'n2', phrases: ['ФРАЗА 1', 'ФРАЗА 2'] })
  })
  it('risk.set и brain.*: только accepted, без allow_amber', () => {
    for (const a of ['risk.set', 'brain.use', 'brain.rollback', 'brain.reload']) {
      expect(buildCommand({ action: a, args: { x: 1 } }, risky).args).toEqual({ x: 1, accepted: ['W1', 'W2'] })
    }
  })
  it('brain.role (смена цепочки): accepted есть, allow_amber нет', () => {
    expect(ACCEPT_KEYS['brain.role']).toEqual({ accepted: true, allowAmber: false })
    const c = buildCommand({ action: 'brain.role', args: { op: 'chain', role: 'A.entry_scout', primary: 'p1', fallback: ['p2'] } }, risky)
    expect(c.args).toEqual({ op: 'chain', role: 'A.entry_scout', primary: 'p1', fallback: ['p2'], accepted: ['W1', 'W2'] })
    expect(c.args).not.toHaveProperty('allow_amber')
    expect(buildCommand({ action: 'brain.role', args: { op: 'chain', role: 'A.x' } }, calm).args).toMatchObject({ accepted: [] })
  })
  it('остальные действия: чужих ключей нет (сервер их отбросит или отвергнет)', () => {
    const c = buildCommand({ action: 'kill.set', args: { flatten: true, reason: 'r' } }, risky)
    expect(c.args).toEqual({ flatten: true, reason: 'r' })
    expect(c.nonce).toBe('n2')
  })
  it('без рисков accepted пустой, allow_amber false; исходные args не меняются', () => {
    const args = { mode: 'paper' }
    expect(buildCommand({ action: 'day.start', args }, calm).args).toEqual({ mode: 'paper', accepted: [], allow_amber: false })
    expect(args).toEqual({ mode: 'paper' })
    expect(buildCommand({ action: 'kill.set', args: {} }, {}).nonce).toBeUndefined()
  })
})

describe('время', () => {
  it('secondsLeft: пауза min_delay от получения challenge', () => {
    expect(secondsLeft(3, 1000, 1000)).toBe(3)
    expect(secondsLeft(3, 1000, 2100)).toBe(2)
    expect(secondsLeft(3, 1000, 4000)).toBe(0)
    expect(secondsLeft(undefined, 1000, 1000)).toBe(0)
    expect(secondsLeft(0, 1000, 1000)).toBe(0)
  })
  it('isExpired: срок expires_in; нет срока не истекает', () => {
    expect(isExpired(60, 0, 59_999)).toBe(false)
    expect(isExpired(60, 0, 60_000)).toBe(true)
    expect(isExpired(undefined, 0, 9e9)).toBe(false)
  })
  it('подпись кнопки с остатком секунд', () => {
    expect(acceptLabel(2)).toBe('Принимаю риски и применить · 2 с')
    expect(acceptLabel(0)).toBe('Принимаю риски и применить')
  })
})

describe('unavailableReason', () => {
  const ok = { enabled: true, csrf: 'c', full: true, modes: ['kill.set', 'day.start'] }
  it('доступно при full и действии в modes', () => {
    expect(unavailableReason(ok, 'kill.set')).toBeNull()
  })
  it('не full или нет в modes: текст про auth.dash2_full', () => {
    expect(unavailableReason({ ...ok, full: false }, 'kill.set')).toBe(UNAVAILABLE_TEXT)
    expect(unavailableReason(ok, 'risk.set')).toBe(UNAVAILABLE_TEXT)
    expect(unavailableReason({ enabled: true, csrf: 'c' }, 'kill.set')).toBe(UNAVAILABLE_TEXT)
  })
  it('управление выключено: другой текст', () => {
    expect(unavailableReason({ enabled: false }, 'kill.set')).toMatch(/недоступно/)
  })
  it('запуск и остановка дня доступны и без расширенного режима, остальное нет', () => {
    const base = { enabled: true, csrf: 'c', full: false, modes: ['day.start', 'day.stop'] }
    expect(unavailableReason(base, 'day.start')).toBeNull()
    expect(unavailableReason(base, 'day.stop')).toBeNull()
    expect(unavailableReason({ ...base, modes: ['risk.set'] }, 'risk.set')).toBe(UNAVAILABLE_TEXT)
    expect(unavailableReason({ ...base, modes: [] }, 'day.start')).toBe(UNAVAILABLE_TEXT)
  })
})

describe('dialogUp', () => {
  it('диалог виден при подтверждении, блокировке и рискованном запуске в пути', () => {
    expect(dialogUp('confirm', true)).toBe(true)
    expect(dialogUp('blocked', null)).toBe(true)
    expect(dialogUp('sending', true)).toBe(true)
    expect(dialogUp('done', true)).toBe(true)
  })
  it('безрисковое и пустое состояние диалога не показывают', () => {
    expect(dialogUp('sending', false)).toBe(false)
    expect(dialogUp('checking', null)).toBe(false)
    expect(dialogUp('idle', true)).toBe(false)
  })
})

describe('timeoutText', () => {
  it('называет срок в секундах', () => {
    expect(timeoutText(60_000)).toMatch(/60 секунд/)
    expect(timeoutText(180_000)).toMatch(/180 секунд/)
  })
})

describe('buildCommand для действий настроек', () => {
  const ch: Challenge = { nonce: 'n9', phrases: ['ФРАЗА'], warnings: [{ id: 'W_RISK_UP' }], pre_amber: [] }
  it('risk.set получает accepted из challenge, фразы и nonce подставляются сами', () => {
    expect(buildCommand({ action: 'risk.set', args: { level: 5 } }, ch)).toEqual({ args: { level: 5, accepted: ['W_RISK_UP'] }, nonce: 'n9', phrases: ['ФРАЗА'] })
  })
  it('mode.set и exchange.set не получают лишних accepted и allow_amber', () => {
    expect(buildCommand({ action: 'mode.set', args: { mode: 'paper' } }, ch).args).toEqual({ mode: 'paper' })
    expect(buildCommand({ action: 'exchange.set', args: { op: 'mode', id: 'bybit', mode: 'live' } }, ch).args).toEqual({ op: 'mode', id: 'bybit', mode: 'live' })
  })
  it('day.start получает accepted и allow_amber', () => {
    const d: Challenge = { warnings: [{ id: 'W1' }], pre_amber: [{ id: 'P1' }], nonce: 'n' }
    expect(buildCommand({ action: 'day.start', args: { mode: 'paper', session: 'day' } }, d).args).toEqual({ mode: 'paper', session: 'day', accepted: ['W1'], allow_amber: true })
  })
})
