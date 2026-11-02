import { describe, expect, it } from 'vitest'
import { humanize } from './human'
import { CODE_IDS, DECISION_CODES, STEP_NAMES, decisionReason, failureText, humanCode, stepLabel } from './human-codes'
import { GATE_IDS, KILL_GATE_IDS, PRE_IDS, WARNING_IDS, gateName, h7Parts } from './human-gates'
import { NODE_IDS } from './human-nodes'
import { dayLabel } from './human-text'
import type { Human, HumanCtx } from './human-types'
import { ROLE_IDS, actionName, roleAbout, roleTitle } from './human-words'

/** Служебные следы, которых не должно быть в основном тексте: коды проверок и шагов, коды ответов, snake_case, имена файлов. */
const CODE_LIKE = [/\b[HSWP]\d{1,2}\b/, /E_[A-Z_]+/, /\b[a-z]+_[a-z_]+\b/, /\.json/, /\.py\b/, /\bpid\b/i]
const main = (h: Human): string[] => [h.title, h.what, h.why, h.todo]
const clean = (h: Human): void => {
  for (const s of main(h)) for (const re of CODE_LIKE) expect(s, `«${s}» ${re}`).not.toMatch(re)
}
const CTXS: HumanCtx[] = [{ mode: 'paper', running: true }, { mode: 'live', running: true }, {}]

describe('словарь знает всё, что показывает интерфейс', () => {
  it('гейты H1–H8: и не пройденные, и пройденные, в любом режиме', () => {
    expect(GATE_IDS).toEqual(['H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'H7', 'H8'])
    for (const ctx of CTXS) for (const id of GATE_IDS) for (const ok of [true, false]) {
      const h = humanize({ kind: 'gate', id, ok, msg: 'исходный текст agents.json limits.modes' }, ctx)
      expect(h.title.length, id).toBeGreaterThan(3)
      clean(h)
      expect(h.details).toContain(id)
    }
  })
  it('предупреждения W1–W9 и проверки перед запуском P1–P16', () => {
    expect(WARNING_IDS).toEqual(['W1', 'W2', 'W3', 'W4', 'W5', 'W6', 'W7', 'W8', 'W9'])
    expect(PRE_IDS).toHaveLength(16)
    for (const ctx of CTXS) for (const id of [...WARNING_IDS, ...PRE_IDS]) for (const kind of ['gate', 'warning'] as const) {
      const h = humanize(kind === 'gate' ? { kind, id, ok: false, msg: 'x_y', sev: 'amber' } : { kind, id, msg: 'x_y' }, ctx)
      expect(h.title.length, id).toBeGreaterThan(3)
      clean(h)
    }
  })
  it('шаги запуска S1–S9: готов, замечание, провал с кодом', () => {
    expect(STEP_NAMES).toEqual(['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9'])
    for (const name of STEP_NAMES) {
      expect(stepLabel(name)).not.toMatch(/\bS\d\b/)
      for (const item of [{ ok: true }, { ok: true, amber: true }, { ok: false, code: 'E_GATE' }, { ok: false, code: 'E_NOPE' }, { ok: false }]) {
        const h = humanize({ kind: 'step', name, ...item })
        clean(h)
      }
    }
  })
  it('узлы платформы: все id из system.nodes, в каждом уровне', () => {
    expect(NODE_IDS).toEqual(expect.arrayContaining(['exec', 'net', 'recon', 'events', 'scan', 'feed', 'context', 'session']))
    for (const id of [...NODE_IDS, 'неизвестный']) for (const level of ['ok', 'warn', 'bad', 'off'] as const) {
      const h = humanize({ kind: 'node', node: { id, level, label: 'Узел', text: 'paper · pid 1 · v0.9' } })
      clean(h)
    }
  })
  it('коды ответов действий E_*: все известные объяснены, неизвестный не показывается сырым', () => {
    for (const code of ['OK', 'E_AUTH', 'E_ELEVATE', 'E_MAC', 'E_EXPIRED', 'E_DUP', 'E_NONCE', 'E_PHRASE', 'E_STATE', 'E_GATE', 'E_WARN_CHANGED', 'E_WARN', 'E_AMBER', 'E_PREFLIGHT', 'E_LIMITS', 'E_COOLDOWN', 'E_ARGS',
      'E_UNKNOWN', 'E_DISABLED', 'E_UNAVAILABLE', 'E_INTERNAL', 'E_SWAP_NO_LIVE_RIGHTS', 'E_SWAP_AMBIGUOUS_STATE', 'E_SWAP_PHRASE', 'E_SWAP_PROBE', 'E_SWAP_LIMIT_DAY', 'E_SWAP_PROFILE', 'E_SWAP_TARGET',
      'E_SWAP_NO_KEY', 'E_SWAP_ROLLBACK', 'E_EXISTS', 'E_NO_KEY', 'E_IN_USE', 'E_LIVE_IN_USE', 'E_PROVIDER_OFF', 'E_CHECK', 'E_CSRF', 'E_RATE', 'E_KEY', 'E_LINK', 'E_WAIT', 'E_BUSY']) {
      expect(CODE_IDS, code).toContain(code)
    }
    for (const code of [...CODE_IDS, 'E_ЧТО_ТО_НОВОЕ', 'E_SOMETHING_NEW']) {
      const h = humanCode(code, 'исходный текст')
      clean(h)
      expect(h.details).toContain(code)
    }
    expect(humanCode('E_SOMETHING_NEW').title).toContain('не умеем объяснить')
  })
  it('причины отказов решений: известные по-русски, неизвестная не светит кодом', () => {
    for (const c of DECISION_CODES) expect(decisionReason(c)).not.toMatch(/[A-Z_]{3,}/)
    expect(decisionReason('NO_SLOTS')).toBe('нет свободных мест под позиции')
    expect(decisionReason('NEW_CODE_X')).toBe('причина не расшифрована')
    expect(decisionReason(null)).toBe('')
  })
})

describe('баннеры и свободные тексты', () => {
  it('«День запущен; замечания: S1,S5,S6»: без кодов, нейтральный тон, объяснение последствий', () => {
    const h = humanize({ kind: 'banner', level: 'warn', text: 'День D-20261101-2 запущен (paper); замечания: S1,S5,S6' })
    clean(h)
    expect(h.tone).toBe('info')
    expect(h.title).toContain('на бумаге')
    expect(h.title).toContain('1 ноября')
    expect(h.what).toContain('мозг не прислал')
    expect(h.why).toContain('запасной')
    expect(h.todo).toContain('Действий не требуется')
    expect(h.details).toContain('S1,S5,S6')
  })
  it('замечание сверки (S3) окрашивает баннер в жёлтый', () => {
    expect(humanize({ kind: 'banner', level: 'warn', text: 'День D-20261101 запущен (paper); замечания: S3' }).tone).toBe('warn')
  })
  it('старт отклонён, итог дня, аварийная остановка: понятные тексты', () => {
    const rej = humanize({ kind: 'banner', level: 'alarm', text: 'Старт дня D-20261101-1 отклонён на шаге S1: P2 исполнитель не жив' })
    expect(rej.tone).toBe('bad')
    clean(rej)
    expect(humanize({ kind: 'banner', level: 'info', text: 'Итог дня D-20261101-1 готов: +1.20 USDT, перенесено 0, вопросов 2' }).todo).toContain('вопросы')
    expect(humanize({ kind: 'banner', level: 'alarm', text: 'аварийная остановка: KILL активен (flatten)' }).tone).toBe('bad')
  })
  it('неизвестная фраза: запасной текст по серьёзности, исходник только в подробностях', () => {
    const calm = humanize({ kind: 'text', level: 'info', text: 'Some_Weird_Server.json says foo_bar' })
    const loud = humanize({ kind: 'text', level: 'bad', text: 'Some_Weird_Server.json says foo_bar' })
    for (const h of [calm, loud]) { clean(h); expect(h.title).toContain('не умеем объяснить'); expect(h.details).toContain('foo_bar') }
    expect(calm.what).toContain('не останавливает')
    expect(loud.what).toContain('может мешать')
    expect(loud.tone).toBe('bad')
  })
  it('простой русский текст сервера показывается как есть', () => {
    expect(humanize({ kind: 'text', level: 'warn', text: 'что-то случилось' }).title).toBe('Что-то случилось')
  })
  it('номер дня превращается в дату', () => {
    expect(dayLabel('D-20261101-2')).toBe('1 ноября, запуск 2')
    expect(dayLabel('D-20261101')).toBe('1 ноября')
    expect(dayLabel('мусор')).toBe('торговый день')
  })
})

describe('режим определяет серьёзность проверок живой торговли', () => {
  const h7 = { kind: 'gate', id: 'H7', ok: false, msg: 'агенту api не разрешён live (agents.json limits.modes)' } as const
  it('H7 в бумажном режиме: справка с объяснением и тем, как включить', () => {
    const h = humanize(h7, { mode: 'paper', running: true })
    expect(h.tone).toBe('info')
    expect(h.title).toContain('Живая торговля')
    expect(h.what).toContain('только на бумаге')
    expect(h.todo).toContain('владелец')
    clean(h)
  })
  it('H7 при живой торговле: нужно действие', () => {
    expect(humanize(h7, { mode: 'live', running: true }).tone).toBe('bad')
  })
  it('несколько причин H7 разбираются по одной', () => {
    expect(h7Parts('агенту api не разрешён live (agents.json); allow_add у агента должен быть false')).toHaveLength(2)
  })
  it('H5 и H2 вне live это справка; H1 красный всегда; «исполнитель не отвечает» красный в любом режиме', () => {
    expect(humanize({ kind: 'gate', id: 'H5', ok: false, msg: 'дневной убыток 2.00 USDT исчерпал лимит 1.95 USDT' }, { mode: 'paper' }).tone).toBe('info')
    expect(humanize({ kind: 'gate', id: 'H5', ok: false, msg: 'дневной убыток 2.00 USDT исчерпал лимит 1.95 USDT' }, { mode: 'live' }).tone).toBe('bad')
    expect(humanize({ kind: 'gate', id: 'H1', ok: false, msg: 'KILL активен' }, { mode: 'paper' }).tone).toBe('bad')
    expect(humanize({ kind: 'gate', id: 'H3', ok: false, msg: 'исполнитель не отвечает (heartbeat нет)' }, { mode: 'paper' }).tone).toBe('bad')
    expect(humanize({ kind: 'gate', id: 'H3', ok: false, msg: 'приватный WS не подключён' }, { mode: 'paper' }).tone).toBe('info')
  })
})

describe('роли мозга словами', () => {
  it('у каждой известной роли есть название и описание без латиницы и служебных слов', () => {
    expect(ROLE_IDS.length).toBe(6)
    for (const r of ROLE_IDS) {
      expect(roleTitle(r)).not.toMatch(/[A-Za-z_]/)
      expect(roleAbout(r)).not.toMatch(/[A-Za-z_]/)
      expect(roleAbout(r)).not.toMatch(/карантин/i)
      expect(roleAbout(r).length).toBeGreaterThan(30)
    }
  })
  it('незнакомая роль и пустое значение не показывают код', () => {
    expect(roleTitle('some_new_role')).toBe('Другая роль мозга')
    expect(roleAbout('some_new_role')).not.toContain('some_new_role')
    expect(roleTitle(null)).toBe('Мозг')
    expect(roleAbout(undefined)).toBe('')
  })
})

describe('снятие аварийной остановки: проверки K1–K3 и отказы', () => {
  it('идентификаторы, названия и действие', () => {
    expect(KILL_GATE_IDS).toEqual(['K1', 'K2', 'K3'])
    for (const id of KILL_GATE_IDS) expect(gateName(id)).toBeTruthy()
    expect(actionName('kill.clear')).toBe('Снятие аварийной остановки')
  })
  it('каждая проверка объясняется словами: пройдена и не пройдена, не пройденная не справка', () => {
    for (const id of KILL_GATE_IDS) {
      for (const ok of [true, false]) {
        const h = humanize({ kind: 'gate', id, ok, msg: 'исполнитель жив, режим paper' })
        expect(h.title).toBeTruthy()
        expect(h.title).not.toMatch(/не умеем объяснить/)
        clean(h)
      }
      expect(humanize({ kind: 'gate', id, ok: false, msg: 'x' }).tone).not.toBe('info')
    }
  })
  it('отказы снятия по-человечески: отключено, условия, состояние, подтверждение', () => {
    expect(failureText('E_DISABLED')).toMatch(/отключено в настройках/)
    expect(failureText('E_GATE')).toMatch(/Условия безопасности/)
    expect(failureText('E_STATE')).toMatch(/нельзя/)
    expect(failureText('E_NONCE')).toMatch(/подтвердить заново/)
  })
})
