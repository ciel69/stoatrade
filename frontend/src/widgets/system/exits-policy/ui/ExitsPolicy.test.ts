import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import { useExitsStore, type ExitsPolicy as Policy } from '@/entities/exit'
import ExitsPolicy from './ExitsPolicy.vue'

const TS = new Date(2026, 10, 4, 21, 10).getTime() / 1000

const policy = (over: Partial<Policy> = {}): Policy => ({
  ok: true,
  ts: TS,
  db: 'OK',
  error: null,
  version: {
    seq: 42, ver: 'X-0042', parent: 'X-0041', reason_code: 'PROMOTE', policy_h: 'policy-h', floor_h: 'floor-h', actor: 'CYCLE', ts: TS, numbers: null,
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
    rows: [
      { trial_id: 'T-0009', var_id: 'v-1', var_ver: 2, base_ver: 'X-0038', state: 'closed', verdict: 'promote', start_ts: TS, deadline_ts: TS, closed_ts: TS, numbers: null },
    ],
  },
  watch: { ts: TS, ver: 'X-0042', prev: 'X-0041', codes: ['EXIT_SIM_GAP'], points: 12, last_point: null },
  config: { errors: [] },
  ...over,
})

function show(over: Partial<Policy> = {}) {
  setActivePinia(createPinia())
  useExitsStore().policy = policy(over)
  return mount(ExitsPolicy)
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('ExitsPolicy', () => {
  it('полное состояние: версия, исполнитель, выключатель, режим, испытания, сторож и только чтение', () => {
    const w = show()
    const t = w.text()
    expect(t).toContain('Политика выхода')
    expect(t).toContain('X-0042')
    expect(t).toContain('продвинута')
    expect(t).toContain('4 ноя, 21:10')
    expect(t).toContain('всё сходится')
    expect(t).toContain('снимок, аренда и файлы совпадают')
    expect(t).toContain('включено')
    expect(t).toContain('авто')
    expect(t).toContain('открыто 2, закрыто 5')
    expect(t).toContain('T-0009 — продвинуто')
    expect(t).toContain('EXIT_SIM_GAP')
    expect(t).toContain('расхождение факта и симуляции')
    expect(t).toContain('./tb exits ...')
    expect(w.findAll('button').map((b) => b.text())).toEqual(['обновить'])
  })

  it('ok:false с NO_DB: причина и код, файловые поля на месте, нули базы не выдумываются', () => {
    const w = show({
      ok: false,
      db: 'NO_DB',
      error: 'база знаний ещё не создана',
      version: null,
      effective: null,
      snapshot: null,
      lease: null,
      switch: { apply: false, source: 'broken', ts: null, actor: null },
      promotion: { mode: 'off' },
      trials: { open: 0, closed: 0, rows: [] },
      watch: null,
      config: { errors: ['config/exits.json: promotion.mode'] },
    })
    const t = w.text()
    expect(t).toContain('база знаний ещё не создана')
    expect(t).toContain('NO_DB')
    expect(t).toContain('ВЫКЛЮЧЕНО')
    expect(t).toContain('файл повреждён')
    expect(t).toContain('версии не продвигаются автоматически')
    expect(t).toContain('config/exits.json: promotion.mode')
    expect(t).toContain('снимка нет или он битый')
    expect(t).not.toContain('открыто 0')
    expect(t).toContain('нет данных без базы знаний')
  })

  it('нет версии и снимка: явные слова вместо выдуманных значений', () => {
    const w = show({
      version: null,
      versions: 0,
      effective: { ver: null, policy_h: null, why: 'SNAPSHOT' },
      snapshot: null,
      lease: null,
      trials: { open: 0, closed: 0, rows: [] },
      watch: null,
    })
    const t = w.text()
    expect(t).toContain('версии ещё нет')
    expect(t).toContain('снимка нет или он битый')
    expect(t).toContain('Снимок')
    expect(t).toContain('записи нет')
  })

  it('загрузка: «собираю…», пока ответа нет', () => {
    setActivePinia(createPinia())
    const w = mount(ExitsPolicy)
    expect(w.text()).toContain('собираю…')
    expect(w.find('section').attributes('aria-busy')).toBe('true')
  })
})
