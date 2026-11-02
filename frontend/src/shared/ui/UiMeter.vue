<script setup lang="ts">
/** Тонкая шкала запаса: fill 0..1 (доля оставшегося). Цвет по состоянию. Заполнение меняется переходом transform (не width), без дёрганья на каждом обновлении. */
import { computed } from 'vue'
const props = withDefaults(defineProps<{ fill: number; level?: 'ok' | 'warn' | 'bad' | 'off'; label?: string }>(), { level: 'ok' })
const f = computed(() => Math.max(0, Math.min(1, Number.isFinite(props.fill) ? props.fill : 0)))
</script>

<template>
  <div class="meter" role="progressbar" :aria-label="label" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="Math.round(f * 100)">
    <i class="fill" :class="`l-${level}`" :style="{ transform: `scaleX(${f})` }" />
  </div>
</template>

<style scoped>
.meter { height: 4px; border-radius: 99px; background: var(--panel2); overflow: hidden; }
.fill { display: block; height: 100%; width: 100%; transform-origin: left; border-radius: 99px; background: var(--pos); transition: transform 400ms var(--ease-out), background-color var(--dur-fast) ease; }
.l-warn { background: var(--warn); }
.l-bad { background: var(--neg); }
.l-off { background: var(--muted); }
</style>
