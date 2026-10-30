import { BRAIN_NODES } from './brain-regions'

/**
 * Живая сеть мозга (чистая модель без DOM, рисует UiBrainIcon). Нейроны медленно блуждают около своих мест, связи изогнуты и дышат; каждая связь то образуется,
 * то растворяется (по расстоянию между нейронами и по собственному медленному ритму), поэтому соединения со временем меняются. Сигнал это импульс: короткая точка с хвостом
 * бежит по кривой связи, по прибытии нейрон возбуждается и с вероятностью передаёт импульс дальше по другим связям (каскад, как потенциал действия). В «думает» каскады частые
 * и длинные, в ожидании редкие одиночные импульсы.
 */
export interface Pulse { link: number; from: number; to: number; age: number; dur: number; depth: number; power: number; boost: number }

const N = BRAIN_NODES.length
const LINK_MAX = 17          // дальше этого расстояния связь не образуется
const MAX_DEG = 4            // у нейрона не больше четырёх связей: сеть остаётся разреженной
const MAX_PULSES = 30
const POINTER_R = 19         // радиус влияния курсора, единиц рисунка
const POINTER_PULL = 5       // на сколько ближний нейрон тянется к курсору
const CLICK_PUSH = 4.2       // толчок нейрона от места клика
const CLICK_R = 24

/** Кандидаты в связи: ближайшие пары, у каждого нейрона не больше MAX_DEG. */
function buildLinks(): Array<[number, number]> {
  const all: Array<{ a: number; b: number; d: number }> = []
  for (let a = 0; a < N; a++) for (let b = a + 1; b < N; b++) {
    const d = Math.hypot(BRAIN_NODES[a][0] - BRAIN_NODES[b][0], BRAIN_NODES[a][1] - BRAIN_NODES[b][1])
    if (d <= LINK_MAX) all.push({ a, b, d })
  }
  all.sort((p, q) => p.d - q.d)
  const deg = new Array<number>(N).fill(0)
  const out: Array<[number, number]> = []
  for (const { a, b } of all) {
    if (deg[a] >= MAX_DEG || deg[b] >= MAX_DEG) continue
    deg[a]++; deg[b]++
    out.push([a, b])
  }
  return out
}
export const LINKS: ReadonlyArray<readonly [number, number]> = buildLinks()
export const PULSE_POOL = MAX_PULSES

function mulberry32(seed: number): () => number {
  let a = seed | 0
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
const smooth = (e0: number, e1: number, x: number): number => {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)))
  return t * t * (3 - 2 * t)
}

export class BrainSim {
  readonly x = new Array<number>(N).fill(0)
  readonly y = new Array<number>(N).fill(0)
  readonly str = new Array<number>(LINKS.length).fill(0)        // насколько связь сейчас есть, 0..1
  readonly glow = new Array<number>(LINKS.length).fill(0)       // подсветка связи, по которой идёт импульс
  readonly fire = new Array<number>(N).fill(0)                  // возбуждение нейрона, 1 в момент прибытия импульса и затухает
  readonly qx = new Array<number>(LINKS.length).fill(0)         // контрольная точка изгиба связи
  readonly qy = new Array<number>(LINKS.length).fill(0)
  pulses: Pulse[] = []
  private ox = new Array<number>(N).fill(0)                    // смещение нейрона от курсора и от толчка клика, плавно возвращается к нулю
  private oy = new Array<number>(N).fill(0)
  private ptr: [number, number] | null = null                  // курсор в координатах рисунка
  private t = 0
  private spawnIn = 0.4
  private rnd: () => number
  private nph: Array<[number, number, number, number]>          // фазы и частоты блуждания нейрона
  private lph: Array<[number, number, number]>                  // период и фаза образования/растворения связи, фаза изгиба
  private out: number[][]

  constructor(seed = 11) {
    this.rnd = mulberry32(seed)
    const r = this.rnd
    this.nph = BRAIN_NODES.map(() => [r() * 6.28, r() * 6.28, 0.18 + r() * 0.22, 0.15 + r() * 0.2])
    this.lph = LINKS.map(() => [9 + r() * 14, r() * 6.28, r() * 6.28])
    this.out = Array.from({ length: N }, () => [] as number[])
    LINKS.forEach(([a, b], i) => { this.out[a].push(i); this.out[b].push(i) })
    this.layout(0)
  }

  /** Положение нейронов, сила и изгиб связей в момент this.t; drift 0..1 это размах блуждания. */
  private layout(drift: number, dt = 0): void {
    const A = 0.7 + 1.5 * drift
    const k = 1 - Math.exp(-dt / 0.14)
    for (let i = 0; i < N; i++) {
      const [px, py, fx, fy] = this.nph[i]
      const wx = BRAIN_NODES[i][0] + A * Math.sin(this.t * fx * 3 + px)
      const wy = BRAIN_NODES[i][1] + A * Math.sin(this.t * fy * 3 + py)
      let tx = 0
      let ty = 0
      if (this.ptr) {                                          // курсор мягко тянет ближние нейроны к себе, но не дальше самого курсора
        const dx = this.ptr[0] - wx
        const dy = this.ptr[1] - wy
        const d = Math.hypot(dx, dy) || 1
        const w = Math.max(0, 1 - d / POINTER_R) ** 2
        const pull = Math.min(d, POINTER_PULL * w)
        tx = (dx / d) * pull
        ty = (dy / d) * pull
      }
      this.ox[i] += (tx - this.ox[i]) * k
      this.oy[i] += (ty - this.oy[i]) * k
      this.x[i] = wx + this.ox[i]
      this.y[i] = wy + this.oy[i]
    }
    LINKS.forEach(([a, b], i) => {
      const dx = this.x[b] - this.x[a]
      const dy = this.y[b] - this.y[a]
      const d = Math.hypot(dx, dy) || 1
      const prox = 1 - smooth(LINK_MAX * 0.55, LINK_MAX * 1.05, d)                    // близкие нейроны связаны крепче, далёкие рвутся
      const [per, p1, p2] = this.lph[i]
      const gate = smooth(0.1, 0.55, 0.5 + 0.5 * Math.sin((this.t * 6.283) / per + p1))   // своё медленное «образуется, растворяется»
      this.str[i] = prox * gate
      const bend = (1.4 + 1.6 * Math.sin(this.t * 0.5 + p2)) * (i % 2 ? 1 : -1)
      this.qx[i] = (this.x[a] + this.x[b]) / 2 - (dy / d) * bend
      this.qy[i] = (this.y[a] + this.y[b]) / 2 + (dx / d) * bend
    })
  }

  /** Точка на связи (кривая Безье) на доле u пути от нейрона from. */
  point(p: Pulse, back = 0): [number, number] {
    const [a, b] = LINKS[p.link]
    const fwd = a === p.from
    let u = Math.min(1, Math.max(0, p.age / p.dur - back))
    u = u * 0.7 + (u * u * (3 - 2 * u)) * 0.3
    if (!fwd) u = 1 - u
    const v = 1 - u
    return [v * v * this.x[a] + 2 * v * u * this.qx[p.link] + u * u * this.x[b], v * v * this.y[a] + 2 * v * u * this.qy[p.link] + u * u * this.y[b]]
  }

  /** Курсор над мозгом (x, y в координатах рисунка) или null, когда ушёл. */
  setPointer(p: [number, number] | null): void { this.ptr = p }

  /** Нейрон ответил на касание курсора: одиночный тихий импульс. */
  touch(node: number): void {
    this.fire[node] = Math.max(this.fire[node], 0.9)
    this.emit(node, -1, 0, 0.9, 0, 0.35)
  }

  /** Клик в точке (x, y): ближние нейроны упруго отталкиваются, от нейрона node идёт каскад импульсов (глубже и вероятнее обычного, даже в ожидании). */
  burst(node: number, x: number, y: number): void {
    for (let i = 0; i < N; i++) {
      const dx = this.x[i] - x
      const dy = this.y[i] - y
      const d = Math.hypot(dx, dy) || 1
      const w = Math.max(0, 1 - d / CLICK_R)
      this.ox[i] += (dx / d) * CLICK_PUSH * w
      this.oy[i] += (dy / d) * CLICK_PUSH * w
    }
    this.fire[node] = 1
    this.emit(node, -1, 0, 1, 0, 1)
  }

  private emit(node: number, except: number, depth: number, power: number, amp: number, boost = 0): void {
    const all = this.out[node].filter((l) => l !== except)
    const cand = all.filter((l) => this.str[l] > 0.3)
    if (boost >= 1 && !cand.length) cand.push(...all.sort((a, b) => this.str[b] - this.str[a]).slice(0, 2))      // клик не должен пропасть, если связи вокруг растворились
    for (let i = cand.length - 1; i > 0; i--) { const j = Math.floor(this.rnd() * (i + 1)); [cand[i], cand[j]] = [cand[j], cand[i]] }
    const k = Math.min(cand.length, this.rnd() < 0.35 + 0.3 * Math.max(amp, boost) ? 2 : 1)
    for (let n = 0; n < k && this.pulses.length < MAX_PULSES; n++) {
      const l = cand[n]
      const [a, b] = LINKS[l]
      const to = a === node ? b : a
      const len = Math.hypot(this.x[a] - this.x[b], this.y[a] - this.y[b])
      this.pulses.push({ link: l, from: node, to, age: 0, dur: Math.min(1.1, Math.max(0.28, len / (22 + 20 * Math.max(amp, boost)))), depth, power, boost })
    }
  }

  /** Шаг модели: dt секунд, amp 0..1 это насколько «думает», live false: без новых импульсов (кадр для замершей иконки). */
  step(dt: number, amp: number, live: boolean): void {
    this.t += dt * (0.7 + 0.9 * amp)
    this.layout(amp, dt)
    this.spawnIn -= dt
    if (live && this.spawnIn <= 0) {
      this.spawnIn = (0.7 + 0.7 * this.rnd()) / (0.45 + 3.4 * amp)
      this.emit(Math.floor(this.rnd() * N), -1, 0, 1, amp)
    }
    const old = this.pulses
    this.pulses = []                       // сюда emit кладёт новые импульсы (каскад и новые источники)
    const alive: Pulse[] = []
    for (const p of old) {
      p.age += dt
      if (p.age < p.dur) { this.glow[p.link] = 1; alive.push(p); continue }
      this.fire[p.to] = 1
      const e = Math.max(amp, p.boost)                 // клик и касание ведут свой каскад независимо от состояния мозга
      const maxDepth = p.boost >= 1 ? 5 : p.boost > 0 ? 1 : amp > 0.5 ? 4 : 1
      if (live && p.depth < maxDepth && this.rnd() < 0.3 + 0.55 * e) this.emit(p.to, p.link, p.depth + 1, p.power * 0.85, amp, p.boost)
    }
    this.pulses = alive.concat(this.pulses).slice(0, MAX_PULSES)
    for (let i = 0; i < N; i++) this.fire[i] = Math.max(0, this.fire[i] - dt / 0.55)
    for (let l = 0; l < this.glow.length; l++) this.glow[l] = Math.max(0, this.glow[l] - dt / 0.6)
  }
}
