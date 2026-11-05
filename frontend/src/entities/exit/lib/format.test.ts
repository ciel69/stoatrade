import { describe, expect, it } from 'vitest'
import {
  DASH, dateText, dbMessage, leaseByWord, normalizeExits, promotionNote, promotionWord, reasonWord, switchNote, switchWord, trialVerdictWord, watchCodeWord, watchCodesText, whyText,
  whyWord,
} from './format'

const TS = new Date(2026, 10, 4, 21, 10).getTime() / 1000

const payload = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  ok: true,
  ts: TS,
  db: 'OK',
  version: {
    seq: 42, ver: 'X-0042', parent: 'X-0041', reason_code: 'PROMOTE', policy_h: 'policy-h', floor_h: 'floor-h', actor: 'CYCLE', ts: TS, numbers: { n: 12 },
  },
  versions: 42,
  effective: { ver: 'X-0042', policy_h: 'policy-h', why: 'OK' },
  snapshot: { ver: 'X-0042', policy_h: 'policy-h', floor_h: 'floor-h', ts: TS },
  lease: { ts: TS, ver: 'X-0042', policy_h: 'policy-h', by: 'promote' },
  switch: { apply: true, source: 'default', ts: null, actor: null },
  promotion: { mode: 'auto' },
  trials: {
    open: 2,
    closed: 5,
    rows: [{ trial_id: 'T-0009', var_id: 'v-1', var_ver: 2, base_ver: 'X-0038', state: 'closed', verdict: 'promote', start_ts: TS, deadline_ts: TS, closed_ts: TS, numbers: null }],
  },
  watch: { ts: TS, ver: 'X-0042', prev: 'X-0041', codes: ['EXIT_SIM_GAP'], points: 12, last_point: null },
  config: { errors: [] },
  ...over,
})

describe('время и слова', () => {
  it('время местное и короткое, неизвестное — прочерк', () => {
    expect(dateText(TS)).toBe('4 ноя, 21:10')
    expect(dateText(null)).toBe(DASH)
    expect(dateText(Number.NaN)).toBe(DASH)
  })

  it('причины версии: все пять слов и неизвестный код с русским словом', () => {
    expect(reasonWord('INIT')).toBe('создана')
    expect(reasonWord('PROMOTE')).toBe('продвинута')
    expect(reasonWord('ROLLBACK')).toBe('откат')
    expect(reasonWord('FLOOR_CHANGED')).toBe('пол изменён')
    expect(reasonWord('OWNER_ROLLBACK')).toBe('откат владельца')
    expect(reasonWord('WHAT')).toBe('код WHAT')
    expect(reasonWord(null)).toBe('причина не указана')
  })

  it('что видит исполнитель: слова и пояснения', () => {
    expect(whyWord('OK')).toBe('всё сходится')
    expect(whyWord('SWITCH')).toBe('применение выключено')
    expect(whyWord('SNAPSHOT')).toBe('снимка нет')
    expect(whyWord('LEASE')).toBe('аренда просрочена')
    expect(whyWord('HASH')).toBe('хеши разошлись')
    expect(whyWord('WHAT')).toBe('код WHAT')
    expect(whyText('HASH')).toMatch(/снимок и аренда/)
    expect(whyText('LEASE')).toMatch(/срок аренды/)
    expect(whyText('SWITCH')).toMatch(/выключатель/)
    expect(whyText('SNAPSHOT')).toMatch(/снимка нет или он битый/)
    expect(whyText('WHAT')).toBe('причина не распознана')
  })

  it('выключатель, продвижение, аренда, испытания и сторож', () => {
    expect(switchWord({ apply: true, source: 'default', ts: null, actor: null })).toBe('включено')
    expect(switchWord({ apply: false, source: 'file', ts: null, actor: null })).toBe('ВЫКЛЮЧЕНО')
    expect(switchWord(null)).toBe(DASH)
    expect(switchNote({ apply: true, source: 'default', ts: null, actor: null })).toMatch(/по умолчанию/)
    expect(switchNote({ apply: false, source: 'broken', ts: null, actor: null })).toMatch(/повреждён/)
    expect(switchNote(null)).toMatch(/неизвестно/)
    expect(promotionWord('auto')).toBe('авто')
    expect(promotionWord('off')).toBe('выключено')
    expect(promotionWord('WHAT')).toBe('код WHAT')
    expect(promotionNote('off')).toMatch(/не продвигаются/)
    expect(leaseByWord('promote')).toBe('продвижение')
    expect(leaseByWord('rollback')).toBe('откат')
    expect(leaseByWord('watch')).toBe('сторож')
    expect(leaseByWord('WHAT')).toBe('код WHAT')
    expect(trialVerdictWord('promote')).toBe('продвинуто')
    expect(trialVerdictWord('reject')).toBe('отклонено')
    expect(trialVerdictWord('inconclusive')).toBe('без вывода')
    expect(watchCodeWord('EXIT_SIM_GAP')).toBe('расхождение факта и симуляции')
    expect(watchCodesText(['EXIT_SIM_GAP'])).toBe('расхождение факта и симуляции (EXIT_SIM_GAP)')
    expect(watchCodesText([])).toBe('кодов нет')
  })

  it('причины недоступности базы', () => {
    expect(dbMessage('NO_DB')).toBe('база знаний ещё не создана')
    expect(dbMessage('NOT_READY')).toMatch(/появятся после первого прохода/)
    expect(dbMessage('RESTORING')).toBe('база восстанавливается')
    expect(dbMessage('DB_ERROR')).toBe('ошибка чтения базы')
    expect(dbMessage('WHAT')).toBe('база недоступна (код WHAT)')
    expect(dbMessage(null)).toBe('база знаний не ответила')
  })
})

describe('приведение ответа exits_status_detail', () => {
  it('полный ответ сохраняет версию, исполнителя, файлы, испытания и сторожа', () => {
    const p = normalizeExits(payload())
    expect(p).not.toBeNull()
    expect(p).toMatchObject({
      ok: true,
      db: 'OK',
      versions: 42,
      version: { ver: 'X-0042', reason_code: 'PROMOTE', ts: TS },
      effective: { ver: 'X-0042', why: 'OK' },
      snapshot: { ver: 'X-0042', ts: TS },
      lease: { ver: 'X-0042', by: 'promote', ts: TS },
      switch: { apply: true, source: 'default' },
      promotion: { mode: 'auto' },
      trials: { open: 2, closed: 5 },
      watch: { ver: 'X-0042', points: 12, codes: ['EXIT_SIM_GAP'] },
      config: { errors: [] },
    })
    expect(p?.trials.rows[0]?.trial_id).toBe('T-0009')
  })

  it('ok:false сохраняет файловые поля и не выдумывает данные базы', () => {
    const p = normalizeExits(payload({
      ok: false,
      db: 'NO_DB',
      error: 'база знаний ещё не создана',
      version: null,
      versions: 0,
      effective: null,
      snapshot: null,
      lease: null,
      switch: { apply: false, source: 'broken', ts: null, actor: null },
      promotion: { mode: 'off' },
      trials: { open: 0, closed: 0, rows: [] },
      watch: null,
      config: { errors: ['config/exits.json: promotion.mode'] },
    }))
    expect(p?.ok).toBe(false)
    expect(p?.switch).toEqual({ apply: false, source: 'broken', ts: null, actor: null })
    expect(p?.promotion).toEqual({ mode: 'off' })
    expect(p?.snapshot).toBeNull()
    expect(p?.lease).toBeNull()
    expect(p?.effective).toBeNull()
    expect(p?.version).toBeNull()
    expect(p?.config.errors).toEqual(['config/exits.json: promotion.mode'])
  })

  it('не объект отбрасывается, отсутствующие поля остаются пустыми', () => {
    expect(normalizeExits(null)).toBeNull()
    expect(normalizeExits('garbage')).toBeNull()
    expect(normalizeExits([])).toBeNull()
    expect(normalizeExits({})).toBeNull()
    const p = normalizeExits({ ok: true, db: 'OK' })
    expect(p?.version).toBeNull()
    expect(p?.switch).toBeNull()
    expect(p?.promotion).toBeNull()
    expect(p?.trials).toEqual({ open: 0, closed: 0, rows: [] })
    expect(p?.config).toEqual({ errors: [] })
  })

  it('повреждённые строки испытаний не ломают разбор', () => {
    const p = normalizeExits(payload({ trials: { open: '2', closed: 5, rows: ['x', { trial_id: 7, state: 'closed' }] } }))
    expect(p?.trials.open).toBe(0)
    expect(p?.trials.closed).toBe(5)
    expect(p?.trials.rows).toHaveLength(1)
    expect(p?.trials.rows[0]?.trial_id).toBeNull()
    expect(p?.trials.rows[0]?.state).toBe('closed')
  })
})
