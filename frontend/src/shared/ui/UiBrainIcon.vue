<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { BRAIN_NODES, nearestNode, waveOrder } from './brain-regions'
import { BrainSim, LINKS, PULSE_POOL } from './brain-sim'
/**
 * Нарисованный мозг (вид спереди): два полушария, борозды коры, 16 нейронов и связи между ними, включая мостик между полушариями. Один SVG, одна толщина линии.
 * Анимация (навык animate), назначение: показать состояние.
 *  - думает: нейроны загораются волной с мягким свечением, по связям бегут искры, сигнал переходит из полушария в полушарие, весь мозг медленно «дышит»;
 *  - ждёт: тихая жизнь: почти незаметное дыхание и редкие мерцания трёх-четырёх нейронов;
 *  - только защита (янтарный) и не работает (красный, перечёркнут): без движения.
 * Движется только opacity и transform, искры идут штрихом по линии (stroke-dashoffset, линейное движение); всё на CSS, вне основного потока. При prefers-reduced-motion
 * движения нет: в «думает» нейроны и связи горят ровно.
 *
 * Вход в «думает» и выход из него без скачка: интенсивность всех эффектов «думает» это одно зарегистрированное число --amp (0..1), которое плавно переходит
 * (transition 450 мс, ease-out) вместо того, чтобы обрывать анимацию. Циклы вспышек, искр и дыхания не снимаются, пока --amp не дошло до нуля: они продолжают идти, но их
 * размах (непрозрачность вспышек и искр, масштаб дыхания, свечение) умножен на --amp, поэтому гаснут по-плавному. Вспышки это отдельные кружки-наложения поверх нейронов,
 * а дыхание «думает» это отдельная группа над дыханием «ждёт»: фоновые анимации ожидания не вырываются и не перезапускаются при смене состояния.
 */
export type BrainIconState = 'idle' | 'thinking' | 'protect' | 'down'
const props = withDefaults(defineProps<{ state?: BrainIconState; size?: number; label?: string; interactive?: boolean; lit?: readonly number[]; crisp?: boolean }>(), { state: 'idle', size: 88, interactive: false, lit: () => [], crisp: false })
const emit = defineEmits<{ node: [index: number] }>()

const nodes = BRAIN_NODES
const links = LINKS
const pool = Array.from({ length: PULSE_POOL * 4 }, (_, i) => i)      // у каждого импульса голова и три точки хвоста

/*
 * Интерактив (interactive): курсор над нейроном сообщает наверх его номер (область коры), lit подсвечивает нейроны области роли, fire() запускает волну от места нажатия.
 * Подсветка одна и лаконичная: нейроны области и связи между ними заливаются акцентным цветом, остальной мозг приглушается. Назначение: пространственная связь
 * с панелью ролей. Всё через transition по opacity и transform (масштаб от 0.6, не от нуля); слой подсветки отдельный от фоновых анимаций состояний.
 * Наведение работает только с точным указателем (на touch есть тап), при prefers-reduced-motion волна зажигает нейроны разом.
 */
const FADE_MS = 450
const amp = ref(0)                 // доля эффектов «думает» сейчас, 0..1
const fx = ref(false)              // циклы эффектов запущены (остаются, пока amp не дойдёт до нуля)
let fxTimer: ReturnType<typeof setTimeout> | undefined
let raf = 0
watch(() => props.state, (s) => {
  clearTimeout(fxTimer)
  cancelAnimationFrame(raf)
  if (s === 'thinking') {
    fx.value = true
    raf = requestAnimationFrame(() => { raf = requestAnimationFrame(() => { amp.value = 1 }) })      // кадр с amp 0 успевает отрисоваться: нарастание плавное, а не скачок
  } else {
    amp.value = 0
    fxTimer = setTimeout(() => { fx.value = false }, FADE_MS + 150)
  }
}, { immediate: true })

const svg = ref<SVGSVGElement | null>(null)
const canHover = typeof matchMedia === 'function' && matchMedia('(hover: hover) and (pointer: fine)').matches
const calm = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
const fired = ref<number[]>([])
let overNode = -1
let timers: ReturnType<typeof setTimeout>[] = []

const toBox = (ev: MouseEvent): [number, number] => {
  const r = svg.value!.getBoundingClientRect()
  return [((ev.clientX - r.left) / r.width) * 64, ((ev.clientY - r.top) / r.height) * 64]
}
const isOn = (i: number) => fired.value.includes(i) || props.lit.includes(i)
const hasLit = computed(() => props.lit.length > 0)
/** Связи, у которых оба конца в подсвеченной области. */
const hlLink = computed(() => links.map(([a, b]) => props.lit.includes(a) && props.lit.includes(b)))

function onMove(ev: PointerEvent): void {
  if (!props.interactive || !canHover || ev.pointerType === 'touch' || !svg.value) return
  const [x, y] = toBox(ev)
  const n = nearestNode(x, y, 9)
  if (n !== overNode) {
    overNode = n
    emit('node', n)
    const t = performance.now()
    if (n >= 0 && loop && t - lastTouch > 160) { lastTouch = t; sim.touch(n) }       // курсор вошёл в нейрон: он отвечает тихим импульсом (не чаще раза в 160 мс)
  }
  if (loop) sim.setPointer([x, y])
}
function onLeave(): void {
  sim.setPointer(null)
  if (overNode !== -1) { overNode = -1; emit('node', -1) }
}

/** Импульс по сети: нейроны вспыхивают волной от точки нажатия (по умолчанию от центра). */
function fire(ev?: MouseEvent): void {
  if (!svg.value) return
  timers.forEach(clearTimeout)
  timers = []
  const [x, y] = ev && ev.clientX ? toBox(ev) : [32, 32]
  if (loop) {                         // живая сеть: от ближайшего к клику нейрона идёт каскад импульсов, окрестность упруго отталкивается; без точки клика (шаг запуска) старт с любого нейрона
    const n = ev && ev.clientX ? nearestNode(x, y, 64) : Math.floor(Math.random() * nodes.length)
    sim.burst(n, x, y)
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

/*
 * Живая сеть (brain-sim.ts): нейроны блуждают, связи изгибаются, образуются и растворяются, по ним бегут импульсы с хвостом, нейрон по прибытии вспыхивает и передаёт сигнал дальше.
 * Модель считается кадрами (около 30 в секунду, только пока иконка живая), положения пишутся прямо в атрибуты SVG, а не через реактивность. Для замершего мозга
 * (защита, нет связи, reduce motion) рисуется один кадр.
 */
const sim = new BrainSim(7 + Math.floor(Math.random() * 90))
const kk = props.crisp ? 0.8 : 1                    // размер голов импульсов и вспышек
let els: { wire: SVGPathElement[]; hl: SVGPathElement[]; node: SVGCircleElement[]; flash: SVGCircleElement[]; halo: SVGCircleElement[]; imp: SVGCircleElement[] } | null = null
let sAmp = 0                                         // плавная доля «думает» для модели
let last = 0
let acc = 0
let loop = 0
let lastTouch = 0
const TAIL = [[0, 1, 1.05], [0.07, 0.5, 0.8], [0.14, 0.26, 0.6], [0.21, 0.12, 0.45]]       // отступ по пути, прозрачность, радиус

function collect(): void {
  const q = <T extends Element>(c: string): T[] => Array.from(svg.value!.querySelectorAll<T>(c))
  els = { wire: q('.wire'), hl: q('.wire-hl'), node: q('.node'), flash: q('.flash'), halo: q('.halo'), imp: q('.imp circle') }
}
const num = (v: number): string => v.toFixed(2)
function draw(): void {
  if (!els) return
  const e = els
  const wireBase = 0.1 + 0.2 * sAmp
  LINKS.forEach(([a, b], i) => {
    const dd = `M${num(sim.x[a])} ${num(sim.y[a])}Q${num(sim.qx[i])} ${num(sim.qy[i])} ${num(sim.x[b])} ${num(sim.y[b])}`
    e.wire[i].setAttribute('d', dd)
    e.wire[i].style.opacity = String(Math.min(1, (wireBase + 0.55 * sim.glow[i]) * sim.str[i]))
    if (hasLit.value) e.hl[i].setAttribute('d', dd)
  })
  for (let i = 0; i < nodes.length; i++) {
    const f = sim.fire[i]
    const x = num(sim.x[i]); const y = num(sim.y[i])
    for (const c of [e.node[i], e.flash[i], e.halo[i]]) { c.setAttribute('cx', x); c.setAttribute('cy', y) }
    e.node[i].style.opacity = String(0.38 + 0.62 * f)
    e.flash[i].setAttribute('r', num((1.7 + 1.5 * f) * kk))
    e.flash[i].style.opacity = String(0.8 * f * f)
  }
  const vis = 0.55 + 0.45 * sAmp
  for (let p = 0; p < PULSE_POOL; p++) {
    const pl = sim.pulses[p]
    for (let k = 0; k < 4; k++) {
      const c = e.imp[p * 4 + k]
      if (!pl) { c.style.opacity = '0'; continue }
      const [px, py] = sim.point(pl, TAIL[k][0])
      c.setAttribute('cx', num(px)); c.setAttribute('cy', num(py)); c.setAttribute('r', num(TAIL[k][2] * kk))
      c.style.opacity = String(TAIL[k][1] * pl.power * vis)
    }
  }
}
function frame(ts: number): void {
  loop = requestAnimationFrame(frame)
  const dt = Math.min(0.1, (ts - last) / 1000)
  last = ts
  acc += dt
  if (acc < 0.028) return
  sAmp += ((props.state === 'thinking' ? 1 : 0) - sAmp) * (1 - Math.exp(-acc / 0.25))
  sim.step(acc, sAmp, true)
  acc = 0
  draw()
}
function run(): void {
  if (!els) return
  const alive = !calm && (props.state === 'idle' || props.state === 'thinking')
  if (alive) {
    if (!loop) { last = performance.now(); acc = 0; loop = requestAnimationFrame(frame) }
    return
  }
  if (loop) { cancelAnimationFrame(loop); loop = 0 }
  sim.setPointer(null)
  sAmp = props.state === 'thinking' ? 1 : 0           // замершая иконка: один кадр без импульсов
  sim.step(0, sAmp, false)
  draw()
}
onMounted(() => { collect(); run() })
watch(() => props.state, run)
watch(hasLit, () => draw())
onUnmounted(() => {
  timers.forEach(clearTimeout)
  clearTimeout(fxTimer)
  cancelAnimationFrame(raf)
  cancelAnimationFrame(loop)
})
</script>

<template>
  <svg ref="svg" class="brain" :class="[`s-${state}`, { live: interactive, fx, 'has-lit': hasLit, crisp }]" :style="{ '--amp': amp }" :width="size" :height="size" viewBox="0 0 64 64" role="img" :aria-label="label ?? 'мозг'" fill="none" stroke="currentColor"
       stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" @pointermove="onMove" @pointerleave="onLeave">
    <g class="pulse">
    <g class="body">
      <g class="cortex">
        <path d="M32 10C25 5 15 7 13 16C7 18 5 27 9 32C6 38 9 46 15 47C17 54 26 57 32 54" />
        <path d="M32 10C39 5 49 7 51 16C57 18 59 27 55 32C58 38 55 46 49 47C47 54 38 57 32 54" />
        <path d="M32 10V54" class="mid" />
        <path class="fold" d="M13 16C18 16 21 19 21 24M21 24C25 24 28 27 28 31M9 32C14 31 18 33 19 38M15 47C19 45 23 46 25 50M12 23C15 24 16 27 15 30M22 42C25 41 27 43 27 46M18 12C20 13 22 15 22 18" />
        <path class="fold" d="M51 16C46 16 43 19 43 24M43 24C39 24 36 27 36 31M55 32C50 31 46 33 45 38M49 47C45 45 41 46 39 50M52 23C49 24 48 27 49 30M42 42C39 41 37 43 37 46M46 12C44 13 42 15 42 18" />
      </g>
      <g class="net">
        <path v-for="(_l, i) in links" :key="`w${i}`" class="wire" />
        <g class="imp" fill="currentColor" stroke="none"><circle v-for="i in pool" :key="i" r="1" /></g>
      </g>
      <g class="nodes">
        <circle v-for="(n, i) in nodes" :key="i" class="node" :cx="n[0]" :cy="n[1]" r="1.7" fill="currentColor" stroke="none" />
        <circle v-for="(n, i) in nodes" :key="`f${i}`" class="flash" :cx="n[0]" :cy="n[1]" r="1.7" fill="currentColor" stroke="none" />
      </g>
      <g class="hl">
        <path v-for="(_l, i) in links" :key="`h${i}`" class="wire-hl" :class="{ on: hlLink[i] }" />
        <circle v-for="(n, i) in nodes" :key="`c${i}`" class="halo" :class="{ on: isOn(i) }" :cx="n[0]" :cy="n[1]" r="2.5" stroke="none" />
      </g>
    </g>
    </g>
    <path v-if="state === 'down'" class="cut" d="M10 54L54 10" />
  </svg>
</template>

<style scoped>
.brain {
  color: var(--muted);
  transition: color var(--dur-fast) ease;
  overflow: visible;
}
.s-thinking { color: var(--zone-brain); }
.s-protect { color: var(--warn); }
.s-down { color: var(--neg); }
.s-down .body { opacity: 0.55; }
.cut { stroke-width: 2.4; }

/* крупный размер (окно запуска): контур остаётся тонким и чётким при любом масштабе (не растёт вместе с рисунком), связи тоньше, свечение без размытия */
.crisp .cortex path { vector-effect: non-scaling-stroke; stroke-width: 1.6px; }
.crisp .cut { vector-effect: non-scaling-stroke; stroke-width: 2px; }
.crisp .wire { stroke-width: 0.5; }
.crisp .wire-hl { stroke-width: 0.9; }
.crisp .flash { filter: none; }
.crisp .node { r: 1.3px; }
.crisp .halo { r: 1.9px; }
.crisp .mid, .crisp .fold { opacity: 0.55; }

/* подсветка области: нейроны и связи области акцентным цветом, остальной мозг приглушён; transitions, чтобы быстрые движения мыши перенацеливали движение, а не перезапускали его */
.cortex, .net, .nodes { transition: opacity var(--dur-enter) var(--ease-out); }
.has-lit .cortex, .has-lit .net, .has-lit .nodes { opacity: 0.45; }
.halo, .wire-hl { pointer-events: none; opacity: 0; transition: opacity var(--dur-enter) var(--ease-out), transform var(--dur-enter) var(--ease-out); }
.halo { fill: var(--accent); transform-box: fill-box; transform-origin: center; transform: scale(0.6); }
.halo.on { opacity: 1; transform: scale(1); }
.wire-hl { stroke: var(--accent); stroke-width: 1.6; }
.wire-hl.on { opacity: 0.95; }
@media (prefers-reduced-motion: reduce) {
  .halo { transform: none; }
}

.mid, .fold { opacity: 0.5; }
.node { opacity: 0.38; }

/* --amp: доля эффектов «думает» (0..1); число зарегистрировано, поэтому плавно переходит и все зависящие от него значения вместе с ним (вход и выход без скачка) */
@property --amp {
  syntax: '<number>';
  inherits: true;
  initial-value: 0;
}
.brain { transition: color var(--dur-fast) ease, --amp 450ms var(--ease-out); }

/* ждёт (и пока думает, чтобы фон не обрывался): едва заметное дыхание и редкие мерцания */
.s-idle .body, .s-thinking .body { transform-origin: 32px 32px; animation: brain-breathe 7s var(--ease-in-out) infinite; }

/* думает: поверх ожидания нейроны вспыхивают волной со свечением, по связям бегут искры, мозг дышит чаще. Циклы идут, пока .fx, размах умножен на --amp */
.wire { stroke-width: 1; opacity: 0.2; }
.imp circle { opacity: 0; pointer-events: none; }
.flash { opacity: 0; filter: drop-shadow(0 0 2.4px currentColor); pointer-events: none; }
.pulse { transform-origin: 32px 32px; }
.fx .pulse { animation: brain-pulse 2.6s var(--ease-in-out) infinite; }

@keyframes brain-breathe { 50% { transform: scale(1.012); } }
@keyframes brain-pulse { 50% { transform: scale(calc(1 + 0.035 * var(--amp))); } }

@media (prefers-reduced-motion: reduce) {
  .s-idle .body, .s-thinking .body, .fx .pulse { animation: none; }
}
</style>
