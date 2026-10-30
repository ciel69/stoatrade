<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { BrainSim, PULSE_POOL, type Pt } from './brain-sim'

/**
 * Живая сеть нейронов на произвольной раскладке (фон кнопки, плашка): тот же движок, что у мозга (brain-sim). Нейроны блуждают, связи изгибаются, образуются и растворяются,
 * по ним бегут импульсы с хвостом. Цвет берётся из `color` родителя (currentColor), поэтому сеть ложится на любую заливку.
 * Состояния: idle (редкие тихие импульсы), thinking (частые каскады), still (один кадр, без движения). Курсор и клик приходят от родителя через pointer() и burst(): сама сеть
 * событий не слушает (pointer-events: none), чтобы не перехватывать нажатие кнопки. Один раз в 30 кадров в секунду пишет координаты в SVG; при reduce motion не движется.
 */
const props = withDefaults(defineProps<{ nodes: ReadonlyArray<Pt>; width: number; height: number; scale?: number; linkMax?: number; maxDeg?: number; state?: 'idle' | 'thinking' | 'still'; seed?: number; dot?: number; ink?: number }>(),
  { scale: 1, state: 'idle', seed: 11, dot: 1, ink: 1 })

const svg = ref<SVGSVGElement | null>(null)
const calm = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
const sim = new BrainSim(props.seed, props.nodes, { scale: props.scale, linkMax: props.linkMax, maxDeg: props.maxDeg })
const links = sim.links
const pool = Array.from({ length: PULSE_POOL * 4 }, (_, i) => i)
const TAIL = [[0, 1, 1.5], [0.07, 0.6, 1.2], [0.14, 0.34, 0.9], [0.21, 0.16, 0.65]]       // отступ по пути, прозрачность, радиус (в единицах × dot)
let els: { wire: SVGPathElement[]; node: SVGCircleElement[]; flash: SVGCircleElement[]; imp: SVGCircleElement[] } | null = null
let sAmp = 0
let last = 0
let acc = 0
let loop = 0
let lastTouch = 0
let overNode = -1

function collect(): void {
  const q = <T extends Element>(c: string): T[] => Array.from(svg.value!.querySelectorAll<T>(c))
  els = { wire: q('.wire'), node: q('.node'), flash: q('.flash'), imp: q('.imp circle') }
}
const num = (v: number): string => v.toFixed(2)
function draw(): void {
  if (!els) return
  const e = els
  const base = (0.12 + 0.2 * sAmp) * props.ink
  links.forEach(([a, b], i) => {
    e.wire[i].setAttribute('d', `M${num(sim.x[a])} ${num(sim.y[a])}Q${num(sim.qx[i])} ${num(sim.qy[i])} ${num(sim.x[b])} ${num(sim.y[b])}`)
    e.wire[i].style.opacity = String(Math.min(1, (base + 0.7 * sim.glow[i]) * sim.str[i]))
  })
  for (let i = 0; i < props.nodes.length; i++) {
    const f = sim.fire[i]
    const x = num(sim.x[i])
    const y = num(sim.y[i])
    for (const c of [e.node[i], e.flash[i]]) { c.setAttribute('cx', x); c.setAttribute('cy', y) }
    e.node[i].style.opacity = String(Math.min(1, (0.35 + 0.65 * f) * props.ink))
    e.flash[i].setAttribute('r', num((1.8 + 2.4 * f) * props.dot))
    e.flash[i].style.opacity = String(0.9 * f * f)
  }
  const vis = 0.6 + 0.4 * sAmp
  for (let p = 0; p < PULSE_POOL; p++) {
    const pl = sim.pulses[p]
    for (let k = 0; k < 4; k++) {
      const c = e.imp[p * 4 + k]
      if (!pl) { c.style.opacity = '0'; continue }
      const [px, py] = sim.point(pl, TAIL[k][0])
      c.setAttribute('cx', num(px)); c.setAttribute('cy', num(py)); c.setAttribute('r', num(TAIL[k][2] * props.dot))
      c.style.opacity = String(TAIL[k][1] * pl.power * vis)
    }
  }
}
function frame(ts: number): void {
  loop = requestAnimationFrame(frame)
  acc += Math.min(0.1, (ts - last) / 1000)
  last = ts
  if (acc < 0.028) return
  sAmp += ((props.state === 'thinking' ? 1 : 0) - sAmp) * (1 - Math.exp(-acc / 0.25))
  sim.step(acc, sAmp, true)
  acc = 0
  draw()
}
function run(): void {
  if (!els) return
  if (!calm && props.state !== 'still') {
    if (!loop) { last = performance.now(); acc = 0; loop = requestAnimationFrame(frame) }
    return
  }
  if (loop) { cancelAnimationFrame(loop); loop = 0 }
  sim.setPointer(null)
  sAmp = props.state === 'thinking' ? 1 : 0
  sim.step(0, sAmp, false)
  draw()
}

const toBox = (ev: MouseEvent): [number, number] => {
  const r = svg.value!.getBoundingClientRect()
  return [((ev.clientX - r.left) / r.width) * props.width, ((ev.clientY - r.top) / r.height) * props.height]
}
function nearest(x: number, y: number, maxD: number): number {
  let best = -1
  let d = maxD
  for (let i = 0; i < props.nodes.length; i++) {
    const e = Math.hypot(sim.x[i] - x, sim.y[i] - y)
    if (e <= d) { d = e; best = i }
  }
  return best
}
/** Курсор над родителем (null: ушёл): ближние нейроны тянутся к нему, вход в нейрон будит его тихим импульсом (не чаще раза в 160 мс). */
function pointer(ev: PointerEvent | null): void {
  if (!loop || !svg.value) return
  if (!ev || ev.pointerType === 'touch') { sim.setPointer(null); overNode = -1; return }
  const [x, y] = toBox(ev)
  sim.setPointer([x, y])
  const n = nearest(x, y, 9 * props.scale)
  if (n !== overNode) {
    overNode = n
    const t = performance.now()
    if (n >= 0 && t - lastTouch > 160) { lastTouch = t; sim.touch(n) }
  }
}
/** Клик по родителю: ближний к точке нейрон возбуждается и запускает каскад, окрестность упруго отталкивается. */
function burst(ev?: MouseEvent): void {
  if (!loop || !svg.value) return
  const [x, y] = ev && ev.clientX ? toBox(ev) : [props.width / 2, props.height / 2]
  sim.burst(nearest(x, y, 1e9), x, y)
}
defineExpose({ pointer, burst })
onMounted(() => { collect(); run() })
watch(() => props.state, run)
onUnmounted(() => cancelAnimationFrame(loop))
</script>

<template>
  <svg ref="svg" class="field" :viewBox="`0 0 ${width} ${height}`" preserveAspectRatio="xMidYMid slice" aria-hidden="true" fill="none" stroke="currentColor" stroke-linecap="round">
    <path v-for="(_l, i) in links" :key="`w${i}`" class="wire" />
    <g class="imp" fill="currentColor" stroke="none"><circle v-for="i in pool" :key="i" r="1" /></g>
    <g fill="currentColor" stroke="none">
      <circle v-for="(n, i) in nodes" :key="`n${i}`" class="node" :cx="n[0]" :cy="n[1]" :r="1.8 * dot" />
      <circle v-for="(n, i) in nodes" :key="`f${i}`" class="flash" :cx="n[0]" :cy="n[1]" :r="1.8 * dot" />
    </g>
  </svg>
</template>

<style scoped>
.field { display: block; width: 100%; height: 100%; pointer-events: none; overflow: hidden; }
.wire { stroke-width: 0.9; opacity: 0.2; }
.imp circle { opacity: 0; }
.flash { opacity: 0; }
</style>
