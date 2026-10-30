import { describe, expect, it } from 'vitest'
import type { ChallengeData, CtlSnapshot } from '../model/types'
import { acceptList, buttonKind, canStart, launchFill, litNodes, regionProgress, startArgs, stepRows } from './flow'

const s = (name: string, ok: boolean | null) => ({ name, phase: 'start', ok, code: null })
const REGIONS = [[0, 1, 8, 9], [2, 3, 10, 11], [4, 6, 12, 14], [5, 7, 13, 15]]

describe('launchFill', () => {
  const rowsAt = (done: number) => stepRows(Array.from({ length: done }, (_, i) => s(`S${i + 1}`, true)), true)
  it('растёт со временем внутри шага и не доходит до конца шага', () => {
    const a = launchFill(rowsAt(4), 0)
    const b = launchFill(rowsAt(4), 10)
    const c = launchFill(rowsAt(4), 600)
    expect(b).toBeGreaterThan(a)
    expect(c).toBeLessThan(launchFill(rowsAt(5), 0))
  })
  it('долгие LLM-шаги (S5, S6) занимают основную часть шкалы, без прыжка в конце', () => {
    expect(launchFill(rowsAt(4), 0)).toBeLessThan(0.3)
    expect(launchFill(rowsAt(6), 0)).toBeGreaterThan(0.8)
  })
})

describe('stepRows', () => {
  it('записанные шаги как есть, первый ненаписанный идёт сейчас', () => {
    const rows = stepRows([s('S1', true), s('S2', true), s('S3', null)], true)
    expect(rows.map((r) => r.state)).toEqual(['ok', 'ok', 'run', 'todo', 'todo', 'todo', 'todo', 'todo', 'todo'])
  })
  it('шаг без записи идёт сейчас, если уже есть записанные', () => {
    const rows = stepRows([s('S1', true), s('S2', true)], true)
    expect(rows[2].state).toBe('run')
  })
  it('в самом начале идёт S1; если запуск не идёт, ничего не бежит', () => {
    expect(stepRows([], true)[0].state).toBe('run')
    expect(stepRows([], false).every((r) => r.state === 'todo')).toBe(true)
    expect(stepRows([s('S1', false)], true)[0].state).toBe('bad')
  })
  it('чужие фазы (закрытие дня) не считаются', () => {
    expect(stepRows([{ name: 'S1', phase: 'close', ok: true, code: null }], false)[0].state).toBe('todo')
  })
})

describe('нейроны по прогрессу', () => {
  it('область горит долей готовых шагов', () => {
    const rows = stepRows([s('S1', true), s('S2', true), s('S3', true)], true)
    const p = regionProgress(rows)
    expect(p.map((x) => x.done)).toEqual([1, 0.5, 0, 0])
    expect(p[1].running).toBe(true)
    expect(litNodes(p, REGIONS)).toEqual([0, 1, 8, 9, 2, 3])
  })
  it('все шаги готовы: горят все нейроны областей', () => {
    const rows = stepRows(['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9'].map((n) => s(n, true)), false)
    expect(litNodes(regionProgress(rows), REGIONS)).toHaveLength(16)
  })
})

describe('принятие и аргументы', () => {
  const ch: ChallengeData = { warnings: [{ id: 'W1', msg: 'мало истории' }], pre_amber: [{ id: 'P1', msg: 'жёлтая' }, { msg: 'без id' }], blocked: false }
  it('список принимаемого: предупреждения и жёлтые проверки', () => {
    expect(acceptList(ch).map((a) => a.id)).toEqual(['W1', 'P1', 'amber-1'])
  })
  it('старт доступен, когда принято всё и нет блокировки', () => {
    expect(canStart(ch, new Set())).toBe(false)
    expect(canStart(ch, new Set(['W1', 'P1', 'amber-1']))).toBe(true)
    expect(canStart({ blocked: true }, new Set())).toBe(false)
    expect(canStart({ danger: true }, new Set())).toBe(false)
    expect(canStart(null, new Set())).toBe(false)
    expect(canStart({}, new Set())).toBe(true)
  })
  it('аргументы: принятые предупреждения и allow_amber, сессия day', () => {
    expect(startArgs('paper', ch)).toEqual({ mode: 'paper', session: 'day', accepted: ['W1'], allow_amber: true })
    expect(startArgs('observe', {})).toEqual({ mode: 'observe', session: 'day', accepted: [], allow_amber: false })
  })
})

describe('buttonKind', () => {
  const base = (over: Partial<CtlSnapshot['platform']> = {}, top: Partial<CtlSnapshot> = {}): CtlSnapshot => ({
    alive: true, configured: true, platform: { state: 'stopped', mode: null, session: null, health: null, day_id: null, trading: null, observe: null, ...over }, step: null, kill: null,
    risk: { level: 3, name: 'Базовый' }, busy: false, checks: [], banner: null, day: null, ...top,
  })
  it('по состоянию платформы', () => {
    expect(buttonKind(base(), true).kind).toBe('start')
    expect(buttonKind(base({ state: 'starting' }), true).kind).toBe('starting')
    expect(buttonKind(base({ state: 'running' }), true).kind).toBe('running')
    expect(buttonKind(base({ state: 'closing' }), true).kind).toBe('closing')
  })
  it('недоступна и объясняет почему', () => {
    expect(buttonKind(null, true).kind).toBe('unavailable')
    expect(buttonKind(base(), false).why).toMatch(/open_local/)
    expect(buttonKind(base({}, { alive: false }), true).why).toMatch(/ctl/)
    expect(buttonKind(base({}, { kill: { flatten: false, reason: '' } }), true).kind).toBe('blocked')
  })
})
