import { describe, expect, it } from 'vitest'
import { BrainSim, LINKS } from './brain-sim'

const run = (sim: BrainSim, seconds: number, amp: number, onStep?: () => void): void => {
  for (let t = 0; t < seconds; t += 1 / 30) { sim.step(1 / 30, amp, true); onStep?.() }
}

describe('BrainSim', () => {
  it('у каждого нейрона не больше четырёх связей, связи без повторов', () => {
    const deg = new Map<number, number>()
    for (const [a, b] of LINKS) { deg.set(a, (deg.get(a) ?? 0) + 1); deg.set(b, (deg.get(b) ?? 0) + 1) }
    expect(Math.max(...deg.values())).toBeLessThanOrEqual(4)
    expect(new Set(LINKS.map(([a, b]) => `${a}-${b}`)).size).toBe(LINKS.length)
  })
  it('соединения меняются: набор живых связей со временем разный', () => {
    const sim = new BrainSim(3)
    const seen = new Set<string>()
    run(sim, 40, 1, () => seen.add(sim.str.map((s) => (s > 0.3 ? 1 : 0)).join('')))
    expect(seen.size).toBeGreaterThan(5)
  })
  it('в «думает» идут каскады: нейроны возбуждаются, импульсов много, но не больше пула', () => {
    const sim = new BrainSim(5)
    let max = 0
    let fired = 0
    run(sim, 20, 1, () => { max = Math.max(max, sim.pulses.length); if (sim.fire.some((f) => f > 0.9)) fired++ })
    expect(max).toBeGreaterThan(2)
    expect(max).toBeLessThanOrEqual(30)
    expect(fired).toBeGreaterThan(20)
  })
  it('замершая модель (live=false) не рождает импульсов, положения конечные', () => {
    const sim = new BrainSim(9)
    for (let i = 0; i < 100; i++) sim.step(1 / 30, 0, false)
    expect(sim.pulses.length).toBe(0)
    expect(sim.x.every(Number.isFinite) && sim.qy.every(Number.isFinite)).toBe(true)
  })
  it('нейроны остаются близко к своим местам', () => {
    const sim = new BrainSim(2)
    const x0 = [...sim.x]
    run(sim, 30, 1, () => sim.x.forEach((x, i) => expect(Math.abs(x - x0[i])).toBeLessThan(4)))
  })
})
