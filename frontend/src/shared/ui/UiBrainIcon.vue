<script setup lang="ts">
/**
 * Нарисованный мозг (вид спереди): два полушария, борозды коры, 16 нейронов и связи между ними, включая мостик между полушариями. Один SVG, одна толщина линии.
 * Анимация (навык animate), назначение: показать состояние.
 *  - думает: нейроны загораются волной с мягким свечением, по связям бегут искры, сигнал переходит из полушария в полушарие, весь мозг медленно «дышит»;
 *  - ждёт: тихая жизнь: почти незаметное дыхание и редкие мерцания трёх-четырёх нейронов;
 *  - только защита (янтарный) и не работает (красный, перечёркнут): без движения.
 * Движется только opacity и transform, искры идут штрихом по линии (stroke-dashoffset, линейное движение); всё на CSS, вне основного потока. При prefers-reduced-motion
 * движения нет: в «думает» нейроны и связи горят ровно.
 */
export type BrainIconState = 'idle' | 'thinking' | 'protect' | 'down'
withDefaults(defineProps<{ state?: BrainIconState; size?: number; label?: string }>(), { state: 'idle', size: 88 })

// 0..7 левое полушарие, 8..15 зеркально в правом
const left: Array<[number, number]> = [[19, 20], [26, 27], [15, 29], [22, 36], [12, 38], [27, 42], [19, 46], [25, 52]]
const nodes: Array<[number, number]> = [...left, ...left.map(([x, y]): [number, number] => [64 - x, y])]
const pairs: Array<[number, number]> = [[0, 1], [1, 3], [2, 3], [3, 5], [4, 6], [6, 5], [5, 7], [2, 1]]
const links: Array<[number, number]> = [...pairs, ...pairs.map(([a, b]): [number, number] => [a + 8, b + 8]), [1, 9], [3, 11], [5, 13], [7, 15]]
const d = (a: number, b: number) => `M${nodes[a][0]} ${nodes[a][1]}L${nodes[b][0]} ${nodes[b][1]}`
const delays = [0, 380, 140, 620, 250, 500, 90, 700, 310, 60, 560, 200, 430, 680, 160, 520]       // порядок вспышек: не по кругу, как у живой сети
const idleTwinkle = new Set([2, 7, 10, 13])
</script>

<template>
  <svg class="brain" :class="`s-${state}`" :width="size" :height="size" viewBox="0 0 64 64" role="img" :aria-label="label ?? 'мозг'" fill="none" stroke="currentColor"
       stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
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

.mid, .fold { opacity: 0.5; }
.wire { opacity: 0.12; stroke-width: 1; transition: opacity var(--dur-fast) ease; }
.spark { opacity: 0; stroke-width: 1.8; stroke-dasharray: 2 10; }
.node { opacity: 0.38; }

/* ждёт: едва заметное дыхание и редкие мерцания */
.s-idle .body { transform-origin: 32px 32px; animation: brain-breathe 7s var(--ease-in-out) infinite; }
.s-idle .node.tw { animation: brain-twinkle 6s var(--ease-in-out) infinite; animation-delay: calc(var(--i) * 410ms); }

/* думает: нейроны вспыхивают волной со свечением, по связям бегут искры */
.s-thinking .body { transform-origin: 32px 32px; animation: brain-breathe 2.6s var(--ease-in-out) infinite; }
.s-thinking .wire { opacity: 0.34; }
.s-thinking .spark { opacity: 0.95; animation: brain-spark 1.1s linear infinite; animation-delay: calc(var(--k) * 90ms); }
.s-thinking .node { filter: drop-shadow(0 0 2.4px currentColor); animation: brain-fire 1.3s var(--ease-in-out) infinite; animation-delay: var(--d); }

@keyframes brain-breathe { 50% { transform: scale(1.035); } }
@keyframes brain-twinkle { 0%, 70%, 100% { opacity: 0.38; } 85% { opacity: 1; } }
@keyframes brain-fire { 0%, 100% { opacity: 0.22; } 40% { opacity: 1; } }
@keyframes brain-spark { to { stroke-dashoffset: -12; } }

@media (prefers-reduced-motion: reduce) {
  .s-idle .body, .s-thinking .body, .s-idle .node.tw, .s-thinking .node, .s-thinking .spark { animation: none; }
  .s-thinking .node { opacity: 1; }
  .s-thinking .spark { opacity: 0; }
}
</style>
