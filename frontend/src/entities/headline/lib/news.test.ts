import { describe, expect, it } from 'vitest'
import { filterNews, importance, markState, weightOf } from './news'
import type { NewsItem } from '../model/types'

const item = (o: Partial<NewsItem> = {}): NewsItem => ({ ref: 'N', src: 's', ts: 1, title: 't', snip: '', domain: 'd', susp: false, seen: 0, cls: null, struct: false, ...o })
const cls = (imp: number, syms: string[] = []): NewsItem['cls'] => ({ st: 'ok', type: 'hack', imp, ir: imp, dir: 'down', syms, ttl_min: 60, conf: 0.7 })

describe('новости', () => {
  it('важность: из разметки, у подозрительных 1, без разметки 0', () => {
    expect(importance(item({ cls: cls(4) }))).toBe(4)
    expect(importance(item({ susp: true, cls: cls(5) }))).toBe(1)
    expect(importance(item())).toBe(0)
    expect(importance(item({ cls: { ...cls(3)!, st: 'fail' } }))).toBe(0)
  })
  it('вес: 4+ высокий, 3 средний, остальное низкий', () => {
    expect([weightOf(5), weightOf(3), weightOf(2)]).toEqual(['high', 'mid', 'low'])
  })
  it('состояние разметки', () => {
    expect(markState(item())).toBe('waiting')
    expect(markState(item({ susp: true }))).toBe('raw')
    expect(markState(item({ struct: true }))).toBe('struct')
    expect(markState(item({ cls: { ...cls(2)!, st: 'susp' } }))).toBe('susp')
    expect(markState(item({ cls: cls(2) }))).toBe('ok')
  })
  it('фильтры: важные и по монете (BTCUSDT и BTC одно и то же)', () => {
    const a = item({ ref: 'A', cls: cls(4, ['BTC']) })
    const b = item({ ref: 'B', cls: cls(2, ['ETHUSDT']) })
    const c = item({ ref: 'C' })
    expect(filterNews([a, b, c], 'important', null).map((i) => i.ref)).toEqual(['A'])
    expect(filterNews([a, b, c], 'coin', 'ETH').map((i) => i.ref)).toEqual(['B'])
    expect(filterNews([a, b, c], 'coin', null).length).toBe(3)
  })
})
