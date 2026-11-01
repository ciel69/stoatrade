<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { BRAIN_DEPTH, BRAIN_NODES, nearestNode, waveOrder } from './brain-regions'
import { BRAIN_PATH, FOLD_PATH } from './brain-shape'
import { BrainNet, MAX_PULSES, NET_LINKS, TAIL, netNodeRadius } from './brain-net'
/**
 * Нарисованный мозг (вид спереди): аккуратный контур из двух долей с мягкой заливкой, борозды линиями, 24 нейрона разной глубины и связи между ними.
 * Всё внутри контура: места нейронов подогнаны под форму с запасом (brain-shape.ts), отклонение ограничено, сверху ещё clipPath по контуру.
 *
 * Нейрон: тонкий кольцевой контур, мягкая градиентная заливка с бликом (без свечения), ядро. Принявший сигнал нейрон на миг крупнеет и меняет фактуру
 * (плотная заливка, яркое ядро) и отпускает тонкую расходящуюся окружность. Импульс идёт по кривой связи с разгоном и замедлением, за ним тянется хвост
 * из пяти сегментов; по прибытии нейрон принимает сигнал и может передать дальше (ветвление), каждый шаг слабее.
 *
 * Состояния: думает (много каскадов), отдыхает (редкие одиночные), только защита (редко и медленно, янтарный), не отвечает (приглушён, без импульсов, перечёркнут).
 * Движение: модель считается кадрами (30 в секунду) и пишет положения в атрибуты SVG; цикл стоит, пока вкладка скрыта или иконка вне экрана.
 * Курсор: наклон всей иконки (transform) и мягкое отклонение ближних нейронов, пара импульсов навстречу; клик: каскад от ближайшего нейрона.
 * prefers-reduced-motion: статичная иконка без импульсов и наклона. crisp: режим окна запуска (тоньше линии).
 */
export type BrainIconState = 'idle' | 'thinking' | 'protect' | 'down'
const props = withDefaults(defineProps<{ state?: BrainIconState; size?: number; label?: string; interactive?: boolean; lit?: readonly number[]; crisp?: boolean }>(), { state: 'idle', size: 88, interactive: false, lit: () => [], crisp: false })
const emit = defineEmits<{ node: [index: number] }>()

let uid = (globalThis as { __brainUid?: number }).__brainUid ?? 0
uid = (globalThis as { __brainUid?: number }).__brainUid = uid + 1
const id = `br${uid}`

const nodes = BRAIN_NODES
const links = NET_LINKS
const radii = BRAIN_DEPTH.map((_, i) => netNodeRadius(i))
const pool = Array.from({ length: MAX_PULSES * TAIL }, (_, i) => i)

const svg = ref<SVGSVGElement | null>(null)
const canHover = typeof matchMedia === 'function' && matchMedia('(hover: hover) and (pointer: fine)').matches
const calm = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
const fired = ref<number[]>([])
let timers: ReturnType<typeof setTimeout>[] = []
const isOn = (i: number): boolean => fired.value.includes(i) || props.lit.includes(i)
const hasLit = computed(() => props.lit.length > 0)
const hlLink = computed(() => links.map(([a, b]) => props.lit.includes(a) && props.lit.includes(b)))

const net = new BrainNet(7 + Math.floor(Math.random() * 90))
net.setMode(props.state, true)
type El = SVGElement
let els: { wire: El[]; hl: El[]; g: El[]; body: El[]; hot: El[]; core: El[]; rip: El[]; imp: El[] } | null = null
const prevRecv = new Array<number>(nodes.length).fill(-1)
const pulseOn = new Array<boolean>(MAX_PULSES).fill(false)
const num = (v: number): string => v.toFixed(2)

function collect(): void {
  const q = (c: string): El[] => Array.from(svg.value!.querySelectorAll<SVGElement>(c))
  els = { wire: q('.wire'), hl: q('.wire-hl'), g: q('.nd'), body: q('.nb'), hot: q('.nh'), core: q('.nc'), rip: q('.nr'), imp: q('.imp circle') }
}
function draw(): void {
  if (!els) return
  const e = els
  const wireBase = net.wire
  links.forEach(([a, b], i) => {
    const dd = `M${num(net.x[a])} ${num(net.y[a])}C${num(net.c1x[i])} ${num(net.c1y[i])} ${num(net.c2x[i])} ${num(net.c2y[i])} ${num(net.x[b])} ${num(net.y[b])}`
    e.wire[i].setAttribute('d', dd)
    e.wire[i].style.opacity = String(Math.min(1, wireBase + 0.6 * net.glow[i]).toFixed(3))
    if (hasLit.value) e.hl[i].setAttribute('d', dd)
  })
  for (let i = 0; i < nodes.length; i++) {
    e.g[i].setAttribute('transform', `translate(${num(net.x[i])} ${num(net.y[i])})`)
    const rv = net.recv[i] < 0.01 ? 0 : net.recv[i]
    if (rv === prevRecv[i]) continue
    prevRecv[i] = rv
    const r0 = radii[i]
    e.body[i].setAttribute('r', num(r0 * (1 + 0.22 * rv)))
    e.hot[i].setAttribute('r', num(r0 * (1 + 0.22 * rv)))
    e.hot[i].style.opacity = String(rv.toFixed(3))
    e.core[i].setAttribute('r', num(r0 * (0.34 + 0.18 * rv)))
    e.rip[i].setAttribute('r', num(r0 * (1.2 + 1.2 * (1 - rv))))
    e.rip[i].style.opacity = String((0.5 * rv * rv).toFixed(3))
  }
  const vis = net.vigor > 0.1 ? 1 : 0.8
  for (let p = 0; p < MAX_PULSES; p++) {
    const pl = net.pulses[p]
    const on = !!pl && pl.age > 0
    if (!on) {
      if (pulseOn[p]) { for (let k = 0; k < TAIL; k++) e.imp[p * TAIL + k].style.opacity = '0'; pulseOn[p] = false }
      continue
    }
    pulseOn[p] = true
    const fade = 1 - 0.3 * Math.max(0, pl.age / pl.dur - 0.7) / 0.3                    // перед прибытием импульс чуть гаснет
    for (let k = 0; k < TAIL; k++) {
      const c = e.imp[p * TAIL + k]
      const pt = net.tailPoint(pl, k)
      if (!pt) { c.style.opacity = '0'; continue }
      c.setAttribute('cx', num(pt[0])); c.setAttribute('cy', num(pt[1]))
      c.setAttribute('r', num((1.5 - 0.2 * k) * (0.65 + 0.35 * pl.power) * (props.crisp ? 0.85 : 1)))
      c.style.opacity = String((Math.pow(1 - k / TAIL, 1.6) * pl.power * fade * vis).toFixed(3))
    }
  }
}

// ---- цикл: 30 кадров в секунду, стоит при скрытой вкладке и вне экрана
let loop = 0
let last = 0
let acc = 0
let inView = true
let tx = 0                           // целевой наклон, -1..1
let ty = 0
let cx = 0                           // текущий
let cy = 0
let wrote = ''
function tilt(): void {
  if (!svg.value) return
  const k = 0.14
  cx += (tx - cx) * k
  cy += (ty - cy) * k
  const s = `perspective(520px) rotateX(${(-cy * 6).toFixed(2)}deg) rotateY(${(cx * 8).toFixed(2)}deg)`
  const flat = Math.abs(cx) < 0.003 && Math.abs(cy) < 0.003
  const next = flat ? '' : s
  if (next !== wrote) { wrote = next; svg.value.style.transform = next }
}
function frame(ts: number): void {
  loop = requestAnimationFrame(frame)
  acc += Math.min(0.1, (ts - last) / 1000)
  last = ts
  if (acc < 0.032) return
  net.step(acc, true)
  acc = 0
  tilt()
  draw()
}
const alive = (): boolean => !calm && props.state !== 'down' && inView && !document.hidden
function run(): void {
  if (!els) return
  if (alive()) {
    if (!loop) { last = performance.now(); acc = 0; loop = requestAnimationFrame(frame) }
    return
  }
  if (loop) { cancelAnimationFrame(loop); loop = 0 }
  net.setPointer(null)
  tx = ty = cx = cy = 0
  tilt()
  if (calm || props.state === 'down') { net.setMode(props.state, true); net.step(0, false); draw() }
}
let io: IntersectionObserver | null = null
const onVis = (): void => run()
onMounted(() => {
  collect()
  if (typeof IntersectionObserver === 'function' && svg.value) {
    io = new IntersectionObserver((en) => { inView = en[en.length - 1].isIntersecting; run() })
    io.observe(svg.value)
  }
  document.addEventListener('visibilitychange', onVis)
  net.step(0, false)
  draw()
  run()
})
watch(() => props.state, (s) => { net.setMode(s); run() })
watch(hasLit, () => draw())
onUnmounted(() => {
  timers.forEach(clearTimeout)
  cancelAnimationFrame(loop)
  io?.disconnect()
  document.removeEventListener('visibilitychange', onVis)
})

// ---- курсор и клик
let overNode = -1
let lastTouch = 0
let lastToward = 0
const toBox = (ev: MouseEvent): [number, number] => {
  const r = svg.value!.getBoundingClientRect()
  return [((ev.clientX - r.left) / r.width) * 64, ((ev.clientY - r.top) / r.height) * 64]
}
function onMove(ev: PointerEvent): void {
  if (!props.interactive || !canHover || ev.pointerType === 'touch' || !svg.value || calm) return
  const [x, y] = toBox(ev)
  if (loop) {
    net.setPointer([x, y])
    tx = Math.max(-1, Math.min(1, (x - 32) / 32))
    ty = Math.max(-1, Math.min(1, (y - 32) / 32))
  }
  const n = nearestNode(x, y, 6)
  if (n !== overNode) {
    overNode = n
    emit('node', n)
    const t = performance.now()
    if (n >= 0 && loop && t - lastTouch > 220) { lastTouch = t; net.touch(n) }      // курсор вошёл в нейрон: он отвечает тихим импульсом
  }
}
function onEnter(ev: PointerEvent): void {
  if (!props.interactive || !canHover || ev.pointerType === 'touch' || !loop || !svg.value) return
  const t = performance.now()
  if (t - lastToward < 2000) return
  lastToward = t
  const [x, y] = toBox(ev)
  net.toward(x, y)                                  // пара импульсов идёт к нейрону рядом с курсором
}
function onLeave(): void {
  net.setPointer(null)
  tx = ty = 0
  if (overNode !== -1) { overNode = -1; emit('node', -1) }
}

/** Каскад: от ближайшего к точке нажатия нейрона идут импульсы, окрестность упруго отталкивается; без точки (шаг запуска) старт с любого нейрона. */
function fire(ev?: MouseEvent): void {
  if (!svg.value) return
  timers.forEach(clearTimeout)
  timers = []
  const pos = ev && ev.clientX ? toBox(ev) : null
  const [x, y] = pos ?? [32, 32]
  if (loop) {
    const n = pos ? nearestNode(x, y, 64) : Math.floor(Math.random() * nodes.length)
    net.burst(n, x, y)
    return
  }
  const order = waveOrder(x, y)
  if (calm) {
    fired.value = order
    timers.push(setTimeout(() => (fired.value = []), 500))
    return
  }
  fired.value = []
  order.forEach((n, k) => {
    timers.push(setTimeout(() => (fired.value = [...fired.value, n]), k * 40))
    timers.push(setTimeout(() => (fired.value = fired.value.filter((i) => i !== n)), k * 40 + 340))
  })
}
defineExpose({ fire })
</script>

<template>
  <svg ref="svg" class="brain" :class="[`s-${state}`, { live: interactive, 'has-lit': hasLit, crisp }]" :width="size" :height="size" viewBox="0 0 64 64" role="img" :aria-label="label ?? 'мозг'"
       fill="none" stroke-linecap="round" stroke-linejoin="round" @pointermove="onMove" @pointerenter="onEnter" @pointerleave="onLeave">
    <defs>
      <clipPath :id="`${id}c`"><path :d="BRAIN_PATH" /></clipPath>
      <linearGradient :id="`${id}f`" x1="0" y1="0" x2="0.35" y2="1">
        <stop offset="0" stop-color="currentColor" stop-opacity="0.2" />
        <stop offset="1" stop-color="currentColor" stop-opacity="0.04" />
      </linearGradient>
      <radialGradient :id="`${id}b`" cx="0.36" cy="0.3" r="0.85">
        <stop offset="0" style="stop-color: color-mix(in srgb, currentColor 30%, #fff)" stop-opacity="0.95" />
        <stop offset="1" stop-color="currentColor" stop-opacity="0.62" />
      </radialGradient>
      <radialGradient :id="`${id}h`" cx="0.4" cy="0.35" r="0.8">
        <stop offset="0" style="stop-color: color-mix(in srgb, currentColor 8%, #fff)" />
        <stop offset="0.5" stop-color="currentColor" stop-opacity="1" />
        <stop offset="1" style="stop-color: color-mix(in srgb, currentColor 80%, #000)" />
      </radialGradient>
    </defs>
    <g class="body">
      <path class="shell" :d="BRAIN_PATH" :fill="`url(#${id}f)`" stroke="none" />
      <g :clip-path="`url(#${id}c)`">
        <g class="folds" stroke="currentColor">
          <path :d="FOLD_PATH" />
          <path :d="FOLD_PATH" transform="matrix(-1 0 0 1 64 0)" />
          <path class="mid" d="M32 15C31.2 24 32.8 40 32 50" />
        </g>
        <g class="net">
          <path v-for="(_l, i) in links" :key="`w${i}`" class="wire" stroke="currentColor" />
          <g class="imp" fill="currentColor" stroke="none"><circle v-for="i in pool" :key="i" r="1" /></g>
        </g>
        <path v-for="(_l, i) in links" :key="`h${i}`" class="wire-hl" :class="{ on: hlLink[i] }" />
        <g class="nodes">
          <g v-for="(n, i) in nodes" :key="`n${i}`" class="nd" :transform="`translate(${n[0]} ${n[1]})`" :style="{ opacity: 0.5 + 0.5 * BRAIN_DEPTH[i] }">
            <circle class="nr" :r="radii[i] * 1.2" fill="none" stroke="currentColor" stroke-width="0.35" style="opacity: 0" />
            <circle class="lit-ring" :class="{ on: isOn(i) }" :r="radii[i] * 1.9" fill="none" />
            <circle class="nb" :r="radii[i]" :fill="`url(#${id}b)`" stroke="currentColor" stroke-width="0.32" stroke-opacity="0.75" />
            <circle class="nh" :r="radii[i]" :fill="`url(#${id}h)`" stroke="none" style="opacity: 0" />
            <circle class="nc" :r="radii[i] * 0.34" fill="#fff" stroke="none" />
          </g>
        </g>
      </g>
      <path class="outline" :d="BRAIN_PATH" stroke="currentColor" />
    </g>
    <path v-if="state === 'down'" class="cut" d="M14 50L50 14" stroke="currentColor" :clip-path="`url(#${id}c)`" />
  </svg>
</template>

<style scoped>
.brain {
  color: var(--muted);
  transition: color var(--dur-fast) ease;
  overflow: visible;
  transform-origin: 50% 50%;
}
.s-idle { color: var(--zone-brain); }
.s-thinking { color: var(--zone-brain); }
.s-protect { color: var(--warn); }
.s-down { color: var(--neg); }
.s-down .body { opacity: 0.55; }

.outline { stroke-width: 1.7px; vector-effect: non-scaling-stroke; }
.folds path { stroke-width: 1.1px; vector-effect: non-scaling-stroke; opacity: 0.32; }
.folds .mid { opacity: 0.4; }
.wire { stroke-width: 0.5; opacity: 0.17; pointer-events: none; }
.imp circle { opacity: 0; pointer-events: none; }
.nd { pointer-events: none; }
.cut { stroke-width: 1.6px; vector-effect: non-scaling-stroke; opacity: 0.9; }
.crisp .outline { stroke-width: 1.5px; }
.crisp .folds path { stroke-width: 1px; }
.crisp .wire { stroke-width: 0.4; }

/* подсветка области (окно запуска, роли): акцентное кольцо вокруг нейронов области и акцентные связи между ними, остальной мозг приглушён */
.net, .nb, .nc { transition: opacity var(--dur-enter) var(--ease-out); }
.has-lit .net, .has-lit .nb, .has-lit .nc { opacity: 0.5; }
.lit-ring, .wire-hl { pointer-events: none; opacity: 0; transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.lit-ring { stroke: var(--accent); stroke-width: 0.7; transform-box: fill-box; transform-origin: center; transform: scale(0.7); }
.lit-ring.on { opacity: 1; transform: scale(1); }
.wire-hl { stroke: var(--accent); stroke-width: 0.9; }
.wire-hl.on { opacity: 0.95; }
.has-lit .lit-ring.on, .has-lit .wire-hl.on { opacity: 1; }
@media (prefers-reduced-motion: reduce) {
  .lit-ring { transform: none; }
}
</style>
