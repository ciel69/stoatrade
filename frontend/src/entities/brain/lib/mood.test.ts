import { describe, expect, it } from 'vitest'
import { MOOD, pickQuip, quietDelay, quipDelay } from './mood'

describe('настроение мозга', () => {
  it('20-30 реплик, 3-6 слов, без разговора о деньгах и сделках', () => {
    const all = Object.values(MOOD).flat()
    expect(all.length).toBeGreaterThanOrEqual(20)
    expect(all.length).toBeLessThanOrEqual(30)
    for (const q of all) {
      const words = q.replace(/[…?,.]/g, '').trim().split(/\s+/).length
      expect(words).toBeGreaterThanOrEqual(2)
      expect(words).toBeLessThanOrEqual(6)
      expect(q).not.toMatch(/сделк|деньг|прибыл|убыт|купи|продай|вход|доллар|\$/i)
    }
    expect(new Set(all).size).toBe(all.length)
  })
  it('pickQuip: подходит состоянию, не повторяет прошлую, для «не отвечает» пусто', () => {
    expect(pickQuip('down', null)).toBeNull()
    for (const s of ['thinking', 'idle', 'protect'] as const) {
      const first = pickQuip(s, null)!
      expect(MOOD[s]).toContain(first)
      for (let i = 0; i < 30; i++) expect(pickQuip(s, first)).not.toBe(first)
    }
  })
  it('темп смены: думает 4-6 с, защита реже, облако отдыха прячется надолго', () => {
    for (let i = 0; i < 20; i++) {
      const d = quipDelay('thinking')
      expect(d).toBeGreaterThanOrEqual(4000)
      expect(d).toBeLessThanOrEqual(6000)
      expect(quipDelay('protect')).toBeGreaterThan(5999)
      expect(quietDelay()).toBeGreaterThan(8999)
    }
  })
})
