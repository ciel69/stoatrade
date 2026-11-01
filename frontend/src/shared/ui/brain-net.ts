import { BRAIN_DEPTH, BRAIN_NODES } from './brain-regions'
import { MAX_OFFSET, depthAt, nodeRadius } from './brain-shape'

/**
 * Нейросеть мозга (чистая модель без DOM, рисует UiBrainIcon). Нейроны медленно блуждают около своих мест (не дальше MAX_OFFSET, поэтому не выходят за контур).
 * Постоянных связей нет: когда нейрон испускает сигнал, он выбирает одного из 2-3 ближайших В ЭТОТ МОМЕНТ соседей (не дальше LINK_MAX), поэтому из-за блуждания
 * набор соседей и связи со временем меняются. Импульс идёт по кубической кривой между текущими положениями концов (кривая пересчитывается каждый кадр, при дрейфе
 * движение остаётся плавным): головка с хвостом, разгон от нейрона и замедление перед следующим. Нейрон «просыпается» в момент испускания и приёма (recv),
 * затем растворяется; принявший может передать дальше, каждый шаг слабее. Частота, ветвление и скорость зависят от состояния: думает (часто, ветвится, быстро),
 * отдыхает (редко), только защита (редко и медленно), не отвечает (ничего). Единицы: рисунок мозга 64×64.
 */
export type NetMode = 'thinking' | 'idle' | 'protect' | 'down'
export interface NetPulse { from: number; to: number; age: number; dur: number; power: number; depth: number; boost: number; b1: number; b2: number; sent?: boolean; arrived?: boolean }
export type NetLink = readonly [number, number]

interface ModeCfg { spawn: number; branch: number; depth: number; speed: number; drift: number }
const CFG: Record<NetMode, ModeCfg> = {
  thinking: { spawn: 2.6, branch: 0.72, depth: 4, speed: 28, drift: 1 },
  idle: { spawn: 0.4, branch: 0.25, depth: 1, speed: 17, drift: 0.45 },
  protect: { spawn: 0.22, branch: 0, depth: 0, speed: 9, drift: 0.25 },
  down: { spawn: 0, branch: 0, depth: 0, speed: 9, drift: 0 },
}
export const MAX_PULSES = 28
export const TAIL = 5                       // сегментов в хвосте импульса, считая голову
export const LINK_MAX = 15.5                // дальше этого связь не образуется
export const NEIGHBORS = 3                  // нейрон отправляет сигнал одному из стольких ближайших соседей
export const MAX_DEND = 4                   // отростков у нейрона не больше (у каждого от 2 до 4)
export const LINGER = 0.5                  // сколько линия связи остаётся после прибытия и гаснет, с
const POINTER_R = 20
const POINTER_PULL = 1.2
const CLICK_PUSH = 1.2
const CLICK_R = 24

function mulberry32(seed: number): () => number {
  let a = seed | 0
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** Фиксированные пары по базовым местам: только для подсветки области (окно запуска, роли), сама сеть связей не хранит. */
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
  return out
}
export const NET_LINKS: ReadonlyArray<NetLink> = buildNetLinks()

export const easeU = (t: number): number => 0.45 * t + 0.55 * (t * t * (3 - 2 * t))      // разгон от нейрона и замедление перед следующим

export class BrainNet {
  readonly n = BRAIN_NODES.length
  readonly x: number[]
  readonly y: number[]
  readonly recv: number[]                  // нейрон испустил или принял сигнал: 1 в этот момент, затухает
  readonly act: number[]                   // видимость нейрона: быстро проявляется за recv (~0.1 с) и растворяется до нуля вместе с ним
  readonly dcount: number[]                // сколько у нейрона отростков (2..4)
  readonly dang: number[][]                // направление каждого отростка: плавно поворачивается к ближайшим соседям
  readonly dlen: number[][]                // длина отростка (укорачивается у края контура)
  readonly dglow: number[][]               // подсветка отростка, по которому идёт сигнал
  private dbase: number[][]
  pulses: NetPulse[] = []
  private ox: number[]; private oy: number[]
  private ptr: [number, number] | null = null
  private t = 0
  private spawnIn = 0.5
  private rnd: () => number
  private nph: Array<[number, number, number, number]>
  private cur: ModeCfg = { ...CFG.idle }
  private mode: NetMode = 'idle'

  constructor(seed = 11) {
    const fill = (k: number): number[] => new Array<number>(k).fill(0)
    this.x = fill(this.n); this.y = fill(this.n); this.recv = fill(this.n); this.ox = fill(this.n); this.oy = fill(this.n)
    this.rnd = mulberry32(seed)
    const r = this.rnd
    this.nph = BRAIN_NODES.map(() => [r() * 6.28, r() * 6.28, 0.5 + r() * 0.6, 0.45 + r() * 0.6])
    this.act = fill(this.n)
    this.dcount = BRAIN_NODES.map((_, i) => 2 + ((i * 5 + 1) % 3))
    const slots = (f: (i: number, k: number) => number): number[][] => BRAIN_NODES.map((_, i) => Array.from({ length: MAX_DEND }, (_, k) => f(i, k)))
    this.dang = slots(() => r() * 6.28)
    this.dbase = slots((i, k) => 2.2 + ((i * 3 + k * 7) % 5) * 0.55)
    this.dlen = slots((i, k) => this.dbase[i][k])
    this.dglow = slots(() => 0)
    this.layout(0)
    this.dendrites(0)
  }

  /** Отростки: каждый тянется к одному из ближайших соседей (по текущим положениям) и плавно поворачивается, когда соседи меняются; у края контура укорачивается. */
  private dendrites(dt: number): void {
    const ka = dt > 0 ? 1 - Math.exp(-dt / 0.25) : 1
    const kl = dt > 0 ? 1 - Math.exp(-dt / 0.5) : 1
    for (let i = 0; i < this.n; i++) {
      const nb = this.nearest(i, MAX_DEND)
      for (let k = 0; k < this.dcount[i]; k++) {
        let target = this.dang[i][k]
        let reach = this.dbase[i][k]
        if (nb.length) {
          const j = nb[k % nb.length]
          const lap = Math.floor(k / nb.length)                                       // если соседей меньше отростков, лишние расходятся веером
          target = Math.atan2(this.y[j] - this.y[i], this.x[j] - this.x[i]) + (lap ? 0.55 * lap * (k % 2 ? 1 : -1) : 0)
          reach = Math.min(reach, 0.55 * Math.hypot(this.x[j] - this.x[i], this.y[j] - this.y[i]))
        }
        let d = target - this.dang[i][k]
        d -= Math.round(d / 6.2832) * 6.2832
        this.dang[i][k] += d * ka
        let L = reach
        for (let s = 0; s < 8; s++) {
          const [tx, ty, cx, cy] = this.dendPoints(i, k, L)
          const px = this.x[i]; const py = this.y[i]
          if (depthAt(px + tx, py + ty) >= 0.3 && depthAt(px + 0.5 * cx + 0.25 * tx, py + 0.5 * cy + 0.25 * ty) >= 0.3) break
          L *= 0.75
        }
        const cur = this.dlen[i][k]
        this.dlen[i][k] = L < cur ? L : cur + (L - cur) * kl
      }
    }
  }
  /** Конец и контрольная точка изогнутого отростка относительно центра нейрона: [tx, ty, cx, cy]; длину можно задать (иначе текущая). */
  dendPoints(i: number, k: number, len = this.dlen[i][k], out: number[] = [0, 0, 0, 0]): number[] {
    const a = this.dang[i][k]
    const ca = Math.cos(a)
    const sa = Math.sin(a)
    const bend = ((i + k) % 2 ? 1 : -1) * 0.22 * len
    out[0] = ca * len; out[1] = sa * len
    out[2] = ca * len * 0.5 - sa * bend; out[3] = sa * len * 0.5 + ca * bend
    return out
  }
  /** Подсветить у нейрона отросток к соседу partner (или ближайший по направлению). */
  private mark(node: number, partner: number, v: number): void {
    const px = this.x[partner] - this.x[node]
    const py = this.y[partner] - this.y[node]
    const want = Math.atan2(py, px)
    let best = 0
    let bd = Infinity
    for (let k = 0; k < this.dcount[node]; k++) {
      let d = this.dang[node][k] - want
      d -= Math.round(d / 6.2832) * 6.2832
      if (Math.abs(d) < bd) { bd = Math.abs(d); best = k }
    }
    this.dglow[node][best] = Math.max(this.dglow[node][best], v)
  }

  /** Состояние: параметры плавно подтягиваются к новым в step(); snap=true ставит их сразу (замершая иконка). */
  setMode(mode: NetMode, snap = false): void {
    this.mode = mode
    if (snap) this.cur = { ...CFG[mode] }
  }
  /** Доля «живости» состояния, 0..1. */
  get vigor(): number { return this.cur.drift }
  setPointer(p: [number, number] | null): void { this.ptr = p }

  private layout(dt: number): void {
    const A = 0.5 + 0.9 * this.cur.drift
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
  }

  /** Ближайшие к нейрону node соседи по ТЕКУЩИМ положениям: до k штук, не дальше LINK_MAX, без except; от ближнего к дальнему. */
  nearest(node: number, k = NEIGHBORS, except = -1): number[] {
    const c: Array<{ i: number; d: number }> = []
    for (let i = 0; i < this.n; i++) {
      if (i === node || i === except) continue
      const d = Math.hypot(this.x[i] - this.x[node], this.y[i] - this.y[node])
      if (d <= LINK_MAX) c.push({ i, d })
    }
    return c.sort((a, b) => a.d - b.d).slice(0, k).map((e) => e.i)
  }

  /** Контрольные точки кривой импульса по текущим положениям концов: [c1x, c1y, c2x, c2y]. Изгиб это доля длины, поэтому при дрейфе кривая меняется плавно. */
  ctrl(p: NetPulse, out: number[] = [0, 0, 0, 0]): number[] {
    const ax = this.x[p.from]; const ay = this.y[p.from]
    const dx = this.x[p.to] - ax
    const dy = this.y[p.to] - ay
    const len = Math.hypot(dx, dy) || 1
    const nx = -dy / len
    const ny = dx / len
    const s1 = 0.09 * len * p.b1
    const s2 = 0.09 * len * p.b2
    out[0] = ax + dx / 3 + nx * s1; out[1] = ay + dy / 3 + ny * s1
    out[2] = ax + (2 * dx) / 3 + nx * s2; out[3] = ay + (2 * dy) / 3 + ny * s2
    return out
  }
  /** Точка кривой импульса; u 0..1 от нейрона from к to. */
  bezier(p: NetPulse, u: number, c: number[] = this.ctrl(p)): [number, number] {
    const v = 1 - u
    const w0 = v * v * v
    const w1 = 3 * v * v * u
    const w2 = 3 * v * u * u
    const w3 = u * u * u
    return [w0 * this.x[p.from] + w1 * c[0] + w2 * c[2] + w3 * this.x[p.to], w0 * this.y[p.from] + w1 * c[1] + w2 * c[3] + w3 * this.y[p.to]]
  }
  /** Положение сегмента k хвоста (0 голова); null, пока импульс ещё не вышел. */
  tailPoint(p: NetPulse, k: number, c?: number[]): [number, number] | null {
    if (p.age < 0) return null
    const t = p.age / p.dur - k * 0.05
    return this.bezier(p, easeU(Math.min(1, Math.max(0, t))), c)
  }

  private emit(node: number, except: number, depth: number, power: number, boost: number, delay = 0): void {
    const cand = this.nearest(node, NEIGHBORS, except)
    if (!cand.length) return
    for (let i = cand.length - 1; i > 0; i--) { const j = Math.floor(this.rnd() * (i + 1)); [cand[i], cand[j]] = [cand[j], cand[i]] }
    const k = Math.min(cand.length, this.rnd() < 0.3 + 0.35 * Math.max(this.cur.branch, boost) ? 2 : 1)
    for (let n = 0; n < k && this.pulses.length < MAX_PULSES; n++) this.launch(node, cand[n], depth, power, boost, delay)
  }
  private launch(from: number, to: number, depth: number, power: number, boost: number, delay = 0): void {
    const len = Math.hypot(this.x[from] - this.x[to], this.y[from] - this.y[to])
    const speed = Math.max(this.cur.speed, 9) * (1 + 0.35 * boost)
    this.pulses.push({ from, to, age: -delay, dur: Math.min(1.5, Math.max(0.3, len / speed)), power, depth, boost, b1: this.rnd() * 2 - 1, b2: this.rnd() * 2 - 1 })
  }

  /** Нейрон ответил на касание курсора: один тихий импульс. */
  touch(node: number): void {
    this.recv[node] = Math.max(this.recv[node], 0.8)
    this.emit(node, -1, 0, 0.8, 0.35)
  }
  /** Курсор вошёл: пара импульсов идёт к ближайшему к нему нейрону от его соседей. */
  toward(px: number, py: number): void {
    let best = 0
    let bd = Infinity
    for (let i = 0; i < this.n; i++) { const d = Math.hypot(this.x[i] - px, this.y[i] - py); if (d < bd) { bd = d; best = i } }
    this.nearest(best, 2).forEach((s, i) => this.launch(s, best, 1, 0.75, 0.2, i * 0.12))
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
    this.dendrites(dt)
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
      if (!p.sent && p.age >= 0) { p.sent = true; this.recv[p.from] = Math.max(this.recv[p.from], 0.8 * p.power); this.mark(p.from, p.to, p.power) }       // нейрон испускает импульс
      if (!p.arrived && p.age >= p.dur) {
        p.arrived = true
        this.recv[p.to] = Math.max(this.recv[p.to], p.power)
        this.mark(p.to, p.from, p.power)
        const maxDepth = p.boost >= 1 ? 4 : p.boost > 0 ? 1 : Math.round(Math.min(CFG[this.mode].depth, this.cur.depth + 0.5))
        const prob = p.boost >= 1 ? (p.depth === 0 ? 1 : 0.9) : Math.max(this.cur.branch, p.boost * 0.9) * (0.5 + 0.5 * p.power)
        if (live && p.depth < maxDepth && this.rnd() < prob) this.emit(p.to, p.from, p.depth + 1, p.power * 0.8, p.boost, 0.06)
      }
      if (p.age < p.dur + LINGER) alive.push(p)
    }
    this.pulses = alive.concat(this.pulses).slice(0, MAX_PULSES)
    const up = dt > 0 ? 1 - Math.exp(-dt / 0.07) : 1
    for (let i = 0; i < this.n; i++) {
      this.recv[i] = Math.max(0, this.recv[i] - dt / 0.6)
      this.act[i] = this.recv[i] > this.act[i] ? this.act[i] + (this.recv[i] - this.act[i]) * up : this.recv[i]      // проявляется за ~0.1 с, растворяется до нуля за ~0.6 с
      for (let k = 0; k < MAX_DEND; k++) this.dglow[i][k] = Math.max(0, this.dglow[i][k] - dt / 0.7)
    }
  }
}

/** Радиус нейрона i в единицах рисунка (для рисовальщика и тестов). */
export const netNodeRadius = (i: number): number => nodeRadius(BRAIN_DEPTH[i])
