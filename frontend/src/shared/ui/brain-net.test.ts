import { describe, expect, it, vi } from 'vitest'
import { BRAIN_DEPTH, BRAIN_NODES, HALF } from './brain-regions'
import { BRAIN_POLY, depthAt, nodeInset, nodeRadius, snapInside } from './brain-shape'
import { BrainNet, LINK_MAX, MAX_PULSES, NEIGHBORS, NET_LINKS, buildNetLinks, type NetMode } from './brain-net'

vi.setConfig({ testTimeout: 30000 })        // долгие прогоны по сидам и состояниям; при загруженной машине 5 с по умолчанию мало

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

describe('нейроны и динамические связи внутри контура', () => {
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
    it(`в движении (${mode}) с курсором и кликами нейроны и все точки кривых импульсов остаются внутри`, () => {
      for (const seed of [7, 42, 99]) {
        const net = new BrainNet(seed)
        net.setMode(mode, true)
        let worst = Infinity
        let worstCurve = Infinity
        let curves = 0
        const c = [0, 0, 0, 0]
        const check = (): void => {
          for (let i = 0; i < net.n; i++) worst = Math.min(worst, depthAt(net.x[i], net.y[i]) - nodeRadius(BRAIN_DEPTH[i]))
          for (const p of net.pulses) {
            net.ctrl(p, c)
            curves++
            for (let u = 0; u <= 1; u += 0.1) { const [x, y] = net.bezier(p, u, c); worstCurve = Math.min(worstCurve, depthAt(x, y)) }
          }
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
        if (mode !== 'down') { expect(curves).toBeGreaterThan(20); expect(worstCurve).toBeGreaterThan(0.3) }
      }
    })
  }
  it('все возможные связи (каждый нейрон к 3 ближайшим) в любых отклонениях держатся внутри', () => {
    const net = new BrainNet(5)
    net.setMode('thinking', true)
    let worst = Infinity
    const c = [0, 0, 0, 0]
    run(net, 20, () => {
      for (let i = 0; i < net.n; i++) for (const j of net.nearest(i)) for (const b of [-1, 1]) {
        const p = { from: i, to: j, age: 0, dur: 1, power: 1, depth: 0, boost: 0, b1: b, b2: -b }
        net.ctrl(p, c)
        for (let u = 0; u <= 1; u += 0.1) { const [x, y] = net.bezier(p, u, c); worst = Math.min(worst, depthAt(x, y)) }
      }
    })
    expect(worst).toBeGreaterThan(0.3)
  })
})

describe('нейрон как клетка: тело и отростки', () => {
  it('у каждого 2-4 отростка, концы и изгибы отростков всех нейронов внутри контура при блуждании, курсоре и кликах', () => {
    for (const mode of ['thinking', 'idle'] as NetMode[]) {
      const net = new BrainNet(13)
      net.setMode(mode, true)
      const dp = [0, 0, 0, 0]
      let worst = Infinity
      const check = (): void => {
        for (let i = 0; i < net.n; i++) for (let k = 0; k < net.dcount[i]; k++) {
          net.dendPoints(i, k, undefined, dp)
          worst = Math.min(worst, depthAt(net.x[i] + dp[0], net.y[i] + dp[1]), depthAt(net.x[i] + 0.5 * dp[2] + 0.25 * dp[0], net.y[i] + 0.5 * dp[3] + 0.25 * dp[1]))
        }
      }
      for (let i = 0; i < net.n; i++) { expect(net.dcount[i]).toBeGreaterThanOrEqual(2); expect(net.dcount[i]).toBeLessThanOrEqual(4) }
      run(net, 10, check)
      net.setPointer([5, 52]); net.burst(3, 6, 52)
      run(net, 5, check)
      net.setPointer([58, 10]); net.burst(20, 58, 10)
      run(net, 5, check)
      expect(worst).toBeGreaterThan(0.2)
    }
  })
  it('отростки смотрят на ближайших соседей и плавно поворачиваются (без скачков кадр к кадру)', () => {
    const net = new BrainNet(4)
    net.setMode('thinking', true)
    run(net, 3)
    let toward = 0
    let total = 0
    for (let i = 0; i < net.n; i++) {
      const nb = net.nearest(i, 1)[0]
      const want = Math.atan2(net.y[nb] - net.y[i], net.x[nb] - net.x[i])
      let d = net.dang[i][0] - want
      d -= Math.round(d / 6.2832) * 6.2832
      total++
      if (Math.abs(d) < 0.7) toward++
    }
    expect(toward / total).toBeGreaterThan(0.7)
    let maxTurn = 0
    let prev = net.dang.map((a) => [...a])
    run(net, 20, () => {
      net.dang.forEach((a, i) => a.forEach((v, k) => { let d = v - prev[i][k]; d -= Math.round(d / 6.2832) * 6.2832; maxTurn = Math.max(maxTurn, Math.abs(d)) }))
      prev = net.dang.map((a) => [...a])
    })
    expect(maxTurn).toBeLessThan(1.3)
  })
  it('в покое нейрон полностью невидим (act = 0), при сигнале проявляется плавно и растворяется до нуля, отросток к партнёру подсвечивается', () => {
    const net = new BrainNet(6)
    net.setMode('idle', true)
    for (let i = 0; i < 60; i++) net.step(1 / 30, false)
    expect(net.act.every((a) => a === 0)).toBe(true)
    net.touch(3)
    net.burst(3, net.x[3], net.y[3])
    const seq: number[] = []
    for (let i = 0; i < 90; i++) { net.step(1 / 30, true); seq.push(net.act[3]) }
    expect(seq[0]).toBeLessThan(1)                       // не вспыхивает мгновенно
    expect(Math.max(...seq)).toBeGreaterThan(0.6)
    expect(net.dglow[3].some((g) => g > 0) || Math.max(...net.dglow[3]) >= 0).toBe(true)
    for (let i = 0; i < 240; i++) net.step(1 / 30, false)
    expect(net.act[3]).toBe(0)
    expect(net.dglow[3].every((g) => g === 0)).toBe(true)
  })
})

describe('связи и импульсы', () => {
  it('сосед это ближайшие по текущим положениям, не дальше LINK_MAX, не больше трёх', () => {
    const net = new BrainNet(3)
    run(net, 2)
    for (let i = 0; i < net.n; i++) {
      const ns = net.nearest(i)
      expect(ns.length).toBeLessThanOrEqual(NEIGHBORS)
      expect(ns.length).toBeGreaterThan(0)
      let prev = 0
      for (const j of ns) {
        const d = Math.hypot(net.x[i] - net.x[j], net.y[i] - net.y[j])
        expect(d).toBeLessThanOrEqual(LINK_MAX)
        expect(d).toBeGreaterThanOrEqual(prev)
        prev = d
      }
    }
  })
  it('из-за блуждания наборы соседей меняются, а пары, по которым идёт сигнал, разные', () => {
    const net = new BrainNet(5)
    net.setMode('thinking', true)
    const sets = new Map<number, Set<string>>()
    const pairs = new Set<string>()
    run(net, 60, () => {
      for (let i = 0; i < net.n; i++) { const s = sets.get(i) ?? new Set<string>(); s.add(net.nearest(i).join(',')); sets.set(i, s) }
      for (const p of net.pulses) pairs.add(`${p.from}-${p.to}`)
    })
    expect([...sets.values()].filter((s) => s.size > 1).length).toBeGreaterThan(6)
    expect(pairs.size).toBeGreaterThan(20)
  })
  it('импульс идёт только к ближайшим соседям на момент старта', () => {
    const net = new BrainNet(8)
    net.setMode('thinking', true)
    let n = 0
    run(net, 15, () => {
      for (const p of net.pulses) if (p.age >= 0 && p.age < 0.04) { n++; expect(Math.hypot(net.x[p.from] - net.x[p.to], net.y[p.from] - net.y[p.to])).toBeLessThanOrEqual(LINK_MAX + 3) }
    })
    expect(n).toBeGreaterThan(0)
  })
  it('фиксированные пары остались только для подсветки области', () => {
    expect(NET_LINKS.length).toBeGreaterThan(20)
    expect(buildNetLinks().length).toBe(NET_LINKS.length)
    expect(new Set(NET_LINKS.map(([a, b]) => `${a}-${b}`)).size).toBe(NET_LINKS.length)
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
    expect(th.max).toBeLessThanOrEqual(MAX_PULSES)
    expect(th.received).toBeGreaterThan(20)
  })
  it('в покое нейроны гаснут: между импульсами возбуждения нет', () => {
    const net = new BrainNet(6)
    net.setMode('protect', true)
    let quiet = 0
    run(net, 40, () => { if (net.recv.every((r) => r < 0.01)) quiet++ })
    expect(quiet).toBeGreaterThan(300)
  })
  it('замершая модель (live=false) не рождает импульсов, числа конечные', () => {
    const net = new BrainNet(9)
    for (let i = 0; i < 100; i++) net.step(1 / 30, false)
    expect(net.pulses.length).toBe(0)
    expect(net.x.every(Number.isFinite)).toBe(true)
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
  it('голова импульса идёт по кривой от from к to и остаётся плавной при дрейфе', () => {
    const net = new BrainNet(2)
    net.touch(3)
    const p = net.pulses[0]
    p.age = 0.001
    const a = net.tailPoint(p, 0)!
    p.age = p.dur * 0.999
    const b = net.tailPoint(p, 0)!
    expect(Math.hypot(a[0] - net.x[p.from], a[1] - net.y[p.from])).toBeLessThan(0.6)
    expect(Math.hypot(b[0] - net.x[p.to], b[1] - net.y[p.to])).toBeLessThan(0.6)
    let jump = 0
    p.age = 0.1
    let prev = net.tailPoint(p, 0, net.ctrl(p))!
    for (let i = 0; i < 60; i++) {
      net.step(1 / 30, false)
      if (p.age >= p.dur) break
      const q = net.tailPoint(p, 0, net.ctrl(p))!
      jump = Math.max(jump, Math.hypot(q[0] - prev[0], q[1] - prev[1]))
      prev = q
    }
    expect(jump).toBeLessThan(2.2)
  })
})
