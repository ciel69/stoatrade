<script setup lang="ts">
/**
 * Точка состояния. Движение только у `busy` (мозг думает): мягкое мерцание прозрачности. Назначение: показать состояние (state indication).
 * Бесконечная CSS-анимация opacity идёт вне основного потока; при prefers-reduced-motion остаётся статичная точка с кольцом.
 */
export type DotState = 'ok' | 'warn' | 'bad' | 'idle' | 'busy'
withDefaults(defineProps<{ state?: DotState; label?: string }>(), { state: 'idle' })
</script>

<template>
  <span class="dot" :class="`s-${state}`" role="img" :aria-label="label ?? state" />
</template>

<style scoped>
.dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--muted);
  vertical-align: middle;
  transition: background-color var(--dur-fast) ease, box-shadow var(--dur-fast) ease;
}
.s-ok { background: var(--pos); }
.s-warn { background: var(--warn); }
.s-bad { background: var(--neg); }
.s-idle { background: var(--muted); }
.s-busy {
  background: var(--zone-brain);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--zone-brain) 28%, transparent);
  animation: dot-think 1.4s ease-in-out infinite;
}
@keyframes dot-think {
  50% { opacity: 0.35; }
}
@media (prefers-reduced-motion: reduce) {
  .s-busy { animation: none; }
}
</style>
