import { describe, expect, it } from 'vitest'
import { BRAIN_DEPTH, BRAIN_NODES, HALF } from './brain-regions'
import { BRAIN_POLY, depthAt, nodeInset, nodeRadius, snapInside } from './brain-shape'
import { BrainNet, NET_LINKS, buildNetLinks, type NetMode } from './brain-net'

const run = (net: BrainNet, seconds: number, onStep?: () => void): void => {
  for (let t = 0; t < seconds; t += 1 / 30) { net.step(1 / 30, true); onStep?.() }
}

describe('форма мозга', () => {
  it('контур замкнут и симметричен, точки в центре внутри, снаружи вне', () => {
    expect(BRAIN_POLY.length).toBeGreaterThan(150)
    expect(depthAt(20, 30)).toBeGreaterThan(5)
    expect(depthAt(44, 30)).toBeGreaterThan(5)
    expect(depthAt(1, 30)).toBeLessThan(0)
    expect(depthAt(32, 2)).toBeLessThan(0)
    expect(Math.abs(depthAt(20, 25) - depthAt(44, 25))).toBeLessThan(1e-6)
  })
  it('snapInside вытаскивает вынесенную наружу точку внутрь с запасом', () => {
    const p = snapInside([5, 55], 4)
    expect(depthAt(p[0], p[1])).toBeGreaterThanOrEqual(4)
  })
})

describe('нейроны внутри контура', () => {
  it('каждый нейрон лежит глубже радиуса + возможного отклонения', () => {
    BRAIN_NODES.forEach(([x, y], i) => expect(depthAt(x, y)).toBeGreaterThanOrEqual(nodeInset(BRAIN_DEPTH[i]) - 1e-6))
  })
  it('нейроны не слипаются и доли разделены', () => {
    for (let a = 0; a < BRAIN_NODES.length; a++) for (let b = a + 1; b < BRAIN_NODES.length; b++) {
      expect(Math.hypot(BRAIN_NODES[a][0] - BRAIN_NODES[b][0], BRAIN_NODES[a][1] - BRAIN_NODES[b][1])).toBeGreaterThan(4.5)
    }
    for (let i = 0; i < HALF; i++) expect(BRAIN_NODES[i][0]).toBeLessThan(32)
  })
  const modes: NetMode[] = ['thinking', 'idle', 'protect', 'down']
  for (const mode of modes) {
    it(`в движении (${mode}) с курсором и кликами нейроны, их края и все точки связей остаются внутри`, () => {
      for (const seed of [7, 42, 99]) {
        const net = new BrainNet(seed)
        net.setMode(mode, true)
        let worst = Infinity
        let worstLink = Infinity
        const check = (): void => {
          for (let i = 0; i < net.n; i++) worst = Math.min(worst, depthAt(net.x[i], net.y[i]) - nodeRadius(BRAIN_DEPTH[i]))
          for (let l = 0; l < net.links.length; l++) for (let u = 0; u <= 1; u += 0.1) { const [x, y] = net.bezier(l, u); worstLink = Math.min(worstLink, depthAt(x, y)) }
        }
        run(net, 8, check)
        net.setPointer([6, 50])
        net.burst(3, 8, 50)
        run(net, 4, check)
        net.setPointer([57, 52])
        net.burst(20, 57, 52)
        run(net, 4, check)
        net.setPointer([32, 30])
        run(net, 3, check)
        expect(worst).toBeGreaterThan(0.3)
        expect(worstLink).toBeGreaterThan(0.3)
      }
    })
  }
})

describe('связи и импульсы', () => {
  it('сеть связная, у нейрона не больше четырёх связей (кроме сшивки), без повторов', () => {
    const seen = new Set<number>([0])
    for (let k = 0; k < NET_LINKS.length; k++) for (const [a, b] of NET_LINKS) { if (seen.has(a)) seen.add(b); if (seen.has(b)) seen.add(a) }
    expect(seen.size).toBe(BRAIN_NODES.length)
    expect(new Set(NET_LINKS.map(([a, b]) => `${a}-${b}`)).size).toBe(NET_LINKS.length)
    expect(buildNetLinks().length).toBe(NET_LINKS.length)
  })
  const count = (mode: NetMode, seconds: number): { spawned: number; max: number; received: number } => {
    const net = new BrainNet(5)
    net.setMode(mode, true)
    let spawned = 0
    let max = 0
    let received = 0
    const seen = new WeakSet<object>()
    run(net, seconds, () => {
      max = Math.max(max, net.pulses.length)
      for (const p of net.pulses) if (!seen.has(p)) { seen.add(p); spawned++ }
      if (net.recv.some((r) => r > 0.85)) received++
    })
    return { spawned, max, received }
  }
  it('«думает» много импульсов и ветвится, «отдыхает» редко, «защита» ещё реже, «не отвечает» ни одного', () => {
    const th = count('thinking', 30)
    const idle = count('idle', 30)
    const pr = count('protect', 30)
    const dn = count('down', 30)
    expect(th.spawned).toBeGreaterThan(idle.spawned * 2)
    expect(idle.spawned).toBeGreaterThan(pr.spawned)
    expect(dn.spawned).toBe(0)
    expect(th.max).toBeGreaterThan(3)
    expect(th.max).toBeLessThanOrEqual(22)
    expect(th.received).toBeGreaterThan(20)
  })
  it('замершая модель (live=false) не рождает импульсов, числа конечные', () => {
    const net = new BrainNet(9)
    for (let i = 0; i < 100; i++) net.step(1 / 30, false)
    expect(net.pulses.length).toBe(0)
    expect(net.x.every(Number.isFinite) && net.c1y.every(Number.isFinite)).toBe(true)
  })
  it('клик даёт каскад даже в покое, потом всё затихает', () => {
    const net = new BrainNet(4)
    net.setMode('idle', true)
    run(net, 2)
    net.burst(3, BRAIN_NODES[3][0], BRAIN_NODES[3][1])
    expect(net.recv[3]).toBe(1)
    expect(net.pulses.length).toBeGreaterThan(0)
    let depth = 0
    run(net, 4, () => net.pulses.forEach((p) => { if (p.boost >= 1) depth = Math.max(depth, p.depth) }))
    expect(depth).toBeGreaterThan(0)
    run(net, 6)
    expect(net.pulses.filter((p) => p.boost >= 1).length).toBe(0)
  })
  it('голова импульса идёт по кривой от начала к концу связи', () => {
    const net = new BrainNet(2)
    net.touch(3)
    const p = net.pulses[0]
    p.age = 0.001
    const a = net.tailPoint(p, 0)!
    p.age = p.dur * 0.999
    const b = net.tailPoint(p, 0)!
    expect(Math.hypot(a[0] - net.x[p.from], a[1] - net.y[p.from])).toBeLessThan(0.6)
    expect(Math.hypot(b[0] - net.x[p.to], b[1] - net.y[p.to])).toBeLessThan(0.6)
  })
})
