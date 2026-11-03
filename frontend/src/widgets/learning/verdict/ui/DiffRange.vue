<script setup lang="ts">
import { computed } from 'vue'
import type { Est } from '@/entities/learning'
import { rangeGeom } from '../lib/verdict'

/** Малая полоса: интервал разности относительно нуля. Рисунок дублирует текст рядом, поэтому скрыт от читалок. */
const props = withDefaults(defineProps<{ diff: Est | null; width?: number }>(), { width: 132 })
const g = computed(() => rangeGeom(props.diff, props.width))
</script>

<template>
  <svg v-if="g" class="rng" :width="width" height="16" :viewBox="`0 0 ${width} 16`" aria-hidden="true" focusable="false">
    <line class="axis" x1="0" :x2="width" y1="8" y2="8" />
    <line class="span" :x1="g.lo" :x2="Math.max(g.hi, g.lo + 0.01)" y1="8" y2="8" :class="{ clear: g.clear }" />
    <line class="zero" :x1="g.zero" :x2="g.zero" y1="1" y2="15" />
    <circle class="pt" :cx="g.v" cy="8" r="3.5" :class="{ clear: g.clear }" />
  </svg>
</template>

<style scoped>
.rng { display: block; flex: none; overflow: visible; }
.axis { stroke: var(--line); stroke-width: 1; }
.span { stroke: var(--muted); stroke-width: 4; stroke-linecap: round; stroke-opacity: 0.55; }
.span.clear { stroke: var(--accent); stroke-opacity: 0.6; }
.zero { stroke: var(--text); stroke-width: 1; }
.pt { fill: var(--text); stroke: var(--panel); stroke-width: 2; }
.pt.clear { fill: var(--accent); }
</style>
