<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { BRAIN_NODES, nearestNode, waveOrder } from './brain-regions'
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
const pairs: Array<[number, number]> = [[0, 1], [1, 3], [2, 3], [3, 5], [4, 6], [6, 5], [5, 7], [2, 1]]
const links: Array<[number, number]> = [...pairs, ...pairs.map(([a, b]): [number, number] => [a + 8, b + 8]), [1, 9], [3, 11], [5, 13], [7, 15]]
const d = (a: number, b: number) => `M${nodes[a][0]} ${nodes[a][1]}L${nodes[b][0]} ${nodes[b][1]}`
const delays = [0, 380, 140, 620, 250, 500, 90, 700, 310, 60, 560, 200, 430, 680, 160, 520]       // порядок вспышек: не по кругу, как у живой сети
const idleTwinkle = new Set([2, 7, 10, 13])

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
  if (n !== overNode) { overNode = n; emit('node', n) }
}
function onLeave(): void {
  if (overNode !== -1) { overNode = -1; emit('node', -1) }
}

/** Импульс по сети: нейроны вспыхивают волной от точки нажатия (по умолчанию от центра). */
function fire(ev?: MouseEvent): void {
  if (!svg.value) return
  timers.forEach(clearTimeout)
  timers = []
  const [x, y] = ev && ev.clientX ? toBox(ev) : [32, 32]
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
onUnmounted(() => {
  timers.forEach(clearTimeout)
  clearTimeout(fxTimer)
  cancelAnimationFrame(raf)
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
        <path v-for="(l, i) in links" :key="`w${i}`" class="wire" :d="d(l[0], l[1])" />
        <path v-for="(l, i) in links" :key="`s${i}`" class="spark" :style="{ '--k': i }" :d="d(l[0], l[1])" pathLength="12" />
      </g>
      <g class="nodes">
        <circle v-for="(n, i) in nodes" :key="i" class="node" :class="{ tw: idleTwinkle.has(i) }" :style="{ '--d': delays[i] + 'ms', '--i': i }" :cx="n[0]" :cy="n[1]" r="1.7" fill="currentColor" stroke="none" />
        <circle v-for="(n, i) in nodes" :key="`f${i}`" class="flash" :style="{ '--d': delays[i] + 'ms' }" :cx="n[0]" :cy="n[1]" r="1.7" fill="currentColor" stroke="none" />
      </g>
      <g class="hl">
        <path v-for="(l, i) in links" :key="`h${i}`" class="wire-hl" :class="{ on: hlLink[i] }" :d="d(l[0], l[1])" />
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
.crisp .spark { stroke-width: 0.9; }
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
.s-idle .node.tw, .s-thinking .node.tw { animation: brain-twinkle 6s var(--ease-in-out) infinite; animation-delay: calc(var(--i) * 410ms); }

/* думает: поверх ожидания нейроны вспыхивают волной со свечением, по связям бегут искры, мозг дышит чаще. Циклы идут, пока .fx, размах умножен на --amp */
.wire { opacity: calc(0.12 + 0.22 * var(--amp)); stroke-width: 1; }
.spark { opacity: calc(0.95 * var(--amp)); stroke-width: 1.8; stroke-dasharray: 2 10; }
.flash { opacity: 0; filter: drop-shadow(0 0 2.4px currentColor); pointer-events: none; }
.pulse { transform-origin: 32px 32px; }
.fx .pulse { animation: brain-pulse 2.6s var(--ease-in-out) infinite; }
.fx .spark { animation: brain-spark 1.1s linear infinite; animation-delay: calc(var(--k) * 90ms); }
.fx .flash { animation: brain-fire 1.3s var(--ease-in-out) infinite; animation-delay: var(--d); }

@keyframes brain-breathe { 50% { transform: scale(1.012); } }
@keyframes brain-pulse { 50% { transform: scale(calc(1 + 0.035 * var(--amp))); } }
@keyframes brain-twinkle { 0%, 70%, 100% { opacity: 0.38; } 85% { opacity: 1; } }
@keyframes brain-fire { 0%, 100% { opacity: 0; } 40% { opacity: var(--amp); } }
@keyframes brain-spark { to { stroke-dashoffset: -12; } }

@media (prefers-reduced-motion: reduce) {
  .s-idle .body, .s-thinking .body, .s-idle .node.tw, .s-thinking .node.tw, .fx .pulse, .fx .spark, .fx .flash { animation: none; }
  .flash { opacity: calc(0.8 * var(--amp)); }
  .spark { opacity: 0; }
}
</style>
