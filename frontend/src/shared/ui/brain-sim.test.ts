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
  it('клик запускает каскад даже в ожидании, затем всё затихает', () => {
    const sim = new BrainSim(4)
    run(sim, 3, 0)
    sim.burst(3, 22, 36)
    expect(sim.fire[3]).toBe(1)
    expect(sim.pulses.length).toBeGreaterThan(0)
    let maxDepth = 0
    run(sim, 4, 0, () => sim.pulses.forEach((p) => { if (p.boost >= 1) maxDepth = Math.max(maxDepth, p.depth) }))
    expect(maxDepth).toBeGreaterThan(0)
    run(sim, 8, 0)
    expect(sim.pulses.filter((p) => p.boost >= 1).length).toBe(0)        // клик не заводит сеть навсегда
  })
  it('курсор притягивает ближний нейрон и отпускает, когда уходит', () => {
    const a = new BrainSim(8)
    const b = new BrainSim(8)
    const [bx, by] = [22, 36]
    b.setPointer([bx + 6, by])
    run(a, 2, 0)
    run(b, 2, 0)
    expect(b.x[3]).toBeGreaterThan(a.x[3] + 0.5)
    b.setPointer(null)
    run(b, 3, 0)
    expect(Math.abs(b.x[3] - a.x[3])).toBeLessThan(1.6)        // свободное блуждание само расходится по времени, но не на величину притяжения
  })
  it('нейроны остаются близко к своим местам', () => {
    const sim = new BrainSim(2)
    const x0 = [...sim.x]
    run(sim, 30, 1, () => sim.x.forEach((x, i) => expect(Math.abs(x - x0[i])).toBeLessThan(4)))
  })
})
