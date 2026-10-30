<script setup lang="ts">
/**
 * Нарисованный мозг (вид спереди): два полушария, борозды и восемь узлов. Свой SVG в одной толщине линии, цвет от состояния.
 * Анимация «думает» (навык animate): назначение state indication, идёт, пока мозг в работе. Узлы вспыхивают по очереди с шагом 140 мс (opacity), будто сигнал бежит по извилинам,
 * сам значок медленно «дышит» (transform: scale, 2.4 с). CSS-анимации идут вне основного потока. При prefers-reduced-motion движения нет: узлы горят ровно.
 */
export type BrainIconState = 'idle' | 'thinking' | 'protect' | 'down'
withDefaults(defineProps<{ state?: BrainIconState; size?: number; label?: string }>(), { state: 'idle', size: 44 })

const nodes: Array<[number, number]> = [[21, 24], [28, 31], [19, 38], [25, 50], [39, 50], [45, 38], [36, 31], [43, 24]]      // порядок = путь сигнала
</script>

<template>
  <svg class="brain" :class="`s-${state}`" :width="size" :height="size" viewBox="0 0 64 64" role="img" :aria-label="label ?? 'мозг'" fill="none" stroke="currentColor"
       stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
    <g class="body">
      <path d="M32 10C25 5 15 7 13 16C7 18 5 27 9 32C6 38 9 46 15 47C17 54 26 57 32 54" />
      <path d="M32 10C39 5 49 7 51 16C57 18 59 27 55 32C58 38 55 46 49 47C47 54 38 57 32 54" />
      <path d="M32 10V54" class="mid" />
      <path d="M13 16C18 16 21 19 21 24M21 24C25 24 28 27 28 31M9 32C14 31 18 33 19 38M15 47C19 45 23 46 25 50" class="fold" />
      <path d="M51 16C46 16 43 19 43 24M43 24C39 24 36 27 36 31M55 32C50 31 46 33 45 38M49 47C45 45 41 46 39 50" class="fold" />
      <circle v-for="(n, i) in nodes" :key="i" class="node" :style="{ '--i': i }" :cx="n[0]" :cy="n[1]" r="1.9" fill="currentColor" stroke="none" />
    </g>
    <path v-if="state === 'down'" class="cut" d="M12 52L52 12" />
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
.mid, .fold { opacity: 0.55; }
.node { opacity: 0.4; transition: opacity var(--dur-fast) ease; }
.s-down .body { opacity: 0.55; }
.cut { stroke-width: 2.6; }

.s-thinking .body {
  transform-origin: 32px 32px;
  animation: brain-breath 2.4s var(--ease-in-out) infinite;
}
.s-thinking .node {
  animation: brain-fire 1.12s var(--ease-in-out) infinite;
  animation-delay: calc(var(--i) * 140ms);
}
@keyframes brain-breath {
  50% { transform: scale(1.04); }
}
@keyframes brain-fire {
  0%, 100% { opacity: 0.25; }
  35% { opacity: 1; }
}
@media (prefers-reduced-motion: reduce) {
  .s-thinking .body, .s-thinking .node { animation: none; }
  .s-thinking .node { opacity: 1; }
}
</style>
