import { BRAIN_DEPTH, BRAIN_NODES } from './brain-regions'
import { MAX_OFFSET, nodeRadius } from './brain-shape'

/**
 * Нейросеть мозга (чистая модель без DOM, рисует UiBrainIcon). Нейроны медленно блуждают около своих мест (не дальше MAX_OFFSET, поэтому не выходят за контур),
 * связи это кубические кривые, которые слегка дышат. Сигнал это импульс: голова с хвостом идёт по кривой связи с ускорением от нейрона и замедлением перед следующим,
 * по прибытии нейрон «принимает» сигнал (recv) и с вероятностью передаёт дальше по другим связям, слабея с каждым шагом. Частота, ветвление и скорость
 * зависят от состояния: думает (часто, ветвится, быстро), отдыхает (редко), только защита (редко и медленно), не отвечает (ничего).
 * Единицы: рисунок мозга 64×64.
 */
export type NetMode = 'thinking' | 'idle' | 'protect' | 'down'
export interface NetPulse { link: number; from: number; to: number; age: number; dur: number; power: number; depth: number; boost: number; sent?: boolean }
export type NetLink = readonly [number, number]

interface ModeCfg { spawn: number; branch: number; depth: number; speed: number; drift: number; wire: number }
const CFG: Record<NetMode, ModeCfg> = {
  thinking: { spawn: 2.6, branch: 0.72, depth: 4, speed: 28, drift: 1, wire: 0.3 },
  idle: { spawn: 0.4, branch: 0.25, depth: 1, speed: 17, drift: 0.45, wire: 0.17 },
  protect: { spawn: 0.22, branch: 0, depth: 0, speed: 9, drift: 0.25, wire: 0.14 },
  down: { spawn: 0, branch: 0, depth: 0, speed: 9, drift: 0, wire: 0.07 },
}
export const MAX_PULSES = 22
export const TAIL = 5                       // сегментов в хвосте импульса, считая голову
const POINTER_R = 20
const POINTER_PULL = 1.2
const CLICK_PUSH = 1.2
const CLICK_R = 24
const LINK_MAX = 15.5

function mulberry32(seed: number): () => number {
  let a = seed | 0
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** Связи: ближайшие пары в пределах linkMax, у нейрона не больше maxDeg; отдельные куски сети затем сшиваются ближайшей парой, чтобы сеть была связной. */
export function buildNetLinks(nodes: ReadonlyArray<readonly [number, number]> = BRAIN_NODES, linkMax = LINK_MAX, maxDeg = 4): NetLink[] {
  const d = (a: number, b: number): number => Math.hypot(nodes[a][0] - nodes[b][0], nodes[a][1] - nodes[b][1])
  const all: Array<{ a: number; b: number; d: number }> = []
  for (let a = 0; a < nodes.length; a++) for (let b = a + 1; b < nodes.length; b++) if (d(a, b) <= linkMax) all.push({ a, b, d: d(a, b) })
  all.sort((p, q) => p.d - q.d)
  const deg = new Array<number>(nodes.length).fill(0)
  const out: Array<[number, number]> = []
  for (const { a, b } of all) {
    if (deg[a] >= maxDeg || deg[b] >= maxDeg) continue
    deg[a]++; deg[b]++
    out.push([a, b])
  }
  const comp = nodes.map((_, i) => i)
  const find = (i: number): number => (comp[i] === i ? i : (comp[i] = find(comp[i])))
  out.forEach(([a, b]) => { comp[find(a)] = find(b) })
  for (;;) {
    let best: [number, number] | null = null
    let bd = Infinity
    for (let a = 0; a < nodes.length; a++) for (let b = a + 1; b < nodes.length; b++) if (find(a) !== find(b) && d(a, b) < bd) { bd = d(a, b); best = [a, b] }
    if (!best) break
    out.push(best)
    comp[find(best[0])] = find(best[1])
  }
  return out
}
export const NET_LINKS: ReadonlyArray<NetLink> = buildNetLinks()

const ease = (t: number): number => 0.45 * t + 0.55 * (t * t * (3 - 2 * t))      // разгон от нейрона и замедление перед следующим

export class BrainNet {
  readonly n = BRAIN_NODES.length
  readonly links: ReadonlyArray<NetLink> = NET_LINKS
  readonly x: number[]
  readonly y: number[]
  readonly recv: number[]                  // сигнал принят, 1 в момент прибытия, затухает
  readonly glow: number[]                  // подсветка связи, по которой идёт импульс
  readonly c1x: number[]; readonly c1y: number[]; readonly c2x: number[]; readonly c2y: number[]
  pulses: NetPulse[] = []
  private ox: number[]; private oy: number[]
  private ptr: [number, number] | null = null
  private t = 0
  private spawnIn = 0.5
  private rnd: () => number
  private nph: Array<[number, number, number, number]>
  private lph: Array<[number, number]>
  private out: number[][]
  private cur: ModeCfg = { ...CFG.idle }
  private mode: NetMode = 'idle'

  constructor(seed = 11) {
    const m = this.links.length
    const fill = (k: number): number[] => new Array<number>(k).fill(0)
    this.x = fill(this.n); this.y = fill(this.n); this.recv = fill(this.n); this.ox = fill(this.n); this.oy = fill(this.n)
    this.glow = fill(m); this.c1x = fill(m); this.c1y = fill(m); this.c2x = fill(m); this.c2y = fill(m)
    this.rnd = mulberry32(seed)
    const r = this.rnd
    this.nph = BRAIN_NODES.map(() => [r() * 6.28, r() * 6.28, 0.5 + r() * 0.6, 0.45 + r() * 0.6])
    this.lph = this.links.map(() => [r() * 6.28, r() * 6.28])
    this.out = Array.from({ length: this.n }, () => [] as number[])
    this.links.forEach(([a, b], i) => { this.out[a].push(i); this.out[b].push(i) })
    this.layout(0)
  }

  /** Состояние: параметры плавно подтягиваются к новым в step(); snap=true ставит их сразу (замершая иконка). */
  setMode(mode: NetMode, snap = false): void {
    this.mode = mode
    if (snap) this.cur = { ...CFG[mode] }
  }
  /** Доля «живости» состояния, 0..1: для яркости связей и узлов. */
  get vigor(): number { return this.cur.drift }
  get wire(): number { return this.cur.wire }
  setPointer(p: [number, number] | null): void { this.ptr = p }

  private layout(dt: number): void {
    const A = 0.35 + 0.85 * this.cur.drift
    const k = 1 - Math.exp(-dt / 0.16)
    for (let i = 0; i < this.n; i++) {
      const [bx, by] = BRAIN_NODES[i]
      const [px, py, fx, fy] = this.nph[i]
      const wx = A * (this.cur.drift > 0 ? Math.sin(this.t * fx + px) : 0)
      const wy = A * (this.cur.drift > 0 ? Math.sin(this.t * fy + py) : 0)
      let tx = 0
      let ty = 0
      if (this.ptr) {                                   // курсор мягко отклоняет ближние нейроны к себе, но не дальше самого курсора
        const dx = this.ptr[0] - bx
        const dy = this.ptr[1] - by
        const d = Math.hypot(dx, dy) || 1
        const w = Math.max(0, 1 - d / POINTER_R) ** 2
        const pull = Math.min(d, POINTER_PULL * w)
        tx = (dx / d) * pull
        ty = (dy / d) * pull
      }
      this.ox[i] += (tx - this.ox[i]) * k
      this.oy[i] += (ty - this.oy[i]) * k
      let ux = wx + this.ox[i]
      let uy = wy + this.oy[i]
      const m = Math.hypot(ux, uy)
      if (m > MAX_OFFSET) { ux = (ux / m) * MAX_OFFSET; uy = (uy / m) * MAX_OFFSET }       // главная гарантия «внутри контура»
      this.x[i] = bx + ux
      this.y[i] = by + uy
    }
    this.links.forEach(([a, b], i) => {
      const dx = this.x[b] - this.x[a]
      const dy = this.y[b] - this.y[a]
      const len = Math.hypot(dx, dy) || 1
      const nx = -dy / len
      const ny = dx / len
      const [p1, p2] = this.lph[i]
      const s1 = 0.09 * len * Math.sin(this.t * 0.55 + p1)
      const s2 = 0.09 * len * Math.sin(this.t * 0.45 + p2 + 1.3)
      this.c1x[i] = this.x[a] + dx / 3 + nx * s1
      this.c1y[i] = this.y[a] + dy / 3 + ny * s1
      this.c2x[i] = this.x[a] + (2 * dx) / 3 + nx * s2
      this.c2y[i] = this.y[a] + (2 * dy) / 3 + ny * s2
    })
  }

  /** Точка на связи; u 0..1 от первого конца связи. */
  bezier(link: number, u: number): [number, number] {
    const [a, b] = this.links[link]
    const v = 1 - u
    const w0 = v * v * v
    const w1 = 3 * v * v * u
    const w2 = 3 * v * u * u
    const w3 = u * u * u
    return [w0 * this.x[a] + w1 * this.c1x[link] + w2 * this.c2x[link] + w3 * this.x[b], w0 * this.y[a] + w1 * this.c1y[link] + w2 * this.c2y[link] + w3 * this.y[b]]
  }

  /** Положение сегмента k хвоста импульса (0 голова) в долях пути; null, пока импульс ещё не вышел. */
  tailPoint(p: NetPulse, k: number): [number, number] | null {
    const t = p.age / p.dur - k * 0.05
    if (p.age < 0) return null
    const u = ease(Math.min(1, Math.max(0, t)))
    return this.bezier(p.link, this.links[p.link][0] === p.from ? u : 1 - u)
  }

  private emit(node: number, except: number, depth: number, power: number, boost: number, delay = 0, away?: number): void {
    const all = this.out[node].filter((l) => l !== except)
    let cand = all.filter((l) => this.links[l][0] === away || this.links[l][1] === away ? false : true)
    if (!cand.length) cand = all
    for (let i = cand.length - 1; i > 0; i--) { const j = Math.floor(this.rnd() * (i + 1)); [cand[i], cand[j]] = [cand[j], cand[i]] }
    const k = Math.min(cand.length, this.rnd() < 0.3 + 0.35 * Math.max(this.cur.branch, boost) ? 2 : 1)
    for (let n = 0; n < k && this.pulses.length < MAX_PULSES; n++) this.launch(cand[n], node, depth, power, boost, delay)
  }
  private launch(link: number, from: number, depth: number, power: number, boost: number, delay = 0): void {
    const [a, b] = this.links[link]
    const to = a === from ? b : a
    const len = Math.hypot(this.x[a] - this.x[b], this.y[a] - this.y[b])
    const speed = Math.max(this.cur.speed, 9) * (1 + 0.35 * boost)
    this.pulses.push({ link, from, to, age: -delay, dur: Math.min(1.5, Math.max(0.3, len / speed)), power, depth, boost })
  }

  /** Нейрон ответил на касание курсора: один тихий импульс. */
  touch(node: number): void {
    this.recv[node] = Math.max(this.recv[node], 0.8)
    this.emit(node, -1, 0, 0.8, 0.35)
  }
  /** Курсор вошёл: пара импульсов идёт к ближайшему к нему нейрону от его дальних соседей. */
  toward(px: number, py: number): void {
    let best = 0
    let bd = Infinity
    for (let i = 0; i < this.n; i++) { const d = Math.hypot(this.x[i] - px, this.y[i] - py); if (d < bd) { bd = d; best = i } }
    const ls = this.out[best].slice().sort((p, q) => this.farther(q, best, px, py) - this.farther(p, best, px, py)).slice(0, 2)
    ls.forEach((l, i) => { const [a, b] = this.links[l]; this.launch(l, a === best ? b : a, 1, 0.75, 0.2, i * 0.12) })
  }
  private farther(l: number, node: number, px: number, py: number): number {
    const [a, b] = this.links[l]
    const o = a === node ? b : a
    return Math.hypot(this.x[o] - px, this.y[o] - py)
  }

  /** Клик в точке: окрестность упруго отталкивается, от нейрона node идёт каскад (глубже и вероятнее обычного, даже в покое). */
  burst(node: number, x: number, y: number): void {
    for (let i = 0; i < this.n; i++) {
      const dx = this.x[i] - x
      const dy = this.y[i] - y
      const d = Math.hypot(dx, dy) || 1
      const w = Math.max(0, 1 - d / CLICK_R)
      this.ox[i] += (dx / d) * CLICK_PUSH * w
      this.oy[i] += (dy / d) * CLICK_PUSH * w
    }
    this.recv[node] = 1
    this.emit(node, -1, 0, 1, 1)
  }

  /** Шаг модели: dt секунд; live=false: без новых импульсов (кадр для замершей иконки). */
  step(dt: number, live: boolean): void {
    const tgt = CFG[this.mode]
    const k = dt > 0 ? 1 - Math.exp(-dt / 0.6) : 0
    for (const key of Object.keys(tgt) as Array<keyof ModeCfg>) this.cur[key] += (tgt[key] - this.cur[key]) * k
    this.t += dt * (0.6 + 0.8 * this.cur.drift)
    this.layout(dt)
    this.spawnIn -= dt
    if (live && this.spawnIn <= 0 && this.cur.spawn > 0.03) {
      this.spawnIn = (0.6 + 0.8 * this.rnd()) / this.cur.spawn
      this.emit(Math.floor(this.rnd() * this.n), -1, 0, 1, 0)
    }
    const old = this.pulses
    this.pulses = []
    const alive: NetPulse[] = []
    for (const p of old) {
      p.age += dt
      if (!p.sent && p.age >= 0) { p.sent = true; this.recv[p.from] = Math.max(this.recv[p.from], 0.8 * p.power) }       // нейрон испускает импульс: на миг «просыпается»
      if (p.age < p.dur) { if (p.age > 0) this.glow[p.link] = 1; alive.push(p); continue }
      this.recv[p.to] = Math.max(this.recv[p.to], p.power)
      const maxDepth = p.boost >= 1 ? 4 : p.boost > 0 ? 1 : MODE_DEPTH(this.mode, this.cur)
      const prob = Math.max(this.cur.branch, p.boost * 0.9) * (0.5 + 0.5 * p.power)
      if (live && p.depth < maxDepth && this.rnd() < prob) this.emit(p.to, p.link, p.depth + 1, p.power * 0.8, p.boost, 0.06, p.from)
    }
    this.pulses = alive.concat(this.pulses).slice(0, MAX_PULSES)
    for (let i = 0; i < this.n; i++) this.recv[i] = Math.max(0, this.recv[i] - dt / 0.5)
    for (let l = 0; l < this.glow.length; l++) this.glow[l] = Math.max(0, this.glow[l] - dt / 0.5)
  }
}
const MODE_DEPTH = (mode: NetMode, cur: ModeCfg): number => Math.round(Math.min(CFG[mode].depth, cur.depth + 0.5))

/** Радиус нейрона i в единицах рисунка (для рисовальщика и тестов). */
export const netNodeRadius = (i: number): number => nodeRadius(BRAIN_DEPTH[i])
